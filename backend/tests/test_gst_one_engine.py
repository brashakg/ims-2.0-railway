"""ONE GST engine across the purchase order, the purchase bill and the sale.

Covers the defects the adversarial verifier filed on `claude/po-gst-and-ux`:

  1. the two AUTOMATIC purchase-order doors (the per-power CL/lens draft and
     the demand-forecast draft) charged a flat 18% and stored no per-line
     tax_rate, so the bill drafted off them charged 0%;
  2. collapsing the state parser dropped the bare-2-digit fallback, so the GST
     portal's own display form "27-Maharashtra" resolved to nothing and an
     out-of-state B2B SALE billed CGST+SGST instead of IGST;
  3. the HSN prefix match answered for strings that are not HSNs, including
     "9021" -> 0% with full confidence (hearing-aid PARTS are 18%);
  6. the purchase ORDER and the purchase BILL could return OPPOSITE tax
     verdicts for the same vendor/store pair.

Everything is driven through the real functions -- no stubbed subjects.
"""
from __future__ import annotations

import os
import sys
import asyncio

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from api.routers import cl_po as cl  # noqa: E402
from api.routers import orders as o  # noqa: E402
from api.routers import purchase_invoices as pi  # noqa: E402
from api.services import purchase_invoice_engine as pinv  # noqa: E402
from api.services.gst_rates import resolve_gst_rate_strict  # noqa: E402


# ============================================================================
# Shared fixtures. Jharkhand = 20, Maharashtra = 27.
# ============================================================================

_JH_STORE = {
    "store_id": "BV-TEST-01",
    "entity_id": "E1",
    "gstin": "20AABCU9603R1ZM",
    "state": "Jharkhand",
    "state_code": "20",
}
_MH_VENDOR = {
    "vendor_id": "V1",
    "trade_name": "Mumbai Frames",
    "gstin": "27AACCA1234B1Z2",
}
_JH_VENDOR = {
    "vendor_id": "V1",
    "trade_name": "Ranchi Optics",
    "gstin": "20AACCA1234B1Z5",
}


class _Repo:
    def __init__(self, doc=None):
        self.doc = doc
        self.created = []

    def find_by_id(self, _id):
        return self.doc

    def create(self, doc):
        self.created.append(doc)
        return doc


class _Cursor(list):
    def limit(self, _n):
        return self


class _Coll:
    """One collection backed by a list of dicts; find_one matches on equality."""

    def __init__(self, docs):
        self.docs = list(docs)

    def find_one(self, query=None, *a, **kw):
        for d in self.docs:
            if all(d.get(k) == q for k, q in (query or {}).items()):
                return d
        return None

    def find(self, query=None, projection=None):
        return _Cursor(list(self.docs))


class _DB:
    def __init__(self, **colls):
        self._colls = {k: _Coll(val) for k, val in colls.items()}

    def get_collection(self, name):
        return self._colls.get(name, _Coll([]))


def _user():
    return {"user_id": "u1", "roles": ["ADMIN"], "active_store_id": "BV-TEST-01"}


# ============================================================================
# MUST-FIX 1 -- the two AUTOMATIC purchase-order doors
# ============================================================================


