"""IMS 2.0 - the product-incentive kicker redacts TO SELF, and the total goes too.

OWNER RULING, answered 2026-08-13. He was offered self-only / admins-only /
leave-open for GET /api/v1/incentive/kicker/{ym} and chose SELF-ONLY: a store
manager sees their OWN incentive but not a colleague's. He accepted the cost --
his managers can no longer check the incentive figure on a sale they personally
entered, and will ask him instead.

WHY THESE TESTS LOOK THE WAY THEY DO
------------------------------------
The test that appeared to guard the salary rule elsewhere in this repo declared
its OWN copy of the rule in the test body, so its assertion was true by
construction; 56 tests stayed green while a STORE_MANAGER received the wage
bill. So every test here DRIVES THE ENDPOINT and asserts on the RESPONSE BODY.
None re-implements the rule, none asserts on a reason string, and each fix has
been mutation-checked (revert the fix -> a NAMED test below fails on its
requirement assertion; see the PR body for the roll-call).

There are POSITIVE CONTROLS throughout: an ADMIN must still get the full
breakdown, sales staff must still get their own row, and a manager must still be
able to LOG a kicker for their staff. A suite that only proves we can refuse
everything proves nothing.

THE DATASET: one store, two people. The reviewer's planted colleague kicker of
4321 for a NAMED employee, plus 111 for the reader themselves -- deliberately
chosen so the team total (4432), the colleague's figure (4321) and the reader's
own (111) are three distinct numbers that cannot be confused for one another.

Hermetic fakes throughout -- no Mongo, so nothing here can flake on a shared CI
database. No emoji (Windows cp1252).
"""

from __future__ import annotations

import itertools
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-kicker-self-only")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import kicker, payout  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import rbac_policy  # noqa: E402


# ===========================================================================
# The planted dataset
# ===========================================================================

STORE = "ZZ-KICK-01"
YM = "2026-06"

COLLEAGUE_ID = "ZZ-EMP-COLLEAGUE"
COLLEAGUE_NAME = "Rekha Colleague"
COLLEAGUE_RUPEES = 4321.0          # the reviewer's planted figure

READER_ID = "ZZ-EMP-READER"        # whoever is signed in, in every role below
READER_NAME = "Reader Themselves"
READER_RUPEES = 111.0

TEAM_TOTAL = COLLEAGUE_RUPEES + READER_RUPEES   # 4432.0

_ENTRIES = [
    {
        "entry_id": "K-1",
        "store_id": STORE,
        "ym": YM,
        "date_str": "2026-06-10",
        "date": datetime(2026, 6, 10),
        "staff_id": COLLEAGUE_ID,
        "staff_name": COLLEAGUE_NAME,
        "sku": "ZEISS-PAL-1.5",
        "brand": "ZEISS",
        "category": "PAL",
        "order_id": "ZZ-O1",
        "incentive_amount": COLLEAGUE_RUPEES,
        "deleted_at": None,
    },
    {
        "entry_id": "K-2",
        "store_id": STORE,
        "ym": YM,
        "date_str": "2026-06-12",
        "date": datetime(2026, 6, 12),
        "staff_id": READER_ID,
        "staff_name": READER_NAME,
        "sku": "ZEISS-SV-1.6",
        "brand": "ZEISS",
        "category": "SV",
        "order_id": "ZZ-O2",
        "incentive_amount": READER_RUPEES,
        "deleted_at": None,
    },
]


class _FakeKickerRepo:
    """Only the two methods the router calls. list_for_ym mirrors the real
    repository's semantics EXACTLY, including the one that matters here: a
    FALSY staff_id means NO FILTER, i.e. the whole store. A fake that quietly
    returned nothing for staff_id=None would hide the fail-open branch this
    suite exists to pin."""

    def __init__(self):
        self.logged = []

    def list_for_ym(self, store_id, ym, staff_id=None):
        rows = [
            dict(e)
            for e in _ENTRIES
            if e["store_id"] == store_id and e["ym"] == ym
        ]
        if staff_id:
            rows = [r for r in rows if r["staff_id"] == staff_id]
        return rows

    def log_entry(self, doc):
        saved = dict(doc, entry_id="K-NEW")
        self.logged.append(saved)
        return saved


def _session(*roles, user_id=READER_ID, store=STORE):
    return {
        "user_id": user_id,
        "username": "tester",
        "full_name": READER_NAME,
        "active_store_id": store,
        "store_ids": [store],
        "roles": list(roles),
    }


