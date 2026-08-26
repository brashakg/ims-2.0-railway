"""
IMS 2.0 - Vendor master: GSTIN is the source of truth for the vendor's state
============================================================================
Owner report (2026-08-26): *"make sure vendor gst calculations are done
according to state using gst no"*.

Before this: the vendor form asked the user to TYPE a state next to a GSTIN,
and the router validated the GSTIN with a local regex (`_GSTIN_RE`) that
checked shape only -- no state-code list, no check digit. So

  * a mistyped GSTIN was accepted silently, and
  * `state` could disagree with the state the GSTIN actually encodes,

which flips a purchase between IGST and CGST+SGST. The purchase engine
(`api/services/purchase_invoice_engine`) derives the state from the GSTIN, so a
typed state that disagreed produced two different answers for one vendor.

These tests pin: one validator (`org_validation`), the state DERIVED from the
GSTIN, and the vendor master's stored state code agreeing -- exactly -- with
what the purchase GST engine derives from the same number.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.vendors import VendorCreate  # noqa: E402
from api.services import org_validation as ov  # noqa: E402
from api.services.purchase_invoice_engine import (  # noqa: E402
    determine_place_of_supply,
    state_code_of,
)

# Real, published GSTINs (checksum-valid). 20 = Jharkhand, 27 = Maharashtra --
# the two states this business actually operates in.
JH_GSTIN = "20AABCU9603R1Z1"
MH_GSTIN = "27AAPFU0939F1ZV"
# Same number, last character mistyped -> check digit no longer matches.
MH_GSTIN_TYPO = "27AAPFU0939F1ZX"
# Shape-valid but "88" is not an Indian GST state code.
BAD_STATE_GSTIN = "88AABCU9603R1ZF"


def _body(**kw):
    body = {
        "legal_name": "Universal Optics Pvt Ltd",
        "trade_name": "Universal Optics",
        "gstin_status": "REGISTERED",
        "address": "12 Main Road",
        "city": "Ranchi",
        # Deliberately WRONG / absent so the tests prove the state is derived
        # from the GSTIN, not taken from what the user typed.
        "state": "Maharashtra",
        "mobile": "9000000000",
    }
    body.update(kw)
    return body


def _msg(exc) -> str:
    return str(exc).lower()


# ---------------------------------------------------------------------------
# An invalid GSTIN is refused, and the message names the problem
# ---------------------------------------------------------------------------


class TestGstinIsRefusedWithAReason:
    def test_a_single_character_typo_is_refused_for_the_check_digit(self):
        with pytest.raises(ValueError) as exc:
            VendorCreate(**_body(gstin=MH_GSTIN_TYPO))
        # Must name the CHECK DIGIT -- otherwise the user is told "invalid"
        # about a number that looks perfectly well-formed to them.
        assert "check" in _msg(exc)

    def test_an_unknown_state_code_is_refused_and_named(self):
        with pytest.raises(ValueError) as exc:
            VendorCreate(**_body(gstin=BAD_STATE_GSTIN))
        m = _msg(exc)
        assert "state" in m
        assert "88" in m

    def test_a_short_gstin_is_refused_for_its_length(self):
        with pytest.raises(ValueError) as exc:
            VendorCreate(**_body(gstin="20AABCU9603R1Z"))
        assert "15" in _msg(exc)

    def test_a_valid_gstin_is_accepted_and_uppercased(self):
        v = VendorCreate(**_body(gstin=JH_GSTIN.lower()))
        assert v.gstin == JH_GSTIN

    def test_no_gstin_is_still_fine_for_an_unregistered_vendor(self):
        v = VendorCreate(**_body(gstin=None, gstin_status="UNREGISTERED"))
        assert v.gstin is None


# ---------------------------------------------------------------------------
# A valid GSTIN yields the right state WITHOUT the user choosing it
# ---------------------------------------------------------------------------


class TestStateIsDerivedNotChosen:
    def test_gstin_overrides_the_typed_state(self):
        from api.routers.vendors import derive_vendor_state

        # User typed "Maharashtra"; the GSTIN says Jharkhand. GSTIN wins.
        code, name = derive_vendor_state(JH_GSTIN, "Maharashtra")
        assert code == "20"
        assert name == "Jharkhand"

    def test_maharashtra_gstin_yields_maharashtra(self):
        from api.routers.vendors import derive_vendor_state

        code, name = derive_vendor_state(MH_GSTIN, "")
        assert code == "27"
        assert name == "Maharashtra"

    def test_without_a_gstin_the_typed_state_is_normalised(self):
        from api.routers.vendors import derive_vendor_state

        # An unregistered vendor has no GSTIN; "MH" / "Maharashtra" still
        # resolve to the canonical GST code so downstream code has one shape.
        assert derive_vendor_state(None, "MH") == ("27", "Maharashtra")
        assert derive_vendor_state(None, "Jharkhand") == ("20", "Jharkhand")

    def test_an_unresolvable_typed_state_is_kept_but_uncoded(self):
        from api.routers.vendors import derive_vendor_state

        code, name = derive_vendor_state(None, "Narnia")
        assert code is None
        assert name == "Narnia"


# ---------------------------------------------------------------------------
# The vendor master and the purchase GST engine must not disagree
# ---------------------------------------------------------------------------


class TestAgreesWithThePurchaseGstEngine:
    @pytest.mark.parametrize("gstin,expected", [(JH_GSTIN, "20"), (MH_GSTIN, "27")])
    def test_stored_state_code_is_the_one_the_gst_engine_derives(self, gstin, expected):
        from api.routers.vendors import derive_vendor_state

        code, _ = derive_vendor_state(gstin, "")
        assert code == expected
        # The canonical purchase-GST helper must read the SAME code off the
        # same number. If these two ever diverge, one purchase is taxed twice
        # differently depending on which code path priced it.
        assert state_code_of(gstin) == code

    def test_same_state_vendor_is_intra_state(self):
        # Buying entity in Jharkhand, vendor in Jharkhand -> CGST + SGST.
        pos, interstate = determine_place_of_supply(JH_GSTIN, "20AABCB0001Q1ZZ"[:2])
        assert interstate is False

    def test_other_state_vendor_is_inter_state(self):
        # Buying entity in Jharkhand, vendor in Maharashtra -> IGST.
        pos, interstate = determine_place_of_supply(MH_GSTIN, "20")
        assert pos == "20"
        assert interstate is True

    def test_the_validator_is_the_shared_one(self):
        # Not a second parser: the router's rule is org_validation's rule.
        assert ov.validate_gstin(JH_GSTIN) is True
        assert ov.validate_gstin(MH_GSTIN_TYPO) is False
        assert ov.validate_gstin(BAD_STATE_GSTIN) is False


# ---------------------------------------------------------------------------
# The Edit Vendor save must round-trip -- including the fields the Add form
# always collected and the router used to throw away.
# ---------------------------------------------------------------------------


@pytest.fixture
def vendor_client(monkeypatch):
    """Vendors router over a fake in-memory vendor repository."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tests.test_vendor_portal import FakeDB
    from api import dependencies as deps_module
    from api.routers import vendors as vendors_module
    from api.routers.auth import get_current_user
    from database.repositories.vendor_repository import VendorRepository

    fake_db = FakeDB()
    repo = VendorRepository(fake_db.get_collection("vendors"))
    for mod in (deps_module, vendors_module):
        monkeypatch.setattr(mod, "get_vendor_repository", lambda: repo, raising=False)

    app = FastAPI()
    app.include_router(vendors_module.router, prefix="/api/v1/vendors")

    async def _u():
        return {
            "user_id": "u1",
            "username": "t",
            "roles": ["SUPERADMIN"],
            "store_ids": ["S1"],
            "active_store_id": "S1",
        }

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), repo


