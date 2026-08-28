"""Purchase P1 / S1 -- PO per-line GST + residual fields.

create_po previously hardcoded a flat 18% GST AND stored lines with no
tax_rate, so the downstream invoice draft (lines_from_grn, which reads
po_line['tax_rate']) computed 0% tax. S1 resolves GST per line (server-side,
with product hsn/category fallback) and stamps the residual fields
(ordered_qty / received_qty / line_status) the receiving cockpit reads.
The endpoint is driven directly with monkeypatched repos -- no Mongo, no HTTP.
"""
from __future__ import annotations

import os
import sys
import asyncio

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from datetime import timedelta  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from api.routers.vendors import (  # noqa: E402
    create_po,
    send_po,
    POCreate,
    POItemCreate,
)
from api.services.gst_rates import resolve_gst_rate  # noqa: E402
from api.services import product_master as _pm  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402


class _FakePORepo:
    def __init__(self):
        self.created = None

    def create(self, doc):
        self.created = doc
        return doc


class _FakeVendorRepo:
    def find_by_id(self, vid):
        return {"vendor_id": vid, "trade_name": "Acme Optics"}


class _FakeProductRepo:
    def __init__(self, prods):
        self.prods = prods

    def find_by_id(self, pid):
        return self.prods.get(pid)


def _patch(mp, po_repo, prod_repo=None):
    mp.setattr(v, "get_purchase_order_repository", lambda: po_repo)
    mp.setattr(v, "get_vendor_repository", lambda: _FakeVendorRepo())
    mp.setattr(v, "get_product_repository", lambda: prod_repo)
    mp.setattr(v, "generate_po_number", lambda store: "PO-TEST-1")


def _user():
    return {"user_id": "u1", "roles": ["ADMIN"], "active_store_id": "BV-TEST-01"}


def test_per_line_gst_not_flat_18_plus_residual_fields(monkeypatch):
    po_repo = _FakePORepo()
    _patch(monkeypatch, po_repo)
    po = POCreate(
        vendor_id="V1",
        delivery_store_id="BV-TEST-01",
        items=[
            POItemCreate(product_id="P1", product_name="Ray-Ban", sku="RB1",
                         quantity=2, unit_price=1000, gst_rate=5),
            POItemCreate(product_id="P2", product_name="Oakley SG", sku="OK1",
                         quantity=1, unit_price=2000, gst_rate=18),
        ],
    )
    asyncio.run(create_po(po, current_user=_user()))
    doc = po_repo.created
    assert doc is not None
    # subtotal 2000 + 2000 = 4000; tax = 100 (5% of 2000) + 360 (18% of 2000) = 460
    # -- NOT the old flat 18% of 4000 (= 720).
    assert doc["subtotal"] == 4000
    assert doc["tax_amount"] == 460
    assert doc["total_amount"] == 4460
    line0 = doc["items"][0]
    assert line0["tax_rate"] == 5
    assert line0["ordered_qty"] == 2
    assert line0["received_qty"] == 0
    assert line0["line_status"] == "OPEN"
    assert doc["items"][1]["tax_rate"] == 18


def test_gst_resolved_from_product_when_line_omits_rate(monkeypatch):
    po_repo = _FakePORepo()
    prod_repo = _FakeProductRepo(
        {"P1": {"product_id": "P1", "category": "FRAME", "hsn_code": "9003"}}
    )
    _patch(monkeypatch, po_repo, prod_repo)
    po = POCreate(
        vendor_id="V1",
        delivery_store_id="BV-TEST-01",
        items=[POItemCreate(product_id="P1", product_name="Frame", sku="F1",
                            quantity=1, unit_price=1000)],  # no gst_rate -> resolve
    )
    asyncio.run(create_po(po, current_user=_user()))
    line = po_repo.created["items"][0]
    # The stored rate must equal what the canonical resolver returns for this
    # product's hsn/category (frames = 5% in the static table) -- not 18%.
    expected = resolve_gst_rate(hsn_code="9003", category="FRAME")
    assert line["tax_rate"] == expected
    assert line["hsn"] == "9003"
    assert line["line_status"] == "OPEN"


# ============================================================================
# Owner items 1 + 2 (2026-08-26): interstate vs intrastate GST, rate from HSN
# ============================================================================


class _FakeStoreRepo:
    def __init__(self, store):
        self.store = store

    def find_by_id(self, sid):
        return self.store


def _patch_store(mp, store):
    mp.setattr(v, "get_store_repository", lambda: _FakeStoreRepo(store))


