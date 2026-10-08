"""Fold system settings saved under the old _id into the one document.

WHY
---
/admin/system/settings used to save under _id "system_settings" while
/settings/system and the /health auto-logout reader use _id "default", so a
value saved through that door was never read. Both doors now use "default"
(owner ruling 2026-10-08). This script keeps any value saved under the old id.

SAFETY
------
  * Dry run by default. Nothing is written without --apply.
  * Never overwrites: a key the "default" document already holds keeps its
    value (that is the value in force today) and is only reported.
  * low_stock_alert_enabled is not carried over - the setting was removed.
  * Idempotent: once folded, the old document is gone and a re-run is a no-op.

USAGE
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/migrate_system_settings_one_id.py
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/migrate_system_settings_one_id.py --apply
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

ONE_ID = "default"
OLD_ID = "system_settings"
REMOVED = {"_id", "low_stock_alert_enabled"}


def run(coll, *, apply: bool) -> Dict[str, Any]:
    old = coll.find_one({"_id": OLD_ID}) or {}
    one = coll.find_one({"_id": ONE_ID}) or {}
    copied = {k: v for k, v in old.items() if k not in REMOVED and k not in one}
    kept = {k: one[k] for k in old if k not in REMOVED and k in one}
    if apply and old:
        if copied:
            coll.update_one({"_id": ONE_ID}, {"$set": copied}, upsert=True)
        coll.delete_one({"_id": OLD_ID})
    return {"copied": copied, "kept": kept, "old_document_found": bool(old), "applied": apply}


def main() -> int:
    apply = "--apply" in sys.argv
    uri = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGO_URL")
    if not uri:
        print("No MONGO_PUBLIC_URL / MONGO_URL in the environment.")
        return 2

    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=20000)["ims_2_0"]
    print("DRY RUN -- nothing will be written." if not apply else "APPLYING.")
    print(run(db["system_settings"], apply=apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
