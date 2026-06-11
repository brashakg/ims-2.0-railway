"""
IMS 2.0 - Feature #14 Non-adaptation / remake tracking -- intent tests.

Exercises the REAL non_adapt engine + the route role/store gates against a fake
Mongo (no network). Covers:
  - within-window remake -> FREE (policy), paise-exact; outside-window -> chargeable,
  - record persists the charge DECISION + links the original order line,
  - the quality report aggregates by reason / optometrist / lens brand,
  - a cashier cannot record a non-adapt or initiate a (waivable) remake (403),
  - store-scope: a cross-store actor is blocked (validate_store_access).

No whole-JSON substring asserts; every DB/policy touch is seeded/monkeypatched.
No emoji.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import non_adapt as svc  # noqa: E402


# --------------------------------------------------------------------------- #
# Fake Mongo
# --------------------------------------------------------------------------- #
class _Coll:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, q):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None

    def find(self, q=None):
        q = q or {}
        return [dict(d) for d in self.docs if all(d.get(k) == v for k, v in q.items())]

    def find_one_and_update(self, q, u, return_document=None, **k):
        for d in self.docs:
            if all(d.get(k2) == v for k2, v in q.items()):
                d.update(u.get("$set") or {})
                return dict(d)
        return None


class _DB:
    def __init__(self):
        self._c: Dict[str, _Coll] = {}

    def get_collection(self, name):
        return self._c.setdefault(name, _Coll())


def _order(store="S1", sale_date="2026-06-01"):
    return {
        "order_id": "ORD-1", "store_id": store, "created_at": sale_date,
        "items": [{"item_id": "L1", "product_id": "P1", "brand": "Zeiss",
                   "category": "OPTICAL_LENS", "item_type": "LENS",
                   "item_total": 5000.0, "prescription_id": "RX-1"}],
    }


_OPTO = {"user_id": "o1", "roles": ["OPTOMETRIST"], "active_store_id": "S1"}
_CASHIER = {"user_id": "c1", "roles": ["SALES_CASHIER"], "active_store_id": "S1"}


@pytest.fixture
def db():
    return _DB()


# --------------------------------------------------------------------------- #
# Pure window + charge math
# --------------------------------------------------------------------------- #
def test_is_within_window():
    assert svc.is_within_window("2026-06-01", "2026-06-20", 45) is True   # 19 days
    assert svc.is_within_window("2026-06-01", "2026-08-01", 45) is False  # 61 days


def test_record_within_window_is_free(db, monkeypatch):
    from api.services import policy_engine as pe
    monkeypatch.setattr(pe, "get_policy", lambda key, scope=None, *, default=None:
                        45 if key.endswith("window_days") else (svc.CHARGE_FREE if key.endswith("charge_policy") else default))
    eng = svc.NonAdaptEngine(db)
    order = _order(sale_date="2026-06-01")
    rec = eng.record(order=order, line=order["items"][0], reason="PROGRESSIVE_INTOLERANCE",
                     store_id="S1", actor=_OPTO, optometrist_id="o9", today="2026-06-20")
    assert rec["within_window"] is True
    assert rec["remake_charge_paise"] == 0      # FREE within window
    assert rec["charge_waived"] is True
    assert rec["original_order_id"] == "ORD-1"
    assert rec["lens_brand"] == "Zeiss"


def test_record_outside_window_is_chargeable(db, monkeypatch):
    from api.services import policy_engine as pe
    monkeypatch.setattr(pe, "get_policy", lambda key, scope=None, *, default=None:
                        45 if key.endswith("window_days") else (svc.CHARGE_FULL if key.endswith("charge_policy") else default))
    eng = svc.NonAdaptEngine(db)
    order = _order(sale_date="2026-06-01")
    rec = eng.record(order=order, line=order["items"][0], reason="WRONG_POWER_FELT",
                     store_id="S1", actor=_OPTO, today="2026-08-01")
    assert rec["within_window"] is False
    assert rec["remake_charge_paise"] > 0       # outside window -> chargeable
    assert rec["charge_waived"] is False


def test_bad_reason_rejected(db):
    eng = svc.NonAdaptEngine(db)
    with pytest.raises(svc.NonAdaptError):
        eng.record(order=_order(), line=None, reason="NOT_A_REASON", store_id="S1", actor=_OPTO)


# --------------------------------------------------------------------------- #
# Report aggregation
# --------------------------------------------------------------------------- #
def test_report_aggregates_by_reason_brand(db, monkeypatch):
    from api.services import policy_engine as pe
    monkeypatch.setattr(pe, "get_policy", lambda key, scope=None, *, default=None: default)
    eng = svc.NonAdaptEngine(db)
    o = _order()
    eng.record(order=o, line=o["items"][0], reason="PROGRESSIVE_INTOLERANCE",
               store_id="S1", actor=_OPTO, optometrist_id="o9", today="2026-06-10")
    eng.record(order=o, line=o["items"][0], reason="PROGRESSIVE_INTOLERANCE",
               store_id="S1", actor=_OPTO, optometrist_id="o9", today="2026-06-11")
    rep = eng.report(store_id="S1")
    assert rep["total"] == 2
    assert rep["by_reason"].get("PROGRESSIVE_INTOLERANCE") == 2
    assert rep["by_lens_brand"].get("Zeiss") == 2


# --------------------------------------------------------------------------- #
# Route RBAC + store-scope
# --------------------------------------------------------------------------- #
def test_route_cashier_cannot_record(db, monkeypatch):
    from api.routers import non_adapt as r
    from fastapi import HTTPException
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_load_order", lambda oid: _order())
    body = r.RecordBody(order_id="ORD-1", reason="PROGRESSIVE_INTOLERANCE")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.record_non_adapt(body, _CASHIER))
    assert ei.value.status_code == 403


def test_route_cross_store_blocked(db, monkeypatch):
    from api.routers import non_adapt as r
    from fastapi import HTTPException
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_load_order", lambda oid: _order(store="S2"))

    def _deny(store_id, user):
        raise HTTPException(status_code=403, detail="cross-store")

    monkeypatch.setattr(r, "validate_store_access", _deny)
    body = r.RecordBody(order_id="ORD-1", reason="PROGRESSIVE_INTOLERANCE")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.record_non_adapt(body, _OPTO))
    assert ei.value.status_code == 403


def test_route_record_happy_path(db, monkeypatch):
    from api.routers import non_adapt as r
    from api.services import policy_engine as pe
    monkeypatch.setattr(pe, "get_policy", lambda key, scope=None, *, default=None: default)
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_load_order", lambda oid: _order())
    monkeypatch.setattr(r, "validate_store_access", lambda s, u: s)
    monkeypatch.setattr(r, "_audit", lambda *a, **k: None)
    body = r.RecordBody(order_id="ORD-1", item_id="L1", reason="PROGRESSIVE_INTOLERANCE")
    out = asyncio.run(r.record_non_adapt(body, _OPTO))
    assert out["record_id"].startswith("NA-")
    assert out["reason"] == "PROGRESSIVE_INTOLERANCE"


# --------------------------------------------------------------------------- #
# Adversarial fixes: bad item_id fails loud, bad date fails soft, once-only remake
# --------------------------------------------------------------------------- #
def test_route_bad_item_id_fails_loud(db, monkeypatch):
    """P1: a supplied item_id matching no order line must 400 -- never silently
    zero the charge basis into a free remake."""
    from api.routers import non_adapt as r
    from fastapi import HTTPException
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_load_order", lambda oid: _order())
    monkeypatch.setattr(r, "validate_store_access", lambda s, u: s)
    body = r.RecordBody(order_id="ORD-1", item_id="GHOST", reason="PROGRESSIVE_INTOLERANCE")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.record_non_adapt(body, _OPTO))
    assert ei.value.status_code == 400


def test_bad_sale_date_fails_soft_within_window():
    """P2: an unparseable date must not crash -- treat as within-window (free)."""
    assert svc.is_within_window("not-a-date", "2026-06-20", 45) is True
    assert svc.is_within_window("2026-06-01", "garbage", 45) is True


def test_remake_is_once_only(db, monkeypatch):
    """Idempotency: a second /remake on the same record is rejected (409), so it
    can't clobber the prior link or re-stamp authorized_by."""
    from api.services import policy_engine as pe
    monkeypatch.setattr(pe, "get_policy", lambda key, scope=None, *, default=None: default)
    eng = svc.NonAdaptEngine(db)
    o = _order()
    rec = eng.record(order=o, line=o["items"][0], reason="PROGRESSIVE_INTOLERANCE",
                     store_id="S1", actor=_OPTO, today="2026-06-10")
    eng.link_remake(rec["record_id"], remake_order_id="RMK-1", actor=_OPTO)
    with pytest.raises(svc.NonAdaptError) as ei:
        eng.link_remake(rec["record_id"], remake_order_id="RMK-2", actor=_OPTO)
    assert ei.value.status == 409
