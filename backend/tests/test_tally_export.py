"""
IMS 2.0 — Tally per-store export tests (Phase I-6)
====================================================
Phase I-6 contract:
  - tally_build_day_voucher_xml accepts optional store_meta and bakes
    store_code + store_name into <NARRATION> + <COSTCENTRECATEGORY>
  - validate_voucher_balance flags taxable+tax≠grand_total mismatches
    with 50p tolerance and a per-batch ₹1 tolerance
  - _build_tally_export iterates active stores, splits per-store, and
    writes one row per (export_date, store_id) tuple to tally_exports
  - admin endpoints surface the per-store rows with download streaming
    and an idempotent regenerate

The 12 cases listed in the plan; FakeCollection / FakeDB shape is
borrowed from test_walkouts.py since those repos already work against
it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.nexus_providers import (  # noqa: E402
    tally_build_day_voucher_xml,
    validate_voucher_balance,
)


# ============================================================================
# Test fixtures
# ============================================================================


def _make_order(
    *,
    order_id: str,
    store_id: str,
    grand_total: float = 118.0,
    taxable: float = 100.0,
    tax: float = 18.0,
    subtotal: float = 100.0,
    discount: float = 0.0,
    cgst: float = 9.0,
    sgst: float = 9.0,
    status: str = "COMPLETED",
    created_iso: str = "2026-05-08T10:00:00+00:00",
    customer_name: str = "Test Customer",
):
    return {
        "order_id": order_id,
        "store_id": store_id,
        "status": status,
        "created_at": created_iso,
        "customer_name": customer_name,
        "grand_total": grand_total,
        "taxable": taxable,
        "tax": tax,
        "subtotal": subtotal,
        "total_discount": discount,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
    }


def _cmp(actual, op, op_val) -> bool:
    """One operator, with Mongo type-bracketing: an incomparable type pair (e.g. a
    Date bound against a string field) simply does not match instead of raising."""
    if actual is None:
        return op == "$ne"
    try:
        if op == "$gte":
            return actual >= op_val
        if op == "$lte":
            return actual <= op_val
        if op == "$lt":
            return actual < op_val
    except TypeError:
        return False  # type bracketing: Date range never matches a string, etc.
    if op == "$in":
        return actual in op_val
    if op == "$ne":
        return actual != op_val
    return True


def _doc_matches(doc, filter_):
    """Mini Mongo-filter matcher — supports plain equality, $or, $in, $ne,
    $gte/$lte/$lt (with type-bracketing), the operators the orchestrator uses."""
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_doc_matches(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if not _cmp(actual, op, op_val):
                    return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    """Lazy cursor with the chainable methods StoreRepository.find_many
    actually calls: .sort(), .skip(), .limit()."""

    def __init__(self, docs):
        self._docs = list(docs)
        self._skip = 0
        self._limit = None

    def sort(self, *args, **kwargs):
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def _materialize(self):
        out = list(self._docs)
        if self._skip:
            out = out[self._skip:]
        if self._limit:
            out = out[: self._limit]
        return out

    def __iter__(self):
        return iter(self._materialize())


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id", doc.get("order_id"))})()

    def find_one(self, filter_=None):
        for d in self.docs:
            if _doc_matches(d, filter_):
                return d
        return None

    def find(self, filter_=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter_))

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _doc_matches(d, filter_):
                set_block = (update or {}).get("$set", {}) or {}
                d.update(set_block)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            new_doc = dict((update or {}).get("$set", {}))
            new_doc.update(filter_)
            self.docs.append(new_doc)
            return type("R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": "x"})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def count_documents(self, filter_=None):
        return sum(1 for d in self.docs if _doc_matches(d, filter_))


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        if name.startswith("_") or name in {"is_connected"}:
            raise AttributeError(name)
        return self.get_collection(name)


# ============================================================================
# Pure-function tests — XML generator + balance validator
# ============================================================================


class TestXmlGenerator:
    def test_xml_generator_well_formed(self):
        """Three orders → three VOUCHER blocks each with the four ledger entries."""
        orders = [
            _make_order(order_id=f"O{i}", store_id="BV-GK1") for i in range(3)
        ]
        xml = tally_build_day_voucher_xml(orders)
        assert "<ENVELOPE>" in xml
        assert "<TALLYREQUEST>Import Data</TALLYREQUEST>" in xml
        assert xml.count("<VOUCHER VCHTYPE=\"Sales\"") == 3
        # Each voucher should have all four ledger entries
        assert xml.count("<LEDGERNAME>Sales A/c</LEDGERNAME>") == 3
        assert xml.count("<LEDGERNAME>CGST Output</LEDGERNAME>") == 3
        assert xml.count("<LEDGERNAME>SGST Output</LEDGERNAME>") == 3
        # Party ledger entries: one per voucher (party name appears twice
        # — once as PARTYLEDGERNAME, once as the ledger entry)
        assert xml.count("<PARTYLEDGERNAME>Test Customer</PARTYLEDGERNAME>") == 3

    def test_xml_generator_includes_store_meta(self):
        """store_meta bakes both code and name into NARRATION + cost-centre."""
        orders = [_make_order(order_id="O1", store_id="BV-GK1")]
        xml = tally_build_day_voucher_xml(
            orders,
            store_meta={
                "store_id": "BV-GK1",
                "store_code": "GK1",
                "store_name": "GK-I Flagship",
            },
        )
        assert "<NARRATION>GK1 · GK-I Flagship</NARRATION>" in xml
        assert "<COSTCENTRECATEGORY>GK1</COSTCENTRECATEGORY>" in xml

    def test_xml_generator_omits_meta_blocks_when_no_store_passed(self):
        """No store_meta → no narration/cost-centre tags."""
        xml = tally_build_day_voucher_xml(
            [_make_order(order_id="O1", store_id="BV-GK1")]
        )
        assert "<NARRATION>" not in xml
        assert "<COSTCENTRECATEGORY>" not in xml


class TestBalanceValidator:
    def test_passes_clean_orders(self):
        """taxable + tax = grand_total per order, batch sums consistent."""
        orders = [_make_order(order_id=f"O{i}", store_id="S1") for i in range(5)]
        report = validate_voucher_balance(orders)
        assert report["ok"] is True
        assert report["mismatch_count"] == 0
        assert report["totals"]["order_count"] == 5
        assert report["totals"]["grand_total"] == 590.0  # 5 × 118
        assert report["totals"]["taxable"] == 500.0
        assert report["totals"]["tax"] == 90.0

    def test_catches_mismatch_outside_tolerance(self):
        """₹2 off → flagged; mismatch row carries order_id and delta."""
        orders = [
            _make_order(order_id="OK", store_id="S1"),
            _make_order(
                order_id="BAD",
                store_id="S1",
                grand_total=120.0,  # taxable 100 + tax 18 = 118, off by 2
                taxable=100.0,
                tax=18.0,
            ),
        ]
        report = validate_voucher_balance(orders)
        assert report["ok"] is False
        assert report["mismatch_count"] == 1
        assert report["mismatches"][0]["order_id"] == "BAD"
        assert report["mismatches"][0]["delta"] == 2.0

    def test_tolerates_50p_rounding(self):
        """Within 50p (we use < 0.50 strict) — fine; at 0.30 should pass."""
        orders = [
            _make_order(
                order_id="O1",
                store_id="S1",
                grand_total=118.30,
                taxable=100.0,
                tax=18.0,
            ),
        ]
        report = validate_voucher_balance(orders)
        # delta is 0.30 → within tolerance
        assert report["mismatch_count"] == 0

    def test_skips_legacy_orders_with_no_taxable(self):
        """Pre-Phase-6.15 orders may have taxable=0 — skip the per-row check."""
        orders = [
            _make_order(
                order_id="LEGACY",
                store_id="S1",
                taxable=0.0,
                tax=0.0,
                grand_total=100.0,
            ),
        ]
        report = validate_voucher_balance(orders)
        assert report["mismatch_count"] == 0


# ============================================================================
# Orchestrator tests — _build_tally_export
# ============================================================================


@pytest.fixture
def fake_db_with_stores():
    """FakeDB seeded with 2 active stores + the StoreRepository wired
    onto the fake collection so NEXUS sees real-looking stores."""
    db = FakeDB()
    db.get_collection("stores").insert_one(
        {
            "store_id": "BV-GK1",
            "store_code": "GK1",
            "store_name": "GK-I Flagship",
            "brand": "BV",
            "is_active": True,
        }
    )
    db.get_collection("stores").insert_one(
        {
            "store_id": "BV-LAJ",
            "store_code": "LAJ",
            "store_name": "Lajpat Nagar",
            "brand": "BV",
            "is_active": True,
        }
    )
    return db


@pytest.fixture
def patched_nexus(monkeypatch, fake_db_with_stores):
    """Wire the NEXUS agent to use the fake DB — both for orders+exports
    and for the StoreRepository the orchestrator now needs."""
    from agents.implementations import nexus as nexus_module
    from api import dependencies as deps_module
    from database.repositories.store_repository import StoreRepository

    # NEXUS pulls collections via self.get_collection(name) → JarvisAgent
    # base. Easiest path is to patch the get_collection method on the
    # agent instance to look at our fake DB.
    nexus = nexus_module.NexusAgent(db=fake_db_with_stores)
    nexus.get_collection = lambda name: fake_db_with_stores.get_collection(name)

    # Patch the StoreRepository factory to return one bound to our fake.
    store_repo = StoreRepository(fake_db_with_stores.get_collection("stores"))
    monkeypatch.setattr(deps_module, "get_store_repository", lambda: store_repo)

    return nexus, fake_db_with_stores


@pytest.mark.asyncio
async def test_orchestrator_splits_per_store(patched_nexus):
    """Two stores with 2 + 1 orders → two rows in tally_exports."""
    nexus, db = patched_nexus
    orders = db.get_collection("orders")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    # Two for GK1, one for LAJ (same day, all completed)
    orders.insert_one(_make_order(order_id="O1", store_id="BV-GK1", created_iso=today))
    orders.insert_one(_make_order(order_id="O2", store_id="BV-GK1", created_iso=today))
    orders.insert_one(_make_order(order_id="O3", store_id="BV-LAJ", created_iso=today))

    result = await nexus._build_tally_export()

    assert result.ok is True
    rows = db.get_collection("tally_exports").docs
    assert len(rows) == 2
    by_store = {r["store_id"]: r for r in rows}
    assert by_store["BV-GK1"]["voucher_count"] == 2
    assert by_store["BV-LAJ"]["voucher_count"] == 1
    assert by_store["BV-GK1"]["balanced"] is True
    # Narration baked into XML
    assert "GK1 · GK-I Flagship" in by_store["BV-GK1"]["xml"]


@pytest.mark.asyncio
async def test_orchestrator_skips_stores_with_no_orders(patched_nexus):
    """A store with zero qualifying orders gets no row written."""
    nexus, db = patched_nexus
    orders = db.get_collection("orders")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    orders.insert_one(_make_order(order_id="O1", store_id="BV-GK1", created_iso=today))
    # No orders for BV-LAJ

    await nexus._build_tally_export()
    rows = db.get_collection("tally_exports").docs
    assert len(rows) == 1
    assert rows[0]["store_id"] == "BV-GK1"


@pytest.mark.asyncio
async def test_orchestrator_writes_unbalanced_row(patched_nexus):
    """A store with mismatched orders still gets a row, marked unbalanced."""
    nexus, db = patched_nexus
    orders = db.get_collection("orders")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    # Off by ₹3 — outside 50p tolerance
    orders.insert_one(
        _make_order(
            order_id="BAD",
            store_id="BV-GK1",
            grand_total=121.0,
            taxable=100.0,
            tax=18.0,
            created_iso=today,
        )
    )

    result = await nexus._build_tally_export()
    rows = db.get_collection("tally_exports").docs
    assert len(rows) == 1
    assert rows[0]["balanced"] is False
    assert rows[0]["balance_check"]["mismatch_count"] == 1
    assert result.ok is True  # Export still completes; row just flagged


@pytest.mark.asyncio
async def test_orchestrator_regenerate_overwrites_prior_row(patched_nexus):
    """Calling _build_tally_export twice with the same date+store should
    upsert (overwrite) — the natural key is (export_date, store_id)."""
    nexus, db = patched_nexus
    orders = db.get_collection("orders")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    orders.insert_one(
        _make_order(order_id="O1", store_id="BV-GK1", created_iso=today.isoformat())
    )
    await nexus._build_tally_export(target_date=today, store_id="BV-GK1")

    # Add a second order, regenerate — voucher_count should reflect both
    orders.insert_one(
        _make_order(order_id="O2", store_id="BV-GK1", created_iso=today.isoformat())
    )
    await nexus._build_tally_export(target_date=today, store_id="BV-GK1")

    rows = db.get_collection("tally_exports").docs
    matching = [r for r in rows if r.get("store_id") == "BV-GK1"]
    assert len(matching) == 1, "Should upsert, not duplicate"
    assert matching[0]["voucher_count"] == 2


# ============================================================================
# Endpoint tests — admin Tally export routes
# ============================================================================


@pytest.fixture
def jwt_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test_secret_for_tally_tests")
    yield


@pytest.fixture
def super_token(jwt_env):
    """Mint a SUPERADMIN JWT for the admin role gate."""
    from api.routers.auth import create_access_token
    return create_access_token(
        {
            "user_id": "test-superadmin",
            "username": "admin",
            "roles": ["SUPERADMIN"],
            "active_role": "SUPERADMIN",
            "store_ids": [],
            "active_store_id": None,
        }
    )


@pytest.fixture
def staff_token(jwt_env):
    """Mint a SALES_STAFF JWT — should be rejected by the admin gate."""
    from api.routers.auth import create_access_token
    return create_access_token(
        {
            "user_id": "test-staff",
            "username": "staff",
            "roles": ["SALES_STAFF"],
            "active_role": "SALES_STAFF",
            "store_ids": ["BV-GK1"],
            "active_store_id": "BV-GK1",
        }
    )


@pytest.fixture
def admin_client(jwt_env, monkeypatch):
    """TestClient with the admin module patched to use a fake collection."""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.routers import admin as admin_module

    fake_coll = FakeCollection()
    # Seed one balanced + one unbalanced row for date 2026-05-08
    fake_coll.insert_one(
        {
            "agent_id": "nexus",
            "export_date": datetime(2026, 5, 8, tzinfo=timezone.utc).isoformat(),
            "store_id": "BV-GK1",
            "store_code": "GK1",
            "store_name": "GK-I",
            "voucher_count": 3,
            "xml": "<ENVELOPE><BODY>balanced</BODY></ENVELOPE>",
            "balanced": True,
            "generated_at": "2026-05-08T23:00:00+00:00",
            "consumed": False,
        }
    )
    fake_coll.insert_one(
        {
            "agent_id": "nexus",
            "export_date": datetime(2026, 5, 8, tzinfo=timezone.utc).isoformat(),
            "store_id": "BV-LAJ",
            "store_code": "LAJ",
            "store_name": "Lajpat",
            "voucher_count": 1,
            "xml": "<ENVELOPE><BODY>unbalanced</BODY></ENVELOPE>",
            "balanced": False,
            "balance_check": {"ok": False, "mismatch_count": 1},
            "generated_at": "2026-05-08T23:00:00+00:00",
            "consumed": False,
        }
    )

    monkeypatch.setattr(admin_module, "_tally_exports_collection", lambda: fake_coll)
    return TestClient(app), fake_coll


def test_list_exports_envelope_shape(admin_client, super_token):
    client, _ = admin_client
    r = client.get(
        "/api/v1/admin/integrations/tally/exports?date=2026-05-08",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-05-08"
    assert body["total"] == 2
    by_id = {row["store_id"]: row for row in body["exports"]}
    assert "BV-GK1" in by_id and "BV-LAJ" in by_id
    assert by_id["BV-GK1"]["balanced"] is True
    assert by_id["BV-LAJ"]["balanced"] is False
    # download_url precomputed
    assert by_id["BV-GK1"]["download_url"].startswith(
        "/api/v1/admin/integrations/tally/voucher.xml"
    )
    # heavy XML payload not in list response
    assert "xml" not in by_id["BV-GK1"]


def test_download_endpoint_streams_xml(admin_client, super_token):
    client, _ = admin_client
    r = client.get(
        "/api/v1/admin/integrations/tally/voucher.xml?date=2026-05-08&store_id=BV-GK1",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "GK1_2026-05-08.xml" in r.headers["content-disposition"]
    assert "_UNBALANCED" not in r.headers["content-disposition"]
    assert "balanced" in r.text


def test_download_endpoint_filename_marks_unbalanced(admin_client, super_token):
    client, _ = admin_client
    r = client.get(
        "/api/v1/admin/integrations/tally/voucher.xml?date=2026-05-08&store_id=BV-LAJ",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r.status_code == 200
    assert "LAJ_2026-05-08_UNBALANCED.xml" in r.headers["content-disposition"]


def test_download_endpoint_404s_on_missing(admin_client, super_token):
    client, _ = admin_client
    r = client.get(
        "/api/v1/admin/integrations/tally/voucher.xml?date=2026-05-08&store_id=BV-MUMBAI",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r.status_code == 404


def test_role_gate_rejects_non_admin(admin_client, staff_token):
    """SALES_STAFF gets 403 on every Tally export route."""
    client, _ = admin_client
    for path in [
        "/api/v1/admin/integrations/tally/exports?date=2026-05-08",
        "/api/v1/admin/integrations/tally/voucher.xml?date=2026-05-08&store_id=BV-GK1",
    ]:
        r = client.get(path, headers={"Authorization": f"Bearer {staff_token}"})
        assert r.status_code == 403, f"{path} should be 403 for SALES_STAFF, got {r.status_code}"
