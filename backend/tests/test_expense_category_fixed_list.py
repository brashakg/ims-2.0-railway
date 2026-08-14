"""
IMS 2.0 -- an expense category is a fixed list of SHOP heads, never pay
======================================================================
OWNER RULING 2026-08-14: the expense category is a closed list, enforced on the
server.

THE DEFECT. ExpenseCreate.category was a free-text ``str``, so POST
/api/v1/expenses accepted anything -- including "Staff Salaries". The finance
and budget screens strip payroll-shaped heads, but only for APPROVED expenses,
so a wage bill booked as an expense was visible by name and amount to every
approver while it sat PENDING (GET /expenses/pending-approval) and then vanished
once approved. The form has only ever offered a fixed dropdown, so the hole was
reachable only by calling the API directly.

Closing the list REMOVES the problem rather than managing it: pay cannot be
recorded as a shop expense at all, so nothing downstream has to hide it. The
pending-approval queue, the approval roles and every amount an approver sees are
DELIBERATELY unchanged -- an approver who cannot see the amount cannot approve,
and that is a live workflow in real stores.

Every assertion is on the RESPONSE BODY or on the repository writes that did or
did not happen, never on a reason string, and never on a filter the handler
happened to build. The positive control (every allowed head accepted, over HTTP)
comes FIRST: without it, a validator that refused everything would pass every
rejection test here and break expense entry in four shops.
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

from api.routers import expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


CATEGORIES_TSX = ts_constants.frontend_path(
    "pages", "finance", "ExpenseTracker.tsx"
)


class FakeExpenseRepo:
    """Records every write so a rejected claim can be shown to have written nothing."""

    def __init__(self):
        self.created = []

    def create(self, doc):
        self.created.append(dict(doc))
        return dict(doc)

    def find_one(self, filter=None):
        return None

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        return []

    def find_by_id(self, expense_id):
        return None


def _client(monkeypatch, repo, roles=("SALES_STAFF",)):
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Ravi Salesperson",
            "active_store_id": "store-001",
            "store_ids": ["store-001"],
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(expenses, "get_expense_repository", lambda: repo)
    monkeypatch.setattr(expenses, "get_advance_repository", lambda: None)
    monkeypatch.setattr(expenses, "_period_locked", lambda year, month: False)
    monkeypatch.setattr(expenses, "_load_caps", lambda: {"caps": [], "global": {}})
    return TestClient(app)


def _post(client, category, amount=1250.0):
    return client.post(
        "/expenses",
        json={
            "category": category,
            "amount": amount,
            "description": "Electricity bill for August",
            "expense_date": "2026-08-15",
        },
    )


# ---------------------------------------------------------------------------
# POSITIVE CONTROL FIRST. Live expense entry must keep working.
# ---------------------------------------------------------------------------
class TestEveryRealCategoryStillWorks:
    @pytest.mark.parametrize("category", list(expenses.EXPENSE_CATEGORIES))
    def test_every_allowed_category_is_accepted_and_stored_verbatim(
        self, monkeypatch, category
    ):
        repo = FakeExpenseRepo()
        resp = _post(_client(monkeypatch, repo), category)

        assert resp.status_code == 201, resp.text
        assert resp.json()["expense_id"]
        assert len(repo.created) == 1
        # Stored spelling must be the canonical one, because the petty-cash
        # rule, the spend caps and the finance reports all match it by string.
        assert repo.created[0]["category"] == category

    def test_petty_cash_keeps_the_exact_spelling_the_float_rule_matches(self):
        """A tidy-up to "petty_cash" would stop APPROVED claims debiting the float."""
        assert expenses._PETTY_CASH_CATEGORY in expenses.EXPENSE_CATEGORIES

    def test_miscellaneous_is_present_and_carries_no_extra_guard(self, monkeypatch):
        """OWNER RULING 2026-08-14: Miscellaneous stays EXACTLY as it is.

        No cap, no warning, no nudge -- he was offered a capped variant and
        removal and explicitly chose to keep it unchanged. A large amount under
        it is accepted, which is the accepted residual: an amount with a typed
        description, not a named salary.
        """
        assert "miscellaneous" in expenses.EXPENSE_CATEGORIES

        repo = FakeExpenseRepo()
        resp = _post(_client(monkeypatch, repo), "miscellaneous", amount=250000.0)

        assert resp.status_code == 201, resp.text
        assert repo.created[0]["amount"] == 250000.0


# ---------------------------------------------------------------------------
# THE HOLE ITSELF.
# ---------------------------------------------------------------------------
class TestPayCannotBeBookedAsAnExpense:
    @pytest.mark.parametrize(
        "category",
        [
            "Staff Salaries",
            "salary",
            "salaries",
            "payroll",
            "PAYROLL",
            "wages",
            "Advance against salary",
            "PF",
            "bonus",
        ],
    )
    def test_a_pay_head_is_refused_and_nothing_is_written(self, monkeypatch, category):
        repo = FakeExpenseRepo()
        resp = _post(_client(monkeypatch, repo), category)

        assert resp.status_code == 422
        assert repo.created == []

    @pytest.mark.parametrize(
        "category",
        ["", "  ", "anything at all", "OTHER", "misc", "rent ledger", "utilities2"],
    )
    def test_an_arbitrary_string_is_refused(self, monkeypatch, category):
        repo = FakeExpenseRepo()
        resp = _post(_client(monkeypatch, repo), category)

        assert resp.status_code == 422
        assert repo.created == []

    def test_no_category_names_pay(self):
        """The list itself must stay pay-free, whatever a future edit adds.

        Matched on WORDS, not substrings, so a future head that merely contains
        one of these letters-in-order is not banned by accident.
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
            "STIPEND",
        }
        for value in expenses.EXPENSE_CATEGORIES:
            words = set(value.upper().replace("-", "_").split("_"))
            assert not (words & banned), (
                f"expense category {value} is pay-shaped; pay belongs in Payroll"
            )

    def test_the_refusal_names_the_allowed_categories(self, monkeypatch):
        for value in expenses.EXPENSE_CATEGORIES:
            assert value in expenses.EXPENSE_CATEGORY_ERROR
        assert "Payroll" in expenses.EXPENSE_CATEGORY_ERROR

        repo = FakeExpenseRepo()
        resp = _post(_client(monkeypatch, repo), "Staff Salaries")
        assert expenses.EXPENSE_CATEGORY_ERROR in resp.text


