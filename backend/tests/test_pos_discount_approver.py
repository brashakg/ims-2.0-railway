"""
POS discount approver: decided by the SERVER (owner decision 2026-10-18).
=========================================================================
Before: who approved a discount was a string the till typed
(cart_discount_approved_by / a line's discount_approved_by) -- any till could
write any manager's name. A discount over the seller's ROLE cap was simply
refused, and the E4 engine's discount_override action was used by nothing.

Now (services/discount_approval.py, the ONE rule both order doors call):
  * the seller's own role cap covers it -> the seller is the approver;
  * else the order carries the request_id of an E4 discount_override approval
    -- APPROVED, unspent, unexpired, raised by THIS seller for THIS store and
    cart, for at least this %, approved by SOMEONE ELSE whose own role cap
    covers it -- and that manager is the approver; it is spent exactly once;
  * else 403 DISCOUNT_NEEDS_APPROVAL (structured, the till asks a manager).
The override lifts ONLY the role cap: category / luxury-brand caps and the
cost floor still refuse.

Harness: the order door over test_pos_cap_failclosed's in-memory repos, and
the REAL ApprovalEngine over test_e4_approval_pin's faithful fake Mongo
(services/discount_approval._db is pointed at it).

No emoji in this file (Windows cp1252).
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from test_pos_cap_failclosed import (  # noqa: E402
    _item,
    _post,
    _seed_product,
    priced_orders,  # noqa: F401  (pytest fixture)
)
from test_e4_approval_pin import FakeDB as ApprovalsDB  # noqa: E402

STORE = "BV-TEST-01"
STAFF = "test-staff-001"  # conftest staff_headers: SALES_STAFF, 10% cap
PIN = "4826"


def _token(user_id, roles, store_ids=(STORE,)):
    from api.routers.auth import create_access_token

    tok = create_access_token({
        "user_id": user_id, "username": user_id, "roles": list(roles),
        "store_ids": list(store_ids), "active_store_id": store_ids[0],
    })
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def env(priced_orders, monkeypatch):
    """Orders over fakes + the approvals engine over a faithful fake Mongo,
    with a store manager / an area manager who hold PINs."""
    from api.routers.auth import hash_password
    from api.routers import approvals as approvals_router
    from api.services import discount_approval

    adb = ApprovalsDB()
    monkeypatch.setattr(discount_approval, "_db", lambda: adb)
    monkeypatch.setattr(approvals_router, "_get_db", lambda: adb)
    users = adb.get_collection("users")
    pin_hash = hash_password(PIN)
    for uid, roles, name in (
        (STAFF, ["SALES_STAFF"], "Priya Staff"),
        ("mgr-001", ["STORE_MANAGER"], "Meera Manager"),
        ("mgr-002", ["STORE_MANAGER"], "Second Manager"),
        ("area-001", ["AREA_MANAGER"], "Arun Area"),
        ("admin-001", ["ADMIN"], "Asha Admin"),
    ):
        users.insert_one({
            "user_id": uid, "roles": roles, "full_name": name, "is_active": True,
            "store_ids": [STORE], "approval_pin_hash": pin_hash,
        })
    return {**priced_orders, "adb": adb}


def _orders(env):
    return env["db"].get_collection("orders").docs


def _approval(env, request_id):
    return env["adb"].get_collection("approval_requests").find_one({"request_id": request_id})


def _approved(env, *, pct, product_ids, requested_by=STAFF, store=STORE,
              approver="mgr-001", approver_roles=("STORE_MANAGER",), zero_total=False):
    """An E4 discount_override approval through the REAL engine."""
    from api.services.approvals import ApprovalEngine

    eng = ApprovalEngine(db=env["adb"])
    req = eng.request(
        action_type="discount_override", requested_by=requested_by,
        requested_by_roles=["SALES_STAFF"], store_id=store,
        context={"discount_percent": pct, "zero_total": zero_total,
                 "product_ids": product_ids},
        reason="test: customer asks for more",
    )
    res = eng.approve(req["request_id"], approver_user_id=approver,
                      approver_roles=list(approver_roles), pin=PIN,
                      approver_store_ids=[STORE])
    assert res["ok"], res
    return req["request_id"]


def _premium(env, pid, **kw):
    return _seed_product(env, pid=pid, discount_category="PREMIUM", **kw)


# ---------------------------------------------------------------------------
# A. the server decides the approver
# ---------------------------------------------------------------------------


def test_a_typed_approver_is_ignored(client, staff_headers, env):
    """Within the seller's own 10%: approved by the SELLER. The typed names
    are not stored anywhere on the order."""
    pid = _premium(env, "FR-TYPED")
    it = _item(pid, discount_percent=5)
    it["discount_approved_by"] = "mgr-forged-line"
    r = _post(client, staff_headers, [it], cart_discount_percent=4,
              cart_discount_reason="test: loyal customer",
              cart_discount_approved_by="mgr-forged-cart")
    assert r.status_code == 201, r.text
    saved = _orders(env)[-1]
    assert saved["cart_discount_approved_by"] == STAFF
    assert saved["cart_discount_approved_by_name"] == "Priya Staff"
    assert saved["items"][0]["discount_approved_by"] == STAFF
    assert "forged" not in repr(saved)


def test_a_manager_self_approves_within_own_cap(client, env):
    """A store manager's 18% on a PREMIUM (20%) frame: their own 20% covers
    it, so they are the approver -- id and name from the user record."""
    pid = _premium(env, "FR-MGR-SELF")
    r = _post(client, _token("mgr-001", ["STORE_MANAGER"]),
              [_item(pid, discount_percent=18)])
    assert r.status_code == 201, r.text
    line = _orders(env)[-1]["items"][0]
    assert line["discount_approved_by"] == "mgr-001"
    assert line["discount_approved_by_name"] == "Meera Manager"


def test_a_undiscounted_line_records_no_approver(client, staff_headers, env):
    pid = _premium(env, "FR-NODISC")
    r = _post(client, staff_headers, [_item(pid, discount_percent=0)])
    assert r.status_code == 201, r.text
    saved = _orders(env)[-1]
    assert saved["items"][0]["discount_approved_by"] is None
    assert saved["cart_discount_approved_by"] is None


# ---------------------------------------------------------------------------
# B. over the seller's cap -> a structured refusal the till acts on
# ---------------------------------------------------------------------------


def test_b_cashier_over_cap_gets_the_structured_refusal(client, staff_headers, env):
    pid = _premium(env, "FR-OVER")
    r = _post(client, staff_headers, [_item(pid, discount_percent=18)])
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "DISCOUNT_NEEDS_APPROVAL"
    assert d["requested_percent"] == 18.0
    assert d["max_percent"] == 10.0
    assert [ln["product_id"] for ln in d["lines"]] == [pid]
    req = d["approval_request"]
    assert req["action_type"] == "discount_override"
    assert req["store_id"] == STORE
    assert req["context"] == {"discount_percent": 18.0, "zero_total": False,
                              "product_ids": pid}
    assert not _orders(env)  # nothing saved


def test_b_stacked_line_and_bill_discount_counts_together(client, staff_headers, env):
    """8% line x 5% bill = 12.6%: each under 10%, the product over it."""
    pid = _premium(env, "FR-STACK")
    r = _post(client, staff_headers, [_item(pid, discount_percent=8)],
              cart_discount_percent=5, cart_discount_reason="test: stacking")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["requested_percent"] == pytest.approx(12.6)


def test_b_the_whole_e4_flow_then_single_use(client, staff_headers, env):
    """Refused -> the till opens THE E4 request it was handed -> a manager
    approves with their PIN -> the till resubmits with the approval id and the
    sale is approved BY THAT MANAGER. The same approval cannot pay twice."""
    pid = _premium(env, "FR-FLOW")
    refused = _post(client, staff_headers, [_item(pid, discount_percent=18)])
    body = {**refused.json()["detail"]["approval_request"], "reason": "test: price match"}
    made = client.post("/api/v1/approvals/requests", json=body, headers=staff_headers)
    assert made.status_code == 200, made.text
    rid = made.json()["request_id"]

    ok = client.post(f"/api/v1/approvals/requests/{rid}/approve", json={"pin": PIN},
                     headers=_token("mgr-001", ["STORE_MANAGER"]))
    assert ok.status_code == 200, ok.text

    r = _post(client, staff_headers, [_item(pid, discount_percent=18)],
              discount_approval_id=rid)
    assert r.status_code == 201, r.text
    saved = _orders(env)[-1]
    assert saved["items"][0]["discount_approved_by"] == "mgr-001"
    assert saved["items"][0]["discount_approved_by_name"] == "Meera Manager"
    assert saved["discount_approval_id"] == rid
    doc = _approval(env, rid)
    assert doc["status"] == "CONSUMED" and doc["consumed_by"] == STAFF

    again = _post(client, staff_headers, [_item(pid, discount_percent=18)],
                  discount_approval_id=rid)
    assert again.status_code == 403, again.text
    assert again.json()["detail"]["code"] == "DISCOUNT_APPROVAL_INVALID"
    assert "already been used" in again.json()["detail"]["message"]
    assert len(_orders(env)) == 1


@pytest.mark.parametrize(
    "case, why",
    [
        ("other_store", "another store"),
        ("other_requester", "someone else"),
        ("expired", "expired"),
        ("smaller_pct", "covers 15%"),
        ("other_cart", "different bill"),
        ("not_approved", "not approved"),
    ],
)
def test_b_an_approval_that_does_not_fit_is_refused_and_not_burned(
    client, staff_headers, env, case, why
):
    from api.services import approvals as appr
    from api.services.approvals import ApprovalEngine

    pid = _premium(env, f"FR-BAD-{case}")
    kw = {"pct": 18.0, "product_ids": pid}
    if case == "other_store":
        # An HQ admin may approve any store's request -- just not for THIS one.
        kw.update(store="BV-OTHER-02", approver="admin-001", approver_roles=("ADMIN",))
    if case == "other_requester":
        kw["requested_by"] = "test-staff-999"
    if case == "smaller_pct":
        kw["pct"] = 15.0
    if case == "other_cart":
        kw["product_ids"] = "SOME-OTHER-FRAME"
    if case == "not_approved":
        rid = ApprovalEngine(db=env["adb"]).request(
            action_type="discount_override", requested_by=STAFF, store_id=STORE,
            context={"discount_percent": 18.0, "zero_total": False, "product_ids": pid},
        )["request_id"]
    else:
        rid = _approved(env, **kw)
    if case == "expired":
        env["adb"].get_collection("approval_requests").update_one(
            {"request_id": rid}, {"$set": {"expires_at": appr._now() - timedelta(seconds=1)}}
        )
    before = dict(_approval(env, rid))

    r = _post(client, staff_headers, [_item(pid, discount_percent=18)],
              discount_approval_id=rid)
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "DISCOUNT_APPROVAL_INVALID"
    assert why in d["message"]
    assert not _orders(env)
    after = _approval(env, rid)
    assert after["consumed"] is False and after["status"] == before["status"]


def test_b_self_approval_is_refused_by_e4_and_by_the_order(client, env):
    """A store manager asking for 25% on a lens (over their own 20%) cannot
    PIN-approve their own request (E4 maker-checker), and an approval whose
    approver IS the requester is refused at the till (a row approved before
    discount_override became maker-checker)."""
    from api.services.approvals import ApprovalEngine

    eng = ApprovalEngine(db=env["adb"])
    req = eng.request(action_type="discount_override", requested_by="mgr-001",
                      store_id=STORE,
                      context={"discount_percent": 25.0, "zero_total": False,
                               "product_ids": "lens-sv-1"})
    res = eng.approve(req["request_id"], approver_user_id="mgr-001",
                      approver_roles=["STORE_MANAGER"], pin=PIN,
                      approver_store_ids=[STORE])
    assert res["ok"] is False and res["error"] == "cannot_approve_own"

    # Force the legacy shape: APPROVED by its own requester.
    env["adb"].get_collection("approval_requests").update_one(
        {"request_id": req["request_id"]},
        {"$set": {"status": "APPROVED", "reviewed_by": "mgr-001",
                  "expires_at": eng._raw(req["request_id"])["expires_at"]}},
    )
    lens = {"product_id": "lens-sv-1", "product_name": "SV Lens", "item_type": "CONTACT_LENS",
            "category": "CONTACT_LENS", "quantity": 1, "unit_price": 4000.0,
            "discount_percent": 25, "discount_reason": "test: own approval"}
    r = _post(client, _token("mgr-001", ["STORE_MANAGER"]), [lens],
              discount_approval_id=req["request_id"])
    assert r.status_code == 403, r.text
    assert "your own" in r.json()["detail"]["message"]


# ---------------------------------------------------------------------------
# C. the override lifts ONLY the role cap -- and only to the approver's own
# ---------------------------------------------------------------------------


def _lens(pct):
    # A contact lens: no product doc (no item cap), exempt from the hard Rx
    # gate -- only the ROLE limit governs it.
    return {"product_id": "lens-cl-1", "product_name": "Monthly CL", "item_type": "CONTACT_LENS",
            "category": "CONTACT_LENS", "quantity": 1, "unit_price": 5000.0,
            "discount_percent": pct, "discount_reason": "test: lens discount"}


def test_c_never_above_the_approving_users_own_cap(client, staff_headers, env):
    """25% needs more than a store manager's 20%: their approval is refused;
    an area manager's (25%) is accepted."""
    weak = _approved(env, pct=25.0, product_ids="lens-cl-1")
    r = _post(client, staff_headers, [_lens(25)], discount_approval_id=weak)
    assert r.status_code == 403, r.text
    assert "own limit is 20%" in r.json()["detail"]["message"]
    assert _approval(env, weak)["consumed"] is False

    strong = _approved(env, pct=25.0, product_ids="lens-cl-1",
                       approver="area-001", approver_roles=("AREA_MANAGER",))
    r = _post(client, staff_headers, [_lens(25)], discount_approval_id=strong)
    assert r.status_code == 201, r.text
    assert _orders(env)[-1]["items"][0]["discount_approved_by"] == "area-001"


