"""
Nightly Shopify stock-parity tests.
Pins: the PURE comparator (drift only when |IMS - Shopify| exceeds tolerance;
Shopify-unknown rows never count as drift; sorted worst-first), the deduped
SYSTEM-task filing (one active drift task at a time), snapshot pruning, and a
fail-soft run_parity_tick orchestration smoke.

In-memory fakes + injected Shopify boundary -- no DB, no network.
"""

import asyncio
import os
import sys
import types

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import shopify_stock_parity as sp  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Pure comparator
# ---------------------------------------------------------------------------

def test_compare_variant_parity_respects_tolerance_and_unknowns():
    rows = [
        {"sku": "A", "inventory_item_id": "1", "ims_available": 10, "shopify_available": 10},  # 0
        {"sku": "B", "inventory_item_id": "2", "ims_available": 10, "shopify_available": 8},   # 2 == tol
        {"sku": "C", "inventory_item_id": "3", "ims_available": 10, "shopify_available": 7},   # 3 > tol
        {"sku": "D", "inventory_item_id": "4", "ims_available": 20, "shopify_available": 5},   # 15 > tol
        {"sku": "E", "inventory_item_id": "5", "ims_available": 10, "shopify_available": None},  # unknown
    ]
    out = sp.compare_variant_parity(rows, tolerance=2)
    assert out["compared"] == 4          # E is unknown, excluded
    assert out["unknown"] == 1
    assert out["drift_count"] == 2       # C and D (B is exactly at tolerance, OK)
    assert out["max_delta"] == 15
    # Sorted worst-first.
    assert [d["sku"] for d in out["drift"]] == ["D", "C"]
    assert out["tolerance"] == 2


def test_compare_variant_parity_empty_is_clean():
    out = sp.compare_variant_parity([], tolerance=2)
    assert out["drift_count"] == 0 and out["max_delta"] == 0 and out["compared"] == 0


def test_parity_tolerance_env_override(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STOCK_PARITY_TOLERANCE", "5")
    assert sp.parity_tolerance() == 5
    monkeypatch.setenv("SHOPIFY_STOCK_PARITY_TOLERANCE", "junk")
    assert sp.parity_tolerance() == 2  # bad value -> default
    monkeypatch.delenv("SHOPIFY_STOCK_PARITY_TOLERANCE", raising=False)
    assert sp.parity_tolerance() == 2


# ---------------------------------------------------------------------------
# Deduped SYSTEM task
# ---------------------------------------------------------------------------

class _FakeRepo:
    """Matches the create_system_task contract: find_many + create."""

    def __init__(self, existing=None):
        self.existing = existing or []
        self.created = []

    def find_many(self, query):
        # create_system_task looks up by source_ref; return our seeded rows.
        return list(self.existing)

    def create(self, doc):
        self.created.append(doc)
        return doc


_DRIFT_SUMMARY = {
    "drift": [
        {"sku": "D", "inventory_item_id": "4", "ims": 20, "shopify": 5, "delta": 15},
        {"sku": "C", "inventory_item_id": "3", "ims": 10, "shopify": 7, "delta": 3},
    ],
    "drift_count": 2,
    "max_delta": 15,
    "tolerance": 2,
}


def test_file_drift_task_creates_when_none_active():
    repo = _FakeRepo(existing=[])
    task = sp.file_drift_task(repo, _DRIFT_SUMMARY)
    assert task is not None
    assert task["source_ref"] == sp._DRIFT_TASK_REF
    assert task["priority"] == "P2"
    assert task["source"] == "SYSTEM"
    assert len(repo.created) == 1


def test_file_drift_task_dedupes_when_active_task_open():
    # An OPEN task already exists for the same source_ref -> no new task.
    repo = _FakeRepo(existing=[{"source_ref": sp._DRIFT_TASK_REF, "status": "OPEN"}])
    task = sp.file_drift_task(repo, _DRIFT_SUMMARY)
    assert task is None
    assert repo.created == []


def test_file_drift_task_refiles_after_prior_resolved():
    # Prior drift task is COMPLETED -> a fresh drift may file again.
    repo = _FakeRepo(existing=[{"source_ref": sp._DRIFT_TASK_REF, "status": "COMPLETED"}])
    task = sp.file_drift_task(repo, _DRIFT_SUMMARY)
    assert task is not None
    assert len(repo.created) == 1


# ---------------------------------------------------------------------------
# Snapshot pruning
# ---------------------------------------------------------------------------

class _FakePruneColl:
    def __init__(self):
        self.deleted_query = None

    def delete_many(self, query):
        self.deleted_query = query
        return types.SimpleNamespace(deleted_count=3)


def test_prune_snapshots_uses_iso_cutoff():
    coll = _FakePruneColl()
    n = sp.prune_snapshots(coll, retention_days=30)
    assert n == 3
    assert "generated_at" in coll.deleted_query
    assert "$lt" in coll.deleted_query["generated_at"]


def test_prune_snapshots_none_coll_is_zero():
    assert sp.prune_snapshots(None) == 0


# ---------------------------------------------------------------------------
# Orchestration smoke (fail-soft, injected boundaries)
# ---------------------------------------------------------------------------

def test_run_parity_tick_files_task_on_drift(monkeypatch):
    # creds present
    monkeypatch.setattr("api.services.shopify_push._has_shopify_creds", lambda db, *a, **k: True)
    # sampled variants
    monkeypatch.setattr(
        sp, "_sample_variants",
        lambda db, limit=500: [
            {"sku": "C", "inventory_item_id": "3"},
            {"sku": "D", "inventory_item_id": "4"},
        ],
    )
    # pooled IMS availability
    monkeypatch.setattr(sp, "_pooled_availability", lambda db, skus: {"C": 10, "D": 20})
    # captured snapshot
    stored = {}
    monkeypatch.setattr(sp, "_store_snapshot", lambda db, snap: stored.update(snap))
    # repo for the drift task
    repo = _FakeRepo(existing=[])
    monkeypatch.setattr(sp, "_task_repo", lambda db: repo)

    async def fake_gql(db, query, variables):
        # Shopify reports C=7 (delta 3), D=5 (delta 15) -> both drift beyond tol 2.
        return {
            "data": {
                "nodes": [
                    {"id": "gid://shopify/InventoryItem/3", "inventoryLevels": {"edges": [
                        {"node": {"location": {"id": "L"}, "quantities": [{"name": "available", "quantity": 7}]}}]}},
                    {"id": "gid://shopify/InventoryItem/4", "inventoryLevels": {"edges": [
                        {"node": {"location": {"id": "L"}, "quantities": [{"name": "available", "quantity": 5}]}}]}},
                ]
            }
        }

    out = _run(sp.run_parity_tick(None, graphql=fake_gql))
    assert out["checked"] is True
    assert out["sampled"] == 2
    assert out["drift_count"] == 2
    assert out["max_delta"] == 15
    assert out["task_filed"] is True
    assert len(repo.created) == 1
    assert stored.get("drift_count") == 2  # snapshot persisted


def test_run_parity_tick_no_creds_is_fail_soft(monkeypatch):
    monkeypatch.setattr("api.services.shopify_push._has_shopify_creds", lambda db, *a, **k: False)
    monkeypatch.setattr(sp, "_store_snapshot", lambda db, snap: None)
    out = _run(sp.run_parity_tick(None))
    assert out["checked"] is False
    assert "creds" in (out["reason"] or "")
