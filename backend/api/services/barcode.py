"""
IMS 2.0 - Per-unit barcode generation (EAN-13 + Code128)
========================================================
Business rule (from the operator): a product's SKU is stable across purchases,
but every physical UNIT gets a UNIQUE barcode at intake (GRN), unique per unit
per purchase. The chosen symbology is EAN-13 (numeric, scanner-standard, check-
digit protected) with a Code128 alphanumeric alternative.

EAN-13 layout used here:
    [prefix][sequence][check]   = 13 digits total
- `prefix` defaults to "20": GS1 reserves prefixes 20-29 for "restricted
  distribution" / in-store use, so our internally minted codes never collide
  with a real manufacturer GTIN.
- `sequence` is a monotonic per-deployment counter (atomic, multi-worker safe)
  zero-padded to fill the remaining payload width.
- `check` is the standard EAN-13 mod-10 check digit.

All helpers are pure + dependency-light; only `allocate_sequence` /
`next_unit_ean13` touch Mongo, and they fail soft (return None) when no DB is
available so a stock intake is never blocked by the counter.
"""

from __future__ import annotations

import os
from typing import Optional

# In-store / restricted-distribution prefix (GS1 20-29). Override per deployment.
DEFAULT_EAN13_PREFIX = os.getenv("BARCODE_EAN13_PREFIX", "20")
_COUNTER_NAME = "unit_barcode_seq"


def ean13_check_digit(payload12: str) -> str:
    """Standard EAN-13 mod-10 check digit for a 12-digit payload.

    Odd positions (1st, 3rd, ... from the left, 1-indexed) weigh x1, even
    positions weigh x3; check = (10 - sum mod 10) mod 10.
    """
    if len(payload12) != 12 or not payload12.isdigit():
        raise ValueError("EAN-13 payload must be exactly 12 digits")
    total = 0
    for i, ch in enumerate(payload12):
        total += int(ch) * (1 if i % 2 == 0 else 3)
    return str((10 - total % 10) % 10)


def format_ean13(sequence: int, prefix: str = DEFAULT_EAN13_PREFIX) -> str:
    """Build a full 13-digit EAN-13 from an internal prefix + sequence.

    Raises ValueError on a bad prefix, a negative sequence, or a sequence that
    overflows the available payload width for the chosen prefix.
    """
    if not prefix.isdigit() or not (1 <= len(prefix) <= 11):
        raise ValueError("prefix must be 1-11 digits")
    if sequence < 0:
        raise ValueError("sequence must be non-negative")
    body_len = 12 - len(prefix)
    body = str(sequence)
    if len(body) > body_len:
        raise ValueError(
            f"sequence {sequence} overflows the {body_len}-digit body for prefix '{prefix}'"
        )
    payload = prefix + body.zfill(body_len)
    return payload + ean13_check_digit(payload)


def validate_ean13(code: str) -> bool:
    """True iff `code` is a 13-digit string with a correct check digit."""
    if not isinstance(code, str) or len(code) != 13 or not code.isdigit():
        return False
    return ean13_check_digit(code[:12]) == code[12]


def format_code128(sequence: int, store_code: str = "", width: int = 8) -> str:
    """Alphanumeric Code128 *value* for an internal unit barcode.

    Code128 can encode the full alphanumeric set, so a human-readable
    store-prefixed code is fine here (the symbology rendering is a print-side
    concern). Returns e.g. 'BVRNC00000042'.
    """
    sc = "".join(c for c in (store_code or "").upper() if c.isalnum())
    return f"{sc}{str(sequence).zfill(width)}"


def allocate_sequence(counter_coll, name: str = _COUNTER_NAME) -> Optional[int]:
    """Atomically claim the next monotonic sequence from a counter doc.

    Uses a single find_one_and_update so concurrent multi-worker intakes each
    get a unique sequence with no torn reads. Fail-soft: no collection / any
    error -> None (caller falls back, never blocks the intake).
    """
    if counter_coll is None:
        return None
    try:
        from pymongo import ReturnDocument

        doc = counter_coll.find_one_and_update(
            {"_id": name},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if doc and isinstance(doc.get("seq"), int):
            return doc["seq"]
    except Exception:  # noqa: BLE001 - fail-soft, intake must not break
        return None
    return None


def next_unit_ean13(counter_coll, prefix: str = DEFAULT_EAN13_PREFIX) -> Optional[str]:
    """Allocate the next sequence and return a fresh, valid EAN-13 unit barcode.

    None when no counter is available (caller should fall back to its existing
    barcode scheme so a GRN/intake is never blocked)."""
    seq = allocate_sequence(counter_coll)
    if seq is None:
        return None
    return format_ean13(seq, prefix=prefix)
