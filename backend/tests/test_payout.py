"""
IMS 2.0 — Payout Router + Calculator tests (Pune Incentive Module iii)
=======================================================================
Coverage map (per docs/PUNE_INCENTIVE_BUILD_PLAN.md §"Phased plan"):

  P1 — calculator pure functions + GET /preview
  P2 — POST /lock + 409 + mark-paid + audit
  P3 — CSV export + settings PATCH

Plus the critical Pune May-26 cross-check that asserts every value
matches Excel ±₹1.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Test fakes — Mongo emulator with $set + unique-partial-index hooks
# ============================================================================


def _doc_matches(doc, filter):
    if not filter:
        return True
    for k, expected in filter.items():
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
                out.sort(key=lambda d, k=key: (d.get(k) is None, d.get(k)),
                         reverse=(direction == -1))
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
                if not _doc_matches(d, partial):
                    continue
                if not _doc_matches(doc, partial):
                    continue
                if all(d.get(k) == doc.get(k) for k in fields):
                    raise _DupKey()
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()
    def find_one(self, filter=None, projection=None):
        if not filter:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _doc_matches(d, filter):
                return d
        return None
    def find(self, filter=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter))
    def update_one(self, filter, update):
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filter):
                d.update((update or {}).get("$set", {}) or {})
                modified += 1
                break
        return type("R", (), {"modified_count": modified, "matched_count": modified})()
    def aggregate(self, pipeline):
        # Tiny aggregation handler for the orders → sales path
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
        # Unique partial index on payout_snapshots (status=LOCKED)
        ps = self.get_collection("payout_snapshots")
        ps.add_unique(
            ["store_id", "year", "month"],
            {"status": "LOCKED"},
        )
        # Unique partial index on points_log
        pl = self.get_collection("points_log")
        pl.add_unique(
            ["store_id", "date_str", "staff_id"],
            {"deleted_at": None},
        )
    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]
    def __getattr__(self, name):
        return self.get_collection(name)


@pytest.fixture
def patched_payout(monkeypatch):
    """Wire fake DB into payout + points routers."""
    fake_db = FakeDB()

    from api.routers import payout as payout_module
    from api.routers import points as points_module
    monkeypatch.setattr(payout_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(points_module, "get_db", lambda: fake_db)

    # Audit
    from database.repositories.audit_repository import AuditRepository
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(payout_module, "get_audit_repository", lambda: audit_repo)
    monkeypatch.setattr(points_module, "get_audit_repository", lambda: audit_repo)

    # Users
    def _fake_user_repo():
        class _R:
            def find_by_id(self, uid):
                return {"user_id": uid, "name": uid.replace("user-", "").upper()}
            def find_one(self, filter):
                return self.find_by_id(filter.get("user_id", ""))
        return _R()
    monkeypatch.setattr(payout_module, "get_user_repository", _fake_user_repo)
    monkeypatch.setattr(points_module, "get_user_repository", _fake_user_repo)

    # Walkouts/walkin (for points conversion auto-fill, irrelevant to payout)
    monkeypatch.setattr(points_module, "get_walkout_repository", lambda: None)
    monkeypatch.setattr(points_module, "get_walkin_counter_repository", lambda: None)

    return {"db": fake_db, "audit_repo": audit_repo}


# ============================================================================
# Helpers
# ============================================================================


PUNE_FIXTURE = json.load(open(
    os.path.join(os.path.dirname(__file__), "fixtures", "pune_may26_cross_check.json")
))


def _seed_settings(fake_db, store_id="BV-TEST-01", **overrides):
    """Drop the Pune fixture's settings into incentive_settings for store."""
    coll = fake_db.get_collection("incentive_settings")
    doc = {
        "_id": store_id, "store_id": store_id,
        **PUNE_FIXTURE["settings"],
        **overrides,
    }
    existing = coll.find_one({"store_id": store_id})
    if existing:
        coll.update_one({"store_id": store_id}, {"$set": doc})
    else:
        coll.insert_one(doc)