# Jharkhand = state code 20, Maharashtra = 27. Real 15-char GSTIN shapes.
_JH_STORE = {"store_id": "BV-TEST-01", "gstin": "20AABCU9603R1ZM", "state": "Jharkhand"}
_MH_STORE = {
    "store_id": "WO-TEST-01",
    "gstin": "27AABCU9603R1ZX",
    "state": "Maharashtra",
}
_JH_VENDOR = {"vendor_id": "V1", "trade_name": "Ranchi Optics", "gstin": "20AACCA1234B1Z5"}
_MH_VENDOR = {"vendor_id": "V1", "trade_name": "Mumbai Frames", "gstin": "27AACCA1234B1Z2"}


class _VendorRepoWith:
    def __init__(self, vendor):
        self.vendor = vendor

    def find_by_id(self, vid):
        return self.vendor


def _one_frame_po(store_id="BV-TEST-01"):
    return POCreate(
        vendor_id="V1",
        delivery_store_id=store_id,
        items=[
            POItemCreate(
                product_id="P1",
                product_name="Ray-Ban Aviator",
                sku="RB-AV",
                quantity=2,
                unit_price=1000,
            )
        ],
    )


def _frame_product(**over):
    """A frame that is catalogue-complete EXCEPT cost_price -- exactly the shape
    of all 68 live product rows on 2026-08-26."""
    doc = {
        "product_id": "P1",
        "category": "FRAME",
        "brand": "Ray-Ban",
        "model": "RB3025",
        "color": "GOLD",
        "mrp": 6000,
        "offer_price": 4800,
        "hsn_code": "900311",
        "gst_rate": 5.0,
        "catalog_status": "DRAFT",
    }
    doc.update(over)
    return doc


def test_intrastate_po_splits_cgst_sgst(monkeypatch):
    po_repo = _FakePORepo()
    _patch(monkeypatch, po_repo, _FakeProductRepo({"P1": _frame_product()}))
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    out = asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    doc = po_repo.created
    # Jharkhand vendor -> Jharkhand store: SAME state, so CGST + SGST, no IGST.
    assert doc["interstate"] is False
    assert doc["supplier_state"] == "20"
    assert doc["supply_place_recipient"] == "20"
    assert doc["supply_place_assumed"] is False
    # A purchase order must NOT carry a bare `place_of_supply`: on a vendor
    # BILL that name means the SUPPLIER state (itc_reconcile keys on it), so a
    # PO storing the RECIPIENT under the same name would flip every inter-state
    # purchase to intra-state the day anything carried it onto a bill.
    assert "place_of_supply" not in doc
    line = doc["items"][0]
    # HSN 900311 -> 5% on 2000 = 100, halved into CGST 50 + SGST 50.
    assert line["tax_rate"] == 5.0
    assert line["gst_source"] == "hsn"
    assert (line["cgst"], line["sgst"], line["igst"]) == (50.0, 50.0, 0.0)
    assert doc["gst_summary"] == {"cgst": 50.0, "sgst": 50.0, "igst": 0.0, "tax": 100.0}
    assert doc["total_amount"] == 2100.0
    assert out["interstate"] is False


def test_interstate_po_raises_igst(monkeypatch):
    po_repo = _FakePORepo()
    _patch(monkeypatch, po_repo, _FakeProductRepo({"P1": _frame_product()}))
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_MH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    doc = po_repo.created
    # Maharashtra vendor -> Jharkhand store: DIFFERENT states, one IGST charge.
    assert doc["interstate"] is True
    assert doc["supplier_state"] == "27"
    assert doc["supply_place_recipient"] == "20"
    line = doc["items"][0]
    assert line["tax_rate"] == 5.0
    assert (line["cgst"], line["sgst"], line["igst"]) == (0.0, 0.0, 100.0)
    assert doc["gst_summary"] == {"cgst": 0.0, "sgst": 0.0, "igst": 100.0, "tax": 100.0}
    # The TOTAL is identical either way -- only the split moves. That is the
    # point: the money is the same, the return it is filed in is not.
    assert doc["total_amount"] == 2100.0


def test_the_two_gstins_decide_the_split_not_the_store_id(monkeypatch):
    """The second store is in Maharashtra, so the SAME Maharashtra vendor that
    was inter-state above is now intra-state. 'Our state' is not a constant:
    3 legal entities, 4 GSTINs, 2 states."""
    po_repo = _FakePORepo()
    _patch(monkeypatch, po_repo, _FakeProductRepo({"P1": _frame_product()}))
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_MH_VENDOR))
    _patch_store(monkeypatch, _MH_STORE)

    asyncio.run(create_po(_one_frame_po("WO-TEST-01"), current_user=_user()))
    doc = po_repo.created
    assert doc["interstate"] is False
    assert (doc["items"][0]["cgst"], doc["items"][0]["igst"]) == (50.0, 0.0)


