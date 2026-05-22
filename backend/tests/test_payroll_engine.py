"""
Payroll computation engine tests (Phase 2)
==========================================
Pure, deterministic checks of the statutory math: EPF (+ EPS/EDLI/admin),
ESI eligibility + rounding, Professional Tax integration, LWP proration,
incentive merge, advance recovery, net pay, and employer cost.
"""

from api.services.payroll_engine import (
    compute_payroll,
    compute_pf,
    esi_eligible,
    pt_for,
    pt_code_for_state,
    DEFAULT_PT_SLABS,
)

JH = DEFAULT_PT_SLABS["JH"]
MH = DEFAULT_PT_SLABS["MH"]


# ---------------------------------------------------------------------------
# compute_pf
# ---------------------------------------------------------------------------


def test_pf_capped_at_15k():
    pf = compute_pf(20000, ceiling_cap=True)  # basic above ceiling
    assert pf["contributory_wage"] == 15000
    assert pf["employee"] == 1800          # 12% of 15000
    assert pf["employer_eps"] == 1250      # 8.33% of 15000 -> 1249.5 -> 1250
    assert pf["employer_epf"] == 550       # 1800 - 1250
    assert pf["edli"] == 75                # 0.5% of 15000
    assert pf["admin"] == 75               # 0.5% of 15000
    assert pf["employer_total"] == 1950


def test_pf_uncapped_on_actual_basic():
    pf = compute_pf(25000, ceiling_cap=False)
    assert pf["employee"] == 3000          # 12% of 25000
    assert pf["employer_eps"] == 1250      # EPS still capped at 15000
    assert pf["employer_epf"] == 1750      # 3000 - 1250
    assert pf["edli"] == 75                # EDLI on EPS-capped 15000
    assert pf["admin"] == 125              # admin on actual 25000
    assert pf["employer_total"] == 3200


def test_pf_below_ceiling():
    pf = compute_pf(10000, ceiling_cap=True)
    assert pf["employee"] == 1200
    assert pf["employer_eps"] == 833       # 8.33% of 10000 = 833.0
    assert pf["employer_epf"] == 367       # 1200 - 833


# ---------------------------------------------------------------------------
# ESI eligibility
# ---------------------------------------------------------------------------


def test_esi_auto_eligible_under_ceiling():
    assert esi_eligible({}, 21000) is True
    assert esi_eligible({}, 20999) is True


def test_esi_auto_not_eligible_over_ceiling():
    assert esi_eligible({}, 21001) is False
    assert esi_eligible({}, 35000) is False


def test_esi_explicit_overrides():
    assert esi_eligible({"esi_applicable": True}, 30000) is True
    assert esi_eligible({"esi_applicable": False}, 10000) is False


# ---------------------------------------------------------------------------
# Professional Tax
# ---------------------------------------------------------------------------


def test_pt_state_code_normalization():
    assert pt_code_for_state("Jharkhand") == "JH"
    assert pt_code_for_state("maharashtra") == "MH"
    assert pt_code_for_state("27") == "MH"   # GST state code
    assert pt_code_for_state("JH") == "JH"
    assert pt_code_for_state("Goa") is None


def test_pt_jharkhand_and_maharashtra():
    assert pt_for(JH, 35000, 6) == 100       # 4.2L/yr
    assert pt_for(MH, 12000, 6, "MALE") == 200
    assert pt_for(MH, 12000, 2, "MALE") == 300   # February
    assert pt_for(MH, 20000, 6, "FEMALE") == 0   # women nil <= 25k


# ---------------------------------------------------------------------------
# compute_payroll — full scenarios
# ---------------------------------------------------------------------------

CONFIG_A = {
    "employee_id": "EMP-A",
    "basic": 20000, "hra": 8000, "conveyance": 1600, "medical": 1250,
    "special_allowance": 5000,
    "pf_applicable": True, "pf_wage_ceiling_cap": True,
    "esi_applicable": None, "pt_applicable": True,
}