def _seed_eligible_staff(fake_db, store_id="BV-TEST-01", year=2026, month=5):
    """Seed enough points_log rows that every staff in the fixture
    weightages map ends up with eligibility=1.0 in MTD aggregation."""
    coll = fake_db.get_collection("points_log")
    base_scores = dict(
        attendance=10, conversion=20, task=10, visufit=10, punctuality=10,
        behaviour=10, kicker_1=10, kicker_2=10, reviews=10,
    )
    bands = PUNE_FIXTURE["settings"].get("eligibility_bands") or [
        {"min": 95, "max": 1000, "value": 1.0},
    ]
    # Pick a date in the target month
    for staff_id in PUNE_FIXTURE["settings"]["staff_weightages"]:
        coll.insert_one({
            "log_id": f"PL-fix-{staff_id}",
            "_id": f"PL-fix-{staff_id}",
            "store_id": store_id,
            "date_str": f"{year:04d}-{month:02d}-15",
            "staff_id": staff_id,
            "staff_name": staff_id.replace("user-", "").upper(),
            **base_scores,
            "total": sum(base_scores.values()),
            "eligibility": 1.0,
            "eligibility_thresholds_used": {"bands": bands},
            "deleted_at": None,
        })


# ============================================================================
# Calculator pure-function tests
# ============================================================================


def test_calc_compute_targets_rounds_up_to_10k():
    from api.services.payout_calculator import compute_targets
    out = compute_targets(1838000, {"L1": 0.20, "L2": 0.25, "L3": 0.30})
    assert out == {"L1": 2210000, "L2": 2300000, "L3": 2390000}


def test_calc_multiplier_floor_rounding():
    """floor(pct*100)/100 — 11.99% must floor to 11% bucket → 1.4×
    (NOT 1.3×). Spec example from the Pune team."""
    from api.services.payout_calculator import compute_multiplier
    multipliers = PUNE_FIXTURE["settings"]["discount_multipliers"]
    # 10.99% floors to 10% → 1.5× (still in the smallest bucket)
    assert compute_multiplier(0.1099, multipliers, 0.15) == 1.5
    # 11.99% floors to 11% → 1.4× (the canonical Pune example)
    assert compute_multiplier(0.1199, multipliers, 0.15) == 1.4
    # Sanity: clean integers walk the tiers as expected
    assert compute_multiplier(0.10, multipliers, 0.15) == 1.5
    assert compute_multiplier(0.11, multipliers, 0.15) == 1.4
    assert compute_multiplier(0.12, multipliers, 0.15) == 1.3
    assert compute_multiplier(0.15, multipliers, 0.15) == 1.0


def test_calc_discount_kill_zeroes_multiplier():
    from api.services.payout_calculator import compute_multiplier
    multipliers = PUNE_FIXTURE["settings"]["discount_multipliers"]
    # 16% > 15% kill threshold → multiplier=0
    assert compute_multiplier(0.16, multipliers, 0.15) == 0


def test_calc_best_level_walks_down():
    from api.services.payout_calculator import compute_best_level
    targets = {"L1": 2210000, "L2": 2300000, "L3": 2390000}
    assert compute_best_level(2_700_000, targets) == "L3"
    assert compute_best_level(2_350_000, targets) == "L2"
    assert compute_best_level(2_220_000, targets) == "L1"
    assert compute_best_level(2_000_000, targets) is None


def test_calc_pools_best_level_only():
    """Hitting L3 → only L3 pool nonzero (winner-takes-level)."""
    from api.services.payout_calculator import compute_pools
    targets = {"L1": 2210000, "L2": 2300000, "L3": 2390000}
    rates = {"L1": 0.01, "L2": 0.0125, "L3": 0.015}
    pools = compute_pools(2_600_000, targets, rates, 1.5, "L3")
    assert pools["L3"] == 58_500.0
    assert pools["L1"] == 0.0 and pools["L2"] == 0.0