# ---------------------------------------------------------------------------
# TRAP 1: CASE. The form submits "rent" but the approval screen compares
# category.toUpperCase() == "PETTY_CASH". The codebase is already inconsistent,
# so both cases must be accepted and exactly one spelling must be stored.
# ---------------------------------------------------------------------------
class TestCaseIsNormalisedAtTheBoundary:
    @pytest.mark.parametrize(
        "sent,stored",
        [
            ("rent", "rent"),
            ("RENT", "rent"),
            ("Rent", "rent"),
            ("  rent  ", "rent"),
            ("PETTY_CASH", "PETTY_CASH"),
            ("petty_cash", "PETTY_CASH"),
            ("Petty_Cash", "PETTY_CASH"),
            ("MISCELLANEOUS", "miscellaneous"),
        ],
    )
    def test_either_case_is_accepted_and_stored_canonically(
        self, monkeypatch, sent, stored
    ):
        repo = FakeExpenseRepo()
        resp = _post(_client(monkeypatch, repo), sent)

        assert resp.status_code == 201, resp.text
        assert repo.created[0]["category"] == stored

    def test_canonicaliser_is_total(self):
        """Any non-string / unknown input is None, never a crash."""
        assert expenses.canonical_expense_category(None) is None
        assert expenses.canonical_expense_category(42) is None
        assert expenses.canonical_expense_category(["rent"]) is None
        assert expenses.canonical_expense_category("Staff Salaries") is None
        assert expenses.canonical_expense_category("rent") == "rent"


# ---------------------------------------------------------------------------
# TRAP 2: THE CAPS CONFIG. resolve_cap matches (role, category) by exact string,
# so closing the expense side must not leave a configured cap unable to bind.
# ---------------------------------------------------------------------------
class TestSpendCapsAreNotOrphaned:
    def test_a_cap_saved_in_the_other_case_still_binds_to_an_expense(self):
        """The cap doc says "Rent"; the expense is stored as "rent"."""
        caps = expenses._load_caps  # noqa: F841  (documenting the read path below)
        normalised = expenses._canonicalise_cap_categories(
            [{"role": "SALES_STAFF", "category": "Rent", "daily": 500.0}]
        )
        assert normalised[0]["category"] == "rent"

        resolved = expenses.resolve_cap(
            "rent", "SALES_STAFF", {"caps": normalised, "global": {}}
        )
        assert resolved["daily"] == 500.0
        assert resolved["source"] == "role_category"

    def test_a_cap_configured_through_the_api_blocks_the_expense_it_names(
        self, monkeypatch
    ):
        """End to end: cap saved in one case, expense filed in another, blocked.

        This is the assertion that would have caught an orphaned cap -- it is on
        the 400 the claim receives, not on a filter dict.
        """
        repo = FakeExpenseRepo()
        client = _client(monkeypatch, repo)
        stored_doc = {
            "caps": [{"role": "SALES_STAFF", "category": "RENT", "daily": 1000.0}],
            "global": {},
        }
        monkeypatch.setattr(
            expenses,
            "_load_caps",
            lambda: {
                "caps": expenses._canonicalise_cap_categories(stored_doc["caps"]),
                "global": {},
            },
        )
        monkeypatch.setattr(
            expenses, "_spent_for_category", lambda emp, cat, on: (0.0, 0.0)
        )

        resp = _post(client, "rent", amount=5000.0)

        assert resp.status_code == 400, resp.text
        assert repo.created == []

    def test_a_cap_cannot_be_saved_against_a_category_that_does_not_exist(self):
        """Dead config is a limit an admin believes in and that never binds."""
        with pytest.raises(Exception):
            expenses.CapEntry(role="SALES_STAFF", category="Staff Salaries", daily=1.0)

    def test_a_cap_saved_through_the_model_is_canonicalised(self):
        entry = expenses.CapEntry(role="SALES_STAFF", category="petty_cash", daily=100.0)
        assert entry.category == "PETTY_CASH"

    def test_an_unrecognised_stored_cap_is_left_alone_not_invented(self):
        """It never bound before; rewriting it would invent a cap nobody set."""
        rows = expenses._canonicalise_cap_categories(
            [{"role": "SALES_STAFF", "category": "Staff Salaries", "daily": 1.0}]
        )
        assert rows[0]["category"] == "Staff Salaries"


