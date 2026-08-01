"""Signal Agent CLI — run the LAN scanner (Phase 14.6).

Usage:
    python -m snmp_monitor.agent            # loop every SCAN_INTERVAL_MINUTES
    python -m snmp_monitor.agent --once     # run a single scan cycle and exit
    python -m snmp_monitor.agent --relay https://your-app.onrender.com  # override RELAY_URL

The scanner must run on a PC inside the operator's LAN (where CPEs are
reachable). It caches locally and relays ONE batched POST per cycle.
"""

import argparse
import sys


def main(argv=None):
    """Parse args and run the scanner once or as a loop."""
    parser = argparse.ArgumentParser(description="Al-Nathim signal scanner agent")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--relay", default=None, help="override RELAY_URL")
    args = parser.parse_args(argv)

    sys.path.insert(0, ".")
    from snmp_monitor.signal_scanner import run_once, main as loop_main

    if args.relay:
        import os

        os.environ["RELAY_URL"] = args.relay

    if args.once:
        run_once()
    else:
        loop_main()


if __name__ == "__main__":
    main()