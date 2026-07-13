"""
IMS 2.0 - Cataloguing scorecard + QC sampling test suite (attribution phase 2)
==============================================================================
Covers:
  * GET /products/cataloguing-scorecard -- per-user creations/rate/coverage/
    created_today, approvals (catalog_products promote stamps), QC stats, and
    the corrections metric:
      - CLASSIFIED via product.updated audit rows (before/after keys): only
        CATALOGUING fields count; pricing-only edits NEVER count
      - self-edits never count
      - the 30-days-of-creation recency rule
      - APPROXIMATE via baseline activity-middleware rows (exact spine /
        catalog PUT paths only; engine-door paths excluded)
  * POST /products/qc-samples/generate -- per_user cap, exclusion of items
    already PENDING for that cataloguer, batch summary shape
  * GET /products/qc-samples -- status filter + per-batch progress
  * POST /products/qc-samples/{item_id}/verdict -- self-QC 403, immutability
    409 for non-admin re-verdict, ADMIN overwrite stamps overwritten_by,
    error_fields validation + persistence
  * RBAC: policy rows via check_access + live 403 for SALES_STAFF

Handler-level tests run against a small in-memory Mongo fake (find/find_one/
insert_many/update_one with $gte/$in/$nin support -- exactly the operator set
the handlers use). The role-gate tests use the shared TestClient fixtures.
"""

# pylint: disable=redefined-outer-name,unused-argument,protected-access

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from fastapi import HTTPException  # noqa: E402

from api.routers import products as products_mod  # noqa: E402


# ============================================================================
# In-memory Mongo fake (only the operators the new handlers use)
# ============================================================================


def _matches(doc: Dict, flt: Optional[Dict]) -> bool:
    for key, cond in (flt or {}).items():
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op == "$gte":
                    if val is None or not val >= arg:
                        return False
                elif op == "$lte":
                    if val is None or not val <= arg:
                        return False
                elif op == "$in":
                    if val not in arg:
                        return False
                elif op == "$nin":
                    if val in arg:
                        return False
                else:  # unsupported operator -> loud failure in tests
                    raise AssertionError(f"FakeCollection: unsupported op {op}")
        else:
            if val != cond:
                return False
    return True


class FakeCollection:
    def __init__(self, docs: Optional[List[Dict]] = None):
        self.docs: List[Dict] = [dict(d) for d in (docs or [])]

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self.docs if _matches(d, flt)]

    def find_one(self, flt=None, projection=None):
        for d in self.docs:
            if _matches(d, flt):
                return dict(d)
        return None

    def insert_many(self, docs):
        self.docs.extend(dict(d) for d in docs)

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, flt, update):
        for d in self.docs:
            if _matches(d, flt):
                d.update(update.get("$set", {}))
                return


class FakeDB:
    is_connected = True

    def __init__(self, **collections):
        self._colls = {name: FakeCollection(docs) for name, docs in collections.items()}

    def get_collection(self, name):
        return self._colls.setdefault(name, FakeCollection())


def _manager(uid="mgr-1", name="manager", roles=("STORE_MANAGER",)):
    return {"user_id": uid, "username": name, "roles": list(roles)}


def _patch_db(monkeypatch, db):
    from api import dependencies as deps_mod

    monkeypatch.setattr(deps_mod, "get_db", lambda: db)


NOW = datetime.now()


def _product(pid, uid, name=None, category="FRAME", created_days_ago=5, **extra):
    doc = {
        "product_id": pid,
        "created_by": uid,
        "created_by_name": name,
        "category": category,
        "brand": "Acme",
        "model": f"M-{pid}",
        "sku": f"SKU-{pid}",
        "created_at": NOW - timedelta(days=created_days_ago),
        "is_active": True,
    }
    doc.update(extra)
    return doc


def _run_scorecard(monkeypatch, db, days=30):
    _patch_db(monkeypatch, db)
    return asyncio.run(
        products_mod.cataloguing_scorecard(days=days, current_user=_manager())
    )


def _rows_by_user(out):
    return {r["user_id"]: r for r in out["rows"]}


