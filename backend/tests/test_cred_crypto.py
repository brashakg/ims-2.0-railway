"""
BUG-155 - integration-credential encryption at rest (shared cred_crypto module).

Asserts the canonical encrypt/decrypt/mask used by every integration read/write
path: sensitive fields are Fernet-encrypted at rest, decrypt round-trips, plain
legacy values pass through (backward-compatible), encryption is idempotent, and
non-sensitive fields are left untouched.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import cred_crypto as cc  # noqa: E402


def test_encrypt_config_encrypts_sensitive_only():
    cfg = {"key_id": "rzp_live_x", "key_secret": "SECRET123", "enabled_flag": "yes"}
    enc = cc.encrypt_config(cfg)
    # Sensitive field -> fernet ciphertext at rest, NOT the plaintext.
    assert enc["key_secret"].startswith("fernet:")
    assert "SECRET123" not in enc["key_secret"]
    # Non-sensitive fields are untouched.
    assert enc["key_id"] == "rzp_live_x"
    assert enc["enabled_flag"] == "yes"


def test_decrypt_round_trips():
    cfg = {"api_secret": "shh-very-secret", "client_id": "abc"}
    enc = cc.encrypt_config(cfg)
    dec = cc.decrypt_config(enc)
    assert dec["api_secret"] == "shh-very-secret"
    assert dec["client_id"] == "abc"


def test_decrypt_passthrough_on_plaintext():
    """Legacy plaintext rows (pre-encryption) must read back unchanged."""
    legacy = {"api_secret": "old-plaintext-secret", "x": 1}
    assert cc.decrypt_config(legacy) == legacy


def test_encrypt_is_idempotent():
    once = cc.encrypt_config({"token": "T0KEN"})
    twice = cc.encrypt_config(once)
    assert once["token"] == twice["token"]  # not double-encrypted
    assert cc.decrypt_config(twice)["token"] == "T0KEN"


def test_nested_dict_encrypted():
    cfg = {"outer": {"password": "p@ss"}, "name": "x"}
    enc = cc.encrypt_config(cfg)
    assert enc["outer"]["password"].startswith("fernet:")
    assert cc.decrypt_config(enc)["outer"]["password"] == "p@ss"


def test_mask_config_hides_secret():
    masked = cc.mask_config({"api_key": "ABCD1234EFGH", "label": "prod"})
    assert masked["api_key"] != "ABCD1234EFGH"
    assert masked["api_key"].startswith("ABCD")
    assert masked["label"] == "prod"


def test_encrypt_refuses_weak_write_when_fernet_unavailable(monkeypatch):
    """Fail-loud (security P2): a broken Fernet init must REFUSE new credential
    writes instead of silently degrading them to the weak legacy XOR scheme."""
    monkeypatch.setattr(cc, "_fernet_instance", None)
    with pytest.raises(RuntimeError, match="credential encryption unavailable"):
        cc.encrypt_value("secret-value")
    # encrypt_config hits the same guard for any sensitive field.
    with pytest.raises(RuntimeError, match="credential encryption unavailable"):
        cc.encrypt_config({"api_secret": "secret-value"})


def test_decrypt_legacy_xor_still_reads_when_fernet_unavailable(monkeypatch):
    """Back-compat: legacy ``enc:`` rows stay readable even with Fernet down."""
    key = hashlib.sha256(cc._CRED_SECRET.encode()).digest()
    encoded = "old-xor-secret".encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(encoded))
    legacy = "enc:" + base64.b64encode(xored).decode("ascii")
    monkeypatch.setattr(cc, "_fernet_instance", None)
    assert cc.decrypt_value(legacy) == "old-xor-secret"
