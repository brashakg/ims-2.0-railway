"""IDOR + IST hardening: orders / returns / tasks (audit 2026-06-10).

(1) orders.py P1  -- every order MUTATION (PUT, items, confirm, payments,
    ready, deliver, cancel, invoice, bopis-transfer) now mirrors the
    validate_store_access guard GET /{order_id} already had. /cancel is
    additionally role-gated to POS_WRITE_ROLES.
(2) returns.py P1 -- create_return validates body.store_id AND the resolved
    order's store ownership; GET /{return_id} is store-scoped.
(3) tasks.py P2   -- single-task endpoints check the task's store via
    can_access_store_scoped (no-store task = global -> allowed);
    /my-tasks + /overdue pass limit=0 (no silent 100-cap).
(4) tz            -- the period-lock day for order/return creation is the IST
    business day (ist_today()), not the UTC date.today().

Every repo/db accessor is monkeypatched; every doc is seeded in-memory; no
live DB. Assertions are field-level (no whole-JSON substring matching).

CRITICAL same-store proof: the guards are purely additive -- a same-store
actor must still succeed on EVERY guarded endpoint with identical money math
(POS safety). Asserted explicitly below.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import orders as orders_mod  # noqa: E402
from api.routers import returns as returns_mod  # noqa: E402
from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

# Reuse the battle-tested returns fakes (same pattern as test_returns_refund_cap).
from tests.test_returns_gst_refund import (  # noqa: E402
    _FakeColl,
    _FakeCustomerRepo,
    _FakeOrderRepo,
    _qa_order,
    _qa_payload,
)

OWN_STORE = "BV-PUN-01"
OTHER_STORE = "BV-BOK-01"


def _user_dep(roles, store):
    async def _u():
        return {
            "user_id": "u-test",
            "roles": roles,
            "active_store_id": store,
            "store_ids": [store],
        }

    return _u


# ===========================================================================
# 1. ORDERS -- mutation endpoints mirror the GET store guard
# ===========================================================================


class _OrderRepo:
    """Seeded single-order repo recording every write (mutation proof)."""

    def __init__(self, order):
        self.order = order
        self.updates = []
        self.status_updates = []
        self.payments = []

    def find_by_id(self, oid):
        return dict(self.order) if self.order.get("order_id") == oid else None

    def update(self, oid, data):
        if self.order.get("order_id") != oid:
            return False
        self.updates.append(data)
        self.order.update(data)
        return True

    def update_status(self, oid, status, user_id=None):
        if self.order.get("order_id") != oid:
            return False
        self.status_updates.append(status)
        self.order["status"] = status
        return True

    def add_payment(self, oid, payment_data):
        if self.order.get("order_id") != oid:
            return False
        self.payments.append(payment_data)
        self.order.setdefault("payments", []).append(payment_data)
        self.order["payment_status"] = "PARTIAL"
        return True


def _seed_order(**over):
    base = {
        "order_id": "ORD-1",
        "order_number": "ORD-X-2026-AAAA01",
        "store_id": OTHER_STORE,
        "status": "DRAFT",
        "customer_id": "walkin-1",
        "payment_status": "UNPAID",
        "grand_total": 1000.0,
        "balance_due": 1000.0,
        "amount_paid": 0.0,
        "items": [
            {
                "item_id": "li1",
                "item_type": "FRAME",
                "product_id": "PRD-1",
                "product_name": "Frame",
                "quantity": 1,
                "unit_price": 1000.0,
                "item_total": 1000.0,
            }
        ],
        "payments": [],
    }
    base.update(over)
    return base


def _orders_client(monkeypatch, order, roles, store):
    app = FastAPI()
    app.include_router(orders_mod.router, prefix="/api/v1/orders")
    repo = _OrderRepo(order)
    monkeypatch.setattr(orders_mod, "get_order_repository", lambda: repo)
    monkeypatch.setattr(orders_mod, "get_customer_repository", lambda: None)
    monkeypatch.setattr(orders_mod, "get_product_repository", lambda: None)
    monkeypatch.setattr(orders_mod, "get_stock_repository", lambda: None)
    monkeypatch.setattr(orders_mod, "_get_db", lambda: None)
    app.dependency_overrides[get_current_user] = _user_dep(roles, store)
    return TestClient(app), repo


_CANCEL_QS = {"reason": "customer changed mind"}


def test_order_mutations_cross_store_403(monkeypatch):
    """A STORE_MANAGER from another store gets 403 on EVERY order mutation,
    and the order is not touched."""
    client, repo = _orders_client(
        monkeypatch, _seed_order(), ["STORE_MANAGER"], OWN_STORE
    )
    calls = [
        ("put", "/api/v1/orders/ORD-1", {"json": {"notes": "hijack"}}),
        (
            "post",
            "/api/v1/orders/ORD-1/items",
            {"json": {"item_type": "FRAME", "product_id": "custom-x", "unit_price": 10}},
        ),
        ("delete", "/api/v1/orders/ORD-1/items/li1", {}),
        ("post", "/api/v1/orders/ORD-1/confirm", {}),
        (
            "post",
            "/api/v1/orders/ORD-1/payments",
            {"json": {"method": "CASH", "amount": 100}},
        ),
        ("post", "/api/v1/orders/ORD-1/ready", {}),
        ("post", "/api/v1/orders/ORD-1/deliver", {}),
        ("post", "/api/v1/orders/ORD-1/cancel", {"params": _CANCEL_QS}),
        ("get", "/api/v1/orders/ORD-1/invoice", {}),
        (
            "post",
            "/api/v1/orders/ORD-1/bopis-transfer",
            {
                "json": {
                    "items": [
                        {
                            "product_id": "PRD-1",
                            "quantity": 1,
                            "unit_price": 10,
                            "source_store_id": OTHER_STORE,
                        }
                    ],
                    "pickup_store_id": OWN_STORE,
                }
            },
        ),
    ]
    for method, url, kw in calls:
        r = getattr(client, method)(url, **kw)
        assert r.status_code == 403, f"{method.upper()} {url} -> {r.status_code}"
        assert "store" in r.json()["detail"].lower()
    # Mutation proof: nothing was written.
    assert repo.updates == []
    assert repo.status_updates == []
    assert repo.payments == []
    assert repo.order["status"] == "DRAFT"


def test_order_payments_idempotent_replay_cross_store_403(monkeypatch):
    """The idempotency replay path must not leak another store's payment row."""
    order = _seed_order(
        payments=[{"payment_id": "PMT-1", "idempotency_key": "IK-1", "amount": 100}]
    )
    client, _ = _orders_client(monkeypatch, order, ["STORE_MANAGER"], OWN_STORE)
    r = client.post(
        "/api/v1/orders/ORD-1/payments",
        json={"method": "CASH", "amount": 100},
        headers={"Idempotency-Key": "IK-1"},
    )
    assert r.status_code == 403
    assert "payment_id" not in (r.json().get("detail") or "")


