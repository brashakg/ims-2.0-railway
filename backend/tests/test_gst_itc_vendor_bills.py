"""BUG-138: GSTR-3B ITC must come from recorded purchase invoices
(vendor_bills cgst/sgst/igst_total), scoped to the store's entity -- NOT the
qty-only `grns` collection (which had no tax fields, so ITC was always 0 and the
business over-paid GST)."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.reports import _itc_from_vendor_bills  # noqa: E402


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


def test_itc_reads_vendor_bills_tax_totals_entity_scoped():
    vb = _Coll(agg_ret=[{"igst": 100.0, "cgst": 50.0, "sgst": 50.0}])
    db = _DB(_Coll(find_one_ret={"entity_id": "E1"}), vb)
    assert _itc_from_vendor_bills(db, "S1", 2026, 6, 30) == (100.0, 50.0, 50.0)
    grp = vb.agg_pipeline[1]["$group"]
    # The BUG-138 fix: sum vendor_bills' *_total fields (not grns *_amount).
    assert grp["igst"] == {"$sum": "$igst_total"}
    assert grp["cgst"] == {"$sum": "$cgst_total"}
    assert grp["sgst"] == {"$sum": "$sgst_total"}
    match = vb.agg_pipeline[0]["$match"]
    assert match["recipient_entity_id"] == "E1"        # scoped to the entity
    assert match["itc_eligible"] == {"$ne": False}      # eligible-by-default
    assert any("invoice_date" in c for c in match["$or"])  # string-date month window


def test_itc_no_entity_falls_back_unscoped():
    vb = _Coll(agg_ret=[{"igst": 10.0, "cgst": 0.0, "sgst": 0.0}])
    db = _DB(_Coll(find_one_ret=None), vb)  # store not found -> no entity
    assert _itc_from_vendor_bills(db, "S1", 2026, 6, 30)[0] == 10.0
    assert "recipient_entity_id" not in vb.agg_pipeline[0]["$match"]


def test_itc_db_none_safe():
    assert _itc_from_vendor_bills(None, "S1", 2026, 6, 30) == (0.0, 0.0, 0.0)


def test_itc_no_bills_is_zero():
    db = _DB(_Coll(find_one_ret={"entity_id": "E1"}), _Coll(agg_ret=[]))
    assert _itc_from_vendor_bills(db, "S1", 2026, 6, 30) == (0.0, 0.0, 0.0)
