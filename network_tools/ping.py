"""Live Ping tool — executes a real ICMP ping and returns latency/packet loss.

Uses the platform-appropriate `ping` binary via subprocess argument arrays
(no shell), so it is safe against command injection. Never raises — always
returns a dict describing the outcome.
"""

import logging
import re
import subprocess
import sys
import time

log = logging.getLogger(__name__)

# Host validation: IP v4/v6 or hostname (letters, digits, dots, dashes, colon, underscore).
HOST_RE = re.compile(r"^[A-Za-z0-9.\-_:]+$")

# On Windows the desktop app is a windowed (GUI) EXE, so spawning the console
# `ping` binary without this flag flashes a black command window. Suppress it.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0


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


def is_private_ip(host):
    """Return True when host looks like a private/LAN IPv4 (RFC1918 etc.).

    Private ranges (10.x, 192.168.x, 172.16-31.x) are only reachable from
    inside the operator's LAN. A public server (e.g. Render) cannot reach
    them, so the UI uses this flag to explain why a device check fails
    instead of showing a generic offline/timeout message.

    Args:
        host: IP or hostname to classify.

    Returns:
        True when host is a private IPv4/IPv6 address; False for hostnames.
    """
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


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


def _tcp_probe(host, count=3, timeout=5):
    """Fallback probe when the system `ping` binary is unavailable.

    Opens a TCP connection to port 80 (or 443) to determine reachability —
    a meaningful liveness check in containers like Render where ICMP and raw
    sockets are restricted.

    Args:
        host: validated host/IP.
        count: number of attempts (1-3).
        timeout: per-attempt timeout in seconds.

    Returns:
        dict result with ok/error/latencies; ok=True when a TCP connect succeeds.
    """
    import socket

    latencies = []
    last_error = "فشل فتح اتصال TCP"
    for _ in range(max(1, min(int(count or 3), 5))):
        for port in (80, 443):
            try:
                start = time.monotonic()
                with socket.create_connection((host, port), timeout=timeout):
                    latencies.append(round((time.monotonic() - start) * 1000, 2))
                    break
            except Exception as e:  # noqa: BLE001
                last_error = f"فشل الاتصال بمنفذ {port}: {e}"
            else:
                break
    if not latencies:
        return {
            "ok": False, "host": host,
            "error": "لا يوجد استجابة — الجهاز غير متصل",
            "latencies_ms": [],
        }
    return {
        "ok": True, "host": host, "is_tcp_probe": True,
        "sent": len(latencies), "received": len(latencies),
        "loss_percent": 0.0, "latencies_ms": latencies,
        "min_ms": round(min(latencies), 2),
        "avg_ms": round(sum(latencies) / len(latencies), 2),
        "max_ms": round(max(latencies), 2),
    }


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
    log.info("PING host=%s count=%d cmd=%s", host, count, cmd)
    try:
        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            creationflags=_NO_WINDOW,
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
        # `ping` binary missing (e.g. Render minimal container) — fall back to
        # a TCP probe so the feature keeps working and returns a meaningful result.
        return _tcp_probe(host, count=count)
    except Exception as e:  # noqa: BLE001 — diagnostics must never crash
        return {
            "ok": False, "host": host,
            "error": f"فشل الفحص: {e}",
            "latencies_ms": [],
        }
