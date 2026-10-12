"""Catalogue flags: 'this product needs work' (owner 2026-10-12, mockup
artboards Main + FlagDialog).

An ADMIN, SUPERADMIN or STORE_MANAGER flags products from the Catalog page
(never a counter role, never a CATALOG_MANAGER -- the cataloguers CLEAR flags,
they do not raise them). The flags live ON THE PRODUCT (the spine `products`
doc) as a list; the person who added each product gets ONE task per flag
action listing their products, the note and a link to each edit page. The
cataloguer presses Mark fixed on the edit page.

THE ONE RULE linking a flag task to its flags: the task is open exactly while
one of its flags is open. Marking the last flag fixed completes the task
(fix_flags -> _settle), and completing the task from the Tasks screen fixes
its flags (on_task_completed). There is no second copy of either half.

Flags never block anything: a flagged product keeps selling at the till and
stays online (owner ruling). Writing a flag is not a catalogue edit either --
it never touches updated_at / updated_by, so it cannot reorder Recently
edited.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

FLAG_TYPES: "OrderedDict[str, str]" = OrderedDict(
    [
        ("needs_revision", "Needs revision"),
        ("photo_redo", "Needs photo redo"),
        ("price_check", "Price check"),
        ("wrong_category", "Wrong category / duplicate"),
        ("other", "Something else"),
    ]
)
NOTE_REQUIRED = {"other"}
NOTE_MAX = 500
BULK_CAP = 200

# Who may raise a flag / mark one fixed. SUPERADMIN passes require_roles on
# its own; the rbac_policy rows say the same (pinned by test).
FLAGGER_ROLES = ("ADMIN", "STORE_MANAGER")
FIXER_ROLES = ("ADMIN", "CATALOG_MANAGER")

TASK_KIND = "catalog_flag"


def _deps():
    from .. import dependencies as deps

    return deps


def _products():
    repo = _deps().get_product_repository()
    return getattr(repo, "collection", None)


def _bust_list_cache() -> None:
    try:
        from .cache import cache

        cache.delete_pattern("products:*")
    except Exception:  # noqa: BLE001
        pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _actor_name(actor: Dict[str, Any]) -> str:
    return actor.get("full_name") or actor.get("username") or actor.get("user_id") or ""


def _product_label(doc: Dict[str, Any]) -> str:
    name = doc.get("name") or " ".join(
        str(x) for x in (doc.get("brand"), doc.get("model")) if x
    )
    return f"{name or 'Product'} ({doc.get('sku') or doc.get('product_id')})"


def _assignee(created_by: Optional[str]) -> Optional[str]:
    """The PERSON who added the product (owner rule: tasks go to people, not
    titles); when that user is gone (unknown or switched off), an active
    CATALOG_MANAGER through the task person helper; else nobody."""
    if created_by and created_by not in ("system", "SYSTEM"):
        try:
            users = _deps().get_user_repository()
            user = users.find_by_id(created_by) if users is not None else None
        except Exception:  # noqa: BLE001
            user = None
        if user and user.get("is_active", True) is not False:
            return created_by
    from .task_triggers import _person_holding

    return _person_holding("CATALOG_MANAGER", None, any_store=True)


def raise_flags(
    product_ids: List[str], flag_type: str, note: Optional[str], actor: Dict[str, Any]
) -> Dict[str, Any]:
    """Flag each product; one task per person. Per-product results:
    done / already (that type is already open on it) / refused (+ reason).
    Raises ValueError for a bad type or a missing note."""
    if flag_type not in FLAG_TYPES:
        raise ValueError("Unknown flag type.")
    note = (note or "").strip()[:NOTE_MAX]
    if flag_type in NOTE_REQUIRED and not note:
        raise ValueError("Write a note for 'Something else'.")
    coll = _products()
    if coll is None:
        raise ValueError("The catalogue is not reachable right now.")

    action_id = uuid.uuid4().hex[:12]
    results: List[Dict[str, Any]] = []
    groups: "OrderedDict[Optional[str], List[Dict[str, Any]]]" = OrderedDict()
    for pid in list(OrderedDict.fromkeys(p for p in product_ids if p))[:BULK_CAP]:
        doc = coll.find_one({"product_id": pid})
        if doc is None:
            results.append({"product_id": pid, "status": "refused", "reason": "Product not found"})
            continue
        if doc.get("deleted_at"):
            results.append({"product_id": pid, "status": "refused", "reason": "Archived"})
            continue
        if any(
            f.get("type") == flag_type and f.get("status") == "open"
            for f in doc.get("flags") or []
            if isinstance(f, dict)
        ):
            results.append({"product_id": pid, "status": "already"})
            continue
        groups.setdefault(_assignee(doc.get("created_by")), []).append(doc)
        results.append({"product_id": pid, "status": "done"})

    label = FLAG_TYPES[flag_type]
    by_name = _actor_name(actor)
    tasks: List[Dict[str, Any]] = []
    deps = _deps()
    task_repo = deps.get_task_repository()
    from .task_triggers import create_system_task

    for assignee, docs in groups.items():
        n = len(docs)
        lines = [f"{label}: {n} product{'s' if n != 1 else ''} you added need work."]
        if note:
            lines.append(f"Note from {by_name}: {note}")
        lines += [f"- {_product_label(d)}" for d in docs]
        lines.append("Fix each one on its edit page, then press Mark fixed there.")
        task = create_system_task(
            task_repo,
            title=f"{label}: {n} product{'s' if n != 1 else ''} to fix",
            description="\n".join(lines),
            priority="P3",
            category="Catalogue",
            store_id=None,
            dedupe_ref=f"{TASK_KIND}:{action_id}:{assignee or 'nobody'}",
            assigned_to=assignee,
            extra={
                "created_by": actor.get("user_id"),
                "created_by_name": by_name,
                "links": [
                    {
                        "label": _product_label(d),
                        "to": f"/catalog/add?edit={d.get('product_id')}",
                    }
                    for d in docs
                ],
                "payload": {
                    "kind": TASK_KIND,
                    "flag_type": flag_type,
                    "product_ids": [d.get("product_id") for d in docs],
                },
            },
        )
        task_id = (task or {}).get("task_id")
        for d in docs:
            coll.update_one(
                {"product_id": d.get("product_id")},
                {
                    "$push": {
                        "flags": {
                            "flag_id": uuid.uuid4().hex[:12],
                            "type": flag_type,
                            "note": note,
                            "by": actor.get("user_id"),
                            "by_name": by_name,
                            "at": _now(),
                            "status": "open",
                            "fixed_by": None,
                            "fixed_at": None,
                            "task_id": task_id,
                        }
                    }
                },
            )
        tasks.append({"task_id": task_id, "assigned_to": assignee, "product_count": n})

    if groups:
        _bust_list_cache()
    return {"results": results, "tasks": tasks}


def _fix(coll, doc: Dict[str, Any], wanted, actor: Dict[str, Any]) -> List[str]:
    """Mark the open flags on `doc` that `wanted(flag)` picks as fixed. Returns
    the task ids they carried. Writes the flags list only (not an edit)."""
    flags = [dict(f) for f in doc.get("flags") or [] if isinstance(f, dict)]
    touched: List[str] = []
    for f in flags:
        if f.get("status") == "open" and wanted(f):
            f.update(status="fixed", fixed_by=actor.get("user_id"), fixed_at=_now())
            touched.append(f.get("task_id"))
    if touched:
        coll.update_one({"product_id": doc.get("product_id")}, {"$set": {"flags": flags}})
    return [t for t in touched if t]


def _complete_task(task_id: str, actor: Dict[str, Any], note: str) -> None:
    repo = _deps().get_task_repository()
    task = repo.find_by_id(task_id) if repo is not None else None
    if not task or str(task.get("status", "")).upper() in ("COMPLETED", "CANCELLED"):
        return
    now = datetime.now()
    repo.update(
        task_id,
        {
            "status": "COMPLETED",
            "completed_at": now,
            "completed_by": actor.get("user_id"),
            "completion_notes": note,
            "history": (task.get("history") or [])
            + [{"action": "completed", "by": actor.get("user_id"), "notes": note, "at": now}],
        },
    )


def _settle(coll, task_ids: List[str], actor: Dict[str, Any]) -> None:
    """THE RULE, flag side: a flag task with no open flag left completes."""
    for tid in dict.fromkeys(task_ids):
        if coll.count_documents({"flags": {"$elemMatch": {"task_id": tid, "status": "open"}}}) == 0:
            _complete_task(tid, actor, "Every flagged product was marked fixed.")


def fix_flags(product_id: str, flag_ids: List[str], actor: Dict[str, Any]) -> Dict[str, Any]:
    """Mark fixed (the product edit page). Returns the product's flags."""
    coll = _products()
    doc = coll.find_one({"product_id": product_id}) if coll is not None else None
    if doc is None:
        raise LookupError("Product not found")
    wanted = set(flag_ids or [])
    task_ids = _fix(coll, doc, lambda f: f.get("flag_id") in wanted, actor)
    _settle(coll, task_ids, actor)
    _bust_list_cache()
    fresh = coll.find_one({"product_id": product_id}) or {}
    return {"product_id": product_id, "flags": fresh.get("flags") or []}


def on_task_completed(task: Dict[str, Any], actor: Dict[str, Any]) -> int:
    """THE RULE, task side: completing a flag task from the Tasks screen fixes
    every flag it raised. Returns how many products changed. Never raises."""
    try:
        if ((task or {}).get("payload") or {}).get("kind") != TASK_KIND:
            return 0
        tid = task.get("task_id")
        coll = _products()
        if not tid or coll is None:
            return 0
        changed = 0
        for doc in list(coll.find({"flags": {"$elemMatch": {"task_id": tid, "status": "open"}}})):
            if _fix(coll, doc, lambda f: f.get("task_id") == tid, actor):
                changed += 1
        if changed:
            _bust_list_cache()
        return changed
    except Exception:  # noqa: BLE001 -- the task completion stands
        logger.warning("[FLAGS] fixing flags of a completed task failed", exc_info=True)
        return 0
