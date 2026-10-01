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
    naming an OPEN one closes it and adds a Fulfillment carrying the
    create's tracking number; on one that is not open it answers a userError
    and creates nothing (the FulfillmentOrder model). ``refuse``: HTTP
    statuses answered to the next creates BEFORE anything applies (an edge
    refusal). ``lose``: exceptions raised AFTER the next creates applied
    (the answer is lost). ``late``: HTTP statuses answered to the next
    creates while Shopify is STILL COMMITTING them -- each lands right after
    the next read has answered (the read saw the FulfillmentOrder open).
    ``cancelled``: Fulfillments already on the order, cancelled in the
    admin. ``others``: (gid, tracking number) of live Fulfillments someone
    else made (a partial fulfilment in the admin: the FulfillmentOrder of the
    rest stays OPEN). ``log`` interleaves the reads, creates and the backoff sleeps."""

    def __init__(self):
        self.posts = []
        self.log = []
        self.sleeps = []
        self.fo_status = "OPEN"
        self.fulfillments = []
        self.tracking = {}
        self.cancelled = []
        self.others = []
        self.refuse = []
        self.lose = []
        self.late = []
        self._committing = None

    def _commit(self, variables):
        self.fo_status = "CLOSED"
        fid = "gid://shopify/Fulfillment/%d" % (900 + len(self.fulfillments))
        self.fulfillments.append(fid)
        self.tracking[fid] = ((variables["fulfillment"].get("trackingInfo") or {}).get("number"))
        return fid

    async def post_once(self, url, headers, payload):
        import httpx

        query, variables = payload["query"], payload["variables"]
        if "imsFulfillmentCreate" not in query:
            self.posts.append("read")
            self.log.append("read")
            answer = httpx.Response(200, json={"data": {"order": {
                "id": variables["id"],
                "fulfillments": [{"id": f, "status": "CANCELLED", "trackingInfo": []} for f in self.cancelled]
                + [{"id": f, "status": "SUCCESS", "trackingInfo": [{"number": n}]} for f, n in self.others]
                + [
                    {"id": f, "status": "SUCCESS", "trackingInfo": [{"number": self.tracking.get(f)}]}
                    for f in self.fulfillments
                ],
                "fulfillmentOrders": {"edges": [
                    {"node": {"id": "gid://shopify/FulfillmentOrder/1", "status": self.fo_status}}
                ]},
            }}})
            if self._committing is not None:
                self._commit(self._committing)
                self._committing = None
            return answer
        self.posts.append("create")
        self.log.append("create")
        if self.refuse:
            return httpx.Response(self.refuse.pop(0), text="service unavailable")
        if self.late:
            self._committing = variables
            return httpx.Response(self.late.pop(0), text="gateway timeout")
        if self.fo_status != "OPEN":
            return httpx.Response(200, json={"data": {"fulfillmentCreateV2": {
                "fulfillment": None,
                "userErrors": [{"field": ["fulfillment"], "message": "Fulfillment order has an unfulfillable status= closed."}],
            }}})
        fid = self._commit(variables)
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

    async def _no_sleep(s):
        shop.log.append("sleep")
        shop.sleeps.append(s)

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


@pytest.mark.parametrize("lost", ["read-timeout", "disconnected", "read-error"])
def test_a_lost_answer_of_an_applied_create_is_never_sent_again(monkeypatch, lost):
    """A dropped connection after the create left is a lost answer like a
    read timeout: the next pass reads, finds it applied and stamps it.
    REVERT-PROOF (the panel's fulfilment probe): SentOnce only for a timeout
    -> the RemoteProtocolError / ReadError cases end ok=False after
    ['read', 'create'], the FulfillmentOrder closed but nothing stamped."""
    import httpx

    shop, db = _through_transport(monkeypatch)
    shop.lose = [{
        "read-timeout": httpx.ReadTimeout("read timed out"),
        "disconnected": httpx.RemoteProtocolError("Server disconnected without sending a response."),
        "read-error": httpx.ReadError("connection reset by peer"),
    }[lost]]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is True and res.reason == "ims_create_landed", res.error
    assert shop.posts == ["read", "create", "read"], "the re-read found it applied: no second create"
    assert shop.fulfillments == ["gid://shopify/Fulfillment/900"] and _stamp(db) == "gid://shopify/Fulfillment/900"


def test_a_shopify_that_keeps_refusing_is_given_up_on_after_a_bounded_number_of_passes(monkeypatch):
    shop, db = _through_transport(monkeypatch)
    shop.refuse = [503] * 20

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is False and "not retried" in (res.error or "")
    assert shop.posts == ["read", "create"] * shopify_push._MAX_RETRIES
    assert shop.fulfillments == [] and _stamp(db) is None


def test_the_next_pass_backs_off_like_the_transport_before_it_reads(monkeypatch):
    """Round 4 (R6 / R10). After a lost answer the next pass WAITS the
    transport's own first backoff (1s + jitter) before it reads the order: a
    read right after a client timeout is the read most likely to miss a
    create Shopify is still committing.
    REVERT-PROOF: the sleep before the next pass removed -> the log reads
    read, create, read -- no wait."""
    import httpx

    shop, db = _through_transport(monkeypatch)
    shop.lose = [httpx.ReadTimeout("read timed out")]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is True, res.error
    assert shop.log == ["read", "create", "sleep", "read"]
    assert 1.0 <= shop.sleeps[0] <= 1.5


def test_a_create_that_lands_after_the_re_read_is_read_again_not_failed(monkeypatch):
    """Round 4. Pass 1's create answers 504 while Shopify is still committing
    it; pass 2 reads the FulfillmentOrder still OPEN, the first create then
    lands, and pass 2's create is refused ('unfulfillable status= closed').
    That refusal is read again, never reported as a plain failure: the
    fulfilment Shopify holds -- IMS's, by its tracking number -- is stamped,
    and there is exactly one.
    REVERT-PROOF: a later pass's userError returned as is -> ok=False, the
    applied fulfilment unstamped (and the booking hook never runs again)."""
    shop, db = _through_transport(monkeypatch)
    shop.late = [504]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is True and res.reason == "ims_create_landed", res.error
    assert shop.posts == ["read", "create", "read", "create", "read"]
    assert shop.fulfillments == ["gid://shopify/Fulfillment/900"] and _stamp(db) == res.shopify_id


def test_a_cancelled_fulfilment_is_never_stamped_as_ims_own(monkeypatch):
    """Round 4. The order already carries a Fulfillment cancelled in the
    Shopify admin (Fulfillment/1) and an OPEN FulfillmentOrder. IMS's create
    commits and its answer is lost: the next pass stamps IMS's own
    fulfilment (900, its tracking number), never the cancelled one, and says
    it is IMS's create that landed -- not an out-of-band fulfilment.
    REVERT-PROOF: stamp the first Fulfillment whatever its status ->
    Fulfillment/1 stamped, reason already_fulfilled_on_shopify."""
    import httpx

    shop, db = _through_transport(monkeypatch)
    shop.cancelled = ["gid://shopify/Fulfillment/1"]
    shop.lose = [httpx.ReadTimeout("read timed out")]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is True and res.shopify_id == "gid://shopify/Fulfillment/900", res
    assert res.reason == "ims_create_landed" and _stamp(db) == "gid://shopify/Fulfillment/900"


def test_a_create_refused_unapplied_is_not_given_a_next_pass(monkeypatch):
    """A create Shopify refused before applying it (a 4xx: a plain error, not
    SentOnce) is reported as it is -- no re-read, no second create.
    REVERT-PROOF (the panel's M8): the next pass taken on any exception ->
    posts read, create, read, create and ok=True on a refusal."""
    shop, db = _through_transport(monkeypatch)
    shop.refuse = [422]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is False and "status 422" in (res.error or ""), res
    assert shop.posts == ["read", "create"] and shop.fulfillments == [] and _stamp(db) is None


def test_ims_own_landed_create_is_stamped_over_an_earlier_fulfilment_of_someone_else(monkeypatch):
    """A person fulfilled part of the order in the admin (Fulfillment/1,
    their own tracking number); the rest stays OPEN. IMS's create of the
    rest commits and its answer is lost: the next pass stamps IMS's own
    (900, IMS's tracking number) -- not the person's, which comes first.
    REVERT-PROOF (the panel's M6): stamp the first live fulfilment ->
    Fulfillment/1 stamped, reason already_fulfilled_on_shopify."""
    import httpx

    shop, db = _through_transport(monkeypatch)
    shop.others = [("gid://shopify/Fulfillment/1", "HANDAWB")]
    shop.lose = [httpx.ReadTimeout("read timed out")]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is True and res.reason == "ims_create_landed", res
    assert res.shopify_id == "gid://shopify/Fulfillment/900" == _stamp(db)


def test_an_order_with_nothing_open_and_only_a_cancelled_fulfilment_is_never_stamped(monkeypatch):
    """The order's FulfillmentOrder is closed and its only Fulfillment was
    cancelled in the admin: nothing to fulfil and nothing live to stamp -- a
    clean noop, the order left unstamped.
    REVERT-PROOF (the panel's M7): drop the cancelled-status filter -> the
    cancelled Fulfillment/1 stamped as 'already_fulfilled_on_shopify'."""
    shop, db = _through_transport(monkeypatch)
    shop.fo_status = "CLOSED"
    shop.cancelled = ["gid://shopify/Fulfillment/1"]

    res = _run(sfp.push_fulfillment(db, _online_order(), tracking={"number": "AWB1"}))

    assert res.ok is True and res.action == "noop", res
    assert _stamp(db) is None and shop.posts == ["read"]
