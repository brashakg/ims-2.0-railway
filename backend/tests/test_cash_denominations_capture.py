"""
IMS 2.0 - Denominated cash capture: the deciding tests
======================================================
These drive the REAL doors (the orders payment endpoint over a REAL
OrderRepository, the REAL returns tender normaliser, the REAL till session
service) and assert on the PERSISTED DOCUMENT, never on a log line.

What each block proves:

  SAFETY RULE C -- existing money behaviour is unchanged. The same cash sale is
    run twice, once bare and once with a full notes-and-coins breakdown, and
    the two persisted orders are compared FIELD BY FIELD. Everything except the
    new keys must be byte-identical: grand_total, amount_paid, balance_due,
    payment_status, credit_sale, and every GST figure on every line.

  SAFETY RULE A -- the breakdown never becomes the money. A breakdown that does
    not add up is FLAGGED and the stored amount is asserted explicitly.

  SAFETY RULE B -- no cash entry may block a sale. A cashier who sends nothing
    completes the bill and the record says NOT CAPTURED, not zero.

  THE DRAWER -- the per-face tally across open -> sale -> change -> refund ->
    payout -> close, and a planted missing Rs 500 note surfacing as a Rs 500
    discrepancy AT THE Rs 500 FACE.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import copy
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import orders as orders_module  # noqa: E402
from api.routers.orders import PaymentCreate, add_payment  # noqa: E402
from api.services import cash_denominations as cd  # noqa: E402
from api.services import eod_tally as till  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402


# ===========================================================================
# A fake orders collection with the operators the real repository uses
# ===========================================================================


class _OrdersColl:
    """Just enough Mongo for OrderRepository: find_one, update_one with
    $push/$set, and find() for the drawer reader. Deliberately NOT a mock of
    anything under test -- the repository's real arithmetic runs on top."""

    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None):
        self.docs: List[Dict[str, Any]] = [copy.deepcopy(d) for d in (docs or [])]

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                return copy.deepcopy(d)
        return None

    @staticmethod
    def _match(doc, query):
        """The operators the real readers send: a dual-typed $or window, plus
        the $ne the returns reader uses to exclude historical imports."""
        for key, cond in (query or {}).items():
            if key == "$or":
                if not any(_OrdersColl._match(doc, sub) for sub in cond):
                    return False
                continue
            actual = doc.get(key)
            if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
                for op, exp in cond.items():
                    try:
                        if op == "$ne" and actual == exp:
                            return False
                        if op == "$gte" and not (actual is not None and actual >= exp):
                            return False
                        if op == "$lte" and not (actual is not None and actual <= exp):
                            return False
                    except TypeError:
                        return False  # Mongo type-bracketing
                continue
            if actual != cond:
                return False
        return True

    def find(self, query=None, projection=None):
        return iter(
            [copy.deepcopy(d) for d in self.docs if self._match(d, query or {})]
        )

    def update_one(self, query, update, **_kw):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                for key, val in (update.get("$push") or {}).items():
                    d.setdefault(key, []).append(copy.deepcopy(val))
                for key, val in (update.get("$set") or {}).items():
                    d[key] = copy.deepcopy(val)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


def _order_doc(order_id="ORD-D1", grand_total=1600.0) -> Dict[str, Any]:
    """A Rs 1,600 inclusive-priced counter sale at 18% GST."""
    return {
        "order_id": order_id,
        "order_number": order_id,
        "store_id": "BV-PUN-01",
        "status": "CONFIRMED",
        "grand_total": grand_total,
        "amount_paid": 0.0,
        "balance_due": grand_total,
        "payment_status": "UNPAID",
        "payments": [],
        "created_at": "2026-08-24T10:00:00",
        "items": [
            {
                "item_id": "li1",
                "product_id": "PRD-1",
                "product_name": "Ray-Ban Frame",
                "quantity": 1,
                "unit_price": 1355.93,
                "gst_rate": 18.0,
                "taxable_value": 1355.93,
                "tax_amount": 244.07,
                "cgst_amount": 122.035,
                "sgst_amount": 122.035,
                "item_total": 1600.0,
            }
        ],
    }


