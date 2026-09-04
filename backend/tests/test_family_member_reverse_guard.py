"""
IMS 2.0 -- The REVERSE family-member split (owner ruling 2026-09-04)
====================================================================
"Block it the same way."

The forward split is closed (test_family_member_guard.py): a top-level
customer cannot be created with a number that already belongs to a FAMILY
MEMBER on another account. This file closes the other side: a family member
cannot be ADDED to an account with a number that is already a top-level
customer in their own right. One rule (customer_service.own_account_conflict),
applied at every door that adds a member row:

  * POST /customers            with a patients[] array (via ensure_customer)
  * PUT  /customers/{id}       patients-append
  * POST /customers/{id}/patients

Each refuses with the SAME 409 body (a fixed key set: the existing account's
id + name and the offending row's index + name -- never the account's record).
Exempt: the account's own number -- the holder's Self row, or a child recorded
under the parent's phone -- because that is one record, not two. Only rows
actually being appended are checked, so the clinical per-visit re-send of an
existing member never trips on a legacy split.

Every test drives the REAL router / service / repository over the strict
in-memory Mongo double (tests/strict_fakes.py).
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import HTTPException  # noqa: E402

from api.routers.customers import (  # noqa: E402
    CustomerCreate,
    CustomerUpdate,
    PatientCreate,
    add_patient,
    create_customer,
    update_customer,
)
from api.services import customer_service as cs  # noqa: E402
from strict_fakes import StrictCollection, StrictDB  # noqa: E402
from test_family_member_guard import (  # noqa: E402
    DAUGHTER_MOBILE,
    DAUGHTER_PID,
    HOLDER,
    HOLDER_ID,
    HOLDER_MOBILE,
    STORE,
    _user,
    _wire,
)

OWN_ID = "cust-own"
OWN_MOBILE = "9876500005"
FREE_MOBILE = "9876500009"

# Arun has his OWN account. Meena's household must not also list him as a
# family member under his number -- that is the reverse split.
OWN = {
    "customer_id": OWN_ID,
    "name": "Arun Kumar",
    "mobile": OWN_MOBILE,
    "phone": OWN_MOBILE,
    "customer_type": "B2C",
    "home_store_id": STORE,
    "preferred_store_id": STORE,
    "is_active": True,
    "primary_patient_id": "pat-arun",
    "patients": [
        {
            "patient_id": "pat-arun",
            "name": "Arun Kumar",
            "mobile": OWN_MOBILE,
            "relation": "Self",
            "is_primary": True,
        }
    ],
}

EXPECTED_KEYS = {
    "code",
    "message",
    "customer_id",
    "customer_name",
    "patient_index",
    "patient_name",
}


@pytest.fixture
def world(monkeypatch):
    db = StrictDB()
    db.seed("customers", [copy.deepcopy(HOLDER), copy.deepcopy(OWN)])
    repo = _wire(monkeypatch, db)
    return {"db": db, "repo": repo, "customers": db.get_collection("customers")}


def _member(name, mobile, relation="Son"):
    return {"name": name, "mobile": mobile, "relation": relation}


# The three doors, each driven exactly the way its caller drives it.
def _via_add_patient(member, customer_id=HOLDER_ID):
    return asyncio.run(add_patient(customer_id, PatientCreate(**member), _user()))


def _via_put(members, customer_id=HOLDER_ID):
    return asyncio.run(
        update_customer(
            customer_id,
            CustomerUpdate(patients=[PatientCreate(**m) for m in members]),
            _user(),
        )
    )


def _via_create(members, *, mobile="9876500008", name="Sunita Kumari"):
    return asyncio.run(
        create_customer(
            CustomerCreate(name=name, mobile=mobile, patients=[PatientCreate(**m) for m in members]),
            _user(),
        )
    )


def _refused(fn) -> HTTPException:
    with pytest.raises(HTTPException) as ei:
        fn()
    return ei.value


def _holder_patients(world):
    return world["repo"].find_by_id(HOLDER_ID)["patients"]


# ---------------------------------------------------------------------------
# 1. THE GUARD -- one rule, every member-adding door
# ---------------------------------------------------------------------------


def test_add_patient_refuses_a_number_that_is_someones_own_account_with_actionable_409(world):
    exc = _refused(lambda: _via_add_patient(_member("Arun", OWN_MOBILE)))
    assert exc.status_code == 409
    d = exc.detail
    assert d["code"] == "MOBILE_IS_OWN_ACCOUNT"
    assert d["customer_id"] == OWN_ID
    assert d["customer_name"] == "Arun Kumar"
    assert d["patient_index"] == 0
    assert d["patient_name"] == "Arun"
    # Enough for the UI to act on -- and nothing else of Arun's record.
    assert set(d) == EXPECTED_KEYS
    assert [p["patient_id"] for p in _holder_patients(world)] == ["pat-self", DAUGHTER_PID]


def test_put_append_refuses_and_appends_nothing_naming_the_submitted_index(world):
    """Two new rows, the second bad: the index is the SUBMITTED position (so a
    form can point at the row) and the good row is not appended either --
    one bad row, nothing added."""
    exc = _refused(
        lambda: _via_put([_member("Kabir", FREE_MOBILE), _member("Arun", OWN_MOBILE)])
    )
    assert exc.status_code == 409
    assert exc.detail["code"] == "MOBILE_IS_OWN_ACCOUNT"
    assert exc.detail["customer_id"] == OWN_ID
    assert exc.detail["patient_index"] == 1
    assert exc.detail["patient_name"] == "Arun"
    assert set(exc.detail) == EXPECTED_KEYS
    assert [p["patient_id"] for p in _holder_patients(world)] == ["pat-self", DAUGHTER_PID]


def test_post_customers_with_patients_refuses_and_creates_nothing(world):
    """The create door: its patients[] goes through the same rule inside
    ensure_customer. The new holder's own Self row (index 0) is exempt; the
    stranger's number at index 1 is refused and NO customer is created."""
    exc = _refused(
        lambda: _via_create(
            [_member("Sunita Kumari", "9876500008", "Self"), _member("Arun", OWN_MOBILE)]
        )
    )
    assert exc.status_code == 409
    assert exc.detail["code"] == "MOBILE_IS_OWN_ACCOUNT"
    assert exc.detail["customer_id"] == OWN_ID
    assert exc.detail["patient_index"] == 1
    assert exc.detail["patient_name"] == "Arun"
    assert set(exc.detail) == EXPECTED_KEYS
    assert world["customers"].count_documents({}) == 2
    assert world["repo"].find_by_mobile("9876500008") is None


