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
    # online_configured = IMS Mongo carries Shopify-mapped objects; no DB -> False.
    assert out["online_configured"] is False
    # Catalog-push signal (OS-049) degrades to not-found on no DB.
    assert out["catalog_push"] == {
        "found": False,
        "last_pushed_at": None,
        "pushed_products": 0,
    }


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
# Stock tally (BVI Phase 5 read-only reconciliation dashboard)
# ---------------------------------------------------------------------------


class _StockUnitsColl(_FakeColl):
    """A fake stock_units collection that supports the on-hand / reserved
    aggregation the tally reader uses: {$match:{product_id:{$in},status...}},
    {$group:{_id:'$product_id', n:{$sum:{$ifNull:['$quantity',1]}}}}."""

    def aggregate(self, pipeline):
        match = {}
        for stage in pipeline:
            if "$match" in stage:
                match = stage["$match"]
        pid_in = ((match.get("product_id") or {}).get("$in")) or []
        # on-hand uses an $or over AVAILABLE-ish / absent status; reserved uses a
        # flat status == "RESERVED". Detect which predicate this call carries.
        avail_statuses = None
        or_clause = match.get("$or")
        if isinstance(or_clause, list):
            for clause in or_clause:
                cond = (clause or {}).get("status")
                if isinstance(cond, dict) and "$in" in cond:
                    avail_statuses = set(cond["$in"])
        status_eq = match.get("status")
        counts: Dict[str, int] = {}
        for r in self._rows:
            pid = r.get("product_id")
            if pid_in and pid not in pid_in:
                continue
            st = r.get("status")
            if avail_statuses is not None:
                # on-hand: AVAILABLE-ish OR status absent/None
                if not (st in avail_statuses or st is None):
                    continue
            elif status_eq is not None:
                # reserved: exact status match
                if st != status_eq:
                    continue
            counts[pid] = counts.get(pid, 0) + int(r.get("quantity", 1) or 1)
        return iter([{"_id": pid, "n": n} for pid, n in counts.items()])


def _tally_db():
    """Two online-listed SKUs: one healthy, one oversell-risk; plus one
    not-online SKU that must be skipped from the tally."""
    products = _FakeColl(
        [
            {"product_id": "P1", "sku": "SKU-OK", "name": "Ray-Ban RB1", "is_active": True},
            {"product_id": "P2", "sku": "SKU-RISK", "name": "Oakley OO2", "is_active": True},
            {"product_id": "P3", "sku": "SKU-OFFLINE", "name": "Local Only", "is_active": True},
        ]
    )
    stock = _StockUnitsColl(
        [
            # P1: 5 AVAILABLE, 1 RESERVED -> sellable 4
            *[{"product_id": "P1", "status": "AVAILABLE", "quantity": 1} for _ in range(5)],
            {"product_id": "P1", "status": "RESERVED", "quantity": 1},
            # P2: 2 AVAILABLE, 0 RESERVED -> sellable 2
            *[{"product_id": "P2", "status": "AVAILABLE", "quantity": 1} for _ in range(2)],
            # P3: 3 AVAILABLE (but not listed online)
            *[{"product_id": "P3", "status": "AVAILABLE", "quantity": 1} for _ in range(3)],
        ]
    )
    return _FakeDb({"products": products, "stock_units": stock})


def _patch_online(monkeypatch, mapping):
    """Stub online_status_for_skus (IMS Mongo catalog) with a fixed mapping."""
    from api.services import online_catalog

    monkeypatch.setattr(
        online_catalog, "online_status_for_skus", lambda db, skus: mapping
    )
    # stock_tally imports the name inside the function from .online_catalog, so
    # patching the module attribute is sufficient.


def test_stock_tally_failsoft_no_db():
    """No DB -> empty envelope with the documented summary keys, never raises."""
    out = sh.stock_tally_summary(None)
    assert out["items"] == []
    s = out["summary"]
    assert s["skus_checked"] == 0
    assert s["at_risk_count"] == 0
    assert s["total_online_listed"] == 0
    assert s["total_on_hand"] == 0
    assert "online_configured" in s


