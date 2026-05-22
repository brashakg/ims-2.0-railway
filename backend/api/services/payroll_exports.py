"""
IMS 2.0 - Payroll Exports (pure)
================================
Builds the month-end outputs from computed payroll rows (run docs that carry a
`breakdown`): a statutory summary, a balanced Tally salary Journal Voucher
(XML), the EPFO PF ECR text file, and a branded HTML payslip.

All functions are PURE (no DB) so they are unit tested and reused by the
payroll router.
"""

from __future__ import annotations

from typing import Iterable, Optional
from xml.sax.saxutils import escape

EPS_WAGE_CEILING = 15000


def _b(row: dict) -> dict:
    return row.get("breakdown") or {}


def statutory_summary(rows: Iterable[dict]) -> dict:
    """Aggregate PF/ESI/PT/TDS + gross/net/employer-cost across payroll rows."""
    s = {
        "count": 0, "gross": 0.0, "pf_employee": 0.0, "pf_employer": 0.0,
        "esi_employee": 0.0, "esi_employer": 0.0, "professional_tax": 0.0,
        "tds": 0.0, "advance_recovery": 0.0, "net": 0.0, "employer_cost": 0.0,
    }
    for r in rows:
        b = _b(r)
        ded = b.get("deductions", {})
        emp = b.get("employer_contributions", {})
        earn = b.get("earnings", {})
        s["count"] += 1
        s["gross"] += earn.get("total_earnings", 0) or 0
        s["pf_employee"] += ded.get("pf_employee", 0) or 0
        s["pf_employer"] += emp.get("pf_employer_total", 0) or 0
        s["esi_employee"] += ded.get("esi_employee", 0) or 0
        s["esi_employer"] += emp.get("esi_employer", 0) or 0
        s["professional_tax"] += ded.get("professional_tax", 0) or 0
        s["tds"] += ded.get("tds", 0) or 0
        s["advance_recovery"] += ded.get("advance_recovery", 0) or 0
        s["net"] += b.get("net_pay", r.get("net_salary", 0)) or 0
        s["employer_cost"] += b.get("ctc_cost", 0) or 0
    out = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()}
    out["pf_total_payable"] = round(out["pf_employee"] + out["pf_employer"], 2)
    out["esi_total_payable"] = round(out["esi_employee"] + out["esi_employer"], 2)
    return out


def build_salary_jv_xml(
    entity: Optional[dict], rows: list, month: int, year: int
) -> str:
    """Build a balanced Tally Journal Voucher (XML) for the month's salary.

    Tally convention: debits carry ISDEEMEDPOSITIVE=Yes + negative AMOUNT,
    credits ISDEEMEDPOSITIVE=No + positive AMOUNT. The voucher always balances
    (any rounding residual is absorbed into Salary Payable).
    """
    s = statutory_summary(rows)
    entity_name = (entity or {}).get("name", "Entity")
    date_str = f"{year:04d}{month:02d}01"

    # (ledger, amount, is_debit) — debits negative, credits positive.
    raw = [
        ("Salaries & Wages", -s["gross"], True),
        ("Employer PF Contribution", -s["pf_employer"], True),
        ("Employer ESI Contribution", -s["esi_employer"], True),
        ("PF Payable", s["pf_total_payable"], False),
        ("ESI Payable", s["esi_total_payable"], False),
        ("Professional Tax Payable", s["professional_tax"], False),
        ("TDS Payable", s["tds"], False),
        ("Salary Advance", s["advance_recovery"], False),
        ("Salary Payable", s["net"], False),
    ]
    entries = [(n, round(a, 2), d) for (n, a, d) in raw if abs(a) >= 0.005]

    # Force exact balance: nudge Salary Payable by any rounding residual.
    residual = round(sum(a for (_, a, _) in entries), 2)
    if abs(residual) >= 0.005:
        for i, (n, a, d) in enumerate(entries):
            if n == "Salary Payable":
                entries[i] = (n, round(a - residual, 2), d)
                break

    led_xml = []
    for name, amount, is_debit in entries:
        led_xml.append(
            "<ALLLEDGERENTRIES.LIST>"
            f"<LEDGERNAME>{escape(name)}</LEDGERNAME>"
            f"<ISDEEMEDPOSITIVE>{'Yes' if is_debit else 'No'}</ISDEEMEDPOSITIVE>"
            f"<AMOUNT>{amount:.2f}</AMOUNT>"
            "</ALLLEDGERENTRIES.LIST>"
        )
    narration = escape(f"Salary for {month:02d}/{year} - {entity_name}")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>"
        "<BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>"
        "<REQUESTDATA><TALLYMESSAGE>"
        '<VOUCHER VCHTYPE="Journal" ACTION="Create">'
        f"<DATE>{date_str}</DATE>"
        f"<NARRATION>{narration}</NARRATION>"
        "<VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>"
        f"{''.join(led_xml)}"
        "</VOUCHER></TALLYMESSAGE></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"
    )


