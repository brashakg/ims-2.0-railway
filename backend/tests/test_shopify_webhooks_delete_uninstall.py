"""
IMS 2.0 - Shopify orders/delete + customers/delete + app/uninstalled handlers
==============================================================================
Pins the three lifecycle-cleanup webhook handlers IMS added so nothing is lost
once BVI is retired:

  orders/delete    (api.services.shopify_order_delete.handle_shopify_order_delete)
    - VOIDs the matching IMS online order (soft status change + shopify_deleted_at;
      NEVER hard-deletes), keeps the prior status for audit.
    - unknown order -> fails soft (order_not_found, no crash).
    - re-delivery is idempotent (duplicate; no re-void).

  customers/delete (api.services.shopify_customer_delete.handle_shopify_customer_delete)
    - flags the matching IMS customer shopify_erasure_requested (+timestamp) and
      RETAINS the record + PII-linked history (never hard-deletes).
    - unknown customer -> fails soft. Re-delivery is idempotent.

  app/uninstalled  (api.services.shopify_app_uninstall.handle_shopify_app_uninstalled)
    - LOUD log + integration_alerts health row + integrations.shopify stamp +
      a HIGH audit alert (emit_audit_alert). The alert fires ONCE; a redelivery
      is a duplicate with NO second alert.

  NEXUS dispatch table (agents.implementations.nexus)
    - routes each of the three new topics to the right handler.

Pure: no network, no real Mongo. A tiny in-memory FakeDB exercises the handlers.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ.setdefault("ENVIRONMENT", "test")


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (only the ops these handlers touch).
# ---------------------------------------------------------------------------


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$ne":
                    if actual == op_val:
                        return False
                elif op == "$exists":
                    if (actual is not None) != bool(op_val):
                        return False
                else:
                    return False
        else:
            if actual != expected:
                return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs: list = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _match(d, filter_):
                return dict(d)
        return None

    def count_documents(self, filter_=None):
        return len([d for d in self.docs if _match(d, filter_)])

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _match(d, filter_):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            doc = dict(filter_)
            for k, v in (update.get("$set") or {}).items():
                doc[k] = v
            self.docs.append(doc)
            return type("R", (), {"modified_count": 0, "upserted_id": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, name):
        return self.get_collection(name)

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db():
    return FakeDB()


def _seed_order(db, *, shopify_order_id="5001", status="CONFIRMED", extra=None):
    order = {
        "order_id": "ord-abc",
        "shopify_order_id": shopify_order_id,
        "channel": "ONLINE",
        "source": "shopify",
        "status": status,
    }
    if extra:
        order.update(extra)
    db.get_collection("orders").insert_one(order)
    return order


def _seed_customer(db, *, shopify_customer_id="555", extra=None):
    cust = {
        "customer_id": "CUST-1",
        "shopify_customer_id": shopify_customer_id,
        "name": "Ravi Kumar",
        "email": "ravi@example.com",
        "mobile": "9876543210",
    }
    if extra:
        cust.update(extra)
    db.get_collection("customers").insert_one(cust)
    return cust


# ===========================================================================
# orders/delete
# ===========================================================================


def test_order_delete_voids_found_order(db):
    _seed_order(db, shopify_order_id="5001", status="CONFIRMED")
    from api.services.shopify_order_delete import handle_shopify_order_delete

    res = handle_shopify_order_delete(db, {"id": 5001}, topic="orders/delete")

    assert res["status"] == "voided"
    assert res["status_before_void"] == "CONFIRMED"
    order = db.get_collection("orders").find_one({"shopify_order_id": "5001"})
    assert order["status"] == "VOID"                       # soft void, not deleted
    assert order.get("shopify_deleted_at")                 # marker stamped
    assert order["status_before_void"] == "CONFIRMED"      # prior status preserved
    # NEVER hard-deleted: the doc is still present.
    assert db.get_collection("orders").count_documents({}) == 1


def test_order_delete_unknown_order_is_noop(db):
    from api.services.shopify_order_delete import handle_shopify_order_delete

    res = handle_shopify_order_delete(db, {"id": 404040}, topic="orders/delete")
    assert res["status"] == "order_not_found"              # no crash
    assert db.get_collection("orders").count_documents({}) == 0


def test_order_delete_idempotent_on_redelivery(db):
    _seed_order(db, shopify_order_id="5001", status="CONFIRMED")
    from api.services.shopify_order_delete import handle_shopify_order_delete

    first = handle_shopify_order_delete(db, {"id": 5001}, topic="orders/delete")
    deleted_at = db.get_collection("orders").find_one(
        {"shopify_order_id": "5001"}
    )["shopify_deleted_at"]
    second = handle_shopify_order_delete(db, {"id": 5001}, topic="orders/delete")

    assert first["status"] == "voided"
    assert second["status"] == "duplicate"                 # no re-void
    # The delete marker is unchanged by the redelivery.
    order = db.get_collection("orders").find_one({"shopify_order_id": "5001"})
    assert order["shopify_deleted_at"] == deleted_at


def test_order_delete_skips_historical_import(db):
    _seed_order(db, shopify_order_id="5001", status="HISTORICAL",
                extra={"source": "bvi_import"})
    from api.services.shopify_order_delete import handle_shopify_order_delete

    res = handle_shopify_order_delete(db, {"id": 5001}, topic="orders/delete")
    assert res["status"] == "skipped"
    order = db.get_collection("orders").find_one({"shopify_order_id": "5001"})
    assert order["status"] == "HISTORICAL"                 # untouched
    assert not order.get("shopify_deleted_at")


def test_order_delete_no_db_simulates():
    from api.services.shopify_order_delete import handle_shopify_order_delete

    res = handle_shopify_order_delete(None, {"id": 5001}, topic="orders/delete")
    assert res["status"] == "simulated"


# ===========================================================================
# customers/delete
# ===========================================================================


def test_customer_delete_flags_erasure_keeps_history(db):
    _seed_customer(db, shopify_customer_id="555")
    from api.services.shopify_customer_delete import handle_shopify_customer_delete

    res = handle_shopify_customer_delete(db, {"id": 555}, topic="customers/delete")

    assert res["status"] == "erasure_flagged"
    cust = db.get_collection("customers").find_one({"shopify_customer_id": "555"})
    assert cust["shopify_erasure_requested"] is True
    assert cust.get("shopify_erasure_requested_at")
    # PII-linked history RETAINED: the record still exists, name/email intact.
    assert db.get_collection("customers").count_documents({}) == 1
    assert cust["name"] == "Ravi Kumar"
    assert cust["email"] == "ravi@example.com"


def test_customer_delete_unknown_is_noop(db):
    from api.services.shopify_customer_delete import handle_shopify_customer_delete

    res = handle_shopify_customer_delete(db, {"id": 999}, topic="customers/delete")
    assert res["status"] == "customer_not_found"           # no crash


def test_customer_delete_idempotent_on_redelivery(db):
    _seed_customer(db, shopify_customer_id="555")
    from api.services.shopify_customer_delete import handle_shopify_customer_delete

    first = handle_shopify_customer_delete(db, {"id": 555}, topic="customers/delete")
    flagged_at = db.get_collection("customers").find_one(
        {"shopify_customer_id": "555"}
    )["shopify_erasure_requested_at"]
    second = handle_shopify_customer_delete(db, {"id": 555}, topic="customers/delete")

    assert first["status"] == "erasure_flagged"
    assert second["status"] == "duplicate"
    cust = db.get_collection("customers").find_one({"shopify_customer_id": "555"})
    assert cust["shopify_erasure_requested_at"] == flagged_at


def test_customer_delete_no_db_simulates():
    from api.services.shopify_customer_delete import handle_shopify_customer_delete

    res = handle_shopify_customer_delete(None, {"id": 555}, topic="customers/delete")
    assert res["status"] == "simulated"


# ===========================================================================
# app/uninstalled
# ===========================================================================


def _patch_emit(monkeypatch):
    """Capture emit_audit_alert calls; return the recording list."""
    calls = []

    async def fake_emit(**kwargs):
        calls.append(kwargs)
        return "audit-xyz"

    monkeypatch.setattr("api.services.audit_alerts.emit_audit_alert", fake_emit)
    return calls


def test_app_uninstalled_raises_high_alert(db, monkeypatch):
    calls = _patch_emit(monkeypatch)
    # Seed a shopify integration doc so the health stamp lands.
    db.get_collection("integrations").insert_one(
        {"type": "shopify", "enabled": True, "connected": True}
    )
    from api.services.shopify_app_uninstall import handle_shopify_app_uninstalled

    payload = {"id": 42, "myshopify_domain": "bettervision.myshopify.com"}
    res = _run(handle_shopify_app_uninstalled(db, payload, topic="app/uninstalled"))

    assert res["status"] == "recorded"
    assert res["alerted"] is True
    assert res["shop"] == "bettervision.myshopify.com"
    # A HIGH audit alert was raised exactly once.
    assert len(calls) == 1
    assert calls[0]["severity"] == "HIGH"
    assert calls[0]["action"] == "integration.shopify.uninstalled"
    # The integration-health row was written...
    alerts = db.get_collection("integration_alerts")
    assert alerts.count_documents(
        {"type": "shopify", "kind": "app_uninstalled"}
    ) == 1
    # ...and the integrations doc was stamped connection-gone.
    integ = db.get_collection("integrations").find_one({"type": "shopify"})
    assert integ["connected"] is False
    assert integ.get("app_uninstalled_at")


def test_app_uninstalled_idempotent_no_double_alert(db, monkeypatch):
    calls = _patch_emit(monkeypatch)
    from api.services.shopify_app_uninstall import handle_shopify_app_uninstalled

    payload = {"id": 42, "myshopify_domain": "bettervision.myshopify.com"}
    first = _run(handle_shopify_app_uninstalled(db, payload, topic="app/uninstalled"))
    second = _run(handle_shopify_app_uninstalled(db, payload, topic="app/uninstalled"))

    assert first["status"] == "recorded"
    assert first["alerted"] is True
    assert second["status"] == "duplicate"
    assert second["alerted"] is False
    # The HIGH alert fired ONCE and only one health row exists.
    assert len(calls) == 1
    assert db.get_collection("integration_alerts").count_documents({}) == 1


def test_app_uninstalled_no_db_simulates(monkeypatch):
    _patch_emit(monkeypatch)
    from api.services.shopify_app_uninstall import handle_shopify_app_uninstalled

    res = _run(handle_shopify_app_uninstalled(None, {"id": 1}, topic="app/uninstalled"))
    assert res["status"] == "simulated"
    assert res["alerted"] is False


# ===========================================================================
# NEXUS dispatch table routes the 3 new topics
# ===========================================================================


def test_dispatch_routes_new_topics(monkeypatch):
    from agents.implementations.nexus import NexusAgent

    calls = []

    monkeypatch.setattr(
        "api.services.shopify_order_delete.handle_shopify_order_delete",
        lambda db, payload, **kw: calls.append("order_delete") or {"status": "voided"},
    )
    monkeypatch.setattr(
        "api.services.shopify_customer_delete.handle_shopify_customer_delete",
        lambda db, payload, **kw: calls.append("customer_delete")
        or {"status": "erasure_flagged"},
    )

    async def fake_app_uninstall(db, payload, **kw):
        calls.append("app_uninstalled")
        return {"status": "recorded", "shop": "x", "alerted": True}

    monkeypatch.setattr(
        "api.services.shopify_app_uninstall.handle_shopify_app_uninstalled",
        fake_app_uninstall,
    )

    agent = NexusAgent(db=FakeDB())

    for topic, expected in [
        ("orders/delete", "order_delete"),
        ("customers/delete", "customer_delete"),
        ("app/uninstalled", "app_uninstalled"),
    ]:
        calls.clear()
        agent._current_webhook_headers = {"x-shopify-topic": topic}
        _run(agent._handle_shopify_webhook({"id": 1, "topic": topic}))
        assert calls == [expected], f"topic {topic} routed to {calls}"
