"""'rtv' approvals are the accounts roles' -- owner ruling 2026-10-12 (R1).

Owner ruling (extending 2026-10-01 "supplier balances should not be shown to
anyone apart from admin superadmin and accountant"): a Return-to-Vendor ('rtv')
approval -- the supplier credit raised on a vendor RMA credit note -- is routed
to ADMIN / SUPERADMIN / ACCOUNTANT, who see its amount. Store and area managers
no longer approve one.

What this file pins, through the REAL ApprovalEngine and the REAL approvals
router on an in-memory Mongo (mongomock), at EVERY amount tier:
  * a new 'rtv' request is routed to SUPERADMIN + cost_mask.AP_ROLES;
  * a STORE_MANAGER / AREA_MANAGER finds none in the inbox, and opening,
    approving, rejecting or spending one by id answers exactly what a missing
    request answers (404) -- the request is left as it was, the PIN throttle
    untouched; its own maker still reads it, amount-free;
  * an ACCOUNTANT / ADMIN lists, opens, approves, rejects and spends one, with
    the amount; an accountant sets their own approval PIN and uses it;
  * a request raised BEFORE the ruling (stored with the manager tier's roles)
    routes the same way;
  * every other action type is unchanged for the managers and still refused
    to the accountant;
  * the approve / reject route gate and its rbac_policy row are the same set,
    and the request-time RBAC middleware lets an ACCOUNTANT through;
  * two people: whoever raises an 'rtv' (an accountant or admin) cannot also
    approve it -- 'rtv' is maker-checker, read from the action type so a
    request raised before is held to it too;
  * spending one by id (the generic /consume) stays inside the caller's shops,
    the rule GET applies -- for every action type.

No network. No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET_KEY", "test-rtv-approvals")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import approvals as appr_router  # noqa: E402
from api.routers import users as users_router  # noqa: E402
from api.routers.auth import get_current_user, hash_password  # noqa: E402
from api.services import approvals as appr  # noqa: E402
from api.services.approvals import ApprovalEngine  # noqa: E402
from api.services.cost_mask import AP_ROLES  # noqa: E402
from api.services.payables_mask import can_see_payables  # noqa: E402

S1 = "BV-DHN-01"
S2 = "WO-PUN-01"
PIN = "4321"
ACCOUNTS_ROUTE = ["SUPERADMIN", *AP_ROLES]
MANAGERS = ("STORE_MANAGER", "AREA_MANAGER")
ACCOUNTS = ("ACCOUNTANT", "ADMIN")
# Rupee amounts that resolve to each tier on the E2 defaults (auto < Rs 2,000
# <= admin < Rs 10,000 <= super).
TIER_AMOUNTS = {"auto": 1500.0, "admin": 4500.0, "super": 48250.0}
MANAGER_TIER = ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"]

_APPROVE = "/api/v1/approvals/requests/{}/approve"
_REJECT = "/api/v1/approvals/requests/{}/reject"
_CONSUME = "/api/v1/approvals/requests/{}/consume"
_ONE = "/api/v1/approvals/requests/{}"
_INBOX = "/api/v1/approvals/requests/inbox"


def _user(role, store=S1, uid=None):
    return {
        "user_id": uid or f"u-{role.lower()}",
        "roles": [role],
        "store_ids": [store],
        "active_store_id": store,
    }


MAKER = _user("STORE_MANAGER", uid="u-maker")
_PIN_HASH = hash_password(PIN)  # bcrypt once, not per user per test


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient().db
    for role in ("STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT", "ADMIN"):
        db["users"].insert_one({
            "user_id": f"u-{role.lower()}", "username": role.lower(),
            "roles": [role], "store_ids": [S1], "is_active": True,
            "approval_pin_hash": _PIN_HASH,
        })
    # The maker, an accountant of another shop and one with no PIN yet.
    db["users"].insert_one({"user_id": "u-maker", "roles": ["STORE_MANAGER"],
                            "store_ids": [S1], "is_active": True,
                            "approval_pin_hash": _PIN_HASH})
    db["users"].insert_one({"user_id": "u-accountant-pune", "roles": ["ACCOUNTANT"],
                            "store_ids": [S2], "is_active": True})
    db["users"].insert_one({"user_id": "u-accountant-new", "roles": ["ACCOUNTANT"],
                            "store_ids": [S1], "is_active": True})
    monkeypatch.setattr(appr_router, "_get_db", lambda: db)

    app = FastAPI()
    app.include_router(appr_router.router, prefix="/api/v1/approvals")
    app.include_router(users_router.router, prefix="/api/v1/users")
    who = {"user": MAKER}

    async def _current():
        return who["user"]

    app.dependency_overrides[get_current_user] = _current
    cli = TestClient(app)

    def as_(user):
        who["user"] = user
        return cli

    eng = ApprovalEngine(db=db)

    def rtv(tier="auto", store=S1, rma="RMA-1"):
        res = eng.request(action_type="rtv", requested_by="u-maker",
                          requested_by_roles=["STORE_MANAGER"], store_id=store,
                          amount=TIER_AMOUNTS[tier],
                          context={"rma_id": rma, "credit_amount": TIER_AMOUNTS[tier]})
        assert res["ok"], res
        return res["request_id"]

    def refund(amount=999.0):
        res = eng.request(action_type="refund", requested_by="u-maker",
                          requested_by_roles=["STORE_MANAGER"], store_id=S1,
                          amount=amount, context={"order_id": "O-1"})
        assert res["ok"], res
        return res["request_id"]

    return SimpleNamespace(db=db, eng=eng, as_=as_, rtv=rtv, refund=refund)


def _doc(world, rid):
    return world.db["approval_requests"].find_one({"request_id": rid}, {"_id": 0})


def _attempts(world, uid):
    return (world.db["users"].find_one({"user_id": uid}) or {}).get("pin_attempts")


def _inbox(world, user, status="ALL"):
    r = world.as_(user).get(_INBOX, params={"status": status})
    assert r.status_code == 200, r.text
    return r.json()["requests"]


def _missing_like(a, b):
    assert a.status_code == b.status_code == 404, (a.text, b.text)
    return a.json()["detail"], b.json()["detail"]


# ============================================================================
# 1. Routing: every 'rtv' request goes to the accounts roles, whatever its tier
# ============================================================================


@pytest.mark.parametrize("tier", sorted(TIER_AMOUNTS))
def test_rtv_routes_to_the_accounts_roles_at_every_tier(world, tier):
    doc = _doc(world, world.rtv(tier))
    assert doc["required_roles"] == ACCOUNTS_ROUTE
    # The amount tier itself is kept (consume's min_tier reads it).
    assert doc["required_tier"] == tier


def test_other_action_types_keep_their_tier_roles(world):
    assert _doc(world, world.refund(999.0))["required_roles"] == MANAGER_TIER
    assert _doc(world, world.refund(4500.0))["required_roles"] == [
        "SUPERADMIN", "ADMIN", "AREA_MANAGER"]


def test_routing_is_the_payables_rule():
    for role in ("SUPERADMIN", "ADMIN", "ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER",
                 "CATALOG_MANAGER", "SALES_STAFF", "CASHIER", "WORKSHOP_STAFF"):
        assert appr.may_approve({"action_type": "rtv"}, [role]) is can_see_payables(
            {"roles": [role]})


# ============================================================================
# 2. Store / area managers: no 'rtv' in the inbox, by id it is not there
# ============================================================================


@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize("tier", sorted(TIER_AMOUNTS))
def test_a_manager_never_finds_an_rtv_in_the_inbox(world, role, tier):
    world.rtv(tier)
    ref = world.refund()
    for status in ("REQUESTED", "ALL"):
        rows = _inbox(world, _user(role), status)
        assert [r["action_type"] for r in rows] == ["refund"]
    (row,) = _inbox(world, _user(role))
    # Every other action type is unchanged for them.
    assert row["request_id"] == ref and row["amount"] == 999.0 and row["can_approve"] is True


@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize("tier", sorted(TIER_AMOUNTS))
def test_a_manager_cannot_open_approve_reject_or_spend_one_by_id(world, role, tier):
    rid = world.rtv(tier)
    cli = world.as_(_user(role))
    before = _doc(world, rid)

    got, ghost = _missing_like(cli.get(_ONE.format(rid)), cli.get(_ONE.format("REQ-NOPE")))
    assert got == ghost == "Request not found"
    for path, body in ((_APPROVE, {"pin": PIN}), (_REJECT, {"pin": PIN, "reason": "x"})):
        got, ghost = _missing_like(cli.post(path.format(rid), json=body),
                                   cli.post(path.format("REQ-NOPE"), json=body))
        assert got == ghost == {"error": "not_found"}
    # Nothing moved: still REQUESTED, no token, the manager's PIN untouched.
    after = _doc(world, rid)
    assert after["status"] == "REQUESTED" and after["approval_token"] is None
    assert after == before
    assert _attempts(world, _user(role)["user_id"]) is None

    # Even once an accounts role approved it, a manager cannot spend it.
    world.as_(_user("ACCOUNTANT")).post(_APPROVE.format(rid), json={"pin": PIN})
    token = _doc(world, rid)["approval_token"]
    body = {"action_type": "rtv", "approval_token": token}
    r = world.as_(_user(role)).post(_CONSUME.format(rid), json=body)
    assert r.status_code == 404
    assert _doc(world, rid)["status"] == "APPROVED" and _doc(world, rid)["consumed"] is False


def test_a_wrong_pin_from_a_manager_burns_nothing(world):
    """The 404 comes before the PIN check, so a manager guessing cannot lock
    themselves (or learn the request exists)."""
    rid = world.rtv()
    for _ in range(6):
        r = world.as_(_user("STORE_MANAGER")).post(_APPROVE.format(rid), json={"pin": "0000"})
        assert r.status_code == 404
    assert _attempts(world, "u-store_manager") is None


def test_its_manager_maker_still_reads_it_without_the_amount(world):
    rid = world.rtv("super")
    r = world.as_(MAKER).get(_ONE.format(rid))
    assert r.status_code == 200
    assert "amount" not in r.json() and r.json()["context"] == {"rma_id": "RMA-1"}
    (mine,) = world.as_(MAKER).get("/api/v1/approvals/requests/mine").json()["requests"]
    assert mine["request_id"] == rid and "amount" not in mine
    # ... and cannot approve their own (they are not an accounts role).
    assert world.as_(MAKER).post(_APPROVE.format(rid), json={"pin": PIN}).status_code == 404


# ============================================================================
# 3. ACCOUNTANT / ADMIN: list, open, approve, reject, spend -- with the amount
# ============================================================================


@pytest.mark.parametrize("role", ACCOUNTS)
@pytest.mark.parametrize("tier", sorted(TIER_AMOUNTS))
def test_accounts_see_and_approve_an_rtv_at_every_tier(world, role, tier):
    rid = world.rtv(tier)
    user = _user(role)
    rows = {r["request_id"]: r for r in _inbox(world, user, "REQUESTED")}
    assert rows[rid]["amount"] == TIER_AMOUNTS[tier]
    assert rows[rid]["context"]["credit_amount"] == TIER_AMOUNTS[tier]
    assert rows[rid]["can_approve"] is True
    assert world.as_(user).get(_ONE.format(rid)).json()["amount"] == TIER_AMOUNTS[tier]

    r = world.as_(user).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 200, r.text
    assert r.json()["approval_token"].startswith("APT-")
    doc = _doc(world, rid)
    assert doc["status"] == "APPROVED" and doc["reviewed_by"] == user["user_id"]

    # The RMA credit-note path spends it bound to its RMA + shop
    # (routers/vendor_rma._consume_rtv_approval passes exactly these).
    res = world.eng.consume_approval(
        consumed_by=user["user_id"], action_type="rtv", request_id=rid,
        approval_token=doc["approval_token"], amount=TIER_AMOUNTS[tier],
        expected_store_id=S1, expected_context={"rma_id": "RMA-1"})
    assert res["ok"] is True, res


@pytest.mark.parametrize("role", ACCOUNTS)
def test_accounts_reject_an_rtv(world, role):
    rid = world.rtv("admin")
    r = world.as_(_user(role)).post(_REJECT.format(rid), json={"pin": PIN, "reason": "short"})
    assert r.status_code == 200, r.text
    assert _doc(world, rid)["status"] == "REJECTED"


def test_the_accountant_spends_one_through_the_generic_consume(world):
    rid = world.rtv()
    world.as_(_user("ADMIN")).post(_APPROVE.format(rid), json={"pin": PIN})
    body = {"action_type": "rtv", "approval_token": _doc(world, rid)["approval_token"]}
    r = world.as_(_user("ACCOUNTANT")).post(_CONSUME.format(rid), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["request"]["status"] == "CONSUMED"
    assert r.json()["request"]["amount"] == 1500.0


def test_an_accountant_stays_inside_their_shops(world):
    rid = world.rtv(store=S2, rma="RMA-PUN")
    acct = _user("ACCOUNTANT")  # S1 only
    assert rid not in {r["request_id"] for r in _inbox(world, acct)}
    assert world.as_(acct).get(_ONE.format(rid)).status_code == 404
    # Approving it answers exactly what a missing id answers (F63).
    got, ghost = _missing_like(world.as_(acct).post(_APPROVE.format(rid), json={"pin": PIN}),
                               world.as_(acct).post(_APPROVE.format("REQ-NOPE"), json={"pin": PIN}))
    assert got == ghost == {"error": "not_found"}
    # Nor by naming the other shop on the inbox (the one shop rule).
    assert world.as_(acct).get(_INBOX, params={"store_id": S2}).status_code == 403
    # Pune's own accountant lists it.
    pune = _user("ACCOUNTANT", store=S2, uid="u-accountant-pune")
    assert rid in {r["request_id"] for r in _inbox(world, pune)}


@pytest.mark.parametrize("kind", ("rtv", "refund"))
@pytest.mark.parametrize(
    "who",
    [_user("ACCOUNTANT"), _user("STORE_MANAGER"), _user("AREA_MANAGER"),
     {"user_id": "u-accountant-new", "roles": ["ACCOUNTANT"], "store_ids": [], "active_store_id": None}],
    ids=["accountant", "store_manager", "area_manager", "accountant_no_shop"],
)
def test_another_shops_request_is_approved_or_rejected_like_a_missing_one(world, who, kind):
    """F63 on approve / reject, through the rule GET applies: a BV-DHN-01 login
    (or one with no shop) acting on a WO-PUN-01 request gets byte for byte what
    a missing id gets -- never 403 store_scope or insufficient_tier, which would
    confirm the request exists -- and nothing moves: the request stays
    REQUESTED and the caller's PIN throttle is untouched."""
    if kind == "rtv":
        rid = world.rtv(store=S2, rma="RMA-PUN")
    else:
        rid = world.eng.request(action_type="refund", requested_by="u-maker",
                                requested_by_roles=["STORE_MANAGER"], store_id=S2,
                                amount=999.0, context={"order_id": "O-PUN"})["request_id"]
    before = _doc(world, rid)
    cli = world.as_(who)
    for path, body in ((_APPROVE, {"pin": PIN}), (_REJECT, {"pin": PIN, "reason": "x"})):
        got = cli.post(path.format(rid), json=body)
        ghost = cli.post(path.format("REQ-NOPE"), json=body)
        assert (got.status_code, got.json()) == (ghost.status_code, ghost.json()) == (
            404, {"detail": {"error": "not_found"}}), (path, got.text)
    assert _doc(world, rid) == before
    assert _attempts(world, who["user_id"]) is None
    # Pune's own accountant still acts on Pune's 'rtv' (the rule, not a block).
    if kind == "rtv":
        pune = _user("ACCOUNTANT", store=S2, uid="u-accountant-pune")
        world.db["users"].update_one({"user_id": "u-accountant-pune"},
                                     {"$set": {"approval_pin_hash": _PIN_HASH}})
        r = world.as_(pune).post(_REJECT.format(rid), json={"pin": PIN, "reason": "short"})
        assert r.status_code == 200, r.text


