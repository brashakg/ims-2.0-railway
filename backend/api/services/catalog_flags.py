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
(fix_flags -> _settle); closing the task from the Tasks screen closes its
open flags (on_task_closed): completed -> fixed, cancelled -> withdrawn, so a
product never stays flagged with no live task and can be flagged again.
There is no second copy of either half.

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


def _roles(user: Optional[Dict[str, Any]]) -> set:
    return {str(r).strip().upper() for r in ((user or {}).get("roles") or [])}


def is_flag_task(task: Optional[Dict[str, Any]]) -> bool:
    return ((task or {}).get("payload") or {}).get("kind") == TASK_KIND


def handles_flags(user: Optional[Dict[str, Any]]) -> bool:
    """Who sees a flag task at all (its note, its products): the people who
    raise flags and the people who clear them. Counter roles never handle
    flags (owner ruling)."""
    return bool(_roles(user) & {"SUPERADMIN", *FLAGGER_ROLES, *FIXER_ROLES})


def clears_flags(user: Optional[Dict[str, Any]]) -> bool:
    """THE rule for who may clear a flag, by every door: Mark fixed (POST
    /catalog/flags/fix, require_roles(*FIXER_ROLES)) and closing its task on
    the Tasks screen (complete = fixed, cancel = withdrawn)."""
    return bool(_roles(user) & {"SUPERADMIN", *FIXER_ROLES})


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
    """The flagger's name through THE users name helper: the request's
    current_user is the token payload, which has no full_name."""
    from .name_resolver import actor_name

    return actor_name(_deps().get_db(), actor)


def _product_label(doc: Dict[str, Any]) -> str:
    name = doc.get("name") or " ".join(
        str(x) for x in (doc.get("brand"), doc.get("model")) if x
    )
    return f"{name or 'Product'} ({doc.get('sku') or doc.get('product_id')})"


def _assignee(created_by: Optional[str]) -> Optional[str]:
    """THE rule for who gets a flag task: the PERSON who added the product
    (owner rule: tasks go to people, not titles) when they are active and may
    clear a flag (clears_flags -- a catalogue manager or an admin), so the
    person the task names can open it, Mark fixed and close it. Anyone else
    -- gone, switched off, or a role that can neither open nor close a flag
    task (an area manager or accountant who bought it on a PO, a store
    manager, a counter role) -- hands it to an active CATALOG_MANAGER through
    the task person helper; else nobody."""
    if created_by and created_by not in ("system", "SYSTEM"):
        try:
            users = _deps().get_user_repository()
            user = users.find_by_id(created_by) if users is not None else None
        except Exception:  # noqa: BLE001
            user = None
        if user and user.get("is_active", True) is not False and clears_flags(user):
            return created_by
    from .task_triggers import _person_holding

    return _person_holding("CATALOG_MANAGER", None, any_store=True)