def test_rate_comes_from_the_hsn_not_a_flat_18(monkeypatch):
    """Two lines, two HSNs, one PO: frames 5% and sunglasses 18%."""
    po_repo = _FakePORepo()
    prods = {
        "P1": _frame_product(),
        "P2": _frame_product(
            product_id="P2", category="SUNGLASS", hsn_code="900410", gst_rate=18.0
        ),
    }
    _patch(monkeypatch, po_repo, _FakeProductRepo(prods))
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    po = POCreate(
        vendor_id="V1",
        delivery_store_id="BV-TEST-01",
        items=[
            POItemCreate(
                product_id="P1", product_name="Frame", sku="F", quantity=1, unit_price=1000
            ),
            POItemCreate(
                product_id="P2", product_name="Shades", sku="S", quantity=1, unit_price=1000
            ),
        ],
    )
    asyncio.run(create_po(po, current_user=_user()))
    doc = po_repo.created
    assert [i["tax_rate"] for i in doc["items"]] == [5.0, 18.0]
    assert [i["gst_source"] for i in doc["items"]] == ["hsn", "hsn"]
    # 50 + 180 = 230, NOT a flat 18% of 2000 (= 360).
    assert doc["tax_amount"] == 230.0


def test_product_with_no_hsn_is_flagged_not_guessed(monkeypatch):
    po_repo = _FakePORepo()
    naked = {"product_id": "P1", "category": "FRAME"}  # no hsn_code, no gst_rate
    _patch(monkeypatch, po_repo, _FakeProductRepo({"P1": naked}))
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    out = asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    line = po_repo.created["items"][0]
    # NOT silently taxed at the optical-dominant 5% default just because the
    # category says FRAME -- resolve_gst_rate() would have returned 5.0 here.
    assert resolve_gst_rate(hsn_code=None, category="FRAME") == 5.0
    assert line["gst_unresolved"] is True
    assert line["tax_rate"] == 0.0
    assert line["line_tax"] == 0.0
    assert line["gst_missing"] == "no HSN on this product"
    assert out["gst_warnings"] == [
        {
            "product_id": "P1",
            "product_name": "Ray-Ban Aviator",
            "missing": "no HSN on this product",
            "taxed": False,
        }
    ]


def test_unknown_hsn_is_flagged_with_what_is_missing(monkeypatch):
    po_repo = _FakePORepo()
    unknown_hsn = _frame_product(hsn_code="123456")
    unknown_hsn.pop("gst_rate")  # nothing else can settle it either
    _patch(monkeypatch, po_repo, _FakeProductRepo({"P1": unknown_hsn}))
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    line = po_repo.created["items"][0]
    assert line["gst_unresolved"] is True
    assert line["line_tax"] == 0.0
    assert line["gst_missing"] == "HSN 123456 is not in the GST rate list"


def test_unknown_hsn_with_a_catalogued_rate_is_taxed_but_still_says_why(monkeypatch):
    """A rate a person chose at cataloguing is not a guess -- the line IS taxed
    -- but the unresolved HSN still travels with it so the screen can say so."""
    po_repo = _FakePORepo()
    _patch(
        monkeypatch, po_repo, _FakeProductRepo({"P1": _frame_product(hsn_code="123456")})
    )
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    out = asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    line = po_repo.created["items"][0]
    assert line["gst_unresolved"] is False
    assert line["gst_source"] == "catalogue"
    assert line["tax_rate"] == 5.0
    assert line["gst_missing"] == "HSN 123456 is not in the GST rate list"
    # ... and it is SURFACED. A line that was taxed is not the same as a line
    # that is fine.
    assert out["gst_warnings"] == [
        {
            "product_id": "P1",
            "product_name": "Ray-Ban Aviator",
            "missing": "HSN 123456 is not in the GST rate list",
            "taxed": True,
        }
    ]


