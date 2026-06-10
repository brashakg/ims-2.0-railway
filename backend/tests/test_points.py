"""
IMS 2.0 — Daily Points router tests (Pune Incentive Module ii)
================================================================
Coverage map (per docs/PUNE_INCENTIVE_BUILD_PLAN.md §"Phased plan"):

  P1 — schema + single POST + GET + 409
  P2 — bulk + delete + conversion auto-fill from Module (i)
  P3 — MTD + leaderboard + Visufit gate edge cases
  P4 — eligibility settings PATCH (SUPERADMIN-only)
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Test fakes — minimal Mongo emulator with $set + unique-index hooks
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
        else:
            if actual != expected:
                return False
    return True


class _DupKey(Exception):
    """Stand-in for pymongo.errors.DuplicateKeyError. The router checks
    by class name OR by 'duplicate key' substring, so this works."""
    def __init__(self, msg="duplicate key"):
        super().__init__(msg)


# Make the class look like pymongo's by name match
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
    """Like the walkouts FakeCollection but adds a per-collection
    unique-key tuple list so we can simulate the points_log unique
    partial index on (store_id, date_str, staff_id) where
    deleted_at=None."""

    def __init__(self, unique_keys=None):
        self.docs = []
        # list of (key_tuple_def, partial_filter)
        self.unique_specs = unique_keys or []

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
                set_block = (update or {}).get("$set", {}) or {}
                d.update(set_block)
                modified += 1
                break
        return type("R", (), {"modified_count": modified, "matched_count": modified})()


class FakeDB:
    is_connected = True
    def __init__(self):
        self._collections = {}
        # Pre-register the unique partial index on points_log
        pl = self.get_collection("points_log")
        pl.add_unique(
            ["store_id", "date_str", "staff_id"], {"deleted_at": None}
        )
    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]
    def __getattr__(self, name):
        return self.get_collection(name)


@pytest.fixture
def patched_points(monkeypatch):
    """Wire fake DB + repos into the points router."""
    fake_db = FakeDB()

    from api.routers import points as points_module
    monkeypatch.setattr(points_module, "get_db", lambda: fake_db)

    # Audit
    from database.repositories.audit_repository import AuditRepository
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(points_module, "get_audit_repository", lambda: audit_repo)

    # Users
    def _fake_user_repo():
        class _R:
            def find_by_id(self, uid):
                return {"user_id": uid, "name": f"User-{uid}"}
            def find_one(self, filter):
                return self.find_by_id(filter.get("user_id", ""))
        return _R()
    monkeypatch.setattr(points_module, "get_user_repository", _fake_user_repo)

    # Walkouts (for the conversion auto-fill path)
    from database.repositories.walkout_repository import WalkoutRepository
    walkout_repo = WalkoutRepository(fake_db.get_collection("walkouts"))
    monkeypatch.setattr(points_module, "get_walkout_repository", lambda: walkout_repo)

    from database.repositories.walkin_counter_repository import WalkInCounterRepository
    walkin_repo = WalkInCounterRepository(fake_db.get_collection("walk_in_counters"))
    monkeypatch.setattr(
        points_module, "get_walkin_counter_repository", lambda: walkin_repo
    )

    return {
        "db": fake_db,
        "audit_repo": audit_repo,
        "walkout_repo": walkout_repo,
        "walkin_repo": walkin_repo,
    }


@pytest.fixture
def frozen_points_now(monkeypatch):
    """Freeze the points router clock to a fixed MID-MONTH date so the MTD /
    staff-history window tests are deterministic. Those tests seed rows at
    `today - N days`; on the 1st/2nd of a month the earlier days fall in the
    PREVIOUS month, so the window endpoints (which key off datetime.now().month)
    see fewer rows -> a real calendar-boundary flake (e.g. days_logged 1 != 3 on
    the 1st). Returns the frozen `date`."""
    from datetime import datetime as _dt
    from api.routers import points as points_module

    frozen = _dt(2026, 6, 15, 12, 0, 0)

    class _FrozenDateTime(_dt):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(points_module, "datetime", _FrozenDateTime)
    # tz-p3 sweep: points.py now defaults its day/month windows off ist_today()
    # (IST), not datetime.now() -- freeze that too so the seeded `today - N days`
    # rows still fall inside the leaderboard / staff-history window.
    if hasattr(points_module, "ist_today"):
        monkeypatch.setattr(points_module, "ist_today", lambda: frozen.date())
    return frozen.date()


# ============================================================================
# Auth helpers (mirror conftest.auth_headers but with extra roles)
# ============================================================================


@pytest.fixture
def staff_self_headers():
    """SALES_STAFF token whose user_id == 'user-akshay' so the staff
    can post their own row."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "user-akshay",
        "username": "akshay",
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def store_manager_headers():
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "user-sameer",
        "username": "sameer",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# Test payload helper
