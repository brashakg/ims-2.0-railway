"""
PR #1410 (payroll, batch b09) -- the four corrections after Codex's review
==========================================================================
Codex reviewed head 53197510 against a REAL Mongo and reproduced three
defects that the in-memory tests of test_audit_b09_payroll.py could not see;
decision D30 adds a fourth rule.

P1   a payroll run and the accounting period lock could each pass their check
     before the other wrote, leaving a DRAFT inside a locked month (approve
     and re-run then answer 423 for good; the wages never reach the P&L).
     Both orders of interleaving are driven here on stored state.
P2a  an APPROVED / PAID payslip vanished from My Work, because the shared
     payslip helpers truth-tested the database handle and a pymongo Database
     refuses bool().
P2b  a stored payslip copy explicitly marked DRAFT was served to staff at all
     three payslip doors (decision D3: staff see approved payslips only).
D30  an incentive override or an advance recovery that would make earnings or
     net pay negative is refused (422) by the shared engine.

Every route test runs on TWO databases:

``real``    a genuine ``pymongo`` Database on a local mongod. CI always runs
            it (its mongo:7.0 service on 27017). On a PC give it a port and a
            scratch database name, which the test DROPS:
            ``CI=1 IMS_TEST_MONGO_PORT=27084 IMS_TEST_MONGO_DB=<your scratch name>``
            (CI=1 only stops conftest skipping; never set MONGODB_URL here).
``memory``  the strict in-memory double, made to refuse ``bool()`` on the
            database and on every collection exactly as pymongo does, to stamp
            ``_id`` on insert and to refuse a second ``_id``. It keeps these
            cases running on a PC with no Mongo.

Wiring mirrors production: the payroll router gets a truthy connection WRAPPER
(``dependencies.get_db()``); My Work and the period lock get the RAW database
(``database.connection.get_db().db``).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymongo  # noqa: E402
import pytest  # noqa: E402
from bson import ObjectId  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from strict_fakes import StrictCollection, StrictDB  # noqa: E402

from api.routers import finance as finance_router  # noqa: E402
from api.routers import hr_self_service  # noqa: E402
from api.routers import payroll as payroll_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import _shared as finance_shared  # noqa: E402
from api.routers.finance import period_lock  # noqa: E402
from api.services import payroll_period_guard as guard  # noqa: E402
from api.services.payroll_engine import compute_payroll  # noqa: E402

REAL_HOST = "127.0.0.1"
REAL_PORT = os.environ.get("IMS_TEST_MONGO_PORT") or ("27017" if os.environ.get("CI") else "")
# IMS_TEST_MONGO_DB: a builder's own scratch name. The test DROPS it.
REAL_DB = os.environ.get("IMS_TEST_MONGO_DB") or f"ims_test_b09_payroll_{os.getpid()}"
# Kept out of the fixture's own source on purpose: conftest skips every test
# whose fixture opens a client with this option when no Mongo answers on the
# default port, and that would take the in-memory half with it. This fixture
# has its own skip rule for the real half.
_CLIENT_OPTIONS = {"serverSelectionTimeoutMS": 2000}
REAL_ONLY = pytest.mark.parametrize("rig", ["real"], indirect=True)

EMP, EMP2 = "EMP-B09-N1", "EMP-B09-N2"
ENTITY, STORE = "ENT-B09-N", "ST-B09-N"
YEAR, MONTH = 2026, 9
ADMIN = {"user_id": "admin-b09-n", "username": "admin", "roles": ["ADMIN"],
         "store_ids": [STORE], "active_store_id": STORE}
STAFF = {"user_id": EMP, "username": "staff", "roles": ["SALES_STAFF"],
         "store_ids": [STORE], "active_store_id": STORE}

MINE = "/api/v1/hr/me/payslip"
LATEST = f"/api/v1/payroll/payslip/{EMP}"


def _by_month(month):
    return f"/api/v1/payroll/payslip/{EMP}/{month}/{YEAR}"


# ---------------------------------------------------------------------------
# The two databases
# ---------------------------------------------------------------------------

_NO_BOOL = "objects do not implement truth value testing or bool(). Compare with None instead"


class _NativeLikeCollection(StrictCollection):
    def insert_one(self, doc):
        doc.setdefault("_id", ObjectId())  # pymongo mutates the caller's dict
        return super().insert_one(doc)

    def __bool__(self):
        raise NotImplementedError("Collection " + _NO_BOOL)


class _NativeLikeDB(StrictDB):
    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = _NativeLikeCollection(name)
        return self._collections[name]

    def __bool__(self):
        raise NotImplementedError("Database " + _NO_BOOL)


class _ConnectionWrapper:
    """What ``dependencies.get_db()`` hands the payroll router in production:
    a truthy connection object in front of the raw database."""

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db.get_collection(name)


def _real_db():
    client = pymongo.MongoClient(REAL_HOST, int(REAL_PORT), **_CLIENT_OPTIONS)
    client.admin.command("ping")
    assert client.address[0] == REAL_HOST
    client.drop_database(REAL_DB)
    return client, client[REAL_DB]


class Rig:
    def __init__(self, db, coll_cls):
        self.db = db
        self.coll_cls = coll_cls  # the class whose insert_one a test pauses

    def client(self, user=ADMIN) -> TestClient:
        app = FastAPI()
        app.include_router(payroll_router.router, prefix="/api/v1/payroll")
        app.include_router(hr_self_service.router, prefix="/api/v1/hr")
        app.include_router(finance_router.router, prefix="/api/v1/finance")
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def run(self, month=MONTH, **extra):
        body = {"month": month, "year": YEAR, "entity_id": ENTITY, **extra}
        return self.client().post("/api/v1/payroll/run", json=body)

    def approve(self, month=MONTH):
        return self.client().post(
            "/api/v1/payroll/approve", json={"month": month, "year": YEAR, "entity_id": ENTITY})

    def pay(self, month=MONTH):
        return self.client().post(
            "/api/v1/payroll/lock", json={"month": month, "year": YEAR, "entity_id": ENTITY})

    def lock(self, month=MONTH, without_payroll=False):
        flag = "&without_payroll=true" if without_payroll else ""
        return self.client().post(f"/api/v1/finance/period-lock?month={month}&year={YEAR}{flag}")

    # -- stored state ------------------------------------------------------
    def rows(self, month=MONTH):
        return sorted(
            self.db.get_collection("payroll").find({"month": month, "year": YEAR}),
            key=lambda r: r["employee_id"],
        )

    def statuses(self, month=MONTH):
        return [r["status"] for r in self.rows(month)]

    def lock_rows(self, month=MONTH):
        return list(self.db.get_collection("period_locks").find({"month": month, "year": YEAR}))

    def guard(self, month=MONTH):
        return guard.read(self.db, YEAR, month)

    def pnl(self, month=MONTH):
        return finance_shared._payroll_cost(
            self.db, STORE, f"{YEAR}-{month:02d}-01", f"{YEAR}-{month:02d}-28")

    def stranded(self, month=MONTH) -> bool:
        """THE defect: a DRAFT inside a locked month."""
        return bool(self.lock_rows(month)) and "DRAFT" in self.statuses(month)


@pytest.fixture(params=["memory", "real"])
def rig(request, monkeypatch):
    client = None
    if request.param == "real":
        if not REAL_PORT:
            pytest.skip("no local Mongo given (IMS_TEST_MONGO_PORT)")
        client, db = _real_db()
        assert type(db) is pymongo.database.Database
        coll_cls = pymongo.collection.Collection
    else:
        db = _NativeLikeDB()
        coll_cls = _NativeLikeCollection
    with pytest.raises(NotImplementedError):
        bool(db)  # the handle refuses truth tests, as production's does
    db.get_collection("users").insert_many(
        [{"user_id": EMP, "full_name": "ZZ Native One"}, {"user_id": EMP2, "full_name": "ZZ Native Two"}])
    db.get_collection("salary_config").insert_many(
        [{"employee_id": e, "entity_id": ENTITY, "store_id": STORE, "basic": 15000,
          "hra": 5000, "tds_monthly": 100, "is_active": True} for e in (EMP, EMP2)])
    monkeypatch.setattr(payroll_router, "_get_db", lambda: _ConnectionWrapper(db))
    monkeypatch.setattr(hr_self_service, "_get_db", lambda: db)
    monkeypatch.setattr(period_lock, "_get_db", lambda: db)
    monkeypatch.setattr("api.utils.ist.now_ist_naive", lambda: datetime(2026, 11, 12, 12))
    yield Rig(db, coll_cls)
    if client is not None:
        client.drop_database(REAL_DB)


class Pause:
    """Holds ONE call at a chosen point until the test lets it go."""

    def __init__(self):
        self.reached = threading.Event()
        self.go = threading.Event()

    def here(self):
        self.reached.set()
        assert self.go.wait(30), "the other request never finished"


def _pause_first_insert(monkeypatch, rig, collection, pause):
    original = rig.coll_cls.insert_one

    def insert_one(self, doc, *a, **k):
        if self.name == collection and not pause.reached.is_set():
            pause.here()
        return original(self, doc, *a, **k)

    monkeypatch.setattr(rig.coll_cls, "insert_one", insert_one)


def _in_thread(fn):
    box = {}
    t = threading.Thread(target=lambda: box.update(reply=fn()))
    t.start()
    return t, box


def _detail(reply):
    return str(reply.json().get("detail"))


# ===========================================================================
# P1 -- the run and the period lock can no longer commit across each other
# ===========================================================================


def test_p1_lock_arriving_while_a_run_is_about_to_write_is_refused(rig, monkeypatch):
    """Codex's order: the run has passed its period check and is paused before
    its first payroll insert; the lock arrives (with "lock anyway")."""
    pause = Pause()
    _pause_first_insert(monkeypatch, rig, "payroll", pause)
    t, run = _in_thread(rig.run)
    assert pause.reached.wait(30), "the run never reached its first write"
    locked = rig.lock(without_payroll=True)
    pause.go.set()
    t.join(30)

    assert locked.status_code == 409 and "being saved right now" in _detail(locked)
    assert run["reply"].status_code == 200
    assert rig.statuses() == ["DRAFT", "DRAFT"] and rig.lock_rows() == []
    assert not rig.stranded()

    # The draft is not stuck: it is approved, the month then locks, and the
    # wages are the month's cost exactly once.
    assert rig.approve().status_code == 200
    assert rig.lock().status_code == 200
    assert rig.statuses() == ["APPROVED", "APPROVED"] and len(rig.lock_rows()) == 1
    assert rig.pnl() == sum(r["breakdown"]["ctc_cost"] for r in rig.rows()) > 0
    assert rig.run().status_code == 423  # and now the month really is closed


def test_p1_lock_that_already_read_payroll_is_refused_once_a_run_is_saved(rig, monkeypatch):
    """The other order: the lock has read payroll (nothing there) and is
    paused before it commits; a whole payroll run is saved; the lock resumes."""
    pause = Pause()
    original = payroll_router._scope_configs

    def scope_configs(db, store_id, entity_id):
        # The lock asks for every shop's setups; the run names its entity.
        if store_id is None and entity_id is None and not pause.reached.is_set():
            pause.here()
        return original(db, store_id, entity_id)

    monkeypatch.setattr(payroll_router, "_scope_configs", scope_configs)
    t, locked = _in_thread(lambda: rig.lock(without_payroll=True))
    assert pause.reached.wait(30), "the lock never reached its payroll check"
    ran = rig.run()
    pause.go.set()
    t.join(30)

    assert ran.status_code == 200
    assert locked["reply"].status_code == 409
    assert "changed while the month was being locked" in _detail(locked["reply"])
    assert rig.statuses() == ["DRAFT", "DRAFT"] and rig.lock_rows() == []
    assert rig.approve().status_code == 200  # not stranded
    assert rig.pnl() > 0


def test_p1_run_is_refused_from_the_moment_the_lock_commits(rig, monkeypatch):
    """The lock has committed on the shared guard but its period_locks row is
    not written yet: the old run check (period_locks only) saw an open month."""
    pause = Pause()
    _pause_first_insert(monkeypatch, rig, "period_locks", pause)
    t, locked = _in_thread(lambda: rig.lock(without_payroll=True))
    assert pause.reached.wait(30), "the lock never reached its last write"
    ran = rig.run()
    pause.go.set()
    t.join(30)

    assert ran.status_code == 423 and rig.rows() == []
    assert locked["reply"].status_code == 200
    assert [r.get("without_payroll_for") for r in rig.lock_rows()] == [2]  # D14 kept
    assert rig.run(dry_run=True).status_code == 200  # a preview still works


def test_p1_lock_that_stopped_after_committing_is_finished_by_locking_again(rig, monkeypatch):
    """The server dies between the guard commit and the period_locks row."""
    original = rig.coll_cls.insert_one
    died = []

    def insert_one(self, doc, *a, **k):
        if self.name == "period_locks" and not died:
            died.append(1)
            raise RuntimeError("server stopped here")
        return original(self, doc, *a, **k)

    monkeypatch.setattr(rig.coll_cls, "insert_one", insert_one)
    assert rig.lock(without_payroll=True).status_code == 500
    assert rig.lock_rows() == [] and rig.guard()["locked"] is True

    assert rig.run().status_code == 423 and rig.rows() == []  # no draft slips in
    assert rig.lock().status_code == 200  # no second "lock anyway" needed
    (row,) = rig.lock_rows()
    assert row["locked_by"] == ADMIN["user_id"] and row["without_payroll_for"] == 2
    assert rig.lock().status_code == 400  # already locked, as before


@pytest.mark.parametrize("staff_on_payroll", [2, 1], ids=["found-at-next-row", "found-at-the-end"])
def test_p1_run_that_stalled_past_the_lease_leaves_no_draft_behind_the_lock(
    rig, monkeypatch, staff_on_payroll
):
    """A run stalls for longer than the guard's lease just before a write; the
    month is locked meanwhile; the write then lands behind the lock. The run
    finds the month closed (at its next row, or when it finishes), takes its
    own drafts back and answers 423."""
    if staff_on_payroll == 1:
        rig.db.get_collection("salary_config").delete_one({"employee_id": EMP2})
    original = rig.coll_cls.insert_one
    locked = []

    def insert_one(self, doc, *a, **k):
        if self.name == "payroll" and not locked:
            rig.db.get_collection(guard.COLLECTION).update_one(
                {"_id": f"{YEAR}-{MONTH:02d}"},
                {"$set": {"beat": time.time() - guard.LEASE_SECONDS - 1}},
            )
            locked.append(rig.lock(without_payroll=True))
        return original(self, doc, *a, **k)

    monkeypatch.setattr(rig.coll_cls, "insert_one", insert_one)
    ran = rig.run()

    assert locked[0].status_code == 200 and len(rig.lock_rows()) == 1
    assert ran.status_code == 423
    assert rig.rows() == [] and not rig.stranded()


def test_p1_run_that_died_holding_the_month_blocks_the_lock_only_for_the_lease(rig):
    guard.claim_run(rig.db, YEAR, MONTH)  # a run that never came back
    assert rig.lock(without_payroll=True).status_code == 409
    rig.db.get_collection(guard.COLLECTION).update_one(
        {"_id": f"{YEAR}-{MONTH:02d}"},
        {"$set": {"beat": time.time() - guard.LEASE_SECONDS - 1}},
    )
    assert rig.lock(without_payroll=True).status_code == 200
    assert guard.release_run(rig.db, YEAR, MONTH) is False  # it may not write now
    assert guard.renew_run(rig.db, YEAR, MONTH) is False


def test_p1_decisions_d1_and_d14_still_hold_through_the_guard(rig):
    """A DRAFT always refuses the lock, even with "lock anyway"; payroll not
    run asks first; an approved month locks without the flag."""
    asked = rig.lock()
    assert asked.status_code == 409 and asked.json()["detail"]["code"] == "PAYROLL_NOT_RUN"
    assert rig.run().status_code == 200
    refused = rig.lock(without_payroll=True)
    assert refused.status_code == 409 and "still a draft" in _detail(refused)
    assert rig.lock_rows() == [] and not rig.guard().get("locked")
    assert rig.run().status_code == 200  # a refused lock closes nothing
    assert rig.approve().status_code == 200 and rig.lock().status_code == 200
    assert "without_payroll_for" not in rig.lock_rows()[0]


@REAL_ONLY
def test_p1_free_running_race_never_strands_a_draft(rig):
    """No pauses: a run and a "lock anyway" start together, eight months in a
    row, against real Mongo. Whoever wins, no month ends locked with a DRAFT,
    and whatever was saved can still be approved or was refused cleanly."""
    for month in range(1, 9):
        start = threading.Barrier(2)

        def go(call, month=month, start=start):
            start.wait(10)
            return call(month)

        t1, run = _in_thread(lambda: go(rig.run))
        t2, lock = _in_thread(lambda: go(lambda m: rig.lock(m, without_payroll=True)))
        t1.join(30)
        t2.join(30)
        outcome = (run["reply"].status_code, lock["reply"].status_code, rig.statuses(month))
        assert not rig.stranded(month), outcome
        assert run["reply"].status_code in (200, 423), outcome
        assert lock["reply"].status_code in (200, 409), outcome
        if run["reply"].status_code == 423:
            assert rig.rows(month) == [], outcome
        g = rig.guard(month)
        assert g is None or g.get("runs") == 0, g


# ===========================================================================
# P2a -- My Work on a database that refuses truth tests
# ===========================================================================


def test_p2_my_work_shows_the_approved_then_the_paid_payslip(rig):
    staff = rig.client(STAFF)  # an ordinary floor role, through the staff door
    assert rig.run().status_code == 200
    draft = staff.get(MINE).json()
    assert draft["payslip"] is None and draft["not_ready"] == {"month": MONTH, "year": YEAR}

    net = rig.rows()[0]["net_salary"]
    assert rig.approve().status_code == 200
    for door in (MINE, LATEST, _by_month(MONTH)):
        slip = staff.get(door).json()["payslip"]
        assert slip and (slip["status"], slip["breakdown"]["net_pay"]) == ("APPROVED", net), door

    assert rig.pay().status_code == 200
    for door in (MINE, LATEST, _by_month(MONTH)):
        slip = staff.get(door).json()["payslip"]
        assert slip and (slip["status"], slip["breakdown"]["net_pay"]) == ("PAID", net), door


def test_p2_no_payroll_door_truth_tests_a_raw_database(rig, monkeypatch):
    """The whole helper chain, handed the RAW database (what My Work and the
    finance routers hold): nothing answers 500 for a truth test."""
    monkeypatch.setattr(payroll_router, "_get_db", lambda: rig.db)
    admin = rig.client()
    assert admin.post("/api/v1/payroll/run", json={"month": MONTH, "year": YEAR, "entity_id": ENTITY}).status_code == 200
    period = f"month={MONTH}&year={YEAR}"
    for door in (
        f"/api/v1/payroll/config/{EMP}", "/api/v1/payroll/config",
        f"/api/v1/payroll/salary-sheet?{period}", f"/api/v1/payroll/advances/{EMP}",
        LATEST, _by_month(MONTH), _by_month(MONTH) + "/print",
        f"/api/v1/payroll/incentive-summary/{EMP}/{MONTH}/{YEAR}", "/api/v1/payroll/pt-slabs",
        f"/api/v1/payroll/run/rows?{period}", f"/api/v1/payroll/registers/summary?{period}",
    ):
        reply = admin.get(door)
        assert reply.status_code < 500, (door, reply.status_code, reply.text[:200])
    for action in ("approve", "lock"):
        assert admin.post(f"/api/v1/payroll/{action}", json={"month": MONTH, "year": YEAR}).status_code == 200


def test_p2_shared_helpers_do_not_truth_test_an_offline_pymongo_database():
    """No server needed: a genuine pymongo Database raises on ``if not db``
    before any network call. The helpers must get past that (and then fail
    soft on the unreachable server)."""
    db = pymongo.MongoClient("127.0.0.1", 9, connect=False, serverSelectionTimeoutMS=1)["offline"]
    assert payroll_router._get_employee_details(db, EMP) == {}
    assert payroll_router._get_salary_config(db, EMP) is None


# ===========================================================================
# P2b -- a stored payslip copy marked DRAFT is a draft at every staff door
# ===========================================================================


def _copy(rig, month, status=None, net=1000):
    doc = {"employee_id": EMP, "month": month, "year": YEAR,
           "employee_name": "ZZ Native One", "breakdown": {"net_pay": net}}
    if status is not None:
        doc["status"] = status
    rig.db.get_collection("payslips").insert_one(doc)


def _seen(reply):
    assert reply.status_code == 200, reply.text
    p = reply.json()["payslip"]
    return None if p is None else (p["month"], p.get("status"), p["breakdown"]["net_pay"])


@pytest.mark.parametrize("door", [MINE, LATEST, _by_month(12)], ids=["my-work", "latest", "by-month"])
def test_p2_stored_draft_copy_is_withheld_from_staff_at_every_door(rig, door):
    """Codex's case: an explicitly DRAFT copy for December with no run row."""
    _copy(rig, 12, "DRAFT", net=98765)
    assert _seen(rig.client(STAFF).get(door)) is None
    assert rig.db.get_collection("payslips").count_documents({}) == 1  # reading wrote nothing


