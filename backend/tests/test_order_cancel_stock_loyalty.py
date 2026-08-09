"""
IMS 2.0 - Order CANCEL must undo the sale: stock AND loyalty
=============================================================
F3 (P1 STOCK) -- create_order flips serialized frame/sunglass stock_units to
    SOLD (even for a DRAFT). cancel_order released ONLY the lens cells and set
    status=CANCELLED, so the serialized units stayed SOLD *forever* against a
    cancelled order: every cancellation permanently removed a sellable frame
    from AVAILABLE. Cancel now reactivates the units this order consumed --
    idempotently, and fail-soft-but-loud.

F4 (P1 MONEY) -- create_order awards loyalty points at CREATE; cancel had NO
    reversal. The points stayed redeemable at Re 1/point => unfunded discounts
    plus a farming vector (big order -> points -> cancel -> redeem). Points the
    customer had REDEEMED against the cancelled order were silently burned.
    Cancel now claws back the EARN and restores the REDEEM, idempotently on the
    order id, and stamps the outcome on the order for reconciliation.

Isolated fakes (no Mongo): the REAL StockRepository runs over a fake collection
so the atomic guarded writes are genuinely exercised, and the REAL
loyalty.reverse_for_cancel runs over fake ledger/account repos.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api.routers import loyalty as L  # noqa: E402
from api.routers import orders as om  # noqa: E402
from database.repositories.product_repository import StockRepository  # noqa: E402

_MISSING = object()


# --------------------------------------------------------------------------- #
# Fake stock collection (same matcher contract as test_stock_expiry_*)
# --------------------------------------------------------------------------- #
def _same_bson_type(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return True
    if isinstance(a, (datetime, date)) and isinstance(b, (datetime, date)):
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool)
    return isinstance(a, (int, float)) and isinstance(b, (int, float))


def _op(val, op, arg) -> bool:
    if op == "$in":
        return (None if val is _MISSING else val) in arg
    if op == "$nin":
        return (None if val is _MISSING else val) not in arg
    if op == "$ne":
        return not (val is not _MISSING and val == arg)
    if op == "$gte":
        return val is not _MISSING and _same_bson_type(val, arg) and val >= arg
    if op == "$type":
        return isinstance(val, str) if arg == "string" else False
    raise AssertionError("fake collection: unsupported operator " + op)


def _match(doc, flt) -> bool:
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(_match(doc, c) for c in cond):
                return False
            continue
        if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
            val = doc.get(key, _MISSING)
            for op, arg in cond.items():
                if op == "$exists":
                    if bool(arg) != (key in doc):
                        return False
                elif op == "$not":
                    if all(_op(val, o2, a2) for o2, a2 in arg.items()):
                        return False
                elif not _op(val, op, arg):
                    return False
            continue
        if doc.get(key) != cond:
            return False
    return True


class _FakeStockColl:
    def __init__(self, docs):
        self.docs = docs

    def _matching(self, flt):
        return [d for d in self.docs if _match(d, flt)]

    def find_one(self, flt, projection=None):
        found = self._matching(flt)
        return dict(found[0]) if found else None

    def count_documents(self, flt=None):
        return len(self._matching(flt or {}))

    def find_one_and_update(self, flt, upd, sort=None, **kw):
        cands = self._matching(flt)
        if sort:
            for key, direction in reversed(list(sort)):
                cands.sort(key=lambda d, k=key: str(d.get(k)), reverse=direction == -1)
        if not cands:
            return None
        target = cands[0]
        before = dict(target)
        target.update(upd.get("$set", {}))
        return before


class _FakeOrderRepo:
    def __init__(self, orders):
        self.orders = {o["order_id"]: o for o in orders}

    def find_by_id(self, oid):
        doc = self.orders.get(oid)
        return dict(doc) if doc else None

    def update(self, oid, data):
        if oid not in self.orders:
            return False
        self.orders[oid].update(data)
        return True


class _FakeTxns:
    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]
        self.created = []

    def find_for_customer(self, cid, limit=20):
        return [dict(r) for r in self.rows if r.get("customer_id") == cid][:limit]

    def create(self, doc):
        self.created.append(dict(doc))
        self.rows.append(dict(doc))
        return dict(doc)


class _FakeAccounts:
    def __init__(self, balance=0, le=0, lr=0):
        self.acct = {
            "balance_points": balance,
            "lifetime_earned": le,
            "lifetime_redeemed": lr,
        }
        self.adjustments = []

    def find_or_create(self, cid):
        return dict(self.acct)

    def adjust_balance(
        self,
        cid,
        delta_points=0,
        delta_lifetime_earned=0,
        delta_lifetime_redeemed=0,
        new_tier=None,
    ):
        self.adjustments.append(
            {
                "dp": delta_points,
                "dle": delta_lifetime_earned,
                "dlr": delta_lifetime_redeemed,
            }
        )
        self.acct["balance_points"] += delta_points
        self.acct["lifetime_earned"] += delta_lifetime_earned
        self.acct["lifetime_redeemed"] += delta_lifetime_redeemed


_ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["SUPERADMIN"],
    "store_ids": ["S1"],
    "active_store_id": "S1",
}


def _order(order_id="ORD-1", customer_id="C1", status="CONFIRMED", items=None):
    return {
        "order_id": order_id,
        "order_number": "SO-1",
        "store_id": "S1",
        "customer_id": customer_id,
        "status": status,
        "items": items
        if items is not None
        else [
            {
                "item_id": "L1",
                "item_type": "FRAME",
                "product_id": "P1",
                "quantity": 1,
                "line_index": 0,
            }
        ],
    }


def _unit(sid, **over):
    d = {
        "stock_id": sid,
        "_id": sid,
        "product_id": "P1",
        "store_id": "S1",
        "status": "SOLD",
        "order_id": "ORD-1",
        "sold_at": "2026-08-01T10:00:00",
    }
    d.update(over)
    return d


@pytest.fixture()
def wired(monkeypatch):
    """cancel_order with every external seam replaced by an isolated fake."""
    orders = _FakeOrderRepo([_order()])
    units = []
    stock_repo = StockRepository(_FakeStockColl(units))
    txns = _FakeTxns()
    accounts = _FakeAccounts()

    monkeypatch.setattr(om, "get_order_repository", lambda: orders)
    monkeypatch.setattr(om, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(om, "validate_store_access", lambda *a, **k: None)
    monkeypatch.setattr(L, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: accounts)

    # Lens release + the CRITICAL audit alert are separate concerns here.
    import api.services.lens_stock_hook as hook
    import api.services.audit_alerts as alerts

    async def _noop_release(**kwargs):
        return None

    async def _noop_alert(*a, **k):
        return None

    monkeypatch.setattr(hook, "release_for_cancel", _noop_release)
    monkeypatch.setattr(alerts, "alert_order_cancelled", _noop_alert)

    return {
        "orders": orders,
        "units": units,
        "stock": stock_repo,
        "txns": txns,
        "accounts": accounts,
    }


def _cancel(order_id="ORD-1"):
    return asyncio.run(
        om.cancel_order(
            order_id, reason="customer changed mind", current_user=_ADMIN
        )
    )


# =========================================================================== #
# F3 -- cancel puts the serialized units back on the shelf
# =========================================================================== #


def test_cancel_reactivates_the_units_this_order_consumed(wired):
    wired["units"].extend([_unit("U1"), _unit("U2")])

    res = _cancel()

    assert res["status"] == "CANCELLED"
    assert res["stock_units_released"] == 2
    for u in wired["units"]:
        assert u["status"] == "AVAILABLE"
        assert u["order_id"] is None          # stale sale attribution cleared
        assert u["sold_at"] is None
        assert u["prior_sold_order_id"] == "ORD-1"   # ...but lineage preserved
        assert u["release_reason"] == "ORDER_CANCELLED"
    # The unit is genuinely sellable again.
    assert wired["stock"].find_available("P1", "S1") == 2


def test_cancel_does_not_touch_another_orders_units_or_damaged_stock(wired):
    wired["units"].extend(
        [
            _unit("MINE"),
            _unit("THEIRS", order_id="ORD-OTHER"),
            _unit("BROKEN", status="DAMAGED"),
        ]
    )

    res = _cancel()

    assert res["stock_units_released"] == 1
    by_id = {u["stock_id"]: u for u in wired["units"]}
    assert by_id["MINE"]["status"] == "AVAILABLE"
    assert by_id["THEIRS"]["status"] == "SOLD"
    assert by_id["THEIRS"]["order_id"] == "ORD-OTHER"
    # A DAMAGED unit must NEVER be resurrected onto the sellable shelf.
    assert by_id["BROKEN"]["status"] == "DAMAGED"


def test_cancel_restock_is_idempotent(wired):
    wired["units"].extend([_unit("U1"), _unit("U2")])
    first = _cancel()
    assert first["stock_units_released"] == 2
    snapshot = [dict(u) for u in wired["units"]]

    # A retried cancel is refused by the status guard...
    with pytest.raises(HTTPException) as exc:
        _cancel()
    assert exc.value.status_code == 400

    # ...and even the raw stock undo cannot double-reactivate.
    assert wired["stock"].release_sold_units_for_order("ORD-1") == []
    assert wired["units"] == snapshot


def test_cancel_survives_a_stock_backend_failure_and_flags_it(wired, monkeypatch):
    class _BoomStock:
        def release_sold_units_for_order(self, *a, **k):
            raise RuntimeError("stock backend down")

    monkeypatch.setattr(om, "get_stock_repository", lambda: _BoomStock())

    res = _cancel()

    assert res["status"] == "CANCELLED"          # the cancel still succeeds
    assert res["stock_units_released"] == 0
    doc = wired["orders"].orders["ORD-1"]
    assert doc["cancel_stock_release_failed"] is True   # ...and is discoverable


def test_cancel_records_the_released_unit_ids_on_the_order(wired):
    wired["units"].append(_unit("U1"))
    _cancel()
    assert wired["orders"].orders["ORD-1"]["cancel_stock_released"] == ["U1"]


# =========================================================================== #
# F4 -- cancel claws back the earn and restores the redeem
# =========================================================================== #


def _earn(cid, oid, pts):
    return {"customer_id": cid, "type": "EARN", "points": pts, "order_id": oid}


def _redeem(cid, oid, pts):
    return {"customer_id": cid, "type": "REDEEM", "points": pts, "order_id": oid}


def test_cancel_claws_back_points_earned_on_the_order(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})

    res = _cancel()

    assert res["loyalty_reversal_failed"] is False
    assert wired["accounts"].adjustments == [{"dp": -100, "dle": -100, "dlr": 0}]
    assert wired["accounts"].acct["balance_points"] == 0
    stamp = wired["orders"].orders["ORD-1"]["loyalty_reversal"]
    assert stamp["ok"] is True and stamp["earned_clawed"] == 100


def test_cancel_restores_points_the_customer_redeemed_on_the_order(wired):
    # Bought with 150 earned + 40 redeemed against this same order.
    wired["txns"].rows.extend(
        [_earn("C1", "ORD-1", 150), _redeem("C1", "ORD-1", 40)]
    )
    wired["accounts"].acct.update(
        {"balance_points": 110, "lifetime_earned": 150, "lifetime_redeemed": 40}
    )

    _cancel()

    # net = restore 40 - claw 150 = -110 -> balance back to 0, both lifetimes undone.
    assert wired["accounts"].adjustments == [{"dp": -110, "dle": -150, "dlr": -40}]
    assert wired["accounts"].acct["balance_points"] == 0
    assert wired["accounts"].acct["lifetime_redeemed"] == 0
    stamp = wired["orders"].orders["ORD-1"]["loyalty_reversal"]
    assert stamp["earned_clawed"] == 150 and stamp["redeemed_restored"] == 40


def test_cancel_only_redeemed_no_earn_returns_the_points(wired):
    """A cancelled order paid partly with points must give the points BACK --
    the old behaviour silently burned the customer's balance."""
    wired["txns"].rows.append(_redeem("C1", "ORD-1", 60))
    wired["accounts"].acct.update({"balance_points": 0, "lifetime_redeemed": 60})

    _cancel()

    assert wired["accounts"].adjustments == [{"dp": 60, "dle": 0, "dlr": -60}]
    assert wired["accounts"].acct["balance_points"] == 60


