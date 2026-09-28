"""
Tests for the Shopify FULFILLMENT PUSH-BACK module (IMS -> Shopify).

***** SAFETY-CRITICAL: every Shopify call is MOCKED. *****
The real network boundary is shopify_push._graphql (reused, never forked). It is
monkeypatched in every LIVE test so NO real Shopify request is ever made; the
DARK-by-default tests install a boom-spy proving the network is never reached.

Coverage:
  - DARK by default            -> SIMULATED plan, NO network (boom-spy silent).
  - LIVE happy path            -> fulfillmentOrders -> fulfillmentCreateV2, new
                                  fulfilment gid written BACK on the order.
  - LIVE userErrors            -> ok=False, no write-back.
  - already-fulfilled (no open FulfillmentOrder) -> SKIP + stamp existing gid.
  - idempotent re-call         -> stamped id short-circuits, NO network.
  - send-once create, its next pass (REAL transport): a lost answer re-reads
    the order before re-sending -- an applied create is stamped, never sent
    twice; an unapplied one is sent again; bounded.
  - non-online order           -> clean skip (source!=shopify / no id).
  - dispatch hook (shipping._maybe_push_online_fulfillment) fires EXACTLY once,
    never fires for a non-online / FAILED booking, and swallows any push error
    so it can never block the booking.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_shopify_fulfillment_push.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services import shopify_fulfillment_push as sfp  # noqa: E402


# ===========================================================================
# Helpers
# ===========================================================================

def _run(coro):
    return asyncio.run(coro)


class _FakeDB:
    """In-memory DB exposing get_collection() (and subscript) -> a shared
    MockCollection, so the service's write-back is observable."""

    def __init__(self):
        self._colls = {}

    def get_collection(self, name):
        return self._colls.setdefault(name, MockCollection(name))

    def __getitem__(self, name):
        return self._colls.setdefault(name, MockCollection(name))


class _RoutingGraphQL:
    """Fake shopify_push._graphql: routes by query content to a canned response
    and records every call. Proves the LIVE branch calls it (and how)."""

    def __init__(self, fo_response=None, create_response=None):
        self.calls = []
        self._fo = fo_response
        self._create = create_response

    async def __call__(self, db, query, variables):
        self.calls.append({"query": query, "variables": variables})
        if "fulfillmentOrders" in query:
            return self._fo
        if "fulfillmentCreateV2" in query:
            return self._create
        raise AssertionError("unexpected GraphQL query")


def _force_live(monkeypatch, graphql):
    """Open all three gates on shopify_push (the service reuses that gate) and
    install the given fake as the network boundary."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push, "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {
            "shop_url": "test.myshopify.com",
            "access_token": "shpat_test",
            "source": "vault",
        },
    )
    monkeypatch.setattr(shopify_push, "_graphql", graphql)


def _force_dark(monkeypatch):
    """Close a gate (writes off) so the service is SIMULATED, and install a
    network boundary that EXPLODES if reached."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")

    async def _boom(db, query, variables):  # pragma: no cover - must never run
        raise AssertionError("DARK fulfilment push must not hit the network")

    monkeypatch.setattr(shopify_push, "_graphql", _boom)


def _online_order(**over):
    doc = {
        "order_id": "ORD-1",
        "order_number": "ONL-123",
        "source": "shopify",
        "shopify_order_id": "123",
        "status": "CONFIRMED",
    }
    doc.update(over)
    return doc


# Canned GraphQL bodies -----------------------------------------------------

_FO_OPEN = {
    "data": {
        "order": {
            "id": "gid://shopify/Order/123",
            "fulfillments": [],
            "fulfillmentOrders": {
                "edges": [
                    {"node": {"id": "gid://shopify/FulfillmentOrder/1", "status": "OPEN"}}
                ]
            },
        }
    }
}

_FO_ALREADY_FULFILLED = {
    "data": {
        "order": {
            "id": "gid://shopify/Order/123",
            "fulfillments": [
                {"id": "gid://shopify/Fulfillment/555", "status": "SUCCESS"}
            ],
            "fulfillmentOrders": {
                "edges": [
                    {"node": {"id": "gid://shopify/FulfillmentOrder/1", "status": "CLOSED"}}
                ]
            },
        }
    }
}