# ============================================================================
# 1. Scorecard: creations / rate / coverage / created_today / approvals / qc
# ============================================================================


class TestScorecardBasics:
    def test_counts_rate_coverage_and_today(self, monkeypatch):
        db = FakeDB(
            products=[
                _product("p1", "u-a", name="asha", category="FRAME"),
                _product("p2", "u-a", name="asha", category="SUNGLASS"),
                _product("p3", "u-a", name="asha", category="FRAME",
                         created_days_ago=0),
                _product("p4", "u-b", name="bala", category="FRAME"),
                # outside the window -> ignored entirely
                _product("p5", "u-a", name="asha", created_days_ago=40),
            ]
        )
        out = _run_scorecard(monkeypatch, db, days=30)
        rows = _rows_by_user(out)
        a = rows["u-a"]
        assert a["name"] == "asha"
        assert a["created_count"] == 3
        assert a["created_today"] == 1
        assert a["per_day_rate"] == round(3 / 30.0, 2)
        assert a["category_coverage"] == {"FRAME": 2, "SUNGLASS": 1}
        assert rows["u-b"]["created_count"] == 1
        # sorted by created_count desc
        assert out["rows"][0]["user_id"] == "u-a"

    def test_approvals_from_promote_stamps(self, monkeypatch):
        recent = (NOW - timedelta(days=2)).isoformat()
        old = (NOW - timedelta(days=90)).isoformat()
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            catalog_products=[
                {"id": "c1", "promoted_by": "u-a", "promoted_at": recent},
                {"id": "c2", "promoted_by": "u-a", "promoted_at": recent},
                {"id": "c3", "promoted_by": "u-a", "promoted_at": old},
                {"id": "c4", "promoted_by": "u-approver", "promoted_at": recent},
            ],
        )
        out = _run_scorecard(monkeypatch, db, days=30)
        rows = _rows_by_user(out)
        assert rows["u-a"]["approvals"] == 2
        # a user whose only activity is approving still gets a row
        assert rows["u-approver"]["approvals"] == 1
        assert rows["u-approver"]["created_count"] == 0

    def test_qc_stats_from_verdicts(self, monkeypatch):
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            qc_samples=[
                {"cataloguer_id": "u-a", "verdict": "OK",
                 "status": "REVIEWED", "sampled_at": NOW - timedelta(days=1)},
                {"cataloguer_id": "u-a", "verdict": "OK",
                 "status": "REVIEWED", "sampled_at": NOW - timedelta(days=1)},
                {"cataloguer_id": "u-a", "verdict": "ERROR",
                 "status": "REVIEWED", "sampled_at": NOW - timedelta(days=1)},
                # pending -> not counted until reviewed
                {"cataloguer_id": "u-a", "status": "PENDING",
                 "sampled_at": NOW - timedelta(days=1)},
            ],
        )
        out = _run_scorecard(monkeypatch, db, days=30)
        qc = _rows_by_user(out)["u-a"]["qc"]
        assert qc == {"sampled": 3, "errors": 1, "error_rate": 33.3}

    def test_qc_zeros_when_no_samples(self, monkeypatch):
        db = FakeDB(products=[_product("p1", "u-a", name="asha")])
        out = _run_scorecard(monkeypatch, db, days=30)
        assert _rows_by_user(out)["u-a"]["qc"] == {
            "sampled": 0, "errors": 0, "error_rate": 0.0,
        }

    def test_no_db_fail_soft(self, monkeypatch):
        _patch_db(monkeypatch, None)
        out = asyncio.run(
            products_mod.cataloguing_scorecard(days=30, current_user=_manager())
        )
        assert out["rows"] == []


# ============================================================================
# 2. Scorecard: corrections classification
# ============================================================================


def _audit_domain(pid, actor, days_after_creation, keys, created_days_ago=5):
    """A product.updated (engine-door) audit row with before/after keys."""
    ts = NOW - timedelta(days=created_days_ago) + timedelta(days=days_after_creation)
    return {
        "action": "product.updated",
        "entity_type": "product",
        "entity_id": pid,
        "actor": actor,
        "user_id": actor,
        "timestamp": ts,
        "ts": ts.isoformat(),
        "before": {k: "old" for k in keys},
        "after": {k: "new" for k in keys},
    }


