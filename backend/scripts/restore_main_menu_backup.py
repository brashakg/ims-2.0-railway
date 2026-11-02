#!/usr/bin/env python3
"""
IMS 2.0 - Put the website main menu back from a backup (newest by default)
================================================================
Runbook-only one-off. NOT in CI. ASCII only (Windows cp1252).

Every LIVE overwrite of the generated website menu first saves the Shopify
menu it replaces in ``ecom_menu_backups`` (services/generated_menu; every
adoption backup is kept, plus the 20 newest others). This script picks one
backup for the handle -- the NEWEST by default; ``--adopted`` the one taken
when IMS first took the menu over (the owner's hand-made menu); ``--first``
the oldest; ``--taken-at`` an exact one from ``--list`` -- maps it to
Shopify's MenuItemUpdateInput (title, type, url, resourceId, tags, items)
and runs menuUpdate on the backup's own Shopify menu id.

--list: prints every backup (taken at, adopted or not, top-level items).

DRY RUN (default): prints which backup was chosen and the payload. Nothing
is sent.

--apply: sends the menuUpdate (LIVE gates only) and clears the generated
menu's pushed hash, so no automatic run overwrites the restored menu --
the next overwrite is again a person's "Regenerate & push now".

USAGE
    railway run -- ".venv\\Scripts\\python.exe" backend/scripts/restore_main_menu_backup.py --list
    ... backend/scripts/restore_main_menu_backup.py --adopted            (dry run)
    ... backend/scripts/restore_main_menu_backup.py --adopted --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

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


def backups(db, handle: str) -> List[Dict[str, Any]]:
    """Every backup of ``handle``, oldest first."""
    from api.services import generated_menu

    return sorted(db[generated_menu.BACKUPS].find({"handle": handle}), key=lambda r: str(r.get("taken_at")))


def choose_backup(db, handle: str, which: str = "newest", taken_at: Optional[str] = None):
    """newest (default) | adopted (the OLDEST adoption backup: the hand-made
    menu IMS first replaced) | first (the oldest of all) | an exact
    ``taken_at`` as --list prints it. None when there is no such backup."""
    rows = backups(db, handle)
    if taken_at:
        return next((r for r in rows if str(r.get("taken_at")) == taken_at), None)
    if which == "adopted":
        rows = [r for r in rows if r.get("adopted")]
        return rows[0] if rows else None
    if not rows:
        return None
    return rows[0] if which == "first" else rows[-1]


def payload(backup: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": backup["shopify_menu_id"], "title": backup.get("title") or backup["handle"],
            "handle": backup["handle"], "items": update_items(backup.get("items") or [])}


def list_backups(db, handle: str = "main-menu") -> List[Dict[str, Any]]:
    rows = backups(db, handle)
    for r in rows:
        print(f"  {r.get('taken_at')}  {'ADOPTED ' if r.get('adopted') else ''}"
              f"{len(r.get('items') or [])} top-level items: "
              f"{', '.join(str(i.get('title')) for i in r.get('items') or [])}")
    if not rows:
        print(f"  (no backup of {handle})")
    return rows


async def run(db, apply: bool, handle: str = "main-menu", which: str = "newest",
              taken_at: Optional[str] = None) -> Dict[str, Any]:
    from api.services import generated_menu
    from api.services.shopify_push import _graphql, _live_or_reason
    from api.services.shopify_push.queries import _MENU_UPDATE
    from api.services.shopify_push.transport import _user_errors

    backup = choose_backup(db, handle, which, taken_at)
    if backup is None:
        print(f"[refused] no {taken_at or which} backup of {handle} in {generated_menu.BACKUPS} (see --list).")
        return {"restored": False, "reason": "no backup"}
    body = payload(backup)
    adopted = " (taken when IMS adopted the menu)" if backup.get("adopted") else ""
    print(f"CHOSEN: the {taken_at or which} backup{adopted}.")
    print(f"BACKUP taken {backup.get('taken_at')} of {body['handle']} ({body['id']}), "
          f"{len(body['items'])} top-level items:")
    for it in body["items"]:
        print(f"  - {it['title']}  ({len(it.get('items') or [])} below)")
    if not apply:
        print("\nDRY RUN: nothing sent. Re-run with --apply to put this menu back on the website.")
        return {"restored": False, "reason": "dry-run", "payload": body, "taken_at": backup.get("taken_at")}
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
    return {"restored": True, "payload": body, "taken_at": backup.get("taken_at")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Restore the website main menu from a backup.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--handle", default="main-menu")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--list", action="store_true", help="print every backup and stop")
    pick = parser.add_mutually_exclusive_group()
    pick.add_argument("--adopted", action="store_true", help="the backup taken when IMS adopted the menu")
    pick.add_argument("--first", action="store_true", help="the oldest backup")
    pick.add_argument("--taken-at", default=None, help="an exact taken_at, as --list prints it")
    args = parser.parse_args(argv)
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    if args.list:
        list_backups(db, args.handle)
        return 0
    which = "adopted" if args.adopted else "first" if args.first else "newest"
    asyncio.run(run(db, args.apply, args.handle, which, args.taken_at))
    return 0


if __name__ == "__main__":
    sys.exit(main())