# ============================================================================


def _scores(**overrides):
    base = {
        "attendance": 9, "conversion": 16, "task": 9, "visufit": 8,
        "punctuality": 10, "behaviour": 9, "kicker_1": 0, "kicker_2": 0,
        "reviews": 8,
    }
    base.update(overrides)
    return base


def _today():
    from datetime import date as _d
    return _d.today().isoformat()


def _yesterday():
    from datetime import date as _d, timedelta
    return (_d.today() - timedelta(days=1)).isoformat()


def _payload(**overrides):
    p = {
        "date": _today(),
        "staff_id": "user-akshay",
        "scores": _scores(),
    }
    p.update(overrides)
    return p


# ============================================================================
# P1 — schema + single POST + 409
# ============================================================================


def test_create_daily_full_payload(client, auth_headers, patched_points):
    """Happy path: 9 categories sum to total; eligibility snapshot stamped."""
    resp = client.post(
        "/api/v1/incentive/points/daily", json=_payload(), headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["staff_id"] == "user-akshay"
    assert body["staff_name"] == "User-user-akshay"
    # 9 + 16 + 9 + 8 + 10 + 9 + 0 + 0 + 8 = 69
    assert body["total"] == 69
    # Falls in 0..70 band → 0.0
    assert body["eligibility"] == 0.0
    assert "eligibility_thresholds_used" in body
    assert body["log_id"].startswith("PL-")


def test_create_daily_409_on_duplicate_same_day_same_staff(
    client, auth_headers, patched_points
):
    """The unique partial index → 409 on second save (delete first)."""
    resp = client.post(
        "/api/v1/incentive/points/daily", json=_payload(), headers=auth_headers,
    )
    assert resp.status_code == 201
    resp = client.post(
        "/api/v1/incentive/points/daily", json=_payload(), headers=auth_headers,
    )
    assert resp.status_code == 409
    assert "Already logged" in resp.json()["detail"]


def test_create_daily_validation_attendance_above_10(
    client, auth_headers, patched_points
):
    resp = client.post(
        "/api/v1/incentive/points/daily",
        json=_payload(scores=_scores(attendance=11)),
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_daily_validation_conversion_above_20(
    client, auth_headers, patched_points
):
    resp = client.post(
        "/api/v1/incentive/points/daily",
        json=_payload(scores=_scores(conversion=21)),
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_eligibility_snapshot_uses_settings_at_write_time(
    client, auth_headers, patched_points
):
    """A 80-total today → 0.8. If we now mutate settings to push the
    threshold to 90, the existing row's eligibility_thresholds_used
    should still reflect the OLD bands (snapshot semantics)."""
    payload = _payload(
        scores=_scores(attendance=10, conversion=18, task=10, visufit=10,
                       punctuality=10, behaviour=10, kicker_1=2, kicker_2=2,
                       reviews=8),
    )
    # 10+18+10+10+10+10+2+2+8 = 80
    resp = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] == 80
    assert body["eligibility"] == 0.8
    assert body["eligibility_thresholds_used"]["bands"][2]["min"] == 80


def test_get_daily_lists_today(client, auth_headers, patched_points):
    """GET /daily returns rows for today (or specified date)."""
    client.post(
        "/api/v1/incentive/points/daily", json=_payload(), headers=auth_headers,
    )
    client.post(
        "/api/v1/incentive/points/daily",
        json=_payload(staff_id="user-rupesh"),
        headers=auth_headers,
    )
    resp = client.get("/api/v1/incentive/points/daily", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    staff_ids = sorted(r["staff_id"] for r in body["items"])
    assert staff_ids == ["user-akshay", "user-rupesh"]


def test_rbac_sales_staff_cannot_post_for_other_staff(
    client, staff_self_headers, patched_points
):
    """Sales staff can post for self but not for someone else."""
    resp = client.post(
        "/api/v1/incentive/points/daily",
        json=_payload(staff_id="user-rupesh"),
        headers=staff_self_headers,
    )
    assert resp.status_code == 403
    # Self is fine
    resp = client.post(
        "/api/v1/incentive/points/daily",
        json=_payload(staff_id="user-akshay"),
        headers=staff_self_headers,
    )
    assert resp.status_code == 201


def test_rbac_store_manager_can_post_for_anyone(
    client, store_manager_headers, patched_points
):
    """Store-manager writes for any staff in store."""
    resp = client.post(
        "/api/v1/incentive/points/daily",
        json=_payload(staff_id="user-rupesh"),
        headers=store_manager_headers,
    )
    assert resp.status_code == 201


# ============================================================================
# P2 — bulk + delete + conversion auto-fill
# ============================================================================


def test_bulk_post_per_row_success_failure(
    client, auth_headers, patched_points
):
    """Bulk endpoint: 1 success + 1 dup → saved=1, failures=1."""
    # Pre-load a row that the second bulk row will collide with
    client.post(
        "/api/v1/incentive/points/daily",
        json=_payload(staff_id="user-akshay"),
        headers=auth_headers,
    )
    resp = client.post(
        "/api/v1/incentive/points/daily/bulk",
        json={"rows": [
            _payload(staff_id="user-rupesh"),
            _payload(staff_id="user-akshay"),
        ]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["saved_count"] == 1
    assert body["failed_count"] == 1
    assert body["failures"][0]["status_code"] == 409
    assert body["failures"][0]["staff_id"] == "user-akshay"


def test_delete_then_repost(client, auth_headers, patched_points):
    """DELETE soft-deletes (frees the unique-key slot) → re-POST OK."""
    r = client.post(
        "/api/v1/incentive/points/daily", json=_payload(), headers=auth_headers,
    )
    assert r.status_code == 201
    log_id = r.json()["log_id"]

    r = client.request(
        "DELETE", f"/api/v1/incentive/points/daily/{log_id}",
        json={"reason": "wrong attendance"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # Re-POST with corrected attendance — should succeed (slot freed)
    r = client.post(
        "/api/v1/incentive/points/daily",
        json=_payload(scores=_scores(attendance=10)),
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["attendance"] == 10


def test_delete_audit_logged(client, auth_headers, patched_points):
    audit_repo = patched_points["audit_repo"]
    r = client.post(
        "/api/v1/incentive/points/daily", json=_payload(), headers=auth_headers,
    )
    log_id = r.json()["log_id"]
    client.request(
        "DELETE", f"/api/v1/incentive/points/daily/{log_id}",
        json={"reason": "Mistake"}, headers=auth_headers,
    )
    audit = next(
        d for d in audit_repo.collection.docs
        if d.get("action") == "points.delete"
    )
    assert audit["entity_id"] == log_id
    assert audit["detail"]["reason"] == "Mistake"


def test_conversion_auto_fill_today(client, auth_headers, patched_points):
    """conversion=null + date=today → server fetches conversion_score
    from Module (i)'s feed math (in-process, no HTTP self-call)."""
    walkin_repo = patched_points["walkin_repo"]
    # Akshay has 5 walk-ins, 1 walkout today → conversion = 16 (4/5 * 20)
    for mob in ("9100100001", "9100100002", "9100100003", "9100100004", "9100100005"):
        walkin_repo.auto_increment(
            store_id="BV-TEST-01", sales_person_id="user-akshay", mobile=mob,
        )
    walkout_repo = patched_points["walkout_repo"]
    walkout_repo.create_walkout({
        "store_id": "BV-TEST-01",
        "date_str": _today(),
        "sales_person_id": "user-akshay", "sales_person_name": "AKSHAY",
        "customer_name": "X", "mobile": "9100100099",
        "primary_walkout_reason": "BUDGET/PRICE",
    })

    payload = _payload(scores=_scores(conversion=None))
    resp = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # walk_ins=5, walkouts=1, retro=0 → (5-1+0)/5*20 = 16
    assert body["conversion"] == 16


def test_conversion_auto_fill_past_date_falls_back_to_zero(
    client, auth_headers, patched_points
):
    """conversion=null on past date → 0 (operator must explicitly set)."""
    payload = _payload(date=_yesterday(), scores=_scores(conversion=None))
    resp = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["conversion"] == 0


# ============================================================================
# P3 — MTD + leaderboard + Visufit gate
# ============================================================================


def test_visufit_gate_overrides_to_zero_below_threshold(
    client, auth_headers, patched_points
):
    """If gate enabled (default) and visufit_usage_pct_mtd < 0.90,
    visufit category snaps to 0 even if the operator entered 9."""
    payload = _payload(
        scores=_scores(visufit=9), visufit_usage_pct_mtd=0.85,
    )
    resp = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["visufit"] == 0
    assert body["visufit_gate_applied"] is True


def test_visufit_gate_passes_above_threshold(
    client, auth_headers, patched_points
):
    payload = _payload(
        scores=_scores(visufit=9), visufit_usage_pct_mtd=0.95,
    )
    resp = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["visufit"] == 9
    assert body["visufit_gate_applied"] is False


def test_visufit_gate_no_data_does_not_apply(
    client, auth_headers, patched_points
):
    """visufit_usage_pct_mtd=null → don't penalize for missing data."""
    payload = _payload(scores=_scores(visufit=8))  # no usage field
    resp = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["visufit"] == 8
    assert body["visufit_gate_applied"] is False


def test_mtd_aggregation_per_staff(client, auth_headers, patched_points, frozen_points_now):
    """MTD endpoint: per-staff days_logged + per-category averages."""
    from datetime import date as _d, timedelta
    today = frozen_points_now
    for offset in range(3):
        d = (today - timedelta(days=offset)).isoformat()
        client.post(
            "/api/v1/incentive/points/daily",
            json={
                "date": d, "staff_id": "user-akshay",
                "scores": _scores(attendance=8 + offset),
            },
            headers=auth_headers,
        )
    # Rupesh logs only twice
    for offset in range(2):
        d = (today - timedelta(days=offset)).isoformat()
        client.post(
            "/api/v1/incentive/points/daily",
            json={
                "date": d, "staff_id": "user-rupesh",
                "scores": _scores(attendance=10),
            },
            headers=auth_headers,
        )

    resp = client.get("/api/v1/incentive/points/mtd", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    by_staff = {r["staff_id"]: r for r in body["items"]}
    assert by_staff["user-akshay"]["days_logged"] == 3
    # avg attendance = (8 + 9 + 10) / 3 = 9.0
    assert by_staff["user-akshay"]["avg"]["attendance"] == 9.0
    assert by_staff["user-rupesh"]["days_logged"] == 2
    assert by_staff["user-rupesh"]["avg"]["attendance"] == 10.0


def test_leaderboard_sorted_desc_with_tiebreak(
    client, auth_headers, patched_points, frozen_points_now
):
    """Leaderboard sorted by avg.total DESC; tie-broken by days_logged."""
    from datetime import date as _d, timedelta
    today = frozen_points_now
    # Akshay: 1 day, total = 80 (high avg, low days)
    client.post(
        "/api/v1/incentive/points/daily",
        json={"date": today.isoformat(), "staff_id": "user-akshay",
              "scores": _scores(attendance=10, conversion=18, task=10,
                                visufit=10, punctuality=10, behaviour=10,
                                kicker_1=2, kicker_2=2, reviews=8)},
        headers=auth_headers,
    )
    # Rupesh: 3 days, each total = 80 (same avg, more days)
    for offset in range(3):
        d = (today - timedelta(days=offset)).isoformat()
        client.post(
            "/api/v1/incentive/points/daily",
            json={"date": d, "staff_id": "user-rupesh",
                  "scores": _scores(attendance=10, conversion=18, task=10,
                                    visufit=10, punctuality=10, behaviour=10,
                                    kicker_1=2, kicker_2=2, reviews=8)},
            headers=auth_headers,
        )

    resp = client.get(
        "/api/v1/incentive/points/leaderboard", headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Same avg, but rupesh has more days_logged → wins
    assert items[0]["staff_id"] == "user-rupesh"
    assert items[1]["staff_id"] == "user-akshay"


def test_staff_history_returns_ordered_rows(
    client, auth_headers, patched_points, frozen_points_now
):
    """staff/{id}/history returns rows for the date range, newest-first."""
    from datetime import date as _d, timedelta
    today = frozen_points_now
    for offset in range(4):
        d = (today - timedelta(days=offset)).isoformat()
        client.post(
            "/api/v1/incentive/points/daily",
            json={"date": d, "staff_id": "user-akshay",
                  "scores": _scores(attendance=offset + 5)},
            headers=auth_headers,
        )
    resp = client.get(
        "/api/v1/incentive/points/staff/user-akshay/history",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 4
    # date_str descending
    dates = [r["date_str"] for r in items]
    assert dates == sorted(dates, reverse=True)


# ============================================================================
# P4 — settings PATCH (SUPERADMIN-only)
# ============================================================================


def test_eligibility_settings_get_returns_defaults(
    client, auth_headers, patched_points
):
    resp = client.get(
        "/api/v1/incentive/points/settings/eligibility", headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["eligibility_bands"]) == 4
    # Default visufit gate
    assert body["visufit_gate_threshold"] == 0.9
    assert body["visufit_gate_enabled"] is True


def test_eligibility_patch_superadmin_only(
    client, store_manager_headers, patched_points
):
    """STORE_MANAGER can't change eligibility — SUPERADMIN-only."""
    resp = client.patch(
        "/api/v1/incentive/points/settings/eligibility",
        json={"bands": [
            {"min": 0, "max": 60, "value": 0.0},
            {"min": 60, "max": 1000, "value": 1.0},
        ]},
        headers=store_manager_headers,
    )
    assert resp.status_code == 403


def test_eligibility_patch_does_not_rewrite_history(
    client, auth_headers, patched_points
):
    """Snapshot semantics: patching bands doesn't touch existing rows."""
    # Seed a row at total=72 with default bands → eligibility=0.6
    payload = _payload(
        scores=_scores(attendance=10, conversion=16, task=8, visufit=8,
                       punctuality=10, behaviour=8, kicker_1=2, kicker_2=2,
                       reviews=8),
    )
    # 10+16+8+8+10+8+2+2+8 = 72 → 0.6 in default bands
    r = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["total"] == 72
    assert body["eligibility"] == 0.6
    log_id = body["log_id"]

    # Now PATCH bands to make 72 fall into a 0.0 bucket
    r = client.patch(
        "/api/v1/incentive/points/settings/eligibility",
        json={"bands": [
            {"min": 0, "max": 90, "value": 0.0},
            {"min": 90, "max": 1000, "value": 1.0},
        ]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    # Re-fetch the row — its snapshot should still show 0.6
    repo_docs = patched_points["db"].get_collection("points_log").docs
    saved = next(d for d in repo_docs if d["log_id"] == log_id)
    assert saved["eligibility"] == 0.6
    assert saved["eligibility_thresholds_used"]["bands"][1]["max"] == 80


def test_visufit_gate_patch_disabled_skips_override(
    client, auth_headers, patched_points
):
    """Disable the gate via PATCH → subsequent rows preserve visufit
    even when usage<threshold."""
    r = client.patch(
        "/api/v1/incentive/points/settings/visufit-gate",
        json={"enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    payload = _payload(
        scores=_scores(visufit=9), visufit_usage_pct_mtd=0.50,
    )
    r = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["visufit"] == 9
    assert body["visufit_gate_applied"] is False


# ============================================================================
# Calculator unit tests (no HTTP)
# ============================================================================


def test_calculator_compute_total():
    from api.services.points_calculator import compute_total
    assert compute_total({}) == 0
    assert compute_total({"attendance": 10}) == 10
    assert compute_total({
        "attendance": 9, "conversion": 16, "task": 9, "visufit": 8,
        "punctuality": 10, "behaviour": 9, "kicker_1": 0, "kicker_2": 0,
        "reviews": 8,
    }) == 69


def test_calculator_eligibility_band_walk():
    from api.services.points_calculator import compute_eligibility
    from database.repositories.incentive_settings_repository import (
        DEFAULT_ELIGIBILITY_BANDS,
    )
    assert compute_eligibility(50, DEFAULT_ELIGIBILITY_BANDS) == 0.0
    assert compute_eligibility(70, DEFAULT_ELIGIBILITY_BANDS) == 0.6
    assert compute_eligibility(85, DEFAULT_ELIGIBILITY_BANDS) == 0.8
    assert compute_eligibility(99, DEFAULT_ELIGIBILITY_BANDS) == 1.0


def test_calculator_visufit_gate_pure():
    from api.services.points_calculator import apply_visufit_gate
    s, applied = apply_visufit_gate(
        {"visufit": 9}, visufit_usage_pct_mtd=0.85,
        threshold=0.9, enabled=True,
    )
    assert s["visufit"] == 0 and applied is True
    s, applied = apply_visufit_gate(
        {"visufit": 9}, visufit_usage_pct_mtd=0.95,
        threshold=0.9, enabled=True,
    )
    assert s["visufit"] == 9 and applied is False
    s, applied = apply_visufit_gate(
        {"visufit": 9}, visufit_usage_pct_mtd=None,
        threshold=0.9, enabled=True,
    )
    # Missing data → don't apply
    assert s["visufit"] == 9 and applied is False
    s, applied = apply_visufit_gate(
        {"visufit": 9}, visufit_usage_pct_mtd=0.10,
        threshold=0.9, enabled=False,
    )
    # Disabled → don't apply
    assert s["visufit"] == 9 and applied is False
