"""TZ-P3 sweep -- IST "today" defaults + the jarvis brand duplicate-key fix.

The Railway box runs UTC; the business calendar is IST (UTC+5:30). Every test
here freezes the clock at 01:00 IST = 19:30 UTC of the PRIOR day -- the exact
window (00:00-05:30 IST) where a UTC-based "today" lands on yesterday. Each
test then asserts the touched code paths key on the IST calendar day.

CI-robustness contract:
- every clock accessor is monkeypatched IN the consuming module (the helpers
  are imported by name, so patching api.utils.ist alone would not take);
- DB access goes through seeded in-memory fakes (a tiny Mongo-filter
  evaluator), never a live mongod;
- assertions are structural (specific keys / id sets), never whole-JSON
  substring matches.
"""
import os
import re
import sys
from datetime import date, datetime, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.utils.ist import IST  # noqa: E402

# Frozen instant: 01:00 IST on 2026-06-10 == 19:30 UTC on 2026-06-09.
FROZEN_IST_AWARE = datetime(2026, 6, 10, 1, 0, tzinfo=IST)
FROZEN_IST_NAIVE = datetime(2026, 6, 10, 1, 0)
FROZEN_IST_DATE = date(2026, 6, 10)
FROZEN_UTC_NAIVE = datetime(2026, 6, 9, 19, 30)  # same instant, naive UTC


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo stand-in (only the operators the sweep touches)
# ---------------------------------------------------------------------------


def _cmp_compatible(a, b):
    """Mirror Mongo's type-bracket rule: str never compares to BSON Date."""
    return isinstance(a, type(b)) or isinstance(b, type(a))


def _matches(doc, query):
    for key, cond in query.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, sub) for sub in cond):
                return False
            continue
        val = doc.get(key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$in":
                    if val not in arg:
                        return False
                elif op == "$nin":
                    if val in arg:
                        return False
                elif op == "$ne":
                    if val == arg:
                        return False
                elif op == "$regex":
                    if not isinstance(val, str) or re.search(arg, val) is None:
                        return False
                elif op in ("$lt", "$lte", "$gt", "$gte"):
                    # Mongo never range-matches across type brackets (the
                    # pre-fix order_repository bug: a STRING $gte bound vs a
                    # BSON-Date created_at matches nothing).
                    if val is None or not _cmp_compatible(val, arg):
                        return False
                    ok = {
                        "$lt": val < arg,
                        "$lte": val <= arg,
                        "$gt": val > arg,
                        "$gte": val >= arg,
                    }[op]
                    if not ok:
                        return False
                else:  # unsupported operator -> loud failure, not a false pass
                    raise AssertionError("FakeCollection: unsupported op " + op)
        else:
            if val != cond:
                return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, spec):
        for field, direction in reversed(list(spec)):
            non_null = [d.get(field) for d in self._docs if d.get(field) is not None]
            default = datetime.min if non_null and isinstance(non_null[0], datetime) else ""
            self._docs.sort(
                key=lambda d: d.get(field) if d.get(field) is not None else default,
                reverse=(direction == -1),
            )
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(d.copy() for d in self._docs)


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = [d.copy() for d in (docs or [])]
        self.pipelines = []  # every aggregate() call, for structural asserts

    def find(self, query=None, projection=None, **_kw):
        query = query or {}
        return FakeCursor([d for d in self.docs if _matches(d, query)])

    def find_one(self, query=None, *a, **kw):
        for d in self.docs:
            if _matches(d, query or {}):
                return d.copy()
        return None

    def count_documents(self, query=None, **_kw):
        return sum(1 for d in self.docs if _matches(d, query or {}))

    def aggregate(self, pipeline, **_kw):
        self.pipelines.append(pipeline)
        return iter([])

    def distinct(self, _field, *_a, **_kw):
        return []

    def estimated_document_count(self):
        return len(self.docs)


class FakeDB:
    """db.get_collection(name) -> named FakeCollection (empty unless seeded)."""

    def __init__(self, **named):
        self._named = dict(named)

    def get_collection(self, name):
        if name not in self._named:
            self._named[name] = FakeCollection()
        return self._named[name]


# ---------------------------------------------------------------------------
# Sanity anchor: the frozen instant really is on the PRIOR UTC day
# ---------------------------------------------------------------------------


def test_sanity_frozen_instant_is_prior_utc_day():
    as_utc = FROZEN_IST_AWARE.astimezone(timezone.utc)
    assert as_utc.replace(tzinfo=None) == FROZEN_UTC_NAIVE
    assert as_utc.date() == date(2026, 6, 9)  # UTC still on yesterday
    assert FROZEN_IST_AWARE.date() == FROZEN_IST_DATE  # IST already on the 10th


