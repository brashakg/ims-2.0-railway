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
import re
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
        if arg == "string":
            return isinstance(val, str)
        if arg == "date":
            # BSON date-typed values -- the shape inventory._parse_expiry reads
            # natively and buckets as EXPIRED, so the floor must reach them too.
            return isinstance(val, (datetime, date)) and not isinstance(val, bool)
        return False
    if op == "$regex":
        # Mongo $regex only ever matches STRING values -- that type-bracketing is
        # exactly what makes the ISO-shape branch of the expiry floor work.
        return isinstance(val, str) and re.search(arg, val) is not None
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


class _FakeOrdersColl:
    """The order collection's atomic surface. _claim_order_for_cancel uses
    find_one_and_update with a $nin status filter -- WITHOUT this the repo has
    no `collection`, the claim silently takes the legacy fallback branch, and
    the atomic guard that IS the fix is never executed by any test."""

    def __init__(self, orders):
        self.orders = orders
        self.calls = 0

    def find_one_and_update(self, flt, upd, **kw):
        self.calls += 1
        oid = flt.get("order_id")
        doc = self.orders.get(oid)
        if doc is None:
            return None
        status_cond = flt.get("status")
        if isinstance(status_cond, dict):
            if "$nin" in status_cond and doc.get("status") in status_cond["$nin"]:
                return None
            if "$in" in status_cond and doc.get("status") not in status_cond["$in"]:
                return None
        elif status_cond is not None and doc.get("status") != status_cond:
            return None
        before = dict(doc)
        doc.update(upd.get("$set", {}))
        return before


class _FakeOrderRepo:
    def __init__(self, orders, with_collection=True):
        self.orders = {o["order_id"]: o for o in orders}
        self.update_ok = True
        self.collection = (
            _FakeOrdersColl(self.orders) if with_collection else None
        )

    def find_by_id(self, oid):
        doc = self.orders.get(oid)
        return dict(doc) if doc else None

    def update(self, oid, data):
        if not self.update_ok or oid not in self.orders:
            return False
        self.orders[oid].update(data)
        return True


class _FakeTxnColl:
    """The loyalty_transactions collection surface used by the marker flag
    updates and the atomic repair claim. Backed by the SAME row list the repo
    hands out, so a flag written here is visible to the next ledger read."""

    def __init__(self, rows):
        self.rows = rows

    def find(self, flt=None):
        """Order-scoped ledger read. The reversal now queries the collection
        directly so a driver error RAISES instead of being swallowed into an
        empty list that looks like 'this order earned nothing'."""
        return [
            dict(r)
            for r in self.rows
            if all(r.get(k) == v for k, v in (flt or {}).items())
        ]

    def update_one(self, flt, upd):
        for r in self.rows:
            if all(r.get(k) == v for k, v in flt.items()):
                r.update(upd.get("$set", {}))
                return type("obj", (object,), {"modified_count": 1})()
        return type("obj", (object,), {"modified_count": 0})()

    def find_one_and_update(self, flt, upd, **kw):
        for r in self.rows:
            if all(r.get(k) == v for k, v in flt.items()):
                before = dict(r)
                r.update(upd.get("$set", {}))
                return before
        return None


class _DuplicateKeyError(Exception):
    """Stands in for pymongo.errors.DuplicateKeyError (matched by class name)."""


DuplicateKeyError = _DuplicateKeyError
_DuplicateKeyError.__name__ = "DuplicateKeyError"


class _FakeTxns:
    """Models the partial UNIQUE index on (customer_id, cancel_of_order_id) and
    (customer_id, return_id) for type=ADJUST -- the DB-level guard the reversal
    claim now relies on. Without modelling it here the concurrency test would
    prove nothing."""

    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]
        self.created = []
        # The reversal writes its marker flags through the raw collection.
        self.collection = _FakeTxnColl(self.rows)

    def find_for_customer(self, cid, limit=20):
        return [dict(r) for r in self.rows if r.get("customer_id") == cid][:limit]

    def _violates_unique(self, doc):
        if doc.get("type") != "ADJUST":
            return False
        for field in ("cancel_of_order_id", "return_id", "reversal_of_order_id"):
            val = doc.get(field)
            if not isinstance(val, str):
                continue
            for r in self.rows:
                if (
                    r.get("type") == "ADJUST"
                    and r.get("customer_id") == doc.get("customer_id")
                    and r.get(field) == val
                ):
                    return True
        return False

    def create(self, doc, raise_on_duplicate=False):
        if self._violates_unique(doc):
            if raise_on_duplicate:
                raise _DuplicateKeyError("duplicate reversal marker")
            return None
        self.created.append(dict(doc))
        self.rows.append(dict(doc))
        return dict(doc)


class _FakeAccountsColl:
    """Models the EXACTLY-ONCE guard: applied_reversals $ne + $addToSet in one
    atomic write, plus the balance_points $gte underflow guard. Without this the
    concurrency tests would prove nothing -- the guard IS the fix."""

    def __init__(self, owner):
        self.owner = owner

    def find_one_and_update(self, flt, upd, **kw):
        acct = self.owner.acct
        # MONGO SEMANTICS, DELIBERATELY: $ne against a MISSING field MATCHES,
        # and against an EMPTY array it matches too. This fake used to
        # setdefault the array into existence, which made the absent-field
        # shape -- 100% of production loyalty_accounts -- UNREACHABLE in every
        # loyalty test. Do not reintroduce the setdefault; the missing shape is
        # the one that mattered.
        applied = acct.get("applied_reversals")
        ne = (flt.get("applied_reversals") or {}).get("$ne")
        if ne is not None and applied is not None and ne in applied:
            return None                      # this reversal already applied
        need = (flt.get("balance_points") or {}).get("$gte")
        if need is not None and acct.get("balance_points", 0) < need:
            return None                      # underflow guard refuses
        before = dict(acct)
        for k, v in upd.get("$inc", {}).items():
            acct[k] = acct.get(k, 0) + v
        for k, v in upd.get("$set", {}).items():
            acct[k] = v
        for k, v in upd.get("$addToSet", {}).items():
            # $addToSet CREATES the array -- that is the only thing that may.
            acct.setdefault(k, [])
            if v not in acct[k]:
                acct[k].append(v)
        inc = upd.get("$inc", {})
        self.owner.adjustments.append({
            "dp": inc.get("balance_points", 0),
            "dle": inc.get("lifetime_earned", 0),
            "dlr": inc.get("lifetime_redeemed", 0),
        })
        if "tier" in upd.get("$set", {}):
            self.owner.tiers_set.append(upd["$set"]["tier"])
        return before


