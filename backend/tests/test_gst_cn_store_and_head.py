"""
GST credit notes: right HEAD, right STORE (GSTIN), right RATE bucket.
=====================================================================
Four confirmed audit findings, one file, each test measured for
discriminating power by reverting its fix (see the docstrings):

1. GSTR-3B's ledger leg hardcoded intra-state for any credit-note ledger row
   without a bool `interstate` stamp -- reversing CGST/SGST on a sale that was
   filed (and stays, in GSTR-1) under IGST, with the max(0,...) clamp
   swallowing the wrong-head over-reversal silently.
2. An in-store credit note was booked under the CASHIER's store rather than
   the ORDER's store, so the store-scoped dedup missed it and ONE refund
   reversed output tax under TWO GSTINs (once against the order's store via
   the returns leg, once against the cashier's via the ledger leg).
3. The in-store return backed GST out of the whole refund at ONE dominant
   rate; the exact per-line engine (gst_breakup_lines) already existed and was
   used only by the online path.
4. A credit note with no usable GST rate was filed at a fabricated 18% and
   subtracted from the 18% HSN bucket, understating declared 18% turnover.

Doubles: strict_fakes.StrictDB for the report computations (its matcher
honours every filter or raises -- a permissive fake is blind to exactly the
store/date scoping these bugs live in); the endpoint tests reuse the
fake-repo harness style of test_returns_gst_refund.py.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.routers.reports as r  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

pytestmark = pytest.mark.asyncio

WHEN = datetime(2026, 6, 15, 6, 0)  # mid-June 2026 in the IST month window


def _db(**seed) -> StrictDB:
    db = StrictDB()
    db.seed("stores", seed.pop("stores", [
        {"store_id": "S1", "gstin": "20AAACR0000A1ZZ", "store_name": "S-one",
         "state": "Jharkhand"},
        {"store_id": "S2", "gstin": "20BBBCR0000B1ZZ", "store_name": "S-two",
         "state": "Jharkhand"},
    ]))
    for name, docs in seed.items():
        db.seed(name, docs)
    return db


def _order(oid="O1", store="S1", tax=1800.0, gross=11800.0, **extra):
    d = {"order_id": oid, "store_id": store, "status": "COMPLETED",
         "created_at": WHEN, "grand_total": gross, "tax_amount": tax,
         "customer_id": "C1", "items": []}
    d.update(extra)
    return d


def _return_doc(rid, store="S1", oid="O1", tax=1800.0, taxable=10000.0):
    return {"return_id": rid, "store_id": store, "order_id": oid,
            "status": "COMPLETED", "created_at": WHEN, "customer_id": "C1",
            "return_type": "RETURN", "refund_method": "CASH",
            "gst_breakup": {"gross": taxable + tax, "taxable": taxable,
                            "tax": tax, "gst_rate": 18.0}}


def _ledger_row(rid, store="S1", tax=1800.0, taxable=10000.0, **extra):
    d = {"store_id": store, "type": "ISSUED", "created_at": WHEN.isoformat(),
         "ref": rid, "reason": f"Credit note for return {rid}",
         "tax": tax, "taxable": taxable, "amount": taxable,
         "gross_refund": taxable + tax, "net_refund": taxable,
         "customer_id": "C1"}
    d.update(extra)
    return d


def _heads(rep):
    return rep["outwardTaxableSupplies"]


# ===========================================================================
# Finding 1 -- the ledger leg must reverse the head the SALE filed under
# ===========================================================================


def test_unstamped_ledger_row_reverses_igst_when_the_sale_was_interstate(
    monkeypatch,
):
    """An IGST sale refunded via a credit note whose ledger row carries NO
    bool `interstate` stamp must still reverse IGST -- resolved from the
    parent order through the return doc, the same one rule the returns leg
    and GSTR-1 use.

    REVERT CHECK: restoring `_split(t, bool(row.get("interstate")))` leaves
    integratedTax at 5000.0 (the CGST/SGST over-reversal is clamped to zero
    and vanishes) -> the 3200.0 assertion fails.
    """
    db = _db(
        orders=[_order(tax=5000.0, gross=32777.78, interstate=True)],
        returns=[_return_doc("RET-260615-HEAD01")],
        credit_note_ledger=[_ledger_row("RET-260615-HEAD01")],
    )
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    t = _heads(rep)
    assert t["integratedTax"] == 3200.0, (
        f"IGST sale of 5000 minus an 1800 credit note must leave 3200, got "
        f"{t['integratedTax']} - the reversal went to the wrong head"
    )
    assert t["centralTax"] == 0.0 and t["stateTax"] == 0.0, t


# ===========================================================================
# Finding 2 (report side) -- a legacy wrong-store row must not double-file
# ===========================================================================


def test_legacy_cashier_store_row_reverses_once_across_the_group(monkeypatch):
    """Order + return at S1, credit-note ledger row booked (old behaviour)
    under cashier store S2. S1's report reverses the tax ONCE via its returns
    leg; S2's report must NOT also reverse it via its ledger leg.

    REVERT CHECK: removing the `_cn_foreign_store` skip makes S2's outward
    3200.0 (5000 - a reversal that belongs to S1's GSTIN) -> fails.
    """
    fixtures = dict(
        orders=[_order(oid="O1", store="S1", tax=1800.0),
                _order(oid="O2", store="S2", tax=5000.0, gross=32777.78)],
        returns=[_return_doc("RET-260615-XSTORE1", store="S1", oid="O1")],
        credit_note_ledger=[_ledger_row("RET-260615-XSTORE1", store="S2")],
    )

    monkeypatch.setattr(r, "_get_raw_db", lambda: _db(**fixtures))
    s1 = _heads(r._compute_gstr3b("2026-06", "S1"))
    assert round(s1["centralTax"] + s1["stateTax"] + s1["integratedTax"], 2) == 0.0

    monkeypatch.setattr(r, "_get_raw_db", lambda: _db(**fixtures))
    s2 = _heads(r._compute_gstr3b("2026-06", "S2"))
    assert round(s2["centralTax"] + s2["stateTax"] + s2["integratedTax"], 2) == 5000.0, (
        f"S2 declared {s2} - the S1 refund was reversed a second time under "
        "S2's GSTIN (group under-declares)"
    )


def test_legacy_cashier_store_row_is_not_a_second_gstr1_credit_note(monkeypatch):
    """Same fixture, GSTR-1: the credit note must appear in ONE store's CDNR,
    not both. REVERT CHECK: without the foreign-store skip S2's CDNR carries
    the S1 refund as a second filed note."""
    fixtures = dict(
        orders=[_order(oid="O1", store="S1", tax=1800.0)],
        returns=[_return_doc("RET-260615-XSTORE1", store="S1", oid="O1")],
        credit_note_ledger=[_ledger_row("RET-260615-XSTORE1", store="S2")],
    )
    monkeypatch.setattr(r, "_get_raw_db", lambda: _db(**fixtures))
    s1_cdnr = r._compute_gstr1("2026-06", "S1")["cdnr"]
    monkeypatch.setattr(r, "_get_raw_db", lambda: _db(**fixtures))
    s2_cdnr = r._compute_gstr1("2026-06", "S2")["cdnr"]
    assert len(s1_cdnr) == 1, s1_cdnr
    assert s2_cdnr == [], (
        f"S2 filed a CDNR row for S1's refund: {s2_cdnr}"
    )


# ===========================================================================
# Finding 1 (clamp) -- the zero-clamp must be LOUD, never silent
# ===========================================================================


def test_credit_note_excess_is_surfaced_as_carry_forward(monkeypatch):
    """A refund-heavy month keeps the non-negative screen figure (deliberate,
    see test_refunds_never_produce_a_negative_liability) but the swallowed
    excess must be REPORTED, not discarded.

    REVERT CHECK: removing the creditNotes / creditNoteCarryForward payload
    fields raises KeyError here."""
    db = _db(
        orders=[_order(tax=100.0, gross=700.0)],
        returns=[_return_doc("RET-260615-CARRY1")],
    )
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    t = _heads(rep)
    assert round(t["centralTax"] + t["stateTax"] + t["integratedTax"], 2) == 0.0
    assert rep["creditNotes"]["centralTax"] == 900.0
    assert rep["creditNotes"]["stateTax"] == 900.0
    # 900 per head reversed against 50 per head of sales -> 850 carried.
    assert rep["creditNoteCarryForward"]["centralTax"] == 850.0
    assert rep["creditNoteCarryForward"]["stateTax"] == 850.0
    assert rep["creditNoteCarryForward"]["integratedTax"] == 0.0


# ===========================================================================
# Finding 4 -- no fabricated 18%: derive the rate from the note itself
# ===========================================================================


def test_rateless_credit_note_derives_its_rate_and_bucket(monkeypatch):
    """A legacy ledger row with tax 25 on taxable 500 is a 5% note. It must
    file at 5% and subtract from the 5% HSN bucket -- never default to 18%
    and raid the 18% bucket.

    REVERT CHECK: restoring the `else 18` default files the note at 18% and
    leaves the 18% bucket at 500 (understated) and the 5% bucket at 1000.
    """
    row = _ledger_row("CN-MANUAL-1", tax=25.0, taxable=500.0)
    row.pop("gross_refund"); row.pop("net_refund")
    assert "gst_rate" not in row
    db = _db(
        orders=[
            _order(oid="O18", tax=180.0, gross=1180.0,
                   items=[{"item_id": "a", "item_total": 1180.0,
                           "gst_rate": 18.0}]),
            _order(oid="O05", tax=50.0, gross=1050.0,
                   items=[{"item_id": "b", "item_total": 1050.0,
                           "gst_rate": 5.0}]),
        ],
        credit_note_ledger=[row],
    )
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr1("2026-06", "S1")
    assert len(rep["cdnr"]) == 1
    assert rep["cdnr"][0]["gstRate"] == 5, rep["cdnr"][0]
    by_rate = {h["gstRate"]: h for h in rep["hsnSummary"]}
    assert by_rate[18]["taxableValue"] == 1000.0, (
        f"the 18% bucket was raided by a rateless credit note: {by_rate[18]}"
    )
    assert by_rate[5]["taxableValue"] == 500.0, by_rate.get(5)


# ===========================================================================
# Endpoint tests -- findings 2 (booking store) and 3 (per-line back-out)
# ===========================================================================


class _FakeOrderRepo:
    def __init__(self, order):
        order = dict(order)
        order.setdefault("amount_paid", 1_000_000_000.0)
        self._order = order

    def find_by_id(self, oid):
        return self._order if self._order.get("order_id") == oid else None

    def find_by_order_number(self, num):
        return self._order if self._order.get("order_number") == num else None


class _FakeCustomerRepo:
    def __init__(self):
        self.customers = {
            "CUST-1": {"customer_id": "CUST-1", "name": "Asha",
                       "store_credit": 0.0}
        }

    def find_by_id(self, cid):
        return self.customers.get(cid)

    def update(self, cid, data):
        if cid in self.customers:
            self.customers[cid].update(data)
        return True


class _FakeColl:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"acknowledged": True})()

    def find(self, query=None, projection=None):
        q = query or {}
        return iter(
            [d for d in self.docs if all(d.get(k) == v for k, v in q.items())]
        )

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None

    def count_documents(self, query=None):
        q = query or {}
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in q.items()))


def _token(roles, store_id):
    return jwt.encode(
        {"sub": "u1", "user_id": "u1", "username": "tester", "roles": roles,
         "active_store_id": store_id,
         "exp": datetime.utcnow() + timedelta(hours=1)},
        auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM,
    )


def _mixed_rate_order():
    """Two lines billed at DIFFERENT rates: net 1000 @18% (gross 1180) + net
    1000 @5% (gross 1050). Full-return gross 2230; exact reversal 180+50=230."""
    return {
        "order_id": "ORD-MIX", "order_number": "ORD-MIX-2026-000001",
        "customer_id": "CUST-1", "customer_name": "Asha",
        "payment_method": "UPI", "store_id": "BV-PUN-01",
        "items": [
            {"item_id": "li1", "product_id": "P18", "product_name": "Sunglass",
             "sku": "S18", "quantity": 1, "unit_price": 1000,
             "gst_rate": 18.0, "item_total": 1000},
            {"item_id": "li2", "product_id": "P05", "product_name": "CR Lens",
             "sku": "L05", "quantity": 1, "unit_price": 1000,
             "gst_rate": 5.0, "item_total": 1000},
        ],
    }


@pytest.fixture
def ctx(monkeypatch):
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")
    order_repo = _FakeOrderRepo(_mixed_rate_order())
    customer_repo = _FakeCustomerRepo()
    returns_coll, ledger_coll = _FakeColl(), _FakeColl()

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            return {"returns": returns_coll,
                    "credit_note_ledger": ledger_coll}.get(name, _FakeColl())

    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        returns_router, "get_customer_repository", lambda: customer_repo
    )
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_db", lambda: _FakeDB(), raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )
    return {"client": TestClient(app), "returns_coll": returns_coll,
            "ledger_coll": ledger_coll}


def _cn_payload():
    return {
        "order_id": "ORD-MIX", "return_type": "CREDIT_NOTE",
        "customer_id": "CUST-1",
        "items": [
            {"order_item_id": "li1", "product_id": "P18",
             "product_name": "Sunglass", "sku": "S18", "return_qty": 1,
             "unit_price": 1000, "reason": "DEFECTIVE", "condition": "GOOD"},
            {"order_item_id": "li2", "product_id": "P05",
             "product_name": "CR Lens", "sku": "L05", "return_qty": 1,
             "unit_price": 1000, "reason": "DEFECTIVE", "condition": "GOOD"},
        ],
    }


async def test_mixed_rate_return_reverses_exact_per_line_tax(ctx):
    """Finding 3: the credit note reverses the SUM of the original line taxes
    (1180@18 -> 180, 1050@5 -> 50), via the same gst_breakup_lines engine the
    online path uses.

    REVERT CHECK: restoring the dominant-rate back-out stamps
    tax = 2230 - 2230/1.18 = 340.17 on both the return doc and the ledger row
    -> both 230.0 assertions fail.
    """
    tok = _token(["STORE_MANAGER"], "BV-PUN-01")
    resp = ctx["client"].post(
        "/api/v1/returns", json=_cn_payload(),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 201, resp.text
    doc = ctx["returns_coll"].docs[0]
    gb = doc["gst_breakup"]
    assert gb["gross"] == 2230.0
    assert gb["tax"] == 230.0, (
        f"per-line reversal is 180+50=230, got {gb['tax']} "
        "(340.17 = the dominant-rate approximation)"
    )
    assert gb["taxable"] == 2000.0
    # by_rate carries the exact split the accountant files from.
    assert gb["by_rate"]["18.0"]["tax"] == 180.0
    assert gb["by_rate"]["5.0"]["tax"] == 50.0
    # The ledger stamp (fee 0 -> the exact view figures, scaled by 1).
    entry = ctx["ledger_coll"].docs[0]
    assert entry["tax"] == 230.0, entry
    assert entry["taxable"] == 2000.0, entry


async def test_credit_note_is_booked_under_the_orders_store(ctx):
    """Finding 2: an ADMIN working with active store BV-BOK-01 processes a
    return for an order billed at BV-PUN-01. The credit-note ledger row must
    carry the ORDER's store (whose GSTIN filed the sale).

    REVERT CHECK: restoring `store_id=current_user.get("active_store_id")`
    books the row under BV-BOK-01 -> fails.
    """
    tok = _token(["ADMIN"], "BV-BOK-01")
    resp = ctx["client"].post(
        "/api/v1/returns", json=_cn_payload(),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 201, resp.text
    entry = ctx["ledger_coll"].docs[0]
    assert entry["store_id"] == "BV-PUN-01", (
        f"credit note booked under {entry['store_id']!r} - the cashier's "
        "store, not the order's: one refund now reverses tax under two GSTINs"
    )
    # The return doc and the ledger row agree on the store, so the GST
    # reports' store-scoped dedup can pair them.
    assert ctx["returns_coll"].docs[0]["store_id"] == "BV-PUN-01"
