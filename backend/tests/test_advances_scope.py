"""
IMS 2.0 -- who may READ an advance, and what an advance may be FOR
=================================================================
Two owner rulings of 2026-08-14, both enforced in routers/expenses.py:

  1. APPROVERS PLUS YOUR OWN. GET /api/v1/expenses/advances used to filter by
     store and nothing else, so ANY authenticated user at a store -- a
     salesperson, an optometrist -- could list every colleague's advance for
     that store: employee_id, amount, purpose and type. The approver tier (the
     roles that approve / disburse / settle) sees all advances for the store,
     because they hand over the cash; everyone else sees only their own.

  2. A FIXED LIST OF BUSINESS REASONS, WITH NO PAY OPTION. advance_type was
     free text, so "Salary advance" recorded pay information here and routed
     round the ADMIN-only Payroll module.

Every assertion here is on the RESPONSE BODY (or on the repository writes that
did or did not happen), never on a filter dict the handler happened to build and
never on an error string. The fake repository HONOURS the Mongo filter via
strict_fakes.matches, so a scope that leaks really does put the colleague's row
in the response -- a fake that ignored the filter would hand every row to
everybody and leave these tests unable to fail.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ts_constants  # noqa: E402
from strict_fakes import matches  # noqa: E402

from api.routers import expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import rbac_policy  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture data: two people at store-001, one at store-002.
# ---------------------------------------------------------------------------
ME = {
    "advance_id": "adv-mine",
    "employee_id": "u1",
    "employee_name": "Ravi Salesperson",
    "store_id": "store-001",
    "advance_type": "LOCAL_CONVEYANCE",
    "amount": 700.0,
    "purpose": "Auto to the fitting lab and back",
    "status": "PENDING",
}

COLLEAGUE = {
    "advance_id": "adv-colleague",
    "employee_id": "u2-colleague",
    "employee_name": "Priya Optometrist",
    "store_id": "store-001",
    "advance_type": "TRAVEL",
    "amount": 4321.0,
    "purpose": "Bus to Ranchi for the school screening camp",
    "status": "PENDING",
}

OTHER_STORE = {
    "advance_id": "adv-other-store",
    "employee_id": "u3-other-store",
    "employee_name": "Amit Manager",
    "store_id": "store-002",
    "advance_type": "STORE_MAINTENANCE",
    "amount": 9876.0,
    "purpose": "Electrician for the signage at the Mumbai store",
    "status": "PENDING",
}

ALL_ROWS = [ME, COLLEAGUE, OTHER_STORE]


class FakeAdvanceRepo:
    """Stand-in for AdvanceRepository that APPLIES the filter it is given.

    find_many runs the handler's Mongo filter through strict_fakes.matches, so
    what the endpoint returns is decided by the endpoint's scope and not by the
    double. create() records the write so a rejected request can be shown to
    have written nothing.
    """

    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs if docs is not None else [])]
        self.created = []

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        return [dict(d) for d in self.docs if matches(d, filter or {})]

    def find_by_id(self, advance_id):
        for d in self.docs:
            if d.get("advance_id") == advance_id:
                return dict(d)
        return None

    def create(self, doc):
        self.created.append(dict(doc))
        self.docs.append(dict(doc))
        return dict(doc)


def _client(monkeypatch, roles, repo, user_id="u1", store="store-001", store_ids=None):
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": user_id,
            "full_name": "Test User",
            "active_store_id": store,
            "store_ids": store_ids if store_ids is not None else [store],
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(expenses, "get_advance_repository", lambda: repo)
    return TestClient(app)


def _ids(payload):
    return {a.get("advance_id") for a in payload.get("advances", [])}


# The tier that must see everything for its store. SUPERADMIN is included
# because require_roles auto-passes it at the approve / disburse / settle gates.
APPROVER_ROLES_UNDER_TEST = [
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ACCOUNTANT",
    "ADMIN",
    "SUPERADMIN",
]

# Roles that work at a store and must NOT see a colleague's advance.
NON_APPROVER_ROLES_UNDER_TEST = ["SALES_STAFF", "OPTOMETRIST", "CASHIER"]


class TestAdvanceReadScope:
    def test_sales_staff_sees_only_their_own_advance(self, monkeypatch):
        """THE DEFECT. A salesperson must not receive a colleague's row.

        Asserts the colleague's employee_id, amount and purpose are absent from
        the WHOLE serialised body -- not from one field we expected -- so a
        leak through any key (a nested echo, a debug field, a future column)
        still fails this test.
        """
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, ["SALES_STAFF"], repo)

        resp = client.get("/expenses/advances")

        assert resp.status_code == 200
        body = resp.json()
        assert _ids(body) == {"adv-mine"}
        assert body["total"] == 1
        raw = resp.text
        assert "u2-colleague" not in raw
        assert "Priya Optometrist" not in raw
        assert "4321" not in raw
        assert "Ranchi" not in raw

    @pytest.mark.parametrize("role", NON_APPROVER_ROLES_UNDER_TEST)
    def test_no_store_role_outside_the_approver_tier_sees_a_colleague(
        self, monkeypatch, role
    ):
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, [role], repo)

        resp = client.get("/expenses/advances")

        assert resp.status_code == 200
        assert _ids(resp.json()) == {"adv-mine"}
        assert "u2-colleague" not in resp.text

    # --- POSITIVE CONTROLS ---------------------------------------------------
    # Without these, a handler that returned {"advances": []} to everybody would
    # pass every test above. The approver tier must actually receive the row.

    @pytest.mark.parametrize("role", APPROVER_ROLES_UNDER_TEST)
    def test_approver_tier_does_see_the_colleague_advance(self, monkeypatch, role):
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, [role], repo, user_id="mgr-1")

        resp = client.get("/expenses/advances")

        assert resp.status_code == 200
        body = resp.json()
        assert _ids(body) == {"adv-mine", "adv-colleague"}
        rows = {a["advance_id"]: a for a in body["advances"]}
        assert rows["adv-colleague"]["employee_id"] == "u2-colleague"
        assert rows["adv-colleague"]["amount"] == 4321.0
        assert (
            rows["adv-colleague"]["purpose"]
            == "Bus to Ranchi for the school screening camp"
        )

    def test_salesperson_still_sees_their_own_advance_in_full(self, monkeypatch):
        """The other positive control: scoping must not hide it from its owner."""
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, ["SALES_STAFF"], repo)

        resp = client.get("/expenses/advances")

        body = resp.json()
        assert _ids(body) == {"adv-mine"}
        mine = body["advances"][0]
        assert mine["amount"] == 700.0
        assert mine["purpose"] == "Auto to the fitting lab and back"
        assert mine["advance_type"] == "LOCAL_CONVEYANCE"

    # --- CROSS-STORE ---------------------------------------------------------

    def test_approver_at_store_a_does_not_see_store_b_advances(self, monkeypatch):
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, ["STORE_MANAGER"], repo, user_id="mgr-1")

        resp = client.get("/expenses/advances")

        assert resp.status_code == 200
        assert "adv-other-store" not in _ids(resp.json())
        assert "u3-other-store" not in resp.text
        assert "9876" not in resp.text

    def test_approver_asking_for_another_store_is_refused(self, monkeypatch):
        """A store-level approver cannot reach across stores by query parameter."""
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, ["STORE_MANAGER"], repo, user_id="mgr-1")

        resp = client.get("/expenses/advances", params={"store_id": "store-002"})

        assert resp.status_code == 403
        assert "u3-other-store" not in resp.text

    # --- THE QUERY PARAMETER IS NOT A BYPASS ---------------------------------

    def test_employee_id_query_cannot_widen_a_salespersons_view(self, monkeypatch):
        """Passing a colleague's id must still return only the caller's rows."""
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, ["SALES_STAFF"], repo)

        resp = client.get(
            "/expenses/advances", params={"employee_id": "u2-colleague"}
        )

        assert resp.status_code == 200
        assert _ids(resp.json()) == {"adv-mine"}
        assert "u2-colleague" not in resp.text
        assert "Ranchi" not in resp.text

    def test_employee_id_query_still_narrows_for_an_approver(self, monkeypatch):
        """Positive control for the filter itself: it works for the tier that may use it."""
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, ["STORE_MANAGER"], repo, user_id="mgr-1")

        resp = client.get(
            "/expenses/advances", params={"employee_id": "u2-colleague"}
        )

        assert resp.status_code == 200
        assert _ids(resp.json()) == {"adv-colleague"}

    def test_status_filter_does_not_widen_a_salespersons_view(self, monkeypatch):
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, ["SALES_STAFF"], repo)

        resp = client.get("/expenses/advances", params={"status": "PENDING"})

        assert _ids(resp.json()) == {"adv-mine"}

    def test_a_roleless_session_sees_nobody_elses_advance(self, monkeypatch):
        """Fails closed: an unidentifiable caller is not promoted to approver."""
        repo = FakeAdvanceRepo(ALL_ROWS)
        client = _client(monkeypatch, [], repo)

        resp = client.get("/expenses/advances")

        assert _ids(resp.json()) == {"adv-mine"}
        assert "u2-colleague" not in resp.text


class TestApproverTierDoesNotDrift:
    """The tier and the workflow gates are ONE decision. Pin them together.

    If someone widens (or narrows) who may approve / disburse / settle an
    advance without changing the read scope, this fails -- which is the failure
    that did not happen the first time and produced the leak.
    """

    GATED_ADVANCE_PATHS = (
        "/api/v1/expenses/advances/{advance_id}/approve",
        "/api/v1/expenses/advances/{advance_id}/disburse",
        "/api/v1/expenses/advances/{advance_id}/settle",
    )

    def test_advance_approver_tier_matches_the_workflow_gates(self):
        rows = [
            r
            for r in rbac_policy.POLICY
            if r.get("path") in self.GATED_ADVANCE_PATHS and r.get("method") == "POST"
        ]
        assert len(rows) == len(self.GATED_ADVANCE_PATHS), (
            "an advance workflow gate lost its rbac_policy row"
        )

        # SUPERADMIN is not written into the policy rows because require_roles
        # auto-passes it; it IS part of the read tier for the same reason.
        tier_without_superadmin = set(expenses.ADVANCE_APPROVER_ROLES) - {"SUPERADMIN"}
        for row in rows:
            assert set(row["allowed"]) == tier_without_superadmin, (
                f"{row['path']} allows {row['allowed']} but the advance read "
                f"scope admits {sorted(expenses.ADVANCE_APPROVER_ROLES)}"
            )

    def test_superadmin_is_in_the_tier_because_require_roles_passes_it(self):
        assert "SUPERADMIN" in expenses.ADVANCE_APPROVER_ROLES
        assert expenses.is_advance_approver({"roles": ["SUPERADMIN"]}) is True

    def test_tier_is_derived_from_the_gate_tuple_not_retyped(self):
        """Every role at the gates is in the tier, and nothing extra crept in."""
        assert set(expenses._APPROVAL_ROLES) <= set(expenses.ADVANCE_APPROVER_ROLES)
        assert set(expenses.ADVANCE_APPROVER_ROLES) == set(
            expenses._APPROVAL_ROLES
        ) | {"SUPERADMIN"}

    def test_the_route_stays_open_to_every_authenticated_user(self):
        """Narrowing the policy row would 403 a salesperson out of their OWN list."""
        rows = [
            r
            for r in rbac_policy.POLICY
            if r.get("path") == "/api/v1/expenses/advances" and r.get("method") == "GET"
        ]
        assert rows, "the advances list route lost its rbac_policy row"
        assert rows[0]["allowed"] == "AUTHENTICATED"

    @pytest.mark.parametrize("role", NON_APPROVER_ROLES_UNDER_TEST)
    def test_non_approver_roles_are_not_in_the_tier(self, role):
        assert expenses.is_advance_approver({"roles": [role]}) is False


class TestAdvanceTypeIsAFixedList:
    """No pay option, and no free text to type one into."""

    def _post(self, monkeypatch, repo, advance_type):
        client = _client(monkeypatch, ["SALES_STAFF"], repo)
        return client.post(
            "/expenses/advances",
            json={
                "advance_type": advance_type,
                "amount": 500.0,
                "purpose": "for work",
            },
        )

    @pytest.mark.parametrize("value", list(expenses.ADVANCE_TYPES))
    def test_every_allowed_reason_is_accepted(self, monkeypatch, value):
        """POSITIVE CONTROL: a validator that refused everything would pass the
        rejection tests below and break the feature."""
        repo = FakeAdvanceRepo([])
        resp = self._post(monkeypatch, repo, value)

        assert resp.status_code == 201, resp.text
        assert len(repo.created) == 1
        assert repo.created[0]["advance_type"] == value

    def test_a_salary_advance_is_refused(self, monkeypatch):
        repo = FakeAdvanceRepo([])
        resp = self._post(monkeypatch, repo, "Salary advance")

        assert resp.status_code == 422
        assert repo.created == []

    @pytest.mark.parametrize(
        "value",
        [
            "SALARY",
            "ADVANCE_AGAINST_SALARY",
            "PF",
            "BONUS",
            "OTHER",
            "anything at all",
            "travel",
            "",
            " ",
        ],
    )
    def test_anything_not_on_the_list_is_refused(self, monkeypatch, value):
        repo = FakeAdvanceRepo([])
        resp = self._post(monkeypatch, repo, value)

        assert resp.status_code == 422
        assert repo.created == []

    def test_no_advance_type_names_pay(self):
        """The list itself must stay pay-free, whatever a future edit adds.

        Matched on WORDS, not substrings: VENDOR_PAYMENT is a supplier being
        paid, not an employee being paid, and a substring test would ban it.
        """
        banned = {
            "SALARY",
            "SALARIES",
            "PAY",
            "PAYROLL",
            "WAGE",
            "WAGES",
            "PF",
            "EPF",
            "ESI",
            "BONUS",
            "INCENTIVE",
            "COMMISSION",
            "GRATUITY",
        }
        for value in expenses.ADVANCE_TYPES:
            words = set(value.upper().split("_"))
            assert not (words & banned), (
                f"advance type {value} is pay-shaped; pay belongs in Payroll"
            )

    def test_there_is_no_catch_all_option(self):
        """OTHER re-opens the free-text hole the owner just closed."""
        for value in expenses.ADVANCE_TYPES:
            assert value.upper() not in ("OTHER", "MISC", "MISCELLANEOUS", "GENERAL")

    def test_the_mongo_validator_accepts_exactly_these_reasons(self):
        """The DB-level enum must not contradict the router.

        database/schemas.py declares a $jsonSchema validator for the advances
        collection. It used to list SALARY_ADVANCE -- the one thing the owner
        banned -- and would have rejected every value on the new list the moment
        migrations created the collection.
        """
        from database.schemas import ADVANCE_SCHEMA

        assert set(ADVANCE_SCHEMA["properties"]["advance_type"]["enum"]) == set(
            expenses.ADVANCE_TYPES
        )

    def test_the_refusal_tells_the_requester_what_is_allowed(self, monkeypatch):
        """Plain English, not a bare 422: the message names every allowed value
        and points a salary advance at Payroll."""
        for value in expenses.ADVANCE_TYPES:
            assert value in expenses.ADVANCE_TYPE_ERROR
        assert "Payroll" in expenses.ADVANCE_TYPE_ERROR

        repo = FakeAdvanceRepo([])
        resp = self._post(monkeypatch, repo, "Salary advance")
        assert expenses.ADVANCE_TYPE_ERROR in resp.text


class TestAdvanceTypesMatchTheDropdown:
    """The dropdown a requester sees and the list the server accepts are ONE list.

    The frontend offers ADVANCE_TYPES from services/api/expenses.ts; the backend
    rejects anything outside ADVANCE_TYPES in routers/expenses.py. Two
    hand-maintained copies drift, and the drift shows up as a form that 422s
    AFTER the requester has typed everything -- worse than the hole it closes.
    The BACKEND tuple is the runtime authority; this test reads the real .ts file
    and fails if the pair differ, so the copy can never rot unnoticed.
    """

    ADVANCE_TYPES_TS = ts_constants.frontend_path("services", "api", "expenses.ts")

    def test_the_dropdown_offers_exactly_what_the_server_accepts(self):
        offered = ts_constants.read_object_list_values(
            self.ADVANCE_TYPES_TS, "ADVANCE_TYPES"
        )
        assert list(offered) == list(expenses.ADVANCE_TYPES), (
            "the advance-reason dropdown and the server's allowed list have "
            "drifted; every value the form offers must be accepted (or the "
            "requester gets a 422 after filling the form in) and every value "
            "the server accepts should be reachable from the form"
        )

    def test_every_offered_reason_is_actually_accepted_over_http(self, monkeypatch):
        """Membership is not enough -- drive each dropdown value through the API.

        A validator that agreed with the list but rejected on some other rule
        (whitespace, casing, a stray Optional) would pass the comparison above
        and still break live advance requests.
        """
        offered = ts_constants.read_object_list_values(
            self.ADVANCE_TYPES_TS, "ADVANCE_TYPES"
        )
        for value in offered:
            repo = FakeAdvanceRepo([])
            client = _client(monkeypatch, ["SALES_STAFF"], repo)
            resp = client.post(
                "/expenses/advances",
                json={"advance_type": value, "amount": 500.0, "purpose": "for work"},
            )
            assert resp.status_code == 201, f"the form offers {value} but: {resp.text}"
            assert repo.created[0]["advance_type"] == value