class _FakeAccounts:
    def __init__(self, balance=0, le=0, lr=0, tier="BRONZE", applied=None):
        self.acct = {
            "balance_points": balance,
            "lifetime_earned": le,
            "lifetime_redeemed": lr,
            "tier": tier,
        }
        # DEFAULT IS THE PRODUCTION SHAPE: no applied_reversals key at all.
        # Every one of the 8 live loyalty_accounts looks like this, and seeding
        # an empty list here is what hid the legacy-marker defect from 60 tests.
        if applied is not None:
            self.acct["applied_reversals"] = list(applied)
        self.collection = _FakeAccountsColl(self)
        self.adjustments = []
        # Every non-None new_tier the reversal asked for, in order. Proves the
        # tier moves in the SAME adjust_balance call as the lifetime decrement.
        self.tiers_set = []

    def find_or_create(self, cid):
        return dict(self.acct)

    def find_by_id(self, cid):
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
        if new_tier is not None:
            self.tiers_set.append(new_tier)
            self.acct["tier"] = new_tier
        # The REAL LoyaltyAccountRepository.adjust_balance returns the account
        # doc on BOTH its success and its swallowed-exception path. The
        # reversal verifies the deltas against exactly this value, so a fake
        # that returned None would make every reversal look like a failed $inc.
        return dict(self.acct)


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
        "monkeypatch": monkeypatch,
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
    again = wired["stock"].release_sold_units_for_order("ORD-1")
    assert again.released == [] and again.incomplete is False
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


# =========================================================================== #
# PANEL MUST-FIX 2 -- the reversal claim must be ATOMIC, not check-then-write.
# The ledger snapshot is advisory; the partial UNIQUE index on
# (customer_id, cancel_of_order_id) for type=ADJUST is the real guard.
# =========================================================================== #


def test_two_concurrent_cancels_claw_exactly_once(wired):
    """DOUBLE-CLAW would BURN the customer's points. Both callers see the same
    pre-reversal ledger (the check-then-write window); only one may insert."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})

    first = L.reverse_for_cancel("ORD-1", "C1")
    second = L.reverse_for_cancel("ORD-1", "C1")

    assert first["ok"] and first.get("earned_clawed") == 100
    assert second["ok"] and second.get("already_reversed") is True
    assert len(wired["accounts"].adjustments) == 1     # ONE balance move
    assert wired["accounts"].acct["balance_points"] == 0
    assert len(wired["txns"].created) == 1             # ONE marker row


def test_racing_cancel_that_loses_the_unique_index_does_not_move_money(wired):
    """The loser's ledger snapshot is STALE (taken before the winner inserted),
    so the Python guard passes and only the DuplicateKeyError stops it. This is
    the exact interleaving that used to double-restore/double-claw."""
    wired["txns"].rows.append(_redeem("C1", "ORD-1", 60))
    wired["accounts"].acct.update({"balance_points": 0, "lifetime_redeemed": 60})

    # Snapshot the ledger as it looks BEFORE the winner writes its marker.
    stale = [dict(r) for r in wired["txns"].rows]
    real_coll_find = wired["txns"].collection.find

    def _stale_ledger(flt=None):
        return [dict(r) for r in stale]

    winner = L.reverse_for_cancel("ORD-1", "C1")
    assert winner["ok"] and winner["redeemed_restored"] == 60
    assert wired["accounts"].acct["balance_points"] == 60

    # The racer now runs with the pre-winner ledger view, so its Python guard
    # sees nothing and ONLY the partial-unique index can stop it.
    wired["txns"].collection.find = _stale_ledger
    try:
        loser = L.reverse_for_cancel("ORD-1", "C1")
    finally:
        wired["txns"].collection.find = real_coll_find

    assert loser["ok"] and loser.get("already_reversed") is True
    assert loser.get("raced") is True
    # NO second restore: the balance did not double to 120 (minted rupees).
    assert wired["accounts"].acct["balance_points"] == 60
    assert len(wired["accounts"].adjustments) == 1


def test_marker_write_that_returns_none_does_not_move_the_balance(wired):
    """If the claim insert comes back empty we do NOT hold the claim, so we must
    not touch money -- failing toward NOT clawing."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})
    wired["txns"].create = lambda doc, raise_on_duplicate=False: None

    res = L.reverse_for_cancel("ORD-1", "C1")

    assert res["ok"] is False and res["reason"] == "marker_write_failed"
    assert wired["accounts"].adjustments == []


# =========================================================================== #
# PANEL MUST-FIX 9 -- lifetime_earned drops, so the TIER must be recomputed.
# =========================================================================== #


