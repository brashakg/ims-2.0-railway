"""
IMS 2.0 -- ONE HOUSEHOLD + ONE WAY TO MINT A MEMBER ROW (owner rulings 2026-09-04)
==================================================================================
"Block it, one household account."

RULING A. A number may be a family member on only ONE account. Adding a member
whose number already sits in ``patients[]`` of a DIFFERENT account is refused,
naming the account that already holds them. One rule
(customer_service.household_conflict), applied at the same three doors as the
reverse guard:

  * POST /customers            with a patients[] array (via ensure_customer)
  * PUT  /customers/{id}       patients-append
  * POST /customers/{id}/patients

Each refuses with the SAME 409 body (a fixed key set). Exempt: the same account
(re-sending its own member under another spelling), and the account's own
number (the holder's Self row, a child under the parent's phone).

RULING B. Member rows were minted in three places with drifting relation
defaults ("Other" / "Family" / a name heuristic) and no ``created_at``. They
now all go through ``customer_service.make_patient_row``; the differential
below proves there is one implementation (one key set, one default, one
timestamp) and that the split report can read the timestamp.

Every test drives the REAL router / service / repository over the strict
in-memory Mongo double (tests/strict_fakes.py).
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
from datetime import datetime

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
    promote_patient_to_own_account,
    update_customer,
)
from api.services import customer_service as cs  # noqa: E402
from api.services.member_billing import ensure_primary_member  # noqa: E402
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
from test_family_member_reverse_guard import OWN, OWN_ID, OWN_MOBILE, _report_module  # noqa: E402

HOUSE2_ID = "cust-house2"
HOUSE2_MOBILE = "9876500011"
FREE_MOBILE = "9876500009"
NEW_HOLDER_MOBILE = "9876500008"

# Sunil's household. Riya (Meena's daughter) must not ALSO be listed as a
# family member here -- one household account per person.
HOUSE2 = {
    "customer_id": HOUSE2_ID,
    "name": "Sunil Verma",
    "mobile": HOUSE2_MOBILE,
    "phone": HOUSE2_MOBILE,
    "customer_type": "B2C",
    "home_store_id": STORE,
    "preferred_store_id": STORE,
    "is_active": True,
    "primary_patient_id": "pat-sunil",
    "patients": [
        {
            "patient_id": "pat-sunil",
            "name": "Sunil Verma",
            "mobile": HOUSE2_MOBILE,
            "relation": "Self",
            "is_primary": True,
        }
    ],
}

EXPECTED_KEYS = {
    "code",
    "message",
    "customer_id",
    "account_holder_name",
    "patient_id",
    "patient_name",
    "relation",
    "patient_index",
}

ROW_KEYS = {"patient_id", "name", "mobile", "dob", "anniversary", "relation", "created_at"}


@pytest.fixture
def world(monkeypatch):
    db = StrictDB()
    db.seed("customers", [copy.deepcopy(HOLDER), copy.deepcopy(HOUSE2)])
    repo = _wire(monkeypatch, db)
    return {"db": db, "repo": repo, "customers": db.get_collection("customers")}


def _member(name, mobile, relation="Daughter"):
    return {"name": name, "mobile": mobile, "relation": relation}


def _via_add_patient(member, customer_id=HOUSE2_ID):
    return asyncio.run(add_patient(customer_id, PatientCreate(**member), _user()))


def _via_put(members, customer_id=HOUSE2_ID):
    return asyncio.run(
        update_customer(
            customer_id,
            CustomerUpdate(patients=[PatientCreate(**m) for m in members]),
            _user(),
        )
    )


def _via_create(members, *, mobile=NEW_HOLDER_MOBILE, name="Sunita Kumari"):
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


def _patients(world, customer_id):
    return world["repo"].find_by_id(customer_id)["patients"]


def _assert_riya_body(d, index):
    assert d["code"] == "MOBILE_ON_ANOTHER_HOUSEHOLD"
    assert d["customer_id"] == HOLDER_ID
    assert d["account_holder_name"] == "Meena Devi"
    assert d["patient_id"] == DAUGHTER_PID
    assert d["patient_name"] == "Riya Devi"
    assert d["relation"] == "Daughter"
    assert d["patient_index"] == index
    # Enough for the UI to act on -- and nothing else of Meena's record.
    assert set(d) == EXPECTED_KEYS


# ---------------------------------------------------------------------------
# 1. THE RULE -- one household, every member-adding door
# ---------------------------------------------------------------------------


def test_add_patient_refuses_a_number_already_on_another_household_with_actionable_409(world):
    exc = _refused(lambda: _via_add_patient(_member("Riya", DAUGHTER_MOBILE)))
    assert exc.status_code == 409
    _assert_riya_body(exc.detail, 0)
    assert [p["patient_id"] for p in _patients(world, HOUSE2_ID)] == ["pat-sunil"]


def test_put_append_refuses_and_appends_nothing_naming_the_submitted_index(world):
    exc = _refused(
        lambda: _via_put([_member("Kabir", FREE_MOBILE, "Son"), _member("Riya", DAUGHTER_MOBILE)])
    )
    assert exc.status_code == 409
    _assert_riya_body(exc.detail, 1)
    assert [p["patient_id"] for p in _patients(world, HOUSE2_ID)] == ["pat-sunil"]


def test_post_customers_with_patients_refuses_and_creates_nothing(world):
    exc = _refused(
        lambda: _via_create(
            [_member("Sunita Kumari", NEW_HOLDER_MOBILE, "Self"), _member("Riya", DAUGHTER_MOBILE)]
        )
    )
    assert exc.status_code == 409
    _assert_riya_body(exc.detail, 1)
    assert world["customers"].count_documents({}) == 2
    assert world["repo"].find_by_mobile(NEW_HOLDER_MOBILE) is None


def test_the_same_number_is_refused_identically_through_every_member_adding_door(world):
    """DIFFERENTIAL: one number, three doors, one answer. A door that drifts
    (its own copy of the rule, a different code, a different id) shows up as
    a second tuple here."""
    doors = {
        "POST /customers/{id}/patients": lambda: _via_add_patient(_member("Riya", DAUGHTER_MOBILE)),
        "PUT /customers/{id}": lambda: _via_put([_member("Riya", DAUGHTER_MOBILE)]),
        "POST /customers": lambda: _via_create([_member("Riya", DAUGHTER_MOBILE)]),
    }
    answers = {}
    for door, fn in doors.items():
        exc = _refused(fn)
        d = exc.detail
        answers[door] = (
            exc.status_code,
            d["code"],
            d["customer_id"],
            d["account_holder_name"],
            d["patient_id"],
            d["patient_name"],
            d["relation"],
            d["patient_index"],
            tuple(sorted(d)),
        )
    assert len(set(answers.values())) == 1, answers
    assert next(iter(answers.values()))[:5] == (
        409,
        "MOBILE_ON_ANOTHER_HOUSEHOLD",
        HOLDER_ID,
        "Meena Devi",
        DAUGHTER_PID,
    )
    # Nothing moved anywhere.
    assert world["customers"].count_documents({}) == 2
    assert len(_patients(world, HOUSE2_ID)) == 1
    assert len(_patients(world, HOLDER_ID)) == 2


def test_a_number_that_is_both_an_own_account_and_on_a_household_answers_own_account(world):
    """Sibling precedence: the reverse guard runs first, so the counter is sent
    to the person's OWN account (the popup with no promote), not the household."""
    world["customers"].insert_one({**copy.deepcopy(OWN), "customer_id": OWN_ID})
    holder = world["repo"].find_by_id(HOLDER_ID)
    world["customers"].update_one(
        {"customer_id": HOLDER_ID},
        {"$set": {"patients": holder["patients"] + [_member("Arun", OWN_MOBILE, "Son")]}},
    )
    exc = _refused(lambda: _via_add_patient(_member("Arun", OWN_MOBILE)))
    assert exc.status_code == 409
    assert exc.detail["code"] == "MOBILE_IS_OWN_ACCOUNT"
    assert exc.detail["customer_id"] == OWN_ID


