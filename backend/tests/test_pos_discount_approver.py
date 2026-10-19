"""
POS discount approver: decided by the SERVER (owner decision 2026-10-18).
=========================================================================
Before: who approved a discount was a string the till typed
(cart_discount_approved_by / a line's discount_approved_by) -- any till could
write any manager's name. A discount over the seller's ROLE cap was simply
refused, and the E4 engine's discount_override action was used by nothing.

Now (services/discount_approval.py, the ONE rule every order door calls):
  * the seller's own limit covers it -> the seller is the approver;
  * else the order carries the request_id of a discount_override approval the
    SERVER raised for EXACTLY this bill (its hash: every line's product,
    quantity, unit price and %, the bill %, the rupees off) -- approved by
    someone else, still active, whose own role cap covers it; spent once;
  * else a coded refusal the till acts on.
The override lifts ONLY the role cap: category / luxury caps, the HQ-offer
rule and the cost floor still refuse. An HQ offer price is not a store
discount and never needs anyone's approval.

Harness: the order doors over test_pos_cap_failclosed's in-memory repos, and
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
    with managers who hold PINs."""
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
        ("test-staff-999", ["SALES_STAFF"], "Other Staff"),
        ("mgr-001", ["STORE_MANAGER"], "Meera Manager"),
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


def _ask(client, headers, items, **extra):
    """The till's "Ask a manager to approve": the order re-sent with
    request_approval -> the SERVER raises the request (202)."""
    from api.routers import orders as orders_module

    docs = orders_module.get_order_repository().collection.docs
    before = len(docs)
    r = _post(client, headers, items, request_approval=True, **extra)
    assert r.status_code == 202, r.text
    assert len(docs) == before  # asking never saves an order
    return r.json()["request_id"]


def _approve(env, rid, approver="mgr-001", roles=("STORE_MANAGER",), stores=(STORE,)):
    from api.services.approvals import ApprovalEngine

    res = ApprovalEngine(db=env["adb"]).approve(
        rid, approver_user_id=approver, approver_roles=list(roles), pin=PIN,
        approver_store_ids=list(stores))
    assert res["ok"], res
    return rid


def _forged(env, *, approver="mgr-001", roles=("STORE_MANAGER",)):
    """An APPROVED discount_override that matches no bill -- for the tests
    that prove a hard limit refuses BEFORE any approval is even read."""
    from api.services.approvals import ApprovalEngine

    eng = ApprovalEngine(db=env["adb"])
    rid = eng.request(action_type="discount_override", requested_by=STAFF, store_id=STORE,
                      context={"bill_hash": "not-this-bill"})["request_id"]
    return _approve(env, rid, approver, roles)


def _premium(env, pid, **kw):
    return _seed_product(env, pid=pid, discount_category="PREMIUM", **kw)


def _lens(pct, price=5000.0, pid="lens-cl-1"):
    # A contact lens: no product doc (no item cap), exempt from the hard Rx
    # gate -- only the ROLE limit governs it.
    return {"product_id": pid, "product_name": "Monthly CL", "item_type": "CONTACT_LENS",
            "category": "CONTACT_LENS", "quantity": 1, "unit_price": price,
            "discount_percent": pct,
            "discount_reason": "test: lens discount" if pct else None}


# ---------------------------------------------------------------------------
# A. the server decides the approver
# ---------------------------------------------------------------------------


def test_a_typed_approver_is_ignored(client, staff_headers, env):
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
    pid = _premium(env, "FR-MGR-SELF")
    r = _post(client, _token("mgr-001", ["STORE_MANAGER"]), [_item(pid, discount_percent=18)])
    assert r.status_code == 201, r.text
    line = _orders(env)[-1]["items"][0]
    assert line["discount_approved_by"] == "mgr-001"
    assert line["discount_approved_by_name"] == "Meera Manager"


def test_a_sellers_own_override_counts_like_the_till_shows(client, staff_headers, env):
    """An admin raised this cashier's limit to 15% on their account: 12% is
    theirs to give (the till already shows 15%)."""
    env["adb"].get_collection("users").update_one(
        {"user_id": STAFF}, {"$set": {"discount_cap": 15.0}})
    pid = _premium(env, "FR-OVERRIDE")
    r = _post(client, staff_headers, [_item(pid, discount_percent=12)])
    assert r.status_code == 201, r.text
    assert _orders(env)[-1]["items"][0]["discount_approved_by"] == STAFF


def test_a_undiscounted_line_records_no_approver(client, staff_headers, env):
    pid = _premium(env, "FR-NODISC")
    r = _post(client, staff_headers, [_item(pid, discount_percent=0)])
    assert r.status_code == 201, r.text
    saved = _orders(env)[-1]
    assert saved["items"][0]["discount_approved_by"] is None
    assert saved["cart_discount_approved_by"] is None


