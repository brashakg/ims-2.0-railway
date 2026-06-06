"""NEW-GST-RCM: GSTR-3B Table 3.1(d) -- inward supplies liable to reverse charge.
Vendor bills flagged reverse_charge=True are the buyer's own GST liability; they
must surface in Table 3.1(d) and (being cash-only) add to the cash payable."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.reports import _rcm_from_vendor_bills  # noqa: E402


class _Coll:
    def __init__(self, find_one_ret=None, agg_ret=None):
        self._fo = find_one_ret
        self._agg = agg_ret or []
        self.agg_pipeline = None

    def find_one(self, flt, proj=None):
        return self._fo

    def aggregate(self, pipeline):
        self.agg_pipeline = pipeline
        return iter(self._agg)


class _DB:
    def __init__(self, stores_coll, vb_coll):
        self._map = {"stores": stores_coll, "vendor_bills": vb_coll}

    def __getitem__(self, name):
        return self._map[name]


def test_rcm_sums_reverse_charge_bills_entity_scoped():
    vb = _Coll(agg_ret=[{"igst": 90.0, "cgst": 0.0, "sgst": 0.0, "taxable": 500.0}])
    db = _DB(_Coll(find_one_ret={"entity_id": "E1"}), vb)
    assert _rcm_from_vendor_bills(db, "S1", 2026, 6, 30) == (90.0, 0.0, 0.0, 500.0)
    match = vb.agg_pipeline[0]["$match"]
    assert match["reverse_charge"] is True            # only RCM bills
    assert match["recipient_entity_id"] == "E1"        # entity scoped
    assert any("invoice_date" in c for c in match["$or"])  # string-date window
    grp = vb.agg_pipeline[1]["$group"]
    assert grp["taxable"] == {"$sum": "$taxable_amount"}


def test_rcm_db_none_safe():
    assert _rcm_from_vendor_bills(None, "S1", 2026, 6, 30) == (0.0, 0.0, 0.0, 0.0)


def test_rcm_no_bills_is_zero():
    db = _DB(_Coll(find_one_ret={"entity_id": "E1"}), _Coll(agg_ret=[]))
    assert _rcm_from_vendor_bills(db, "S1", 2026, 6, 30) == (0.0, 0.0, 0.0, 0.0)


# --- integration: _compute_gstr3b surfaces Table 3.1(d) -------------------------

class _SmartVB:
    """vendor_bills whose aggregate returns RCM totals only for the RCM query
    (the one whose $match carries reverse_charge=True), else the ITC query gets
    nothing -- so the two helpers don't cross-contaminate."""
    def __init__(self, rcm_row):
        self._rcm = rcm_row

    def find_one(self, flt, proj=None):
        return None

    def aggregate(self, pipeline):
        if pipeline[0]["$match"].get("reverse_charge") is True:
            return iter([self._rcm] if self._rcm else [])
        return iter([])


class _FullDB:
    def __init__(self, store, rcm_row):
        self._m = {
            "stores": _Coll(find_one_ret=store),
            "orders": _Coll(agg_ret=[]),
            "customers": _Coll(agg_ret=[]),
            "vendor_bills": _SmartVB(rcm_row),
        }

    def get_collection(self, name):
        return self._m.get(name, _Coll())

    def __getitem__(self, name):
        return self._m.get(name, _Coll())


def test_gstr3b_surfaces_table_31d_and_adds_to_cash(monkeypatch):
    import api.routers.reports as r
    store = {"store_id": "S1", "gstin": "07AAACR0000A1ZZ", "store_name": "S", "state": "Delhi"}
    db = _FullDB(store, rcm_row={"igst": 0.0, "cgst": 45.0, "sgst": 45.0, "taxable": 500.0})
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    assert rep["inwardSuppliesReverseChargeValue"] == 500.0
    assert rep["inwardSuppliesReverseCharge"]["centralTax"] == 45.0
    assert rep["inwardSuppliesReverseCharge"]["stateTax"] == 45.0
    # RCM is discharged in cash -> shows in the cash payable.
    assert rep["taxPaidCash"]["centralTax"] >= 45.0
    assert rep["taxPaidCash"]["stateTax"] >= 45.0


def test_gstr3b_no_rcm_bills_zero_table_31d(monkeypatch):
    import api.routers.reports as r
    store = {"store_id": "S1", "gstin": "07AAACR0000A1ZZ", "store_name": "S", "state": "Delhi"}
    db = _FullDB(store, rcm_row=None)
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    assert rep["inwardSuppliesReverseChargeValue"] == 0.0
    assert rep["inwardSuppliesReverseCharge"]["centralTax"] == 0.0