_CREATE_OK = {
    "data": {
        "fulfillmentCreateV2": {
            "fulfillment": {
                "id": "gid://shopify/Fulfillment/999",
                "status": "SUCCESS",
                "trackingInfo": {"number": "AWB1", "company": "BlueDart", "url": "http://t"},
            },
            "userErrors": [],
        }
    }
}

_CREATE_ERR = {
    "data": {
        "fulfillmentCreateV2": {
            "fulfillment": None,
            "userErrors": [{"field": ["fulfillment"], "message": "already fulfilled"}],
        }
    }
}


# ===========================================================================
# DARK by default -- SIMULATED, no network
# ===========================================================================

def test_dark_by_default_is_simulated_no_network(monkeypatch):
    _force_dark(monkeypatch)
    order = _online_order()
    res = _run(sfp.push_fulfillment(
        None, order, tracking={"number": "AWB1", "company": "BlueDart", "url": "http://t"}
    ))
    assert res.mode == "SIMULATED"
    assert res.action == "create"
    assert res.ok is True
    assert res.shopify_id is None
    # The plan carries the tracking + order gid, but nothing was written.
    assert res.payload["trackingInfo"] == {
        "number": "AWB1", "company": "BlueDart", "url": "http://t"
    }
    assert res.payload["order_gid"] == "gid://shopify/Order/123"
    assert res.reason  # a why-dark reason is present


# ===========================================================================
# LIVE happy path -- create + write-back
# ===========================================================================

def test_live_happy_path_creates_and_writes_back(monkeypatch):
    graphql = _RoutingGraphQL(fo_response=_FO_OPEN, create_response=_CREATE_OK)
    _force_live(monkeypatch, graphql)
    db = _FakeDB()
    db.get_collection("orders").insert_one(_online_order())

    order = _online_order()
    res = _run(sfp.push_fulfillment(
        db, order, tracking={"number": "AWB1", "company": "BlueDart", "url": "http://t"}
    ))

    assert res.mode == "LIVE"
    assert res.action == "create"
    assert res.ok is True
    assert res.shopify_id == "gid://shopify/Fulfillment/999"
    # Two calls: resolve FOs, then create.
    assert len(graphql.calls) == 2
    create_vars = graphql.calls[1]["variables"]["fulfillment"]
    assert create_vars["lineItemsByFulfillmentOrder"] == [
        {"fulfillmentOrderId": "gid://shopify/FulfillmentOrder/1"}
    ]
    assert create_vars["trackingInfo"]["number"] == "AWB1"
    assert create_vars["notifyCustomer"] is True
    # Write-back stamped the fulfilment id on the order.
    stored = db.get_collection("orders").find_one({"shopify_order_id": "123"})
    assert stored["shopify_fulfillment_id"] == "gid://shopify/Fulfillment/999"
    assert stored.get("shopify_fulfillment_pushed_at")


# ===========================================================================
# LIVE userErrors -- ok=False, no write-back
# ===========================================================================

def test_live_user_errors_is_fail_soft(monkeypatch):
    graphql = _RoutingGraphQL(fo_response=_FO_OPEN, create_response=_CREATE_ERR)
    _force_live(monkeypatch, graphql)
    db = _FakeDB()
    db.get_collection("orders").insert_one(_online_order())

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.mode == "LIVE"
    assert res.ok is False
    assert "already fulfilled" in (res.error or "")
    # No fulfilment id was stamped on a failed push.
    stored = db.get_collection("orders").find_one({"shopify_order_id": "123"})
    assert "shopify_fulfillment_id" not in stored


# ===========================================================================
# Already fulfilled on Shopify -- SKIP + stamp the existing gid
# ===========================================================================

def test_already_fulfilled_skips_and_stamps(monkeypatch):
    # create_response present but MUST NOT be used (no open FO -> no create call).
    graphql = _RoutingGraphQL(
        fo_response=_FO_ALREADY_FULFILLED, create_response=_CREATE_OK
    )
    _force_live(monkeypatch, graphql)
    db = _FakeDB()
    db.get_collection("orders").insert_one(_online_order())

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.mode == "LIVE"
    assert res.action == "skip"
    assert res.ok is True
    assert res.reason == "already_fulfilled_on_shopify"
    assert res.shopify_id == "gid://shopify/Fulfillment/555"
    # Only the FO query ran; the create mutation was never called.
    assert len(graphql.calls) == 1
    stored = db.get_collection("orders").find_one({"shopify_order_id": "123"})
    assert stored["shopify_fulfillment_id"] == "gid://shopify/Fulfillment/555"


