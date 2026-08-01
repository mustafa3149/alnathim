"""LAN Signal Scanner (Phase 14.6).

Runs on a PC inside the operator's LAN (the only place SNMP can reach the
CPEs). Each cycle it:
  1. loads all active customers that have an IP,
  2. probes them CONCURRENTLY with the existing SignalMonitor,
  3. upserts the whole batch into the local SQLite signal_cache,
  4. sends ONE batched POST to RELAY_URL (Render) when configured.

Guardrails (as required):
  - ONE HTTP POST per cycle (never per subscriber).
  - configurable SCAN_INTERVAL_MINUTES (default 3), not a tight loop.
  - ThreadPoolExecutor(SCAN_THREADS) so one slow Nano doesn't stall the batch.
  - a failed device is recorded as offline in the batch — never blocks others.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import database as db
from config import AGENT_TOKEN, RELAY_URL, SCAN_INTERVAL_MINUTES, SCAN_THREADS
from .signal_monitor import SignalMonitor  # same package (snmp_monitor)

log = logging.getLogger("signal_scanner")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _targets():
    """Return [(ip, device_type)] for all active customers that have an IP.

    list_active_customers() returns sqlite3.Row objects — access by key
    (row["nano_ip"], row["device_type"]), never .get().
    """
    out = []
    try:
        for c in db.list_active_customers():
            ip = str(c["nano_ip"] or "").strip()
            if not ip:
                continue
            dt = str(c["device_type"] or "").strip()
            if dt == "نانو":
                device = "wireless"
            elif dt == "كيبل ضوئي":
                device = "optical"
            else:
                device = "auto"
            out.append((ip, device))
    except Exception as e:  # noqa: BLE001 — the loop must survive
        log.error("targets(): %s", e)
    return out


def _probe(monitor, ip, device):
    """Probe one device and normalize into a cache/relay row."""
    try:
        result = monitor.get_client_signal(ip, device)
    except Exception as e:  # noqa: BLE001
        log.warning("probe %s failed: %s", ip, e)
        return {"ip": ip, "status": "offline"}
    row = {"ip": ip, "status": result.get("status", "offline")}
    if result.get("type") == "optical":
        row["rx_dbm"] = result.get("rx_dbm")
        row["tx_dbm"] = result.get("tx_dbm")
        row["signal_dbm"] = result.get("signal_dbm")
    else:
        row["signal_dbm"] = result.get("signal_dbm")
        row["ccq"] = result.get("ccq")
    return row


def run_once(relay_url=RELAY_URL):
    """Run one full scan cycle: probe, cache locally, relay one batch."""
    monitor = SignalMonitor()
    targets = _targets()
    log.info("scanning %d subscribers", len(targets))
    if not targets:
        log.info("no targets — skipping cycle")
        return

    batch = []
    workers = max(1, int(SCAN_THREADS or 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_probe, monitor, ip, dev): ip for ip, dev in targets}
        for fut in as_completed(futures):
            try:
                batch.append(fut.result())
            except Exception as e:  # noqa: BLE001
                log.warning("probe worker error: %s", e)
                batch.append({"ip": futures[fut], "status": "offline"})

    # Local cache (tower PC + also used by /signal-board on the same machine).
    try:
        db.upsert_signal_batch(batch)
        log.info("cached %d rows locally", len(batch))
    except Exception as e:  # noqa: BLE001
        log.error("local cache failed: %s", e)

    # One batched relay POST per cycle — never per subscriber.
    if not relay_url:
        log.info("relay disabled (RELAY_URL empty) — done")
        return
    try:
        import urllib.request

        payload = json.dumps({"batch": batch}).encode("utf-8")
        req = urllib.request.Request(
            relay_url.rstrip("/") + "/api/agent/signal",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + AGENT_TOKEN,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
        log.info("relay %s -> %s %s", relay_url, resp.status, body[:120])
    except Exception as e:  # noqa: BLE001
        log.error("relay failed: %s", e)


def main():
    """Run the scanner forever at SCAN_INTERVAL_MINUTES intervals."""
    interval = max(1, int(SCAN_INTERVAL_MINUTES or 3))
    log.info(
        "signal scanner started (interval=%sm, threads=%s, relay=%s)",
        interval, SCAN_THREADS, RELAY_URL or "off",
    )
    while True:
        started = time.monotonic()
        try:
            run_once()
        except Exception as e:  # noqa: BLE001 — never die
            log.exception("cycle error: %s", e)
        elapsed = time.monotonic() - started
        sleep = max(0.0, interval * 60 - elapsed)
        log.info("cycle took %.1fs — next scan in %.0fs", elapsed, sleep)
        time.sleep(sleep)


if __name__ == "__main__":
    main()