def test_cancel_downgrades_an_unearned_tier(wired):
    """Buy big -> GOLD -> cancel. Leaving the tier up gives a permanent 1.25x /
    1.5x earn multiplier on every FUTURE genuine purchase."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 6000))
    wired["accounts"].acct.update(
        {"balance_points": 6000, "lifetime_earned": 6000, "tier": "GOLD"}
    )

    _cancel()

    assert wired["accounts"].acct["lifetime_earned"] == 0
    # adjust_balance was told to move the tier back down in the SAME call.
    assert wired["accounts"].tiers_set == ["BRONZE"]
    assert wired["accounts"].acct["tier"] == "BRONZE"


def test_cancel_leaves_a_still_earned_tier_alone(wired):
    """Only the points from THIS order come off; a tier the customer still
    qualifies for on their remaining lifetime must not be touched."""
    wired["txns"].rows.extend(
        [_earn("C1", "ORD-1", 50), _earn("C1", "ORD-OLD", 6000)]
    )
    wired["accounts"].acct.update(
        {"balance_points": 6050, "lifetime_earned": 6050, "tier": "GOLD"}
    )

    _cancel()

    assert wired["accounts"].acct["lifetime_earned"] == 6000
    assert wired["accounts"].tiers_set == []        # no tier change requested
    assert wired["accounts"].acct["tier"] == "GOLD"


# =========================================================================== #
# PANEL MUST-FIX 3 + 4 -- single-shot cancel, and a re-runnable undo.
# =========================================================================== #


def test_concurrent_cancels_run_the_undo_exactly_once(wired):
    """Two cancels in flight: the guarded claim means only ONE flips the order,
    so the stock reactivation and the loyalty clawback happen once."""
    wired["units"].extend([_unit("U1"), _unit("U2")])
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})

    first = _cancel()
    with pytest.raises(HTTPException) as exc:
        _cancel()

    assert first["stock_units_released"] == 2
    assert exc.value.status_code == 400
    assert len(wired["accounts"].adjustments) == 1
    assert sum(1 for u in wired["units"] if u["status"] == "AVAILABLE") == 2


def test_partial_restock_is_reported_and_the_cancel_can_be_rerun(wired):
    """A mid-loop write failure used to answer 200 'Order cancelled' with units
    still SOLD, and the already-cancelled 400 then refused every retry --
    permanent silent stock loss. Now it is flagged AND re-runnable."""
    coll = wired["stock"].collection
    calls = {"n": 0}
    real = coll.find_one_and_update

    def _flaky(flt, upd, sort=None, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("mongo write blip")
        return real(flt, upd, sort=sort, **kw)

    coll.find_one_and_update = _flaky
    wired["units"].extend([_unit("U1"), _unit("U2")])

    first = _cancel()

    assert first["status"] == "CANCELLED"
    assert first["stock_release_failed"] is True      # NOT reported as clean
    assert first["stock_units_still_sold"] == 1
    doc = wired["orders"].orders["ORD-1"]
    assert doc["cancel_stock_release_failed"] is True

    # The blip clears; re-POSTing the cancel finishes the job instead of 400ing.
    coll.find_one_and_update = real
    retry = _cancel()

    assert retry["message"] == "Cancel undo re-run"
    assert retry["stock_release_failed"] is False
    assert retry["stock_units_still_sold"] == 0
    assert all(u["status"] == "AVAILABLE" for u in wired["units"])


def test_a_clean_cancel_still_refuses_a_second_cancel(wired):
    """The re-run door opens ONLY for an unfinished undo -- a completed cancel
    must still 400, or the endpoint becomes replayable."""
    wired["units"].append(_unit("U1"))
    _cancel()
    with pytest.raises(HTTPException) as exc:
        _cancel()
    assert exc.value.status_code == 400
    assert "already cancelled" in str(exc.value.detail).lower()


# =========================================================================== #
# RE-VERIFY MUST-FIX 2 -- the tier recompute must NOT reach the RETURNS path.
# origin/main's reverse_for_return passed no new_tier. Because the return claw
# is order-wide, not line-proportional, ONE partial return of a multi-line order
# would otherwise demote a legitimately held tier and permanently cut the
# customer's earn multiplier on every future purchase.
# =========================================================================== #


def test_partial_return_never_moves_the_tier(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 6000))
    wired["accounts"].acct.update(
        {"balance_points": 6000, "lifetime_earned": 6000, "tier": "GOLD"}
    )

    res = L.reverse_for_return("RET-1", "ORD-1", "C1")

    assert res["ok"] and res["earned_clawed"] == 6000
    assert wired["accounts"].tiers_set == []          # NO tier write at all
    assert wired["accounts"].acct["tier"] == "GOLD"   # untouched
    assert res.get("tier_changed") is False


def test_cancel_still_moves_the_tier(wired):
    """The mirror of the test above -- proving the flag is opt-in, not off."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 6000))
    wired["accounts"].acct.update(
        {"balance_points": 6000, "lifetime_earned": 6000, "tier": "GOLD"}
    )

    res = L.reverse_for_cancel("ORD-1", "C1")

    assert res["ok"] and res["tier_changed"] is True
    assert wired["accounts"].tiers_set == ["BRONZE"]


# =========================================================================== #
# RE-VERIFY MUST-FIX 3 -- a SECOND partial return must not re-reverse the order.
# Partial returns are cumulative by design, so return #2 carries a different
# return_id, collides with no unique index, and used to claw the WHOLE order
# again: points MINTED and lifetime_redeemed driven NEGATIVE.
# =========================================================================== #


def test_second_partial_return_does_not_reverse_the_order_again(wired):
    wired["txns"].rows.extend(
        [_earn("C1", "ORD-1", 100), _redeem("C1", "ORD-1", 40)]
    )
    wired["accounts"].acct.update(
        {"balance_points": 60, "lifetime_earned": 100, "lifetime_redeemed": 40}
    )

    first = L.reverse_for_return("RET-1", "ORD-1", "C1")
    second = L.reverse_for_return("RET-2", "ORD-1", "C1")   # different return!

    assert first["ok"] and first["earned_clawed"] == 100
    assert second["ok"] and second.get("already_reversed") is True
    assert len(wired["accounts"].adjustments) == 1        # ONE reversal only
    assert wired["accounts"].acct["balance_points"] == 0
    assert wired["accounts"].acct["lifetime_redeemed"] == 0
    assert wired["accounts"].acct["lifetime_earned"] == 0


def test_every_reversal_marker_carries_the_canonical_order_key(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 10))
    wired["accounts"].acct.update({"balance_points": 10, "lifetime_earned": 10})
    L.reverse_for_return("RET-9", "ORD-1", "C1")
    marker = wired["txns"].created[0]
    assert marker["reversal_of_order_id"] == "ORD-1"
    assert marker["return_id"] == "RET-9"


def test_cancel_after_a_return_is_still_blocked_by_the_canonical_key(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})
    L.reverse_for_return("RET-1", "ORD-1", "C1")
    wired["accounts"].adjustments.clear()

    res = L.reverse_for_cancel("ORD-1", "C1")

    assert res["ok"] and res.get("already_reversed") is True
    assert wired["accounts"].adjustments == []


# =========================================================================== #
# RE-VERIFY MUST-FIX 4 -- adjust_balance CANNOT raise, so "no exception" proves
# nothing. A silently failed $inc used to report ok=True with the marker
# consumed, leaving the money permanently unreconcilable.
# =========================================================================== #





# =========================================================================== #
# RE-VERIFY MUST-FIX 5 -- the ATOMIC claim must be the path under test, and a
# failed status write must NOT run the undo.
# =========================================================================== #


def test_cancel_uses_the_atomic_claim_path(wired):
    wired["units"].append(_unit("U1"))
    _cancel()
    # The guarded find_one_and_update -- the actual fix -- was executed.
    assert wired["orders"].collection.calls >= 1


def test_atomic_claim_refuses_a_second_cancel_without_a_second_undo(wired):
    wired["units"].extend([_unit("U1"), _unit("U2")])
    _cancel()
    calls_after_first = wired["orders"].collection.calls
    with pytest.raises(HTTPException) as exc:
        _cancel()
    assert exc.value.status_code == 400
    # The second attempt DID go through the atomic filter (and matched nothing).
    assert wired["orders"].collection.calls > calls_after_first


