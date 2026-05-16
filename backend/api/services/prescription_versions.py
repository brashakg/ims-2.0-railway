"""
IMS 2.0 — 4-version prescription helpers (pure logic)
=======================================================

Per the YouTube competitor research (Optical CRM ships this), each
visit captures four distinct Rx states instead of one:

  - before_testing  (auto-refractometer reading at intake)
  - after_testing   (subjective refraction the optometrist arrived at)
  - manual          (manual override / past-Rx reuse)
  - final           (the one that goes onto the spectacles — POS truth)

Final is mirrored into top-level `right_eye` / `left_eye` / `pd` fields
on the prescription doc so existing POS / order code that reads those
keeps working unchanged. Legacy single-Rx docs (no `versions` field)
read-time-backfill — treat top-level as `versions.final`.

Pure functions live here; the router (`prescriptions.py`) handles I/O.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# Allowed version slot names (must match the doc shape)
VALID_VERSION_NAMES = ("before_testing", "after_testing", "manual", "final")

# Allowed `source` values per version. Light validation; not a hard
# enum so the optometrist can extend with custom sources later.
VALID_SOURCES = {
    "before_testing": {"auto_ref", "patient_brought_old_rx", "walk_in_estimate"},
    "after_testing":  {"subjective_refraction", "cycloplegic", "retinoscopy"},
    "manual":         {"manual_override", "past_rx_reuse"},
    "final":          {"optometrist_signoff", "patient_request"},
}


def is_legacy_single_rx(doc: Dict[str, Any]) -> bool:
    """A prescription doc is legacy (pre-Phase-7) if it has top-level
    eye fields but no `versions` block."""
    if not doc:
        return False
    has_top_level = any(doc.get(k) for k in ("right_eye", "left_eye", "rightEye", "leftEye"))
    return has_top_level and not doc.get("versions")


def backfill_versions_from_top_level(doc: Dict[str, Any]) -> Dict[str, Any]:
    """For a legacy single-Rx doc, materialise a synthetic
    `versions.final` from the top-level fields so the API can present
    a consistent shape. Read-time backfill — does NOT mutate the DB."""
    if not is_legacy_single_rx(doc):
        return doc
    out = dict(doc)
    re = doc.get("right_eye") or doc.get("rightEye") or {}
    le = doc.get("left_eye") or doc.get("leftEye") or {}
    pd = doc.get("pd")
    out["versions"] = {
        "before_testing": None,
        "after_testing": None,
        "manual": None,
        "final": {
            "right_eye": re,
            "left_eye": le,
            "pd": pd,
            "captured_at": doc.get("created_at"),
            "captured_by": doc.get("optometrist_id") or doc.get("created_by"),
            "source": "optometrist_signoff",
            "signed_off_by": doc.get("optometrist_id") or doc.get("created_by"),
        },
    }
    return out


def merge_version(
    doc: Dict[str, Any],
    version_name: str,
    version_payload: Dict[str, Any],
    captured_by: Optional[str],
) -> Dict[str, Any]:
    """Apply a per-version write to the prescription doc. Returns the
    NEW doc (immutably — useful for tests). Caller persists via the
    repository."""
    if version_name not in VALID_VERSION_NAMES:
        raise ValueError(f"version_name must be one of {VALID_VERSION_NAMES}")
    if doc.get("status") == "finalized":
        raise ValueError("Prescription is finalized; cannot patch versions")

    out = dict(doc)
    versions = dict(out.get("versions") or {
        "before_testing": None,
        "after_testing": None,
        "manual": None,
        "final": None,
    })

    # Stamp captured_at if not already present in payload
    from datetime import datetime, timezone
    payload = dict(version_payload)
    payload.setdefault("captured_at", datetime.now(timezone.utc))
    if captured_by and "captured_by" not in payload:
        payload["captured_by"] = captured_by

    versions[version_name] = payload
    out["versions"] = versions
    out.setdefault("status", "in_progress")
    return out


def can_finalize(doc: Dict[str, Any]) -> bool:
    """Finalize requires the `final` version to be populated AND the
    record to be in_progress. Legacy single-Rx docs are already
    effectively finalized — this returns False (no-op)."""
    if not doc:
        return False
    if doc.get("status") == "finalized":
        return False
    versions = doc.get("versions") or {}
    final = versions.get("final")
    return bool(final and final.get("right_eye") and final.get("left_eye"))


def mirror_final_to_top_level(doc: Dict[str, Any]) -> Dict[str, Any]:
    """When finalizing, copy `versions.final.{right_eye,left_eye,pd}`
    into the top-level fields the legacy POS code reads. Idempotent.
    Returns a new doc — caller persists."""
    versions = doc.get("versions") or {}
    final = versions.get("final") or {}
    out = dict(doc)
    if final.get("right_eye"):
        out["right_eye"] = final["right_eye"]
    if final.get("left_eye"):
        out["left_eye"] = final["left_eye"]
    if final.get("pd") is not None:
        out["pd"] = final["pd"]
    out["status"] = "finalized"
    from datetime import datetime, timezone
    out["finalized_at"] = datetime.now(timezone.utc)
    return out


def progression_diffs(rx_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Given a chronological list of FINAL Rx's per visit, compute the
    delta between adjacent rows for each eye + parameter.

    Input docs are expected to carry `right_eye`, `left_eye`, and a
    `visit_at` (or `created_at`) timestamp. Output is a list of N-1
    deltas, oldest-first. Each delta:

      {
        "from_visit_at": ISO,
        "to_visit_at": ISO,
        "from_prescription_id": ...,
        "to_prescription_id": ...,
        "right_eye": {sphere_delta, cylinder_delta, axis_delta, addition_delta},
        "left_eye": {...},
      }

    Pure function — no I/O. Useful for clinical dashboards that
    visualise "is this customer's myopia accelerating?"
    """
    if not rx_history or len(rx_history) < 2:
        return []
    # Ensure chronological order — caller may pass desc; we sort asc
    sorted_rx = sorted(
        rx_history,
        key=lambda d: d.get("visit_at") or d.get("created_at") or "",
    )
    out: List[Dict[str, Any]] = []
    for prev, curr in zip(sorted_rx[:-1], sorted_rx[1:]):
        out.append({
            "from_visit_at": prev.get("visit_at") or prev.get("created_at"),
            "to_visit_at": curr.get("visit_at") or curr.get("created_at"),
            "from_prescription_id": prev.get("prescription_id"),
            "to_prescription_id": curr.get("prescription_id"),
            "right_eye": _eye_delta(prev.get("right_eye") or {}, curr.get("right_eye") or {}),
            "left_eye": _eye_delta(prev.get("left_eye") or {}, curr.get("left_eye") or {}),
        })
    return out


def _eye_delta(a: Dict, b: Dict) -> Dict[str, Optional[float]]:
    """Per-field signed delta. None when either side is missing the
    field — different from a 0 delta (which means "value the same")."""
    out: Dict[str, Optional[float]] = {}
    for key in ("sphere", "cylinder", "axis", "addition"):
        av = a.get(key)
        bv = b.get(key)
        try:
            if av is None or bv is None:
                out[f"{key}_delta"] = None
            else:
                out[f"{key}_delta"] = round(float(bv) - float(av), 2)
        except (TypeError, ValueError):
            out[f"{key}_delta"] = None
    return out