def test_stock_tally_populated_with_oversell_risk(monkeypatch):
    """Populated fake repo: SKU-OK healthy, SKU-RISK oversell (listed > sellable),
    SKU-OFFLINE skipped (not online). The online FLAG comes from the IMS Mongo
    catalog; the LISTED quantity from the live Shopify read (online_qty).
    Read-only: no stock is mutated."""
    _patch_online(
        monkeypatch,
        {
            "SKU-OK": {"online": True, "online_stock": None},
            "SKU-RISK": {"online": True, "online_stock": None},
            # present online-status but marked not-online -> excluded
            "SKU-OFFLINE": {"online": False, "online_stock": None},
        },
    )
    db = _tally_db()
    # listed 3 <= sellable 4 -> OK; listed 9 > sellable 2 -> OVERSELL RISK.
    out = sh.stock_tally_summary(db, online_qty={"SKU-OK": 3, "SKU-RISK": 9})

    # Only the two ONLINE skus are assessed; the offline one is skipped.
    assert out["summary"]["skus_checked"] == 2
    assert out["summary"]["at_risk_count"] == 1
    skus = [i["sku"] for i in out["items"]]
    assert "SKU-OFFLINE" not in skus
    # Worst-first: the oversell-risk row is on top.
    assert out["items"][0]["sku"] == "SKU-RISK"

    by_sku = {i["sku"]: i for i in out["items"]}
    ok = by_sku["SKU-OK"]
    assert ok["on_hand"] == 5 and ok["reserved"] == 1 and ok["sellable"] == 4
    assert ok["oversell_risk"] is False
    assert ok["recommended_buffer"] == 1  # max(1, ceil(5% of 5))

    risk = by_sku["SKU-RISK"]
    assert risk["on_hand"] == 2 and risk["reserved"] == 0 and risk["sellable"] == 2
    assert risk["online_listed_qty"] == 9
    assert risk["oversell_risk"] is True

    # Summary totals only cover the online SKUs (P1 + P2), not the offline P3.
    assert out["summary"]["total_on_hand"] == 7      # 5 + 2
    assert out["summary"]["total_reserved"] == 1
    assert out["summary"]["total_sellable"] == 6     # 4 + 2
    assert out["summary"]["total_online_listed"] == 12  # 3 + 9

    # READ-ONLY: the fake stock rows are unchanged (nothing reserved/minted).
    assert len(db["stock_units"]._rows) == 11


def test_stock_tally_no_mapped_products_is_empty(monkeypatch):
    """No Shopify-mapped products in the IMS catalog -> online_status_for_skus
    returns {} -> no SKU is treated as online, so the tally is empty (never
    raises)."""
    _patch_online(monkeypatch, {})
    out = sh.stock_tally_summary(_tally_db())
    assert out["items"] == []
    assert out["summary"]["skus_checked"] == 0


def test_stock_tally_unknown_listed_is_null_and_never_flags(monkeypatch):
    """Without a live Shopify read (online_qty=None) the listed qty is an honest
    None -- never a fake 0 -- and oversell_risk cannot fire."""
    _patch_online(
        monkeypatch,
        {
            "SKU-OK": {"online": True, "online_stock": None},
            "SKU-RISK": {"online": True, "online_stock": None},
        },
    )
    out = sh.stock_tally_summary(_tally_db())
    assert out["summary"]["skus_checked"] == 2
    assert out["summary"]["at_risk_count"] == 0
    assert out["summary"]["listed_qty_live"] is False
    for row in out["items"]:
        assert row["online_listed_qty"] is None
        assert row["oversell_risk"] is False


def test_stock_tally_partial_coverage_is_not_live(monkeypatch):
    """Fix-round P1: when the live read covered only SOME mapped SKUs, the
    tally must NOT claim listed_qty_live -- uncovered rows stay None/no-risk
    and the coverage counts are surfaced."""
    _patch_online(
        monkeypatch,
        {
            "SKU-OK": {"online": True, "online_stock": None},
            "SKU-RISK": {"online": True, "online_stock": None},
        },
    )
    out = sh.stock_tally_summary(
        _tally_db(),
        online_qty={"SKU-OK": 3},
        listed_coverage={"live": 1, "mapped": 2},
    )
    s = out["summary"]
    assert s["listed_qty_live"] is False   # partial, not full coverage
    assert s["listed_live_rows"] == 1
    assert s["listed_mapped_rows"] == 2
    by_sku = {i["sku"]: i for i in out["items"]}
    assert by_sku["SKU-OK"]["online_listed_qty"] == 3
    assert by_sku["SKU-RISK"]["online_listed_qty"] is None  # uncovered
    assert by_sku["SKU-RISK"]["oversell_risk"] is False


def test_live_listed_qty_filters_mapped_first_then_caps(monkeypatch):
    """Fix-round P1: the live-read cap applies to the MAPPED set (an unmapped
    SKU can never waste a cap slot) and the result reports honest coverage:
    600 mapped with cap 500 -> live=500, capped=True, SKUs 501+ uncovered."""
    import asyncio

    import api.services.shopify_push as sp
    from api.services import online_catalog, shopify_stock_parity

    monkeypatch.setattr(sp, "_has_shopify_creds", lambda db: True)
    # 600 mapped SKUs interleaved with 600 unmapped ones in scan order.
    mapped = [f"M{i:04d}" for i in range(600)]
    unmapped = [f"U{i:04d}" for i in range(600)]
    skus = [x for pair in zip(unmapped, mapped) for x in pair]
    monkeypatch.setattr(
        online_catalog,
        "inventory_items_for_skus",
        lambda db, s: {k: f"inv-{k}" for k in s if k.startswith("M")},
    )

    async def _avail(db, inv_ids):
        return {i: 5 for i in inv_ids}

    monkeypatch.setattr(shopify_stock_parity, "_shopify_available_by_item", _avail)

    out = asyncio.run(sh.live_listed_qty_for_skus(object(), skus, cap=500))
    assert out is not None
    assert out["mapped"] == 600
    assert out["live"] == 500
    assert out["capped"] is True
    # The cap is spent ONLY on mapped SKUs -- the first 500 in input order.
    assert set(out["qty"]) == set(mapped[:500])
    assert not any(k.startswith("U") for k in out["qty"])


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
