"""F34 Global Target Ticker (#34) -- INTENT-LEVEL tests.

The intent:
  (a) management roles get rupees + pct; a floor role gets pct ONLY (no
      mtd_revenue/monthly_target/pace keys in the payload).
  (b) no REVENUE budget for the month -> no_target:true, no fabricated number.
  (c) ORACLE._check_milestones fires a bell to floor users when MTD crosses a
      threshold, does NOT re-fire the same month, resets on month rollover, and
      notifies NO management users.
  (d) fail-soft when the DB / budgets collection is absent.

Pure logic is tested directly; the agent path uses a tiny in-memory Mongo
fake so the suite needs no mongod (mirrors backend/tests fake-DB patterns).
No emoji (Windows cp1252).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import ticker_service as ts  # noqa: E402


# --------------------------------------------------------------------------- #
# In-memory Mongo fake (only the ops F34 uses)
# --------------------------------------------------------------------------- #


def _match(doc, flt):
    """Tiny subset of Mongo query matching used by the fake."""
    for k, cond in flt.items():
        v = doc.get(k)
        if isinstance(cond, dict):
            if "$ne" in cond and v == cond["$ne"]:
                return False
            if "$nin" in cond and v in cond["$nin"]:
                return False
            if "$in" in cond:
                # membership; v may be a scalar or a list (e.g. roles, store_ids)
                hay = v if isinstance(v, list) else [v]
                if not any(x in cond["$in"] for x in hay):
                    return False
            if "$gte" in cond and not (v is not None and v >= cond["$gte"]):
                return False
        else:
            # equality; if the stored field is a list (store_ids), test membership
            if isinstance(v, list):
                if cond not in v:
                    return False
            elif v != cond:
                return False
    return True


class _Result:
    def __init__(self, modified):
        self.modified_count = modified
        self.matched_count = modified


class FakeColl:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]
        self.inserted = []

    def find(self, flt=None, _proj=None):
        flt = flt or {}
        return [dict(d) for d in self.docs if _match(d, flt)]

    def find_one(self, flt=None, _proj=None):
        for d in self.docs:
            if _match(d, flt or {}):
                return dict(d)
        return None

    def update_one(self, flt, update):
        for d in self.docs:
            if _match(d, flt):
                if "$addToSet" in update:
                    for k, val in update["$addToSet"].items():
                        cur = d.setdefault(k, [])
                        if val not in cur:
                            cur.append(val)
                if "$set" in update:
                    d.update(update["$set"])
                return _Result(1)
        return _Result(0)

    def insert_many(self, docs, ordered=True):  # noqa: ARG002
        self.inserted.extend(dict(d) for d in docs)
        return None

    def aggregate(self, pipeline):
        # Only the MTD revenue pipeline: $match then $group sum of revenue expr.
        match = {}
        for stage in pipeline:
            if "$match" in stage:
                match = stage["$match"]
        rows = [d for d in self.docs if _match(d, match)]
        total = 0.0
        for r in rows:
            total += float(r.get("grand_total") or r.get("total") or 0)
        return [{"_id": None, "total_revenue": total}] if rows else []


class FakeDB:
    def __init__(self):
        self.colls = {}

    def add(self, name, docs):
        self.colls[name] = FakeColl(docs)
        return self.colls[name]

    def get_collection(self, name):
        if name not in self.colls:
            self.colls[name] = FakeColl([])
        return self.colls[name]


# --------------------------------------------------------------------------- #
# (a) privacy stratification -- pure logic
# --------------------------------------------------------------------------- #


def _full_entry(target=1_000_000, mtd=560_000):
    return ts.compute_store_entry(
        store_id="BV-01", store_name="Bokaro", monthly_target=target, mtd=mtd,
        days_elapsed=14, days_in_month=30, milestones_fired=[25, 50])


def test_management_roles_see_rupees_and_pct():
    assert ts.raw_visible_for({"roles": ["STORE_MANAGER"]}) is True
    for r in ("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"):
        assert ts.raw_visible_for({"activeRole": r}) is True, r
    e = _full_entry()
    assert e["monthly_target"] == 1_000_000
    assert e["mtd_revenue"] == 560_000
    assert e["pct_complete"] == 56.0
    assert "pace_revenue" in e and "pace_delta" in e


def test_floor_role_sees_pct_only_no_rupees():
    for r in ("SALES_CASHIER", "SALES_STAFF", "CASHIER", "OPTOMETRIST",
              "WORKSHOP_STAFF", "CATALOG_MANAGER"):
        assert ts.raw_visible_for({"roles": [r]}) is False, r
    masked = ts.mask_entry(_full_entry())
    # pct + progress fields survive
    assert masked["pct_complete"] == 56.0
    assert "milestones_fired" in masked and "no_target" in masked
    # every rupee key + the store breakdown is ABSENT (not null)
    for k in ("monthly_target", "mtd_revenue", "pace_revenue", "pace_delta", "store_name"):
        assert k not in masked, k


def test_pace_line_is_correct():
    # target 1,000,000 | mtd 400,000 | day 14 of 30 -> pace 466,667 (rounded);
    # behind by 66,667.
    e = ts.compute_store_entry(
        store_id="s", store_name="n", monthly_target=1_000_000, mtd=400_000,
        days_elapsed=14, days_in_month=30)
    assert e["pace_revenue"] == 466667
    assert e["pace_delta"] == -66667
    assert e["pct_complete"] == 40.0


# --------------------------------------------------------------------------- #
# (b) no-target -> no fabricated number
# --------------------------------------------------------------------------- #


def test_no_target_is_greyed_not_fabricated():
    e = ts.compute_store_entry(
        store_id="s", store_name="n", monthly_target=None, mtd=12_345,
        days_elapsed=10, days_in_month=31)
    assert e["no_target"] is True
    assert e["monthly_target"] is None      # never fabricated
    assert e["pct_complete"] == 0.0
    # a zero/negative target is also treated as "no target"
    z = ts.compute_store_entry(
        store_id="s", store_name="n", monthly_target=0, mtd=5_000,
        days_elapsed=1, days_in_month=30)
    assert z["no_target"] is True and z["monthly_target"] is None


def test_crossed_milestones_logic():
    assert ts.crossed_milestones(60.0, [25, 50, 75, 100], [25]) == [50]
    assert ts.crossed_milestones(49.0, [25, 50, 75, 100], [25]) == []
    assert ts.crossed_milestones(100.0, [25, 50, 75, 100], []) == [25, 50, 75, 100]
    # already fired -> not returned again
    assert ts.crossed_milestones(80.0, [25, 50, 75], [25, 50, 75]) == []


# --------------------------------------------------------------------------- #
# (c) ORACLE milestone bell -- fires once, floor-only, resets on rollover
# --------------------------------------------------------------------------- #


def _oracle_with(db):
    from agents.implementations.oracle import OracleAgent
    return OracleAgent(db=db)


def _run(coro):
    """Run a coroutine to completion (asyncio.run; no reliance on a current
    loop, which Python 3.14 no longer auto-creates)."""
    return asyncio.run(coro)


def _orders_for(store_id, total):
    """One booked order whose created_at is inside the current IST month."""
    month_start, _, _ = ts._month_bounds()
    return {"store_id": store_id, "status": "CONFIRMED",
            "grand_total": total, "created_at": month_start}


def _users():
    return [
        {"user_id": "cashier1", "roles": ["SALES_CASHIER"], "store_ids": ["BV-01"], "is_active": True},
        {"user_id": "staff1", "roles": ["SALES_STAFF"], "store_ids": ["BV-01"], "is_active": True},
        {"user_id": "mgr1", "roles": ["STORE_MANAGER"], "store_ids": ["BV-01"], "is_active": True},
        {"user_id": "area1", "roles": ["AREA_MANAGER"], "store_ids": ["BV-01"], "is_active": True},
    ]


def test_milestone_fires_once_floor_only_and_not_refire(monkeypatch):
    monkeypatch.setattr(
        "api.services.policy_engine.get_policy",
        lambda key, scope=None, default=None: [25, 50, 75, 100] if "milestone" in key else default,
    )
    period = ts.current_period()
    db = FakeDB()
    budgets = db.add("budgets", [
        {"store_id": "BV-01", "period": period, "head": "REVENUE",
         "planned_amount": 1_000_000, "milestones_fired": [25]},
    ])
    notif = db.add("notifications", [])
    db.add("users", _users())

    oracle = _oracle_with(db)

    # MTD at 49% -> no new milestone (50 not yet crossed)
    db.add("orders", [_orders_for("BV-01", 490_000)])
    fired = _run(oracle._check_milestones())
    assert fired == 0
    assert notif.inserted == []

    # MTD crosses 51% -> exactly one 50% milestone, bell to FLOOR staff only
    db.add("orders", [_orders_for("BV-01", 510_000)])
    fired = _run(oracle._check_milestones())
    assert fired == 1
    recipients = {n["user_id"] for n in notif.inserted}
    assert recipients == {"cashier1", "staff1"}        # floor only
    assert "mgr1" not in recipients and "area1" not in recipients
    assert all(n["type"] == "TARGET_MILESTONE" for n in notif.inserted)
    assert 50 in budgets.docs[0]["milestones_fired"]

    # Tick again still at 60% -> milestone already fired, NO new notification
    before = len(notif.inserted)
    db.add("orders", [_orders_for("BV-01", 600_000)])
    fired = _run(oracle._check_milestones())
    assert fired == 0
    assert len(notif.inserted) == before


def test_month_rollover_resets_milestones(monkeypatch):
    monkeypatch.setattr(
        "api.services.policy_engine.get_policy",
        lambda key, scope=None, default=None: [25, 50, 75, 100] if "milestone" in key else default,
    )
    db = FakeDB()
    # A budget from a PRIOR month with dirty milestones_fired.
    budgets = db.add("budgets", [
        {"store_id": "BV-01", "period": "2000-01", "head": "REVENUE",
         "planned_amount": 500_000, "milestones_fired": [25, 50, 75, 100]},
    ])
    db.add("notifications", [])
    db.add("users", _users())
    db.add("orders", [])
    oracle = _oracle_with(db)
    _run(oracle._check_milestones())
    # The prior-period doc is reset to [] (new month starts clean).
    assert budgets.docs[0]["milestones_fired"] == []


# --------------------------------------------------------------------------- #
# (d) fail-soft when DB / budgets absent
# --------------------------------------------------------------------------- #


def test_check_milestones_failsoft_no_db():
    oracle = _oracle_with(None)  # db None -> get_collection returns None
    fired = _run(oracle._check_milestones())
    assert fired == 0  # no crash, no notifications


def test_mtd_revenue_failsoft_none_collection():
    assert ts.mtd_revenue(None, "BV-01") == 0.0


# --------------------------------------------------------------------------- #
# HTTP-level: the GET is reachable by floor roles (NOT 403 at the router) AND
# the privacy wall is server-enforced. Runs against the real app via TestClient;
# with no DB it takes the fail-soft path -- which is exactly where we still
# assert the stratification because raw_visible is decided from the JWT alone.
# --------------------------------------------------------------------------- #


def _token(roles):
    from api.routers.auth import create_access_token
    return create_access_token({
        "user_id": "u-" + roles[0].lower(), "username": "u", "roles": roles,
        "store_ids": ["BV-TEST-01"], "active_store_id": "BV-TEST-01",
    })


def _get_ticker(client, roles):
    r = client.get("/api/v1/finance/target-ticker",
                   headers={"Authorization": f"Bearer {_token(roles)}"})
    return r


def test_floor_role_not_403_and_pct_only(client):
    # SALES_CASHIER must NOT be blocked by the finance router role gate.
    r = _get_ticker(client, ["SALES_CASHIER"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["raw_visible"] is False
    for store in body["stores"]:
        assert "pct_complete" in store
        # money keys never sent to a floor role
        for k in ("monthly_target", "mtd_revenue", "pace_revenue", "pace_delta"):
            assert k not in store, k


def test_management_role_gets_raw(client):
    r = _get_ticker(client, ["STORE_MANAGER"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["raw_visible"] is True
    assert "ticker_refresh_seconds" in body
    for store in body["stores"]:
        # raw entries carry the money keys + no_target flag (never fabricated)
        assert "pct_complete" in store and "no_target" in store


def test_settings_post_requires_admin(client):
    # SALES_CASHIER is blocked (router gate 403s before the handler).
    payload = {"milestone_pcts": [25, 50, 75, 100], "refresh_seconds": 120}
    r = client.post("/api/v1/finance/target-ticker/settings", json=payload,
                    headers={"Authorization": f"Bearer {_token(['SALES_CASHIER'])}"})
    assert r.status_code == 403