# ---------------------------------------------------------------------------
# CHECK: an HQ offer price is HQ's price, not a store discount
# ---------------------------------------------------------------------------


def _offer_product(env, pid, offer):
    from database.repositories.product_repository import ProductRepository
    from api.routers import orders as orders_module

    repo = ProductRepository(env["db"].get_collection("products"))
    repo.create({"product_id": pid, "name": "Offer Frame", "category": "FRAME",
                 "mrp": 10000.0, "offer_price": offer, "cost_price": 4000.0,
                 "discount_category": "MASS", "is_active": True})
    env["monkeypatch"].setattr(orders_module, "get_product_repository", lambda: repo)
    return pid


@pytest.mark.parametrize("offer", [8800.0, 8000.0])
def test_check_hq_offer_price_sale_needs_no_approval(client, staff_headers, env, offer):
    """MRP 10,000 offered by HQ at 8,800 (12% -- over a cashier's 10%) or 8,000
    (20% -- over the MASS 15% too): the till sends unit_price = offer, 0%.
    Both refused on main; neither is a store discount."""
    pid = _offer_product(env, f"FR-OFFER-{int(offer)}", offer)
    r = _post(client, staff_headers, [_item(pid, discount_percent=0, unit_price=offer)])
    assert r.status_code == 201, r.text
    line = _orders(env)[-1]["items"][0]
    assert line["discount_approved_by"] is None


def test_check_further_discount_on_an_offer_item_stays_refused(client, staff_headers, env):
    pid = _offer_product(env, "FR-OFFER-MORE", 8800.0)
    r = _post(client, staff_headers, [_item(pid, discount_percent=2, unit_price=8800.0)])
    assert r.status_code == 403, r.text
    assert "already discounted by HQ" in r.text


# ---------------------------------------------------------------------------
# B. over the seller's limit -> refusal -> the SERVER raises the request
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
    assert "approval_request" not in d  # the till never builds a request
    assert not _orders(env)


def test_b_stacked_line_and_bill_discount_counts_together(client, staff_headers, env):
    pid = _premium(env, "FR-STACK")
    r = _post(client, staff_headers, [_item(pid, discount_percent=8)],
              cart_discount_percent=5, cart_discount_reason="test: stacking")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["requested_percent"] == pytest.approx(12.6)


def test_b_wording_never_rounds_down_to_the_limit():
    from api.services import discount_approval as da

    assert da._pct_up(20.004) == 20.01
    bill = da.bill_of(store_id=STORE, lines=[{"product_id": "p", "product_name": "Frame",
                      "quantity": 1, "unit_price": 100.0, "discount_percent": 20.004,
                      "combined_percent": 20.004}], cart_discount_percent=0,
                      discount_rupees=20.0, bill_before=100, bill_after=80)
    exc = da._needs_approval(bill, 20.0)
    assert "20.01%" in exc.detail["message"]


def test_b_the_server_builds_the_request_the_manager_sees(client, staff_headers, env):
    """Ask -> the request carries the SERVER's pricing: names, qty, MRP, unit
    price, line %, bill %, rupees off, totals; the cashier's words are only
    the note. A hand-built discount_override is refused."""
    pid = _premium(env, "FR-BUILT")
    rid = _ask(client, staff_headers, [_item(pid, discount_percent=18)],
               approval_note="customer is a regular")
    doc = _approval(env, rid)
    ctx = doc["context"]
    assert doc["store_id"] == STORE and doc["requested_by"] == STAFF
    assert doc["reason"] == "customer is a regular"
    assert ctx["lines"] == [{"product_id": pid, "product_name": "Priced Frame", "quantity": 1,
                             "mrp": 10000.0, "unit_price": 10000.0, "discount_percent": 18.0}]
    assert ctx["discount_percent"] == 18.0
    assert ctx["discount_rupees"] == 1800.0
    assert ctx["bill_before"] == 10000.0 and ctx["bill_after"] == 8200.0
    assert ctx["requested_by_name"] == "Priya Staff"
    assert len(ctx["bill_hash"]) == 64
    assert "STORE_MANAGER" in doc["required_roles"]

    hand = client.post("/api/v1/approvals/requests", headers=staff_headers, json={
        "action_type": "discount_override", "context": {"bill_hash": ctx["bill_hash"]}})
    assert hand.status_code == 400, hand.text