# ---------------------------------------------------------------------------
# points.py -- GET /points/daily defaults its date to the IST day
# ---------------------------------------------------------------------------


async def test_points_list_daily_defaults_to_ist_day(monkeypatch):
    from api.routers import points as points_mod

    captured = {}

    class _FakeRepo:
        def list_by_date(self, store, date_str):
            captured["store"] = store
            captured["date_str"] = date_str
            return []

    monkeypatch.setattr(points_mod, "ist_today", lambda: FROZEN_IST_DATE)
    monkeypatch.setattr(points_mod, "_points_repo", lambda: _FakeRepo())

    user = {"roles": [], "active_store_id": "BV-TEST-01"}
    resp = await points_mod.list_daily(current_user=user, date=None, store_id=None)

    # 01:00 IST: a UTC clock would have produced "2026-06-09".
    assert resp["date_str"] == "2026-06-10"
    assert captured["date_str"] == "2026-06-10"
    assert captured["store"] == "BV-TEST-01"


# ---------------------------------------------------------------------------
# workshop_repository.find_overdue -- "overdue" rolls over at IST midnight
# ---------------------------------------------------------------------------


def test_workshop_find_overdue_uses_ist_business_day(monkeypatch):
    from database.repositories import workshop_repository as wr

    monkeypatch.setattr(wr, "ist_today", lambda: FROZEN_IST_DATE)

    coll = FakeCollection([
        {"job_id": "J-OLD", "status": "PENDING", "expected_date": "2026-06-08"},
        # Due yesterday-IST: ONLY overdue once "today" is the IST 10th -- the
        # UTC clock (still on the 9th) would miss this row entirely.
        {"job_id": "J-YDAY", "status": "IN_PROGRESS", "expected_date": "2026-06-09"},
        {"job_id": "J-TODAY", "status": "PENDING", "expected_date": "2026-06-10"},
        {"job_id": "J-DONE", "status": "COMPLETED", "expected_date": "2026-06-01"},
    ])
    repo = wr.WorkshopJobRepository(coll)

    got = {j["job_id"] for j in repo.find_overdue()}
    assert got == {"J-OLD", "J-YDAY"}


# ---------------------------------------------------------------------------
# megaphone -- birthday scan keys on the IST MM-DD
# ---------------------------------------------------------------------------


async def test_megaphone_birthday_scan_uses_ist_date(monkeypatch):
    from agents.implementations import megaphone as mp_mod

    monkeypatch.setattr(mp_mod, "_now_ist", lambda: FROZEN_IST_AWARE)

    coll = FakeCollection([
        {"customer_id": "C-IST", "name": "IST Bday", "date_of_birth": "1990-06-10"},
        # The UTC clock would have greeted this one (06-09) instead.
        {"customer_id": "C-UTC", "name": "UTC Bday", "date_of_birth": "1985-06-09"},
        {
            "customer_id": "C-OPTOUT",
            "name": "Opted Out",
            "date_of_birth": "1992-06-10",
            "marketing_consent": False,
        },
    ])
    agent = mp_mod.MegaphoneAgent.__new__(mp_mod.MegaphoneAgent)
    agent.get_collection = lambda name: coll if name == "customers" else None

    got = {c["customer_id"] for c in await agent._scan_birthdays_today()}
    assert got == {"C-IST"}


# ---------------------------------------------------------------------------
# campaign_segments -- fu_due_today includes follow-ups due on the NEW IST day
# ---------------------------------------------------------------------------


def test_campaign_fu_due_today_includes_new_ist_day(monkeypatch):
    from api.services import campaign_segments as cs

    monkeypatch.setattr(cs, "now_ist_naive", lambda: FROZEN_IST_NAIVE)

    follow_ups = FakeCollection([
        {
            # Due on the new IST day -- a UTC "today" of 2026-06-09 would
            # exclude this row ($lte yesterday).
            "follow_up_id": "FU-TODAY",
            "customer_id": "C1",
            "customer_name": "Asha",
            "customer_phone": "9876543210",
            "status": "pending",
            "scheduled_date": "2026-06-10",
            "type": "general",
        },
        {
            "follow_up_id": "FU-FUTURE",
            "customer_id": "C2",
            "customer_name": "Vik",
            "customer_phone": "9876543211",
            "status": "pending",
            "scheduled_date": "2026-06-11",
            "type": "general",
        },
        {
            "follow_up_id": "FU-DONE",
            "customer_id": "C3",
            "customer_name": "Mira",
            "customer_phone": "9876543212",
            "status": "completed",
            "scheduled_date": "2026-06-09",
            "type": "general",
        },
    ])
    db = FakeDB(follow_ups=follow_ups)

    rows = cs._resolve_fu_due_today(db, None)
    got = {r["variables"]["follow_up_id"] for r in rows}
    assert got == {"FU-TODAY"}