@pytest.mark.parametrize("no_hsn", [None, "", "   "])
def test_a_taxed_line_with_no_hsn_at_all_is_still_reported(monkeypatch, no_hsn):
    """HSN is MANDATORY on a GST purchase document. A product with a catalogue
    rate but no HSN was taxed silently and named nowhere -- so a purchase order
    went to a real vendor with no HSN on it and no warning anywhere. Surfacing
    only the lines whose RATE is null misses exactly this line."""
    po_repo = _FakePORepo()
    rated_but_hsn_less = _frame_product(hsn_code=no_hsn)
    _patch(monkeypatch, po_repo, _FakeProductRepo({"P1": rated_but_hsn_less}))
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    out = asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    line = po_repo.created["items"][0]
    # It IS taxed -- a person chose 5% at cataloguing, that is not a guess.
    assert line["tax_rate"] == 5.0
    assert line["gst_unresolved"] is False
    assert line["gst_missing"] == "no HSN on this product"
    # ... and the buyer is told, on THIS order.
    assert [w["missing"] for w in out["gst_warnings"]] == ["no HSN on this product"]
    assert out["gst_warnings"][0]["taxed"] is True
    assert out["gst_warnings"][0]["product_name"] == "Ray-Ban Aviator"


def test_four_digit_hsn_resolves_when_its_children_agree(monkeypatch):
    """Turnover <= Rs 5 Cr entities catalogue 4-digit HSNs. 9003 has only frames
    under it, all 5%, so it settles."""
    po_repo = _FakePORepo()
    _patch(monkeypatch, po_repo, _FakeProductRepo({"P1": _frame_product(hsn_code="9003")}))
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    line = po_repo.created["items"][0]
    assert line["gst_source"] == "hsn"
    assert line["tax_rate"] == 5.0


def test_ambiguous_four_digit_hsn_uses_the_catalogued_rate_and_says_so(monkeypatch):
    """9004 covers corrective spectacles (5%) AND sunglasses (18%), so the HSN
    alone cannot settle it. The rate SETTLED AT CATALOGUING is used instead --
    and labelled, so nobody mistakes it for an HSN-derived figure."""
    po_repo = _FakePORepo()
    _patch(
        monkeypatch,
        po_repo,
        _FakeProductRepo({"P1": _frame_product(hsn_code="9004", gst_rate=18.0)}),
    )
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    line = po_repo.created["items"][0]
    assert line["gst_unresolved"] is False
    assert line["gst_source"] == "catalogue"
    assert line["tax_rate"] == 18.0


def test_unknown_vendor_state_says_assumed_rather_than_pretending(monkeypatch):
    po_repo = _FakePORepo()
    _patch(monkeypatch, po_repo, _FakeProductRepo({"P1": _frame_product()}))
    monkeypatch.setattr(
        v, "get_vendor_repository", lambda: _VendorRepoWith({"vendor_id": "V1"})
    )
    _patch_store(monkeypatch, _JH_STORE)

    asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    doc = po_repo.created
    assert doc["supply_place_assumed"] is True
    assert doc["interstate"] is False  # safe default, same as the sales invoice


# ============================================================================
# Owner item 4: expected delivery date is today or later -- on the SERVER
# ============================================================================


def _po_kwargs(**over):
    kw = dict(
        vendor_id="V1",
        delivery_store_id="BV-TEST-01",
        items=[
            POItemCreate(
                product_id="P1", product_name="F", sku="F", quantity=1, unit_price=100
            )
        ],
    )
    kw.update(over)
    return kw


def test_server_refuses_a_backdated_expected_delivery_date():
    yesterday = (ist_today() - timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError) as exc:
        POCreate(**_po_kwargs(expected_date=yesterday))
    assert "cannot be in the past" in str(exc.value)


def test_server_accepts_today_and_future_expected_delivery_dates():
    """'Today' is the IST calendar day: Railway runs in UTC, so between 00:00
    and 05:30 IST a UTC 'today' would refuse a valid same-day date."""
    for offset in (0, 1, 45):
        day = (ist_today() + timedelta(days=offset)).isoformat()
        assert POCreate(**_po_kwargs(expected_date=day)).expected_date == day


def test_blank_or_absent_expected_date_still_allowed():
    assert POCreate(**_po_kwargs()).expected_date is None
    assert POCreate(**_po_kwargs(expected_date="")).expected_date == ""


def test_garbage_expected_date_is_refused():
    with pytest.raises(ValidationError) as exc:
        POCreate(**_po_kwargs(expected_date="next tuesday"))
    assert "must be a real date" in str(exc.value)


# ============================================================================
# Owner items 5 + 6: the PO rate IS the cost; a missing cost never blocks send
# ============================================================================