def test_b_asking_within_your_own_limit_creates_nothing(client, staff_headers, env):
    pid = _premium(env, "FR-NOTNEEDED")
    r = _post(client, staff_headers, [_item(pid, discount_percent=5)], request_approval=True)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "DISCOUNT_APPROVAL_NOT_NEEDED"
    assert not _orders(env)
    assert not env["adb"].get_collection("approval_requests").docs


def test_b_asking_twice_for_the_same_bill_is_one_request(client, staff_headers, env):
    pid = _premium(env, "FR-DEDUPE")
    first = _ask(client, staff_headers, [_item(pid, discount_percent=18)])
    assert _ask(client, staff_headers, [_item(pid, discount_percent=18)]) == first
    _approve(env, first)
    r = _post(client, staff_headers, [_item(pid, discount_percent=18)], request_approval=True)
    assert r.status_code == 202 and r.json() == {
        "code": "DISCOUNT_APPROVAL_REQUESTED", "request_id": first, "status": "APPROVED",
        "bill_hash": _approval(env, first)["context"]["bill_hash"]}


@pytest.mark.parametrize("pct, roles", [
    (18, {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"}),
    (24, {"SUPERADMIN", "ADMIN", "AREA_MANAGER"}),
    (30, {"SUPERADMIN", "ADMIN"}),
])
def test_b_the_request_goes_only_to_roles_whose_cap_covers_it(client, staff_headers, env, pct, roles):
    rid = _ask(client, staff_headers, [_lens(pct)])
    assert set(_approval(env, rid)["required_roles"]) == roles


def test_b_the_whole_e4_flow_then_single_use(client, staff_headers, env):
    """Refused -> ask (server-built) -> a manager approves with their PIN over
    HTTP -> the same sale with the approval id is approved BY THAT MANAGER.
    The same approval cannot pay twice."""
    pid = _premium(env, "FR-FLOW")
    sale = [_item(pid, discount_percent=18)]
    assert _post(client, staff_headers, sale).status_code == 403
    rid = _ask(client, staff_headers, sale)
    ok = client.post(f"/api/v1/approvals/requests/{rid}/approve", json={"pin": PIN},
                     headers=_token("mgr-001", ["STORE_MANAGER"]))
    assert ok.status_code == 200, ok.text

    r = _post(client, staff_headers, sale, discount_approval_id=rid)
    assert r.status_code == 201, r.text
    saved = _orders(env)[-1]
    assert saved["items"][0]["discount_approved_by"] == "mgr-001"
    assert saved["items"][0]["discount_approved_by_name"] == "Meera Manager"
    assert saved["discount_approval_id"] == rid
    doc = _approval(env, rid)
    assert doc["status"] == "CONSUMED" and doc["consumed_by"] == STAFF

    again = _post(client, staff_headers, sale, discount_approval_id=rid)
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "DISCOUNT_APPROVAL_USED"
    assert len(_orders(env)) == 1


# --- BLOCK-1: the approval is bound to the exact bill -------------------------


@pytest.mark.parametrize("case", ["more_frames", "price_changed", "pct_up",
                                  "into_bill_discount", "other_product",
                                  "bill_pct_added", "other_line_qty"])
def test_b_an_approval_covers_exactly_the_bill_it_was_given_for(client, staff_headers, env, case):
    pid = _premium(env, f"FR-EXACT-{case}")
    other = _premium(env, f"FR-OTHER-{case}")
    if case == "into_bill_discount":
        asked = [_lens(18, price=1000.0)]
        sold = [_lens(0, price=1000.0), _lens(0, price=50000.0, pid="lens-cl-big")]
        extra = {"cart_discount_percent": 18, "cart_discount_reason": "test: moved to bill"}
    elif case == "bill_pct_added":
        # Same lines; only a 1% bill discount is added on top.
        asked = sold = [_lens(18)]
        extra = {"cart_discount_percent": 1, "cart_discount_reason": "test: a little more"}
    elif case == "other_line_qty":
        # The approved 18% line is untouched; an UNDISCOUNTED line's qty
        # changes, which moves neither the % nor the rupees off.
        asked = [_lens(18), _lens(0, price=200.0, pid="lens-cl-solution")]
        sold = [_lens(18), {**_lens(0, price=200.0, pid="lens-cl-solution"), "quantity": 2}]
        extra = {}
    else:
        asked = [_item(pid, discount_percent=18)]
        sold = [{
            "more_frames": {**_item(pid, discount_percent=18), "quantity": 5},
            "price_changed": _item(pid, discount_percent=18, unit_price=9000.0),
            "pct_up": _item(pid, discount_percent=19),
            "other_product": _item(other, discount_percent=18),
        }[case]]
        extra = {}
    rid = _approve(env, _ask(client, staff_headers, asked))
    r = _post(client, staff_headers, sold, discount_approval_id=rid, **extra)
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "DISCOUNT_APPROVAL_INVALID"
    assert "different bill" in d["message"]
    assert not _orders(env)
    assert _approval(env, rid)["consumed"] is False


# --- every other misfit ------------------------------------------------------


@pytest.mark.parametrize(
    "case, code, status, why",
    [
        ("other_store", "DISCOUNT_APPROVAL_INVALID", 403, "another store"),
        ("other_requester", "DISCOUNT_APPROVAL_INVALID", 403, "someone else"),
        ("expired", "DISCOUNT_APPROVAL_EXPIRED", 410, "expired"),
        ("not_approved", "DISCOUNT_APPROVAL_INVALID", 403, "not approved"),
        ("not_found", "DISCOUNT_APPROVAL_NOT_FOUND", 404, "not found"),
        ("approver_inactive", "DISCOUNT_APPROVAL_INVALID", 403, "not active"),
    ],
)
def test_b_an_approval_that_does_not_fit_is_refused_and_not_burned(
    client, staff_headers, env, case, code, status, why
):
    from api.services import approvals as appr

    pid = _premium(env, f"FR-BAD-{case}")
    sale = [_item(pid, discount_percent=18)]
    rid = "REQ-doesnotexist"
    if case == "other_store":
        rid = _approve(env, _ask(client, _token(STAFF, ["SALES_STAFF"], ("BV-OTHER-02",)), sale),
                       "admin-001", ("ADMIN",))
    elif case == "other_requester":
        rid = _approve(env, _ask(client, _token("test-staff-999", ["SALES_STAFF"]), sale))
    elif case == "not_approved":
        rid = _ask(client, staff_headers, sale)
    elif case != "not_found":
        rid = _approve(env, _ask(client, staff_headers, sale))
    if case == "expired":
        env["adb"].get_collection("approval_requests").update_one(
            {"request_id": rid}, {"$set": {"expires_at": appr._now() - timedelta(seconds=1)}})
    if case == "approver_inactive":
        env["adb"].get_collection("users").update_one(
            {"user_id": "mgr-001"}, {"$set": {"is_active": False}})

    r = _post(client, staff_headers, sale, discount_approval_id=rid)
    assert r.status_code == status, r.text
    d = r.json()["detail"]
    assert d["code"] == code
    assert why in d["message"]
    assert not _orders(env)
    if case != "not_found":
        assert _approval(env, rid)["consumed"] is False


def test_b_approvals_unavailable_is_its_own_answer(client, staff_headers, env, monkeypatch):
    from api.services import discount_approval

    pid = _premium(env, "FR-NODB")
    monkeypatch.setattr(discount_approval, "_db", lambda: None)
    r = _post(client, staff_headers, [_item(pid, discount_percent=18)],
              discount_approval_id="REQ-any")
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "DISCOUNT_APPROVAL_UNAVAILABLE"


def test_b_self_approval_is_refused_by_e4_and_by_the_order(client, env):
    """A store manager asking for 25% on a lens (over their own 20%) cannot
    PIN-approve their own request, and an approval whose approver IS the
    requester is refused at the till (a row approved before
    discount_override became maker-checker)."""
    from api.services.approvals import ApprovalEngine

    mgr = _token("mgr-001", ["STORE_MANAGER"])
    rid = _ask(client, mgr, [_lens(25)])
    eng = ApprovalEngine(db=env["adb"])
    res = eng.approve(rid, approver_user_id="mgr-001", approver_roles=["ADMIN"], pin=PIN,
                      approver_store_ids=[STORE])
    assert res["ok"] is False and res["error"] == "cannot_approve_own"

    env["adb"].get_collection("approval_requests").update_one(
        {"request_id": rid},
        {"$set": {"status": "APPROVED", "reviewed_by": "mgr-001"}},
    )
    r = _post(client, mgr, [_lens(25)], discount_approval_id=rid)
    assert r.status_code == 403, r.text
    assert "your own" in r.json()["detail"]["message"]


# ---------------------------------------------------------------------------
# C. the override lifts ONLY the role cap -- and only to the approver's own
# ---------------------------------------------------------------------------


def test_c_never_above_the_approving_users_own_cap(client, staff_headers, env):
    """25% is over a store manager's 20%: E4 does not even route it to them,
    an approval they hold anyway is refused, an area manager's is accepted."""
    from api.services.approvals import ApprovalEngine

    rid = _ask(client, staff_headers, [_lens(25)])
    res = ApprovalEngine(db=env["adb"]).approve(
        rid, approver_user_id="mgr-001", approver_roles=["STORE_MANAGER"], pin=PIN,
        approver_store_ids=[STORE])
    assert res["ok"] is False and res["error"] == "insufficient_tier"

    env["adb"].get_collection("approval_requests").update_one(
        {"request_id": rid}, {"$set": {"status": "APPROVED", "reviewed_by": "mgr-001"}})
    r = _post(client, staff_headers, [_lens(25)], discount_approval_id=rid)
    assert r.status_code == 403, r.text
    assert "own limit is 20%" in r.json()["detail"]["message"]
    assert _approval(env, rid)["consumed"] is False

    strong = _approve(env, _ask(client, staff_headers, [_lens(25, pid="lens-cl-2")]),
                      "area-001", ("AREA_MANAGER",))
    r = _post(client, staff_headers, [_lens(25, pid="lens-cl-2")], discount_approval_id=strong)
    assert r.status_code == 201, r.text
    assert _orders(env)[-1]["items"][0]["discount_approved_by"] == "area-001"


def test_c_category_cap_still_refuses_with_an_approval(client, staff_headers, env):
    pid = _seed_product(env, pid="FR-MASS-APPR", discount_category="MASS")
    assert _post(client, staff_headers, [_item(pid, discount_percent=18)],
                 request_approval=True).status_code == 403  # nothing to ask for
    rid = _forged(env)
    r = _post(client, staff_headers, [_item(pid, discount_percent=18)],
              discount_approval_id=rid)
    assert r.status_code == 403, r.text
    assert "category/brand cap" in r.text
    assert _approval(env, rid)["consumed"] is False


def test_c_luxury_brand_cap_still_refuses_with_an_approval(client, staff_headers, env):
    pid = _seed_product(env, pid="FR-CARTIER-APPR", discount_category="LUXURY",
                        brand="Cartier")
    rid = _forged(env)
    r = _post(client, staff_headers, [_item(pid, discount_percent=4, brand="Cartier")],
              discount_approval_id=rid)
    assert r.status_code == 403, r.text
    assert "category/brand cap" in r.text
    assert _approval(env, rid)["consumed"] is False


def test_c_cost_floor_still_refuses_with_an_approval(client, staff_headers, env, monkeypatch):
    from api.services import cost_floor

    monkeypatch.setattr(cost_floor, "_floor_policy", lambda _store: (True, 10.0))
    pid = _premium(env, "FR-FLOOR-APPR", cost_price=8000.0)
    rid = _forged(env)
    r = _post(client, staff_headers, [_item(pid, discount_percent=18)],
              discount_approval_id=rid)
    assert r.status_code == 400, r.text
    assert _approval(env, rid)["consumed"] is False


# ---------------------------------------------------------------------------
# Rs 0 bills (C-4): reason, a MANAGER's sign-off, the audit row -- every door
# ---------------------------------------------------------------------------


def _free_service(pid="custom-svc-adjust"):
    return {"product_id": pid, "product_name": "Frame adjustment",
            "item_type": "SERVICE", "category": "SERVICE", "quantity": 1,
            "unit_price": 0.0}


def _zero_rows(env):
    return [a for a in env["db"].get_collection("audit_logs").docs
            if a.get("action") == "ORDER_ZERO_TOTAL_APPROVED"]


def test_rs0_bill_by_a_cashier_needs_a_managers_approval(client, staff_headers, env):
    sale = [_free_service()]
    r = _post(client, staff_headers, sale, cart_discount_reason="test: free adjustment")
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "DISCOUNT_NEEDS_APPROVAL" and d["zero_total"] is True

    rid = _approve(env, _ask(client, staff_headers, sale,
                             cart_discount_reason="test: free adjustment"))
    assert _approval(env, rid)["context"]["zero_total"] is True
    r = _post(client, staff_headers, sale, cart_discount_reason="test: free adjustment",
              discount_approval_id=rid)
    assert r.status_code == 201, r.text
    saved = _orders(env)[-1]
    assert saved["zero_total"] is True
    assert saved["zero_total_approved_by"] == "mgr-001"
    assert saved["zero_total_approved_by_name"] == "Meera Manager"
    rows = _zero_rows(env)
    assert len(rows) == 1
    assert rows[0]["details"]["approved_by"] == "mgr-001"
    assert rows[0]["details"]["approval_id"] == rid
    assert rows[0]["details"]["reason"] == "test: free adjustment"


def test_rs0_bill_by_a_manager_is_their_own_approval(client, env):
    r = _post(client, _token("mgr-001", ["STORE_MANAGER"]), [_free_service()],
              cart_discount_reason="test: free adjustment")
    assert r.status_code == 201, r.text
    assert _orders(env)[-1]["zero_total_approved_by"] == "mgr-001"


def test_rs0_line_with_no_mrp_in_a_paid_bill_is_a_giveaway(client, staff_headers, env):
    """A Rs 0 line with no MRP to measure from is a 100% line: even on a paid
    bill, a cashier needs a manager for it."""
    pid = _premium(env, "FR-PAID-PLUS-FREE")
    r = _post(client, staff_headers, [_item(pid, discount_percent=0), _free_service()],
              cart_discount_reason="test: free adjustment")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["zero_total"] is True


def _draft(env, order_id, items, **extra):
    from api.routers import orders as orders_module

    orders_module.get_order_repository().create({
        "order_id": order_id, "store_id": STORE, "customer_id": "cust-x",
        "status": "DRAFT", "items": items, "cart_discount_percent": 0.0, **extra})


def _line(item_id, price, *, pid, mrp=None, pct=0.0, reason=None):
    return {"item_id": item_id, "product_id": pid, "product_name": pid, "item_type": "SERVICE",
            "category": "SERVICE", "quantity": 1, "unit_price": price, "mrp": mrp,
            "discount_percent": pct, "discount_amount": 0.0, "item_total": price,
            "discount_reason": reason}


def test_rs0_reached_by_deleting_a_line_needs_a_manager(client, staff_headers, env):
    """M1: a paid line + a free one; deleting the paid line leaves Rs 0."""
    from api.routers import orders as orders_module

    _draft(env, "ORD-DEL-0", [_line("l-paid", 1000.0, pid="custom-paid"),
                              _line("l-free", 0.0, pid="custom-free")],
           grand_total=1000.0, cart_discount_reason="test: free adjustment")
    url = "/api/v1/orders/ORD-DEL-0/items/l-paid"
    r = client.delete(url, headers=staff_headers)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["zero_total"] is True
    assert len(orders_module.get_order_repository().find_by_id("ORD-DEL-0")["items"]) == 2

    asked = client.delete(url + "?request_approval=true", headers=staff_headers)
    assert asked.status_code == 202, asked.text
    rid = _approve(env, asked.json()["request_id"])
    r = client.delete(f"{url}?discount_approval_id={rid}", headers=staff_headers)
    assert r.status_code == 200, r.text
    doc = orders_module.get_order_repository().find_by_id("ORD-DEL-0")
    assert [ln["item_id"] for ln in doc["items"]] == ["l-free"]
    assert doc["zero_total"] is True and doc["zero_total_approved_by"] == "mgr-001"
    assert _approval(env, rid)["status"] == "CONSUMED"
    assert len(_zero_rows(env)) == 1


def test_rs0_100pct_line_via_add_line_sets_zero_total_and_audits(client, env):
    from api.routers import orders as orders_module

    _draft(env, "ORD-ADD-0", [])
    it = {**_free_service("custom-gift"), "unit_price": 500.0, "discount_percent": 100,
          "discount_reason": "test: goodwill gift"}
    r = client.post("/api/v1/orders/ORD-ADD-0/items", json=it,
                    headers=_token("admin-001", ["ADMIN"]))
    assert r.status_code == 200, r.text
    doc = orders_module.get_order_repository().find_by_id("ORD-ADD-0")
    assert doc["zero_total"] is True and doc["zero_total_approved_by"] == "admin-001"
    assert len(_zero_rows(env)) == 1


# ---------------------------------------------------------------------------
# concurrency + give-back
# ---------------------------------------------------------------------------


def test_two_sales_racing_one_approval_only_one_is_saved(client, staff_headers, env, monkeypatch):
    """Both sales pass the approval READ, then race to spend it. The spend is
    one compare-and-set on the approval doc: exactly one sale is saved, the
    other gets 409 DISCOUNT_APPROVAL_USED and leaves nothing behind."""
    from fastapi import HTTPException
    from api.routers.orders import OrderCreate, create_order
    from api.services import discount_approval, lens_stock_hook

    pid = _premium(env, "FR-RACE")
    rid = _approve(env, _ask(client, staff_headers, [_item(pid, discount_percent=18)]))
    reads = []
    real_resolve = discount_approval.resolve_discount_approver

    def counting_resolve(*a, **k):
        out = real_resolve(*a, **k)
        reads.append(out)
        return out

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
        return OrderCreate(customer_id="cust-x", items=[_item(pid, discount_percent=18)],
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


def test_spending_closes_the_sellers_other_open_requests_for_that_bill(client, staff_headers, env):
    from api.services.approvals import ApprovalEngine

    pid = _premium(env, "FR-SIBLING")
    sale = [_item(pid, discount_percent=18)]
    rid = _approve(env, _ask(client, staff_headers, sale))
    bill_hash = _approval(env, rid)["context"]["bill_hash"]
    twin = ApprovalEngine(db=env["adb"]).request(
        action_type="discount_override", requested_by=STAFF, store_id=STORE,
        context={"bill_hash": bill_hash})["request_id"]
    assert _post(client, staff_headers, sale, discount_approval_id=rid).status_code == 201
    assert _approval(env, twin)["status"] == "EXPIRED"


def test_a_save_that_fails_gives_the_approval_back(client, staff_headers, env, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    pid = _premium(env, "FR-GIVEBACK")
    sale = [_item(pid, discount_percent=18)]
    rid = _approve(env, _ask(client, staff_headers, sale))

    def boom(*_a, **_k):
        raise RuntimeError("simulated Mongo write failure")

    monkeypatch.setattr(OrderRepository, "create_unique", boom)
    r = _post(client, staff_headers, sale, discount_approval_id=rid)
    assert r.status_code == 500, r.text
    doc = _approval(env, rid)
    assert doc["status"] == "APPROVED" and doc["consumed"] is False


def test_a_sale_that_did_save_never_re_arms_its_approval(client, staff_headers, env, monkeypatch):
    """The write landed but the call still raised: the approval stays spent."""
    from database.repositories.order_repository import OrderRepository

    pid = _premium(env, "FR-SAVED-THEN-RAISED")
    sale = [_item(pid, discount_percent=18)]
    rid = _approve(env, _ask(client, staff_headers, sale))
    real = OrderRepository.create_unique

    def saved_then_raised(self, doc, **kw):
        real(self, doc, **kw)
        raise RuntimeError("simulated timeout after the write")

    monkeypatch.setattr(OrderRepository, "create_unique", saved_then_raised)
    r = _post(client, staff_headers, sale, discount_approval_id=rid)
    assert r.status_code == 500, r.text
    assert _approval(env, rid)["status"] == "CONSUMED"


# ---------------------------------------------------------------------------
# D. the add-line door runs the same rule
# ---------------------------------------------------------------------------


def test_d_add_line_door_uses_the_same_rule(client, staff_headers, env):
    from api.routers import orders as orders_module

    pid = _premium(env, "FR-ADD")
    _draft(env, "ORD-ADD-APPR", [])
    it = _item(pid, discount_percent=18)
    it["discount_approved_by"] = "mgr-forged"
    url = "/api/v1/orders/ORD-ADD-APPR/items"
    r = client.post(url, json=it, headers=staff_headers)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "DISCOUNT_NEEDS_APPROVAL"

    asked = client.post(url + "?request_approval=true", json=it, headers=staff_headers)
    assert asked.status_code == 202, asked.text
    rid = _approve(env, asked.json()["request_id"])
    r = client.post(f"{url}?discount_approval_id={rid}", json=it, headers=staff_headers)
    assert r.status_code == 200, r.text
    line = orders_module.get_order_repository().find_by_id("ORD-ADD-APPR")["items"][-1]
    assert line["discount_approved_by"] == "mgr-001"
    assert _approval(env, rid)["status"] == "CONSUMED"


def test_d_add_line_counts_the_drafts_bill_discount_too(client, staff_headers, env):
    """M2: a 5% line on a draft already carrying an 8% bill discount is a
    12.6% sale -- over a cashier's 10% though each part is under it."""
    pid = _premium(env, "FR-ADD-STACK")
    _draft(env, "ORD-ADD-STACK", [], cart_discount_percent=8.0,
           cart_discount_reason="test: bill discount first")
    r = client.post("/api/v1/orders/ORD-ADD-STACK/items",
                    json=_item(pid, discount_percent=5), headers=staff_headers)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["requested_percent"] == pytest.approx(12.6)


def test_d_add_line_gives_back_the_unit_when_the_spend_fails(client, staff_headers, env, monkeypatch):
    """LOW-3: ANY failure at the spend releases the claimed unit + lens cell."""
    from api.routers.orders import items as items_door
    from api.services import discount_approval

    pid = _premium(env, "FR-ADD-SPENDFAIL")
    _draft(env, "ORD-ADD-SPENDFAIL", [])
    released = []
    monkeypatch.setattr(items_door, "_release_claimed_units",
                        lambda oid, claimed: released.append(oid))

    def broken_spend(*_a, **_k):
        raise RuntimeError("simulated approvals outage")

    monkeypatch.setattr(discount_approval, "spend", broken_spend)
    with pytest.raises(RuntimeError):
        client.post("/api/v1/orders/ORD-ADD-SPENDFAIL/items",
                    json=_item(pid, discount_percent=5), headers=staff_headers)
    assert released == ["ORD-ADD-SPENDFAIL"]


def test_the_spend_itself_refuses_a_different_bill(client, staff_headers, env):
    """The atomic spend re-checks the bill hash on its own (not only the
    read before it): a mismatched spend is refused and leaves it unspent."""
    from fastapi import HTTPException
    from api.services import discount_approval

    pid = _premium(env, "FR-SPEND-HASH")
    rid = _approve(env, _ask(client, staff_headers, [_item(pid, discount_percent=18)]))
    user = {"user_id": STAFF, "roles": ["SALES_STAFF"]}
    with pytest.raises(HTTPException) as exc:
        discount_approval.spend({"approval_id": rid, "bill_hash": "f" * 64}, user, STORE)
    assert exc.value.status_code == 409
    assert _approval(env, rid)["consumed"] is False
    good = _approval(env, rid)["context"]["bill_hash"]
    discount_approval.spend({"approval_id": rid, "bill_hash": good}, user, STORE)
    assert _approval(env, rid)["status"] == "CONSUMED"


# ---------------------------------------------------------------------------
# Round-2 review LOWs: whose bill, whose names, a Rs 0 reason, waiting cap
# ---------------------------------------------------------------------------


def test_an_approval_is_for_one_customers_bill(client, staff_headers, env):
    """The same lines rung for another customer are a different bill."""
    from api.routers import orders as orders_module

    orders_module.get_customer_repository().create(
        {"customer_id": "cust-y", "name": "Other", "mobile": "9100000098", "phone": "9100000098"})
    pid = _premium(env, "FR-WHOSE")
    sale = [_item(pid, discount_percent=18)]
    rid = _approve(env, _ask(client, staff_headers, sale))
    ctx = _approval(env, rid)["context"]
    assert ctx["customer_id"] == "cust-x" and ctx["customer_name"] == "Test"
    r = client.post("/api/v1/orders", headers=staff_headers, json={
        "customer_id": "cust-y", "items": sale, "discount_approval_id": rid})
    assert r.status_code == 403, r.text
    assert "different bill" in r.json()["detail"]["message"]
    assert _approval(env, rid)["consumed"] is False


def test_the_manager_sees_the_catalogue_name_not_the_tills(client, staff_headers, env):
    pid = _premium(env, "FR-NAMED")
    it = {**_item(pid, discount_percent=18), "product_name": "anything the till typed"}
    rid = _ask(client, staff_headers, [it])
    assert _approval(env, rid)["context"]["lines"][0]["product_name"] == "Priced Frame"


def test_a_typed_line_renamed_after_approval_is_a_different_bill(client, staff_headers, env):
    """A lens / custom line has no catalogue name: the till's name is part of
    the bill the manager approved."""
    rid = _approve(env, _ask(client, staff_headers, [_lens(18)]))
    renamed = {**_lens(18), "product_name": "Premium Progressive Pair"}
    r = _post(client, staff_headers, [renamed], discount_approval_id=rid)
    assert r.status_code == 403, r.text
    assert "different bill" in r.json()["detail"]["message"]


def test_a_manager_can_take_a_draft_to_rs0_with_a_reason(client, env):
    """LOW-4: the draft carries no reason of its own; the delete door takes
    one. Without it the door still refuses."""
    from api.routers import orders as orders_module

    _draft(env, "ORD-DEL-R", [_line("l-paid", 1000.0, pid="custom-paid"),
                              _line("l-free", 0.0, pid="custom-free")], grand_total=1000.0)
    mgr = _token("mgr-001", ["STORE_MANAGER"])
    url = "/api/v1/orders/ORD-DEL-R/items/l-paid"
    r = client.delete(url, headers=mgr)
    assert r.status_code == 400, r.text
    assert "reason" in r.text.lower()
    r = client.delete(url + "?reason=goodwill%20replacement", headers=mgr)
    assert r.status_code == 200, r.text
    doc = orders_module.get_order_repository().find_by_id("ORD-DEL-R")
    assert doc["zero_total"] is True and doc["zero_total_approved_by"] == "mgr-001"
    assert _zero_rows(env)[-1]["details"]["reason"] == "goodwill replacement"


def test_a_cashier_may_have_three_requests_waiting_at_once(client, staff_headers, env):
    """LOW-5: a fourth waiting request is refused 429; asking again for a bill
    already waiting is not a new request."""
    for pct in (12, 13, 14):
        _ask(client, staff_headers, [_lens(pct)])
    assert _ask(client, staff_headers, [_lens(12)])  # same bill: handed back
    r = _post(client, staff_headers, [_lens(15)], request_approval=True)
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "DISCOUNT_APPROVAL_TOO_MANY"
    assert "3 requests waiting" in r.json()["detail"]["message"]
    assert len(env["adb"].get_collection("approval_requests").docs) == 3