def test_c_category_cap_still_refuses_with_an_approval(client, staff_headers, env):
    """MASS caps at 15%: an 18% approval does not lift it (and is not spent)."""
    pid = _seed_product(env, pid="FR-MASS-APPR", discount_category="MASS")
    rid = _approved(env, pct=18.0, product_ids=pid)
    r = _post(client, staff_headers, [_item(pid, discount_percent=18)],
              discount_approval_id=rid)
    assert r.status_code == 403, r.text
    assert "category/brand cap" in r.text
    assert _approval(env, rid)["consumed"] is False


def test_c_luxury_brand_cap_still_refuses_with_an_approval(client, staff_headers, env):
    pid = _seed_product(env, pid="FR-CARTIER-APPR", discount_category="LUXURY",
                        brand="Cartier")
    rid = _approved(env, pct=4.0, product_ids=pid)
    r = _post(client, staff_headers, [_item(pid, discount_percent=4, brand="Cartier")],
              discount_approval_id=rid)
    assert r.status_code == 403, r.text
    assert "category/brand cap" in r.text
    assert _approval(env, rid)["consumed"] is False


def test_c_cost_floor_still_refuses_with_an_approval(client, staff_headers, env, monkeypatch):
    from api.services import cost_floor

    monkeypatch.setattr(cost_floor, "_floor_policy", lambda _store: (True, 10.0))
    pid = _premium(env, "FR-FLOOR-APPR", cost_price=8000.0)
    rid = _approved(env, pct=18.0, product_ids=pid)
    r = _post(client, staff_headers, [_item(pid, discount_percent=18)],
              discount_approval_id=rid)
    assert r.status_code == 400, r.text
    assert _approval(env, rid)["consumed"] is False


