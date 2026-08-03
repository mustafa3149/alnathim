"""
Turso DB Adapter — sqlite3-compatible connection for Turso (hosted libSQL).
============================================================================
A drop-in replacement for the subset of the Python `sqlite3` API that
`database.py` uses, backed by Turso's documented SQL-over-HTTP endpoint:

    POST https://<db>-<org>.turso.io/v2/pipeline
    Authorization: Bearer <token>

Why HTTP + stdlib instead of the libsql-experimental package?
  - libsql-experimental ships as a Rust extension with NO Windows wheel; pip
    tries to compile from source and fails without a Rust toolchain.
  - urllib/json are stdlib -> works in the PyInstaller desktop EXE and on
    Render with zero extra packages.
  - The SQL dialect is SQLite, so every query in database.py (`?` placeholders,
    AUTOINCREMENT, PRAGMA, INSERT OR IGNORE, ON CONFLICT ... excluded.*,
    datetime('now','localtime')) runs unchanged.

Only enabled when config.USE_TURSO is True (TURSO_DATABASE_URL and
TURSO_AUTH_TOKEN are both set). Otherwise database.py keeps using the local
SQLite file, so the desktop EXE works fully offline.
"""

import base64
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime

# Raised errors subclass the sqlite3 errors so the existing
# `except sqlite3.OperationalError` / `except sqlite3.IntegrityError`
# handlers in database.py keep working unchanged.
class TursoOperationalError(sqlite3.OperationalError):
    """Transport or server-side error (caught as sqlite3.OperationalError)."""


class TursoIntegrityError(sqlite3.IntegrityError):
    """UNIQUE / CHECK / FOREIGN KEY constraint violation."""


class TursoProgrammingError(sqlite3.ProgrammingError):
    """Misuse of the adapter (e.g. operating on a closed connection)."""


# ── Value encoding / decoding ─────────────────────────────────

def _encode_value(value):
    """Encode a Python value into the Turso HTTP cell shape."""
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": str(value)}
    if isinstance(value, (datetime, date)):
        return {"type": "text", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"type": "blob", "value": base64.b64encode(value).decode("ascii")}
    return {"type": "text", "value": str(value)}


def _decode_value(cell):
    """Turn a Turso HTTP cell ({type, value} or null) into a Python value."""
    if cell is None:
        return None
    vtype = cell.get("type") if isinstance(cell, dict) else "text"
    raw = cell.get("value") if isinstance(cell, dict) else cell
    if vtype == "null" or raw is None:
        return None
    if vtype == "integer":
        return int(raw)
    if vtype == "float":
        return float(raw)
    if vtype == "blob":
        try:
            return base64.b64decode(raw)
        except (TypeError, ValueError):
            return raw
    return raw  # text


def _params_to_list(parameters):
    """Normalise sqlite3-style parameters to a list."""
    if parameters is None:
        return []
    if isinstance(parameters, (list, tuple)):
        return list(parameters)
    return [parameters]


def _to_https(url):
    """Convert a libsql:// or plain URL to the https base URL."""
    url = (url or "").strip()
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    if not url.startswith("https://"):
        return "https://" + url
    return url


def _split_sql_script(script):
    """Split a SQL script on ';' that appears outside string/identifier quotes."""
    statements = []
    current = []
    in_single = False
    in_double = False
    for ch in script:
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == ";" and not in_single and not in_double:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _error_from_message(code, message):
    """Map a Turso error to the right sqlite3-compatible exception."""
    text = f"{code or ''} {message or ''}".lower()
    if "constraint" in text:
        return TursoIntegrityError(message or code or "constraint violation")
    return TursoOperationalError(message or code or "unknown Turso error")


# ── Result / Cursor ──────────────────────────────────────────

class Row:
    """sqlite3.Row-compatible row (name AND integer indexing, .keys(), dict())."""

    __slots__ = ("_keys", "_values", "_map")

    def __init__(self, keys, values):
        self._keys = tuple(keys)
        self._values = tuple(values)
        self._map = {k: i for i, k in enumerate(self._keys)}

    def keys(self):
        return self._keys

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._map[key]]

    def __len__(self):
        return len(self._keys)

    def __iter__(self):
        return iter(self._values)

    def __contains__(self, key):
        return key in self._map

    def __repr__(self):
        body = ", ".join(f"{k}={v!r}" for k, v in zip(self._keys, self._values))
        return f"<Row {body}>"


class TursoResult:
    """A decoded single-execute result from the pipeline response."""

    __slots__ = ("rows", "affected_row_count", "last_insert_rowid", "col_names")

    def __init__(self, raw):
        cols = raw.get("cols") or []
        self.col_names = [c.get("name") if isinstance(c, dict) else c for c in cols]
        raw_rows = raw.get("rows") or []
        self.rows = [
            Row(self.col_names, tuple(_decode_value(cell) for cell in row))
            for row in raw_rows
        ]
        self.affected_row_count = raw.get("affected_row_count", 0)
        lid = raw.get("last_insert_rowid")
        self.last_insert_rowid = int(lid) if lid not in (None, "") else None

    @property
    def description(self):
        if not self.col_names:
            return None
        return tuple((name, None, None, None, None, None, None) for name in self.col_names)


