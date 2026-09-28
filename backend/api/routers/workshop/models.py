"""Request schemas: job create / update, fitting details, vendor, lens status,
QC checklist, status PATCH, and the F2 lab-scan / station bodies.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date


# ============================================================================
# SCHEMAS
# ============================================================================


class FittingDetails(BaseModel):
    """Phase 6.8 — physical measurements the sales staff hands over to
    the workshop technician for lens cutting / fitting. All fields are
    optional individually, but `confirmed_by_sales` must be True for
    the workshop to accept the job (sales explicitly confirming that
    power + product details are correct).

    CLI-6 adds the four progressive-lens fitting parameters that matter
    for high-index / progressive / occupational lenses and directly
    impact remake rates when mis-measured:
      - segment_height: seg height (mm) — distance from bottom of lens to
        the optical centre / progression start. Critical for progressives.
      - pantoscopic_tilt: degrees the frame tilts toward the face (typical
        8-12 deg). Mis-tilt shifts the effective Rx by ~0.25D.
      - vertex_distance: mm from the back surface to the cornea (standard
        12 mm). Every mm deviation shifts effective power for strong Rx.
      - wrap_angle: frame wrap / face-form angle (degrees). High wrap shifts
        the effective cylinder axis on peripheral gaze.
    """

    dia: Optional[str] = None  # Lens diameter (e.g. "65", "70")
    fh: Optional[str] = None  # Fitting height (for progressive/bifocal)
    b_size: Optional[str] = None  # Lens vertical measurement
    dbl: Optional[str] = None  # Distance between lenses (bridge width)
    tint: Optional[str] = None  # Tint colour / percentage
    base_curve: Optional[str] = None  # Base curve (e.g. "6", "8")
    coating: Optional[str] = (
        None  # Coating name (redundant with lens_details.coating but captured here for sales confirmation)
    )
    other: Optional[str] = None  # Free-text notes
    order_date: Optional[str] = None  # ISO date (auto-filled on save)
    order_time: Optional[str] = None  # HH:MM (auto-filled on save)
    ordered_by: Optional[str] = None  # User id of sales staff
    ordered_by_name: Optional[str] = None
    expected_lens_receive_date: Optional[date] = None
    # Phase 6.8 — vendor (lens supplier) PO reference. Sales enters the ID
    # issued when the lens was ordered from Zeiss / Essilor / etc; workshop
    # + finance use it to reconcile incoming lens stock.
    vendor_order_id: Optional[str] = None
    confirmed_by_sales: bool = False  # Must be True to submit
    confirmed_at: Optional[str] = None  # ISO timestamp

    # CLI-6 — progressive lens fitting parameters (all optional; only
    # relevant for progressive / high-index / occupational lenses but stored
    # even for single-vision so the data is available for lab scorecards)
    segment_height: Optional[str] = None   # mm, e.g. "19", "22"
    pantoscopic_tilt: Optional[str] = None  # degrees, e.g. "10"
    vertex_distance: Optional[str] = None   # mm, e.g. "12", "13.5"
    wrap_angle: Optional[str] = None        # degrees (face-form), e.g. "5"


class WorkshopJobCreate(BaseModel):
    order_id: str
    frame_details: dict
    lens_details: dict
    prescription_id: str
    fitting_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    expected_date: date
    # Phase 6.8 — optional at create time; sales fills via a modal
    # right after order confirmation (PATCH /jobs/{id}/fitting-details)
    fitting_details: Optional[FittingDetails] = None
    # F9 -- DC HARDLOCK override. When an external-lab lens (lens_status=ORDERED)
    # has no logged Delivery Challan, the create is HARD-BLOCKED (422) -- an
    # ADMIN+ may bypass by supplying a reason (audited). Ignored for in-house
    # lenses / when the lock is disabled.
    override_reason: Optional[str] = None


class WorkshopJobUpdate(BaseModel):
    fitting_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    expected_date: Optional[date] = None


class FittingDetailsUpdate(BaseModel):
    """Payload for PATCH /workshop/jobs/{id}/fitting-details."""

    fitting_details: FittingDetails


class WorkshopVendorPatch(BaseModel):
    """Payload for PATCH /workshop/jobs/{id}/vendor — admin assigns / edits
    the lens lab handling this job. All fields are individually optional;
    we only update what's supplied (so a partial form save doesn't blow
    away tracking_url, etc.)."""

    vendor_id: Optional[str] = None
    vendor_order_id: Optional[str] = None
    vendor_tracking_url: Optional[str] = None
    vendor_dispatch_date: Optional[str] = None  # ISO date
    vendor_received_date: Optional[str] = None


class WorkshopVendorStatusBody(BaseModel):
    """Payload for POST /workshop/jobs/{id}/vendor-status — admin (IMS user)
    logging a vendor-status update on behalf of the lab (e.g. lab phoned
    them). Source on the resulting history row is `ims_user`."""

    status: str
    note: Optional[str] = None


class LensStatusBody(BaseModel):
    """Payload for POST /workshop/jobs/{id}/lens-status — advance the lens
    lifecycle by exactly one forward step (validated by _next_lens_status_ok)."""

    status: str


class QcCheckItem(BaseModel):
    """A single structured checklist item for the QC checklist endpoint."""

    key: str = Field(
        ..., description="Checklist item key, e.g. 'power', 'fitting', 'cosmetic'"
    )
    label: str = Field(..., description="Human-readable label")
    passed: bool = Field(..., description="True if this item passed")
    note: Optional[str] = Field(None, description="Optional note for this item")


class QcChecklistBody(BaseModel):
    """Payload for POST /workshop/jobs/{id}/qc-checklist.

    Carries a structured per-item checklist. A job cannot advance to
    READY_FOR_PICKUP unless either every item passed or an explicit waiver
    is provided with a reason.
    """

    checklist: List[QcCheckItem] = Field(..., min_length=1, description="One or more QC check items (cannot be empty)")
    overall_notes: Optional[str] = Field(
        None, description="Free-text summary / rework instructions"
    )
    # Optional waiver path: a manager can override a failed item with a reason.
    waived: bool = Field(
        False,
        description=(
            "If True the QC result is treated as passed despite individual failures."
            " Requires waive_reason."
        ),
    )
    waive_reason: Optional[str] = Field(
        None, description="Mandatory when waived=True. Explain why QC is being waived."
    )


class StatusBody(BaseModel):
    """Payload for PATCH /workshop/jobs/{id}/status.
    Accepts status + optional notes as a JSON body (the frontend sends PATCH
    with a JSON body, not query params, so this model is needed for
    compatibility)."""

    status: str
    notes: Optional[str] = None
    # F9 DC HARDLOCK override (ADMIN+ + reason) when advancing an external-lab
    # (lens_status=ORDERED) job to IN_PROGRESS with no logged Delivery Challan.
    override_reason: Optional[str] = None
    # Pickup record for the READY -> DELIVERED transition: who actually
    # collected the job (customer, relative, driver...). OPTIONAL -- a record,
    # not a gate: a delivery is never blocked on it. Ignored for any other
    # transition.
    picked_up_by_name: Optional[str] = None
    picked_up_by_phone: Optional[str] = None
    # CREDIT_DELIVERY approval token for the -> DELIVERED transition when the
    # linked order still carries a balance and the caller is not a manager
    # (owner ruling: credit delivery is PIN-gated). Same token the Orders
    # deliver door accepts; ignored for any other transition.
    approval_token: Optional[str] = None


# ---------------------------------------------------------------------------
# F2 -- internal lab routing (disposable job cards). See
# api/services/lab_routing.py for the routing brain.
# ---------------------------------------------------------------------------


class LabScanBody(BaseModel):
    """Payload for POST /workshop/scan -- a barcode scan at a lab bench.

    scanned_code: the value read off the disposable job card (the job_number or
                  job_id). Resolves WHICH job to advance.
    station_code: the bench being scanned at (INTAKE/EDGING/COATING/QC_LAB/
                  DISPATCH/PICKUP). The forward-only gate rejects an out-of-order
                  scan.
    store_id:     optional store hint (HQ roles scanning across stores).

    NOTE: any client-supplied dwell field is IGNORED -- dwell is always
    server-computed from the stored station_timestamps.
    """

    scanned_code: str
    station_code: str
    store_id: Optional[str] = None


class LabStationUpsert(BaseModel):
    """Payload for POST /workshop/stations -- configure one lab station for a
    store. Keyed on store_id + code. All fields besides code are optional so a
    partial save (e.g. just toggling is_active) does not clobber the rest."""

    code: str
    store_id: Optional[str] = None
    label: Optional[str] = None
    sequence_order: Optional[int] = None
    is_active: Optional[bool] = None
    target_dwell_minutes: Optional[int] = None
    advances_job_status: Optional[str] = None
    auto_notify_customer: Optional[bool] = None