def _patch_cl_door(monkeypatch, db, po_repo, needs):
    monkeypatch.setattr(cl, "_get_db", lambda: db)
    monkeypatch.setattr(cl, "validate_store_access", lambda sid, u: sid)
    monkeypatch.setattr(cl, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(cl, "generate_po_number", lambda sid: "PO-CL-1")
    monkeypatch.setattr(cl, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(cl, "_read_gap_planner_needs", lambda sid, llid=None: needs)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _Repo(_JH_VENDOR))
    monkeypatch.setattr(v, "get_store_repository", lambda: _Repo(_JH_STORE))


_LENS_LINE = {
    "lens_line_id": "LL1",
    "brand": "Acuvue",
    "series": "Oasys",
    "hsn_code": "900130",
    "cost_price": 400.0,
    "preferred_vendor_id": "V1",
}
_LENS_NEED = [
    {
        "lens_line_id": "LL1",
        "sph": -2.0,
        "qty": 30,
        "description": "Acuvue Oasys",
        "unit_price": 400.0,
    }
]


def test_cl_po_auto_draft_charges_the_hsn_rate_not_a_flat_18(monkeypatch):
    """30 contact lenses at Rs 400. HSN 900130 -> 5% -> Rs 600 of GST.

    The old door booked round(12000 * 0.18) = Rs 2160 -- over by Rs 1560 on
    every automatic lens order -- and stored no per-line tax_rate at all.
    """
    po_repo = _Repo()
    _patch_cl_door(
        monkeypatch, _DB(lens_catalog=[_LENS_LINE], products=[]), po_repo, _LENS_NEED
    )
    body = cl.CLPOGenerateRequest(
        store_id="BV-TEST-01", source="gap-planner", dry_run=False
    )
    asyncio.run(cl.generate_cl_po(body, current_user=_user()))

    doc = po_repo.created[0]
    assert doc["subtotal"] == 12000.0
    assert doc["tax_amount"] == 600.0, "5% off HSN 900130, not a flat 18%"
    assert doc["total_amount"] == 12600.0
    line = doc["items"][0]
    assert line["tax_rate"] == 5.0
    assert line["hsn"] == "900130"
    assert line["gst_source"] == "hsn"
    # Jharkhand vendor into a Jharkhand shop -> CGST + SGST.
    assert (line["cgst"], line["sgst"], line["igst"]) == (300.0, 300.0, 0.0)
    assert doc["gst_summary"] == {
        "cgst": 300.0,
        "sgst": 300.0,
        "igst": 0.0,
        "tax": 600.0,
    }


def test_the_bill_drafted_off_an_auto_po_is_not_tax_free(monkeypatch):
    """lines_from_grn reads po_line['tax_rate']. The automatic doors never
    wrote one, so the vendor bill drafted from those goods charged Rs 0 and
    Rs 600 of input credit never reached the 3-way match."""
    po_repo = _Repo()
    _patch_cl_door(
        monkeypatch, _DB(lens_catalog=[_LENS_LINE], products=[]), po_repo, _LENS_NEED
    )
    asyncio.run(
        cl.generate_cl_po(
            cl.CLPOGenerateRequest(
                store_id="BV-TEST-01", source="gap-planner", dry_run=False
            ),
            current_user=_user(),
        )
    )
    po = po_repo.created[0]

    grn = {"items": [{"product_id": "LL1", "accepted_qty": 30, "product_name": "A"}]}
    lines = pinv.lines_from_grn(grn, po)
    assert [ln["gst_rate"] for ln in lines] == [5.0]
    computed = pinv.compute_invoice(lines, _JH_VENDOR["gstin"], _JH_STORE["gstin"], None)
    assert computed["taxable_total"] == 12000.0
    assert computed["tax_total"] == 600.0
    assert computed["total"] == 12600.0


def test_forecast_auto_draft_charges_the_hsn_rate_not_a_flat_18(monkeypatch):
    """The demand-forecast door: same flat 18%, same missing tax_rate."""
    product = {
        "product_id": "P1",
        "name": "Ray-Ban RB3025",
        "sku": "RB1",
        "hsn_code": "900311",
        "gst_rate": 5.0,
        "cost_price": 1000.0,
        "quantity": 0,
        "preferred_vendor_id": "V1",
        "reorder_quantity": 10,
    }
    order = {
        "items": [
            {
                "product_id": "P1",
                "quantity": 90,
                "product_name": "Ray-Ban",
                "sku": "RB1",
            }
        ]
    }
    po_repo = _Repo()
    monkeypatch.setattr(v, "_get_db", lambda: _DB(orders=[order], products=[product]))
    monkeypatch.setattr(v, "is_online_store", lambda db_, sid: False)
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _Repo(_MH_VENDOR))
    monkeypatch.setattr(v, "get_store_repository", lambda: _Repo(_JH_STORE))
    monkeypatch.setattr(v, "generate_po_number", lambda sid: "PO-FC-1")

    body = v.ForecastPoRequest(store_id="BV-TEST-01", dry_run=False)
    asyncio.run(v.create_pos_from_forecast(body, current_user=_user()))

    doc = po_repo.created[0]
    line = doc["items"][0]
    assert line["tax_rate"] == 5.0, "HSN 900311 frames are 5%, not 18%"
    assert line["hsn"] == "900311"
    # Maharashtra vendor into a Jharkhand shop -> one IGST charge, no CGST/SGST.
    assert line["cgst"] == 0.0 and line["sgst"] == 0.0
    assert line["igst"] == round(doc["tax_amount"], 2)
    assert doc["tax_amount"] == round(doc["subtotal"] * 0.05, 2)
    assert doc["interstate"] is True


