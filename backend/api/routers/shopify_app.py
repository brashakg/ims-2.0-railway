"""
IMS 2.0 - the page Shopify admin shows inside the "IMS 2.0 Sync" app
=====================================================================
GET /api/v1/shopify/app is the app's App URL. When the owner clicks the app in
Shopify admin, Shopify loads this URL in an iframe with ?shop&host&timestamp
&hmac (plus embedded / id_token / session / locale). We answer with ONE
server-rendered page: a welcome line, an "Open IMS" button and -- only when the
request is PROVABLY Shopify's, for OUR shop -- a read-only connection status
panel.

Proof is Shopify's documented app-load check: drop ``hmac``, sort the rest by
key, join ``key=value`` with ``&``, HMAC-SHA256 hex with the app's client
secret, constant-time compare; plus ``shop`` == the configured store and
``timestamp`` within five minutes. Anything else gets the welcome text and
nothing more -- never an error saying why (that would describe our config).

PUBLIC in rbac_policy: there is no IMS login inside Shopify's iframe; the HMAC
is the gate, as on the webhook receiver. Framing: this route alone sends CSP
``frame-ancestors https://admin.shopify.com https://{verified shop}`` and no
X-Frame-Options; ``add_security_headers`` in main.py keeps DENY everywhere
else.

The status panel REUSES the existing readers (credential resolver, push gates,
location verdict, live-sync run ledger, webhook registrar's dry-run read) --
read-only, no secrets, no customer data, cached ~60 s, and every line fails
soft to "could not check" (amber), never a 500.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..services import shopify_live_sync as live_sync
from ..services import shopify_push
from ..services.shopify_auth import _resolve_shop, resolve_shopify_credentials
from ..utils.event_time import parse_event_utc
from ..utils.ist import IST

logger = logging.getLogger(__name__)

router = APIRouter()

# The ONE spelling of this route's path: main.py's framing exemption reads it.
PAGE_PATH = "/api/v1/shopify/app"
IMS_URL = "https://app.uniparallel.com"
WELCOME = "IMS 2.0 Sync connects Better Vision to IMS. Manage products, stock and orders in IMS."

_MAX_SKEW_SECONDS = 300
_SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
_PRODUCTS_COUNT_QUERY = "query imsProductsCount { productsCount { count } }"

# ponytail: per-process cache (4 uvicorn workers -> at most 4 Shopify reads a
# minute); a shared Redis cache if this page ever gets real traffic.
_CACHE_SECONDS = 60
_cache: Dict[str, Any] = {"at": 0.0, "items": None, "checked": None}

Item = Tuple[str, str, str]  # (level ok|warn|bad, label, plain-English line)


# ---------------------------------------------------------------------------
# The app-load HMAC (shopify.dev: "Verify the request" for app loads / OAuth)
# ---------------------------------------------------------------------------


def verified_shop(
    pairs: Iterable[Tuple[str, str]], secret: str, expected_shop: str, now: float
) -> Optional[str]:
    """The shop when this query string is a fresh Shopify app load for OUR
    shop, else None. ``pairs`` are the DECODED query items, in order."""
    given: Optional[str] = None
    rest: Dict[str, str] = {}
    for key, value in pairs:
        if key == "hmac":
            if given is not None:
                return None
            given = value
        elif key in rest:
            # ponytail: a repeated key (Shopify's ids[] array form) is refused;
            # an app load sends none. Add the JSON-array rule if one ever does.
            return None
        else:
            rest[key] = value
    shop = (rest.get("shop") or "").strip().lower()
    expected = (expected_shop or "").strip().lower()
    if not (given and secret and expected and shop == expected and _SHOP_RE.match(shop)):
        return None
    try:
        if abs(now - int(rest.get("timestamp") or "")) > _MAX_SKEW_SECONDS:
            return None
    except ValueError:
        return None
    # Shopify's escaping, so a value cannot smuggle in a second pair.
    message = "&".join(
        "{}={}".format(
            k.replace("%", "%25").replace("=", "%3D"), v.replace("%", "%25")
        ).replace("&", "%26")
        for k, v in sorted(rest.items())
    )
    want = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want.encode("ascii"), given.strip().lower().encode("utf-8")):
        return None
    return shop


# ---------------------------------------------------------------------------
# The status checks -- each returns (level, line); _soft turns a raise amber
# ---------------------------------------------------------------------------


def _ist(value: Any) -> str:
    at = parse_event_utc(value)
    return at.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if at else "an unknown time"


def _name(store: Dict[str, Any]) -> str:
    return str(store.get("store_name") or store.get("store_code") or store.get("store_id"))


async def _connection(db) -> Tuple[str, str]:
    creds = resolve_shopify_credentials(db)
    if not creds:
        return "bad", "IMS cannot get a working Shopify key. The app's client ID and secret on Railway need checking."
    if creds.get("source") == "oauth":
        return "ok", "IMS has a working Shopify key."
    return "warn", "IMS is using an older stored Shopify key, which may have expired."


async def _writes(mode: Dict[str, Any]) -> Tuple[str, str]:
    if mode["is_live"]:
        return "ok", "LIVE. What you publish or change in IMS reaches the website."
    if not mode["writes_enabled"]:
        why = "the IMS writes switch is off"
    elif mode["dispatch_mode"] != "live":
        why = f"the dispatch mode is {mode['dispatch_mode']}, not live"
    else:
        why = "there is no working Shopify key"
    return "warn", f"PREVIEW only. Nothing is sent to Shopify ({why})."


async def _locations(db, mode: Dict[str, Any]) -> Tuple[str, str]:
    if mode.get("stores_total") is None:
        raise LookupError("the shop list could not be read")
    stores = mode.get("stores") or []
    mapped = shopify_push.mapped_store_locations(stores)
    linked = [s for s in stores if s.get("store_id") in mapped]
    unlinked = [s for s in stores if s.get("store_id") not in mapped]
    line = f"{len(linked)} of {len(stores)} shops linked"
    line += (": " + ", ".join(map(_name, linked)) + ".") if linked else "."
    if unlinked:
        line += " Not linked: " + ", ".join(map(_name, unlinked)) + "."
    verdict = shopify_push.last_location_verdict(db, mapped) or await shopify_push.location_verdict(
        db, mapped
    )
    name_of = {s.get("store_id"): _name(s) for s in stores}
    dead = verdict.get("dead") or []
    if dead:
        line += " Cannot sell online: " + "; ".join(
            f"{name_of.get(d.get('store_id'), d.get('store_id'))} ({d.get('reason')})" for d in dead
        ) + "."
        return "bad", line
    if not linked:
        return "bad", line
    if not verdict.get("read"):
        return "warn", line + " Could not ask Shopify whether these locations sell online."
    return ("warn" if unlinked else "ok"), line


async def _last_sync(db) -> Tuple[str, str]:
    enabled = live_sync.live_sync_config()["enabled"]
    run = live_sync.last_run(db, trigger="scheduled")
    off = "" if enabled else "Switched off in IMS settings. "
    if not run:
        return "warn", off + "No scheduled sync has run yet."
    when = _ist(run.get("finished_at") or run.get("started_at"))
    if run.get("status") != "done":
        return "warn", f"{off}The sync that started {_ist(run.get('started_at'))} has not finished."
    failed = int(run.get("failed") or 0)
    stock_ok = bool((run.get("stock") or {}).get("ok", True))
    preview = run.get("mode") != shopify_push.MODE_LIVE
    # A preview (DARK) run sent nothing: its "first published" are plans only.
    first_word = "would be published for the first time" if preview else "published for the first time"
    line = f"{off}{when}: {int(run.get('pushed_ok') or 0)} updated, "
    line += f"{int(run.get('first_published') or 0)} {first_word}, {failed} failed"
    photos_failed = int(run.get("photos_failed") or 0)
    line += f", {photos_failed} with photos FAILED" if photos_failed else ""
    line += "" if stock_ok else ", stock update failed"
    line += f"; {int(run.get('awaiting_first_publish') or 0)} awaiting their first publish"
    unchecked = int(run.get("first_publish_not_checked") or 0)
    line += f" ({unchecked} not yet checked this run)." if unchecked else "."
    if preview:
        line += " It was a preview run, so nothing was sent."
    if failed or not stock_ok:
        return "bad", line
    return ("warn" if preview or not enabled or photos_failed else "ok"), line


async def _notifications(db, live: Optional[bool], host: str) -> Tuple[str, str]:
    if live is None:
        raise LookupError("push status unread")
    rows = list(
        db["webhook_inbox"]
        .find({"vendor": "shopify", "signature_verified": True}, {"received_at": 1})
        .sort("received_at", -1)
        .limit(1)
    )
    last = f"Last one received {_ist(rows[0].get('received_at'))}." if rows else "None received yet."
    if not live:
        return "warn", f"Could not count the subscriptions (IMS is in preview, so it does not read Shopify). {last}"
    # apply=False: the registrar's dry-run is a READ of Shopify's subscription
    # list -- it never creates or deletes one.
    result = await shopify_push.register_webhooks(db, f"https://{host}", apply=False)
    if not result.get("ok"):
        return "warn", f"Could not read the subscriptions from Shopify. {last}"
    want = set(result.get("topics") or [])
    have = {
        x.get("topic")
        for x in result.get("existing") or []
        if x.get("topic") in want
        and str(x.get("callback_url") or "").endswith(shopify_push._WEBHOOK_RECEIVER_PATH)
    }
    line = f"{len(have)} of {len(want)} notification types are sent to IMS. {last}"
    if not have:
        return "bad", line
    return ("ok" if have == want and rows else "warn"), line


async def _products(db, live: Optional[bool]) -> Tuple[str, str]:
    if live is None:
        raise LookupError("push status unread")
    from .online_store_push import _product_counts

    counts = _product_counts(db)
    ims = f"IMS has {counts['staged']} set up for the website, {counts['pushed']} of them linked to Shopify."
    if not live:
        return "warn", f"Could not count the website's products (IMS is in preview). {ims}"
    body = await shopify_push._graphql(db, _PRODUCTS_COUNT_QUERY, {})
    count = int(((body.get("data") or {}).get("productsCount") or {})["count"])
    return ("ok" if count == counts["pushed"] else "warn"), f"Shopify has {count} products. {ims}"


async def _soft(label: str, check, *args) -> Item:
    try:
        level, line = await check(*args)
    except Exception as exc:  # noqa: BLE001 -- one failed check never breaks the page
        logger.warning("[SHOPIFY_APP] %s: could not check: %s", label, exc)
        level, line = "warn", "Could not check."
    return level, label, line


async def status_items(db, host: str) -> List[Item]:
    try:
        mode = shopify_push.push_mode_status(db)
        live: Optional[bool] = bool(mode["is_live"])
    except Exception as exc:  # noqa: BLE001 -- it never raises; unknown is never "preview"
        logger.warning("[SHOPIFY_APP] push status unread: %s", exc)
        mode, live = {}, None
    return [
        await _soft("Shopify connection", _connection, db),
        await _soft("IMS writes to Shopify", _writes, mode),
        await _soft("Shops linked to Shopify locations", _locations, db, mode),
        await _soft("Last scheduled sync", _last_sync, db),
        await _soft("Notifications from Shopify", _notifications, db, live, host),
        await _soft("Products on the website", _products, db, live),
    ]


async def _cached_status(db, host: str) -> Tuple[List[Item], datetime]:
    now = time.monotonic()
    if _cache["items"] is None or now - _cache["at"] >= _CACHE_SECONDS:
        _cache.update(items=await status_items(db, host), at=now, checked=datetime.now(IST))
    return _cache["items"], _cache["checked"]


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f6f6f7;color:#202223;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:32px 16px}
.card{background:#fff;border:1px solid #e1e3e5;border-radius:12px;padding:24px}
h1{margin:0 0 8px;font-size:22px}
h2{margin:0 0 12px;font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:#6d7175}
p{margin:0 0 20px}
.btn{display:inline-block;background:#303030;color:#fff;text-decoration:none;font-weight:600;padding:10px 18px;border-radius:8px}
.btn:focus-visible{outline:3px solid #2c6ecb;outline-offset:2px}
section{margin-top:20px}
ul{list-style:none;margin:0;padding:0}
li{display:flex;gap:12px;padding:12px 0;border-top:1px solid #f1f2f3}
li:first-child{border-top:0;padding-top:0}
.dot{flex:none;width:10px;height:10px;border-radius:50%;margin-top:7px}
.ok .dot{background:#008060}.warn .dot{background:#b98900}.bad .dot{background:#d72c0d}
.tag{display:inline-block;font-size:12px;font-weight:600;padding:0 8px;border-radius:10px;margin-left:6px}
.ok .tag{background:#e3f1df;color:#004c3f}.warn .tag{background:#fcf1cd;color:#5c4400}.bad .tag{background:#fed3d1;color:#7a0c00}
.line{color:#44474a}
.foot{margin:12px 0 0;font-size:13px;color:#6d7175}
"""