def test_an_accountant_sets_their_own_pin_and_approves_with_it(world, monkeypatch):
    """The approver PIN flow for an ACCOUNTANT: PUT /users/{self}/approval-pin
    (self-service, no current PIN yet), then a PIN-gated approve; a wrong PIN
    is counted on their own throttle."""
    import database.connection as conn

    monkeypatch.setattr(conn, "get_db", lambda: SimpleNamespace(db=world.db))
    fresh = _user("ACCOUNTANT", uid="u-accountant-new")
    rid = world.rtv("super")

    r = world.as_(fresh).post(_APPROVE.format(rid), json={"pin": "2468"})
    assert r.status_code == 423 and r.json()["detail"]["error"] == "pin_not_set"

    r = world.as_(fresh).put("/api/v1/users/u-accountant-new/approval-pin", json={"pin": "2468"})
    assert r.status_code == 200, r.text
    assert world.as_(fresh).get(
        "/api/v1/users/u-accountant-new/approval-pin/status").json()["has_pin"] is True

    r = world.as_(fresh).post(_APPROVE.format(rid), json={"pin": "1111"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "wrong_pin"
    assert _attempts(world, "u-accountant-new")["count"] == 1
    r = world.as_(fresh).post(_APPROVE.format(rid), json={"pin": "2468"})
    assert r.status_code == 200, r.text
    assert _doc(world, rid)["reviewed_by"] == "u-accountant-new"


# ============================================================================
# 4. Requests raised before the ruling route the same way
# ============================================================================


@pytest.mark.parametrize("stored_roles", (MANAGER_TIER, ["SUPERADMIN"]))
def test_a_pending_pre_ruling_rtv_works_for_the_new_approvers(world, stored_roles):
    rid = world.rtv()
    # As written before the ruling: the manager tier's (or the super tier's)
    # roles, stamped on the request.
    world.db["approval_requests"].update_one(
        {"request_id": rid}, {"$set": {"required_roles": stored_roles}})
    assert rid in {r["request_id"] for r in _inbox(world, _user("ACCOUNTANT"))}
    for role in MANAGERS:
        assert rid not in {r["request_id"] for r in _inbox(world, _user(role))}
        assert world.as_(_user(role)).post(
            _APPROVE.format(rid), json={"pin": PIN}).status_code == 404
    r = world.as_(_user("ACCOUNTANT")).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 200, r.text


# ============================================================================
# 5. Every other action type: unchanged for managers, refused to the accountant
# ============================================================================


def test_the_accountant_is_still_refused_other_action_types(world):
    ref = world.refund()
    rows = {r["request_id"]: r for r in _inbox(world, _user("ACCOUNTANT"))}
    # Listed (the inbox was always theirs to read) but not theirs to act on.
    assert rows[ref]["amount"] == 999.0 and rows[ref]["can_approve"] is False
    for path, body in ((_APPROVE, {"pin": PIN}), (_REJECT, {"pin": PIN})):
        r = world.as_(_user("ACCOUNTANT")).post(path.format(ref), json=body)
        assert r.status_code == 403 and r.json()["detail"]["error"] == "insufficient_tier"
    assert _doc(world, ref)["status"] == "REQUESTED"
    assert _attempts(world, "u-accountant") is None


@pytest.mark.parametrize("role", MANAGERS)
def test_managers_still_approve_other_action_types(world, role):
    ref = world.refund()
    r = world.as_(_user(role)).post(_APPROVE.format(ref), json={"pin": PIN})
    assert r.status_code == 200, r.text
    assert _doc(world, ref)["status"] == "APPROVED"


def test_a_manager_is_still_told_a_higher_tier_is_not_theirs(world):
    ref = world.refund(4500.0)  # admin tier
    (row,) = _inbox(world, _user("STORE_MANAGER"))
    assert row["request_id"] == ref and row["can_approve"] is False
    r = world.as_(_user("STORE_MANAGER")).post(_APPROVE.format(ref), json={"pin": PIN})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "insufficient_tier"


# ============================================================================
# 6. The bell rings for the accounts roles alone, and names the amount
# ============================================================================


def test_the_rtv_bell_rings_for_the_accounts_roles_with_the_amount(world):
    rid = world.rtv("admin")
    bells = [b for b in world.db["notifications"].find({"request_id": rid})]
    # ADMIN everywhere; the ACCOUNTANTs of this shop (not Pune's).
    assert sorted(b["user_id"] for b in bells) == [
        "u-accountant", "u-accountant-new", "u-admin"]
    assert all("Rs 4500.00" in b["message"] for b in bells)
    assert all(b["message"].startswith("Return to Vendor for Rs 4500.00") for b in bells)


def test_the_bell_routing_reads_the_action_type_not_the_stored_roles(world):
    """A request stored with the manager tier's roles (before the ruling) is
    still rung to the accounts roles, never to a manager."""
    legacy = {"action_type": "rtv", "required_roles": MANAGER_TIER,
              "store_id": S1, "requested_by": "u-maker"}
    # pylint: disable-next=protected-access
    assert sorted(world.eng._eligible_approver_ids(legacy)) == [
        "u-accountant", "u-accountant-new", "u-admin"]


# ============================================================================
# 7. The route gate, its rbac_policy row and the request-time middleware agree
# ============================================================================


def test_the_approve_and_reject_gate_is_its_policy_row():
    from api.services import rbac_policy

    gate = {"SUPERADMIN", *appr_router._DECIDE_ROLES}  # pylint: disable=protected-access
    assert gate == {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"}
    for verb in ("approve", "reject"):
        row = rbac_policy.policy_for("POST", f"/api/v1/approvals/requests/R1/{verb}")
        assert set(row["allowed"]) == gate
        assert rbac_policy.check_access(
            "POST", f"/api/v1/approvals/requests/R1/{verb}", ["ACCOUNTANT"]) is True
        assert rbac_policy.check_access(
            "POST", f"/api/v1/approvals/requests/R1/{verb}", ["SALES_STAFF"]) is False


def test_the_rbac_middleware_lets_an_accountant_reach_the_engine(world, monkeypatch):
    from api.middleware.rbac_enforcement import rbac_enforcement_middleware
    from api.routers.auth import create_access_token
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_user_repository", lambda: None)
    rid = world.rtv()
    app = FastAPI()
    app.middleware("http")(rbac_enforcement_middleware)
    app.include_router(appr_router.router, prefix="/api/v1/approvals")
    current = {}

    async def _current():
        return current["user"]

    app.dependency_overrides[get_current_user] = _current
    cli = TestClient(app)

    def call(user):
        current["user"] = user
        token = create_access_token({"user_id": user["user_id"], "roles": user["roles"]},
                                    expires_delta=timedelta(minutes=5))
        return cli.post(_APPROVE.format(rid), json={"pin": PIN},
                        headers={"Authorization": f"Bearer {token}"})

    # A counter role is stopped by the middleware row.
    assert call(_user("SALES_STAFF")).status_code == 403
    # A manager reaches the engine and is told the request is not there.
    assert call(_user("STORE_MANAGER")).status_code == 404
    # The accountant reaches the engine and approves.
    r = call(_user("ACCOUNTANT"))
    assert r.status_code == 200, r.text
    assert _doc(world, rid)["status"] == "APPROVED"


def test_expired_rtv_still_answers_the_manager_as_missing(world):
    rid = world.rtv()
    world.db["approval_requests"].update_one(
        {"request_id": rid},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    r = world.as_(_user("AREA_MANAGER")).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 404
    r = world.as_(_user("ACCOUNTANT")).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 410


# ============================================================================
# 8. Two people: whoever raises an 'rtv' cannot also approve it (maker-checker)
# ============================================================================
#
# The accounts roles both record a supplier credit (R2) and approve its 'rtv'
# approval (R1), so without maker-checker one accountant (or admin) could raise,
# PIN-approve and spend their own approval on a Rs 50,000+ credit -- the step
# that used to need a second person (a SUPERADMIN at the super tier).

_CHECKER = {"ACCOUNTANT": "ADMIN", "ADMIN": "ACCOUNTANT"}


def _raise_as(world, user, tier="super", rma="RMA-1"):
    r = world.as_(user).post("/api/v1/approvals/requests", json={
        "action_type": "rtv", "store_id": S1, "amount": TIER_AMOUNTS[tier],
        "context": {"rma_id": rma, "credit_amount": TIER_AMOUNTS[tier]}})
    assert r.status_code == 200, r.text
    return r.json()["request_id"]


@pytest.mark.parametrize("role", ACCOUNTS)
@pytest.mark.parametrize("tier", sorted(TIER_AMOUNTS))
def test_whoever_raises_an_rtv_cannot_approve_it(world, role, tier):
    maker = _user(role)
    rid = _raise_as(world, maker, tier)
    doc = _doc(world, rid)
    assert doc["requested_by"] == maker["user_id"] and doc["maker_checker"] is True

    # Their inbox lists it, without the buttons.
    rows = {r["request_id"]: r for r in _inbox(world, maker, "REQUESTED")}
    assert rows[rid]["can_approve"] is False
    # Refused before the PIN check: nothing moves, no attempt counted.
    r = world.as_(maker).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "cannot_approve_own"
    assert _doc(world, rid) == doc
    assert _attempts(world, maker["user_id"]) is None

    # A second accounts person approves it.
    checker = _user(_CHECKER[role])
    rows = {r["request_id"]: r for r in _inbox(world, checker, "REQUESTED")}
    assert rows[rid]["can_approve"] is True
    r = world.as_(checker).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 200, r.text
    assert _doc(world, rid)["reviewed_by"] == checker["user_id"]


def test_a_pre_ruling_rtv_its_accountant_raised_is_still_two_person(world):
    """A request raised before 'rtv' was maker-checker is stored with
    maker_checker False; its maker is refused all the same (the action type is
    read, not only the stamp)."""
    maker = _user("ACCOUNTANT")
    rid = _raise_as(world, maker)
    world.db["approval_requests"].update_one(
        {"request_id": rid}, {"$set": {"maker_checker": False,
                                       "required_roles": ["SUPERADMIN"]}})
    rows = {r["request_id"]: r for r in _inbox(world, maker, "REQUESTED")}
    assert rows[rid]["can_approve"] is False
    r = world.as_(maker).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "cannot_approve_own"
    assert _doc(world, rid)["status"] == "REQUESTED"
    r = world.as_(_user("ADMIN")).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 200, r.text


def test_the_engine_refuses_its_maker_whatever_the_route(world):
    """The rule lives in the engine, so any caller of approve() gets it."""
    res = world.eng.request(action_type="rtv", requested_by="u-admin",
                            requested_by_roles=["ADMIN"], store_id=S1,
                            amount=80000.0, context={"rma_id": "RMA-9"})
    assert res["required_tier"] == "super"
    out = world.eng.approve(res["request_id"], approver_user_id="u-admin",
                            approver_roles=["ADMIN"], pin=PIN)
    assert out == {"ok": False, "http": 403, "error": "cannot_approve_own"}
    assert world.eng.approve(res["request_id"], approver_user_id="u-accountant",
                             approver_roles=["ACCOUNTANT"], pin=PIN,
                             approver_store_ids=[S1])["ok"] is True


# ============================================================================
# 9. Spending by id stays inside the caller's shops (the rule GET applies)
# ============================================================================


def _approved_rtv(world, store=S1, rma="RMA-1"):
    rid = world.rtv(store=store, rma=rma)
    r = world.as_(_user("ADMIN")).post(_APPROVE.format(rid), json={"pin": PIN})
    assert r.status_code == 200, r.text
    return rid


def test_another_shops_accountant_cannot_spend_an_rtv_by_id(world):
    rid = _approved_rtv(world)
    pune = _user("ACCOUNTANT", store=S2, uid="u-accountant-pune")
    cli = world.as_(pune)
    assert cli.get(_ONE.format(rid)).status_code == 404
    body = {"action_type": "rtv"}  # a request id alone, no token
    got = cli.post(_CONSUME.format(rid), json=body)
    ghost = cli.post(_CONSUME.format("REQ-NOPE"), json=body)
    assert got.status_code == ghost.status_code == 404
    assert got.json() == ghost.json() == {"detail": "not_found"}
    doc = _doc(world, rid)
    assert doc["status"] == "APPROVED" and doc["consumed"] is False
    assert doc["consumed_by"] is None

    # The shop's own accountant spends it.
    r = world.as_(_user("ACCOUNTANT")).post(_CONSUME.format(rid), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["request"]["consumed_by"] == "u-accountant"


def test_another_shops_manager_cannot_spend_any_approval_by_id(world):
    """The same shop rule for every action type: a refund approved in S1 is not
    spent by a manager of S2; its own maker spends it."""
    ref = world.refund()
    r = world.as_(_user("ADMIN")).post(_APPROVE.format(ref), json={"pin": PIN})
    assert r.status_code == 200, r.text
    other = _user("STORE_MANAGER", store=S2, uid="u-sm-pune")
    r = world.as_(other).post(_CONSUME.format(ref), json={"action_type": "refund"})
    assert r.status_code == 404 and r.json()["detail"] == "not_found"
    assert _doc(world, ref)["consumed"] is False
    r = world.as_(MAKER).post(_CONSUME.format(ref), json={"action_type": "refund"})
    assert r.status_code == 200, r.text
    assert r.json()["request"]["status"] == "CONSUMED"
