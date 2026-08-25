"""
IMS 2.0 -- A COUNT SHEET NEVER BLOCKS THE MONEY
===============================================
The count block rides ALONGSIDE money; it is never a gate on it. Typing the
wire model strictly put the rejection BEFORE the module's own coercion, so
Pydantic 422'd the whole request over a bad piece count and the coercion
(``_coerce_pieces`` "junk or negative -> 0", ``_coerce_face`` "junk -> None")
was dead code on every HTTP path.

That matters because ``POSLayout.tsx`` records the payment inside a bare
``catch {}`` ("Don't block order -- payment can be recorded later"): a 4xx
there saves the ORDER WITH NO PAYMENT ROW -- wrong payment status, wrong
drawer, wrong receivables -- and an absurd piece count (10**15) overflows the
8-byte BSON int on the write, which is a 500 into the same swallowing catch.

What is proved here, against the REAL request models and the REAL payment
endpoint over the REAL OrderRepository (nothing under test is stubbed; only
the collection is in-memory):

  * every garbage shape VALIDATES -- the request model is where the 422 was
    raised, so a shape that constructs is a shape that no longer 4xx's
  * the payment still PERSISTS, and the persisted money (amount, amount_paid,
    balance_due, payment_status and the whole payment row outside the new
    keys) is IDENTICAL to the same sale sent with no breakdown at all
  * the sheet itself is recorded as what it is: coerced rows, and either
    NOT_CAPTURED or FLAGGED (``matches_amount: False``) -- never a silent
    "counted and correct"
  * the persisted row is BSON-ENCODABLE at 10**15 / 10**18 / 10**30 pieces
    (mongomock stores unbounded Python ints; real Mongo raises OverflowError,
    so this is asserted through real ``bson``)
  * POSITIVE CONTROL: a valid breakdown still stores cleanly and still tallies

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_cash_count_never_blocks_the_money.py -q

No emoji (Windows cp1252).
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import bson
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

# REUSE, do not fork: the in-memory orders collection, the Rs 1,600 order and
# the "money keys minus the new ones" comparison already written for this
# feature's first suite.
from test_cash_denominations_capture import (  # noqa: E402
    _OrdersColl,
    _cashier,
    _order_doc,
    _strip_new,
)

from api.routers import expenses as expenses_module  # noqa: E402
from api.routers import orders as orders_module  # noqa: E402
from api.routers.expenses import ExpenseCreate, create_expense  # noqa: E402
from api.routers.orders import PaymentCreate, add_payment  # noqa: E402
from api.routers.returns import (  # noqa: E402
    RefundTenderLine,
    _normalize_refund_tenders,
)
from api.routers.finance import CashRegisterClose  # noqa: E402
from api.routers.till import OpenSession  # noqa: E402
from api.services import cash_denominations as cd  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402


# The known-good body a POS cash strip will send: Rs 2,000 handed over for a
# Rs 1,600 bill, Rs 400 back. Every garbage case below is this body with ONE
# field replaced, so the only variable is the thing being tested.
GOOD_TENDERED = {"rows": [{"face": 500, "kind": "note", "pieces": 4}], "state": "COUNTED"}
GOOD_CHANGE = {"rows": [{"face": 200, "kind": "note", "pieces": 2}], "state": "COUNTED"}


def _body(**overrides) -> Dict[str, Any]:
    body = {
        "method": "CASH",
        "amount": 1600.0,
        "cash_tendered": dict(GOOD_TENDERED),
        "cash_change": dict(GOOD_CHANGE),
        "tendered_amount": 2000.0,
        "change_amount": 400.0,
    }
    body.update(overrides)
    return body


def _t_rows(*pieces_per_face) -> List[Dict[str, Any]]:
    return [{"face": f, "kind": "note", "pieces": p} for f, p in pieces_per_face]


# name -> (body override, expected TENDERED (state, rows as (kind, face, pieces),
#          matches_amount))
#
# The 12 shapes the verifier reproduced as 422s at /orders/{id}/payments, plus
# the absurd counts that overflow an 8-byte BSON int.
GARBAGE: List[Tuple[str, Dict[str, Any], str, List[Tuple[str, int, int]], Any]] = [
    (
        "negative_pieces",
        {"cash_tendered": {"rows": _t_rows((500, -3)), "state": "COUNTED"}},
        "COUNTED",
        [("note", 500, 0)],
        False,
    ),
    (
        "string_pieces",
        {"cash_tendered": {"rows": [{"face": 500, "pieces": "two"}], "state": "COUNTED"}},
        "COUNTED",
        [("note", 500, 0)],
        False,
    ),
    (
        "float_pieces",
        {"cash_tendered": {"rows": [{"face": 500, "pieces": 2.5}], "state": "COUNTED"}},
        "COUNTED",
        [("note", 500, 2)],
        False,
    ),
    (
        "string_face",
        {
            "cash_tendered": {
                "rows": [{"face": "five hundred", "pieces": 4}],
                "state": "COUNTED",
            }
        },
        "COUNTED",
        [],
        False,
    ),
    (
        "null_face",
        {"cash_tendered": {"rows": [{"face": None, "pieces": 4}], "state": "COUNTED"}},
        "COUNTED",
        [],
        False,
    ),
    (
        "row_is_a_string",
        {"cash_tendered": {"rows": ["four 500s"], "state": "COUNTED"}},
        "COUNTED",
        [],
        False,
    ),
    (
        "rows_not_a_list",
        {"cash_tendered": {"rows": "500x4", "state": "COUNTED"}},
        "COUNTED",
        [],
        False,
    ),
    (
        "block_is_string",
        {"cash_tendered": "four five hundreds"},
        # Nothing usable at all -> absence, not a fabricated zero count.
        "NOT_CAPTURED",
        [],
        None,
    ),
    (
        "kind_is_a_number",
        {
            "cash_tendered": {
                "rows": [{"face": 500, "kind": 7, "pieces": 4}],
                "state": "COUNTED",
            }
        },
        # A junk kind falls back to 'note' -- the count itself is real and tallies.
        "COUNTED",
        [("note", 500, 4)],
        True,
    ),
    (
        "state_is_a_number",
        {"cash_tendered": {"rows": _t_rows((500, 4)), "state": 7}},
        "COUNTED",
        [("note", 500, 4)],
        True,
    ),
    (
        "absurd_pieces_10p15",
        {"cash_tendered": {"rows": _t_rows((500, 10**15)), "state": "COUNTED"}},
        "COUNTED",
        [("note", 500, cd.MAX_PIECES)],
        False,
    ),
    (
        "absurd_pieces_10p30",
        {"cash_tendered": {"rows": _t_rows((500, 10**30)), "state": "COUNTED"}},
        "COUNTED",
        [("note", 500, cd.MAX_PIECES)],
        False,
    ),
    (
        "absurd_face",
        {"cash_tendered": {"rows": _t_rows((10**30, 2)), "state": "COUNTED"}},
        "COUNTED",
        [("note", cd.MAX_FACE, 2)],
        False,
    ),
]

# Garbage in the CHANGE sheet: same money assertions, and the change block
# carries the flag instead of the tendered one.
GARBAGE_CHANGE: List[Tuple[str, Dict[str, Any], str, List[Tuple[str, int, int]], Any]] = [
    (
        "neg_change_pcs",
        {"cash_change": {"rows": _t_rows((200, -2)), "state": "COUNTED"}},
        "COUNTED",
        [("note", 200, 0)],
        False,
    ),
    ("change_block_is_string", {"cash_change": 400}, "NOT_CAPTURED", [], None),
]

# Garbage in the two SCALARS that ride with the sheets. These are records, not
# money: they are coerced and capped, and the per-leg identity
# (tendered - change == the leg amount) reports that the leg no longer adds up.
GARBAGE_SCALARS: List[Tuple[str, Dict[str, Any]]] = [
    ("neg_tendered_amt", {"tendered_amount": -2000.0}),
    ("huge_tendered_amt", {"tendered_amount": 10**15}),
    ("string_tendered_amt", {"tendered_amount": "two thousand"}),
    ("huge_change_amt", {"change_amount": 10**30}),
]


@pytest.fixture()
def sale(monkeypatch):
    """A live Rs 1,600 order + the REAL repository, and a runner that posts a
    payment through the REAL endpoint."""

    def _build(order_id="ORD-G1"):
        coll = _OrdersColl([_order_doc(order_id, 1600.0)])
        repo = OrderRepository(coll)
        monkeypatch.setattr(orders_module, "get_order_repository", lambda: repo)
        return {"coll": coll, "order_id": order_id}

    return _build


async def _pay(order_id: str, body: Dict[str, Any]):
    """Validate the body the way FastAPI does (this IS the 422 site), then run
    the real handler."""
    return await add_payment(
        order_id, PaymentCreate(**body), current_user=_cashier()
    )


def _paid_row(ctx) -> Dict[str, Any]:
    stored = ctx["coll"].find_one({"order_id": ctx["order_id"]})
    assert stored["payment_status"] == "PAID"
    assert stored["amount_paid"] == 1600.0
    assert stored["balance_due"] == 0.0
    assert len(stored["payments"]) == 1
    return stored["payments"][0]


def _shape(block: Dict[str, Any]):
    return (
        block["state"],
        [(r["kind"], r["face"], r["pieces"]) for r in block["rows"]],
        block["matches_amount"],
    )


# ===========================================================================
# THE MONEY GOES THROUGH -- every shape, at the sale door
# ===========================================================================


@pytest.mark.asyncio
class TestGarbageInTheCountSheetNeverTouchesTheSale:
    @pytest.fixture()
    def control(self, sale):
        """The same Rs 1,600 cash sale with NO breakdown at all. Every garbage
        case is diffed against this row."""

        async def _run():
            ctx = sale(order_id="ORD-CONTROL")
            await _pay(ctx["order_id"], {"method": "CASH", "amount": 1600.0})
            return _paid_row(ctx)

        return _run

    @pytest.mark.parametrize(
        "name,override,state,rows,matches",
        GARBAGE,
        ids=[g[0] for g in GARBAGE],
    )
    async def test_a_malformed_sheet_is_recorded_not_refused(
        self, sale, control, name, override, state, rows, matches
    ):
        bare = await control()

        ctx = sale(order_id=f"ORD-{name}")
        # 1. It VALIDATES. This construction is where the 422 came from.
        await _pay(ctx["order_id"], _body(**override))
        pay = _paid_row(ctx)

        # 2. THE MONEY IS EXACTLY THE NO-BREAKDOWN SALE.
        assert pay["amount"] == 1600.0
        assert _strip_new(pay) == _strip_new(bare)

        # 3. The sheet is recorded as what it is -- coerced, and either absent
        #    or flagged. Never a silent "counted and correct".
        assert _shape(pay["cash_tendered_count"]) == (state, rows, matches)

        # 4. It survives the real Mongo encoder (8-byte ints).
        assert bson.encode(pay)

    @pytest.mark.parametrize(
        "name,override,state,rows,matches",
        GARBAGE_CHANGE,
        ids=[g[0] for g in GARBAGE_CHANGE],
    )
    async def test_a_malformed_change_sheet_is_the_same_story(
        self, sale, control, name, override, state, rows, matches
    ):
        bare = await control()

        ctx = sale(order_id=f"ORD-{name}")
        await _pay(ctx["order_id"], _body(**override))
        pay = _paid_row(ctx)

        assert pay["amount"] == 1600.0
        assert _strip_new(pay) == _strip_new(bare)
        assert _shape(pay["cash_change_count"]) == (state, rows, matches)
        # The Rs 400 that physically went back is the SCALAR, and it is
        # untouched by the state of the sheet.
        assert pay["change_amount_paisa"] == 40000
        assert pay["cash_leg_balanced"] is True
        assert bson.encode(pay)

    @pytest.mark.parametrize(
        "name,override", GARBAGE_SCALARS, ids=[g[0] for g in GARBAGE_SCALARS]
    )
    async def test_an_absurd_scalar_is_capped_and_flagged_not_refused(
        self, sale, control, name, override
    ):
        bare = await control()

        ctx = sale(order_id=f"ORD-{name}")
        await _pay(ctx["order_id"], _body(**override))
        pay = _paid_row(ctx)

        assert pay["amount"] == 1600.0
        assert _strip_new(pay) == _strip_new(bare)
        for key in ("tendered_amount_paisa", "change_amount_paisa"):
            value = pay[key]
            if value is not None:
                assert abs(value) <= cd.MAX_PAISA
        # tendered - change no longer equals the Rs 1,600 leg. A FLAG, and the
        # leg amount above is untouched by it.
        assert pay["cash_leg_balanced"] is False
        assert bson.encode(pay)

    async def test_a_good_breakdown_still_stores_cleanly_and_still_tallies(
        self, sale
    ):
        """POSITIVE CONTROL. If the permissive wire model had swallowed real
        counts too, this is what would die."""
        ctx = sale(order_id="ORD-GOOD")
        await _pay(ctx["order_id"], _body())
        pay = _paid_row(ctx)

        assert pay["amount"] == 1600.0
        assert _shape(pay["cash_tendered_count"]) == (
            "COUNTED",
            [("note", 500, 4)],
            True,
        )
        assert _shape(pay["cash_change_count"]) == ("COUNTED", [("note", 200, 2)], True)
        assert pay["tendered_amount_paisa"] == 200000
        assert pay["change_amount_paisa"] == 40000
        # Rs 2,000 in minus Rs 400 out IS the Rs 1,600 leg.
        assert pay["cash_leg_balanced"] is True
        assert bson.encode(pay)

    async def test_an_absurd_count_is_written_not_a_500(self, sale):
        """MUST-FIX 2. ``face * 100 * pieces`` goes into a Mongo document and
        BSON integers are 8 bytes: 10**15 pieces raised OverflowError on the
        write -- a 500 into the same swallowing catch as the 422. Clamped, not
        bounded: a bound would be a rejection."""
        for pieces in (10**15, 10**18, 10**30):
            ctx = sale(order_id=f"ORD-BIG-{pieces}")
            await _pay(
                ctx["order_id"],
                _body(
                    cash_tendered={
                        "rows": _t_rows((500, pieces)),
                        "state": "COUNTED",
                    }
                ),
            )
            pay = _paid_row(ctx)
            # THE REAL ENCODER FIRST -- this is the production failure: an
            # OverflowError on the write, i.e. a 500 into the swallowing catch.
            # mongomock stores unbounded Python ints and never sees it.
            assert bson.encode(pay)
            block = pay["cash_tendered_count"]
            assert block["rows"][0]["pieces"] == cd.MAX_PIECES
            assert block["total_paisa"] < 2**63

    async def test_an_absurd_SHEET_is_written_not_a_500(self, sale):
        """THE CLAMP CAPPED A ROW, NEVER THE SHEET. A row maxes out at
        MAX_FACE * 100 * MAX_PIECES = 1e15, so ~9,224 of them already exceed
        the 8-byte BSON int -- and the sheet total is what goes into the
        document. 9,300 rows returned HTTP 500 with ZERO payments persisted
        and payment_status UNPAID: the same swallowing catch, one order of
        magnitude up. Capped and FLAGGED, never refused."""
        rows = _t_rows(*[(cd.MAX_FACE, cd.MAX_PIECES)] * 10_000)

        ctx = sale(order_id="ORD-BIG-SHEET")
        await _pay(
            ctx["order_id"],
            _body(cash_tendered={"rows": rows, "state": "COUNTED"}),
        )
        pay = _paid_row(ctx)

        # THE REAL ENCODER: this is the production failure (OverflowError on
        # the write). mongomock stores unbounded Python ints and never sees it.
        assert bson.encode(pay)
        assert pay["amount"] == 1600.0
        block = pay["cash_tendered_count"]
        # Every row is still RECORDED -- nothing was dropped to make it fit.
        assert len(block["rows"]) == 10_000
        assert block["total_paisa"] == cd.MAX_PAISA
        assert block["total_paisa"] < 2**63
        assert block["matches_amount"] is False  # flagged, not silently "right"

    def test_folding_absurd_rows_onto_one_face_is_capped_too(self):
        """The other place a sheet is added up: ``merge_rows`` folds several
        legs onto one row per (kind, face), and 10,000 capped rows on the SAME
        face make a piece count no single row could hold -- face * 100 * that
        is the same overflow on the returns door, which folds two CASH legs."""
        merged = cd.merge_rows(*[[{"face": cd.MAX_FACE, "pieces": cd.MAX_PIECES}]] * 10_000)

        assert len(merged) == 1
        assert merged[0]["pieces"] == cd.MAX_PIECES
        assert merged[0]["line_total_paisa"] == cd.MAX_PAISA
        assert bson.encode({"rows": merged})


# ===========================================================================
# THE OTHER TWO DOORS -- the same shared wire model
# ===========================================================================


class TestTheRefundDoor:
    @pytest.mark.parametrize(
        "junk",
        [
            "four five hundreds",
            {"rows": "500x1", "state": "COUNTED"},
            {"rows": [{"face": 500, "pieces": -1}], "state": "COUNTED"},
            {"rows": [{"face": "junk", "pieces": 1}], "state": 7},
            {"rows": [{"face": 500, "pieces": 10**18}], "state": "COUNTED"},
        ],
    )
    def test_a_malformed_sheet_never_refuses_the_refund(self, junk):
        """The refund leg validates and normalises; the REFUNDED AMOUNT is the
        one the customer is owed, untouched by anything on the sheet."""
        leg = RefundTenderLine(method="CASH", amount=500.0, cash_count=junk)
        rows, netted = _normalize_refund_tenders([leg], 500.0)

        assert netted is True
        assert len(rows) == 1
        assert rows[0]["method"] == "CASH"
        assert rows[0]["amount"] == 500.0  # the money, untouched
        block = rows[0]["cash_count"]
        assert block["amount_paisa"] == 50000
        assert block["state"] in ("COUNTED", "NOT_CAPTURED")
        for row in block["rows"]:
            assert 0 <= row["pieces"] <= cd.MAX_PIECES
            assert 0 < row["face"] <= cd.MAX_FACE
        assert bson.encode({"refund_tenders": rows})

    def test_a_real_refund_sheet_still_records_the_notes(self):
        """POSITIVE CONTROL at this door."""
        leg = RefundTenderLine(
            method="CASH",
            amount=500.0,
            cash_count={"rows": _t_rows((500, 1)), "state": "COUNTED"},
        )
        rows, _ = _normalize_refund_tenders([leg], 500.0)
        block = rows[0]["cash_count"]
        assert _shape(block) == ("COUNTED", [("note", 500, 1)], True)


class _ExpenseRepo:
    """Just enough repository for the payout door: no idempotent replay, and a
    create that keeps the document so it can be asserted on."""

    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find_one(self, _query):
        return None

    def create(self, doc):
        self.docs.append(doc)
        return doc["expense_id"]


class TestThePayoutDoor:
    @pytest.fixture()
    def payout(self, monkeypatch):
        repo = _ExpenseRepo()
        monkeypatch.setattr(expenses_module, "get_expense_repository", lambda: repo)
        monkeypatch.setattr(expenses_module, "get_advance_repository", lambda: None)
        monkeypatch.setattr(expenses_module, "_period_locked", lambda *a, **k: False)
        return repo

    @staticmethod
    def _run(cash_count):
        import asyncio

        body = ExpenseCreate(
            category="supplies",
            amount=300.0,
            description="tea and biscuits",
            expense_date=_dt.date(2026, 8, 24),
            payment_mode="CASH",
            store_id="BV-PUN-01",
            cash_count=cash_count,
        )
        user = {
            "user_id": "U-mgr",
            "full_name": "Priya",
            "roles": ["ADMIN"],
            "active_store_id": "BV-PUN-01",
        }
        return asyncio.run(create_expense(body, current_user=user))

    @pytest.mark.parametrize(
        "junk",
        [
            "three hundreds",
            {"rows": "100x3", "state": "COUNTED"},
            {"rows": [{"face": 100, "pieces": -3}], "state": "COUNTED"},
            {"rows": [{"face": 100, "pieces": 10**18}], "state": "COUNTED"},
        ],
    )
    def test_a_malformed_sheet_never_refuses_the_payout(self, payout, junk):
        out = self._run(junk)
        assert out["expense_id"]
        doc = payout.docs[0]
        assert doc["amount"] == 300.0  # the money, untouched
        block = doc["cash_count"]
        assert block["amount_paisa"] == 30000
        for row in block["rows"]:
            assert 0 <= row["pieces"] <= cd.MAX_PIECES
        assert bson.encode(doc)

    def test_a_real_payout_sheet_still_records_the_notes(self, payout):
        """POSITIVE CONTROL at this door."""
        self._run({"rows": _t_rows((100, 3)), "state": "COUNTED"})
        assert _shape(payout.docs[0]["cash_count"]) == (
            "COUNTED",
            [("note", 100, 3)],
            True,
        )


class TestTheDrawerDoors:
    def test_the_till_open_grid_is_coerced_not_refused(self):
        """The same shared row model backs the till / cash-register grids, so
        the guarantee reaches them too."""
        body = OpenSession(
            store_id="BV-PUN-01",
            opening_denominations=[
                "a bundle of 500s",
                {"face": 500, "pieces": -2},
                {"face": "junk", "pieces": 4},
                {"face": 100, "kind": 9, "pieces": 10**20},
            ],
        )
        rows = cd.normalize_rows([d.model_dump() for d in body.opening_denominations])
        assert [(r["kind"], r["face"], r["pieces"]) for r in rows] == [
            ("note", 500, 0),
            ("note", 100, cd.MAX_PIECES),
        ]
        assert bson.encode({"opening_denominations": rows})

    @pytest.mark.parametrize("junk", ["ninety five hundred", 500, {"face": 500}])
    def test_a_whole_sheet_sent_as_junk_is_an_empty_sheet_not_a_422(self, junk):
        """The ROW model was lenient while the LIST around it was not, so a
        sheet sent as a bare string still 422'd -- a refusal one level up, at
        the same doors whose rule is that a count sheet never blocks the
        money."""
        assert OpenSession(opening_denominations=junk).opening_denominations == []
        assert (
            CashRegisterClose(session_id="CR-1", denominations=junk).denominations
            == []
        )
