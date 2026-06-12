"""
IMS 2.0 — F33 Gamified Leaderboard tests (display layer over the points engine)
================================================================================
Coverage:
  - tier banding (PODIUM / CONTENDER / BUILDING incl. small boards)
  - deterministic titles from the 9 scorecard categories (+ tie-break)
  - badge computation (eligibility_100 / logged_every_day / top_riser /
    consistent_90) + rank_delta math
  - SERVER-SIDE rupee strip: each junior role never receives
    revenue/amount/incentive/payout/sales_value keys; managers do
  - ?scope=store|area|org gating on GET /leaderboard + /mtd
    (403 for floor roles asking area/org; org aggregates all stores;
    area aggregates the caller's store_ids)
  - GET /leaderboard/titles catalog (any authenticated user)
  - POST /leaderboard/settings role gate + persistence on the existing
    incentive_settings doc + config defaults
Style mirrors backend/tests/test_points.py (FakeDB + monkeypatch).
"""
from __future__ import annotations

import os
import sys
from datetime import date as date_type

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.leaderboard_display import (  # noqa: E402
    badge_keys_for,
    build_leaderboard_row,
    leaderboard_config_defaults,
    tier_for_rank,
    title_for,
    titles_catalog,
)


# ============================================================================
# Test fakes — minimal Mongo emulator ($gte/$lte/$in aware)
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
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, keys):
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter=None, projection=None):
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


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


FROZEN_TODAY = date_type(2026, 6, 15)


