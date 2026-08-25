"""
IMS 2.0 - the EXCHANGE door is CLOSED (owner ruling 2026-08-25, finding S1)
===========================================================================
An exchange put the returned frame back on the shelf and collected the price
difference at the till, but NEVER took the replacement unit out of stock and
NEVER billed it. Result on EVERY exchange: one phantom frame the system still
believes it owns, and one sale that reaches no revenue figure, no GST return
and no Tally export. The drawer balanced perfectly, so nothing looked wrong.

Owner's ruling: switch the exchange off first. Staff do a return, then a fresh
sale - both of those paths already work correctly today. Exchange billing comes
later as its own change.

So this file pins FOUR things at once, because closing a door badly is how the
defect was born:

  1. the WRITE door (POST /returns) refuses an EXCHANGE and writes NOTHING;
  2. the QUOTE door refuses it too (an old tab quotes before it posts);
  3. the refusal is a plain-English instruction a cashier can act on - it names
     the return AND the fresh sale - not a code and not a bare "not allowed";
  4. RETURN and CREDIT_NOTE still complete, and an EXCHANGE recorded BEFORE the
     change still reads and still lists (read-only history survives).

A test that only asserts "400" would pass against a door slammed with no
instructions, which the owner explicitly ruled out.
"""
from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import returns as returns_router  # noqa: E402
from tests.test_returns_gst_refund import (  # noqa: E402
    _FakeOrderRepo,
    _FakeCustomerRepo,
    _FakeColl,
    _qa_order,
    _qa_payload,
    _staff_token,
)

_HDR = {"Authorization": f"Bearer {_staff_token(['ADMIN'])}"}


class _ListableColl(_FakeColl):
    """`returns` with a REAL cursor.

    list_returns swallows every exception and answers {"returns": [], "total": 0},
    so a fake whose find() has no .sort()/.skip()/.limit() would make the
    history assertions below pass while listing nothing at all.
    """

    class _Cursor:
        def __init__(self, docs):
            self._docs = list(docs)

        def sort(self, *_a, **_k):
            return self

        def skip(self, n):
            self._docs = self._docs[n:]
            return self

        def limit(self, n):
            self._docs = self._docs[:n]
            return self

        def __iter__(self):
            return iter(self._docs)

    def find(self, query=None, projection=None):
        q = query or {}
        return self._Cursor(
            {k: v for k, v in d.items() if k != "_id"}
            for d in self.docs
            if all(d.get(k) == v for k, v in q.items())
        )


class _FakeProductRepo:
    """A REAL catalog for the replacement line.

    Without it the exchange 503s on "product catalog unavailable" and every
    assertion below would pass against an open door - the refusal has to be
    the reason the exchange stops, not a missing fixture.
    """

    def find_by_id(self, pid):
        if pid == "PRD-2":
            return {
                "product_id": "PRD-2",
                "name": "Replacement Frame",
                "sku": "RB-2",
                "offer_price": 1500.0,
            }
        return None

    def find_by_sku(self, sku):
        return self.find_by_id("PRD-2") if sku == "RB-2" else None


def _ctx(monkeypatch):
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")
    order_repo = _FakeOrderRepo(_qa_order())
    returns_coll = _ListableColl()
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
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        returns_router, "get_customer_repository", lambda: _FakeCustomerRepo()
    )
    monkeypatch.setattr(
        returns_router, "get_product_repository", lambda: _FakeProductRepo()
    )
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )
    return TestClient(app), returns_coll


def _exchange_payload():
    return _qa_payload(
        return_type="EXCHANGE",
        replacement_items=[
            {
                "product_id": "PRD-2",
                "name": "Replacement Frame",
                "sku": "RB-2",
                "quantity": 1,
                "unit_price": 1500,
            }
        ],
        collect_method="CASH",
    )


def _instructs_staff(text: str) -> bool:
    """The refusal must tell a cashier the two steps to take instead."""
    low = text.lower()
    return "return" in low and "sale" in low


# ---------------------------------------------------------------------------
# 1 + 3. The WRITE door refuses, writes nothing, and says what to do instead.
# ---------------------------------------------------------------------------


def test_create_exchange_is_refused(monkeypatch):
    client, _ = _ctx(monkeypatch)
    r = client.post("/api/v1/returns", json=_exchange_payload(), headers=_HDR)
    assert r.status_code == 400, r.text
    assert _instructs_staff(r.text), (
        "the refusal must name the two steps (a return, then a normal sale); "
        f"got: {r.text}"
    )