def test_order_put_same_store_still_succeeds(monkeypatch):
    client, repo = _orders_client(
        monkeypatch, _seed_order(store_id=OWN_STORE), ["STORE_MANAGER"], OWN_STORE
    )
    r = client.put("/api/v1/orders/ORD-1", json={"notes": "fit note"})
    assert r.status_code == 200
    assert r.json()["message"] == "Order updated"
    assert repo.updates and repo.updates[0]["notes"] == "fit note"


def test_order_payments_same_store_still_succeeds(monkeypatch):
    """POS safety: a same-store CASH payment records exactly as before --
    same amount, same auto-confirm, no math change."""
    client, repo = _orders_client(
        monkeypatch, _seed_order(store_id=OWN_STORE), ["SALES_CASHIER"], OWN_STORE
    )
    r = client.post("/api/v1/orders/ORD-1/payments", json={"method": "CASH", "amount": 250})
    assert r.status_code == 200
    body = r.json()
    assert body["amount"] == 250
    assert len(repo.payments) == 1
    assert repo.payments[0]["amount"] == 250
    assert repo.payments[0]["method"] == "CASH"
    # DRAFT auto-confirm on first payment (pre-existing behavior, untouched).
    assert repo.status_updates == ["CONFIRMED"]


def test_order_cancel_same_store_still_succeeds(monkeypatch):
    client, repo = _orders_client(
        monkeypatch, _seed_order(store_id=OWN_STORE), ["STORE_MANAGER"], OWN_STORE
    )
    r = client.post("/api/v1/orders/ORD-1/cancel", params=_CANCEL_QS)
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    assert repo.order["status"] == "CANCELLED"