# ===========================================================================
# Idempotent re-call -- stamped id short-circuits BEFORE any network
# ===========================================================================

def test_idempotent_recall_short_circuits_no_network(monkeypatch):
    # Any GraphQL call would explode -- proves we never reach the network.
    async def _boom(db, query, variables):  # pragma: no cover
        raise AssertionError("re-call must not hit the network")

    _force_live(monkeypatch, _boom)
    order = _online_order(shopify_fulfillment_id="gid://shopify/Fulfillment/777")
    res = _run(sfp.push_fulfillment(None, order, tracking={"number": "AWB1"}))

    assert res.action == "skip"
    assert res.ok is True
    assert res.shopify_id == "gid://shopify/Fulfillment/777"
    assert "already_pushed" in (res.reason or "")


# ===========================================================================
# Non-online orders -- clean skip
# ===========================================================================

@pytest.mark.parametrize("over", [
    {"source": "pos", "shopify_order_id": "123"},   # in-store order
    {"source": "shopify", "shopify_order_id": ""},   # no shopify id
])
def test_non_online_order_is_skipped(monkeypatch, over):
    async def _boom(db, query, variables):  # pragma: no cover
        raise AssertionError("non-online order must not hit the network")

    _force_live(monkeypatch, _boom)
    order = _online_order(**over)
    res = _run(sfp.push_fulfillment(None, order))
    assert res.action == "skip"
    assert res.ok is True
    assert "not_an_online_order" in (res.reason or "")


# ===========================================================================
# Dispatch hook (shipping._maybe_push_online_fulfillment)
# ===========================================================================

class _ShipResult:
    def __init__(self, status="BOOKED", awb="AWB1", courier="BlueDart", url="http://t"):
        self.status = status
        self.awb = awb
        self.courier = courier
        self.tracking_url = url


def test_hook_fires_exactly_once_for_online_order(monkeypatch):
    from api.routers import shipping

    calls = []

    async def _spy(db, order, *, tracking=None, notify_customer=True):
        calls.append({"order": order, "tracking": tracking})
        return sfp.FulfillmentPushResult(mode="SIMULATED", action="create", ok=True)

    monkeypatch.setattr(sfp, "push_fulfillment", _spy)
    # Audit repo is unavailable in this bare test -> the audit write is a no-op.

    _run(shipping._maybe_push_online_fulfillment(
        None, _online_order(), _ShipResult(), {"user_id": "u1"}
    ))
    assert len(calls) == 1
    assert calls[0]["tracking"] == {"number": "AWB1", "company": "BlueDart", "url": "http://t"}


def test_hook_never_fires_for_non_online_or_failed(monkeypatch):
    from api.routers import shipping

    calls = []

    async def _spy(db, order, *, tracking=None, notify_customer=True):  # pragma: no cover
        calls.append(order)
        return sfp.FulfillmentPushResult(mode="SIMULATED", action="create", ok=True)

    monkeypatch.setattr(sfp, "push_fulfillment", _spy)

    # In-store order -> no fire.
    _run(shipping._maybe_push_online_fulfillment(
        None, _online_order(source="pos"), _ShipResult(), {"user_id": "u1"}
    ))
    # FAILED booking -> no fire (never tell Shopify it shipped).
    _run(shipping._maybe_push_online_fulfillment(
        None, _online_order(), _ShipResult(status="FAILED"), {"user_id": "u1"}
    ))
    assert calls == []


def test_hook_swallows_push_error_never_blocks(monkeypatch):
    from api.routers import shipping

    async def _explode(db, order, *, tracking=None, notify_customer=True):
        raise RuntimeError("shopify down")

    monkeypatch.setattr(sfp, "push_fulfillment", _explode)

    # Must NOT raise -- the booking can never be blocked by a push failure.
    _run(shipping._maybe_push_online_fulfillment(
        None, _online_order(), _ShipResult(), {"user_id": "u1"}
    ))


