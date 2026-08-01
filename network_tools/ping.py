"""Live Ping tool — executes a real ICMP ping and returns latency/packet loss.

Uses the platform-appropriate `ping` binary via subprocess argument arrays
(no shell), so it is safe against command injection. Never raises — always
returns a dict describing the outcome.
"""

import re
import subprocess
import sys
import time

# Host validation: IP v4/v6 or hostname (letters, digits, dots, dashes, colon, underscore).
HOST_RE = re.compile(r"^[A-Za-z0-9.\-_:]+$")

# Windows: "Reply from 8.8.8.8: bytes=32 time=12ms TTL=116"
# Linux:   "64 bytes from 8.8.8.8: icmp_seq=1 ttl=116 time=12.3 ms"
_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)


def validate_host(host):
    """Return True when host is a plausible IP/hostname string (no shell chars)."""
    if not host or not HOST_RE.match(host):
        return False
    if len(host) > 255:
        return False
    return True


def _pick_command(host, count):
    """Return a subprocess arg list for the current platform.

    Args:
        host: validated host/IP to ping.
        count: number of ICMP packets to send.

    Returns:
        list of command-line arguments (safe, no shell).
    """
    if sys.platform.startswith("win"):
        return ["ping", "-n", str(count), host]
    return ["ping", "-c", str(count), "-W", "2", host]


def _parse_output(text):
    """Parse ping stdout into dict with min/avg/max/loss.

    Args:
        text: raw stdout from the ping binary.

    Returns:
        dict with 'sent', 'received', 'loss_percent', 'latencies_ms'
        (list of floats) and derived 'min_ms'/'avg_ms'/'max_ms'.
    """
    times = [float(m) for m in _TIME_RE.findall(text)]

    sent = received = 0
    # Windows: "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)"
    # Linux:   "4 packets transmitted, 4 received, 0% packet loss"
    m = re.search(r"Sent\s*=\s*(\d+)[^\d]*Received\s*=\s*(\d+)", text, re.IGNORECASE)
    if m:
        sent = int(m.group(1))
        received = int(m.group(2))
    else:
        m = re.search(r"(\d+)\s+packets transmitted,\s*(\d+)\s+received", text, re.IGNORECASE)
        if m:
            sent = int(m.group(1))
            received = int(m.group(2))

    if sent == 0:
        sent = len(times) or 1  # fallback: at least one response observed

    loss = round((sent - received) / sent * 100, 2) if sent else 100.0
    result = {
        "sent": sent,
        "received": received,
        "loss_percent": loss,
        "latencies_ms": times,
    }
    if times:
        result["min_ms"] = round(min(times), 2)
        result["avg_ms"] = round(sum(times) / len(times), 2)
        result["max_ms"] = round(max(times), 2)
    else:
        result["min_ms"] = None
        result["avg_ms"] = None
        result["max_ms"] = None
    return result


def ping_host(host, count=4, timeout=15):
    """Run a real ICMP ping and return a result dict.

    Args:
        host: IP address or hostname to ping.
        count: number of packets (1-10, clamped).
        timeout: total subprocess timeout in seconds.

    Returns:
        dict: {
            ok (bool), host,
            error (str, when ok=False),
            sent, received, loss_percent,
            min_ms, avg_ms, max_ms, latencies_ms
        }
    """
    if not validate_host(host):
        return {
            "ok": False, "host": host,
            "error": "عنوان غير صالح. استخدم IP أو اسم مضيف فقط",
            "latencies_ms": [],
        }

    count = max(1, min(int(count or 4), 10))
    cmd = _pick_command(host, count)
    try:
        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        elapsed = round(time.monotonic() - start, 2)
        result = _parse_output(proc.stdout or proc.stderr or "")
        result.update({"ok": True, "host": host, "elapsed_sec": elapsed})
        if result["received"] == 0:
            result["ok"] = False
            result["error"] = "لا يوجد استجابة — الجهاز غير متصل"
        return result
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "host": host,
            "error": "انتهت مهلة الفحص — الجهاز لا يستجيب",
            "latencies_ms": [],
        }
    except FileNotFoundError:
        return {
            "ok": False, "host": host,
            "error": "أداة ping غير مثبتة على هذا النظام",
            "latencies_ms": [],
        }
    except Exception as e:  # noqa: BLE001 — diagnostics must never crash
        return {
            "ok": False, "host": host,
            "error": f"فشل الفحص: {e}",
            "latencies_ms": [],
        }