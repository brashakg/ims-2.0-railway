"""
IMS 2.0 - Per-unit barcode generator (EAN-13 + Code128)
=======================================================
Pure-function + fake-counter tests; no Mongo required.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from api.services.barcode import (  # noqa: E402
    ean13_check_digit,
    format_ean13,
    validate_ean13,
    format_code128,
    allocate_sequence,
    next_unit_ean13,
)


# --- check digit (known references) -------------------------------------

def test_check_digit_known_values():
    # ISBN-13 9780143007234 -> payload 978014300723, check 4
    assert ean13_check_digit("978014300723") == "4"
    assert ean13_check_digit("012345678901") == "2"
    assert ean13_check_digit("000000000000") == "0"


def test_check_digit_rejects_bad_payload():
    with pytest.raises(ValueError):
        ean13_check_digit("123")          # too short
    with pytest.raises(ValueError):
        ean13_check_digit("12345678901A")  # non-digit


# --- format / validate round-trip ---------------------------------------

def test_format_ean13_is_valid_13_digits():
    code = format_ean13(42, prefix="20")
    assert len(code) == 13
    assert code.isdigit()
    assert code.startswith("20")
    assert validate_ean13(code)


def test_format_ean13_distinct_sequences_distinct_codes():
    a = format_ean13(1)
    b = format_ean13(2)
    assert a != b
    assert validate_ean13(a) and validate_ean13(b)


def test_format_ean13_overflow_and_negative():
    # prefix "20" leaves a 10-digit body; 10**10 overflows it
    with pytest.raises(ValueError):
        format_ean13(10 ** 10, prefix="20")
    with pytest.raises(ValueError):
        format_ean13(-1)
    with pytest.raises(ValueError):
        format_ean13(1, prefix="2A")  # bad prefix


def test_validate_ean13_rejects_tampered():
    code = format_ean13(123)
    bad = code[:12] + str((int(code[12]) + 1) % 10)  # flip the check digit
    assert validate_ean13(code)
    assert not validate_ean13(bad)
    assert not validate_ean13("123")                 # wrong length
    assert not validate_ean13("20000000004A2")       # non-digit
    assert not validate_ean13(None)  # type: ignore[arg-type]


# --- Code128 value ------------------------------------------------------

def test_format_code128_store_prefixed():
    assert format_code128(42, store_code="BV-RNC") == "BVRNC00000042"
    assert format_code128(7, store_code="", width=4) == "0007"


# --- atomic allocation (fake counter) -----------------------------------

class _FakeCounter:
    """Mimics find_one_and_update with $inc + upsert + ReturnDocument.AFTER."""

    def __init__(self):
        self.docs = {}

    def find_one_and_update(self, flt, update, upsert=False, return_document=None):
        _id = flt["_id"]
        cur = dict(self.docs.get(_id, {"_id": _id, "seq": 0}))
        cur["seq"] = cur.get("seq", 0) + update["$inc"]["seq"]
        self.docs[_id] = cur
        return cur


def test_allocate_sequence_is_monotonic():
    c = _FakeCounter()
    seqs = [allocate_sequence(c) for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


def test_allocate_sequence_fail_soft_without_db():
    assert allocate_sequence(None) is None


def test_next_unit_ean13_allocates_valid_unique():
    c = _FakeCounter()
    codes = [next_unit_ean13(c) for _ in range(3)]
    assert all(validate_ean13(x) for x in codes)
    assert len(set(codes)) == 3            # unique per unit
    assert next_unit_ean13(None) is None   # fail-soft
