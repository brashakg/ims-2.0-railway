# Collapse DUPLICATE agent_config docs down to exactly ONE per agent_id and
# stamp the intended enabled state, then (on --apply) add the unique index that
# stops duplicates from ever coming back.
#
# Why this exists
# ---------------
# seed_configs() historically did find_one() + insert_one() with no unique
# constraint. Railway boots 4 uvicorn workers that all run the FastAPI lifespan
# at once, so each worker's find_one() returned None before the others inserted
# -> up to 4 identical docs per agent_id. find_one() then returned a
# nondeterministic one; when it carried enabled=None/False the agent's tick was
# silently skipped. That is the live "duplicate agent_config with conflicting
# enabled values" finding.
#
# This script keeps the richest surviving doc per agent (most run history),
# forces the intended enabled state, deletes the rest, and builds the unique
# index. The code fix (config.py upsert + ensure_indexes) prevents recurrence;
# this heals the data that already drifted.
#
# Dry-run by default (prints before/after, writes NOTHING). Pass --apply to
# persist. Run against prod via Railway so creds are injected, e.g.:
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\dedupe_agent_config.py
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\dedupe_agent_config.py --apply
import os
import sys
from datetime import datetime, timezone

from pymongo import MongoClient

# Intended enabled state per canonical agent (owner-directed). Agents NOT in
# this map (unexpected agent_ids) are still de-duplicated, but their existing
# enabled value on the surviving doc is left untouched.
INTENDED_ENABLED = {
    "jarvis": True,
    "cortex": True,
    "sentinel": True,
    "nexus": True,
    "oracle": True,
    "taskmaster": True,
    "megaphone": True,
    "pixel": False,
}


def _keeper_sort_key(doc):
    """Rank docs within an agent_id group; the FIRST after sorting is kept.

    Preserve the richest history: most runs, then most recent run, then the
    doc carrying the most fields, then a deterministic _id tiebreak.
    """
    run_count = int(doc.get("run_count") or 0)
    last_run = doc.get("last_run")
    if isinstance(last_run, datetime):
        try:
            ts = last_run.timestamp()
        except (OverflowError, OSError, ValueError):
            ts = 0.0
    else:
        # No run yet -> rank behind any doc that has actually ticked.
        ts = 0.0
    return (
        -run_count,      # more runs first
        -ts,             # more recent run first
        -len(doc.keys()),  # richer doc first
        str(doc.get("_id")),  # deterministic tiebreak
    )


def main():
    apply = "--apply" in sys.argv
    url = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGODB_URL")
    if not url:
        print("ERROR: set MONGO_PUBLIC_URL or MONGODB_URL (use `railway run`)")
        return 1

    db = MongoClient(url, serverSelectionTimeoutMS=20000)["ims_2_0"]
    col = db["agent_config"]

    all_docs = list(col.find({}))
    if not all_docs:
        print("agent_config is EMPTY -- nothing to dedupe (a boot will seed it).")
        return 0

    # Group by agent_id.
    groups = {}
    for d in all_docs:
        groups.setdefault(d.get("agent_id"), []).append(d)

    print(f"agent_config: {len(all_docs)} docs across {len(groups)} agent_id(s)\n")

    to_delete_ids = []
    planned = []  # (agent_id, before_count, enabled_values, keeper_id, final_enabled)

    for agent_id, docs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        enabled_values = [d.get("enabled") for d in docs]
        docs_sorted = sorted(docs, key=_keeper_sort_key)
        keeper = docs_sorted[0]
        losers = docs_sorted[1:]

        if agent_id in INTENDED_ENABLED:
            final_enabled = INTENDED_ENABLED[agent_id]
        else:
            # Unknown agent: keep whatever the survivor already had (prefer
            # True if any duplicate was enabled).
            final_enabled = True if any(v is True for v in enabled_values) else keeper.get(
                "enabled", False
            )

        planned.append(
            (agent_id, len(docs), enabled_values, keeper.get("_id"), final_enabled)
        )
        to_delete_ids.extend(d.get("_id") for d in losers)

        flag = " <-- DUPLICATES" if len(docs) > 1 else ""
        print(
            f"  {str(agent_id):12} count={len(docs)} enabled_seen={enabled_values} "
            f"-> keep 1, enabled={final_enabled}{flag}"
        )

    dupes = sum(1 for p in planned if p[1] > 1)
    print(
        f"\nSUMMARY: {dupes} agent(s) have duplicates; "
        f"{len(to_delete_ids)} duplicate doc(s) would be deleted; "
        f"{len(planned)} agent(s) kept."
    )

    if not apply:
        print("\nDry-run only. Re-run with --apply to persist.")
        return 0

    # Apply: stamp the survivor's enabled state, then delete the losers.
    now = datetime.now(timezone.utc)
    for agent_id, _cnt, _seen, keeper_id, final_enabled in planned:
        col.update_one(
            {"_id": keeper_id},
            {
                "$set": {
                    "enabled": final_enabled,
                    "toggled_by": "dedupe_script",
                    "toggled_at": now,
                }
            },
        )
    if to_delete_ids:
        result = col.delete_many({"_id": {"$in": to_delete_ids}})
        print(f"\nAPPLIED: deleted {result.deleted_count} duplicate doc(s).")
    else:
        print("\nAPPLIED: no duplicates to delete; enabled states stamped.")

    # Now that each agent_id is unique, add the guard so this can't recur.
    try:
        col.create_index("agent_id", unique=True, name="uq_agent_id")
        print("APPLIED: unique index uq_agent_id on agent_config.agent_id.")
    except Exception as e:
        print(f"WARNING: could not create unique index ({e}). Re-run after review.")

    # Verify.
    after = list(col.find({}, {"agent_id": 1, "enabled": 1, "_id": 0}))
    counts = {}
    for d in after:
        counts[d.get("agent_id")] = counts.get(d.get("agent_id"), 0) + 1
    dup_after = {k: v for k, v in counts.items() if v > 1}
    print(f"\nAFTER: {len(after)} docs; duplicates remaining: {dup_after or 'NONE'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