class _CostRecordingProductRepo(_FakeProductRepo):
    def __init__(self, prods):
        super().__init__(prods)
        self.updates = []

    def update(self, pid, fields):
        self.updates.append((pid, dict(fields)))
        self.prods[pid] = {**self.prods.get(pid, {}), **fields}
        return self.prods[pid]


def test_po_rate_lands_on_the_product_cost_and_clears_the_draft_gap(monkeypatch):
    po_repo = _FakePORepo()
    prod = _frame_product()
    # The live shape: DRAFT for one reason only.
    assert _pm.compute_catalog_status(prod) == ("DRAFT", ["cost_price"])

    repo = _CostRecordingProductRepo({"P1": prod})
    _patch(monkeypatch, po_repo, repo)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    out = asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    assert ("P1", {"cost_price": 1000.0, "cost_source": "PO_RATE"}) in repo.updates
    assert out["cost_filled"] == [{"product_id": "P1", "cost_price": 1000.0}]
    # The gap is GONE: the product now computes ACTIVE, so the amber Draft chip
    # clears without anyone reopening the catalogue screen.
    assert _pm.compute_catalog_status(repo.prods["P1"]) == ("ACTIVE", [])


def test_po_rate_never_overwrites_a_cost_the_product_already_has(monkeypatch):
    po_repo = _FakePORepo()
    repo = _CostRecordingProductRepo(
        {"P1": _frame_product(cost_price=850, cost_source="MANUAL")}
    )
    _patch(monkeypatch, po_repo, repo)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    out = asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    assert out["cost_filled"] == []
    assert repo.updates == []
    assert repo.prods["P1"]["cost_price"] == 850


class _RecordingAuditRepo:
    def __init__(self):
        self.rows = []

    def create(self, doc):
        self.rows.append(doc)
        return doc


def test_the_cost_this_po_wrote_is_named_in_the_audit_trail(monkeypatch):
    """Cost feeds margin and stock valuation, so 'who set this cost, and from
    where' has to be answerable. Only the PO create path writes one from an
    agreed rate, and only it audits -- so this is the check that the promise
    'every cost written this way is recorded' is true."""
    po_repo = _FakePORepo()
    repo = _CostRecordingProductRepo({"P1": _frame_product()})
    audit = _RecordingAuditRepo()
    _patch(monkeypatch, po_repo, repo)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    monkeypatch.setattr(v, "get_audit_repository", lambda: audit)
    _patch_store(monkeypatch, _JH_STORE)

    asyncio.run(create_po(_one_frame_po(), current_user=_user()))

    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["action"] == "purchase.cost_from_po_rate"
    assert row["entity_type"] == "purchase_order"
    assert row["user_id"] == "u1"
    assert row["detail"]["po_number"] == "PO-TEST-1"
    assert row["detail"]["products"] == [{"product_id": "P1", "cost_price": 1000.0}]


def test_nothing_is_audited_when_this_po_wrote_no_cost(monkeypatch):
    """A PO for an already-costed product must not leave a cost-change entry
    behind -- an audit trail that logs non-events is one nobody reads."""
    po_repo = _FakePORepo()
    repo = _CostRecordingProductRepo(
        {"P1": _frame_product(cost_price=850, cost_source="MANUAL")}
    )
    audit = _RecordingAuditRepo()
    _patch(monkeypatch, po_repo, repo)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    monkeypatch.setattr(v, "get_audit_repository", lambda: audit)
    _patch_store(monkeypatch, _JH_STORE)

    asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    assert audit.rows == []


def test_two_lines_of_one_po_for_the_same_product_do_not_re_price_its_cost(
    monkeypatch,
):
    """A 40-line order can list the same product twice -- two colours ordered on
    one row each, a corrected quantity added below. The FIRST agreed rate is the
    cost; the second line must see the cost the first just wrote, or it
    overwrites it at its own price and 'never overwrites an existing cost'
    quietly stops being true within a single request."""
    po_repo = _FakePORepo()
    repo = _CostRecordingProductRepo({"P1": _frame_product()})
    _patch(monkeypatch, po_repo, repo)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)

    two_lines = POCreate(
        vendor_id="V1",
        delivery_store_id="BV-TEST-01",
        items=[
            POItemCreate(
                product_id="P1",
                product_name="Ray-Ban Aviator",
                sku="RB-AV",
                quantity=2,
                unit_price=1000,
            ),
            POItemCreate(
                product_id="P1",
                product_name="Ray-Ban Aviator",
                sku="RB-AV",
                quantity=1,
                unit_price=1400,
            ),
        ],
    )
    out = asyncio.run(create_po(two_lines, current_user=_user()))

    # Only the COST writes; the catalogue restamp that follows each one is a
    # separate update on the same repo.
    cost_writes = [(pid, f) for pid, f in repo.updates if "cost_price" in f]
    assert cost_writes == [("P1", {"cost_price": 1000.0, "cost_source": "PO_RATE"})]
    assert out["cost_filled"] == [{"product_id": "P1", "cost_price": 1000.0}]
    assert repo.prods["P1"]["cost_price"] == 1000.0