def test_failed_status_write_does_not_run_the_undo(monkeypatch):
    """Legacy fallback path (no `collection`): base_repository.update returns
    False on failure, so discarding it ran the FULL stock + loyalty undo and
    reported success for an order still sitting CONFIRMED."""
    orders = _FakeOrderRepo([_order()], with_collection=False)
    orders.update_ok = False
    units = [_unit("U1")]
    stock = StockRepository(_FakeStockColl(units))
    txns = _FakeTxns([_earn("C1", "ORD-1", 100)])
    accounts = _FakeAccounts(balance=100, le=100)

    monkeypatch.setattr(om, "get_order_repository", lambda: orders)
    monkeypatch.setattr(om, "get_stock_repository", lambda: stock)
    monkeypatch.setattr(om, "validate_store_access", lambda *a, **k: None)
    monkeypatch.setattr(L, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: accounts)

    import api.services.lens_stock_hook as hook
    import api.services.audit_alerts as alerts

    async def _noop(**kwargs):
        return None

    async def _noop_alert(*a, **k):
        return None

    monkeypatch.setattr(hook, "release_for_cancel", _noop)
    monkeypatch.setattr(alerts, "alert_order_cancelled", _noop_alert)

    with pytest.raises(HTTPException) as exc:
        _cancel()

    assert exc.value.status_code == 500
    # NOTHING was undone: the order is not cancelled, so its stock and points
    # must still belong to it.
    assert orders.orders["ORD-1"]["status"] == "CONFIRMED"
    assert units[0]["status"] == "SOLD"
    assert accounts.adjustments == []


# =========================================================================== #
# RE-VERIFY R4 MF3 -- the retry door must open on GROUND TRUTH, not on a flag.
# The failure stamp goes through base_repository.update, which swallows every
# exception and returns False, so the very blip that fails the stock release
# ALSO loses its own failure marker. Gating the retry on the flag meant the
# operator log told staff to re-POST a cancel that 400s forever with units
# still SOLD.
# =========================================================================== #


def test_retry_door_opens_on_stranded_stock_even_with_no_failure_marker(wired):
    """Simulates the blip losing BOTH the release and the stamp: the doc says
    the cancel was clean, the stock says otherwise. Stock wins."""
    wired["units"].extend([_unit("U1"), _unit("U2")])
    coll = wired["stock"].collection
    real = coll.find_one_and_update
    coll.find_one_and_update = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("blip")
    )
    stamps = {"blocked": True}
    real_update = wired["orders"].update

    def _update(oid, data):
        # The stamp write fails too -- exactly the correlated failure.
        if stamps["blocked"] and "cancel_stock_release_failed" in data:
            return False
        return real_update(oid, data)

    wired["orders"].update = _update
    try:
        first = _cancel()
    finally:
        coll.find_one_and_update = real
        wired["orders"].update = real_update

    assert first["status"] == "CANCELLED"
    doc = wired["orders"].orders["ORD-1"]
    # The doc carries NO failure marker -- the stamp was lost.
    assert doc.get("cancel_stock_release_failed") is not True
    # ...but both units are still SOLD, and that is what opens the door.
    assert wired["stock"].count_sold_units_for_order("ORD-1") == 2

    retry = _cancel()

    assert retry["message"] == "Cancel undo re-run"
    assert retry["stock_units_released"] == 2
    assert all(u["status"] == "AVAILABLE" for u in wired["units"])


def test_clean_cancel_with_a_stamp_still_refuses_a_retry(wired):
    """The ground-truth door must not make the endpoint replayable."""
    wired["units"].append(_unit("U1"))
    _cancel()
    assert wired["stock"].count_sold_units_for_order("ORD-1") == 0
    with pytest.raises(HTTPException) as exc:
        _cancel()
    assert exc.value.status_code == 400


def test_lost_stamp_is_logged_loudly(wired, caplog):
    wired["units"].append(_unit("U1"))
    real_update = wired["orders"].update

    def _update(oid, data):
        if "cancel_stock_released" in data or "cancel_stock_release_failed" in data:
            return False
        return real_update(oid, data)

    wired["orders"].update = _update
    try:
        with caplog.at_level("ERROR"):
            _cancel()
    finally:
        wired["orders"].update = real_update

    assert any(
        "CANCEL RECONCILIATION STAMP LOST" in r.message for r in caplog.records
    )


# =========================================================================== #
# RE-VERIFY R4 MF9 -- an unreadable ledger must not look like an empty one.
# find_for_customer swallows every driver error and returns [], so a transient
# blip during a cancel returned ok:True / clawed 0 / no failure flag, leaving
# the customer holding redeemable points on a cancelled order.
# =========================================================================== #


def test_ledger_read_failure_is_not_mistaken_for_an_empty_ledger(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})

    def _boom(flt=None):
        raise RuntimeError("mongo read blip")

    wired["txns"].collection.find = _boom

    res = L.reverse_for_cancel("ORD-1", "C1")

    assert res["ok"] is False and res["reason"] == "ledger_read_failed"
    assert wired["accounts"].adjustments == []      # no money moved on a guess


def test_cancel_flags_an_unreadable_ledger_for_reconciliation(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})

    def _boom(flt=None):
        raise RuntimeError("mongo read blip")

    wired["txns"].collection.find = _boom

    res = _cancel()

    assert res["status"] == "CANCELLED"
    assert res["loyalty_reversal_failed"] is True
    assert wired["orders"].orders["ORD-1"]["loyalty_reversal_failed"] is True


def test_a_genuinely_empty_ledger_is_still_a_clean_no_op(wired):
    """The failure signal must not fire on an order that simply earned nothing."""
    res = L.reverse_for_cancel("ORD-1", "C1")
    assert res["ok"] is True and res.get("earned_clawed") == 0
    assert wired["accounts"].adjustments == []


def test_order_scoped_ledger_read_is_not_truncated_by_a_long_history(wired):
    """The old limit=1000 could drop an old order's EARN row off the end. The
    order-scoped query cannot truncate."""
    for i in range(1200):
        wired["txns"].rows.append(_earn("C1", f"OTHER-{i}", 1))
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})

    res = L.reverse_for_cancel("ORD-1", "C1")

    assert res["ok"] and res["earned_clawed"] == 100


