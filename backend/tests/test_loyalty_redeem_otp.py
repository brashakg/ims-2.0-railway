"""
IMS 2.0 - OTP on loyalty-points REDEMPTION (owner ruling 2026-08-30, final:
redemption ONLY - never customer creation)
============================================================================
Contracts proven here, each by a named test that dies on the requirement:

  * policy key msg.loyalty_otp is REGISTERED, default off, store-scopable
  * the gate is off while DISPATCH_MODE is dark - the policy engine is not
    even consulted (dark deploy byte-identical to before the gate existed)
  * gate on requires BOTH armed dispatch AND policy "on"; policy errors
    fail soft to off (config hiccup must not block POS revenue)
  * dark redeem never contacts the challenge store and its audit row carries
    no otp field (byte-identical dark path)
  * gated redeem with no verified challenge refuses 403 with points and
    ledger untouched - the debit NEVER runs before verification
  * a VERIFIED challenge releases exactly ONE redemption (atomic consume);
    a second redeem refuses
  * wrong code burns an attempt, not points; expired / attempt-capped codes
    refuse with plain messages
  * the send endpoint uses the customer's STORED mobile - a client-supplied
    phone is ignored, so staff cannot route the code to themselves
  * providers.send_otp: SIMULATED dark proves the payload shape and never
    leaks the code; armed without an OTP template id fails honestly
  * rbac POLICY rows for the two new routes match the /loyalty/redeem family
  * the 7 MSG91 hook-only integration rows are honest: not_wired, armed
    False, docs link, and NO credential fields (the dead-end-tile class)
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import providers as _providers  # noqa: E402
from api.services import loyalty_otp  # noqa: E402

# House fakes from the main loyalty suite (same directory, pytest prepend
# import mode) - FakeDB gives Mongo-shaped collections incl. the guarded
# find_one_and_update the atomic consume relies on.
from test_loyalty import FakeDB, FakeOrderRepo  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _arm(monkeypatch, mode: str):
    """Arm the dispatch gate the way a deploy does (module snapshot)."""
    monkeypatch.setattr(_providers, "DISPATCH_MODE", mode)


class _FakeCustomerRepo:
    def __init__(self):
        self._docs = {}

    def seed(self, customer_id, **fields):
        self._docs[customer_id] = {"customer_id": customer_id, **fields}

    def find_by_id(self, customer_id):
        return self._docs.get(customer_id)


@pytest.fixture
def rig(monkeypatch):
    """Loyalty repos + customers + the OTP challenge collection, all faked."""
    from api.routers import loyalty as loyalty_module
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltySettingsRepository,
        LoyaltyTransactionRepository,
    )

    fake_db = FakeDB()
    accounts = LoyaltyAccountRepository(fake_db.get_collection("loyalty_accounts"))
    txns = LoyaltyTransactionRepository(fake_db.get_collection("loyalty_transactions"))
    settings = LoyaltySettingsRepository(fake_db.get_collection("loyalty_settings"))
    audit = AuditRepository(fake_db.get_collection("audit_logs"))
    customers = _FakeCustomerRepo()
    orders = FakeOrderRepo()

    monkeypatch.setattr(loyalty_module, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(loyalty_module, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(loyalty_module, "get_loyalty_settings_repository", lambda: settings)
    monkeypatch.setattr(loyalty_module, "get_audit_repository", lambda: audit)
    monkeypatch.setattr(loyalty_module, "get_order_repository", lambda: orders)
    monkeypatch.setattr(loyalty_module, "get_customer_repository", lambda: customers)

    otp_coll = fake_db.get_collection("loyalty_otp_challenges")
    monkeypatch.setattr(loyalty_otp, "_coll", lambda: otp_coll)

    return {
        "db": fake_db,
        "accounts": accounts,
        "txns": txns,
        "audit": audit,
        "customers": customers,
        "otp_coll": otp_coll,
    }


def _seed_balance(rig, customer_id="cust-otp-1", points=500):
    rig["accounts"].find_or_create(customer_id)
    rig["accounts"].adjust_balance(
        customer_id, delta_points=points, delta_lifetime_earned=points
    )
    return customer_id


def _pending_doc(customer_id, code="123456", **over):
    challenge_id = over.pop("challenge_id", "chal-1")
    doc = {
        "challenge_id": challenge_id,
        "customer_id": customer_id,
        "code_hash": loyalty_otp._hash(challenge_id, code),
        "status": "PENDING",
        "attempts": 0,
        "created_at": time.time(),
        "expires_at": time.time() + 300,
        "created_by": "test",
    }
    doc.update(over)
    return doc


def _force_gate_on(monkeypatch):
    """Force the gate decision on, bypassing env/policy - for door tests."""
    monkeypatch.setattr(loyalty_otp, "redeem_otp_required", lambda store_id=None: True)


# ---------------------------------------------------------------------------
# Policy key + gate decision (ONE implementation: loyalty_otp.redeem_otp_required)
# ---------------------------------------------------------------------------


def test_policy_key_registered_default_off_store_scopable():
    from api.services.policy_registry import REGISTRY

    spec = REGISTRY.get("msg.loyalty_otp")
    assert spec is not None, "policy key msg.loyalty_otp must be REGISTERED"
    assert spec.default == "off"
    assert "store" in spec.scopes, "owner can scope the OTP gate per store"
    assert spec.enum == ("off", "on")


def test_gate_dark_never_even_reads_the_policy(monkeypatch):
    """DISPATCH_MODE off/unset -> gate False EVEN WHEN THE POLICY SAYS ON,
    and the policy engine is not consulted at all - an unarmed deployment
    is byte-identical to main.

    The first version of this test used a RAISING bomb to prove the policy
    was never read - useless: redeem_otp_required has a fail-soft
    `except Exception: return False`, which swallowed the bomb, so deleting
    the dispatch check left the whole suite green (verifier mutation M4,
    2026-08-31). A raising detector inside a fail-soft function can never
    detect anything. Two non-raising assertions replace it: the policy
    RETURNS "on" and the gate must still be False (that kills the mutant),
    and a recording spy proves it was never consulted."""
    from api.services import policy_engine

    consulted = []

    def _spy(key, scope=None, *, default=None):
        consulted.append(key)
        return "on"  # the dangerous answer: dark must beat it anyway

    monkeypatch.setattr(policy_engine, "get_policy", _spy)
    for mode in ("off", "", "garbage"):
        _arm(monkeypatch, mode)
        assert loyalty_otp.redeem_otp_required("BV-TEST-01") is False, (
            f"dark ({mode!r}) must beat a policy answering 'on' - an owner"
            " flipping the setting before arming would brick POS redemption"
        )
    assert consulted == [], (
        f"the policy engine must not be consulted while dark: {consulted}"
    )


def test_gate_on_only_when_armed_and_policy_on(monkeypatch):
    from api.services import policy_engine

    seen_scopes = []

    def _policy(key, scope=None, *, default=None):
        assert key == "msg.loyalty_otp"
        seen_scopes.append(scope)
        return "on"

    monkeypatch.setattr(policy_engine, "get_policy", _policy)
    _arm(monkeypatch, "test")
    assert loyalty_otp.redeem_otp_required("BV-TEST-01") is True
    _arm(monkeypatch, "live")
    assert loyalty_otp.redeem_otp_required("BV-TEST-01") is True
    # The store reaches the policy resolution (store-scopable).
    assert {"store_id": "BV-TEST-01"} in seen_scopes

    monkeypatch.setattr(policy_engine, "get_policy", lambda *a, **k: "off")
    assert loyalty_otp.redeem_otp_required("BV-TEST-01") is False
    # Sloppy stored value still counts as on (strip + lower).
    monkeypatch.setattr(policy_engine, "get_policy", lambda *a, **k: " ON ")
    assert loyalty_otp.redeem_otp_required("BV-TEST-01") is True


def test_gate_fails_soft_to_off_on_policy_error(monkeypatch):
    from api.services import policy_engine

    def _boom(*a, **k):
        raise RuntimeError("policy store down")

    monkeypatch.setattr(policy_engine, "get_policy", _boom)
    _arm(monkeypatch, "live")
    assert loyalty_otp.redeem_otp_required("BV-TEST-01") is False


# ---------------------------------------------------------------------------
# The redeem door (dark path byte-identical; gated path refuses before debit)
# ---------------------------------------------------------------------------


def test_redeem_dark_path_untouched_and_audit_carries_no_otp_field(
    client, auth_headers, rig, monkeypatch
):
    """Differential proof for the dark deploy: redemption succeeds exactly as
    before, the challenge store is NEVER contacted, and the audit payload has
    no otp field."""
    _arm(monkeypatch, "off")

    def _bomb(*a, **k):  # pragma: no cover
        raise AssertionError("challenge store contacted while gate is dark")

    monkeypatch.setattr(loyalty_otp, "consume_verified", _bomb)
    cid = _seed_balance(rig, points=500)

    r = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": cid, "points": 100},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["redeemed_points"] == 100
    acct = rig["accounts"].find_by_id(cid)
    assert acct["balance_points"] == 400

    audit_rows = [
        d
        for d in rig["db"].get_collection("audit_logs").docs
        if d.get("action") == "loyalty.redeem"
    ]
    assert audit_rows, "redeem must audit"
    assert "otp_verified" not in (audit_rows[-1].get("detail") or {}), (
        "dark-path audit row must be byte-identical to before the OTP gate"
    )


def test_redeem_refuses_403_without_verification_and_points_untouched(
    client, auth_headers, rig, monkeypatch
):
    _force_gate_on(monkeypatch)
    cid = _seed_balance(rig, points=500)

    r = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": cid, "points": 100},
        headers=auth_headers,
    )
    assert r.status_code == 403, r.text
    assert "OTP" in r.json()["detail"]
    # Points untouched, no REDEEM ledger row - the refusal happened BEFORE
    # the atomic debit.
    assert rig["accounts"].find_by_id(cid)["balance_points"] == 500
    redeem_rows = [
        d
        for d in rig["db"].get_collection("loyalty_transactions").docs
        if d.get("type") == "REDEEM"
    ]
    assert redeem_rows == []


def test_verified_challenge_releases_exactly_one_redemption(
    client, auth_headers, rig, monkeypatch
):
    _force_gate_on(monkeypatch)
    cid = _seed_balance(rig, points=500)
    rig["otp_coll"].insert_one(
        _pending_doc(cid, status="VERIFIED", expires_at=time.time() + 900)
    )

    r1 = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": cid, "points": 100},
        headers=auth_headers,
    )
    assert r1.status_code == 200, r1.text
    assert rig["accounts"].find_by_id(cid)["balance_points"] == 400
    docs = rig["otp_coll"].docs
    assert docs[0]["status"] == "USED", "consume must flip VERIFIED -> USED"

    # Same verification cannot release a second redemption.
    r2 = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": cid, "points": 100},
        headers=auth_headers,
    )
    assert r2.status_code == 403
    assert rig["accounts"].find_by_id(cid)["balance_points"] == 400


def test_expired_verified_challenge_does_not_release(client, auth_headers, rig, monkeypatch):
    _force_gate_on(monkeypatch)
    cid = _seed_balance(rig, points=500)
    rig["otp_coll"].insert_one(
        _pending_doc(cid, status="VERIFIED", expires_at=time.time() - 1)
    )
    r = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": cid, "points": 100},
        headers=auth_headers,
    )
    assert r.status_code == 403
    assert rig["accounts"].find_by_id(cid)["balance_points"] == 500


# ---------------------------------------------------------------------------
# Challenge lifecycle (verify_challenge / consume_verified)
# ---------------------------------------------------------------------------


def test_wrong_code_burns_an_attempt_not_the_challenge(rig):
    cid = "cust-otp-2"
    rig["otp_coll"].insert_one(_pending_doc(cid, code="123456"))

    ok, reason = loyalty_otp.verify_challenge(cid, "999999")
    assert ok is False
    assert "not correct" in reason
    doc = rig["otp_coll"].docs[0]
    assert doc["status"] == "PENDING"
    assert doc["attempts"] == 1

    # The right code still works afterwards - and extends the window for the
    # checkout that follows.
    ok2, _ = loyalty_otp.verify_challenge(cid, "123456")
    assert ok2 is True
    doc = rig["otp_coll"].docs[0]
    assert doc["status"] == "VERIFIED"
    assert doc["expires_at"] > time.time() + 300  # VERIFIED_TTL > code TTL


def test_expired_code_refuses_plainly(rig):
    cid = "cust-otp-3"
    rig["otp_coll"].insert_one(
        _pending_doc(cid, code="123456", expires_at=time.time() - 1)
    )
    ok, reason = loyalty_otp.verify_challenge(cid, "123456")
    assert ok is False
    assert "expired" in reason
    assert loyalty_otp.consume_verified(cid) is False


def test_attempt_cap_refuses_plainly(rig):
    cid = "cust-otp-4"
    rig["otp_coll"].insert_one(
        _pending_doc(cid, code="123456", attempts=loyalty_otp.MAX_ATTEMPTS)
    )
    ok, reason = loyalty_otp.verify_challenge(cid, "123456")
    assert ok is False
    assert "Too many" in reason


def test_no_challenge_refuses_with_send_first_message(rig):
    ok, reason = loyalty_otp.verify_challenge("cust-none", "123456")
    assert ok is False
    assert "send a code" in reason.lower()


# ---------------------------------------------------------------------------
# The send endpoint (customer's STORED mobile only)
# ---------------------------------------------------------------------------


def test_send_endpoint_gate_off_sends_nothing(client, auth_headers, rig, monkeypatch):
    _arm(monkeypatch, "off")

    async def _bomb(*a, **k):  # pragma: no cover
        raise AssertionError("challenge started while gate is off")

    monkeypatch.setattr(loyalty_otp, "start_challenge", _bomb)
    r = client.post(
        "/api/v1/loyalty/redeem/otp/send",
        json={"customer_id": "cust-x"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"otp_required": False}


def test_send_endpoint_uses_stored_mobile_never_client_supplied(
    client, auth_headers, rig, monkeypatch
):
    _force_gate_on(monkeypatch)
    rig["customers"].seed("cust-otp-5", mobile="9876543210")
    captured = {}

    async def _capture(customer_id, mobile, created_by=None):
        captured["customer_id"] = customer_id
        captured["mobile"] = mobile
        return {"ok": True, "challenge_id": "c", "send_status": "SIMULATED",
                "expires_in_seconds": 300}

    monkeypatch.setattr(loyalty_otp, "start_challenge", _capture)
    # A hostile client supplies its own phone - it must be IGNORED.
    r = client.post(
        "/api/v1/loyalty/redeem/otp/send",
        json={"customer_id": "cust-otp-5", "mobile": "1112223334",
              "phone": "1112223334"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert captured["mobile"] == "9876543210"
    assert r.json()["sent_to_last4"] == "3210"


def test_send_endpoint_no_mobile_on_file_400(client, auth_headers, rig, monkeypatch):
    _force_gate_on(monkeypatch)
    rig["customers"].seed("cust-otp-6")  # no mobile
    r = client.post(
        "/api/v1/loyalty/redeem/otp/send",
        json={"customer_id": "cust-otp-6"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "no mobile number" in r.json()["detail"]


def test_verify_endpoint_wrong_code_400_plain(client, auth_headers, rig, monkeypatch):
    _force_gate_on(monkeypatch)
    rig["otp_coll"].insert_one(_pending_doc("cust-otp-7", code="123456"))
    r = client.post(
        "/api/v1/loyalty/redeem/otp/verify",
        json={"customer_id": "cust-otp-7", "code": "000000"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "not correct" in r.json()["detail"]

    r2 = client.post(
        "/api/v1/loyalty/redeem/otp/verify",
        json={"customer_id": "cust-otp-7", "code": "123456"},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json() == {"verified": True}


def test_account_envelope_reports_gate_state(client, auth_headers, rig, monkeypatch):
    cid = _seed_balance(rig, "cust-otp-8", points=10)
    _arm(monkeypatch, "off")
    r = client.get(f"/api/v1/loyalty/account/{cid}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["redeem_otp_required"] is False

    _force_gate_on(monkeypatch)
    r2 = client.get(f"/api/v1/loyalty/account/{cid}", headers=auth_headers)
    assert r2.json()["redeem_otp_required"] is True


# ---------------------------------------------------------------------------
# providers.send_otp (MSG91 OTP API; transport only - IMS verifies)
# ---------------------------------------------------------------------------


async def test_send_otp_simulated_dark_proves_shape_and_never_leaks_code(monkeypatch):
    monkeypatch.setattr(_providers, "DISPATCH_MODE", "off")
    result = await _providers.send_otp("9876543210", "424242", expiry_minutes=5)
    assert result.ok is True
    assert result.status == "SIMULATED"
    assert result.channel == "otp"
    assert result.meta["endpoint"] == "/otp"
    assert result.meta["mobile_last4"] == "3210"
    assert result.meta["expiry_minutes"] == 5
    # The code is a money-gating secret: it must appear NOWHERE in the result.
    assert "424242" not in repr(result)


async def test_send_otp_armed_without_template_id_fails_honestly(monkeypatch):
    monkeypatch.setattr(_providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(
        _providers, "_msg91", lambda: {"api_key": "k-test", "otp_template_id": ""}
    )
    result = await _providers.send_otp("9876543210", "424242")
    assert result.ok is False
    assert result.status == "FAILED"
    assert "OTP" in (result.error or "") and "template" in (result.error or "").lower()


async def test_send_otp_invalid_phone_fails(monkeypatch):
    monkeypatch.setattr(_providers, "DISPATCH_MODE", "off")
    result = await _providers.send_otp("", "424242")
    assert result.ok is False
    assert result.status == "FAILED"


# ---------------------------------------------------------------------------
# rbac rows + hook-only integration rows
# ---------------------------------------------------------------------------


def test_rbac_rows_for_otp_routes_match_redeem_family():
    from api.services.rbac_policy import POLICY

    def _row(path):
        rows = [
            r for r in POLICY if r.get("path") == path and r.get("method") == "POST"
        ]
        assert rows, f"POLICY row missing for POST {path}"
        return rows[0]

    redeem = _row("/api/v1/loyalty/redeem")
    send = _row("/api/v1/loyalty/redeem/otp/send")
    verify = _row("/api/v1/loyalty/redeem/otp/verify")
    assert sorted(send["allowed"]) == sorted(redeem["allowed"])
    assert sorted(verify["allowed"]) == sorted(redeem["allowed"])


HOOK_IDS = (
    "msg91_rcs",
    "msg91_hello",
    "msg91_segmento",
    "msg91_campaign",
    "msg91_one_api",
    "msg91_push",
    "msg91_numbers",
)


def test_msg91_hook_rows_honest_and_credential_free():
    """The 7 hook-only rows: honest not_wired state, armed False, a docs
    link, a WHY note - and NO credential fields (env or collection), the
    dead-end-tile class purged in #1016 must not come back."""
    from api.services.integration_status import build_integration_status

    report = build_integration_status(db=None)
    by_id = {i["id"]: i for i in report["integrations"]}
    for hook_id in HOOK_IDS:
        row = by_id.get(hook_id)
        assert row is not None, f"hook row {hook_id} missing"
        assert row["source"] == "not_wired"
        assert row["state"] == "not_wired"
        assert row["armed"] is False
        assert row["configured"] is False
        assert row["docs"].startswith("https://msg91.com"), hook_id
        assert row["notes"], f"{hook_id} needs its WHY note"
        # No dead credential fields.
        assert row["env_keys"] == [], f"{hook_id} must not list env keys"
        assert row["collection"] is None, f"{hook_id} must not have a collection form"
