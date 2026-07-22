"""BUG-100: GSTR-1 must (1) file the INVOICE SERIAL (invoice_number) not the
order id, (2) include a CDNR section sourced from credit_note_ledger (type=ISSUED)
so credit notes reduce GST liability, and (3) carry an HSN summary.

Uses a minimal in-memory db whose .find() matches on the non-date keys (Mongo
would match the in-window docs); _compute_gstr1 reads stores/customers/orders/
credit_note_ledger via get_collection + __getitem__."""
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
        # Match on every non-date key (Mongo would match the in-window docs;
        # the date window itself is exercised by the router, not the fake).
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
_B2B_CUST = {"customer_id": "cust-001", "gstin": "07AAAAA0000A1ZZ", "name": "ACME Ltd", "state": "Delhi"}
_ORDER = {
    "order_id": "ORD-001", "order_number": "ORD-001", "invoice_number": "INV-2026-00001",
    "store_id": "store-001", "customer_id": "cust-001",
    "created_at": datetime(2026, 4, 15, 10, 0, 0), "status": "COMPLETED",
    "grand_total": 1000.0, "tax_amount": 180.0, "items": [{"hsn_code": "9004", "gst_rate": 18}],
}
_CREDIT = {
    "entry_id": "entry-001", "customer_id": "cust-001", "type": "ISSUED",
    "ref": "RET-250415-ABC123", "store_id": "store-001",
    "created_at": datetime(2026, 4, 16, 11, 0, 0), "gross_refund": 118.0, "net_refund": 100.0,
}


def _db(credit_docs):
    return _DB({
        "stores": _Coll([_STORE]),
        "customers": _Coll([_B2B_CUST]),
        "orders": _Coll([_ORDER]),
        "credit_note_ledger": _Coll(credit_docs),
    })


def _patch(monkeypatch, db):
    import api.routers.reports as r
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)


def test_gstr1_uses_invoice_number_not_order_id(monkeypatch):
    _patch(monkeypatch, _db([]))
    report = _compute_gstr1("2026-04", "store-001")
    assert len(report["b2b"]) == 1
    assert report["b2b"][0]["invoiceNumber"] == "INV-2026-00001"
    assert report["b2b"][0]["invoiceNumber"] != "ORD-001"


def test_gstr1_includes_cdnr_from_credit_note_ledger(monkeypatch):
    _patch(monkeypatch, _db([_CREDIT]))
    report = _compute_gstr1("2026-04", "store-001")
    assert "cdnr" in report and isinstance(report["cdnr"], list)
    assert len(report["cdnr"]) == 1, report["cdnr"]
    cn = report["cdnr"][0]
    # The 17-char internal ref exceeds GSTN's 16-char note-number cap, so the
    # filing caps it (synthesized historical CNs instead carry a dedicated
    # GSTN-legal note_number the resolver prefers -- see _cdnr_note_number).
    assert cn["refReference"] == "RET-250415-ABC12"
    assert len(cn["refReference"]) <= 16
    assert cn["grossValue"] == 118.0
    assert cn["taxableValue"] == 100.0
    assert round(cn["taxValue"], 2) == 18.0  # 118 gross - 100 net


def test_gstr1_cdnr_empty_when_no_credit_notes(monkeypatch):
    _patch(monkeypatch, _db([]))
    report = _compute_gstr1("2026-04", "store-001")
    assert report["cdnr"] == []


def test_gstr1_cdnr_prefers_explicit_tax_when_fee_less(monkeypatch):
    """Finding #4: a FEE-LESS credit note has gross == net, so the legacy
    gross-minus-net derivation would report tax 0. When the ledger row carries the
    explicit GST split (Shopify refund + in-store CREDIT_NOTE now stamp it), the
    CDNR must report the REAL taxable/tax, not 0."""
    fee_less = {
        "entry_id": "entry-cn", "customer_id": "cust-001", "type": "ISSUED",
        "ref": "RET-SHOPIFY-1", "store_id": "store-001",
        "created_at": datetime(2026, 4, 20, 9, 0, 0),
        # fee-less: gross == net -> the old derivation would give tax 0...
        "gross_refund": 5900.0, "net_refund": 5900.0,
        # ...but the explicit stamp carries the true reversal.
        "taxable": 5000.0, "tax": 900.0, "gst_rate": 18.0,
    }
    _patch(monkeypatch, _db([fee_less]))
    report = _compute_gstr1("2026-04", "store-001")
    assert len(report["cdnr"]) == 1
    cn = report["cdnr"][0]
    assert cn["grossValue"] == 5900.0
    assert cn["taxableValue"] == 5000.0
    assert cn["taxValue"] == 900.0          # NOT 0
    assert cn["gstRate"] == 18
    # Intra-state (store + customer both Delhi) -> CGST/SGST split, no IGST.
    assert round(cn["cgst"] + cn["sgst"], 2) == 900.0
    assert cn["igst"] == 0.0


def test_gstr1_has_hsn_summary(monkeypatch):
    _patch(monkeypatch, _db([]))
    report = _compute_gstr1("2026-04", "store-001")
    assert "hsnSummary" in report and isinstance(report["hsnSummary"], list)