@pytest.fixture
def patched_points(monkeypatch):
    """Wire a FakeDB into the points router + freeze IST today."""
    fake_db = FakeDB()
    from api.routers import points as points_module

    monkeypatch.setattr(points_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(points_module, "get_audit_repository", lambda: None)
    monkeypatch.setattr(points_module, "ist_today", lambda: FROZEN_TODAY)
    return fake_db


def _headers(roles, user_id="user-x", store_ids=None, active=None):
    from api.routers.auth import create_access_token

    store_ids = store_ids or ["BV-TEST-01"]
    token = create_access_token(
        {
            "user_id": user_id,
            "username": user_id,
            "roles": roles,
            "store_ids": store_ids,
            "active_store_id": active or store_ids[0],
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _seed_log(db, store_id, staff_id, date_str, total=80, eligibility=0.8, **cats):
    base = {
        "attendance": 9, "conversion": 16, "task": 9, "visufit": 8,
        "punctuality": 9, "behaviour": 9, "kicker_1": 5, "kicker_2": 5,
        "reviews": 8,
    }
    base.update(cats)
    db.get_collection("points_log").insert_one(
        {
            "store_id": store_id,
            "staff_id": staff_id,
            "staff_name": f"Name-{staff_id}",
            "date_str": date_str,
            "deleted_at": None,
            "total": total,
            "eligibility": eligibility,
            **base,
        }
    )


# ============================================================================
# Pure service — tiers
# ============================================================================


def test_tier_banding_ten_person_board():
    """Top 3 PODIUM, top half CONTENDER, rest BUILDING."""
    tiers = [tier_for_rank(r, 10) for r in range(1, 11)]
    assert tiers[:3] == ["PODIUM", "PODIUM", "PODIUM"]
    assert tiers[3:5] == ["CONTENDER", "CONTENDER"]
    assert tiers[5:] == ["BUILDING"] * 5


def test_tier_banding_small_and_degenerate_boards():
    # 2-person board: everyone is podium
    assert tier_for_rank(1, 2) == "PODIUM"
    assert tier_for_rank(2, 2) == "PODIUM"
    # 7-person board: ceil(7/2)=4 -> rank 4 contender, rank 5 building
    assert tier_for_rank(4, 7) == "CONTENDER"
    assert tier_for_rank(5, 7) == "BUILDING"
    # Garbage in -> most conservative band, never a crash
    assert tier_for_rank(0, 10) == "BUILDING"
    assert tier_for_rank(1, 0) == "BUILDING"


# ============================================================================
# Pure service — titles
# ============================================================================


def _row(avg_overrides=None, **kw):
    avg = {
        "attendance": 0.0, "conversion": 0.0, "task": 0.0, "visufit": 0.0,
        "punctuality": 0.0, "behaviour": 0.0, "kicker_1": 0.0,
        "kicker_2": 0.0, "reviews": 0.0, "total": 0.0,
    }
    avg.update(avg_overrides or {})
    row = {
        "staff_id": "user-a",
        "staff_name": "A",
        "days_logged": 10,
        "avg": avg,
        "eligibility_avg": 0.8,
    }
    row.update(kw)
    return row


def test_title_normalized_by_category_max():
    """conversion 15/20 (0.75) loses to punctuality 9/10 (0.9)."""
    row = _row({"conversion": 15.0, "punctuality": 9.0, "total": 60.0})
    assert title_for(row) == "First Through The Door"


def test_title_conversion_champion_and_determinism():
    row = _row({"conversion": 18.0, "attendance": 8.0, "total": 70.0})
    assert title_for(row) == "Conversion Champion"
    # Pure + deterministic: same input, same output, input not mutated
    snapshot = dict(row["avg"])
    assert title_for(row) == "Conversion Champion"
    assert row["avg"] == snapshot


def test_title_tie_break_is_fixed_order():
    """attendance and reviews both 0.9 normalized -> attendance wins
    (earlier in the fixed title order); never random."""
    row = _row({"attendance": 9.0, "reviews": 9.0, "total": 18.0})
    assert title_for(row) == "Reliability Anchor"


def test_title_none_when_no_signal():
    assert title_for(_row()) is None
    assert title_for({"staff_id": "x"}) is None  # missing avg entirely


# ============================================================================
# Pure service — badges + rank delta
# ============================================================================


def test_badges_eligibility_and_logged_every_day():
    row = _row({"total": 75.0}, eligibility_avg=1.0, days_logged=30)
    keys = badge_keys_for(row, rank=4, period_days=30)
    assert "eligibility_100" in keys
    assert "logged_every_day" in keys
    assert "consistent_90" not in keys
    # Short of the period -> no logged_every_day
    row2 = _row({"total": 75.0}, eligibility_avg=0.8, days_logged=29)
    keys2 = badge_keys_for(row2, rank=4, period_days=30)
    assert "logged_every_day" not in keys2
    assert "eligibility_100" not in keys2


def test_badge_top_riser_and_consistent_90():
    row = _row({"total": 92.0})
    keys = badge_keys_for(row, rank=2, prev_rank=5)
    assert "top_riser" in keys  # climbed 3
    assert "consistent_90" in keys
    # Climbing only 1 place is not a riser; no prev data is not a riser
    assert "top_riser" not in badge_keys_for(row, rank=4, prev_rank=5)
    assert "top_riser" not in badge_keys_for(row, rank=1, prev_rank=None)


def test_rank_delta_math():
    viewer = ["STORE_MANAGER"]
    up = build_leaderboard_row(_row(), 2, 10, viewer, prev_rank=5)
    assert up["rank_delta"] == 3
    down = build_leaderboard_row(_row(), 7, 10, viewer, prev_rank=4)
    assert down["rank_delta"] == -3
    new = build_leaderboard_row(_row(), 3, 10, viewer, prev_rank=None)
    assert new["rank_delta"] is None


# ============================================================================
# Pure service — privacy strip
# ============================================================================

_RUPEE_ROW_EXTRAS = {
    "mtd_revenue": 125000.0,
    "incentive_amount": 4200.0,
    "payout_estimate": 3100.0,
    "totals": {"sales_value": 98000.0, "days": 10},
}

JUNIOR_ROLES = [
    "SALES_STAFF",
    "SALES_CASHIER",
    "CASHIER",
    "WORKSHOP_STAFF",
    "OPTOMETRIST",
]


@pytest.mark.parametrize("role", JUNIOR_ROLES)
def test_rupee_strip_for_each_junior_role(role):
    """A raw row WITH rupee-ish fields: junior viewers never receive them
    (top-level AND nested), but keep the gamified presentation fields."""
    raw = _row({"conversion": 18.0, "total": 80.0}, **_RUPEE_ROW_EXTRAS)
    out = build_leaderboard_row(raw, 1, 5, [role], prev_rank=3)
    assert "mtd_revenue" not in out
    assert "incentive_amount" not in out
    assert "payout_estimate" not in out
    assert "sales_value" not in out.get("totals", {})
    # Non-sensitive sibling key survives — strip, don't nuke
    assert out["totals"]["days"] == 10
    # Presentation fields still present
    assert out["tier_label"] == "PODIUM"
    assert out["title_earned"] == "Conversion Champion"
    assert out["rank_delta"] == 2
    # eligibility_avg is a multiplier, not rupees — must survive
    assert out["eligibility_avg"] == 0.8


@pytest.mark.parametrize("role", ["STORE_MANAGER", "SUPERADMIN", "ACCOUNTANT"])
def test_rupee_fields_present_for_privileged_viewers(role):
    raw = _row({"conversion": 18.0, "total": 80.0}, **_RUPEE_ROW_EXTRAS)
    out = build_leaderboard_row(raw, 1, 5, [role])
    assert out["mtd_revenue"] == 125000.0
    assert out["incentive_amount"] == 4200.0
    assert out["totals"]["sales_value"] == 98000.0


def test_rupee_strip_for_empty_or_unknown_roles():
    """No roles / unknown roles -> most conservative: strip."""
    raw = _row({}, **_RUPEE_ROW_EXTRAS)
    assert "mtd_revenue" not in build_leaderboard_row(raw, 1, 1, [])
    assert "mtd_revenue" not in build_leaderboard_row(raw, 1, 1, ["MYSTERY_ROLE"])


def test_strip_never_fabricates():
    """A raw row WITHOUT rupee fields stays rupee-free for managers too —
    present-or-absent, never invented."""
    out = build_leaderboard_row(_row(), 1, 1, ["STORE_MANAGER"])
    assert not any("revenue" in k or "amount" in k for k in out)


# ============================================================================
# Pure service — catalog + config defaults
# ============================================================================


def test_titles_catalog_shape():
    items = titles_catalog()
    titles = [i for i in items if i["kind"] == "title"]
    badges = [i for i in items if i["kind"] == "badge"]
    assert len(titles) == 9  # one per scorecard category
    assert {b["key"] for b in badges} >= {
        "eligibility_100", "logged_every_day", "top_riser",
    }
    for i in items:
        assert i["key"] and i["label"] and i["description"]
    # Deterministic ordering
    assert [i["key"] for i in titles_catalog()] == [i["key"] for i in items]


def test_leaderboard_config_defaults():
    assert leaderboard_config_defaults() == {
        "enabled": True,
        "scope_default": "store",
        "show_titles": True,
        "show_badges": True,
    }


# ============================================================================
# API — decorated rows + scope gating
# ============================================================================


def test_leaderboard_rows_are_decorated(client, patched_points):
    _seed_log(patched_points, "BV-TEST-01", "user-a", "2026-06-10", total=90)
    _seed_log(patched_points, "BV-TEST-01", "user-b", "2026-06-10", total=60)
    resp = client.get(
        "/api/v1/incentive/points/leaderboard",
        headers=_headers(["STORE_MANAGER"], "mgr-1"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "store"
    items = body["items"]
    assert [i["staff_id"] for i in items] == ["user-a", "user-b"]
    for rank, row in enumerate(items, start=1):
        assert row["rank"] == rank
        assert row["tier_label"] == "PODIUM"  # 2-person board
        assert "title_earned" in row
        assert isinstance(row["badge_keys"], list)
        assert "rank_delta" in row


def test_scope_org_403_for_floor_role(client, patched_points):
    for scope in ("org", "area"):
        resp = client.get(
            f"/api/v1/incentive/points/leaderboard?scope={scope}",
            headers=_headers(["SALES_STAFF"], "user-a"),
        )
        assert resp.status_code == 403, resp.text
    # mtd has the same gate
    resp = client.get(
        "/api/v1/incentive/points/mtd?scope=org",
        headers=_headers(["SALES_CASHIER"], "user-a"),
    )
    assert resp.status_code == 403


def test_scope_invalid_value_rejected(client, patched_points):
    resp = client.get(
        "/api/v1/incentive/points/leaderboard?scope=galaxy",
        headers=_headers(["SUPERADMIN"], "admin-1"),
    )
    assert resp.status_code == 422


def test_scope_org_aggregates_all_stores(client, patched_points):
    _seed_log(patched_points, "BV-TEST-01", "user-a", "2026-06-10", total=90)
    _seed_log(patched_points, "BV-TEST-02", "user-b", "2026-06-10", total=70)
    _seed_log(patched_points, "WO-TEST-09", "user-c", "2026-06-10", total=50)
    hdrs = _headers(["SUPERADMIN"], "admin-1", store_ids=["BV-TEST-01"])
    # Default store scope: only own store
    resp = client.get("/api/v1/incentive/points/leaderboard", headers=hdrs)
    assert {i["staff_id"] for i in resp.json()["items"]} == {"user-a"}
    # org scope: everyone
    resp = client.get(
        "/api/v1/incentive/points/leaderboard?scope=org", headers=hdrs
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {i["staff_id"] for i in body["items"]} == {"user-a", "user-b", "user-c"}
    assert body["scope"] == "org"
    assert body["store_id"] is None


def test_scope_area_uses_callers_stores(client, patched_points):
    _seed_log(patched_points, "BV-TEST-01", "user-a", "2026-06-10", total=90)
    _seed_log(patched_points, "BV-TEST-02", "user-b", "2026-06-10", total=70)
    _seed_log(patched_points, "WO-TEST-09", "user-c", "2026-06-10", total=50)
    hdrs = _headers(
        ["AREA_MANAGER"], "am-1", store_ids=["BV-TEST-01", "BV-TEST-02"]
    )
    resp = client.get(
        "/api/v1/incentive/points/leaderboard?scope=area", headers=hdrs
    )
    assert resp.status_code == 200
    assert {i["staff_id"] for i in resp.json()["items"]} == {"user-a", "user-b"}


def test_leaderboard_rank_delta_from_previous_window(client, patched_points):
    """user-b beat user-a in the previous 30-day window; this window
    user-a wins -> user-a delta +1, user-b delta -1."""
    # Previous window (days=30 -> 2026-04-17..2026-05-16)
    _seed_log(patched_points, "BV-TEST-01", "user-a", "2026-05-10", total=50)
    _seed_log(patched_points, "BV-TEST-01", "user-b", "2026-05-10", total=90)
    # Current window
    _seed_log(patched_points, "BV-TEST-01", "user-a", "2026-06-10", total=95)
    _seed_log(patched_points, "BV-TEST-01", "user-b", "2026-06-10", total=60)
    resp = client.get(
        "/api/v1/incentive/points/leaderboard",
        headers=_headers(["STORE_MANAGER"], "mgr-1"),
    )
    items = resp.json()["items"]
    by_staff = {i["staff_id"]: i for i in items}
    assert by_staff["user-a"]["rank_delta"] == 1
    assert by_staff["user-b"]["rank_delta"] == -1


# ============================================================================
# API — titles catalog + settings
# ============================================================================


def test_titles_endpoint_authenticated(client, patched_points):
    resp = client.get(
        "/api/v1/incentive/points/leaderboard/titles",
        headers=_headers(["SALES_STAFF"], "user-a"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["titles"]) == 9
    assert len(body["badges"]) >= 3
    assert body["tiers"] == ["PODIUM", "CONTENDER", "BUILDING"]
    # No token -> 401
    assert client.get("/api/v1/incentive/points/leaderboard/titles").status_code == 401


def test_settings_post_role_gate(client, patched_points):
    payload = {"enabled": False}
    for role in ("SALES_STAFF", "STORE_MANAGER", "AREA_MANAGER"):
        resp = client.post(
            "/api/v1/incentive/points/leaderboard/settings",
            json=payload,
            headers=_headers([role], f"u-{role.lower()}"),
        )
        assert resp.status_code == 403, f"{role}: {resp.text}"


def test_settings_post_upserts_subdoc(client, patched_points):
    resp = client.post(
        "/api/v1/incentive/points/leaderboard/settings",
        json={"show_titles": False, "scope_default": "org"},
        headers=_headers(["ADMIN"], "admin-1"),
    )
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["leaderboard_config"]
    # Defaults merged underneath the patch
    assert cfg == {
        "enabled": True,
        "scope_default": "org",
        "show_titles": False,
        "show_badges": True,
    }
    # Persisted on the EXISTING incentive_settings doc shape (no new collection)
    doc = patched_points.get_collection("incentive_settings").find_one(
        {"store_id": "BV-TEST-01"}
    )
    assert doc is not None
    assert doc["leaderboard_config"]["scope_default"] == "org"
    assert "eligibility_bands" in doc  # seeded with the standard defaults

    # Second POST patches over the stored config, not the defaults
    resp = client.post(
        "/api/v1/incentive/points/leaderboard/settings",
        json={"enabled": False},
        headers=_headers(["SUPERADMIN"], "admin-2"),
    )
    cfg = resp.json()["leaderboard_config"]
    assert cfg["enabled"] is False
    assert cfg["show_titles"] is False  # earlier patch retained


def test_settings_post_validation(client, patched_points):
    hdrs = _headers(["SUPERADMIN"], "admin-1")
    resp = client.post(
        "/api/v1/incentive/points/leaderboard/settings",
        json={"scope_default": "galaxy"},
        headers=hdrs,
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/v1/incentive/points/leaderboard/settings", json={}, headers=hdrs
    )
    assert resp.status_code == 400
