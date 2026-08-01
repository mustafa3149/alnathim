"""MikroTikManager — connect to a MikroTik router via the RouterOS API.

Phase 1 scope: connection + read-only fetches of /ppp secret and /ppp active.
"""
import routeros_api


class MikroTikManager:
    """Wrap a MikroTik RouterOS API connection and expose PPP data as dicts."""

    def __init__(
        self,
        host,
        username,
        password,
        port=8728,
        plaintext_login=True,
        ssl=False,
        ssl_verify=False,
        connect_timeout=10,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.plaintext_login = plaintext_login
        self.ssl = ssl
        self.ssl_verify = ssl_verify
        self.connect_timeout = connect_timeout
        self._api = None

    def connect(self):
        """Open the RouterOS API connection.

        `connect_timeout` is only passed when the installed routeros_api
        version supports it (0.21.0 dropped/renamed the kwarg).
        """
        kwargs = dict(
            username=self.username,
            password=self.password,
            port=self.port,
            plaintext_login=self.plaintext_login,
            use_ssl=self.ssl,
            ssl_verify=self.ssl_verify,
        )
        try:
            self._api = routeros_api.RouterOsApiPool(
                self.host, connect_timeout=self.connect_timeout, **kwargs
            )
        except TypeError:
            # Older routeros_api versions don't accept connect_timeout.
            self._api = routeros_api.RouterOsApiPool(self.host, **kwargs)
        self._api.login()
        return self

    def disconnect(self):
        """Close the API connection."""
        if self._api:
            try:
                self._api.disconnect()
            finally:
                self._api = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()
        return False

    def _get_resource(self, path):
        """Return a routeros_api resource for the given RouterOS path."""
        if not self._api:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._api.get_resource(path)

    @staticmethod
    def _as_dict(entry):
        """Convert a RouterOS API row to a plain dict."""
        return {k: v for k, v in entry.items()}

    def get_ppp_secrets(self):
        """Return all /ppp secret users as a list of dicts."""
        secrets = self._get_resource("/ppp secret")
        return [self._as_dict(e) for e in secrets.get()]

    def get_ppp_active(self):
        """Return all /ppp active sessions as a list of dicts (incl. IPs)."""
        active = self._get_resource("/ppp active")
        return [self._as_dict(e) for e in active.get()]

    def get_active_clients_with_ip(self):
        """Return active sessions normalized to JSON-friendly keys."""
        rows = self.get_ppp_active()
        result = []
        for r in rows:
            result.append(
                {
                    "name": r.get("name", ""),
                    "user": r.get("user", ""),
                    "address": r.get("address", ""),
                    "service": r.get("service", ""),
                    "uptime": r.get("uptime", ""),
                    "interface": r.get("interface", ""),
                }
            )
        return result

    def set_ppp_secret_disabled(self, username, disabled=True):
        """Enable/disable a /ppp secret by username (disabled=yes/no).

        Args:
            username: the PPP username (name field of the secret).
            disabled: True sets disabled=yes, False sets disabled=no.

        Returns:
            True if the secret was found and updated, False if no secret
            exists with that username.
        """
        secrets = self._get_resource("/ppp secret")
        flag = "yes" if disabled else "no"
        for row in secrets.get():
            if row.get("name") == username:
                secrets.set(id=row.get("id"), disabled=flag)
                return True
        return False

    def disconnect_ppp_active(self, username):
        """Disconnect all active PPP sessions for a username.

        Args:
            username: the PPP username to kick from /ppp active.

        Returns:
            The number of sessions that were removed (0 if none/nonexistent).
        """
        active = self._get_resource("/ppp active")
        removed = 0
        for row in active.get():
            if row.get("user") == username or row.get("name") == username:
                active.call("remove", {"numbers": row.get("id")})
                removed += 1
        return removed
