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
                handler_error=None, customer_name=("Guest", "Buyer")):
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
    row = {
        "webhook_id": f"wh-{sid}",
        "vendor": "shopify",
        "received_at": f"2026-07-21T09:{int(sid) % 60:02d}:00Z",
        "headers": {"x-shopify-topic": topic},
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


def test_clear_rx_hold_missing_order_404(ctx):
    r = ctx["client"].post(f"{BASE}/nope/clear-rx-hold", headers=_hdr(["ADMIN"]))
    assert r.status_code == 404


def test_clear_rx_hold_requires_admin(ctx):
    _seed_booked(ctx, "5007", rx_pending=True, fulfillment_hold=True)
    r = ctx["client"].post(
        f"{BASE}/ord-5007/clear-rx-hold", headers=_hdr(["CATALOG_MANAGER"])
    )
    assert r.status_code == 403
