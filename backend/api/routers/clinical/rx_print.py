"""Prescription A5 print card + redo tracking.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Path
from fastapi.responses import HTMLResponse
from typing import Optional
from datetime import datetime
from ...utils.ist import ist_date_str
from html import escape as _html_escape
import uuid
from ..auth import get_current_user, require_roles
from ...dependencies import get_prescription_repository, get_store_repository
from ...services.rx_print_values import (
    first_present_rx_value,
    is_absent_rx_value,
    format_axis_value,
    format_rx_value,
)
from ._shared import _REDO_ROLES, router
from .models import RedoCreate


# ============================================================================
# PRESCRIPTION PRINT (A5 card) + REDO TRACKING
# ============================================================================


def _eye_block(rx: dict, key: str) -> dict:
    """Normalise a per-eye block. Prescriptions store eyes under several shapes:
    right_eye/left_eye with sph/cyl/axis/add (the auto-created Rx in
    complete_test) or sphere/cylinder. Return a dict of raw values; rendering
    is left to format_rx_value / format_axis_value."""
    eye = rx.get(key) or {}
    if not isinstance(eye, dict):
        eye = {}
    return {
        "sph": eye.get("sph", eye.get("sphere")),
        "cyl": eye.get("cyl", eye.get("cylinder")),
        "axis": eye.get("axis"),
        "add": eye.get("add"),
        "pd": eye.get("pd"),
    }


def _rx_date(rx: dict) -> str:
    """Best-effort human date for the Rx (prescription_date / test_date / created_at).

    BUG-104, VALUE rule, on the legacy created_at fallback leg: this date is
    PRINTED on the patient's Rx card. prescription_date / test_date are IST
    business-date strings (frame-less -- their parse lands on midnight, which
    the +5:30 shift leaves on the same day). created_at is a stored instant
    on the UTC wall clock (BSON datetime, or an aware 'Z' string on imported
    rows), so formatting it raw printed YESTERDAY's date on a card for an eye
    test recorded 00:00-05:30 IST. ist_date_str shifts datetimes to the IST
    calendar day; a NAIVE ISO string still passes through unshifted by
    documented design (no reliable frame), matching the old behaviour.
    """
    raw = rx.get("prescription_date") or rx.get("test_date") or rx.get("created_at")
    if raw is None:
        return ""
    if isinstance(raw, datetime):
        return datetime.strptime(ist_date_str(raw), "%Y-%m-%d").strftime("%d %b %Y")
    if isinstance(raw, str):
        # ISO strings -> dd Mon YYYY; fall back to the raw string on parse fail.
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                # An offset-bearing string has an exact frame: use it.
                return datetime.strptime(
                    ist_date_str(parsed), "%Y-%m-%d"
                ).strftime("%d %b %Y")
            return parsed.strftime("%d %b %Y")
        except ValueError:
            return raw[:10]
    return str(raw)


def _build_rx_card_html(rx: dict, store: Optional[dict]) -> str:
    """Build a self-contained, printable A5 Rx card (no external assets).

    PURE-ish: depends only on its two dict args, so it renders deterministically
    for a given prescription + store. All dynamic text is HTML-escaped.
    """
    store = store or {}

    def esc(v) -> str:
        return _html_escape("" if v is None else str(v))

    clinic_name = (
        store.get("store_name")
        or store.get("storeName")
        or store.get("name")
        or "Eye Clinic"
    )
    addr_parts = [
        store.get("address"),
        store.get("city"),
        store.get("state"),
        store.get("pincode"),
    ]
    clinic_addr = ", ".join(str(p) for p in addr_parts if p)
    clinic_phone = store.get("phone") or store.get("contact_number") or ""
    clinic_gstin = store.get("gstin") or ""

    patient_name = (
        rx.get("patient_name")
        or rx.get("patientName")
        or rx.get("customer_name")
        or "Patient"
    )
    age = rx.get("patient_age", rx.get("age"))
    phone = rx.get("customer_phone") or rx.get("patient_phone") or rx.get("phone")
    optometrist = rx.get("optometrist_name") or rx.get("optometristName") or ""
    rx_number = rx.get("prescription_number") or rx.get("prescription_id") or ""
    rx_date = _rx_date(rx)

    od = _eye_block(rx, "right_eye")
    os_ = _eye_block(rx, "left_eye")

    # PD: prefer a top-level pd, else fall back to per-eye pd values.
    #
    # This block used to read `if pd in (None, "", 0)` then `od.get("pd") or
    # os_.get("pd")`, which failed three ways on a patient's card:
    #   * a stored junk string ("None") is truthy, so it won the `or` and the
    #     card printed "PD: None mm";
    #   * a REAL top-level PD of 0 was in the discard tuple and was thrown away;
    #   * `or` between the eyes swallowed a REAL right-eye 0 and silently
    #     printed the LEFT eye's number in its place.
    # `first_present_rx_value` keeps the same preference order while treating
    # only genuinely-absent values as absent. A PD of 0 is real clinical data.
    pd = first_present_rx_value(rx.get("pd"), od.get("pd"), os_.get("pd"))
    pd_str = "" if is_absent_rx_value(pd) else str(pd)

    def row(label: str, eye: dict) -> str:
        return (
            "<tr>"
            f"<td class='eye'>{esc(label)}</td>"
            f"<td>{esc(format_rx_value(eye['sph']))}</td>"
            f"<td>{esc(format_rx_value(eye['cyl']))}</td>"
            f"<td>{esc(format_axis_value(eye['axis']))}</td>"
            f"<td>{esc(format_rx_value(eye['add']))}</td>"
            "</tr>"
        )

    # Optional meta lines built only when present.
    age_html = f"<span><b>Age:</b> {esc(age)}</span>" if age not in (None, "") else ""
    phone_html = (
        f"<span><b>Phone:</b> {esc(phone)}</span>" if phone not in (None, "") else ""
    )
    # `if pd_str` would be a truthiness test again; "0" is truthy so it happens
    # to work, but the explicit form is the one that stays correct.
    pd_html = (
        f"<div class='pd'><b>PD:</b> {esc(pd_str)} mm</div>" if pd_str != "" else ""
    )
    gstin_html = (
        f"<div class='clinic-gstin'>GSTIN: {esc(clinic_gstin)}</div>"
        if clinic_gstin
        else ""
    )
    addr_html = (
        f"<div class='clinic-addr'>{esc(clinic_addr)}</div>" if clinic_addr else ""
    )
    phone_line_html = (
        f"<div class='clinic-phone'>Ph: {esc(clinic_phone)}</div>"
        if clinic_phone
        else ""
    )
    rxno_html = (
        f"<span class='rx-no'>Rx No: {esc(rx_number)}</span>" if rx_number else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Prescription {esc(rx_number)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: Arial, Helvetica, sans-serif;
    color: #111827;
    margin: 0;
    background: #f3f4f6;
  }}
  .card {{
    width: 148mm;
    min-height: 210mm;
    margin: 8mm auto;
    padding: 12mm;
    background: #ffffff;
    border: 1px solid #e5e7eb;
  }}
  .clinic {{ text-align: center; border-bottom: 2px solid #111827; padding-bottom: 6px; }}
  .clinic-name {{ font-size: 18px; font-weight: bold; }}
  .clinic-addr, .clinic-phone, .clinic-gstin {{ font-size: 11px; color: #4b5563; }}
  .doc-title {{
    text-align: center; font-size: 13px; font-weight: bold;
    letter-spacing: 2px; margin: 10px 0 6px;
  }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: 12px; margin-bottom: 4px; }}
  .meta-row {{ display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 8px; }}
  table.rx {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
  table.rx th, table.rx td {{
    border: 1px solid #9ca3af; padding: 6px 4px; text-align: center; font-size: 13px;
  }}
  table.rx th {{ background: #f9fafb; font-size: 11px; }}
  table.rx td.eye {{ text-align: left; font-weight: bold; }}
  .pd {{ font-size: 12px; margin: 6px 0; }}
  .footer {{ margin-top: 18mm; display: flex; justify-content: space-between; align-items: flex-end; }}
  .sign {{ text-align: center; font-size: 11px; }}
  .sign .line {{ border-top: 1px solid #111827; width: 48mm; margin-bottom: 2px; }}
  .rx-no {{ color: #6b7280; }}
  @media print {{
    @page {{ size: A5; margin: 0; }}
    body {{ background: #ffffff; }}
    .card {{ margin: 0; border: none; }}
  }}
</style>
</head>
<body onload="window.print()">
  <div class="card">
    <div class="clinic">
      <div class="clinic-name">{esc(clinic_name)}</div>
      {addr_html}
      {phone_line_html}
      {gstin_html}
    </div>
    <div class="doc-title">PRESCRIPTION</div>
    <div class="meta-row">
      <span><b>Patient:</b> {esc(patient_name)}</span>
      {rxno_html}
    </div>
    <div class="meta">
      {age_html}
      {phone_html}
      <span><b>Date:</b> {esc(rx_date)}</span>
    </div>
    <table class="rx">
      <thead>
        <tr><th>Eye</th><th>SPH</th><th>CYL</th><th>AXIS</th><th>ADD</th></tr>
      </thead>
      <tbody>
        {row("Right (OD)", od)}
        {row("Left (OS)", os_)}
      </tbody>
    </table>
    {pd_html}
    <div class="footer">
      <div></div>
      <div class="sign">
        <div class="line"></div>
        <div>{esc(optometrist) or "Optometrist"}</div>
      </div>
    </div>
  </div>
</body>
</html>"""