def build_pf_ecr(rows: list, config_by_emp: Optional[dict] = None) -> str:
    """EPFO ECR text: 11 '#~#'-delimited fields per PF-eligible employee."""
    config_by_emp = config_by_emp or {}
    lines = []
    for r in rows:
        b = _b(r)
        if not b.get("pf_applicable"):
            continue
        emp = r.get("employee_id")
        earn = b.get("earnings", {})
        ded = b.get("deductions", {})
        empc = b.get("employer_contributions", {})
        pf_wage = b.get("pf_wage", 0) or 0
        eps_wage = min(pf_wage, EPS_WAGE_CEILING)
        cfg = config_by_emp.get(emp, {}) or {}
        fields = [
            cfg.get("uan", "") or "",
            r.get("employee_name", "") or emp,
            int(round(earn.get("earned_gross", 0) or 0)),    # gross wages
            int(round(pf_wage)),                              # EPF wages
            int(round(eps_wage)),                             # EPS wages
            int(round(eps_wage)),                             # EDLI wages
            int(round(ded.get("pf_employee", 0) or 0)),      # EPF contribution
            int(round(empc.get("pf_employer_eps", 0) or 0)), # EPS contribution
            int(round(empc.get("pf_employer_epf", 0) or 0)), # EPF-EPS diff
            int(round(b.get("lwp_days", 0) or 0)),           # NCP days
            0,                                               # refund of advances
        ]
        lines.append("#~#".join(str(f) for f in fields))
    return "\n".join(lines)


def _inr(n) -> str:
    try:
        return f"Rs. {round(float(n or 0)):,}"
    except Exception:
        return "Rs. 0"


def build_payslip_html(row: dict, entity: Optional[dict], employee: Optional[dict]) -> str:
    """Branded, printable HTML payslip for one computed payroll row."""
    b = _b(row)
    earn = b.get("earnings", {})
    ded = b.get("deductions", {})
    empc = b.get("employer_contributions", {})
    entity_name = escape((entity or {}).get("name", "") or "Payslip")
    emp_name = escape(row.get("employee_name", "") or (employee or {}).get("full_name", "") or row.get("employee_id", ""))
    designation = escape((employee or {}).get("designation", "") or "")
    month = row.get("month") or b.get("month") or 0
    year = row.get("year") or b.get("year") or 0

    def erow(label, value):
        return f"<tr><td>{escape(label)}</td><td style='text-align:right'>{_inr(value)}</td></tr>"

    earnings_rows = "".join([
        erow("Basic", earn.get("basic")),
        erow("HRA", earn.get("hra")),
        erow("Conveyance", earn.get("conveyance")),
        erow("Medical", earn.get("medical")),
        erow("Special Allowance", earn.get("special_allowance")),
        erow("Incentive", earn.get("incentive")),
    ])
    deduction_rows = "".join([
        erow("PF (Employee)", ded.get("pf_employee")),
        erow("ESI", ded.get("esi_employee")),
        erow("Professional Tax", ded.get("professional_tax")),
        erow("TDS", ded.get("tds")),
        erow("Advance Recovery", ded.get("advance_recovery")),
    ])
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Payslip - {emp_name}</title>
<style>
  body {{ font-family: Arial, sans-serif; color:#1f2937; max-width:720px; margin:24px auto; }}
  h1 {{ font-size:18px; margin:0; }}
  .sub {{ color:#6b7280; font-size:12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ padding:6px 8px; border-bottom:1px solid #eee; }}
  .cols {{ display:flex; gap:24px; }} .cols > div {{ flex:1; }}
  .net {{ display:flex; justify-content:space-between; border-top:2px solid #111; margin-top:12px; padding-top:8px; font-weight:bold; font-size:15px; }}
  .head {{ border-bottom:2px solid #111; padding-bottom:8px; margin-bottom:12px; }}
  h3 {{ font-size:12px; color:#6b7280; text-transform:uppercase; }}
  @media print {{ body {{ margin:0; }} }}
</style></head>
<body>
  <div class="head">
    <h1>{entity_name}</h1>
    <div class="sub">Payslip — {month:02d}/{year}</div>
  </div>
  <p><strong>{emp_name}</strong>{(' · ' + designation) if designation else ''}<br>
     <span class="sub">Employee ID: {escape(row.get('employee_id',''))} · LWP: {b.get('lwp_days',0)} day(s)</span></p>
  <div class="cols">
    <div>
      <h3>Earnings</h3>
      <table>{earnings_rows}
        <tr><td><strong>Gross</strong></td><td style="text-align:right"><strong>{_inr(earn.get('total_earnings'))}</strong></td></tr>
      </table>
    </div>
    <div>
      <h3>Deductions</h3>
      <table>{deduction_rows}
        <tr><td><strong>Total</strong></td><td style="text-align:right"><strong>{_inr(ded.get('total_deductions'))}</strong></td></tr>
      </table>
    </div>
  </div>
  <div class="net"><span>Net Pay</span><span>{_inr(b.get('net_pay'))}</span></div>
  <p class="sub">Employer cost (CTC): {_inr(b.get('ctc_cost'))} ·
     Employer PF {_inr(empc.get('pf_employer_total'))} · Employer ESI {_inr(empc.get('esi_employer'))}</p>
  <p class="sub">This is a computer-generated payslip.</p>
</body></html>"""
