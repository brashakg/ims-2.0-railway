"""PR #1423 review, F-1423-G: one Shopify refund id gives ONE credit-note
ledger row across the order-history import and the live refund doors (the
webhook in AUTO mode, the accountant's Confirm).

The import's insert-once ledger `_id` only arbitrated import against import.
A live door that claimed the same refund after the import's "is it booked"
read posted its own credit note (ref = its return id), and the import's
RET-HIST stamp then read the live door's claim as its own: two GSTR-1 CDNR
reversals for one refund. The import now takes the SAME claim the live doors
take -- the returns doc under uniq_shopify_refund_id -- BEFORE it writes its
ledger row, and refuses to book when that index cannot be confirmed.

Every door here is the real code. The helpers take any database: mongomock
in these tests, native Mongo in test_native_* (the CI mongo service) and in
the local probe.
"""
import os
import threading
import uuid
from types import SimpleNamespace

import mongomock
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ.setdefault("ENVIRONMENT", "test")

pytestmark = pytest.mark.usefixtures("gst_numbers")

REFUND_AT = "2026-11-08T05:00:00+00:00"


def wire(monkeypatch, db, orders=None):
    """Point every door (the import's booker, the live refund door and the
    returns helpers both reuse) at `db`. `orders`: the orders collection the
    order-line claims write through (mongomock needs its positional shim)."""
    import api.dependencies as deps
    from api.routers import returns as rr

    conn = SimpleNamespace(db=db, is_connected=True)
    customers = SimpleNamespace(
        find_by_id=lambda cid: db.customers.find_one({"customer_id": cid}),
        update=lambda cid, data: db.customers.update_one({"customer_id": cid}, {"$set": data}))
    order_repo = SimpleNamespace(find_by_id=lambda oid: db.orders.find_one({"order_id": oid}))
    monkeypatch.setattr(rr, "get_customer_repository", lambda: customers)
    monkeypatch.setattr(rr, "get_stock_repository", lambda: None)
    monkeypatch.setattr(rr, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(rr, "_orders_coll", lambda: orders if orders is not None else db.orders)
    monkeypatch.setattr(rr, "_returns_coll", lambda: db.returns)
    monkeypatch.setattr(deps, "get_db", lambda: conn, raising=False)
    monkeypatch.setattr(deps, "get_task_repository", lambda: None, raising=False)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None, raising=False)


def seed(db, tag, never_shipped=False):
    """An imported, paid, customer-linked order of two Rs 1,000 pieces (5%
    GST) and the Shopify refund of one of them. `never_shipped`: the order
    Shopify cancelled and restocked before it shipped, both pieces refunded
    -- the imported shape whose refund AUTO mode posts by itself (a refund of
    goods that did ship is queued for a person even under AUTO)."""
    oid, shop_id, refund_id = f"OC-{tag}", f"SH-{tag}", f"RF-{tag}"
    qty = 2 if never_shipped else 1
    db.customers.insert_one({"customer_id": f"CU-{tag}", "name": "Synthetic", "store_credit": 0.0})
    order = {
        "order_id": oid, "shopify_order_id": shop_id, "order_number": f"ONL-{tag}", "store_id": "S1",
        "customer_id": f"CU-{tag}", "customer_name": "Synthetic", "channel": "ONLINE", "source": "shopify",
        "status": "REFUNDED" if never_shipped else "DELIVERED",
        "fulfillment_status": "RESTOCKED" if never_shipped else "FULFILLED",
        "payment_status": "PAID", "historical": True,
        "import_source": "shopify_order_history", "created_at": "2026-10-04T05:00:00+00:00",
        "grand_total": 2000.0, "amount_paid": 2000.0, "tax_amount": 95.24, "fulfillment_stores": ["S1"],
        "items": [{"item_id": f"{oid}-L1", "product_id": "PI", "ims_product_id": "PI",
                   "shopify_line_item_id": f"LI-{tag}", "sku": "SYN-1", "product_name": "Synthetic frame",
                   "quantity": 2, "unit_price": 1000.0, "item_total": 2000.0, "taxable_value": 1904.76,
                   "tax_amount": 95.24, "gst_rate": 5.0, "cost_at_sale": 600.0}],
    }
    db.orders.insert_one(dict(order))
    refund = {"id": refund_id, "order_id": shop_id, "processed_at": REFUND_AT, "created_at": REFUND_AT,
              "restock": True,
              "refund_line_items": [{"line_item_id": f"LI-{tag}", "quantity": qty,
                                     "subtotal": round(952.38 * qty, 2), "total_tax": round(47.62 * qty, 2),
                                     "restock_type": "cancel" if never_shipped else "return"}],
              "transactions": [{"kind": "refund", "status": "success", "amount": f"{1000 * qty}.00",
                                "gateway": "razorpay"}]}
    payload = {"id": shop_id, "financial_status": "refunded" if never_shipped else "partially_refunded",
               "refunds": [dict(refund)]}
    return SimpleNamespace(order=order, refund=refund, payload=payload, refund_id=refund_id, oid=oid,
                           gross=1000.0 * qty, tax=round(47.62 * qty, 2))


