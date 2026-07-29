"""
IMS 2.0 - Public GTIN validation
================================
A variant's `gtin` (or `barcode`) is the PUBLIC barcode: it is pushed to
Shopify as `ProductVariant.barcode` and from there feeds the Google and Meta
Shopping catalogs. A wrong value there is customer-visible and actively harmful
-- the feeds either reject the item or, worse, match it to somebody else's
product. An EMPTY gtin is always safer than a wrong one.

This is deliberately NOT the internal barcode. The two-barcode model:

    gtin / barcode   the manufacturer's public GTIN  -> pushed to Shopify
    store_barcode    our internally minted EAN-13    -> NEVER pushed

`services/barcode.py` mints the internal one under GS1 prefix 20-29
("restricted distribution" / in-store only), which is exactly why a code in
that range must never be accepted as a public GTIN: it is by definition not a
manufacturer identifier.

Observed real damage this guards against (prod audit, 2026-07-29 -- 353 of
2,815 gtin-bearing variants were invalid):

    tag string   "brand_boss, shape_square, framecolor_golden, ... gender_men,"
                 (a Shopify browse-tag list that landed in the GTIN column)
    two-in-one   "8056597720373 8056597720380"  (a spec cell listing two EANs)
    model code   "TW003HG14" / "VPLD94 510509"  (not a GTIN at all)
    supplier ref "2511661"                      (a 7-digit internal item code)
    fake check   "92939293"                     (a repeated 4-digit stub)

Pure + dependency-light: no DB, no I/O, safe to call from any door.
No emojis (Windows cp1252).
"""

from __future__ import annotations

import re
from typing import Any, Optional

# GS1 defines exactly these four lengths: GTIN-8, GTIN-12 (UPC-A), GTIN-13
# (EAN-13) and GTIN-14 (case/carton).
VALID_GTIN_LENGTHS = (8, 12, 13, 14)

# Reasons, mirroring the prod audit script so a rejection here is directly
# comparable with the 2026-07-29 breakdown.
REASON_NONNUMERIC = "NONNUMERIC"
REASON_ZEROS = "ZEROS"
REASON_BADLEN = "BADLEN"
REASON_RESTRICTED = "RESTRICTED"
REASON_BADCHECK = "BADCHECK"

_DIGITS = re.compile(r"^\d+$")
# Separators a human or a spec sheet may legitimately put inside one code.
# Stripping them is what makes "805-6597-72037-3" valid and, deliberately,
# what makes "8056597720373 8056597720380" collapse to 26 digits -> BADLEN.
_SEPARATORS = re.compile(r"[\s\-]+")

_RESTRICTED_PREFIXES = frozenset(str(n) for n in range(20, 30))


def check_digit_ok(digits: str) -> bool:
    """True iff `digits` carries a correct GS1 mod-10 check digit.

    Weights alternate 3/1 from the RIGHT, starting at 3 on the digit
    immediately left of the check digit. Applying it from the right makes the
    one implementation correct for all four GTIN lengths (a left-anchored 1/3
    weighting only happens to agree on even-length codes).
    """
    if len(digits) < 2 or not _DIGITS.match(digits):
        return False
    body, check = digits[:-1], digits[-1]
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == int(check)


def _gs1_prefix2(digits: str) -> str:
    """The two leading digits of the GS1 company prefix.

    A GTIN-14 leads with a packaging INDICATOR digit, so its GS1 prefix starts
    one position later; for 8/12/13 the prefix starts at the front.
    """
    return digits[1:3] if len(digits) == 14 else digits[:2]


def normalise_candidate(raw: Any) -> str:
    """Trim and drop internal spaces/hyphens. Never raises; '' for empty/None."""
    if raw is None:
        return ""
    return _SEPARATORS.sub("", str(raw).strip())


def classify_gtin(raw: Any) -> Optional[str]:
    """Why `raw` is not a usable public GTIN, or None when it IS valid.

    None is also returned for an empty/absent value: "no GTIN" is a legitimate
    state (most of the catalog has none), it is simply not something to push.
    Use `is_valid_gtin` when you need "is there a real GTIN here".
    """
    candidate = normalise_candidate(raw)
    if not candidate:
        return None
    if not _DIGITS.match(candidate):
        return REASON_NONNUMERIC
    if set(candidate) == {"0"}:
        return REASON_ZEROS
    if len(candidate) not in VALID_GTIN_LENGTHS:
        return REASON_BADLEN
    if _gs1_prefix2(candidate) in _RESTRICTED_PREFIXES:
        # GS1 20-29 is restricted distribution / in-store only. That is the
        # range services/barcode.py mints our own store_barcode in, so such a
        # code is either somebody's shelf label or our own internal barcode
        # leaking into the public field. Never publish it.
        return REASON_RESTRICTED
    if not check_digit_ok(candidate):
        return REASON_BADCHECK
    return None


def is_valid_gtin(raw: Any) -> bool:
    """True only for a real, publishable GTIN. Empty -> False."""
    return bool(normalise_candidate(raw)) and classify_gtin(raw) is None


def sanitise_gtin(raw: Any) -> Optional[str]:
    """The normalised GTIN when `raw` is publishable, else None.

    This is the function every write/push door should use: it turns "anything
    the caller handed us" into "a GTIN we are willing to put in front of a
    customer, or nothing at all".
    """
    if not is_valid_gtin(raw):
        return None
    return normalise_candidate(raw)