class TestEditVendorRoundTrips:
    def test_create_keeps_the_contact_code_and_credit_limit(self, vendor_client):
        client, repo = vendor_client
        r = client.post(
            "/api/v1/vendors",
            json=_body(
                gstin=JH_GSTIN,
                vendor_code="sup004",
                contact_person="Rakesh Sinha",
                credit_limit=250000,
            ),
        )
        assert r.status_code == 201, r.text
        doc = repo.find_by_id(r.json()["vendor_id"])
        assert doc["contact_person"] == "Rakesh Sinha"
        assert doc["vendor_code"] == "SUP004"
        assert doc["credit_limit"] == 250000
        # State taken from the GSTIN, not the "Maharashtra" the body typed.
        assert (doc["state"], doc["state_code"]) == ("Jharkhand", "20")

    def test_edit_saves_and_reads_back(self, vendor_client):
        client, repo = vendor_client
        vid = client.post("/api/v1/vendors", json=_body(gstin=JH_GSTIN)).json()[
            "vendor_id"
        ]

        r = client.put(
            f"/api/v1/vendors/{vid}",
            json={
                "trade_name": "Universal Optics (Ranchi)",
                "contact_person": "Sunita Devi",
                "mobile": "9800000001",
                "credit_days": 45,
                "credit_limit": 500000,
            },
        )
        assert r.status_code == 200, r.text

        got = client.get(f"/api/v1/vendors/{vid}").json()
        assert got["trade_name"] == "Universal Optics (Ranchi)"
        assert got["contact_person"] == "Sunita Devi"
        assert got["mobile"] == "9800000001"
        assert got["credit_days"] == 45
        assert got["credit_limit"] == 500000

    def test_correcting_the_gstin_moves_the_state_with_it(self, vendor_client):
        client, repo = vendor_client
        vid = client.post("/api/v1/vendors", json=_body(gstin=JH_GSTIN)).json()[
            "vendor_id"
        ]
        assert repo.find_by_id(vid)["state_code"] == "20"

        r = client.put(f"/api/v1/vendors/{vid}", json={"gstin": MH_GSTIN})
        assert r.status_code == 200, r.text
        doc = repo.find_by_id(vid)
        assert doc["gstin"] == MH_GSTIN
        assert (doc["state"], doc["state_code"]) == ("Maharashtra", "27")

    def test_editing_an_unrelated_field_does_not_disturb_the_state(self, vendor_client):
        client, repo = vendor_client
        vid = client.post("/api/v1/vendors", json=_body(gstin=MH_GSTIN)).json()[
            "vendor_id"
        ]
        assert client.put(f"/api/v1/vendors/{vid}", json={"city": "Pune"}).status_code == 200
        doc = repo.find_by_id(vid)
        assert doc["city"] == "Pune"
        assert (doc["state"], doc["state_code"]) == ("Maharashtra", "27")

    def test_edit_refuses_a_mistyped_gstin(self, vendor_client):
        client, repo = vendor_client
        vid = client.post("/api/v1/vendors", json=_body(gstin=MH_GSTIN)).json()[
            "vendor_id"
        ]
        r = client.put(f"/api/v1/vendors/{vid}", json={"gstin": MH_GSTIN_TYPO})
        assert r.status_code == 422, r.text
        assert "check" in r.text.lower()
        # ...and the stored vendor is untouched.
        assert repo.find_by_id(vid)["gstin"] == MH_GSTIN

    def test_edit_refuses_a_gstin_already_used_by_another_vendor(self, vendor_client):
        client, repo = vendor_client
        a = client.post("/api/v1/vendors", json=_body(gstin=JH_GSTIN)).json()["vendor_id"]
        b = client.post(
            "/api/v1/vendors", json=_body(gstin=MH_GSTIN, trade_name="Other")
        ).json()["vendor_id"]
        assert a != b

        r = client.put(f"/api/v1/vendors/{b}", json={"gstin": JH_GSTIN})
        assert r.status_code == 400, r.text
        assert repo.find_by_id(b)["gstin"] == MH_GSTIN