# ============================================================================
# MUST-FIX 2 -- "27-Maharashtra" is the GST portal's own display form
# ============================================================================

_PORTAL_FORMS = [
    ("27-Maharashtra", "27"),
    ("20-Jharkhand", "20"),
    ("27 Maharashtra", "27"),
    ("27AAFCM3456N1Z", "27"),  # a 14-char (truncated / mistyped) GSTIN
]


@pytest.mark.parametrize("raw,code", _PORTAL_FORMS)
def test_invoice_state_code_reads_the_gst_portal_display_form(raw, code):
    assert o._invoice_state_code(raw) == code


def test_a_bare_two_digit_code_must_still_be_a_real_state():
    # The fallback is not "take any two digits": 1900 -> 19 is West Bengal,
    # but nothing maps to a state that does not exist.
    assert o._invoice_state_code("1900") == "19"
    assert o._invoice_state_code("xyz") == ""
    assert o._invoice_state_code("0") == ""


@pytest.mark.parametrize("raw,code", _PORTAL_FORMS)
def test_out_of_state_b2b_sale_is_igst_when_the_state_reads_like_the_portal(raw, code):
    """THE MONEY: a Maharashtra customer buying from the Jharkhand shop must be
    billed IGST and filed in the inter-state GSTR-1 bucket. With the bare
    2-digit fallback missing, '27-Maharashtra' resolved to '' -> 'customer
    state unknown' -> intra-state -> CGST + SGST on a real inter-state sale."""
    if code == "20":
        pytest.skip("20-Jharkhand is the shop's own state -- covered separately")
    store = {"gstin": "20AABCU9603R1ZM", "state_code": "20"}
    customer = {"gstin": None, "billing_address": {"state": raw}}
    items = [{"gst_rate": 5.0, "taxable_value": 1000.0, "tax_amount": 50.0}]
    split = o._build_invoice_gst_split(items, store, customer)
    assert split["interstate"] is True
    assert split["place_of_supply"] == "27"
    assert split["place_of_supply_assumed"] is False
    assert split["rows"][0]["igst"] == 50.0
    assert split["rows"][0]["cgst"] == 0.0
    assert split["rows"][0]["sgst"] == 0.0


def test_same_state_sale_in_portal_form_stays_cgst_sgst():
    store = {"gstin": "20AABCU9603R1ZM", "state_code": "20"}
    customer = {"billing_address": {"state": "20-Jharkhand"}}
    items = [{"gst_rate": 5.0, "taxable_value": 1000.0, "tax_amount": 50.0}]
    split = o._build_invoice_gst_split(items, store, customer)
    assert split["interstate"] is False
    assert (split["rows"][0]["cgst"], split["rows"][0]["sgst"]) == (25.0, 25.0)


# ============================================================================
# MUST-FIX 3 -- the HSN match must not answer for things that are not HSNs,
# and must not answer a HEADING out of one sub-item's rate
# ============================================================================