# =========================================================================== #
# RE-VERIFY R4 MF10 -- the clawback must be guard-in-the-filter, not a read
# followed by an unguarded $inc.
# =========================================================================== #


class _GuardedAccounts(_FakeAccounts):
    """Models the real collection surface, so the reversal takes the GUARDED
    find_one_and_update path rather than the unguarded adjust_balance."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        outer = self

        class _Coll:
            def find_one_and_update(self, flt, upd, **kw):
                need = flt.get("balance_points", {}).get("$gte")
                if need is not None and outer.acct["balance_points"] < need:
                    return None          # guard refuses -> underflow
                before = dict(outer.acct)
                for k2, v2 in upd.get("$inc", {}).items():
                    outer.acct[k2] = outer.acct.get(k2, 0) + v2
                for k2, v2 in upd.get("$set", {}).items():
                    outer.acct[k2] = v2
                outer.adjustments.append({"guarded": True})
                return before

        self.collection = _Coll()


def test_clawback_uses_the_guarded_filter(monkeypatch, wired):
    acc = _GuardedAccounts(balance=100, le=100)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: acc)
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))

    res = L.reverse_for_cancel("ORD-1", "C1")

    assert res["ok"] and res["earned_clawed"] == 100
    assert acc.acct["balance_points"] == 0
    assert acc.adjustments == [{"guarded": True}]


def test_concurrent_redeem_cannot_drive_the_balance_negative(monkeypatch, wired):
    """A redeem at another store lands between our read and our write. The
    unguarded $inc would take balance_points NEGATIVE; the filter refuses."""
    acc = _GuardedAccounts(balance=100, le=100)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: acc)
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))

    original_find = acc.find_or_create

    def _find(cid):
        snap = original_find(cid)
        acc.acct["balance_points"] = 30   # the concurrent redeem lands NOW
        return snap

    acc.find_or_create = _find

    res = L.reverse_for_cancel("ORD-1", "C1")

    assert res["ok"] is False and res["reason"] == "balance_underflow"
    assert acc.acct["balance_points"] == 30      # never negative
    assert acc.adjustments == []


# =========================================================================== #
# SELF-FOUND (not on the panel list): clearing the incomplete flag is itself
# fail-soft, so a marker can be left flagged even though the money DID land.
# The repair must then be a NO-OP, not a second application.
# =========================================================================== #



# =========================================================================== #
# MUTATION-GAP CLOSURE. Three round-4 mutations were MISSED by the first pass:
# the tests passed with the fix reverted, i.e. they were not actually pinning
# the behaviour. These isolate each fix so reverting it FAILS.
# =========================================================================== #


def test_retry_door_opens_on_stranded_stock_with_a_fully_clean_order_doc(wired):
    """Isolates the GROUND-TRUTH clause of the retry door.

    The earlier test also lost the stamp, so the door could open via the
    "no cancel_stock_released key" clause and the ground-truth clause was never
    the deciding factor. Here the first cancel is completely clean -- stamp
    written, both failure flags False -- and a straggler write then re-marks a
    unit SOLD against the cancelled order. ONLY count_sold_units_for_order can
    open the door on that.
    """
    wired["units"].append(_unit("U1"))
    first = _cancel()
    assert first["stock_release_failed"] is False
    doc = wired["orders"].orders["ORD-1"]
    assert doc["cancel_stock_release_failed"] is False
    assert doc["loyalty_reversal_failed"] is False
    assert "cancel_stock_released" in doc          # the stamp DID land

    # A straggler write lands after the cancel: the unit is SOLD again.
    wired["units"][0]["status"] = "SOLD"
    wired["units"][0]["order_id"] = "ORD-1"
    assert wired["stock"].count_sold_units_for_order("ORD-1") == 1

    retry = _cancel()

    assert retry["message"] == "Cancel undo re-run"
    assert retry["stock_units_released"] == 1
    assert wired["units"][0]["status"] == "AVAILABLE"


# =========================================================================== #
# THE CHAIR'S REPRODUCTION (P0). MF4's own repair machinery double-applied
# money: RET-1 reverses correctly, _clear_reversal_incomplete blips, and RET-2
# -- a legitimate second partial return -- hits the reversal_incomplete branch
# BEFORE it can answer already_reversed and RE-APPLIES the same deltas.
# Observed on round 3: 240 points clawed on a 120-point order and
# lifetime_earned driven NEGATIVE.
#
# Failure INJECTION, not reversion: the chair's point is that mutation testing
# reverts fixes but does not inject transient faults, and this class of bug only
# appears under one.
# =========================================================================== #


def test_two_partial_returns_apply_exactly_one_reversal_despite_a_clear_blip(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 120))
    wired["accounts"].acct.update({"balance_points": 500, "lifetime_earned": 120})

    # The flag-clear write fails EXACTLY ONCE, then recovers.
    real_update_one = wired["txns"].collection.update_one
    calls = {"n": 0}

    def _flaky_update_one(flt, upd):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient blip clearing the flag")
        return real_update_one(flt, upd)

    wired["txns"].collection.update_one = _flaky_update_one
    try:
        first = L.reverse_for_return("RET-1", "ORD-1", "C1")
        second = L.reverse_for_return("RET-2", "ORD-1", "C1")
    finally:
        wired["txns"].collection.update_one = real_update_one

    assert first["ok"] is True and first["earned_clawed"] == 120
    # The stale flag must NOT authorise a re-application.
    assert second["ok"] is True
    assert second.get("repaired") is not True

    # THE ASSERTION THAT MATTERS: exactly ONE balance movement, ever.
    assert len(wired["accounts"].adjustments) == 1
    assert wired["accounts"].acct["balance_points"] == 380   # 500 - 120, once
    assert wired["accounts"].acct["lifetime_earned"] == 0    # never negative





# =========================================================================== #
# P0-ENABLER: a LANDED write whose read-back failed must read as UNKNOWN, not
# as "did not land" -- otherwise it flags the marker and authorises a repair
# that re-applies money which already moved.
# =========================================================================== #


# =========================================================================== #
# EXACTLY-ONCE BY CONSTRUCTION (the root fix).
#
# The balance move is keyed on the marker's txn_id via `applied_reversals`:
#   {"applied_reversals": {"$ne": txn_id}}  +  {"$addToSet": {...: txn_id}}
# in ONE atomic write. That single filter retires four rounds of
# verify-after-the-fact machinery -- the tri-state landed check, the
# exact-equality already-landed comparison, the incomplete-marker flag and the
# verification_unknown trap -- because a matched filter IS the proof, and an
# unmatched one is equally definitive.
#
# These tests are written as the chair reproduced the defects: INJECTED
# interleavings and injected transients, not reverted lines.
# =========================================================================== #


def _marker_of(wired):
    return [r for r in wired["txns"].rows if r.get("type") == "ADJUST"][-1]


def test_two_concurrent_flows_on_one_marker_apply_the_money_once(wired):
    """MF1. Request A holds a marker mid-flight; request B comes through the
    retry door (open by construction, because cancel_stock_released is stamped
    only AFTER the loyalty reversal) and completes the same marker. Both used to
    apply the same $inc: EXPECTED 4900, ACTUAL 9800.
    """
    wired["txns"].rows.extend(
        [_earn("C1", "ORD-1", 100), _redeem("C1", "ORD-1", 5000)]
    )
    wired["accounts"].acct.update(
        {"balance_points": 0, "lifetime_earned": 100, "lifetime_redeemed": 5000}
    )

    # A: writes the marker, then its balance write is interleaved by B.
    a_result = {}
    real_updater = wired["accounts"].collection.find_one_and_update
    state = {"reentered": False}

    def _interleaving_updater(flt, upd, **kw):
        if not state["reentered"]:
            state["reentered"] = True
            # B runs to completion INSIDE A's write window, on A's marker.
            a_result["b"] = L.reverse_for_cancel("ORD-1", "C1")
        return real_updater(flt, upd, **kw)

    wired["accounts"].collection.find_one_and_update = _interleaving_updater
    try:
        a_result["a"] = L.reverse_for_cancel("ORD-1", "C1")
    finally:
        wired["accounts"].collection.find_one_and_update = real_updater

    # THE ASSERTION: the money moved exactly once, whichever call won.
    assert wired["accounts"].acct["balance_points"] == 4900
    assert wired["accounts"].acct["lifetime_earned"] == 0
    assert wired["accounts"].acct["lifetime_redeemed"] == 0
    assert len(wired["accounts"].acct["applied_reversals"]) == 1
    # ...and neither counter went negative.
    assert wired["accounts"].acct["lifetime_earned"] >= 0
    assert wired["accounts"].acct["lifetime_redeemed"] >= 0


def test_burn_direction_also_applies_once(wired):
    """The mirror: an earn-only order clawed twice DESTROYS points the customer
    holds. EXPECTED 4000, the old code produced 2000."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 2000))
    wired["accounts"].acct.update({"balance_points": 6000, "lifetime_earned": 2000})

    first = L.reverse_for_cancel("ORD-1", "C1")
    marker = _marker_of(wired)
    # A second flow completes the SAME marker directly.
    second = L._ensure_reversal_applied(wired["accounts"], marker)

    assert first["ok"] and second["ok"]
    assert wired["accounts"].acct["balance_points"] == 4000
    assert len(wired["accounts"].acct["applied_reversals"]) == 1