@router.get("/prescriptions/{prescription_id}/print")
async def print_prescription(
    prescription_id: str = Path(...),
    current_user: dict = Depends(get_current_user),
):
    """Return a self-contained, printable A5 Rx card (read-only).

    Any authenticated clinical/POS user can print. Fail-soft: if the DB is
    unavailable we still return a minimal valid card rather than 500-ing, so a
    degraded backend never blocks a counter staffer from handing a patient a
    printout.
    """
    rx_repo = get_prescription_repository()
    if rx_repo is None:
        # DB down: render an empty-but-valid card so the print window opens.
        return HTMLResponse(
            _build_rx_card_html({"prescription_id": prescription_id}, None)
        )

    rx = rx_repo.find_by_id(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    store = None
    store_repo = get_store_repository()
    store_id = rx.get("store_id")
    if store_repo is not None and store_id:
        try:
            store = store_repo.find_by_id(store_id)
        except Exception:
            store = None  # Header is optional; never block the printout.

    return HTMLResponse(_build_rx_card_html(rx, store))


@router.post("/prescriptions/{prescription_id}/redo")
async def create_prescription_redo(
    prescription_id: str = Path(...),
    body: RedoCreate = ...,
    current_user: dict = Depends(require_roles(*_REDO_ROLES)),
):
    """Record a redo against a prescription (lens remake / re-dispense).

    Appends to the prescription's `redos` array AND stamps the latest-redo
    fields (redo_of / redo_reason / redo_by / redo_at) so both an audit list
    and a quick "was this redone?" check are cheap. Gated to optometry/manager
    roles.
    """
    rx_repo = get_prescription_repository()
    if rx_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    rx = rx_repo.find_by_id(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    now = datetime.utcnow()
    redo_entry = {
        "redo_id": str(uuid.uuid4()),
        "reason": body.reason,
        "redo_by": current_user.get("user_id", ""),
        "redo_by_name": current_user.get("full_name", current_user.get("username", "")),
        "redo_at": now.isoformat(),
    }

    existing = rx.get("redos") or []
    if not isinstance(existing, list):
        existing = []
    updated = existing + [redo_entry]

    ok = rx_repo.update(
        prescription_id,
        {
            "redos": updated,
            "redo_count": len(updated),
            # Latest-redo shortcut fields (linked back to the original Rx).
            "redo_of": prescription_id,
            "redo_reason": body.reason,
            "redo_by": redo_entry["redo_by"],
            "redo_at": redo_entry["redo_at"],
        },
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to record redo")

    return {
        "message": "Redo recorded",
        "prescriptionId": prescription_id,
        "redo": redo_entry,
        "redoCount": len(updated),
    }


@router.get("/prescriptions/{prescription_id}/redos")
async def list_prescription_redos(
    prescription_id: str = Path(...),
    current_user: dict = Depends(get_current_user),
):
    """Return the redo history for a prescription (most recent last)."""
    rx_repo = get_prescription_repository()
    if rx_repo is None:
        return {"redos": [], "total": 0}

    rx = rx_repo.find_by_id(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    redos = rx.get("redos") or []
    if not isinstance(redos, list):
        redos = []
    return {"redos": redos, "total": len(redos)}
