"""
IMS 2.0 -- Family-member guard + promote-to-own-account (owner ruling 2026-09-04)
=================================================================================
"Block it outright, but make a proper system ... to be able to do it effectively."

A person recorded as a FAMILY MEMBER (patients[].mobile) on someone else's
account must not ALSO be created as their own top-level customer -- that splits
one person's Rx/purchase history across two records. Locks:

  * POST /customers refuses with a 409 whose body is actionable (holder id +
    name, member id/name/relation) and NOTHING more of the holder's record.
  * The walkout door and the online door go through the SAME implementation
    (ensure_customer): on the identical seeded state they resolve the number to
    the account already holding the person and create nothing.
  * A race-lost unique-index insert is a 409, not the old 500 / phantom 201.
  * PROMOTE moves the member out: new own account (same patient_id), Rx + eye
    tests re-pointed, member row pulled from the parent -- in that order, so a
    failure after the create leaves the person in BOTH places, never neither.
  * PUT (mobile edit) cannot open the side door the create door closed.
  * Sibling landmines: an email-only web buyer's record carries NO `mobile`
    key (sparse-unique index indexes ""), a swallowed duplicate re-finds the
    winner instead of returning a never-persisted id, and a phoneless
    TechCherry row carries no `mobile: None`.

Every test drives the REAL router / service / repository over the strict
in-memory Mongo double (tests/strict_fakes.py) -- a fake that honours
`patients.mobile` and `$pull` with a document condition, because a double that
ignores them is blind to exactly this bug.
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

import api.dependencies as deps  # noqa: E402
from api.routers import customers as customers_mod  # noqa: E402
from api.routers.customers import (  # noqa: E402
    CustomerCreate,
    CustomerUpdate,
    create_customer,
    promote_patient_to_own_account,
    update_customer,
)
from api.services import customer_service as cs  # noqa: E402
from database.repositories.customer_repository import CustomerRepository  # noqa: E402
from strict_fakes import StrictCollection, StrictDB  # noqa: E402

# The walkout router's own DB wiring (fixture) + payload helper.
from test_walkouts import _full_payload, patched_walkouts  # noqa: E402,F401

STORE = "BV-BOK-01"
HOLDER_ID = "cust-holder"
HOLDER_MOBILE = "9876500001"
DAUGHTER_PID = "pat-daughter"
DAUGHTER_MOBILE = "9876500002"

HOLDER = {
    "customer_id": HOLDER_ID,
    "name": "Meena Devi",
    "mobile": HOLDER_MOBILE,
    "phone": HOLDER_MOBILE,
    "email": "meena@example.com",
    "customer_type": "B2C",
    "home_store_id": STORE,
    "preferred_store_id": STORE,
    "is_active": True,
    "marketing_consent": False,
    "data_consent": True,
    "data_consent_at": "2026-01-01T00:00:00",
    "data_consent_text_version": "v3",
    "primary_patient_id": "pat-self",
    "patients": [
        {
            "patient_id": "pat-self",
            "name": "Meena Devi",
            "mobile": HOLDER_MOBILE,
            "relation": "Self",
            "is_primary": True,
        },
        {
            "patient_id": DAUGHTER_PID,
            "name": "Riya Devi",
            "mobile": DAUGHTER_MOBILE,
            "relation": "Daughter",
            "dob": "2010-05-01",
        },
    ],
}


class DuplicateKeyError(Exception):
    """Named like pymongo's: BaseRepository matches the class NAME."""


def _user(store=STORE, roles=("SALES_STAFF",)):
    return {
        "user_id": "u-staff",
        "username": "staff",
        "roles": list(roles),
        "active_store_id": store,
    }


def _wire(monkeypatch, db: StrictDB):
    repo = CustomerRepository(db.get_collection("customers"))
    monkeypatch.setattr(customers_mod, "get_customer_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_customer_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    monkeypatch.setattr(customers_mod, "get_audit_repository", lambda: None)
    return repo


@pytest.fixture
def world(monkeypatch):
    db = StrictDB()
    db.seed("customers", [copy.deepcopy(HOLDER)])
    repo = _wire(monkeypatch, db)
    return {"db": db, "repo": repo, "customers": db.get_collection("customers")}


# ---------------------------------------------------------------------------
# 1. THE GUARD -- through the human door and through both lenient doors
# ---------------------------------------------------------------------------


def test_post_customers_refuses_a_family_members_number_with_actionable_409(world):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            create_customer(CustomerCreate(name="Riya Devi", mobile=DAUGHTER_MOBILE), _user())
        )
    assert ei.value.status_code == 409
    d = ei.value.detail
    assert d["code"] == "MOBILE_BELONGS_TO_FAMILY_MEMBER"
    assert d["customer_id"] == HOLDER_ID
    assert d["account_holder_name"] == "Meena Devi"
    assert d["patient_id"] == DAUGHTER_PID
    assert d["patient_name"] == "Riya Devi"
    assert d["relation"] == "Daughter"
    # Enough for the UI to act on -- and nothing else of the holder's record.
    assert set(d) == {
        "code",
        "message",
        "customer_id",
        "account_holder_name",
        "patient_id",
        "patient_name",
        "relation",
    }
    assert world["customers"].count_documents({}) == 1  # nothing was created