def books(db, s):
    """What storage holds for the refund: its credit-note ledger rows (every
    writer's), its returns docs, and the order line's booked pieces."""
    rows = list(db.credit_note_ledger.find({"$or": [
        {"shopify_refund_id": s.refund_id}, {"ref": f"SHOPIFY-HIST-REFUND-{s.refund_id}"},
        {"order_id": s.oid}]}))
    docs = list(db.returns.find({"shopify_refund_id": s.refund_id}))
    line = db.orders.find_one({"order_id": s.oid})["items"][0]
    return SimpleNamespace(
        rows=rows, docs=docs, returned=float(line.get("returned_qty") or 0),
        tax=round(sum(float(r.get("tax") or 0) for r in rows), 2),
        gross=round(sum(float(r.get("gross_refund") or 0) for r in rows), 2))


def import_run(db, s):
    from api.services import shopify_ingest

    return shopify_ingest._book_historical_refund_credit_notes(
        db, s.payload, db.orders.find_one({"order_id": s.oid}, {"_id": 0}))


def live_webhook(db, s):
    from api.services import shopify_refund

    return shopify_refund.handle_shopify_refund(db, dict(s.refund))


def live_confirm(db, s):
    """The accountant's Confirm of the queued review row."""
    from api.services import shopify_refund

    row = db.shopify_refund_review.find_one({"shopify_refund_id": s.refund_id})
    assert row is not None, "the webhook queued no review row"
    return shopify_refund.post_from_review(db, row)


def after_the_imports_booked_read(monkeypatch, refund_id, then):
    """Run `then` once, right after the import's "is it booked" read of this
    refund answered -- the live door arriving in that window."""
    from api.services import shopify_refund

    real, fired = shopify_refund._refund_already_processed, []

    def read(db, rid, strict=False):
        answer = real(db, rid, strict=strict)
        if strict and rid == refund_id and not fired:
            fired.append(None)
            fired[0] = then()
        return answer

    monkeypatch.setattr(shopify_refund, "_refund_already_processed", read)
    return fired


@pytest.fixture
def db(monkeypatch):
    from test_b11_finance_ap_pnl import _Positional

    d = mongomock.MongoClient()[f"oc_{uuid.uuid4().hex[:8]}"]
    wire(monkeypatch, d, _Positional(d.orders))
    return d


def test_a_webhook_that_claims_the_refund_after_the_imports_read_is_the_only_note(db, monkeypatch):
    """AUTO on, uniq_shopify_refund_id present: the webhook claims and credits
    the refund between the import's read and its booking. The import booked
    its own ledger row all the same and reported 'booked'."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "on")
    s = seed(db, "A", never_shipped=True)
    live = after_the_imports_booked_read(monkeypatch, s.refund_id, lambda: live_webhook(db, s))
    run = import_run(db, s)
    assert live and live[0]["status"] == "credited", live
    b = books(db, s)
    assert len(b.rows) == 1 and (b.gross, b.tax) == (s.gross, s.tax), [(r.get("ref"), r.get("tax")) for r in b.rows]
    assert not str(b.rows[0].get("ref")).startswith("SHOPIFY-HIST-REFUND-")  # the live door's note
    assert len(b.docs) == 1 and b.docs[0]["source"] == "shopify"
    assert (run["credit_notes"], run["cn_failed"], run["duplicate_refunds"]) == (0, 0, 1)
    again = import_run(db, s)
    assert (again["credit_notes"], again["cn_failed"], again["duplicate_refunds"]) == (0, 0, 1)
    assert len(books(db, s).rows) == 1


def after_the_imports_claim(monkeypatch, db, refund_id, then):
    """Run `then` once, right after the import's claim of this refund landed
    and before its ledger row is written."""
    cls = type(db.returns)
    real, fired = cls.insert_one, []

    def insert_one(self, doc, *a, **k):
        out = real(self, doc, *a, **k)
        if self.name == "returns" and doc.get("return_id") == f"RET-HIST-{refund_id}" and not fired:
            fired.append(None)
            fired[0] = then()
        return out

    monkeypatch.setattr(cls, "insert_one", insert_one)
    return fired


def no_claim_index(monkeypatch, db):
    """uniq_shopify_refund_id cannot be built or confirmed (its build fails,
    as over rows that already hold one refund id twice). Returns the undo."""
    cls = type(db.returns)
    real = cls.create_index
    try:
        db.returns.drop_index("uniq_shopify_refund_id")
    except Exception:  # noqa: BLE001 -- it was never built
        pass

    def create_index(self, keys, *a, **k):
        if k.get("name") == "uniq_shopify_refund_id":
            raise RuntimeError("uniq_shopify_refund_id cannot be built")
        return real(self, keys, *a, **k)

    monkeypatch.setattr(cls, "create_index", create_index)
    return lambda: monkeypatch.setattr(cls, "create_index", real)


def test_the_import_that_claimed_first_holds_the_refund_until_it_has_finished(db, monkeypatch):
    """The other order: the import's claim lands first. The webhook arriving
    before the import's ledger row books nothing and is NOT a duplicate
    (decision D103(1)): it reports that the import holds the refund, which
    fails its inbox row for a retry. Once the import has finished, the
    redelivery is the duplicate."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "on")
    s = seed(db, "B", never_shipped=True)
    live = after_the_imports_claim(monkeypatch, db, s.refund_id, lambda: live_webhook(db, s))
    run = import_run(db, s)
    assert live and (live[0]["status"], live[0]["reason"]) == ("error", "held_by_import"), live
    b = books(db, s)
    assert [r["_id"] for r in b.rows] == [f"SHOPIFY-HIST-REFUND-{s.refund_id}"] and b.tax == s.tax
    assert len(b.docs) == 1 and b.docs[0]["return_id"] == f"RET-HIST-{s.refund_id}"
    assert (run["credit_notes"], run["cn_failed"]) == (1, 0)
    assert live_webhook(db, s)["status"] == "duplicate" and len(books(db, s).rows) == 1


