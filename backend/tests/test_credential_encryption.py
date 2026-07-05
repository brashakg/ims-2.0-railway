"""
Tests for SEC-3: Fernet credential encryption in settings router.

Covers:
- Fernet round-trip (encrypt -> decrypt gives back plaintext)
- Legacy XOR "enc:" values are still decryptable (back-compat)
- Plain / unencrypted values are passed through unchanged
- _encrypt_config / _decrypt_config walk nested dicts correctly
- Already-encrypted values are NOT double-encrypted on re-save
- Fail-soft: no-key environment raises RuntimeError (existing behaviour)
"""

import base64
import hashlib
import importlib
import os
import sys
import types
import pytest


# ---------------------------------------------------------------------------
# Helpers to build an isolated module environment for each test
# ---------------------------------------------------------------------------

def _import_settings(env: dict):
    """
    Import (or re-import) backend.api.routers.settings with the given env vars.
    Returns the module object so tests can call its private helpers.
    """
    # Ensure the backend package is importable from whichever directory the
    # test runner uses.
    backend_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    # Temporarily patch os.environ
    original = os.environ.copy()
    os.environ.clear()
    os.environ.update(env)
    try:
        # Force a fresh import each time so module-level code re-runs.
        mod_name = "api.routers.settings"
        sys.modules.pop(mod_name, None)
        # We also need auth and dependencies to be importable stubs.
        _stub_auth()
        _stub_dependencies()
        # BUG-155: settings DELEGATES its credential crypto to the shared
        # api.services.cred_crypto module, which derives its Fernet key from env
        # AT IMPORT. importlib.reload re-runs it IN PLACE under the patched env so
        # the test key takes effect (a plain sys.modules.pop is not enough -- the
        # parent package keeps the stale submodule attribute, so settings would
        # re-bind the old key).
        import api.services.cred_crypto as _cc
        importlib.reload(_cc)
        module = importlib.import_module(mod_name)
        return module
    finally:
        os.environ.clear()
        os.environ.update(original)


def _stub_auth():
    """Insert a minimal stub for api.routers.auth so the import doesn't fail."""
    stub = types.ModuleType("api.routers.auth")
    stub.get_current_user = lambda: None
    stub.hash_password = lambda p: p
    stub.verify_password = lambda p, h: True
    stub.require_roles = lambda *a, **kw: (lambda f: f)
    sys.modules.setdefault("api.routers.auth", stub)


def _stub_dependencies():
    """Insert a minimal stub for api.dependencies."""
    stub = types.ModuleType("api.dependencies")
    stub.get_audit_repository = lambda: None
    sys.modules.setdefault("api.dependencies", stub)
    sys.modules.setdefault("api", sys.modules.get("api", types.ModuleType("api")))
    sys.modules.setdefault("api.routers", sys.modules.get("api.routers", types.ModuleType("api.routers")))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _unpollute_cred_crypto():
    """This module reloads api.services.cred_crypto under a fixture key. Reload
    it again under the REAL env on teardown so later test files (policy engine,
    integrations, ...) don't encrypt/decrypt with a stale key."""
    yield
    import api.services.cred_crypto as _cc
    importlib.reload(_cc)
    sys.modules.pop("api.routers.settings", None)


@pytest.fixture(scope="module")
def settings_mod():
    """Return the settings module loaded with a known test key."""
    # Use a well-known test secret that is NOT empty.
    env = {
        "JWT_SECRET_KEY": "test-jwt-secret-key-for-unit-tests",
        "ENVIRONMENT": "test",
    }
    mod = _import_settings(env)
    return mod


@pytest.fixture(scope="module")
def cred_secret():
    return "test-jwt-secret-key-for-unit-tests"


# ---------------------------------------------------------------------------
# Round-trip: Fernet encrypt -> decrypt
# ---------------------------------------------------------------------------

def test_fernet_encrypt_produces_fernet_prefix(settings_mod):
    ct = settings_mod._encrypt_value("super-secret-api-key")
    assert ct.startswith("fernet:"), f"Expected 'fernet:' prefix, got: {ct[:20]}"


def test_fernet_round_trip(settings_mod):
    plaintext = "razorpay_secret_key_12345"
    ct = settings_mod._encrypt_value(plaintext)
    assert ct != plaintext
    recovered = settings_mod._decrypt_value(ct)
    assert recovered == plaintext


def test_fernet_round_trip_unicode(settings_mod):
    plaintext = "key-with-special-chars-!@#$%^&*()_+-="
    ct = settings_mod._encrypt_value(plaintext)
    assert settings_mod._decrypt_value(ct) == plaintext


def test_fernet_different_plaintexts_produce_different_ciphertexts(settings_mod):
    ct1 = settings_mod._encrypt_value("key-one")
    ct2 = settings_mod._encrypt_value("key-two")
    assert ct1 != ct2


def test_fernet_same_plaintext_produces_different_ciphertexts(settings_mod):
    # Fernet uses a random IV so two encryptions of the same value differ.
    ct1 = settings_mod._encrypt_value("same-key")
    ct2 = settings_mod._encrypt_value("same-key")
    assert ct1 != ct2
    # Both must decrypt correctly.
    assert settings_mod._decrypt_value(ct1) == "same-key"
    assert settings_mod._decrypt_value(ct2) == "same-key"


