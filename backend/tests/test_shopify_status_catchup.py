"""
Sync audit -- the one remaining inbound gap: STATUS changes on orders IMS
already booked whose webhook Shopify failed to deliver.

#1130's hourly pull booked orders IMS had never seen; a cancellation, payment,
fulfilment or refund on an order already in IMS stayed stale. Now the pull
(nexus_providers.shopify_pull_orders) fetches by updated_at and, for every
order already in IMS, compares the Shopify-owned facts against the IMS doc and
feeds each one that moved through the SAME handler its webhook would have
reached: map_shopify_order (orders/*), reconcile_fulfillment (fulfillments/*),
handle_shopify_refund (refunds/create). The sweep decides only WHETHER to call.

Rig: the order-catchup `pull` fixture (mapper FakeDB + REAL mapper/ingest, a
faked Shopify fetch, faked creds, live dispatch) plus the phase-0 webhook
test's returns/stock wiring so the REAL refund handler runs too. Nothing here
is hollow: every rule has a test that goes red when that rule alone is
reverted (table in the PR body).
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_online_order_mapper import wired  # noqa: E402,F401 (fixture `pull` depends on it)
from test_shopify_order_catchup import UPDATED, _pulled, pull  # noqa: E402,F401 (fixture)
from test_shopify_webhooks_phase0 import _FakeConn, _FakeStockRepo  # noqa: E402

from agents import nexus_providers as np  # noqa: E402
from api.routers import online_store_orders as oso  # noqa: E402
from api.services import online_order_mapper, shopify_fulfillment, shopify_refund  # noqa: E402

CANCELLED_AT = "2026-09-06T00:50:00Z"


@pytest.fixture
def swept(pull, monkeypatch):
    """The pull plus the refund handler's wiring: the reused returns machinery
    resolves its repos from the returns router's namespace and the ledger via
    dependencies.get_db, so refunds run REAL against the same FakeDB."""
    import api.dependencies as deps
    import api.routers.returns as returns_router
    from database.repositories.customer_repository import CustomerRepository

    db = pull["db"]
    stock_repo = _FakeStockRepo()
    customer_repo = CustomerRepository(db.get_collection("customers"))
    orders_coll = db.get_collection("orders")

    class _OrderRepo:
        def find_by_id(self, oid):
            return orders_coll.find_one({"order_id": oid})

    monkeypatch.setattr(returns_router, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: _OrderRepo())
    monkeypatch.setattr(deps, "get_db", lambda: _FakeConn(db), raising=False)
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)

    refund_calls = []
    real_refund = shopify_refund.handle_shopify_refund

    def refund_spy(db_, payload, **kw):
        refund_calls.append(str(payload.get("id")))
        return real_refund(db_, payload, **kw)

    monkeypatch.setattr(shopify_refund, "handle_shopify_refund", refund_spy)

    pull.update(
        stock_repo=stock_repo,
        review=db["shopify_refund_review"],
        returns=db["returns"],
        ledger=db["credit_note_ledger"],
        refund_calls=refund_calls,
    )
    return pull


def _book(swept, order_id, **over):
    """Book the order the way a delivered orders/create would, then forget the
    spy saw it. Returns the IMS doc."""
    res = swept["real_map"](_pulled(order_id, **over), swept["db"], topic="orders/create")
    assert res["status"] == "created"
    swept["seen"].clear()
    return swept["orders"].find_one({"shopify_order_id": str(order_id)})


def _doc(swept, order_id):
    return swept["orders"].find_one({"shopify_order_id": str(order_id)})


def _snap(doc):
    return {k: v for k, v in doc.items() if k != "updated_at"}


def _refund(refund_id, order_id, *, restock_type="cancel", amount="999.00"):
    """A Shopify Refund as it sits nested in orders.json (== the refunds/create
    body): one line against the booked frame line (Shopify line id 9001)."""
    return {
        "id": refund_id,
        "order_id": order_id,
        "restock": True,
        "created_at": UPDATED,
        "refund_line_items": [
            {
                "id": refund_id * 10,
                "quantity": 1,
                "line_item_id": 9001,
                "restock_type": restock_type,
                "subtotal": 951.43,
                "total_tax": 47.57,
                "line_item": {
                    "id": 9001,
                    "variant_id": 999001,
                    "product_id": 7001,
                    "sku": "RB-1234",
                    "quantity": 1,
                    "price": "999.00",
                },
            }
        ],
        "transactions": [{"kind": "refund", "status": "success", "amount": amount}],
    }


def _fulfilment(order_id, fid, **over):
    """A Shopify Fulfillment as nested in orders.json (== fulfillments/* body)."""
    f = {
        "id": fid,
        "order_id": order_id,
        "status": "success",
        "tracking_number": f"AWB{order_id}",
        "tracking_company": "Delhivery",
        "tracking_url": f"https://track/AWB{order_id}",
        "created_at": UPDATED,
        "updated_at": UPDATED,
    }
    f.update(over)
    return f


def _claim_unit(swept, doc):
    """Make the booked order look like ingest claimed a serialized unit for it
    (the stock decrement that runs when the sku resolves to an IMS product)."""
    items = [{**doc["items"][0], "ims_product_id": "IMS-P-1"}]
    swept["orders"].update_one(
        {"shopify_order_id": doc["shopify_order_id"]},
        {"$set": {"items": items, "fulfillment_stores": ["BV-GANGA-01"]}},
    )
    swept["stock_repo"].units.append(
        {
            "stock_id": "stk-1",
            "product_id": "IMS-P-1",
            "store_id": "BV-GANGA-01",
            "order_id": doc["order_id"],
            "status": "SOLD",
        }
    )


# ---------------------------------------------------------------------------
# Rule: cancelled on Shopify -> the IMS order is cancelled through the mapper
# and the cancel Refund (the door that gives the units back) is fed through the
# refund handler -- each once; a second sweep re-applies nothing.
# ---------------------------------------------------------------------------


def test_cancelled_on_shopify_lands_cancelled_and_feeds_the_cancel_refund_once(swept):
    doc = _book(swept, 30001)
    assert doc["status"] == "CONFIRMED" and doc["payment_status"] == "PAID"

    swept["state"]["orders"] = [
        _pulled(
            30001,
            cancelled_at=CANCELLED_AT,
            financial_status="refunded",
            refunds=[_refund(700301, 30001)],
        )
    ]
    res = swept["run"]()

    assert res.ok is True
    p = res.payload
    assert p["already_in_ims"] == 1 and p["mapped"] == []
    assert p["status_synced"] == ["30001"]
    assert p["status_failed"] == [] and p["status_skipped_terminal"] == []
    assert res.items_synced == 1
    doc = _doc(swept, 30001)
    assert doc["status"] == "CANCELLED"
    assert doc["cancelled_at"] == CANCELLED_AT
    assert doc["payment_status"] == "REFUNDED"
    assert len(swept["orders"].docs) == 1, "count-once: never a 2nd IMS order"
    # The cancel refund reached the REAL refund handler: default posture queues
    # it for the accountant WITH the restock lines (no ledger, no stock write).
    assert swept["refund_calls"] == ["700301"]
    review = swept["review"].find_one({"shopify_refund_id": "700301"})
    assert review is not None and review["status"] == "PENDING"
    assert swept["returns"].count_documents({}) == 0

    # The inbox carries each synthesised delivery (source=shopify_pull) ...
    row = swept["inbox"].find_one({"_id": f"pull:30001:{UPDATED}"})
    assert row["source"] == "shopify_pull"
    assert row["headers"]["x-shopify-topic"] == "orders/cancelled"
    assert row["payload"]["line_items"][0]["sku"] == "RB-1234"
    assert row["handler_error"] is None and row["skipped_reason"] is None
    rrow = swept["inbox"].find_one({"_id": f"pull:30001:{UPDATED}:refunds/create:700301"})
    assert rrow["headers"]["x-shopify-topic"] == "refunds/create"
    assert rrow["payload"]["id"] == 700301
    # ... and the remap door finds the ORDER row (never the refund child).
    payload, _wid, topic = oso._load_last_shopify_payload(swept["db"], "30001")
    assert payload["id"] == 30001 and topic == "orders/cancelled"

    # Second sweep: Shopify and IMS agree -> no handler is even called.
    before = _snap(doc)
    swept["seen"].clear()
    res2 = swept["run"]()
    assert res2.payload["status_synced"] == [] and res2.payload["status_failed"] == []
    assert res2.payload["already_in_ims"] == 1
    assert swept["seen"] == [] and swept["refund_calls"] == ["700301"]
    assert _snap(_doc(swept, 30001)) == before
    assert swept["review"].count_documents({"shopify_refund_id": "700301"}) == 1


def test_cancel_refund_puts_the_claimed_unit_back_once_under_auto_posture(swept, monkeypatch):
    """With the owner's opt-in (SHOPIFY_REFUND_AUTO) the sweep's refund call
    restocks the serialized unit ingest claimed -- through the refund handler's
    own restock, exactly as the webhook path does -- and a second sweep neither
    re-credits nor mints a phantom second unit."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    doc = _book(swept, 30002)
    _claim_unit(swept, doc)
    assert swept["stock_repo"].units[0]["status"] == "SOLD"

    swept["state"]["orders"] = [
        _pulled(
            30002,
            cancelled_at=CANCELLED_AT,
            financial_status="refunded",
            refunds=[_refund(700302, 30002)],
        )
    ]
    assert swept["run"]().payload["status_synced"] == ["30002"]

    assert _doc(swept, 30002)["status"] == "CANCELLED"
    units = swept["stock_repo"].units
    assert len(units) == 1 and units[0]["status"] == "AVAILABLE", "the SAME unit, back on the shelf"
    ret = swept["returns"].find_one({"shopify_refund_id": "700302"})
    assert ret is not None and ret["status"] == "COMPLETED"
    assert swept["ledger"].count_documents({}) == 1

    swept["run"]()
    assert len(swept["stock_repo"].units) == 1, "no phantom unit minted on the re-run"
    assert swept["returns"].count_documents({}) == 1
    assert swept["ledger"].count_documents({}) == 1
    assert swept["refund_calls"] == ["700302"]


# ---------------------------------------------------------------------------
# Rule: paid on Shopify -> the payment is recorded once (the mapper's money leg)
# ---------------------------------------------------------------------------


def test_payment_landed_on_shopify_is_recorded_once(swept):
    doc = _book(swept, 30003, financial_status="pending")
    assert doc["payment_status"] == "UNPAID" and doc["amount_paid"] == 0.0
    assert doc["payments"] == []

    swept["state"]["orders"] = [_pulled(30003, financial_status="paid")]
    assert swept["run"]().payload["status_synced"] == ["30003"]

    doc = _doc(swept, 30003)
    assert doc["payment_status"] == "PAID"
    assert doc["amount_paid"] == doc["grand_total"] == 999.0 and doc["balance_due"] == 0.0
    assert len(doc["payments"]) == 1, "ONE synthesized gateway row for the collected money"
    assert doc["status"] == "CONFIRMED"
    row = swept["inbox"].find_one({"_id": f"pull:30003:{UPDATED}"})
    assert row["headers"]["x-shopify-topic"] == "orders/paid"

    payments = copy.deepcopy(doc["payments"])
    swept["seen"].clear()
    assert swept["run"]().payload["status_synced"] == []
    assert swept["seen"] == []
    assert _doc(swept, 30003)["payments"] == payments


def test_ims_paid_by_the_counter_is_not_knocked_back_by_shopify_pending(swept):
    """A COD order the shop collected at the counter is PAID in IMS while
    Shopify still says pending; the sweep must not re-fire the mapper every
    hour over it (the mapper has no knowledge of the till's tender)."""
    _book(swept, 30016, financial_status="pending")
    swept["orders"].update_one(
        {"shopify_order_id": "30016"},
        {"$set": {"payment_status": "PAID", "amount_paid": 999.0, "balance_due": 0.0}},
    )

    swept["state"]["orders"] = [_pulled(30016, financial_status="pending")]
    p = swept["run"]().payload

    assert p["status_synced"] == [] and swept["seen"] == []
    assert _doc(swept, 30016)["payment_status"] == "PAID"


# ---------------------------------------------------------------------------
# Rule: a fulfilment IMS never saw -> SHIPPED with the AWB via the fulfilment
# reconcile (the mapper is not needed once the reconcile landed the fact)
# ---------------------------------------------------------------------------


def test_fulfilment_ims_missed_lands_shipped_with_awb_once(swept):
    _book(swept, 30004)

    swept["state"]["orders"] = [
        _pulled(30004, fulfillment_status="fulfilled", fulfillments=[_fulfilment(30004, 88804)])
    ]
    assert swept["run"]().payload["status_synced"] == ["30004"]

    doc = _doc(swept, 30004)
    assert doc["status"] == "SHIPPED"
    assert doc["awb"] == "AWB30004" and doc["tracking_company"] == "Delhivery"
    assert doc["fulfillment_status"] == "FULFILLED"
    assert doc["shopify_fulfillment_id"] == "88804"
    # The reconcile landed the fulfilment fact, so the mapper had nothing left
    # to sync (and did not flip the parcel to DELIVERED on its own).
    assert swept["seen"] == []
    row = swept["inbox"].find_one({"_id": f"pull:30004:{UPDATED}:fulfillments/update:88804"})
    assert row["headers"]["x-shopify-topic"] == "fulfillments/update"
    assert row["payload"]["id"] == 88804 and row["source"] == "shopify_pull"

    before = _snap(doc)
    assert swept["run"]().payload["status_synced"] == []
    assert _snap(_doc(swept, 30004)) == before


def test_a_newer_fulfilment_replaces_the_stamped_one(swept):
    """Only the NEWEST Shopify fulfilment is compared (the reconcile stores one
    id): a fulfilment re-created under the SAME AWB (label cancelled and
    re-issued) is recognised by its id alone, and a later re-shipment with a
    new AWB lands that."""
    _book(swept, 30017)
    swept["state"]["orders"] = [
        _pulled(30017, fulfillment_status="fulfilled", fulfillments=[_fulfilment(30017, 1)])
    ]
    swept["run"]()
    assert _doc(swept, 30017)["shopify_fulfillment_id"] == "1"

    same_awb = _fulfilment(30017, 2, updated_at="2026-09-06T02:00:00Z")
    swept["state"]["orders"] = [
        _pulled(30017, fulfillment_status="fulfilled", fulfillments=[_fulfilment(30017, 1), same_awb])
    ]
    assert swept["run"]().payload["status_synced"] == ["30017"]
    doc = _doc(swept, 30017)
    assert doc["shopify_fulfillment_id"] == "2" and doc["awb"] == "AWB30017"

    reship = _fulfilment(30017, 3, tracking_number="AWB-RESHIP", updated_at="2026-09-06T03:00:00Z")
    swept["state"]["orders"] = [
        _pulled(30017, fulfillment_status="fulfilled", fulfillments=[same_awb, reship, _fulfilment(30017, 1)])
    ]
    assert swept["run"]().payload["status_synced"] == ["30017"]
    doc = _doc(swept, 30017)
    assert doc["shopify_fulfillment_id"] == "3" and doc["awb"] == "AWB-RESHIP"


# ---------------------------------------------------------------------------
# Rule: a terminal IMS status is never knocked back -- the mapper has no guard
# of its own, so the sweep withholds the call and reports it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ims_state, shopify_body",
    [
        # Delivered in IMS; Shopify later shows the fulfilment restocked -> the
        # mapper would derive CONFIRMED.
        ({"status": "DELIVERED", "fulfillment_status": "FULFILLED"}, {"fulfillment_status": "restocked"}),
        # Cancelled by staff in IMS; Shopify (still open there) partially refunds.
        ({"status": "CANCELLED"}, {"financial_status": "partially_refunded"}),
    ],
)
def test_terminal_ims_status_is_never_knocked_back(swept, ims_state, shopify_body):
    _book(swept, 30005)
    swept["orders"].update_one({"shopify_order_id": "30005"}, {"$set": ims_state})
    before = _snap(_doc(swept, 30005))

    swept["state"]["orders"] = [_pulled(30005, **shopify_body)]
    p = swept["run"]().payload

    assert p["status_skipped_terminal"] == ["30005"]
    assert p["status_synced"] == [] and p["status_failed"] == []
    assert swept["seen"] == []
    assert _snap(_doc(swept, 30005)) == before
    assert "terminal-skipped 1" in swept["run"]().notes


def test_a_fact_that_keeps_the_terminal_status_still_lands(swept):
    """The guard is 'would the mapper CHANGE a terminal status', not 'is it
    terminal': a refund on an order both sides agree is CANCELLED lands."""
    _book(swept, 30018)
    swept["orders"].update_one(
        {"shopify_order_id": "30018"}, {"$set": {"status": "CANCELLED", "cancelled_at": CANCELLED_AT}}
    )

    swept["state"]["orders"] = [_pulled(30018, cancelled_at=CANCELLED_AT, financial_status="refunded")]
    p = swept["run"]().payload

    assert p["status_synced"] == ["30018"] and p["status_skipped_terminal"] == []
    doc = _doc(swept, 30018)
    assert doc["status"] == "CANCELLED" and doc["payment_status"] == "REFUNDED"


# ---------------------------------------------------------------------------
# Rule: a refund IMS never saw -> one accountant review row; the handler's own
# dedupe is asked first so a seen refund costs no handler call every hour
# ---------------------------------------------------------------------------


def test_refund_ims_missed_is_queued_for_the_accountant_once(swept):
    _book(swept, 30006)

    swept["state"]["orders"] = [
        _pulled(
            30006,
            financial_status="partially_refunded",
            refunds=[_refund(700306, 30006, restock_type="return")],
        )
    ]
    assert swept["run"]().payload["status_synced"] == ["30006"]

    doc = _doc(swept, 30006)
    assert doc["payment_status"] == "PARTIAL_REFUND" and doc["status"] == "CONFIRMED"
    review = swept["review"].find_one({"shopify_refund_id": "700306"})
    assert review is not None and review["status"] == "PENDING"
    assert swept["refund_calls"] == ["700306"]

    swept["run"]()
    assert swept["refund_calls"] == ["700306"], "a seen refund is not re-fed"
    assert swept["review"].count_documents({"shopify_refund_id": "700306"}) == 1


# ---------------------------------------------------------------------------
# Rule: the real webhook arriving AFTER the sweep applied the same change is a
# no-op (the handlers' own idempotency, exercised end to end)
# ---------------------------------------------------------------------------


def test_status_webhook_after_the_sweep_is_a_no_op(swept):
    _book(swept, 30007)
    body = _pulled(
        30007,
        cancelled_at=CANCELLED_AT,
        financial_status="refunded",
        refunds=[_refund(700307, 30007)],
    )
    swept["state"]["orders"] = [body]
    assert swept["run"]().payload["status_synced"] == ["30007"]
    before = _snap(_doc(swept, 30007))

    # Shopify finally delivers orders/cancelled and refunds/create.
    res = swept["real_map"](
        copy.deepcopy(body), swept["db"], webhook_id="real-cancel-1", topic="orders/cancelled"
    )
    assert res["status"] == "duplicate" and res["order_id"]
    rres = shopify_refund.handle_shopify_refund(
        swept["db"], copy.deepcopy(body["refunds"][0]), webhook_id="real-refund-1", topic="refunds/create"
    )
    assert rres["status"] == "duplicate"

    assert _snap(_doc(swept, 30007)) == before
    assert len(swept["orders"].docs) == 1
    assert swept["review"].count_documents({"shopify_refund_id": "700307"}) == 1


# ---------------------------------------------------------------------------
# Rule: dark dispatch mode applies nothing -- it records what it WOULD have done
# ---------------------------------------------------------------------------


def test_dark_mode_applies_no_status_change(swept):
    swept["mp"].setattr(np, "shopify_dispatch_mode", lambda: "off")
    _book(swept, 30008)
    before = _snap(_doc(swept, 30008))

    swept["state"]["orders"] = [
        _pulled(
            30008,
            cancelled_at=CANCELLED_AT,
            financial_status="refunded",
            refunds=[_refund(700308, 30008)],
            fulfillments=[_fulfilment(30008, 88808)],
        )
    ]
    res = swept["run"]()

    p = res.payload
    assert p["skipped_dark"] == ["30008"]
    assert p["status_synced"] == [] and p["status_failed"] == []
    assert p["watermark_advanced"] is False
    assert _snap(_doc(swept, 30008)) == before
    assert swept["seen"] == [] and swept["refund_calls"] == []
    assert swept["review"].count_documents({}) == 0
    for key in (
        f"pull:30008:{UPDATED}",
        f"pull:30008:{UPDATED}:fulfillments/update:88808",
        f"pull:30008:{UPDATED}:refunds/create:700308",
    ):
        row = swept["inbox"].find_one({"_id": key})
        assert row["source"] == "shopify_pull" and "not live" in row["skipped_reason"]


# ---------------------------------------------------------------------------
# Rule: a handler exception on one order never stops the others (nor the other
# handlers of the same order); it is surfaced where the operator looks
# ---------------------------------------------------------------------------


def test_a_handler_exception_on_one_order_never_stops_the_others(swept):
    for oid in (30009, 30010, 30011, 30019):
        _book(swept, oid, financial_status="pending")

    real_reconcile = shopify_fulfillment.reconcile_fulfillment

    def exploding_reconcile(db, payload, **kw):
        if str(payload.get("order_id")) == "30009":
            raise RuntimeError("reconcile exploded")
        return real_reconcile(db, payload, **kw)

    real_map = swept["real_map"]

    def exploding_map(payload, db, **kw):
        if str(payload.get("id")) == "30010":
            raise RuntimeError("mapper exploded")
        return real_map(payload, db, **kw)

    real_newest = np._newest_fulfilment

    def exploding_newest(order):
        if str(order.get("id")) == "30019":
            raise RuntimeError("comparison exploded")  # the sweep's own code, not a handler
        return real_newest(order)

    swept["mp"].setattr(shopify_fulfillment, "reconcile_fulfillment", exploding_reconcile)
    swept["mp"].setattr(online_order_mapper, "map_shopify_order", exploding_map)
    swept["mp"].setattr(np, "_newest_fulfilment", exploding_newest)
    swept["state"]["orders"] = [
        _pulled(30009, cancelled_at=CANCELLED_AT, fulfillments=[_fulfilment(30009, 1)]),
        _pulled(30010, cancelled_at=CANCELLED_AT),
        _pulled(30019, cancelled_at=CANCELLED_AT),
        _pulled(30011, financial_status="paid"),
    ]
    res = swept["run"]()

    assert res.ok is True, "per-order failures never fail the run"
    p = res.payload
    assert p["status_failed"] == ["30009", "30010", "30019"]
    assert p["status_synced"] == ["30011"]
    assert "fulfillments/update:RuntimeError" in p["failed_reasons"]["30009"]
    assert "orders/cancelled:RuntimeError" in p["failed_reasons"]["30010"]
    assert "RuntimeError: comparison exploded" in p["failed_reasons"]["30019"]
    # The failing handler did not stop the SAME order's other handler ...
    assert _doc(swept, 30009)["status"] == "CANCELLED"
    assert _doc(swept, 30010)["status"] == "CONFIRMED"
    assert _doc(swept, 30011)["payment_status"] == "PAID"
    # ... and the failure sits on the inbox row the remap door reads.
    row = swept["inbox"].find_one({"_id": f"pull:30009:{UPDATED}:fulfillments/update:1"})
    assert "RuntimeError" in row["handler_error"]


def test_a_handler_that_fails_soft_is_reported_not_counted_as_synced(swept):
    """The handlers never raise -- they answer {'status': 'skipped'/'error'} on
    an internal failure -- so the sweep must read the verdict, not the return."""
    for oid in (30020, 30021):
        _book(swept, oid)
    real_map = swept["real_map"]
    swept["mp"].setattr(
        online_order_mapper,
        "map_shopify_order",
        lambda payload, db, **kw: (
            {"status": "skipped", "reason": "exception:KeyError"}
            if str(payload.get("id")) == "30020" else real_map(payload, db, **kw)
        ),
    )
    swept["mp"].setattr(
        shopify_fulfillment,
        "reconcile_fulfillment",
        lambda db, payload, **kw: {"status": "error", "error": "write failed"},
    )
    swept["state"]["orders"] = [
        _pulled(30020, cancelled_at=CANCELLED_AT),
        _pulled(30021, fulfillment_status="fulfilled", fulfillments=[_fulfilment(30021, 21)]),
    ]
    p = swept["run"]().payload

    assert p["status_failed"] == ["30020", "30021"] and p["status_synced"] == []
    assert p["failed_reasons"]["30020"] == "orders/cancelled:skipped:exception:KeyError"
    assert p["failed_reasons"]["30021"] == "fulfillments/update:error:write failed"
    row = swept["inbox"].find_one({"_id": f"pull:30020:{UPDATED}"})
    assert row["handler_error"] == "orders/cancelled:skipped:exception:KeyError"


# ---------------------------------------------------------------------------
# Rule: the STATUS window is by updated_at (an old order cancelled today is
# seen) while the CREATE window stays #1130's created_at (an old order IMS
# never booked is never booked now either)
# ---------------------------------------------------------------------------


def test_status_sweep_sees_an_old_order_but_the_create_path_never_books_one(swept):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _book(swept, 30012)

    swept["state"]["orders"] = [
        _pulled(30012, created_at=old, cancelled_at=CANCELLED_AT),  # booked months ago, cancelled today
        _pulled(30013, created_at=old),  # never booked, merely touched today
    ]
    res = swept["run"]()

    p = res.payload
    assert p["status_synced"] == ["30012"]
    assert _doc(swept, 30012)["status"] == "CANCELLED"
    assert p["mapped"] == [] and p["failed"] == []
    assert p["outside_create_window"] == 1
    assert "30013" not in swept["seen"]
    assert len(swept["orders"].docs) == 1, "no GST invoice minted for a stale sale"
    # The fetch was asked by updated_at (the fake's keyword-only signature pins
    # the parameter name); the window value itself is covered next door.
    assert swept["state"]["calls"] and swept["state"]["calls"][-1] == p["since"]


# ---------------------------------------------------------------------------
# Rule: the booked-order lookup hands the comparisons the WHOLE doc. The mapper
# FakeCollection ignores projections, so no end-to-end test above can tell a
# projected lookup (#1130's {_id: 1}) from a full one -- on real Mongo it would
# hand every comparison None: each cancelled order re-fed hourly, and the
# terminal guard blind to DELIVERED. Pinned against a stub that honours the
# projection the way Mongo does.
# ---------------------------------------------------------------------------


def test_booked_order_lookup_is_not_projected():
    doc = {"_id": "x", "shopify_order_id": "1", "status": "DELIVERED", "payment_status": "PAID"}

    class _Coll:
        def find_one(self, filter_, projection=None):
            assert filter_ == {"shopify_order_id": "1"}
            return {k: doc[k] for k in projection if k in doc} if projection else dict(doc)

    class _DB:
        def get_collection(self, name):
            assert name == "orders"
            return _Coll()

    assert np._booked_order(_DB(), "1")["status"] == "DELIVERED"
    assert np._booked_order(_DB(), "") is None


# ---------------------------------------------------------------------------
# Rule: a HISTORICAL import is left alone (every handler skips it; comparing it
# would only report a false failure every hour)
# ---------------------------------------------------------------------------


def test_historical_import_orders_are_left_alone(swept):
    _book(swept, 30014)
    swept["orders"].update_one({"shopify_order_id": "30014"}, {"$set": {"historical": True}})
    before = _snap(_doc(swept, 30014))

    swept["state"]["orders"] = [_pulled(30014, cancelled_at=CANCELLED_AT)]
    p = swept["run"]().payload

    assert p["already_in_ims"] == 1
    assert p["status_synced"] == [] and p["status_failed"] == []
    assert swept["seen"] == []
    assert _snap(_doc(swept, 30014)) == before