def test_a_review_row_queued_after_the_imports_read_is_refused_at_confirm(db, monkeypatch):
    """Default mode (AUTO off): the webhook queues a review row between the
    import's read and its booking. The import books; the accountant's Confirm
    of that row is then a duplicate."""
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    s = seed(db, "C")
    live = after_the_imports_booked_read(monkeypatch, s.refund_id, lambda: live_webhook(db, s))
    run = import_run(db, s)
    assert live[0]["status"] == "queued" and (run["credit_notes"], run["cn_failed"]) == (1, 0)
    assert live_confirm(db, s)["status"] == "duplicate"
    b = books(db, s)
    assert [r["_id"] for r in b.rows] == [f"SHOPIFY-HIST-REFUND-{s.refund_id}"] and (b.gross, b.tax) == (s.gross, s.tax)
    assert len(b.docs) == 1 and b.returned == 1.0


def test_without_the_claim_index_the_import_books_nothing_and_the_confirm_books_once(db, monkeypatch):
    """uniq_shopify_refund_id missing and not buildable: the live door's
    claim is then no claim at all, so the import books nothing (reported
    failed) instead of a row the accountant's Confirm would double. With the
    index back the re-import finds the refund booked by the live door."""
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    s = seed(db, "D")
    index_back = no_claim_index(monkeypatch, db)
    live = after_the_imports_booked_read(monkeypatch, s.refund_id, lambda: live_webhook(db, s))
    run = import_run(db, s)
    assert live[0]["status"] == "queued"
    assert (run["credit_notes"], run["cn_failed"], run["cn_failed_refund_ids"]) == (0, 1, [s.refund_id])
    b = books(db, s)
    assert (len(b.rows), len(b.docs), b.returned) == (0, 0, 0.0)  # nothing written at all
    assert live_confirm(db, s)["status"] == "credited"
    index_back()
    again = import_run(db, s)
    assert (again["credit_notes"], again["cn_failed"], again["duplicate_refunds"]) == (0, 0, 1)
    b = books(db, s)
    assert len(b.rows) == 1 and (b.gross, b.tax) == (s.gross, s.tax)
    assert len(b.docs) == 1 and b.docs[0]["source"] == "shopify"


def test_without_the_claim_index_and_no_live_door_the_next_run_books_once(db, monkeypatch):
    s = seed(db, "E")
    index_back = no_claim_index(monkeypatch, db)
    run = import_run(db, s)
    assert (run["credit_notes"], run["cn_failed"]) == (0, 1) and not books(db, s).rows
    index_back()
    again = import_run(db, s)
    assert (again["credit_notes"], again["cn_failed"]) == (1, 0)
    b = books(db, s)
    assert (len(b.rows), len(b.docs), b.returned, b.tax) == (1, 1, 1.0, s.tax)


