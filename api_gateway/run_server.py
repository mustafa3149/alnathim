"""Start the FastAPI gateway on 0.0.0.0:8000.

Run:  py api_gateway\\run_server.py
"""
import os
import socket
import sys

_API_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_API_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

import uvicorn  # noqa: E402

HOST = "0.0.0.0"
PORT = int(os.getenv("API_PORT", "8000"))


def _port_in_use():
    """Return True when something already listens on HOST:PORT."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, PORT))
            return False
        except OSError:
            return True


def main():
    """Boot Uvicorn with the API gateway app."""
    if _port_in_use():
        print(
            "\n"
            "⚠️  المنفذ %d مستخدم بالفعل.\n"
            "ربما الخادم يعمل مسبقاً — افتح المتصفح على:\n"
            "   http://localhost:%d\n"
            "أو أغلق النافذة السابقة وأعد المحاولة.\n" % (PORT, PORT)
        )
        return 1
    uvicorn.run("api_gateway.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()