def _cashier() -> Dict[str, Any]:
    return {
        "user_id": "U-cash",
        "username": "asha",
        "roles": ["SALES_CASHIER"],
        "active_store_id": "BV-PUN-01",
    }


@pytest.fixture()
def sale(monkeypatch):
    """Build a live order + REAL repository and hand back a runner that posts a
    payment through the REAL endpoint. Nothing under test is stubbed."""

    def _build(order_id="ORD-D1", grand_total=1600.0):
        coll = _OrdersColl([_order_doc(order_id, grand_total)])
        repo = OrderRepository(coll)
        monkeypatch.setattr(orders_module, "get_order_repository", lambda: repo)
        return {"coll": coll, "repo": repo, "order_id": order_id}

    return _build


async def _pay(order_id, **kw):
    return await add_payment(order_id, PaymentCreate(**kw), current_user=_cashier())


def _rows(*pairs):
    """(face, pieces) or (face, pieces, kind) -> count-sheet rows."""
    out = []
    for p in pairs:
        face, pieces = p[0], p[1]
        kind = p[2] if len(p) > 2 else "note"
        out.append({"face": face, "pieces": pieces, "kind": kind})
    return out


# The keys the feature adds. Everything OUTSIDE this set must be identical
# between a sale with a breakdown and the same sale without one.
_NEW_PAYMENT_KEYS = {
    "cash_tendered_count",
    "cash_change_count",
    "tendered_amount_paisa",
    "change_amount_paisa",
    "cash_leg_balanced",
}


def _strip_new(payment: Dict[str, Any]) -> Dict[str, Any]:
    return {
        k: v
        for k, v in payment.items()
        if k not in _NEW_PAYMENT_KEYS and k not in ("payment_id", "received_at")
    }


# ===========================================================================
# SAFETY RULE C -- the money is untouched
# ===========================================================================


@pytest.mark.asyncio
class TestTheSaleIsUnchanged:
    async def test_a_counted_cash_sale_persists_both_breakdowns(self, sale):
        """The customer hands 4 x Rs 500 for a Rs 1,600 bill and takes
        Rs 400 back. BOTH movements are on the record, at their own faces --
        a single net figure could never say a Rs 500 came in and two Rs 200
        went out."""
        ctx = sale()
        await _pay(
            ctx["order_id"],
            method="CASH",
            amount=1600.0,
            cash_tendered={"rows": _rows((500, 4)), "state": "COUNTED"},
            cash_change={"rows": _rows((200, 2)), "state": "SUGGESTED"},
            tendered_amount=2000.0,
            change_amount=400.0,
        )
        stored = ctx["coll"].find_one({"order_id": ctx["order_id"]})
        pay = stored["payments"][0]

        tendered = pay["cash_tendered_count"]
        change = pay["cash_change_count"]
        assert tendered["state"] == "COUNTED"
        assert [(r["face"], r["pieces"]) for r in tendered["rows"]] == [(500, 4)]
        assert tendered["total_paisa"] == 200000
        assert tendered["amount_paisa"] == 200000
        assert tendered["matches_amount"] is True
        assert tendered["captured_by"] == "U-cash"

        assert change["state"] == "SUGGESTED"
        assert [(r["face"], r["pieces"]) for r in change["rows"]] == [(200, 2)]
        assert change["total_paisa"] == 40000

        assert pay["tendered_amount_paisa"] == 200000
        assert pay["change_amount_paisa"] == 40000
        # tendered - change == the CASH LEG amount.
        assert pay["cash_leg_balanced"] is True

    async def test_the_money_is_byte_identical_with_and_without_a_breakdown(
        self, sale
    ):
        """THE SAFETY-RULE-C TEST. Run the SAME sale twice and diff the two
        persisted orders field by field."""
        bare_ctx = sale(order_id="ORD-BARE")
        await _pay(bare_ctx["order_id"], method="CASH", amount=1600.0)
        bare = bare_ctx["coll"].find_one({"order_id": "ORD-BARE"})

        rich_ctx = sale(order_id="ORD-RICH")
        await _pay(
            rich_ctx["order_id"],
            method="CASH",
            amount=1600.0,
            cash_tendered={"rows": _rows((500, 4)), "state": "COUNTED"},
            cash_change={"rows": _rows((200, 2)), "state": "COUNTED"},
            tendered_amount=2000.0,
            change_amount=400.0,
        )
        rich = rich_ctx["coll"].find_one({"order_id": "ORD-RICH"})

        # Every money field on the order.
        for field in (
            "grand_total",
            "amount_paid",
            "balance_due",
            "payment_status",
            "credit_sale",
        ):
            assert bare.get(field) == rich.get(field), field
        assert rich["payment_status"] == "PAID"
        assert rich["amount_paid"] == 1600.0
        assert rich["balance_due"] == 0.0

        # Every GST figure on every line -- ASSERT THE SET AND THE COUNT.
        assert len(bare["items"]) == len(rich["items"]) == 1
        assert bare["items"] == rich["items"]

        # The payment row itself, minus only the keys this feature adds.
        assert len(bare["payments"]) == len(rich["payments"]) == 1
        assert _strip_new(bare["payments"][0]) == _strip_new(rich["payments"][0])
        # ...and the bare sale carries the new keys too, as ABSENCE.
        assert set(rich["payments"][0]) - set(bare["payments"][0]) == set()

    async def test_a_non_cash_leg_carries_no_count_at_all(self, sale):
        """A UPI leg has no notes. Nothing is attached -- an empty count sheet
        on a digital tender would be a fiction."""
        ctx = sale()
        await _pay(ctx["order_id"], method="UPI", amount=1600.0, reference="upi-1")
        pay = ctx["coll"].find_one({"order_id": ctx["order_id"]})["payments"][0]
        assert _NEW_PAYMENT_KEYS.isdisjoint(pay.keys())