def test_a_run_cut_off_after_its_claim_is_finished_by_the_next_and_no_live_door_books_meanwhile(db, monkeypatch):
    """The claim and the piece are booked, the ledger row is refused: the
    run is reported failed, the claim reads unfinished, a webhook in the gap
    books nothing and says the import holds the refund (decision D103(1)),
    and the next import run writes the one ledger row and finishes it."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "on")
    s = seed(db, "F", never_shipped=True)
    cls = type(db.credit_note_ledger)
    real, refused = cls.insert_one, []

    def insert_one(self, doc, *a, **k):
        if self.name == "credit_note_ledger" and not refused:
            refused.append(1)
            raise RuntimeError("ledger write refused")
        return real(self, doc, *a, **k)

    monkeypatch.setattr(cls, "insert_one", insert_one)
    run = import_run(db, s)
    assert refused and (run["credit_notes"], run["cn_failed"]) == (0, 1)
    b = books(db, s)
    assert (len(b.rows), len(b.docs), b.returned) == (0, 1, 2.0)
    assert (b.docs[0]["status"], b.docs[0]["credit_note_issued"]) == ("PENDING", False)
    assert live_webhook(db, s)["reason"] == "held_by_import" and not books(db, s).rows
    again = import_run(db, s)
    assert (again["credit_notes"], again["cn_failed"]) == (1, 0)
    assert (again["unfinished_claims_found"], again["unfinished_claims_finished"]) == (1, 1)
    assert live_webhook(db, s)["status"] == "duplicate"
    third = import_run(db, s)
    assert (third["credit_notes"], third["cn_failed"], third["duplicate_refunds"]) == (0, 0, 1)
    b = books(db, s)
    assert (len(b.rows), len(b.docs), b.returned, b.tax) == (1, 1, 2.0, s.tax)
    assert b.docs[0]["goods_lines"][0]["return_qty"] == 2.0
    assert (b.docs[0]["status"], b.docs[0]["credit_note_issued"]) == ("COMPLETED", True)


def test_a_ledger_row_an_older_version_left_without_its_record_is_healed_not_doubled(db):
    """Before the claim came first the ledger row was written first: a run
    cut off after it left a row (a random _id in still older versions) with
    no returns record. The next run completes the record; no second row."""
    s = seed(db, "G")
    first = import_run(db, s)
    assert first["credit_notes"] == 1
    row = db.credit_note_ledger.find_one({"shopify_refund_id": s.refund_id})
    db.credit_note_ledger.delete_one({"_id": row["_id"]})
    db.credit_note_ledger.insert_one({**row, "_id": uuid.uuid4().hex})
    db.returns.delete_many({"shopify_refund_id": s.refund_id})
    again = import_run(db, s)
    assert (again["credit_notes"], again["cn_failed"], again.get("healed")) == (0, 0, 1)
    b = books(db, s)
    assert (len(b.rows), len(b.docs), b.returned) == (1, 1, 1.0)
    assert b.docs[0]["goods_lines"][0]["return_qty"] == 1.0


def test_a_whole_order_note_is_not_booked_over_a_refund_a_live_door_holds(db, monkeypatch):
    """A fully refunded order with two amount-only refunds gets ONE
    whole-order note for their total, and both refund ids are claimed for it
    first. A live door that claimed the second after the import read it
    holds that refund: the whole-order note is not booked on top of it, and
    the first refund's claim is given up (it stands for no note)."""
    s = seed(db, "H")
    ids = [f"{s.refund_id}-1", f"{s.refund_id}-2"]
    s.payload.update(financial_status="refunded", refunds=[
        {"id": rid, "processed_at": REFUND_AT,
         "transactions": [{"kind": "refund", "status": "success", "amount": "1000.00"}]} for rid in ids])

    def live_claim():
        db.returns.insert_one({"return_id": "RET-LIVE-H", "shopify_refund_id": ids[1], "order_id": s.oid,
                               "source": "shopify", "status": "PENDING", "created_at": REFUND_AT})

    after_the_imports_booked_read(monkeypatch, ids[1], live_claim)
    run = import_run(db, s)
    assert db.credit_note_ledger.count_documents({}) == 0
    assert [d["return_id"] for d in db.returns.find({})] == ["RET-LIVE-H"]
    assert (run["credit_notes"], run["cn_failed"], run["duplicate_refunds"], run["unmapped_refunds"]) == (0, 0, 1, 1)
    # A run after that live claim reads the same: nothing booked over it.
    again = import_run(db, s)
    assert (again["credit_notes"], again["duplicate_refunds"]) == (0, 1)
    assert db.credit_note_ledger.count_documents({}) == 0 and db.returns.count_documents({}) == 1


def test_a_live_claim_on_a_refund_a_stored_whole_order_note_covers_is_left_alone(db, monkeypatch):
    """The whole-order note is stored; the import's claim of one covered
    refund is gone and a live door claims that refund inside the next run's
    window. The run books nothing, leaves the live door's claim exactly as
    it is, and does not report a failure for it (it is not the import's to
    finish)."""
    s = seed(db, "L")
    ids = [f"{s.refund_id}-1", f"{s.refund_id}-2"]
    s.payload.update(financial_status="refunded", refunds=[
        {"id": rid, "processed_at": REFUND_AT,
         "transactions": [{"kind": "refund", "status": "success", "amount": "1000.00"}]} for rid in ids])
    assert import_run(db, s)["credit_notes"] == 1
    db.returns.delete_one({"shopify_refund_id": ids[1]})
    live = {"return_id": "RET-LIVE-L", "shopify_refund_id": ids[1], "order_id": s.oid,
            "source": "shopify", "status": "PENDING", "created_at": REFUND_AT}
    after_the_imports_booked_read(monkeypatch, ids[1], lambda: db.returns.insert_one(dict(live)))
    run = import_run(db, s)
    assert (run["credit_notes"], run["cn_failed"], run["cn_failed_refund_ids"]) == (0, 0, []), run
    held = db.returns.find_one({"shopify_refund_id": ids[1]}, {"_id": 0})
    assert held == live and db.credit_note_ledger.count_documents({}) == 1


# --- decision D103: an import claim is unfinished until its GST row exists -----


class Killed(BaseException):
    """The process dies here: nothing in the application catches it (every
    handler in the import and the live doors is `except Exception`)."""


