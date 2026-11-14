"""
IMS 2.0 - every online refund leaves a record; its credit note files only what was not taken back (D62, #1397).
================================================================================================================
Native-Mongo probe (claude_d62_realmongo.py) at cd76c9dc on a Rs 1,000 cash bill (line A Rs 500, line B Rs 500):

  S1  AUTO on. Shopify refunds line A and sends Rs 500 back; the counter takes line A back just before the post
      takes the bill's hold, so the post answers 'already_returned'. Nothing was written: no review row, no returns
      doc. The counter then paid line B too -- Rs 1,500 given back on a Rs 1,000 bill.
  S2  The counter took line A back; a Shopify refund of A + B was confirmed: its credit note filed A + B, so A's
      output tax was reversed a second time (Rs 71.43 of tax reversed on Rs 47.62 billed).

Now: AUTO's 'already_returned' is a review row (a person sees it, every cap counts it); the counter's bill cap
counts the money Shopify sent back on a review row; a failed review-row write is a failure the inbox retries; the
post files only the units no other door took back.

Real code over the online refund fakes of test_refund_review_unpaid_credit (env) and the counter endpoint fakes of
test_returns_endpoint_tender_capture (ctx). No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

from tests.test_bill_money_doors_r7 import _line_refund, _two_line_bill  # noqa: E402
from tests.test_refund_review_unpaid_credit import _balance, _ledger, _txn, env  # noqa: E402,F401
from tests.test_returns_endpoint_tender_capture import ctx  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("gst_numbers")


def _two_lines(env, txns, refund_id):
    p = _line_refund(9001, refund_id)
    second = dict(p["refund_line_items"][0], id=2, line_item_id=9002,
                  line_item={"id": 9002, "sku": "RB-2", "quantity": 1})
    p["refund_line_items"].append(second)
    p["transactions"] = txns
    return p


def test_auto_already_returned_leaves_a_review_row_every_cap_counts(env, monkeypatch):
    """S1: the counter took line A back between the webhook's read and the AUTO
    post. The post books nothing -- and the Rs 500 Shopify sent back is a
    review row, counted by the online cap and by the counter's."""
    from api.routers import returns as R
    from api.services import shopify_refund as S

    monkeypatch.setattr(S, "_refund_auto_enabled", lambda db: True)
    _two_line_bill(env)
    counter_doc = env.get_collection("returns").find_one({"return_id": "RET-C"})
    env.get_collection("returns").delete_one({"return_id": "RET-C"})
    real = S._post_credit_and_restock

    def counter_first(*a, **k):
        env.get_collection("returns").insert_one(counter_doc)  # the counter lands now
        return real(*a, **k)

    monkeypatch.setattr(S, "_post_credit_and_restock", counter_first)
    p = _line_refund(9001, "710001")
    p["transactions"] = [_txn(500.0, "manual")]
    res = S.handle_shopify_refund(env, p, topic="refunds/create")

    assert res["status"] == "queued" and res["queue_status"] == "PENDING", res
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "710001"})
    assert row["note"] == S.ALREADY_RETURNED_NOTE and row["credit_note"]["money_back"] == 500.0
    assert env.get_collection("credit_note_ledger").count_documents({}) == 0
    assert env.get_collection("returns").count_documents({"shopify_refund_id": "710001"}) == 0
    # Every cap counts it: the online door's, the counter's.
    order = env.get_collection("orders").find_one({"order_id": "ord-1"})
    assert S._given_back_elsewhere(env, order, "")["given"] == 1000.0
    assert R._sum_prior_refunds("ord-1", strict=True) == 1000.0
    # A resend is a duplicate (the row is its record), never a second answer.
    monkeypatch.setattr(S, "_post_credit_and_restock", real)
    assert S.handle_shopify_refund(env, p, topic="refunds/create")["status"] == "duplicate"


def test_the_counter_cap_counts_shopify_money_on_a_review_row_once(env):
    """The counter's prior counts what Shopify sent back on a review row; once
    the refund's own returns doc counts, the row is not counted again."""
    from api.routers import returns as R

    _two_line_bill(env)
    reviews = env.get_collection("shopify_refund_review")
    reviews.insert_one({"shopify_refund_id": "R-X", "order_id": "ord-1", "status": "REJECTED",
                        "credit_note": {"money_back": 300.0, "shopify_refunded_amount": 500.0}})
    reviews.insert_one({"shopify_refund_id": "R-Y", "order_id": "ord-1", "status": "POSTED",
                        "credit_note": {"money_back": 200.0}})
    env.get_collection("returns").insert_one({"return_id": "RET-Y", "order_id": "ord-1", "shopify_refund_id": "R-Y",
                                              "status": "COMPLETED", "net_refund": 500.0})
    # RET-C 500 + RET-Y 500 (its doc) + R-X 300 (its row only).
    assert R._sum_prior_refunds("ord-1", strict=True) == 1300.0