def test_refused_exchange_writes_no_return_at_all(monkeypatch):
    """The leak is the HAND-OVER, so a half-written exchange is still a leak."""
    client, returns_coll = _ctx(monkeypatch)
    client.post("/api/v1/returns", json=_exchange_payload(), headers=_HDR)
    assert returns_coll.docs == [], (
        "a refused exchange must leave no return document behind: "
        f"{returns_coll.docs}"
    )


def test_create_exchange_refused_even_with_no_replacement_lines(monkeypatch):
    """An old tab can post an EXCHANGE with an empty replacement list. That is
    the SAME unbilled hand-over, so the TYPE - not the payload shape - is what
    the door refuses."""
    client, returns_coll = _ctx(monkeypatch)
    r = client.post(
        "/api/v1/returns", json=_qa_payload(return_type="EXCHANGE"), headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert _instructs_staff(r.text), r.text
    assert returns_coll.docs == []


# ---------------------------------------------------------------------------
# 2. The QUOTE door refuses too - it is the till's Review step.
# ---------------------------------------------------------------------------


def test_quote_exchange_is_refused(monkeypatch):
    client, _ = _ctx(monkeypatch)
    r = client.post("/api/v1/returns/quote", json=_exchange_payload(), headers=_HDR)
    assert r.status_code == 400, r.text
    assert _instructs_staff(r.text), r.text


# ---------------------------------------------------------------------------
# 4. Everything else is untouched.
# ---------------------------------------------------------------------------


def test_plain_return_still_completes(monkeypatch):
    client, returns_coll = _ctx(monkeypatch)
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_HDR)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["return_type"] == "RETURN"
    assert body["net_refund"] == 1404.20
    assert len(returns_coll.docs) == 1


def test_credit_note_still_completes(monkeypatch):
    client, returns_coll = _ctx(monkeypatch)
    r = client.post(
        "/api/v1/returns",
        json=_qa_payload(return_type="CREDIT_NOTE"),
        headers=_HDR,
    )
    assert r.status_code == 201, r.text
    assert r.json()["return_type"] == "CREDIT_NOTE"
    assert len(returns_coll.docs) == 1


def test_plain_return_quote_still_prices(monkeypatch):
    client, _ = _ctx(monkeypatch)
    r = client.post("/api/v1/returns/quote", json=_qa_payload(), headers=_HDR)
    assert r.status_code == 200, r.text
    assert r.json()["net_refund"] == 1404.20


def test_exchange_recorded_before_the_change_still_reads(monkeypatch):
    """Ruling 3: nothing already in flight may break."""
    client, returns_coll = _ctx(monkeypatch)
    returns_coll.insert_one(
        {
            "return_id": "RET-OLD-EXCH",
            "return_type": "EXCHANGE",
            "store_id": "BV-PUN-01",
            "order_id": "ORD-BOK01",
            "collect_amount": 300.0,
            "collect_method": "CASH",
            "created_at": "2026-08-01T10:00:00",
        }
    )

    got = client.get("/api/v1/returns/RET-OLD-EXCH", headers=_HDR)
    assert got.status_code == 200, got.text
    assert got.json()["return_type"] == "EXCHANGE"
    assert got.json()["collect_amount"] == 300.0

    listed = client.get("/api/v1/returns?store_id=BV-PUN-01", headers=_HDR)
    assert listed.status_code == 200, listed.text
    ids = [d["return_id"] for d in listed.json()["returns"]]
    assert "RET-OLD-EXCH" in ids, listed.text
    assert listed.json()["total"] == 1


def test_history_can_still_be_filtered_to_exchanges(monkeypatch):
    """Searchable, not merely readable."""
    client, returns_coll = _ctx(monkeypatch)
    returns_coll.insert_one(
        {
            "return_id": "RET-OLD-EXCH",
            "return_type": "EXCHANGE",
            "store_id": "BV-PUN-01",
            "created_at": "2026-08-01T10:00:00",
        }
    )
    returns_coll.insert_one(
        {
            "return_id": "RET-OLD-PLAIN",
            "return_type": "RETURN",
            "store_id": "BV-PUN-01",
            "created_at": "2026-08-02T10:00:00",
        }
    )
    r = client.get(
        "/api/v1/returns?store_id=BV-PUN-01&return_type=EXCHANGE", headers=_HDR
    )
    assert r.status_code == 200, r.text
    ids = [d["return_id"] for d in r.json()["returns"]]
    assert ids == ["RET-OLD-EXCH"], r.text