@pytest.fixture
def kicker_repo(monkeypatch):
    repo = _FakeKickerRepo()
    monkeypatch.setattr(kicker, "_kicker_repo", lambda: repo)
    monkeypatch.setattr(kicker, "_audit", lambda **k: None)
    monkeypatch.setattr(kicker, "_resolve_staff_name", lambda sid: "Whoever")
    return repo


def _client(session):
    app = FastAPI()
    app.include_router(kicker.router, prefix="/api/v1/incentive/kicker")

    async def _u():
        return session

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _rollup(session, query=""):
    return _client(session).get(f"/api/v1/incentive/kicker/{YM}{query}")


# ===========================================================================
# 1. POSITIVE CONTROL -- the admin keeps the full breakdown
# ===========================================================================


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_admin_still_gets_the_whole_store_breakdown(kicker_repo, role):
    """Guard against over-stripping. The owner kept ADMIN/SUPERADMIN whole; if
    this fails, somebody 'fixed' the leak by blinding everybody."""
    body = _rollup(_session(role)).json()
    by_staff = {row["staff_id"]: row for row in body["items"]}
    assert set(by_staff) == {COLLEAGUE_ID, READER_ID}
    assert by_staff[COLLEAGUE_ID]["staff_name"] == COLLEAGUE_NAME
    assert by_staff[COLLEAGUE_ID]["total_rupees"] == COLLEAGUE_RUPEES
    assert body["total"] == TEAM_TOTAL
    assert body["scope"] == "store"


# ===========================================================================
# 2. THE REQUIREMENT -- a colleague's row is gone below admin
# ===========================================================================

_REDACTED_ROLES = ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"]


@pytest.mark.parametrize("role", _REDACTED_ROLES)
def test_a_colleagues_kicker_is_withheld_below_admin(kicker_repo, role):
    """The reviewer drove this at 200 for all three roles and read
    staff_name + total_rupees for a NAMED employee straight off the row."""
    r = _rollup(_session(role))
    assert r.status_code == 200, r.text
    assert COLLEAGUE_NAME not in r.text, f"{role} received the colleague's name"
    assert COLLEAGUE_ID not in r.text, f"{role} received the colleague's id"
    assert str(COLLEAGUE_RUPEES) not in r.text
    assert str(int(COLLEAGUE_RUPEES)) not in r.text


@pytest.mark.parametrize("role", _REDACTED_ROLES)
def test_the_reader_still_sees_their_own_row(kicker_repo, role):
    """Self-only means SELF, not nothing. A manager who loses their own
    incentive line has been over-stripped, and would have to ask the owner for
    a number the owner said they may keep."""
    body = _rollup(_session(role)).json()
    assert [row["staff_id"] for row in body["items"]] == [READER_ID]
    assert body["items"][0]["total_rupees"] == READER_RUPEES
    assert body["scope"] == "self"


# ===========================================================================
# 3. THE PART THAT IS EASY TO MISS -- the grand total goes with the rows
# ===========================================================================


@pytest.mark.parametrize("role", _REDACTED_ROLES)
def test_the_total_is_the_readers_own_not_the_teams(kicker_repo, role):
    """Redact-to-self alone does not hold: a manager subtracts their own row
    from a team total and, in a two-person store, has the other person exactly.
    So `total` must be the reader's OWN total. 111, never 4432."""
    body = _rollup(_session(role)).json()
    assert body["total"] == READER_RUPEES, (
        f"{role} received a team-level total of {body['total']}; subtracting "
        f"their own {READER_RUPEES} yields the colleague's {COLLEAGUE_RUPEES}"
    )