# ---------------------------------------------------------------------------
# 2. EXEMPTIONS -- the same account, and the account's own number
# ---------------------------------------------------------------------------


def test_re_sending_the_accounts_own_member_under_another_spelling_is_not_blocked(world):
    """The (name, mobile) dedup misses a re-spelt name, so the row reaches the
    guard: the holder found is THIS account -> never a household conflict."""
    res = _via_add_patient(_member("Riya D", DAUGHTER_MOBILE), customer_id=HOLDER_ID)
    assert res.get("patient_id") and "code" not in res
    res = _via_put([_member("Riya Dev", DAUGHTER_MOBILE)], customer_id=HOLDER_ID)
    assert res["customer_id"] == HOLDER_ID


def test_the_accounts_own_number_on_a_member_row_is_exempt_even_when_it_sits_elsewhere(world):
    """A legacy forward split: Meena's own number is ALSO a member row on
    Sunil's account. Recording her child under her own phone (the norm at
    the counter) is still her household, not Sunil's -- never a conflict."""
    house2 = world["repo"].find_by_id(HOUSE2_ID)
    world["customers"].update_one(
        {"customer_id": HOUSE2_ID},
        {"$set": {"patients": house2["patients"] + [_member("Meena V", HOLDER_MOBILE, "Sister")]}},
    )
    assert world["repo"].find_by_patient_mobile(HOLDER_MOBILE)["customer_id"] == HOLDER_ID
    # Make Sunil's account the one the index yields first, so the exemption
    # (not scan order) is what passes the row.
    world["customers"].docs.reverse()
    assert world["repo"].find_by_patient_mobile(HOLDER_MOBILE)["customer_id"] == HOUSE2_ID

    res = _via_add_patient(_member("Baby Devi", HOLDER_MOBILE), customer_id=HOLDER_ID)
    assert res.get("patient_id") and not res.get("deduped")
    res = _via_put([_member("Chotu Devi", HOLDER_MOBILE, "Son")], customer_id=HOLDER_ID)
    assert res["customer_id"] == HOLDER_ID
    assert [p["name"] for p in _patients(world, HOLDER_ID)][-2:] == ["Baby Devi", "Chotu Devi"]