_TAGS = {"ok": "OK", "warn": "Check", "bad": "Problem"}


def render_page(items: Optional[List[Item]], checked: Optional[datetime] = None) -> str:
    """The whole page. ``items`` None -> the welcome only (an unverified load)."""
    esc = html.escape
    panel = ""
    if items is not None:
        rows = "".join(
            f'<li class="{esc(level)}"><span class="dot" aria-hidden="true"></span><div>'
            f"<strong>{esc(label)}</strong>"
            f'<span class="tag">{_TAGS.get(level, "Check")}</span>'
            f'<div class="line">{esc(line)}</div></div></li>'
            for level, label, line in items
        )
        when = checked.strftime("%I:%M %p IST") if checked else ""
        panel = (
            '<section class="card" aria-labelledby="status"><h2 id="status">Connection status</h2>'
            f'<ul>{rows}</ul><p class="foot">Checked {esc(when)}. Refreshes at most once a minute.</p></section>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        f"<title>IMS 2.0 Sync</title><style>{_CSS}</style></head><body><main>"
        f'<div class="card"><h1>IMS 2.0 Sync</h1><p>{esc(WELCOME)}</p>'
        f'<a class="btn" href="{IMS_URL}" target="_blank" rel="noopener noreferrer">Open IMS</a></div>'
        f"{panel}</main></body></html>"
    )