def test_p2_admin_still_sees_the_stored_draft_copy_labelled_draft(rig):
    _copy(rig, 12, "DRAFT", net=98765)
    for door in (LATEST, _by_month(12)):
        assert _seen(rig.client().get(door)) == (12, "DRAFT", 98765), door


def test_p2_staff_keep_their_newest_approved_payslip_behind_a_stored_draft_copy(rig):
    """With an older PAID run row: the latest doors show that payslip, never
    the newer DRAFT copy; the month door for the draft shows nothing."""
    assert rig.run().status_code == 200 and rig.approve().status_code == 200
    assert rig.pay().status_code == 200
    net = rig.rows()[0]["net_salary"]
    _copy(rig, 12, "DRAFT", net=98765)
    staff = rig.client(STAFF)
    assert _seen(staff.get(MINE)) == (MONTH, "PAID", net)
    assert _seen(staff.get(LATEST)) == (MONTH, "PAID", net)
    assert _seen(staff.get(_by_month(12))) is None


def test_p2_issued_stored_copies_still_show_to_staff(rig):
    """History is not hidden: a copy marked APPROVED or PAID, and an
    old-format copy that carries no status at all (written before payslips
    had one; always shown as an issued payslip, and still shown)."""
    _copy(rig, 5, None, net=111)
    _copy(rig, 6, "APPROVED", net=222)
    _copy(rig, 7, "PAID", net=333)
    _copy(rig, 12, "DRAFT", net=98765)
    staff = rig.client(STAFF)
    assert _seen(staff.get(_by_month(5))) == (5, None, 111)
    assert _seen(staff.get(_by_month(6))) == (6, "APPROVED", 222)
    assert _seen(staff.get(_by_month(7))) == (7, "PAID", 333)
    # the newest ISSUED copy is the latest payslip; the December draft is not
    assert _seen(staff.get(MINE)) == (7, "PAID", 333)
    assert _seen(staff.get(LATEST)) == (7, "PAID", 333)
    rig.db.get_collection("payslips").delete_many({"month": {"$in": [6, 7]}})
    assert _seen(staff.get(MINE)) == (5, None, 111)