# ---------------------------------------------------------------------------
# TRAP 3: OTHER WRITERS. If a path creates an expense document without going
# through ExpenseCreate, the validation is decorative.
# ---------------------------------------------------------------------------
class TestEveryWriterGoesThroughTheModel:
    def test_the_router_has_exactly_one_expense_create_call_site(self):
        """A second ``expense_repo.create(`` would be a path this never guards.

        Grepped the whole backend at the time of writing: the ONLY code that
        creates an expense document is create_expense in this router (the
        POST "" / POST "/" pair, one handler). petty_cash_service moves float
        money and never writes an expense; budgets, finance, analytics,
        dashboard_widgets, survival_cashflow and jarvis only READ. If that ever
        stops being true this fails and the new writer must be validated too.
        """
        import inspect

        source = inspect.getsource(expenses)
        assert source.count("expense_repo.create(") == 1

    def test_the_only_writer_is_the_validated_handler(self):
        """The create route's request model IS ExpenseCreate."""
        import inspect

        sig = inspect.signature(expenses.create_expense)
        assert sig.parameters["expense"].annotation is expenses.ExpenseCreate


# ---------------------------------------------------------------------------
# THE TWO LISTS. The dropdown the user sees and the list the server accepts.
# ---------------------------------------------------------------------------
class TestCategoriesMatchTheDropdown:
    """Backend tuple = runtime authority; this test reads the real .tsx file.

    Two hand-maintained copies drift, and the symptom is a live expense form
    that 422s AFTER the user has typed everything -- worse than the hole it
    closes. This repo has been bitten repeatedly by a rule in one place and not
    its twin, so the pair are pinned rather than trusted.
    """

    def test_the_form_offers_exactly_what_the_server_accepts(self):
        offered = ts_constants.read_object_list_values(CATEGORIES_TSX, "CATEGORIES")
        assert list(offered) == list(expenses.EXPENSE_CATEGORIES), (
            "the expense-category dropdown and the server's allowed list have "
            "drifted; every category the form offers must be accepted, or the "
            "user gets a 422 after filling the form in"
        )

    def test_every_category_the_form_offers_is_accepted_over_http(self, monkeypatch):
        """Membership is not enough -- drive each dropdown value through the API.

        A validator that agreed with the list but rejected on some other rule
        (whitespace, casing, a stray Optional) would pass the comparison above
        and still break live expense entry.
        """
        offered = ts_constants.read_object_list_values(CATEGORIES_TSX, "CATEGORIES")
        for value in offered:
            repo = FakeExpenseRepo()
            resp = _post(_client(monkeypatch, repo), value)
            assert resp.status_code == 201, f"the form offers {value} but: {resp.text}"
            assert repo.created[0]["category"] == value


# ---------------------------------------------------------------------------
# THE APPROVAL WORKFLOW IS UNCHANGED. Stated as a test so a later "tidy-up"
# that blanks an amount or narrows the queue trips a named failure.
# ---------------------------------------------------------------------------
class TestApproverStillSeesWhatTheyMustApprove:
    def test_pending_approval_still_shows_the_full_amount_to_an_approver(
        self, monkeypatch
    ):
        """An approver who cannot see the amount cannot approve. Live workflow."""
        pending = {
            "expense_id": "exp-1",
            "employee_id": "u9",
            "employee_name": "Ravi Salesperson",
            "store_id": "store-001",
            "category": "rent",
            "amount": 18500.0,
            "description": "August shop rent",
            "status": "PENDING",
        }

        class Repo(FakeExpenseRepo):
            def find_many(self, filter=None, sort=None, skip=0, limit=100):
                return [dict(pending)]

        app = FastAPI()
        app.include_router(expenses.router, prefix="/expenses")

        async def _fake_user():
            return {
                "user_id": "mgr-1",
                "active_store_id": "store-001",
                "store_ids": ["store-001"],
                "roles": ["STORE_MANAGER"],
            }

        app.dependency_overrides[get_current_user] = _fake_user
        monkeypatch.setattr(expenses, "get_expense_repository", lambda: Repo())
        monkeypatch.setattr(expenses, "get_advance_repository", lambda: None)

        resp = TestClient(app).get("/expenses/pending-approval")

        assert resp.status_code == 200
        body = resp.json()
        assert body["expenses"][0]["amount"] == 18500.0
        assert body["expenses"][0]["employee_name"] == "Ravi Salesperson"
