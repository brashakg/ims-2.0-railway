#!/usr/bin/env python3
"""
IMS 2.0 - Put the website main menu back from its newest backup
================================================================
Runbook-only one-off. NOT in CI. ASCII only (Windows cp1252).

Every LIVE overwrite of the generated website menu first saves the Shopify
menu it replaces in ``ecom_menu_backups`` (services/generated_menu). This
script takes the NEWEST backup for the handle, maps it to Shopify's
MenuItemUpdateInput (title, type, url, resourceId, tags, items) and runs
menuUpdate on the backup's own Shopify menu id.

DRY RUN (default): prints the backup and the payload. Nothing is sent.

--apply: sends the menuUpdate (LIVE gates only) and clears the generated
menu's pushed hash, so no automatic run overwrites the restored menu --
the next overwrite is again a person's "Regenerate & push now".

USAGE
    railway run -- ".venv\\Scripts\\python.exe" backend/scripts/restore_main_menu_backup.py
    ... backend/scripts/restore_main_menu_backup.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
REPO = os.path.dirname(BACKEND)
for p in (BACKEND, os.path.join(REPO, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)


def update_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A backed-up Shopify item tree -> [MenuItemUpdateInput]. Pure."""
    out: List[Dict[str, Any]] = []
    for n in items or []:
        it: Dict[str, Any] = {"title": n.get("title") or "", "type": n.get("type") or "HTTP"}
        for k in ("url", "resourceId"):
            if n.get(k):
                it[k] = n[k]
        if n.get("tags"):
            it["tags"] = list(n["tags"])
        kids = update_items(n.get("items") or [])
        if kids:
            it["items"] = kids
        out.append(it)
    return out


def newest_backup(db, handle: str):
    from api.services import generated_menu

    rows = list(db[generated_menu.BACKUPS].find({"handle": handle}))
    return max(rows, key=lambda r: r.get("taken_at")) if rows else None


def payload(backup: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": backup["shopify_menu_id"], "title": backup.get("title") or backup["handle"],
            "handle": backup["handle"], "items": update_items(backup.get("items") or [])}


async def run(db, apply: bool, handle: str = "main-menu") -> Dict[str, Any]:
    from api.services import generated_menu
    from api.services.shopify_push import _graphql, _live_or_reason
    from api.services.shopify_push.queries import _MENU_UPDATE
    from api.services.shopify_push.transport import _user_errors

    backup = newest_backup(db, handle)
    if backup is None:
        print(f"[refused] no backup of {handle} in {generated_menu.BACKUPS}.")
        return {"restored": False, "reason": "no backup"}
    body = payload(backup)
    print(f"BACKUP taken {backup.get('taken_at')} of {body['handle']} ({body['id']}), "
          f"{len(body['items'])} top-level items:")
    for it in body["items"]:
        print(f"  - {it['title']}  ({len(it.get('items') or [])} below)")
    if not apply:
        print("\nDRY RUN: nothing sent. Re-run with --apply to put this menu back on the website.")
        return {"restored": False, "reason": "dry-run", "payload": body}
    live, why = _live_or_reason(db)
    if not live:
        print(f"\n[refused] Shopify writes are not LIVE: {why}")
        return {"restored": False, "reason": why}
    res = await _graphql(db, _MENU_UPDATE, body)
    err = _user_errors(res, "menuUpdate")
    if err:
        print(f"\n[failed] {err}")
        return {"restored": False, "reason": err}
    db[generated_menu.MENUS].update_one({"handle": handle, generated_menu.GENERATED: True},
                                        {"$unset": {"pushed_hash": ""}})
    print("\nRESTORED. The next overwrite waits for a person's Regenerate & push now.")
    return {"restored": True, "payload": body}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Restore the website main menu from its newest backup.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--handle", default="main-menu")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    asyncio.run(run(db, args.apply, args.handle))
    return 0


if __name__ == "__main__":
    sys.exit(main())