# ===========================================================================
# D30 -- nothing typed for a run may take earnings or net pay below zero
# ===========================================================================

_CONFIG = {"employee_id": EMP, "basic": 15000, "hra": 5000, "tds_monthly": 100}


def _engine(**typed):
    return compute_payroll(_CONFIG, month=MONTH, year=YEAR, **typed)


def test_d30_engine_refuses_what_would_take_pay_below_zero():
    net = _engine()["net_pay"]
    assert net > 0
    with pytest.raises(ValueError, match="Enter a smaller recovery"):
        _engine(advance_recovery=net + 0.01)
    with pytest.raises(ValueError, match="stays outstanding"):
        _engine(advance_recovery=50000)
    with pytest.raises(ValueError, match="entered as a deduction"):
        _engine(incentive=-1)
    with pytest.raises(ValueError, match="Incentive cannot be negative"):
        _engine(incentive=-(net + 5000))  # the one that made EARNINGS negative
    with pytest.raises(ValueError):
        _engine(incentive=float("nan"))
    # the whole of the month's pay may be recovered, and an incentive adds room
    assert _engine(advance_recovery=net)["net_pay"] == 0
    assert _engine(incentive=500, advance_recovery=net + 500)["net_pay"] == 0
    # nothing left to recover from: the message says so instead of "Rs 0.00 or less"
    with pytest.raises(ValueError, match="Enter 0"):
        compute_payroll({**_CONFIG, "tds_monthly": 99999}, month=MONTH, year=YEAR, advance_recovery=1)
    # a month with no recovery typed is not refused by this rule
    assert compute_payroll({**_CONFIG, "tds_monthly": 99999}, month=MONTH, year=YEAR)["net_pay"] < 0