def test_order_deliver_same_store_still_succeeds(monkeypatch):
    order = _seed_order(store_id=OWN_STORE, status="READY", payment_status="PARTIAL")
    client, repo = _orders_client(monkeypatch, order, ["STORE_MANAGER"], OWN_STORE)
    r = client.post("/api/v1/orders/ORD-1/deliver")
    assert r.status_code == 200
    assert r.json()["status"] == "DELIVERED"
    assert repo.status_updates == ["DELIVERED"]


def test_order_confirm_ready_same_store_still_succeed(monkeypatch):
    client, repo = _orders_client(
        monkeypatch, _seed_order(store_id=OWN_STORE), ["SALES_STAFF"], OWN_STORE
    )
    assert client.post("/api/v1/orders/ORD-1/confirm").status_code == 200
    assert repo.order["status"] == "CONFIRMED"
    # CONFIRMED -> READY is a valid transition.
    assert client.post("/api/v1/orders/ORD-1/ready").status_code == 200
    assert repo.order["status"] == "READY"


def test_order_admin_cross_store_bypass(monkeypatch):
    """SUPERADMIN/ADMIN keep their cross-store reach on mutations."""
    client, repo = _orders_client(monkeypatch, _seed_order(), ["ADMIN"], "BV-HQ")
    r = client.put("/api/v1/orders/ORD-1", json={"notes": "HQ note"})
    assert r.status_code == 200
    r2 = client.post("/api/v1/orders/ORD-1/cancel", params=_CANCEL_QS)
    assert r2.status_code == 200
    assert repo.order["status"] == "CANCELLED"


def test_order_cancel_role_gate_blocks_non_pos_roles(monkeypatch):
    """/cancel was previously open to ANY authenticated role; now POS-tier only.
    Same-store ACCOUNTANT / WORKSHOP_STAFF / OPTOMETRIST are 403'd."""
    for role in ("ACCOUNTANT", "WORKSHOP_STAFF", "OPTOMETRIST", "CASHIER"):
        client, repo = _orders_client(
            monkeypatch, _seed_order(store_id=OWN_STORE), [role], OWN_STORE
        )
        r = client.post("/api/v1/orders/ORD-1/cancel", params=_CANCEL_QS)
        assert r.status_code == 403, f"{role} -> {r.status_code}"
        assert "not permitted" in r.json()["detail"]
        assert repo.order["status"] == "DRAFT"


def test_order_cancel_policy_row_matches_pos_roles():
    """The rbac_policy registry row stays in sync with the in-function gate."""
    from api.services import rbac_policy as rbac

    entry = rbac.policy_for("POST", "/api/v1/orders/{order_id}/cancel")
    assert entry is not None
    assert sorted(entry["allowed"]) == sorted(orders_mod.POS_WRITE_ROLES)


# ===========================================================================
# 2. ORDERS + RETURNS -- period lock uses the IST business day
# ===========================================================================

_IST_SENTINEL = date(2026, 6, 1)


def _patch_period_lock(monkeypatch):
    captured = {}

    def _fake_check(db, day):
        captured["day"] = day
        raise HTTPException(status_code=423, detail="PERIOD_LOCKED_TEST")

    monkeypatch.setattr("api.routers.finance.check_period_locked", _fake_check)
    monkeypatch.setattr("api.utils.ist.ist_today", lambda: _IST_SENTINEL)
    return captured


def test_order_create_period_lock_receives_ist_day(monkeypatch):
    """check_period_locked must receive ist_today(), not the UTC date.today() --
    at 01-Jun 02:00 IST the UTC day is still 31-May, which falsely blocked POS
    for 5.5h after a month-lock."""
    captured = _patch_period_lock(monkeypatch)
    app = FastAPI()
    app.include_router(orders_mod.router, prefix="/api/v1/orders")
    monkeypatch.setattr(orders_mod, "get_order_repository", lambda: None)
    monkeypatch.setattr(orders_mod, "get_customer_repository", lambda: None)
    monkeypatch.setattr(orders_mod, "get_product_repository", lambda: None)
    monkeypatch.setattr(orders_mod, "_get_db", lambda: object())
    app.dependency_overrides[get_current_user] = _user_dep(
        ["STORE_MANAGER"], OWN_STORE
    )
    client = TestClient(app)
    r = client.post(
        "/api/v1/orders",
        json={
            "customer_id": "walkin-1",
            "items": [
                {"item_type": "FRAME", "product_id": "custom-x", "unit_price": 100}
            ],
        },
    )
    assert r.status_code == 423
    assert captured["day"] == _IST_SENTINEL