def test_the_same_number_is_refused_identically_through_every_member_adding_door(world):
    """DIFFERENTIAL: one number, three doors, one answer. A door that drifts
    (its own copy of the rule, a different code, a different id) shows up as
    a second tuple here."""
    doors = {
        "POST /customers/{id}/patients": lambda: _via_add_patient(_member("Arun", OWN_MOBILE)),
        "PUT /customers/{id}": lambda: _via_put([_member("Arun", OWN_MOBILE)]),
        "POST /customers": lambda: _via_create([_member("Arun", OWN_MOBILE)]),
    }
    answers = {}
    for door, fn in doors.items():
        exc = _refused(fn)
        d = exc.detail
        answers[door] = (
            exc.status_code,
            d["code"],
            d["customer_id"],
            d["customer_name"],
            d["patient_index"],
            d["patient_name"],
            tuple(sorted(d)),
        )
    assert len(set(answers.values())) == 1, answers
    assert next(iter(answers.values()))[:4] == (409, "MOBILE_IS_OWN_ACCOUNT", OWN_ID, "Arun Kumar")
    # Nothing moved anywhere.
    assert world["customers"].count_documents({}) == 2
    assert len(_holder_patients(world)) == 2


# ---------------------------------------------------------------------------
# 2. EXEMPTIONS -- the account's own number, and rows not actually added
# ---------------------------------------------------------------------------


def test_the_accounts_own_number_on_a_member_row_is_exempt(world):
    """A child recorded under the parent's phone (the norm at the counter) and
    the holder's own Self row are ONE record -- never a conflict."""
    res = _via_add_patient(_member("Baby Devi", HOLDER_MOBILE, "Daughter"))
    assert res.get("patient_id") and not res.get("deduped")
    assert [p["name"] for p in _holder_patients(world)][-1] == "Baby Devi"

    res = _via_put([_member("Chotu Devi", HOLDER_MOBILE, "Son")])
    assert res["customer_id"] == HOLDER_ID
    assert [p["name"] for p in _holder_patients(world)][-1] == "Chotu Devi"

    # Create door: the new holder's Self row carries her own (free) number.
    created = _via_create([_member("Sunita Kumari", "9876500008", "Self")])
    assert created["customer_id"]
    assert [p["mobile"] for p in created["patients"]] == ["9876500008"]

    # ...and when the holder's number is TAKEN, the answer is the plain
    # top-level duplicate (a string), not a reverse-split body about her own
    # Self row -- the Self row never pre-empts the holder's own check.
    exc = _refused(
        lambda: _via_create(
            [_member("Arun Kumar", OWN_MOBILE, "Self")], mobile=OWN_MOBILE, name="Arun Kumar"
        )
    )
    assert exc.status_code == 409
    assert exc.detail == "Customer with this mobile already exists"


