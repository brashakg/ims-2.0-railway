"""
IMS 2.0 — CustomerUpdate accepts phone/address (edit-customer persistence)
=========================================================================
Editing a customer "didn't connect": the edit form sends {name, phone,
email, address} but CustomerUpdate had no `phone` and no `address` field, so
model_dump(exclude_unset=True) dropped them — the PUT returned 200 yet phone/
address never persisted (the UI's optimistic change reverted on reload).
CustomerUpdate now declares phone/mobile/address; the endpoint mirrors
phone<->mobile so every reader (TechCherry docs use `phone`, native docs use
`mobile`) sees the update.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.customers import CustomerUpdate  # noqa: E402


class TestCustomerUpdateFields:
    def test_accepts_phone_address_mobile(self):
        u = CustomerUpdate(phone="9876543210", address="12 MG Road", mobile="9876543210")
        dumped = u.model_dump(exclude_unset=True)
        assert dumped["phone"] == "9876543210"
        assert dumped["address"] == "12 MG Road"
        assert dumped["mobile"] == "9876543210"

    def test_exclude_unset_keeps_payload_minimal(self):
        # Only fields the operator actually set are sent to the DB update.
        u = CustomerUpdate(name="New Name")
        dumped = u.model_dump(exclude_unset=True)
        assert dumped == {"name": "New Name"}
        assert "phone" not in dumped
        assert "address" not in dumped

    def test_address_still_supports_billing_address(self):
        # billing_address (the legacy nested shape) is still accepted alongside
        # the new flat `address`.
        u = CustomerUpdate(billing_address={"address": "x", "city": "Pune"})
        dumped = u.model_dump(exclude_unset=True)
        assert dumped["billing_address"]["city"] == "Pune"