# ---------------------------------------------------------------------------
# order_repository.find_by_store -- datetime bound + no $or clobber
# ---------------------------------------------------------------------------


def _order_repo(coll):
    from database.repositories.order_repository import OrderRepository

    return OrderRepository(coll)


def test_order_repo_find_by_store_from_date_matches_datetime_doc():
    # created_at is a (naive-UTC) datetime in Mongo. The pre-fix code compared
    # it against an ISO STRING, which in Mongo matches NOTHING; the fake
    # mirrors that type-bracket rule, so this test fails against the old code.
    coll = FakeCollection([
        {
            "order_id": "O-NEW",  # 01:00 IST on the 10th == 19:30 UTC the 9th
            "store_id": "S1",
            "created_at": datetime(2026, 6, 9, 19, 30),
        },
        {
            "order_id": "O-OLD",  # 17:30 IST on the 9th -> before IST midnight
            "store_id": "S1",
            "created_at": datetime(2026, 6, 9, 12, 0),
        },
        {
            "order_id": "O-CAMEL",  # legacy camelCase shape, in range
            "storeId": "S1",
            "createdAt": datetime(2026, 6, 10, 5, 0),
        },
        {
            "order_id": "O-OTHER-STORE",
            "store_id": "S2",
            "created_at": datetime(2026, 6, 10, 5, 0),
        },
    ])
    repo = _order_repo(coll)

    got = {o["order_id"] for o in repo.find_by_store("S1", from_date=date(2026, 6, 10))}
    assert got == {"O-NEW", "O-CAMEL"}


def test_order_repo_find_by_store_status_does_not_clobber_date_filter():
    # Pre-fix, the status branch REPLACED the whole $or, silently dropping the
    # from_date filter -- O-DEL-OLD would have leaked into the result.
    coll = FakeCollection([
        {
            "order_id": "O-DEL",
            "store_id": "S1",
            "status": "DELIVERED",
            "created_at": datetime(2026, 6, 10, 3, 0),
        },
        {
            "order_id": "O-DEL-OLD",
            "store_id": "S1",
            "status": "DELIVERED",
            "created_at": datetime(2026, 6, 1, 3, 0),
        },
        {
            "order_id": "O-PENDING",
            "store_id": "S1",
            "status": "PENDING",
            "created_at": datetime(2026, 6, 10, 3, 0),
        },
    ])
    repo = _order_repo(coll)

    got = {
        o["order_id"]
        for o in repo.find_by_store("S1", from_date=date(2026, 6, 10), status="DELIVERED")
    }
    assert got == {"O-DEL"}


# ---------------------------------------------------------------------------
# jarvis -- brand rollup $match must use $nin (the {"$ne": "", "$ne": None}
# literal collapses to {"$ne": None} at parse time, leaking "" brands)
# ---------------------------------------------------------------------------


def test_jarvis_brand_rollup_excludes_blank_and_null_brands(monkeypatch):
    from api.routers import jarvis as jarvis_mod

    prod_col = FakeCollection()
    monkeypatch.setattr(
        jarvis_mod,
        "get_db_collection",
        lambda name: prod_col if name == "products" else None,
    )

    jarvis_mod.JarvisAnalyticsEngine.get_extended_context()

    # Find the by-brand pipeline (the one grouping on "$brand").
    brand_matches = [
        stage["$match"]
        for pipeline in prod_col.pipelines
        if any(s.get("$group", {}).get("_id") == "$brand" for s in pipeline)
        for stage in pipeline
        if "$match" in stage
    ]
    assert brand_matches, "by-brand aggregation pipeline not found"
    match = brand_matches[0]
    assert match["brand"] == {"$nin": ["", None]}

    # Semantic check: ""/None/missing brands are excluded, real brands pass.
    assert not _matches({"brand": ""}, {"brand": match["brand"]})
    assert not _matches({"brand": None}, {"brand": match["brand"]})
    assert not _matches({}, {"brand": match["brand"]})
    assert _matches({"brand": "Ray-Ban"}, {"brand": match["brand"]})


# ---------------------------------------------------------------------------
# ap_engine -- aging as_of defaults to the IST business day
# ---------------------------------------------------------------------------


def test_ap_aging_as_of_defaults_to_ist_day(monkeypatch):
    from api.services import ap_engine as ap_mod

    monkeypatch.setattr(ap_mod, "now_ist_naive", lambda: FROZEN_IST_NAIVE)

    out = ap_mod.build_aging_by_vendor([], [], [], None)
    # A UTC default would have reported "2026-06-09".
    assert out["as_of"] == "2026-06-10"