# ===========================================================================
# SEND-ONCE and its next pass: through the REAL transport (_graphql over
# _post_once). imsFulfillmentCreate is sent once (a lost answer may have been
# applied), so push_fulfillment READS the order again before it re-sends:
# a create that landed has closed its FulfillmentOrder, one that did not is
# still open. Never a blind re-send.
# REVERT-PROOF: (a) no next pass -> the 503 case ends ok=False, the order
# Unfulfilled on Shopify (the booking hook runs once). (b) a blind re-send
# (imsFulfillmentCreate on _REPLAY_SAFE, or a re-create without the re-read)
# -> the lost-answer case sends a second create onto the CLOSED
# FulfillmentOrder: a userError, ok=False, the applied fulfilment unstamped.
# ===========================================================================


class _OrderShopify:
    """A PRODUCTION-SHAPED order on Shopify: one FulfillmentOrder. A create
    naming an OPEN one closes it and adds a Fulfillment; on one that is not
    open it answers a userError and creates nothing (the FulfillmentOrder
    model). ``refuse``: HTTP statuses answered to the next creates BEFORE
    anything applies (an edge refusal). ``lose``: exceptions raised AFTER
    the next creates applied (the answer is lost)."""

    def __init__(self):
        self.posts = []
        self.fo_status = "OPEN"
        self.fulfillments = []
        self.refuse = []
        self.lose = []

    async def post_once(self, url, headers, payload):
        import httpx

        query, variables = payload["query"], payload["variables"]
        if "imsFulfillmentCreate" not in query:
            self.posts.append("read")
            return httpx.Response(200, json={"data": {"order": {
                "id": variables["id"],
                "fulfillments": [{"id": f, "status": "SUCCESS"} for f in self.fulfillments],
                "fulfillmentOrders": {"edges": [
                    {"node": {"id": "gid://shopify/FulfillmentOrder/1", "status": self.fo_status}}
                ]},
            }}})
        self.posts.append("create")
        if self.refuse:
            return httpx.Response(self.refuse.pop(0), text="service unavailable")
        if self.fo_status != "OPEN":
            return httpx.Response(200, json={"data": {"fulfillmentCreateV2": {
                "fulfillment": None,
                "userErrors": [{"field": ["fulfillment"], "message": "Fulfillment order has an unfulfillable status= closed."}],
            }}})
        self.fo_status = "CLOSED"
        fid = "gid://shopify/Fulfillment/%d" % (900 + len(self.fulfillments))
        self.fulfillments.append(fid)
        if self.lose:
            raise self.lose.pop(0)
        return httpx.Response(200, json={"data": {"fulfillmentCreateV2": {
            "fulfillment": {"id": fid, "status": "SUCCESS", "trackingInfo": []},
            "userErrors": [],
        }}})


def _through_transport(monkeypatch):
    shop = _OrderShopify()
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push, "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "t.myshopify.com", "access_token": "shpat_t", "source": "vault"},
    )
    monkeypatch.setattr(shopify_push, "_post_once", shop.post_once)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    db = _FakeDB()
    db.get_collection("orders").insert_one(_online_order())
    return shop, db


def _stamp(db):
    return db.get_collection("orders").find_one({"shopify_order_id": "123"}).get("shopify_fulfillment_id")


def test_an_edge_refusal_of_the_create_is_sent_again_after_a_fresh_read(monkeypatch):
    shop, db = _through_transport(monkeypatch)
    shop.refuse = [503]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is True and res.shopify_id == "gid://shopify/Fulfillment/900", res.error
    assert shop.posts == ["read", "create", "read", "create"]
    assert shop.fulfillments == ["gid://shopify/Fulfillment/900"] and _stamp(db) == res.shopify_id


def test_a_lost_answer_of_an_applied_create_is_never_sent_again(monkeypatch):
    import httpx

    shop, db = _through_transport(monkeypatch)
    shop.lose = [httpx.ReadTimeout("read timed out")]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is True and res.reason == "already_fulfilled_on_shopify", res.error
    assert shop.posts == ["read", "create", "read"], "the re-read found it applied: no second create"
    assert shop.fulfillments == ["gid://shopify/Fulfillment/900"] and _stamp(db) == "gid://shopify/Fulfillment/900"


def test_a_shopify_that_keeps_refusing_is_given_up_on_after_a_bounded_number_of_passes(monkeypatch):
    shop, db = _through_transport(monkeypatch)
    shop.refuse = [503] * 20

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is False and "not retried" in (res.error or "")
    assert shop.posts == ["read", "create"] * shopify_push._MAX_RETRIES
    assert shop.fulfillments == [] and _stamp(db) is None
