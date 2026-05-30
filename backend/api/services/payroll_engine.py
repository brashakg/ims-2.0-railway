"""
IMS 2.0 - Payroll Computation Engine (pure)
===========================================
Stateless salary computation for the Indian statutory regime:
EPF (with EPS split + EDLI/admin), ESI (with the wage-ceiling gate),
Professional Tax (state-aware slab lookup), TDS (manual), monthly LWP
proration, incentive merge, and advance recovery.

Every function here is PURE (no DB, no I/O) so it is exhaustively unit
tested and reused by the payroll router. Rounding follows the regulators:
EPFO rounds each PF contribution to the nearest rupee; ESIC rounds each ESI
contribution UP to the next rupee.
"""

from __future__ import annotations

import math
from typing import Optional

# ---------------------------------------------------------------------------
# Statutory constants (FY 2025-26 conventions; verify against current rules)
# ---------------------------------------------------------------------------

PF_EMPLOYEE_RATE = 0.12
PF_EMPLOYER_RATE = 0.12
EPS_RATE = 0.0833  # employer's pension portion
EPS_WAGE_CEILING = 15000  # EPS is always capped at this monthly wage
PF_WAGE_CEILING = 15000  # PF contributory wage cap when ceiling is applied
EDLI_RATE = 0.005  # employer EDLI (A/c 21), on the EPS-capped wage
PF_ADMIN_RATE = 0.005  # employer PF admin charges (A/c 2)

ESI_EMPLOYEE_RATE = 0.0075
ESI_EMPLOYER_RATE = 0.0325
ESI_WAGE_CEILING = 21000  # ESI applies only when monthly gross <= this

DAYS_BASIS_DEFAULT = 30  # 30-day proration basis

# Map a store's state (full name / GST state code / 2-letter) -> PT slab code.
STATE_TO_PT_CODE = {
    "jharkhand": "JH",
    "jh": "JH",
    "20": "JH",
    "maharashtra": "MH",
    "mh": "MH",
    "27": "MH",
}


# ---------------------------------------------------------------------------
# Professional Tax slabs (editable defaults; the accountant must verify)
# ---------------------------------------------------------------------------

DEFAULT_PT_SLABS = {
    "MH": {
        "state_code": "MH",
        "state_name": "Maharashtra",
        "basis": "MONTHLY",
        "gender_aware": True,
        "slabs": [
            {"min": 0, "max": 7500, "amount": 0, "gender": "MALE"},
            {"min": 7500.01, "max": 10000, "amount": 175, "gender": "MALE"},
            {
                "min": 10000.01,
                "max": None,
                "amount": 200,
                "amount_february": 300,
                "gender": "MALE",
            },
            {"min": 0, "max": 25000, "amount": 0, "gender": "FEMALE"},
            {
                "min": 25000.01,
                "max": None,
                "amount": 200,
                "amount_february": 300,
                "gender": "FEMALE",
            },
        ],
        "notes": "EDITABLE default - verify current Maharashtra PT. Women nil up to 25,000; +100 in February for the top slab.",
    },
    "JH": {
        "state_code": "JH",
        "state_name": "Jharkhand",
        "basis": "ANNUAL",
        "gender_aware": False,
        "slabs": [
            {"min": 0, "max": 300000, "amount": 0},
            {"min": 300000.01, "max": 500000, "amount": 100},
            {"min": 500000.01, "max": 800000, "amount": 150},
            {"min": 800000.01, "max": 1000000, "amount": 175},
            {"min": 1000000.01, "max": None, "amount": 208},
        ],
        "notes": "EDITABLE default - verify current Jharkhand PT. Annual gross basis; ~2,500/yr cap.",
    },
}


def pt_code_for_state(state: Optional[str]) -> Optional[str]:
    """Normalize a store's state (name / GST code / 2-letter) to a PT slab code."""
    if not state:
        return None
    return STATE_TO_PT_CODE.get(str(state).strip().lower())


