"""
IMS 2.0 - F7 Predictive Purchasing tests
=========================================
Covers the packet's INTENT-level acceptance (T1-T9):

  T1  burn-rate proposal generation (7d velocity -> days_remaining < horizon)
  T2  zero-7d / non-zero-30d fallback (weekly SKU not mistaken for dead)
  T3  separate proposals per store (per (product_id, store_id))
  T4  missing-vendor flag (vendor_missing=True; approve still drafts)
  T5  ADMIN RBAC (200 for ADMIN, 404 for AREA_MANAGER)
  T6  Act On It creates a DRAFT (not SENT) PO + audit row
  T7  missing-vendor surfaces in the payload (not silently dropped)
  T8  proposal type filter
  T9  the 14-day horizon (NOT reorder_point) drives the decision
  + pure-math + divide-by-zero / no-history guards.

CI-ROBUSTNESS: a single in-memory FakeDB stands in for pymongo and supports the
EXACT query shapes ORACLE + ProposalStore run (find with $nin / $gte on
datetime, find_one, insert_one, update_one). EVERY doc a query reads is seeded
in the test so there is no local(no-Mongo)-vs-CI(real-Mongo) fail-soft
divergence. No assertion checks a value's ABSENCE via a whole-JSON substring.

Run: JWT_SECRET_KEY=test .venv/Scripts/python.exe -m \
     pytest backend/tests/test_f7_predictive_purchasing.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.predictive_reorder import (  # noqa: E402
    burn_rates,
    days_remaining,
    recommended_qty,
    projected_stockout_iso,
    tally_demand_by_product_store,
)
from agents.proposals import ProposalStore, ProposalStatus  # noqa: E402
from agents.implementations.oracle import OracleAgent  # noqa: E402


# ============================================================================
# In-memory fake Mongo (supports the exact query shapes F7 code runs)
# ============================================================================


def _cmp_match(actual: Any, cond: Any) -> bool:
    """Match a field value against an equality OR a {$nin/$gte/$lt/$ne} clause."""
    if isinstance(cond, dict):
        for op, operand in cond.items():
            if op == "$nin":
                if actual in operand:
                    return False
            elif op == "$in":
                if actual not in operand:
                    return False
            elif op == "$gte":
                if actual is None or not (actual >= operand):
                    return False
            elif op == "$lt":
                if actual is None or not (actual < operand):
                    return False
            elif op == "$ne":
                if actual == operand:
                    return False
            else:  # unknown operator -> no match (forces a visible failure)
                return False
        return True
    return actual == cond


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in (query or {}).items():
        if k == "$expr":
            # Not used by F7 code; treat as no-match so a regression surfaces.
            return False
        if not _cmp_match(doc.get(k), v):
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

    def sort(self, field, direction=-1):
        rev = direction == -1
        self._docs = sorted(
            self._docs, key=lambda d: (d.get(field) is None, d.get(field)), reverse=rev
        )
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{len(self.docs)}")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def insert_many(self, docs, ordered=True):
        for d in docs:
            self.insert_one(d)
        return type("R", (), {"inserted_ids": []})()

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return _project(d, projection)
        return None

    def find(self, query=None, projection=None):
        matched = [
            _project(d, projection) for d in self.docs if _matches(d, query or {})
        ]
        return FakeCursor(matched)

    def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                if "$set" in update:
                    d.update(update["$set"])
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            new = {}
            for k, v in (query or {}).items():
                if not isinstance(v, dict):
                    new[k] = v
            if "$set" in update:
                new.update(update["$set"])
            self.insert_one(new)
            return type("R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": new["_id"]})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class FakeDB:
    def __init__(self):
        self._colls: Dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        return self._colls.setdefault(name, FakeCollection())


# ============================================================================
# Seed helpers
# ============================================================================

NOW = datetime(2026, 6, 9, 12, 0, 0)  # naive, matches stored created_at frame


def _order(store_id: str, product_id: str, *, days_ago: int, qty: int = 1,
           name: str = "Aviator", brand: str = "Ray-Ban", category: str = "SUNGLASS",
           status: str = "COMPLETED") -> Dict[str, Any]:
    return {
        "order_id": f"ORD-{store_id}-{product_id}-{days_ago}-{qty}",
        "store_id": store_id,
        "status": status,
        "created_at": NOW - timedelta(days=days_ago, hours=1),
        "items": [{
            "product_id": product_id, "quantity": qty,
            "product_name": name, "brand": brand, "category": category,
        }],
    }


def _stock(store_id: str, product_id: str, qty: int, status: str = "AVAILABLE") -> Dict[str, Any]:
    return {"product_id": product_id, "store_id": store_id, "quantity": qty, "status": status}


def _product(product_id: str, *, vendor: Optional[str] = "VEND-1", reorder_point: int = 0) -> Dict[str, Any]:
    d = {"product_id": product_id, "reorder_point": reorder_point}
    if vendor is not None:
        d["preferred_vendor_id"] = vendor
    return d


def _run_oracle(db: FakeDB) -> int:
    """Run ORACLE._propose_reorders with the agent bound to the fake DB.

    Patches now_ist_naive in oracle's namespace so the trailing-window math is
    deterministic against the seeded created_at instants.
    """
    import agents.implementations.oracle as omod
    orig = omod.now_ist_naive
    omod.now_ist_naive = lambda: NOW  # type: ignore
    try:
        agent = OracleAgent(db=db)
        return asyncio.run(agent._propose_reorders())
    finally:
        omod.now_ist_naive = orig  # type: ignore


def _pending(db: FakeDB, ptype: str = "draft_po") -> List[Dict[str, Any]]:
    return [d for d in db.get_collection("ai_proposals").docs
            if d.get("type") == ptype and d.get("status") == ProposalStatus.PENDING.value]


# ============================================================================
# Pure math (no DB)
# ============================================================================


class TestBurnRateMath:
    def test_seven_day_primary(self):
        br = burn_rates(8, 8)
        assert abs(br["burn_rate_7d"] - 8 / 7) < 1e-9
        assert br["effective"] == br["burn_rate_7d"]

    def test_zero_seven_day_falls_back_to_thirty(self):
        br = burn_rates(0, 15)
        assert br["burn_rate_7d"] == 0
        assert abs(br["burn_rate_30d"] - 15 / 30) < 1e-9
        assert br["effective"] == br["burn_rate_30d"]

    def test_days_remaining_divide_by_zero_is_infinite(self):
        # No demand -> never stocks out, no matter how low the stock.
        assert days_remaining(1, 0) == float("inf")
        assert days_remaining(0, 0) == float("inf")

    def test_days_remaining_basic(self):
        assert abs(days_remaining(10, 8 / 7) - 8.75) < 1e-6

    def test_recommended_qty_never_below_one(self):
        # Tiny burn rate but flagged -> still suggest at least 1.
        assert recommended_qty(on_hand=0, effective_rate=0.01, horizon_days=14,
                               lead_time_days=7) >= 1

    def test_recommended_qty_covers_lead_plus_horizon(self):
        # 1/day, on_hand 2, horizon 14 + lead 7 = 21 cover -> order ~19.
        q = recommended_qty(on_hand=2, effective_rate=1.0, horizon_days=14,
                            lead_time_days=7)
        assert q == 19

    def test_projected_stockout_none_when_infinite(self):
        assert projected_stockout_iso(NOW, float("inf")) is None

    def test_projected_stockout_iso_for_finite(self):
        iso = projected_stockout_iso(NOW, 8.75)
        assert iso is not None and iso.startswith("2026-06-")


class TestTallyDemand:
    def test_excludes_cancelled_and_draft(self):
        orders = [
            _order("S1", "P1", days_ago=1, status="CANCELLED"),
            _order("S1", "P1", days_ago=1, status="DRAFT"),
            _order("S1", "P1", days_ago=1, status="COMPLETED"),
        ]
        demand = tally_demand_by_product_store(orders, now=NOW)
        assert demand[("P1", "S1")]["units_7d"] == 1

    def test_no_history_yields_empty(self):
        assert tally_demand_by_product_store([], now=NOW) == {}

    def test_thirty_day_window_excludes_older(self):
        orders = [_order("S1", "P1", days_ago=40)]  # outside 30d
        assert tally_demand_by_product_store(orders, now=NOW) == {}

    def test_seven_vs_thirty_split(self):
        orders = [_order("S1", "P1", days_ago=2)] * 3 + [_order("S1", "P1", days_ago=20)] * 2
        demand = tally_demand_by_product_store(orders, now=NOW)
        d = demand[("P1", "S1")]
        assert d["units_7d"] == 3
        assert d["units_30d"] == 5


# ============================================================================
# T1 - burn-rate proposal generation + dedup
# ============================================================================


class TestT1ProposalGeneration:
    def _seed(self) -> FakeDB:
        db = FakeDB()
        orders = db.get_collection("orders")
        # 8 sales in last 7 days (1 unit each), none in prior 23 days.
        for i in range(8):
            orders.insert_one(_order("S1", "P1", days_ago=i % 7))
        db.get_collection("stock_units").insert_one(_stock("S1", "P1", 10))
        db.get_collection("products").insert_one(_product("P1", vendor="VEND-1"))
        return db

    def test_generates_one_proposal_with_full_payload(self):
        db = self._seed()
        count = _run_oracle(db)
        assert count == 1
        props = _pending(db)
        assert len(props) == 1
        pl = props[0]["payload"]
        assert pl["product_id"] == "P1"
        assert pl["store_id"] == "S1"
        assert pl["burn_rate_7d"] > 0
        assert pl["days_remaining"] < 14
        assert pl["projected_stockout_date"] is not None
        assert pl["vendor_missing"] is False
        assert pl["quantity"] >= 1

    def test_second_run_dedups_same_day(self):
        db = self._seed()
        _run_oracle(db)
        _run_oracle(db)  # same simulated day -> dedup
        assert len(_pending(db)) == 1


# ============================================================================
# T2 - zero-7d / non-zero-30d fallback
# ============================================================================


class TestT2Fallback:
    def _seed(self, on_hand: int) -> FakeDB:
        db = FakeDB()
        orders = db.get_collection("orders")
        # 0 sales in last 7 days; 15 sales spread across days 8..29.
        for i in range(15):
            orders.insert_one(_order("S1", "P1", days_ago=8 + i))
        db.get_collection("stock_units").insert_one(_stock("S1", "P1", on_hand))
        db.get_collection("products").insert_one(_product("P1"))
        return db

    def test_high_stock_no_proposal(self):
        # burn30 = 15/30 = 0.5/day; 5 on hand -> 10 days? No: 5/0.5 = 10 < 14.
        # Use 8 on hand -> 16 days > 14 -> NO proposal.
        db = self._seed(on_hand=8)
        assert _run_oracle(db) == 0
        assert _pending(db) == []

    def test_low_stock_uses_thirty_day_fallback(self):
        # 1 on hand / 0.5 per day = 2 days < 14 -> proposal, with burn_rate_7d=0
        # and a non-zero burn_rate_30d.
        db = self._seed(on_hand=1)
        assert _run_oracle(db) == 1
        pl = _pending(db)[0]["payload"]
        assert pl["burn_rate_7d"] == 0
        assert pl["burn_rate_30d"] > 0
        assert pl["days_remaining"] < 14


# ============================================================================
# T3 - separate proposals per store
# ============================================================================


class TestT3PerStore:
    def test_only_low_store_flagged_and_po_is_store_attributed(self):
        db = FakeDB()
        orders = db.get_collection("orders")
        # Same SKU sells the same way in both stores.
        for store in ("STORE_A", "STORE_B"):
            for i in range(8):
                orders.insert_one(_order(store, "P1", days_ago=i % 7))
        db.get_collection("stock_units").insert_one(_stock("STORE_A", "P1", 3))   # low
        db.get_collection("stock_units").insert_one(_stock("STORE_B", "P1", 20))  # high
        db.get_collection("products").insert_one(_product("P1", vendor="VEND-1"))

        count = _run_oracle(db)
        props = _pending(db)
        assert count == 1
        assert len(props) == 1
        assert props[0]["payload"]["store_id"] == "STORE_A"

        # Approving the store-A proposal creates a DRAFT PO with delivery_store_id.
        store = ProposalStore(db=db)
        res = store.approve(props[0]["proposal_id"], reviewed_by="ceo")
        assert res["ok"] and res["executed"]
        pos = db.get_collection("purchase_orders").docs
        assert len(pos) == 1
        assert pos[0]["delivery_store_id"] == "STORE_A"
        assert pos[0]["status"] == "DRAFT"


# ============================================================================
# T4 / T7 - missing-vendor flag
# ============================================================================


class TestT4T7MissingVendor:
    def _seed_no_vendor(self) -> FakeDB:
        db = FakeDB()
        orders = db.get_collection("orders")
        for i in range(8):
            orders.insert_one(_order("S1", "P1", days_ago=i % 7))
        db.get_collection("stock_units").insert_one(_stock("S1", "P1", 5))
        db.get_collection("products").insert_one(_product("P1", vendor=None))
        return db

    def test_vendor_missing_flag_set(self):
        db = self._seed_no_vendor()
        _run_oracle(db)
        pl = _pending(db)[0]["payload"]
        assert pl["vendor_missing"] is True
        assert pl["vendor_id"] is None

    def test_approve_still_drafts_with_null_vendor(self):
        db = self._seed_no_vendor()
        _run_oracle(db)
        prop = _pending(db)[0]
        res = ProposalStore(db=db).approve(prop["proposal_id"], reviewed_by="ceo")
        assert res["ok"] and res["executed"]
        po = db.get_collection("purchase_orders").docs[0]
        assert po["status"] == "DRAFT"
        assert po["vendor_id"] is None


# ============================================================================
# T9 - 14-day horizon (NOT reorder_point) drives the decision
# ============================================================================


class TestT9HorizonNotReorderPoint:
    def _seed(self, on_hand: int) -> FakeDB:
        db = FakeDB()
        orders = db.get_collection("orders")
        # ~1.5/day: 6 sales over 4 days within the 7-day window.
        for da in (0, 1, 1, 2, 3, 3):
            orders.insert_one(_order("S1", "P1", days_ago=da))
        db.get_collection("stock_units").insert_one(_stock("S1", "P1", on_hand))
        db.get_collection("products").insert_one(_product("P1", vendor="V", reorder_point=20))
        return db

    def test_above_reorder_point_but_above_horizon_no_proposal(self):
        # on_hand 25 > reorder_point 20; burn ~0.857/day (6/7) -> 25/0.857 ~ 29d
        # > 14 -> NO proposal even though old reorder_point logic ignores it.
        db = self._seed(on_hand=25)
        assert _run_oracle(db) == 0
        assert _pending(db) == []

    def test_below_horizon_triggers_even_if_calc_differs_from_reorder_point(self):
        # on_hand 8; burn 6/7=0.857/day -> 8/0.857 ~ 9.3d < 14 -> proposal,
        # despite reorder_point=20 (the horizon, not the threshold, decides).
        db = self._seed(on_hand=8)
        assert _run_oracle(db) == 1
        pl = _pending(db)[0]["payload"]
        assert pl["days_remaining"] < 14
        assert pl["reorder_point"] == 20


# ============================================================================
# T6 - Act On It creates a DRAFT (not SENT) PO + audit row
# ============================================================================


class TestT6DraftNotSent:
    def test_approve_creates_draft_and_audit(self):
        db = FakeDB()
        orders = db.get_collection("orders")
        for i in range(8):
            orders.insert_one(_order("S1", "P1", days_ago=i % 7))
        db.get_collection("stock_units").insert_one(_stock("S1", "P1", 6))
        db.get_collection("products").insert_one(_product("P1", vendor="V"))
        _run_oracle(db)
        prop = _pending(db)[0]
        res = ProposalStore(db=db).approve(prop["proposal_id"], reviewed_by="ceo")
        assert res["ok"] and res["executed"]

        pos = db.get_collection("purchase_orders").docs
        assert len(pos) == 1
        assert pos[0]["status"] == "DRAFT"
        assert all(p["status"] != "SENT" for p in pos)

        audits = db.get_collection("audit_logs").docs
        executed = [a for a in audits if a.get("action") == "ai_proposal_executed"]
        assert len(executed) == 1
        assert executed[0]["proposal_type"] == "draft_po"
        assert executed[0]["before_state"] is not None
        assert executed[0]["after_state"] is not None


# ============================================================================
# T8 - proposal type filter (ProposalStore.list)
# ============================================================================


class TestT8TypeFilter:
    def _seed(self) -> ProposalStore:
        db = FakeDB()
        store = ProposalStore(db=db)
        store.create(created_by_agent="oracle", proposal_type="draft_po",
                     title="po", rationale="r", payload={"sku": "P1"})
        store.create(created_by_agent="megaphone", proposal_type="rx_reminder",
                     title="rx", rationale="r", payload={})
        return store

    def test_filter_draft_po_only(self):
        store = self._seed()
        rows = store.list(proposal_type="draft_po")
        assert len(rows) == 1
        assert all(r["type"] == "draft_po" for r in rows)

    def test_no_filter_returns_all(self):
        store = self._seed()
        rows = store.list()
        assert len(rows) == 2
        assert {r["type"] for r in rows} == {"draft_po", "rx_reminder"}

    def test_filter_other_type(self):
        store = self._seed()
        rows = store.list(proposal_type="rx_reminder")
        assert len(rows) == 1
        assert rows[0]["type"] == "rx_reminder"


# ============================================================================
# T5 - ADMIN RBAC on the HTTP endpoints
# ============================================================================


class TestT5Rbac:
    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from api.main import app
        return TestClient(app)

    def _hdr(self, roles):
        import jwt
        from api.routers import auth as auth_mod
        token = jwt.encode(
            {"sub": "u1", "user_id": "u1", "username": "t", "roles": roles,
             "store_ids": ["S1"], "active_store_id": "S1",
             "exp": datetime.utcnow() + timedelta(hours=1)},
            auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM,
        )
        return {"Authorization": f"Bearer {token}"}

    def test_admin_can_list(self, client):
        r = client.get("/api/v1/jarvis/proposals?type=draft_po&status=PENDING",
                       headers=self._hdr(["ADMIN"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert "proposals" in body
        assert body.get("filter_type") == "draft_po"

    def test_superadmin_can_list(self, client):
        r = client.get("/api/v1/jarvis/proposals?type=draft_po",
                       headers=self._hdr(["SUPERADMIN"]))
        assert r.status_code == 200, r.text

    def test_area_manager_gets_404(self, client):
        r = client.get("/api/v1/jarvis/proposals?type=draft_po",
                       headers=self._hdr(["AREA_MANAGER"]))
        assert r.status_code == 404, r.text

    def test_store_manager_approve_404(self, client):
        r = client.post("/api/v1/jarvis/proposals/PROP-x/approve",
                        headers=self._hdr(["STORE_MANAGER"]))
        assert r.status_code == 404, r.text


# ============================================================================
# No-DB fail-soft (no local-vs-CI divergence: empty inputs never crash)
# ============================================================================


class TestFailSoft:
    def test_oracle_no_db_returns_zero(self):
        agent = OracleAgent(db=None)
        n = asyncio.run(agent._propose_reorders())
        assert n == 0

    def test_oracle_no_orders_returns_zero(self):
        db = FakeDB()
        db.get_collection("orders")  # exists but empty
        assert _run_oracle(db) == 0