def kill_once(monkeypatch, db, coll, method, match=lambda *a: True):
    """Kill the run at the first `method` on collection `coll` that `match`
    accepts, BEFORE the write. Works on mongomock and on native pymongo.
    Returns the undo."""
    cls = type(db[coll])
    real, fired = getattr(cls, method), []

    def write(self, *a, **k):
        if self.name == coll and not fired and match(*a):
            fired.append(1)
            raise Killed()
        return real(self, *a, **k)

    monkeypatch.setattr(cls, method, write)
    return lambda: monkeypatch.setattr(cls, method, real)


def killed_import(monkeypatch, db, s, coll="credit_note_ledger", method="insert_one", match=lambda *a: True):
    undo = kill_once(monkeypatch, db, coll, method, match)
    with pytest.raises(Killed):
        import_run(db, s)
    undo()


def claims(db, s):
    """The import's own returns docs of the order: (return_id, status,
    credit_note_issued), sorted."""
    return sorted((d["return_id"], d["status"], d["credit_note_issued"])
                  for d in db.returns.find({"order_id": s.oid, "source": "shopify_order_history"}))


def pnl_returns(db, shop):
    """How many returns the P&L's own reader takes off this shop's November."""
    from api.routers.finance._shared import customer_return_adjustments

    return (customer_return_adjustments(db, {}, [shop], "2026-11-01", "2026-11-30").get(shop) or {}).get("count", 0)


def own_shop(db, s, shop):
    """Give the order a shop of its own, so a reader's figure is this order's."""
    db.orders.update_one({"order_id": s.oid}, {"$set": {"store_id": shop, "fulfillment_stores": [shop]}})
    s.order["store_id"] = shop
    return shop


def confirm_at_the_screen(db, s):
    """POST /online-store/refund-reviews/{id}/confirm as an accountant: the
    route function itself (its answer or its HTTPException)."""
    import asyncio

    from api.routers.online_store_refund_reviews import confirm_refund_review

    row = db.shopify_refund_review.find_one({"shopify_refund_id": s.refund_id})
    return asyncio.run(confirm_refund_review(
        row["review_id"], current_user={"user_id": "acct-1", "roles": ["ACCOUNTANT"]}))


def killed_between_claim_and_ledger(db, monkeypatch, tag="K"):
    """A run KILLED after its claim and before its ledger row (decision
    D103(1)). The claim is unfinished: the P&L does not take the return off,
    the webhook books nothing and says the import holds it (never a silent
    duplicate, never a re-claim), and the next run finishes it once and
    reports it."""
    from api.services import shopify_refund

    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "on")
    s = seed(db, tag, never_shipped=True)
    shop = own_shop(db, s, f"S-{tag}")
    killed_import(monkeypatch, db, s)
    assert claims(db, s) == [(f"RET-HIST-{s.refund_id}", "PENDING", False)]
    assert not books(db, s).rows and pnl_returns(db, shop) == 0
    before = db.returns.find_one({"shopify_refund_id": s.refund_id})
    hook = live_webhook(db, s)
    assert (hook["status"], hook["reason"]) == ("error", "held_by_import"), hook
    assert "order-history import" in hook["error"]
    # the status sweep's guard: fed (it is the import's, unfinished), not passed over
    assert shopify_refund._refund_already_processed(db, s.refund_id) and shopify_refund.import_holds(db, s.refund_id)
    # nothing was written by the live door: not booked, not re-claimed, no review row
    assert db.returns.find_one({"shopify_refund_id": s.refund_id}) == before and not books(db, s).rows
    assert db.shopify_refund_review.count_documents({"shopify_refund_id": s.refund_id}) == 0
    run = import_run(db, s)
    assert (run["unfinished_claims_found"], run["unfinished_claims_finished"]) == (1, 1), run
    assert (run["credit_notes"], run["cn_failed"], run["tax"]) == (1, 0, s.tax), run
    b = books(db, s)
    assert [r["_id"] for r in b.rows] == [f"SHOPIFY-HIST-REFUND-{s.refund_id}"] and b.tax == s.tax
    assert claims(db, s) == [(f"RET-HIST-{s.refund_id}", "COMPLETED", True)] and pnl_returns(db, shop) == 1
    assert live_webhook(db, s)["status"] == "duplicate" and not shopify_refund.import_holds(db, s.refund_id)
    third = import_run(db, s)
    assert (third["unfinished_claims_found"], third["credit_notes"], third["duplicate_refunds"]) == (0, 0, 1)
    assert len(books(db, s).rows) == 1
    return s