def _audit_baseline(pid, actor, days_after_creation, path=None,
                    method="PUT", created_days_ago=5):
    """A baseline activity-middleware row (no field detail)."""
    ts = NOW - timedelta(days=created_days_ago) + timedelta(days=days_after_creation)
    return {
        "action": "UPDATE",
        "entity_type": "PRODUCT",
        "entity_id": pid,
        "user_id": actor,
        "timestamp": ts,
        "method": method,
        "path": path or f"/api/v1/products/{pid}",
        "source": "middleware",
    }


class TestScorecardCorrections:
    def test_classified_cataloguing_edit_counts(self, monkeypatch):
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            audit_logs=[_audit_domain("p1", "u-b", 1, ["brand"])],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db))["u-a"]
        assert row["corrections_received"] == 1
        assert row["corrections_classified"] == 1
        assert row["corrections_approximate"] == 0

    def test_pricing_only_edit_never_counts(self, monkeypatch):
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            audit_logs=[
                _audit_domain("p1", "u-b", 1, ["mrp", "offer_price"]),
                _audit_domain("p1", "u-b", 2, ["cost_price", "is_active"]),
                _audit_domain("p1", "u-b", 3, ["discount_category"]),
            ],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db))["u-a"]
        assert row["corrections_received"] == 0

    def test_dotted_attributes_key_counts(self, monkeypatch):
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            audit_logs=[_audit_domain("p1", "u-b", 1, ["attributes.colour_code"])],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db))["u-a"]
        assert row["corrections_classified"] == 1

    def test_self_edit_never_counts(self, monkeypatch):
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            audit_logs=[_audit_domain("p1", "u-a", 1, ["brand"])],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db))["u-a"]
        assert row["corrections_received"] == 0

    def test_edit_after_30_days_of_creation_never_counts(self, monkeypatch):
        # 90-day window so an 80-day-old product is still on the scorecard,
        # but its day-40 edit is beyond the 30-days-of-creation recency rule.
        db = FakeDB(
            products=[
                _product("p-old", "u-a", name="asha", created_days_ago=80),
                _product("p-old2", "u-a", name="asha", created_days_ago=80),
            ],
            audit_logs=[
                _audit_domain("p-old", "u-b", 40, ["brand"], created_days_ago=80),
                _audit_domain("p-old2", "u-b", 5, ["brand"], created_days_ago=80),
            ],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db, days=90))["u-a"]
        assert row["corrections_received"] == 1  # only the day-5 edit

    def test_baseline_row_counts_as_approximate(self, monkeypatch):
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            audit_logs=[_audit_baseline("p1", "u-b", 2)],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db))["u-a"]
        assert row["corrections_received"] == 1
        assert row["corrections_approximate"] == 1
        assert row["corrections_classified"] == 0

    def test_baseline_engine_door_path_excluded(self, monkeypatch):
        # /products/master/{id} edits emit a classified domain row; their
        # baseline twin must not be approximated (double count guard).
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            audit_logs=[
                _audit_baseline("p1", "u-b", 2, path="/api/v1/products/master/p1"),
            ],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db))["u-a"]
        assert row["corrections_received"] == 0

    def test_classified_and_baseline_same_product_counts_once(self, monkeypatch):
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            audit_logs=[
                _audit_domain("p1", "u-b", 1, ["brand"]),
                _audit_baseline("p1", "u-b", 1),
            ],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db))["u-a"]
        assert row["corrections_received"] == 1
        assert row["corrections_classified"] == 1
        assert row["corrections_approximate"] == 0

    def test_catalog_put_path_counts_as_approximate(self, monkeypatch):
        db = FakeDB(
            products=[_product("p1", "u-a", name="asha")],
            audit_logs=[
                _audit_baseline("p1", "u-b", 1, path="/api/v1/catalog/products/p1"),
            ],
        )
        row = _rows_by_user(_run_scorecard(monkeypatch, db))["u-a"]
        assert row["corrections_approximate"] == 1


