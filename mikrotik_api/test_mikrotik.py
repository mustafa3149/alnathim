"""Verify the MikroTik connection and print PPP secrets + active sessions.

Run:  cd mikrotik_api && python -m test_mikrotik [--show-passwords]
"""
import json
import sys

from config import load_mikrotik_config
from mikrotik_manager import MikroTikManager


def mask(secret_rows, show_passwords):
    out = []
    for row in secret_rows:
        r = dict(row)
        if "password" in r and not show_passwords:
            r["password"] = "*****"
        out.append(r)
    return out


def main():
    cfg = load_mikrotik_config()
    show_passwords = "--show-passwords" in sys.argv

    if not cfg["host"]:
        print("ERROR: MIKROTIK_HOST is not set. Copy .env.example to .env and fill it.")
        return 1

    print("Connecting to %s:%s as %s ..." % (cfg["host"], cfg["port"], cfg["username"]))
    try:
        with MikroTikManager(cfg["host"], cfg["username"], cfg["password"], port=cfg["port"]) as m:
            secrets = m.get_ppp_secrets()
            active = m.get_ppp_active()
            clients = m.get_active_clients_with_ip()

            print("\n=== /ppp secret (%d users) ===" % len(secrets))
            print(json.dumps(mask(secrets, show_passwords), ensure_ascii=False, indent=2))

            print("\n=== /ppp active (%d sessions, raw) ===" % len(active))
            print(json.dumps(active, ensure_ascii=False, indent=2))

            print("\n=== Active clients with IP (normalized) ===")
            print(json.dumps(clients, ensure_ascii=False, indent=2))
    except Exception as e:
        print("CONNECTION FAILED: %s" % e)
        return 1

    print("\nOK: MikroTik connection verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())