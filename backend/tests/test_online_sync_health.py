"""
IMS 2.0 - Online-store sync-health tile (council D10)
=====================================================
Two layers:

  1. PURE service tests (no DB) -- lock the fail-soft shape of
     services.online_sync_health and prove each signal is read from the right
     collection via tiny in-memory fakes. Always run.

  2. HTTP endpoint tests -- prove GET /api/v1/admin/online-store/sync-health is
     SUPERADMIN-only (ADMIN passes the admin-router gate but is narrowed out;
     SALES_STAFF is rejected by the router gate) and that a SUPERADMIN gets the
     documented payload shape. These talk to the real app; on CI's mongo they
     hit the fail-soft empty DB, which is exactly what we assert.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import online_sync_health as sh  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory fakes (subscript collection access, like MockDatabase)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_a, **_k):
        # Tests feed rows pre-ordered newest-first; identity sort is fine.
        return self

    def limit(self, n):
        self._rows = self._rows[: int(n)]
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeColl:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def find(self, query=None, _projection=None, *_a, **_k):
        rows = [r for r in self._rows if _matches(r, query or {})]
        return _FakeCursor(rows)

    def count_documents(self, query):
        return sum(1 for r in self._rows if _matches(r, query))

    def aggregate(self, _pipeline):
        # Not exercised in these fakes (reconcile tests use the None/empty path).
        return iter([])


def _matches(row, query) -> bool:
    """Minimal query matcher for the {$exists,$nin,$ne} predicates used here."""
    for key, cond in query.items():
        val = row.get(key)
        if isinstance(cond, dict):
            if "$exists" in cond:
                if cond["$exists"] and key not in row:
                    return False
                if not cond["$exists"] and key in row:
                    return False
            if "$nin" in cond and val in cond["$nin"]:
                return False
            if "$ne" in cond and val == cond["$ne"]:
                return False
        else:
            if val != cond:
                return False
    return True


class _FakeDb:
    def __init__(self, colls=None):
        self._colls = dict(colls or {})

    def __getitem__(self, name):
        return self._colls.get(name, _FakeColl([]))


# ---------------------------------------------------------------------------
# PURE service tests
# ---------------------------------------------------------------------------


def test_sync_health_none_db_is_failsoft():
    """No DB -> every section degrades to its empty/zero shape, never raises."""
    out = sh.sync_health(None)
    assert set(out) >= {
        "online_configured",
        "last_shopify_sync",
        "last_successful_shopify_sync_at",
        "reconcile",
        "webhooks",
    }
    assert out["last_shopify_sync"] == {"found": False}
    assert out["last_successful_shopify_sync_at"] is None
    assert out["reconcile"]["pending"] == 0
    assert out["reconcile"]["oversell_risk"] == 0
    assert out["webhooks"] == {"failed": 0, "skipped": 0, "pending": 0}
    # online_configured reflects env; in tests ECOMMERCE_DATABASE_URL is unset.
    assert out["online_configured"] is False


def test_last_shopify_sync_reads_newest_row():
    db = _FakeDb(
        {
            "sync_runs": _FakeColl(
                [
                    # newest-first (the service sorts ran_at desc; fake keeps order)
                    {"integration": "shopify", "ok": True, "ran_at": "2026-06-01T10:00:00Z",
                     "items_synced": 12, "error": None},
                    {"integration": "shopify", "ok": False, "ran_at": "2026-05-31T10:00:00Z",
                     "items_synced": 0, "error": "boom"},
                ]
            )
        }
    )
    out = sh.last_shopify_sync(db)
    assert out["found"] is True
    assert out["ok"] is True
    assert out["ran_at"] == "2026-06-01T10:00:00Z"
    assert out["items_synced"] == 12


def test_last_successful_shopify_sync_filters_ok_true():
    db = _FakeDb(
        {
            "sync_runs": _FakeColl(
                [
                    {"integration": "shopify", "ok": True, "ran_at": "2026-06-01T09:00:00Z"},
                ]
            )
        }
    )
    assert sh.last_successful_shopify_sync_at(db) == "2026-06-01T09:00:00Z"


def test_last_successful_shopify_sync_none_when_all_failed():
    db = _FakeDb(
        {"sync_runs": _FakeColl([{"integration": "shopify", "ok": False, "ran_at": "x"}])}
    )
    # The fake's count/find here returns the ok=True filtered set -> empty.
    assert sh.last_successful_shopify_sync_at(db) is None


def test_failed_webhook_summary_counts_each_bucket():
    db = _FakeDb(
        {
            "webhook_inbox": _FakeColl(
                [
                    {"vendor": "shopify", "processed": True, "handler_error": "TypeError: x"},
                    {"vendor": "razorpay", "processed": False, "skipped_reason": "secret_not_configured"},
                    {"vendor": "shopify", "processed": True},   # clean, processed
                    {"vendor": "shopify", "processed": False},  # pending drain
                ]
            )
        }
    )
    out = sh.failed_webhook_summary(db)
    assert out["failed"] == 1     # one handler_error row
    assert out["skipped"] == 1    # one skipped_reason row
    assert out["pending"] == 2    # two processed != True


def test_pending_reconcile_failsoft_with_no_products():
    """Empty/absent products collection -> zeros, never raises."""
    db = _FakeDb({"products": _FakeColl([])})
    out = sh.pending_reconcile_summary(db)
    assert out["pending"] == 0
    assert out["scanned"] == 0
    assert "online_configured" in out


# ---------------------------------------------------------------------------
# HTTP endpoint tests (SUPERADMIN gate + payload shape)
# ---------------------------------------------------------------------------

_EP = "/api/v1/admin/online-store/sync-health"


def _token(roles):
    from api.routers.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "user_id": f"sh-{'-'.join(roles).lower()}",
                "username": "sh-tester",
                "roles": roles,
                "store_ids": ["BV-TEST-01"],
                "active_store_id": "BV-TEST-01",
            }
        )
    }


def test_endpoint_superadmin_ok_shape(client):
    r = client.get(_EP, headers=_token(["SUPERADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {
        "online_configured",
        "last_shopify_sync",
        "last_successful_shopify_sync_at",
        "reconcile",
        "webhooks",
    }
    assert "pending" in body["reconcile"]
    assert "failed" in body["webhooks"]


def test_endpoint_sales_staff_forbidden(client):
    # Rejected by the admin router gate (not SUPERADMIN/ADMIN) -> 403.
    r = client.get(_EP, headers=_token(["SALES_STAFF"]))
    assert r.status_code == 403


def test_endpoint_admin_narrowed_to_superadmin(client):
    # ADMIN passes the admin-router gate but the endpoint narrows to SUPERADMIN.
    r = client.get(_EP, headers=_token(["ADMIN"]))
    assert r.status_code == 403


def test_endpoint_requires_auth(client):
    # No token -> the route's own 401 (auth), never a silent 200.
    r = client.get(_EP)
    assert r.status_code in (401, 403)
