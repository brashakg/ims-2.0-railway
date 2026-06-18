"""
IMS 2.0 - Estimates / Quotations tests
======================================

Covers the three things the spec asks for, with no live Mongo dependency
(the endpoint functions are driven directly with the persistence accessors
monkeypatched to an in-test fake collection -- same pattern as
test_idor_transfers.py):

  1. render well-formed:  render_estimate produces a complete self-contained
     HTML page with the mandatory "ESTIMATE / QUOTATION - not a tax invoice"
     header and the line/total content.
  2. estimate totals:     _price_estimate reuses the order GST engine so the
     taxable + tax reconcile to the grand total (inclusive default mode: the
     offer price IS the all-in price; GST is extracted from within).
  3. RBAC:                create is denied for non-POS roles (ACCOUNTANT,
     OPTOMETRIST, CASHIER, WORKSHOP_STAFF) and allowed for POS roles + ADMIN.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import estimates  # noqa: E402
from api.routers.estimates import EstimateCreate, EstimateItem, _price_estimate  # noqa: E402
from api.services.print_render import render_estimate  # noqa: E402


# ===========================================================================
# Fakes + helpers
# ===========================================================================


class _FakeColl:
    """Minimal in-test stand-in for the `estimates` pymongo collection."""

    def __init__(self):
        self.docs = {}

    def insert_one(self, doc):
        self.docs[doc["estimate_id"]] = dict(doc)

    def find_one(self, flt):
        eid = flt.get("estimate_id")
        d = self.docs.get(eid)
        return dict(d) if d else None

    def find(self, flt=None):
        rows = list(self.docs.values())
        flt = flt or {}
        sid = flt.get("store_id")
        if isinstance(sid, dict) and "$in" in sid:
            allowed = set(sid["$in"])
            rows = [r for r in rows if r.get("store_id") in allowed]
        elif isinstance(sid, str):
            rows = [r for r in rows if r.get("store_id") == sid]
        return _FakeCursor(rows)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter([dict(r) for r in self._rows])


# Seeded issuing store + entity for the render endpoint. #754 added a fail-loud
# guard (print_identity.assert_issuing_identity raises HTTPException(404) when the
# store has no name), so the estimate render can no longer resolve the store
# fail-soft to {} -- it must see a real issuing store. The names are imported into
# the estimates module namespace, so we patch them there.
_TEST_STORE = {
    "store_id": "BV-TEST-01",
    "store_name": "ZZ Test Store Bokaro",
    "store_code": "BV-TEST-01",
    "state": "Jharkhand",
    "state_code": "20",
    "city": "Bokaro",
    "address": "Test Road",
    "phone": "9000000000",
    "entity_id": "ent-zz-test",
}
_TEST_ENTITY = {
    "entity_id": "ent-zz-test",
    "legal_name": "ZZ Test Optical Pvt Ltd",
    "trade_name": "ZZ Test",
    "gstins": [{"state_code": "20", "gstin": "20ABCDE1234F1Z5"}],
    "invoice": {},
}


@pytest.fixture()
def coll(monkeypatch):
    fake = _FakeColl()
    monkeypatch.setattr(estimates, "_coll", lambda: fake)
    monkeypatch.setattr(estimates, "_get_db", lambda: None)
    # #754 fail-loud guard: the render endpoint requires a resolvable issuing
    # store + entity (was previously allowed to resolve fail-soft to {}).
    monkeypatch.setattr(estimates, "load_store", lambda sid: dict(_TEST_STORE) if sid else {})
    monkeypatch.setattr(estimates, "load_entity_for_store", lambda store: dict(_TEST_ENTITY) if store else {})
    return fake


def _user(role, stores=("BV-TEST-01",)):
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": stores[0] if stores else None,
    }


def _payload(**over):
    base = dict(
        customer_name="Ravi Kumar",
        customer_phone="9000000001",
        store_id="BV-TEST-01",
        items=[
            EstimateItem(
                description="Frame Premium",
                category="FRAME",
                hsn_code="900311",
                quantity=1,
                mrp=5000.0,
                offer_price=4000.0,
            )
        ],
        validity_days=15,
    )
    base.update(over)
    return EstimateCreate(**base)


# ===========================================================================
# 1. render well-formed
# ===========================================================================


def test_render_estimate_is_well_formed_html():
    entity = {
        "legal_name": "Better Vision Opticals Pvt Ltd",
        "pan": "AAACB1234C",
        "gstins": [
            {
                "gstin": "20AAACB1234C1Z5",
                "state_code": "20",
                "state_name": "Jharkhand",
                "is_primary": True,
            }
        ],
    }
    store = {
        "name": "BV Bokaro",
        "store_id": "BV-BOK-01",
        "city": "Bokaro",
        "state": "Jharkhand",
        "state_code": "20",
        "pincode": "827004",
    }
    items = [
        {
            "description": "Frame Premium",
            "hsn_code": "900311",
            "quantity": 1,
            "mrp": 5000,
            "offer_price": 4000,
            "gst_rate": 5.0,
            "taxable_value": 3809.52,
            "tax_amount": 190.48,
            "line_total": 4000,
        }
    ]
    totals = {"subtotal": 4000, "taxable": 3809.52, "tax": 190.48, "grand_total": 4000.0}
    html = render_estimate(
        entity=entity,
        store=store,
        estimate_number="EST-ABCD1234",
        estimate_date="2026-06-17",
        valid_until="2026-07-02",
        customer_name="Ravi Kumar",
        items=items,
        totals=totals,
    )
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html
    # Mandatory header wording.
    assert "ESTIMATE / QUOTATION" in html.upper()
    assert "not a tax invoice" in html.lower()
    # Content present.
    assert "Frame Premium" in html
    assert "EST-ABCD1234" in html
    # The estimate must NOT claim to be an invoice with a serial.
    assert "Estimated Total" in html


def test_render_estimate_empty_items_still_valid():
    html = render_estimate(
        entity={},
        store={},
        estimate_number="EST-EMPTY",
        estimate_date="2026-06-17",
        items=[],
        totals={},
    )
    assert html.startswith("<!DOCTYPE html>")
    assert "No items" in html


# ===========================================================================
# 2. estimate totals (reuse order GST engine)
# ===========================================================================


def test_price_estimate_totals_reconcile_inclusive():
    """Inclusive default: taxable + tax == grand_total == offer*qty."""
    payload = _payload()
    priced = _price_estimate(payload)
    totals = priced["totals"]
    # One frame @ 4000 inclusive 5% GST.
    assert totals["grand_total"] == 4000.0
    assert round(totals["taxable"] + totals["tax"], 2) == totals["grand_total"]
    # Each line carries the engine-stamped GST fields.
    line = priced["items"][0]
    assert line["gst_rate"] == 5.0
    assert line["taxable_value"] > 0
    assert round(line["taxable_value"] + line["tax_amount"], 2) == 4000.0


def test_price_estimate_multi_line_and_discount():
    payload = _payload(
        items=[
            EstimateItem(
                description="Frame", category="FRAME", quantity=2,
                offer_price=1000.0, discount_percent=10.0,
            ),
            EstimateItem(
                description="Sunglass", category="SUNGLASS", quantity=1,
                offer_price=2000.0,
            ),
        ],
    )
    priced = _price_estimate(payload)
    totals = priced["totals"]
    # 2 x 1000 less 10% = 1800 ; + 2000 = 3800 gross.
    assert totals["grand_total"] == 3800.0
    assert round(totals["taxable"] + totals["tax"], 2) == 3800.0
    # Two GST rates should be present on the lines (5% frame, 18% sunglass).
    rates = {it["gst_rate"] for it in priced["items"]}
    assert rates == {5.0, 18.0}


# ===========================================================================
# 3. RBAC + create/get/render flow
# ===========================================================================


def test_create_denied_for_non_pos_roles(coll):
    for role in ("ACCOUNTANT", "OPTOMETRIST", "CASHIER", "WORKSHOP_STAFF"):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(estimates.create_estimate(_payload(), _user(role)))
        assert ei.value.status_code == 403, role


def test_create_allowed_for_pos_roles_and_admin(coll):
    for role in ("SALES_STAFF", "SALES_CASHIER", "STORE_MANAGER", "ADMIN", "SUPERADMIN"):
        out = asyncio.run(estimates.create_estimate(_payload(), _user(role)))
        assert out["estimate_number"].startswith("EST-")
        assert out["status"] == "DRAFT"
        assert "_id" not in out  # scrubbed
        assert out["totals"]["grand_total"] == 4000.0


def test_create_get_and_render_roundtrip(coll):
    created = asyncio.run(estimates.create_estimate(_payload(), _user("SALES_STAFF")))
    eid = created["estimate_id"]

    fetched = asyncio.run(estimates.get_estimate(eid, _user("SALES_STAFF")))
    assert fetched["estimate_id"] == eid
    assert fetched["customer_name"] == "Ravi Kumar"

    html_resp = asyncio.run(
        estimates.render_estimate_html(eid, False, _user("SALES_STAFF"))
    )
    body = html_resp.body.decode("utf-8")
    assert "ESTIMATE / QUOTATION" in body.upper()
    assert "Frame Premium" in body
    assert "EST-" in body


def test_get_missing_estimate_404(coll):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(estimates.get_estimate("nope", _user("SALES_STAFF")))
    assert ei.value.status_code == 404


def test_create_requires_at_least_one_item():
    with pytest.raises(Exception):
        _payload(items=[])


def test_list_is_store_scoped_for_non_hq(coll):
    asyncio.run(estimates.create_estimate(_payload(store_id="BV-TEST-01"), _user("SALES_STAFF")))
    # A staff user bound to a DIFFERENT store sees nothing (store-scoped filter).
    other = _user("SALES_STAFF", stores=("BV-OTHER-99",))
    listing = asyncio.run(estimates.list_estimates(None, 100, other))
    assert listing["total"] == 0
    # The owning-store staff sees it.
    owner = _user("SALES_STAFF", stores=("BV-TEST-01",))
    listing2 = asyncio.run(estimates.list_estimates(None, 100, owner))
    assert listing2["total"] == 1
