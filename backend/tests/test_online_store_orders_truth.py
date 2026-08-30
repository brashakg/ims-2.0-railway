"""
IMS 2.0 - Online Orders list TRUTH (HTTP layer)  -- online-screens audit RC-H
=============================================================================
Exercises routers/online_store_orders.py via a FastAPI TestClient with a real JWT
+ monkeypatched fake DB:

  * OS-011: webhook_inbox order payloads with NO matching orders doc are merged
    into the list as map_status=FAILED (processed, never booked -> with an honest
    map_error) / PENDING (received, not yet drained); booked orders are excluded;
    a remap whose mapper result is 'skipped' reports ok=false / map_status=FAILED
    (it used to be indistinguishable from success).
  * OS-012: rx_pending / fulfillment_hold are returned on list rows, and
    POST /{order_id}/clear-rx-hold releases the hold (gated, audited, 409 when
    no hold is active).
  * OS-044: ?search= filters server-side (booked + failed rows).
  * OS-063: list rows are server-projected -- items collapse to items_count and
    payments / tax tables never reach the client.
  * The store-scope IDOR guard stays intact: a store-scoped ACCOUNTANT neither
    sees other stores' booked orders nor the (store-unstamped) FAILED queue.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import online_store_orders as orders_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api import dependencies as deps  # noqa: E402
from api.services import online_order_mapper as mapper_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal fakes ($and / $or / $in / $regex aware -- the list query uses them)
# ---------------------------------------------------------------------------


def _match(doc, filter_):
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$and":
            if not all(_match(doc, sub) for sub in expected):
                return False
            continue
        if k == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            if "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif "$regex" in expected:
                flags = re.I if "i" in str(expected.get("$options") or "") else 0
                if actual is None or not re.search(
                    str(expected["$regex"]), str(actual), flags
                ):
                    return False
            elif "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
            else:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=-1):
        try:
            self._docs = sorted(
                self._docs,
                key=lambda d: str(d.get(key) or ""),
                reverse=(direction == -1),
            )
        except Exception:  # noqa: BLE001
            pass
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter([dict(d) for d in self._docs])


class _Coll:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": 1})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _match(d, filter_):
                return dict(d)
        return None

    def find(self, filter_=None, projection=None):
        return _Cursor([d for d in self.docs if _match(d, filter_)])

    def count_documents(self, filter_=None):
        return len([d for d in self.docs if _match(d, filter_)])

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _match(d, filter_):
                for kk, vv in (update.get("$set") or {}).items():
                    d[kk] = vv
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


class _DB:
    is_connected = True

    def __init__(self):
        self._c = {}
        self.db = self

    def get_collection(self, name):
        if name not in self._c:
            self._c[name] = _Coll()
        return self._c[name]


def _token(roles, store_id="BV-ONLINE-01", uid="u-1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": roles,
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


@pytest.fixture
def ctx(monkeypatch):
    app = FastAPI()
    app.include_router(orders_router.router, prefix="/api/v1/online-store/orders")
    db = _DB()
    monkeypatch.setattr(deps, "get_db", lambda: db, raising=False)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None, raising=False)
    return {
        "client": TestClient(app),
        "db": db,
        "orders": db.get_collection("orders"),
        "inbox": db.get_collection("webhook_inbox"),
    }


def _seed_booked(ctx, sid="1001", status="CONFIRMED", **over):
    doc = {
        "order_id": f"ord-{sid}",
        "order_number": f"ONL-{sid}",
        "channel": "ONLINE",
        "source": "shopify",
        "shopify_order_id": sid,
        "shopify_order_name": f"#{sid}",
        "store_id": "BV-ONLINE-01",
        "customer_name": "Ravi Kumar",
        "customer_phone": "9876500001",
        "items": [{"sku": "A"}, {"sku": "B"}],
        "payments": [{"mode": "ONLINE", "amount": 1234.0}],
        "tax_summary": [{"hsn": "9004"}],
        "tax_totals": {"cgst": 0.0},
        "grand_total": 1234.0,
        "currency": "INR",
        "status": status,
        "payment_status": "PAID",
        "fulfillment_status": "UNFULFILLED",
        "rx_pending": False,
        "fulfillment_hold": False,
        "created_at": f"2026-07-2{int(sid) % 10}T10:00:00",
    }
    doc.update(over)
    ctx["orders"].insert_one(doc)
    return doc


def _seed_inbox(ctx, sid, *, processed=True, line_items=True, topic="orders/create",
                handler_error=None, customer_name=("Guest", "Buyer"), order_id=None):
    """Seed one shopify webhook_inbox row. topic=None -> a legacy row with NO
    stored topic header. order_id -> a CHILD-resource-shaped payload
    (fulfillments / refunds / checkouts reference their parent order; true
    order payloads carry only 'id')."""
    payload = {
        "id": int(sid),
        "name": f"#{sid}",
        "currency": "INR",
        "total_price": "999.0",
        "email": "guest@example.com",
        "customer": {"first_name": customer_name[0], "last_name": customer_name[1]},
        "created_at": "2026-07-21T08:00:00Z",
    }
    if line_items:
        payload["line_items"] = [{"sku": "Z", "quantity": 1}]
    if order_id is not None:
        payload["order_id"] = order_id
    row = {
        "webhook_id": f"wh-{sid}",
        "vendor": "shopify",
        "received_at": f"2026-07-21T09:{int(sid) % 60:02d}:00Z",
        "headers": ({"x-shopify-topic": topic} if topic else {}),
        "payload": payload,
        "processed": processed,
    }
    if handler_error:
        row["handler_error"] = handler_error
    ctx["inbox"].insert_one(row)


BASE = "/api/v1/online-store/orders"


def _hdr(roles, **kw):
    return {"Authorization": f"Bearer {_token(roles, **kw)}"}


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------


def test_list_requires_role(ctx):
    r = ctx["client"].get(BASE, headers=_hdr(["SALES_STAFF"]))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# OS-011: FAILED queue merged from webhook_inbox
# ---------------------------------------------------------------------------


def test_list_merges_unbooked_webhooks_as_failed_and_pending(ctx):
    _seed_booked(ctx, "1001")
    _seed_inbox(ctx, "1001", processed=True)  # booked -> NOT in the failed queue
    _seed_inbox(ctx, "2002", processed=True)  # drained, never booked -> FAILED
    _seed_inbox(ctx, "3003", processed=False)  # not yet drained -> PENDING

    r = ctx["client"].get(BASE, headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["total"] == 1
    assert len(data["orders"]) == 1
    assert data["orders"][0]["shopify_order_id"] == "1001"
    assert data["orders"][0]["map_status"] == "MAPPED"

    by_sid = {f["shopify_order_id"]: f for f in data["failed"]}
    assert set(by_sid) == {"2002", "3003"}
    assert by_sid["2002"]["map_status"] == "FAILED"
    assert by_sid["2002"]["map_error"]  # honest reason, never empty
    assert by_sid["3003"]["map_status"] == "PENDING"
    # failed_count counts genuine FAILURES only (not the still-pending row).
    assert data["failed_count"] == 1


def test_failed_rows_only_on_first_page(ctx):
    _seed_inbox(ctx, "2002", processed=True)
    r = ctx["client"].get(f"{BASE}?offset=50", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200
    data = r.json()
    assert data["failed"] == []  # no duplication across pages
    assert data["failed_count"] == 1  # ... but the count stays visible


# ---------------------------------------------------------------------------
# P0 regression (adversarial review of PR #947): CHILD-resource webhooks
# (fulfillments/refunds/checkouts) carry line_items but their payload.id is the
# CHILD id -- they must NEVER surface as FAILED rows, and Re-map must refuse
# them (replaying one would book a DUPLICATE order + GST invoice under the
# fulfillment id).
# ---------------------------------------------------------------------------


def test_fulfillment_webhook_never_surfaces_as_failed(ctx):
    # A booked + fulfilled order: its fulfillments/create webhook carries
    # line_items and payload.id = the FULFILLMENT id (parent in order_id).
    _seed_booked(ctx, "5001")
    _seed_inbox(
        ctx, "9990001", processed=True, topic="fulfillments/create", order_id=5001
    )
    r = ctx["client"].get(BASE, headers=_hdr(["ADMIN"]))
    assert r.status_code == 200
    data = r.json()
    assert data["failed"] == []  # no phantom 'not in the books' row
    assert data["failed_count"] == 0
    assert data["total"] == 1  # the real order is simply in the books


def test_remap_refuses_fulfillment_payload_and_books_nothing(ctx, monkeypatch):
    _seed_booked(ctx, "5001")
    _seed_inbox(
        ctx, "9990001", processed=True, topic="fulfillments/create", order_id=5001
    )
    calls = []
    monkeypatch.setattr(
        mapper_mod,
        "map_shopify_order",
        lambda *a, **k: calls.append(a) or {"status": "created"},
    )
    r = ctx["client"].post(f"{BASE}/remap/9990001", headers=_hdr(["ADMIN"]))
    assert r.status_code == 404, r.text  # nothing safe to replay
    assert calls == []  # the mapper was never consulted
    assert ctx["orders"].count_documents({}) == 1  # no duplicate order booked


def test_remap_by_order_id_never_matches_its_fulfillment_payload(ctx, monkeypatch):
    # Secondary hazard: remapping a LEGIT order id used to match the order's
    # NEWER fulfillment payload via payload.order_id and book a phantom.
    _seed_inbox(
        ctx, "9990002", processed=True, topic="fulfillments/create", order_id=5002
    )
    calls = []
    monkeypatch.setattr(
        mapper_mod,
        "map_shopify_order",
        lambda *a, **k: calls.append(a) or {"status": "created"},
    )
    r = ctx["client"].post(f"{BASE}/remap/5002", headers=_hdr(["ADMIN"]))
    assert r.status_code == 404
    assert calls == []


def test_topicless_legacy_order_row_still_surfaces_and_remaps(ctx, monkeypatch):
    # A legacy inbox row with NO stored topic header but a true ORDER shape
    # (line_items, no parent order_id) must keep working end-to-end.
    _seed_inbox(ctx, "6006", processed=True, topic=None)
    data = ctx["client"].get(BASE, headers=_hdr(["ADMIN"])).json()
    assert [f["shopify_order_id"] for f in data["failed"]] == ["6006"]
    assert data["failed"][0]["map_status"] == "FAILED"

    seen_topics = []
    monkeypatch.setattr(
        mapper_mod,
        "map_shopify_order",
        lambda payload, db, webhook_id=None, topic=None: (
            seen_topics.append(topic) or {"status": "created", "order_id": "ord-6006"}
        ),
    )
    r = ctx["client"].post(f"{BASE}/remap/6006", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # An order-shaped topicless row replays as a create -- the ONLY case that
    # may still default; stored non-order topics never reach the mapper.
    assert seen_topics == ["orders/create"]


def test_topicless_child_shaped_row_is_skipped(ctx):
    # No topic header + parent order_id != id -> a child resource: not queued.
    _seed_inbox(ctx, "9990003", processed=True, topic=None, order_id=7007)
    data = ctx["client"].get(BASE, headers=_hdr(["ADMIN"])).json()
    assert data["failed"] == []
    assert data["failed_count"] == 0


# ---------------------------------------------------------------------------
# OS-063: server-side projection
# ---------------------------------------------------------------------------


def test_list_rows_are_projected(ctx):
    _seed_booked(ctx, "1001")
    r = ctx["client"].get(BASE, headers=_hdr(["ADMIN"]))
    row = r.json()["orders"][0]
    for bulk in ("items", "payments", "tax_summary", "tax_totals"):
        assert bulk not in row, f"{bulk} must not reach the browser"
    assert row["items_count"] == 2
    # The rx-hold fields the screen needs ARE present (OS-012).
    assert row["rx_pending"] is False
    assert row["fulfillment_hold"] is False


def test_historical_status_passes_through(ctx):
    _seed_booked(ctx, "1001", status="HISTORICAL", source="bvi_import")
    row = ctx["client"].get(BASE, headers=_hdr(["ADMIN"])).json()["orders"][0]
    assert row["status"] == "HISTORICAL"  # the FE renders the grey badge off this


# ---------------------------------------------------------------------------
# OS-044: server-side search
# ---------------------------------------------------------------------------


def test_search_filters_booked_rows_server_side(ctx):
    _seed_booked(ctx, "1001", customer_name="Ravi Kumar")
    _seed_booked(ctx, "1002", customer_name="Anita Shah")
    r = ctx["client"].get(f"{BASE}?search=ravi", headers=_hdr(["ADMIN"]))
    data = r.json()
    assert data["total"] == 1
    assert data["orders"][0]["shopify_order_id"] == "1001"


def test_search_covers_failed_rows_too(ctx):
    _seed_booked(ctx, "1001", customer_name="Ravi Kumar")
    _seed_inbox(ctx, "2002", processed=True, customer_name=("Guest", "Buyer"))
    r = ctx["client"].get(f"{BASE}?search=guest", headers=_hdr(["ADMIN"]))
    data = r.json()
    assert data["total"] == 0
    assert len(data["failed"]) == 1
    assert data["failed"][0]["shopify_order_id"] == "2002"


# ---------------------------------------------------------------------------
# Store scope stays intact (IDOR guard + no failed-queue leak)
# ---------------------------------------------------------------------------


def test_store_scoped_accountant_sees_neither_other_stores_nor_failed_queue(ctx):
    _seed_booked(ctx, "1001")  # store BV-ONLINE-01
    _seed_inbox(ctx, "2002", processed=True)
    r = ctx["client"].get(BASE, headers=_hdr(["ACCOUNTANT"], store_id="BV-BOK-01"))
    assert r.status_code == 200
    data = r.json()
    assert data["orders"] == []
    assert data["failed"] == []
    assert data["failed_count"] == 0


# ---------------------------------------------------------------------------
# OS-011: remap verdict is explicit (a 'skipped' can never toast success)
# ---------------------------------------------------------------------------


def test_remap_skipped_reports_failed_not_success(ctx, monkeypatch):
    _seed_inbox(ctx, "4004", processed=True)
    monkeypatch.setattr(
        mapper_mod,
        "map_shopify_order",
        lambda *a, **k: {"status": "skipped", "reason": "no_line_items"},
    )
    r = ctx["client"].post(f"{BASE}/remap/4004", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["map_status"] == "FAILED"
    assert "no_line_items" in body["map_error"]


def test_remap_created_reports_ok(ctx, monkeypatch):
    _seed_inbox(ctx, "4005", processed=True)
    monkeypatch.setattr(
        mapper_mod,
        "map_shopify_order",
        lambda *a, **k: {"status": "created", "order_id": "ord-4005"},
    )
    r = ctx["client"].post(f"{BASE}/remap/4005", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["map_status"] == "MAPPED"
    assert body["map_error"] is None


def test_remap_requires_admin(ctx):
    r = ctx["client"].post(f"{BASE}/remap/4004", headers=_hdr(["CATALOG_MANAGER"]))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# OS-012: clear-rx-hold
# ---------------------------------------------------------------------------


def test_clear_rx_hold_releases_the_hold(ctx):
    _seed_booked(
        ctx,
        "5005",
        rx_pending=True,
        fulfillment_hold=True,
        rx_hold_reasons=["No valid prescription on file"],
    )
    r = ctx["client"].post(f"{BASE}/ord-5005/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rx_pending"] is False and body["fulfillment_hold"] is False

    doc = ctx["orders"].find_one({"order_id": "ord-5005"})
    assert doc["rx_pending"] is False
    assert doc["fulfillment_hold"] is False
    assert doc["rx_hold_cleared"] is True
    assert doc["rx_hold_cleared_by"] == "u-1"
    # The reasons stay on the doc as the audit trail of WHY it was held.
    assert doc["rx_hold_reasons"] == ["No valid prescription on file"]


def test_clear_rx_hold_conflict_when_no_hold(ctx):
    _seed_booked(ctx, "5006")  # no hold
    r = ctx["client"].post(f"{BASE}/ord-5006/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# PR #1029 follow-up 2: the release NAMES what it released. A stock-miss hold
# rides the same fulfillment_hold flag as the Rx hold, but calling its release
# "Rx hold cleared" told staff a prescription was verified when what actually
# happened was a stock resolution.
# ---------------------------------------------------------------------------


def test_clear_hold_names_a_stock_release(ctx):
    """A stock-miss-held order (fulfillment_hold + its own stock_hold_reason,
    rx_pending False) releases with a message that says STOCK, not Rx."""
    _seed_booked(
        ctx,
        "5015",
        fulfillment_hold=True,
        stock_hold_reason=(
            "Stock could not be claimed for this paid online order "
            "(oversell) - resolve stock, then clear the hold."
        ),
    )
    r = ctx["client"].post(f"{BASE}/ord-5015/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] == ["STOCK"]
    assert "stock hold" in body["message"].lower()
    assert "rx" not in body["message"].lower(), (
        "a stock release must not claim a prescription was verified"
    )
    doc = ctx["orders"].find_one({"order_id": "ord-5015"})
    assert doc["fulfillment_hold"] is False


def test_clear_hold_names_an_rx_release(ctx):
    _seed_booked(ctx, "5016", rx_pending=True, fulfillment_hold=True)
    r = ctx["client"].post(f"{BASE}/ord-5016/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] == ["RX"]
    assert "rx hold" in body["message"].lower()
    assert "stock" not in body["message"].lower()


def test_clear_hold_names_both_when_both_are_active(ctx):
    _seed_booked(
        ctx,
        "5017",
        rx_pending=True,
        fulfillment_hold=True,
        rx_hold_reasons=["RX_MISSING"],
        stock_hold_reason="Stock could not be claimed for this paid online order",
    )
    r = ctx["client"].post(f"{BASE}/ord-5017/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] == ["RX", "STOCK"]
    assert "rx hold" in body["message"].lower()
    assert "stock hold" in body["message"].lower()


def test_clear_hold_releases_a_legacy_stock_miss_shape_and_names_it(ctx):
    """Orders held by the FIRST cut of the stock-miss hold (PR #1029) carry the
    stock reason INSIDE rx_hold_reason and no stock_hold_reason field. They
    must still release cleanly -- and be named a STOCK release."""
    _seed_booked(
        ctx,
        "5018",
        fulfillment_hold=True,
        rx_hold_reason=(
            "Stock could not be claimed for this paid online order "
            "(oversell) - resolve stock, then clear the hold."
        ),
    )
    r = ctx["client"].post(f"{BASE}/ord-5018/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] == ["STOCK"]
    assert "stock hold" in body["message"].lower()
    doc = ctx["orders"].find_one({"order_id": "ord-5018"})
    assert doc["fulfillment_hold"] is False
    assert doc["rx_hold_cleared"] is True


def test_clear_rx_hold_missing_order_404(ctx):
    r = ctx["client"].post(f"{BASE}/nope/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 404


def test_clear_rx_hold_requires_admin(ctx):
    _seed_booked(ctx, "5007", rx_pending=True, fulfillment_hold=True)
    r = ctx["client"].post(
        f"{BASE}/ord-5007/clear-rx-hold", headers=_hdr(["CATALOG_MANAGER"])
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# PR #947 follow-up 4: clear-rx-hold accepts an optional {note, prescription_id}
# body and audits loudly on failure.
# ---------------------------------------------------------------------------


def test_clear_rx_hold_persists_optional_note_and_prescription_id(ctx):
    _seed_booked(ctx, "5008", rx_pending=True, fulfillment_hold=True)
    r = ctx["client"].post(
        f"{BASE}/ord-5008/clear-rx-hold",
        headers=_hdr(["ADMIN"]),
        json={"note": "Verified over phone", "prescription_id": "RX-99"},
    )
    assert r.status_code == 200, r.text
    doc = ctx["orders"].find_one({"order_id": "ord-5008"})
    assert doc["rx_hold_cleared_note"] == "Verified over phone"
    assert doc["rx_hold_cleared_prescription_id"] == "RX-99"


def test_clear_rx_hold_body_is_optional_backward_compatible(ctx):
    # No body at all (the existing FE call shape) must keep working exactly as
    # before -- no note/prescription_id keys written.
    _seed_booked(ctx, "5009", rx_pending=True, fulfillment_hold=True)
    r = ctx["client"].post(f"{BASE}/ord-5009/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    doc = ctx["orders"].find_one({"order_id": "ord-5009"})
    assert "rx_hold_cleared_note" not in doc
    assert "rx_hold_cleared_prescription_id" not in doc


def test_clear_rx_hold_audit_failure_is_logged_loudly(ctx, monkeypatch, caplog):
    """A clinical release must never be blocked by a broken audit trail (the
    release still succeeds), but the failure must be LOUD -- logged at ERROR --
    not silently swallowed. Covers both audit failure modes: fail-soft None
    return AND a raised exception."""
    import logging as _logging

    from api import dependencies as deps
    from api.routers import online_store_orders as orders_router

    class _FailingAudit:
        def create(self, row):
            return None  # audit_repository.create()'s own fail-soft contract

    _seed_booked(ctx, "5010", rx_pending=True, fulfillment_hold=True)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: _FailingAudit())
    with caplog.at_level(_logging.ERROR, logger=orders_router.logger.name):
        r = ctx["client"].post(f"{BASE}/ord-5010/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text  # the release itself is never blocked
    error_records = [rec for rec in caplog.records if rec.levelno == _logging.ERROR]
    assert error_records, "expected a LOUD (ERROR) log on audit write failure"
    assert any("audit" in rec.message.lower() for rec in error_records)


def test_clear_rx_hold_audit_exception_is_logged_loudly(ctx, monkeypatch, caplog):
    import logging as _logging

    from api import dependencies as deps
    from api.routers import online_store_orders as orders_router

    class _RaisingAudit:
        def create(self, row):
            raise RuntimeError("boom")

    _seed_booked(ctx, "5011", rx_pending=True, fulfillment_hold=True)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: _RaisingAudit())
    with caplog.at_level(_logging.ERROR, logger=orders_router.logger.name):
        r = ctx["client"].post(f"{BASE}/ord-5011/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text  # the release itself is never blocked
    assert any(rec.levelno == _logging.ERROR for rec in caplog.records)


# ---------------------------------------------------------------------------
# PR #947 follow-up 2: server-side rx_hold filter + envelope count. The
# banner/count/filter must reflect the WHOLE scope, not just loaded pages.
# ---------------------------------------------------------------------------


def test_rx_hold_count_reflects_full_scope_beyond_page_size(ctx):
    # Two held orders + one clean order; request a page size of 1 so the held
    # rows are NOT all on the loaded page -- rx_hold_count must still be 2.
    _seed_booked(ctx, "6001", rx_pending=True, fulfillment_hold=True)
    _seed_booked(ctx, "6002", rx_pending=False, fulfillment_hold=True)
    _seed_booked(ctx, "6003", rx_pending=False, fulfillment_hold=False)
    r = ctx["client"].get(f"{BASE}?limit=1", headers=_hdr(["ADMIN"]))
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["orders"]) == 1  # only one row loaded...
    assert data["rx_hold_count"] == 2  # ...but the count covers the whole scope


def test_rx_hold_filter_returns_only_held_orders(ctx):
    _seed_booked(ctx, "6001", rx_pending=True, fulfillment_hold=True)
    _seed_booked(ctx, "6002", rx_pending=False, fulfillment_hold=False)
    r = ctx["client"].get(f"{BASE}?rx_hold=true", headers=_hdr(["ADMIN"]))
    data = r.json()
    assert data["total"] == 1
    assert data["orders"][0]["shopify_order_id"] == "6001"
    # The count stays accurate under the filter too.
    assert data["rx_hold_count"] == 1


def test_rx_hold_filter_excludes_the_failed_queue(ctx):
    # Unbooked webhook rows carry no rx-hold state -- an Rx-hold-only view must
    # not surface them (they were previously merged in unconditionally).
    _seed_booked(ctx, "6001", rx_pending=True, fulfillment_hold=True)
    _seed_inbox(ctx, "7007", processed=True)
    r = ctx["client"].get(f"{BASE}?rx_hold=true", headers=_hdr(["ADMIN"]))
    data = r.json()
    assert data["failed"] == []
    assert data["failed_count"] == 0


def test_rx_hold_count_honours_search_and_store_scope(ctx):
    _seed_booked(ctx, "6001", rx_pending=True, customer_name="Ravi Kumar")
    _seed_booked(ctx, "6002", rx_pending=True, customer_name="Anita Shah")
    r = ctx["client"].get(f"{BASE}?search=ravi", headers=_hdr(["ADMIN"]))
    assert r.json()["rx_hold_count"] == 1

    # Store-scoped caller: rx_hold_count must respect their own store scope too.
    r2 = ctx["client"].get(BASE, headers=_hdr(["ACCOUNTANT"], store_id="BV-BOK-01"))
    assert r2.json()["rx_hold_count"] == 0


# ---------------------------------------------------------------------------
# P1 regression (adversarial review of PR #947): the OS-026 lifetime-spend
# enrichment sums the WHOLE orders collection -- store-pinned roles must never
# receive that cross-store money. Cross-store callers only; everyone else gets
# nothing (the FE renders an honest em-dash for the absent fields).
# ---------------------------------------------------------------------------


def test_attach_order_stats_skipped_for_store_pinned_roles(monkeypatch):
    from api.routers import customers as customers_router

    db = _DB()
    orders = db.get_collection("orders")
    orders.insert_one(
        {"customer_id": "C1", "grand_total": 100.0, "status": "DELIVERED", "store_id": "BV-PUN-01"}
    )
    orders.insert_one(
        {"customer_id": "C1", "grand_total": 50.0, "status": "DELIVERED", "store_id": "BV-BOK-01"}
    )
    monkeypatch.setattr(deps, "get_db", lambda: db, raising=False)

    rows = [{"customer_id": "C1"}]
    customers_router._attach_order_stats(
        rows, {"roles": ["STORE_MANAGER"], "active_store_id": "BV-BOK-01"}
    )
    # Store-pinned caller: NO cross-store totals attached at all.
    assert "orders_count" not in rows[0]
    assert "total_spent" not in rows[0]

    customers_router._attach_order_stats(rows, {"roles": ["ADMIN"]})
    # HQ caller: full lifetime figures.
    assert rows[0]["orders_count"] == 2
    assert rows[0]["total_spent"] == 150.0