def test_hearing_aid_heading_9021_refuses_instead_of_answering_zero_percent():
    """The table holds ONE child of 9021 -- 902140, complete devices at NIL.
    Heading 9021 also covers PARTS at 18%. Answering '9021 -> 0%' zero-rated a
    hearing-aid part on a purchase order, with full confidence and no warning."""
    rate, missing = resolve_gst_rate_strict("9021")
    assert rate is None
    assert missing and "9021" in missing
    assert "6-digit" in missing


@pytest.mark.parametrize(
    "bad", ["902", "91", "39", "85", "998", "90031", "9", "900311x"]
)
def test_a_string_that_is_not_an_hsn_length_is_refused(bad):
    """A real HSN is 4, 6 or 8 digits. Anything else is a typo or a truncation
    and must never be answered by walking the table for codes that start with
    it -- '91' used to answer 18%, '90031' used to answer 5%, '902' 0%."""
    rate, missing = resolve_gst_rate_strict(bad)
    assert rate is None, f"{bad!r} must not resolve to {rate}"
    assert missing and "4, 6 or 8 digits" in missing


@pytest.mark.parametrize(
    "good,rate",
    [
        ("9003", 5.0),
        ("9001", 5.0),
        ("900311", 5.0),
        ("900410", 18.0),
        ("902140", 0.0),
        ("9993", 0.0),
    ],
)
def test_the_headings_and_codes_that_genuinely_resolve_still_do(good, rate):
    assert resolve_gst_rate_strict(good) == (rate, None)


def test_an_eight_digit_tariff_item_inherits_its_six_digit_parent():
    """A sub-item is NARROWER than the entry above it, so it may take that
    entry's rate. The reverse -- a heading taking one sub-item's rate -- is
    exactly what the 9021 bug was."""
    assert resolve_gst_rate_strict("90031100") == (5.0, None)


def test_ambiguous_heading_9004_still_says_which_rates_it_covers():
    rate, missing = resolve_gst_rate_strict("9004")
    assert rate is None
    assert "5% / 18%" in missing


# ============================================================================
# MUST-FIX 6 -- the ORDER and the BILL must not return opposite verdicts
# ============================================================================


def _po_verdict(vendor, store_doc):
    out = v.build_po_gst(
        [
            {
                "product_id": "P1",
                "product_name": "F",
                "quantity": 1,
                "unit_price": 1000,
                "hsn": "900311",
            }
        ],
        lambda _pid: None,
        vendor,
        store_doc,
    )
    return out["interstate"]


def _bill_verdict(vendor, recipient_gstin):
    computed = pinv.compute_invoice(
        [{"product_id": "P1", "qty": 1, "unit_price": 1000, "gst_rate": 5.0}],
        (vendor or {}).get("gstin"),
        recipient_gstin,
        None,
    )
    return computed["interstate"]


def test_an_unregistered_out_of_state_vendor_reads_the_same_on_both():
    """The verifier's first disagreement: a vendor with NO GSTIN whose ADDRESS
    says Maharashtra. The bill cannot prove a supplier state from a missing
    GSTIN and stays intra-state; the order used to read the address and say
    IGST. Both now answer from the GST numbers, and agree."""
    vendor = {"vendor_id": "V1", "state": "Maharashtra"}
    assert _po_verdict(vendor, _JH_STORE) is False
    assert _bill_verdict(vendor, _JH_STORE["gstin"]) is False


def test_a_registered_out_of_state_vendor_is_igst_on_both():
    assert _po_verdict(_MH_VENDOR, _JH_STORE) is True
    assert _bill_verdict(_MH_VENDOR, _JH_STORE["gstin"]) is True


_MH_SHOP_WITH_JH_GSTIN = {
    "store_id": "WO-TEST-01",
    "entity_id": "E1",
    "gstin": "20AABCU9603R1ZM",  # the entity's JH primary, not a MH number
    "state": "Maharashtra",
    "state_code": "27",
}


