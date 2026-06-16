"""
BUG-155 - integration-credential encryption at rest (shared cred_crypto module).

Asserts the canonical encrypt/decrypt/mask used by every integration read/write
path: sensitive fields are Fernet-encrypted at rest, decrypt round-trips, plain
legacy values pass through (backward-compatible), encryption is idempotent, and
non-sensitive fields are left untouched.
"""
from __future__ import annotations

import os
import sys

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
