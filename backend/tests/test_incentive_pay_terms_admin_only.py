"""IMS 2.0 - per-person commission terms reach ADMIN/SUPERADMIN only.

OWNER RULING 2026-09-04: a staff member's commission weighting percentage
(``staff_weightages``) and a supervisor's bonus percentage
(``supervisor_bonuses``) are that person's pay terms -- ADMIN/SUPERADMIN only,
like salary. Nobody else, managers included, sees another named person's
commission terms. Predicate: services/salary_visibility.is_salary_admin via
points._viewer_visibility -- ONE definition, enforced where the JSON is built.

TWO DOORS carry those keys and both are driven here:
  GET /incentive/points/settings/eligibility  (AUTHENTICATED -- the scorecard
                                               entry grid reads it)
  GET /incentive/points/settings/effective    (managers + accountant admitted
                                               by the handler's own role gate)

WHY THE ASSERTIONS LOOK LIKE THIS: every test drives the endpoint and asserts
on the RESPONSE BODY -- the keys must be ABSENT (not empty: an empty dict still
answers "does anyone here have a weighting"), and the colleague's id and the
distinctive percentage strings must appear nowhere in resp.text. The ADMIN
tests are positive controls proving those assertions are not vacuous.

DISCRIMINATING POWER, measured: with points._redact_pay_terms neutered to a
pass-through, every non-admin test below fails on its absence assertion while
the admin controls stay green; with the pop replaced by "set to empty", the
``not in body`` assertions fail. Roll-call in the PR body.

Hermetic: strict in-memory Mongo doubles (tests/strict_fakes.py), no live DB.
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-incentive-pay-terms")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.strict_fakes import StrictDB  # noqa: E402


# ===========================================================================
# The planted dataset -- percentages chosen so they collide with NOTHING in
# the defaults envelope (growth 0.20/0.25/0.30, rates 0.01/0.0125/0.015,
# multipliers 1.0-1.5, gate 0.9, bands 0.6/0.8/1.0).
# ===========================================================================

STORE = "ZZ-PAYTERMS-01"

READER_ID = "ZZ-EMP-READER"            # whoever is signed in, in every role
READER_WEIGHT = 0.1111

COLLEAGUE_ID = "ZZ-EMP-WEIGHTED"       # must never reach a non-admin
COLLEAGUE_WEIGHT = 0.2731

SUPERVISOR_ID = "ZZ-SUP-BONUSED"       # must never reach a non-admin
SUPERVISOR_BONUS = 0.1937

NON_ADMIN_ROLES = [
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ACCOUNTANT",
    "SALES_STAFF",
    "SALES_CASHIER",
    "CASHIER",
]

# Roles the /settings/effective handler admits on its own (its role gate
# predates this ruling and is untouched); the rest get its 403.
EFFECTIVE_ADMITTED_NON_ADMIN = ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"]


@pytest.fixture
def seeded_settings(monkeypatch):
    """StrictDB wired into the points router with one store settings doc
    that carries real per-person pay terms."""
    fake_db = StrictDB()
    from api.routers import points as points_module

    monkeypatch.setattr(points_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(points_module, "get_audit_repository", lambda: None)

    fake_db.get_collection("incentive_settings").insert_one(
        {
            "_id": STORE,
            "store_id": STORE,
            "staff_weightages": {
                COLLEAGUE_ID: COLLEAGUE_WEIGHT,
                READER_ID: READER_WEIGHT,
            },
            "supervisor_bonuses": [
                {
                    "user_id": SUPERVISOR_ID,
                    "role": "STORE_MANAGER",
                    "bonus_pct": {"L1": SUPERVISOR_BONUS},
                }
            ],
            "updated_at": None,
            "updated_by": None,
        }
    )
    return fake_db


def _headers(roles, user_id=READER_ID):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": user_id,
            "username": user_id,
            "roles": roles,
            "store_ids": [STORE],
            "active_store_id": STORE,
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _assert_no_pay_terms_in_raw_body(resp):
    """What devtools sees: no named person's terms, no distinctive figure."""
    assert COLLEAGUE_ID not in resp.text
    assert SUPERVISOR_ID not in resp.text
    assert str(COLLEAGUE_WEIGHT) not in resp.text
    assert str(SUPERVISOR_BONUS) not in resp.text
    assert str(READER_WEIGHT) not in resp.text