@router.get(PAGE_PATH, response_class=HTMLResponse)
async def shopify_app_page(request: Request) -> HTMLResponse:
    """The App URL Shopify admin frames. PUBLIC; the status panel needs a valid
    app-load HMAC for the configured shop (see the module docstring)."""
    db = live_sync.connected_db()
    secret = (os.getenv("SHOPIFY_CLIENT_SECRET") or "").strip()
    shop = None
    # No hmac or no secret -> unverified without reading the shop config, so an
    # anonymous hit costs nothing.
    if secret and request.query_params.get("hmac"):
        try:
            shop = verified_shop(
                request.query_params.multi_items(), secret, _resolve_shop(db), time.time()
            )
        except Exception as exc:  # noqa: BLE001 -- an unreadable config = unverified
            logger.warning("[SHOPIFY_APP] app-load check failed: %s", exc)
    items, checked = None, None
    if shop:
        try:
            items, checked = await _cached_status(db, request.url.hostname or "")
        except Exception as exc:  # noqa: BLE001 -- the page always renders
            logger.warning("[SHOPIFY_APP] status unread: %s", exc)
            items = [("warn", "Connection status", "Could not check right now.")]
    ancestors = "https://admin.shopify.com" + (f" https://{shop}" if shop else "")
    return HTMLResponse(
        render_page(items, checked),
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                f"form-action 'none'; frame-ancestors {ancestors}"
            ),
            "Cache-Control": "no-store",
        },
    )
