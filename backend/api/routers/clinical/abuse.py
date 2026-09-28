"""Clinical abuse / fraud-signal detection.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import Depends, Query
from typing import List, Optional
from datetime import datetime
from ..auth import require_roles
from ...dependencies import get_db, get_prescription_repository, validate_store_access
from ...services import clinical_abuse as _abuse
from ._shared import _ABUSE_VIEW_ROLES, router


# ============================================================================
# ABUSE / FRAUD-SIGNAL DETECTION
# ============================================================================


def _rx_has_redo(rx: dict) -> bool:
    """True if a prescription needed a redo. A redo is stamped onto the Rx
    (redo_count / redos array / redo_of) by the redo endpoint, so any of those
    being set marks this test as redone."""
    if rx.get("redo_count"):
        try:
            if int(rx.get("redo_count")) > 0:
                return True
        except (TypeError, ValueError):
            pass
    redos = rx.get("redos")
    if isinstance(redos, list) and len(redos) > 0:
        return True
    if rx.get("redo_of"):
        return True
    return False


def _looks_like_id(value: str) -> bool:
    """Heuristic: a UUID-ish / opaque-id-ish string we should NOT show a human.

    A 32-hex / 8-4-4-4-12 UUID, or any token with no spaces longer than ~16
    chars that is mostly hex/dashes. Real names have spaces or are short words.
    """
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or " " in s:
        return False
    compact = s.replace("-", "")
    if len(compact) >= 24 and all(c in "0123456789abcdefABCDEF" for c in compact):
        return True
    return False


def _opto_label(rx: dict, name_map: Optional[dict] = None) -> str:
    """Human label for the optometrist on an Rx.

    Prefers an embedded name, else resolves the optometrist_id via ``name_map``
    (id -> name), else falls back to the raw id. The owner saw bare UUIDs here
    (backlog #4): when the Rx row only stored optometrist_id, the resolver map
    now turns it into the real name.
    """
    name = rx.get("optometrist_name") or rx.get("optometristName")
    if name and not _looks_like_id(name):
        return name
    oid = rx.get("optometrist_id") or rx.get("optometristId")
    if oid and name_map:
        resolved = name_map.get(str(oid))
        if resolved:
            return resolved
    return name or oid or "Unknown"


def _patient_label(rx: dict) -> str:
    """Human label for the patient on an Rx."""
    return (
        rx.get("patient_name")
        or rx.get("patientName")
        or rx.get("customer_name")
        or rx.get("customer_phone")
        or rx.get("customer_id")
        or "Unknown patient"
    )


def _build_abuse_alerts(
    prescriptions: List[dict], now: datetime, name_map: Optional[dict] = None
) -> List[dict]:
    """Pure-ish assembly of AbuseAlert dicts from a window of prescriptions.

    Takes the already-fetched, already-window-filtered prescription list and
    the reference 'now', groups by optometrist / patient, and runs each
    detector in ``services.clinical_abuse``. Returns a list of dicts matching
    the frontend ``AbuseAlert`` interface (camelCase keys). No IO -> unit-test
    friendly. Each detector is wrapped so a malformed row can't sink the rest.

    ``name_map`` (optometrist_id -> human name) lets the caller resolve the
    optometrist NAME when the Rx row only stored the id (backlog #4 -- the
    owner saw "Optometrist: <uuid>"). Optional + fail-soft: absent map -> the
    previous id-fallback behaviour.
    """
    alerts: List[dict] = []
    now_iso = now.isoformat()

    # --- group by optometrist ---
    by_opto: dict = {}
    for rx in prescriptions:
        if not isinstance(rx, dict):
            continue
        opto_id = (
            rx.get("optometrist_id")
            or rx.get("optometristId")
            or _opto_label(rx, name_map)
        )
        bucket = by_opto.setdefault(
            opto_id,
            {
                "name": _opto_label(rx, name_map),
                "rxs": [],
                "redos": 0,
                "oor": 0,
                "dates": [],
            },
        )
        bucket["rxs"].append(rx)
        if _rx_has_redo(rx):
            bucket["redos"] += 1
        try:
            if _abuse.is_rx_out_of_range(rx):
                bucket["oor"] += 1
        except Exception:  # pragma: no cover - defensive
            pass
        dt = _abuse.rx_date(rx)
        if dt is not None:
            bucket["dates"].append(dt)

    # 1. Excessive redos per optometrist
    for opto_id, b in by_opto.items():
        total = len(b["rxs"])
        try:
            sev = _abuse.redo_severity(b["redos"], total)
        except Exception:  # pragma: no cover - defensive
            sev = None
        if sev:
            rate = _abuse.redo_rate_percent(b["redos"], total)
            alerts.append(
                {
                    "id": f"redo-{opto_id}",
                    "type": "high-redo-rate",
                    "severity": sev,
                    "optometristName": b["name"],
                    "optometristId": str(opto_id),
                    "details": (
                        f"{b['redos']} redo(s) across {total} tests "
                        f"({rate:.1f}% redo rate) -- above the "
                        f"{int(_abuse.REDO_RATE_WARN * 100)}% review threshold."
                    ),
                    "timestamp": now_iso,
                    "redoRate": rate,
                }
            )

    # 2. Out-of-range / suspicious Rx values per optometrist
    for opto_id, b in by_opto.items():
        total = len(b["rxs"])
        try:
            sev = _abuse.out_of_range_severity(b["oor"], total)
        except Exception:  # pragma: no cover - defensive
            sev = None
        if sev:
            pct = round(b["oor"] / total * 100.0, 1) if total else 0.0
            alerts.append(
                {
                    "id": f"oor-{opto_id}",
                    "type": "high-redo-rate",
                    "severity": sev,
                    "optometristName": b["name"],
                    "optometristId": str(opto_id),
                    "details": (
                        f"{b['oor']} of {total} tests ({pct:.1f}%) carry Rx "
                        "values at or beyond the validation limits "
                        "(SPH +/-20, CYL +/-6, AXIS 1-180, ADD +0.75..+3.50)."
                    ),
                    "timestamp": now_iso,
                }
            )

    # 4. Rapid / implausibly-fast entries per optometrist
    for opto_id, b in by_opto.items():
        try:
            burst = _abuse.find_rapid_burst(b["dates"])
        except Exception:  # pragma: no cover - defensive
            burst = None
        if burst:
            sev = _abuse.rapid_severity(burst["avg_gap_minutes"])
            alerts.append(
                {
                    "id": f"rapid-{opto_id}",
                    "type": "suspicious-speed",
                    "severity": sev,
                    "optometristName": b["name"],
                    "optometristId": str(opto_id),
                    "details": (
                        f"{burst['count']} tests entered within "
                        f"{burst['span_minutes']:.0f} min "
                        f"(~{burst['avg_gap_minutes']:.1f} min apart) -- "
                        "implausibly fast for genuine refractions."
                    ),
                    "timestamp": now_iso,
                }
            )

    # 3. Repeat tests for the same patient in a short window
    by_patient: dict = {}
    for rx in prescriptions:
        if not isinstance(rx, dict):
            continue
        pid = (
            rx.get("customer_id")
            or rx.get("patient_id")
            or rx.get("customer_phone")
            or _patient_label(rx)
        )
        pb = by_patient.setdefault(pid, {"name": _patient_label(rx), "dates": []})
        dt = _abuse.rx_date(rx)
        if dt is not None:
            pb["dates"].append(dt)

    for pid, pb in by_patient.items():
        try:
            in_window = _abuse.max_tests_in_window(
                pb["dates"], _abuse.REPEAT_WINDOW_DAYS
            )
            sev = _abuse.repeat_severity(in_window)
        except Exception:  # pragma: no cover - defensive
            sev = None
            in_window = 0
        if sev:
            alerts.append(
                {
                    "id": f"repeat-{pid}",
                    "type": "exact-copy",
                    "severity": sev,
                    # The patient is the subject here; surface them in the
                    # optometrist slot so the (single-name) card stays useful.
                    "optometristName": pb["name"],
                    "optometristId": str(pid),
                    "details": (
                        f"{in_window} eye tests for the same patient within "
                        f"{_abuse.REPEAT_WINDOW_DAYS} days -- possible repeat-"
                        "visit gaming of footfall / incentives."
                    ),
                    "timestamp": now_iso,
                }
            )

    # Critical first, then warnings; stable within each severity.
    alerts.sort(key=lambda a: 0 if a.get("severity") == "critical" else 1)
    return alerts


@router.get("/abuse-detection")
async def get_abuse_detection(
    store_id: Optional[str] = Query(None, alias="store_id"),
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(require_roles(*_ABUSE_VIEW_ROLES)),
):
    """Clinical fraud-control: compute abuse alerts over a recent window.

    Returns ``{"alerts": [AbuseAlert, ...], "generated_at": iso}`` where each
    alert matches the frontend ``AbuseAlert`` interface. Fail-soft throughout:
    no DB or no data -> an empty alert list (never a 500), so the management
    view degrades to "No issues detected" rather than erroring.

    Gated to STORE_MANAGER / AREA_MANAGER / ADMIN (SUPERADMIN auto-passes).
    """
    from datetime import timedelta

    now = datetime.utcnow()
    generated_at = now.isoformat()

    # Fail-soft: degraded backend returns an empty (valid) envelope.
    db = get_db()
    rx_repo = get_prescription_repository()
    if db is None or rx_repo is None:
        return {"alerts": [], "generated_at": generated_at}

    # BUG-062: authorise the requested store; default to the caller's own when
    # none is given (a store-scoped role passing another store's id is 403'd).
    store_id = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    cutoff = now - timedelta(days=days)

    try:
        flt: dict = {}
        if store_id:
            flt["store_id"] = store_id
        # Pull a generous slice (well past a single store's 30-day volume) and
        # window-filter in Python -- prescriptions store dates inconsistently
        # (prescription_date as datetime, test_date/created_at as ISO string),
        # so a single Mongo range query would silently miss the string-dated
        # auto-created Rx rows. Python-side parsing via rx_date() catches all.
        rows = rx_repo.find_many(flt, sort=[("created_at", -1)], limit=5000)
    except Exception as e:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__package__).warning("[CLINICAL] abuse fetch failed: %s", e)
        return {"alerts": [], "generated_at": generated_at}

    in_window: List[dict] = []
    for rx in rows or []:
        dt = _abuse.rx_date(rx)
        # Undated rows are kept (can't prove they're stale); dated rows must
        # fall inside the window.
        if dt is None or dt >= cutoff:
            in_window.append(rx)

    # Resolve optometrist ids -> human names so the alert card never shows a
    # bare UUID (backlog #4). Batched single lookup; fail-soft to {}.
    name_map: dict = {}
    try:
        from ...services.name_resolver import user_name_map

        opto_ids = [
            rx.get("optometrist_id") or rx.get("optometristId")
            for rx in in_window
            if isinstance(rx, dict)
        ]
        name_map = user_name_map(db, opto_ids)
    except Exception:  # pragma: no cover - defensive
        name_map = {}

    try:
        alerts = _build_abuse_alerts(in_window, now, name_map)
    except Exception as e:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__package__).warning("[CLINICAL] abuse build failed: %s", e)
        alerts = []

    return {"alerts": alerts, "generated_at": generated_at}