def test_the_counter_refuses_a_return_beyond_what_shopify_already_sent_back(ctx, monkeypatch):
    """The counter door itself (POST /returns): a rejected review row whose
    Shopify money took the whole bill leaves nothing to refund."""
    from tests.test_return_points_paid_once import _post
    from tests.test_returns_endpoint_tender_capture import _BILLED_GROSS, _QueryColl

    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    reviews, real = _QueryColl(), c["db"].get_collection
    monkeypatch.setattr(c["db"], "get_collection", lambda n: reviews if n == "shopify_refund_review" else real(n))
    reviews.insert_one({"shopify_refund_id": "R-Z", "order_id": c["order"]["order_id"], "status": "REJECTED",
                        "credit_note": {"money_back": _BILLED_GROSS}})
    r = _post(c, [{"method": "CASH", "amount": _BILLED_GROSS}])
    assert r.status_code == 400 and "exceed" in r.text, r.text
    assert c["returns"].docs == []
    # Control: with no Shopify money on the bill the same return is taken.
    reviews.docs.clear()
    assert _post(c, [{"method": "CASH", "amount": _BILLED_GROSS}]).status_code == 201


def test_a_review_row_that_cannot_be_saved_is_a_failure_the_inbox_retries(env, monkeypatch):
    from api.services import shopify_refund as S
    from tests.test_refund_review_unpaid_credit import _order, _payload

    _order(env)
    coll = env.get_collection("shopify_refund_review")

    def down(doc):
        raise RuntimeError("write refused")

    monkeypatch.setattr(coll, "insert_one", down)
    res = S.handle_shopify_refund(env, _payload(txns=[]), topic="refunds/create")
    assert res["status"] == "error" and res["reason"] == "review_not_saved", res


def test_a_partial_overlap_files_a_credit_note_for_the_other_line_only(env):
    """S2: line A came back at the counter (its own note); Shopify refunds A + B.
    The accountant's Confirm files B only: B's gross and B's tax, never A's
    output tax a second time. The row shows what was filed."""
    import asyncio

    from api.routers import online_store_refund_reviews as RR
    from api.services.shopify_refund import handle_shopify_refund

    _two_line_bill(env)
    assert handle_shopify_refund(env, _two_lines(env, [], "710002"), topic="refunds/create")["status"] == "queued"
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "710002"})
    out = asyncio.run(RR.confirm_refund_review(row["review_id"], {"user_id": "acct", "roles": ["ADMIN"]}))
    assert out["result"]["status"] == "credited"

    rows = _ledger(env)
    assert len(rows) == 1, rows
    assert (rows[0]["gross_refund"], rows[0]["taxable"], rows[0]["tax"], rows[0]["delta"]) == (
        500.0, 476.19, 23.81, 500.0)
    doc = env.get_collection("returns").find_one({"shopify_refund_id": "710002"})
    assert doc["gross_refund"] == 500.0 and doc["gst_breakup"]["tax"] == 23.81
    assert [i["order_item_id"] for i in doc["items"]] == ["it-2"]
    assert _balance(env) == (500.0, 500.0)
    shown = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "710002"})
    assert shown["gross_refund"] == 500.0 and shown["credit_note"]["gross_refund"] == 500.0
    assert "covers only the items not already taken back" in shown["note"]


def test_a_partial_overlap_shopify_paid_files_the_other_line_with_no_credit(env, monkeypatch):
    """AUTO, Shopify sent all Rs 1,000 back: the note files B (Shopify's money
    covers it, no store credit), never A again."""
    from api.services import shopify_refund as S

    monkeypatch.setattr(S, "_refund_auto_enabled", lambda db: True)
    _two_line_bill(env)
    res = S.handle_shopify_refund(env, _two_lines(env, [_txn(1000.0, "manual")], "710003"), topic="refunds/create")
    # The counter took A: a person confirms (unchanged); the confirm files B only.
    assert res["status"] == "queued", res
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "710003"})
    posted = S.post_from_review(env, row)
    assert posted["status"] == "credited" and posted["gross_refund"] == 500.0, posted
    rows = _ledger(env)
    assert [(r["gross_refund"], r["tax"], r["delta"]) for r in rows] == [(500.0, 23.81, 0.0)]


def test_a_refund_with_nothing_taken_back_files_its_whole_note(env):
    """Control: no other door took a unit: the note is the refund, A + B."""
    from api.services import shopify_refund as S

    _two_line_bill(env)
    env.get_collection("returns").delete_one({"return_id": "RET-C"})
    assert S.handle_shopify_refund(env, _two_lines(env, [], "710004"), topic="refunds/create")["status"] == "queued"
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "710004"})
    assert S.post_from_review(env, row)["status"] == "credited"
    assert [(r["gross_refund"], r["tax"], r["delta"]) for r in _ledger(env)] == [(1000.0, 47.62, 1000.0)]