def test_control_an_unrelated_number_still_adds(world):
    res = _via_add_patient(_member("Kabir", FREE_MOBILE, "Son"))
    assert res.get("patient_id") and not res.get("deduped")
    rows = _patients(world, HOUSE2_ID)
    assert len(rows) == 2 and rows[-1]["mobile"] == FREE_MOBILE


def test_rule_is_fail_soft_on_a_broken_read(world, monkeypatch):
    def _boom(_mobile):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(world["repo"], "find_by_patient_mobile", _boom)
    assert cs.household_conflict(world["repo"], [(0, _member("Riya", DAUGHTER_MOBILE))]) is None


# ---------------------------------------------------------------------------
# 3. ONE WAY TO MINT A MEMBER ROW
# ---------------------------------------------------------------------------


def _shape(row):
    return (tuple(sorted(row)), row["relation"], type(row["created_at"]))


def test_every_mint_site_produces_the_one_row_shape(world):
    """DIFFERENTIAL across the mint sites: same key set, same relation
    default when none is given, a real ``created_at``. A site with its own
    row dict (the old "Family" / name-heuristic copies) shows up as a second
    shape here."""
    before = datetime.now()
    created = _via_create(
        [
            # The holder's own name with NO relation: the dead name heuristic
            # used to stamp "Self" here.
            {"name": "Sunita Kumari", "mobile": NEW_HOLDER_MOBILE},
            {"name": "Kabir", "mobile": FREE_MOBILE},
        ]
    )
    _via_add_patient({"name": "Dev", "mobile": "9876500021"}, customer_id=HOLDER_ID)
    _via_put([{"name": "Tara", "mobile": "9876500022"}], customer_id=HOUSE2_ID)
    after = datetime.now()

    create_rows = _patients(world, created["customer_id"])
    rows = {
        "POST /customers": create_rows[1],
        "POST /customers/{id}/patients": _patients(world, HOLDER_ID)[-1],
        "PUT /customers/{id}": _patients(world, HOUSE2_ID)[-1],
    }
    shapes = {door: _shape(r) for door, r in rows.items()}
    assert len(set(shapes.values())) == 1, shapes
    assert next(iter(shapes.values())) == (tuple(sorted(ROW_KEYS)), "Other", datetime)
    for r in rows.values():
        assert before <= r["created_at"] <= after
    # The holder-named row: no heuristic, same default; it is still the
    # Primary (first row) by position, not by a guessed relation.
    assert create_rows[0]["relation"] == "Other" and create_rows[0]["is_primary"] is True
    assert set(create_rows[0]) == ROW_KEYS | {"is_primary"}


def test_the_self_row_and_a_promoted_account_mint_through_the_same_builder(world):
    before = datetime.now()
    legacy = {"customer_id": "cust-legacy", "name": " Legacy Person ", "mobile": "+91 98765 00012"}
    primary, changed = ensure_primary_member(legacy)
    assert changed and legacy["patients"] == [primary]
    assert set(primary) == ROW_KEYS | {"is_primary"}
    assert primary["relation"] == "Self" and primary["is_primary"] is True
    assert primary["name"] == "Legacy Person" and primary["mobile"] == "9876500012"
    assert before <= primary["created_at"] <= datetime.now()

    res = asyncio.run(promote_patient_to_own_account(HOLDER_ID, DAUGHTER_PID, _user()))
    own = world["repo"].find_by_id(res["customer_id"])["patients"][0]
    assert set(own) == ROW_KEYS | {"is_primary"}
    assert own["patient_id"] == DAUGHTER_PID and own["relation"] == "Self"
    assert own["dob"] == "2010-05-01" and own["is_primary"] is True
    assert before <= own["created_at"] <= datetime.now()


def test_the_row_builder_keeps_an_unparseable_legacy_number_rather_than_dropping_it():
    """A TechCherry landline on a Self row must not vanish, and must not 500
    an order-create that seeds a Primary for a legacy account."""
    row = cs.make_patient_row(name="Old Shop", mobile="0651-234567")
    assert row["mobile"] == "0651-234567"
    assert cs.make_patient_row(name="X", mobile="+91 98765 00012")["mobile"] == "9876500012"
    assert cs.make_patient_row(name="X", mobile="  ")["mobile"] is None