# ---------------------------------------------------------------------------
# Rs 0 bills (C-4): reason still required, manager approval, audit row
# ---------------------------------------------------------------------------


def _free_service():
    return {"product_id": "custom-svc-adjust", "product_name": "Frame adjustment",
            "item_type": "SERVICE", "category": "SERVICE", "quantity": 1,
            "unit_price": 0.0}


def test_rs0_bill_by_a_cashier_needs_a_managers_approval(client, staff_headers, env):
    r = _post(client, staff_headers, [_free_service()],
              cart_discount_reason="test: free adjustment")
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "DISCOUNT_NEEDS_APPROVAL" and d["zero_total"] is True
    assert d["approval_request"]["context"]["zero_total"] is True

    rid = _approved(env, pct=d["requested_percent"], zero_total=True,
                    product_ids="custom-svc-adjust")
    r = _post(client, staff_headers, [_free_service()],
              cart_discount_reason="test: free adjustment", discount_approval_id=rid)
    assert r.status_code == 201, r.text
    saved = _orders(env)[-1]
    assert saved["zero_total"] is True
    assert saved["zero_total_approved_by"] == "mgr-001"
    assert saved["zero_total_approved_by_name"] == "Meera Manager"
    rows = [a for a in env["db"].get_collection("audit_logs").docs
            if a.get("action") == "ORDER_ZERO_TOTAL_APPROVED"]
    assert len(rows) == 1
    assert rows[0]["details"]["approved_by"] == "mgr-001"
    assert rows[0]["details"]["approval_id"] == rid
    assert rows[0]["details"]["reason"] == "test: free adjustment"


