"""
IMS 2.0 — DPDP data-consent capture + editable consent text
============================================================
DPDP Act 2023: record that a customer agreed to us storing their data, provably
(who/when/which text version). Distinct from marketing_consent (promo messages).
The consent WORDING is editable by ADMIN under Marketing; the version is stamped
onto each customer's consent so the agreement traces to the exact text shown.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-dpdp")


def test_customer_create_accepts_consent_fields():
    from api.routers.customers import CustomerCreate

    c = CustomerCreate(name="Asha", mobile="9876543210",
                       data_consent=True, data_consent_text_version="3")
    assert c.data_consent is True
    assert c.data_consent_text_version == "3"


def test_consent_defaults_true_when_omitted():
    from api.routers.customers import CustomerCreate

    c = CustomerCreate(name="Asha", mobile="9876543210")
    assert c.data_consent is True  # operator ticks at the counter; default on
    assert c.data_consent_text_version is None


def test_consent_can_be_declined():
    from api.routers.customers import CustomerCreate

    c = CustomerCreate(name="Asha", mobile="9876543210", data_consent=False)
    assert c.data_consent is False


def test_consent_text_update_model_bounds():
    from pydantic import ValidationError

    from api.routers.marketing import ConsentTextUpdate

    ConsentTextUpdate(text="A reasonable consent sentence the customer reads.")
    with pytest.raises(ValidationError):
        ConsentTextUpdate(text="short")  # < 10 chars


def test_consent_text_endpoints_are_correctly_gated():
    """GET is open to any authenticated user (the create form needs it); PUT is
    ADMIN-gated (editing legal wording is privileged)."""
    import inspect

    import api.routers.marketing as mk

    get_src = inspect.getsource(mk.get_consent_text)
    put_src = inspect.getsource(mk.update_consent_text)
    assert "get_current_user" in get_src
    assert 'require_roles("ADMIN")' in put_src


def test_default_consent_text_is_sensible():
    from api.routers.marketing import _DEFAULT_CONSENT_TEXT

    t = _DEFAULT_CONSENT_TEXT.lower()
    assert "store" in t and "consent" in t and "withdraw" in t