# ===========================================================================
# SAFETY RULE A -- a breakdown that does not add up never rewrites the money
# ===========================================================================


@pytest.mark.asyncio
class TestTheAmountIsTruth:
    async def test_a_mismatched_breakdown_is_flagged_and_the_amount_stands(
        self, sale
    ):
        """THE MONEY TEST. The cashier keys one Rs 500 note against a
        Rs 1,600 cash leg. The sale still records Rs 1,600."""
        ctx = sale()
        await _pay(
            ctx["order_id"],
            method="CASH",
            amount=1600.0,
            cash_tendered={"rows": _rows((500, 1)), "state": "COUNTED"},
            tendered_amount=1600.0,
        )
        stored = ctx["coll"].find_one({"order_id": ctx["order_id"]})
        pay = stored["payments"][0]

        # THE MONEY, explicitly. Not the count, not a corrected figure.
        assert pay["amount"] == 1600.0
        assert stored["amount_paid"] == 1600.0
        assert stored["balance_due"] == 0.0
        assert stored["payment_status"] == "PAID"
        # ...and the count is preserved exactly as keyed, with the flag up.
        assert pay["cash_tendered_count"]["total_paisa"] == 50000
        assert pay["cash_tendered_count"]["amount_paisa"] == 160000
        assert pay["cash_tendered_count"]["matches_amount"] is False
        assert cd.is_flagged(pay["cash_tendered_count"]) is True

    async def test_the_breakdown_is_anchored_to_the_cash_leg_not_the_bill(
        self, sale
    ):
        """A UPI Rs 1,000 + CASH Rs 850 split. The Rs 1,000 note the customer
        actually handed over is measured against the Rs 850 CASH LEG, with
        Rs 150 change -- not against the Rs 1,850 bill."""
        ctx = sale(grand_total=1850.0)
        await _pay(ctx["order_id"], method="UPI", amount=1000.0, reference="upi-9")
        await _pay(
            ctx["order_id"],
            method="CASH",
            amount=850.0,
            cash_tendered={"rows": _rows((1000, 1)), "state": "COUNTED"},
            cash_change={"rows": _rows((100, 1), (50, 1)), "state": "COUNTED"},
            tendered_amount=1000.0,
            change_amount=150.0,
        )
        stored = ctx["coll"].find_one({"order_id": ctx["order_id"]})
        assert len(stored["payments"]) == 2
        cash_leg = stored["payments"][1]
        assert cash_leg["cash_leg_balanced"] is True
        assert cash_leg["amount"] == 850.0
        assert stored["payment_status"] == "PAID"

    async def test_the_leg_identity_breaking_is_a_flag_never_a_rejection(
        self, sale
    ):
        """tendered - change != the leg amount. The sale still completes; the
        row simply carries a flag for a human."""
        ctx = sale()
        await _pay(
            ctx["order_id"],
            method="CASH",
            amount=1600.0,
            cash_tendered={"rows": _rows((500, 4)), "state": "COUNTED"},
            cash_change={"rows": _rows((100, 1)), "state": "COUNTED"},
            tendered_amount=2000.0,
            change_amount=100.0,
        )
        stored = ctx["coll"].find_one({"order_id": ctx["order_id"]})
        assert stored["payment_status"] == "PAID"
        assert stored["payments"][0]["amount"] == 1600.0
        assert stored["payments"][0]["cash_leg_balanced"] is False