def test_calc_pools_uses_max_of_actual_and_target():
    """Pool base = max(this_year_sale, target[best])."""
    from api.services.payout_calculator import compute_pools
    targets = {"L1": 2210000, "L2": 2300000, "L3": 2390000}
    rates = {"L1": 0.01, "L2": 0.0125, "L3": 0.015}
    # Sale 2_400_000 < L3 target 2_390_000? No — actually it's higher.
    # Use 2_395_000 which is just above target → max(2_395_000, 2_390_000)
    pools = compute_pools(2_395_000, targets, rates, 1.5, "L3")
    # 2_395_000 * 0.015 * 1.5 = 53_887.5
    assert pools["L3"] == 53_887.5


def test_calc_individual_payout_uses_eligibility():
    from api.services.payout_calculator import compute_individual_payouts
    pools = {"L1": 0, "L2": 0, "L3": 58500.0}
    weightages = {"user-akshay": 0.24, "user-rupesh": 0.18}
    mtd = {
        "user-akshay": {"eligibility": 1.0},
        "user-rupesh": {"eligibility": 0.6},
    }
    out = compute_individual_payouts(pools, weightages, mtd)
    by_uid = {r["user_id"]: r for r in out}
    # akshay: 58500 * 0.24 * 1.0 = 14040
    assert by_uid["user-akshay"]["total_payout"] == 14040.0
    # rupesh: 58500 * 0.18 * 0.6 = 6318
    assert by_uid["user-rupesh"]["total_payout"] == 6318.0


def test_calc_manager_bonus_uses_own_eligibility():
    from api.services.payout_calculator import compute_manager_bonuses
    pools = {"L1": 0, "L2": 0, "L3": 58500.0}
    sups = [{
        "user_id": "user-sameer", "role": "STORE_MANAGER",
        "bonus_pct": {"L1": 0.25, "L2": 0.30, "L3": 0.35},
    }]
    # sameer's eligibility=0.8 → 58500 * 0.35 * 0.8 = 16380
    out = compute_manager_bonuses(
        pools, sups, {"user-sameer": {"eligibility": 0.8}},
    )
    assert out[0]["total_bonus"] == 16380.0


def test_payout_matches_excel_pune_may26():
    """Critical cross-check: every output ±₹1 of the Excel calc."""
    from api.services.payout_calculator import assemble_payout
    inputs = PUNE_FIXTURE["inputs"]
    settings = PUNE_FIXTURE["settings"]
    expected = PUNE_FIXTURE["expected"]
    mtd = {sid: {"eligibility": 1.0}
           for sid in settings["staff_weightages"]}
    env = assemble_payout(inputs=inputs, settings=settings, mtd_data=mtd)
    assert env["targets"]["L1"]["target"] == expected["targets"]["L1"]
    assert env["targets"]["L2"]["target"] == expected["targets"]["L2"]
    assert env["targets"]["L3"]["target"] == expected["targets"]["L3"]
    assert env["best_level_achieved"] == expected["best_level"]
    assert env["discount_kill_active"] == expected["discount_kill_active"]
    assert env["multiplier"] == expected["multiplier"]
    assert env["multiplier_tier"] == expected["multiplier_tier"]
    assert abs(env["pools"]["L3"] - expected["pool_l3"]) < 1.0
    assert abs(env["total_team_pool"] - expected["pool_total"]) < 1.0
    # Sum of staff payouts should equal pool (every staff at eligibility=1.0
    # and weightages sum to 1.0)
    staff_total = sum(p["total_payout"] for p in env["staff_payouts"])
    assert abs(staff_total - expected["pool_total"]) < 1.0


def test_calc_assemble_discount_killed_returns_zero_pools():
    from api.services.payout_calculator import assemble_payout
    inputs = dict(PUNE_FIXTURE["inputs"])
    inputs["avg_discount_pct"] = 0.16  # over kill threshold
    settings = PUNE_FIXTURE["settings"]
    mtd = {sid: {"eligibility": 1.0}
           for sid in settings["staff_weightages"]}
    env = assemble_payout(inputs=inputs, settings=settings, mtd_data=mtd)
    assert env["discount_kill_active"] is True
    assert env["multiplier"] == 0
    assert env["total_team_pool"] == 0
    assert env["multiplier_tier"] == "KILLED"


# ============================================================================
# /preview endpoint
# ============================================================================


