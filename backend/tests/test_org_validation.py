"""
Unit tests for services/org_validation.py — Indian statutory ID validators.
Pure functions, no DB.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import org_validation as ov  # noqa: E402


# --- PAN ---
def test_pan_valid():
    assert ov.validate_pan("AAPFU0939F")
    assert ov.validate_pan("aapfu0939f")  # normalised to upper


def test_pan_invalid():
    assert not ov.validate_pan("AAPFU0939")      # too short
    assert not ov.validate_pan("12PFU0939F")     # must start with letters
    assert not ov.validate_pan("")
    assert not ov.validate_pan(None)


# --- GSTIN (format + checksum) ---
def test_gstin_checksum_known_good():
    # 27AAPFU0939F1ZV is a widely-used valid sample GSTIN (Maharashtra).
    assert ov.gstin_checksum_char("27AAPFU0939F1Z") == "V"


def test_gstin_valid_known_good():
    assert ov.validate_gstin("27AAPFU0939F1ZV")


def test_gstin_bad_checksum_rejected():
    assert not ov.validate_gstin("27AAPFU0939F1ZA")  # wrong last char


def test_gstin_format_rejected():
    assert not ov.validate_gstin("27AAPFU0939F1Z")   # only 14 chars
    assert not ov.validate_gstin("99AAPFU0939F1ZV", verify_checksum=False) is False  # 99 is a valid jurisdiction code
    assert not ov.validate_gstin("ZZAAPFU0939F1ZV")  # non-numeric state


def test_gstin_unknown_state_rejected():
    # 88 is not a real state code
    assert not ov.validate_gstin("88AAPFU0939F1ZV", verify_checksum=False)


# --- IFSC ---
def test_ifsc():
    assert ov.validate_ifsc("HDFC0001234")
    assert ov.validate_ifsc("SBIN0000001")
    assert not ov.validate_ifsc("HDFC1001234")   # 5th char must be 0
    assert not ov.validate_ifsc("HDF0001234")    # too short


# --- pincode ---
def test_pincode():
    assert ov.validate_pincode("834001")
    assert not ov.validate_pincode("034001")   # cannot start 0
    assert not ov.validate_pincode("83400")    # too short


# --- phone ---
def test_phone():
    assert ov.validate_phone("9876543210")
    assert ov.validate_phone("+919876543210")
    assert ov.validate_phone("09876543210")
    assert not ov.validate_phone("1234567890")  # must start 6-9
    assert not ov.validate_phone("98765")


# --- consistency ---
def test_gstin_embeds_pan():
    assert ov.gstin_pan("27AAPFU0939F1ZV") == "AAPFU0939F"
    assert ov.gstin_matches_pan("27AAPFU0939F1ZV", "AAPFU0939F")
    assert not ov.gstin_matches_pan("27AAPFU0939F1ZV", "BBPFU0939F")


def test_gstin_state_match():
    assert ov.gstin_state_code("27AAPFU0939F1ZV") == "27"
    assert ov.gstin_matches_state("27AAPFU0939F1ZV", "27")
    assert not ov.gstin_matches_state("27AAPFU0939F1ZV", "20")


def test_state_name():
    assert ov.state_name("20") == "Jharkhand"
    assert ov.state_name("27") == "Maharashtra"
    assert ov.state_name("00") is None


def test_normalize_state_code():
    assert ov.normalize_state_code("20") == "20"      # already numeric
    assert ov.normalize_state_code("JH") == "20"      # abbreviation
    assert ov.normalize_state_code("jh") == "20"      # case-insensitive
    assert ov.normalize_state_code("Maharashtra") == "27"  # full name
    assert ov.normalize_state_code("MH") == "27"
    assert ov.normalize_state_code("ZZ") == "ZZ"      # unknown -> unchanged
    assert ov.normalize_state_code(None) is None


def test_validate_tan():
    assert ov.validate_tan("RANC01234E")
    assert not ov.validate_tan("RANC0123E")   # too short
    assert not ov.validate_tan("1ANC01234E")  # must start with letters


def test_resolve_gstin_for_state():
    gstins = [
        {"gstin": "20AAPFU0939F1Z?", "state_code": "20"},
        {"gstin": "27AAPFU0939F1ZV", "state_code": "27"},
    ]
    got = ov.resolve_gstin_for_state(gstins, "27")
    assert got and got["gstin"] == "27AAPFU0939F1ZV"
    assert ov.resolve_gstin_for_state(gstins, "09") is None
