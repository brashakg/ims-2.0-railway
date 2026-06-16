"""
IMS 2.0 -- Unification steps 2 + 3: customer phone-normalizer + PUT-door guards
================================================================================
Locks the two unification fixes:

STEP 2 -- ONE phone normalizer (api.services.phone.normalize_indian_mobile) is
the single source of truth in the three non-canonical phone-accepting paths:
  * walkouts.CreateWalkoutRequest -- accepts '+91 98...' / spaced input, rejects
    a 0-leading non-mobile (the old local ^\\d{10}$ regex did the opposite).
  * techcherry_import._normalise_phone -- normalizes a valid number to the bare
    10-digit form; never crashes a bulk-import row on a junk/landline value.
  * online_order_mapper._match_or_create_customer -- stores the NORMALIZED form
    in mobile/phone and keeps the ORIGINAL verbatim under a new `raw_phone`
    field (the raw-string fallback that could persist an un-dedupable phone is
    gone).

STEP 3 -- PUT /customers/{id} no longer skips the validation POST enforces:
  * mobile/email/GSTIN format + customer_type whitelist are reused from create.
  * B2B-without-GSTIN is blocked on the merged state (422).
  * a mobile that belongs to ANOTHER customer -> 409 (same customer -> allowed).
  * credit_limit (khata) edits are gated to manager+ roles: a cashier -> 403,
    a store manager -> ok.
  * POST .../patients de-dupes family members on (name, mobile) like the PUT.

Every DB accessor is monkeypatched + docs are seeded; no whole-JSON substring
assertions. The async endpoints are driven directly (no HTTP / no Mongo).
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.routers import customers as customers_mod  # noqa: E402
from api.routers.customers import (  # noqa: E402
    CustomerUpdate,
    PatientCreate,
    add_patient,
    get_customer,
    update_customer,
)


# ---------------------------------------------------------------------------
# Fake customer repository -- the ONE accessor every endpoint reads through.
# ---------------------------------------------------------------------------


class _FakeCustomerRepo:
    """In-memory stand-in for CustomerRepository -- only the methods the two
    endpoints touch. Seeded with docs the test controls; no Mongo."""

    def __init__(self, docs):
        # docs: list of customer dicts (each carries customer_id + mobile/phone)
        self.docs = {d["customer_id"]: dict(d) for d in docs}
        self.updates = []  # records (customer_id, update_data) for assertions
        self.added_patients = []  # records (customer_id, patient_dict)

    def find_by_id(self, customer_id):
        d = self.docs.get(customer_id)
        return dict(d) if d else None

    def find_by_mobile(self, mobile):
        # Mirror the real repo: match either `phone` or `mobile`.
        for d in self.docs.values():
            if d.get("mobile") == mobile or d.get("phone") == mobile:
                return dict(d)
        return None

    def update(self, customer_id, update_data):
        self.updates.append((customer_id, update_data))
        if customer_id in self.docs:
            self.docs[customer_id].update(update_data)
            return True
        return False

    def add_patient(self, customer_id, patient):
        self.added_patients.append((customer_id, patient))
        self.docs.setdefault(customer_id, {}).setdefault("patients", []).append(patient)
        return True


def _patch_repo(monkeypatch, docs):
    # get_customer / update / add_patient now enforce OBJECT-LEVEL store scope.
    # These tests assert business rules (GSTIN/dedup/credit-role), not scope, so
    # default each seed customer to the test store the acting user belongs to
    # (BV-TEST-01) -- a doc may still override by setting its own home_store_id.
    docs = [{"home_store_id": "BV-TEST-01", **d} for d in docs]
    repo = _FakeCustomerRepo(docs)
    monkeypatch.setattr(customers_mod, "get_customer_repository", lambda: repo)
    # Audit is best-effort; neuter it so a missing audit repo can't interfere.
    monkeypatch.setattr(customers_mod, "_audit_customer", lambda *a, **k: None)
    return repo


def _cashier(store="BV-TEST-01"):
    return {
        "user_id": "u-cashier",
        "username": "cashier",
        "roles": ["SALES_CASHIER"],
        "active_store_id": store,
    }


def _manager(store="BV-TEST-01"):
    return {
        "user_id": "u-mgr",
        "username": "mgr",
        "roles": ["STORE_MANAGER"],
        "active_store_id": store,
    }


# ===========================================================================
# STEP 2 -- walkout phone normalization (canonical util)
# ===========================================================================


class TestWalkoutPhoneNormalizer:
    def _make(self, mobile):
        from api.routers.walkouts import CreateWalkoutRequest

        return CreateWalkoutRequest(
            customer_name="Walk In",
            mobile=mobile,
            age_group="26-35",
            gender="MALE",
            product_interested="FRAME",
            has_prescription="YES",
            displayed_price_range="1000-2000",
            required_price_range="<1000",
            primary_walkout_reason="BUDGET/PRICE",
            purchase_planned_in="1-7 DAYS",
            sales_person_id="sp-1",
        )

    def test_accepts_plus91_spaced(self):
        # The old local ^\d{10}$ regex REJECTED this; the shared util accepts it.
        assert self._make("+91 98765 43210").mobile == "9876543210"

    def test_accepts_leading_0_trunk(self):
        assert self._make("09876543210").mobile == "9876543210"

    def test_rejects_zero_leading_nonmobile(self):
        # A 10-digit number starting 0 is NOT a valid Indian mobile -> 422.
        with pytest.raises(ValidationError):
            self._make("0123456789")

    def test_rejects_leading_5(self):
        with pytest.raises(ValidationError):
            self._make("5234567890")

    def test_empty_is_none(self):
        assert self._make("").mobile is None

    def test_bare_valid_unchanged(self):
        assert self._make("9876543210").mobile == "9876543210"


# ===========================================================================
# STEP 2 -- TechCherry import normalizes (and never crashes on junk)
# ===========================================================================


class TestTechCherryNormalizer:
    def test_normalises_valid_variants(self):
        from api.routers.techcherry_import import _normalise_phone

        assert _normalise_phone("+91 98765 43210") == "9876543210"
        assert _normalise_phone("09876543210") == "9876543210"
        assert _normalise_phone("91-9876543210") == "9876543210"
        assert _normalise_phone("9876543210") == "9876543210"

    def test_junk_returns_blank_not_raises(self):
        from api.routers.techcherry_import import _normalise_phone

        # Too-short / leading 0-5 / foreign -> '' (best-effort import keeps
        # going), NOT a raised ValueError that would 500 the whole batch row.
        assert _normalise_phone("12345") == ""  # too short
        assert _normalise_phone("1234567890") == ""  # 10 digits but leading 1
        assert _normalise_phone("0000000000") == ""  # leading 0 -> not 6-9
        assert _normalise_phone(None) == ""
        assert _normalise_phone("") == ""

    def test_map_customer_uses_canonical_form(self):
        from api.routers.techcherry_import import _map_customer

        doc = _map_customer(
            {"name": "Ramesh", "Mobile": "+91 98765 43210"}, "BV-PUN-01", "techcherry"
        )
        assert doc["phone"] == "9876543210"
        # The dedupe key (customer_id) is the canonical phone.
        assert doc["customer_id"] == "9876543210"


# ===========================================================================
# STEP 2 -- online buyer keeps the raw phone under raw_phone
# ===========================================================================


class TestOnlineMapperRawPhone:
    def test_normalized_stored_and_raw_preserved(self, monkeypatch):
        from api.services import online_order_mapper

        created = {}

        class _Repo:
            def find_by_mobile(self, m):
                return None

            def find_by_email(self, e):
                return None

            def create(self, doc):
                created.update(doc)
                return doc

        # The mapper lazily imports get_customer_repository from ..dependencies.
        import api.dependencies as deps

        monkeypatch.setattr(deps, "get_customer_repository", lambda: _Repo())

        buyer = {"name": "Online Bob", "phone": "+91 98765 43210", "email": "b@x.com"}
        cid = online_order_mapper._match_or_create_customer(
            db=object(), buyer=buyer, store_id="BV-ONLINE-01"
        )
        assert cid  # a customer was created
        # Canonical normalized form stored in BOTH mobile + phone.
        assert created["mobile"] == "9876543210"
        assert created["phone"] == "9876543210"
        # Original buyer input preserved verbatim under the new raw_phone field.
        assert created["raw_phone"] == "+91 98765 43210"

    def test_unparseable_phone_does_not_persist_fake_mobile(self, monkeypatch):
        from api.services import online_order_mapper

        created = {}

        class _Repo:
            def find_by_mobile(self, m):
                return None

            def find_by_email(self, e):
                return None

            def create(self, doc):
                created.update(doc)
                return doc

        import api.dependencies as deps

        monkeypatch.setattr(deps, "get_customer_repository", lambda: _Repo())

        # Junk phone + a usable email -> still creates (keyed by email), but the
        # normalized mobile is '' (no fake un-dedupable number) and raw is kept.
        buyer = {"name": "Bad Phone", "phone": "not-a-number", "email": "e@x.com"}
        online_order_mapper._match_or_create_customer(
            db=object(), buyer=buyer, store_id="BV-ONLINE-01"
        )
        assert created["mobile"] == ""
        assert created["raw_phone"] == "not-a-number"


# ===========================================================================
# STEP 3 -- CustomerUpdate reuses the create validators
# ===========================================================================


class TestCustomerUpdateValidation:
    def test_mobile_normalized(self):
        assert CustomerUpdate(mobile="+91 98765 43210").mobile == "9876543210"

    def test_bad_mobile_rejected(self):
        with pytest.raises(ValidationError):
            CustomerUpdate(mobile="12345")

    def test_zero_leading_mobile_rejected(self):
        with pytest.raises(ValidationError):
            CustomerUpdate(mobile="0123456789")

    def test_bad_email_rejected(self):
        with pytest.raises(ValidationError):
            CustomerUpdate(email="notanemail")

    def test_good_email_ok(self):
        assert CustomerUpdate(email="a@b.com").email == "a@b.com"

    def test_bad_gstin_rejected(self):
        with pytest.raises(ValidationError):
            CustomerUpdate(gstin="27aapfu0939f1zv")

    def test_good_gstin_ok(self):
        assert CustomerUpdate(gstin="27AAPFU0939F1ZV").gstin == "27AAPFU0939F1ZV"

    def test_bad_customer_type_rejected(self):
        with pytest.raises(ValidationError):
            CustomerUpdate(customer_type="B2X")

    def test_future_dob_rejected(self):
        from datetime import date, timedelta

        with pytest.raises(ValidationError):
            CustomerUpdate(dob=date.today() + timedelta(days=1))

    def test_omitted_fields_pass(self):
        # A partial edit that omits a field is never rejected by the new rules.
        u = CustomerUpdate(name="New Name")
        assert u.model_dump(exclude_unset=True) == {"name": "New Name"}


# ===========================================================================
# STEP 3 -- update_customer endpoint guards
# ===========================================================================


class TestUpdateCustomerEndpoint:
    def _seed(self, monkeypatch, extra=None):
        base = {
            "customer_id": "C1",
            "name": "Alice",
            "mobile": "9876543210",
            "phone": "9876543210",
            "customer_type": "B2C",
            "patients": [],
        }
        if extra:
            base.update(extra)
        return _patch_repo(monkeypatch, [base])

    def test_b2b_without_gstin_blocked(self, monkeypatch):
        self._seed(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                update_customer(
                    customer_id="C1",
                    customer=CustomerUpdate(customer_type="B2B"),
                    current_user=_manager(),
                )
            )
        assert exc.value.status_code == 422
        assert "GSTIN" in exc.value.detail

    def test_b2b_with_gstin_ok(self, monkeypatch):
        repo = self._seed(monkeypatch)
        res = asyncio.run(
            update_customer(
                customer_id="C1",
                customer=CustomerUpdate(
                    customer_type="B2B", gstin="27AAPFU0939F1ZV"
                ),
                current_user=_manager(),
            )
        )
        assert res["customer_id"] == "C1"
        assert repo.docs["C1"]["customer_type"] == "B2B"

    def test_dup_mobile_other_customer_409(self, monkeypatch):
        # Two customers; editing C1's mobile to C2's number must 409.
        repo = _patch_repo(
            monkeypatch,
            [
                {"customer_id": "C1", "name": "Alice", "mobile": "9876543210",
                 "phone": "9876543210", "customer_type": "B2C", "patients": []},
                {"customer_id": "C2", "name": "Bob", "mobile": "9000000001",
                 "phone": "9000000001", "customer_type": "B2C", "patients": []},
            ],
        )
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                update_customer(
                    customer_id="C1",
                    customer=CustomerUpdate(mobile="9000000001"),
                    current_user=_manager(),
                )
            )
        assert exc.value.status_code == 409
        # Nothing was written.
        assert repo.updates == []

    def test_same_customer_mobile_resave_allowed(self, monkeypatch):
        # Re-saving the SAME customer's own number is NOT a dup collision.
        repo = self._seed(monkeypatch)
        res = asyncio.run(
            update_customer(
                customer_id="C1",
                customer=CustomerUpdate(mobile="9876543210", name="Alice II"),
                current_user=_manager(),
            )
        )
        assert res["customer_id"] == "C1"
        assert repo.docs["C1"]["name"] == "Alice II"

    def test_cashier_credit_limit_403(self, monkeypatch):
        repo = self._seed(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                update_customer(
                    customer_id="C1",
                    customer=CustomerUpdate(credit_limit=50000),
                    current_user=_cashier(),
                )
            )
        assert exc.value.status_code == 403
        # The khata limit was NOT written.
        assert repo.updates == []

    def test_manager_credit_limit_ok(self, monkeypatch):
        repo = self._seed(monkeypatch)
        res = asyncio.run(
            update_customer(
                customer_id="C1",
                customer=CustomerUpdate(credit_limit=50000),
                current_user=_manager(),
            )
        )
        assert res["customer_id"] == "C1"
        assert repo.docs["C1"]["credit_limit"] == 50000

    def test_cashier_non_credit_edit_still_allowed(self, monkeypatch):
        # The gate is ONLY on credit_limit; a cashier can still edit a name.
        repo = self._seed(monkeypatch)
        res = asyncio.run(
            update_customer(
                customer_id="C1",
                customer=CustomerUpdate(name="Alice Renamed"),
                current_user=_cashier(),
            )
        )
        assert res["customer_id"] == "C1"
        assert repo.docs["C1"]["name"] == "Alice Renamed"


# ===========================================================================
# STEP 3 -- add_patient family-member dedup
# ===========================================================================


class TestAddPatientDedup:
    def _seed(self, monkeypatch):
        return _patch_repo(
            monkeypatch,
            [
                {
                    "customer_id": "C1",
                    "name": "Alice",
                    "mobile": "9876543210",
                    "phone": "9876543210",
                    "customer_type": "B2C",
                    "patients": [
                        {
                            "patient_id": "P-existing",
                            "name": "Bittu",
                            "mobile": "9000000002",
                            "relation": "Son",
                        }
                    ],
                }
            ],
        )

    def test_duplicate_patient_is_deduped(self, monkeypatch):
        repo = self._seed(monkeypatch)
        res = asyncio.run(
            add_patient(
                customer_id="C1",
                patient=PatientCreate(name="Bittu", mobile="9000000002"),
                current_user=_manager(),
            )
        )
        # Returns the EXISTING patient, flagged deduped; no new push happened.
        assert res["patient_id"] == "P-existing"
        assert res.get("deduped") is True
        assert repo.added_patients == []

    def test_new_patient_is_added(self, monkeypatch):
        repo = self._seed(monkeypatch)
        res = asyncio.run(
            add_patient(
                customer_id="C1",
                patient=PatientCreate(name="Chiku", mobile="9000000003"),
                current_user=_manager(),
            )
        )
        assert res["name"] == "Chiku"
        assert res.get("deduped") is None
        assert len(repo.added_patients) == 1

    def test_same_name_different_mobile_is_not_dedup(self, monkeypatch):
        # Dedup key is (name, mobile): same name + different number -> NEW patient.
        repo = self._seed(monkeypatch)
        res = asyncio.run(
            add_patient(
                customer_id="C1",
                patient=PatientCreate(name="Bittu", mobile="9000000099"),
                current_user=_manager(),
            )
        )
        assert res.get("deduped") is None
        assert len(repo.added_patients) == 1


class TestCrossStoreObjectScope:
    """Object-level store scope (cross-store IDOR fix): a store-level user must
    not read/edit a customer belonging to a store outside their reach. Seed
    customers default to BV-TEST-01 (via _patch_repo); act from BV-OTHER-99."""

    def _seed_other_store(self, monkeypatch):
        return _patch_repo(
            monkeypatch,
            [{"customer_id": "C1", "name": "Alice", "mobile": "9876543210",
              "phone": "9876543210", "customer_type": "B2C", "patients": []}],
        )

    def test_foreign_store_read_is_404(self, monkeypatch):
        self._seed_other_store(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_customer(customer_id="C1", current_user=_manager(store="BV-OTHER-99")))
        assert exc.value.status_code == 404  # not 403 -> don't confirm it exists

    def test_foreign_store_write_is_403(self, monkeypatch):
        self._seed_other_store(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                update_customer(
                    customer_id="C1",
                    customer=CustomerUpdate(name="Hacked"),
                    current_user=_cashier(store="BV-OTHER-99"),
                )
            )
        assert exc.value.status_code == 403

    def test_same_store_read_ok(self, monkeypatch):
        self._seed_other_store(monkeypatch)
        res = asyncio.run(get_customer(customer_id="C1", current_user=_manager()))
        assert res["customer_id"] == "C1"

    def test_superadmin_cross_store_read_ok(self, monkeypatch):
        self._seed_other_store(monkeypatch)
        admin = {"user_id": "u-sa", "username": "sa", "roles": ["SUPERADMIN"]}
        res = asyncio.run(get_customer(customer_id="C1", current_user=admin))
        assert res["customer_id"] == "C1"