@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "save"])
@pytest.mark.parametrize(
    "typed, says",
    [({"advances": {EMP: 50000}}, "Enter a smaller recovery"),
     ({"incentives": {EMP: -30000}}, "entered as a deduction")],
    ids=["recovery-beyond-net-pay", "negative-incentive"],
)
def test_d30_run_is_refused_and_nothing_at_all_is_saved(rig, dry_run, typed, says):
    advances = rig.db.get_collection("salary_advances")
    advances.insert_one({"advance_id": "ADV-B09-N", "employee_id": EMP, "amount": 50000,
                         "balance": 50000, "status": "APPROVED"})
    before = list(advances.find({}))

    reply = rig.run(dry_run=dry_run, **typed)
    assert reply.status_code == 422, reply.text
    assert says in _detail(reply) and "ZZ Native One" in _detail(reply)
    # Nobody's row is saved -- not the refused employee's, not the other
    # employee's who was fine -- and the advance is exactly as it was.
    assert rig.rows() == []
    assert list(advances.find({})) == before
    g = rig.guard()
    assert g is None or g.get("runs") == 0  # the month is not left held


def test_d30_recovery_up_to_the_months_pay_is_saved(rig):
    net = rig.run(dry_run=True).json()["rows"][0]["net_salary"]
    assert rig.run(advances={EMP: net}).status_code == 200
    stored = {r["employee_id"]: r for r in rig.rows()}
    assert stored[EMP]["net_salary"] == 0 and stored[EMP]["advance_deduction"] == net
    assert stored[EMP2]["net_salary"] == net
    assert rig.run(advances={EMP: net + 1}).status_code == 422
    assert {r["employee_id"]: r["net_salary"] for r in rig.rows()} == {EMP: 0, EMP2: net}


