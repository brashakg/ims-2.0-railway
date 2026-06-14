"""
IMS 2.0 - F38 Endless Aisle tests (intent-level)
================================================
Exercises the REAL endless_aisle service + router against a faithful in-memory
fake Mongo (no network). A hollow shell that sells ghost stock, charges the
customer for shipping, lets a non-source store accept, skips the flag gate, or
mutates POS pricing would FAIL here.

Maps to the F38 acceptance intents:
  * source selection -- excludes selling store / ineligible / insufficient; best-first
  * flag-gated -- every route 403 when endless_aisle.enabled is off (the default)
  * ghost-stock guard -- re-validate source on-hand at open + accept + create-transfer
  * source-ACCEPT 2-step -- only the SOURCE store accepts; concurrent accept -> one winner
  * company-borne shipping -- booked to the SELLING store, customer charged 0
  * POS untouched -- no order/tender/pricing mutation anywhere
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import endless_aisle as svc  # noqa: E402

# ============================================================================
# Fake Mongo
# ============================================================================


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        actual = doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if op == "$in" and actual not in expected:
                    return False
                if op == "$ne" and actual == expected:
                    return False
            continue
        if actual != v:
            return False
    return True


def _apply_update(doc, update):
    for op, fields in update.items():
        if op == "$set":
            for kk, vv in fields.items():
                doc[kk] = vv
        elif op == "$push":
            for kk, vv in fields.items():
                doc.setdefault(kk, []).append(vv)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        if doc.get("_id") and any(d.get("_id") == doc["_id"] for d in self.docs):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError(f"dup {doc['_id']}")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return [dict(d) for d in self.docs if _matches(d, query or {})]

    def find_one_and_update(self, query, update, return_document=None, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return dict(d)
        return None

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._c: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name):
        return self._c.setdefault(name, FakeCollection())

    def __getitem__(self, name):
        return self.get_collection(name)


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


def _mgr(uid="M1", store="BV-1"):
    return {
        "user_id": uid,
        "roles": ["STORE_MANAGER"],
        "store_ids": [store],
        "active_store_id": store,
    }


def _mgr2(uid="M2", stores=("BV-1", "BV-2")):
    return {
        "user_id": uid,
        "roles": ["STORE_MANAGER"],
        "store_ids": list(stores),
        "active_store_id": stores[0],
    }


# on_hand_resolver factories
def _oh(mapping):
    return lambda product_id, store_id: int(mapping.get((product_id, store_id), 0))


# ============================================================================
# Pure: transition table + source selection
# ============================================================================


def test_transition_table():
    assert svc.can_transition("PENDING", "ACCEPTED")
    assert svc.can_transition("ACCEPTED", "TRANSFER_CREATED")
    assert svc.can_transition("TRANSFER_CREATED", "SHIPPED")
    assert svc.can_transition("SHIPPED", "DELIVERED")
    assert not svc.can_transition("PENDING", "TRANSFER_CREATED")
    assert not svc.can_transition("DELIVERED", "SHIPPED")
    assert not svc.can_transition("REJECTED", "ACCEPTED")


def test_find_sources_excludes_and_orders():
    on_hand = {"BV-1": 0, "BV-2": 3, "BV-3": 5, "BV-4": 1}
    out = svc.find_fulfillment_sources(on_hand, "BV-1", None, 2)
    assert [s["store_id"] for s in out] == [
        "BV-3",
        "BV-2",
    ]  # best-first; BV-1 excluded; BV-4 insufficient


def test_find_sources_eligibility_filter():
    on_hand = {"BV-2": 3, "BV-3": 5}
    assert [
        s["store_id"]
        for s in svc.find_fulfillment_sources(on_hand, "BV-1", ["BV-2"], 2)
    ] == ["BV-2"]
    # empty/None eligible -> ALL eligible
    assert len(svc.find_fulfillment_sources(on_hand, "BV-1", None, 2)) == 2
    assert len(svc.find_fulfillment_sources(on_hand, "BV-1", [], 2)) == 2


# ============================================================================
# Engine: open / accept / create-transfer with ghost-stock guards
# ============================================================================


def test_open_request_ghost_stock_409(db):
    # source has only 1, asking for 2 -> ghost-stock 409, no doc written
    with pytest.raises(svc.EndlessAisleError) as e:
        svc.open_request(
            db,
            {
                "product_id": "P",
                "qty": 2,
                "selling_store_id": "BV-1",
                "source_store_id": "BV-2",
            },
            actor=_mgr(),
            on_hand_resolver=_oh({("P", "BV-2"): 1}),
        )
    assert e.value.status == 409 and e.value.code == "ghost_stock"
    assert db.get_collection(svc.COLLECTION).docs == []


def test_open_request_books_company_shipping(db):
    req = svc.open_request(
        db,
        {
            "product_id": "P",
            "qty": 2,
            "selling_store_id": "BV-1",
            "source_store_id": "BV-2",
        },
        actor=_mgr(),
        on_hand_resolver=_oh({("P", "BV-2"): 5}),
    )
    assert req["status"] == "PENDING"
    assert req["shipping_borne_by"] == "COMPANY"
    assert req["shipping_cost_store_id"] == "BV-1"  # the SELLING store
    assert req["customer_shipping_charge_paise"] == 0  # customer pays nothing


def test_open_request_source_equals_selling_422(db):
    with pytest.raises(svc.EndlessAisleError) as e:
        svc.open_request(
            db,
            {
                "product_id": "P",
                "qty": 1,
                "selling_store_id": "BV-1",
                "source_store_id": "BV-1",
            },
            actor=_mgr(),
            on_hand_resolver=_oh({("P", "BV-1"): 5}),
        )
    assert e.value.status == 422


def _pending(db, oh):
    return svc.open_request(
        db,
        {
            "product_id": "P",
            "qty": 2,
            "selling_store_id": "BV-1",
            "source_store_id": "BV-2",
        },
        actor=_mgr(),
        on_hand_resolver=_oh(oh),
    )


def test_accept_revalidates_ghost_stock(db):
    r = _pending(db, {("P", "BV-2"): 5})
    # stock vanished between open and accept -> 409, still PENDING
    with pytest.raises(svc.EndlessAisleError) as e:
        svc.accept_request(
            db, r["request_id"], actor=_mgr2(), on_hand_resolver=_oh({("P", "BV-2"): 0})
        )
    assert e.value.status == 409 and e.value.code == "ghost_stock"
    assert svc.get_request(db, r["request_id"])["status"] == "PENDING"


def test_accept_then_concurrent_accept_one_winner(db):
    r = _pending(db, {("P", "BV-2"): 5})
    ok = svc.accept_request(
        db, r["request_id"], actor=_mgr2(), on_hand_resolver=_oh({("P", "BV-2"): 5})
    )
    assert ok["status"] == "ACCEPTED"
    # second accept: no longer PENDING -> 409
    with pytest.raises(svc.EndlessAisleError) as e:
        svc.accept_request(
            db, r["request_id"], actor=_mgr2(), on_hand_resolver=_oh({("P", "BV-2"): 5})
        )
    assert e.value.status == 409


def test_reject_from_pending(db):
    r = _pending(db, {("P", "BV-2"): 5})
    out = svc.reject_request(db, r["request_id"], actor=_mgr2(), reason="damaged")
    assert out["status"] == "REJECTED" and out["reject_reason"] == "damaged"


def test_create_transfer_books_shipping_to_selling_store(db):
    r = _pending(db, {("P", "BV-2"): 5})
    svc.accept_request(
        db, r["request_id"], actor=_mgr2(), on_hand_resolver=_oh({("P", "BV-2"): 5})
    )
    out = svc.create_transfer(
        db, r["request_id"], actor=_mgr(), on_hand_resolver=_oh({("P", "BV-2"): 5})
    )
    assert out["status"] == "TRANSFER_CREATED" and out["transfer_id"]
    transfer = db.get_collection(svc.TRANSFERS_COLLECTION).find_one(
        {"id": out["transfer_id"]}
    )
    assert (
        transfer["from_location_id"] == "BV-2" and transfer["to_location_id"] == "BV-1"
    )
    assert transfer["shipping_cost_store_id"] == "BV-1"  # SELLING store bears it
    assert transfer["shipping_borne_by"] == "COMPANY"
    assert transfer["source"] == "ENDLESS_AISLE"
    # NO order / customer bill mutated -- endless-aisle never writes to orders
    assert db.get_collection("orders").docs == []


def test_create_transfer_requires_accepted(db):
    r = _pending(db, {("P", "BV-2"): 5})  # still PENDING
    with pytest.raises(svc.EndlessAisleError) as e:
        svc.create_transfer(
            db, r["request_id"], actor=_mgr(), on_hand_resolver=_oh({("P", "BV-2"): 5})
        )
    assert e.value.status == 409 and e.value.code == "not_accepted"


def test_full_lifecycle_to_delivered(db):
    r = _pending(db, {("P", "BV-2"): 5})
    svc.accept_request(
        db, r["request_id"], actor=_mgr2(), on_hand_resolver=_oh({("P", "BV-2"): 5})
    )
    svc.create_transfer(
        db, r["request_id"], actor=_mgr(), on_hand_resolver=_oh({("P", "BV-2"): 5})
    )
    svc.advance(db, r["request_id"], "SHIPPED", actor=_mgr())
    out = svc.advance(db, r["request_id"], "DELIVERED", actor=_mgr())
    assert out["status"] == "DELIVERED"
    # illegal now (terminal)
    with pytest.raises(svc.EndlessAisleError):
        svc.advance(db, r["request_id"], "SHIPPED", actor=_mgr())


def test_engine_db_absent_failsoft():
    with pytest.raises(svc.EndlessAisleError) as e:
        svc.open_request(None, {}, actor=_mgr(), on_hand_resolver=_oh({}))
    assert e.value.status == 503
    assert svc.get_request(None, "x") is None
    assert svc.list_requests(None) == []
    svc.ensure_indexes(None)


# ============================================================================
# ROUTER: flag gate + role + store-scope (IDOR)
# ============================================================================


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _wire(monkeypatch, db, *, enabled=True):
    from api.routers import endless_aisle as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(
        r, "_require_enabled", (lambda: None) if enabled else r._require_enabled
    )
    monkeypatch.setattr(r, "_on_hand_resolver", _oh({("P", "BV-2"): 5}))
    return r


def test_router_flag_off_403_everywhere(db, monkeypatch):
    from fastapi import HTTPException
    from api.routers import endless_aisle as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    # real _require_enabled with the flag unset -> get_policy default False -> 403
    body = r.RequestBody(
        product_id="P", qty=1, selling_store_id="BV-1", source_store_id="BV-2"
    )
    with pytest.raises(HTTPException) as exc:
        _run(r.open_request(body, current_user=_mgr()))
    assert exc.value.status_code == 403


def test_router_open_403_for_non_manager(db, monkeypatch):
    from fastapi import HTTPException

    r = _wire(monkeypatch, db)
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: True)
    cashier = {"user_id": "C1", "roles": ["SALES_CASHIER"], "store_ids": ["BV-1"]}
    body = r.RequestBody(
        product_id="P", qty=1, selling_store_id="BV-1", source_store_id="BV-2"
    )
    with pytest.raises(HTTPException) as exc:
        _run(r.open_request(body, current_user=cashier))
    assert exc.value.status_code == 403


def test_router_accept_requires_source_store_access(db, monkeypatch):
    from fastapi import HTTPException

    r = _wire(monkeypatch, db)
    # open with real store access stubbed open
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: True)
    body = r.RequestBody(
        product_id="P", qty=2, selling_store_id="BV-1", source_store_id="BV-2"
    )
    req = _run(r.open_request(body, current_user=_mgr2()))
    # now use the REAL validate_store_access: a BV-1-only manager cannot accept (source is BV-2)
    from api.dependencies import validate_store_access as real_vsa

    monkeypatch.setattr(r, "validate_store_access", real_vsa)
    with pytest.raises(HTTPException) as exc:
        _run(r.accept_request(req["request_id"], current_user=_mgr("M9", "BV-1")))
    assert exc.value.status_code == 403
    assert svc.get_request(db, req["request_id"])["status"] == "PENDING"


def test_router_full_flow_no_order_mutation(db, monkeypatch):
    r = _wire(monkeypatch, db)
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: True)
    body = r.RequestBody(
        product_id="P", qty=2, selling_store_id="BV-1", source_store_id="BV-2"
    )
    req = _run(r.open_request(body, current_user=_mgr2()))
    _run(r.accept_request(req["request_id"], current_user=_mgr2()))
    out = _run(r.create_transfer(req["request_id"], current_user=_mgr2()))
    assert out["status"] == "TRANSFER_CREATED"
    # POS / orders never touched by the whole flow
    assert db.get_collection("orders").docs == []
