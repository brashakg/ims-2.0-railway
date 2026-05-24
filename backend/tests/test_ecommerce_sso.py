"""Unit tests for services/ecommerce_sso.py (IMS -> online-store SSO token)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import ecommerce_sso as sso  # noqa: E402


def test_role_map_deny_by_default():
    assert sso.mapped_bvi_role(["SUPERADMIN"]) == "ADMIN"
    assert sso.mapped_bvi_role(["ADMIN"]) == "ADMIN"
    assert sso.mapped_bvi_role(["CATALOG_MANAGER"]) == "CATALOG_MANAGER"
    assert sso.mapped_bvi_role(["ADMIN", "SALES_STAFF"]) == "ADMIN"
    # Everyone else is denied.
    for r in ["SALES_CASHIER", "SALES_STAFF", "CASHIER", "OPTOMETRIST",
              "WORKSHOP_STAFF", "ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER"]:
        assert sso.mapped_bvi_role([r]) is None
    assert sso.mapped_bvi_role([]) is None


def test_build_claims_shape():
    c = sso.build_claims({"user_id": "u1", "email": "A@B.com", "username": "Av"}, "ADMIN", 300)
    assert c["aud"] == "bvi"
    assert c["iss"] == "ims"
    assert c["scope"] == "ecommerce"
    assert c["role"] == "ADMIN"
    assert c["email"] == "a@b.com"   # normalised to lowercase
    assert c["sub"] == "u1"
    assert c["exp"] > c["iat"]
    assert c["jti"]


def test_mint_none_without_key(monkeypatch):
    monkeypatch.delenv("ECOMMERCE_SSO_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("ECOMMERCE_SSO_PRIVATE_KEY_B64", raising=False)
    assert sso.sso_configured() is False
    assert sso.mint_sso_token({"user_id": "u1", "email": "a@b.com", "roles": ["ADMIN"]}) is None


def test_mint_and_verify_roundtrip(monkeypatch):
    import pytest

    pytest.importorskip("cryptography")  # RS256 needs it (present in CI/prod)
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("ECOMMERCE_SSO_PRIVATE_KEY", priv)
    assert sso.sso_configured() is True

    # Denied role yields None even with a key configured.
    assert sso.mint_sso_token({"user_id": "u", "email": "a@b.com", "roles": ["CASHIER"]}) is None

    tok = sso.mint_sso_token({"user_id": "u1", "email": "a@b.com", "username": "Av", "roles": ["SUPERADMIN"]})
    assert tok
    import jwt

    decoded = jwt.decode(tok, pub, algorithms=["RS256"], audience="bvi", issuer="ims")
    assert decoded["role"] == "ADMIN"
    assert decoded["email"] == "a@b.com"
    assert decoded["scope"] == "ecommerce"
