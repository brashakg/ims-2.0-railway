"""Unit tests for services/ecommerce_sso.py (IMS -> online-store SSO token)."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import ecommerce_sso as sso  # noqa: E402


class _FakeDuplicateKeyError(Exception):
    """Mimic pymongo.errors.DuplicateKeyError for tests (matches class-name check)."""
    pass


_FakeDuplicateKeyError.__name__ = "DuplicateKeyError"


class _FakeJtiCollection:
    """In-memory fake of the sso_jti collection that emulates unique-_id."""

    def __init__(self):
        self._rows = {}

    def insert_one(self, doc):
        key = doc["_id"]
        if key in self._rows:
            raise _FakeDuplicateKeyError(f"E11000 duplicate key on _id={key}")
        self._rows[key] = doc

    def create_index(self, *args, **kwargs):
        # Mark that the call happened; real Mongo would do the work.
        return kwargs.get("name") or "fake_index"


class _FakeDb:
    """db[name] dispatch like a Mongo Database."""

    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _FakeJtiCollection()
        return self._colls[name]


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


# ----------------------------------------------------------------------------
# Council Branch C: jti single-use replay store + 90s TTL
# ----------------------------------------------------------------------------


def test_default_ttl_is_ninety_seconds():
    """C2: Council shortened exp 300s -> 90s. Pinning so regressions are loud."""
    assert sso._DEFAULT_TTL == 90  # pylint: disable=protected-access


def test_build_claims_uses_ninety_second_default():
    c = sso.build_claims({"user_id": "u1", "email": "a@b.com", "username": "Av"}, "ADMIN")
    assert c["exp"] - c["iat"] == 90


def test_claim_jti_first_call_succeeds():
    """First mint of a fresh jti must be accepted."""
    db = _FakeDb()
    assert sso.claim_jti(db, "jti-abc", int(time.time()) + 90) is True


def test_claim_jti_replay_returns_false():
    """Re-claiming the SAME jti within its TTL must be refused."""
    db = _FakeDb()
    exp = int(time.time()) + 90
    assert sso.claim_jti(db, "jti-replay", exp) is True
    # Second claim of the same jti -- duplicate-key path.
    assert sso.claim_jti(db, "jti-replay", exp) is False


def test_claim_jti_fail_soft_on_no_db():
    """If the DB is None, fail-soft to True so SSO is not blocked.
    BVI's verifier still enforces aud/iss/scope/exp on the wire."""
    assert sso.claim_jti(None, "jti-x", int(time.time()) + 90) is True


def test_claim_jti_fail_soft_on_unexpected_error(monkeypatch):
    """Non-DuplicateKeyError on insert -> fail-soft True (defence-in-depth)."""

    class _BrokenColl:
        def insert_one(self, doc):
            raise RuntimeError("connection reset")

    class _BrokenDb:
        def __getitem__(self, name):
            return _BrokenColl()

    assert sso.claim_jti(_BrokenDb(), "jti-y", int(time.time()) + 90) is True


def test_ensure_jti_indexes_no_db():
    """ensure_jti_indexes is also fail-soft when db is None."""
    assert sso.ensure_jti_indexes(None) is False


def test_ensure_jti_indexes_calls_create_index():
    """When a db is provided, ensure_jti_indexes should issue a TTL create_index call."""
    db = _FakeDb()
    assert sso.ensure_jti_indexes(db) is True
    # _FakeDb lazily made the sso_jti collection on the create_index access.
    assert "sso_jti" in db._colls  # pylint: disable=protected-access