def test_post_customers_still_creates_an_unrelated_number(world):
    """Control: the guard is not a blanket refusal."""
    res = asyncio.run(
        create_customer(CustomerCreate(name="Anita Singh", mobile="9876500009"), _user())
    )
    assert res["customer_id"]
    assert world["repo"].find_by_id(res["customer_id"])["mobile"] == "9876500009"
    assert world["customers"].count_documents({}) == 2


def test_walkout_door_resolves_family_member_to_the_holding_account(world):
    """Same seeded state, other door, same implementation: a walk-in whose number
    is Meena's daughter links to Meena's account -- no second record."""
    cid, created = cs.ensure_customer(
        world["db"], mobile="+91 98765 00002", name="Riya", store_id=STORE, source="WALKOUT"
    )
    assert (cid, created) == (HOLDER_ID, False)
    assert world["customers"].count_documents({}) == 1


def test_walkout_http_door_links_family_member_to_holder_without_a_create(
    client, auth_headers, patched_walkouts
):
    """End to end through POST /walkouts (the router's own DB wiring)."""
    coll = patched_walkouts["customer_repo"].collection
    coll.insert_one(copy.deepcopy(HOLDER))

    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(mobile=DAUGHTER_MOBILE), headers=auth_headers
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["customer_id"] == HOLDER_ID
    assert coll.count_documents({}) == 1


def test_online_door_resolves_family_member_to_the_holding_account(world):
    from api.services.online_order_mapper import _match_or_create_customer

    cid = _match_or_create_customer(
        world["db"],
        {"phone": "+919876500002", "email": "riya@example.com", "name": "Riya D"},
        "BV-ONLINE-01",
    )
    assert cid == HOLDER_ID
    assert world["customers"].count_documents({}) == 1
    # A match is not a create: nothing of the buyer was stamped onto the holder.
    assert world["repo"].find_by_id(HOLDER_ID)["email"] == "meena@example.com"


def test_race_lost_insert_is_a_409_not_a_500(monkeypatch):
    """The unique index rejects the insert because a racing writer landed the
    same number between the dedup read and the write."""

    class _RacingCollection(StrictCollection):
        def insert_one(self, doc):
            winner = {
                "customer_id": "cust-winner",
                "name": "Anita (won)",
                "mobile": doc.get("mobile"),
                "phone": doc.get("mobile"),
                "patients": [],
            }
            super().insert_one(winner)
            raise DuplicateKeyError("E11000 duplicate key error index: mobile_1")

    db = StrictDB()
    coll = _RacingCollection("customers")
    db._collections["customers"] = coll
    repo = _wire(monkeypatch, db)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            create_customer(CustomerCreate(name="Anita Singh", mobile="9876500009"), _user())
        )
    assert ei.value.status_code == 409
    assert ei.value.detail == "Customer with this mobile already exists"
    assert repo.find_by_mobile("9876500009")["customer_id"] == "cust-winner"
    assert coll.count_documents({}) == 1


# ---------------------------------------------------------------------------
# 2. PROMOTE -- carries the history, leaves the parent without the member
# ---------------------------------------------------------------------------


def _seed_history(db: StrictDB):
    db.seed(
        "prescriptions",
        [
            {"prescription_id": "rx-1", "patient_id": DAUGHTER_PID, "customer_id": HOLDER_ID},
            {"prescription_id": "rx-2", "patient_id": DAUGHTER_PID, "customer_id": HOLDER_ID},
            {"prescription_id": "rx-mom", "patient_id": "pat-self", "customer_id": HOLDER_ID},
        ],
    )
    db.seed("eye_tests", [{"test_id": "t-1", "patient_id": DAUGHTER_PID, "customer_id": HOLDER_ID}])