# ---------------------------------------------------------------------------
# D30 and the LOCKED incentive payout. Roadmap decision #31: a clawback (a
# kicker taken back on a return) is a negative line folded into the locked
# payout snapshot, the one incentive feed payroll reads. Nobody types it, so
# it is not D30's "negative incentive override": it still comes off that
# month's pay as it always did -- but never below zero.
# ---------------------------------------------------------------------------

SNAPSHOT = "SNAP-B09-N"


def _lock_payout(rig, **product_incentive):
    rig.db.get_collection("payout_snapshots").insert_one({
        "snapshot_id": SNAPSHOT, "store_id": STORE, "year": YEAR, "month": MONTH, "status": "LOCKED",
        "staff_payouts": [{"user_id": emp, "total_payout": 0, "product_incentive": rupees}
                          for emp, rupees in product_incentive.items()]})


def _fed(rig):
    return rig.db.get_collection("payout_snapshots").find_one({"snapshot_id": SNAPSHOT}).get("payroll_fed_at")


def test_d30_engine_takes_a_payout_clawback_off_pay_but_never_below_zero():
    net = _engine()["net_pay"]
    clawed = _engine(incentive_clawback=500)
    assert clawed["earnings"]["incentive"] == -500 and clawed["net_pay"] == net - 500
    assert _engine(incentive=200, incentive_clawback=500)["earnings"]["incentive"] == -300
    assert _engine(incentive_clawback=net)["net_pay"] == 0
    with pytest.raises(ValueError, match="clawback"):
        _engine(incentive_clawback=net + 0.01)
    with pytest.raises(ValueError):
        _engine(incentive_clawback=-1)  # a clawback is an amount taken back
    # with a recovery typed as well, the recovery is what the Admin can change
    with pytest.raises(ValueError, match="Enter a smaller recovery"):
        _engine(incentive_clawback=500, advance_recovery=net)
    assert _engine(incentive_clawback=500, advance_recovery=net - 500)["net_pay"] == 0