# ============================================================================
# 3. QC sample generation
# ============================================================================


def _run_generate(monkeypatch, db, days=7, per_user=10, user=None):
    _patch_db(monkeypatch, db)
    body = products_mod.QcGenerateRequest(days=days, per_user=per_user)
    return asyncio.run(
        products_mod.generate_qc_samples(body, current_user=user or _manager())
    )


class TestQcGenerate:
    def test_respects_per_user_cap(self, monkeypatch):
        db = FakeDB(
            products=[_product(f"p{i}", "u-a", name="asha", created_days_ago=1)
                      for i in range(5)]
        )
        out = _run_generate(monkeypatch, db, per_user=3)
        assert out["total_items"] == 3
        assert out["cataloguers"] == [
            {"user_id": "u-a", "name": "asha", "sampled": 3}
        ]
        items = db.get_collection("qc_samples").docs
        assert len(items) == 3
        assert all(i["status"] == "PENDING" for i in items)
        assert all(i["batch_id"] == out["batch_id"] for i in items)
        assert all(i["cataloguer_id"] == "u-a" for i in items)
        # sampled products are distinct and from the creator's pool
        pids = {i["product_id"] for i in items}
        assert len(pids) == 3
        assert pids <= {f"p{i}" for i in range(5)}

    def test_excludes_already_pending_items(self, monkeypatch):
        db = FakeDB(
            products=[_product(f"p{i}", "u-a", name="asha", created_days_ago=1)
                      for i in range(3)],
            qc_samples=[
                {"item_id": "old-1", "batch_id": "b0", "product_id": "p0",
                 "cataloguer_id": "u-a", "status": "PENDING",
                 "sampled_at": NOW - timedelta(days=1)},
            ],
        )
        out = _run_generate(monkeypatch, db, per_user=10)
        new_items = [
            i for i in db.get_collection("qc_samples").docs
            if i.get("batch_id") == out["batch_id"]
        ]
        assert out["total_items"] == 2
        assert {i["product_id"] for i in new_items} == {"p1", "p2"}

    def test_reviewed_item_can_be_resampled(self, monkeypatch):
        # Only OPEN (PENDING) items block re-sampling; a reviewed one doesn't.
        db = FakeDB(
            products=[_product("p0", "u-a", name="asha", created_days_ago=1)],
            qc_samples=[
                {"item_id": "old-1", "batch_id": "b0", "product_id": "p0",
                 "cataloguer_id": "u-a", "status": "REVIEWED", "verdict": "OK",
                 "sampled_at": NOW - timedelta(days=1)},
            ],
        )
        out = _run_generate(monkeypatch, db, per_user=10)
        assert out["total_items"] == 1

    def test_multiple_cataloguers_sampled_independently(self, monkeypatch):
        db = FakeDB(
            products=(
                [_product(f"a{i}", "u-a", name="asha", created_days_ago=1)
                 for i in range(4)]
                + [_product(f"b{i}", "u-b", name="bala", created_days_ago=1)
                   for i in range(2)]
            )
        )
        out = _run_generate(monkeypatch, db, per_user=3)
        assert out["total_items"] == 5  # 3 for u-a (cap), 2 for u-b (pool)
        by_user = {c["user_id"]: c["sampled"] for c in out["cataloguers"]}
        assert by_user == {"u-a": 3, "u-b": 2}

    def test_no_db_is_503(self, monkeypatch):
        _patch_db(monkeypatch, None)
        body = products_mod.QcGenerateRequest()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                products_mod.generate_qc_samples(body, current_user=_manager())
            )
        assert exc.value.status_code == 503


# ============================================================================
# 4. QC list
# ============================================================================