def killed_with_a_review_row_then_confirm(db, monkeypatch, tag="Q"):
    """Default mode: the webhook queued its review row in the import's
    window, then the import was killed after its claim. The accountant's
    Confirm books nothing, says the import holds the refund and keeps the
    row open; after the next import run the same Confirm reads it posted."""
    from fastapi import HTTPException

    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    s = seed(db, tag)
    live = after_the_imports_booked_read(monkeypatch, s.refund_id, lambda: live_webhook(db, s))
    killed_import(monkeypatch, db, s)
    assert live[0]["status"] == "queued" and claims(db, s) == [(f"RET-HIST-{s.refund_id}", "PENDING", False)]
    before = db.returns.find_one({"shopify_refund_id": s.refund_id})
    with pytest.raises(HTTPException) as refused:
        confirm_at_the_screen(db, s)
    assert refused.value.status_code == 422 and "order-history import" in refused.value.detail
    assert "duplicate" not in refused.value.detail
    row = db.shopify_refund_review.find_one({"shopify_refund_id": s.refund_id})
    assert (row["status"], row["resolved"]) == ("CREDIT_FAILED", False)
    assert db.returns.find_one({"shopify_refund_id": s.refund_id}) == before  # never re-claimed
    assert not books(db, s).rows and db.returns.count_documents({"shopify_refund_id": s.refund_id}) == 1
    run = import_run(db, s)
    assert (run["unfinished_claims_found"], run["unfinished_claims_finished"], run["credit_notes"]) == (1, 1, 1)
    assert confirm_at_the_screen(db, s)["status"] == "POSTED"
    b = books(db, s)
    assert [r["_id"] for r in b.rows] == [f"SHOPIFY-HIST-REFUND-{s.refund_id}"] and (b.gross, b.tax) == (s.gross, s.tax)
    assert len(b.docs) == 1 and b.returned == 1.0
    return s


def webhook_that_read_first_never_takes_the_claim_over(db, monkeypatch, tag="P"):
    """AUTO on. The webhook passed its "is it booked" read BEFORE the
    import's claim and reaches its own claim AFTER it (the import was killed
    before its ledger row). The import's claim is old by its date (the
    refund's), which is what makes a dead LIVE claim re-claimable -- it must
    never be re-claimed: the live door would post a second note under its
    own key and the next import run a first under its."""
    from api.services import shopify_refund

    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "on")
    s = seed(db, tag, never_shipped=True)
    real, fired = shopify_refund._mark_order, []

    def mark(d, order_id):  # runs inside the live post, right before its claim
        real(d, order_id)
        if not fired:
            fired.append(1)
            killed_import(monkeypatch, db, s)

    monkeypatch.setattr(shopify_refund, "_mark_order", mark)
    hook = live_webhook(db, s)
    monkeypatch.setattr(shopify_refund, "_mark_order", real)
    assert fired and (hook["status"], hook["reason"]) == ("credit_failed", "held_by_import"), hook
    claim = db.returns.find_one({"shopify_refund_id": s.refund_id})
    assert (claim["return_id"], claim["status"], claim["credit_note_issued"]) == (
        f"RET-HIST-{s.refund_id}", "PENDING", False)
    assert "reprocessing_at" not in claim and not books(db, s).rows
    row = db.shopify_refund_review.find_one({"shopify_refund_id": s.refund_id})
    assert row is not None and "order-history import" in row["note"]
    run = import_run(db, s)
    assert (run["unfinished_claims_found"], run["unfinished_claims_finished"], run["credit_notes"]) == (1, 1, 1)
    assert live_confirm(db, s)["status"] == "duplicate" and live_webhook(db, s)["status"] == "duplicate"
    b = books(db, s)
    assert [r["_id"] for r in b.rows] == [f"SHOPIFY-HIST-REFUND-{s.refund_id}"] and (b.gross, b.tax) == (s.gross, s.tax)
    assert len(b.docs) == 1
    return s


def whole_order(db, tag, amount="1000.00"):
    """A fully refunded order with two amount-only refunds."""
    s = seed(db, tag)
    ids = [f"{s.refund_id}-1", f"{s.refund_id}-2"]
    refunds = [{"id": rid, "order_id": s.order["shopify_order_id"], "processed_at": REFUND_AT,
                "created_at": REFUND_AT,
                "transactions": [{"kind": "refund", "status": "success", "amount": amount,
                                  "gateway": "razorpay"}]} for rid in ids]
    s.payload.update(financial_status="refunded", refunds=[dict(r) for r in refunds])
    return s, ids, refunds


def whole_order_note_that_cannot_be_priced(db, monkeypatch, tag="U"):
    """Decision D103(2): the order's lines carry no billed value, so the
    whole-order note comes back 'unmapped'. The claims taken for the refunds
    it was to cover are given up: no claim stands for a note that will never
    exist, and a later webhook for one of them is handled by the live door
    (a MONEY_ONLY review row), not swallowed as a duplicate."""
    from api.services import shopify_refund

    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    s, ids, refunds = whole_order(db, tag)
    db.orders.update_one({"order_id": s.oid}, {"$set": {
        "items.0.unit_price": 0.0, "items.0.item_total": 0.0, "items.0.taxable_value": 0.0,
        "items.0.tax_amount": 0.0, "grand_total": 0.0, "tax_amount": 0.0}})
    for _ in range(2):  # the second run finds nothing to heal and leaves nothing
        run = import_run(db, s)
        assert (run["credit_notes"], run["cn_failed"], run["unfinished_claims_found"]) == (0, 0, 0), run
        assert claims(db, s) == [] and db.returns.count_documents({"order_id": s.oid}) == 0
        assert db.credit_note_ledger.count_documents({"shopify_refund_id": {"$in": ids}}) == 0
    hook = shopify_refund.handle_shopify_refund(db, dict(refunds[0]))
    assert hook["status"] == "queued", hook
    row = db.shopify_refund_review.find_one({"shopify_refund_id": ids[0]})
    assert row is not None and row["status"] == "MONEY_ONLY"
    return s


