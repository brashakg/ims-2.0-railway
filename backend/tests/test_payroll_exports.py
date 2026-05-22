"""
Payroll exports tests (Phase 4)
===============================
Pure checks of the statutory summary, the balanced Tally salary JV (XML),
the PF ECR text, and the HTML payslip. Rows are built from the real engine
so the breakdown shape matches production.
"""

import re

from api.services.payroll_engine import compute_payroll, DEFAULT_PT_SLABS
from api.services.payroll_exports import (
    statutory_summary,
    build_salary_jv_xml,
    build_pf_ecr,
    build_payslip_html,
)

JH = DEFAULT_PT_SLABS["JH"]

CFG_A = {"employee_id": "EMP-A", "basic": 20000, "hra": 8000, "conveyance": 1600,
         "medical": 1250, "special_allowance": 5000}
CFG_B = {"employee_id": "EMP-B", "basic": 8000, "hra": 2000, "conveyance": 800,
         "special_allowance": 1200}


def _row(cfg, **kw):
    bd = compute_payroll(cfg, month=6, year=2026, pt_slab=JH, **kw)
    return {
        "employee_id": cfg["employee_id"],
        "employee_name": cfg["employee_id"],
        "breakdown": bd,
        "net_salary": bd["net_pay"],
        "deductions": bd["deductions"]["total_deductions"],
        "entity_id": "ent1",
    }


ROWS = [_row(CFG_A), _row(CFG_B)]


def test_statutory_summary_totals():
    s = statutory_summary(ROWS)
    assert s["count"] == 2
    assert s["gross"] == 47850          # 35850 + 12000
    assert s["pf_employee"] == 2760     # 1800 + 960
    assert s["pf_employer"] == 2990     # 1950 + 1040
    assert s["esi_employee"] == 90      # 0 + 90
    assert s["esi_employer"] == 390     # 0 + 390
    assert s["professional_tax"] == 100 # 100 + 0
    assert s["net"] == 44900            # 33950 + 10950
    assert s["pf_total_payable"] == 5750
    assert s["esi_total_payable"] == 480


def test_salary_jv_balances_and_has_ledgers():
    xml = build_salary_jv_xml({"name": "BV Chas"}, ROWS, 6, 2026)
    amounts = [float(a) for a in re.findall(r"<AMOUNT>(-?\d+\.\d+)</AMOUNT>", xml)]
    assert amounts, "no ledger amounts found"
    assert abs(sum(amounts)) < 0.01      # double entry must balance
    assert "Salaries &amp; Wages" in xml
    assert "PF Payable" in xml
    assert "Salary Payable" in xml
    assert "VCHTYPE=\"Journal\"" in xml


def test_salary_jv_empty_rows_balances():
    xml = build_salary_jv_xml({"name": "X"}, [], 6, 2026)
    amounts = [float(a) for a in re.findall(r"<AMOUNT>(-?\d+\.\d+)</AMOUNT>", xml)]
    assert abs(sum(amounts)) < 0.01      # zero/empty still balances (no entries)


def test_pf_ecr_fields():
    cfgs = {"EMP-A": {"uan": "UANA"}, "EMP-B": {"uan": "UANB"}}
    text = build_pf_ecr(ROWS, cfgs)
    lines = text.split("\n")
    assert len(lines) == 2
    a = lines[0].split("#~#")
    assert len(a) == 11
    assert a[0] == "UANA"
    assert a[2] == "35850"   # gross wages
    assert a[3] == "15000"   # EPF wages (capped)
    assert a[6] == "1800"    # EPF employee contribution
    assert a[7] == "1250"    # EPS contribution
    assert a[8] == "550"     # EPF-EPS diff


def test_pf_ecr_skips_non_pf_employees():
    row = _row({"employee_id": "EMP-N", "basic": 20000, "pf_applicable": False})
    assert build_pf_ecr([row], {}) == ""


def test_payslip_html_renders_key_fields():
    html = build_payslip_html(
        ROWS[0], {"name": "BV Chas"}, {"full_name": "Alice", "designation": "Optometrist"}
    )
    assert "BV Chas" in html
    assert "Net Pay" in html
    assert "Optometrist" in html
    assert "<!DOCTYPE html>" in html
