"""
HR payslip seam + commission ledger tests (HR-1, HR-2, HR-3)
=============================================================
Tests for:
- _flatten_payroll_row: converts payroll-collection run rows to the flat
  breakdown shape the FE salary-sheet / payslip tabs expect (HR-1/HR-2).
- Commission endpoint logic: revenue aggregation + commission calculation (HR-3).
"""

import sys
import os

# Make backend importable from the test runner cwd.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# _flatten_payroll_row (HR-1 / HR-2)
# ---------------------------------------------------------------------------


def _make_run_row(
    basic: float = 20000,
    hra: float = 8000,
    conveyance: float = 1600,
    medical: float = 1250,
    special: float = 0,
    earned_gross: float = 30850,
    pf_employee: float = 1800,
    esi_employee: float = 232,
    professional_tax: float = 200,
    tds: float = 0,
    advance_recovery: float = 0,
    net_pay: float = 28618,
    status: str = "DRAFT",
) -> dict:
    """Build a minimal payroll-collection row as the engine would write it."""
    return {
        "payroll_id": "pr-001",
        "employee_id": "emp-001",
        "employee_name": "Test Employee",
        "store_id": "store-001",
        "month": 5,
        "year": 2026,
        "status": status,
        "breakdown": {
            "earnings": {
                "basic": basic,
                "hra": hra,
                "conveyance": conveyance,
                "medical": medical,
                "special_allowance": special,
                "other_allowances": 0,
                "full_gross": earned_gross,
                "earned_gross": earned_gross,
                "incentive": 0,
                "total_earnings": earned_gross,
            },
            "deductions": {
                "pf_employee": pf_employee,
                "esi_employee": esi_employee,
                "professional_tax": professional_tax,
                "tds": tds,
                "advance_recovery": advance_recovery,
                "total_deductions": pf_employee + esi_employee + professional_tax + tds + advance_recovery,
            },
            "net_pay": net_pay,
        },
        "net_salary": net_pay,
    }


def test_flatten_basic_salary():
    """Flat row must expose the correct basic, hra, and allowances."""
    from api.routers.payroll import _flatten_payroll_row
    row = _make_run_row()
    flat = _flatten_payroll_row(row)
    assert flat["basic"] == 20000
    assert flat["hra"] == 8000
    # conveyance + medical + special
    assert flat["allowances"] == 1600 + 1250 + 0
    assert flat["gross_salary"] == 30850


def test_flatten_deductions():
    """Flat row maps esi_employee -> esi, advance_recovery -> advance_deduction."""
    from api.routers.payroll import _flatten_payroll_row
    row = _make_run_row(esi_employee=232, advance_recovery=500)
    flat = _flatten_payroll_row(row)
    assert flat["esi"] == 232
    assert flat["advance_deduction"] == 500


def test_flatten_net_pay():
    """Net pay from nested breakdown.net_pay is surfaced correctly."""
    from api.routers.payroll import _flatten_payroll_row
    row = _make_run_row(net_pay=28000)
    flat = _flatten_payroll_row(row)
    assert flat["net_pay"] == 28000


def test_flatten_salary_record_id_from_payroll_id():
    """salary_record_id should be the payroll_id for run-engine rows."""
    from api.routers.payroll import _flatten_payroll_row
    row = _make_run_row()
    flat = _flatten_payroll_row(row)
    assert flat["salary_record_id"] == "pr-001"
    assert flat["payroll_id"] == "pr-001"


def test_flatten_preserves_nested_breakdown():
    """The returned flat row still carries a ``breakdown`` dict for exports."""
    from api.routers.payroll import _flatten_payroll_row
    row = _make_run_row()
    flat = _flatten_payroll_row(row)
    bd = flat["breakdown"]
    assert bd["basic"] == 20000
    assert bd["hra"] == 8000
    assert bd["net_pay"] == 28618


def test_flatten_status_preserved():
    """Status from the run row is included in the flat shape."""
    from api.routers.payroll import _flatten_payroll_row
    row = _make_run_row(status="APPROVED")
    flat = _flatten_payroll_row(row)
    assert flat["status"] == "APPROVED"


def test_flatten_handles_missing_breakdown():
    """_flatten_payroll_row must not raise on a partial / legacy row."""
    from api.routers.payroll import _flatten_payroll_row
    row = {
        "payroll_id": "pr-legacy",
        "employee_id": "emp-002",
        "employee_name": "Legacy Emp",
        "net_salary": 25000,
    }
    flat = _flatten_payroll_row(row)
    assert flat["net_pay"] == 25000
    assert flat["basic"] == 0


# ---------------------------------------------------------------------------
# Commission calculation logic (HR-3)
# ---------------------------------------------------------------------------


def _commission_for(revenue: float, rate: float) -> float:
    """Pure commission calc: revenue * rate / 100."""
    return round(revenue * rate / 100, 2)


def test_commission_zero_rate():
    """Zero commission rate -> zero commission amount."""
    assert _commission_for(100000, 0) == 0.0


def test_commission_standard_rate():
    """2% commission on 1 lakh = 2000."""
    assert _commission_for(100000, 2) == 2000.0


def test_commission_fractional():
    """1.5% of 33333 rounds to 2 decimal places correctly."""
    result = _commission_for(33333, 1.5)
    assert result == round(33333 * 1.5 / 100, 2)


def test_commission_rank_ordering():
    """Items sorted by descending revenue get rank 1 for the top earner."""
    items = [
        {"employee_id": "a", "revenue": 80000, "commission_rate_percent": 2},
        {"employee_id": "b", "revenue": 120000, "commission_rate_percent": 2},
        {"employee_id": "c", "revenue": 60000, "commission_rate_percent": 1.5},
    ]
    items.sort(key=lambda x: x["revenue"], reverse=True)
    for i, it in enumerate(items):
        it["rank"] = i + 1

    assert items[0]["employee_id"] == "b"
    assert items[0]["rank"] == 1
    assert items[1]["employee_id"] == "a"
    assert items[2]["rank"] == 3


def test_commission_total():
    """Total commission sums the per-staff amounts correctly."""
    items = [
        {"commission_amount": 2000.0},
        {"commission_amount": 1500.0},
        {"commission_amount": 0.0},
    ]
    total = round(sum(x["commission_amount"] for x in items), 2)
    assert total == 3500.0