def test_account_drift_between_attempts_cannot_cause_a_re_apply(wired):
    """MF2. No concurrency needed. Attempt 1 moves the money; the customer then
    does ordinary business, which defeated the old exact-equality check; the
    retry re-applied and MINTED 4900."""
    wired["txns"].rows.extend(
        [_earn("C1", "ORD-1", 100), _redeem("C1", "ORD-1", 5000)]
    )
    wired["accounts"].acct.update(
        {"balance_points": 0, "lifetime_earned": 100, "lifetime_redeemed": 5000}
    )

    # INJECT the transient the defect needs: the marker-clear write blips, so
    # the old code left the marker flagged with the money already moved. (On the
    # exactly-once design nothing reads that flag, so this is inert here -- it is
    # here so the test genuinely REPRODUCES against the previous commit.)
    real_update_one = wired["txns"].collection.update_one
    wired["txns"].collection.update_one = (
        lambda flt, upd: (_ for _ in ()).throw(RuntimeError("clear-flag blip"))
    )
    try:
        first = L.reverse_for_cancel("ORD-1", "C1")
    finally:
        wired["txns"].collection.update_one = real_update_one
    assert first["ok"] and wired["accounts"].acct["balance_points"] == 4900

    # Ordinary activity elsewhere -- the exact-equality check's blind spot.
    wired["accounts"].acct["balance_points"] += 40

    second = L.reverse_for_cancel("ORD-1", "C1")

    assert second["ok"] and second.get("already_reversed") is True
    assert wired["accounts"].acct["balance_points"] == 4940      # NOT 9840
    assert wired["accounts"].acct["lifetime_earned"] == 0
    assert wired["accounts"].acct["lifetime_redeemed"] == 0
    assert len(wired["accounts"].adjustments) == 1


def test_a_marker_whose_money_never_moved_is_completed_by_the_next_call(wired):
    """The state the repair door existed for -- now handled with no flag, no
    guess, and no way to double-apply: the next call simply re-issues the same
    guarded write."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})

    # The balance write fails; the marker still lands.
    real = wired["accounts"].collection.find_one_and_update
    wired["accounts"].collection.find_one_and_update = (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("write blip"))
    )
    first = L.reverse_for_cancel("ORD-1", "C1")
    wired["accounts"].collection.find_one_and_update = real

    assert first["ok"] is False and first["reason"] == "balance_update_failed"
    assert wired["accounts"].acct["balance_points"] == 100        # untouched

    second = L.reverse_for_cancel("ORD-1", "C1")

    assert second["ok"] is True and second.get("completed_now") is True
    assert wired["accounts"].acct["balance_points"] == 0          # finished
    assert len(wired["accounts"].acct["applied_reversals"]) == 1

    third = L.reverse_for_cancel("ORD-1", "C1")
    assert third["ok"] and third.get("completed_now") is not True
    assert wired["accounts"].acct["balance_points"] == 0          # still once


def test_an_unresolved_reversal_keeps_reporting_unresolved(wired):
    """MF3. The old verification_unknown trap answered ok=True on the retry,
    erasing the only reconciliation signal and closing the orders.py door too.
    An unapplied reversal must say so EVERY time it is asked."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 5000))
    wired["accounts"].acct.update({"balance_points": 5000, "lifetime_earned": 5000})

    real = wired["accounts"].collection.find_one_and_update
    wired["accounts"].collection.find_one_and_update = (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("write blip"))
    )
    try:
        first = L.reverse_for_cancel("ORD-1", "C1")
        second = L.reverse_for_cancel("ORD-1", "C1")
        third = L.reverse_for_cancel("ORD-1", "C1")
    finally:
        wired["accounts"].collection.find_one_and_update = real

    for res in (first, second, third):
        assert res["ok"] is False, res
    assert second.get("unapplied_reversal") is True
    assert third.get("unapplied_reversal") is True