def test_re_sending_an_existing_member_on_a_legacy_split_is_not_blocked(world):
    """A split that pre-dates the guard: Riya is Meena's daughter AND has her
    own account. The clinical flow re-sends the existing member every visit
    (PUT) -- that adds nothing, so it must not 409; the repair is the owner's
    per-case decision (split report), not a blocked visit."""
    world["customers"].insert_one(
        {
            "customer_id": "cust-split",
            "name": "Riya D",
            "mobile": DAUGHTER_MOBILE,
            "phone": DAUGHTER_MOBILE,
            "home_store_id": STORE,
            "patients": [],
        }
    )
    res = _via_put([_member("Riya Devi", DAUGHTER_MOBILE, "Daughter")])
    assert res["customer_id"] == HOLDER_ID  # deduped -> nothing to change
    res = _via_add_patient(_member("Riya Devi", DAUGHTER_MOBILE, "Daughter"))
    assert res == {"patient_id": DAUGHTER_PID, "name": "Riya Devi", "deduped": True}
    assert len(_holder_patients(world)) == 2


def test_control_an_unrelated_number_still_adds(world):
    res = _via_add_patient(_member("Kabir", FREE_MOBILE))
    assert res.get("patient_id") and not res.get("deduped")
    rows = _holder_patients(world)
    assert len(rows) == 3 and rows[-1]["mobile"] == FREE_MOBILE


def test_rule_is_fail_soft_on_a_broken_read(world, monkeypatch):
    """Like the forward rule: a repo read that raises never 500s the counter."""

    def _boom(_mobile):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(world["repo"], "find_by_mobile", _boom)
    assert cs.own_account_conflict(world["repo"], [(0, _member("Arun", OWN_MOBILE))]) is None


# ---------------------------------------------------------------------------
# 3. The read-only repair list now says which splits are provably REVERSE
# ---------------------------------------------------------------------------


def _report_module():
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
        "family_member_split_report.py",
    )
    spec = importlib.util.spec_from_file_location("family_member_split_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_marks_a_split_reverse_only_when_the_own_account_predates_the_family_account(
    capsys,
):
    mod = _report_module()
    coll = StrictCollection("customers")
    # Arun's own account existed BEFORE Meena's family account was created, so
    # his member row can only have been added afterwards: provably REVERSE.
    holder = copy.deepcopy(HOLDER)
    holder["created_at"] = "2026-03-01T00:00:00"
    holder["patients"].append(
        {"patient_id": "pat-arun-copy", "name": "Arun", "mobile": OWN_MOBILE, "relation": "Son"}
    )
    coll.insert_one(holder)
    coll.insert_one({**copy.deepcopy(OWN), "created_at": "2026-01-01T00:00:00"})
    # Riya's own account came AFTER the family account: direction not provable.
    coll.insert_one(
        {
            "customer_id": "cust-split",
            "name": "Riya D",
            "mobile": DAUGHTER_MOBILE,
            "patients": [],
            "created_at": "2026-05-01T00:00:00",
        }
    )

    rows = mod.find_splits(coll)
    assert sorted(rows, key=lambda r: r["own_customer_id"]) == [
        {
            "holder_customer_id": HOLDER_ID,
            "patient_id": "pat-arun-copy",
            "own_customer_id": OWN_ID,
            "direction": "REVERSE",
        },
        {
            "holder_customer_id": HOLDER_ID,
            "patient_id": DAUGHTER_PID,
            "own_customer_id": "cust-split",
            "direction": "UNKNOWN",
        },
    ]
    mod.print_report(rows)
    out = capsys.readouterr().out
    assert "splits found: 2" in out
    assert "provably REVERSE" in out and ": 1;" in out
    assert f"{OWN_ID} | REVERSE" in out
    for pii in ("Arun", "Riya", "Meena", OWN_MOBILE, DAUGHTER_MOBILE, HOLDER_MOBILE):
        assert pii not in out
    assert len(coll.docs) == 3  # read-only


def test_report_direction_is_unknown_without_timestamps():
    """No created_at on either side -> never guessed."""
    mod = _report_module()
    coll = StrictCollection("customers")
    coll.insert_one(copy.deepcopy(HOLDER))
    coll.insert_one(
        {"customer_id": "cust-split", "name": "Riya D", "mobile": DAUGHTER_MOBILE, "patients": []}
    )
    assert [r["direction"] for r in mod.find_splits(coll)] == ["UNKNOWN"]
