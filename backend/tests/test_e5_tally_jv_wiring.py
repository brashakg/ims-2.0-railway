"""
IMS 2.0 - E5 -> live Tally export wiring tests (flag-gated Receipt voucher)
===========================================================================
The merged E5 engine (tender_routing.build_tender_jv_legs +
assert_voucher_balanced) is wired into the LIVE Tally export as a NEW, separate
Receipt-voucher stream:

  * GET /api/v1/finance/tally/tender-receipt-jv  (sibling of /tally/sales-jv)
  * gated by E2 policy ``tally.tender_receipt_voucher`` (registry default
    False -> DARK on deploy)
  * the existing Sales day-voucher output is BYTE-IDENTICAL flag-on or
    flag-off (receipt legs are never injected into the Sales vouchers); when
    the flag is ON the sales-jv response additively OFFERS the sibling via an
    X-Tally-Tender-Receipt header.

Intent asserted here (a hollow shell FAILS):
  1  flag OFF -> sales-jv body byte-identical to the pre-change pipeline
     (decorate + nexus builder, nothing else), no Receipt voucher anywhere,
     no offer header; the sibling route is 403 (dark)
  2  flag ON  -> sales-jv body STILL byte-identical; offer header appears
  3  mixed CASH+UPI day -> ONE balanced Receipt voucher: UPI on the UPI bank
     ledger, Cash leg EXACTLY the cash tendered (UPI never folded into Cash)
  4  unknown/blank tender -> Suspense A/c leg, not Cash
  5  paise-heavy day -> signed voucher legs sum to 0.00 exactly
  6  E2 store override of the tender map reaches the emitted ledger name
  7  READ-ONLY: order.payments[] rows byte-equal before/after (no stamp,
     no capture mutation)
  8  the route goes through the MERGED engine (build_tender_jv_legs spy --
     no fork) and fails LOUDLY (500) when the balance gate trips
  9  RBAC: POLICY row exists; SALES_STAFF is 403 even with the flag on

CI-robustness: every DB accessor the handlers touch is monkeypatched onto a
faithful in-memory fake (orders / stores / customers / entities /
tender_ledger_map) and every doc read is SEEDED; the policy read is pinned via
a monkeypatched policy_engine.get_policy (no fail-soft divergence between a
Mongo-less local run and CI). Absence is asserted FIELD-AWARE (parsed XML
voucher types / per-ledger amounts), never via whole-payload substring.

No emoji (Windows cp1252).
"""
from __future__ import annotations