# ===========================================================================
# SAFETY RULE B -- nothing blocks the sale, and blank is never zero
# ===========================================================================


@pytest.mark.asyncio
class TestNothingBlocksTheSale:
    async def test_a_cashier_in_a_hurry_completes_the_sale_as_not_captured(
        self, sale
    ):
        ctx = sale()
        resp = await _pay(ctx["order_id"], method="CASH", amount=1600.0)
        assert resp["amount"] == 1600.0

        stored = ctx["coll"].find_one({"order_id": ctx["order_id"]})
        assert stored["payment_status"] == "PAID"
        pay = stored["payments"][0]

        # BLANK IS NOT ZERO. Both are recorded as an absence, and the scalars
        # stay None rather than becoming a fabricated Rs 0.00 tendered.
        for block in (pay["cash_tendered_count"], pay["cash_change_count"]):
            assert block["state"] == "NOT_CAPTURED"
            assert block["rows"] == []
            assert block["matches_amount"] is None
            assert cd.is_captured(block) is False
        assert pay["tendered_amount_paisa"] is None
        assert pay["change_amount_paisa"] is None
        assert pay["cash_leg_balanced"] is None

    async def test_a_genuine_zero_change_is_counted_not_absent(self, sale):
        """"I counted the change and there was none" is a real answer and must
        not read as "nobody asked"."""
        ctx = sale()
        await _pay(
            ctx["order_id"],
            method="CASH",
            amount=1600.0,
            cash_tendered={"rows": _rows((500, 3), (100, 1)), "state": "COUNTED"},
            cash_change={"rows": [], "state": "COUNTED"},
            tendered_amount=1600.0,
            change_amount=0.0,
        )
        pay = ctx["coll"].find_one({"order_id": ctx["order_id"]})["payments"][0]
        assert pay["cash_change_count"]["state"] == "COUNTED"
        assert pay["cash_change_count"]["matches_amount"] is True
        assert pay["change_amount_paisa"] == 0
        assert pay["cash_leg_balanced"] is True

    async def test_counting_the_notes_without_typing_a_total_still_records_it(
        self, sale
    ):
        """The count IS the scalar when no scalar came with it -- that is what
        the cashier physically handled."""
        ctx = sale()
        await _pay(
            ctx["order_id"],
            method="CASH",
            amount=1600.0,
            cash_tendered={"rows": _rows((500, 4)), "state": "COUNTED"},
        )
        pay = ctx["coll"].find_one({"order_id": ctx["order_id"]})["payments"][0]
        assert pay["tendered_amount_paisa"] == 200000
        assert pay["cash_tendered_count"]["matches_amount"] is True
        # Change was never counted, so the identity is UNKNOWN -- not False.
        assert pay["change_amount_paisa"] is None
        assert pay["cash_leg_balanced"] is None