def test_cancel_keeps_flagging_an_unresolved_reversal_on_the_order(wired):
    """...and the order doc keeps carrying the flag, so the retry door stays
    open instead of being closed by a cheerful ok=True."""
    wired["units"].append(_unit("U1"))
    wired["txns"].rows.append(_earn("C1", "ORD-1", 5000))
    wired["accounts"].acct.update({"balance_points": 5000, "lifetime_earned": 5000})
    real = wired["accounts"].collection.find_one_and_update
    wired["accounts"].collection.find_one_and_update = (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("write blip"))
    )
    try:
        first = _cancel()
        assert first["loyalty_reversal_failed"] is True
        retry = _cancel()
    finally:
        wired["accounts"].collection.find_one_and_update = real

    assert retry["loyalty_reversal_failed"] is True
    assert wired["orders"].orders["ORD-1"]["loyalty_reversal_failed"] is True


def test_underflow_is_refused_and_then_completes_when_the_balance_recovers(wired):
    """The guard rides in the same filter, so a concurrent redeem cannot drive
    the balance negative -- and the reversal is not lost, it completes later."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})
    real_find = wired["accounts"].find_or_create

    def _redeem_lands_between(cid):
        snap = real_find(cid)
        wired["accounts"].acct["balance_points"] = 30    # concurrent redeem
        return snap

    wired["accounts"].find_or_create = _redeem_lands_between
    try:
        first = L.reverse_for_cancel("ORD-1", "C1")
    finally:
        wired["accounts"].find_or_create = real_find

    assert first["ok"] is False and first["reason"] == "balance_underflow"
    assert wired["accounts"].acct["balance_points"] == 30      # never negative

    wired["accounts"].acct["balance_points"] = 150             # customer earns
    second = L.reverse_for_cancel("ORD-1", "C1")

    assert second["ok"] is True and second.get("completed_now") is True
    assert wired["accounts"].acct["balance_points"] == 50
    assert len(wired["accounts"].acct["applied_reversals"]) == 1


def test_completion_applies_the_tier_rule_carried_on_the_marker(wired):
    """A completed CANCEL must still drop the tier; a completed RETURN must not.
    The rule is read off the marker, not re-derived."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 6000))
    wired["accounts"].acct.update(
        {"balance_points": 6000, "lifetime_earned": 6000, "tier": "GOLD"}
    )
    real = wired["accounts"].collection.find_one_and_update
    wired["accounts"].collection.find_one_and_update = (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blip"))
    )
    assert L.reverse_for_cancel("ORD-1", "C1")["ok"] is False
    wired["accounts"].collection.find_one_and_update = real
    assert _marker_of(wired)["recompute_tier"] is True

    done = L.reverse_for_cancel("ORD-1", "C1")

    assert done["ok"] and done.get("completed_now") is True
    assert wired["accounts"].acct["lifetime_earned"] == 0
    assert wired["accounts"].tiers_set == ["BRONZE"]


def test_completion_of_a_return_marker_never_moves_the_tier(wired):
    wired["txns"].rows.append(_earn("C1", "ORD-1", 6000))
    wired["accounts"].acct.update(
        {"balance_points": 6000, "lifetime_earned": 6000, "tier": "GOLD"}
    )
    real = wired["accounts"].collection.find_one_and_update
    wired["accounts"].collection.find_one_and_update = (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blip"))
    )
    assert L.reverse_for_return("RET-1", "ORD-1", "C1")["ok"] is False
    wired["accounts"].collection.find_one_and_update = real
    assert _marker_of(wired)["recompute_tier"] is False

    done = L.reverse_for_return("RET-1", "ORD-1", "C1")

    assert done["ok"] and done.get("completed_now") is True
    assert wired["accounts"].tiers_set == []
    assert wired["accounts"].acct["tier"] == "GOLD"


# =========================================================================== #
# ROUND 6 / MUST-FIX 2 -- LEGACY (PRE-GUARD) REVERSAL MARKERS.
#
# applied_reversals is NEW IN THIS PR. At merge-base b4410af, reverse_for_return
# wrote {txn_id, customer_id, type, points, order_id, return_id, reason,
# created_at} and moved money via adjust_balance, which never touched the
# account array. reverse_for_cancel did not exist at all.
#
# LIVE CLUSTER: loyalty_accounts TOTAL 8, WITH applied_reversals 0 -- and
# {applied_reversals: {$ne: <random>}} matched ALL 8. $ne on a MISSING field
# MATCHES, so the exactly-once guard was blind to 100% of existing accounts.
#
# Two failure directions, both reported as success:
#   money moves   -> the reversal is applied a SECOND time (a REGRESSION: main
#                    returns already_reversed with no money move)
#   $gte refuses  -> unapplied_reversal=True FOREVER on a reversal main already
#                    completed, wedging the cancel retry door permanently open
#
# The fix detects PRE-GUARD markers BY SHAPE. A backfill alone cannot work: old
# and new workers coexist during a rolling deploy, so a legacy marker can be
# written after the migration runs.
# =========================================================================== #


def _legacy_marker(cid, order_id, points, return_id="RET-OLD"):
    """EXACTLY the marker shape origin/main writes -- no reversal_of_order_id,
    no reversed_earn_points, no recompute_tier."""
    return {
        "txn_id": "legacy-txn-1",
        "customer_id": cid,
        "type": "ADJUST",
        "points": points,
        "order_id": order_id,
        "return_id": return_id,
        "reason": f"Return {return_id}: claw on order {order_id}",
        "created_at": datetime(2026, 7, 1),
    }


def test_production_accounts_have_no_applied_reversals_field(wired):
    """The premise. If this ever starts failing because the fake seeds the
    field again, every test below silently stops testing production's shape."""
    assert "applied_reversals" not in wired["accounts"].acct


def test_ne_matches_a_missing_field_so_the_guard_alone_is_blind(wired):
    """Pins the Mongo semantics the whole defect rests on."""
    coll = wired["accounts"].collection
    doc = coll.find_one_and_update(
        {"customer_id": "C1", "applied_reversals": {"$ne": "some-random-txn"}},
        {"$set": {"probe": 1}},
    )
    assert doc is not None, "$ne on a MISSING field must MATCH, as Mongo does"