def test_promote_carries_rx_and_eye_tests_and_removes_the_member(world):
    _seed_history(world["db"])

    res = asyncio.run(promote_patient_to_own_account(HOLDER_ID, DAUGHTER_PID, _user()))
    new_id = res["customer_id"]
    assert new_id and new_id != HOLDER_ID
    assert res["carried"] == {"prescriptions": 2, "eye_tests": 1, "eye_test_queue": 0}

    own = world["repo"].find_by_id(new_id)
    assert own["mobile"] == DAUGHTER_MOBILE and own["phone"] == DAUGHTER_MOBILE
    assert own["name"] == "Riya Devi"
    assert own["dob"] == "2010-05-01"
    assert own["home_store_id"] == STORE
    assert own["promoted_from"]["customer_id"] == HOLDER_ID
    # Same patient_id on the new account: that is what keeps the Rx links whole.
    assert [p["patient_id"] for p in own["patients"]] == [DAUGHTER_PID]
    assert own["patients"][0]["is_primary"] is True
    assert own["patients"][0]["relation"] == "Self"
    assert own["primary_patient_id"] == DAUGHTER_PID
    # Consent given on the family account travels with the person.
    assert own["data_consent_text_version"] == "v3"

    parent = world["repo"].find_by_id(HOLDER_ID)
    assert [p["patient_id"] for p in parent["patients"]] == ["pat-self"]

    rx = world["db"].get_collection("prescriptions")
    assert {r["customer_id"] for r in rx.find({"patient_id": DAUGHTER_PID})} == {new_id}
    assert rx.find_one({"prescription_id": "rx-mom"})["customer_id"] == HOLDER_ID
    assert world["db"].get_collection("eye_tests").find_one({"test_id": "t-1"})["customer_id"] == new_id

    # The guard now sees her as a top-level customer, not a family member.
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            create_customer(CustomerCreate(name="Riya Devi", mobile=DAUGHTER_MOBILE), _user())
        )
    assert ei.value.status_code == 409
    assert ei.value.detail == "Customer with this mobile already exists"


def test_promote_refuses_when_the_member_already_has_an_own_account(world):
    """An EXISTING split (the 9 prod cases): owner decides per case, no auto-merge."""
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
    with pytest.raises(HTTPException) as ei:
        asyncio.run(promote_patient_to_own_account(HOLDER_ID, DAUGHTER_PID, _user()))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "MOBILE_ALREADY_OWN_ACCOUNT"
    assert ei.value.detail["customer_id"] == "cust-split"
    assert len(world["repo"].find_by_id(HOLDER_ID)["patients"]) == 2
    assert world["customers"].count_documents({}) == 2


def test_promote_refuses_the_account_holder(world):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(promote_patient_to_own_account(HOLDER_ID, "pat-self", _user()))
    assert ei.value.status_code == 400
    assert world["customers"].count_documents({}) == 1


def test_promote_unknown_member_is_404(world):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(promote_patient_to_own_account(HOLDER_ID, "pat-nobody", _user()))
    assert ei.value.status_code == 404


def test_promote_is_store_scoped_like_every_customer_write(world):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            promote_patient_to_own_account(HOLDER_ID, DAUGHTER_PID, _user(store="BV-PUN-01"))
        )
    assert ei.value.status_code == 403
    assert world["customers"].count_documents({}) == 1
    assert len(world["repo"].find_by_id(HOLDER_ID)["patients"]) == 2


def test_promote_failure_after_create_leaves_person_in_both_places_never_neither(
    world, monkeypatch
):
    """ORDERING: create -> re-point -> pull. When the LAST step fails the own
    account exists AND the parent still lists the member (recoverable, listed by
    the repair script), and the error names the account that was created."""
    _seed_history(world["db"])
    monkeypatch.setattr(world["repo"], "pull_patient", lambda *_a, **_k: False)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(promote_patient_to_own_account(HOLDER_ID, DAUGHTER_PID, _user()))
    assert ei.value.status_code == 500
    detail = ei.value.detail
    assert detail["code"] == "PROMOTE_INCOMPLETE"
    new_id = detail["customer_id"]
    assert world["repo"].find_by_id(new_id)["mobile"] == DAUGHTER_MOBILE
    assert DAUGHTER_PID in [p["patient_id"] for p in world["repo"].find_by_id(HOLDER_ID)["patients"]]
    rx = world["db"].get_collection("prescriptions")
    assert {r["customer_id"] for r in rx.find({"patient_id": DAUGHTER_PID})} == {new_id}


# ---------------------------------------------------------------------------
# 3. PUT cannot open the side door
# ---------------------------------------------------------------------------


def test_put_mobile_edit_refuses_a_family_members_number(world):
    world["customers"].insert_one(
        {
            "customer_id": "cust-other",
            "name": "Someone Else",
            "mobile": "9876500003",
            "phone": "9876500003",
            "home_store_id": STORE,
            "patients": [],
        }
    )
    with pytest.raises(HTTPException) as ei:
        asyncio.run(update_customer("cust-other", CustomerUpdate(mobile=DAUGHTER_MOBILE), _user()))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "MOBILE_BELONGS_TO_FAMILY_MEMBER"
    assert ei.value.detail["customer_id"] == HOLDER_ID
    assert world["repo"].find_by_id("cust-other")["mobile"] == "9876500003"


