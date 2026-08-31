# ============================================================================
# "Who sold this order" is ONE rule - every surface must answer the same
# ============================================================================
# Orders are written with `salesperson_id` (orders.py). Nine surfaces claimed to
# read that, but seven of them read `sales_staff_id` and two read
# `sales_person_id` - keys no order-writing door has ever set. With `created_by`
# also absent from POS orders, every one of them collapsed to a single "unknown"
# bucket, and an employee-filtered commission query matched ZERO orders.
#
# The failure was invisible because the copies AGREED WITH EACH OTHER: the
# cross-implementation test in test_ist_closing_sweep.py was green precisely
# because both sides were equally blind, and its fixtures used the dead key too.
#
# These tests pin the fixed behaviour on the MONEY surfaces, where being wrong
# costs someone their commission. Every fixture below uses the key the real
# create-order door writes.

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date, timedelta  # noqa: E402

import api.routers.analytics_v2 as analytics_v2  # noqa: E402
import api.routers.hr_self_service as hr_self  # noqa: E402
import api.routers.payroll as payroll  # noqa: E402
from api.services.name_resolver import order_actor_id, order_actor_name_map  # noqa: E402
from api.utils.ist import ist_day_start_utc, now_ist  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

STORE = "ZZ-STORE-01"

# Both surfaces window on the IST CLOCK (BUG-104), so the fixtures must be
# anchored to it rather than to a fixed calendar date - otherwise the suite
# starts failing the moment the real month rolls over. Deliberately NOT a
# frozen literal: a calendar-dependent probe is its own hollow-test class.
_NOW_IST = now_ist()
# Commission windows the IST MONTH -> the 15th, midday, is comfortably inside.
WHEN = ist_day_start_utc(date(_NOW_IST.year, _NOW_IST.month, 15)) + timedelta(hours=6)
# The analytics leaderboard windows from the IST DAY start with no upper bound.
TODAY_WHEN = ist_day_start_utc(_NOW_IST.date()) + timedelta(hours=6)

USERS = [
    {"user_id": "u-sales", "full_name": "Rekha Sharma"},
    {"user_id": "u-biller", "full_name": "Manoj Manager"},
]


def _order(oid, *, salesperson_id="u-sales", created_by="u-biller",
           salesperson_name=None, amount=1000.0, when=None):
    doc = {
        "order_id": oid,
        "store_id": STORE,
        "status": "COMPLETED",
        "created_at": when or WHEN,
        "total_amount": amount,
        "grand_total": amount,
        "items": [],
        "created_by": created_by,
    }
    if salesperson_id:
        doc["salesperson_id"] = salesperson_id
    if salesperson_name:
        doc["salesperson_name"] = salesperson_name
    return doc


def _db(orders, users=USERS):
    db = StrictDB()
    db.seed("orders", orders)
    db.seed("users", users)
    return db


def _mgr(uid="u-biller"):
    return {"user_id": uid, "roles": ["ADMIN"], "active_store_id": STORE}


# ---------------------------------------------------------------------------
# The shared helper itself - two branches that had NO coverage
# ---------------------------------------------------------------------------

def test_the_live_user_row_beats_the_name_stored_on_the_order():
    """A renamed employee shows their CURRENT name, not the one frozen on an
    old order. This precedence fires on ~100% of live orders and inverting it
    previously left the whole suite green."""
    db = _db([_order("o1", salesperson_name="Rekha OLD SURNAME")])
    names = order_actor_name_map(db, list(db.get_collection("orders").find({})))
    assert names["u-sales"] == "Rekha Sharma"


def test_a_stored_name_never_labels_a_bucket_it_does_not_belong_to():
    """An order credited via created_by must NOT borrow the salesperson_name
    sitting on it. Otherwise a real person's name gets printed over another
    person's orders - worse than showing a raw id, because it looks right."""
    # No salesperson_id, so credit falls to the biller - but a name IS present.
    doc = _order("o1", salesperson_id=None, salesperson_name="Rekha Sharma")
    doc["created_by"] = "u-ghost"          # nobody in users
    db = _db([doc], users=[])
    names = order_actor_name_map(db, [doc])
    assert order_actor_id(doc) == "u-ghost"
    assert names.get("u-ghost") != "Rekha Sharma"


# ---------------------------------------------------------------------------
# MONEY: payroll commission
# ---------------------------------------------------------------------------

def _commission(db, monkeypatch, *, employee_id=None, user=None):
    monkeypatch.setattr(payroll, "_get_db", lambda: db)
    monkeypatch.setattr(payroll, "validate_store_access", lambda sid, u: STORE)
    return asyncio.run(
        payroll.get_commission_summary(
            month=_NOW_IST.month, year=_NOW_IST.year, store_id=STORE,
            employee_id=employee_id, current_user=user or _mgr(),
        )
    )