def raise_flags(
    product_ids: List[str],
    flag_type: str,
    note: Optional[str],
    actor: Dict[str, Any],
    *,
    apply: bool = True,
) -> Dict[str, Any]:
    """Flag each product; one task per person. Per-product results:
    done / already (that type is already open on it) / refused (+ reason).
    `tasks` names each person (assigned_to + assigned_to_name) and how many
    products they get. apply=False is the Flag dialog's preview: the same
    rule (_assignee), nothing written ('done' reads 'would be flagged').
    Raises ValueError for a bad type or a missing note."""
    if flag_type not in FLAG_TYPES:
        raise ValueError("Unknown flag type.")
    note = (note or "").strip()[:NOTE_MAX]
    if apply and flag_type in NOTE_REQUIRED and not note:
        raise ValueError("Write a note for 'Something else'.")
    coll = _products()
    if coll is None:
        raise ValueError("The catalogue is not reachable right now.")

    action_id = uuid.uuid4().hex[:12]
    results: List[Dict[str, Any]] = []
    groups: "OrderedDict[Optional[str], List[Dict[str, Any]]]" = OrderedDict()
    deps = _deps()
    by_name = _actor_name(actor) if apply else ""
    no_open_of_type = {"$not": {"$elemMatch": {"type": flag_type, "status": "open"}}}
    for pid in list(OrderedDict.fromkeys(p for p in product_ids if p))[:BULK_CAP]:
        doc = coll.find_one({"product_id": pid})
        if doc is None:
            results.append({"product_id": pid, "status": "refused", "reason": "Product not found"})
            continue
        if doc.get("deleted_at"):
            results.append({"product_id": pid, "status": "refused", "reason": "Archived"})
            continue
        if apply:
            # ONE guarded write claims the product (never read-then-write):
            # of two people flagging the same type at once, one gets 'already'.
            flag = {
                "flag_id": uuid.uuid4().hex[:12],
                "type": flag_type,
                "note": note,
                "by": actor.get("user_id"),
                "by_name": by_name,
                "at": _now(),
                "status": "open",
                "fixed_by": None,
                "fixed_at": None,
                "task_id": None,  # set below, once its task exists
            }
            claimed = coll.update_one(
                {"product_id": pid, "flags": no_open_of_type}, {"$push": {"flags": flag}}
            ).modified_count
            doc = dict(doc, _flag_id=flag["flag_id"])
        else:
            claimed = coll.count_documents({"product_id": pid, "flags": no_open_of_type})
        if not claimed:
            results.append({"product_id": pid, "status": "already"})
            continue
        groups.setdefault(_assignee(doc.get("created_by")), []).append(doc)
        results.append({"product_id": pid, "status": "done"})

    from .name_resolver import user_name_map

    names = user_name_map(deps.get_db(), [a for a in groups if a])
    if not apply:
        return {
            "results": results,
            "tasks": [
                {
                    "task_id": None,
                    "assigned_to": a,
                    "assigned_to_name": names.get(str(a)) if a else None,
                    "product_count": len(docs),
                }
                for a, docs in groups.items()
            ],
        }

    label = FLAG_TYPES[flag_type]
    tasks: List[Dict[str, Any]] = []
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
        if task_id:
            for d in docs:  # this action's own flag only: one element, by its id
                coll.update_one(
                    {"product_id": d.get("product_id"), "flags.flag_id": d["_flag_id"]},
                    {"$set": {"flags.$.task_id": task_id}},
                )
            # A flag Mark-fixed in the moment before its task existed.
            _settle(coll, [task_id], actor)
        tasks.append(
            {
                "task_id": task_id,
                "assigned_to": assignee,
                "assigned_to_name": names.get(str(assignee)) if assignee else None,
                "product_count": n,
            }
        )

    if groups:
        _bust_list_cache()
    return {"results": results, "tasks": tasks}


def _fix(
    coll, doc: Dict[str, Any], wanted, actor: Dict[str, Any], status: str = "fixed"
) -> List[str]:
    """Close the open flags on `doc` that `wanted(flag)` picks: `status`
    'fixed' (Mark fixed, a completed task) or 'withdrawn' (a cancelled task).
    Returns the task ids of the flags THIS call closed. Each flag is closed
    by its own write on that one element, and only while it is still open
    -- never the whole list from a stale read, so a flag raised meanwhile is
    never wiped and one closed meanwhile is never reopened. Not an edit."""
    now = _now()
    touched: List[str] = []
    for f in doc.get("flags") or []:
        if not (isinstance(f, dict) and f.get("status") == "open" and wanted(f)):
            continue
        res = coll.update_one(
            {
                "product_id": doc.get("product_id"),
                "flags": {"$elemMatch": {"flag_id": f.get("flag_id"), "status": "open"}},
            },
            {
                "$set": {
                    "flags.$.status": status,
                    "flags.$.fixed_by": actor.get("user_id"),
                    "flags.$.fixed_at": now,
                }
            },
        )
        if res.modified_count:
            touched.append(f.get("task_id"))
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


def on_task_closed(task: Dict[str, Any], actor: Dict[str, Any], status: str) -> int:
    """THE RULE, task side: closing a flag task from the Tasks screen closes
    every flag it raised that is still open -- COMPLETED marks them fixed,
    CANCELLED withdraws them (the product can be flagged again). Returns how
    many products changed. Never raises."""
    try:
        if ((task or {}).get("payload") or {}).get("kind") != TASK_KIND:
            return 0
        flag_status = {"COMPLETED": "fixed", "CANCELLED": "withdrawn"}.get(
            str(status or "").upper()
        )
        tid = task.get("task_id")
        coll = _products()
        if not tid or coll is None or flag_status is None:
            return 0
        changed = 0
        for doc in list(coll.find({"flags": {"$elemMatch": {"task_id": tid, "status": "open"}}})):
            if _fix(coll, doc, lambda f: f.get("task_id") == tid, actor, flag_status):
                changed += 1
        if changed:
            _bust_list_cache()
        return changed
    except Exception:  # noqa: BLE001 -- the task's close stands
        logger.warning("[FLAGS] closing flags of a closed task failed", exc_info=True)
        return 0
