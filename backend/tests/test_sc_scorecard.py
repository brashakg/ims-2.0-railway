"""
IMS 2.0 - SC Scorecard + Slab-Incentive Engine tests
=====================================================
Intent-level acceptance (SC-T1..SC-T12). A hollow shell that returns a 200
stub envelope fails these because every assertion checks business behaviour.

Binding corrections enforced here:
  * P0-5: slab multiplier is 1.1 @ 14% discount (NOT 1.4). Weightages sum 1.0.
  * P0-4: get_incentive_for_payroll returns the locked-snapshot total and the
          payroll feed reads ONLY that (no double-count).
  * E2  : settings resolve global -> entity -> store.
  * Product-Incentive Kicker idempotency + monthly rollup.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")


# ===========================================================================
# Fake Mongo (mirrors test_payout.py + adds find_one_and_update for the
# payroll-feed stamp guard)
# ===========================================================================


def _doc_matches(doc, filt):
    if not filt:
        return True
    for k, expected in filt.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$in" and actual not in op_val:
                    return False
                if op == "$nin" and actual in op_val:
                    return False
                if op == "$type" and actual is None:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _DupKey(Exception):
    def __init__(self, msg="duplicate key"):
        super().__init__(msg)


_DupKey.__name__ = "DuplicateKeyError"


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort = None

    def sort(self, keys):
        self._sort = keys
        return self

    def __iter__(self):
        out = list(self._docs)
        if self._sort:
            for key, direction in reversed(self._sort):
                out.sort(
                    key=lambda d, k=key: (d.get(k) is None, d.get(k)),
                    reverse=(direction == -1),
                )
        return iter(out)


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.unique_specs = []

    def add_unique(self, fields, partial_filter=None):
        self.unique_specs.append((tuple(fields), dict(partial_filter or {})))

    def insert_one(self, doc):
        for fields, partial in self.unique_specs:
            for d in self.docs:
                if not _doc_matches(d, partial) or not _doc_matches(doc, partial):
                    continue
                if all(d.get(k) == doc.get(k) for k in fields):
                    raise _DupKey()
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filt=None, projection=None):
        if not filt:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _doc_matches(d, filt):
                return d
        return None

    def find(self, filt=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filt))

    def update_one(self, filt, update):
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filt):
                d.update((update or {}).get("$set", {}) or {})
                modified += 1
                break
        return type("R", (), {"modified_count": modified, "matched_count": modified})()

    def find_one_and_update(self, filt, update, return_document=None):
        for d in self.docs:
            if _doc_matches(d, filt):
                d.update((update or {}).get("$set", {}) or {})
                return d
        return None

    def aggregate(self, pipeline):
        if not pipeline:
            return iter(self.docs)
        match = next((s.get("$match") for s in pipeline if "$match" in s), None)
        group = next((s.get("$group") for s in pipeline if "$group" in s), None)
        rows = list(self.docs)
        if match:
            rows = [r for r in rows if _doc_matches(r, match)]
        if group and group.get("_id") is None:
            out = {}
            for k, spec in group.items():
                if k == "_id":
                    continue
                if isinstance(spec, dict) and "$sum" in spec:
                    field = spec["$sum"].lstrip("$")
                    out[k] = sum(float(r.get(field) or 0) for r in rows)
            return iter([{"_id": None, **out}])
        return iter(rows)


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}
        ps = self.get_collection("payout_snapshots")
        ps.add_unique(["store_id", "year", "month"], {"status": "LOCKED"})
        pl = self.get_collection("points_log")
        pl.add_unique(["store_id", "date_str", "staff_id"], {"deleted_at": None})
        pil = self.get_collection("product_incentive_log")
        pil.add_unique(["order_id", "sku"], {"order_id": {"$type": "string"}})

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self.get_collection(name)


# ===========================================================================
# Fixtures
# ===========================================================================

STORE = "BV-TEST-01"

# Seeded multiplier table exactly as incentive_settings_repository.py:62-69.
SEEDED_MULTIPLIERS = [
    {"max_pct": 0.10, "multiplier": 1.5},
    {"max_pct": 0.11, "multiplier": 1.4},
    {"max_pct": 0.12, "multiplier": 1.3},
    {"max_pct": 0.13, "multiplier": 1.2},
    {"max_pct": 0.14, "multiplier": 1.1},
    {"max_pct": 0.15, "multiplier": 1.0},
]


@pytest.fixture
def patched(monkeypatch):
    fake_db = FakeDB()
    from api.routers import payout as payout_module
    from api.routers import points as points_module
    from api.routers import kicker as kicker_module

    for mod in (payout_module, points_module, kicker_module):
        monkeypatch.setattr(mod, "get_db", lambda: fake_db)

    from database.repositories.audit_repository import AuditRepository

    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    for mod in (payout_module, points_module, kicker_module):
        monkeypatch.setattr(mod, "get_audit_repository", lambda: audit_repo)

    def _fake_user_repo():
        class _R:
            def find_by_id(self, uid):
                return {"user_id": uid, "name": uid.replace("user-", "").upper()}

            def find_one(self, filt):
                return self.find_by_id(filt.get("user_id", ""))

        return _R()

    for mod in (payout_module, points_module, kicker_module):
        monkeypatch.setattr(mod, "get_user_repository", _fake_user_repo)

    monkeypatch.setattr(points_module, "get_walkout_repository", lambda: None)
    monkeypatch.setattr(points_module, "get_walkin_counter_repository", lambda: None)
    return {"db": fake_db, "audit_repo": audit_repo}


def _settings_doc(**overrides):
    return {
        "staff_weightages": {},
        "eligibility_bands": [
            {"min": 0, "max": 70, "value": 0.0},
            {"min": 70, "max": 80, "value": 0.6},
            {"min": 80, "max": 95, "value": 0.8},
            {"min": 95, "max": 1000, "value": 1.0},
        ],
        "growth_targets": {"L1": 0.20, "L2": 0.25, "L3": 0.30},
        "base_rates": {"L1": 0.01, "L2": 0.0125, "L3": 0.015},
        "discount_kill_threshold": 0.15,
        "discount_multipliers": list(SEEDED_MULTIPLIERS),
        "visufit_gate_threshold": 0.90,
        "visufit_gate_enabled": True,
        "supervisor_bonuses": [],
        **overrides,
    }


# ===========================================================================
# SC-T1 -- Tier math (model-of-record)
# ===========================================================================


def test_sc_t1_tier_math_bands():
    from api.services import scorecard_engine as eng

    bands = _settings_doc()["eligibility_bands"]
    assert eng.compute_eligibility(76, bands) == 0.6
    assert eng.compute_eligibility(84, bands) == 0.8
    assert eng.compute_eligibility(96, bands) == 1.0
    assert eng.compute_eligibility(69, bands) == 0.0
    # boundary: 80 lands in [80, 95)
    assert eng.compute_eligibility(80, bands) == 0.8
    # boundary: 70 lands in [70, 80)
    assert eng.compute_eligibility(70, bands) == 0.6


def test_sc_t1_nine_component_scorecard_to_tier():
    """The 9 components sum /100 and snap to a tier via the engine."""
    from api.services import scorecard_engine as eng

    # row totalling 76 (10+16+10+10+10+10+10+0+0) -> tier 0.6
    scores = {
        "attendance": 10, "conversion": 16, "task": 10, "visufit": 10,
        "punctuality": 10, "behaviour": 10, "kicker_1": 10, "kicker_2": 0,
        "reviews": 0,
    }
    row = eng.score_daily(
        raw_scores=scores, date_str="2026-01-27", staff_id="AKSHAY",
        store_id=STORE, settings=_settings_doc(),
        visufit_usage_pct_mtd=None, today_str="2026-06-08",
    )
    assert row["total"] == 76
    assert row["eligibility"] == 0.6


# ===========================================================================
# SC-T2 -- Conversion auto-calc
# ===========================================================================


def test_sc_t2_conversion_auto_calc():
    from api.services import scorecard_engine as eng

    class _WalkoutRepo:
        def list_walkouts(self, store_id, date_from, date_to, limit):
            if date_from == date_to:  # "today" window
                return [
                    {"sales_person_id": "S1", "result": "PENDING"},
                    {"sales_person_id": "S1", "result": "PENDING"},
                    {"sales_person_id": "S1", "result": "PENDING"},
                ]
            return []  # no retro

    class _WalkinRepo:
        def get_today(self, store_id, date_str):
            return {"per_staff": {"S1": 10}}

    # (10 - 3 + 0)/10 * 20 = 14
    assert eng.conversion_score(
        STORE, "2026-06-08", "S1",
        walkout_repo=_WalkoutRepo(), walkin_repo=_WalkinRepo(),
    ) == 14

    class _ZeroWalkin:
        def get_today(self, store_id, date_str):
            return {"per_staff": {}}

    assert eng.conversion_score(
        STORE, "2026-06-08", "S1",
        walkout_repo=_WalkoutRepo(), walkin_repo=_ZeroWalkin(),
    ) == 0
    # repo unavailable -> None (no auto-fill)
    assert eng.conversion_score(
        STORE, "2026-06-08", "S1", walkout_repo=None, walkin_repo=None
    ) is None


# ===========================================================================
# SC-T3 -- Slab pool (P0-5 multiplier = 1.1 @ 14%)
# ===========================================================================


def test_sc_t3_multiplier_1_1_at_14pct():
    from api.services import scorecard_engine as eng

    # THE binding correction: 14% -> 1.1 (NOT 1.4).
    assert eng.compute_multiplier(0.14, SEEDED_MULTIPLIERS, 0.15) == 1.1
    # 11% -> 1.4 (the table floor-walks ascending the OTHER way)
    assert eng.compute_multiplier(0.11, SEEDED_MULTIPLIERS, 0.15) == 1.4
    # 16% > kill 15% -> 0
    assert eng.compute_multiplier(0.16, SEEDED_MULTIPLIERS, 0.15) == 0.0


def test_sc_t3_weightages_sum_to_one_and_pool():
    from api.services import scorecard_engine as eng

    weightages = {"user-a": 0.22, "user-b": 0.27, "user-c": 0.26, "user-d": 0.25}
    assert abs(sum(weightages.values()) - 1.0) < 1e-9

    # last_year 1,672,000 -> L1=2,010,000 L2=2,090,000 L3=2,180,000
    targets = eng.compute_targets(1_672_000, {"L1": 0.20, "L2": 0.25, "L3": 0.30})
    assert targets["L1"] == 2_010_000
    # this_year 2,050,000 lands in [L1, L2) -> L1 only (best-level-only)
    best = eng.compute_best_level(2_050_000, targets)
    assert best == "L1"
    mult = eng.compute_multiplier(0.14, SEEDED_MULTIPLIERS, 0.15)
    assert mult == 1.1
    pools = eng.compute_pools(
        2_050_000, targets, {"L1": 0.01, "L2": 0.0125, "L3": 0.015}, mult, best
    )
    # max(2,050,000, 2,010,000) * 0.01 * 1.1 = 22,550
    assert abs(pools["L1"] - 22_550.0) < 1.0
    assert pools["L2"] == 0.0 and pools["L3"] == 0.0


# ===========================================================================
# SC-T4 / SC-T5 -- per-staff payout + manager bonus stacks
# ===========================================================================


def test_sc_t4_t5_payout_and_manager_bonus_stack():
    from api.services import scorecard_engine as eng

    settings = _settings_doc(
        staff_weightages={"user-rupesh": 0.22, "user-sameer": 0.27},
        supervisor_bonuses=[
            {"user_id": "user-sameer", "role": "STORE_MANAGER",
             "bonus_pct": {"L1": 0.25}},
        ],
    )
    # L1-only band: this_year 2,050,000 in [L1 2,010,000, L2 2,090,000),
    # avg_disc 0.14 -> multiplier 1.1 (P0-5). pool = 2,050,000*0.01*1.1 = 22,550
    inputs = {
        "last_year_sale": 1_672_000, "this_year_sale": 2_050_000,
        "avg_discount_pct": 0.14, "visufit_usage_pct": 0.95,
    }
    mtd = {
        "user-rupesh": {"eligibility": 1.0},
        "user-sameer": {"eligibility": 1.0},
    }
    env = eng.compute_payout(
        store_id=STORE, year=2026, month=5, settings=settings,
        inputs=inputs, mtd_data=mtd, kicker_repo=None,
    )
    assert env["multiplier"] == 1.1
    assert env["best_level_achieved"] == "L1"
    pool = env["total_team_pool"]
    assert abs(pool - 22_550.0) < 1.0  # max(2.05M, target 2.01M)*0.01*1.1

    by_uid = {s["user_id"]: s for s in env["staff_payouts"]}
    # Rupesh: pool * 0.22 * 1.0
    assert abs(by_uid["user-rupesh"]["total_payout"] - round(pool * 0.22, 2)) < 0.5
    # Sameer individual: pool * 0.27 * 1.0
    assert abs(by_uid["user-sameer"]["total_payout"] - round(pool * 0.27, 2)) < 0.5

    # Manager bonus STACKS (separate field, not one-or-the-other)
    sameer_bonus = next(
        b for b in env["manager_bonuses"] if b["user_id"] == "user-sameer"
    )
    assert abs(sameer_bonus["total_bonus"] - round(pool * 0.25, 2)) < 0.5
    # Combined per-staff total = individual + bonus
    expected_total = round(pool * 0.27, 2) + round(pool * 0.25, 2)
    assert abs(by_uid["user-sameer"]["total_with_kicker"] - expected_total) < 0.5


# ===========================================================================
# SC-T6 -- Visufit gate
# ===========================================================================


def test_sc_t6_visufit_gate():
    from api.services import scorecard_engine as eng

    scores = {
        "attendance": 10, "conversion": 20, "task": 10, "visufit": 10,
        "punctuality": 10, "behaviour": 10, "kicker_1": 10, "kicker_2": 10,
        "reviews": 10,
    }
    # usage 0.85 < 0.90 threshold, gate enabled -> visufit zeroed
    row = eng.score_daily(
        raw_scores=scores, date_str="2026-06-08", staff_id="S1", store_id=STORE,
        settings=_settings_doc(), visufit_usage_pct_mtd=0.85,
        visufit_source="clinical", today_str="2026-06-08",
    )
    assert row["visufit"] == 0
    assert row["visufit_gate_applied"] is True
    assert row["visufit_source"] == "clinical"

    # usage None -> NOT applied (no data, don't penalise)
    row2 = eng.score_daily(
        raw_scores=scores, date_str="2026-06-08", staff_id="S1", store_id=STORE,
        settings=_settings_doc(), visufit_usage_pct_mtd=None,
        today_str="2026-06-08",
    )
    assert row2["visufit"] == 10
    assert row2["visufit_gate_applied"] is False

    # usage 0.95 >= threshold -> NOT applied
    row3 = eng.score_daily(
        raw_scores=scores, date_str="2026-06-08", staff_id="S1", store_id=STORE,
        settings=_settings_doc(), visufit_usage_pct_mtd=0.95,
        today_str="2026-06-08",
    )
    assert row3["visufit"] == 10
    assert row3["visufit_gate_applied"] is False


# ===========================================================================
# SC-T7 -- Product-Incentive Kicker idempotency + rollup
# ===========================================================================


def test_sc_t7_kicker_idempotency_and_rollup(client, patched):
    from api.routers.auth import create_access_token

    token = create_access_token({
        "user_id": "mgr", "username": "mgr", "roles": ["STORE_MANAGER"],
        "store_ids": [STORE], "active_store_id": STORE,
    })
    hdr = {"Authorization": f"Bearer {token}"}
    body = {
        "staff_id": "S1", "date": "2026-06-10", "sku": "ZEISS-PAL-1.5",
        "brand": "ZEISS", "category": "PAL", "order_id": "O1",
        "incentive_amount": 500,
    }
    r = client.post("/api/v1/incentive/kicker/product-sale", json=body, headers=hdr)
    assert r.status_code == 201, r.text
    # Duplicate (order_id, sku) -> 409
    r2 = client.post("/api/v1/incentive/kicker/product-sale", json=body, headers=hdr)
    assert r2.status_code == 409, r2.text

    # Different order_id, same staff -> accepted, adds up
    body2 = dict(body, order_id="O2", incentive_amount=300)
    r3 = client.post("/api/v1/incentive/kicker/product-sale", json=body2, headers=hdr)
    assert r3.status_code == 201

    # kicker_for via engine = 800, 2 entries
    from api.services import scorecard_engine as eng
    from database.repositories.product_incentive_log_repository import (
        ProductIncentiveLogRepository,
    )

    repo = ProductIncentiveLogRepository(
        patched["db"].get_collection("product_incentive_log")
    )
    out = eng.kicker_for(STORE, "2026-06", "S1", kicker_repo=repo)
    assert out["product_incentive_amount"] == 800.0
    assert out["sale_count"] == 2

    # GET rollup endpoint
    g = client.get("/api/v1/incentive/kicker/2026-06", headers=hdr)
    assert g.status_code == 200
    assert g.json()["total"] == 800.0


# ===========================================================================
# SC-T8 -- Payroll feed: no double-count (P0-4)
# ===========================================================================


def test_sc_t8_payroll_feed_no_double_count(patched):
    from api.services import scorecard_engine as eng
    from database.repositories.payout_snapshot_repository import (
        PayoutSnapshotRepository,
    )

    db = patched["db"]
    repo = PayoutSnapshotRepository(db.get_collection("payout_snapshots"))
    repo.create_snapshot(
        {
            "store_id": STORE, "year": 2026, "month": 6,
            "staff_payouts": [
                {"user_id": "S1", "total_payout": 5000.0, "product_incentive": 500.0},
            ],
            "manager_bonuses": [],
        },
        status="LOCKED",
    )

    feed = eng.get_incentive_for_payroll(STORE, 2026, 6, snapshot_repo=repo)
    assert feed == {"S1": 5500.0}

    # First payroll run stamps the snapshot
    snap = repo.find_locked(STORE, 2026, 6)
    assert snap["payroll_fed_at"] is None
    stamped = repo.stamp_payroll_fed(snap["snapshot_id"], "RUN1:2026-06")
    assert stamped is True

    # Second run: feed total is UNCHANGED (5500, not 11000) and the stamp
    # guard refuses to re-stamp (returns False) -> no double-count.
    feed2 = eng.get_incentive_for_payroll(STORE, 2026, 6, snapshot_repo=repo)
    assert feed2 == {"S1": 5500.0}
    stamped2 = repo.stamp_payroll_fed(snap["snapshot_id"], "RUN2:2026-06")
    assert stamped2 is False

    # The old `incentives` collection is NEVER read by the feed.
    assert db.get_collection("incentives").docs == []


def test_sc_t8b_payroll_fetch_reads_only_snapshot():
    """payroll._fetch_incentive reads the snapshot feed, not the old
    `incentives` collection (P0-4). Even with a stray `incentives` row, the
    employee gets the snapshot value (or 0), never the legacy row."""
    from api.routers import payroll as payroll_module

    db = FakeDB()
    # Stray legacy incentives row that must be IGNORED.
    db.get_collection("incentives").insert_one(
        {"staff_id": "S1", "month": 6, "year": 2026, "incentive_amount": 9999.0}
    )
    from database.repositories.payout_snapshot_repository import (
        PayoutSnapshotRepository,
    )

    PayoutSnapshotRepository(db.get_collection("payout_snapshots")).create_snapshot(
        {
            "store_id": STORE, "year": 2026, "month": 6,
            "staff_payouts": [{"user_id": "S1", "total_payout": 7000.0}],
            "manager_bonuses": [],
        },
        status="LOCKED",
    )
    val = payroll_module._fetch_incentive(db, "S1", 6, 2026, store_id=STORE)
    assert val == 7000.0  # snapshot, not the 9999 legacy row
    # Employee with no snapshot line -> 0 (never the legacy 9999)
    assert payroll_module._fetch_incentive(db, "S2", 6, 2026, store_id=STORE) == 0.0


# ===========================================================================
# SC-T12 -- No locked snapshot -> payroll uses zero
# ===========================================================================


def test_sc_t12_no_snapshot_returns_empty(patched):
    from api.services import scorecard_engine as eng
    from database.repositories.payout_snapshot_repository import (
        PayoutSnapshotRepository,
    )

    repo = PayoutSnapshotRepository(patched["db"].get_collection("payout_snapshots"))
    # July has no snapshot
    assert eng.get_incentive_for_payroll(STORE, 2026, 7, snapshot_repo=repo) == {}


# ===========================================================================
# SC-T9 -- E2 settings resolution (global -> entity -> store)
# ===========================================================================


def test_sc_t9_e2_resolution(patched, monkeypatch):
    from api.services import scorecard_engine as eng
    from database.repositories.incentive_settings_repository import (
        IncentiveSettingsRepository,
    )

    db = patched["db"]
    # Make the E2 entity lookup map STORE -> E1 (so the scope chain includes
    # the entity even though we pass entity_id explicitly too).
    db.get_collection("stores").insert_one({"store_id": STORE, "entity_id": "E1"})

    settings_repo = IncentiveSettingsRepository(
        db.get_collection("incentive_settings")
    )
    coll = db.get_collection("incentive_settings")
    coll.insert_one({
        "store_id": eng.GLOBAL_SCOPE_ID, "_id": eng.GLOBAL_SCOPE_ID,
        "scope": "global", "entity_id": None, "discount_kill_threshold": 0.15,
    })
    coll.insert_one({
        "store_id": eng._entity_scope_id("E1"), "_id": eng._entity_scope_id("E1"),
        "scope": "entity", "entity_id": "E1", "discount_kill_threshold": 0.12,
    })
    coll.insert_one({
        "store_id": STORE, "_id": STORE, "scope": "store", "entity_id": "E1",
        "discount_kill_threshold": 0.10,
    })

    # Store wins
    r = eng.resolve_settings(STORE, entity_id="E1", settings_repo=settings_repo)
    assert r["discount_kill_threshold"] == 0.10
    assert r["_resolution_sources"]["discount_kill_threshold"] == "store"

    # Remove store row -> entity wins
    coll.docs = [d for d in coll.docs if d.get("store_id") != STORE]
    r2 = eng.resolve_settings(STORE, entity_id="E1", settings_repo=settings_repo)
    assert r2["discount_kill_threshold"] == 0.12
    assert r2["_resolution_sources"]["discount_kill_threshold"] == "entity"

    # Remove entity row -> global wins
    coll.docs = [d for d in coll.docs if d.get("scope") != "entity"]
    r3 = eng.resolve_settings(STORE, entity_id="E1", settings_repo=settings_repo)
    assert r3["discount_kill_threshold"] == 0.15
    assert r3["_resolution_sources"]["discount_kill_threshold"] == "global"


# ===========================================================================
# SC-T10 -- Snapshot immutability + RBAC (HTTP)
# ===========================================================================


def test_sc_t10_lock_rbac_and_immutability(client, patched):
    from api.routers.auth import create_access_token

    db = patched["db"]
    settings_repo_coll = db.get_collection("incentive_settings")
    settings_repo_coll.insert_one({"store_id": STORE, "_id": STORE, **_settings_doc(
        staff_weightages={"user-a": 1.0})})
    # eligible staff row
    db.get_collection("points_log").insert_one({
        "log_id": "PL1", "_id": "PL1", "store_id": STORE,
        "date_str": "2026-05-15", "staff_id": "user-a", "staff_name": "A",
        "attendance": 10, "conversion": 20, "task": 10, "visufit": 10,
        "punctuality": 10, "behaviour": 10, "kicker_1": 10, "kicker_2": 10,
        "reviews": 10, "total": 100, "eligibility": 1.0, "deleted_at": None,
    })

    # Non-superadmin cannot lock
    mgr = create_access_token({
        "user_id": "mgr", "username": "mgr", "roles": ["STORE_MANAGER"],
        "store_ids": [STORE], "active_store_id": STORE,
    })
    payload = {
        "year": 2026, "month": 5, "last_year_sale": 1_672_000,
        "this_year_sale": 2_210_000, "avg_discount_pct": 0.14,
    }
    r = client.post(
        "/api/v1/payout/lock", json=payload,
        headers={"Authorization": f"Bearer {mgr}"},
    )
    assert r.status_code == 403

    # SUPERADMIN locks
    admin = create_access_token({
        "user_id": "admin", "username": "admin", "roles": ["SUPERADMIN"],
        "store_ids": [STORE], "active_store_id": STORE,
    })
    ah = {"Authorization": f"Bearer {admin}"}
    r2 = client.post("/api/v1/payout/lock", json=payload, headers=ah)
    assert r2.status_code == 201, r2.text
    assert r2.json()["multiplier"] == 1.1  # P0-5 through the HTTP path
    snap_id = r2.json()["snapshot_id"]

    # Re-lock same month -> 409
    r3 = client.post("/api/v1/payout/lock", json=payload, headers=ah)
    assert r3.status_code == 409

    # Immutable read
    g = client.get(f"/api/v1/payout/snapshot/{snap_id}", headers=ah)
    assert g.status_code == 200
    assert g.json()["multiplier"] == 1.1

    # Payroll-feed endpoint reads from the locked snapshot
    feed = client.get(
        f"/api/v1/payout/payroll-feed?store_id={STORE}&year=2026&month=5",
        headers=ah,
    )
    assert feed.status_code == 200
    assert "user-a" in feed.json()["feed"]


def test_sc_t10b_payroll_feed_404_when_no_lock(client, patched):
    from api.routers.auth import create_access_token

    admin = create_access_token({
        "user_id": "admin", "username": "admin", "roles": ["SUPERADMIN"],
        "store_ids": [STORE], "active_store_id": STORE,
    })
    r = client.get(
        f"/api/v1/payout/payroll-feed?store_id={STORE}&year=2026&month=9",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 404


# ===========================================================================
# SC-T11 -- Export formula-injection safety
# ===========================================================================


def test_sc_t11_csv_formula_injection_safe(client, patched):
    from api.routers.auth import create_access_token
    from database.repositories.payout_snapshot_repository import (
        PayoutSnapshotRepository,
    )

    db = patched["db"]
    PayoutSnapshotRepository(db.get_collection("payout_snapshots")).create_snapshot(
        {
            "store_id": STORE, "year": 2026, "month": 5,
            "inputs": {"last_year_sale": 1, "this_year_sale": 1,
                       "avg_discount_pct": 0.1, "visufit_usage_pct": 0.9},
            "staff_payouts": [
                {"user_id": "u1", "name": "=cmd|'/C calc'!A0",
                 "weightage": 1.0, "eligibility": 1.0,
                 "payout_by_level": {"L1": 0, "L2": 0, "L3": 1.0},
                 "total_payout": 1.0, "product_incentive": 0.0},
            ],
            "manager_bonuses": [], "grand_total": {"staff": 1, "manager": 0, "all": 1},
            "best_level_achieved": "L3", "multiplier": 1.0,
            "discount_kill_active": False, "total_team_pool": 1.0,
        },
        status="LOCKED",
    )
    admin = create_access_token({
        "user_id": "admin", "username": "admin", "roles": ["SUPERADMIN"],
        "store_ids": [STORE], "active_store_id": STORE,
    })
    r = client.get(
        f"/api/v1/payout/export/PAY-TES-2026-05.csv",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200, r.text
    # The dangerous cell must be neutralised (prefixed with a single quote).
    assert "'=cmd" in r.text
    # And must NOT appear as a raw leading-= cell.
    assert "\n=cmd" not in r.text


# ===========================================================================
# Engine reuse contract -- no forked math
# ===========================================================================


def test_engine_reuses_calculators_not_forks():
    """The engine re-exports the existing pure calculators (same objects)."""
    from api.services import scorecard_engine as eng
    from api.services import payout_calculator as pc
    from api.services import points_calculator as ptc

    assert eng.compute_multiplier is pc.compute_multiplier
    assert eng.assemble_payout is pc.assemble_payout
    assert eng.compute_eligibility is ptc.compute_eligibility
    assert eng.aggregate_mtd is ptc.aggregate_mtd
