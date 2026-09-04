"""
IMS 2.0 - /orders/overdue/list: a STRING column needs a STRING bound
=====================================================================
``orders.expected_delivery`` is an ISO STRING on every write path:

  * POST /orders      -> ``expected_delivery.isoformat()`` on a datetime:
                         '2026-09-01T00:00:00' (explicit delivery_date) or
                         '2026-09-08T14:23:11.123456' (the +N-days default);
  * PUT /orders/{id}  -> ``date.isoformat()``, a bare '2026-09-01';
  * seed_data_OLD.py and every test fixture -> full ISO datetime strings;
  * the Shopify / ONDC / TechCherry importers never write the field.

No writer stores a BSON date, whatever schemas.py declares (its validator is
not applied at runtime). ``OrderRepository.find_overdue`` bound a naive IST
DATETIME against the column; BSON never compares a date with a string, so the
overdue screen returned [] in production. The fix binds the IST-today string
through ``api.utils.dates.iso_date_window`` -- the ONE rule the clinical date
window and the workshop overdue list share.

Discriminating power, MEASURED (fix reverted to the datetime bound, this file
re-run, fix restored): every "is overdue" assertion and the route test fail
because the reverted filter selects no row at all; the source guard fails by
name. The double is StrictCollection, whose ``$lt`` answers False on a Python
TypeError -- a datetime against a string never matches, which is exactly
Mongo's type bracketing. A fake that coerced or compared Python-style would
return rows for the reverted bound and prove nothing; the pinned-defect test
below is what keeps the double honest.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import inspect
import os
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.utils.dates import iso_date_window  # noqa: E402
from api.utils.ist import now_ist_naive  # noqa: E402
from database.repositories import order_repository as order_repo_module  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402
from database.repositories.prescription_repository import (  # noqa: E402
    PrescriptionRepository,
)
from database.repositories.workshop_repository import (  # noqa: E402
    WorkshopJobRepository,
)
from tests.strict_fakes import StrictCollection  # noqa: E402

# A frozen IST "today", far from the real calendar, so a reader that quietly
# fell back to the box clock could not pass by coincidence.
TODAY = date(2026, 6, 10)
STORE = "BV-TEST-01"
OPEN_STATUSES = ["CONFIRMED", "PROCESSING", "READY"]
_OMIT = object()


def _row(order_id, expected_delivery=_OMIT, status="READY", store_id=STORE):
    doc = {"order_id": order_id, "store_id": store_id, "status": status}
    if expected_delivery is not _OMIT:
        doc["expected_delivery"] = expected_delivery
    return doc


ROWS = [
    # -- promised before today, in EVERY shape a writer produces -> overdue
    _row("bare-yday", "2026-06-09"),                              # PUT shape
    _row("stamp-yday-midnight", "2026-06-09T00:00:00"),           # POST, explicit date
    _row("stamp-yday-evening", "2026-06-09T18:12:44.123456"),     # POST, +N-days default
    _row("bare-last-week", "2026-06-03", status="PROCESSING"),
    _row("stamp-last-week", "2026-06-03T00:00:00", status="CONFIRMED"),
    # -- due TODAY: not overdue until the IST day has passed, in either shape
    _row("bare-today", "2026-06-10"),
    _row("stamp-today-midnight", "2026-06-10T00:00:00"),
    _row("stamp-today-evening", "2026-06-10T19:30:00"),
    # -- future
    _row("bare-tomorrow", "2026-06-11"),
    _row("stamp-tomorrow", "2026-06-11T00:00:00"),
    # -- status filter must stay intact: closed / not-yet-open never surface
    _row("delivered-yday", "2026-06-09", status="DELIVERED"),
    _row("cancelled-yday", "2026-06-09", status="CANCELLED"),
    _row("draft-yday", "2026-06-09", status="DRAFT"),
    # -- another store
    _row("other-store-yday", "2026-06-09", store_id="WO-TEST-01"),
    # -- no promise recorded (online orders never write the field)
    _row("no-promise"),
]

EXPECTED_OVERDUE = {
    "bare-yday",
    "stamp-yday-midnight",
    "stamp-yday-evening",
    "bare-last-week",
    "stamp-last-week",
}


def _ids(rows):
    return {r["order_id"] for r in rows}


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setattr(order_repo_module, "ist_today", lambda: TODAY)
    return OrderRepository(StrictCollection("orders", docs=ROWS))


# ---------------------------------------------------------------------------
# The bound, against the strict double
# ---------------------------------------------------------------------------


def test_every_stored_shape_of_a_past_promise_is_overdue(repo):
    """The whole point: bare, midnight-stamped and time-stamped strings from
    yesterday and last week ALL come back. Reverting to the datetime bound
    empties this set."""
    assert _ids(repo.find_overdue(STORE)) == EXPECTED_OVERDUE


def test_a_job_due_today_is_not_overdue_before_the_ist_day_ends(repo):
    """Day granularity, both shapes. A stamped bound ('...T00:00:01') would
    have flagged the midnight-stamped row one minute into the day."""
    got = _ids(repo.find_overdue(STORE))
    for today_row in ("bare-today", "stamp-today-midnight", "stamp-today-evening"):
        assert today_row not in got, today_row


def test_future_and_missing_promises_are_not_overdue(repo):
    got = _ids(repo.find_overdue(STORE))
    for row in ("bare-tomorrow", "stamp-tomorrow", "no-promise"):
        assert row not in got, row


def test_status_and_store_filters_are_intact(repo):
    """Fixing the date bound must not loosen the other two predicates."""
    scoped = _ids(repo.find_overdue(STORE))
    for row in ("delivered-yday", "cancelled-yday", "draft-yday", "other-store-yday"):
        assert row not in scoped, row
    # No store -> every store's late jobs (the ADMIN / SUPERADMIN view).
    assert _ids(repo.find_overdue()) == EXPECTED_OVERDUE | {"other-store-yday"}


def test_overdue_rolls_over_at_ist_midnight_not_the_utc_box_clock(monkeypatch):
    """02:00 IST on the 10th is 20:30 UTC on the 9th. A job promised for the
    9th is already late in India while the box clock still says 'today'.
    Freezes the IST clock itself (not ist_today) so the repository's own
    day derivation is what is under test."""
    from api.utils import ist as ist_module

    frozen = datetime(2026, 6, 10, 2, 0, tzinfo=ist_module.IST)
    monkeypatch.setattr(ist_module, "now_ist", lambda: frozen)
    repo = OrderRepository(StrictCollection("orders", docs=ROWS))
    got = _ids(repo.find_overdue(STORE))
    assert "bare-yday" in got and "stamp-yday-evening" in got
    assert "bare-today" not in got and "stamp-today-midnight" not in got


def test_the_datetime_bound_it_used_to_carry_matches_no_stored_row():
    """The defect, pinned -- the literal filter find_overdue carried. Every
    open row above is days past due and it selects none of them.

    Also the proof that the double is honest: a fake that coerced the
    datetime to a string, or compared it Python-style, would return rows
    here and FAIL this test."""
    coll = StrictCollection("orders", docs=ROWS)
    reverted = {
        "status": {"$in": OPEN_STATUSES},
        "expected_delivery": {"$lt": now_ist_naive()},
    }
    assert list(coll.find(reverted)) == []


def test_a_real_bson_date_row_is_deliberately_not_matched(repo):
    """No writer of expected_delivery stores a BSON date (POST / PUT / seeds
    all write strings; the schema's bsonType is not enforced), so the bound
    is string-only BY DECISION. If a writer ever starts storing datetimes,
    this is the tripwire: the bound must then become an $or over both shapes.
    Until then a datetime row is invisible here -- exactly as it is to Mongo.
    """
    repo.collection.docs.append(
        _row("bson-date-yday", datetime(2026, 6, 9, 10, 30))
    )
    assert "bson-date-yday" not in _ids(repo.find_overdue(STORE))


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def test_iso_date_window_is_day_granular_even_when_handed_a_datetime():
    """A datetime slipping in as an edge must not smuggle a time component
    back into the bound -- that is the edge defect the rule exists to prevent
    (both edges are pinned by test_prescription_date_window through
    _clinical_date_filter, which now delegates here)."""
    stamped = datetime(2026, 6, 18, 12, 5, 26)
    assert iso_date_window(stamped, stamped) == {
        "$gte": "2026-06-18",
        "$lt": "2026-06-19",
    }
    assert iso_date_window() == {}
    # "Strictly before TODAY" is the window whose last admitted day is yesterday.
    assert iso_date_window(to_day=TODAY - timedelta(days=1)) == {"$lt": "2026-06-10"}


# ---------------------------------------------------------------------------
# Source guard: one rule, one helper, no clock-typed bound against a string
# ---------------------------------------------------------------------------

_DATETIME_BOUND_TOKENS = (
    "now_ist_naive(",
    "datetime.now(",
    "now_ist()",
    "date.today(",
    "utcnow(",
    "datetime.combine",
    '"$lt"',
    '"$gte"',
)


@pytest.mark.parametrize(
    "fn",
    [
        OrderRepository.find_overdue,
        WorkshopJobRepository.find_overdue,
        PrescriptionRepository._clinical_date_filter,
    ],
    ids=["orders.expected_delivery", "workshop.expected_date", "prescription_date"],
)
def test_every_string_date_bound_goes_through_the_one_helper(fn):
    """This repo has been bitten by datetime-vs-string bracketing five times.
    Each string-dated reader must build its bound through iso_date_window and
    must not hand-roll a clock or a range operator against the column."""
    src = inspect.getsource(fn)
    assert "iso_date_window(" in src, fn.__qualname__ + " does not use the shared builder"
    for token in _DATETIME_BOUND_TOKENS:
        assert token not in src, "%s builds its own bound: %r" % (fn.__qualname__, token)


# ---------------------------------------------------------------------------
# The screen: GET /orders/overdue/list
# ---------------------------------------------------------------------------


def _headers(roles, user_id="u-test"):
    from api.routers.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "user_id": user_id,
                "username": user_id,
                "roles": roles,
                "store_ids": [STORE],
                "active_store_id": STORE,
            }
        )
    }


def test_the_overdue_screen_lists_the_late_jobs(client, monkeypatch):
    """End to end through the router: the list staff read to chase late jobs
    carries every past-due open order and nothing due today or later."""
    from api.routers import orders as orders_module

    monkeypatch.setattr(order_repo_module, "ist_today", lambda: TODAY)
    repo = OrderRepository(StrictCollection("orders", docs=ROWS))
    monkeypatch.setattr(orders_module, "get_order_repository", lambda: repo)

    r = client.get(
        "/api/v1/orders/overdue/list",
        headers=_headers(["SALES_CASHIER"]),
        params={"store_id": STORE},
    )
    assert r.status_code == 200, r.text
    assert {o["id"] for o in r.json()["orders"]} == EXPECTED_OVERDUE
