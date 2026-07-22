"""
IMS 2.0 - Shopify ORDER-HISTORY import tests (historical=True bypass)
====================================================================
scripts/import_shopify_order_history.py replays the Shopify back-catalogue through
the SAME api.services.online_order_mapper.map_shopify_order the live webhook path
uses, but with historical=True. These tests pin the load-bearing guarantees of
that new mode:

  Terminal, settled, date-preserving
    - a historical order is CREATED tagged channel='ONLINE' + historical=True, in a
      TERMINAL status (DELIVERED), fully settled (amount_paid==grand_total,
      balance_due==0), carrying its REAL Shopify order date (not import time), and
      with NO fresh GST invoice serial (invoice_number is None -- a back-dated order
      must never consume the live per-FY sequence).

  NO live side effects (the hard safety contract)
    - historical=True fires NONE of: Rx flag-and-hold evaluation, Rx-hold task,
      inventory decrement, oversell write-back, or system-task creation
      (loyalty / messaging have no hook in this path at all).
    - CONTROL: the live path (historical=False) DOES fire those hooks, proving the
      bypass is real and the live webhook path is unchanged.

  Customer LINK, never duplicate
    - an already-imported buyer is MATCHED (by mobile) and the order carries that
      customer_id; NO new customer is minted.
    - an unmatched buyer becomes a guest sale (customer_id None), still no create.

  Idempotent
    - re-importing the same Shopify order does NOT create a 2nd IMS order.

Pure: an in-memory fake DB + a real OrderRepository/CustomerRepository over fake
collections exercise the real _build_invoice_gst_split (no network, no real Mongo).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ["GST_PRICING_MODE"] = "inclusive"
os.environ["ONLINE_STORE_ID"] = "BV-ONLINE-01"

from api.services import online_order_mapper, shopify_ingest


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (only what the mapper + ingest touch).
# ---------------------------------------------------------------------------


class _DuplicateKeyError(Exception):
    pass


def _type_name(val) -> str:
    """Minimal BSON $type emulation for the operators these tests use."""
    from datetime import datetime as _dt

    if isinstance(val, bool):
        return "bool"
    if isinstance(val, str):
        return "string"
    if isinstance(val, _dt):
        return "date"
    if isinstance(val, (int, float)):
        return "number"
    return "object"


def _cmp_ok(actual, op, op_val) -> bool:
    if actual is None:
        # A None field only satisfies negative/exists-false style checks.
        return op in ("$ne", "$nin")
    try:
        if op == "$gte":
            return actual >= op_val
        if op == "$gt":
            return actual > op_val
        if op == "$lte":
            return actual <= op_val
        if op == "$lt":
            return actual < op_val
    except TypeError:
        # Mongo type-bracketing: a Date range never matches a string field (and
        # vice-versa). Mirror that -- an incomparable type simply doesn't match.
        return False
    if op == "$in":
        return actual in op_val
    if op == "$nin":
        return actual not in op_val
    if op == "$ne":
        return actual != op_val
    if op == "$exists":
        return (actual is not None) == bool(op_val)
    if op == "$type":
        return _type_name(actual) == op_val
    return False


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$exists":
                    if (actual is not None) != bool(op_val):
                        return False
                elif not _cmp_ok(actual, op, op_val):
                    return False
        else:
            if actual != expected:
                return False
    return True


class FakeCollection:
    def __init__(self, name: str, database: "FakeDB"):
        self._name = name
        self.database = database
        self.docs: list = []
        self._unique_fields: set = set()

    def create_index(self, keys, **kwargs):
        if kwargs.get("unique") and isinstance(keys, str):
            self._unique_fields.add(keys)
        return None

    def _violates_unique(self, doc) -> bool:
        for f in self._unique_fields:
            val = doc.get(f)
            if val is None:
                continue
            for d in self.docs:
                if d.get(f) == val:
                    return True
        if "_id" in doc:
            for d in self.docs:
                if d.get("_id") == doc.get("_id"):
                    return True
        return False

    def insert_one(self, doc):
        if self._violates_unique(doc):
            raise _DuplicateKeyError("E11000 duplicate key error")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _match(d, filter_):
                return dict(d)
        return None

    def find(self, filter_=None, projection=None):
        return iter([dict(d) for d in self.docs if _match(d, filter_)])

    def count_documents(self, filter_=None):
        return len([d for d in self.docs if _match(d, filter_)])

    def find_one_and_update(self, filter_, update, upsert=False, return_document=None):
        target = None
        for d in self.docs:
            if _match(d, filter_):
                target = d
                break
        if target is None and upsert:
            target = dict(filter_)
            self.docs.append(target)
        if target is None:
            return None
        for op, fields in (update or {}).items():
            if op == "$inc":
                for k, v in fields.items():
                    target[k] = (target.get(k) or 0) + v
            elif op == "$set":
                for k, v in fields.items():
                    target[k] = v
        return dict(target)

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _match(d, filter_):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, name):
        return self.get_collection(name)

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self)
        return self._collections[name]


class Spy:
    """A callable that records whether/when it was invoked."""

    def __init__(self, ret=None):
        self.calls = 0
        self.ret = ret

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.ret


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()

    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository

    order_repo = OrderRepository(db.get_collection("orders"))
    customer_repo = CustomerRepository(db.get_collection("customers"))

    class _StoreRepo:
        def find_by_id(self, _store_id):
            return {"gstin": "", "state_code": "20"}

        def find_active(self, filter=None):
            return [{"store_id": "BV-ONLINE-01", "state_code": "20"}]

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())
    monkeypatch.setattr(deps, "get_customer_repository", lambda: customer_repo)

    return {
        "db": db,
        "orders": db.get_collection("orders"),
        "customers": db.get_collection("customers"),
        "customer_repo": customer_repo,
    }


@pytest.fixture
def spies(monkeypatch):
    """Spy out every live side effect the historical path must NOT trigger."""
    import api.services.online_rx_hold as rx_mod
    import api.services.online_stock_writeback as wb_mod
    import api.services.task_triggers as task_mod

    s = {
        "claim": Spy(ret=(0, [])),
        "writeback": Spy(),
        "rx_eval": Spy(ret={"rx_pending": False, "reasons": [], "lines": [], "detail": ""}),
        "rx_task": Spy(),
        "system_task": Spy(),
    }
    monkeypatch.setattr(shopify_ingest, "_claim_units_multistore", s["claim"])
    monkeypatch.setattr(wb_mod, "writeback_after_sale", s["writeback"])
    monkeypatch.setattr(rx_mod, "evaluate_rx_hold", s["rx_eval"])
    monkeypatch.setattr(rx_mod, "raise_rx_hold_task", s["rx_task"])
    monkeypatch.setattr(task_mod, "create_system_task", s["system_task"])
    return s


def _hist_order(order_id, *, created_at="2023-05-10T09:30:00Z", price="999.00",
                financial="paid", phone="+91 98765 43210", email="buyer@example.com"):
    """A Shopify order payload as pulled from the Admin REST API (paid frame)."""
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "created_at": created_at,
        "financial_status": financial,
        "fulfillment_status": "fulfilled",
        "total_price": price,
        "email": email,
        "phone": phone,
        "customer": {"id": 555, "first_name": "Ravi", "last_name": "Kumar", "phone": phone},
        "shipping_address": {"province": "20", "province_code": "20"},
        "line_items": [
            {
                "id": 9001,
                "product_id": 7001,
                "variant_id": 999001,
                "title": "Ray-Ban Frame RB1234",
                "product_type": "Frames",
                "sku": "RB-1234",
                "quantity": 1,
                "price": price,
                "total_discount": "0.00",
            }
        ],
        # Stamp the online store bucket so attribution is deterministic in the test.
        "_ims_online_store_id": "BV-ONLINE-01",
    }


# ---------------------------------------------------------------------------
# Terminal, settled, date-preserving, no serial
# ---------------------------------------------------------------------------


def test_historical_import_creates_terminal_settled_order(wired):
    res = online_order_mapper.map_shopify_order(
        _hist_order(20001), wired["db"], topic="orders/create", historical=True
    )
    assert res["status"] == "created"
    assert res.get("historical") is True
    assert res.get("invoice_number") is None

    order = wired["orders"].find_one({"shopify_order_id": "20001"})
    assert order is not None
    assert order["channel"] == "ONLINE"
    assert order["source"] == "shopify"
    assert order["historical"] is True
    assert order["import_source"] == "shopify_order_history"
    # Terminal status + settled payment (no phantom receivable).
    assert order["status"] == "DELIVERED"
    assert order["payment_status"] == "PAID"
    assert order["amount_paid"] == order["grand_total"]
    assert order["balance_due"] == 0.0
    # NO fresh GST serial consumed for a back-dated order.
    assert order["invoice_number"] is None
    # Not held.
    assert order["rx_pending"] is False
    assert order["fulfillment_hold"] is False
    # GST-inclusive total is the all-in price the buyer paid; tax math reused.
    assert order["grand_total"] == 999.0
    assert round(order["tax_totals"]["taxable"] + order["tax_totals"]["tax"], 2) == 999.0


def test_historical_preserves_shopify_order_date(wired):
    online_order_mapper.map_shopify_order(
        _hist_order(20002, created_at="2022-11-01T12:00:00Z"),
        wired["db"],
        topic="orders/create",
        historical=True,
    )
    order = wired["orders"].find_one({"shopify_order_id": "20002"})
    # Order date = the Shopify created_at (2022), NOT the import time.
    assert str(order["created_at"]).startswith("2022-11-01")
    assert str(order["invoice_date"]).startswith("2022-11-01")


def test_refunded_history_lands_refunded_status(wired):
    online_order_mapper.map_shopify_order(
        _hist_order(20003, financial="refunded"),
        wired["db"],
        topic="orders/create",
        historical=True,
    )
    order = wired["orders"].find_one({"shopify_order_id": "20003"})
    assert order["status"] == "REFUNDED"
    assert order["payment_status"] == "REFUNDED"


# ---------------------------------------------------------------------------
# NO live side effects (hard safety) + live-path control
# ---------------------------------------------------------------------------


def test_historical_fires_no_side_effects(wired, spies):
    res = online_order_mapper.map_shopify_order(
        _hist_order(20010), wired["db"], topic="orders/create", historical=True
    )
    assert res["status"] == "created"
    # None of the live hooks may fire for a settled back-catalogue order.
    assert spies["rx_eval"].calls == 0, "no Rx flag-and-hold evaluation"
    assert spies["rx_task"].calls == 0, "no Rx-hold follow-up task"
    assert spies["claim"].calls == 0, "no inventory decrement"
    assert spies["writeback"].calls == 0, "no oversell write-back"
    assert spies["system_task"].calls == 0, "no system-task creation"


def test_live_path_still_fires_side_effects(wired, spies):
    # CONTROL: the same order via the LIVE path (historical=False) DOES run the
    # hooks -- proving the historical bypass is real, not a broken code path.
    res = online_order_mapper.map_shopify_order(
        _hist_order(20011), wired["db"], topic="orders/create", historical=False
    )
    assert res["status"] == "created"
    assert spies["rx_eval"].calls >= 1, "live path evaluates Rx flag-and-hold"
    assert spies["writeback"].calls >= 1, "live path pushes stock write-back"


# ---------------------------------------------------------------------------
# Customer LINK, never duplicate
# ---------------------------------------------------------------------------


def test_historical_links_existing_customer_without_duplicate(wired):
    wired["customer_repo"].create(
        {"customer_id": "CUST-IMPORTED", "name": "Ravi K", "mobile": "9876543210"}
    )
    before = len(wired["customers"].docs)

    res = online_order_mapper.map_shopify_order(
        _hist_order(20020), wired["db"], topic="orders/create", historical=True
    )
    assert res["customer_id"] == "CUST-IMPORTED"
    order = wired["orders"].find_one({"shopify_order_id": "20020"})
    assert order["customer_id"] == "CUST-IMPORTED"
    assert order.get("is_guest_order") is False
    assert len(wired["customers"].docs) == before, "no duplicate customer minted"


def test_historical_unmatched_buyer_is_guest_no_customer_created(wired):
    # A buyer with no matching IMS customer + no usable Indian mobile / email.
    payload = _hist_order(20021, phone="", email="")
    payload["customer"] = {"id": 999, "first_name": "Foreign", "last_name": "Buyer"}
    before = len(wired["customers"].docs)

    res = online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    assert res["status"] == "created"
    assert res["customer_id"] is None
    order = wired["orders"].find_one({"shopify_order_id": "20021"})
    assert order["customer_id"] is None
    assert order["is_guest_order"] is True
    assert len(wired["customers"].docs) == before, "historical import never creates a customer"


def test_historical_matches_by_shopify_customer_id(wired):
    # The import stamped shopify_customer_id on the customer; a buyer with a
    # non-Indian phone still links via that id.
    wired["customers"].insert_one(
        {"customer_id": "CUST-SHOP", "name": "X", "shopify_customer_id": "555"}
    )
    before = len(wired["customers"].docs)
    payload = _hist_order(20022, phone="+1 415 555 0000", email="")
    res = online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    assert res["customer_id"] == "CUST-SHOP"
    assert len(wired["customers"].docs) == before


# ---------------------------------------------------------------------------
# Idempotent
# ---------------------------------------------------------------------------


def test_historical_reimport_is_idempotent(wired):
    first = online_order_mapper.map_shopify_order(
        _hist_order(20030), wired["db"], topic="orders/create", historical=True
    )
    assert first["status"] == "created"
    second = online_order_mapper.map_shopify_order(
        _hist_order(20030), wired["db"], topic="orders/create", historical=True
    )
    assert second["status"] == "duplicate"
    online = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "20030"]
    assert len(online) == 1, "count-once: still exactly one IMS order"
    # The historical order's terminal status is NOT re-synced by a replay.
    assert online[0]["status"] == "DELIVERED"


# ---------------------------------------------------------------------------
# P0 -- created_at / invoice_date persist as BSON DATETIMES (not ISO strings),
# so a date-windowed finance/GST query actually matches the order. A STRING
# created_at never matches a Date-typed $gte/$lt range (Mongo type bracketing).
# ---------------------------------------------------------------------------


def _in_window(coll, lo, hi):
    """Orders whose datetime created_at falls in [lo, hi) -- exactly how every
    finance/GST window queries (naive-UTC datetime bounds)."""
    return list(coll.find({"created_at": {"$gte": lo, "$lt": hi}}))


def test_historical_created_at_is_bson_datetime_and_matches_window(wired):
    online_order_mapper.map_shopify_order(
        _hist_order(21001, created_at="2023-05-10T09:30:00Z"),
        wired["db"], topic="orders/create", historical=True,
    )
    order = wired["orders"].find_one({"shopify_order_id": "21001"})
    # created_at / invoice_date are datetimes, NOT strings.
    assert isinstance(order["created_at"], datetime)
    assert isinstance(order["invoice_date"], datetime)
    assert isinstance(order["updated_at"], datetime)
    # naive-UTC (tz stripped) so it compares with the finance/GST naive bounds.
    assert order["created_at"].tzinfo is None
    assert order["created_at"] == datetime(2023, 5, 10, 9, 30, 0)

    coll = wired["orders"]
    # In-period window MATCHES (the whole point of the fix).
    hit = _in_window(coll, datetime(2023, 5, 1), datetime(2023, 6, 1))
    assert any(o.get("shopify_order_id") == "21001" for o in hit)
    # Out-of-period window does NOT match.
    miss = _in_window(coll, datetime(2024, 1, 1), datetime(2024, 2, 1))
    assert not any(o.get("shopify_order_id") == "21001" for o in miss)


def test_live_order_created_at_is_bson_datetime_and_matches_window(wired, spies):
    # The date-type fix applies to the LIVE webhook path too: a live online order
    # must be datetime-dated and visible to a date-windowed query.
    res = online_order_mapper.map_shopify_order(
        _hist_order(21002), wired["db"], topic="orders/create", historical=False
    )
    assert res["status"] == "created"
    order = wired["orders"].find_one({"shopify_order_id": "21002"})
    assert isinstance(order["created_at"], datetime)
    assert isinstance(order["invoice_date"], datetime)
    assert order["created_at"].tzinfo is None

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hit = _in_window(
        wired["orders"], now - timedelta(days=1), now + timedelta(days=1)
    )
    assert any(o.get("shopify_order_id") == "21002" for o in hit)


# ---------------------------------------------------------------------------
# P3 -- a settled payment row (cross-check payments_collected coherence) and
# partially_paid label consistency.
# ---------------------------------------------------------------------------


def test_historical_settled_payment_row_present(wired):
    online_order_mapper.map_shopify_order(
        _hist_order(21010), wired["db"], topic="orders/create", historical=True
    )
    order = wired["orders"].find_one({"shopify_order_id": "21010"})
    payments = order.get("payments") or []
    assert len(payments) == 1, "one settled payment row for cross-check coherence"
    assert payments[0]["amount"] == order["grand_total"]
    assert payments[0].get("settled_outside_ims") is True


def test_partially_paid_history_labeled_paid_not_partial(wired):
    # partially_paid is booked as fully SETTLED (balance_due 0, amount_paid=grand),
    # so its label must be PAID -- PARTIAL with a zero balance was inconsistent.
    online_order_mapper.map_shopify_order(
        _hist_order(21011, financial="partially_paid"),
        wired["db"], topic="orders/create", historical=True,
    )
    order = wired["orders"].find_one({"shopify_order_id": "21011"})
    assert order["payment_status"] == "PAID"
    assert order["balance_due"] == 0.0
    assert order["amount_paid"] == order["grand_total"]


# ---------------------------------------------------------------------------
# P1 -- refunded / partially_refunded orders synthesize GST credit notes (CDNR)
# so GSTR-1 nets them; idempotent; and a FUTURE refund webhook against an
# import-sourced order is no longer permanently skipped.
# ---------------------------------------------------------------------------


def _refund_line(line_item_id=9001, qty=1, subtotal="999.00", total_tax="0.00"):
    return {
        "line_item_id": line_item_id,
        "quantity": qty,
        "subtotal": subtotal,
        "total_tax": total_tax,
    }


def test_refunded_history_synthesizes_full_credit_note(wired):
    # Fully refunded, no per-line refund detail -> reverse the WHOLE order so it
    # never books as full revenue. A credit_note_ledger CDNR entry is written.
    online_order_mapper.map_shopify_order(
        _hist_order(21020, financial="refunded"),
        wired["db"], topic="orders/create", historical=True,
    )
    ledger = [d for d in wired["db"].get_collection("credit_note_ledger").docs]
    mine = [d for d in ledger if d.get("shopify_refund_id", "").startswith("orderfull:")]
    assert len(mine) == 1, "one whole-order credit note for a full refund"
    cn = mine[0]
    assert cn["type"] == "ISSUED"
    assert cn["settlement"] == "EXTERNAL"
    assert cn["delta"] == 0.0, "no redeemable store credit minted"
    assert cn["gross_refund"] == 999.0, "reverses the full order gross"
    assert isinstance(cn["created_at"], str), "CDNR reads ISO-string created_at"
    # A returns doc is stamped so a future webhook is recognised as credited.
    returns = wired["db"].get_collection("returns").docs
    assert any(r.get("credit_note_issued") is True for r in returns)


def test_partially_refunded_history_credit_note_from_lines(wired):
    payload = _hist_order(21021, financial="partially_refunded")
    payload["refunds"] = [
        {"id": 88021, "created_at": "2023-06-01T10:00:00Z",
         "refund_line_items": [_refund_line()]}
    ]
    online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    mine = [d for d in ledger if d.get("shopify_refund_id") == "88021"]
    assert len(mine) == 1
    assert mine[0]["ref"] == "SHOPIFY-HIST-REFUND-88021"
    assert mine[0]["gross_refund"] > 0
    # Credit-note date = the refund's date (correct GST period), not the sale date.
    assert str(mine[0]["created_at"]).startswith("2023-06-01")


def test_historical_refund_credit_note_is_idempotent(wired):
    payload = _hist_order(21022, financial="partially_refunded")
    payload["refunds"] = [{"id": 88022, "refund_line_items": [_refund_line()]}]
    online_order_mapper.map_shopify_order(
        dict(payload), wired["db"], topic="orders/create", historical=True
    )
    # Re-import the same order -> order-level dedupe + ledger ref guard both hold.
    online_order_mapper.map_shopify_order(
        dict(payload), wired["db"], topic="orders/create", historical=True
    )
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    mine = [d for d in ledger if d.get("shopify_refund_id") == "88022"]
    assert len(mine) == 1, "credit note booked exactly once on re-import"


def test_historical_refund_scaled_to_actual_shopify_amount(wired):
    # Rs 999 billed line but Shopify ACTUALLY refunded only Rs 200 (goodwill /
    # partial-amount refund). The CN must book Rs 200 -- never the billed gross
    # (over-reversing output GST on the Rs 799 the customer kept paying for).
    payload = _hist_order(21040, financial="partially_refunded")
    payload["refunds"] = [
        {
            "id": 88040,
            "created_at": "2023-06-02T10:00:00Z",
            "refund_line_items": [_refund_line()],
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "200.00"}
            ],
        }
    ]
    online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    mine = [d for d in ledger if d.get("shopify_refund_id") == "88040"]
    assert len(mine) == 1
    cn = mine[0]
    assert cn["gross_refund"] == 200.0, "books the ACTUAL refunded amount"
    assert cn["billed_gross"] == 999.0, "pre-scale figure kept for audit"
    assert cn["reconciliation"] == "SCALED_TO_SHOPIFY_REFUNDED"
    assert round(cn["taxable"] + cn["tax"], 2) == 200.0, "split scales with it"
    assert cn["delta"] == 0.0


def test_historical_refund_clamped_when_actual_exceeds_billed(wired):
    # Shopify's refunded total INCLUDES shipping/goodwill the import never books
    # as sale revenue (line items only), so actual > billed is SYSTEMATIC for
    # shipping-charged orders. A credit note can never exceed the original
    # invoice value: the CN is CLAMPED at the billed gross (full reversal of
    # what was booked) and the excess is surfaced as an unreconciled residual.
    payload = _hist_order(21044, financial="partially_refunded")
    payload["refunds"] = [
        {
            "id": 88044,
            "created_at": "2023-06-03T10:00:00Z",
            "refund_line_items": [_refund_line()],
            # Rs 999 goods + Rs 151 shipping refunded via the gateway.
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "1150.00"}
            ],
        }
    ]
    online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    mine = [d for d in ledger if d.get("shopify_refund_id") == "88044"]
    assert len(mine) == 1
    cn = mine[0]
    assert cn["gross_refund"] == 999.0, "CN never exceeds the billed gross"
    assert cn["billed_gross"] == 999.0
    assert cn["reconciliation"] == "CLAMPED_AT_BILLED_GROSS"
    assert cn["unreconciled_excess"] == 151.0, "shipping/goodwill excess surfaced"
    assert round(cn["taxable"] + cn["tax"], 2) == 999.0, "split NOT scaled up"


def test_cn_failure_reported_and_healed_on_rerun(wired):
    # Run 1: the ledger insert fails on a transient error -> the failure is
    # COUNTED in the summary (cn_failed), not silently dropped. Run 2 (the
    # order now dedupes as 'duplicate'): the idempotent synthesizer re-runs on
    # the duplicate path and HEALS the missing GST reversal.
    ledger = wired["db"].get_collection("credit_note_ledger")
    orig_insert = ledger.insert_one

    def _flaky_insert(doc):
        raise RuntimeError("transient mongo blip")

    ledger.insert_one = _flaky_insert
    payload = _hist_order(21045, financial="partially_refunded")
    payload["refunds"] = [{"id": 88045, "refund_line_items": [_refund_line()]}]
    try:
        res1 = online_order_mapper.map_shopify_order(
            dict(payload), wired["db"], topic="orders/create", historical=True
        )
    finally:
        ledger.insert_one = orig_insert
    assert res1["status"] == "created"
    rcn1 = res1["refund_credit_notes"]
    assert rcn1["credit_notes"] == 0
    assert rcn1["cn_failed"] == 1, "failed booking is REPORTED, not swallowed"
    assert rcn1["cn_failed_refund_ids"] == ["88045"]
    assert ledger.docs == [], "nothing landed on the failed run"

    # Re-run: duplicate order path still invokes the idempotent CN synthesizer.
    res2 = online_order_mapper.map_shopify_order(
        dict(payload), wired["db"], topic="orders/create", historical=True
    )
    assert res2["status"] == "duplicate"
    rcn2 = res2.get("refund_credit_notes") or {}
    assert rcn2.get("credit_notes") == 1, "re-run heals the missing credit note"
    assert rcn2.get("cn_failed") == 0
    mine = [d for d in ledger.docs if d.get("shopify_refund_id") == "88045"]
    assert len(mine) == 1, "healed exactly once"

    # Run 3: nothing left to heal -- no double booking.
    res3 = online_order_mapper.map_shopify_order(
        dict(payload), wired["db"], topic="orders/create", historical=True
    )
    assert res3["status"] == "duplicate"
    assert (res3.get("refund_credit_notes") or {}).get("credit_notes") == 0
    assert len([d for d in ledger.docs if d.get("shopify_refund_id") == "88045"]) == 1


def test_heal_rerun_never_stacks_whole_order_cn_on_prior_per_refund_cn(wired):
    # ROUND-4 P0 regression: mixed-shape fully-refunded order -- refund A is
    # line-mappable (books a per-refund CN on run 1), refund B is amount-only
    # (residual). On the heal re-run A dedupes ('duplicate') so the fresh
    # summary's credit_notes stays 0 -- the whole-order fallback gate must be
    # duplicate-aware and NOT fire, or it would stack a full whole-order CN on
    # top of run 1's per-refund CN (total reversal > invoice, GST-illegal).
    payload = _hist_order(21047, financial="refunded")
    payload["refunds"] = [
        {
            "id": 88048,
            "created_at": "2023-09-01T10:00:00Z",
            "refund_line_items": [_refund_line()],
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "799.00"}
            ],
        },
        {
            "id": 88049,
            "created_at": "2023-09-02T10:00:00Z",
            "refund_line_items": [],
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "200.00"}
            ],
        },
    ]
    # Run 1 (create branch): A books a scaled Rs 799 CN; B stays a residual;
    # the fallback is suppressed because credit_notes == 1.
    res1 = online_order_mapper.map_shopify_order(
        dict(payload), wired["db"], topic="orders/create", historical=True
    )
    rcn1 = res1["refund_credit_notes"]
    assert rcn1["credit_notes"] == 1
    assert rcn1["unmapped_refunds"] == 1
    assert "whole_order_fallback" not in rcn1

    # Run 2 (duplicate -> heal): A returns 'duplicate' -> the fallback gate
    # must see duplicate_refunds > 0 and book NOTHING.
    res2 = online_order_mapper.map_shopify_order(
        dict(payload), wired["db"], topic="orders/create", historical=True
    )
    assert res2["status"] == "duplicate"
    rcn2 = res2["refund_credit_notes"]
    assert rcn2["credit_notes"] == 0, "heal re-run books NOTHING"
    assert rcn2["duplicate_refunds"] == 1
    assert "whole_order_fallback" not in rcn2
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    order_cns = [
        d for d in ledger
        if d.get("shopify_refund_id") in ("88048", "88049")
        or str(d.get("shopify_refund_id", "")).startswith("orderfull:21047")
    ]
    assert not any(
        str(d.get("shopify_refund_id", "")).startswith("orderfull:")
        for d in order_cns
    ), "no whole-order CN stacked on the per-refund CN"
    total_reversed = round(
        sum(float(d.get("gross_refund") or 0) for d in order_cns), 2
    )
    assert total_reversed == 799.0
    assert total_reversed <= 999.0, "total reversal never exceeds the invoice"


def test_cn_failed_gates_fallback_so_heal_never_stacks(wired):
    # ROUND-5 P1 regression (chair-reproduced): mixed-shape fully-refunded
    # order where refund A's ledger insert fails TRANSIENTLY on run 1
    # ('skipped', cn_failed=1) while refund B is amount-only. Without the
    # cn_failed gate, run 1 books the whole-order CN at the FULL actual total
    # (which sums failed A too) and the promised run-2 heal then stacks A's
    # per-refund CN on top: Rs 1,798 reversed vs a Rs 999 invoice. The gate
    # must defer the WHOLE decision to the clean re-run.
    ledger = wired["db"].get_collection("credit_note_ledger")
    orig_insert = ledger.insert_one

    def _fail_only_refund_a(doc):
        if doc.get("shopify_refund_id") == "88051":
            raise RuntimeError("transient mongo blip on refund A only")
        return orig_insert(doc)

    payload = _hist_order(21049, financial="refunded")
    payload["refunds"] = [
        {
            "id": 88051,
            "created_at": "2023-11-01T10:00:00Z",
            "refund_line_items": [_refund_line()],
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "799.00"}
            ],
        },
        {
            "id": 88052,
            "created_at": "2023-11-02T10:00:00Z",
            "refund_line_items": [],
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "200.00"}
            ],
        },
    ]
    ledger.insert_one = _fail_only_refund_a
    try:
        res1 = online_order_mapper.map_shopify_order(
            dict(payload), wired["db"], topic="orders/create", historical=True
        )
    finally:
        ledger.insert_one = orig_insert
    rcn1 = res1["refund_credit_notes"]
    assert rcn1["cn_failed"] == 1
    assert rcn1["cn_failed_refund_ids"] == ["88051"]
    assert rcn1["credit_notes"] == 0, "run 1 books NOTHING (fallback gated)"
    assert "whole_order_fallback" not in rcn1, "fallback deferred to the heal"
    assert ledger.docs == []

    # Clean re-run (the promised heal): A books its per-refund CN; credit_notes
    # > 0 then suppresses the fallback, B stays a reported residual.
    res2 = online_order_mapper.map_shopify_order(
        dict(payload), wired["db"], topic="orders/create", historical=True
    )
    assert res2["status"] == "duplicate"
    rcn2 = res2["refund_credit_notes"]
    assert rcn2["credit_notes"] == 1
    assert rcn2["cn_failed"] == 0
    assert rcn2["unmapped_refunds"] == 1, "B stays a reported residual"
    assert "whole_order_fallback" not in rcn2

    order_cns = [
        d for d in ledger.docs
        if d.get("shopify_refund_id") in ("88051", "88052")
        or str(d.get("shopify_refund_id", "")).startswith("orderfull:21049")
    ]
    assert not any(
        str(d.get("shopify_refund_id", "")).startswith("orderfull:")
        for d in order_cns
    ), "no orderfull CN coexists with a per-refund CN"
    total_reversed = round(
        sum(float(d.get("gross_refund") or 0) for d in order_cns), 2
    )
    assert total_reversed == 799.0
    assert total_reversed <= 999.0, "total reversal never exceeds the invoice"


def test_stamp_failure_healed_on_rerun_via_duplicate_fallback(wired):
    # ROUND-4 rider: run 1 books the whole-order CN but the covered-stamp
    # inserts silently fail (fail-soft). The heal re-run's fallback returns
    # 'duplicate' (CN already booked) -- the stamp block must STILL run so the
    # missing dedupe stamps are healed and the phantom residual clears.
    returns_coll = wired["db"].get_collection("returns")
    orig_insert = returns_coll.insert_one

    def _broken_insert(doc):
        raise RuntimeError("transient stamp failure")

    returns_coll.insert_one = _broken_insert
    payload = _hist_order(21048, financial="refunded")
    payload["refunds"] = [
        {
            "id": 88050,
            "created_at": "2023-10-01T10:00:00Z",
            "refund_line_items": [],
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "999.00"}
            ],
        }
    ]
    try:
        res1 = online_order_mapper.map_shopify_order(
            dict(payload), wired["db"], topic="orders/create", historical=True
        )
    finally:
        returns_coll.insert_one = orig_insert
    rcn1 = res1["refund_credit_notes"]
    assert rcn1["credit_notes"] == 1
    assert rcn1.get("whole_order_fallback") is True
    assert not any(
        r.get("shopify_refund_id") == "88050" for r in returns_coll.docs
    ), "stamp silently failed on run 1"

    # Heal re-run: fallback synthetic dedupes on the existing orderfull CN, but
    # the covered-stamp block still runs and repairs the missing stamp.
    res2 = online_order_mapper.map_shopify_order(
        dict(payload), wired["db"], topic="orders/create", historical=True
    )
    assert res2["status"] == "duplicate"
    rcn2 = res2["refund_credit_notes"]
    assert rcn2["credit_notes"] == 0, "no second whole-order CN"
    assert "whole_order_fallback" not in rcn2, "flag only set on a fresh booking"
    assert rcn2["unmapped_refunds"] == 0, "covered residual clears on the heal"
    assert any(
        r.get("shopify_refund_id") == "88050" for r in returns_coll.docs
    ), "missing stamp healed"
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    full = [
        d for d in ledger
        if str(d.get("shopify_refund_id", "")).startswith("orderfull:21048")
    ]
    assert len(full) == 1, "still exactly one whole-order CN"

    # And the healed stamp makes a webhook redelivery dedupe.
    from api.services import shopify_refund

    res_wh = shopify_refund.handle_shopify_refund(
        wired["db"],
        {"id": 88050, "order_id": "21048", "refund_line_items": []},
        topic="refunds/create",
    )
    assert res_wh["status"] == "duplicate", res_wh


def test_zero_gross_and_amount_only_refunds_both_covered_by_whole_order(wired):
    # fin=refunded with one AMOUNT-ONLY refund (no line items) and one
    # ZERO-GROSS refund (maps to a free/zero-priced line): the whole-order
    # reversal covers BOTH -- both ids are stamped into returns (webhook
    # redelivery dedupes; no double credit on top of the reversal) and the
    # residual count reconciles to zero.
    payload = _hist_order(21046, financial="refunded")
    payload["line_items"].append(
        {
            "id": 9002,
            "product_id": 7002,
            "variant_id": 999002,
            "title": "Free cleaning kit",
            "product_type": "Accessories",
            "sku": "KIT-0",
            "quantity": 1,
            "price": "0.00",
            "total_discount": "0.00",
        }
    )
    payload["refunds"] = [
        {
            "id": 88046,
            "created_at": "2023-08-01T10:00:00Z",
            "refund_line_items": [],
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "999.00"}
            ],
        },
        {
            "id": 88047,
            "created_at": "2023-08-02T10:00:00Z",
            "refund_line_items": [
                _refund_line(line_item_id=9002, subtotal="0.00", total_tax="0.00")
            ],
        },
    ]
    res = online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    rcn = res["refund_credit_notes"]
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    full = [
        d for d in ledger
        if str(d.get("shopify_refund_id", "")).startswith("orderfull:")
    ]
    assert len(full) == 1, "whole-order reversal booked"
    assert full[0]["gross_refund"] == 999.0
    assert rcn["whole_order_fallback"] is True
    assert rcn["unmapped_refunds"] == 0, "both residuals covered by the reversal"
    # BOTH refund ids stamped -> webhook redeliveries dedupe.
    returns = wired["db"].get_collection("returns").docs
    stamped = {r.get("shopify_refund_id") for r in returns}
    assert "88046" in stamped and "88047" in stamped

    from api.services import shopify_refund

    for rid in (88046, 88047):
        res_wh = shopify_refund.handle_shopify_refund(
            wired["db"],
            {"id": rid, "order_id": "21046", "refund_line_items": []},
            topic="refunds/create",
        )
        assert res_wh["status"] == "duplicate", res_wh


def test_fully_refunded_amount_only_refunds_fall_through_to_whole_order(wired):
    # financial_status='refunded' with refunds[] PRESENT but every entry
    # amount-only (empty refund_line_items -- Shopify 'refund custom amount'
    # shape) previously booked ZERO credit notes while the full sale stayed in
    # GSTR-1. It must fall through to the whole-order reversal, reconciled
    # against what Shopify actually refunded.
    payload = _hist_order(21041, financial="refunded")
    payload["refunds"] = [
        {
            "id": 88041,
            "created_at": "2023-07-01T10:00:00Z",
            "refund_line_items": [],
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "999.00"}
            ],
        }
    ]
    online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    full = [
        d for d in ledger
        if str(d.get("shopify_refund_id", "")).startswith("orderfull:")
    ]
    assert len(full) == 1, "whole-order reversal booked for the amount-only case"
    assert full[0]["gross_refund"] == 999.0
    # CDNR period = the refund's own date, not the sale date.
    assert str(full[0]["created_at"]).startswith("2023-07-01")
    # The covered amount-only refund id is STAMPED so a webhook redelivery of
    # that same refund dedupes instead of double-crediting.
    returns = wired["db"].get_collection("returns").docs
    assert any(r.get("shopify_refund_id") == "88041" for r in returns)

    from api.services import shopify_refund

    res = shopify_refund.handle_shopify_refund(
        wired["db"],
        {"id": 88041, "order_id": "21041", "refund_line_items": []},
        topic="refunds/create",
    )
    assert res["status"] == "duplicate", res


def test_historical_cn_carries_gstn_legal_note_number(wired):
    payload = _hist_order(21042, financial="partially_refunded")
    payload["refunds"] = [
        {"id": 8804212345678, "refund_line_items": [_refund_line()]}
    ]
    online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    ledger = wired["db"].get_collection("credit_note_ledger").docs
    mine = [d for d in ledger if d.get("shopify_refund_id") == "8804212345678"]
    assert len(mine) == 1
    cn = mine[0]
    # Dedicated GSTN-legal note number; the internal idempotency ref UNCHANGED.
    assert cn["note_number"].startswith("CNH-") and len(cn["note_number"]) <= 16
    assert cn["ref"] == "SHOPIFY-HIST-REFUND-8804212345678"


def test_offset_timestamp_converts_to_utc_and_lands_in_correct_ist_month(wired):
    # Shopify REST emits +05:30 offsets for an IST shop. 2023-04-01T01:00 IST is
    # 2023-03-31T19:30 UTC -- INSIDE April's IST GST window (which opens at
    # 2023-03-31T18:30 UTC), so the order must file in April, not March. A naive
    # 'strip the offset' regression would ship silently without this test.
    from datetime import date

    from api.utils.ist import ist_day_start_utc

    online_order_mapper.map_shopify_order(
        _hist_order(21043, created_at="2023-04-01T01:00:00+05:30"),
        wired["db"], topic="orders/create", historical=True,
    )
    order = wired["orders"].find_one({"shopify_order_id": "21043"})
    assert order["created_at"] == datetime(2023, 3, 31, 19, 30, 0), (
        "true offset-to-UTC conversion (not an offset strip)"
    )
    apr_start = ist_day_start_utc(date(2023, 4, 1))
    may_start = ist_day_start_utc(date(2023, 5, 1))
    assert apr_start <= order["created_at"] < may_start, (
        "order falls inside the April IST GST window"
    )
    # And NOT inside March's.
    mar_start = ist_day_start_utc(date(2023, 3, 1))
    assert not (mar_start <= order["created_at"] < apr_start)


def test_future_refund_webhook_against_history_import_not_skipped(wired):
    # Book a historical order, then a NEW real refund (absent at import) arrives by
    # webhook. It must NOT be permanently skipped -- it is booked (review queue).
    from api.services import shopify_refund

    online_order_mapper.map_shopify_order(
        _hist_order(21030, financial="paid"),
        wired["db"], topic="orders/create", historical=True,
    )
    refund_payload = {
        "id": 99030,
        "order_id": "21030",
        "refund_line_items": [_refund_line()],
    }
    res = shopify_refund.handle_shopify_refund(
        wired["db"], refund_payload, topic="refunds/create"
    )
    assert res["status"] != "skipped", res
    assert res.get("reason") != "historical_import_order"
    # Default posture is the accountant review queue (no auto-post).
    assert res["status"] == "queued"


def test_refund_already_booked_at_import_is_duplicate_on_webhook(wired):
    # A refund present at import time is credited + stamped, so the same refund id
    # arriving later by webhook is recognised as a duplicate (no double credit).
    from api.services import shopify_refund

    payload = _hist_order(21031, financial="partially_refunded")
    payload["refunds"] = [{"id": 88031, "refund_line_items": [_refund_line()]}]
    online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    res = shopify_refund.handle_shopify_refund(
        wired["db"],
        {"id": 88031, "order_id": "21031", "refund_line_items": [_refund_line()]},
        topic="refunds/create",
    )
    assert res["status"] == "duplicate", res


# ---------------------------------------------------------------------------
# GSTR-1 invoice number: the REAL minted serial ALWAYS wins verbatim; only the
# fallback tiers (invoice_number None -- historical imports) are 16-char-gated
# and never fall through to the 36-char order_id UUID.
# ---------------------------------------------------------------------------


def test_gstr1_bill_number_real_minted_serial_always_wins_verbatim():
    from api.routers.reports import _gstr1_bill_number

    # The system's OWN minted serial format ({PREFIX}/{STORE}/{FY}/{serial},
    # 20 chars -- pinned by test_orders_numbering) must be returned VERBATIM:
    # GSTR-1 files the number of the tax invoice actually issued; substituting a
    # UUID fragment breaks B2B recipients' GSTR-2A/2B matching.
    real = "BV/BOK-01/26-27/0001"
    assert len(real) == 20
    uuid_like = "b1f2c3d4-e5f6-7890-abcd-ef1234567890"
    got = _gstr1_bill_number(
        {
            "invoice_number": real,
            "order_number": "ORD-BOK01-2026-A1B2C3",
            "order_id": uuid_like,
        }
    )
    assert got == real
    # A live ONLINE serial (23 chars) also wins over the Shopify name.
    online = "BV/ONLINE-01/26-27/0001"
    assert _gstr1_bill_number(
        {"invoice_number": online, "shopify_order_name": "#1001"}
    ) == online
    # An over-cap serial is FLAGGED as a validation issue, never substituted.
    issues: list = []
    assert _gstr1_bill_number({"invoice_number": real}, issues) == real
    assert len(issues) == 1
    assert issues[0]["invoice"] == real
    assert "16" in issues[0]["issue"]
    assert issues[0]["issue_code"] == "INVOICE_SERIAL_OVER_16"
    assert issues[0]["count"] == 1
    # DEDUPED: further over-cap orders AGGREGATE into the SAME issue (count
    # bumps) instead of appending one warn per order -- per-order warns would
    # flood issues[:50] and bury the genuinely actionable warnings.
    assert _gstr1_bill_number({"invoice_number": online}, issues) == online
    assert _gstr1_bill_number({"invoice_number": real}, issues) == real
    assert len(issues) == 1, "one aggregated issue, not one per order"
    assert issues[0]["count"] == 3
    # A short real serial wins with NO issue raised.
    issues2: list = []
    assert _gstr1_bill_number({"invoice_number": "INV-0007"}, issues2) == "INV-0007"
    assert issues2 == []


def test_gstr1_bill_number_falls_back_to_order_number_not_uuid():
    from api.routers.reports import _gstr1_bill_number

    uuid_like = "b1f2c3d4-e5f6-7890-abcd-ef1234567890"  # 36 chars
    # invoice_number EXISTS as None (historical) -> order_number, NOT the UUID.
    got = _gstr1_bill_number(
        {"invoice_number": None, "order_number": "ONL-123", "order_id": uuid_like}
    )
    assert got == "ONL-123"
    assert got != uuid_like and len(got) <= 16
    # order_number too long (ONL-<13-digit> = 17) -> the short Shopify name.
    got2 = _gstr1_bill_number(
        {
            "invoice_number": None,
            "order_number": "ONL-1234567890123",
            "shopify_order_name": "#1001",
            "order_id": uuid_like,
        }
    )
    assert got2 == "1001" and len(got2) <= 16
    # Last resort: never emit a 36-char UUID -> capped to 16.
    got3 = _gstr1_bill_number({"invoice_number": None, "order_id": uuid_like})
    assert len(got3) <= 16 and got3 != uuid_like


def test_gstr1_bill_number_fallbacks_prefix_storefront_and_sanitize_charset():
    from api.routers.reports import _gstr1_bill_number

    uuid_like = "b1f2c3d4-e5f6-7890-abcd-ef1234567890"
    # Bare Shopify-name numerics are prefixed per storefront: BV and WizOpt
    # names both start at #1001 and bill under ONE GSTIN -> 'BV-1001'/'WO-1001'
    # pre-empts the same-FY doc-number collision.
    base = {
        "invoice_number": None,
        "order_number": "ONL-1234567890123",  # 17 chars -> fails the gate
        "shopify_order_name": "#1001",
        "order_id": uuid_like,
    }
    assert _gstr1_bill_number({**base, "store_id": "BV-ONLINE-01"}) == "BV-1001"
    assert _gstr1_bill_number({**base, "store_id": "WO-ONLINE-01"}) == "WO-1001"
    # GSTN charset: fallback tiers are sanitized to [A-Za-z0-9/-].
    got = _gstr1_bill_number(
        {
            "invoice_number": None,
            "shopify_order_name": "#10 01*A",
            "order_id": uuid_like,
        }
    )
    assert got == "1001A"


def test_hist_cn_note_number_and_cdnr_resolver_are_gstn_legal():
    from api.routers.reports import _cdnr_note_number
    from api.services.shopify_ingest import _hist_cn_note_number

    # Deterministic, <=16 chars, GSTN charset, for both real and pseudo ids.
    n1 = _hist_cn_note_number("8804212345678")
    assert n1.startswith("CNH-") and len(n1) <= 16
    assert n1 == _hist_cn_note_number("8804212345678"), "deterministic"
    n2 = _hist_cn_note_number("orderfull:1234567890123")
    assert n2.startswith("CNH-") and len(n2) <= 16
    # The CDNR resolver PREFERS the dedicated note_number...
    assert _cdnr_note_number(
        {"note_number": n1, "ref": "SHOPIFY-HIST-REFUND-8804212345678"}
    ) == n1
    # ...and caps legacy refs (no note_number) at the GSTN 16-char limit.
    legacy = _cdnr_note_number({"ref": "SHOPIFY-HIST-REFUND-8804212345678"})
    assert len(legacy) <= 16
    assert len(_cdnr_note_number({"ref": "RET-250415-ABC123"})) <= 16
