"""
IMS 2.0 - F2 Internal lab routing (disposable barcoded job cards)
=================================================================

A workshop order travels physically through a sequence of in-house lab benches
(INTAKE -> EDGING -> COATING -> QC_LAB -> DISPATCH -> PICKUP). A single
disposable barcoded job card (Code128 of the job_number) is printed at intake
and rides with the spectacles. At each bench the technician scans the card; the
system gates the scan to the NEXT active station in sequence, advances the
job's `current_station`, and records dwell time per station.

This module is the pure-ish routing brain. It owns:

  * `lab_stations` -- a store-configurable station registry (one doc per
    store+code). Seeded with 6 sensible defaults on first use per store.
  * `_advance_lab_station` -- the forward-only scan gate. A single
    `find_one_and_update` on `workshop_jobs` with `current_station` IN THE
    FILTER is the concurrency guard (mirrors `vouchers.redeem_voucher_atomic`):
    exactly one of two racing scans on the same job wins, the other loses
    cleanly with CONCURRENT_CONFLICT.

CONSTRAINTS honoured (CORRECTIONS + PROTOCOL):
  * Standalone Mongo -> NO multi-document transactions. The job advance is a
    SINGLE-document CAS; the `lab_stations` read is a separate read-before-write
    (safe: station config is rare/admin-only). No cross-collection atomic write.
  * No emoji / non-ASCII in source (Windows cp1252). ASCII tag [LAB_ROUTING].
  * This feature calls NO money/loyalty/settings/E3 engine. It does not touch a
    stocked unit, so it does NOT call `item_events.record_event` (that ledger is
    for `stock_units` state, not job routing -- forking it here would be wrong).
  * Dwell time is ALWAYS server-computed from the stored station_timestamps;
    any client-supplied dwell is ignored.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Canonical station code vocabulary. A station config row's `code` must be one
# of these (validated on upsert). The default sequence is INTAKE..PICKUP.
VALID_STATION_CODES = (
    "INTAKE",
    "EDGING",
    "COATING",
    "QC_LAB",
    "DISPATCH",
    "PICKUP",
)

# Human-friendly station labels (ASCII only).
STATION_LABELS = {
    "INTAKE": "Intake",
    "EDGING": "Edging Bench",
    "COATING": "Coating",
    "QC_LAB": "Lab QC",
    "DISPATCH": "Dispatch",
    "PICKUP": "Front-desk Pickup",
}

# Seeded defaults inserted once per store on first use. Order matters: the
# forward-only gate walks active stations by sequence_order.
#   target_dwell_minutes -> SLA threshold (0 = no alert).
#   advances_job_status  -> when scanned, also set workshop_jobs.status to this.
#   auto_notify_customer -> when True + advances to READY, fire notify_ready().
DEFAULT_STATIONS: List[dict] = [
    {"code": "INTAKE", "sequence_order": 1, "target_dwell_minutes": 0,
     "advances_job_status": "IN_PROGRESS", "auto_notify_customer": False},
    {"code": "EDGING", "sequence_order": 2, "target_dwell_minutes": 180,
     "advances_job_status": None, "auto_notify_customer": False},
    {"code": "COATING", "sequence_order": 3, "target_dwell_minutes": 120,
     "advances_job_status": None, "auto_notify_customer": False},
    {"code": "QC_LAB", "sequence_order": 4, "target_dwell_minutes": 60,
     "advances_job_status": None, "auto_notify_customer": False},
    {"code": "DISPATCH", "sequence_order": 5, "target_dwell_minutes": 0,
     "advances_job_status": "READY", "auto_notify_customer": True},
    {"code": "PICKUP", "sequence_order": 6, "target_dwell_minutes": 0,
     "advances_job_status": "DELIVERED", "auto_notify_customer": False},
]

# Status values that mean the job can no longer be routed (terminal / branch).
_TERMINAL_JOB_STATUSES = {"DELIVERED", "CANCELLED"}


# ---------------------------------------------------------------------------
# Station registry (lab_stations collection)
# ---------------------------------------------------------------------------


def _stations_collection(db):
    """Return the `lab_stations` Mongo collection, or None when no DB."""
    if db is None:
        return None
    try:
        return db.get_collection("lab_stations")
    except Exception as e:  # noqa: BLE001
        logger.warning("[LAB_ROUTING] lab_stations collection unavailable: %s", e)
        return None


def seed_default_stations(db, store_id: str, actor_id: Optional[str] = None) -> int:
    """Insert the 6 default station rows for `store_id` if it has none yet.

    Idempotent: returns the number of rows inserted (0 when the store already
    has a config). Fail-soft: no DB / write error -> 0.
    """
    coll = _stations_collection(db)
    if coll is None or not store_id:
        return 0
    try:
        if coll.count_documents({"store_id": store_id}) > 0:
            return 0
    except Exception:  # noqa: BLE001
        return 0
    now = datetime.now()
    inserted = 0
    for spec in DEFAULT_STATIONS:
        doc = {
            "station_id": str(uuid.uuid4()),
            "store_id": store_id,
            "code": spec["code"],
            "label": STATION_LABELS.get(spec["code"], spec["code"]),
            "sequence_order": spec["sequence_order"],
            "is_active": True,
            "target_dwell_minutes": spec["target_dwell_minutes"],
            "advances_job_status": spec["advances_job_status"],
            "auto_notify_customer": spec["auto_notify_customer"],
            "created_at": now,
            "updated_at": now,
            "updated_by": actor_id,
        }
        try:
            coll.insert_one(dict(doc))
            inserted += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("[LAB_ROUTING] seed insert failed (%s): %s", spec["code"], e)
    return inserted


def _strip_id(doc: Optional[dict]) -> Optional[dict]:
    """Drop a BSON _id so FastAPI can serialise the row."""
    if doc is not None and "_id" in doc:
        d = dict(doc)
        d.pop("_id", None)
        return d
    return doc


def list_stations(db, store_id: str, *, seed_if_empty: bool = True) -> List[dict]:
    """All stations for a store in sequence_order. Seeds defaults on first use.

    Returns ACTIVE + INACTIVE rows (the queue/gate filter active ones); the
    config UI needs to see deactivated stations too. Fail-soft [].
    """
    coll = _stations_collection(db)
    if coll is None or not store_id:
        return []
    if seed_if_empty:
        try:
            if coll.count_documents({"store_id": store_id}) == 0:
                seed_default_stations(db, store_id)
        except Exception:  # noqa: BLE001
            pass
    try:
        rows = list(coll.find({"store_id": store_id}))
    except Exception:  # noqa: BLE001
        return []
    rows = [_strip_id(r) for r in rows]
    rows.sort(key=lambda r: (r or {}).get("sequence_order", 0))
    return rows


def active_sequence(db, store_id: str) -> List[dict]:
    """The ordered list of ACTIVE stations for a store (the routing path)."""
    return [s for s in list_stations(db, store_id) if s.get("is_active", True)]


def upsert_station(
    db,
    *,
    store_id: str,
    code: str,
    actor_id: Optional[str] = None,
    label: Optional[str] = None,
    sequence_order: Optional[int] = None,
    is_active: Optional[bool] = None,
    target_dwell_minutes: Optional[int] = None,
    advances_job_status: Optional[str] = None,
    auto_notify_customer: Optional[bool] = None,
) -> Tuple[bool, Optional[dict], Optional[str]]:
    """Create or update a single station config (keyed on store_id+code).

    Returns (ok, station_doc, error_reason). Validates `code` against the
    canonical vocabulary -> UNKNOWN_STATION on a bad code. Fail-soft on no DB.
    """
    coll = _stations_collection(db)
    if coll is None or not store_id:
        return False, None, "REPO_UNAVAILABLE"
    code_u = (code or "").strip().upper()
    if code_u not in VALID_STATION_CODES:
        return False, None, "UNKNOWN_STATION"

    # Ensure the store has its full default sequence BEFORE editing one station,
    # so a manager deactivating (say) COATING on a never-configured store does
    # not leave it with ONLY that one row + no routing path.
    try:
        if coll.count_documents({"store_id": store_id}) == 0:
            seed_default_stations(db, store_id, actor_id)
    except Exception:  # noqa: BLE001
        pass

    now = datetime.now()
    existing = None
    try:
        existing = coll.find_one({"store_id": store_id, "code": code_u})
    except Exception as e:  # noqa: BLE001
        logger.warning("[LAB_ROUTING] station lookup failed: %s", e)

    set_block: dict = {"updated_at": now, "updated_by": actor_id}
    if label is not None:
        set_block["label"] = label
    if sequence_order is not None:
        set_block["sequence_order"] = int(sequence_order)
    if is_active is not None:
        set_block["is_active"] = bool(is_active)
    if target_dwell_minutes is not None:
        set_block["target_dwell_minutes"] = max(0, int(target_dwell_minutes))
    if advances_job_status is not None:
        # Empty string clears the flag.
        set_block["advances_job_status"] = (advances_job_status or "").strip().upper() or None
    if auto_notify_customer is not None:
        set_block["auto_notify_customer"] = bool(auto_notify_customer)

    try:
        if existing is None:
            # Establish defaults for any unset field on create.
            default = next((d for d in DEFAULT_STATIONS if d["code"] == code_u), {})
            doc = {
                "station_id": str(uuid.uuid4()),
                "store_id": store_id,
                "code": code_u,
                "label": set_block.get("label", STATION_LABELS.get(code_u, code_u)),
                "sequence_order": set_block.get(
                    "sequence_order", default.get("sequence_order", 99)
                ),
                "is_active": set_block.get("is_active", True),
                "target_dwell_minutes": set_block.get(
                    "target_dwell_minutes", default.get("target_dwell_minutes", 0)
                ),
                "advances_job_status": set_block.get(
                    "advances_job_status", default.get("advances_job_status")
                ),
                "auto_notify_customer": set_block.get(
                    "auto_notify_customer", default.get("auto_notify_customer", False)
                ),
                "created_at": now,
                "updated_at": now,
                "updated_by": actor_id,
            }
            coll.insert_one(dict(doc))
            return True, _strip_id(doc), None
        coll.update_one(
            {"store_id": store_id, "code": code_u}, {"$set": set_block}
        )
        updated = coll.find_one({"store_id": store_id, "code": code_u})
        return True, _strip_id(updated), None
    except Exception as e:  # noqa: BLE001
        logger.warning("[LAB_ROUTING] station upsert failed: %s", e)
        return False, None, "WRITE_FAILED"


# ---------------------------------------------------------------------------
# Forward-only scan gate (the core)
# ---------------------------------------------------------------------------


def _next_expected_station(seq: List[dict], current_station: Optional[str]) -> Optional[str]:
    """Given the ACTIVE station sequence and a job's current_station, return the
    code of the station the job is allowed to advance into next, or None when
    there is no forward step (already at the last station).

    A null/empty current_station means the job has not entered the lab yet -> the
    next station is the FIRST in the sequence.
    """
    if not seq:
        return None
    codes = [s.get("code") for s in seq]
    cur = (current_station or "").strip().upper() or None
    if cur is None:
        return codes[0]
    if cur not in codes:
        # current_station is not in this store's active sequence (e.g. a station
        # was deactivated after the job entered it). No safe forward step.
        return None
    idx = codes.index(cur)
    if idx >= len(codes) - 1:
        return None
    return codes[idx + 1]


def _dwell_ms(previous_ts: Optional[str], now: datetime) -> Optional[int]:
    """Server-computed dwell in ms between a stored ISO timestamp and `now`.

    Never trusts a client value. Returns None when there is no previous stamp,
    and clamps negatives to 0 (clock skew). Pure."""
    if not previous_ts:
        return None
    try:
        prev = datetime.fromisoformat(str(previous_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    # Compare naive-to-naive: station_timestamps are stamped with datetime.now()
    # (naive local), so strip tz if the parsed value carried one.
    if prev.tzinfo is not None and now.tzinfo is None:
        prev = prev.replace(tzinfo=None)
    delta_ms = int((now - prev).total_seconds() * 1000)
    return max(0, delta_ms)


def advance_lab_station(
    db,
    job: dict,
    station_code: str,
    operator_id: str,
) -> dict:
    """Advance a workshop job into `station_code` via a barcode scan.

    LOUD-failure contract (mirrors labels.scan_advance): returns a dict with
    `ok=False` + a `reason` on any guard failure and DOES NOT mutate state.
    On success returns the advanced job + routing fields.

    reasons: REPO_UNAVAILABLE, NOT_FOUND, NO_STATIONS, TERMINAL_STAGE,
             UNKNOWN_STATION, WRONG_STATION, ALREADY_HERE, CONCURRENT_CONFLICT

    Concurrency: the advance is a SINGLE find_one_and_update on workshop_jobs
    guarded by ``{job_id, current_station: <expected>}`` -- exactly one of two
    racing scans on the same job wins; the loser gets CONCURRENT_CONFLICT.
    """
    if db is None:
        return {"ok": False, "reason": "REPO_UNAVAILABLE",
                "message": "Database unavailable; cannot route."}
    if job is None:
        return {"ok": False, "reason": "NOT_FOUND", "message": "No workshop job."}

    store_id = job.get("store_id")
    job_id = job.get("job_id")
    code = (station_code or "").strip().upper()

    job_status = (job.get("status") or "PENDING").strip().upper()
    if job_status in _TERMINAL_JOB_STATUSES:
        return {
            "ok": False,
            "reason": "TERMINAL_STAGE",
            "message": f"Job is {job_status} and can no longer be routed.",
            "got": job_status,
        }

    seq = active_sequence(db, store_id)
    if not seq:
        return {"ok": False, "reason": "NO_STATIONS",
                "message": "No active lab stations configured for this store."}

    codes = [s.get("code") for s in seq]
    if code not in codes:
        return {
            "ok": False,
            "reason": "UNKNOWN_STATION",
            "message": f"Station {code} is not an active station for this store.",
            "got": code,
        }

    current_station = (job.get("current_station") or "").strip().upper() or None

    # Already at this station = a duplicate scan; loud no-op (no state change).
    if current_station == code:
        return {
            "ok": False,
            "reason": "ALREADY_HERE",
            "message": f"Job is already at {STATION_LABELS.get(code, code)}.",
            "current_station": current_station,
        }

    expected = _next_expected_station(seq, current_station)
    if expected is None:
        return {
            "ok": False,
            "reason": "TERMINAL_STAGE",
            "message": "Job has already cleared the final lab station.",
            "current_station": current_station,
        }
    if code != expected:
        return {
            "ok": False,
            "reason": "WRONG_STATION",
            "message": (
                f"This job is ready for {STATION_LABELS.get(expected, expected)}, "
                f"not {STATION_LABELS.get(code, code)}."
            ),
            "expected": expected,
            "got": code,
            "current_station": current_station,
        }

    now = datetime.now()
    station_cfg = next((s for s in seq if s.get("code") == code), {})

    # Dwell for the station the job is LEAVING (the current one), computed from
    # its stored entry timestamp -- never client-supplied.
    set_block: dict = {
        "current_station": code,
        f"station_timestamps.{code}": now.isoformat(),
        "updated_at": now,
    }
    if current_station:
        prev_ts = (job.get("station_timestamps") or {}).get(current_station)
        dwell = _dwell_ms(prev_ts, now)
        if dwell is not None:
            set_block[f"station_dwell_ms.{current_station}"] = dwell

    history_entry = {
        "stage": code,
        "previous": current_station,
        "via": "scan",
        "station": code,
        "current_station": code,
        "scanned_code": job.get("job_number") or job_id,
        "operator": operator_id,
        "at": now.isoformat(),
    }

    # CAS guard: current_station must still equal what we read. A racing scan
    # that already advanced the job flips current_station, so the second writer
    # matches zero docs -> CONCURRENT_CONFLICT.
    try:
        from pymongo import ReturnDocument

        cas_filter: dict = {"job_id": job_id}
        if current_station is None:
            cas_filter["$or"] = [
                {"current_station": None},
                {"current_station": {"$exists": False}},
            ]
        else:
            cas_filter["current_station"] = current_station

        updated = db.get_collection("workshop_jobs").find_one_and_update(
            cas_filter,
            {"$set": set_block, "$push": {"scan_history": history_entry}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[LAB_ROUTING] scan CAS failed for %s: %s", job_id, e)
        return {"ok": False, "reason": "WRITE_FAILED",
                "message": "Failed to persist the station advance."}

    if updated is None:
        return {
            "ok": False,
            "reason": "CONCURRENT_CONFLICT",
            "message": "Another scan advanced this job first.",
            "expected": expected,
            "current_station": current_station,
        }

    updated.pop("_id", None)

    # Optional job-status transition driven by the station config (e.g. DISPATCH
    # -> READY, PICKUP -> DELIVERED). Done as a SEPARATE single-doc update via the
    # repo so status timestamps stay consistent with the rest of workshop.py.
    advances_to = station_cfg.get("advances_job_status")
    new_status = job_status
    gate_block = None
    if advances_to:
        advances_to = str(advances_to).strip().upper() or None
    if advances_to and advances_to != job_status:
        # SAFETY GATES (BUG-116c / BUG-116a): the scan-driven status flip must
        # enforce the SAME gates as the workshop PATCH handler -- a scan may NOT
        # start a job (-> IN_PROGRESS) until sales confirm the fitting, nor mark it
        # READY (-> patient pickup) without a QC pass/waiver. On a gate fail we keep
        # the physical-station scan but DO NOT flip status and DO NOT auto-notify.
        if advances_to == "IN_PROGRESS" and not (
            (updated.get("fitting_details") or {}).get("confirmed_by_sales")
        ):
            gate_block = "SALES_CONFIRM_REQUIRED"
        elif advances_to == "READY" and not (
            updated.get("qc_passed") is True or updated.get("qc_waived") is True
        ):
            gate_block = "QC_REQUIRED"
        if gate_block is None:
            try:
                from ..dependencies import get_workshop_repository

                repo = get_workshop_repository()
                if repo is not None:
                    repo.update_status(job_id, advances_to, operator_id, "lab-scan")
                    new_status = advances_to
            except Exception as e:  # noqa: BLE001
                logger.warning("[LAB_ROUTING] status transition failed: %s", e)

    _gate_msg = {
        "QC_REQUIRED": " Status held: record/waive lens QC before marking READY.",
        "SALES_CONFIRM_REQUIRED": " Status held: sales must confirm the fitting before work starts.",
    }
    return {
        "ok": True,
        "job_id": job_id,
        "job_number": job.get("job_number"),
        "customer_name": job.get("customer_name") or "",
        "store_id": store_id,
        "previous_station": current_station,
        "current_station": code,
        "station_label": STATION_LABELS.get(code, code),
        "stage": new_status,
        "advanced_status": new_status if (advances_to and gate_block is None) else None,
        "status_gate_blocked": gate_block,
        "auto_notify": (
            bool(station_cfg.get("auto_notify_customer"))
            and new_status == "READY"
            and gate_block is None
        ),
        "stamped_at": now.isoformat(),
        "message": f"Scanned in at {STATION_LABELS.get(code, code)}." + _gate_msg.get(gate_block, ""),
    }


# ---------------------------------------------------------------------------
# Station queue + dwell aging (pure helpers + DB query)
# ---------------------------------------------------------------------------


def dwell_chip(target_minutes: Optional[int], dwell_minutes: float) -> str:
    """SLA colour semantics for a job sitting at a station (pure):
      - 'green'  within SLA (or no SLA set)
      - 'amber'  50-80% of SLA consumed
      - 'red'    >80% of SLA consumed / overdue
    """
    if not target_minutes or target_minutes <= 0:
        return "green"
    pct = dwell_minutes / float(target_minutes)
    if pct > 0.8:
        return "red"
    if pct >= 0.5:
        return "amber"
    return "green"


def station_queue(db, store_id: str, code: str) -> List[dict]:
    """Jobs currently AT `code` for a store, oldest-first (most urgent first).

    Each row carries the time-at-station (minutes) + an SLA chip computed from
    the station's target_dwell_minutes. Fail-soft []."""
    if db is None or not store_id:
        return []
    code_u = (code or "").strip().upper()
    target = 0
    for s in list_stations(db, store_id):
        if s.get("code") == code_u:
            target = int(s.get("target_dwell_minutes") or 0)
            break
    try:
        rows = list(
            db.get_collection("workshop_jobs").find(
                {"store_id": store_id, "current_station": code_u}
            )
        )
    except Exception:  # noqa: BLE001
        return []

    now = datetime.now()
    out: List[dict] = []
    for r in rows:
        ts = (r.get("station_timestamps") or {}).get(code_u)
        dwell_ms = _dwell_ms(ts, now)
        dwell_min = round((dwell_ms or 0) / 60000.0, 1)
        out.append(
            {
                "job_id": r.get("job_id"),
                "job_number": r.get("job_number"),
                "customer_name": r.get("customer_name") or "",
                "current_station": code_u,
                "entered_at": ts,
                "dwell_minutes": dwell_min,
                "sla_minutes": target,
                "sla_chip": dwell_chip(target, dwell_min),
                "status": r.get("status"),
            }
        )
    out.sort(key=lambda x: x.get("entered_at") or "")
    return out