class TestQcList:
    def _db(self):
        return FakeDB(
            qc_samples=[
                {"item_id": "i1", "batch_id": "b1", "product_id": "p1",
                 "cataloguer_id": "u-a", "status": "PENDING",
                 "sampled_at": NOW - timedelta(days=2)},
                {"item_id": "i2", "batch_id": "b1", "product_id": "p2",
                 "cataloguer_id": "u-a", "status": "REVIEWED", "verdict": "OK",
                 "sampled_at": NOW - timedelta(days=2)},
                {"item_id": "i3", "batch_id": "b2", "product_id": "p3",
                 "cataloguer_id": "u-b", "status": "PENDING",
                 "sampled_at": NOW - timedelta(days=1)},
            ]
        )

    def _run(self, monkeypatch, db, **kw):
        _patch_db(monkeypatch, db)
        params = {"status": None, "batch_id": None, "cataloguer": None,
                  "limit": 500, "current_user": _manager()}
        params.update(kw)
        return asyncio.run(products_mod.list_qc_samples(**params))

    def test_status_filter_and_batch_progress(self, monkeypatch):
        out = self._run(monkeypatch, self._db(), status="PENDING")
        assert {i["item_id"] for i in out["items"]} == {"i1", "i3"}
        # batch progress is computed BEFORE the status narrow
        progress = {b["batch_id"]: (b["reviewed"], b["total"]) for b in out["batches"]}
        assert progress == {"b1": (1, 2), "b2": (0, 1)}
        # newest batch first
        assert out["batches"][0]["batch_id"] == "b2"

    def test_cataloguer_filter(self, monkeypatch):
        out = self._run(monkeypatch, self._db(), cataloguer="u-b")
        assert {i["item_id"] for i in out["items"]} == {"i3"}

    def test_newest_first_ordering(self, monkeypatch):
        out = self._run(monkeypatch, self._db())
        assert out["items"][0]["item_id"] == "i3"

    def test_no_db_fail_soft(self, monkeypatch):
        out = self._run(monkeypatch, None)
        assert out == {"items": [], "total": 0, "batches": []}


# ============================================================================
# 5. QC verdict
# ============================================================================


def _verdict_db(**item_extra):
    item = {
        "item_id": "i1",
        "batch_id": "b1",
        "product_id": "p1",
        "cataloguer_id": "u-cataloguer",
        "cataloguer_name": "asha",
        "status": "PENDING",
        "sampled_at": NOW - timedelta(days=1),
    }
    item.update(item_extra)
    return FakeDB(qc_samples=[item])


def _run_verdict(monkeypatch, db, user, verdict="OK", error_fields=None, note=None,
                 item_id="i1"):
    _patch_db(monkeypatch, db)
    body = products_mod.QcVerdictRequest(
        verdict=verdict, error_fields=error_fields, note=note
    )
    return asyncio.run(
        products_mod.qc_sample_verdict(item_id, body, current_user=user)
    )


