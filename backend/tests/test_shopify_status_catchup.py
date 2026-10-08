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
reverted (table in the PR body). Even the "whether" is the mapper's own: the money
trigger is what _recompute_money WOULD write, the stale skip is
_shopify_payload_stale, the terminal report is the handlers' own
terminal_withheld verdict (every leg decides through the ONE transition table,
online_order_status) -- each pinned on the webhook drain in the same test that
pins the sweep.
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
    # System tasks (a blocked restock, a historical order's stock-in) land in
    # the same FakeDB, assigned from its users.
    from database.repositories.task_repository import TaskRepository
    from database.repositories.user_repository import UserRepository

    monkeypatch.setattr(deps, "get_task_repository",
                        lambda: TaskRepository(db.get_collection("tasks")), raising=False)
    monkeypatch.setattr(deps, "get_user_repository",
                        lambda: UserRepository(db.get_collection("users")), raising=False)
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


@pytest.mark.parametrize(
    "door_stamps",
    [
        # The door's claim + its release stamp (a completed cancel).
        {"cancelled_by": "staff-1", "cancel_stock_released": ["stk-1"]},
        # The claim only: the release stamp was lost (base_repository.update
        # swallows the blip) -- the door still ran and owns the retry.
        {"cancelled_by": "staff-1"},
    ],
)
def test_a_refund_on_an_order_the_ims_cancel_door_released_restocks_nothing(swept, monkeypatch, door_stamps):
    """Staff cancelled at the counter (the IMS cancel door put the unit back and
    stamped the order), Shopify cancels later with a restocking Refund. Even
    under AUTO the handler queues it for the accountant (no ledger, no returns
    doc) with NO restock proposed -- so the accountant's confirm posts the
    credit without minting a phantom second unit."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    doc = _book(swept, 30032)
    _claim_unit(swept, doc)
    swept["orders"].update_one(
        {"shopify_order_id": "30032"},
        {"$set": {"status": "CANCELLED", "cancelled_at": "2026-09-05T10:00:00Z", **door_stamps}},
    )
    swept["stock_repo"].units[0].update(status="AVAILABLE", order_id=None)

    swept["state"]["orders"] = [
        _pulled(30032, cancelled_at=CANCELLED_AT, financial_status="refunded",
                refunds=[_refund(740030, 30032)])
    ]
    p = swept["run"]().payload
    assert p["status_failed"] == [] and swept["refund_calls"] == ["740030"]

    assert swept["returns"].count_documents({}) == 0 and swept["ledger"].count_documents({}) == 0
    review = swept["review"].find_one({"shopify_refund_id": "740030"})
    assert review["status"] == "PENDING"
    assert [r["restock"] for r in review["proposed_restock"]] == [False]

    shopify_refund.post_from_review(swept["db"], review)
    assert [(u["stock_id"], u["status"]) for u in swept["stock_repo"].units] == [("stk-1", "AVAILABLE")]


def test_a_queued_refund_confirmed_after_the_ims_cancel_door_ran_restocks_nothing(swept):
    """The door can run AFTER the refund was queued: Shopify cancels (the unit is
    still SOLD, the review row proposes its restock), then staff re-POST the IMS
    cancel -- its retry door puts the unit back and stamps only
    cancel_stock_released. The accountant's later confirm must not restock:
    the SOLD unit is gone, so the restock would mint a phantom. The credit
    note still posts."""
    doc = _book(swept, 30033)
    _claim_unit(swept, doc)
    swept["state"]["orders"] = [
        _pulled(30033, cancelled_at=CANCELLED_AT, financial_status="refunded",
                refunds=[_refund(740033, 30033)])
    ]
    assert swept["run"]().payload["status_synced"] == ["30033"]
    review = swept["review"].find_one({"shopify_refund_id": "740033"})
    assert review["status"] == "PENDING"
    assert [r["restock"] for r in review["proposed_restock"]] == [True]

    swept["orders"].update_one(
        {"shopify_order_id": "30033"}, {"$set": {"cancel_stock_released": ["stk-1"]}}
    )
    swept["stock_repo"].units[0].update(status="AVAILABLE", order_id=None)

    res = shopify_refund.post_from_review(swept["db"], review)
    assert res["status"] == "credited" and swept["ledger"].count_documents({}) == 1
    assert [(u["stock_id"], u["status"]) for u in swept["stock_repo"].units] == [("stk-1", "AVAILABLE")]


# ---------------------------------------------------------------------------
# Rule: a Shopify refund restocks no unit the COUNTER return door already took
# back -- capped by that door's own returnable-qty answer
# (returns._already_returned_qty), asked when the refund is queued AND when it
# is posted; an overlap is queued for the accountant even under AUTO
# ---------------------------------------------------------------------------


def _delivered_with_units(swept, oid, qty=1, **over):
    line = {**_pulled(oid)["line_items"][0], "quantity": qty}
    doc = _book(swept, oid, line_items=[line], **over)
    _claim_unit(swept, doc)
    for n in range(2, qty + 1):
        swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": f"stk-{n}"})
    swept["orders"].update_one(
        {"shopify_order_id": str(oid)},
        {"$set": {"status": "DELIVERED", "fulfillment_status": "FULFILLED"}},
    )
    return line


def _counter_return(swept, oid, qty=1):
    """What the counter return door (returns.create_return) leaves behind for
    `qty` unit(s) of the frame line: its returns doc (the priced lines, keyed
    by the order line), the line's returned_qty claim, and the unit(s) back on
    the shelf through the SAME _restock_good_items."""
    from api.routers import returns as returns_router

    doc = _doc(swept, oid)
    item = doc["items"][0]
    line = returns_router.ReturnLine(
        order_item_id=item["item_id"], product_id="IMS-P-1", return_qty=qty, unit_price=0.0
    )
    swept["returns"].insert_one({
        "return_id": f"RET-C-{oid}", "order_id": doc["order_id"], "return_type": "RETURN",
        "items": returns_router._priced_return_lines([line], doc), "status": "COMPLETED",
    })
    swept["orders"].update_one(
        {"shopify_order_id": str(oid)}, {"$set": {"items": [{**item, "returned_qty": qty}]}}
    )
    returns_router._restock_good_items(
        [line], doc["store_id"], f"RET-C-{oid}", order_id=doc["order_id"], order=doc
    )


def _units(swept):
    return [(u["stock_id"], u["status"]) for u in swept["stock_repo"].units]


@pytest.mark.parametrize("auto", [False, True])
def test_a_refund_of_a_unit_the_counter_already_took_back_restocks_nothing(swept, monkeypatch, auto):
    """Staff took the return at the counter (the door booked it and put the
    unit back), then Shopify refunds the same unit. Nothing is left to
    restock, so no phantom -- and even under AUTO the credit is queued for the
    accountant, saying why: the counter may already have refunded it (no
    second GST reversal without a human)."""
    if auto:
        monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _delivered_with_units(swept, 41005)
    _counter_return(swept, 41005)
    assert _units(swept) == [("stk-1", "AVAILABLE")]

    swept["state"]["orders"] = [_pulled(
        41005, financial_status="refunded", fulfillment_status="fulfilled",
        refunds=[_refund(741005, 41005, restock_type="return")],
    )]
    p = swept["run"]().payload

    assert p["status_failed"] == [] and swept["refund_calls"] == ["741005"]
    assert swept["returns"].count_documents({}) == 1 and swept["ledger"].count_documents({}) == 0
    review = swept["review"].find_one({"shopify_refund_id": "741005"})
    assert review["status"] == "PENDING" and "already booked a return" in review["note"]
    assert [r["restock"] for r in review["proposed_restock"]] == [False]
    res = shopify_refund.post_from_review(swept["db"], review)
    assert res["status"] == "credited" and _units(swept) == [("stk-1", "AVAILABLE")]


def test_a_queued_refund_confirmed_after_a_counter_return_restocks_nothing(swept):
    """The counter can take the return AFTER the refund was queued (the review
    row still proposes the restock): the confirm asks the returnable-qty
    answer again and restocks nothing -- the SOLD unit is gone."""
    _delivered_with_units(swept, 41006)
    swept["state"]["orders"] = [_pulled(
        41006, financial_status="partially_refunded", fulfillment_status="fulfilled",
        refunds=[_refund(741006, 41006, restock_type="return")],
    )]
    swept["run"]()
    review = swept["review"].find_one({"shopify_refund_id": "741006"})
    assert [r["restock"] for r in review["proposed_restock"]] == [True]

    _counter_return(swept, 41006)
    res = shopify_refund.post_from_review(swept["db"], review)

    assert res["status"] == "credited" and _units(swept) == [("stk-1", "AVAILABLE")]


def test_a_refund_over_a_partial_counter_return_restocks_only_the_rest(swept):
    """Two frames on one line, one returned at the counter, both refunded on
    Shopify: the refund restocks the ONE still out with the buyer (the line
    splits), and the credit note still covers both units."""
    line = _delivered_with_units(swept, 41007, qty=2)
    _counter_return(swept, 41007)
    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "SOLD")]
    refund = _refund(741007, 41007, restock_type="return", amount="1998.00")
    refund["refund_line_items"][0].update(quantity=2, subtotal=1902.86, total_tax=95.14)

    swept["state"]["orders"] = [_pulled(
        41007, line_items=[line], financial_status="refunded", fulfillment_status="fulfilled",
        refunds=[refund],
    )]
    swept["run"]()

    review = swept["review"].find_one({"shopify_refund_id": "741007"})
    assert review["status"] == "PENDING" and review["gross_refund"] == 1998.0
    assert [(r["return_qty"], r["restock"]) for r in review["proposed_restock"]] == [(1, True), (1, False)]
    shopify_refund.post_from_review(swept["db"], review)
    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "AVAILABLE")]


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


# A Rs 500 CASH advance taken at the counter (routers/orders/payments.py) with
# the order moved on to READY -- money and lifecycle state Shopify never sees.
_ADVANCE = {
    "payment_status": "PARTIAL",
    "amount_paid": 500.0,
    "balance_due": 499.0,
    "bill_type": "ADVANCE",
    "payments": [{"method": "CASH", "amount": 500.0}],
    "status": "READY",
}


@pytest.mark.parametrize(
    "ims_money, shopify_financial",
    [
        # COD collected at the counter: PAID in IMS, Shopify still pending.
        ({"payment_status": "PAID", "amount_paid": 999.0, "balance_due": 0.0}, "pending"),
        (_ADVANCE, "pending"),
        (_ADVANCE, "authorized"),
        # Khata / store credit: an IMS-only money state Shopify cannot derive.
        ({"payment_status": "CREDIT", "bill_type": "CREDIT"}, "partially_paid"),
    ],
)
def test_ims_money_state_is_never_knocked_back_by_a_lesser_shopify_state(
    swept, ims_money, shopify_financial
):
    """Shopify pending / authorized / partially_paid says only "the gateway has
    not collected it all" -- the till may have. The sweep must not fire the
    mapper over it: no webhook would have (Shopify did not change), and the
    mapper would write UNPAID / bill_type PENDING / status CONFIRMED over the
    advance every hour."""
    _book(swept, 30016, financial_status="pending")
    swept["orders"].update_one({"shopify_order_id": "30016"}, {"$set": ims_money})
    before = _snap(_doc(swept, 30016))

    body = _pulled(30016, financial_status=shopify_financial, total_outstanding="999.00")
    swept["state"]["orders"] = [body]
    p = swept["run"]().payload

    assert p["status_synced"] == [] and p["status_failed"] == [] and swept["seen"] == []
    assert _snap(_doc(swept, 30016)) == before

    # The SAME body through the REAL webhook drain (any Shopify edit -- a note,
    # a tag -- delivers orders/updated): the money is withheld THERE too, by the
    # one rule in the mapper. Before, the drain wrote payment_status UNPAID +
    # bill_type PENDING over amount_paid 999 -- an incoherent doc.
    res = swept["real_map"](copy.deepcopy(body), swept["db"], webhook_id="real-edit-1", topic="orders/updated")
    assert res["status"] == "duplicate" and res["status_synced"] is True
    after = _doc(swept, 30016)
    for k in ("payment_status", "amount_paid", "balance_due", "bill_type", "payments"):
        assert after.get(k) == before.get(k), k


def test_shopify_partially_paid_over_nothing_collected_lands(swept):
    """Shopify ADVANCED (a partial payment landed there) and IMS holds nothing:
    the mapper's money leg records Shopify's collected amount, once."""
    doc = _book(swept, 30025, financial_status="pending")
    assert doc["payment_status"] == "UNPAID" and doc["amount_paid"] == 0.0

    swept["state"]["orders"] = [
        _pulled(30025, financial_status="partially_paid", total_outstanding="499.00")
    ]
    assert swept["run"]().payload["status_synced"] == ["30025"]

    doc = _doc(swept, 30025)
    assert doc["payment_status"] == "PARTIAL"
    assert doc["amount_paid"] == 500.0 and doc["balance_due"] == 499.0
    row = swept["inbox"].find_one({"_id": f"pull:30025:{UPDATED}"})
    assert row["headers"]["x-shopify-topic"] == "orders/updated"
    swept["seen"].clear()
    assert swept["run"]().payload["status_synced"] == [] and swept["seen"] == []


def test_a_second_partial_payment_on_shopify_lands(swept):
    """partially_paid -> partially_paid with a smaller total_outstanding: the
    label never moves, the money does. The sweep asks the mapper's own money
    function what it WOULD write, so the new gateway money lands -- once."""
    _book(swept, 30028, financial_status="pending")
    swept["state"]["orders"] = [
        _pulled(30028, financial_status="partially_paid", total_outstanding="499.00")
    ]
    assert swept["run"]().payload["status_synced"] == ["30028"]
    assert _doc(swept, 30028)["amount_paid"] == 500.0

    later = "2026-09-06T02:00:00Z"
    swept["state"]["orders"] = [
        _pulled(30028, financial_status="partially_paid", total_outstanding="199.00", updated_at=later)
    ]
    assert swept["run"]().payload["status_synced"] == ["30028"]
    doc = _doc(swept, 30028)
    assert doc["payment_status"] == "PARTIAL"
    assert doc["amount_paid"] == 800.0 and doc["balance_due"] == 199.0
    assert [p["amount"] for p in doc["payments"]] == [800.0], "the ONE gateway row, re-amounted"
    assert swept["inbox"].find_one({"_id": f"pull:30028:{later}"})["headers"]["x-shopify-topic"] == "orders/updated"

    swept["seen"].clear()
    assert swept["run"]().payload["status_synced"] == [] and swept["seen"] == []
    assert _doc(swept, 30028)["amount_paid"] == 800.0


# ---------------------------------------------------------------------------
# Rule: a fulfilment IMS never saw lands EXACTLY what the drain lands for the
# pair of deliveries Shopify makes -- fulfillments/create (the reconcile: AWB,
# tracking, SHIPPED) then orders/fulfilled (the mapper: fulfilled -> SHIPPED,
# owner ruling 2026-09-28 on BOTH paths; DELIVERED is the courier's)
# ---------------------------------------------------------------------------


def _fulfilled_body(oid, fid):
    return _pulled(oid, fulfillment_status="fulfilled", fulfillments=[_fulfilment(oid, fid)])


def _shipping_snap(doc):
    return {
        k: doc.get(k)
        for k in ("status", "fulfillment_status", "shopify_fulfillment_id", "awb",
                  "tracking_number", "tracking_company", "tracking_url", "payment_status")
    }


def test_fulfilment_ims_missed_lands_like_the_drain_once(swept):
    _book(swept, 30004)
    _book(swept, 30027)

    swept["state"]["orders"] = [_fulfilled_body(30004, 88804)]
    assert swept["run"]().payload["status_synced"] == ["30004"]
    # Both deliveries were synthesised, in Shopify's order, and recorded.
    assert swept["seen"] == ["30004"]
    row = swept["inbox"].find_one({"_id": f"pull:30004:{UPDATED}:fulfillments/update:88804"})
    assert row["headers"]["x-shopify-topic"] == "fulfillments/update"
    assert row["payload"]["id"] == 88804 and row["source"] == "shopify_pull"
    assert swept["inbox"].find_one({"_id": f"pull:30004:{UPDATED}"})["headers"]["x-shopify-topic"] == "orders/fulfilled"

    # The SAME body through the REAL drain: fulfillments/create then orders/fulfilled.
    body = _fulfilled_body(30027, 88827)
    shopify_fulfillment.reconcile_fulfillment(swept["db"], body["fulfillments"][0], topic="fulfillments/create")
    swept["real_map"](body, swept["db"], webhook_id="real-ful-1", topic="orders/fulfilled")

    doc = _doc(swept, 30004)
    assert doc["status"] == "SHIPPED" and doc["fulfillment_status"] == "FULFILLED"
    assert "delivered_at" not in doc
    assert doc["awb"] == "AWB30004" and doc["tracking_company"] == "Delhivery"
    assert doc["shopify_fulfillment_id"] == "88804"
    drain = _shipping_snap(_doc(swept, 30027))
    assert _shipping_snap(doc) == {**drain, "shopify_fulfillment_id": "88804", "awb": "AWB30004",
                                   "tracking_number": "AWB30004", "tracking_url": "https://track/AWB30004"}

    before = _snap(doc)
    swept["seen"].clear()
    assert swept["run"]().payload["status_synced"] == [] and swept["seen"] == []
    assert _snap(_doc(swept, 30004)) == before


def test_an_ims_pushed_fulfilment_stamped_as_a_gid_is_the_same_fulfilment(swept):
    """IMS shipped and pushed the fulfilment itself (shopify_fulfillment_push
    stamps the GraphQL gid); orders.json carries the bare numeric id. Nothing
    moved: no call, no re-stamp, no status_synced noise."""
    _book(swept, 30030)
    swept["orders"].update_one(
        {"shopify_order_id": "30030"},
        {"$set": {
            "status": "SHIPPED", "fulfillment_status": "FULFILLED",
            "shopify_fulfillment_id": "gid://shopify/Fulfillment/5008",
            "awb": "AWB30030", "tracking_number": "AWB30030",
        }},
    )
    before = _snap(_doc(swept, 30030))

    swept["state"]["orders"] = [_fulfilled_body(30030, 5008)]
    p = swept["run"]().payload

    assert p["status_synced"] == [] and p["status_failed"] == [] and swept["seen"] == []
    assert _snap(_doc(swept, 30030)) == before


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


def _spy_reconcile(swept):
    calls = []
    real = shopify_fulfillment.reconcile_fulfillment

    def spy(db, payload, **kw):
        calls.append(payload.get("id"))
        return real(db, payload, **kw)

    swept["mp"].setattr(shopify_fulfillment, "reconcile_fulfillment", spy)
    return calls


@pytest.mark.parametrize("oid, shopify_ful, f1_shipment", [
    (30108, "fulfilled", "delivered"),  # D: one order, two parcels
    (30109, "partial", "in_transit"),   # D2: split shipment
])
def test_a_newest_fulfilment_without_shipment_status_is_reconciled_once(swept, oid, shopify_ful, f1_shipment):
    """The reconcile never clears shipment_status with an empty one (it writes
    only the non-empty tracking fields), so the sweep compares only those:
    the older parcel's status the doc keeps is no difference, and the handler
    is fed once -- not every hour for the 48h the order stays in the fetch
    (D2 used to flip CONFIRMED/SHIPPED and PARTIAL/FULFILLED hourly)."""
    _book(swept, oid)
    f1 = _fulfilment(oid, 1, shipment_status=f1_shipment, updated_at="2026-09-06T00:40:00Z")
    shopify_fulfillment.reconcile_fulfillment(swept["db"], f1, topic="fulfillments/update")
    calls = _spy_reconcile(swept)
    f2 = _fulfilment(oid, 2, tracking_number="AWB-2", updated_at="2026-09-06T00:59:00Z")
    swept["state"]["orders"] = [_pulled(oid, fulfillment_status=shopify_ful, fulfillments=[f1, f2])]

    assert swept["run"]().payload["status_synced"] == [str(oid)]
    settled = _snap(_doc(swept, oid))
    assert (settled["shopify_fulfillment_id"], settled["awb"]) == ("2", "AWB-2")
    assert settled["shipment_status"] == f1_shipment, "an empty field never clears the older one"
    for _ in range(3):
        p = swept["run"]().payload
        assert p["status_synced"] == [] and p["status_failed"] == []
        assert _snap(_doc(swept, oid)) == settled
    assert calls == [2]


# ---------------------------------------------------------------------------
# Rule: a finished IMS status is never moved by a Shopify fact (never knocked
# back to CONFIRMED, never DELIVERED over a cancelled order; a DELIVERED order
# Shopify cancels / refunds stays DELIVERED with one task) -- ONE transition
# table (online_order_status) that the webhook drain enforces and the sweep
# reports from each handler's own verdict (terminal_withheld on the mapper and
# the reconcile); the payment / fulfilment facts still land. A body that
# states no lifecycle fact (restocked, partially refunded) is no report.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ims_state, shopify_body, landed, reported",
    [
        # Delivered at the counter; Shopify later shows the fulfilment restocked
        # -> no lifecycle fact (the table writes nothing); the fact lands.
        (
            {"status": "DELIVERED", "fulfillment_status": "FULFILLED"},
            {"fulfillment_status": "restocked"},
            {"fulfillment_status": "RESTOCKED"},
            False,
        ),
        # Cancelled by staff in IMS; Shopify (still open there) partially refunds
        # -> no lifecycle fact; the money lands.
        (
            {"status": "CANCELLED"},
            {"financial_status": "partially_refunded"},
            {"payment_status": "PARTIAL_REFUND"},
            False,
        ),
        # Cancelled by staff in IMS (units released); Shopify fulfils it later
        # -> SHIP on a finished order: kept, and reported. The tracking lands.
        (
            {"status": "CANCELLED", "cancelled_at": "2026-09-05T10:00:00Z"},
            {"fulfillment_status": "fulfilled", "fulfillments": [_fulfilment(30005, 41)]},
            {"fulfillment_status": "FULFILLED", "awb": "AWB30005"},
            True,
        ),
    ],
)
def test_terminal_ims_status_is_never_knocked_back(swept, ims_state, shopify_body, landed, reported):
    _book(swept, 30005)
    swept["orders"].update_one({"shopify_order_id": "30005"}, {"$set": ims_state})

    swept["state"]["orders"] = [_pulled(30005, **shopify_body)]
    res = swept["run"]()

    p = res.payload
    assert p["status_synced"] == ["30005"]
    assert p["status_skipped_terminal"] == (["30005"] if reported else [])
    assert p["status_failed"] == []
    assert ("terminal-skipped 1" in res.notes) is reported
    doc = _doc(swept, 30005)
    assert doc["status"] == ims_state["status"]
    for field, value in landed.items():
        assert doc[field] == value
    # Second sweep: the facts agree now -> no call, nothing reported.
    swept["seen"].clear()
    p2 = swept["run"]().payload
    assert p2["status_synced"] == [] and p2["status_skipped_terminal"] == []
    assert swept["seen"] == []


@pytest.mark.parametrize(
    "ims_status, ims_ful, shopify_ful, topic, landed",
    [
        ("DELIVERED", "FULFILLED", "restocked", "orders/updated", "RESTOCKED"),
        ("CANCELLED", "UNFULFILLED", "fulfilled", "orders/fulfilled", "FULFILLED"),
    ],
)
def test_the_webhook_drain_shares_the_terminal_rule(swept, ims_status, ims_ful, shopify_ful, topic, landed):
    """The same body through the REAL webhook path (map_shopify_order as
    nexus._dispatch_shopify_order calls it) keeps the terminal status too --
    the rule lives in the mapper, not in the sweep."""
    _book(swept, 30026)
    swept["orders"].update_one(
        {"shopify_order_id": "30026"},
        {"$set": {"status": ims_status, "fulfillment_status": ims_ful}},
    )

    res = swept["real_map"](
        _pulled(30026, fulfillment_status=shopify_ful), swept["db"],
        webhook_id="real-upd-1", topic=topic,
    )

    assert res["status"] == "duplicate" and res["status_synced"] is True
    doc = _doc(swept, 30026)
    assert doc["status"] == ims_status and doc["fulfillment_status"] == landed


def _refunded_after_delivery(oid):
    return _pulled(
        oid,
        financial_status="refunded",
        fulfillment_status="fulfilled",
        refunds=[_refund(700000 + oid, oid, restock_type="return")],
    )


def test_a_refund_after_delivery_stays_delivered_with_one_task_on_both_paths(swept):
    """The most common refund -- after delivery. Owner ruling 2026-09-28: an
    order IMS holds DELIVERED stays DELIVERED (the customer has the goods) and
    ONE task asks a person refund / return / Shopify mistake; the refund money
    lands and the refund itself waits in the review queue. The sweep and the
    webhook drain land the same doc; the sweep reports it once, not hourly."""
    for oid in (30022, 30023):
        _book(swept, oid)
        swept["orders"].update_one(
            {"shopify_order_id": str(oid)},
            {"$set": {"status": "DELIVERED", "fulfillment_status": "FULFILLED"}},
        )

    swept["state"]["orders"] = [_refunded_after_delivery(30022)]
    p = swept["run"]().payload
    assert p["status_synced"] == ["30022"]
    assert p["status_skipped_terminal"] == ["30022"] and p["status_failed"] == []
    drain = swept["real_map"](
        _refunded_after_delivery(30023), swept["db"], webhook_id="real-upd-2", topic="orders/updated"
    )
    assert drain["terminal_withheld"] is True

    tasks = swept["db"]["tasks"]
    for oid in (30022, 30023):
        doc = _doc(swept, oid)
        assert (doc["status"], doc["payment_status"]) == ("DELIVERED", "REFUNDED")
        assert tasks.count_documents(
            {"order_id": doc["order_id"], "task_type": "online_status_conflict"}) == 1
    assert swept["review"].find_one({"shopify_refund_id": "730022"})["status"] == "PENDING"
    p2 = swept["run"]().payload
    assert p2["status_synced"] == [] and p2["status_skipped_terminal"] == []


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


_SHIPPED_THEN_STAFF_CANCELLED = {
    "status": "CANCELLED", "cancelled_at": "2026-09-05T10:00:00Z", "cancelled_by": "staff-1",
    "fulfillment_status": "PARTIAL", "shopify_fulfillment_id": "49",
    "shipment_status": "in_transit", "awb": "AWB40011", "tracking_number": "AWB40011",
}


@pytest.mark.parametrize("ims_state, shopify_body, landed", [
    # A partial shipment landed SHIPPED, staff then cancelled at the counter
    # (cancel.py blocks only DELIVERED): Shopify says the parcel was delivered.
    (
        _SHIPPED_THEN_STAFF_CANCELLED,
        {"fulfillment_status": "partial", "fulfillments": [
            _fulfilment(40011, 49, shipment_status="delivered", tracking_number="AWB40011")]},
        {"shipment_status": "delivered"},
    ),
    (
        {"status": "CANCELLED", "fulfillment_status": "FULFILLED", "shopify_fulfillment_id": "45",
         "shipment_status": "in_transit", "awb": "AWB40011", "tracking_number": "AWB40011"},
        {"fulfillment_status": "fulfilled", "fulfillments": [_fulfilment(40011, 45, shipment_status="delivered")]},
        {"shipment_status": "delivered"},
    ),
    (
        {"status": "CANCELLED"},
        {"fulfillment_status": None, "fulfillments": [_fulfilment(40011, 46, status="open")]},
        {"shopify_fulfillment_id": "46", "awb": "AWB40011"},
    ),
])
def test_a_terminal_status_the_fulfilment_leg_held_back_is_reported(swept, ims_state, shopify_body, landed):
    """The fulfilment reconcile decides with the mapper's ONE terminal rule and
    says when it held the SHIPPED / DELIVERED flip back: the operator is told
    Shopify shipped / delivered an order IMS holds cancelled or delivered."""
    _book(swept, 40011)
    swept["orders"].update_one({"shopify_order_id": "40011"}, {"$set": ims_state})

    swept["state"]["orders"] = [_pulled(40011, **shopify_body)]
    res = swept["run"]()

    p = res.payload
    assert p["status_synced"] == ["40011"] and p["status_skipped_terminal"] == ["40011"]
    assert "terminal-skipped 1" in res.notes
    doc = _doc(swept, 40011)
    assert doc["status"] == ims_state["status"]
    for field, value in landed.items():
        assert doc[field] == value
    # The drain's answer for the same fulfilment is the same rule's.
    again = shopify_fulfillment.reconcile_fulfillment(swept["db"], shopify_body["fulfillments"][0])
    assert again["terminal_withheld"] is True and _doc(swept, 40011)["status"] == ims_state["status"]


def test_a_shipped_fulfilment_on_a_delivered_order_is_no_disagreement(swept):
    """Ruling 1: a fulfilment states SHIP, which DELIVERED is already past (a
    counter-delivered pickup order Shopify then fulfils). Its tracking lands,
    the status stays DELIVERED, and nothing is reported -- sweep and drain."""
    _book(swept, 40012)
    swept["orders"].update_one({"shopify_order_id": "40012"},
                               {"$set": {"status": "DELIVERED", "fulfillment_status": "FULFILLED"}})
    f = _fulfilment(40012, 47, shipment_status="in_transit")

    swept["state"]["orders"] = [_pulled(40012, fulfillment_status="fulfilled", fulfillments=[f])]
    p = swept["run"]().payload
    assert p["status_synced"] == ["40012"] and p["status_skipped_terminal"] == []
    doc = _doc(swept, 40012)
    assert (doc["status"], doc["shopify_fulfillment_id"], doc["shipment_status"]) == (
        "DELIVERED", "47", "in_transit")
    again = shopify_fulfillment.reconcile_fulfillment(swept["db"], f)
    assert again["terminal_withheld"] is False and _doc(swept, 40012)["status"] == "DELIVERED"


def test_a_delivered_order_and_a_newer_delivered_fulfilment_is_no_terminal_hold(swept):
    """The fulfilment leg asks the mapper's ONE rule -- 'would it CHANGE a
    terminal status' -- not 'is the order terminal': a newer delivered parcel
    on an order both sides hold DELIVERED lands its tracking and reports
    nothing held back. Parcel 70 was reconciled before the clocks (no
    watermark; written at 00:30): 71 is the newer fulfilment by Shopify's
    increasing ids, and its body (01:00) is newer than that write."""
    _book(swept, 53004)
    swept["orders"].update_one({"shopify_order_id": "53004"}, {"$set": {
        "status": "DELIVERED", "fulfillment_status": "FULFILLED", "shopify_fulfillment_id": "70",
        "awb": "AWB-70", "tracking_number": "AWB-70", "shipment_status": "in_transit",
        "updated_at": "2026-09-06T00:30:00+00:00",
    }})
    swept["state"]["orders"] = [_pulled(53004, fulfillment_status="fulfilled", fulfillments=[
        _fulfilment(53004, 71, shipment_status="delivered")])]

    p = swept["run"]().payload

    assert p["status_synced"] == ["53004"] and p["status_skipped_terminal"] == []
    doc = _doc(swept, 53004)
    assert (doc["status"], doc["shopify_fulfillment_id"], doc["shipment_status"]) == (
        "DELIVERED", "71", "delivered")


def test_the_report_is_the_mappers_own_verdict_after_the_fulfilment_leg(swept):
    """The report is what the handler DID, not the rule re-asked of the doc as
    it stood before the sweep: the fulfilment leg lands DELIVERED (a delivered
    parcel -- the courier's fact), then the mapper sees Shopify's 'partially
    fulfilled', which states no lifecycle fact: nothing moves and nothing is
    reported. The drain's mapper returns the same verdict for the same body."""
    _book(swept, 53001)
    body = _pulled(53001, fulfillment_status="partial", fulfillments=[
        _fulfilment(53001, 61, shipment_status="delivered", tracking_number="AWB53001")])
    swept["state"]["orders"] = [body]

    res = swept["run"]()

    p = res.payload
    assert p["status_synced"] == ["53001"] and p["status_skipped_terminal"] == []
    doc = _doc(swept, 53001)
    assert (doc["status"], doc["fulfillment_status"]) == ("DELIVERED", "PARTIAL")
    assert doc["delivered_at"] and doc["status_history"][-1]["changed_by"] == "system:SHOPIFY_FULFILL"
    drain = swept["real_map"](body, swept["db"], webhook_id="real-upd-53001", topic="orders/updated")
    assert drain["status_synced"] is True and drain["terminal_withheld"] is False
    assert _doc(swept, 53001)["status"] == "DELIVERED"


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


def test_a_refund_with_no_ims_line_is_settled_not_churned(swept):
    """A shipping-only / goodwill refund (refund_line_items empty, the money in
    transactions[] + order_adjustments[]) is a routine Shopify refund. The
    refund handler answers skipped:no_mappable_refund_lines -- nothing for IMS
    to credit -- so the sweep settles it: not a failure (which would mask the
    order's real syncs), not re-raised hourly; the order's own facts land."""
    _book(swept, 30024)
    goodwill = {
        "id": 700424,
        "order_id": 30024,
        "restock": False,
        "created_at": UPDATED,
        "refund_line_items": [],
        "order_adjustments": [{"kind": "shipping_refund", "amount": "-99.00"}],
        "transactions": [{"kind": "refund", "status": "success", "amount": "99.00"}],
    }
    swept["state"]["orders"] = [
        _pulled(30024, financial_status="partially_refunded", refunds=[goodwill])
    ]

    for _ in range(2):
        p = swept["run"]().payload
        assert p["status_failed"] == [] and p["failed_reasons"] == {}

    assert _doc(swept, 30024)["payment_status"] == "PARTIAL_REFUND"
    row = swept["inbox"].find_one({"_id": f"pull:30024:{UPDATED}:refunds/create:700424"})
    assert row["handler_error"] is None
    # The handler writes no row for this verdict, so the pre-filter asks it
    # again each hour (a read-only call); a handler-side review row for
    # lines-less refunds is the upgrade path.
    assert swept["refund_calls"] == ["700424", "700424"]
    assert swept["review"].count_documents({}) == 0


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

    real_fulfilments = np._fulfilments

    def exploding_fulfilments(order):
        if str(order.get("id")) == "30019":
            raise RuntimeError("comparison exploded")  # the sweep's own code, not a handler
        return real_fulfilments(order)

    swept["mp"].setattr(shopify_fulfillment, "reconcile_fulfillment", exploding_reconcile)
    swept["mp"].setattr(online_order_mapper, "map_shopify_order", exploding_map)
    swept["mp"].setattr(np, "_fulfilments", exploding_fulfilments)
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
    # Where the operator sees it: the run payload (above) and the remap door,
    # which replays the sweep's orders/* row for the booked order. NOT the
    # FAILED queue -- that lists unbooked orders only, by construction.
    payload, webhook_id, topic = oso._load_last_shopify_payload(swept["db"], "30020")
    assert (payload["id"], webhook_id, topic) == (30020, f"pull:30020:{UPDATED}", "orders/cancelled")
    assert oso._unbooked_webhook_rows(swept["db"], search=None) == []


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
# Rule: a body that lost the race with a webhook (older than the last applied
# updated_at) is the mapper's own stale skip on both paths -- a benign race
# the next hour re-reads, never a failure
# ---------------------------------------------------------------------------


def test_a_body_older_than_the_last_applied_webhook_is_not_a_failure(swept):
    _book(swept, 30029)
    newer = "2026-09-06T02:00:00Z"  # a real orders/updated landed after the fetch
    swept["real_map"](_pulled(30029, updated_at=newer), swept["db"], webhook_id="real-upd-9", topic="orders/updated")
    before = _snap(_doc(swept, 30029))
    swept["seen"].clear()

    stale = _pulled(30029, cancelled_at=CANCELLED_AT)  # updated_at=UPDATED, 01:00 < 02:00
    swept["state"]["orders"] = [stale]
    p = swept["run"]().payload

    assert p["status_failed"] == [] and p["failed_reasons"] == {} and p["status_synced"] == []
    assert swept["seen"] == [], "the mapper was not even asked"
    assert _snap(_doc(swept, 30029)) == before
    # The same predicate is the drain's guard: the stale body through the real
    # webhook path is skipped whole, too.
    res = swept["real_map"](copy.deepcopy(stale), swept["db"], webhook_id="real-stale-1", topic="orders/cancelled")
    assert res["status"] == "duplicate" and res["status_synced"] is False
    assert _snap(_doc(swept, 30029)) == before


def test_a_stale_body_never_rewinds_the_fulfilment_a_newer_webhook_applied(swept):
    """The stale skip covers the body's fulfilments too: the reconcile keeps no
    watermark of its own, so the older pulled fulfilment would overwrite the
    tracking / shipment status a newer fulfillments/update already applied."""
    _book(swept, 30031)
    newer = "2026-09-06T03:00:00Z"
    f2 = _fulfilment(30031, 2, tracking_number="AWB-NEW", shipment_status="delivered", updated_at=newer)
    shopify_fulfillment.reconcile_fulfillment(swept["db"], f2, topic="fulfillments/update")
    swept["real_map"](
        _pulled(30031, updated_at=newer, fulfillment_status="fulfilled", fulfillments=[f2]),
        swept["db"], webhook_id="real-upd-31", topic="orders/updated",
    )
    before = _snap(_doc(swept, 30031))
    assert (before["shopify_fulfillment_id"], before["awb"]) == ("2", "AWB-NEW")
    swept["seen"].clear()

    f1 = _fulfilment(30031, 1, tracking_number="AWB-OLD", shipment_status="in_transit")
    swept["state"]["orders"] = [_pulled(30031, fulfillment_status="fulfilled", fulfillments=[f1])]
    p = swept["run"]().payload

    assert p["status_synced"] == [] and p["status_failed"] == [] and swept["seen"] == []
    assert _snap(_doc(swept, 30031)) == before


NEWER = "2026-09-06T03:00:00Z"


@pytest.mark.parametrize("variant", ["A", "A2", "B", "C"])
def test_a_body_older_than_the_last_fulfilment_webhook_never_rewinds_it(swept, variant):
    """A fulfillments/* webhook stamps no ORDER watermark, so the body's order
    clock cannot see it lost the race: the reconcile keeps its own (the
    fulfilment's updated_at) and the SAME stale rule reads it on both paths.
    The older body neither re-stamps an older fulfilment (A, A2 new id; B the
    same id's carrier event) nor fires orders/updated on a fulfillment_status
    it predates (C: the pre-fulfilment body rewound SHIPPED to CONFIRMED)."""
    oid = 30101
    _book(swept, oid)
    if variant in ("A2", "B"):  # an orders/updated at 00:30: an order watermark OLDER than the body
        swept["real_map"](_pulled(oid, updated_at="2026-09-06T00:30:00Z"), swept["db"],
                          webhook_id="real-030", topic="orders/updated")
    if variant in ("A", "A2"):
        newer_f = _fulfilment(oid, 2, tracking_number="AWB-NEW", shipment_status="delivered", updated_at=NEWER)
        body = _pulled(oid, fulfillment_status="fulfilled", fulfillments=[
            _fulfilment(oid, 1, tracking_number="AWB-OLD", shipment_status="in_transit")])
    elif variant == "B":
        newer_f = _fulfilment(oid, 1, tracking_number="AWB-B", shipment_status="delivered", updated_at=NEWER)
        body = _pulled(oid, fulfillment_status="fulfilled", fulfillments=[
            _fulfilment(oid, 1, tracking_number="AWB-B", shipment_status="in_transit")])
    else:
        newer_f = _fulfilment(oid, 1, tracking_number="AWB-C", updated_at=NEWER)
        body = _pulled(oid)  # 01:00, before the fulfilment: no fulfilments at all
    topic = "fulfillments/create" if variant == "C" else "fulfillments/update"
    assert shopify_fulfillment.reconcile_fulfillment(swept["db"], newer_f, topic=topic)["status"] == "reconciled"
    before = _snap(_doc(swept, oid))
    swept["seen"].clear()
    calls = _spy_reconcile(swept)

    swept["state"]["orders"] = [body]
    p = swept["run"]().payload

    assert p["status_synced"] == [] and p["status_failed"] == [] and p["failed_reasons"] == {}
    assert swept["seen"] == [] and calls == []
    assert _snap(_doc(swept, oid)) == before
    # The drain asks each fulfilment's OWN clock: the same fulfilment's older
    # state (B) is skipped whole; another parcel's (A, A2) states its own fact
    # but never takes the newer one's tracking over -- it only joins the live
    # parcels the courier legs track.
    for f in body.get("fulfillments") or []:
        res = shopify_fulfillment.reconcile_fulfillment(swept["db"], f)
        assert res.get("reason") == ("stale_fulfillment" if variant == "B" else None)
    own = (shopify_fulfillment.FULFILLMENT_CLOCKS, shopify_fulfillment.PARCEL_AWBS)
    after = _snap(_doc(swept, oid))
    clocks, parcels = (after.pop(k) for k in own)
    assert after == {k: v for k, v in before.items() if k not in own}
    assert clocks["f1"], "each parcel keeps its own clock"
    if variant in ("A", "A2"):
        assert sorted(p["awb"] for p in parcels) == ["AWB-NEW", "AWB-OLD"]


# ---------------------------------------------------------------------------
# A split shipment's parcels move on their own clocks. Parcel 1 is delivered
# at 02:00, parcel 2 was cancelled at 03:00: parcel 1's "delivered" is a real
# fact however late it lands (a Shopify retry, or another worker processed
# parcel 2 first) -- never "stale" against parcel 2's clock -- and the hourly
# sweep feeds every parcel that moved, not only the newest. Ruling 1: the
# courier's delivered makes the order DELIVERED.
# ---------------------------------------------------------------------------


def _parcel(oid, fid, at, **over):
    return _fulfilment(oid, fid, tracking_number=f"AWB-F{fid}", updated_at=f"2026-09-06T{at}:00Z", **over)


def _split_shipment(swept, oid):
    _book(swept, oid)
    for f in (_parcel(oid, 1, "01:10", shipment_status="in_transit"),
              _parcel(oid, 2, "01:20", shipment_status="in_transit"),
              _parcel(oid, 2, "03:00", status="cancelled")):
        assert shopify_fulfillment.reconcile_fulfillment(swept["db"], f)["status"] == "reconciled"
    assert _doc(swept, oid)["status"] == "SHIPPED"


def test_a_late_delivered_parcel_is_never_stale_against_another_parcel(swept):
    oid = 30120
    _split_shipment(swept, oid)
    delivered = _parcel(oid, 1, "02:00", shipment_status="delivered")

    res = shopify_fulfillment.reconcile_fulfillment(swept["db"], delivered)
    assert (res["status"], res["order_status"]) == ("reconciled", "DELIVERED")
    doc = _doc(swept, oid)
    assert doc["status"] == "DELIVERED"
    assert (doc["shopify_fulfillment_id"], doc["awb"]) == ("1", "AWB-F1"), (
        "the delivered parcel shows, never the cancelled one")
    # Parcel 1's OWN older state is still stale.
    older = _parcel(oid, 1, "01:30", shipment_status="in_transit")
    assert shopify_fulfillment.reconcile_fulfillment(swept["db"], older)["reason"] == "stale_fulfillment"


def test_the_sweep_feeds_every_parcel_that_moved(swept):
    oid = 30121
    _split_shipment(swept, oid)
    calls = _spy_reconcile(swept)
    swept["state"]["orders"] = [_pulled(oid, fulfillment_status="fulfilled", updated_at="2026-09-06T03:00:00Z",
                                        fulfillments=[_parcel(oid, 1, "02:00", shipment_status="delivered"),
                                                      _parcel(oid, 2, "03:00", status="cancelled")])]

    assert swept["run"]().payload["status_synced"] == [str(oid)]
    assert calls == [1], "parcel 1 moved; parcel 2 did not"
    assert _doc(swept, oid)["status"] == "DELIVERED"
    for _ in range(2):
        p = swept["run"]().payload
        assert p["status_synced"] == [] and p["status_failed"] == []
    assert calls == [1]


# ---------------------------------------------------------------------------
# Rule: an imported order's status legs are left alone (the mapper and the
# reconcile skip every import), but its refund leg is the REFUND HANDLER's
# call: our own shopify_order_history import books real revenue, so a NEW
# refund on it must still reach the accountant; a pre-IMS bvi_import is
# settled by the handler's own verdict, not reported as a failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "import_stamp, review_rows",
    [
        ({"historical": True, "import_source": "shopify_order_history", "status": "DELIVERED"}, 1),
        ({"historical": True, "source": "bvi_import", "status": "HISTORICAL"}, 0),
    ],
)
def test_a_new_refund_on_an_import_is_the_refund_handlers_call(swept, import_stamp, review_rows):
    _book(swept, 30031)
    swept["orders"].update_one({"shopify_order_id": "30031"}, {"$set": import_stamp})
    before = _snap(_doc(swept, 30031))

    swept["state"]["orders"] = [
        _pulled(
            30031,
            cancelled_at=CANCELLED_AT,
            financial_status="refunded",
            fulfillments=[_fulfilment(30031, 31)],
            refunds=[_refund(700331, 30031, restock_type="return")],
        )
    ]
    for _ in range(2):
        p = swept["run"]().payload
        assert p["status_failed"] == [] and p["failed_reasons"] == {}
        assert p["already_in_ims"] == 1

    assert swept["seen"] == [], "the mapper is never asked about an import"
    assert _snap(_doc(swept, 30031)) == before, "no status / fulfilment / money write"
    assert swept["refund_calls"][:1] == ["700331"]
    assert swept["review"].count_documents({"shopify_refund_id": "700331"}) == review_rows
    if review_rows:
        assert swept["refund_calls"] == ["700331"], "queued once; the 2nd sweep asks the pre-filter only"
        assert swept["review"].find_one({"shopify_refund_id": "700331"})["status"] == "PENDING"


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