def test_put_own_number_that_matches_own_self_row_is_not_a_conflict(world):
    """The holder's Self member row carries her own number: re-saving it must pass."""
    res = asyncio.run(
        update_customer(HOLDER_ID, CustomerUpdate(mobile=HOLDER_MOBILE, name="Meena Devi"), _user())
    )
    assert res is not None
    assert world["repo"].find_by_id(HOLDER_ID)["mobile"] == HOLDER_MOBILE


# ---------------------------------------------------------------------------
# 4. Sibling landmines on the same door
# ---------------------------------------------------------------------------


class _SparseUniqueMobile(StrictCollection):
    """Prod's index: UNIQUE + SPARSE on `mobile`. Sparse exempts only a MISSING
    key -- an explicit "" or null IS indexed and therefore collides."""

    def insert_one(self, doc):
        if "mobile" in doc and any(
            "mobile" in d and d["mobile"] == doc["mobile"] for d in self.docs
        ):
            raise DuplicateKeyError("E11000 duplicate key error index: mobile_1")
        return super().insert_one(doc)


def _online_world(monkeypatch, collection_cls=_SparseUniqueMobile):
    db = StrictDB()
    db._collections["customers"] = collection_cls("customers")
    repo = _wire(monkeypatch, db)
    return db, repo


def test_second_email_only_web_buyer_is_persisted_not_a_phantom(monkeypatch):
    from api.services.online_order_mapper import _match_or_create_customer

    db, repo = _online_world(monkeypatch)
    first = _match_or_create_customer(db, {"email": "a@example.com", "name": "A"}, "BV-ONLINE-01")
    second = _match_or_create_customer(db, {"email": "b@example.com", "name": "B"}, "BV-ONLINE-01")
    assert first and second and first != second
    assert repo.find_by_id(first) is not None
    assert repo.find_by_id(second) is not None  # the phantom: an id never saved
    assert db.get_collection("customers").count_documents({}) == 2
    assert "mobile" not in repo.find_by_id(second)


def test_swallowed_duplicate_refinds_the_winner_instead_of_a_phantom_id(monkeypatch):
    """A racing email-only create landed between the read and the write; the
    repo swallows the index error. The order must link to the row that EXISTS."""
    from api.services.online_order_mapper import _match_or_create_customer

    class _RacingEmail(StrictCollection):
        def insert_one(self, doc):
            super().insert_one(
                {
                    "customer_id": "cust-winner",
                    "email": doc.get("email"),
                    "contact_tier": "MARKETING",
                    "patients": [],
                }
            )
            raise DuplicateKeyError("E11000 duplicate key error index: email_1")

    db, repo = _online_world(monkeypatch, _RacingEmail)
    cid = _match_or_create_customer(db, {"email": "c@example.com", "name": "C"}, "BV-ONLINE-01")
    assert cid == "cust-winner"
    assert repo.find_by_id(cid) is not None


def test_techcherry_phoneless_row_carries_no_mobile_key():
    from api.routers.techcherry_import import _map_customer

    doc = _map_customer({"Name": "No Phone Person"}, "BV-PUN-01", "techcherry")
    assert doc is not None
    assert "mobile" not in doc
    with_phone = _map_customer({"Name": "X", "Mobile": "+91 98765 43210"}, "BV-PUN-01", "techcherry")
    assert with_phone["mobile"] == "9876543210"


# ---------------------------------------------------------------------------
# 5. The read-only repair list (ids and counts only)
# ---------------------------------------------------------------------------


def test_split_report_lists_ids_only(capsys):
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
        "family_member_split_report.py",
    )
    spec = importlib.util.spec_from_file_location("family_member_split_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    coll = StrictCollection("customers")
    coll.insert_one(copy.deepcopy(HOLDER))
    coll.insert_one(
        {"customer_id": "cust-split", "name": "Riya D", "mobile": DAUGHTER_MOBILE, "patients": []}
    )
    coll.insert_one(
        {"customer_id": "cust-clean", "name": "Nobody", "mobile": "9876500007", "patients": []}
    )

    rows = mod.find_splits(coll)
    assert rows == [
        {"holder_customer_id": HOLDER_ID, "patient_id": DAUGHTER_PID, "own_customer_id": "cust-split"}
    ]
    mod.print_report(rows)
    out = capsys.readouterr().out
    assert "splits found: 1" in out
    assert "cust-split" in out
    for pii in ("Riya", "Meena", DAUGHTER_MOBILE, HOLDER_MOBILE):
        assert pii not in out
    assert len(coll.docs) == 3  # read-only