def test_preview_pune_may26_pool_58500(client, auth_headers, patched_payout):
    fake_db = patched_payout["db"]
    _seed_settings(fake_db)
    _seed_eligible_staff(fake_db, year=2026, month=5)
    resp = client.get(
        "/api/v1/payout/preview"
        "?year=2026&month=5"
        "&last_year_sale=1838000&this_year_sale=2600000"
        "&avg_discount_pct=0.10&visufit_usage_pct=0.94",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["best_level_achieved"] == "L3"
    assert body["multiplier"] == 1.5
    assert abs(body["total_team_pool"] - 58500.0) < 1.0
    # Akshay: 58500 * 0.24 * 1.0 = 14040
    akshay = next(s for s in body["staff_payouts"] if s["user_id"] == "user-akshay")
    assert abs(akshay["total_payout"] - 14040.0) < 1.0
    # Sameer manager bonus: 58500 * 0.35 * 1.0 = 20475
    sameer = next(b for b in body["manager_bonuses"] if b["user_id"] == "user-sameer")
    assert abs(sameer["total_bonus"] - 20475.0) < 1.0


def test_preview_discount_kill_zero(client, auth_headers, patched_payout):
    fake_db = patched_payout["db"]
    _seed_settings(fake_db)
    _seed_eligible_staff(fake_db, year=2026, month=5)
    resp = client.get(
        "/api/v1/payout/preview"
        "?year=2026&month=5"
        "&last_year_sale=1838000&this_year_sale=2600000"
        "&avg_discount_pct=0.16",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["discount_kill_active"] is True
    assert body["total_team_pool"] == 0


def test_preview_blocked_for_sales_staff(client, patched_payout):
    """View permissions: managers/admin/accountant only."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "user-x", "username": "x",
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-TEST-01"], "active_store_id": "BV-TEST-01",
    })
    resp = client.get(
        "/api/v1/payout/preview?year=2026&month=5",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ============================================================================
# /lock endpoint
# ============================================================================


def test_lock_creates_locked_snapshot(client, auth_headers, patched_payout):
    fake_db = patched_payout["db"]
    _seed_settings(fake_db)
    _seed_eligible_staff(fake_db, year=2026, month=5)
    resp = client.post(
        "/api/v1/payout/lock",
        json={
            "year": 2026, "month": 5,
            "last_year_sale": 1838000, "this_year_sale": 2600000,
            "avg_discount_pct": 0.10, "visufit_usage_pct": 0.94,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "LOCKED"
    assert body["snapshot_id"] == "PAY-TES-2026-05"
    assert body["locked_by"] == "test-admin-001"
    assert body["paid_at"] is None
    audits = patched_payout["audit_repo"].collection.docs
    assert any(a.get("action") == "payout.lock" for a in audits)


def test_lock_409_on_duplicate(client, auth_headers, patched_payout):
    fake_db = patched_payout["db"]
    _seed_settings(fake_db)
    _seed_eligible_staff(fake_db, year=2026, month=5)
    payload = {
        "year": 2026, "month": 5,
        "last_year_sale": 1838000, "this_year_sale": 2600000,
        "avg_discount_pct": 0.10,
    }
    r = client.post("/api/v1/payout/lock", json=payload, headers=auth_headers)
    assert r.status_code == 201
    r = client.post("/api/v1/payout/lock", json=payload, headers=auth_headers)
    assert r.status_code == 409


def test_lock_blocked_for_non_superadmin(
    client, patched_payout
):
    """Even STORE_MANAGER can't lock (SUPERADMIN-only write)."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "user-mgr", "username": "mgr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-TEST-01"], "active_store_id": "BV-TEST-01",
    })
    resp = client.post(
        "/api/v1/payout/lock",
        json={"year": 2026, "month": 5, "last_year_sale": 1, "this_year_sale": 1, "avg_discount_pct": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ============================================================================
# mark-paid + listing + CSV
# ============================================================================


def _create_locked(client, auth_headers, patched_payout, year=2026, month=5):
    fake_db = patched_payout["db"]
    _seed_settings(fake_db)
    _seed_eligible_staff(fake_db, year=year, month=month)
    r = client.post(
        "/api/v1/payout/lock",
        json={
            "year": year, "month": month,
            "last_year_sale": 1838000, "this_year_sale": 2600000,
            "avg_discount_pct": 0.10,
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    return r.json()


def test_mark_paid_flips_status_and_audits(
    client, auth_headers, patched_payout
):
    snap = _create_locked(client, auth_headers, patched_payout)
    r = client.patch(
        f"/api/v1/payout/snapshot/{snap['snapshot_id']}/mark-paid",
        json={"note": "Disbursed via NEFT"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "PAID"
    assert body["paid_at"]
    audits = patched_payout["audit_repo"].collection.docs
    assert any(a.get("action") == "payout.mark_paid" for a in audits)


def test_mark_paid_409_if_not_locked(
    client, auth_headers, patched_payout
):
    snap = _create_locked(client, auth_headers, patched_payout)
    # First mark-paid → 200, then re-mark → 409
    client.patch(
        f"/api/v1/payout/snapshot/{snap['snapshot_id']}/mark-paid",
        json={}, headers=auth_headers,
    )
    r = client.patch(
        f"/api/v1/payout/snapshot/{snap['snapshot_id']}/mark-paid",
        json={}, headers=auth_headers,
    )
    assert r.status_code == 409


def test_list_snapshots_for_year(client, auth_headers, patched_payout):
    _create_locked(client, auth_headers, patched_payout, year=2026, month=5)
    _create_locked(client, auth_headers, patched_payout, year=2026, month=6)
    r = client.get("/api/v1/payout/snapshots?year=2026", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    months = sorted([i["month"] for i in items])
    assert months == [5, 6]


def test_csv_export_renders(client, auth_headers, patched_payout):
    snap = _create_locked(client, auth_headers, patched_payout)
    r = client.get(
        f"/api/v1/payout/export/{snap['snapshot_id']}.csv",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.text
    assert "Pune Incentive Payout" in body
    assert "user-akshay" in body or "AKSHAY" in body
    assert "Total team pool" in body


# ============================================================================
# Settings PATCH endpoints (live on the points router)
# ============================================================================


def test_settings_payout_patch_superadmin_only(
    client, patched_payout
):
    """STORE_MANAGER cannot tune the calculator."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "user-mgr", "username": "mgr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-TEST-01"], "active_store_id": "BV-TEST-01",
    })
    r = client.patch(
        "/api/v1/incentive/points/settings/payout",
        json={"discount_kill_threshold": 0.20},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_settings_payout_patch_updates_fields(
    client, auth_headers, patched_payout
):
    """SUPERADMIN can patch growth/rates/multipliers/weightages."""
    r = client.patch(
        "/api/v1/incentive/points/settings/payout",
        json={
            "growth_targets": {"L1": 0.15, "L2": 0.20, "L3": 0.25},
            "discount_kill_threshold": 0.18,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["growth_targets"]["L3"] == 0.25
    assert body["discount_kill_threshold"] == 0.18


def test_last_year_sale_input_persists(
    client, auth_headers, patched_payout
):
    """Manual last-year-sale POST gets picked up by /preview."""
    r = client.post(
        "/api/v1/incentive/points/inputs/last-year-sale",
        json={"year": 2026, "month": 5, "last_year_sale": 2_000_000},
        headers=auth_headers,
    )
    assert r.status_code == 201
    # Now preview without the override should pick this up
    fake_db = patched_payout["db"]
    _seed_settings(fake_db)
    _seed_eligible_staff(fake_db, year=2026, month=5)
    r = client.get(
        "/api/v1/payout/preview?year=2026&month=5"
        "&this_year_sale=2700000&avg_discount_pct=0.10",
        headers=auth_headers,
    )
    assert r.status_code == 200
    inp = r.json()["inputs"]
    assert inp["last_year_sale"] == 2_000_000
    # Targets are 1.20× / 1.25× / 1.30× of 2,000,000 → 2.4M / 2.5M / 2.6M
    targets = r.json()["targets"]
    assert targets["L1"]["target"] == 2_400_000
    assert targets["L3"]["target"] == 2_600_000