def test_rs0_bill_by_a_manager_is_their_own_approval(client, env):
    r = _post(client, _token("mgr-001", ["STORE_MANAGER"]), [_free_service()],
              cart_discount_reason="test: free adjustment")
    assert r.status_code == 201, r.text
    assert _orders(env)[-1]["zero_total_approved_by"] == "mgr-001"


# ---------------------------------------------------------------------------
# concurrency: two sales racing one approval -> one sale
# ---------------------------------------------------------------------------


def test_two_sales_racing_one_approval_only_one_is_saved(env, monkeypatch):
    """Both sales pass the approval READ, then race to spend it. The spend is
    one compare-and-set on the approval doc: exactly one sale is saved, the
    other gets 409 DISCOUNT_APPROVAL_USED and leaves nothing behind."""
    from fastapi import HTTPException
    from api.routers.orders import OrderCreate, create_order
    from api.services import lens_stock_hook

    pid = _premium(env, "FR-RACE")
    rid = _approved(env, pct=18.0, product_ids=pid)
    reads = []

    real_resolve = __import__(
        "api.services.discount_approval", fromlist=["x"]
    ).resolve_discount_approver

    def counting_resolve(*a, **k):
        out = real_resolve(*a, **k)
        reads.append(out)
        return out

    from api.services import discount_approval
    monkeypatch.setattr(discount_approval, "resolve_discount_approver", counting_resolve)

    async def yielding_reserve(**_kw):
        # Hand the loop to the other sale between its approval READ and its
        # spend -- the window two tills share in real life.
        await asyncio.sleep(0)
        return None

    monkeypatch.setattr(lens_stock_hook, "reserve_for_order_item", yielding_reserve)
    user = {"user_id": STAFF, "roles": ["SALES_STAFF"], "active_store_id": STORE,
            "store_ids": [STORE], "username": STAFF}

    def _order():
        return OrderCreate(customer_id="cust-x",
                           items=[_item(pid, discount_percent=18)],
                           discount_approval_id=rid)

    async def race():
        return await asyncio.gather(
            create_order(order=_order(), current_user=user, idempotency_key=None),
            create_order(order=_order(), current_user=user, idempotency_key=None),
            return_exceptions=True,
        )

    results = asyncio.run(race())
    assert len(reads) == 2 and all(r and r["approval_id"] == rid for r in reads)
    wins = [r for r in results if isinstance(r, dict)]
    losses = [r for r in results if isinstance(r, HTTPException)]
    assert len(wins) == 1 and len(losses) == 1, results
    assert losses[0].status_code == 409
    assert losses[0].detail["code"] == "DISCOUNT_APPROVAL_USED"
    assert len(_orders(env)) == 1
    assert _approval(env, rid)["status"] == "CONSUMED"


