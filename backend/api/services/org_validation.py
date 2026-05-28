"""
IMS 2.0 - Organisation (entity/store) validation helpers
========================================================
Pure, side-effect-free validators for Indian statutory identifiers used across
the entity + store master:

  * PAN        AAAAA9999A
  * GSTIN      15 chars: <2 state><10 PAN><1 entity-no><'Z'><1 checksum>
               (checksum verified with the official GSTN mod-36 algorithm)
  * IFSC       4 letters + '0' + 6 alphanumerics
  * PIN code   6 digits, not starting with 0
  * phone      Indian 10-digit mobile (optionally +91 / 0 prefixed)

Plus cross-field consistency:
  * a GSTIN embeds the holder's PAN at positions 3-12
  * a GSTIN's first two digits are its state code

Everything returns a bool or a small dict; nothing raises on bad input, so the
router can decide whether to 400 or warn. State-code <-> name lookups use the
GST state-code list (used by the e-invoice / e-way-bill systems).
"""

import re
from typing import Optional

# GST state codes (2-digit) -> state / UT name. Source: GSTN state-code master.
INDIAN_STATE_CODES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

# 2-letter abbreviations -> GST numeric code, so legacy data that stored a
# state as "JH"/"MH" normalises to "20"/"27" (the canonical GST form).
STATE_ABBR = {
    "JK": "01",
    "HP": "02",
    "PB": "03",
    "CH": "04",
    "UK": "05",
    "UT": "05",
    "HR": "06",
    "DL": "07",
    "RJ": "08",
    "UP": "09",
    "BR": "10",
    "SK": "11",
    "AR": "12",
    "NL": "13",
    "MN": "14",
    "MZ": "15",
    "TR": "16",
    "ML": "17",
    "AS": "18",
    "WB": "19",
    "JH": "20",
    "OD": "21",
    "OR": "21",
    "CG": "22",
    "MP": "23",
    "GJ": "24",
    "MH": "27",
    "KA": "29",
    "GA": "30",
    "LD": "31",
    "KL": "32",
    "TN": "33",
    "PY": "34",
    "AN": "35",
    "TS": "36",
    "TG": "36",
    "AP": "37",
    "LA": "38",
    "DN": "26",
    "DD": "26",
}

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# TAN: 4 letters (3 jurisdiction + 1 first-letter-of-name), 5 digits, 1 letter.
_TAN_RE = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")
# 2 state digits, 5 PAN letters, 4 PAN digits, 1 PAN letter, 1 entity char,
# 'Z' (default), 1 checksum char.
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
_PHONE_RE = re.compile(r"^[6-9][0-9]{9}$")

_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _norm(v: Optional[str]) -> str:
    return (v or "").strip().upper()


def validate_pan(pan: Optional[str]) -> bool:
    return bool(_PAN_RE.match(_norm(pan)))


def validate_tan(tan: Optional[str]) -> bool:
    return bool(_TAN_RE.match(_norm(tan)))


def gstin_checksum_char(gstin14: str) -> Optional[str]:
    """The official GSTN check character for the first 14 chars of a GSTIN.

    Mod-36 algorithm: walk the 14 chars, alternating factor 1/2 (rightmost
    char uses factor 2... we iterate left-to-right with factor starting at 1
    on an even index), sum floor(p/36)+(p%36), checksum = (36 - sum%36) % 36.
    Returns None if any char is outside the alphabet.
    """
    if not gstin14 or len(gstin14) != 14:
        return None
    factor = 1
    total = 0
    mod = len(_GSTIN_ALPHABET)  # 36
    for ch in gstin14:
        idx = _GSTIN_ALPHABET.find(ch)
        if idx < 0:
            return None
        product = idx * factor
        total += (product // mod) + (product % mod)
        factor = 2 if factor == 1 else 1
    check = (mod - (total % mod)) % mod
    return _GSTIN_ALPHABET[check]


def validate_gstin(gstin: Optional[str], verify_checksum: bool = True) -> bool:
    """Format + (optionally) checksum validation of a GSTIN."""
    g = _norm(gstin)
    if not _GSTIN_RE.match(g):
        return False
    if g[:2] not in INDIAN_STATE_CODES:
        return False
    if verify_checksum:
        expected = gstin_checksum_char(g[:14])
        if expected is None or expected != g[14]:
            return False
    return True


def validate_ifsc(ifsc: Optional[str]) -> bool:
    return bool(_IFSC_RE.match(_norm(ifsc)))


def validate_pincode(pin: Optional[str]) -> bool:
    return bool(_PINCODE_RE.match((pin or "").strip()))


def validate_phone(phone: Optional[str]) -> bool:
    """Indian 10-digit mobile, tolerating a +91 / 91 / 0 prefix and spaces."""
    raw = re.sub(r"[\s\-]", "", (phone or "").strip())
    if raw.startswith("+91"):
        raw = raw[3:]
    elif raw.startswith("91") and len(raw) == 12:
        raw = raw[2:]
    elif raw.startswith("0") and len(raw) == 11:
        raw = raw[1:]
    return bool(_PHONE_RE.match(raw))


def gstin_state_code(gstin: Optional[str]) -> Optional[str]:
    g = _norm(gstin)
    return g[:2] if len(g) >= 2 else None


def gstin_pan(gstin: Optional[str]) -> Optional[str]:
    g = _norm(gstin)
    return g[2:12] if len(g) >= 12 else None


def gstin_matches_pan(gstin: Optional[str], pan: Optional[str]) -> bool:
    """True only when the GSTIN embeds exactly this PAN (positions 3-12)."""
    gp = gstin_pan(gstin)
    return bool(gp and gp == _norm(pan))


def gstin_matches_state(gstin: Optional[str], state_code: Optional[str]) -> bool:
    """True when the GSTIN's first two digits equal the declared state code."""
    sc = (state_code or "").strip()
    return bool(sc and gstin_state_code(gstin) == sc)


def state_name(state_code: Optional[str]) -> Optional[str]:
    return INDIAN_STATE_CODES.get((state_code or "").strip())


def normalize_state_code(value):
    """Map a state code / 2-letter abbreviation / full name to the 2-digit GST
    numeric code. Returns the input unchanged if unresolvable (so the caller's
    validation still flags it)."""
    if value is None:
        return value
    v = str(value).strip().upper()
    if v in INDIAN_STATE_CODES:
        return v
    if v in STATE_ABBR:
        return STATE_ABBR[v]
    for code, nm in INDIAN_STATE_CODES.items():
        if nm.upper() == v:
            return code
    return value


def resolve_gstin_for_state(gstins, state_code: Optional[str]) -> Optional[dict]:
    """From a list of {gstin, state_code, ...} entries, return the one matching
    the given state code (the store's state). None if not found."""
    sc = (state_code or "").strip()
    if not sc or not gstins:
        return None
    for g in gstins:
        if isinstance(g, dict) and (g.get("state_code") or "").strip() == sc:
            return g
    return None
