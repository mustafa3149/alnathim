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
    next_cmd,
)

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

    async def _on_get(self, oid, ip):
        """Async coroutine: SNMP GET, return value string or None."""
        error_indication, error_status, error_index, var_binds = await get_cmd(
            CommunityData(self.community),
            self._transport(ip),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        if error_indication or error_status:
            return None
        if var_binds:
            return var_binds[0][1].prettyPrint()
        return None

    async def _on_walk(self, oid_base, ip):
        """Async coroutine: SNMP WALK, return {oid: value}."""
        results = {}
        it = next_cmd(
            CommunityData(self.community),
            self._transport(ip),
            ContextData(),
            ObjectType(ObjectIdentity(oid_base)),
            lexicographicMode=False,
        )
        async for error_indication, error_status, error_index, var_binds in it:
            if error_indication or error_status:
                break
            for name, val in var_binds:
                results[name.prettyPrint()] = val.prettyPrint()
        return results

    # ── Low-level primitives (never raise) ──────────────────────
    def snmp_get(self, oid, ip):
        """Return the value of a single OID as str, or None on any failure."""
        try:
            return asyncio.run(self._on_get(oid, ip))
        except Exception:
            return None

    def snmp_walk(self, oid_base, ip):
        """Return {oid: value} for a walk under oid_base, or {} on any failure."""
        try:
            return asyncio.run(self._on_walk(oid_base, ip))
        except Exception:
            return {}

    # ── Optical (GPON ONT ST) ───────────────────────────────────
    def get_olt_rx(self, ip, onu_index=1):
        """Return ONU RX optical power (dBm) or None."""
        return self.snmp_get("%s.%s" % (self.oid_onu_rx, onu_index), ip)

    def get_olt_tx(self, ip, onu_index=1):
        """Return ONU TX optical power (dBm) or None."""
        return self.snmp_get("%s.%s" % (self.oid_onu_tx, onu_index), ip)

    def get_optical_signal(self, ip, onu_index=1):
        """Return RX/TX optical dict, or status offline/timeout."""
        rx = self.get_olt_rx(ip, onu_index)
        tx = self.get_olt_tx(ip, onu_index)
        if rx is None and tx is None:
            return {"ip": ip, "status": "offline/timeout"}
        return {
            "ip": ip,
            "rx_dbm": rx,
            "tx_dbm": tx,
            "type": "optical",
            "status": "good",
        }

    # ── Wireless (Ubiquiti / MikroTik) ──────────────────────────
    def get_wireless_signal(self, ip):
        """Return signal strength + CCQ dict, or status offline/timeout."""
        data = self.snmp_walk(self.oid_ubnt_signal, ip)
        if not data:
            data = self.snmp_walk(self.oid_mikrotik_signal, ip)
        if not data:
            return {"ip": ip, "status": "offline/timeout"}

        values = list(data.values())
        signal = values[0] if values else None

        ccq = None
        ccq_data = self.snmp_walk(self.oid_ubnt_ccq, ip)
        if ccq_data:
            ccq = list(ccq_data.values())[0]

        return {
            "ip": ip,
            "signal_dbm": signal,
            "ccq": ccq,
            "type": "wireless",
            "status": "good",
        }

    # ── Unified entry point ─────────────────────────────────────
    def get_client_signal(self, ip, device_type="auto"):
        """Return a signal dict for ip. device_type: auto/optical/wireless."""
        if device_type == "optical":
            return self.get_optical_signal(ip)
        if device_type == "wireless":
            return self.get_wireless_signal(ip)

        # auto: try optical first, fall back to wireless
        optical = self.get_optical_signal(ip)
        if optical.get("status") == "good":
            return optical
        return self.get_wireless_signal(ip)