def test_full_month_jharkhand_no_esi():
    r = compute_payroll(CONFIG_A, month=6, year=2026, pt_slab=JH)
    assert r["earnings"]["full_gross"] == 35850
    assert r["earnings"]["earned_gross"] == 35850
    assert r["proration_factor"] == 1.0
    assert r["deductions"]["pf_employee"] == 1800
    assert r["deductions"]["esi_employee"] == 0      # gross > 21k
    assert r["esi_applicable"] is False
    assert r["deductions"]["professional_tax"] == 100
    assert r["deductions"]["total_deductions"] == 1900
    assert r["net_pay"] == 33950
    assert r["employer_contributions"]["pf_employer_total"] == 1950
    assert r["ctc_cost"] == 37800


def test_lwp_proration_maharashtra_with_esi():
    cfg = {
        "employee_id": "EMP-B",
        "basic": 8000, "hra": 2000, "conveyance": 800, "special_allowance": 1200,
        "pf_applicable": True, "pf_wage_ceiling_cap": True,
        "esi_applicable": None, "pt_applicable": True,
    }
    r = compute_payroll(cfg, month=6, year=2026, lwp_days=3, pt_slab=MH)
    assert r["proration_factor"] == 0.9
    assert r["earnings"]["earned_gross"] == 10800     # 12000 * 27/30
    assert r["deductions"]["pf_employee"] == 864      # 12% of 7200
    assert r["esi_applicable"] is True
    assert r["deductions"]["esi_employee"] == 81      # ceil(0.75% of 10800)
    assert r["employer_contributions"]["esi_employer"] == 351
    assert r["deductions"]["professional_tax"] == 200
    assert r["net_pay"] == 9655
    assert r["ctc_cost"] == 12087


def test_incentive_and_advance_do_not_change_statutory_base():
    r = compute_payroll(
        CONFIG_A, month=6, year=2026, pt_slab=JH, incentive=5000, advance_recovery=2000
    )
    # PF/ESI unchanged vs the no-incentive case
    assert r["deductions"]["pf_employee"] == 1800
    assert r["deductions"]["esi_employee"] == 0
    assert r["earnings"]["incentive"] == 5000
    assert r["earnings"]["total_earnings"] == 40850
    assert r["deductions"]["advance_recovery"] == 2000
    assert r["deductions"]["total_deductions"] == 3900  # 1800 + 100 + 2000
    assert r["net_pay"] == 36950
    assert r["ctc_cost"] == 42800


def test_pf_and_pt_disabled():
    cfg = {
        "employee_id": "EMP-E", "basic": 20000,
        "pf_applicable": False, "esi_applicable": False, "pt_applicable": False,
    }
    r = compute_payroll(cfg, month=6, year=2026, pt_slab=JH)
    assert r["deductions"]["pf_employee"] == 0
    assert r["deductions"]["esi_employee"] == 0
    assert r["deductions"]["professional_tax"] == 0
    assert r["deductions"]["total_deductions"] == 0
    assert r["net_pay"] == 20000
    assert r["ctc_cost"] == 20000


def test_esi_forced_on_above_ceiling():
    cfg = {"employee_id": "EMP-F", "basic": 30000, "esi_applicable": True,
           "pf_applicable": False, "pt_applicable": False}
    r = compute_payroll(cfg, month=6, year=2026)
    assert r["esi_applicable"] is True
    assert r["deductions"]["esi_employee"] == 225   # ceil(0.75% of 30000)
    assert r["employer_contributions"]["esi_employer"] == 975


def test_full_lwp_zeroes_earnings():
    r = compute_payroll(CONFIG_A, month=6, year=2026, lwp_days=30, pt_slab=JH)
    assert r["earnings"]["earned_gross"] == 0
    assert r["deductions"]["pf_employee"] == 0
    assert r["net_pay"] == 0
