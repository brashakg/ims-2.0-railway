"""
IMS 2.0 - Catalog Autopilot router (Phase 1)
============================================
Operator enters brand + model -> we search prioritised sources, score
candidates, and a human approves before anything is published. Gated to
catalog roles. Persists jobs/candidates in Mongo when available; search itself
is fail-soft and works even without Mongo (it reads the BVI catalog).
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles
from ..dependencies import get_db
from ..services import catalog_autopilot as ap

router = APIRouter()

_CATALOG_ROLES = ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER")
_DECISIONS = {"APPROVE", "REJECT", "SPECS_ONLY", "NEEDS_REVIEW"}


def _db():
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    return db


class JobCreate(BaseModel):
    brand: str
    model: str
    color: Optional[str] = ""
    size: Optional[str] = ""
    # v2: the category the operator is searching in (a Quick Add picker code
    # like "SG", a canonical key like "SUNGLASS", or a human label). Optional;
    # when present it is normalised to the canonical product_master key,
    # persisted on the job, used to refine source queries, and stamped onto
    # every returned candidate so downstream field-mapping never guesses.
    category: Optional[str] = ""


def _normalise_category(raw: str) -> str:
    """Best-effort canonicalisation via the product_master registry ("SG" ->
    "SUNGLASS"). Unknown values pass through upper-cased (harmless: the FE
    mapper ignores categories it can't resolve). Never raises."""
    value = (raw or "").strip()
    if not value:
        return ""
    try:
        from ..services.product_master import resolve_category

        canonical = resolve_category(value)
        if canonical:
            return canonical
    except Exception:  # noqa: BLE001
        pass
    return value.upper().replace("-", "_").replace(" ", "_")


class Decision(BaseModel):
    decision: str = Field(
        ..., description="APPROVE | REJECT | SPECS_ONLY | NEEDS_REVIEW"
    )
    rights_confirmed: bool = False  # required to use an UNVERIFIED-source image
    note: Optional[str] = None


@router.get("/sources")
async def list_sources(current_user: dict = Depends(require_roles(*_CATALOG_ROLES))):
    """Which sources exist and whether they're active right now."""
    return {"sources": ap._provider_status()}


@router.post("/jobs", status_code=201)
async def create_job(
    body: JobCreate, current_user: dict = Depends(require_roles(*_CATALOG_ROLES))
):
    """Create an ingestion job: search sources for brand+model and return scored
    candidates. Persists when Mongo is available (so decisions can reference
    them); otherwise returns candidates without persistence."""
    if not body.brand.strip() or not body.model.strip():
        raise HTTPException(status_code=400, detail="brand and model are required")

    category = _normalise_category(body.category or "")
    result = ap.run_search(
        body.brand.strip(),
        body.model.strip(),
        (body.color or "").strip(),
        (body.size or "").strip(),
        category=category,
    )

    job_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    cands = result["candidates"]
    for c in cands:
        c["candidate_id"] = str(uuid.uuid4())
        c["job_id"] = job_id
        c["decision"] = None

    db = _db()
    persisted = False
    if db is not None:
        try:
            db.get_collection("catalog_ingest_jobs").insert_one(
                {
                    "job_id": job_id,
                    "brand": body.brand.strip(),
                    "model": body.model.strip(),
                    "color": (body.color or "").strip(),
                    "size": (body.size or "").strip(),
                    "category": category,
                    "status": "SEARCHED",
                    "candidate_count": len(cands),
                    "created_by": current_user.get("user_id"),
                    "created_at": now,
                }
            )
            if cands:
                db.get_collection("catalog_ingest_candidates").insert_many(
                    [{k: v for k, v in c.items() if k != "_id"} for c in cands]
                )
            persisted = True
        except Exception:  # noqa: BLE001
            persisted = False

    # Strip Mongo _id from the response payload.
    for c in cands:
        c.pop("_id", None)
    return {
        "job_id": job_id,
        "query": result["query"],
        "candidates": cands,
        "sources": result["sources"],
        "candidate_count": len(cands),
        "persisted": persisted,
    }


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(30, ge=1, le=100),
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    db = _db()
    if db is None:
        return {"jobs": [], "total": 0}
    try:
        jobs = list(
            db.get_collection("catalog_ingest_jobs")
            .find({}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
    except Exception:  # noqa: BLE001
        jobs = []
    return {"jobs": jobs, "total": len(jobs)}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str, current_user: dict = Depends(require_roles(*_CATALOG_ROLES))
):
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    job = db.get_collection("catalog_ingest_jobs").find_one(
        {"job_id": job_id}, {"_id": 0}
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    cands = list(
        db.get_collection("catalog_ingest_candidates").find(
            {"job_id": job_id}, {"_id": 0}
        )
    )
    cands.sort(key=lambda c: c.get("score", 0), reverse=True)
    return {"job": job, "candidates": cands}


@router.post("/candidates/{candidate_id}/decision")
async def decide_candidate(
    candidate_id: str,
    body: Decision,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Record a reviewer decision. Using an UNVERIFIED-source image requires
    rights_confirmed (copyright guard)."""
    decision = (body.decision or "").upper()
    if decision not in _DECISIONS:
        raise HTTPException(
            status_code=400, detail=f"decision must be one of {sorted(_DECISIONS)}"
        )

    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    coll = db.get_collection("catalog_ingest_candidates")
    cand = coll.find_one({"candidate_id": candidate_id}, {"_id": 0})
    if cand is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Copyright guard: approving with image use from an unverified source needs
    # an explicit rights confirmation.
    uses_image = decision == "APPROVE"
    if uses_image and not ap.image_use_allowed(
        cand.get("source_class", ap.UNVERIFIED), body.rights_confirmed
    ):
        raise HTTPException(
            status_code=400,
            detail="This source is unverified; confirm image rights to approve, or use SPECS_ONLY.",
        )

    coll.update_one(
        {"candidate_id": candidate_id},
        {
            "$set": {
                "decision": decision,
                "rights_confirmed": bool(body.rights_confirmed),
                "decided_by": current_user.get("user_id"),
                "decided_at": datetime.now().isoformat(),
                "decision_note": body.note,
            }
        },
    )
    return {"candidate_id": candidate_id, "decision": decision}