def test_goods_receipt_outranks_the_provisional_po_rate_but_not_a_typed_cost():
    """Owner: 'goods receipt sets the real cost anyway'. The PO rate only
    unblocks cataloguing; the receipt corrects it. Any OTHER cost stands, and a
    PO can never overwrite a receipt."""
    repo = _CostRecordingProductRepo({})
    cases = {
        # (existing cost_source, the step now writing) -> may it overwrite?
        ("PO_RATE", "GRN_PO"): True,
        ("GRN_PO", "PO_RATE"): False,
        ("PO_RATE", "PO_RATE"): False,
        ("GRN_PO", "GRN_PO"): False,
        ("MANUAL", "GRN_PO"): False,
        ("MANUAL", "PO_RATE"): False,
        ("", "GRN_PO"): False,  # legacy row, no source recorded
    }
    got = {
        pair: v._promote_cost_from_rate(
            "P1", _frame_product(cost_price=1000, cost_source=pair[0]), 940, pair[1], repo
        )
        for pair in cases
    }
    assert got == cases
    # And an EMPTY cost is always fillable, whichever step gets there first.
    for step in ("PO_RATE", "GRN_PO"):
        assert v._promote_cost_from_rate("P1", _frame_product(), 940, step, repo) is True


def test_a_product_with_no_cost_can_be_ordered_and_the_po_still_sent(monkeypatch):
    """The whole point of rulings 5 + 6: a missing cost must not stop the PO
    going out to the vendor."""
    po_repo = _FakePORepo()
    no_cost = _frame_product()

    # A repo that REFUSES to write, so the product stays cost-less all the way
    # to send -- otherwise the create-time fill would mask the send gate.
    class _ReadOnlyRepo(_FakeProductRepo):
        def update(self, pid, fields):
            raise RuntimeError("read-only")

    repo = _ReadOnlyRepo({"P1": no_cost})
    _patch(monkeypatch, po_repo, repo)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepoWith(_JH_VENDOR))
    _patch_store(monkeypatch, _JH_STORE)
    monkeypatch.setattr(v, "_po_catalog_gate_on", lambda: True)

    asyncio.run(create_po(_one_frame_po(), current_user=_user()))
    created = po_repo.created
    assert created["items"][0]["unit_price"] == 1000  # ordered fine

    class _SendablePORepo(_FakePORepo):
        def __init__(self, doc):
            super().__init__()
            self.doc = doc
            self.updated = None

        def find_by_id(self, pid):
            return self.doc

        def update(self, pid, fields):
            self.updated = fields
            return fields

    send_repo = _SendablePORepo({**created, "status": "DRAFT"})
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: send_repo)
    # Still cost-less, still DRAFT, still warned about -- and still sendable.
    assert _pm.compute_catalog_status(no_cost)[1] == ["cost_price"]
    asyncio.run(send_po(created["po_id"], current_user=_user()))
    assert send_repo.updated["status"] == "SENT"


def test_a_gap_other_than_cost_still_blocks_the_send(monkeypatch):
    """The send gate is not weakened: only cost_price is forgiven."""
    po_repo = _FakePORepo()
    half_catalogued = _frame_product(cost_price=900, mrp=None)  # missing MRP

    class _Repo(_FakeProductRepo):
        def update(self, pid, fields):
            return fields

    monkeypatch.setattr(v, "get_product_repository", lambda: _Repo({"P1": half_catalogued}))
    monkeypatch.setattr(v, "_po_catalog_gate_on", lambda: True)

    class _SendablePORepo(_FakePORepo):
        def find_by_id(self, pid):
            return {
                "po_id": "PO1",
                "status": "DRAFT",
                "delivery_store_id": "BV-TEST-01",
                "items": [{"product_id": "P1"}],
            }

        def update(self, pid, fields):
            raise AssertionError("must not send an incomplete PO")

    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _SendablePORepo())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(send_po("PO1", current_user=_user()))
    assert exc.value.detail["code"] == "PO_LINES_INCOMPLETE"