def test_legacy_return_marker_is_not_re_applied(wired):
    """The money direction: retry of the same return moved 2000 -> 1500."""
    wired["txns"].rows.extend([
        _earn("C1", "ORD-1", 500),
        _legacy_marker("C1", "ORD-1", -500),
    ])
    wired["accounts"].acct.update({"balance_points": 2000, "lifetime_earned": 500})

    res = L.reverse_for_return("RET-OLD", "ORD-1", "C1")

    assert res["ok"] is True
    assert res.get("already_reversed") is True
    assert res.get("pre_guard_marker") is True
    assert res.get("completed_now") is not True
    assert wired["accounts"].acct["balance_points"] == 2000      # NOT 1500
    assert wired["accounts"].adjustments == []


def test_legacy_marker_blocks_a_cancel_from_re_reversing(wired):
    """Second partial return / cancel against a legacy marker: also 2000->1500."""
    wired["txns"].rows.extend([
        _earn("C1", "ORD-1", 500),
        _legacy_marker("C1", "ORD-1", -500),
    ])
    wired["accounts"].acct.update({"balance_points": 2000, "lifetime_earned": 500})

    res = L.reverse_for_cancel("ORD-1", "C1")

    assert res["ok"] is True and res.get("pre_guard_marker") is True
    assert wired["accounts"].acct["balance_points"] == 2000
    assert wired["accounts"].adjustments == []


def test_legacy_redeem_direction_mints_nothing(wired):
    """The redeem direction MINTED points at Re 1/point: 800 -> 1600."""
    wired["txns"].rows.extend([
        _redeem("C1", "ORD-1", 800),
        _legacy_marker("C1", "ORD-1", 800),
    ])
    wired["accounts"].acct.update({"balance_points": 800, "lifetime_redeemed": 800})

    res = L.reverse_for_return("RET-OLD", "ORD-1", "C1")

    assert res["ok"] is True and res.get("pre_guard_marker") is True
    assert wired["accounts"].acct["balance_points"] == 800       # NOT 1600
    assert wired["accounts"].adjustments == []


def test_legacy_marker_never_reports_unapplied_forever(wired):
    """The direction that is LIVE TODAY: the $gte guard refuses (prod's one
    legacy row is points=-16 on a balance of 0), so every call returned
    unapplied_reversal=True on a reversal main already completed -- wedging the
    cancel retry door permanently open."""
    wired["txns"].rows.extend([
        _earn("C1", "ORD-1", 16),
        _legacy_marker("C1", "ORD-1", -16),
    ])
    wired["accounts"].acct.update({"balance_points": 0, "lifetime_earned": 16})

    for _ in range(3):
        res = L.reverse_for_return("RET-OLD", "ORD-1", "C1")
        assert res["ok"] is True, res
        assert res.get("unapplied_reversal") is not True
        assert res.get("pre_guard_marker") is True
    assert wired["accounts"].acct["balance_points"] == 0


def test_cancel_does_not_stamp_failure_on_a_legacy_completed_reversal(wired):
    """...and the order doc therefore stays clean instead of carrying
    loyalty_reversal_failed on a finished cancel."""
    wired["units"].append(_unit("U1"))
    wired["txns"].rows.extend([
        _earn("C1", "ORD-1", 16),
        _legacy_marker("C1", "ORD-1", -16),
    ])
    wired["accounts"].acct.update({"balance_points": 0, "lifetime_earned": 16})

    res = _cancel()

    assert res["loyalty_reversal_failed"] is False
    assert wired["orders"].orders["ORD-1"]["loyalty_reversal_failed"] is False


def test_an_empty_array_is_treated_exactly_like_a_missing_one(wired):
    """The chair's self-correction: an account already carrying
    applied_reversals: [] is NOT safe. The guard keys on MEMBERSHIP of this
    marker's txn_id, so an empty array matches $ne exactly like an absent one --
    ADDING THE FIELD IS NOT A FIX."""
    acc = _FakeAccounts(balance=2000, le=500, applied=[])
    monkey = wired["monkeypatch"]
    monkey.setattr(L, "get_loyalty_account_repository", lambda: acc)
    wired["txns"].rows.extend([
        _earn("C1", "ORD-1", 500),
        _legacy_marker("C1", "ORD-1", -500),
    ])

    res = L.reverse_for_return("RET-OLD", "ORD-1", "C1")

    assert res.get("pre_guard_marker") is True
    assert acc.acct["balance_points"] == 2000
    assert acc.adjustments == []


def test_a_marker_written_by_THIS_pr_is_still_completed_normally(wired):
    """The shape guard must not over-block: a marker carrying this PR's fields
    whose money never moved must still be finished."""
    wired["txns"].rows.append(_earn("C1", "ORD-1", 100))
    wired["accounts"].acct.update({"balance_points": 100, "lifetime_earned": 100})

    real = wired["accounts"].collection.find_one_and_update
    wired["accounts"].collection.find_one_and_update = (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("write blip"))
    )
    first = L.reverse_for_cancel("ORD-1", "C1")
    wired["accounts"].collection.find_one_and_update = real
    assert first["ok"] is False

    second = L.reverse_for_cancel("ORD-1", "C1")

    assert second["ok"] is True
    assert second.get("completed_now") is True
    assert second.get("pre_guard_marker") is not True
    assert wired["accounts"].acct["balance_points"] == 0


def test_a_refused_guarded_write_invents_no_field(wired):
    """Mongo writes NOTHING when find_one_and_update matches nothing.

    A fake that setdefaults the array on the READ path invents
    applied_reversals as a side effect of a REFUSED write -- which is exactly
    how the absent-field shape (100% of production loyalty_accounts) became
    unreachable across 60 loyalty tests. This pins the write fidelity, not just
    the initial seed.
    """
    acct = wired["accounts"].acct
    acct["balance_points"] = 0
    assert "applied_reversals" not in acct

    doc = wired["accounts"].collection.find_one_and_update(
        {"customer_id": "C1",
         "applied_reversals": {"$ne": "t1"},
         "balance_points": {"$gte": 500}},          # refuses: balance is 0
        {"$inc": {"balance_points": -500},
         "$addToSet": {"applied_reversals": "t1"}},
    )

    assert doc is None                              # the write was refused
    assert "applied_reversals" not in acct          # ...so nothing was written
    assert acct["balance_points"] == 0
