"""NEW-GST-B2CS-HSN: a consumer (B2CS) invoice that mixes GST rates must be
split into per-rate B2CS buckets, not lumped under the first line's rate.

A walk-in order with a 5% frame (item_total 5250 -> taxable 5000, tax 250) and an
18% sunglass (item_total 11800 -> taxable 10000, tax 1800) must produce TWO b2cs
rows: 5% (5000 / 250) and 18% (10000 / 1800), each CGST/SGST split intra-state."""
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.reports import _compute_gstr1  # noqa: E402


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None

    def find(self, query=None, projection=None):
        out = []
        for d in self.docs:
            if not query or all(
                d.get(k) == v for k, v in query.items() if not isinstance(v, dict)
            ):
                out.append(dict(d))
        return out


class _DB:
    def __init__(self, mapping):
        self._m = mapping

    def get_collection(self, name):
        return self._m.get(name, _Coll([]))

    def __getitem__(self, name):
        return self.get_collection(name)


_STORE = {"store_id": "store-001", "gstin": "07AABCB0001Q1ZZ", "store_name": "S", "state": "Delhi"}
# Walk-in, no GSTIN, value < 2.5L -> B2CS. Mixed rates: 5% frame + 18% sunglass.
_ORDER = {
    "order_id": "ORD-B2CS", "order_number": "ORD-B2CS", "invoice_number": "INV-2026-00009",
    "store_id": "store-001", "customer_id": "",
    "created_at": datetime(2026, 4, 15, 10, 0, 0), "status": "COMPLETED",
    "grand_total": 17050.0, "tax_amount": 2050.0,
    "items": [
        {"hsn_code": "900311", "gst_rate": 5, "category": "FRAME", "item_total": 5250.0},
        {"hsn_code": "9004", "gst_rate": 18, "category": "SUNGLASS", "item_total": 11800.0},
    ],
}


def _patch(monkeypatch):
    import api.routers.reports as r
    db = _DB({
        "stores": _Coll([_STORE]),
        "customers": _Coll([]),       # walk-in -> no customer doc
        "orders": _Coll([_ORDER]),
        "credit_note_ledger": _Coll([]),
    })
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)


def _by_rate(b2cs):
    return {row["gstRate"]: row for row in b2cs}


def test_b2cs_splits_mixed_rate_invoice_into_per_rate_buckets(monkeypatch):
    _patch(monkeypatch)
    report = _compute_gstr1("2026-04", "store-001")
    b2cs = report["b2cs"]
    rates = {row["gstRate"] for row in b2cs}
    assert rates == {5, 18}, b2cs  # two buckets, not one lumped row

    by = _by_rate(b2cs)
    assert round(by[5]["taxableValue"], 2) == 5000.0
    assert round(by[5]["totalTax"], 2) == 250.0
    assert round(by[18]["taxableValue"], 2) == 10000.0
    assert round(by[18]["totalTax"], 2) == 1800.0


def test_b2cs_intra_state_splits_cgst_sgst_per_bucket(monkeypatch):
    _patch(monkeypatch)
    report = _compute_gstr1("2026-04", "store-001")
    by = _by_rate(report["b2cs"])
    # Delhi store, walk-in (no state) -> intra-state -> CGST/SGST, no IGST.
    assert round(by[5]["cgst"], 2) == 125.0 and round(by[5]["sgst"], 2) == 125.0
    assert round(by[18]["cgst"], 2) == 900.0 and round(by[18]["sgst"], 2) == 900.0
    assert by[5]["igst"] == 0.0 and by[18]["igst"] == 0.0


def test_b2cs_taxable_total_preserved_across_buckets(monkeypatch):
    _patch(monkeypatch)
    report = _compute_gstr1("2026-04", "store-001")
    total_taxable = sum(row["taxableValue"] for row in report["b2cs"])
    total_tax = sum(row["totalTax"] for row in report["b2cs"])
    assert round(total_taxable, 2) == 15000.0  # 5000 + 10000
    assert round(total_tax, 2) == 2050.0       # 250 + 1800
