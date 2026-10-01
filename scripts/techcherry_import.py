"""
TechCherry → IMS unified importer.

Handles all three TechCherry XLS exports (which are HTML-disguised-as-xls
with malformed table structure — see scripts/techcherry_import_stock.py
for the original parser).

Run from repo root:
    python scripts/techcherry_import.py stock         "C:\\path\\ItemStockWithBarcodeList.xls"
    python scripts/techcherry_import.py customers     "C:\\path\\AccountMasterList.xls"
    python scripts/techcherry_import.py transactions  "C:\\path\\Sale Invoice With Item Detail.xls"
"""

import os
import re
import sys
import time
from typing import Dict, List

import requests

IMS_BASE = "https://ims-20-railway-production.up.railway.app"
LOGIN_URL = f"{IMS_BASE}/api/v1/auth/login"
IMPORT_URL = f"{IMS_BASE}/api/v1/admin/techcherry/import"
STATUS_URL = f"{IMS_BASE}/api/v1/admin/techcherry/status"
STORE_ID = "BV-PUN-01"
BATCH_SIZE = 500

# Parsing helpers
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(s: str) -> str:
    s = TAG_RE.sub(" ", s)
    return WS_RE.sub(" ", s).replace("&nbsp;", " ").strip()


def read_chunks(path: str):
    """Yield list-of-cells for each row in the malformed-HTML xls file."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()
    for chunk in html.split("</tr>"):
        cells = [clean(c) for c in TD_RE.findall(chunk)]
        if not cells:
            continue
        yield cells


# ---------- per-type parsers ----------

def parse_stock(path: str):
    """Stock columns (14): SNo, Prod Name, Prod Grp, Unit, HSN,
    Stock In_Hand, Stock Value, Barcode, Qty, Pur Prc, Ttl Pur Val,
    Sale Prc, Ttl Sale Val, eCom"""
    cols = ["SNo", "Prod Name", "Prod Grp", "Unit", "HSN",
            "Stock In_Hand", "Stock Value", "Barcode", "Qty",
            "Pur Prc", "Ttl Pur Val", "Sale Prc", "Ttl Sale Val", "eCom"]
    rows = []
    for cells in read_chunks(path):
        if len(cells) < 13 or not cells[0].isdigit():
            continue
        row = dict(zip(cols, cells))
        if not row.get("Prod Name") and row.get("Barcode", "").upper() in ("", "NA"):
            continue
        rows.append(row)
    return rows


def parse_customers(path: str):
    """Customer columns (14): SNo, Account Name, Mobile No, Gst No,
    Parent Group, Age, Address, Station, State, Acc Category,
    Opn.Bal(Dr), Opn.Bal(Cr), Edit, Delete"""
    cols = ["SNo", "Account Name", "Mobile No", "Gst No", "Parent Group",
            "Age", "Address", "Station", "State", "Acc Category",
            "Opn.Bal(Dr)", "Opn.Bal(Cr)", "Edit", "Delete"]
    seen_phones = set()
    rows = []
    for cells in read_chunks(path):
        if len(cells) < 10 or not cells[0].isdigit():
            continue
        row = dict(zip(cols, cells))
        name = (row.get("Account Name") or "").strip()
        if not name:
            continue
        # Map to TechCherry-importer's expected keys
        mapped = {
            "Name": name,
            "Mobile": row.get("Mobile No") or "",
            "GSTIN": row.get("Gst No") or "",
            "Address": row.get("Address") or "",
            "City": row.get("Station") or "",
            "OpeningBal": row.get("Opn.Bal(Dr)") or "0",
        }
        # Local dedup so we don't send 2x the same phone in one batch
        # (the endpoint also dedupes on its side via upsert).
        phone_digits = re.sub(r"\D+", "", mapped["Mobile"])
        if phone_digits and phone_digits in seen_phones:
            continue
        if phone_digits:
            seen_phones.add(phone_digits)
        rows.append(mapped)
    return rows


def parse_transactions(path: str):
    """Transaction columns (53). Each XLS row is ONE LINE ITEM of one
    invoice — multiple rows share the same Vch No. Group by Vch No to
    build IMS orders with an `items` array."""
    cols = ["SNo", "Date", "Vch No", "Party Name", "Mob.No", "State",
            "Address", "GSTIN", "Mat.Cen", "Group", "Product", "HSN Code",
            "COLOR", "Item Detail", "Barcode", "Unit", "Qty", "Price",
            "Ttl Prc", "Dis%", "Dis Amt", "Taxable", "CGST%", "CGST Amt",
            "SGST%", "SGST Amt", "IGST%", "IGST Amt", "Ttl Price",
            "GstWise Amt", "Sale Prc(Pur)", "Mrp Prc(Pur)", "Ttl Disc",
            "Sndry Amt", "Grss Amt", "Due Amt", "Booked By", "Dlvry Date",
            "Payment Detail", "Paid By Cash", "Paid By Bank", "Status",
            "Remarks", "Remarks 1", "Remarks 2", "Remarks 3",
            "Approved By", "Bill No", "Bill Dt", "Vendor", "Place Order",
            "Prescription", "View"]

    def _f(s):
        try:
            return float(str(s).replace(",", "").strip() or 0)
        except ValueError:
            return 0.0

    by_invoice: Dict[str, Dict] = {}
    for cells in read_chunks(path):
        if len(cells) < 30 or not cells[0].isdigit():
            continue
        row = dict(zip(cols, cells))
        vch = (row.get("Vch No") or "").strip()
        if not vch:
            continue
        order = by_invoice.setdefault(vch, {
            "InvoiceNo": vch,
            "Date": row.get("Date") or "",
            "CustomerName": row.get("Party Name") or "",
            "Mobile": row.get("Mob.No") or "",
            "GSTIN": row.get("GSTIN") or "",
            "items": [],
            "GrandTotal": 0.0,
            "TaxableAmount": 0.0,
            "TaxAmount": 0.0,
            "Discount": 0.0,
            "PaymentMode": "CASH" if _f(row.get("Paid By Cash")) > 0 else (
                "BANK" if _f(row.get("Paid By Bank")) > 0 else ""
            ),
            "Status": row.get("Status") or "DELIVERED",
        })
        # Accumulate line-item totals into the order envelope
        order["GrandTotal"] += _f(row.get("Ttl Price"))
        order["TaxableAmount"] += _f(row.get("Taxable"))
        order["TaxAmount"] += _f(row.get("CGST Amt")) + _f(row.get("SGST Amt")) + _f(row.get("IGST Amt"))
        order["Discount"] += _f(row.get("Dis Amt"))
        order["items"].append({
            "product_name": row.get("Product") or "",
            "barcode": row.get("Barcode") or "",
            "quantity": _f(row.get("Qty")),
            "unit_price": _f(row.get("Price")),
            "item_total": _f(row.get("Ttl Price")),
            "hsn": row.get("HSN Code") or "",
            "discount_amount": _f(row.get("Dis Amt")),
            "tax_amount": _f(row.get("CGST Amt")) + _f(row.get("SGST Amt")) + _f(row.get("IGST Amt")),
            "color": row.get("COLOR") or "",
            "detail": row.get("Item Detail") or "",
        })
    return list(by_invoice.values())


# ---------- pusher ----------

def login() -> str:
    r = requests.post(
        LOGIN_URL,
        json={"username": "admin", "password": "admin123"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def push(token: str, kind: str, rows: List[Dict]):
    h = {"Authorization": f"Bearer {token}"}
    totals = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    start = time.time()
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        body = {"type": kind, "store_id": STORE_ID, "rows": batch, "overwrite": True}
        try:
            r = requests.post(IMPORT_URL, headers=h, json=body, timeout=180)
            r.raise_for_status()
            resp = r.json()
            totals["inserted"] += resp.get("inserted", 0)
            totals["updated"] += resp.get("updated", 0)
            totals["skipped"] += resp.get("skipped", 0)
            totals["errors"] += len(resp.get("errors", []))
            elapsed = time.time() - start
            print(f"  batch {i // BATCH_SIZE + 1}: +{resp.get('inserted', 0)} ~{resp.get('updated', 0)} "
                  f"errors={len(resp.get('errors', []))} ({elapsed:.1f}s, running={totals})")
        except requests.HTTPError as e:
            body_preview = e.response.text[:300] if e.response is not None else ""
            print(f"  batch {i // BATCH_SIZE + 1} HTTP-FAIL: {e}  body={body_preview}")
            totals["errors"] += len(batch)
        except Exception as e:
            print(f"  batch {i // BATCH_SIZE + 1} ERR: {type(e).__name__}: {e}")
            totals["errors"] += len(batch)
    print(f"Done in {time.time() - start:.1f}s. Totals: {totals}")


# ---------- main ----------

PARSERS = {
    "stock": ("products", parse_stock),
    "customers": ("customers", parse_customers),
    "transactions": ("orders", parse_transactions),
}


def main():
    if len(sys.argv) < 3:
        print("usage: techcherry_import.py <stock|customers|transactions> <path-to-xls>",
              file=sys.stderr)
        sys.exit(2)
    kind_arg = sys.argv[1].lower()
    path = sys.argv[2]
    if kind_arg not in PARSERS:
        print(f"unknown kind: {kind_arg}", file=sys.stderr)
        sys.exit(2)
    if not os.path.exists(path):
        print(f"file not found: {path}", file=sys.stderr)
        sys.exit(2)

    api_kind, parser = PARSERS[kind_arg]
    print(f"Parsing {kind_arg} from {path}...")
    rows = parser(path)
    print(f"Parsed {len(rows)} {kind_arg} rows")
    if rows[:1]:
        sample = rows[0]
        preview_keys = list(sample.keys())[:8]
        print(f"  sample: {{{', '.join(f'{k!r}: {sample[k]!r}' for k in preview_keys)}}}")

    print("Logging in to IMS...")
    token = login()

    print(f"Pushing {len(rows)} {kind_arg} -> IMS type={api_kind}...")
    push(token, api_kind, rows)

    print()
    print("Migration status:")
    s = requests.get(STATUS_URL, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    print(s.json())


if __name__ == "__main__":
    main()