@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "save"])
def test_d30_a_clawback_in_the_locked_payout_does_not_stop_the_shops_payroll(rig, dry_run):
    plain = {r["employee_id"]: r["net_salary"] for r in rig.run(dry_run=True).json()["rows"]}
    _lock_payout(rig, **{EMP: -500.0, EMP2: 300.0})

    reply = rig.run(dry_run=dry_run)
    assert reply.status_code == 200, reply.text
    got = {r["employee_id"]: (r["incentives"], r["net_salary"]) for r in reply.json()["rows"]}
    assert got == {EMP: (-500, plain[EMP] - 500), EMP2: (300, plain[EMP2] + 300)}
    assert [(r["incentives"], r["net_salary"]) for r in rig.rows()] == (
        [] if dry_run else [got[EMP], got[EMP2]])
    # a TYPED negative incentive is still refused, even a small one
    assert rig.run(dry_run=dry_run, incentives={EMP: -1}).status_code == 422


def test_d30_a_payout_clawback_bigger_than_the_months_pay_is_refused(rig):
    _lock_payout(rig, **{EMP: -90000.0, EMP2: 300.0})
    for dry_run in (True, False):
        reply = rig.run(dry_run=dry_run)
        assert reply.status_code == 422, reply.text
        assert "clawback" in _detail(reply) and "ZZ Native One" in _detail(reply)
    assert rig.rows() == [] and _fed(rig) is None  # nothing saved, payout not consumed
    g = rig.guard()
    assert g is None or g.get("runs") == 0


# ---------------------------------------------------------------------------
# P1, the lock's own last write: one period_locks row, however many requests
# finish the same lock.
# ---------------------------------------------------------------------------


def test_p1_second_lock_arriving_before_the_first_has_written_leaves_one_lock_row(rig, monkeypatch):
    pause = Pause()
    _pause_first_insert(monkeypatch, rig, "period_locks", pause)
    t, first = _in_thread(lambda: rig.lock(without_payroll=True))
    assert pause.reached.wait(30), "the lock never reached its last write"
    second = rig.lock()  # the same press again: finishes the committed lock
    pause.go.set()
    t.join(30)

    assert first["reply"].status_code == 200 and second.status_code == 200
    (row,) = rig.lock_rows()
    assert row["locked_by"] == ADMIN["user_id"] and row["without_payroll_for"] == 2
    assert rig.lock().status_code == 400  # already locked, as before