# ---------------------------------------------------------------------------
# Back-compat: legacy XOR "enc:" values still decrypt
# ---------------------------------------------------------------------------

def _make_legacy_xor(plaintext: str, secret: str) -> str:
    """Replicate the OLD XOR scheme to produce a test "enc:" value."""
    key = hashlib.sha256(secret.encode()).digest()
    encoded = plaintext.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(encoded))
    return "enc:" + base64.b64encode(xored).decode("ascii")


def test_legacy_xor_still_decrypts(settings_mod, cred_secret):
    legacy = _make_legacy_xor("shopify_api_key_old", cred_secret)
    assert legacy.startswith("enc:")
    recovered = settings_mod._decrypt_value(legacy)
    assert recovered == "shopify_api_key_old"


def test_legacy_xor_multiple_values(settings_mod, cred_secret):
    for value in ("key1", "a" * 64, "short", "with spaces and !@#"):
        legacy = _make_legacy_xor(value, cred_secret)
        assert settings_mod._decrypt_value(legacy) == value


# ---------------------------------------------------------------------------
# Passthrough: plain / unencrypted values
# ---------------------------------------------------------------------------

def test_decrypt_plain_value_passthrough(settings_mod):
    plain = "not-yet-encrypted"
    assert settings_mod._decrypt_value(plain) == plain


def test_decrypt_empty_string_passthrough(settings_mod):
    assert settings_mod._decrypt_value("") == ""


# ---------------------------------------------------------------------------
# _encrypt_config / _decrypt_config: nested dict handling
# ---------------------------------------------------------------------------

def test_encrypt_config_encrypts_sensitive_fields(settings_mod):
    cfg = {
        "api_key": "my-api-key",
        "name": "Razorpay",
        "enabled": True,
        "nested": {
            "secret": "nested-secret",
            "label": "inner",
        },
    }
    enc = settings_mod._encrypt_config(cfg)
    assert enc["api_key"].startswith("fernet:")
    assert enc["name"] == "Razorpay"          # non-sensitive: unchanged
    assert enc["enabled"] is True             # non-string: unchanged
    assert enc["nested"]["secret"].startswith("fernet:")
    assert enc["nested"]["label"] == "inner"  # non-sensitive: unchanged


def test_encrypt_decrypt_config_round_trip(settings_mod):
    cfg = {
        "api_key": "test-api-key",
        "api_secret": "test-api-secret",
        "merchant_id": "mid-123",
        "credentials": {
            "token": "bearer-tok",
            "scope": "read",
        },
    }
    enc = settings_mod._encrypt_config(cfg)
    dec = settings_mod._decrypt_config(enc)
    assert dec["api_key"] == "test-api-key"
    assert dec["api_secret"] == "test-api-secret"
    assert dec["merchant_id"] == "mid-123"
    assert dec["credentials"]["token"] == "bearer-tok"
    assert dec["credentials"]["scope"] == "read"


def test_encrypt_config_does_not_double_encrypt_fernet(settings_mod):
    """Re-saving an already-encrypted value must not wrap it again."""
    cfg = {"api_key": "original-value"}
    enc1 = settings_mod._encrypt_config(cfg)
    # Simulate a re-save with the already-encrypted value still in the dict.
    enc2 = settings_mod._encrypt_config(enc1)
    # The value should still decrypt back to the original.
    dec = settings_mod._decrypt_config(enc2)
    assert dec["api_key"] == "original-value"


def test_encrypt_config_does_not_double_encrypt_legacy_xor(settings_mod, cred_secret):
    """Re-saving a legacy XOR value must not wrap it again."""
    legacy_ct = _make_legacy_xor("old-value", cred_secret)
    cfg = {"api_key": legacy_ct}
    enc = settings_mod._encrypt_config(cfg)
    # enc:... starts with "enc:" not "fernet:", so _encrypt_config must skip it.
    assert enc["api_key"] == legacy_ct
    # It must still decrypt correctly.
    dec = settings_mod._decrypt_config(enc)
    assert dec["api_key"] == "old-value"


def test_decrypt_config_with_mixed_legacy_and_new(settings_mod, cred_secret):
    """A config with both old XOR and new Fernet values must decrypt cleanly."""
    fernet_ct = settings_mod._encrypt_value("new-secret")
    legacy_ct = _make_legacy_xor("old-secret", cred_secret)
    cfg = {
        "api_key": fernet_ct,
        "api_secret": legacy_ct,
        "label": "mixed",
    }
    dec = settings_mod._decrypt_config(cfg)
    assert dec["api_key"] == "new-secret"
    assert dec["api_secret"] == "old-secret"
    assert dec["label"] == "mixed"


# ---------------------------------------------------------------------------
# Fail-soft: no-key env raises RuntimeError (preserves existing contract)
# ---------------------------------------------------------------------------

def test_no_key_raises_runtime_error():
    """Importing settings without any key env var must raise RuntimeError."""
    original = os.environ.copy()
    os.environ.clear()
    mod_name = "api.routers.settings"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    _stub_auth()
    _stub_dependencies()
    try:
        with pytest.raises(RuntimeError, match="CREDENTIAL_ENCRYPTION_KEY"):
            importlib.import_module(mod_name)
    finally:
        os.environ.clear()
        os.environ.update(original)
        if mod_name in sys.modules:
            del sys.modules[mod_name]