# ===========================================================================
# Cash OUT: refunds and payouts
# ===========================================================================


class _Leg:
    """The shape _normalize_refund_tenders reads off a RefundTenderLine."""

    def __init__(self, method, amount, cash_count=None):
        self.method = method
        self.amount = amount
        self.cash_count = (
            None if cash_count is None else cd.CashCountInput(**cash_count)
        )


class TestRefundLegsCarryTheirNotes:
    def test_a_cash_refund_leg_records_which_notes_left_the_drawer(self):
        from api.routers.returns import _normalize_refund_tenders

        legs, netted = _normalize_refund_tenders(
            [_Leg("CASH", 500.0, {"rows": _rows((500, 1)), "state": "COUNTED"})],
            500.0,
        )
        assert netted is True
        assert len(legs) == 1
        assert legs[0]["method"] == "CASH"
        assert legs[0]["amount"] == 500.0
        block = legs[0]["cash_count"]
        assert block["state"] == "COUNTED"
        assert [(r["face"], r["pieces"]) for r in block["rows"]] == [(500, 1)]
        assert block["matches_amount"] is True

    def test_two_cash_legs_fold_their_counts_together_not_just_their_amounts(
        self,
    ):
        """The amounts fold into one canonical CASH row; the notes must fold
        with them, or the folded row would report half the money as notes."""
        from api.routers.returns import _normalize_refund_tenders

        legs, _ = _normalize_refund_tenders(
            [
                _Leg("CASH", 500.0, {"rows": _rows((500, 1)), "state": "COUNTED"}),
                _Leg("CASH", 700.0, {"rows": _rows((500, 1), (200, 1)),
                                     "state": "COUNTED"}),
            ],
            1200.0,
        )
        assert len(legs) == 1
        block = legs[0]["cash_count"]
        assert legs[0]["amount"] == 1200.0
        by_face = {r["face"]: r["pieces"] for r in block["rows"]}
        assert by_face == {500: 2, 200: 1}
        assert block["total_paisa"] == 120000
        assert block["matches_amount"] is True

    def test_a_mismatched_refund_count_flags_but_the_refund_amount_stands(self):
        """Rule A on the way OUT. The 400 balance gate compares AMOUNTS; a
        denomination sum never enters that comparison."""
        from api.routers.returns import _normalize_refund_tenders

        legs, netted = _normalize_refund_tenders(
            [_Leg("CASH", 500.0, {"rows": _rows((100, 1)), "state": "COUNTED"})],
            500.0,
        )
        assert netted is True
        assert legs[0]["amount"] == 500.0          # THE MONEY, unchanged
        assert legs[0]["cash_count"]["matches_amount"] is False
        assert cd.is_flagged(legs[0]["cash_count"]) is True

    def test_a_refund_with_no_count_is_not_captured_not_zero(self):
        from api.routers.returns import _normalize_refund_tenders

        legs, netted = _normalize_refund_tenders([_Leg("CASH", 500.0)], 500.0)
        assert netted is True
        assert legs[0]["amount"] == 500.0
        assert legs[0]["cash_count"]["state"] == "NOT_CAPTURED"
        assert legs[0]["cash_count"]["matches_amount"] is None

    def test_a_digital_refund_leg_carries_no_count_object(self):
        from api.routers.returns import _normalize_refund_tenders

        legs, _ = _normalize_refund_tenders([_Leg("UPI", 500.0)], 500.0)
        assert "cash_count" not in legs[0]


# ===========================================================================
# THE DRAWER: the per-face tally across a whole day
# ===========================================================================


class _DrawerDB:
    """Orders / returns / expenses for one store-day."""

    def __init__(self, orders=None, returns=None, expenses=None):
        self._c = {
            "orders": _OrdersColl(orders or []),
            "returns": _OrdersColl(returns or []),
            "expenses": _OrdersColl(expenses or []),
        }

    def get_collection(self, name):
        return self._c.get(name, _OrdersColl([]))