def pt_for(
    slab_doc: Optional[dict], monthly_gross: float, month: int, gender: str = "ANY"
) -> float:
    """Resolve the monthly Professional Tax from a state's slab doc.

    Annualizes gross when the state's basis is ANNUAL; applies the February
    override when present. Gender-aware states default unknown gender to the
    general (male) slab.
    """
    if not slab_doc:
        return 0.0
    slabs = slab_doc.get("slabs") or []
    basis = (slab_doc.get("basis") or "MONTHLY").upper()
    income = (monthly_gross * 12) if basis == "ANNUAL" else monthly_gross
    gender = (gender or "ANY").upper()
    if slab_doc.get("gender_aware") and gender == "ANY":
        gender = "MALE"
    for slab in slabs:
        s_gender = (slab.get("gender") or "ANY").upper()
        if s_gender != "ANY" and s_gender != gender:
            continue
        lo = slab.get("min", 0) or 0
        hi = slab.get("max", None)
        if income >= lo and (hi is None or income <= hi):
            amount = slab.get("amount", 0) or 0
            if month == 2 and slab.get("amount_february") is not None:
                amount = slab.get("amount_february")
            return float(amount)
    return 0.0


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def _earnings(config: dict) -> dict:
    """Full (un-prorated) monthly earning components + gross.

    Raises ValueError if any named earning component is negative -- negative
    wages indicate bad data and must be caught at the source rather than
    silently producing a negative gross/net ("Fail Loudly" rule from
    SYSTEM_INTENT). Zero is allowed (e.g. zero-allowance structures).
    """
    basic = float(config.get("basic", 0) or 0)
    hra = float(config.get("hra", 0) or 0)
    conveyance = float(config.get("conveyance", 0) or 0)
    medical = float(config.get("medical", 0) or 0)
    special = float(config.get("special_allowance", 0) or 0)
    other_list = config.get("other_allowances") or []
    other = sum(float(a.get("amount", 0) or 0) for a in other_list)

    _components = {
        "basic": basic,
        "hra": hra,
        "conveyance": conveyance,
        "medical": medical,
        "special_allowance": special,
        "other_allowances": other,
    }
    for name, val in _components.items():
        if val < 0:
            raise ValueError(
                "Negative earning component rejected: %s=%.2f for employee '%s'. "
                "Fix the salary config before running payroll."
                % (name, val, config.get("employee_id", "unknown"))
            )

    return {
        "basic": basic,
        "hra": hra,
        "conveyance": conveyance,
        "medical": medical,
        "special_allowance": special,
        "other_allowances": other,
        "gross": basic + hra + conveyance + medical + special + other,
    }


def compute_pf(earned_basic: float, *, ceiling_cap: bool = True) -> dict:
    """EPF split: employee 12%, employer EPS 8.33% (capped 15k) + EPF balance,
    plus employer EDLI (0.5%) and admin (0.5%). EPFO rounds to nearest rupee."""
    contributory = min(earned_basic, PF_WAGE_CEILING) if ceiling_cap else earned_basic
    eps_wage = min(contributory, EPS_WAGE_CEILING)
    # Integer-basis arithmetic keeps half-rupee rounding deterministic and
    # float-drift free (e.g. EPS on 15000 = 1249.5 -> 1250).
    employee = round(contributory * 12 / 100)
    employer_total = round(contributory * 12 / 100)
    employer_eps = round(eps_wage * 833 / 10000)
    employer_epf = employer_total - employer_eps
    edli = round(eps_wage * 5 / 1000)
    admin = round(contributory * 5 / 1000)
    return {
        "contributory_wage": round(contributory, 2),
        "employee": float(employee),
        "employer_epf": float(employer_epf),
        "employer_eps": float(employer_eps),
        "edli": float(edli),
        "admin": float(admin),
        "employer_total": float(employer_eps + employer_epf + edli + admin),
    }


def esi_eligible(config: dict, full_gross: float) -> bool:
    """ESI applies when explicitly on, or (auto) when monthly gross <= ceiling."""
    flag = config.get("esi_applicable", None)
    if flag is True:
        return True
    if flag is False:
        return False
    return full_gross <= ESI_WAGE_CEILING