def test_the_order_reads_the_place_of_supply_off_the_shop_not_its_paperwork():
    """stores.py falls back to the entity's PRIMARY registration when the
    entity holds none in the shop's state, so a Maharashtra shop can be stamped
    with a Jharkhand GSTIN. Goods delivered in Maharashtra from a Maharashtra
    vendor are CGST + SGST; the shop's own state decides, not the fallback."""
    assert _po_verdict(_MH_VENDOR, _MH_SHOP_WITH_JH_GSTIN) is False


def _entity_db():
    return _DB(
        entities=[
            {
                "entity_id": "E1",
                "gstins": [
                    {
                        "gstin": "20AABCU9603R1ZM",
                        "state_code": "20",
                        "is_primary": True,
                    },
                    {"gstin": "27AABCU9603R1ZX", "state_code": "27"},
                ],
            }
        ]
    )


def test_the_bill_keeps_todays_recipient_until_the_owner_arms_the_change(monkeypatch):
    """The residual disagreement, held OPEN on purpose. The bill resolves the
    recipient as the entity's PRIMARY GSTIN, ignoring the receiving shop, so a
    Maharashtra shop's purchase from a Maharashtra vendor is booked inter-state
    while the order says intra-state. Changing the bill would RE-CLASSIFY what
    live purchase bills produce, and this business does not re-state -- so the
    default is unchanged and the owner arms it."""
    monkeypatch.setattr(pi, "_recipient_state_follows_store", lambda: False)
    recipient = pi._resolve_recipient(
        _entity_db(), "E1", None, pi._store_place_of_supply(_MH_SHOP_WITH_JH_GSTIN)
    )
    assert recipient["recipient_gstin"] == "20AABCU9603R1ZM"  # the JH primary
    assert _bill_verdict(_MH_VENDOR, recipient["recipient_gstin"]) is True


def test_arming_the_gate_makes_the_bill_agree_with_the_order(monkeypatch):
    """Same purchase, gate ON: the receiving shop's own state picks which of our
    GSTINs receives, so the bill lands on the Maharashtra registration and
    agrees with the purchase order -- CGST + SGST on both."""
    monkeypatch.setattr(pi, "_recipient_state_follows_store", lambda: True)
    recipient = pi._resolve_recipient(
        _entity_db(), "E1", None, pi._store_place_of_supply(_MH_SHOP_WITH_JH_GSTIN)
    )
    assert recipient["recipient_gstin"] == "27AABCU9603R1ZX"
    assert _bill_verdict(_MH_VENDOR, recipient["recipient_gstin"]) is False
    assert _po_verdict(_MH_VENDOR, _MH_SHOP_WITH_JH_GSTIN) is False


def test_one_state_parser_answers_for_the_order_the_bill_and_the_sale():
    """Two parsers is HOW the order and the bill came to disagree. The purchase
    engine's state_code_of, the sale's _invoice_state_code and the shared
    org_validation.resolve_state_code must be the same function."""
    from api.services.org_validation import resolve_state_code

    for raw in [
        "27AACCA1234B1Z2",
        "27-Maharashtra",
        "MH",
        "Maharashtra",
        "27",
        "20-Jharkhand",
        "JH",
        "",
        "xyz",
        "99AAAAA0000A1Z5",
        "27AAFCM3456N1Z",
    ]:
        engine = pinv.state_code_of(raw)
        shared = resolve_state_code(raw)
        sale = o._invoice_state_code(raw)
        assert engine == shared == sale, raw


def test_one_split_answers_for_the_order_the_bill_and_the_sale():
    from api.services.gst_rates import split_gst

    for taxable in [4497.0, 19800.0, 0.2, 24297.0, 20.0, 100.2]:
        line = pinv.split_line_gst(taxable, 5.0, False)
        assert (line["cgst"], line["sgst"], line["igst"]) == split_gst(
            line["gst"], False
        )
        assert round(line["cgst"] + line["sgst"], 2) == line["gst"]