def _block(rows, state="COUNTED"):
    return cd.build_drawer_block(rows, state=state)


def _day():
    """A realistic store-day. Open Rs 13,000 (20 x Rs 500, 10 x Rs 200,
    10 x Rs 100), sell twice for cash, refund one Rs 500, pay one Rs 200 bill
    out of the till. Every movement carries its notes."""
    opening = _block(_rows((500, 20), (200, 10), (100, 10)))
    orders = [
        {
            "order_id": "ORD-1",
            "store_id": "BV-PUN-01",
            "created_at": "2026-08-24T11:00:00",
            "payments": [
                {
                    "method": "CASH",
                    "amount": 1600.0,
                    # 4 x Rs 500 in, 2 x Rs 200 out.
                    "cash_tendered_count": cd.build_block(
                        _rows((500, 4)), 200000, state="COUNTED"
                    ),
                    "cash_change_count": cd.build_block(
                        _rows((200, 2)), 40000, state="COUNTED"
                    ),
                    "tendered_amount_paisa": 200000,
                    "change_amount_paisa": 40000,
                    "cash_leg_balanced": True,
                },
                {"method": "UPI", "amount": 500.0},
            ],
        },
        {
            "order_id": "ORD-2",
            "store_id": "BV-PUN-01",
            "created_at": "2026-08-24T12:00:00",
            "payments": [
                {
                    "method": "CASH",
                    "amount": 300.0,
                    "cash_tendered_count": cd.build_block(
                        _rows((100, 3)), 30000, state="COUNTED"
                    ),
                    "cash_change_count": cd.build_block([], 0, state="COUNTED"),
                    "tendered_amount_paisa": 30000,
                    "change_amount_paisa": 0,
                    "cash_leg_balanced": True,
                }
            ],
        },
    ]
    returns = [
        {
            "return_id": "RET-1",
            "store_id": "BV-PUN-01",
            "created_at": "2026-08-24T13:00:00",
            "status": "COMPLETED",
            "refund_tenders": [
                {
                    "method": "CASH",
                    "amount": 500.0,
                    "cash_count": cd.build_block(
                        _rows((500, 1)), 50000, state="COUNTED"
                    ),
                }
            ],
        }
    ]
    expenses = [
        {
            "expense_id": "EXP-1",
            "store_id": "BV-PUN-01",
            "created_at": "2026-08-24T14:00:00",
            "payment_mode": "CASH",
            "amount": 200.0,
            "cash_count": cd.build_block(_rows((200, 1)), 20000, state="COUNTED"),
        }
    ]
    return opening, _DrawerDB(orders, returns, expenses)


# Expected pieces at close, worked out by hand:
#   Rs 500: 20 opening + 4 taken in - 1 refunded            = 23
#   Rs 200: 10 opening - 2 given as change - 1 paid out     =  7
#   Rs 100: 10 opening + 3 taken in                         = 13
_EXPECTED_PIECES = {("note", 500): 23, ("note", 200): 7, ("note", 100): 13}
_BALANCED_CLOSE = _rows((500, 23), (200, 7), (100, 13))