def whole_order_note_killed_before_its_row(db, monkeypatch, tag="W"):
    """The refunds a whole-order note covers are claimed unfinished and
    finished only once the note's ledger row is stored (decision D103(1)).
    Killed before that row: all three claims read unfinished, a webhook for
    a covered refund is held, and the next run books the one note and
    finishes all three."""
    from api.services import shopify_refund

    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    s, ids, refunds = whole_order(db, tag)
    cover = f"orderfull:{s.order['shopify_order_id']}"
    killed_import(monkeypatch, db, s)
    want = sorted(f"RET-HIST-{rid}" for rid in ids + [cover])
    assert claims(db, s) == [(rid, "PENDING", False) for rid in want]
    assert db.credit_note_ledger.count_documents({"ref": f"SHOPIFY-HIST-REFUND-{cover}"}) == 0
    hook = shopify_refund.handle_shopify_refund(db, dict(refunds[0]))
    assert (hook["status"], hook["reason"]) == ("error", "held_by_import"), hook
    assert db.shopify_refund_review.count_documents({"order_id": s.oid}) == 0
    run = import_run(db, s)
    assert (run["unfinished_claims_found"], run["unfinished_claims_finished"]) == (3, 3), run
    assert (run["credit_notes"], run["cn_failed"], run["gross"]) == (1, 0, 2000.0), run
    assert claims(db, s) == [(rid, "COMPLETED", True) for rid in want]
    assert db.credit_note_ledger.count_documents({"ref": f"SHOPIFY-HIST-REFUND-{cover}"}) == 1
    assert db.credit_note_ledger.count_documents({"shopify_refund_id": {"$in": ids}}) == 0
    assert shopify_refund.handle_shopify_refund(db, dict(refunds[0]))["status"] == "duplicate"
    again = import_run(db, s)
    assert (again["credit_notes"], again["unfinished_claims_found"], again["cn_failed"]) == (0, 0, 0)
    assert db.credit_note_ledger.count_documents({"ref": f"SHOPIFY-HIST-REFUND-{cover}"}) == 1
    return s


def test_a_run_killed_after_its_claim_is_unfinished_for_every_reader_and_door(db, monkeypatch):
    killed_between_claim_and_ledger(db, monkeypatch)


def test_a_confirm_that_meets_an_unfinished_import_claim_says_so_and_books_nothing(db, monkeypatch):
    killed_with_a_review_row_then_confirm(db, monkeypatch)


def test_a_live_door_never_takes_over_an_unfinished_import_claim(db, monkeypatch):
    webhook_that_read_first_never_takes_the_claim_over(db, monkeypatch)


def test_a_whole_order_note_that_cannot_be_priced_leaves_no_claim_behind(db, monkeypatch):
    whole_order_note_that_cannot_be_priced(db, monkeypatch)


def test_the_refunds_a_whole_order_note_covers_are_finished_only_with_its_row(db, monkeypatch):
    whole_order_note_killed_before_its_row(db, monkeypatch)


def test_a_whole_order_note_booked_in_one_run_finishes_every_claim(db):
    s, _ids, _ = whole_order(db, "X")
    run = import_run(db, s)
    assert (run["credit_notes"], run["cn_failed"], run["unfinished_claims_found"]) == (1, 0, 0), run
    assert [c[1:] for c in claims(db, s)] == [("COMPLETED", True)] * 3


