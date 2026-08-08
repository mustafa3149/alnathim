#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fat_import.py — Legacy subscriber extraction → الناظم import bundle.

Reads the legacy system's exported subscriber file (CSV / TSV / plain text)
and produces `import_bundle.json` ready to be POSTed to the API endpoint:

    POST /api/mobile/v1/import/customers      (admin)
    body: {"rows": [ {full_name, phone, mikrotik_username,
                      mikrotik_password, fat_number, port_number}, ... ]}

Usage (local PC, Windows):
    python fat_import.py path\\to\\export.csv [--delimiter ,] [--output out.json]

The legacy export is usually a simple CSV with these columns (headers are
detected automatically):
    Name | Username | Password | Phone? | FAT? | Port?

Examples:
    python fat_import.py subscribers_export.csv
    python fat_import.py "C:\\الموقع\\export.txt" --delimiter "\\t"
    python fat_import.py export.csv --output bundle.json

If a column is missing it is skipped; rows without a name are reported.
Matching is done later by the server (mikrotik_username first, then full_name).
"""

import argparse
import csv
import json
import os
import re
import sys

# Header aliases (lowercased, punctuation stripped) -> canonical key.
HEADER_ALIASES = {
    "name": "full_name", "fullname": "full_name", "full_name": "full_name",
    "الاسم": "full_name", "اسم": "full_name",
    "username": "mikrotik_username", "user": "mikrotik_username",
    "اليوزر": "mikrotik_username", "يوزر": "mikrotik_username",
    "mikrotik_username": "mikrotik_username", "login": "mikrotik_username",
    "password": "mikrotik_password", "pass": "mikrotik_password",
    "الباسورد": "mikrotik_password", "باسورد": "mikrotik_password",
    "كلمة المرور": "mikrotik_password", "passwd": "mikrotik_password",
    "phone": "phone", "mobile": "phone", "tel": "phone", "الهاتف": "phone",
    "رقم الهاتف": "phone", "فاتورة": "phone", "جوال": "phone",
    "fat": "fat_number", "fat_number": "fat_number", "cabinet": "fat_number",
    "cab": "fat_number", "كابينة": "fat_number", "الكابينة": "fat_number",
    "port": "port_number", "port_number": "port_number", "منفذ": "port_number",
    "المنفذ": "port_number",
}

_ALIAS_KEYS = {k.lower().replace(" ", "").replace("_", ""): v for k, v in HEADER_ALIASES.items()}


def _norm_header(text):
    """Normalize a header cell to an alias lookup key."""
    return re.sub(r"[^0-9a-z\u0600-\u06ff]", "", str(text).lower())


def _sniff_delimiter(path):
    """Pick the delimiter by scanning the first data lines."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
    candidates = ["\t", ";", ","]
    for d in candidates:
        lines = [ln for ln in sample.splitlines() if ln.strip()]
        if lines and d in lines[0]:
            return d
    return ","


def read_rows(path, delimiter=None):
    """Read the export file into a list of raw row dicts (headers mapped)."""
    if delimiter is None:
        delimiter = _sniff_delimiter(path)
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        try:
            reader = csv.reader(f, delimiter=delimiter)
            table = [row for row in reader if any(cell.strip() for cell in row)]
        except csv.Error:
            table = []

    if not table:
        raise SystemExit(f"لا يوجد محتوى قابل للقراءة في: {path}")

    # Find a header row: a row whose cells mostly map to known aliases.
    header_idx = None
    for i, row in enumerate(table[:10]):
        mapped = [_ALIAS_KEYS.get(_norm_header(c)) for c in row]
        if sum(1 for m in mapped if m) >= max(1, len(row) // 2):
            header_idx = i
            break

    if header_idx is None:
        # No headers — assume positional: Name, Username, Password, Phone, FAT, Port
        header_idx = 0
        table.insert(0, ["Name", "Username", "Password", "Phone", "FAT", "Port"])

    headers = [
        _ALIAS_KEYS.get(_norm_header(c), "")
        for c in table[header_idx]
    ]
    rows = []
    for row in table[header_idx + 1:]:
        item = {}
        for col, cell in zip(headers, row):
            if col:
                item.setdefault(col, cell.strip())
        if item:
            rows.append(item)
    return rows, delimiter


def main():
    parser = argparse.ArgumentParser(description="Legacy subscribers -> الناظم import bundle")
    parser.add_argument("input", help="مسار ملف التصدير (CSV/TSV/TXT)")
    parser.add_argument("--delimiter", default=None, help="فاصل الحقول (افتراضي: تلقائي)")
    parser.add_argument("--output", default="import_bundle.json", help="ملف الناتج")
    parser.add_argument("--server", default="", help="عنوان السيرفر (إن أردت رفع الملف مباشرة)")
    parser.add_argument("--token", default="", help="توكن المدير للرفع المباشر")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        raise SystemExit(f"الملف غير موجود: {args.input}")

    rows, delimiter = read_rows(args.input, args.delimiter)
    clean = []
    skipped = 0
    for r in rows:
        name = r.get("full_name", "")
        if not name:
            skipped += 1
            continue
        clean.append({
            "full_name": name,
            "phone": r.get("phone", ""),
            "mikrotik_username": r.get("mikrotik_username", ""),
            "mikrotik_password": r.get("mikrotik_password", ""),
            "fat_number": r.get("fat_number", ""),
            "port_number": r.get("port_number", ""),
        })

    bundle = {"rows": clean}
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

    print(f"الملف: {args.input} (فاصل: {delimiter!r})")
    print(f"مقروء: {len(rows)} صف · صالح: {len(clean)} · تجاهل (بدون اسم): {skipped}")
    print(f"الناتج: {args.output}")

    if args.server and args.token:
        _upload(args.server, args.token, clean)


def _upload(server, token, rows):
    """Upload the bundle straight to the API (optional)."""
    import urllib.request

    url = server.rstrip("/") + "/api/mobile/v1/import/customers"
    data = json.dumps({"rows": rows}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print("الرفع:", resp.status, resp.read().decode("utf-8"))
    except Exception as e:
        print("فشل الرفع:", e)


if __name__ == "__main__":
    sys.exit(main())
