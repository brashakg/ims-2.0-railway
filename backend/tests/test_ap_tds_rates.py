"""
IMS 2.0 — TDS section rates (Budget 2024 corrections)
======================================================
194H (commission/brokerage) was cut 5% -> 2% by Budget 2024 (eff. 1 Oct 2024);
the table still had 5%, over-withholding on commission payments. And 194J split
into professional (10%) vs technical services (2%) since FY2020-21 — only the 10%
professional rate existed. These lock the corrected rates. (Rates are CA-verified
config; the engine applies the rate to the base it's given and does not enforce
monetary thresholds.)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-tds")


def test_194h_is_two_percent():
    from api.services.ap_engine import compute_tds

    out = compute_tds(100000, "194H")
    assert out["rate"] == 2.0
    assert out["tds_amount"] == 2000.0
    assert out["net_payable"] == 98000.0


def test_194j_professional_and_technical_split():
    from api.services.ap_engine import compute_tds

    assert compute_tds(100000, "194J")["rate"] == 10.0  # professional default
    assert compute_tds(100000, "194J_TECH")["rate"] == 2.0  # technical services


def test_other_sections_unchanged():
    from api.services.ap_engine import compute_tds

    assert compute_tds(100000, "194C_IND")["rate"] == 1.0
    assert compute_tds(100000, "194C_OTHER")["rate"] == 2.0
    assert compute_tds(100000, "194Q")["rate"] == 0.1
    assert compute_tds(100000, "194I_LAND")["rate"] == 10.0
    assert compute_tds(100000, "194I_PLANT")["rate"] == 2.0


def test_unknown_section_is_zero():
    from api.services.ap_engine import compute_tds

    out = compute_tds(100000, "NOPE")
    assert out["rate"] == 0.0
    assert out["section"] == "NONE"
    assert out["net_payable"] == 100000.0
