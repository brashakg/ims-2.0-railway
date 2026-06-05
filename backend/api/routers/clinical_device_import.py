"""
IMS 2.0 - Clinical Device Import Router (CLI-12)
=================================================
Ophthalmic device integration scaffolding: autorefractor / lensmeter -> Rx import.

Provides ONE endpoint:

  POST /api/v1/clinical/device-import

Role gate: same clinical-write roles (OPTOMETRIST, STORE_MANAGER, ADMIN, SUPERADMIN).

The endpoint accepts a multipart CSV file (the most common export from Topcon,
Nidek, Zeiss, Huvitz autorefractors and lensmeters) and maps it to the validated
IMS Rx shape (SPH / CYL / AXIS / ADD per CLAUDE.md §Non-negotiable business rules).

Design:
  - PURE mapper: no DB write. The caller receives the parsed + validated Rx and
    may then POST to /api/v1/prescriptions (or display it for optometrist review).
  - FAIL-SOFT gate: if no device_config key is found in the store's config (or if
    DB is absent), returns 400 with an actionable guidance message rather than 500.
  - Execution owner-gated on the actual device fleet: without a physical device
    generating real CSVs this is a scaffolding seam; the parser is tested against
    the common Topcon/Nidek CSV layout.

Supported CSV layouts
---------------------
Two widely-used formats are accepted (auto-detected by header row):

  A. TOPCON/NIDEK column layout (most common; exported as .csv):
     Date, Time, ID, R-SPH, R-CYL, R-AXIS, R-ADD, L-SPH, L-CYL, L-AXIS, L-ADD, ...

  B. HUVITZ / ZEISS two-section layout (two stacked rows: RIGHT and LEFT):
     Eye, SPH, CYL, AXIS, ADD
     R,   ...
     L,   ...

Both are normalised to the same EyeRx output model. Unknown or malformed CSVs
produce a descriptive 400 so the optometrist knows how to fix the file.

No emojis in Python (Windows cp1252 safety).
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from .auth import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Role gate -- mirrors clinical write roles in clinical.py _CLINICAL_ROLES.
# SUPERADMIN auto-passes via require_roles.
# ---------------------------------------------------------------------------
_DEVICE_IMPORT_ROLES = ("ADMIN", "STORE_MANAGER", "OPTOMETRIST")

# ---------------------------------------------------------------------------
# Rx validation ranges (source of truth: CLAUDE.md + docs/SYSTEM_INTENT.md)
# SPH: -20.00 to +20.00  (0.25 steps)
# CYL: -6.00  to +6.00   (0.25 steps)
# AXIS: 1 to 180          (whole degrees)
# ADD: +0.75 to +3.50    (0.25 steps)
# ---------------------------------------------------------------------------
_RX_LIMITS = {
    "sph": (-20.0, 20.0),
    "cyl": (-6.0, 6.0),
    "add": (0.75, 3.50),
}
_STEP_FIELDS = {"sph", "cyl", "add"}


def _parse_rx_float(raw: str, field: str) -> Optional[float]:
    """Parse a device-exported Rx string to float and enforce IMS range + step.

    Returns None for empty / plano markers ("0", "", "DS", "SPH", "PL", "-").
    Raises ValueError with a human-readable message on any validation failure.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped in ("", "0", "0.00", "DS", "SPH", "PL", "-", "N/A", "n/a"):
        return None

    try:
        value = float(stripped)
    except ValueError:
        raise ValueError(
            f"Device exported '{raw}' for {field.upper()} -- expected a number. "
            "Check the device CSV export settings."
        )

    lo, hi = _RX_LIMITS.get(field, (-999.0, 999.0))
    if value < lo or value > hi:
        raise ValueError(
            f"{field.upper()} value {value} is outside the valid IMS range "
            f"({lo} to {hi}). Verify the device reading and re-export."
        )

    if field in _STEP_FIELDS:
        # Dioptric powers must be on the 0.25 grid.
        if round(value * 100) % 25 != 0:
            raise ValueError(
                f"{field.upper()} value {value} is not on the 0.25-diopter step "
                "(e.g. -1.25, 0.00, +2.50). The device may need calibration."
            )

    return value


def _parse_axis(raw: str) -> Optional[int]:
    """Parse and range-check an AXIS value (1-180 whole degrees)."""
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped in ("", "0", "-", "N/A", "n/a"):
        return None
    try:
        value = int(float(stripped))
    except ValueError:
        raise ValueError(
            f"AXIS value '{raw}' is not a whole number. "
            "Check the device CSV export."
        )
    if not (1 <= value <= 180):
        raise ValueError(
            f"AXIS value {value} is outside the valid range (1-180 degrees)."
        )
    return value


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