class TursoCursor:
    """sqlite3.Cursor-compatible view over one or more pipeline results."""

    def __init__(self, results=None):
        self._results = results or []
        first = self._results[0] if self._results else None
        self.rowcount = first.affected_row_count if first else -1
        self.lastrowid = first.last_insert_rowid if first else None
        self.description = first.description if first else None
        self._rows = first.rows if first else []
        self._pos = 0
        self.arraysize = 1

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        out = self._rows[self._pos:]
        self._pos = len(self._rows)
        return out

    def fetchmany(self, size=None):
        size = size if size is not None else self.arraysize
        out = self._rows[self._pos:self._pos + size]
        self._pos += len(out)
        return out

    def close(self):
        self._rows = []


# ── Connection ────────────────────────────────────────────────

class TursoConnection:
    """Mimics sqlite3.Connection for the subset used by database.py."""

    _TRANSACTION_CONTROL = {
        "BEGIN", "COMMIT", "ROLLBACK", "END",
        "BEGIN TRANSACTION", "BEGIN IMMEDIATE", "BEGIN EXCLUSIVE",
        "COMMIT TRANSACTION", "ROLLBACK TRANSACTION", "END TRANSACTION",
    }

    def __init__(self, url, auth_token, timeout=45):
        self._url = _to_https(url)
        self._token = auth_token
        self._timeout = timeout
        self._closed = False
        # Accepted for sqlite3 compatibility; rows are always sqlite3.Row.
        self.row_factory = None
        self.total_changes = 0

    # ---- internals ----

    def _post(self, statements):
        """Send (sql, args) statements in one pipeline; return [TursoResult].

        Each statement is executed in order inside one server connection.
        """
        requests = []
        for sql, args in statements:
            stmt = {"sql": sql}
            if args:
                stmt["args"] = [_encode_value(v) for v in args]
            requests.append({"type": "execute", "stmt": stmt})
        requests.append({"type": "close"})
        payload = json.dumps({"requests": requests}).encode("utf-8")
        req = urllib.request.Request(
            self._url + "/v2/pipeline",
            data=payload,
            method="POST",
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
                "User-Agent": "alnathim-turso-adapter/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise TursoOperationalError(f"Turso HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TursoOperationalError(
                f"Turso connection failed: {exc.reason}"
            ) from exc

        results = []
        for item in data.get("results", []):
            if item.get("type") == "error":
                err = item.get("error") or {}
                raise _error_from_message(err.get("code", ""), err.get("message", ""))
            resp_item = item.get("response") or {}
            if resp_item.get("type") == "execute":
                results.append(TursoResult(resp_item.get("result") or {}))
        return results


    # ---- sqlite3-like public API ----

    def execute(self, sql, parameters=()):
        self._check_open()
        sql = sql.strip()
        if sql.upper() in self._TRANSACTION_CONTROL or sql.upper().startswith("PRAGMA foreign_keys"):
            return TursoCursor()  # no-op: each pipeline call is already atomic
        results = self._post([(sql, _params_to_list(parameters))])
        return TursoCursor(results)

    def executemany(self, sql, seq_of_parameters):
        self._check_open()
        sql = sql.strip()
        statements = [(sql, _params_to_list(p)) for p in (seq_of_parameters or [])]
        if not statements:
            return TursoCursor()
        results = self._post(statements)
        return TursoCursor(results)

    def executescript(self, script):
        self._check_open()
        statements = [s for s in _split_sql_script(script) if s]
        if not statements:
            return TursoCursor()
        results = self._post([(s, ()) for s in statements])
        return TursoCursor(results)

    def commit(self):
        """No-op: every pipeline execute is auto-committed server-side."""
        self._check_open()
        return None

    def rollback(self):
        """No-op: there is no open transaction to roll back over HTTP."""
        self._check_open()
        return None

    def close(self):
        self._closed = True

    def cursor(self):
        self._check_open()
        return TursoCursor()

    def _check_open(self):
        if self._closed:
            raise TursoProgrammingError("Cannot operate on a closed Turso connection.")


def connect(url, auth_token):
    """Return a TursoConnection (mirrors sqlite3.connect(url))."""
    return TursoConnection(url, auth_token)


def dump_database(url, auth_token, timeout=60):
    """Fetch the full SQL dump of the database (used for admin backups).

    Returns the dump text, or None if the endpoint is unavailable.
    """
    base = _to_https(url)
    req = urllib.request.Request(
        base + "/dump",
        headers={
            "Authorization": "Bearer " + auth_token,
            "User-Agent": "alnathim-turso-adapter/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError):
        return None