def test_return_create_period_lock_receives_ist_day(monkeypatch):
    captured = _patch_period_lock(monkeypatch)
    app = FastAPI()
    app.include_router(returns_mod.router, prefix="/api/v1/returns")
    monkeypatch.setattr(returns_mod, "_get_db", lambda: object())
    monkeypatch.setattr(returns_mod, "get_order_repository", lambda: None)
    app.dependency_overrides[get_current_user] = _user_dep(
        ["STORE_MANAGER"], OWN_STORE
    )
    client = TestClient(app)
    r = client.post("/api/v1/returns", json=_qa_payload())
    assert r.status_code == 423
    assert captured["day"] == _IST_SENTINEL


# ===========================================================================
# 3. RETURNS -- cross-store refund creation blocked; reads store-scoped
# ===========================================================================


def _returns_ctx(monkeypatch, *, order_store=OWN_STORE, seed_return=None):
    """Returns-router app with the seeded QA order stamped to `order_store`."""
    app = FastAPI()
    app.include_router(returns_mod.router, prefix="/api/v1/returns")

    order = _qa_order()
    order["store_id"] = order_store
    order["status"] = "DELIVERED"
    order_repo = _FakeOrderRepo(order)
    customer_repo = _FakeCustomerRepo()
    returns_coll = _FakeColl()
    if seed_return:
        returns_coll.docs.append(dict(seed_return))
    ledger_coll = _FakeColl()

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            return {
                "returns": returns_coll,
                "credit_note_ledger": ledger_coll,
            }.get(name, _FakeColl())

    fake_db = _FakeDB()
    monkeypatch.setattr(returns_mod, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(returns_mod, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(returns_mod, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_mod, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )
    return app, returns_coll, customer_repo


def _returns_client(app, roles, store):
    app.dependency_overrides[get_current_user] = _user_dep(roles, store)
    return TestClient(app)


def test_return_create_cross_store_body_store_403(monkeypatch):
    """body.store_id pointing at ANOTHER store is rejected before any money
    movement (previously trusted as-is)."""
    app, returns_coll, customer_repo = _returns_ctx(monkeypatch, order_store=OTHER_STORE)
    client = _returns_client(app, ["STORE_MANAGER"], OWN_STORE)
    r = client.post("/api/v1/returns", json=_qa_payload(store_id=OTHER_STORE))
    assert r.status_code == 403
    assert returns_coll.docs == []  # no return persisted
    assert customer_repo.updates == []  # no store credit issued


def test_return_create_foreign_order_403(monkeypatch):
    """Even with the caller's OWN store in the body, a refund against another
    store's ORDER is rejected (the _resolve_order ownership gap)."""
    app, returns_coll, customer_repo = _returns_ctx(monkeypatch, order_store=OTHER_STORE)
    client = _returns_client(app, ["STORE_MANAGER"], OWN_STORE)
    r = client.post("/api/v1/returns", json=_qa_payload(store_id=OWN_STORE))
    assert r.status_code == 403
    assert "store" in r.json()["detail"].lower()
    assert returns_coll.docs == []
    assert customer_repo.updates == []


def test_return_create_same_store_still_succeeds(monkeypatch):
    """POS safety: a same-store return succeeds with IDENTICAL money math
    (GST-inclusive gross 1404.20 on the QA order -- unchanged)."""
    app, returns_coll, _ = _returns_ctx(monkeypatch, order_store=OWN_STORE)
    client = _returns_client(app, ["STORE_MANAGER"], OWN_STORE)
    r = client.post("/api/v1/returns", json=_qa_payload(store_id=OWN_STORE))
    assert r.status_code == 201
    body = r.json()
    assert body["gross_refund"] == 1404.20
    assert body["refund_amount"] == 1404.20
    assert len(returns_coll.docs) == 1
    assert returns_coll.docs[0]["store_id"] == OWN_STORE


def test_return_create_admin_cross_store_ok(monkeypatch):
    app, returns_coll, _ = _returns_ctx(monkeypatch, order_store=OTHER_STORE)
    client = _returns_client(app, ["ADMIN"], "BV-HQ")
    r = client.post("/api/v1/returns", json=_qa_payload(store_id=OTHER_STORE))
    assert r.status_code == 201
    assert len(returns_coll.docs) == 1


_SEEDED_RETURN = {
    "return_id": "RET-260601-AB12CD",
    "return_type": "RETURN",
    "store_id": OTHER_STORE,
    "customer_id": "CUST-1",
    "refund_amount": 1404.20,
    "created_at": "2026-06-01T10:00:00",
}


def test_get_return_cross_store_403(monkeypatch):
    app, _, _ = _returns_ctx(monkeypatch, seed_return=_SEEDED_RETURN)
    client = _returns_client(app, ["STORE_MANAGER"], OWN_STORE)
    r = client.get(f"/api/v1/returns/{_SEEDED_RETURN['return_id']}")
    assert r.status_code == 403


def test_get_return_same_store_ok(monkeypatch):
    app, _, _ = _returns_ctx(monkeypatch, seed_return=_SEEDED_RETURN)
    client = _returns_client(app, ["STORE_MANAGER"], OTHER_STORE)
    r = client.get(f"/api/v1/returns/{_SEEDED_RETURN['return_id']}")
    assert r.status_code == 200
    assert r.json()["return_id"] == _SEEDED_RETURN["return_id"]


def test_get_return_admin_ok(monkeypatch):
    app, _, _ = _returns_ctx(monkeypatch, seed_return=_SEEDED_RETURN)
    client = _returns_client(app, ["ADMIN"], "BV-HQ")
    r = client.get(f"/api/v1/returns/{_SEEDED_RETURN['return_id']}")
    assert r.status_code == 200


# ===========================================================================
# 4. TASKS -- object endpoints store-guarded; list endpoints uncapped
# ===========================================================================


class _TaskRepo:
    def __init__(self, task=None):
        self.task = task
        self.updates = []
        self.find_many_calls = []

    def find_by_id(self, tid):
        if self.task and self.task.get("task_id") == tid:
            return dict(self.task)
        return None

    def update(self, tid, data):
        self.updates.append((tid, data))
        if self.task and self.task.get("task_id") == tid:
            self.task.update(data)
            return True
        return False

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        self.find_many_calls.append({"filter": filter, "limit": limit})
        return []

    def create(self, data):
        return data


def _seed_task(**over):
    base = {
        "task_id": "T1",
        "title": "Count stock",
        "status": "OPEN",
        "priority": "P3",
        "assigned_to": "u2",
        "store_id": OTHER_STORE,
        "history": [],
    }
    base.update(over)
    return base


def _tasks_client(monkeypatch, task, roles, store):
    app = FastAPI()
    app.include_router(tasks_mod.router, prefix="/api/v1/tasks")
    repo = _TaskRepo(task)
    monkeypatch.setattr(tasks_mod, "get_task_repository", lambda: repo)
    monkeypatch.setattr(tasks_mod, "get_user_repository", lambda: None)
    app.dependency_overrides[get_current_user] = _user_dep(roles, store)
    return TestClient(app), repo


def test_task_endpoints_cross_store_403(monkeypatch):
    client, repo = _tasks_client(
        monkeypatch, _seed_task(), ["STORE_MANAGER"], OWN_STORE
    )
    calls = [
        ("get", "/api/v1/tasks/T1", {}),
        ("patch", "/api/v1/tasks/T1", {"json": {"notes": "x"}}),
        ("put", "/api/v1/tasks/T1", {"json": {"notes": "x"}}),
        ("patch", "/api/v1/tasks/T1/complete", {"json": {"completion_notes": "done"}}),
        ("post", "/api/v1/tasks/T1/acknowledge", {}),
        ("post", "/api/v1/tasks/T1/escalate", {}),
        ("post", "/api/v1/tasks/T1/start", {}),
        ("post", "/api/v1/tasks/T1/reassign", {"json": {"assigned_to": "u9"}}),
    ]
    for method, url, kw in calls:
        r = getattr(client, method)(url, **kw)
        assert r.status_code == 403, f"{method.upper()} {url} -> {r.status_code}"
        assert "store" in r.json()["detail"].lower()
    assert repo.updates == []
    assert repo.task["status"] == "OPEN"


def test_task_complete_same_store_still_succeeds(monkeypatch):
    client, repo = _tasks_client(
        monkeypatch, _seed_task(), ["STORE_MANAGER"], OTHER_STORE
    )
    r = client.patch(
        "/api/v1/tasks/T1/complete", json={"completion_notes": "all counted"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"
    assert repo.task["status"] == "COMPLETED"
    assert repo.task["completed_by"] == "u-test"


def test_task_reassign_same_store_still_succeeds(monkeypatch):
    client, repo = _tasks_client(
        monkeypatch, _seed_task(), ["STORE_MANAGER"], OTHER_STORE
    )
    r = client.post("/api/v1/tasks/T1/reassign", json={"assigned_to": "u9"})
    assert r.status_code == 200
    assert r.json()["assigned_to"] == "u9"
    assert repo.task["assigned_to"] == "u9"


def test_task_admin_cross_store_bypass(monkeypatch):
    client, repo = _tasks_client(monkeypatch, _seed_task(), ["ADMIN"], "BV-HQ")
    r = client.patch("/api/v1/tasks/T1/complete", json={"completion_notes": "HQ done"})
    assert r.status_code == 200
    assert repo.task["status"] == "COMPLETED"


def test_task_global_no_store_allowed_for_any_caller(monkeypatch):
    """A task with NO store_id is global (system/HQ) -> store staff may act."""
    client, repo = _tasks_client(
        monkeypatch, _seed_task(store_id=None), ["SALES_STAFF"], OWN_STORE
    )
    assert client.get("/api/v1/tasks/T1").status_code == 200
    r = client.patch("/api/v1/tasks/T1/complete", json={"completion_notes": "done it"})
    assert r.status_code == 200
    assert repo.task["status"] == "COMPLETED"


def test_my_tasks_and_overdue_pass_unbounded_limit(monkeypatch):
    """PR-#522 idiom: limit=0 so the lists are not silently capped at 100."""
    client, repo = _tasks_client(
        monkeypatch, _seed_task(), ["STORE_MANAGER"], OWN_STORE
    )
    assert client.get("/api/v1/tasks/my-tasks").status_code == 200
    assert client.get("/api/v1/tasks/overdue").status_code == 200
    assert len(repo.find_many_calls) == 2
    for call in repo.find_many_calls:
        assert call["limit"] == 0


# ===========================================================================
# 5. TASKS -- SOP checklist day key is the IST business day
# ===========================================================================


class _SopColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.update_calls = []

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                return dict(d)
        return None

    def update_one(self, flt, update, upsert=False):
        self.update_calls.append({"filter": flt, "update": update, "upsert": upsert})
        return None


def test_sop_toggle_uses_ist_day_key(monkeypatch):
    """A tick at 23:30 IST (18:00 UTC) must land on TODAY's (IST) completion
    doc -- the UTC strftime day key put it on yesterday's."""
    tpl = {
        "template_id": "TPL1",
        "title": "Opening",
        "steps": [{"step_number": 1, "label": "Unlock", "title": "Unlock"}],
    }
    tcol = _SopColl([tpl])
    ccol = _SopColl()
    monkeypatch.setattr(tasks_mod, "_sop_collection", lambda: tcol)
    monkeypatch.setattr(tasks_mod, "_sop_completions_collection", lambda: ccol)
    monkeypatch.setattr(tasks_mod, "ist_today", lambda: _IST_SENTINEL)

    app = FastAPI()
    app.include_router(tasks_mod.router, prefix="/api/v1/tasks")
    app.dependency_overrides[get_current_user] = _user_dep(
        ["STORE_MANAGER"], OWN_STORE
    )
    client = TestClient(app)

    r = client.post(
        "/api/v1/tasks/sop-checklist/item",
        json={"template_id": "TPL1", "step_number": 1, "completed": True},
    )
    assert r.status_code == 200
    assert r.json()["date"] == _IST_SENTINEL.isoformat()
    assert ccol.update_calls[0]["filter"]["date"] == _IST_SENTINEL.isoformat()

    g = client.get(
        "/api/v1/tasks/sop-checklist", params={"template_id": "TPL1"}
    )
    assert g.status_code == 200
    assert g.json()["date"] == _IST_SENTINEL.isoformat()