class EyeRx(BaseModel):
    """Validated per-eye Rx values from a device import."""
    sph: Optional[float] = None
    cyl: Optional[float] = None
    axis: Optional[int] = None
    add: Optional[float] = None


class DeviceImportResult(BaseModel):
    """Response from a successful device-CSV import."""
    format_detected: str
    right_eye: EyeRx
    left_eye: EyeRx
    raw_row_count: int
    warnings: list[str]


# ---------------------------------------------------------------------------
# CSV parsers (pure functions, fully unit-testable without FastAPI)
# ---------------------------------------------------------------------------

def _normalise_header(s: str) -> str:
    return s.strip().lower().replace("-", "_").replace(" ", "_")


# Format A column names (Topcon / Nidek standard export).
_FORMAT_A_REQUIRED = {"r_sph", "r_cyl", "r_axis", "l_sph", "l_cyl", "l_axis"}
# Format B requires an "eye" discriminator column.
_FORMAT_B_REQUIRED = {"eye", "sph", "cyl", "axis"}


def _parse_format_a(rows: list[dict]) -> tuple[EyeRx, EyeRx, list[str], int]:
    """Parse Topcon/Nidek column-per-eye layout.

    Expects at least one data row; if multiple rows exist (series/average), uses
    the LAST row (devices typically write the "final/averaged" reading last).
    Returns (right_eye, left_eye, warnings, raw_row_count).
    """
    warnings: list[str] = []
    raw_count = len(rows)

    if raw_count > 1:
        warnings.append(
            f"{raw_count} data rows found; using the last row "
            "(devices typically write the final/averaged reading last)."
        )

    row = {_normalise_header(k): v for k, v in rows[-1].items()}

    def _get(col: str) -> str:
        return row.get(col, "").strip()

    try:
        right = EyeRx(
            sph=_parse_rx_float(_get("r_sph"), "sph"),
            cyl=_parse_rx_float(_get("r_cyl"), "cyl"),
            axis=_parse_axis(_get("r_axis")),
            add=_parse_rx_float(_get("r_add"), "add") if _get("r_add") else None,
        )
        left = EyeRx(
            sph=_parse_rx_float(_get("l_sph"), "sph"),
            cyl=_parse_rx_float(_get("l_cyl"), "cyl"),
            axis=_parse_axis(_get("l_axis")),
            add=_parse_rx_float(_get("l_add"), "add") if _get("l_add") else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return right, left, warnings, raw_count


def _parse_format_b(rows: list[dict]) -> tuple[EyeRx, EyeRx, list[str], int]:
    """Parse Huvitz/Zeiss two-row layout (one row per eye, discriminated by 'Eye').

    Expects exactly two rows labelled 'R' and 'L' (or 'OD' and 'OS').
    Returns (right_eye, left_eye, warnings, raw_row_count).
    """
    warnings: list[str] = []
    raw_count = len(rows)
    normalised = [
        {_normalise_header(k): v.strip() for k, v in r.items()} for r in rows
    ]

    right_rows = [
        r for r in normalised if r.get("eye", "").upper() in ("R", "OD", "RIGHT")
    ]
    left_rows = [
        r for r in normalised if r.get("eye", "").upper() in ("L", "OS", "LEFT")
    ]

    if not right_rows or not left_rows:
        raise HTTPException(
            status_code=422,
            detail=(
                "Huvitz/Zeiss format requires one right-eye row (R/OD) and one "
                "left-eye row (L/OS). Check the device export."
            ),
        )

    if len(right_rows) > 1:
        warnings.append("Multiple right-eye rows; using the last one.")
    if len(left_rows) > 1:
        warnings.append("Multiple left-eye rows; using the last one.")

    def _mk(r: dict) -> EyeRx:
        try:
            return EyeRx(
                sph=_parse_rx_float(r.get("sph", ""), "sph"),
                cyl=_parse_rx_float(r.get("cyl", ""), "cyl"),
                axis=_parse_axis(r.get("axis", "")),
                add=_parse_rx_float(r.get("add", ""), "add") if r.get("add") else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    right = _mk(right_rows[-1])
    left = _mk(left_rows[-1])
    return right, left, warnings, raw_count


def parse_device_csv(content: bytes) -> DeviceImportResult:
    """Top-level CSV parser. Auto-detects format and returns a validated result.

    Raises HTTPException(400) for structural issues (empty, no header, wrong
    encoding) and HTTPException(422) for out-of-range Rx values -- so callers
    always see a clean error, never a 500.
    """
    # Decode -- devices export UTF-8 or Latin-1 (cp1252). Try UTF-8 first.
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not decode the uploaded file. "
                    "Please export the device data as UTF-8 or Latin-1 CSV."
                ),
            )

    text = text.strip()
    if not text:
        raise HTTPException(
            status_code=400,
            detail="The uploaded CSV file is empty. Re-export from the device.",
        )

    reader = csv.DictReader(io.StringIO(text))
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse the CSV: {exc}. Check the file format.",
        )

    if not rows:
        raise HTTPException(
            status_code=400,
            detail=(
                "The CSV file has a header but no data rows. "
                "Re-export from the device."
            ),
        )

    if reader.fieldnames is None:
        raise HTTPException(
            status_code=400,
            detail="The CSV file has no header row. Check the device export settings.",
        )

    headers = {_normalise_header(h) for h in reader.fieldnames if h}

    # Format A: Topcon/Nidek column-per-eye.
    if _FORMAT_A_REQUIRED.issubset(headers):
        right, left, warnings, raw_count = _parse_format_a(rows)
        return DeviceImportResult(
            format_detected="TOPCON_NIDEK_COLUMN",
            right_eye=right,
            left_eye=left,
            raw_row_count=raw_count,
            warnings=warnings,
        )

    # Format B: Huvitz/Zeiss per-eye rows.
    if _FORMAT_B_REQUIRED.issubset(headers):
        right, left, warnings, raw_count = _parse_format_b(rows)
        return DeviceImportResult(
            format_detected="HUVITZ_ZEISS_PER_EYE",
            right_eye=right,
            left_eye=left,
            raw_row_count=raw_count,
            warnings=warnings,
        )

    raise HTTPException(
        status_code=400,
        detail=(
            "Unrecognised device CSV format. "
            "Expected columns for Topcon/Nidek layout "
            "(R-SPH, R-CYL, R-AXIS, L-SPH, L-CYL, L-AXIS) "
            "or Huvitz/Zeiss layout (Eye, SPH, CYL, AXIS). "
            f"Detected headers: {sorted(headers)!r}."
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/device-import",
    response_model=DeviceImportResult,
    summary="Import autorefractor/lensmeter CSV -> validated Rx",
    description=(
        "Accepts a CSV file exported from a clinical ophthalmic device "
        "(autorefractor, lensmeter) and maps it to the validated IMS Rx shape. "
        "Supports Topcon/Nidek column-per-eye format and Huvitz/Zeiss per-eye-row "
        "format. The returned Rx has been range-validated (SPH -20..+20, "
        "CYL -6..+6, AXIS 1-180, ADD +0.75..+3.50, 0.25-diopter steps) but is "
        "NOT saved to the database -- the optometrist must review and confirm "
        "before posting to POST /api/v1/prescriptions. "
        "Requires OPTOMETRIST, STORE_MANAGER, or ADMIN role. "
        "Owner-gated: a real device fleet is required for live use."
    ),
    tags=["Clinical"],
)
async def device_import(
    file: UploadFile = File(
        ...,
        description=(
            "CSV file exported from an autorefractor or lensmeter device. "
            "Max recommended: 512 KB. Accepted encodings: UTF-8, Latin-1."
        ),
    ),
    current_user: dict = Depends(require_roles(*_DEVICE_IMPORT_ROLES)),
):
    """Parse and validate a device CSV, returning the IMS Rx shape.

    The endpoint is fail-soft on device-config absence: if the DB is unavailable
    or the store has no device_config, this parse step still runs so the
    optometrist can review the reading -- the DB gate is on the subsequent
    prescription save, not on the parse.

    A 400 is returned for:
    - No file content
    - Unrecognised format (with guidance on expected columns)
    - Encoding errors

    A 422 is returned for:
    - Rx values outside the IMS clinical range
    - Non-0.25-step dioptric powers
    - Non-integer or out-of-range axis values
    """
    if file.content_type and file.content_type not in (
        "text/csv",
        "text/plain",
        "application/csv",
        "application/octet-stream",
        "application/vnd.ms-excel",
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unexpected file type '{file.content_type}'. "
                "Upload a .csv file exported from the device."
            ),
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=400,
            detail=(
                "Uploaded file is empty. "
                "Re-export from the device and upload again."
            ),
        )

    # Size guard: 512 KB is more than enough for any real device CSV.
    if len(content) > 512 * 1024:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File is too large ({len(content) // 1024} KB). "
                "Device CSVs should be under 512 KB -- "
                "check the device export is not a full-history dump."
            ),
        )

    result = parse_device_csv(content)
    logger.info(
        "[DEVICE_IMPORT] format=%s rows=%d user=%s store=%s",
        result.format_detected,
        result.raw_row_count,
        current_user.get("username", "unknown"),
        current_user.get("active_store_id", "unknown"),
    )
    return result
