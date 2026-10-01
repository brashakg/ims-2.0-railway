"""
TechCherry → IMS stock importer.

Reads the broken-HTML "ItemStockWithBarcodeList.xls" TechCherry exports
(it's actually HTML with malformed table structure — `</tr>` closes rows
that never opened with `<tr>`), parses every row, and POSTs to the IMS
import endpoint in batches.

Run from repo root:
    python scripts/techcherry_import_stock.py path/to/file.xls
"""

import os
import re
import sys
import time

import requests

IMS_BASE = "https://ims-20-railway-production.up.railway.app"
LOGIN_URL = f"{IMS_BASE}/api/v1/auth/login"
IMPORT_URL = f"{IMS_BASE}/api/v1/admin/techcherry/import"
STATUS_URL = f"{IMS_BASE}/api/v1/admin/techcherry/status"
STORE_ID = "BV-PUN-01"
BATCH_SIZE = 500

# Stock column order (per the file's header):
COLUMN_NAMES = [
    "SNo", "Prod Name", "Prod Grp", "Unit", "HSN",
    "Stock In_Hand", "Stock Value", "Barcode", "Qty",
    "Pur Prc", "Ttl Pur Val", "Sale Prc", "Ttl Sale Val", "eCom",
]

# Match a single <td> ... </td> tolerating attributes and whitespace.
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")  # strip stray tags inside cells
WS_RE = re.compile(r"\s+")


def clean(s: str) -> str:
    s = TAG_RE.sub(" ", s)
    return WS_RE.sub(" ", s).replace("&nbsp;", " ").strip()


def parse_techcherry_stock(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()
    # Split on </tr> — each chunk before it is one row of <td>s (plus header noise).
    chunks = html.split("</tr>")
    rows = []
    for chunk in chunks:
        cells = [clean(c) for c in TD_RE.findall(chunk)]
        # Stock rows have exactly 13 or 14 td cells (SNo + 12-13 fields).
        # Skip the "Better Vision Pune" label row which has 1 cell.
        if len(cells) < 13:
            continue
        if not cells[0].isdigit():  # SNo must be a number
            continue
        row = dict(zip(COLUMN_NAMES, cells))
        # Drop rows that have neither name nor a valid barcode
        if not row.get("Prod Name") and row.get("Barcode", "").upper() in ("", "NA"):
            continue
        rows.append(row)
    return rows


def login():
    r = requests.post(
        LOGIN_URL,
        json={"username": "admin", "password": "admin123"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def push_batch(token: str, batch):
    h = {"Authorization": f"Bearer {token}"}
    body = {
        "type": "products",
        "store_id": STORE_ID,
        "rows": batch,
        "overwrite": True,
    }
    r = requests.post(IMPORT_URL, headers=h, json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def main():
    if len(sys.argv) < 2:
        print("usage: techcherry_import_stock.py <path-to-xls>", file=sys.stderr)
        sys.exit(2)
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"file not found: {path}", file=sys.stderr)
        sys.exit(2)

    print(f"Parsing {path} ...")
    rows = parse_techcherry_stock(path)
    print(f"Parsed {len(rows)} usable stock rows")
    if not rows:
        print("Nothing to import.")
        return

    # Show a few samples so we know the parser worked
    for r in rows[:2]:
        print("  sample:", {k: r.get(k) for k in ("Prod Name", "Barcode", "Sale Prc", "Stock In_Hand", "Prod Grp")})

    print()
    print("Logging in to IMS...")
    token = login()
    print(f"Token len={len(token)}")

    print(f"Pushing {len(rows)} rows in batches of {BATCH_SIZE}...")
    totals = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    start = time.time()
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        try:
            resp = push_batch(token, batch)
            totals["inserted"] += resp.get("inserted", 0)
            totals["updated"] += resp.get("updated", 0)
            totals["skipped"] += resp.get("skipped", 0)
            totals["errors"] += len(resp.get("errors", []))
            elapsed = time.time() - start
            print(
                f"  batch {i // BATCH_SIZE + 1}: "
                f"+{resp.get('inserted', 0)} inserted, "
                f"~{resp.get('updated', 0)} updated, "
                f"errors={len(resp.get('errors', []))} "
                f"({elapsed:.1f}s elapsed, running totals: {totals})"
            )
        except requests.HTTPError as e:
            print(f"  batch {i // BATCH_SIZE + 1} FAILED: {e}  body={e.response.text[:200]}")
            totals["errors"] += len(batch)
        except Exception as e:
            print(f"  batch {i // BATCH_SIZE + 1} ERROR: {type(e).__name__}: {e}")
            totals["errors"] += len(batch)

    print()
    print(f"Done in {time.time() - start:.1f}s")
    print(f"Totals: {totals}")

    print()
    print("Final IMS status:")
    r = requests.get(STATUS_URL, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    print(r.json())


if __name__ == "__main__":
    main()
