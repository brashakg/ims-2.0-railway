"""
Abandoned-checkout capture tests (Shopify checkouts/create + checkouts/update).
Pins: pure summarisation (contact-only, consent recorded not acted on) and an
IDEMPOTENT upsert keyed on the checkout token (create + repeated updates collapse
to one evolving row; `recovered` seeded False on insert and never clobbered).

In-memory fakes only -- no DB, no network.
"""

import os
import sys
import types

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import shopify_checkout_capture as cc  # noqa: E402


class _FakeUpsertColl:
    """Minimal update_one(upsert=True) with single-field equality filters."""

    def __init__(self):
        self.docs = []

    def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                d.update(update.get("$set", {}))
                return types.SimpleNamespace(upserted_id=None, matched_count=1)
        if upsert:
            new = {}
            new.update(filt)
            new.update(update.get("$setOnInsert", {}))
            new.update(update.get("$set", {}))
            self.docs.append(new)
            return types.SimpleNamespace(upserted_id="new", matched_count=0)
        return types.SimpleNamespace(upserted_id=None, matched_count=0)


class _FakeDb:
    def __init__(self):
        self._colls = {}

    def get_collection(self, name):
        return self._colls.setdefault(name, _FakeUpsertColl())


_PAYLOAD = {
    "id": 998877,
    "token": "chk_tok_1",
    "email": "buyer@example.com",
    "phone": "+919812345678",
    "currency": "INR",
    "total_price": "2499.00",
    "buyer_accepts_marketing": True,
    "customer": {"id": 42, "first_name": "Asha", "last_name": "R"},
    "line_items": [
        {"title": "Aviator", "sku": "SG-1", "quantity": 2},
        {"title": "Case", "sku": "AC-9", "quantity": 1},
    ],
    "created_at": "2026-07-22T10:00:00Z",
    "updated_at": "2026-07-22T10:05:00Z",
}


def test_checkout_token_prefers_token_then_cart_then_id():
    assert cc.checkout_token({"token": "t", "id": 5}) == "t"
    assert cc.checkout_token({"cart_token": "c", "id": 5}) == "c"
    assert cc.checkout_token({"id": 5}) == "5"
    assert cc.checkout_token({}) == ""


def test_summarize_is_contact_only_and_records_consent():
    s = cc.summarize_checkout(_PAYLOAD)
    assert s["email"] == "buyer@example.com"
    assert s["phone"] == "+919812345678"
    assert s["customer_name"] == "Asha R"
    assert s["marketing_consent"] is True  # recorded...
    assert s["item_count"] == 3            # 2 + 1
    assert {li["sku"] for li in s["line_items"]} == {"SG-1", "AC-9"}
    assert s["total_price"] == 2499.0
    # ...but nothing in the summary implies or carries a send action.
    assert "marketing_send" not in s and "notify" not in s


def test_capture_upsert_is_idempotent():
    db = _FakeDb()
    r1 = cc.capture_checkout(db, _PAYLOAD, topic="checkouts/create")
    assert r1["status"] == "captured"

    # A later update for the SAME token must update in place, not duplicate.
    updated = {**_PAYLOAD, "updated_at": "2026-07-22T10:09:00Z", "total_price": "2600.00"}
    r2 = cc.capture_checkout(db, updated, topic="checkouts/update")
    assert r2["status"] == "updated"

    coll = db.get_collection("abandoned_checkouts")
    assert len(coll.docs) == 1
    doc = coll.docs[0]
    assert doc["checkout_token"] == "chk_tok_1"
    assert doc["total_price"] == 2600.0          # latest wins
    assert doc["recovered"] is False             # seeded on insert
    assert "first_seen_at" in doc and "updated_at" in doc


def test_capture_does_not_clobber_recovered_true():
    db = _FakeDb()
    cc.capture_checkout(db, _PAYLOAD, topic="checkouts/create")
    coll = db.get_collection("abandoned_checkouts")
    # Simulate a later order-ingest flipping recovered True (out of scope here).
    coll.docs[0]["recovered"] = True
    cc.capture_checkout(db, _PAYLOAD, topic="checkouts/update")
    assert coll.docs[0]["recovered"] is True     # capture never re-writes it


def test_no_token_is_skipped_and_no_db_simulates():
    assert cc.capture_checkout(_FakeDb(), {"email": "x@y.z"})["status"] == "skipped"
    assert cc.capture_checkout(None, _PAYLOAD)["status"] == "simulated"