class TestTheDrawerTalliesFaceByFace:
    def test_a_balanced_day_shows_no_discrepancy_at_any_face(self):
        opening, db = _day()
        res = till.compute_face_ledger(
            db, "BV-PUN-01", "2026-08-24T00:00:00", "2026-08-24T23:59:59",
            opening_count=opening, closing_count=_block(_BALANCED_CLOSE),
        )
        by = {(r["kind"], r["face"]): r for r in res["rows"]}
        # ASSERT THE SET AND THE COUNT: every face that moved is where it
        # should be, and NO face anywhere on the ladder is out of true.
        for key, pieces in _EXPECTED_PIECES.items():
            assert by[key]["expected_pieces"] == pieces, key
            assert by[key]["counted_pieces"] == pieces, key
        assert [r for r in res["rows"] if r["difference_pieces"] != 0] == []
        assert res["difference_paisa"] == 0
        assert res["coverage"]["cash_sale_legs"] == 2
        assert res["coverage"]["cash_sale_legs_counted"] == 2
        assert res["coverage"]["refund_legs_counted"] == 1
        assert res["coverage"]["payouts_counted"] == 1
        assert res["coverage"]["flagged"] == 0

    def test_a_single_missing_rs500_note_shows_up_at_the_rs500_face(self):
        """THE PLANTED-DISCREPANCY TEST. One Rs 500 note is short at close."""
        opening, db = _day()
        short = _block(_rows((500, 22), (200, 7), (100, 13)))
        res = till.compute_face_ledger(
            db, "BV-PUN-01", "2026-08-24T00:00:00", "2026-08-24T23:59:59",
            opening_count=opening, closing_count=short,
        )
        by = {(r["kind"], r["face"]): r for r in res["rows"]}
        assert by[("note", 500)]["difference_pieces"] == -1
        assert by[("note", 500)]["difference_paisa"] == -50000
        assert res["difference_paisa"] == -50000
        # AND NOWHERE ELSE. Assert the SET as well as the number: exactly one
        # face is out, and it is the Rs 500 note.
        out_of_true = {
            (r["kind"], r["face"])
            for r in res["rows"]
            if r["difference_pieces"] != 0
        }
        assert out_of_true == {("note", 500)}

    def test_every_ladder_face_is_reported_exactly_once(self):
        opening, db = _day()
        res = till.compute_face_ledger(
            db, "BV-PUN-01", "2026-08-24T00:00:00", "2026-08-24T23:59:59",
            opening_count=opening, closing_count=_block(_BALANCED_CLOSE),
        )
        keys = [(r["kind"], r["face"]) for r in res["rows"]]
        assert len(keys) == len(set(keys))
        assert len(keys) == len(cd.NOTE_FACES) + len(cd.COIN_FACES)

    def test_an_uncounted_day_reports_no_coverage_rather_than_a_clean_tally(
        self,
    ):
        """A store where nobody counted anything must NOT read as balanced.
        Every block is NOT_CAPTURED, so nothing is accumulated and coverage
        says so out loud."""
        orders = [
            {
                "order_id": "ORD-9",
                "store_id": "BV-PUN-01",
                "created_at": "2026-08-24T11:00:00",
                "payments": [
                    {
                        "method": "CASH",
                        "amount": 1600.0,
                        "cash_tendered_count": cd.not_captured_block(160000),
                        "cash_change_count": cd.not_captured_block(0),
                    }
                ],
            }
        ]
        res = till.compute_face_ledger(
            _DrawerDB(orders), "BV-PUN-01",
            "2026-08-24T00:00:00", "2026-08-24T23:59:59",
            opening_count=cd.not_captured_block(0),
            closing_count=cd.not_captured_block(0),
        )
        assert res["coverage"]["cash_sale_legs"] == 1
        assert res["coverage"]["cash_sale_legs_counted"] == 0
        assert res["opening_captured"] is False
        assert res["closing_captured"] is False
        assert all(r["expected_pieces"] == 0 for r in res["rows"])

    def test_a_flagged_movement_is_counted_as_a_flag_for_the_manager(self):
        orders = [
            {
                "order_id": "ORD-8",
                "store_id": "BV-PUN-01",
                "created_at": "2026-08-24T11:00:00",
                "payments": [
                    {
                        "method": "CASH",
                        "amount": 1600.0,
                        # Rs 500 of notes keyed against a Rs 1,600 leg.
                        "cash_tendered_count": cd.build_block(
                            _rows((500, 1)), 160000, state="COUNTED"
                        ),
                        "cash_leg_balanced": False,
                    }
                ],
            }
        ]
        res = till.compute_face_ledger(
            _DrawerDB(orders), "BV-PUN-01",
            "2026-08-24T00:00:00", "2026-08-24T23:59:59",
        )
        assert res["coverage"]["flagged"] == 2  # the block, and the leg identity
        assert res["coverage"]["cash_sale_legs_counted"] == 1