# ---------------------------------------------------------------------------
# 4. The read-only report: provable direction from the row timestamp, and the
#    new household listing
# ---------------------------------------------------------------------------


def test_report_direction_is_provable_both_ways_from_the_member_row_timestamp(capsys):
    mod = _report_module()
    coll = StrictCollection("customers")
    # Meena's account (03-01). Arun's own account (04-01). His member row on
    # Meena's account was minted 06-01: own account first -> REVERSE. Without
    # the row stamp this is UNKNOWN (own account is younger than the family
    # account), so only the timestamp can prove it.
    holder = copy.deepcopy(HOLDER)
    holder["created_at"] = "2026-03-01T00:00:00"
    holder["patients"].append(
        {
            "patient_id": "pat-arun-copy",
            "name": "Arun",
            "mobile": OWN_MOBILE,
            "relation": "Son",
            "created_at": datetime(2026, 6, 1),
        }
    )
    # Riya's row was minted 03-02, her own account 05-01: row first -> FORWARD.
    holder["patients"][1]["created_at"] = "2026-03-02T00:00:00"
    coll.insert_one(holder)
    coll.insert_one({**copy.deepcopy(OWN), "created_at": "2026-04-01T00:00:00"})
    coll.insert_one(
        {
            "customer_id": "cust-split",
            "name": "Riya D",
            "mobile": DAUGHTER_MOBILE,
            "patients": [],
            "created_at": "2026-05-01T00:00:00",
        }
    )
    # A third split with NO row stamp and a younger own account: never guessed.
    coll.insert_one(
        {
            "customer_id": "cust-house3",
            "name": "Third",
            "mobile": "9876500031",
            "created_at": "2026-01-01T00:00:00",
            "patients": [{"patient_id": "pat-k", "name": "K", "mobile": "9876500032"}],
        }
    )
    coll.insert_one(
        {
            "customer_id": "cust-k-own",
            "name": "K Own",
            "mobile": "9876500032",
            "patients": [],
            "created_at": "2026-02-01T00:00:00",
        }
    )

    rows = {r["own_customer_id"]: r["direction"] for r in mod.find_splits(coll)}
    assert rows == {OWN_ID: "REVERSE", "cust-split": "FORWARD", "cust-k-own": "UNKNOWN"}
    mod.print_report(mod.find_splits(coll), mod.find_household_overlaps(coll))
    out = capsys.readouterr().out
    assert "splits found: 3" in out
    assert "provably FORWARD" in out and "provably REVERSE" in out
    assert "cust-split | FORWARD" in out and f"{OWN_ID} | REVERSE" in out
    assert "overlaps found: 0" in out
    for pii in ("Arun", "Riya", "Meena", OWN_MOBILE, DAUGHTER_MOBILE, HOLDER_MOBILE):
        assert pii not in out
    assert len(coll.docs) == 5  # read-only


def test_report_lists_a_member_on_two_households_ids_only(capsys):
    mod = _report_module()
    coll = StrictCollection("customers")
    coll.insert_one(copy.deepcopy(HOLDER))
    house2 = copy.deepcopy(HOUSE2)
    house2["patients"] += [
        {"patient_id": "pat-riya-2", "name": "Riya V", "mobile": DAUGHTER_MOBILE, "relation": "Niece"},
        # Meena's OWN number as a member here is a forward split (listed in the
        # splits section), not a second household for her Self row.
        {"patient_id": "pat-meena-2", "name": "Meena V", "mobile": HOLDER_MOBILE, "relation": "Sister"},
    ]
    coll.insert_one(house2)
    coll.insert_one(
        {"customer_id": "cust-clean", "name": "Nobody", "mobile": "9876500007", "patients": []}
    )

    overlaps = mod.find_household_overlaps(coll)
    assert overlaps == [
        {
            "first_customer_id": HOLDER_ID,
            "first_patient_id": DAUGHTER_PID,
            "second_customer_id": HOUSE2_ID,
            "second_patient_id": "pat-riya-2",
        }
    ]
    assert [r["own_customer_id"] for r in mod.find_splits(coll)] == [HOLDER_ID]
    mod.print_report(mod.find_splits(coll), overlaps)
    out = capsys.readouterr().out
    assert "[FAMILY-MEMBER ON TWO HOUSEHOLDS]" in out
    assert "overlaps found: 1" in out
    assert f"{HOLDER_ID} | {DAUGHTER_PID} | {HOUSE2_ID} | pat-riya-2" in out
    for pii in ("Riya", "Meena", "Sunil", DAUGHTER_MOBILE, HOLDER_MOBILE, HOUSE2_MOBILE):
        assert pii not in out
    assert len(coll.docs) == 3  # read-only
