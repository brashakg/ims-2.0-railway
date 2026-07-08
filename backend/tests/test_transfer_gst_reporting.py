"""
IMS 2.0 - Inter-GSTIN transfer deemed supply: per-line rates + sender GSTR-1/3B
================================================================================
Covers the two GST-filing gaps around transfers._book_mirror_purchase:

GAP B (per-line rates): the mirror vendor_bill used a FLAT 18% on the whole
transfer value. Frames / optical lenses are 5% under GST 2.0, so the bill (and
therefore BOTH entities' GST returns) carried the wrong tax. It now resolves
each line via gst_rates.resolve_gst_rate over the product master's
hsn_code/category and stores per-line detail (purchase_invoices line shape).

GAP A (sender outward): the sending GSTIN's deemed supply never reached its
GSTR-1 / GSTR-3B 3.1(a) (both read only orders). reports.py now reads the SAME
mirror vendor_bills doc for the sender side (keyed by from_store_id), so the
two filings reconcile BY CONSTRUCTION.

THE ROUND-TRIP INVARIANT (asserted paisa-exact below, with mixed 5%/18% lines):

    sender GSTR-1 B2B tax  ==  sender GSTR-3B 3.1(a) outward tax
                           ==  receiver GSTR-3B Table 4 ITC claim

Also covered: the same-entity same-state gate books NOTHING (unchanged);
idempotent re-run; the sending store never claims ITC on its own outward
supply. ITC scope is the RECEIVING GSTIN: every store filing under that GSTIN
sees the same Table 4 (one GSTIN = one filing), while stores under any OTHER
GSTIN -- including the sender's -- claim nothing.

Adversarial-review hardening (PR #899 follow-up), all tested here:
  * portal B2B emits ONE itm_det PER RATE (a blended block fails the offline
    tool's txval*rt==iamt check); normal order rows keep the single-item shape.
  * a missing destination-state GSTIN stays EMPTY (never the sender's own
    GSTIN via the old gstins[0] fallback) -> loud validation + dropped row.
  * short-ship: quantity_received==0 bills NOTHING for that line (no ITC on
    goods never received); requested is only a fallback when the field is
    absent (legacy docs).
  * zero-value bills emit no GSTR-1 row (portal rejects all-zero invoices).
  * bill dates are IST-frame, so the deemed supply files in the same IST month
    as the orders in the return.

All tests are pure (no live DB) via a small in-memory Mongo-subset evaluator
(handles the exact operators the collectors use: $exists/$ne/$nin/$in/$gte/
$lte/$or/$nor and aggregate $match+$group/$sum).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.transfers import _book_mirror_purchase  # noqa: E402
import api.routers.reports as reports  # noqa: E402
import api.services.gst_rates as gst_rates  # noqa: E402


# ============================================================================
# In-memory Mongo-subset evaluator
# ============================================================================


def _match(doc, query):
    """Evaluate the Mongo query subset the GST collectors use. NO silent
    fallbacks: an unknown operator raises so a query-shape change can't pass
    vacuously."""
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, c) for c in cond):
                return False
        elif key == "$nor":
            if any(_match(doc, c) for c in cond):
                return False
        elif key == "$and":
            if not all(_match(doc, c) for c in cond):
                return False
        elif isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
            val = doc.get(key)
            for op, arg in cond.items():
                if op == "$exists":
                    if (key in doc and doc.get(key) is not None) != bool(arg):
                        return False
                elif op == "$ne":
                    if val == arg:
                        return False
                elif op == "$nin":
                    if val in arg:
                        return False
                elif op == "$in":
                    # Mongo: {$in: [null]} also matches a MISSING field --
                    # doc.get() returning None reproduces that.
                    if val not in arg:
                        return False
                elif op == "$gte":
                    if val is None or not (val >= arg):
                        return False
                elif op == "$lte":
                    if val is None or not (val <= arg):
                        return False
                else:
                    raise NotImplementedError(f"operator {op} not supported")
        else:
            if doc.get(key) != cond:
                return False
    return True


class MiniColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query=None, projection=None):
        return [dict(d) for d in self.docs if _match(d, query)]

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def aggregate(self, pipeline):
        rows = [dict(d) for d in self.docs]
        for stage in pipeline:
            if "$match" in stage:
                rows = [r for r in rows if _match(r, stage["$match"])]
            elif "$group" in stage:
                spec = stage["$group"]
                if not rows:
                    return iter([])
                acc = {"_id": None}
                for field, expr in spec.items():
                    if field == "_id":
                        continue
                    ref = expr["$sum"]
                    acc[field] = sum(
                        float(r.get(ref[1:], 0) or 0) for r in rows
                    )
                rows = [acc]
            else:
                raise NotImplementedError(f"stage {list(stage)} not supported")
        return iter(rows)


class MiniDB:
    """One DB object usable by BOTH transfers helpers (get_collection) and
    reports collectors (db["name"]) -- so the bill _book_mirror_purchase writes
    is literally the doc the GSTR collectors read."""

    def __init__(self, colls=None):
        self._colls = dict(colls or {})

    def get_collection(self, name):
        return self._colls.setdefault(name, MiniColl())

    def __getitem__(self, name):
        return self.get_collection(name)


# ============================================================================
# Fixture data: 2 entities / 3 GSTIN-relevant stores + a mixed 5%/18% transfer
# ============================================================================

SEND_GSTIN_JH = "20AAPFU0939F1ZV"  # ent_send @ Jharkhand (20)
SEND_GSTIN_MH = "27AAPFU0939F1ZX"  # ent_send @ Maharashtra (27) -- same PAN
RECV_GSTIN_MH = "27BBGAA1234J1ZV"  # ent_recv @ Maharashtra (27)


def _mini_db():
    return MiniDB(
        {
            "stores": MiniColl(
                [
                    {
                        "store_id": "jh_store",
                        "store_name": "Ranchi",
                        "entity_id": "ent_send",
                        "state": "Jharkhand",
                        "state_code": "20",
                        "gstin": SEND_GSTIN_JH,
                    },
                    {
                        "store_id": "jh_store_2",
                        "store_name": "Dhanbad",
                        "entity_id": "ent_send",
                        "state": "Jharkhand",
                        "state_code": "20",
                        "gstin": SEND_GSTIN_JH,
                    },
                    {
                        "store_id": "mh_store",
                        "store_name": "Pune",
                        "entity_id": "ent_recv",
                        "state": "Maharashtra",
                        "state_code": "27",
                        "gstin": RECV_GSTIN_MH,
                    },
                    {
                        "store_id": "mh_store_2",
                        "store_name": "Mumbai",
                        "entity_id": "ent_recv",
                        "state": "Maharashtra",
                        "state_code": "27",
                        "gstin": RECV_GSTIN_MH,
                    },
                    {
                        "store_id": "mh_send_branch",
                        "store_name": "Nagpur",
                        "entity_id": "ent_send",
                        "state": "Maharashtra",
                        "state_code": "27",
                        "gstin": SEND_GSTIN_MH,
                    },
                ]
            ),
            "entities": MiniColl(
                [
                    {
                        "entity_id": "ent_send",
                        "gstins": [
                            {"state_code": "20", "gstin": SEND_GSTIN_JH},
                            {"state_code": "27", "gstin": SEND_GSTIN_MH},
                        ],
                    },
                    {
                        "entity_id": "ent_recv",
                        "gstins": [{"state_code": "27", "gstin": RECV_GSTIN_MH}],
                    },
                ]
            ),
            "products": MiniColl(
                [
                    {
                        "product_id": "prod_frame",
                        "hsn_code": "900311",
                        "category": "FRAME",
                    },
                    {
                        "product_id": "prod_sg",
                        "hsn_code": "900410",
                        "category": "SUNGLASS",
                    },
                ]
            ),
            "orders": MiniColl(),
            "customers": MiniColl(),
            "credit_note_ledger": MiniColl(),
            "vendor_bills": MiniColl(),
        }
    )


def _mixed_transfer(from_store="jh_store", to_store="mh_store", tid="trf_gst_1"):
    """FRAME 2 x 1000 (5%) + SUNGLASS 1 x 2000 (18%), completed 05-Jun-2026.

    Expected (interstate): taxable 4000.00; IGST = 100.00 (frame) + 360.00
    (sunglass) = 460.00; invoice value 4460.00.
    """
    return {
        "id": tid,
        "transfer_number": "TRF-202606-777",
        "from_location_id": from_store,
        "to_location_id": to_store,
        "from_location_name": "Ranchi Store",
        "to_location_name": "Pune Store",
        "total_value": 4000.0,
        "items": [
            {
                "product_id": "prod_frame",
                "product_name": "Acetate Frame",
                "quantity_requested": 2,
                "quantity_received": 2,
                "unit_cost": 1000.0,
            },
            {
                "product_id": "prod_sg",
                "product_name": "Polarized Sunglass",
                "quantity_requested": 1,
                "quantity_received": 1,
                "unit_cost": 2000.0,
            },
        ],
        "completed_at": "2026-06-05T18:00:00",
    }


@pytest.fixture()
def static_rates(monkeypatch):
    """Pin resolve_gst_rate to the static canonical table (no editable-master /
    cache interference from other tests)."""
    monkeypatch.setattr(
        gst_rates, "_load_lookup", lambda: {"by_hsn": {}, "by_cat": {}}
    )


def _book(db, transfer):
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)


# ============================================================================
# GAP B: per-line rate resolution on the mirror bill
# ============================================================================


def test_mixed_rate_lines_resolve_per_product(static_rates):
    db = _mini_db()
    _book(db, _mixed_transfer())

    bills = db["vendor_bills"].docs
    assert len(bills) == 1
    b = bills[0]

    # Header: per-line 5% + 18%, all IGST (JH -> MH is inter-state).
    assert b["interstate"] is True
    assert b["taxable_amount"] == 4000.0
    assert b["igst_total"] == 460.0  # 2000*5% + 2000*18%
    assert b["cgst_total"] == 0.0 and b["sgst_total"] == 0.0
    assert b["tax_amount"] == 460.0
    assert b["total_amount"] == 4460.0

    # Per-line detail: purchase_invoices line shape with hsn + rate + taxable
    # + per-line tax split.
    lines = {ln["product_id"]: ln for ln in b["lines"]}
    frame, sg = lines["prod_frame"], lines["prod_sg"]
    assert frame["hsn"] == "900311" and frame["gst_rate"] == 5.0
    assert frame["taxable"] == 2000.0 and frame["igst"] == 100.0
    assert frame["qty"] == 2.0 and frame["unit_price"] == 1000.0
    assert sg["hsn"] == "900410" and sg["gst_rate"] == 18.0
    assert sg["taxable"] == 2000.0 and sg["igst"] == 360.0
    # Header == sum(lines), paisa-exact.
    assert round(sum(ln["taxable"] for ln in b["lines"]), 2) == b["taxable_amount"]
    assert round(sum(ln["igst"] for ln in b["lines"]), 2) == b["igst_total"]

    # Reporting keys for GAP A.
    assert b["from_store_id"] == "jh_store"
    assert b["to_store_id"] == "mh_store"
    assert b["recipient_entity_id"] == "ent_recv"
    assert b["recipient_gstin"] == RECV_GSTIN_MH
    assert b["vendor_gstin"] == SEND_GSTIN_JH
    assert b["supply_place_recipient"] == "27"


def test_intrastate_per_line_split_paisa_exact(static_rates):
    """Inter-entity SAME state: per-line CGST+SGST == per-line tax, and header
    equals the rounded line sums even on odd-paise lines."""
    db = _mini_db()
    # ent_send JH -> a hypothetical ent_recv JH store: reuse mh_store but move
    # it to state 20 for this test.
    for s in db["stores"].docs:
        if s["store_id"] == "mh_store":
            s["state_code"] = "20"
            s["state"] = "Jharkhand"
    t = _mixed_transfer()
    # Odd-paise costs: 3 x 333.35 = 1000.05 @5%; 1 x 466.63 @18%.
    t["items"][0].update(
        {"quantity_received": 3, "quantity_requested": 3, "unit_cost": 333.35}
    )
    t["items"][1].update({"unit_cost": 466.63})
    _book(db, t)

    bills = db["vendor_bills"].docs
    assert len(bills) == 1
    b = bills[0]
    assert b["interstate"] is False
    assert b["igst_total"] == 0.0
    for ln in b["lines"]:
        # Residual split: CGST + SGST == the line's tax to the paisa.
        line_tax = round(ln["taxable"] * ln["gst_rate"] / 100.0, 2)
        assert round(ln["cgst"] + ln["sgst"], 2) == line_tax
        assert ln["igst"] == 0.0
    assert (
        round(sum(ln["cgst"] + ln["sgst"] for ln in b["lines"]), 2)
        == b["tax_amount"]
    )
    assert round(b["cgst_total"] + b["sgst_total"], 2) == b["tax_amount"]


def test_same_entity_same_state_books_nothing(static_rates):
    """Gate unchanged: no GSTIN boundary -> no mirror bill, even with items."""
    db = _mini_db()
    _book(db, _mixed_transfer(from_store="jh_store", to_store="jh_store_2"))
    assert db["vendor_bills"].docs == []


def test_idempotent_rerun_single_bill(static_rates):
    db = _mini_db()
    t = _mixed_transfer()
    _book(db, t)
    _book(db, t)  # re-run (e.g. a retried COMPLETE) must not double-book
    assert len(db["vendor_bills"].docs) == 1


# ============================================================================
# GAP A: sender GSTR-1 B2B rows + HSN summary
# ============================================================================


def test_sender_gstr1_carries_deemed_supply_b2b(static_rates, monkeypatch):
    db = _mini_db()
    _book(db, _mixed_transfer())
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)

    rep = reports._compute_gstr1("2026-06", "jh_store")

    # One B2B row: the recipient is our sister GSTIN (a registered person).
    assert len(rep["b2b"]) == 1
    row = rep["b2b"][0]
    assert row["deemedSupply"] is True
    assert row["documentType"] == "STOCK_TRANSFER"
    assert row["customerGSTIN"] == RECV_GSTIN_MH
    assert row["invoiceNumber"] == "TRF/TRF-202606-777"
    assert row["invoiceDate"] == "2026-06-05"
    assert row["placeOfSupply"] == "27"  # recipient state
    assert row["taxableValue"] == 4000.0
    assert row["igst"] == 460.0
    assert row["cgst"] == 0.0 and row["sgst"] == 0.0
    assert row["invoiceValue"] == 4460.0

    # Header totals include the deemed supply.
    assert rep["totalInvoices"] == 1
    assert rep["totalTaxableValue"] == 4000.0
    assert rep["totalTax"] == 460.0

    # HSN summary is PER-LINE (mixed 5%/18% preserved, not lumped).
    hsn = {(r["hsnCode"], r["gstRate"]): r for r in rep["hsnSummary"]}
    assert hsn[("900311", 5)]["taxableValue"] == 2000.0
    assert hsn[("900311", 5)]["igst"] == 100.0
    assert hsn[("900410", 18)]["taxableValue"] == 2000.0
    assert hsn[("900410", 18)]["igst"] == 360.0

    # No missing-GSTIN validation warnings for a well-formed bill.
    assert all(
        "recipient GSTIN" not in i["issue"] for i in rep["validation"]["issues"]
    )


def test_receiver_gstr1_has_no_deemed_row(static_rates, monkeypatch):
    """The outward row belongs to the SENDER only (keyed on from_store_id)."""
    db = _mini_db()
    _book(db, _mixed_transfer())
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    rep = reports._compute_gstr1("2026-06", "mh_store")
    assert rep["b2b"] == []
    assert rep["totalTax"] == 0.0


def test_gstr1_flags_missing_recipient_gstin(static_rates, monkeypatch):
    """A deemed-supply bill without a recipient GSTIN is dropped by the portal
    B2B export -- the CA must see a warning."""
    db = _mini_db()
    _book(db, _mixed_transfer())
    db["vendor_bills"].docs[0]["recipient_gstin"] = ""
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    rep = reports._compute_gstr1("2026-06", "jh_store")
    assert any(
        "recipient GSTIN" in i["issue"] for i in rep["validation"]["issues"]
    )


def test_gstr1_wrong_month_excludes_bill(static_rates, monkeypatch):
    db = _mini_db()
    _book(db, _mixed_transfer())
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    rep = reports._compute_gstr1("2026-07", "jh_store")
    assert rep["b2b"] == []


# ============================================================================
# THE ROUND-TRIP INVARIANT: sender outward == receiver ITC, paisa-exact
# ============================================================================


def test_roundtrip_sender_outward_equals_receiver_itc(static_rates, monkeypatch):
    """Inter-entity, mixed 5%/18%: the transfer books ONE mirror bill; the
    sender's GSTR-1 tax and GSTR-3B 3.1(a) equal the receiver's GSTR-3B Table 4
    ITC claim to the paisa."""
    db = _mini_db()
    _book(db, _mixed_transfer())
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)

    sender_g1 = reports._compute_gstr1("2026-06", "jh_store")
    sender_3b = reports._compute_gstr3b("2026-06", "jh_store")
    receiver_3b = reports._compute_gstr3b("2026-06", "mh_store")
    sister_3b = reports._compute_gstr3b("2026-06", "mh_store_2")

    # Sender outward (3.1(a)).
    assert sender_3b["outwardTaxableValue"] == 4000.0
    assert sender_3b["outwardTaxableSupplies"]["integratedTax"] == 460.0
    assert sender_3b["outwardTaxableSupplies"]["centralTax"] == 0.0
    assert sender_3b["outwardTaxableSupplies"]["stateTax"] == 0.0

    # GSTR-1 total tax == GSTR-3B outward tax (same filing, two forms).
    assert sender_g1["totalTax"] == sender_3b["outwardTaxableSupplies"]["integratedTax"]

    # THE INVARIANT: receiver ITC claim == sender outward IGST, paisa-exact.
    assert (
        receiver_3b["itcAvailable"]["integratedTax"]
        == sender_3b["outwardTaxableSupplies"]["integratedTax"]
        == 460.0
    )

    # The sender must NOT claim ITC on its own outward supply.
    assert sender_3b["itcAvailable"]["integratedTax"] == 0.0
    # ITC scope is the RECEIVING GSTIN: mh_store_2 files under the SAME GSTIN
    # as mh_store, so its Table 4 is the SAME filing and must show the SAME
    # credit (one GSTIN = one GSTR-3B; a store-scoped Table 4 would be correct
    # under no filing convention for a multi-store GSTIN).
    assert (
        sister_3b["itcAvailable"]["integratedTax"]
        == receiver_3b["itcAvailable"]["integratedTax"]
        == 460.0
    )

    # Receiver reports no outward supply from this transfer.
    assert receiver_3b["outwardTaxableSupplies"]["integratedTax"] == 0.0

    # Cash liability: sender pays the IGST in cash (no ITC to offset here);
    # receiver's credit sits in Table 4.
    assert sender_3b["taxPaidCash"]["integratedTax"] == 460.0
    assert receiver_3b["taxPaidCash"]["integratedTax"] == 0.0


def test_roundtrip_same_entity_cross_state_odd_paise(static_rates, monkeypatch):
    """Same PAN, two states (Sch I distinct persons): sender GSTIN reports the
    outward IGST; ONLY the receiving branch claims the ITC -- entity-level
    scoping alone would have let the sender net its own liability to zero.
    Odd-paise lines prove the paisa-exact reconciliation."""
    db = _mini_db()
    t = _mixed_transfer(from_store="jh_store", to_store="mh_send_branch", tid="trf_gst_2")
    # Odd-paise: 3 x 333.35 = 1000.05 @5% -> 50.00 (round(50.0025));
    #            1 x 466.63 @18% -> 83.99 (round(83.9934)). IGST = 133.99.
    t["items"][0].update(
        {"quantity_received": 3, "quantity_requested": 3, "unit_cost": 333.35}
    )
    t["items"][1].update({"unit_cost": 466.63})
    _book(db, t)
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)

    bill = db["vendor_bills"].docs[0]
    assert bill["interstate"] is True
    assert bill["recipient_entity_id"] == "ent_send"  # same PAN, other GSTIN
    assert bill["vendor_gstin"] == SEND_GSTIN_JH
    assert bill["recipient_gstin"] == SEND_GSTIN_MH

    sender_3b = reports._compute_gstr3b("2026-06", "jh_store")
    receiver_3b = reports._compute_gstr3b("2026-06", "mh_send_branch")
    sender_sister_3b = reports._compute_gstr3b("2026-06", "jh_store_2")

    expected_igst = round(round(1000.05 * 0.05, 2) + round(466.63 * 0.18, 2), 2)
    assert bill["igst_total"] == expected_igst

    # Sender outward == receiver ITC, paisa-exact, odd paise and all.
    assert sender_3b["outwardTaxableSupplies"]["integratedTax"] == expected_igst
    assert receiver_3b["itcAvailable"]["integratedTax"] == expected_igst

    # The sender's GSTIN (20...) must not claim the credit -- the bill belongs
    # to the 27... GSTIN of the SAME entity. jh_store_2 files under the same
    # 20... GSTIN as the sender, so it claims nothing either.
    assert sender_3b["itcAvailable"]["integratedTax"] == 0.0
    assert sender_sister_3b["itcAvailable"]["integratedTax"] == 0.0
    # And the sister store reports no outward either (it didn't send anything).
    assert sender_sister_3b["outwardTaxableSupplies"]["integratedTax"] == 0.0


def test_regular_purchase_bill_itc_unaffected(static_rates, monkeypatch):
    """The receiver-store scoping ($nor on source_transfer_id/to_store_id) must
    not disturb ordinary vendor purchase bills: entity-scoped ITC as before."""
    db = _mini_db()
    db["vendor_bills"].insert_one(
        {
            "bill_id": "pi_1",
            "invoice_date": "2026-06-10",
            "bill_date": "2026-06-10",
            "recipient_entity_id": "ent_send",
            "status": "OUTSTANDING",
            "itc_eligible": True,
            "taxable_amount": 1000.0,
            "igst_total": 0.0,
            "cgst_total": 25.0,
            "sgst_total": 25.0,
        }
    )
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    rep = reports._compute_gstr3b("2026-06", "jh_store")
    assert rep["itcAvailable"]["centralTax"] == 25.0
    assert rep["itcAvailable"]["stateTax"] == 25.0
    # A regular purchase bill is nobody's outward supply.
    assert rep["outwardTaxableSupplies"]["integratedTax"] == 0.0
    assert rep["outwardTaxableSupplies"]["centralTax"] == 0.0


# ============================================================================
# REVIEW BLOCKER 1: portal B2B emits one itm_det PER RATE
# ============================================================================


def test_portal_b2b_one_item_per_rate(static_rates, monkeypatch):
    """A mixed 5%/18% deemed-supply invoice must export one itm_det per rate,
    each self-consistent (txval * rt == iamt) -- a single blended block (rt=5
    carrying the whole 460 of tax on 4000 taxable) fails the offline tool's
    item validation and the upload is rejected."""
    from api.services.gstn_export import to_gstr1_json

    db = _mini_db()
    _book(db, _mixed_transfer())
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    rep = reports._compute_gstr1("2026-06", "jh_store")
    out = to_gstr1_json(rep, gstin=rep["gstin"], period="2026-06")

    assert len(out["b2b"]) == 1
    bucket = out["b2b"][0]
    assert bucket["ctin"] == RECV_GSTIN_MH
    inv = bucket["inv"][0]
    assert inv["val"] == 4460.0
    itms = inv["itms"]
    assert [it["num"] for it in itms] == [1, 2]

    by_rate = {it["itm_det"]["rt"]: it["itm_det"] for it in itms}
    assert set(by_rate) == {5.0, 18.0}
    for rt, det in by_rate.items():
        # Self-validation the portal enforces per item.
        assert round(det["txval"] * rt / 100.0, 2) == det["iamt"]
        assert det["camt"] == 0.0 and det["samt"] == 0.0
    assert by_rate[5.0]["txval"] == 2000.0 and by_rate[5.0]["iamt"] == 100.0
    assert by_rate[18.0]["txval"] == 2000.0 and by_rate[18.0]["iamt"] == 360.0
    # Items reconcile with the invoice value.
    assert (
        round(sum(d["txval"] + d["iamt"] for d in by_rate.values()), 2)
        == inv["val"]
    )


def test_portal_b2b_single_item_unchanged_for_order_rows():
    """Rows WITHOUT rateLines (normal order-derived B2B rows) keep the original
    single-item export shape byte-identically."""
    from api.services.gstn_export import _build_b2b

    row = {
        "customerGSTIN": "07AAACR0000A1ZZ",
        "invoiceNumber": "INV-1",
        "invoiceDate": "2026-06-10",
        "invoiceValue": 1050.0,
        "taxableValue": 1000.0,
        "cgst": 25.0,
        "sgst": 25.0,
        "igst": 0.0,
        "gstRate": 5,
        "placeOfSupply": "Delhi",
    }
    out = _build_b2b([row], "Delhi")
    assert len(out) == 1
    inv = out[0]["inv"][0]
    assert len(inv["itms"]) == 1
    assert inv["itms"][0]["num"] == 1
    assert inv["itms"][0]["itm_det"] == {
        "rt": 5.0,
        "txval": 1000.0,
        "iamt": 0.0,
        "camt": 25.0,
        "samt": 25.0,
        "csamt": 0.0,
    }


# ============================================================================
# REVIEW BLOCKER 2: missing destination-state GSTIN stays EMPTY (and loud)
# ============================================================================


def test_missing_destination_gstin_stays_empty_and_loud(static_rates, monkeypatch):
    """Same PAN, cross-state, but the entity has NO destination-state GSTIN on
    file. The old gstins[0] fallback stamped the SENDER's own GSTIN as the
    recipient (a supply-to-self B2B row the portal rejects) and the validation
    never fired. Now: recipient stays '', the GSTR-1 validation flags it, the
    portal export drops the row, and the ITC falls back to the RECEIVING store
    only."""
    from api.services.gstn_export import to_gstr1_json

    db = _mini_db()
    for e in db["entities"].docs:
        if e["entity_id"] == "ent_send":
            # Only the sender-state GSTIN is registered.
            e["gstins"] = [{"state_code": "20", "gstin": SEND_GSTIN_JH}]
    t = _mixed_transfer(
        from_store="jh_store", to_store="mh_send_branch", tid="trf_gst_3"
    )
    _book(db, t)

    b = db["vendor_bills"].docs[0]
    assert b["recipient_gstin"] == ""  # NOT the sender's own GSTIN
    assert b["recipient_gstin"] != SEND_GSTIN_JH
    assert b["vendor_gstin"] == SEND_GSTIN_JH

    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    rep = reports._compute_gstr1("2026-06", "jh_store")
    # Loud: the CA sees the broken bill in the validation report...
    assert any(
        "recipient GSTIN" in i["issue"] for i in rep["validation"]["issues"]
    )
    # ...and the portal export drops the row instead of misfiling it.
    out = to_gstr1_json(rep, gstin=rep["gstin"], period="2026-06")
    assert out["b2b"] == []

    # ITC fallback: recipient GSTIN empty -> only the RECEIVING store claims.
    recv_3b = reports._compute_gstr3b("2026-06", "mh_send_branch")
    send_3b = reports._compute_gstr3b("2026-06", "jh_store")
    assert recv_3b["itcAvailable"]["integratedTax"] == 460.0
    assert send_3b["itcAvailable"]["integratedTax"] == 0.0


# ============================================================================
# REVIEW BLOCKER 3: short-ship -- received qty is authoritative
# ============================================================================


def test_short_ship_line_not_billed(static_rates):
    """A line received == 0 on a completed transfer must NOT be billed -- the
    old `received or requested` fallback billed the FULL requested qty and
    handed the receiver ITC on goods never received."""
    db = _mini_db()
    t = _mixed_transfer()
    t["items"][1]["quantity_received"] = 0  # sunglass never arrived
    _book(db, t)

    b = db["vendor_bills"].docs[0]
    assert [ln["product_id"] for ln in b["lines"]] == ["prod_frame"]
    assert b["taxable_amount"] == 2000.0
    assert b["igst_total"] == 100.0  # frame only, 5%


def test_partial_receipt_bills_received_qty(static_rates):
    """1 received of 10 requested bills exactly 1."""
    db = _mini_db()
    t = _mixed_transfer()
    t["items"][0].update({"quantity_requested": 10, "quantity_received": 1})
    t["items"][1]["quantity_received"] = 0
    _book(db, t)

    b = db["vendor_bills"].docs[0]
    assert len(b["lines"]) == 1
    assert b["lines"][0]["qty"] == 1.0
    assert b["taxable_amount"] == 1000.0  # 1 x 1000, not 10 x 1000
    assert b["igst_total"] == 50.0


def test_all_lines_short_shipped_zero_bill(static_rates):
    """Every line received 0 -> ZERO bill; the request-time total_value must
    NOT resurrect through the aggregate fallback."""
    db = _mini_db()
    t = _mixed_transfer()
    t["items"][0]["quantity_received"] = 0
    t["items"][1]["quantity_received"] = 0
    _book(db, t)

    b = db["vendor_bills"].docs[0]
    assert b["taxable_amount"] == 0.0
    assert b["tax_amount"] == 0.0
    assert b["lines"] == []


def test_legacy_items_without_received_key_bill_requested(static_rates):
    """Items with NO quantity_received field at all (legacy pre-receive docs)
    still bill the requested qty."""
    db = _mini_db()
    t = _mixed_transfer()
    for it in t["items"]:
        it.pop("quantity_received")
    _book(db, t)

    b = db["vendor_bills"].docs[0]
    assert b["taxable_amount"] == 4000.0
    assert b["igst_total"] == 460.0


# ============================================================================
# REVIEW ITEM 5: zero-value bills are not exported
# ============================================================================


def test_zero_value_bill_emits_no_gstr1_row(static_rates, monkeypatch):
    db = _mini_db()
    t = _mixed_transfer()
    t["items"][0]["quantity_received"] = 0
    t["items"][1]["quantity_received"] = 0
    _book(db, t)
    assert len(db["vendor_bills"].docs) == 1  # bill still booked (audit trail)

    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    rep = reports._compute_gstr1("2026-06", "jh_store")
    assert rep["b2b"] == []  # an all-zero rt=0 invoice is portal noise
    assert rep["totalTax"] == 0.0
    assert rep["hsnSummary"] == []
    sender_3b = reports._compute_gstr3b("2026-06", "jh_store")
    assert sender_3b["outwardTaxableSupplies"]["integratedTax"] == 0.0
    assert sender_3b["outwardTaxableValue"] == 0.0


# ============================================================================
# REVIEW ITEM 7: IST filing month
# ============================================================================


def test_ist_month_boundary_files_bill_in_ist_month(static_rates, monkeypatch):
    """completed_at 30-Jun 20:00 naive-UTC == 1-Jul 01:30 IST: the deemed
    supply must file in JULY alongside the rest of the IST-month return --
    BOTH sides (sender outward AND receiver ITC) move together, so the
    reconciliation invariant survives the boundary."""
    db = _mini_db()
    t = _mixed_transfer(tid="trf_gst_4")
    t["completed_at"] = "2026-06-30T20:00:00"
    _book(db, t)

    b = db["vendor_bills"].docs[0]
    assert b["invoice_date"].startswith("2026-07-01T01:30")

    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    assert reports._compute_gstr1("2026-06", "jh_store")["b2b"] == []
    july = reports._compute_gstr1("2026-07", "jh_store")
    assert len(july["b2b"]) == 1
    assert july["totalTax"] == 460.0

    # Receiver's ITC lands in the SAME (July) period -- invariant intact.
    assert (
        reports._compute_gstr3b("2026-07", "mh_store")["itcAvailable"]["integratedTax"]
        == 460.0
    )
    assert (
        reports._compute_gstr3b("2026-06", "mh_store")["itcAvailable"]["integratedTax"]
        == 0.0
    )