def _assert_scorecard_inputs_present(body):
    """The entry grid still needs bands + the visufit gate -- redaction must
    not take those with it."""
    assert len(body["eligibility_bands"]) == 4
    assert body["visufit_gate_threshold"] == 0.9
    assert body["visufit_gate_enabled"] is True


# ===========================================================================
# GET /settings/eligibility -- the scorecard grid's door
# ===========================================================================


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_eligibility_settings_non_admin_gets_no_pay_terms(
    client, seeded_settings, role
):
    """REQUIREMENT (owner 2026-09-04): both keys ABSENT for a non-admin --
    managers included -- and visibility 'self'; bands + gate still there."""
    resp = client.get(
        "/api/v1/incentive/points/settings/eligibility",
        headers=_headers([role]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert "staff_weightages" not in body
    assert "supervisor_bonuses" not in body
    _assert_scorecard_inputs_present(body)
    _assert_no_pay_terms_in_raw_body(resp)


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_eligibility_settings_admin_gets_pay_terms(
    client, seeded_settings, role
):
    """POSITIVE CONTROL: the planted terms come back verbatim for an admin,
    proving the absence assertions above are not vacuous."""
    resp = client.get(
        "/api/v1/incentive/points/settings/eligibility",
        headers=_headers([role], user_id="ZZ-ADMIN-1"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "all"
    assert body["staff_weightages"] == {
        COLLEAGUE_ID: COLLEAGUE_WEIGHT,
        READER_ID: READER_WEIGHT,
    }
    assert body["supervisor_bonuses"][0]["user_id"] == SUPERVISOR_ID
    assert body["supervisor_bonuses"][0]["bonus_pct"]["L1"] == SUPERVISOR_BONUS
    assert str(COLLEAGUE_WEIGHT) in resp.text
    _assert_scorecard_inputs_present(body)


# ===========================================================================
# GET /settings/effective -- the second door carrying the same keys
# ===========================================================================


@pytest.mark.parametrize("role", EFFECTIVE_ADMITTED_NON_ADMIN)
def test_effective_settings_admitted_non_admin_gets_no_pay_terms(
    client, seeded_settings, role
):
    """A manager / accountant is admitted to the resolved view (its own gate)
    but must not receive the per-person keys through it."""
    resp = client.get(
        "/api/v1/incentive/points/settings/effective",
        headers=_headers([role]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert "staff_weightages" not in body
    assert "supervisor_bonuses" not in body
    _assert_scorecard_inputs_present(body)
    _assert_no_pay_terms_in_raw_body(resp)


@pytest.mark.parametrize("role", ["SALES_STAFF", "SALES_CASHIER", "CASHIER"])
def test_effective_settings_floor_roles_refused_without_leak(
    client, seeded_settings, role
):
    """The handler's pre-existing role gate still refuses floor roles, and the
    refusal body carries nothing."""
    resp = client.get(
        "/api/v1/incentive/points/settings/effective",
        headers=_headers([role]),
    )
    assert resp.status_code == 403, resp.text
    _assert_no_pay_terms_in_raw_body(resp)


def test_effective_settings_admin_gets_pay_terms(client, seeded_settings):
    """POSITIVE CONTROL for the second door."""
    resp = client.get(
        "/api/v1/incentive/points/settings/effective",
        headers=_headers(["ADMIN"], user_id="ZZ-ADMIN-1"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "all"
    assert body["staff_weightages"][COLLEAGUE_ID] == COLLEAGUE_WEIGHT
    assert body["supervisor_bonuses"][0]["user_id"] == SUPERVISOR_ID
    assert str(SUPERVISOR_BONUS) in resp.text


# ===========================================================================
# Fail closed: a self-only session the server cannot key on gets NOTHING
# (same guard the leaderboard applies -- the envelope is shared)
# ===========================================================================


def test_unidentifiable_non_admin_session_gets_nothing(client, seeded_settings):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "username": "ghost",
            "roles": ["STORE_MANAGER"],
            "store_ids": [STORE],
            "active_store_id": STORE,
        }
    )
    for path in (
        "/api/v1/incentive/points/settings/eligibility",
        "/api/v1/incentive/points/settings/effective",
    ):
        resp = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403), f"{path}: {resp.status_code}"
        _assert_no_pay_terms_in_raw_body(resp)
