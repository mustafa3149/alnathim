"""SignalMonitor — SNMP signal monitoring for optical/wireless clients.

Fetches ONU optical power and wireless signal strength/CCQ via SNMP.
Uses pysnmp 7.x async API internally (wrapped with asyncio.run) so the
public methods stay synchronous and failure-safe. On timeout / unreachable
device every method returns a clean dict with status "offline/timeout" and
NEVER raises.
"""
import asyncio

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    UdpTransportTarget,
    get_cmd,
    walk_cmd,
)
from pysnmp.entity.engine import SnmpEngine

from .config import load_snmp_config


class SignalMonitor:
    """Send SNMP GET/WALK requests and return signal readings as dicts."""

    def __init__(self):
        cfg = load_snmp_config()
        self.community = cfg["community"]
        self.port = cfg["port"]
        self.timeout = cfg["timeout"]
        self.retries = cfg["retries"]
        self.oid_onu_rx = cfg["oid_onu_rx"]
        self.oid_onu_tx = cfg["oid_onu_tx"]
        self.oid_ubnt_signal = cfg["oid_ubnt_signal"]
        self.oid_ubnt_ccq = cfg["oid_ubnt_ccq"]
        self.oid_mikrotik_signal = cfg["oid_mikrotik_signal"]

    def _transport(self, ip):
        """Build the UDP transport target with the configured timeout/retries."""
        return UdpTransportTarget((ip, self.port), timeout=self.timeout, retries=self.retries)

    def _community(self, community=None):
        """Return the effective SNMP community: override or configured default.

        Args:
            community: optional per-call community string; when empty/None
                the configured default (from env/DB) is used.

        Returns:
            string community name.
        """
        if community is not None and str(community).strip() != "":
            return str(community).strip()
        return self.community

    def _community_debug(self, community=None):
        """Print the exact community string that will be used for SNMP (diagnostics)."""
        print(f"SNMP using community='{self._community(community)}' port={self.port} timeout={self.timeout} retries={self.retries}")

    async def _on_get(self, oid, ip, community=None):
        """Async coroutine: SNMP GET, return value string or None.

        Uses a fresh SnmpEngine + SNMPv1 community (mpModel=0) which is the
        pattern verified to work against Ubiquiti AirOS devices.

        Args:
            oid: OID to read.
            ip: target host.
            community: optional per-call SNMP community override; falls
                back to the configured default when empty/None.
        """
        try:
            engine = SnmpEngine()
            transport = await UdpTransportTarget.create(
                (ip, self.port), timeout=self.timeout, retries=self.retries
            )
            error_indication, error_status, error_index, var_binds = await get_cmd(
                engine,
                CommunityData(self._community(community), mpModel=0),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )
            engine.close_dispatcher()
        except Exception as e:
            print(f"SNMP GET ERROR [{ip}] OID={oid} community='{self._community(community)}': {e}")
            return None
        if error_indication or error_status:
            print(f"SNMP GET INDICATION [{ip}] OID={oid}: {error_indication or error_status}")
            return None
        if var_binds:
            return var_binds[0][1].prettyPrint()
        return None

    async def _on_walk(self, oid_base, ip, community=None):
        """Async coroutine: SNMP WALK, return {oid: value}.

        Mirrors the verified working probe pattern:
        walk_cmd + fresh SnmpEngine + SNMPv1 community (mpModel=0) +
        lexicographicMode=True. This is what actually responds on
        Ubiquiti AirOS NanoStation/PowerBeam devices.

        Args:
            oid_base: base OID to walk under.
            ip: target host.
            community: optional per-call SNMP community override; falls
                back to the configured default when empty/None.
        """
        results = {}
        try:
            engine = SnmpEngine()
            transport = await UdpTransportTarget.create(
                (ip, self.port), timeout=self.timeout, retries=self.retries
            )
            it = walk_cmd(
                engine,
                CommunityData(self._community(community), mpModel=0),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity(oid_base)),
                lexicographicMode=True,
            )
            async for error_indication, error_status, error_index, var_binds in it:
                if error_indication or error_status:
                    print(f"SNMP WALK INDICATION [{ip}] base={oid_base}: {error_indication or error_status}")
                    break
                for name, val in var_binds:
                    results[name.prettyPrint()] = val.prettyPrint()
            engine.close_dispatcher()
        except Exception as e:
            print(f"SNMP WALK ERROR [{ip}] base={oid_base} community='{self._community(community)}': {e}")
        return results

    # ── Low-level primitives (never raise) ──────────────────────
    def snmp_get(self, oid, ip, community=None):
        """Return the value of a single OID as str, or None on any failure.

        Args:
            oid: OID to read.
            ip: target host.
            community: optional SNMP community override; defaults to config.
        """
        try:
            return asyncio.run(self._on_get(oid, ip, community))
        except Exception:
            return None

    def snmp_walk(self, oid_base, ip, community=None):
        """Return {oid: value} for a walk under oid_base, or {} on any failure.

        Args:
            oid_base: base OID to walk under.
            ip: target host.
            community: optional SNMP community override; defaults to config.
        """
        try:
            return asyncio.run(self._on_walk(oid_base, ip, community))
        except Exception:
            return {}

    # ── Optical (GPON ONT ST) ───────────────────────────────────
    def get_olt_rx(self, ip, onu_index=1, community=None):
        """Return ONU RX optical power (dBm) or None.

        Args:
            ip: target host.
            onu_index: ONU index suffix on the RX OID.
            community: optional SNMP community override; defaults to config.
        """
        return self.snmp_get("%s.%s" % (self.oid_onu_rx, onu_index), ip, community)

    def get_olt_tx(self, ip, onu_index=1, community=None):
        """Return ONU TX optical power (dBm) or None.

        Args:
            ip: target host.
            onu_index: ONU index suffix on the TX OID.
            community: optional SNMP community override; defaults to config.
        """
        return self.snmp_get("%s.%s" % (self.oid_onu_tx, onu_index), ip, community)

    def get_optical_signal(self, ip, onu_index=1, community=None):
        """Return RX/TX optical dict, or status offline/timeout.

        Args:
            ip: target host.
            onu_index: ONU index suffix on the OIDs.
            community: optional SNMP community override; defaults to config.
        """
        rx = self.get_olt_rx(ip, onu_index, community)
        tx = self.get_olt_tx(ip, onu_index, community)
        if rx is None and tx is None:
            return {"ip": ip, "status": "offline/timeout"}
        return {
            "ip": ip,
            "rx_dbm": rx,
            "tx_dbm": tx,
            "type": "optical",
            "status": "good",
        }

    @staticmethod
    def _oid_suffix(oid):
        """Return the numeric OID segments as ints (digits only).

        pysnmp prettyPrint() prefixes the numeric OID with the MIB label,
        e.g. "SNMPv2-SMI::enterprises.41112.1.4.5.1.5.1". We keep only the
        numeric segments so the marker sequences ([5,1,5], [7,1,6]) match.
        """
        try:
            return [int(x) for x in oid.split(".") if x.strip().isdigit()]
        except (ValueError, AttributeError):
            return []

    @staticmethod
    def _parse_ubnt_signal(data):
        """Extract signal/ccq from a Ubiquiti AirOS subtree walk.

        The verified walk of 1.3.6.1.4.1.41112.1.4 returns, among others:
            ...41112.1.4.5.1.5.1 = -57    (radio signal, dBm)
            ...41112.1.4.7.1.6.1.<mac> = 94   (station CCQ — long MAC tail)

        We scan the whole numeric OID for the marker sequences so the
        station-table MAC bytes never break the match.

        Returns:
            (signal, ccq) tuple where each may be None.
        """
        signal = None
        ccq = None
        for oid, val in data.items():
            parts = SignalMonitor._oid_suffix(oid)
            if len(parts) < 4:
                continue
            for i in range(len(parts) - 2):
                seq = parts[i:i + 3]
                if seq == [5, 1, 5] and signal is None:
                    try:
                        signal = int(float(val))
                    except (ValueError, TypeError):
                        pass
                elif seq == [7, 1, 6] and ccq is None:
                    try:
                        ccq = int(float(val))
                    except (ValueError, TypeError):
                        pass
        if signal is None:
            # fallback: any plausible numeric wireless reading (-120..0 dBm)
            for val in data.values():
                try:
                    n = int(float(val))
                except (ValueError, TypeError):
                    continue
                if -120 <= n <= 0:
                    signal = n
                    break
        return signal, ccq

    # ── Wireless (Ubiquiti / MikroTik) ──────────────────────────
    def get_wireless_signal(self, ip, community=None):
        """Return signal strength + CCQ dict, or status offline/timeout.

        Walks the whole Ubiquiti AirOS subtree (41112.1.4) which is what
        the verified probe responds to, then falls back to MikroTik.

        Args:
            ip: target host.
            community: optional SNMP community override; defaults to config.
        """
        data = self.snmp_walk(self.oid_ubnt_signal, ip, community)
        if not data:
            data = self.snmp_walk(self.oid_mikrotik_signal, ip, community)
        if not data:
            return {"ip": ip, "status": "offline/timeout"}

        signal, ccq = self._parse_ubnt_signal(data)

        if signal is None and ccq is None:
            # Nothing numeric on the Ubiquiti subtree — try MikroTik OID.
            mkt = self.snmp_walk(self.oid_mikrotik_signal, ip, community)
            if mkt:
                for val in mkt.values():
                    try:
                        signal = int(float(val))
                    except (ValueError, TypeError):
                        continue
                    if signal is not None:
                        break

        if signal is None and ccq is None:
            return {"ip": ip, "status": "offline/timeout"}

        return {
            "ip": ip,
            "signal_dbm": str(signal) if signal is not None else None,
            "ccq": str(ccq) if ccq is not None else None,
            "type": "wireless",
            "status": "good",
        }

    @staticmethod
    def _is_good(result):
        """Return True when a signal result dict has status 'good'."""
        return bool(result) and result.get("status") == "good"

    # ── Unified entry point ─────────────────────────────────────
    def get_client_signal(self, ip, device_type="auto", community=None):
        """Return a signal dict for ip with exception-safe fallback.

        Args:
            ip: target host.
            device_type: 'auto' | 'standard' | 'optical' | 'wireless'.
                'auto' and 'standard' are aliases for the automatic probe:
                optical first, then wireless when optics fail. A thrown
                exception or offline/timeout on optics NEVER halts the
                fallback — wireless is always attempted next.
            community: optional SNMP community override; defaults to config.

        Returns:
            dict with status good/offline-timeout and signal fields.
            'offline/timeout' is returned ONLY when both optical and
            wireless checks fail.
        """
        dtype = (device_type or "auto").strip().lower()
        self._community_debug(community)

        if dtype == "wireless":
            try:
                return self.get_wireless_signal(ip, community=community)
            except Exception as e:
                print(f"SNMP GET_CLIENT ERROR [{ip}] type=wireless: {e}")
                return {"ip": ip, "status": "offline/timeout"}

        if dtype == "optical":
            try:
                return self.get_optical_signal(ip, community=community)
            except Exception as e:
                print(f"SNMP GET_CLIENT ERROR [{ip}] type=optical: {e}")
                return {"ip": ip, "status": "offline/timeout"}

        # auto / standard: optical first, then WIRELESS — a failed or
        # throwing optical probe must never prevent the wireless attempt.
        optical = None
        try:
            optical = self.get_optical_signal(ip, community=community)
        except Exception as e:
            print(f"SNMP GET_CLIENT ERROR [{ip}] type=optical(auto): {e}")
            optical = None  # keep going — do not halt on a probe error

        if self._is_good(optical):
            return optical

        wireless = None
        try:
            wireless = self.get_wireless_signal(ip, community=community)
        except Exception as e:
            print(f"SNMP GET_CLIENT ERROR [{ip}] type=wireless(auto): {e}")
            wireless = None

        if self._is_good(wireless):
            return wireless

        # Both probes failed — only now report offline/timeout.
        return {"ip": ip, "status": "offline/timeout"}
