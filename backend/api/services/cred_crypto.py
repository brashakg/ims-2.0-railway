"""
IMS 2.0 - Credential encryption (at-rest) -- shared, canonical implementation.

BUG-155: integration credentials (Shopify/Razorpay/Shiprocket/MSG91/Tally/...)
must be encrypted at rest in the `integrations` collection. The encryption helper
originally lived in routers/settings.py, but the credentials are written AND read
from many places (settings, admin, nexus_providers, einvoice, ad_providers, ondc,
integration_config/status, online_order_mapper, ...). This leaf module is the ONE
source of truth so every write encrypts and every read decrypts with the SAME
Fernet key -- a value written by one path is always readable by every other.

Storage formats (decrypt handles all three transparently):
  - ``fernet:<token>``  -- current Fernet (authenticated AES-128-CBC + HMAC)
  - ``enc:<b64>``       -- legacy XOR (read-only back-compat)
  - ``<plain>``         -- unencrypted legacy value (passthrough)

Key derivation: SHA-256 of CREDENTIAL_ENCRYPTION_KEY (or JWT_SECRET_KEY) -> 32
bytes -> urlsafe-b64 Fernet key. Fail loud if neither env var is set (a known
constant key would be equivalent to plaintext). decrypt() is passthrough on
plaintext, so this is fully backward-compatible with existing plaintext rows.
"""
import os
import base64
import hashlib
import logging

logger = logging.getLogger(__name__)

# Source the at-rest key from env. Prefer a dedicated CREDENTIAL_ENCRYPTION_KEY,
# fall back to JWT_SECRET_KEY (always present whenever the app boots).
_CRED_SECRET = os.getenv("CREDENTIAL_ENCRYPTION_KEY") or os.getenv("JWT_SECRET_KEY")
if not _CRED_SECRET:
    raise RuntimeError(
        "CREDENTIAL_ENCRYPTION_KEY or JWT_SECRET_KEY environment variable is "
        "required to encrypt integration credentials at rest. "
        "Generate one with: openssl rand -hex 32"
    )

try:
    from cryptography.fernet import Fernet as _Fernet, InvalidToken as _InvalidToken

    _fernet_raw_key = hashlib.sha256(_CRED_SECRET.encode()).digest()  # 32 bytes
    _fernet_key = base64.urlsafe_b64encode(_fernet_raw_key)
    _fernet_instance = _Fernet(_fernet_key)
    del _fernet_raw_key, _fernet_key
except Exception as _fernet_init_err:  # pragma: no cover
    _fernet_instance = None
    _InvalidToken = Exception  # type: ignore[assignment,misc]
    logger.warning(
        "[CRED] Fernet init failed (%s); new credential writes will be REFUSED "
        "(legacy enc:/plaintext values remain readable).",
        _fernet_init_err,
    )

# Sensitive config field names that must be encrypted at rest & masked on read.
SENSITIVE_FIELDS = {
    "api_key",
    "api_secret",
    "secret_key",
    "key_secret",
    "secret",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "private_key",
    "signing_key",
    "webhook_secret",
    "webhook_url",
    "app_secret",
    "verify_token",
    "developer_token",
    "client_secret",
    "razorpay_key_secret",
    "shopify_api_secret",
    "whatsapp_api_key",
    "tally_password",
    "shiprocket_password",
}


def mask_value(val: str) -> str:
    """Mask a credential: show first 4 and last 2 chars only."""
    if not val or len(val) < 8:
        return "****"
    return val[:4] + "*" * (len(val) - 6) + val[-2:]


def mask_config(config: dict) -> dict:
    """Deep-mask any sensitive fields in a config dict (for API responses)."""
    if not isinstance(config, dict):
        return config
    masked = {}
    for k, v in config.items():
        if isinstance(v, dict):
            masked[k] = mask_config(v)
        elif isinstance(v, str) and k.lower() in SENSITIVE_FIELDS:
            masked[k] = mask_value(v)
        else:
            masked[k] = v
    return masked


def encrypt_value(plaintext: str) -> str:
    """Encrypt a credential for at-rest storage (Fernet, prefix ``fernet:``).

    Fail-loud: if Fernet is unavailable (cryptography missing / init failed)
    we REFUSE to write rather than silently degrading new writes to the weak
    legacy XOR scheme. decrypt_value keeps reading legacy ``enc:`` rows.
    """
    if _fernet_instance is None:
        raise RuntimeError(
            "credential encryption unavailable - refusing weak-encryption write"
        )
    token = _fernet_instance.encrypt(plaintext.encode("utf-8"))
    return "fernet:" + token.decode("ascii")


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a stored credential (handles fernet:/enc:/plaintext)."""
    if ciphertext.startswith("fernet:"):
        if _fernet_instance is None:
            logger.warning("[CRED] Cannot decrypt Fernet value: cryptography not available.")
            return ciphertext
        try:
            return _fernet_instance.decrypt(ciphertext[7:].encode("ascii")).decode("utf-8")
        except _InvalidToken:
            logger.warning("[CRED] Fernet decryption failed (bad token or wrong key).")
            return ciphertext
    if ciphertext.startswith("enc:"):
        try:
            raw = base64.b64decode(ciphertext[4:])
            key = hashlib.sha256(_CRED_SECRET.encode()).digest()
            return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")
        except Exception:
            return ciphertext
    return ciphertext  # Unencrypted legacy value


def encrypt_config(config: dict) -> dict:
    """Encrypt sensitive fields before writing to MongoDB. Idempotent (skips
    values already encrypted under either scheme)."""
    if not isinstance(config, dict):
        return config
    encrypted = {}
    for k, v in config.items():
        if isinstance(v, dict):
            encrypted[k] = encrypt_config(v)
        elif (
            isinstance(v, str)
            and k.lower() in SENSITIVE_FIELDS
            and not v.startswith("enc:")
            and not v.startswith("fernet:")
        ):
            encrypted[k] = encrypt_value(v)
        else:
            encrypted[k] = v
    return encrypted


def decrypt_config(config: dict) -> dict:
    """Decrypt sensitive fields after reading from MongoDB (for internal use).
    Passthrough on plaintext, so it is safe on legacy unencrypted rows."""
    if not isinstance(config, dict):
        return config
    decrypted = {}
    for k, v in config.items():
        if isinstance(v, dict):
            decrypted[k] = decrypt_config(v)
        elif isinstance(v, str) and (v.startswith("enc:") or v.startswith("fernet:")):
            try:
                decrypted[k] = decrypt_value(v)
            except Exception:
                decrypted[k] = v
        else:
            decrypted[k] = v
    return decrypted