import copy
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from api.routers import finance as finance_module  # noqa: E402
from api.routers import reconciliation as recon_module  # noqa: E402
from api.routers.auth import create_access_token  # noqa: E402
from api.services import policy_engine  # noqa: E402
from api.services import policy_registry as preg  # noqa: E402
from api.services import tally_tender_receipt as ttr  # noqa: E402
from agents.nexus_providers import tally_build_day_voucher_xml  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (the operators these handlers use:
# equality, $in, $nin, $gte/$lte on datetimes, $or, projection _id:0)
# ============================================================================


def _cmp_op(actual: Any, op: str, expected: Any) -> bool:
    if op == "$nin":
        return actual not in expected
    if actual is None and op in ("$gt", "$gte", "$lt", "$lte"):
        return False
    try:
        if op == "$gt":
            return actual > expected
        if op == "$gte":
            return actual >= expected
        if op == "$lt":
            return actual < expected
        if op == "$lte":
            return actual <= expected
        if op == "$ne":
            return actual != expected
        if op == "$in":
            return actual in expected
    except TypeError:
        return False
    return False


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in (query or {}).items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        actual = doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if actual != v:
            return False
    return True


def _project(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    out = dict(doc)
    if projection and projection.get("_id") == 0:
        out.pop("_id", None)
    return out


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{self._n}")
        self._n += 1
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return _project(d, projection)
        return None

    def find(self, query=None, projection=None):
        return FakeCursor([_project(d, projection) for d in self.docs if _matches(d, query or {})])

    def count_documents(self, query=None):
        return sum(1 for d in self.docs if _matches(d, query or {}))


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


# ============================================================================
# Fixtures -- EVERY db/policy accessor the handlers touch is pinned here.
# ============================================================================


def _pay(method, amount, **extra):
    row = {
        "payment_id": extra.pop("payment_id", "PAY-x"),
        "method": method,
        "amount": amount,
        "reference": extra.pop("reference", None),
        "received_by": extra.pop("received_by", "U1"),
        "received_at": extra.pop("received_at", "2026-06-09T10:00:00"),
        "idempotency_key": extra.pop("idempotency_key", "IK-x"),
    }
    row.update(extra)
    return row


def _order(order_id, payments, *, store_id="BV-GK1", grand=1000.0, tax=100.0,
           customer="Test Customer", status="COMPLETED"):
    return {
        "order_id": order_id,
        "store_id": store_id,
        "status": status,
        "created_at": datetime(2026, 6, 8, 20, 0, 0),
        "customer_name": customer,
        "customer_id": "CUST-1",
        "grand_total": grand,
        "tax_amount": tax,
        "payments": payments,
    }


@pytest.fixture()
def db() -> FakeDB:
    fake = FakeDB()
    fake.get_collection("stores").insert_one(
        {
            "store_id": "BV-GK1",
            "store_code": "GK1",
            "store_name": "GK-I Flagship",
            "state": "Jharkhand",
            "is_active": True,
        }
    )
    return fake


@pytest.fixture(autouse=True)
def _pin_db_and_policy(monkeypatch, db):
    """Pin EVERY repo/db accessor the two routers touch to the fake (orders,
    stores, customers, entities, tender_ledger_map) and pin the policy read so
    local-vs-CI never diverges on fail-soft paths."""
    monkeypatch.setattr(finance_module, "_get_db", lambda: db)
    monkeypatch.setattr(recon_module, "_get_db", lambda: db)
    # E2 store->entity resolver memoizes via the real cache + stores collection;
    # pin it so the scope chain is deterministic with NO live DB.
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id", lambda store_id: None
    )

    flag = {"on": False}

    def fake_get_policy(key, scope=None, **kw):
        if key == "tally.tender_receipt_voucher":
            return flag["on"]
        spec = preg.REGISTRY.get(key)
        if spec is not None:
            return spec.default
        return kw.get("default")

    monkeypatch.setattr(policy_engine, "get_policy", fake_get_policy)
    return flag


@pytest.fixture()
def flag(_pin_db_and_policy):
    return _pin_db_and_policy


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _token(role: str) -> str:
    return create_access_token(
        {
            "user_id": f"test-{role.lower()}",
            "username": role.lower(),
            "roles": [role],
            "active_role": role,
            "store_ids": ["BV-GK1"],
            "active_store_id": "BV-GK1",
        }
    )


def _hdr(role: str = "ACCOUNTANT") -> Dict[str, str]:
    return {"Authorization": f"Bearer {_token(role)}"}


SALES_JV = "/api/v1/finance/tally/sales-jv"
RECEIPT_JV = "/api/v1/finance/tally/tender-receipt-jv"
OFFER_HEADER = "X-Tally-Tender-Receipt"


# ============================================================================
# XML helpers -- FIELD-AWARE parsing (never whole-payload substring checks)
# ============================================================================


def _vouchers(xml_text: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out: List[Dict[str, Any]] = []
    for v in root.iter("VOUCHER"):
        legs = []
        for e in v.iter("ALLLEDGERENTRIES.LIST"):
            legs.append(
                {
                    "ledger": e.findtext("LEDGERNAME"),
                    "amount": float(e.findtext("AMOUNT")),
                    "deemed_positive": e.findtext("ISDEEMEDPOSITIVE"),
                }
            )
        out.append(
            {
                "vchtype": v.get("VCHTYPE"),
                "number": v.findtext("VOUCHERNUMBER"),
                "party": v.findtext("PARTYLEDGERNAME"),
                "legs": legs,
            }
        )
    return out


def _legs_by_ledger(voucher: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    by: Dict[str, List[Dict[str, Any]]] = {}
    for leg in voucher["legs"]:
        by.setdefault(leg["ledger"], []).append(leg)
    return by


def _expected_sales_jv_xml(db: FakeDB, store_id: str) -> str:
    """The PRE-CHANGE sales-jv pipeline, replicated independently: fetch the
    seeded orders, apply the handler's intra-state decoration (empty customer
    state map -> CGST/SGST split), and run the untouched nexus builder. The
    endpoint body must equal this BYTE-FOR-BYTE with the flag off AND on."""
    orders = []
    for d in db.get_collection("orders").docs:
        if d.get("status") in ("CANCELLED", "DRAFT", "cancelled", "draft"):
            continue
        if store_id and d.get("store_id") != store_id:
            continue
        o = copy.deepcopy(d)
        o.pop("_id", None)
        tax = float(o.get("tax_amount") or 0)
        grand = float(o.get("grand_total") or 0)
        cgst = round(tax / 2.0, 2)
        o["igst_amount"] = 0.0
        o["cgst_amount"] = cgst
        o["sgst_amount"] = round(tax - cgst, 2)
        o["subtotal"] = round(grand - tax, 2)
        o["grand_total"] = grand
        orders.append(o)
    s = db.get_collection("stores").find_one({"store_id": store_id}) or {}
    store_meta = {
        "store_id": store_id,
        "store_code": s.get("store_code"),
        "store_name": s.get("store_name"),
    }
    return tally_build_day_voucher_xml(orders, store_meta)


# ============================================================================
# Registry: dark on deploy
# ============================================================================


def test_policy_key_registered_default_false_dark_on_deploy():
    spec = preg.REGISTRY.get("tally.tender_receipt_voucher")
    assert spec is not None, "tally.tender_receipt_voucher missing from policy_registry"
    assert spec.default is False  # a fresh deploy stays byte-identical
    assert spec.type == "bool"
    assert "store" in spec.scopes  # orchestrator can light it per store
    assert set(spec.write_roles) <= {"SUPERADMIN", "ADMIN", "ACCOUNTANT"}


# ============================================================================
# 1 -- flag OFF: byte-identical sales JV, no receipt voucher, sibling dark
# ============================================================================


def test_flag_off_sales_jv_byte_identical_to_prechange_pipeline(client, db, flag):
    flag["on"] = False
    db.get_collection("orders").insert_one(
        _order("O1", [_pay("CASH", 400.0), _pay("UPI", 600.0)])
    )
    r = client.get(f"{SALES_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 200
    expected = _expected_sales_jv_xml(db, "BV-GK1")
    assert r.content == expected.encode("utf-8"), "sales-jv body changed with flag OFF"
    # No offer header when dark.
    assert r.headers.get(OFFER_HEADER) is None
    # Field-aware absence: every voucher in the export is a Sales voucher.
    vtypes = [v["vchtype"] for v in _vouchers(r.text)]
    assert vtypes == ["Sales"]


def test_flag_off_receipt_route_is_dark_403(client, db, flag):
    flag["on"] = False
    db.get_collection("orders").insert_one(_order("O1", [_pay("UPI", 600.0)]))
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 403
    assert "tally.tender_receipt_voucher" in r.json().get("detail", "")


# ============================================================================
# 2 -- flag ON: sales JV body STILL byte-identical; offer header is additive
# ============================================================================


def test_flag_on_sales_jv_body_unchanged_offer_header_added(client, db, flag):
    db.get_collection("orders").insert_one(
        _order("O1", [_pay("CASH", 400.0), _pay("UPI", 600.0)])
    )
    flag["on"] = False
    body_off = client.get(f"{SALES_JV}?store_id=BV-GK1", headers=_hdr()).content
    flag["on"] = True
    r_on = client.get(f"{SALES_JV}?store_id=BV-GK1", headers=_hdr())
    assert r_on.status_code == 200
    assert r_on.content == body_off, "flag ON must not alter the Sales voucher body"
    assert r_on.headers.get(OFFER_HEADER) == "/api/v1/finance/tally/tender-receipt-jv"
    # And still zero Receipt vouchers inside the Sales export itself.
    assert [v["vchtype"] for v in _vouchers(r_on.text)] == ["Sales"]


# ============================================================================
# 3 -- mixed CASH+UPI day: balanced Receipt voucher, UPI on the bank ledger,
#      ZERO extra on Cash
# ============================================================================


def test_flag_on_mixed_cash_upi_day_balanced_receipt_voucher(client, db, flag):
    flag["on"] = True
    db.get_collection("orders").insert_one(
        _order("O1", [_pay("CASH", 400.0), _pay("UPI", 600.0)])
    )
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    vouchers = _vouchers(r.text)
    assert len(vouchers) == 1
    v = vouchers[0]
    assert v["vchtype"] == "Receipt"
    assert v["number"] == "RCPT-O1"
    assert v["party"] == "Test Customer"

    by = _legs_by_ledger(v)
    # UPI hits the UPI bank ledger (debit = negative amount, deemed positive).
    assert [leg["amount"] for leg in by["Bank A/c - UPI"]] == [-600.0]
    assert by["Bank A/c - UPI"][0]["deemed_positive"] == "Yes"
    # Cash leg is EXACTLY the cash tendered -- the UPI 600 was NOT folded in.
    assert [leg["amount"] for leg in by["Cash A/c"]] == [-400.0]
    # Party credit clears the full receipt.
    assert [leg["amount"] for leg in by["Test Customer"]] == [1000.0]
    assert by["Test Customer"][0]["deemed_positive"] == "No"
    # BALANCED: signed legs net to zero paise.
    assert round(sum(leg["amount"] for leg in v["legs"]), 2) == 0.0


# ============================================================================
# 4 -- unknown/blank tender -> Suspense A/c, never Cash
# ============================================================================


def test_flag_on_unknown_tender_books_to_suspense_not_cash(client, db, flag):
    flag["on"] = True
    db.get_collection("orders").insert_one(
        _order("O2", [_pay("", 250.0), _pay("CASH", 750.0)])
    )
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 200
    (v,) = _vouchers(r.text)
    by = _legs_by_ledger(v)
    assert [leg["amount"] for leg in by["Suspense A/c"]] == [-250.0]
    # Cash carries ONLY the real cash row -- the blank-method 250 not folded in.
    assert [leg["amount"] for leg in by["Cash A/c"]] == [-750.0]
    assert round(sum(leg["amount"] for leg in v["legs"]), 2) == 0.0


# ============================================================================
# 5 -- paise-heavy day stays paise-exact (balance gate is real)
# ============================================================================


def test_flag_on_paise_heavy_payments_balance_exactly(client, db, flag):
    flag["on"] = True
    db.get_collection("orders").insert_one(
        _order(
            "O3",
            [
                _pay("CASH", 333.33),
                _pay("UPI", 333.33),
                _pay("CARD", 333.34),
                _pay("CARD", -0.01),  # paisa refund contras the SAME card ledger
            ],
        )
    )
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 200
    (v,) = _vouchers(r.text)
    by = _legs_by_ledger(v)
    assert [leg["amount"] for leg in by["Bank A/c - Card EDC"]] == [-333.33]
    assert round(sum(leg["amount"] for leg in v["legs"]), 2) == 0.0


# ============================================================================
# 6 -- E2 store override of the tender map reaches the emitted ledger
# ============================================================================


def test_flag_on_store_ledger_override_reaches_voucher(client, db, flag):
    flag["on"] = True
    db.get_collection("tender_ledger_map").insert_one(
        {"_id": "STORE:BV-GK1", "scope": "STORE:BV-GK1",
         "ledgers": {"UPI": "Bank A/c - HDFC UPI"}}
    )
    db.get_collection("orders").insert_one(_order("O4", [_pay("UPI", 500.0)]))
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 200
    (v,) = _vouchers(r.text)
    by = _legs_by_ledger(v)
    assert [leg["amount"] for leg in by["Bank A/c - HDFC UPI"]] == [-500.0]
    assert "Bank A/c - UPI" not in by  # the default name was overridden


# ============================================================================
# 7 -- READ-ONLY: no capture mutation, no ledger stamp
# ============================================================================


def test_receipt_export_never_mutates_orders_or_payments(client, db, flag):
    flag["on"] = True
    db.get_collection("orders").insert_one(
        _order("O5", [_pay("CASH", 100.0), _pay("UPI", 900.0)])
    )
    before = copy.deepcopy(db.get_collection("orders").docs)
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 200
    after = db.get_collection("orders").docs
    assert after == before, "receipt export must be READ-ONLY over orders"
    for row in after[0]["payments"]:
        assert "ledger" not in row
        assert "canonical_tender" not in row
        assert "ledger_stamped_at" not in row


# ============================================================================
# 8 -- engine reuse (no fork) + fail-loudly on an unbalanced voucher
# ============================================================================


def test_receipt_route_goes_through_merged_e5_engine(client, db, flag, monkeypatch):
    flag["on"] = True
    db.get_collection("orders").insert_one(
        _order("O6", [_pay("CASH", 250.0), _pay("GIFT_VOUCHER", 750.0)])
    )
    calls: List[List[Dict[str, Any]]] = []
    real_legs = ttr.build_tender_jv_legs

    def spy(payments, tender_map=None):
        calls.append(list(payments))
        return real_legs(payments, tender_map)

    monkeypatch.setattr(ttr, "build_tender_jv_legs", spy)
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 200
    # The route fed the order's payment rows to the MERGED engine -- no fork.
    assert len(calls) == 1
    assert sorted(p["method"] for p in calls[0]) == ["CASH", "GIFT_VOUCHER"]
    # And the engine routed GIFT_VOUCHER to the liability ledger, not a bank.
    (v,) = _vouchers(r.text)
    by = _legs_by_ledger(v)
    assert [leg["amount"] for leg in by["Gift Voucher Liability"]] == [-750.0]


def test_unbalanced_voucher_fails_loudly_500_no_partial_file(client, db, flag, monkeypatch):
    flag["on"] = True
    db.get_collection("orders").insert_one(_order("O7", [_pay("CASH", 100.0)]))

    def boom(legs, **kw):
        raise ValueError("Tally voucher does not balance: forced-by-test")

    monkeypatch.setattr(ttr, "assert_voucher_balanced", boom)
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr())
    assert r.status_code == 500
    assert "does not balance" in r.json().get("detail", "")


# ============================================================================
# 9 -- RBAC: catalogued row + role gate
# ============================================================================


def test_rbac_policy_row_exists_and_mirrors_sales_jv():
    from api.services.rbac_policy import policy_for

    row = policy_for("GET", RECEIPT_JV)
    assert row is not None, "no rbac_policy row for the tender-receipt-jv route"
    assert set(row["allowed"]) == {"ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"}
    sales = policy_for("GET", SALES_JV)
    assert set(sales["allowed"]) == set(row["allowed"])  # mirrors the sibling


def test_sales_staff_denied_even_with_flag_on(client, db, flag):
    flag["on"] = True
    db.get_collection("orders").insert_one(_order("O8", [_pay("CASH", 100.0)]))
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr("SALES_STAFF"))
    assert r.status_code == 403


def test_accountant_allowed_through_role_gates(client, db, flag):
    flag["on"] = True
    db.get_collection("orders").insert_one(_order("O9", [_pay("CARD", 100.0)]))
    r = client.get(f"{RECEIPT_JV}?store_id=BV-GK1", headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 200
