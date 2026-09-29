"""Request schemas: queue, exam, SOAP note, redo, recommendation, send-to-floor.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Optional


# ============================================================================
# SCHEMAS
# ============================================================================


class QueueItemCreate(BaseModel):
    store_id: str = Field(..., alias="storeId")
    patient_name: str = Field(..., alias="patientName")
    customer_phone: str = Field(..., alias="customerPhone")
    age: Optional[int] = None
    reason: Optional[str] = None
    customer_id: Optional[str] = Field(None, alias="customerId")
    patient_id: Optional[str] = Field(None, alias="patientId")

    model_config = ConfigDict(populate_by_name=True)


class ClinicalFindings(BaseModel):
    """C6-B: the rest of an optometric exam record beyond refraction.

    Every field is OPTIONAL -- a quick refraction-only test sends none of this
    and behaves exactly as before; a full exam can now persist the clinical
    context that was previously dropped (VA, IOP, history, diagnosis, extra
    findings). Stored on the test record under `clinical_findings`; never blocks
    a completion. Strings are kept free-text on purpose (no premature enum) so
    the optometrist isn't fought by validation mid-exam.
    """

    # Visual acuity (e.g. "6/6", "6/9 N6"), unaided + aided, per eye + binocular.
    va_right_unaided: Optional[str] = Field(None, alias="vaRightUnaided")
    va_left_unaided: Optional[str] = Field(None, alias="vaLeftUnaided")
    va_right_aided: Optional[str] = Field(None, alias="vaRightAided")
    va_left_aided: Optional[str] = Field(None, alias="vaLeftAided")
    va_binocular: Optional[str] = Field(None, alias="vaBinocular")
    # Intra-ocular pressure (tonometry), mmHg, per eye. Bounded to a sane clinical
    # window so a fat-finger ("220") is rejected but a real reading (10-30) passes.
    iop_right: Optional[float] = Field(None, ge=0, le=80, alias="iopRight")
    iop_left: Optional[float] = Field(None, ge=0, le=80, alias="iopLeft")
    # History / presenting problem + structured-ish findings.
    chief_complaint: Optional[str] = Field(None, alias="chiefComplaint")
    history: Optional[str] = None
    diagnosis: Optional[str] = None
    colour_vision: Optional[str] = Field(
        None, alias="colourVision"
    )  # e.g. "Normal", "Ishihara 14/14"
    cover_test: Optional[str] = Field(None, alias="coverTest")
    dominant_eye: Optional[str] = Field(None, alias="dominantEye")  # "RIGHT"/"LEFT"
    additional_notes: Optional[str] = Field(None, alias="additionalNotes")

    @field_validator("dominant_eye", mode="after")
    @classmethod
    def _v_dominant(cls, v):
        if v is None or v == "":
            return None
        up = str(v).strip().upper()
        if up not in ("RIGHT", "LEFT", "R", "L"):
            raise ValueError("dominant_eye must be RIGHT or LEFT")
        return "RIGHT" if up in ("RIGHT", "R") else "LEFT"

    model_config = ConfigDict(populate_by_name=True)


class SoapDxCode(BaseModel):
    """A single ICD-10 / ICPC-2 diagnosis code entry on the exam SOAP note.

    ``code`` is the structured code (e.g. "H52.1" for myopia); ``description``
    is the human label that should always accompany it so the chart is readable
    without a code lookup; ``system`` defaults to "ICD-10" but is stored so we
    can layer in ICPC-2 or custom ophthalmological codes later without a schema
    change.  Both fields are free-text (no server-side code lookup -- the
    optometrist types the code); the constraint is just that `code` is present.
    """

    code: str = Field(..., min_length=1)
    description: Optional[str] = None
    system: str = Field("ICD-10")

    class Config:
        populate_by_name = True


class SoapNote(BaseModel):
    """Structured SOAP (Subjective / Objective / Assessment / Plan) exam record.

    CLI-11: a templated SOAP exam note layer AROUND the existing refraction
    capture.  All four sections are optional text blocks -- a quick refraction-
    only test leaves them all blank and behaves exactly as before.  The note is
    stored on the eye_test document under the key ``soap_note``; a standalone
    POST endpoint also lets an optometrist save / update it after the test is
    completed (so late charting is possible without re-opening the completion
    flow).

    Design decisions:
    * Free-text strings inside each section -- no premature enums that fight the
      optometrist mid-consult.
    * ``dx_codes`` carries a list of structured Dx entries (code + description +
      coding system) so reports can be filtered by diagnosis without parsing the
      free-text assessment.
    * ``plan_referral`` and ``plan_follow_up`` are pulled out as first-class
      booleans so the scheduling module can query them (future integration point).
    * All aliases are camelCase to match the frontend naming convention.
    """

    # -- Subjective (patient-reported history + presenting problem) --
    chief_complaint: Optional[str] = Field(None, alias="chiefComplaint")
    history_present_illness: Optional[str] = Field(
        None, alias="historyPresentIllness"
    )
    ocular_history: Optional[str] = Field(None, alias="ocularHistory")
    systemic_history: Optional[str] = Field(None, alias="systemicHistory")
    family_history: Optional[str] = Field(None, alias="familyHistory")
    medications: Optional[str] = None
    allergies: Optional[str] = None
    vdu_usage: Optional[str] = Field(None, alias="vduUsage")

    # -- Objective (clinician-measured findings) --
    # Visual acuity: mirrors ClinicalFindings VA fields so both paths are
    # consistent; the full SOAP note supersedes them when both are present.
    va_right_unaided: Optional[str] = Field(None, alias="vaRightUnaided")
    va_left_unaided: Optional[str] = Field(None, alias="vaLeftUnaided")
    va_right_aided: Optional[str] = Field(None, alias="vaRightAided")
    va_left_aided: Optional[str] = Field(None, alias="vaLeftAided")
    va_binocular: Optional[str] = Field(None, alias="vaBinocular")
    # Intra-ocular pressure (tonometry), mmHg, per eye.
    iop_right: Optional[float] = Field(None, ge=0, le=80, alias="iopRight")
    iop_left: Optional[float] = Field(None, ge=0, le=80, alias="iopLeft")
    colour_vision: Optional[str] = Field(None, alias="colourVision")
    cover_test: Optional[str] = Field(None, alias="coverTest")
    dominant_eye: Optional[str] = Field(None, alias="dominantEye")
    pupils: Optional[str] = None           # e.g. "PERRL", "APD right"
    ocular_motility: Optional[str] = Field(None, alias="ocularMotility")
    slit_lamp_summary: Optional[str] = Field(None, alias="slitLampSummary")
    fundus_summary: Optional[str] = Field(None, alias="fundusSummary")
    additional_objective: Optional[str] = Field(None, alias="additionalObjective")

    # -- Assessment (diagnosis narrative + structured Dx codes) --
    assessment: Optional[str] = None       # free-text clinical impression
    dx_codes: Optional[List[SoapDxCode]] = Field(None, alias="dxCodes")

    # -- Plan (management + follow-up instructions) --
    plan: Optional[str] = None             # free-text plan narrative
    plan_referral: Optional[bool] = Field(None, alias="planReferral")
    plan_referral_to: Optional[str] = Field(None, alias="planReferralTo")
    plan_follow_up: Optional[bool] = Field(None, alias="planFollowUp")
    plan_follow_up_weeks: Optional[int] = Field(None, alias="planFollowUpWeeks", ge=1, le=104)
    patient_instructions: Optional[str] = Field(None, alias="patientInstructions")

    # -- Metadata --
    recorded_by: Optional[str] = Field(None, alias="recordedBy")
    recorded_at: Optional[str] = Field(None, alias="recordedAt")

    @field_validator("dominant_eye", mode="after")
    @classmethod
    def _v_dominant(cls, v):
        if v is None or v == "":
            return None
        up = str(v).strip().upper()
        if up not in ("RIGHT", "LEFT", "R", "L"):
            raise ValueError("dominant_eye must be RIGHT or LEFT")
        return "RIGHT" if up in ("RIGHT", "R") else "LEFT"

    class Config:
        populate_by_name = True


class ExamEyeReading(BaseModel):
    """One eye's readings on a NON-final exam tab.

    CLINICAL-CRITICAL, and NEW as of 2026-08-24. The lensometer, auto-
    refractometer and subjective-refraction tabs collected these values in the
    browser and then DROPPED them: no field existed on this model, no column
    existed on the document, and consequently NO VALIDATOR EVER SAW THEM. An
    optometrist could record a lensometer sphere of -9999 and the API would
    neither reject it nor keep it.

    Powers stay STRINGS so a signed value ("+4.00") survives byte-for-byte --
    float() would coerce away the explicit plus the printed card and the lab
    job-card have to show. The RANGE checks are not declared here as field
    validators but run through `_validate_eye_test_rx` in the endpoint, so an
    exam reading is judged by EXACTLY the same code, with exactly the same
    message, as the final prescription. One gate, not a lookalike.
    """

    sphere: Optional[str] = None
    cylinder: Optional[str] = None
    axis: Optional[str] = None
    add: Optional[str] = None
    # PER-EYE PD is MONOCULAR (~20-45mm); validated as "pd_mono".
    pd: Optional[str] = None
    va: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class AutoRefEyeReading(ExamEyeReading):
    """An auto-refractometer eye: a refraction PLUS the keratometry pair."""

    k1: Optional[str] = None
    k1_axis: Optional[str] = Field(None, alias="k1Axis")
    k2: Optional[str] = None
    k2_axis: Optional[str] = Field(None, alias="k2Axis")

    model_config = ConfigDict(populate_by_name=True)


class ExamRefraction(BaseModel):
    """A lensometer / subjective-refraction tab: both eyes + free-text remarks."""

    right_eye: ExamEyeReading = Field(default_factory=ExamEyeReading, alias="rightEye")
    left_eye: ExamEyeReading = Field(default_factory=ExamEyeReading, alias="leftEye")
    remarks: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class AutoRefExam(BaseModel):
    """The auto-refractometer tab: both eyes (with K readings) + remarks."""

    right_eye: AutoRefEyeReading = Field(
        default_factory=AutoRefEyeReading, alias="rightEye"
    )
    left_eye: AutoRefEyeReading = Field(
        default_factory=AutoRefEyeReading, alias="leftEye"
    )
    remarks: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class SlitLampEyeExam(BaseModel):
    """One eye's slit-lamp findings. Free text on purpose (the tab offers
    pick-lists but an optometrist must be able to describe what they saw);
    only the intra-ocular pressure is a number, bounded to the same 0-80 mmHg
    clinical window ClinicalFindings and SoapNote already use."""

    lids: Optional[str] = None
    conjunctiva: Optional[str] = None
    cornea: Optional[str] = None
    ac: Optional[str] = None
    iris: Optional[str] = None
    pupil: Optional[str] = None
    lens: Optional[str] = None
    fundus: Optional[str] = None
    iop: Optional[float] = Field(None, ge=0, le=80)

    model_config = ConfigDict(populate_by_name=True)


class SlitLampExam(BaseModel):
    right_eye: SlitLampEyeExam = Field(
        default_factory=SlitLampEyeExam, alias="rightEye"
    )
    left_eye: SlitLampEyeExam = Field(default_factory=SlitLampEyeExam, alias="leftEye")
    remarks: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class EyeTestData(BaseModel):
    right_eye: dict = Field(..., alias="rightEye")
    left_eye: dict = Field(..., alias="leftEye")
    pd: Optional[float] = Field(None, ge=0, le=120)
    # IPD + next-checkup so the clinical Final-Rx mirror writes the SAME parity
    # fields a POS-created prescription does. Kept as str -> no float-coercion
    # 422 on an empty value.
    ipd: Optional[str] = None
    next_checkup: Optional[str] = Field(None, alias="nextCheckup")
    notes: Optional[str] = None
    lens_recommendation: Optional[str] = Field(None, alias="lensRecommendation")
    coating_recommendation: Optional[str] = Field(None, alias="coatingRecommendation")
    # C6-B: optional full-exam findings (VA / IOP / history / diagnosis / ...).
    # Absent -> the test stays a refraction-only record exactly as before.
    clinical_findings: Optional[ClinicalFindings] = Field(
        None, alias="clinicalFindings"
    )
    # CLI-11: optional structured SOAP exam note.  Absent -> refraction-only test
    # exactly as before; present -> stored under ``soap_note`` on the test doc.
    soap_note: Optional[SoapNote] = Field(None, alias="soapNote")

    # ---- The four exam tabs that used to be thrown away (2026-08-24) --------
    # Each is OPTIONAL: a quick refraction-only test sends none of them and the
    # stored document is byte-for-byte what it was before. Present -> validated
    # against the SAME clinical ranges as the final Rx and persisted, so the
    # exam can be re-opened and edited instead of "edit" silently blanking a
    # patient's readings.
    lensometer: Optional[ExamRefraction] = None
    auto_ref: Optional[AutoRefExam] = Field(None, alias="autoRef")
    subjective_rx: Optional[ExamRefraction] = Field(None, alias="subjectiveRx")
    slit_lamp: Optional[SlitLampExam] = Field(None, alias="slitLamp")

    # Exam header fields the form collects and also used to drop.
    exam_date: Optional[str] = Field(None, alias="examDate")
    optometrist_name: Optional[str] = Field(None, alias="optometristName")
    chief_complaint: Optional[str] = Field(None, alias="chiefComplaint")
    vdu_usage: Optional[str] = Field(None, alias="vduUsage")

    # ---- The exam PAGE (2026-09-04) ------------------------------------------
    # `internal_note` is STAFF-ONLY. It is stored on the eye-test document and
    # NOWHERE else: never copied onto the mirrored prescription, so the printed
    # Rx card, the customer portal projection and any WhatsApp send -- all of
    # which read the prescription, not the exam -- cannot carry it. An empty
    # string CLEARS a stored note; an absent field leaves it alone.
    # `exam_step` is the step the optometrist paused on, so "Continue" from the
    # queue reopens the exam where it was left.
    internal_note: Optional[str] = Field(None, alias="internalNote", max_length=2000)
    exam_step: Optional[str] = Field(None, alias="examStep", max_length=32)

    model_config = ConfigDict(populate_by_name=True)


class StatusUpdate(BaseModel):
    status: str


class RedoCreate(BaseModel):
    reason: str = Field(..., min_length=1)


# F50 -- a single free-text product recommendation row on a clinical handover.
# Advisory only: no catalog validation (owner-locked "free-text recommendations").
class ProductRecommendation(BaseModel):
    category: Optional[str] = Field(None, max_length=40)
    brand_preference: Optional[str] = Field(
        None, max_length=60, alias="brandPreference"
    )
    notes: Optional[str] = Field(None, max_length=200)

    model_config = ConfigDict(populate_by_name=True)


# F50 -- payload for POST /clinical/tests/{test_id}/send-to-floor. The Rx itself
# is read live from the auto-created prescription (never copied); the optometrist
# only adds free-text recommendations + an optional one-line summary for sales.
class SendToFloorInput(BaseModel):
    product_recommendations: List[ProductRecommendation] = Field(
        default_factory=list, alias="productRecommendations", max_length=5
    )
    clinical_summary: Optional[str] = Field(
        None, max_length=200, alias="clinicalSummary"
    )

    model_config = ConfigDict(populate_by_name=True)