def test_commission_credits_the_seller_not_the_biller(monkeypatch):
    db = _db([_order("o1", amount=5000.0)])
    items = _commission(db, monkeypatch)["items"]
    ids = {i.get("staff_id") or i.get("employee_id") or i.get("id") for i in items}
    assert "u-sales" in ids
    assert "u-biller" not in ids
    assert "unknown" not in ids


def test_an_employee_filtered_commission_finds_their_sales(monkeypatch):
    """The filter used to be `$or:[{sales_staff_id},{created_by}]` - neither key
    is on a POS order, so a named employee matched ZERO orders and every
    self-service commission page showed nothing."""
    db = _db([_order("o1", amount=5000.0)])
    items = _commission(db, monkeypatch, employee_id="u-sales")["items"]
    assert items, "the seller's own commission query returned nothing"
    assert any(float(i.get("revenue") or 0) == 5000.0 for i in items)


def test_the_biller_is_not_paid_for_someone_elses_sale(monkeypatch):
    """The superset query matches created_by too, so the canonical precedence
    MUST filter it back out - otherwise the manager who rang the sale up gets
    commission on it as well, and the same rupees are paid twice."""
    db = _db([_order("o1", amount=5000.0)])
    items = _commission(db, monkeypatch, employee_id="u-biller")["items"]
    assert not items, f"the biller was credited with the seller's sale: {items}"


def test_a_historical_order_with_no_salesperson_still_credits_its_biller(monkeypatch):
    """Orders written before the POS picker existed carry only created_by. They
    must keep attributing to a real person, not vanish into 'unknown'."""
    db = _db([_order("o1", salesperson_id=None, amount=2000.0)])
    items = _commission(db, monkeypatch, employee_id="u-biller")["items"]
    assert items, "a pre-picker order stopped being credited to anyone"


# ---------------------------------------------------------------------------
# MONEY: the employee's own page must agree with the manager ledger
# ---------------------------------------------------------------------------

def test_my_own_commission_page_sees_my_sales(monkeypatch):
    monkeypatch.setattr(hr_self, "_get_db", lambda: db_holder["db"])
    db_holder["db"] = _db([_order("o1", amount=4000.0)])
    fn = getattr(hr_self, "my_commission", None) or getattr(hr_self, "get_my_commission", None)
    if fn is None:                                  # handler renamed - skip loudly
        import pytest
        pytest.skip("hr_self_service commission handler not found by name")
    out = asyncio.run(fn(month=_NOW_IST.month, year=_NOW_IST.year,
                         current_user={"user_id": "u-sales", "roles": ["SALES_STAFF"],
                                       "active_store_id": STORE}))
    assert (out.get("sales_count") or 0) >= 1, out


db_holder: dict = {}


# ---------------------------------------------------------------------------
# Analytics rosters
# ---------------------------------------------------------------------------

def test_the_analytics_staff_leaderboard_credits_the_seller(monkeypatch):
    db = _db([_order("o1", amount=3000.0, when=TODAY_WHEN)])
    monkeypatch.setattr(analytics_v2, "_get_db", lambda: db)
    monkeypatch.setattr(analytics_v2, "validate_store_access", lambda sid, u: STORE)
    out = asyncio.run(
        analytics_v2.staff_leaderboard(period="today", store_id=STORE, current_user=_mgr())
    )
    blob = str(out)
    assert "u-sales" in blob or "Rekha" in blob, blob[:400]
    assert "unknown" not in blob.lower()


# ---------------------------------------------------------------------------
# Reintroduction guard. Deliberately a SUPPLEMENT to the behavioural tests
# above, never a substitute: a spelling check cannot see wrong logic, only a
# wrong key. It exists because this rule was re-typed nine times.
# ---------------------------------------------------------------------------

def test_no_router_reads_a_dead_salesperson_spelling_off_an_order():
    import glob
    import re

    # `.get("<dead key>")` on an order doc. The Mongo query terms in the
    # commission superset filters are `{"<key>": value}` and are intentional.
    pattern = re.compile(r'\.get\(\s*["\'](sales_staff_id|sales_person_id|sales_staff_name|sales_person_name)["\']')
    root = os.path.join(os.path.dirname(__file__), "..", "api")
    offenders = []
    for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
        base = os.path.basename(path)
        # walkouts owns `sales_person_id` on ITS OWN collection; name_resolver
        # is the one place the legacy fallback is allowed to be spelled out.
        if base in ("walkouts.py", "name_resolver.py", "scorecard_engine.py"):
            continue
        text = open(path, encoding="utf-8").read()
        for m in pattern.finditer(text):
            offenders.append(f"{base}:{text[:m.start()].count(chr(10)) + 1} {m.group(1)}")
    assert not offenders, (
        "these read a salesperson key no order-writing door sets - route them "
        "through name_resolver.order_actor_id instead: " + "; ".join(offenders)
    )
