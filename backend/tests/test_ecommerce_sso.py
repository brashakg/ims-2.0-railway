"""Unit tests for services/ecommerce_sso.py (IMS -> online-store SSO token)."""

import base64
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


# ----------------------------------------------------------------------------
# SSO token-contract HARDENING (#263/#271 locked design)
# ----------------------------------------------------------------------------
# The exchange token is consumed by the BVI verifier (in ecommerce/, NOT in
# this repo). These tests lock in the wire contract the BVI side MUST enforce
# by simulating exactly what a correct verifier does -- pin alg=RS256, verify
# the signature against the PUBLIC key, and require aud="bvi" / iss="ims" --
# and proving that every tampering attempt is REJECTED. They are the
# IMS-side guarantee that, if the mint or the verifier ever drifts (e.g. a
# verifier added HS256 to its allowed list, or the mint stopped scoping the
# audience), CI goes red.


def _rsa_keypair():
    """Return (private_pem, public_pem). Skips if `cryptography` is absent."""
    import pytest

    pytest.importorskip("cryptography")  # RS256 needs it (present in CI/prod)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

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
    return priv, pub


def _verify_like_bvi(token: str, public_pem: str) -> dict:
    """Mirror the BVI verifier's contract: RS256 only, audience 'bvi',
    issuer 'ims'. Any failure raises (PyJWT exception). This is the gate the
    online store applies before trusting the handoff."""
    import jwt

    return jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],          # alg pinned -> blocks HS256 confusion
        audience="bvi",
        issuer="ims",
    )


def test_minted_token_is_rs256_with_correct_claims():
    """The IMS mint must produce an RS256 JWT carrying the locked claim set:
    aud=bvi, iss=ims, scope=ecommerce, a jti, and a SHORT (90s) TTL."""
    import jwt

    priv, pub = _rsa_keypair()
    os.environ["ECOMMERCE_SSO_PRIVATE_KEY"] = priv  # mint reads key from env
    try:
        tok = sso.mint_sso_token(
            {"user_id": "u1", "email": "a@b.com", "username": "Av",
             "roles": ["SUPERADMIN"]}
        )
        assert tok
        # Header pins RS256 (asymmetric) -- never HS256.
        assert jwt.get_unverified_header(tok)["alg"] == "RS256"
        claims = _verify_like_bvi(tok, pub)
        assert claims["aud"] == "bvi"
        assert claims["iss"] == "ims"
        assert claims["scope"] == "ecommerce"
        assert claims["role"] == "ADMIN"
        assert claims["jti"]
        # Short-lived: <= the 90s default, and strictly in the future.
        assert 0 < claims["exp"] - claims["iat"] <= 90
    finally:
        os.environ.pop("ECOMMERCE_SSO_PRIVATE_KEY", None)


def _hs256_forge(claims: dict, secret: str) -> str:
    """Hand-craft an HS256 JWT, signing with `secret` as the HMAC key.

    We assemble it at the byte level rather than via PyJWT's high-level
    `jwt.encode(..., algorithm="HS256")` because modern PyJWT REFUSES to use a
    PEM key as an HMAC secret (it raises InvalidKeyError -- itself an
    encode-side defense). The alg-confusion threat assumes an attacker without
    that guardrail, so we model the raw wire bytes an attacker would send and
    prove the VERIFIER (not the encoder) is what rejects it."""
    import hashlib
    import hmac as _hmac
    import json as _json

    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = _b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(_json.dumps(claims).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    sig = _hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(sig)}"


def test_algorithm_confusion_hs256_is_rejected():
    """The classic alg-confusion attack: an attacker forges a token signed
    HS256 using the server's PUBLIC key (which is, well, public) as the HMAC
    secret. A verifier that PINS RS256 must reject it -- proving BVI must never
    accept HS256 on this token."""
    import jwt
    import pytest

    _priv, pub = _rsa_keypair()
    claims = sso.build_claims(
        {"user_id": "u1", "email": "a@b.com", "username": "Av"}, "ADMIN"
    )
    forged = _hs256_forge(claims, pub)  # public key abused as HMAC secret
    # RS256-pinned verifier refuses the HS256 alg outright.
    with pytest.raises(jwt.InvalidAlgorithmError):
        _verify_like_bvi(forged, pub)


def test_expired_token_is_rejected():
    """A token whose exp is in the past must be refused."""
    import jwt
    import pytest

    priv, pub = _rsa_keypair()
    claims = sso.build_claims(
        {"user_id": "u1", "email": "a@b.com", "username": "Av"}, "ADMIN"
    )
    now = int(time.time())
    claims["iat"] = now - 600
    claims["exp"] = now - 300  # expired 5 min ago
    tok = jwt.encode(claims, priv, algorithm="RS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        _verify_like_bvi(tok, pub)


def test_wrong_audience_is_rejected():
    """A token minted for a different audience (aud != 'bvi') must be refused
    so a token meant for some other service can't be replayed at BVI."""
    import jwt
    import pytest

    priv, pub = _rsa_keypair()
    claims = sso.build_claims(
        {"user_id": "u1", "email": "a@b.com", "username": "Av"}, "ADMIN"
    )
    claims["aud"] = "someone-else"
    tok = jwt.encode(claims, priv, algorithm="RS256")
    with pytest.raises(jwt.InvalidAudienceError):
        _verify_like_bvi(tok, pub)


def test_wrong_issuer_is_rejected():
    """A token whose issuer isn't 'ims' must be refused -- BVI only trusts
    tokens minted by IMS."""
    import jwt
    import pytest

    priv, pub = _rsa_keypair()
    claims = sso.build_claims(
        {"user_id": "u1", "email": "a@b.com", "username": "Av"}, "ADMIN"
    )
    claims["iss"] = "evil"
    tok = jwt.encode(claims, priv, algorithm="RS256")
    with pytest.raises(jwt.InvalidIssuerError):
        _verify_like_bvi(tok, pub)


def test_tampered_signature_is_rejected():
    """A token signed by an UNRELATED private key (i.e. a tampered / forged
    signature) must fail verification against the real public key."""
    import jwt
    import pytest

    _priv_a, real_pub = _rsa_keypair()  # the legitimate keypair (verify w/ pub A)
    priv_b, _pub_b = _rsa_keypair()     # an unrelated attacker keypair
    claims = sso.build_claims(
        {"user_id": "u1", "email": "a@b.com", "username": "Av"}, "ADMIN"
    )
    # Sign with key B but verify against the legitimate public key A.
    forged = jwt.encode(claims, priv_b, algorithm="RS256")
    with pytest.raises(jwt.InvalidSignatureError):
        _verify_like_bvi(forged, real_pub)


def test_tampered_payload_byte_is_rejected():
    """Flipping a byte in the encoded payload invalidates the signature."""
    import jwt
    import pytest

    priv, pub = _rsa_keypair()
    tok = jwt.encode(
        sso.build_claims(
            {"user_id": "u1", "email": "a@b.com", "username": "Av"}, "ADMIN"
        ),
        priv,
        algorithm="RS256",
    )
    header, payload, sig = tok.split(".")
    # Corrupt the payload segment (swap a char) -> signature no longer matches.
    bad_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    tampered = f"{header}.{bad_payload}.{sig}"
    with pytest.raises(jwt.InvalidTokenError):
        _verify_like_bvi(tampered, pub)