class TestQcVerdict:
    def test_ok_verdict_stamps_reviewer(self, monkeypatch):
        db = _verdict_db()
        out = _run_verdict(monkeypatch, db, _manager("mgr-2", "meena"))
        assert out["verdict"] == "OK"
        assert out["status"] == "REVIEWED"
        assert out["reviewed_by"] == "mgr-2"
        assert out["reviewed_by_name"] == "meena"
        assert out["error_fields"] == []

    def test_error_verdict_persists_error_fields_and_note(self, monkeypatch):
        db = _verdict_db()
        out = _run_verdict(
            monkeypatch, db, _manager("mgr-2", "meena"),
            verdict="ERROR", error_fields=["category", "hsn_gst"],
            note="wrong HSN",
        )
        assert out["verdict"] == "ERROR"
        assert out["error_fields"] == ["category", "hsn_gst"]
        assert out["note"] == "wrong HSN"

    def test_self_qc_is_403(self, monkeypatch):
        db = _verdict_db()
        with pytest.raises(HTTPException) as exc:
            _run_verdict(monkeypatch, db, _manager("u-cataloguer", "asha"))
        assert exc.value.status_code == 403

    def test_self_qc_403_even_for_superadmin(self, monkeypatch):
        db = _verdict_db()
        with pytest.raises(HTTPException) as exc:
            _run_verdict(
                monkeypatch, db,
                _manager("u-cataloguer", "asha", roles=("SUPERADMIN",)),
            )
        assert exc.value.status_code == 403

    def test_reverdict_is_409_for_non_admin(self, monkeypatch):
        db = _verdict_db(verdict="OK", status="REVIEWED", reviewed_by="mgr-2")
        with pytest.raises(HTTPException) as exc:
            _run_verdict(monkeypatch, db, _manager("mgr-3", "kiran"))
        assert exc.value.status_code == 409

    def test_admin_overwrite_stamps_overwritten_by(self, monkeypatch):
        db = _verdict_db(
            verdict="OK", status="REVIEWED",
            reviewed_by="mgr-2", reviewed_by_name="meena",
        )
        out = _run_verdict(
            monkeypatch, db, _manager("adm-1", "admin", roles=("ADMIN",)),
            verdict="ERROR", error_fields=["images"],
        )
        assert out["verdict"] == "ERROR"
        assert out["overwritten_by"] == "adm-1"
        assert out["overwritten_by_name"] == "admin"
        assert out["previous_verdict"]["verdict"] == "OK"
        assert out["previous_verdict"]["reviewed_by"] == "mgr-2"

    def test_unknown_item_is_404(self, monkeypatch):
        db = _verdict_db()
        with pytest.raises(HTTPException) as exc:
            _run_verdict(monkeypatch, db, _manager("mgr-2"), item_id="nope")
        assert exc.value.status_code == 404

    def test_invalid_verdict_rejected(self):
        with pytest.raises(ValueError):
            products_mod.QcVerdictRequest(verdict="MAYBE")

    def test_invalid_error_field_rejected(self):
        with pytest.raises(ValueError):
            products_mod.QcVerdictRequest(verdict="ERROR", error_fields=["mrp"])

    def test_ok_verdict_clears_error_fields(self, monkeypatch):
        db = _verdict_db()
        out = _run_verdict(
            monkeypatch, db, _manager("mgr-2"),
            verdict="OK", error_fields=["category"],
        )
        assert out["error_fields"] == []


# ============================================================================
# 6. RBAC
# ============================================================================


_NEW_ROUTES = (
    ("GET", "/api/v1/products/cataloguing-scorecard"),
    ("POST", "/api/v1/products/qc-samples/generate"),
    ("GET", "/api/v1/products/qc-samples"),
    ("POST", "/api/v1/products/qc-samples/{item_id}/verdict"),
)


class TestScorecardQcRbac:
    def test_policy_rows(self):
        from api.services.rbac_policy import check_access

        for method, path in _NEW_ROUTES:
            for role in ("SUPERADMIN", "ADMIN", "AREA_MANAGER",
                         "STORE_MANAGER", "CATALOG_MANAGER"):
                assert check_access(method, path, [role]) is True, (method, path, role)
            for role in ("SALES_STAFF", "OPTOMETRIST", "CASHIER"):
                assert check_access(method, path, [role]) is False, (method, path, role)

    def test_sales_staff_403_live(self, client, staff_headers):
        assert client.get(
            "/api/v1/products/cataloguing-scorecard", headers=staff_headers
        ).status_code == 403
        assert client.get(
            "/api/v1/products/qc-samples", headers=staff_headers
        ).status_code == 403
        assert client.post(
            "/api/v1/products/qc-samples/generate", headers=staff_headers,
            json={"days": 7, "per_user": 10},
        ).status_code == 403
        assert client.post(
            "/api/v1/products/qc-samples/xyz/verdict", headers=staff_headers,
            json={"verdict": "OK"},
        ).status_code == 403

    def test_manager_scorecard_200_shape(self, client, auth_headers):
        resp = client.get(
            "/api/v1/products/cataloguing-scorecard", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["days"] == 30
        assert isinstance(body["rows"], list)

    def test_manager_qc_list_200_shape(self, client, auth_headers):
        resp = client.get("/api/v1/products/qc-samples", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body and "batches" in body