def test_a_save_that_fails_gives_the_approval_back(client, staff_headers, env, monkeypatch):
    """Spent, then the order write fails: the approval is APPROVED again, so
    the retry can still use it -- it never covered a sale that was not saved."""
    from database.repositories.order_repository import OrderRepository

    pid = _premium(env, "FR-GIVEBACK")
    rid = _approved(env, pct=18.0, product_ids=pid)

    def boom(*_a, **_k):
        raise RuntimeError("simulated Mongo write failure")

    monkeypatch.setattr(OrderRepository, "create_unique", boom)
    r = _post(client, staff_headers, [_item(pid, discount_percent=18)],
              discount_approval_id=rid)
    assert r.status_code == 500, r.text
    doc = _approval(env, rid)
    assert doc["status"] == "APPROVED" and doc["consumed"] is False


# ---------------------------------------------------------------------------
# D. the add-item door runs the same rule
# ---------------------------------------------------------------------------


def test_d_add_item_door_uses_the_same_rule(client, staff_headers, env):
    from api.routers import orders as orders_module

    pid = _premium(env, "FR-ADD")
    orders_module.get_order_repository().create({
        "order_id": "ORD-ADD-APPR", "store_id": STORE, "customer_id": "cust-x",
        "status": "DRAFT", "items": [], "cart_discount_percent": 0.0,
    })
    it = _item(pid, discount_percent=18)
    it["discount_approved_by"] = "mgr-forged"
    r = client.post("/api/v1/orders/ORD-ADD-APPR/items", json=it, headers=staff_headers)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "DISCOUNT_NEEDS_APPROVAL"

    rid = _approved(env, pct=18.0, product_ids=pid)
    r = client.post(f"/api/v1/orders/ORD-ADD-APPR/items?discount_approval_id={rid}",
                    json=it, headers=staff_headers)
    assert r.status_code == 200, r.text
    line = orders_module.get_order_repository().find_by_id("ORD-ADD-APPR")["items"][-1]
    assert line["discount_approved_by"] == "mgr-001"
    assert _approval(env, rid)["status"] == "CONSUMED"