def test_a_run_killed_between_its_ledger_row_and_the_finish_stamp_is_finished_once(db, monkeypatch):
    """The other side of the claim's two stamps: the ledger row is stored,
    the run is killed before the claim is stamped finished. The claim still
    reads unfinished (a live door is held, books nothing), and the next run
    stamps it without a second row."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "on")
    s = seed(db, "R", never_shipped=True)
    killed_import(monkeypatch, db, s, "returns", "update_one",
                  lambda flt, upd, *a: (upd.get("$set") or {}).get("credit_note_issued") is True)
    assert claims(db, s) == [(f"RET-HIST-{s.refund_id}", "PENDING", False)] and len(books(db, s).rows) == 1
    assert live_webhook(db, s)["reason"] == "held_by_import" and len(books(db, s).rows) == 1
    run = import_run(db, s)
    assert (run["unfinished_claims_found"], run["unfinished_claims_finished"]) == (1, 1), run
    assert (run["credit_notes"], run["cn_failed"], run["duplicate_refunds"]) == (0, 0, 1), run
    b = books(db, s)
    assert len(b.rows) == 1 and b.tax == s.tax and claims(db, s) == [(f"RET-HIST-{s.refund_id}", "COMPLETED", True)]
    assert live_webhook(db, s)["status"] == "duplicate"


def test_a_finish_stamp_that_cannot_be_written_is_reported_failed_not_complete(db, monkeypatch):
    """The ledger row is stored and the finish stamp is refused (an ordinary
    write error, the run survives): the note counts as booked, the refund is
    reported failed -- the run never reads complete while a claim is
    unfinished -- and the next run stamps it."""
    s = seed(db, "T")
    cls = type(db.returns)
    real, refused = cls.update_one, []

    def update_one(self, flt, upd, *a, **k):
        if self.name == "returns" and (upd.get("$set") or {}).get("credit_note_issued") is True and not refused:
            refused.append(1)
            raise RuntimeError("finish stamp refused")
        return real(self, flt, upd, *a, **k)

    monkeypatch.setattr(cls, "update_one", update_one)
    run = import_run(db, s)
    assert refused and (run["credit_notes"], run["cn_failed"], run["cn_failed_refund_ids"]) == (1, 1, [s.refund_id])
    assert claims(db, s) == [(f"RET-HIST-{s.refund_id}", "PENDING", False)] and len(books(db, s).rows) == 1
    again = import_run(db, s)
    assert (again["credit_notes"], again["cn_failed"]) == (0, 0)
    assert (again["unfinished_claims_found"], again["unfinished_claims_finished"]) == (1, 1)
    assert claims(db, s) == [(f"RET-HIST-{s.refund_id}", "COMPLETED", True)] and len(books(db, s).rows) == 1


# --- native Mongo, two threads -------------------------------------------------


def race(db, s, live, timeout=20):
    """The import and a live door on one refund, released together."""
    start, out = threading.Barrier(2, timeout=timeout), {}

    def go(name, fn):
        try:
            start.wait()
        except threading.BrokenBarrierError:
            pass
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001
            out[name] = exc

    threads = [threading.Thread(target=go, args=("import", lambda: import_run(db, s))),
               threading.Thread(target=go, args=("live", lambda: live(db, s)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    return out


def native_auto_race(db, monkeypatch, n=6, tag="N"):
    """AUTO on, the index present: `n` fresh refunds, each raced by the import
    and the webhook. Whoever wins, storage holds one ledger row and one
    returns doc per refund, and a re-import and a redelivery add nothing."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "on")
    winners = []
    for i in range(n):
        s = seed(db, f"{tag}{i}", never_shipped=True)
        out = race(db, s, live_webhook)
        assert not any(isinstance(v, Exception) for v in out.values()), out
        for _ in range(2):  # anything unfinished is finished; nothing is added
            import_run(db, s)
            live_webhook(db, s)
        b = books(db, s)
        assert len(b.rows) == 1 and (b.gross, b.tax) == (s.gross, s.tax), (i, out, [r.get("ref") for r in b.rows])
        assert len(b.docs) == 1, (i, [d.get("return_id") for d in b.docs])
        winners.append("import" if str(b.rows[0].get("ref")).startswith("SHOPIFY-HIST-REFUND-") else "live")
    return winners


def native_missing_index_race(db, monkeypatch, tag="M"):
    """The index missing and not buildable, default mode: the webhook queues
    its review row after the import's read, and the accountant's Confirm then
    runs on its own thread while the import is still between its read and
    its booking. The import books nothing; the Confirm books the one note."""
    from api.services import shopify_refund

    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    s = seed(db, tag)
    no_claim_index(monkeypatch, db)
    real, seen = shopify_refund._refund_already_processed, {}

    def read(d, rid, strict=False):
        answer = real(d, rid, strict=strict)
        if strict and rid == s.refund_id and not seen:
            seen["queued"] = live_webhook(d, s)
            worker = threading.Thread(target=lambda: seen.update(confirm=live_confirm(d, s)))
            worker.start()
            worker.join(20)
        return answer

    monkeypatch.setattr(shopify_refund, "_refund_already_processed", read)
    run = import_run(db, s)
    assert seen["queued"]["status"] == "queued" and seen["confirm"]["status"] == "credited", seen
    assert (run["credit_notes"], run["cn_failed"]) == (0, 1)
    b = books(db, s)
    assert len(b.rows) == 1 and (b.gross, b.tax) == (s.gross, s.tax) and len(b.docs) == 1
    return b


@pytest.fixture
def native_db(monkeypatch):
    from pymongo import MongoClient

    url = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
    client = MongoClient(url, serverSelectionTimeoutMS=2000)
    client.admin.command("ping")
    d = client[f"ims_test_oneclaim_{uuid.uuid4().hex[:8]}"]
    wire(monkeypatch, d)
    yield d
    client.drop_database(d.name)


def test_native_the_import_racing_the_webhook_books_one_note(native_db, monkeypatch):
    native_auto_race(native_db, monkeypatch)


def test_native_without_the_claim_index_the_import_refuses_and_the_confirm_books_once(native_db, monkeypatch):
    native_missing_index_race(native_db, monkeypatch)


def test_native_a_killed_import_is_held_and_finished_by_the_next_run(native_db, monkeypatch):
    killed_between_claim_and_ledger(native_db, monkeypatch, tag="NK")
    killed_with_a_review_row_then_confirm(native_db, monkeypatch, tag="NQ")
    webhook_that_read_first_never_takes_the_claim_over(native_db, monkeypatch, tag="NP")


def test_native_whole_order_claims_are_given_up_or_finished_with_the_note(native_db, monkeypatch):
    whole_order_note_that_cannot_be_priced(native_db, monkeypatch, tag="NU")
    whole_order_note_killed_before_its_row(native_db, monkeypatch, tag="NW")
