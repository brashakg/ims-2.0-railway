"""IMS 2.0 -- customer address representation sync.

Address is stored in two shapes that must never drift: the structured
`billing_address` dict (what the GST place-of-supply / invoice logic reads) and
the flat top-level `address`/`city`/`state`/`pincode` fields (what the Customers
detail + edit screen reads/writes). `_sync_address_representations` reconciles
them on every write so a created customer's address shows on the edit screen and
an edited address reaches the GST reader.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-key-for-address-sync")

from api.routers.customers import _sync_address_representations  # noqa: E402


def test_create_mirrors_structured_to_flat():
    # Create door path: only billing_address is set -> flat fields get mirrored
    # so the detail/edit screen (which reads flat) shows the address.
    data = {
        "name": "Asha",
        "billing_address": {
            "address": "12 MG Road",
            "city": "Ranchi",
            "state": "Jharkhand",
            "pincode": "834001",
        },
    }
    _sync_address_representations(data)
    assert data["address"] == "12 MG Road"
    assert data["city"] == "Ranchi"
    assert data["state"] == "Jharkhand"
    assert data["pincode"] == "834001"
    # Structured dict is preserved intact for the GST reader.
    assert data["billing_address"]["state"] == "Jharkhand"


def test_edit_flat_folds_into_structured_preserving_existing():
    # Edit door path: the edit form sends only a flat `address` blob. It must be
    # folded into billing_address WITHOUT wiping the existing city/state/pincode,
    # so GST place-of-supply (reads billing_address.state) stays correct.
    existing = {
        "billing_address": {
            "address": "Old Lane",
            "city": "Ranchi",
            "state": "Jharkhand",
            "pincode": "834001",
        }
    }
    update = {"address": "New Colony, Doranda"}
    _sync_address_representations(update, existing)
    ba = update["billing_address"]
    assert ba["address"] == "New Colony, Doranda"  # updated
    assert ba["city"] == "Ranchi"  # preserved
    assert ba["state"] == "Jharkhand"  # preserved -> GST unaffected
    assert ba["pincode"] == "834001"  # preserved
    # Flat mirror is also written for the screen.
    assert update["address"] == "New Colony, Doranda"
    assert update["state"] == "Jharkhand"


def test_edit_without_address_is_noop():
    # An unrelated edit (e.g. marketing opt-out) must not stamp an empty address.
    existing = {"billing_address": {"address": "X", "state": "Bihar"}}
    update = {"marketing_consent": False}
    _sync_address_representations(update, existing)
    assert "billing_address" not in update
    assert "address" not in update
    assert update == {"marketing_consent": False}


def test_flat_fields_win_over_supplied_structured():
    # If both are sent, the explicit flat fields are the freshest intent.
    data = {
        "billing_address": {"address": "From Dict", "state": "Goa"},
        "address": "From Flat",
    }
    _sync_address_representations(data)
    assert data["billing_address"]["address"] == "From Flat"
    assert data["billing_address"]["state"] == "Goa"  # not overwritten
    assert data["address"] == "From Flat"


def test_no_address_anywhere_is_noop():
    data = {"name": "NoAddr"}
    _sync_address_representations(data, {"name": "NoAddr"})
    assert data == {"name": "NoAddr"}


def test_none_values_do_not_clobber_existing():
    # A billing_address dict carrying None values must not erase existing data.
    existing = {"billing_address": {"address": "Keep St", "state": "Kerala"}}
    update = {"billing_address": {"address": None, "state": None, "city": "Kochi"}}
    _sync_address_representations(update, existing)
    ba = update["billing_address"]
    assert ba["address"] == "Keep St"  # None did not clobber
    assert ba["state"] == "Kerala"  # None did not clobber
    assert ba["city"] == "Kochi"  # new value applied