def station_kpis(jobs: List[dict], stations: List[dict]) -> dict:
    """Per-station live counts + average dwell from a pre-fetched job list.

    Pure: takes the store's jobs + its station config, returns
    {per_station_counts:{CODE:int}, avg_dwell_by_station:{CODE:minutes}}.
    avg_dwell uses the COMPLETED dwell (station_dwell_ms) recorded when a job
    left each station, so it reflects historical throughput, not jobs in flight.
    """
    codes = [s.get("code") for s in (stations or [])] or list(VALID_STATION_CODES)
    counts: Dict[str, int] = {c: 0 for c in codes}
    dwell_acc: Dict[str, List[float]] = {c: [] for c in codes}

    for j in jobs or []:
        cur = (j.get("current_station") or "").strip().upper()
        if cur in counts:
            counts[cur] += 1
        dwells = j.get("station_dwell_ms") or {}
        if isinstance(dwells, dict):
            for c, ms in dwells.items():
                cu = (c or "").strip().upper()
                if cu in dwell_acc and isinstance(ms, (int, float)) and ms >= 0:
                    dwell_acc[cu].append(ms / 60000.0)

    avg_dwell: Dict[str, Optional[float]] = {}
    for c, samples in dwell_acc.items():
        avg_dwell[c] = round(sum(samples) / len(samples), 1) if samples else None

    return {"per_station_counts": counts, "avg_dwell_by_station": avg_dwell}