def test_cancel_loyalty_reversal_is_idempotent_on_the_order(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})
    _cancel()
    assert len(wired["accounts"].adjustments) == 1

    # A retried reversal (the cancel endpoint 400s, but the engine itself must
    # be safe under retry / replay) claws NOTHING extra.
    again = L.reverse_for_cancel("ORD-1", "C1")
    assert again["ok"] is True and again.get("already_reversed") is True
    assert len(wired["accounts"].adjustments) == 1
    assert wired["accounts"].acct["balance_points"] == 0


def test_cancel_does_not_double_claw_after_a_return_already_reversed(wired):
    wired["txns"].rows.extend(
        [
            _earn("C1", "ORD-1", 100),
            {
                "customer_id": "C1",
                "type": "ADJUST",
                "points": -100,
                "order_id": "ORD-1",
                "return_id": "RET-9",
            },
        ]
    )
    wired["accounts"].acct.update({"balance_points": 0})

    res = _cancel()

    assert res["loyalty_reversal_failed"] is False
    assert wired["accounts"].adjustments == []      # nothing clawed twice
    assert wired["orders"].orders["ORD-1"]["loyalty_reversal"]["already_reversed"]


def test_cancel_flags_a_failed_clawback_for_reconciliation(wired):
    # Earned 100 but only 30 left (70 already spent on a LATER order): the claw
    # would drive the balance negative, which must escalate, not silently clamp.
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 30, "lifetime_earned": 100})

    res = _cancel()

    assert res["status"] == "CANCELLED"           # cancel is never blocked
    assert res["loyalty_reversal_failed"] is True
    doc = wired["orders"].orders["ORD-1"]
    assert doc["loyalty_reversal_failed"] is True
    assert doc["loyalty_reversal"]["reason"] == "balance_underflow"
    assert wired["accounts"].adjustments == []    # balance untouched


