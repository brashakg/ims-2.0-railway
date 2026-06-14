"""Roster engine -- skills-based staff rostering + optometrist coverage (Feature #29).

Owner decision (binding): all stores are clinical and the optometrist licence never
expires. Therefore EVERY store/day/shift requires optometrist coverage, and there is NO
licence-expiry machinery anywhere in this module -- only WHO is an optometrist matters.

The coverage math is intentionally pure (plain dicts in, plain dicts out, deterministic,
integer counts -- unit-testable in isolation). The DB engine below persists the
staff-skills registry + the per-store-week roster grid and runs the coverage check over a
stored roster, mirroring the single-document guarded-write pattern used across the app.

No money, no POS. No emoji (Windows cp1252).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

# --- shift slots + roster status -------------------------------------------
SHIFT_MORNING = "MORNING"
SHIFT_EVENING = "EVENING"
SHIFT_FULL_DAY = "FULL_DAY"
SHIFTS = (SHIFT_MORNING, SHIFT_EVENING, SHIFT_FULL_DAY)

STATUS_DRAFT = "DRAFT"
STATUS_PUBLISHED = "PUBLISHED"
ROSTER_STATUSES = (STATUS_DRAFT, STATUS_PUBLISHED)

# Coverage verdicts.
COVERAGE_OK = "OK"
COVERAGE_BREACH = "BREACH"

DEFAULT_REQUIRED_OPTOMETRISTS = 1

COLLECTION_SKILLS = "staff_skills"
COLLECTION_ROSTERS = "rosters"
POLICY_REQUIRED_OPTOMS = "hr.roster_required_optometrists"


class RosterError(Exception):
    """A business-rule failure -- carries an HTTP-ish status for the router."""

    def __init__(self, message: str, status: int = 400, code: str = "roster_error"):
        super().__init__(message)
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Pure coverage math
# ---------------------------------------------------------------------------


def _is_optometrist(
    skills_by_employee: Mapping[str, Mapping[str, Any]], emp_id: Any
) -> bool:
    rec = skills_by_employee.get(emp_id) if emp_id is not None else None
    return bool(rec and rec.get("is_optometrist"))


def compute_coverage(
    shifts: Iterable[Mapping[str, Any]],
    skills_by_employee: Mapping[str, Mapping[str, Any]],
    required_optoms: int,
) -> List[Dict[str, Any]]:
    """Optometrist coverage per (store, date, shift).

    ``shifts`` is an iterable of rostered entries, each carrying ``store_id``,
    ``date``, ``shift`` and ``employee_id``. For every distinct (store, date,
    shift) slot, count the rostered employees flagged ``is_optometrist`` in
    ``skills_by_employee``; the slot is BREACH when that count is below
    ``required_optoms``, else OK. Deterministic ordering (store, date, shift).
    Pure -- no I/O.
    """
    req = max(0, int(required_optoms or 0))
    slots: Dict[tuple, Dict[str, Any]] = {}
    for entry in shifts or []:
        store_id = entry.get("store_id")
        date = entry.get("date")
        shift = entry.get("shift")
        key = (store_id, date, shift)
        slot = slots.get(key)
        if slot is None:
            slot = {
                "store_id": store_id,
                "date": date,
                "shift": shift,
                "rostered_total": 0,
                "optoms_rostered": 0,
                "required": req,
            }
            slots[key] = slot
        slot["rostered_total"] += 1
        if _is_optometrist(skills_by_employee, entry.get("employee_id")):
            slot["optoms_rostered"] += 1
    out: List[Dict[str, Any]] = []
    for key in sorted(slots.keys(), key=lambda k: tuple(str(x) for x in k)):
        slot = slots[key]
        slot["status"] = (
            COVERAGE_BREACH if slot["optoms_rostered"] < req else COVERAGE_OK
        )
        out.append(slot)
    return out


def coverage_breaches(coverage: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Just the BREACH slots from a coverage report. Pure."""
    return [dict(c) for c in (coverage or []) if c.get("status") == COVERAGE_BREACH]