def _numeric_leaves(value, prefix=""):
    """Every number anywhere in the body, at any depth -- so a team-level
    figure hiding inside a nested object is caught too, not just top-level."""
    out = []
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        return [(prefix or "<root>", float(value))]
    if isinstance(value, dict):
        for key, sub in value.items():
            out.extend(_numeric_leaves(sub, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for idx, sub in enumerate(value):
            out.extend(_numeric_leaves(sub, f"{prefix}[{idx}]"))
    return out


@pytest.mark.parametrize("role", _REDACTED_ROLES)
def test_a_colleagues_kicker_is_not_recoverable_by_arithmetic(kicker_repo, role):
    """The requirement stated the way the attacker states it.

    Removing the colleague's ROW while leaving any team-level aggregate beside
    it is the fix that LOOKS right and is not -- team_total minus own_row is the
    colleague. This searches every signed combination of up to three numbers
    left anywhere in the body (the reader also knows their own figure, so that
    is supplied as a fourth term they already hold) for both the colleague's
    figure and the team total.
    """
    body = _rollup(_session(role)).json()
    numbers = _numeric_leaves(body)
    # What the reader independently knows: their own incentive, from their
    # payslip. An attack does not have to start from the response alone.
    numbers.append(("<own payslip>", READER_RUPEES))

    for target, label in ((COLLEAGUE_RUPEES, "colleague"), (TEAM_TOTAL, "team total")):
        for size in (1, 2, 3):
            for combo in itertools.combinations(numbers, size):
                for signs in itertools.product((1, -1), repeat=size):
                    got = sum(s * v for s, (_, v) in zip(signs, combo))
                    if abs(abs(got) - target) < 0.005:
                        terms = " ".join(
                            f"{'+' if s > 0 else '-'}{n}({v})"
                            for s, (n, v) in zip(signs, combo)
                        )
                        pytest.fail(
                            f"{role}: the {label} figure {target} is recoverable "
                            f"as {terms} -- hiding a row while leaving the "
                            f"aggregate it sits inside is not hiding it"
                        )


# ===========================================================================
# 4. The query parameter cannot be used to walk around the redaction
# ===========================================================================


@pytest.mark.parametrize("role", _REDACTED_ROLES)
def test_naming_a_colleague_in_staff_id_returns_the_readers_own_rows(
    kicker_repo, role
):
    """staff_id is caller-supplied. Asking for somebody else by name must not
    be the way through -- it is narrowed to the caller, exactly as sales staff
    have always experienced."""
    r = _rollup(_session(role), query=f"?staff_id={COLLEAGUE_ID}")
    assert r.status_code == 200, r.text
    assert COLLEAGUE_NAME not in r.text
    assert r.json()["total"] == READER_RUPEES


def test_an_admin_may_still_filter_to_one_person(kicker_repo):
    """Positive control on the same parameter: it is a genuine filter for the
    people allowed to use it, and must not have been neutered for everyone."""
    body = _rollup(_session("ADMIN"), query=f"?staff_id={COLLEAGUE_ID}").json()
    assert [row["staff_id"] for row in body["items"]] == [COLLEAGUE_ID]
    assert body["total"] == COLLEAGUE_RUPEES


# ===========================================================================
# 5. The path we REUSED rather than re-wrote -- sales staff, unchanged
# ===========================================================================


@pytest.mark.parametrize("role", ["SALES_STAFF", "SALES_CASHIER", "CASHIER"])
def test_sales_staff_self_view_still_works(kicker_repo, role):
    """This branch already behaved correctly and is the one the manager roles
    were routed into. If it broke, the reuse broke."""
    body = _rollup(_session(role)).json()
    assert [row["staff_id"] for row in body["items"]] == [READER_ID]
    assert body["total"] == READER_RUPEES
    assert COLLEAGUE_NAME not in str(body)


def test_a_session_we_cannot_identify_is_refused_not_handed_the_store(
    kicker_repo,
):
    """FAIL CLOSED. The repository treats a falsy staff_id as 'no filter', so a
    session with no user_id would be handed the WHOLE store's rollup by the very
    branch meant to restrict it. It must refuse -- and not by showing a zero,
    which would read as 'you earned nothing this month'."""
    session = _session("STORE_MANAGER")
    session["user_id"] = None
    r = _rollup(session)
    assert r.status_code == 403, r.text
    assert COLLEAGUE_NAME not in r.text
    assert str(int(COLLEAGUE_RUPEES)) not in r.text


def test_the_refusal_is_written_in_plain_english(kicker_repo):
    """The frontend shows `detail` verbatim to a non-technical store manager."""
    session = _session("STORE_MANAGER")
    session["user_id"] = None
    detail = _rollup(session).json()["detail"]
    for jargon in ("403", "forbidden", "rbac", "staff_id", "null", "none"):
        assert jargon not in detail.lower(), f"jargon in user-facing text: {jargon}"


# ===========================================================================
# 6. THE OTHER SIDE OF THE GUARD -- writing is a different rule and still works
# ===========================================================================


_SALE = {
    "staff_id": COLLEAGUE_ID,
    "date": "2026-06-14",
    "sku": "ZEISS-PAL-1.6",
    "brand": "ZEISS",
    "category": "PAL",
    "order_id": "ZZ-O9",
    "incentive_amount": 250,
}


@pytest.mark.parametrize(
    "role", ["ADMIN", "SUPERADMIN", "STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"]
)
def test_a_manager_may_still_log_a_kicker_for_their_staff(kicker_repo, role):
    """The owner restricted READING somebody's incentive, not RECORDING the
    sale that earns it. A manager who cannot log the kicker cannot run the
    store, and the response only echoes the amount they just typed."""
    r = _client(_session(role)).post(
        "/api/v1/incentive/kicker/product-sale", json=_SALE
    )
    assert r.status_code == 201, r.text
    assert kicker_repo.logged[-1]["staff_id"] == COLLEAGUE_ID


def test_sales_staff_still_cannot_log_a_kicker_for_somebody_else(kicker_repo):
    """Unchanged rule, asserted here because the role tuple it uses was renamed
    in this PR (_MANAGER_ROLES -> _LOG_FOR_OTHERS_ROLES) and a rename is exactly
    how a gate goes missing."""
    r = _client(_session("SALES_STAFF")).post(
        "/api/v1/incentive/kicker/product-sale", json=_SALE
    )
    assert r.status_code == 403, r.text
    assert kicker_repo.logged == []


def test_sales_staff_may_log_their_own(kicker_repo):
    """Positive control for the line above."""
    r = _client(_session("SALES_STAFF")).post(
        "/api/v1/incentive/kicker/product-sale",
        json=dict(_SALE, staff_id=READER_ID),
    )
    assert r.status_code == 201, r.text


# ===========================================================================
# 7. The RBAC policy row -- the second, request-time layer
# ===========================================================================


def test_the_kicker_read_row_stays_open_on_purpose(app):
    """DELIBERATE, and recorded so a later 'tidy-up' does not close the route.

    The redaction is on the FIGURE, inside the handler, not on the route: every
    employee keeps a view of their OWN kicker, which the owner asked for. An
    ADMIN-only row here would 403 sales staff at the middleware and delete that.
    What this test does enforce is that the row is still CATALOGUED and still
    marked store-scoped, so the next reviewer reads an accurate table.
    """
    entry = rbac_policy.policy_for("GET", "/api/v1/incentive/kicker/2026-06")
    assert entry is not None, "the kicker rollup fell out of the policy table"
    assert entry["allowed"] == rbac_policy.AUTHENTICATED
    assert entry.get("store_scoped") is True
    assert rbac_policy.is_store_scoped("GET", "/api/v1/incentive/kicker/2026-06")


def test_the_kicker_route_is_not_public():
    """Fail-closed floor: whatever else changes, it never becomes PUBLIC."""
    entry = rbac_policy.policy_for("GET", "/api/v1/incentive/kicker/2026-06")
    assert entry["allowed"] != rbac_policy.PUBLIC


# ===========================================================================
# 8. TASK 3 -- the payout CSV export 500 the previous reviewer saw
# ===========================================================================


_SNAPSHOT = {
    "snapshot_id": "SNAP-ZZ-K",
    "store_id": STORE,
    "year": 2026,
    "month": 6,
    "status": "LOCKED",
    "staff_payouts": [{"user_id": READER_ID, "name": READER_NAME, "total_payout": 1.0}],
    "manager_bonuses": [],
    "grand_total": {"all": 1.0},
    "inputs": {},
}


def _export(doc, monkeypatch):
    class _Repo:
        def find_by_id(self, snapshot_id):
            return dict(doc)

    monkeypatch.setattr(payout, "_snapshot_repo", lambda: _Repo())
    app = FastAPI()
    app.include_router(payout.router, prefix="/api/v1/payout")

    async def _u():
        return _session("ADMIN")

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/payout/export/SNAP-ZZ-K.csv"
    )


def test_payout_csv_export_works_for_an_admin(monkeypatch):
    """Positive control: the ordinary, well-formed export is untouched."""
    r = _export(_SNAPSHOT, monkeypatch)
    assert r.status_code == 200, r.text
    assert "2026-06" in r.text


@pytest.mark.parametrize("bad_month", [None, "6"])
def test_payout_csv_export_survives_a_snapshot_without_an_int_month(
    monkeypatch, bad_month
):
    """The previous reviewer saw this route 500 FOR ADMIN and suspected their
    fixture. They were right about the cause and it was worth chasing: `month`
    was the ONE field in the whole handler whose absence raised instead of
    degrading (an f-string ':02d' on None is a TypeError), so a single header
    cell could fail an entire money export. Every other missing field already
    degraded to a blank; this makes month behave the same way."""
    doc = dict(_SNAPSHOT)
    if bad_month is None:
        doc.pop("month")
    else:
        doc["month"] = bad_month
    r = _export(doc, monkeypatch)
    assert r.status_code == 200, r.text
    assert READER_NAME in r.text, "the export lost its payload while degrading"