def test_cancel_of_a_walkin_order_skips_loyalty_cleanly(wired):
    wired["orders"].orders["ORD-1"]["customer_id"] = "walkin-9812345678"
    wired["units"].append(_unit("U1"))

    res = _cancel()

    assert res["status"] == "CANCELLED"
    assert res["loyalty_reversal_failed"] is False
    assert wired["txns"].created == []
    assert wired["accounts"].adjustments == []
    assert res["stock_units_released"] == 1       # stock still comes back


def test_cancel_survives_a_loyalty_exception_and_flags_it(wired, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("loyalty engine exploded")

    monkeypatch.setattr(L, "reverse_for_cancel", _boom)

    res = _cancel()

    assert res["status"] == "CANCELLED"
    assert res["loyalty_reversal_failed"] is True
    assert wired["orders"].orders["ORD-1"]["loyalty_reversal_failed"] is True


def test_delivered_order_still_cannot_be_cancelled(wired):
    wired["orders"].orders["ORD-1"]["status"] = "DELIVERED"
    wired["units"].append(_unit("U1"))
    with pytest.raises(HTTPException) as exc:
        _cancel()
    assert exc.value.status_code == 400
    # A refused cancel must not move stock or points.
    assert wired["units"][0]["status"] == "SOLD"
    assert wired["accounts"].adjustments == []


# =========================================================================== #
# reverse_for_return is untouched by the refactor into the shared engine
# =========================================================================== #


def test_reverse_for_return_still_keyed_on_return_id(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 80))
    wired["accounts"].acct.update({"balance_points": 80, "lifetime_earned": 80})

    first = L.reverse_for_return("RET-1", "ORD-1", "C1")
    assert first["ok"] and first["earned_clawed"] == 80
    assert wired["txns"].created[0]["return_id"] == "RET-1"
    assert wired["txns"].created[0]["type"] == "ADJUST"

    # Same return id replayed -> no second claw.
    again = L.reverse_for_return("RET-1", "ORD-1", "C1")
    assert again.get("already_reversed") is True
    assert len(wired["accounts"].adjustments) == 1


def test_reverse_for_cancel_marker_is_tagged_with_the_order(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 50))
    wired["accounts"].acct.update({"balance_points": 50, "lifetime_earned": 50})
    L.reverse_for_cancel("ORD-1", "C1")
    marker = wired["txns"].created[0]
    assert marker["type"] == "ADJUST"
    assert marker["cancel_of_order_id"] == "ORD-1"
    assert marker["order_id"] == "ORD-1"
    assert marker["source"] == "ORDER_CANCEL"
    assert marker["points"] == -50