def validate_roster_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate + normalize a roster create/replace payload. Raises RosterError(422)."""
    payload = payload or {}
    store_id = str(payload.get("store_id") or "").strip()
    if not store_id:
        raise RosterError("store_id is required", status=422)
    week_start = str(payload.get("week_start") or "").strip()
    if not week_start:
        raise RosterError("week_start (ISO date) is required", status=422)
    status = str(payload.get("status") or STATUS_DRAFT).strip().upper()
    if status not in ROSTER_STATUSES:
        raise RosterError("status must be DRAFT or PUBLISHED", status=422)
    clean_entries: List[Dict[str, Any]] = []
    for e in payload.get("entries") or []:
        emp = str(e.get("employee_id") or "").strip()
        date = str(e.get("date") or "").strip()
        shift = str(e.get("shift") or "").strip().upper()
        if not emp or not date:
            raise RosterError("each entry needs employee_id and date", status=422)
        if shift not in SHIFTS:
            raise RosterError(
                "entry shift must be one of " + ", ".join(SHIFTS), status=422
            )
        clean_entries.append({"employee_id": emp, "date": date, "shift": shift})
    return {
        "store_id": store_id,
        "week_start": week_start,
        "status": status,
        "entries": clean_entries,
    }


# ---------------------------------------------------------------------------
# DB engine
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_db(db) -> None:
    if db is None:
        raise RosterError("roster store unavailable", status=503, code="no_db")


def ensure_indexes(db) -> None:
    """Idempotent indexes. Fail-soft."""
    if db is None:
        return
    try:
        db.get_collection(COLLECTION_SKILLS).create_index(
            [("employee_id", 1)], unique=True
        )
        db.get_collection(COLLECTION_ROSTERS).create_index(
            [("store_id", 1), ("week_start", 1)]
        )
    except Exception:  # noqa: BLE001
        return


# --- staff-skills registry -------------------------------------------------


def upsert_staff_skill(
    db, employee_id: str, payload: Mapping[str, Any], *, actor: Mapping[str, Any]
) -> Dict[str, Any]:
    """Set whether an employee is an optometrist + their skill tags (one doc/employee)."""
    _require_db(db)
    if not employee_id:
        raise RosterError("employee_id is required", status=422)
    is_optom = bool((payload or {}).get("is_optometrist"))
    skills = [
        str(s).strip() for s in ((payload or {}).get("skills") or []) if str(s).strip()
    ]
    from pymongo import ReturnDocument

    coll = db.get_collection(COLLECTION_SKILLS)
    now = _now_iso()
    updated = coll.find_one_and_update(
        {"employee_id": employee_id},
        {
            "$set": {
                "employee_id": employee_id,
                "is_optometrist": is_optom,
                "skills": skills,
                "store_id": (payload or {}).get("store_id"),
                "updated_by": actor.get("user_id"),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return updated or {
        "employee_id": employee_id,
        "is_optometrist": is_optom,
        "skills": skills,
    }


def list_staff_skills(db, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if db is None:
        return []
    query: Dict[str, Any] = {}
    if store_id:
        query["store_id"] = store_id
    try:
        return list(db.get_collection(COLLECTION_SKILLS).find(query))
    except Exception:  # noqa: BLE001
        return []


def get_staff_skill(db, employee_id: str) -> Optional[Dict[str, Any]]:
    if db is None:
        return None
    return db.get_collection(COLLECTION_SKILLS).find_one({"employee_id": employee_id})


def _skills_map(db, employee_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if db is None:
        return out
    ids = [e for e in set(employee_ids) if e]
    if not ids:
        return out
    try:
        for rec in db.get_collection(COLLECTION_SKILLS).find(
            {"employee_id": {"$in": ids}}
        ):
            out[rec.get("employee_id")] = rec
    except Exception:  # noqa: BLE001
        return {}
    return out


# --- rosters ---------------------------------------------------------------


def create_or_replace_roster(
    db, payload: Mapping[str, Any], *, actor: Mapping[str, Any]
) -> Dict[str, Any]:
    """Create (or replace the existing) DRAFT/PUBLISHED roster for a store-week.
    One roster per (store_id, week_start) -- a re-create replaces it in a single
    guarded write so two concurrent creates converge on one document."""
    _require_db(db)
    clean = validate_roster_payload(payload)
    coll = db.get_collection(COLLECTION_ROSTERS)
    now = _now_iso()
    existing = coll.find_one(
        {"store_id": clean["store_id"], "week_start": clean["week_start"]}
    )
    if existing is not None:
        from pymongo import ReturnDocument

        updated = coll.find_one_and_update(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "entries": clean["entries"],
                    "status": clean["status"],
                    "updated_by": actor.get("user_id"),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return updated
    rid = "RST-" + uuid.uuid4().hex[:10].upper()
    doc = {
        "_id": rid,
        "roster_id": rid,
        "store_id": clean["store_id"],
        "week_start": clean["week_start"],
        "entries": clean["entries"],
        "status": clean["status"],
        "created_by": actor.get("user_id"),
        "created_at": now,
        "updated_at": now,
    }
    coll.insert_one(dict(doc))
    return doc


def get_roster(
    db,
    roster_id: Optional[str] = None,
    *,
    store_id: Optional[str] = None,
    week_start: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if db is None:
        return None
    coll = db.get_collection(COLLECTION_ROSTERS)
    if roster_id:
        return coll.find_one({"roster_id": roster_id})
    if store_id and week_start:
        return coll.find_one({"store_id": store_id, "week_start": week_start})
    return None


def update_roster(
    db, roster_id: str, payload: Mapping[str, Any], *, actor: Mapping[str, Any]
) -> Dict[str, Any]:
    """Edit entries and/or publish a roster. Single guarded write."""
    _require_db(db)
    coll = db.get_collection(COLLECTION_ROSTERS)
    existing = coll.find_one({"roster_id": roster_id})
    if existing is None:
        raise RosterError("roster not found", status=404, code="not_found")
    set_fields: Dict[str, Any] = {
        "updated_by": actor.get("user_id"),
        "updated_at": _now_iso(),
    }
    if "entries" in (payload or {}):
        merged = validate_roster_payload(
            {
                "store_id": existing["store_id"],
                "week_start": existing["week_start"],
                "status": payload.get("status") or existing.get("status"),
                "entries": payload.get("entries") or [],
            }
        )
        set_fields["entries"] = merged["entries"]
    if payload.get("status"):
        status = str(payload["status"]).strip().upper()
        if status not in ROSTER_STATUSES:
            raise RosterError("status must be DRAFT or PUBLISHED", status=422)
        set_fields["status"] = status
    from pymongo import ReturnDocument

    updated = coll.find_one_and_update(
        {"roster_id": roster_id},
        {"$set": set_fields},
        return_document=ReturnDocument.AFTER,
    )
    return updated


def coverage_for_roster(
    db, roster: Mapping[str, Any], required_optoms: int
) -> List[Dict[str, Any]]:
    """Run compute_coverage over a stored roster's entries (store_id injected
    from the roster) against the staff-skills registry."""
    entries = roster.get("entries") or []
    shifts = [{**e, "store_id": roster.get("store_id")} for e in entries]
    skills = _skills_map(db, [e.get("employee_id") for e in entries])
    return compute_coverage(shifts, skills, required_optoms)


# --- coverage breach sweep (consumed by the TASKMASTER agent tick, #29) -------


def _today_iso() -> str:
    """Today's date as an ISO yyyy-mm-dd string (UTC), for the >= comparison
    against a roster entry's `date`. Wrapped so callers can inject a fixed
    `today` in tests."""
    return datetime.now(timezone.utc).date().isoformat()


def published_coverage_breaches(
    db,
    *,
    today: Optional[str] = None,
    required_optoms_for=None,
) -> List[Dict[str, Any]]:
    """Optometrist-coverage BREACH slots across every PUBLISHED roster, limited
    to slots whose date is today or later (a past day's coverage can no longer
    be fixed, so it is not alerted). Each returned slot carries store_id, date,
    shift, required, optoms_rostered PLUS week_start + roster_id for the alert
    dedupe key.

    `today` defaults to _today_iso(); inject a fixed value in tests.
    `required_optoms_for(store_id) -> int` resolves the per-store requirement
    (defaults to DEFAULT_REQUIRED_OPTOMETRISTS). Pure DB-READ + deterministic
    coverage math; fail-soft -> [] on no db / any error (never breaks a tick).
    """
    if db is None:
        return []
    today = today or _today_iso()
    resolver = required_optoms_for or (lambda _sid: DEFAULT_REQUIRED_OPTOMETRISTS)
    try:
        rosters = list(
            db.get_collection(COLLECTION_ROSTERS).find({"status": STATUS_PUBLISHED})
        )
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    for roster in rosters:
        try:
            req = int(resolver(roster.get("store_id")))
        except Exception:  # noqa: BLE001
            req = DEFAULT_REQUIRED_OPTOMETRISTS
        try:
            slots = coverage_breaches(coverage_for_roster(db, roster, req))
        except Exception:  # noqa: BLE001
            continue
        for slot in slots:
            # ISO yyyy-mm-dd strings compare correctly with >=.
            if str(slot.get("date") or "") >= today:
                out.append(
                    {
                        **slot,
                        "week_start": roster.get("week_start"),
                        "roster_id": roster.get("roster_id"),
                    }
                )
    return out


def coverage_breach_dedupe_key(breach: Mapping[str, Any], user_id: str) -> str:
    """Stable key so a repeated agent tick does not re-alert the SAME breach to
    the SAME recipient. One key per (store, week, date, shift, recipient)."""
    return ":".join(
        [
            "roster_coverage_breach",
            str(breach.get("store_id")),
            str(breach.get("week_start")),
            str(breach.get("date")),
            str(breach.get("shift")),
            str(user_id),
        ]
    )


def build_coverage_breach_notification(
    breach: Mapping[str, Any], user_id: str, dedupe_key: str
) -> Dict[str, Any]:
    """An in-app (NOTIFICATION_SCHEMA-shaped) coverage-breach bell. channels is
    IN_APP ONLY -- comms are dark (no WhatsApp/SMS); this is purely the in-app
    notification the bell UI reads. Pure."""
    store = breach.get("store_id")
    date = breach.get("date")
    shift = breach.get("shift")
    return {
        "notification_id": "NTF-"
        + datetime.now().strftime("%Y%m%d")
        + "-"
        + uuid.uuid4().hex[:8].upper(),
        "notification_type": "roster_coverage_breach",
        "user_id": user_id,
        "title": "Optometrist coverage gap",
        "message": (
            f"No optometrist rostered at store {store} on {date} ({shift}). "
            "Assign cover."
        ),
        "entity_type": "roster",
        "entity_id": breach.get("roster_id"),
        "action_url": "/hr/roster",
        "channels": ["IN_APP"],
        "priority": "HIGH",
        "status": "SENT",
        "dedupe_key": dedupe_key,
        "store_id": store,
        "created_at": datetime.now(),
    }