def compute_payroll(
    config: dict,
    *,
    month: int,
    year: int,
    lwp_days: float = 0.0,
    incentive: float = 0.0,
    advance_recovery: float = 0.0,
    pt_slab: Optional[dict] = None,
    gender: str = "ANY",
    days_basis: int = DAYS_BASIS_DEFAULT,
) -> dict:
    """Compute one employee's monthly payslip breakdown.

    Earnings are prorated on a `days_basis`-day basis by `lwp_days`. Statutory
    contributions are computed on the EARNED (prorated) wages; ESI eligibility
    and the PT slab bracket are determined on the FULL contracted gross.
    Incentive is paid on top of gross and is NOT part of the PF/ESI/PT base.
    """
    e = _earnings(config)
    full_gross = e["gross"]

    lwp = max(0.0, float(lwp_days or 0))
    paid_days = max(0.0, float(days_basis) - lwp)
    factor = (paid_days / days_basis) if days_basis else 1.0
    factor = min(1.0, max(0.0, factor))

    # Multiply-before-divide + round to paise keeps proration float-drift free.
    def _prorate(x: float) -> float:
        return round(x * paid_days / days_basis, 2) if days_basis else round(x, 2)

    earned_basic = _prorate(e["basic"])
    earned_gross = _prorate(full_gross)

    # PF (on earned basic)
    pf_applicable = config.get("pf_applicable", True)
    if pf_applicable and earned_basic > 0:
        pf = compute_pf(
            earned_basic, ceiling_cap=config.get("pf_wage_ceiling_cap", True)
        )
    else:
        pf = {
            "contributory_wage": 0.0,
            "employee": 0.0,
            "employer_epf": 0.0,
            "employer_eps": 0.0,
            "edli": 0.0,
            "admin": 0.0,
            "employer_total": 0.0,
        }

    # ESI (on earned gross; eligibility on full gross)
    esi_on = esi_eligible(config, full_gross)
    # round to paise before ceil so float drift can't push 81.0 -> 82.
    esi_employee = (
        float(math.ceil(round(earned_gross * 75 / 10000, 2))) if esi_on else 0.0
    )
    esi_employer = (
        float(math.ceil(round(earned_gross * 325 / 10000, 2))) if esi_on else 0.0
    )

    # Professional Tax: bracket determined by the full contracted gross, but
    # only charged when salary is actually earned this month (no pay -> no PT).
    pt = 0.0
    if config.get("pt_applicable", True) and pt_slab and earned_gross > 0:
        pt = pt_for(pt_slab, full_gross, month, gender)

    # TDS (manual monthly figure)
    tds = float(config.get("tds_monthly", 0) or 0)

    incentive = float(incentive or 0)
    advance_recovery = float(advance_recovery or 0)

    total_earnings = earned_gross + incentive
    total_deductions = pf["employee"] + esi_employee + pt + tds + advance_recovery
    net_pay = total_earnings - total_deductions
    employer_cost = total_earnings + pf["employer_total"] + esi_employer

    return {
        "employee_id": config.get("employee_id"),
        "month": month,
        "year": year,
        "days_basis": days_basis,
        "lwp_days": round(lwp, 2),
        "paid_days": round(paid_days, 2),
        "proration_factor": round(factor, 4),
        "earnings": {
            "basic": round(e["basic"] * factor, 2),
            "hra": round(e["hra"] * factor, 2),
            "conveyance": round(e["conveyance"] * factor, 2),
            "medical": round(e["medical"] * factor, 2),
            "special_allowance": round(e["special_allowance"] * factor, 2),
            "other_allowances": round(e["other_allowances"] * factor, 2),
            "full_gross": round(full_gross, 2),
            "earned_gross": round(earned_gross, 2),
            "incentive": round(incentive, 2),
            "total_earnings": round(total_earnings, 2),
        },
        "deductions": {
            "pf_employee": pf["employee"],
            "esi_employee": float(esi_employee),
            "professional_tax": round(pt, 2),
            "tds": round(tds, 2),
            "advance_recovery": round(advance_recovery, 2),
            "total_deductions": round(total_deductions, 2),
        },
        "pf_wage": pf["contributory_wage"],  # EPF contributory wage (for the PF ECR)
        "employer_contributions": {
            "pf_employer_epf": pf["employer_epf"],
            "pf_employer_eps": pf["employer_eps"],
            "pf_edli": pf["edli"],
            "pf_admin": pf["admin"],
            "pf_employer_total": pf["employer_total"],
            "esi_employer": float(esi_employer),
            "total": round(pf["employer_total"] + esi_employer, 2),
        },
        "esi_applicable": esi_on,
        "pf_applicable": bool(pf_applicable and earned_basic > 0),
        "net_pay": round(net_pay, 2),
        "ctc_cost": round(employer_cost, 2),
    }
