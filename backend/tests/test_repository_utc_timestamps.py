"""RUN-02: repository timestamps are UTC instants, whatever the host's timezone.

Stored ``created_at`` / ``updated_at`` / ``sold_at`` keep their existing BSON shape
(naive datetime in the UTC frame -- what Railway's UTC host always wrote and what
every reader in api/utils/ist.py assumes). Before the fix the repositories wrote
``datetime.now()``, i.e. the HOST'S local wall clock: on an IST machine a sale at
04:32 UTC was stored as 10:02 and displayed as 15:32 IST.

The clock below simulates an IST host deterministically on any CI machine: a bare
``now()`` returns the IST wall clock, ``now(tz)`` returns the true instant.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.utils import event_time as et
from database.connection import MockCollection
from database.repositories import base_repository as base_mod
from database.repositories import product_repository as product_mod
from database.repositories.base_repository import BaseRepository
from database.repositories.product_repository import StockRepository

INSTANT = datetime(2026, 11, 4, 4, 32, 58, tzinfo=timezone.utc)
UTC_NAIVE = INSTANT.replace(tzinfo=None)
IST = timezone(timedelta(hours=5, minutes=30))


class _RealInstances(type):
    # isinstance(x, <patched datetime>) must still accept real datetimes, or a
    # reader's type check silently drops every stored row (hiding the defect).
    def __instancecheck__(cls, obj):
        return isinstance(obj, datetime)


class IstHostClock(datetime, metaclass=_RealInstances):
    @classmethod
    def now(cls, tz=None):
        if tz is None:  # the host's local wall clock (Asia/Kolkata)
            return INSTANT.astimezone(IST).replace(tzinfo=None)
        return INSTANT.astimezone(tz)


@pytest.fixture
def ist_host(monkeypatch):
    for mod in (et, base_mod, product_mod):
        monkeypatch.setattr(mod, "datetime", IstHostClock)


class _Repo(BaseRepository):
    entity_name = "thing"
    id_field = "thing_id"


def test_create_update_delete_stamp_the_utc_instant_on_an_ist_host(ist_host):
    repo = _Repo(MockCollection("things"))
    doc = repo.create({"name": "a"})
    assert doc["created_at"] == UTC_NAIVE
    assert doc["updated_at"] == UTC_NAIVE
    # Same stored shape as legacy rows (naive), so mixed old/new comparisons and
    # every naive-UTC reader keep working.
    assert doc["created_at"].tzinfo is None

    stored = repo.collection._data[doc["thing_id"]]
    assert stored["created_at"] == UTC_NAIVE

    assert repo.update(doc["thing_id"], {"name": "b"})
    assert repo.collection._data[doc["thing_id"]]["updated_at"] == UTC_NAIVE
    assert repo.soft_delete(doc["thing_id"])
    assert repo.collection._data[doc["thing_id"]]["deleted_at"] == UTC_NAIVE
    assert repo.update_if(doc["thing_id"], {}, {"name": "c"})
    assert repo.collection._data[doc["thing_id"]]["updated_at"] == UTC_NAIVE


class _CaptureCollection:
    def __init__(self):
        self.updates = []

    def find_one_and_update(self, flt, update, **kw):
        self.updates.append(update)
        return {"stock_id": "S1"}


def test_unit_sold_at_is_the_utc_instant_on_an_ist_host(ist_host):
    coll = _CaptureCollection()
    repo = StockRepository(coll)
    assert repo.mark_sold("S1", "ORD-1")
    assert coll.updates[-1]["$set"]["sold_at"] == UTC_NAIVE


def test_utc_now_naive_is_host_independent(ist_host):
    assert et.utc_now_naive() == UTC_NAIVE
    assert et.utc_now_naive().tzinfo is None


# Comparisons against user-entered calendar fields whose frame is not an event
# instant; left on the host clock deliberately (see PR notes).
_ALLOWED = {
    ("vendor_repository.py", '"expected_date": {"$lt": datetime.now()}'),
    # PO expected_date day counting (po_variance_engine): a calendar rule.
    ("taskmaster.py", "now = datetime.now()"),
    # A reward id's date label, not a stored instant.
    ("loyalty.py", "reward_id = f\"RWD-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}\""),
    # A return id's date label, not a stored instant.
    ("returns.py", 'stamp = datetime.now().strftime("%y%m%d")'),
}


def _bare_now_calls(source):
    """Lines holding a real zero-argument ``datetime.now()`` call (the host's local
    wall clock). AST, so docstring prose and comments never count, and
    ``utcnow()`` / ``now(tz)`` (already the UTC instant) are not offenders."""
    lines = source.splitlines()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and not node.args and not node.keywords
                and isinstance(node.func, ast.Attribute) and node.func.attr == "now"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "datetime"):
            yield lines[node.lineno - 1].strip()


_BACKEND = Path(base_mod.__file__).parents[2]
# Every task event reader and writer: the escalation clocks compare these.
_TASK_FILES = [
    "api/routers/tasks.py",
    "agents/implementations/taskmaster.py",
    "api/services/task_sla.py",
    "api/services/task_triggers.py",
    "api/services/task_visibility.py",
    "api/services/catalog_flags.py",
    "api/routers/walkouts.py",
    "api/routers/vendors/grn_accept.py",
    # sold_at readers: the unsaved-claim sweep and the stock ledger's GRN join.
    "api/routers/orders/stock.py",
    "api/routers/inventory/stock.py",
    "api/routers/notifications.py",
    "api/routers/loyalty.py",
    "api/routers/products.py",
    "api/routers/reports/blueprint.py",
    "api/routers/workshop/spoilage.py",
    # #1397's return writes (stock flip-back, blocked-restock task reopen) and
    # task closes: stored event instants, UTC like every other (D100).
    "api/routers/returns.py",
]


def test_no_repository_writes_the_host_local_clock():
    root = Path(base_mod.__file__).parent
    paths = sorted(root.glob("*.py")) + [_BACKEND / f for f in _TASK_FILES]
    offenders = [
        f"{path.name}: {line}"
        for path in paths
        for line in _bare_now_calls(path.read_text(encoding="utf-8"))
        if (path.name, line) not in _ALLOWED
    ]
    assert offenders == []


def test_the_guard_sees_a_bare_now_but_not_prose_or_utc():
    src = (
        'def f():\n'
        '    """a plain datetime.now() in prose"""\n'
        '    # datetime.now() in a comment\n'
        '    a = datetime.utcnow().year\n'
        '    b = datetime.now(timezone.utc)\n'
        '    return {"at": datetime.now()}\n'
    )
    assert list(_bare_now_calls(src)) == ['return {"at": datetime.now()}']


# --------------------------------------------------------------------------
# A supplied, unambiguous creation instant is the event's provenance (RUN-02).
# --------------------------------------------------------------------------

YESTERDAY = INSTANT - timedelta(days=1)


@pytest.mark.parametrize(
    "supplied",
    [
        YESTERDAY,  # aware UTC datetime
        YESTERDAY.astimezone(IST),  # aware IST datetime, same instant
        YESTERDAY.isoformat(),  # "+00:00" string
        YESTERDAY.astimezone(IST).isoformat(),  # "+05:30" string
        YESTERDAY.isoformat().replace("+00:00", "Z"),
    ],
)
def test_create_keeps_a_supplied_aware_created_at(ist_host, supplied):
    repo = _Repo(MockCollection("things"))
    doc = repo.create({"name": "a", "created_at": supplied})
    stored = repo.collection._data[doc["thing_id"]]
    assert stored["created_at"] == YESTERDAY.replace(tzinfo=None)
    assert stored["created_at"].tzinfo is None
    assert stored["updated_at"] == UTC_NAIVE


@pytest.mark.parametrize(
    "supplied",
    [
        YESTERDAY.replace(tzinfo=None),  # naive: which clock wrote it is unknown
        "2026-11-03T04:32:58",  # naive string
        "2026-11-03",  # a calendar date, not an instant
        "not a date",
        None,
        12345,
        INSTANT + timedelta(hours=2),  # a future "creation" is not an event
    ],
)
def test_create_replaces_an_ambiguous_or_future_created_at(ist_host, supplied):
    repo = _Repo(MockCollection("things"))
    doc = repo.create({"name": "a", "created_at": supplied})
    assert repo.collection._data[doc["thing_id"]]["created_at"] == UTC_NAIVE


# --------------------------------------------------------------------------
# Readers: the SLA clocks compare UTC rows against the UTC clock (Codex 1438 P2).
# --------------------------------------------------------------------------


def _ist_everywhere(monkeypatch):
    from agents.implementations import taskmaster as tm_mod
    from api.routers import tasks as tasks_mod
    from api.services import task_triggers as trig_mod
    from api.services import task_visibility as vis_mod

    for mod in (et, base_mod, product_mod, tasks_mod, tm_mod, trig_mod, vis_mod):
        monkeypatch.setattr(mod, "datetime", IstHostClock)


def _fresh_p2_task():
    """Created this instant (UTC row), due tomorrow, never acknowledged."""
    return {
        "task_id": "TSK-FRESH",
        "title": "fresh",
        "status": "OPEN",
        "priority": "P2",
        "assigned_to": "staff-1",
        "store_id": "S1",
        "escalation_level": 0,
        "created_at": UTC_NAIVE,
        "due_at": UTC_NAIVE + timedelta(days=1),
        "history": [],
    }


def test_endpoint_does_not_escalate_a_fresh_task_on_an_ist_host(monkeypatch):
    import test_task_scan_reliability as scan

    _ist_everywhere(monkeypatch)
    body, escalated, repo = scan._run_auto_escalate([_fresh_p2_task()])
    assert escalated == [], "a task created this instant breached its 240m ack SLA"
    assert body["escalated"] == 0
    assert repo.tasks[0]["status"] == "OPEN"


def test_taskmaster_does_not_escalate_a_fresh_task_on_an_ist_host(monkeypatch):
    import asyncio

    import test_task_scan_reliability as scan

    _ist_everywhere(monkeypatch)
    agent, coll, _colls = scan._taskmaster_with([_fresh_p2_task()])
    assert asyncio.run(agent._escalate_overdue_tasks()) == []
    assert coll.docs[0]["status"] == "OPEN"


def test_endpoint_still_escalates_after_the_real_ack_window(monkeypatch):
    import test_task_scan_reliability as scan

    _ist_everywhere(monkeypatch)
    task = _fresh_p2_task()
    task["created_at"] = UTC_NAIVE - timedelta(minutes=241)
    _body, escalated, repo = scan._run_auto_escalate([task])
    assert escalated == ["TSK-FRESH"]
    assert repo.tasks[0]["escalated_at"] == UTC_NAIVE


def test_overdue_filter_and_system_task_due_use_the_utc_clock(monkeypatch):
    from api.services import task_visibility as vis_mod
    from database.repositories.task_repository import TaskRepository

    _ist_everywhere(monkeypatch)
    flt = vis_mod.overdue_filter()
    assert flt["$or"][0]["due_at"]["$lt"] == UTC_NAIVE

    class _Find:
        def __init__(self):
            self.filters = []

        def find(self, flt, *a, **k):
            self.filters.append(flt)
            return self

        def sort(self, *a, **k):
            return self

        def skip(self, *a):
            return self

        def limit(self, *a):
            return iter([])

        def __iter__(self):
            return iter([])

    coll = _Find()
    monkeypatch.setattr(
        "database.repositories.task_repository.utc_now_naive", et.utc_now_naive
    )
    TaskRepository(coll).find_overdue("S1")
    assert coll.filters and coll.filters[-1]["due_at"]["$lt"] == UTC_NAIVE


def test_task_create_accepts_a_due_time_two_hours_ahead_on_an_ist_host(monkeypatch):
    from api.routers import tasks as tasks_mod

    _ist_everywhere(monkeypatch)
    # The UI sends Date.toISOString(): aware UTC. 2h ahead is a valid due time;
    # stripping its offset and comparing with IST wall-clock called it 3.5h past.
    task = tasks_mod.TaskCreate(
        title="call back", assigned_to="staff-1", due_at=INSTANT + timedelta(hours=2)
    )
    assert task.due_at is not None
    with pytest.raises(ValueError):
        tasks_mod.TaskCreate(
            title="call back", assigned_to="staff-1", due_at=INSTANT - timedelta(hours=1)
        )


def test_unsaved_claim_sweep_reads_sold_at_on_the_utc_clock(monkeypatch):
    """mark_sold / the claim stamp sold_at in UTC; the sweep that frees claims of
    bills never saved must age them on the same clock. On an IST host the local
    clock made a claim seconds old look 5h30m old: an in-flight sale's unit was
    released to be sold again."""
    from api.routers.orders import stock as guard

    monkeypatch.setattr(guard, "datetime", IstHostClock)
    seen = {}

    class _Units:
        def distinct(self, field, flt):
            seen["flt"] = flt
            return []

    class _Orders:
        def find(self, *a, **k):
            return []

    class _Repo:
        def __init__(self, coll):
            self.collection = coll

    monkeypatch.setattr(guard, "get_stock_repository", lambda: _Repo(_Units()))
    monkeypatch.setattr(guard, "get_order_repository", lambda: _Repo(_Orders()))
    monkeypatch.setattr(et, "utc_now", lambda: INSTANT)
    assert guard.sweep_unsaved_claims() == []
    bound = seen["flt"]["sold_at"]
    assert bound["$lt"] == UTC_NAIVE - timedelta(minutes=guard.CLAIM_SWEEP_MINUTES)
    assert bound["$gt"] == UTC_NAIVE - timedelta(days=1)


# --------------------------------------------------------------------------
# Other readers of UTC-stamped rows in files this PR changed.
# --------------------------------------------------------------------------


def test_loyalty_expiry_sweep_compares_expires_at_on_the_utc_clock(monkeypatch):
    """expires_at = UTC stamp + validity. Read with the host's local clock, an
    IST host expired every lot 5h30m early (points lost before their day)."""
    import asyncio

    from api.routers import loyalty as loy

    monkeypatch.setattr(loy, "datetime", IstHostClock)
    monkeypatch.setattr(et, "utc_now", lambda: INSTANT)
    seen = {}

    class _Txns:
        def find_expired_unprocessed(self, now):
            seen["now"] = now
            return []

    monkeypatch.setattr(loy, "get_loyalty_transaction_repository", lambda: _Txns())
    monkeypatch.setattr(loy, "get_loyalty_account_repository", lambda: object())
    monkeypatch.setattr(loy, "_is_admin", lambda u: True)
    out = asyncio.run(loy.expire_sweep(current_user={"user_id": "a", "roles": ["ADMIN"]}))
    assert out["expired_txns"] == 0
    assert seen["now"] == UTC_NAIVE


def test_loyalty_lot_earned_on_an_ist_host_expires_at_its_utc_instant(monkeypatch):
    """Writer and reader on one clock: expiry_for_earn stamps expires_at, the
    sweep reads it with utc_now_naive(). With expires_at on the local clock an
    IST host kept every lot redeemable 5h30m past its expiry (reviewer B25)."""
    from api.services import loyalty_engine as eng
    from database.repositories.loyalty_repository import LoyaltyTransactionRepository

    monkeypatch.setattr(eng, "datetime", IstHostClock)
    monkeypatch.setattr(et, "utc_now", lambda: INSTANT)
    exp = eng.expiry_for_earn({"expiry_days": 365})
    assert exp == UTC_NAIVE + timedelta(days=365)

    # Earn row through the real repository, then sweep at the expiry instant.
    repo = LoyaltyTransactionRepository(MockCollection("loyalty_transactions"))
    repo.create({"txn_id": "T1", "customer_id": "C1", "type": "EARN",
                 "points": 100, "expires_at": exp})
    at_expiry = UTC_NAIVE + timedelta(days=365)
    monkeypatch.setattr(et, "utc_now", lambda: at_expiry.replace(tzinfo=timezone.utc))
    assert [r["txn_id"] for r in repo.find_expired_unprocessed(et.utc_now_naive())] == ["T1"]
    ledger = repo.find_for_customer("C1", limit=10)
    assert eng.expirable_points_by_lot(ledger) == {"T1": 100}
    # One second before the instant the lot is still live.
    assert repo.find_expired_unprocessed(at_expiry - timedelta(seconds=1)) == []


def test_snooze_one_hour_ahead_is_accepted_and_stored_as_the_utc_instant(monkeypatch):
    """The UI sends an aware ISO instant. Its offset was stripped and the UTC
    digits compared with the IST wall clock: a 1-hour snooze was 'in the past'."""
    import asyncio

    from api.routers import notifications as notif

    monkeypatch.setattr(notif, "datetime", IstHostClock)
    monkeypatch.setattr(et, "utc_now", lambda: INSTANT)
    writes = []

    class _Coll:
        def find_one(self, *a, **k):
            return {"notification_id": "N1", "user_id": "u1", "snooze_count": 0}

        def update_one(self, flt, upd, *a, **k):
            writes.append(upd)

    monkeypatch.setattr(notif, "_coll", lambda: _Coll())
    until = (INSTANT + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    asyncio.run(notif.snooze_notification("N1", until=until, current_user={"user_id": "u1"}))
    assert writes[-1]["$set"]["snoozed_until"] == UTC_NAIVE + timedelta(hours=1)
    # an IST-offset instant is the same moment
    until_ist = (INSTANT + timedelta(hours=1)).astimezone(IST).isoformat()
    asyncio.run(notif.snooze_notification("N1", until=until_ist, current_user={"user_id": "u1"}))
    assert writes[-1]["$set"]["snoozed_until"] == UTC_NAIVE + timedelta(hours=1)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        past = (INSTANT - timedelta(minutes=1)).isoformat()
        asyncio.run(notif.snooze_notification("N1", until=past, current_user={"user_id": "u1"}))


def test_stranded_receipt_healer_ages_created_at_on_the_utc_clock(monkeypatch):
    """A receipt is 'stranded' a minute after its UTC created_at. On the host's
    local clock (IST) a receipt created this second already qualified, so the
    healer raced the live create for its number."""
    from api.routers.vendors import grn as grn_mod

    monkeypatch.setattr(grn_mod, "datetime", IstHostClock)
    monkeypatch.setattr(et, "utc_now", lambda: INSTANT)
    seen = {}

    class _Cur:
        def limit(self, n):
            return []

    class _Coll:
        def find(self, q, *a, **k):
            seen["q"] = q
            return _Cur()

    class _Repo:
        collection = _Coll()

    grn_mod._number_stranded_receipts(_Repo())
    assert seen["q"]["created_at"]["$lt"] == UTC_NAIVE - grn_mod._STRANDED_AFTER
