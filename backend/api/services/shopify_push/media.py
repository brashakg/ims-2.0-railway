"""Shopify push -- media

Images/media: the `product_photo_urls` photo predicate, the media ledger
(``online_media``), the media diff pass BOTH doors run (the product press and
the design-queue press), media inputs and `push_image`.
"""

from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit
import asyncio
import os
import re
import time
import uuid

from pymongo.errors import DuplicateKeyError

from agents.nexus_providers import _as_shopify_gid

from ._shared import (
    MODE_BLOCKED,
    MODE_LIVE,
    MODE_SIMULATED,
    PushResult,
    _blocked_result,
    _live_or_reason,
    is_variant_of,
    logger,
    online_block_refusal,
    online_block_status,
    push_lock_reason,
)
from .transport import _graphql, _now
from .queries import (
    _MEDIA_LIMIT,
    _PRODUCT_CREATE_MEDIA,
    _PRODUCT_DELETE_MEDIA,
    _PRODUCT_MEDIA_QUERY,
    _PRODUCT_REORDER_MEDIA,
)

_APP_IMAGE_PATH = "/api/v1/products/image/"


def product_photo_urls(product: Dict[str, Any]) -> List[str]:
    """Every usable PHOTOGRAPH URL on a catalog product doc, in display order.
    Pure; deduped; never raises.

    This is the single answer to "does this product have a photograph?" -- the
    owner's publish rule ("no photo, no publish") and the media actually sent to
    Shopify are both driven from THIS list, so the check and the payload can
    never disagree.

    Field precedence mirrors the rest of the app (catalogue_pdf._image_url_of,
    products.list_products' image_url alias): the singular `image_url`, then the
    `images[]` array (strings or {url|src} dicts), then a bare `image`.

    ONLY absolute http(s) URLs count. Shopify pulls the bytes over the internet
    from the URL we hand it; a private /uploads/... path is unfetchable, so
    counting one as a photograph would publish exactly the empty grey box the
    rule exists to prevent (the same rule online_sync_health.uploads_image_audit
    already flags rows on).

    The ONE exception is this API's own product-image serve: the in-app
    uploader (POST /products/image) stores `/api/v1/products/image/<id>`, a
    relative path to a PUBLIC, immutable-cached endpoint. It becomes a
    photograph only when PUBLIC_API_BASE_URL names the address Shopify can
    fetch it from (e.g. https://<backend>.up.railway.app). Unset -- the
    default -- it is NOT a photograph, exactly as before: what reaches the
    storefront never changes by a code deploy alone.

    NOTE: this deliberately does NOT read the `product_images` design queue.
    Those rows reach Shopify on their own, explicit press (push_image) and
    never through the product press or the scheduled sync; a product whose
    only photo lives in the design queue is refused rather than published
    bare -- conservative, and the operator fixes it by putting the photo on
    the product (the design press refuses such a product too: same gate,
    same reason). Once pressed, a design-queue media is an ``online_media``
    doc carrying the queue row's ``image_id`` -- its own lane, which the
    product press keeps and never touches (see plan_product_media)."""
    out: List[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, dict):
            value = value.get("url") or value.get("src")
        url = _photo_url(value)
        if url and url not in out:
            out.append(url)

    _add(product.get("image_url"))
    imgs = product.get("images")
    if isinstance(imgs, (list, tuple)):
        for item in imgs:
            _add(item)
    _add(product.get("image"))
    return out


def _photo_url(value: Any) -> Optional[str]:
    """ONE usable photograph url, or None: absolute http(s) only, the in-app
    serve path made absolute through PUBLIC_API_BASE_URL (see the
    product_photo_urls docstring for why). Pure."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    public_base = (os.getenv("PUBLIC_API_BASE_URL") or "").strip().rstrip("/")
    if public_base and url.startswith(_APP_IMAGE_PATH):
        url = public_base + url
    return url if url.lower().startswith(("http://", "https://")) else None


def image_source_url(image: Dict[str, Any]) -> Optional[str]:
    """The url a design-queue row sends to Shopify: the designer's edited
    asset, else the source -- as a PHOTOGRAPH (``_photo_url``: absolute
    http(s), the in-app path rewritten through PUBLIC_API_BASE_URL exactly
    as product_photo_urls rewrites it), or None when Shopify could not fetch
    it. THE identity of that row on the listing -- the media input, the
    online_media doc and the 'already on the listing' check all key on this
    one value, so a row can never be recorded under one spelling and looked
    up under another. Pure."""
    return _photo_url(image.get("edited_url") or image.get("url"))


# ---------------------------------------------------------------------------
# THE MEDIA LEDGER (2026-09-28). What IMS attached to a Shopify listing lives
# in its OWN collection, one doc per attach:
#     {_id, product_id, url, image_id, gid, how, sent_at, at}
# ``gid`` None = PENDING (the attach was recorded before it was sent and no
# answer has named its media yet); set = LIVE (IMS owns that MediaImage).
# ``how`` says how IMS came to own it: "minted" (Shopify's own answer named
# the gid), "settled" (a pending attach recognised on the listing by its file
# name -- see _settle), "adopted" (the runbook,
# scripts/adopt_shopify_media_map.py).
# Nothing about media lives on catalog_products any more, so none of the
# whole-doc or whole-ecom writers of the twin (the catalog PUT, the stock and
# discount write-backs, delist, the collections requeue, ...) can erase,
# revert or resurrect it; and every write here is a single-doc insert,
# update or delete, so there is no map to merge. The only writers are the
# pass (sync_product_media), the design-queue row delete and the adoption
# runbook, each under the product's media_lease.
# ---------------------------------------------------------------------------

MEDIA_COLLECTION = "online_media"


def media_rows(db, product_id: Optional[str]) -> List[Dict[str, Any]]:
    """Every ledger doc of one product (pending and live). RAISES on a db
    error -- a caller that cannot read the ledger must fail closed, never
    read it as 'IMS owns nothing'. No db or no product: []."""
    if db is None or not product_id:
        return []
    return [
        r
        for r in db[MEDIA_COLLECTION].find({"product_id": str(product_id)})
        if isinstance(r, dict) and isinstance(r.get("url"), str) and r.get("url")
    ]


def owned_media(rows: Optional[List[Dict[str, Any]]], own: List[str]) -> List[Dict[str, Any]]:
    """The LIVE ledger docs as ``{_id, url, id: gid, image_id, how}``. THE
    LANE IS DECIDED HERE, BY URL: a doc whose url is one of the product's own
    photographs ``own`` is the product's lane whichever door attached it
    (_lane_of). Pure."""
    return [
        {
            "_id": r.get("_id"),
            "url": r["url"],
            "id": str(r["gid"]),
            "image_id": _lane_of(r["url"], r.get("image_id"), own),
            "how": r.get("how"),
        }
        for r in rows or []
        if r.get("gid")
    ]


def pending_media(rows: Optional[List[Dict[str, Any]]], own: List[str]) -> List[Dict[str, Any]]:
    """The PENDING ledger docs (an attach recorded, no gid yet) as ``{_id,
    url, image_id, sent_at}`` -- lane by url, as owned_media. Pure."""
    return [
        {
            "_id": r.get("_id"),
            "url": r["url"],
            "image_id": _lane_of(r["url"], r.get("image_id"), own),
            "sent_at": r.get("sent_at"),
        }
        for r in rows or []
        if not r.get("gid")
    ]


def _lane_of(url: str, image_id: Any, own: List[str]) -> Optional[str]:
    """THE LANE OF A MEDIA, BY URL: the design row's ``image_id`` it was
    pressed for -- None (the product's lane) when the url is one of the
    product's own photographs ``own``, whichever door put it up. Pure."""
    return None if url in own or not image_id else str(image_id)


def _in_lane(r: Dict[str, Any], design_row: Optional[Dict[str, Any]]) -> bool:
    """Does a pass GOVERN this doc: the design press (``design_row``, the
    queue row being pressed) governs the docs stamped with ITS image_id; the
    product press (None) governs the docs without one. Pure."""
    if not design_row:
        return not r.get("image_id")
    return r.get("image_id") == str(design_row.get("image_id") or "")


def _normalise_lanes(db, product_id: str, own: List[str]) -> None:
    """STORE the lane the read already decides: every doc (pending or live)
    whose url is one of the product's own photographs loses its image_id
    stamp -- so once a design asset is promoted to a product photograph,
    removing it from the product takes it down (the product press governs
    it), even after that photograph leaves ``own``. One op; raises."""
    if own:
        db[MEDIA_COLLECTION].update_many(
            {"product_id": str(product_id), "url": {"$in": list(own)}}, {"$set": {"image_id": None}}
        )


# ---------------------------------------------------------------------------
# The design-queue press predicate (read off the parent twin and its ledger)
# ---------------------------------------------------------------------------


def _listing_map(parent: Optional[Dict[str, Any]], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The live docs a design-queue press may consult: the parent twin's --
    or NOTHING for a size-variant twin, which owns no listing (its twin may
    carry the parent's gid after a repair; a copied gid must not make the
    child's press a no-op that claims the parent's media). Pure."""
    if not parent or is_variant_of(parent):
        return []
    return owned_media(rows, product_photo_urls(parent))


def image_press_plan(
    parent: Optional[Dict[str, Any]],
    image: Dict[str, Any],
    rows: Optional[List[Dict[str, Any]]],
    *,
    lock: Optional[str] = None,
    blocked: Optional[bool] = False,
) -> Dict[str, Any]:
    """PURE: what a press of this design-queue row does, read off the parent
    twin and its ledger docs ``rows`` (media_rows; None = unreadable) alone --
    THE one predicate the press, the sweep's skip and the pushed/pending
    counts all ask (read_image_press supplies the db facts), so they can
    never disagree about a row:
      gid     the MediaImage the row's source url maps to; None when the
              image is not on the listing (or the parent is a size-variant
              twin, which owns no listing);
      drop    the urls this row's lane (image_lane_media) holds that are NOT
              its source url -- the asset it carried before it was replaced,
              whose delete has not happened yet, or an attach of an earlier
              url whose answer never came back (never one of the product's
              own photographs: that is the product's lane, see owned_media);
      action  'noop' (mapped, nothing to drop: the press makes zero calls),
              'update' (mapped, a drop still pending: the press runs the pass
              to take the old asset down), 'create' (not on the listing) or
              'skip' -- the press REFUSES before it sends anything, with
              ``reason`` (push_locked | online_sync_blocked |
              block_status_unverifiable | no_url | no_photo | not_on_shopify |
              map_unreadable | hands_off) and ``error`` (the line the press
              reports). ``lock`` is the parent's push_lock_reason and
              ``blocked`` its online-block status (True / False / None =
              unreadable, fail closed) -- db facts the caller supplies.
    A refusal is part of the answer so a row the press will never send is
    not 'pending' forever and the sweep does not press it every run."""
    src = image_source_url(image)
    known = rows or []
    gid = {r["url"]: r["id"] for r in _listing_map(parent, known)}.get(src) if src else None
    drop = [r["url"] for r in image_lane_media(parent, image, known) if r["url"] != src]
    plan = {"gid": gid, "drop": drop, "action": "create" if not gid else ("update" if drop else "noop")}
    on_shopify = (
        parent is not None
        and not is_variant_of(parent)
        and bool((parent.get("ecom") or {}).get("shopify_product_id"))
    )
    refusal = online_block_refusal(blocked) if parent is not None else None
    if lock:
        skip = ("push_locked", "push-locked: " + lock)
    elif refusal:
        # THE BLOCK, MIRRORED (push_product's gate, same verdict): a product
        # in an online_sync_blocked collection must never be written on
        # Shopify -- a media attach onto its listing included; an unreadable
        # block status refuses too (fail closed).
        skip = refusal
    elif not src:
        skip = (
            "no_url",
            "no fetchable image url: Shopify pulls the bytes from the url, "
            "so it must be an absolute http(s) url",
        )
    elif parent is not None and not product_photo_urls(parent):
        # THE PHOTO RULE, MIRRORED (push_product's gate, same reason): a
        # product with no photograph of its own is never published, so it
        # gets no design image either. Without this a design press would run
        # the pass over a listing the product press refuses to touch.
        skip = (
            "no_photo",
            "refused: the product has no photograph -- a product with no "
            "photograph is never published, so it takes no design image either "
            "(put a photo on the product and push it first)",
        )
    elif not on_shopify:
        skip = ("not_on_shopify", "parent product not on Shopify yet (push the product first)")
    elif rows is None:
        # FAIL CLOSED: a ledger that cannot be read is never 'IMS owns
        # nothing' (that would be hands-off at best, a blind attach at worst).
        skip = (
            "map_unreadable",
            "refused: the product's media record could not be read -- press again",
        )
    elif not _listing_map(parent, rows):
        # HANDS OFF, read off the ledger: IMS owns no media on this listing
        # (it went live before the ledger existed, or its photographs never
        # landed), and the pass never attaches onto a listing it owns nothing
        # on -- a re-press would duplicate every photograph a human put there.
        # The product press (it attaches the product's photographs) or the
        # adoption runbook gives IMS the listing first.
        skip = (
            "hands_off",
            "hands off: IMS owns no media on this listing yet -- push the product "
            "first (it attaches its photographs), or adopt the listing's media "
            "(scripts/adopt_shopify_media_map.py)",
        )
    else:
        return plan
    return {**plan, "action": "skip", "reason": skip[0], "error": skip[1]}


def read_image_press(db, image: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str], Dict[str, Any]]:
    """(parent twin, its push-lock, image_press_plan): the db facts the one
    predicate needs -- the parent, its ledger docs, its push_lock_reason and
    its online-block status -- read HERE, the one way, for the press and for
    the sweep's skip and the counts alike. Never raises (an unreadable
    ledger is rows=None: the plan refuses)."""
    parent = _resolve_product_doc(db, image.get("product_id"))
    if parent is None:
        return None, None, image_press_plan(None, image, [])
    try:
        rows: Optional[List[Dict[str, Any]]] = media_rows(db, image.get("product_id"))
    except Exception:  # noqa: BLE001 -- fail closed in the plan
        rows = None
    lock = push_lock_reason(db, "product", parent)
    blocked = online_block_status(db, parent)
    return parent, lock, image_press_plan(parent, image, rows, lock=lock, blocked=blocked)


def image_lane_media(
    parent: Optional[Dict[str, Any]], image: Dict[str, Any], rows: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """What a press of this design-queue row GOVERNS -- its lane: the live
    docs stamped with its ``image_id`` (whatever url the row carries now),
    AND the pending ones (an attach made for it whose answer never came back:
    ``{url, image_id}`` with no id -- the media may be on the listing).
    Only a press of this row can take them down (or settle them), so the row
    must not be deleted while this is non-empty. Pure."""
    if not image.get("image_id") or not parent or is_variant_of(parent):
        return []
    own = product_photo_urls(parent)
    return [r for r in owned_media(rows, own) + pending_media(rows, own) if _in_lane(r, image)]


def image_media_gid(
    parent: Optional[Dict[str, Any]], image: Dict[str, Any], rows: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """The MediaImage gid the ledger holds for this design-queue row's source
    url, None when it is not on the listing (``image_press_plan``, the gid
    alone -- what a screen shows next to the row). Pure."""
    return image_press_plan(parent, image, rows)["gid"]


async def _attach_one(
    db, product_gid: str, url: str, alt: Optional[str] = None
) -> Tuple[Optional[str], bool, Optional[str]]:
    """LIVE-only: ONE productCreateMedia for ONE url -> (gid, ok, error).
    One input per call, so the answer's node 0 IS this url's media -- no
    positional mapping across inputs.
      * the call raised (transport: sent once, answer lost)  -> (None, False, err)
      * no node in the answer (mediaUserErrors / no media)    -> (None, False, err)
      * a node Shopify already marked FAILED                  -> (gid, False, err)
        -- the gid IS IMS's own media: the caller records it.
      * otherwise                                             -> (gid, True, None)
    AN ID IS NOT A PHOTOGRAPH: Shopify mints the id before it fetches the
    bytes, so a node still UPLOADED/PROCESSING here can go FAILED later; the
    next pass reads the listing's status (a FAILED media is not counted, and
    in the pass's lane it is attached again and deleted). ``alt`` is the
    design queue row's alt text; a product's own photograph carries ''."""
    media = build_media_inputs([{"url": url, "alt_text": alt}])
    if not media:
        return None, False, "no usable photograph"
    try:
        body = await _graphql(db, _PRODUCT_CREATE_MEDIA, {"productId": product_gid, "media": media})
    except Exception as e:  # noqa: BLE001 -- fail-soft side channel
        return None, False, str(e)
    nodes = []
    if isinstance(body, dict):
        nodes = ((body.get("data") or {}).get("productCreateMedia") or {}).get("media") or []
    node = nodes[0] if nodes and isinstance(nodes[0], dict) else {}
    if not node.get("id"):
        return None, False, _user_errors_media(body) or "Shopify returned no media for the photograph"
    gid = str(node["id"])
    if str(node.get("status") or "").upper() == "FAILED":
        detail = "; ".join(
            str(e.get("details") or e.get("message") or e.get("code") or "")
            for e in (node.get("mediaErrors") or [])
            if isinstance(e, dict)
        )
        return gid, False, "Shopify could not fetch the photograph" + (": %s" % detail if detail else "")
    return gid, True, None


# ---------------------------------------------------------------------------
# THE PHOTO PASS (sync audit gap #3, owner 2026-09-06): "replacing or removing
# a photo, and reordering, update Shopify instead of silently doing nothing."
#
# OWNERSHIP. IMS manages ONLY the media it attached itself (or the runbook
# adopted): the LIVE docs of the ledger above. A media is IMS-owned only when
# (a) Shopify's own answer to IMS's productCreateMedia named its gid, or
# (b) it SETTLES a pending doc of IMS's under _settle's rules. Anything else
# on the listing -- the hand-uploaded photographs on the connector-created
# Ray-Ban Meta products, anything a human added in the Shopify admin -- is
# UNMANAGED: never deleted, reordered or claimed, counted as ``unmanaged``
# and left exactly where it is. When IMS owns nothing on a product that
# already carries media, the pass keeps its hands off entirely (no attach
# either): that stops a re-press from minting a duplicate of every
# photograph on a listing that went live before the ledger existed.
#
# TWO LANES. A doc is either the product's own photograph (no ``image_id``)
# or a design-queue media (the queue row's ``image_id``, stamped when the
# design press attached it). Each press GOVERNS exactly one lane -- attaches
# into it, deletes from it, reorders it -- and KEEPS the other lane exactly
# where it is: the product press (and so the 01:00/09:00 sync) governs the
# own-photo docs against product_photo_urls and never reads the design queue;
# the design press governs the docs of the ONE image_id it is pressing. A url
# that is one of the product's own photographs is the product's lane
# whichever door attached it -- decided BY URL on every read (owned_media)
# and stored that way by _normalise_lanes.
#
# NO ATTACH WITHOUT A RECORD, NO RECORD LOST. Before each productCreateMedia
# the pass inserts the url's PENDING doc; Shopify's answer turns it LIVE
# (how="minted"). The transport sends productCreateMedia ONCE (a lost answer
# is never replayed), so a timeout leaves a pending doc and at most one media
# on the listing. The NEXT pass settles it off its read of the listing: the
# pending doc CLAIMS the one READY node whose CDN file name is the url's
# IMS-unique file name; it is DROPPED ('never landed', the url may be
# attached again) only when nothing matches, nothing on the listing is still
# processing and 15 minutes have passed; otherwise it is HELD (reported, the
# url not attached again). Identity is the CDN file name (_same_file):
# originalSource is Shopify's own storage copy in production (measured
# 2026-09-06, 42 twins, 180 media), never the url IMS sent.
#
# ONE PASS PER PRODUCT AT A TIME (media_lease): every press of either door --
# and a design-queue row's delete -- holds the product's lease from BEFORE it
# reads the listing until the pass is done, across every worker, and renews
# it before every attach; a pass that lost its lease sends nothing more.
# Inside it the pass plans on the twin AS IT IS NOW (the 01:00/09:00 sweep
# hands the product press a doc it loaded minutes ago); an unreadable twin or
# ledger means zero Shopify writes.
#
# ORDER OF OPERATIONS is attach -> delete -> reorder, and a failed step stops
# the pass: a replacement is on Shopify BEFORE the photo it replaces comes
# down, so a listing never loses its last photograph to a half-done pass. A
# HELD attach is a step not done: while any url of the photo list is held,
# the pass deletes no photograph (a FAILED media is none).
# Before any delete the {product_id, media_gid, url, shopify_url, deleted_at}
# row goes to ``online_media_tombstones`` -- the never-lose-bytes lesson.
# ---------------------------------------------------------------------------

TOMBSTONES_COLLECTION = "online_media_tombstones"
MEDIA_LIMIT_CODE = "MEDIA_LIMIT_250"
MEDIA_SETTLING = "MEDIA_SETTLING"
MEDIA_NAMING_DRIFT = "MEDIA_NAMING_DRIFT"
NO_PHOTO = "NO_PHOTO"
TWIN_UNREADABLE = "TWIN_UNREADABLE"
MAP_UNREADABLE = "MAP_UNREADABLE"


# Shopify keeps the SOURCE file name on the CDN copy (adding an extension
# when the source had none: the in-app uploader's bare ObjectId url comes
# back as <oid>.png) and appends ``_<uuid>`` when that name collides with a
# file already in Files. That suffix is Shopify's own marker that the file
# is a DIFFERENT upload with the same base name, so it is stripped on the
# CDN side ONLY -- an IMS url that itself carries one (a cdn.shopify.com
# photo on a bvi_import twin) names one specific upload, and another upload
# of the same base name is not that photograph. Measured on the 42 (09-06):
# 0 of 180 CDN names carry ``_WxH`` or a bare-hex suffix, so nothing else is
# stripped; a human's "front_600x600.png" is a different file from
# "front.png". Extensions are a fixed image whitelist so ".v2" is not one;
# nothing is case-folded: "front.JPG" and "front.jpg" are two names.
_IMAGE_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|heic|heif|bmp|tiff?|svg)$", re.I)
_COLLISION_SUFFIX = re.compile(
    r"_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _file_name(url: str) -> str:
    """The path's basename, query dropped ('' when the url has none). Pure."""
    return urlsplit(str(url or "")).path.rsplit("/", 1)[-1]


def _stem_ext(name: str) -> tuple:
    """(stem, ext) of a file name; ext is '' unless it is an image extension.
    The extension is RECOGNISED case-insensitively (so '.JPG' is an extension
    and not part of a stem) but returned as written: comparison is exact."""
    m = _IMAGE_EXT.search(name)
    return (name[: m.start()], m.group(0)) if m else (name, "")


def _same_file(ims_url: str, cdn_url: str) -> bool:
    """R3: the CDN copy carries the IMS file's name -- the name equal, the
    CDN side allowed Shopify's ``_<uuid>`` collision suffix (once), and the
    extension equal -- exactly, no case folding -- whenever the IMS name has
    one (an extension-less IMS name, of ANY shape, not only an ObjectId,
    matches whichever image extension Shopify gave the copy). Pure."""
    stem, ext = _stem_ext(_file_name(ims_url))
    cstem, cext = _stem_ext(_file_name(cdn_url))
    if not stem or (ext and ext != cext):
        return False
    return stem == cstem or stem == _COLLISION_SUFFIX.sub("", cstem, count=1)


# R3b (OPT-IN, owner 2026-09-06): the Ray-Ban Meta connector names every
# upload '<shopify product id>__<nn>__<source file name>'. Measured on the
# 36 connector products: 23 carry exactly one media whose source name is the
# IMS photo's name with the extension re-encoded (4_1.jpeg -> 4_1.png).
_CONNECTOR_NAME = re.compile(r"^(\d+)__\d+__(.+)$")
MATCH_RULES = ("exact", "connector_prefix")


def _numeric_id(gid: Any) -> str:
    """'gid://shopify/Product/123' or '123' -> '123'; '' when not numeric."""
    tail = str(gid or "").rsplit("/", 1)[-1]
    # isascii too: str.isdigit accepts Unicode digits ("\u00b2"), not an id
    return tail if tail.isascii() and tail.isdigit() else ""


def _connector_file(ims_url: str, cdn_url: str, own_id: str) -> bool:
    """R3b: the CDN name is '<own_id>__<nn>__<basename>' where own_id is THIS
    product's own Shopify numeric id (a foreign id, or no prefix, never
    matches) and <basename>'s stem equals the IMS file's stem exactly -- no
    case folding, no ``_<uuid>`` strip, no size suffix; the EXTENSION is
    ignored (the connector re-encodes). The index <nn> is not identity. Pure."""
    m = _CONNECTOR_NAME.match(_file_name(cdn_url))
    if not m or not own_id or m.group(1) != own_id:
        return False
    stem, _ext = _stem_ext(_file_name(ims_url))
    cstem, _cext = _stem_ext(m.group(2))
    return bool(stem) and stem == cstem



# THE SETTLE (see the ownership note). A pending doc is claimed only by a
# file name IMS minted itself -- an ObjectId (the in-app uploader), a uuid4
# hex (the design queue's upload and edit keys) or a dashed uuid -- never by
# a generic name a human's upload can carry too (front.jpg), and never by a
# name that is itself Shopify's collision copy (<name>_<uuid>).
_SETTLE_GRACE = timedelta(minutes=15)  # == _LEASE_TTL: no live pass can still be sending
_IMS_UNIQUE = re.compile(
    r"[0-9a-f]{24}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _ims_unique(url: str) -> bool:
    """Is this url's file name one only IMS mints (so a CDN file of that name
    can only be IMS's own upload)? Pure."""
    stem, _ext = _stem_ext(_file_name(url))
    return bool(_IMS_UNIQUE.search(stem)) and not _COLLISION_SUFFIX.search(stem)


def _cdn(node: Optional[Dict[str, Any]]) -> str:
    """A listing node's CDN image url ('' while Shopify is still processing
    it, or for a non-image media). Pure."""
    return str(((node or {}).get("image") or {}).get("url") or "")


def _utc(dt: datetime) -> datetime:
    """pymongo hands datetimes back NAIVE (UTC); compare them aware."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _settle(
    pending: List[Dict[str, Any]],
    free: List[Dict[str, Any]],
    minted: List[Dict[str, Any]],
    nodes: Dict[str, Dict[str, Any]],
    now: datetime,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], bool, List[str]]:
    """PURE: settle the pending docs against the listing's FREE nodes (the
    nodes no live doc names) -> (claims, drops, held, drift, nameless).
      claim  exactly ONE READY free node carries the pending url's file name
             (_same_file), no other pending doc hits that node, and the name
             is IMS-unique -- at any age;
      drop   ('never landed': the url may be attached again) zero hits, no
             image-less node still UPLOADED/PROCESSING on the listing, and
             older than _SETTLE_GRACE;
      held   everything else: two hits, a generic name, a node still
             processing, too young -- or DRIFT.
    DRIFT (the canary): a READY node IMS minted whose CDN name no longer
    carries its url's file name means Shopify's naming changed; every
    pending doc of the product is then held -- no claim, no drop, no
    re-attach -- until a person looks (MEDIA_NAMING_DRIFT)."""
    drift = any(
        _cdn(nodes.get(r["id"])) and not _same_file(r["url"], _cdn(nodes[r["id"]]))
        for r in minted
        if r["id"] in nodes
    )
    nameless = [
        str(n["id"])
        for n in free
        if not _cdn(n) and str(n.get("status") or "").upper() not in ("READY", "FAILED")
    ]
    hits = {
        p["_id"]: [str(n["id"]) for n in free if _cdn(n) and _same_file(p["url"], _cdn(n))]
        for p in pending
    }
    load = Counter(i for ids in hits.values() for i in ids)
    claims: List[Dict[str, Any]] = []
    drops: List[Dict[str, Any]] = []
    held: List[Dict[str, Any]] = []
    for p in pending:
        ids, sent = hits[p["_id"]], p.get("sent_at")
        young = not isinstance(sent, datetime) or now - _utc(sent) < _SETTLE_GRACE
        if drift:
            held.append(p)
        elif len(ids) == 1 and load[ids[0]] == 1 and _ims_unique(p["url"]):
            claims.append({**p, "id": ids[0]})
        elif not ids and not nameless and not young:
            drops.append(p)
        else:
            held.append(p)
    return claims, drops, held, drift, nameless


def match_media_to_photos(
    photos: List[str],
    shopify_media: List[Dict[str, Any]],
    *,
    rules: tuple = ("exact",),
    product_gid: Optional[str] = None,
) -> Dict[str, Any]:
    """PURE: pair each IMS photo url with the ONE Shopify media that positively
    identifies as that photograph -- the adoption rule for products that went
    live before the ledger existed (scripts/adopt_shopify_media_map.py).

    Under the default ``rules=('exact',)`` a media is the photo when
      R3  its CDN file name IS the IMS url's file name (``_same_file``).
    (There is no originalSource rule: in production originalSource is always
    Shopify's own storage copy, never the url IMS sent -- measured 09-06 on
    the 42, it matched nothing.)
    ``'connector_prefix'`` in ``rules`` (OPT-IN; needs ``product_gid``, the
    product's own Shopify gid, else ValueError) adds
      R3b the CDN name is '<this product's own numeric id>__<nn>__<basename>'
          and <basename> equals the IMS file name stem-for-stem, extension
          ignored (``_connector_file``). A foreign id never matches.
    There is deliberately NO alt rule: IMS's own attach sends alt '' for
    every photo (``build_media_inputs``), so an alt equal to an IMS url or
    file name can only be a human's edit on a media IMS did not attach, and
    claiming it would let the photo pass delete that media later.
    NEVER position or count: a claimed media can later be DELETED by the photo
    pass when IMS drops the photo, so a guess is never a claim. A photo that
    fits two media, or a media that two photos fit, is ambiguous and stays
    unmatched (1:1 only). A url repeated in ``photos`` counts once.

    Returns {map: [{url, id}] in IMS order for the photos that matched,
    unmatched_photos: [url], unmanaged: [media id] (every media no photo
    claimed -- hand uploads, connector media -- left exactly where it is),
    names: {media id: CDN file name} (the evidence a dry-run prints)}.
    Adopt only when ``unmatched_photos`` is empty and ``map`` is not."""
    unknown = [r for r in rules if r not in MATCH_RULES]
    if unknown:
        raise ValueError("unknown match rule(s) %s; known: %s" % (unknown, MATCH_RULES))
    exact = "exact" in rules
    connector = "connector_prefix" in rules
    own_id = _numeric_id(product_gid)
    if connector and not own_id:
        raise ValueError("the connector_prefix rule needs the product's own Shopify gid")
    nodes = [
        (str(n["id"]), _cdn(n).strip())
        for n in shopify_media or []
        if isinstance(n, dict) and n.get("id")
    ]
    photos = list(dict.fromkeys(photos))
    hits: Dict[str, List[str]] = {
        url: [
            mid
            for mid, cdn in nodes
            if (exact and _same_file(url, cdn)) or (connector and _connector_file(url, cdn, own_id))
        ]
        for url in photos
    }
    claimed = Counter(mid for ids in hits.values() for mid in ids)
    media_map: List[Dict[str, str]] = []
    unmatched: List[str] = []
    for url in photos:
        ids = hits[url]
        if len(ids) == 1 and claimed[ids[0]] == 1:
            media_map.append({"url": url, "id": ids[0]})
        else:
            unmatched.append(url)
    owned_ids = {r["id"] for r in media_map}
    return {
        "map": media_map,
        "unmatched_photos": unmatched,
        "unmanaged": [mid for mid, _c in nodes if mid not in owned_ids],
        "names": {mid: _file_name(cdn) for mid, cdn in nodes},
    }


def _is_failed(node: Optional[Dict[str, Any]]) -> bool:
    return str((node or {}).get("status") or "").upper() == "FAILED"


def plan_product_media(
    rows: Optional[List[Dict[str, Any]]],
    photos: List[str],
    own: List[str],
    listing: Optional[List[Dict[str, Any]]] = None,
    *,
    design_row: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """PURE diff of IMS's ordered photo list ``photos`` against the media IMS
    owns (the ledger docs ``rows``; ``own`` = the product's own photographs,
    which decide the lanes). ``listing`` is the product's media node list on
    Shopify RIGHT NOW (_product_media, in listing order); None = UNKNOWN (the
    dark plan): the ledger is trusted as-is, nothing is settled or reordered.

    THE LANES (see the ownership note): without ``design_row`` this is the
    product press's plan and it governs the own-photo docs only; with
    ``design_row`` it governs the docs stamped with THAT row's image_id only.
    A doc of the other lane is never attached, deleted or reordered here. A
    url ``photos`` names that is live in EITHER lane is on the listing and is
    not attached again; one whose attach is HELD is not attached again either,
    and while one is held no photograph is deleted (ORDER OF OPERATIONS).

    Returns {attach: [url], delete: [{url, id, shopify_url, ...}], reorder:
    [gid] (the desired order of the IMS-owned media, [] when in order),
    unmanaged: n, hands_off: bool, owned: [live docs incl. the settled ones],
    claims / drops: [pending doc] (_settle), held: [url], gone: [gid] (live
    docs whose media left the listing), drift: bool, on_shopify: n (the
    listing's media that is not FAILED; None dark)}."""
    owned = owned_media(rows, own)
    pending = pending_media(rows, own)

    def _governed(r: Dict[str, Any]) -> bool:
        return _in_lane(r, design_row)

    out: Dict[str, Any] = {
        "attach": [],
        "delete": [],
        "reorder": [],
        "unmanaged": 0,
        "hands_off": False,
        "owned": owned,
        "claims": [],
        "drops": [],
        "held": [],
        "gone": [],
        "drift": False,
        "on_shopify": None,
    }
    if listing is None:
        on_record = {r["url"] for r in owned} | {p["url"] for p in pending}
        out["attach"] = [u for u in photos if u not in on_record]
        waiting = any(p["url"] in photos for p in pending)
        out["delete"] = [
            {**r, "shopify_url": None}
            for r in owned
            if _governed(r) and r["url"] not in photos and not waiting
        ]
        out["held"] = [p["url"] for p in pending]
        return out

    nodes: Dict[str, Dict[str, Any]] = {}
    for n in listing:
        if isinstance(n, dict) and n.get("id"):
            nodes[str(n["id"])] = n
    gone = [r["id"] for r in owned if r["id"] not in nodes]
    live = [r for r in owned if r["id"] in nodes]
    named = {r["id"] for r in live}
    free = [n for i, n in nodes.items() if i not in named]
    minted = [r for r in live if r.get("how") == "minted"]
    claims, drops, held, drift, _nameless = _settle(pending, free, minted, nodes, now or _now())
    live += [
        {"_id": c["_id"], "url": c["url"], "id": c["id"], "image_id": c["image_id"], "how": "settled"}
        for c in claims
    ]
    claimed = {c["id"] for c in claims}
    unmanaged = [str(n["id"]) for n in free if str(n["id"]) not in claimed]
    hands_off = not live and bool(unmanaged)
    failed = {i for i, n in nodes.items() if _is_failed(n)}
    # A FAILED media of this lane is not a photograph: its url is attached
    # again (before the FAILED one is deleted, below).
    by_url = {r["url"]: r["id"] for r in live if not (r["id"] in failed and _governed(r))}
    held_urls = {p["url"] for p in held}
    attach = [] if hands_off else [u for u in photos if u not in by_url and u not in held_urls]
    # ORDER OF OPERATIONS: a photograph whose attach is HELD has not landed,
    # so no photograph comes down this pass (only a FAILED media, which is none).
    waiting = not held_urls.isdisjoint(photos)
    delete = (
        []
        if hands_off
        else [
            {**r, "shopify_url": _cdn(nodes.get(r["id"])) or None}
            for r in live
            if _governed(r) and ((r["url"] not in photos and not waiting) or r["id"] in failed)
        ]
    )
    desired = [by_url[u] for u in photos if u in by_url]
    reorder: List[str] = []
    if not hands_off:
        # Only the media ``photos`` names has a desired slot; a doc the list
        # omits (the other lane) keeps its place, like unmanaged media.
        want = set(desired)
        if [i for i in nodes if i in want] != desired:
            reorder = desired
    out.update(
        attach=attach,
        delete=delete,
        reorder=reorder,
        unmanaged=len(unmanaged),
        hands_off=hands_off,
        owned=live,
        claims=claims,
        drops=drops,
        held=[p["url"] for p in held],
        gone=gone,
        drift=drift,
        on_shopify=len(nodes) - len(failed),
    )
    return out


def _tombstone_media(db, product_id: Optional[str], rows: List[Dict[str, Any]]) -> None:
    """Record every media about to be deleted (the never-lose-bytes lesson:
    10,355 images were lost once by deleting first). Raises on failure so the
    caller SKIPS the delete -- no record, no removal."""
    now = _now()
    db[TOMBSTONES_COLLECTION].insert_many(
        [
            {
                "product_id": product_id,
                "media_gid": r["id"],
                "url": r["url"],
                "shopify_url": r.get("shopify_url"),
                "deleted_at": now,
            }
            for r in rows
        ]
    )


LEASES_COLLECTION = "online_media_leases"
# A worker that dies holding a lease blocks that product's presses this long.
_LEASE_TTL = timedelta(minutes=15)
_LEASE_WAIT_SECONDS = 60.0
_LEASE_POLL_SECONDS = 0.1


class MediaBusy(RuntimeError):
    """Another press (or a queue-row delete) holds this product's media lease."""


def _claim_lease(coll, key: str, token: str) -> bool:
    """ONE atomic claim: insert the lease (Mongo's unique _id lets exactly
    one claimant in), or take over one that has EXPIRED -- a worker that died
    holding it; the conditional update lets exactly one taker in. Raises on
    a db failure: no lease, no press."""
    now = datetime.now(timezone.utc)
    lease = {"_id": key, "token": token, "until": now + _LEASE_TTL}
    try:
        coll.insert_one(dict(lease))
        return True
    except DuplicateKeyError:
        taken = coll.find_one_and_update(
            {"_id": key, "until": {"$lte": now}},
            {"$set": {"token": token, "until": lease["until"]}},
        )
        return taken is not None


@asynccontextmanager
async def media_lease(db, product_id: Optional[str]):
    """Hold the product's MEDIA LEASE (the ownership note): ONE press of
    either door, or one design-queue row delete, per product at a time --
    across every worker, since the lease lives in Mongo. Waits up to
    _LEASE_WAIT_SECONDS for a running press, then raises MediaBusy (the
    caller reports 'press again'). Yields ``renew()``: pushes the expiry out
    by _LEASE_TTL and answers whether this holder STILL holds the lease --
    the pass calls it before every attach and sends nothing once it is lost.
    No db or no product id: nothing to hold (renew is always True). Released
    on the way out; a lease a dead worker left expires after _LEASE_TTL.

    ponytail: the no-Mongo MockCollection overwrites a duplicate _id instead
    of refusing it, so in local no-Mongo mode the lease never blocks; prod is
    real Mongo."""
    if db is None or not product_id:
        yield lambda: True
        return
    coll = db[LEASES_COLLECTION]
    key, token = str(product_id), uuid.uuid4().hex
    deadline = time.monotonic() + _LEASE_WAIT_SECONDS
    while not _claim_lease(coll, key, token):
        if time.monotonic() >= deadline:
            raise MediaBusy(
                "another press is running on product %s -- press again in a minute" % key
            )
        await asyncio.sleep(_LEASE_POLL_SECONDS)

    def renew() -> bool:
        try:
            res = coll.update_one(
                {"_id": key, "token": token},
                {"$set": {"until": datetime.now(timezone.utc) + _LEASE_TTL}},
            )
            matched = getattr(res, "matched_count", None)
            return (res.modified_count if matched is None else matched) == 1
        except Exception:  # noqa: BLE001 -- unknown = lost: send nothing
            return False

    try:
        yield renew
    finally:
        try:
            coll.delete_one({"_id": key, "token": token})
        except Exception as exc:  # noqa: BLE001 -- it expires on its own
            logger.warning("[SHOPIFY_PUSH] media lease release failed %s: %s", key, exc)


def _refused(summary: Dict[str, Any], code: str, error: str) -> Dict[str, Any]:
    summary.update(code=code, error=error)
    return summary


async def sync_product_media(
    db,
    pid: str,
    product_gid: str,
    *,
    design_row: Optional[Dict[str, Any]] = None,
    heirs: Optional[Dict[str, str]] = None,
    renew: Callable[[], bool] = lambda: True,
) -> Dict[str, Any]:
    """LIVE-only (the caller has passed the gates AND holds the product's
    media_lease, whose ``renew`` it hands in): make the media IMS owns on
    the Shopify product, in the lane this pass governs, match the photo list
    -- attach what is missing, delete what IMS dropped, reorder to IMS order
    -- per the ownership note. It plans ONLY on the twin as it is NOW (the
    product lane's list is product_photo_urls of the twin re-read here) and
    on the listing read here: an unreadable twin (TWIN_UNREADABLE), a twin
    with no photograph (NO_PHOTO) or an unreadable ledger (MAP_UNREADABLE)
    means zero Shopify writes. ``design_row`` (the design-queue row being
    pressed) selects the design lane: the photo list is that row's url alone,
    its attach is stamped with the row's image_id (unless the url is one of
    the product's own photographs) and carries the row's alt text; the asset
    the row held before is dropped -- unless ``heirs`` ({url: image_id})
    names another APPROVED queue row that sources that url too: the doc is
    handed to that row's lane instead.
    Fail-soft summary, never raises: {attached, deleted, reordered,
    unmanaged, adopted (pending docs settled by name), dropped, held
    ([url] -- attaches that may still be landing: not attached again),
    hands_off, on_shopify (the listing's non-FAILED media after the pass --
    the publish precondition), attached_map ([{url, id, image_id}] minted),
    error?, code? (MEDIA_SETTLING / MEDIA_NAMING_DRIFT alone are not an
    error)}."""
    summary: Dict[str, Any] = {
        "attached": 0,
        "deleted": 0,
        "reordered": False,
        "unmanaged": 0,
        "adopted": 0,
        "dropped": 0,
        "held": [],
        "hands_off": False,
        "on_shopify": 0,
        "attached_map": [],
    }
    twin = _resolve_product_doc(db, pid)
    if twin is None:
        return _refused(
            summary, TWIN_UNREADABLE, "refused: the product could not be read -- nothing was sent; press again"
        )
    own = product_photo_urls(twin)
    photos = [u for u in ([image_source_url(design_row)] if design_row else own) if u]
    if not photos or not own:
        return _refused(
            summary,
            NO_PHOTO,
            "refused: the product has no photograph now -- nothing was sent "
            "(a product with no photograph is never published)",
        )
    coll = db[MEDIA_COLLECTION]
    try:
        _normalise_lanes(db, pid, own)
    except Exception as exc:  # noqa: BLE001 -- the read decides the lane anyway
        logger.warning("[SHOPIFY_PUSH] media lane normalise failed %s: %s", pid, exc)
    try:
        rows = media_rows(db, pid)
        if heirs and design_row:
            # HAND-OVER: a doc of this lane whose url another APPROVED row
            # sources is that row's image as well -- moved to its lane BEFORE
            # the plan, so this press never deletes it.
            for r in rows:
                if r["url"] in heirs and _in_lane(
                    {"image_id": _lane_of(r["url"], r.get("image_id"), own)}, design_row
                ):
                    coll.update_one({"_id": r["_id"]}, {"$set": {"image_id": heirs[r["url"]]}})
                    r["image_id"] = heirs[r["url"]]
    except Exception as exc:  # noqa: BLE001 -- fail closed
        return _refused(
            summary,
            MAP_UNREADABLE,
            "refused: the product's media record could not be read (%s) -- nothing was sent; press again" % exc,
        )
    try:
        listing = await _product_media(db, product_gid)
    except Exception as exc:  # noqa: BLE001 -- an unknown listing is never 'no media'
        summary["error"] = "could not read the listing's media: %s" % exc
        return summary
    now = _now()
    plan = plan_product_media(rows, photos, own, listing, design_row=design_row, now=now)
    summary.update(
        unmanaged=plan["unmanaged"],
        adopted=len(plan["claims"]),
        dropped=len(plan["drops"]),
        held=plan["held"],
        hands_off=plan["hands_off"],
        on_shopify=plan["on_shopify"],
    )
    if plan["drift"]:
        summary["code"] = MEDIA_NAMING_DRIFT
    elif plan["held"]:
        summary["code"] = MEDIA_SETTLING
    # THE SETTLE, written before anything is sent: a claim turns its pending
    # doc live, a drop deletes it (the url may be attached again below), a
    # live doc whose media left the listing is pruned. Each write is
    # conditional on the doc still being pending. A failed write stops the
    # pass: re-attaching over a drop that did not land would leave two
    # pending docs for one url.
    try:
        for c in plan["claims"]:
            coll.update_one({"_id": c["_id"], "gid": None}, {"$set": {"gid": c["id"], "how": "settled", "at": now}})
        for d in plan["drops"]:
            coll.delete_one({"_id": d["_id"], "gid": None})
        if plan["gone"]:
            coll.delete_many({"product_id": pid, "gid": {"$in": plan["gone"]}})
    except Exception as exc:  # noqa: BLE001
        summary["error"] = "refused: could not record the settle (%s) -- nothing was sent; press again" % exc
        return summary
    nodes = [str(n["id"]) for n in listing if isinstance(n, dict) and n.get("id")]
    failed = {str(n["id"]) for n in listing if isinstance(n, dict) and n.get("id") and _is_failed(n)}
    if len(nodes) + len(plan["attach"]) > _MEDIA_LIMIT:
        return _refused(
            summary,
            MEDIA_LIMIT_CODE,
            "refused: %d media on Shopify + %d to attach exceeds the %d-per-product "
            "limit -- remove photographs before adding" % (len(nodes), len(plan["attach"]), _MEDIA_LIMIT),
        )
    lane = str((design_row or {}).get("image_id") or "") or None
    alt = design_row.get("alt_text") if design_row else None
    minted: List[Dict[str, Any]] = []
    # 1. ATTACH what IMS has and Shopify lacks, ONE url per call (the
    # replacement lands first) -- each on record BEFORE it is sent, and only
    # while this pass still holds the lease. No lease, no record: no attach.
    for url in plan["attach"]:
        if not renew():
            summary["error"] = "lost the media lease -- nothing sent for the rest; press again"
            return summary
        doc_id = uuid.uuid4().hex
        sent = _now()
        try:
            coll.insert_one(
                {
                    "_id": doc_id,
                    "product_id": pid,
                    "url": url,
                    "image_id": _lane_of(url, lane, own),
                    "gid": None,
                    "how": None,
                    "sent_at": sent,
                    "at": sent,
                }
            )
        except Exception:  # noqa: BLE001
            summary["error"] = (
                "refused: could not record the attach on the product -- nothing was sent; press again"
            )
            return summary
        gid, ok, err = await _attach_one(db, product_gid, url, alt)
        if gid:
            # Shopify's own answer named it: IMS's media, even a FAILED one.
            row = {"url": url, "id": gid, "image_id": _lane_of(url, lane, own)}
            summary["attached_map"].append(row)
            try:
                coll.update_one({"_id": doc_id}, {"$set": {"gid": gid, "how": "minted", "at": _now()}})
                minted.append(row)
            except Exception as exc:  # noqa: BLE001 -- the doc stays pending: the next pass settles it by name
                logger.warning("[SHOPIFY_PUSH] media record of %s failed %s: %s", gid, pid, exc)
        if not ok:
            summary["error"] = err
            return summary
        summary["attached"] += 1
        summary["on_shopify"] += 1
    # 2. DELETE what IMS dropped (and a FAILED media of this lane) --
    # tombstone first, then the call, then the ledger.
    deleted = [d["id"] for d in plan["delete"]]
    if deleted:
        try:
            _tombstone_media(db, pid, plan["delete"])
            body = await _graphql(db, _PRODUCT_DELETE_MEDIA, {"productId": product_gid, "mediaIds": deleted})
            err = _user_errors_media(body, "productDeleteMedia")
        except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
            err = str(exc)
        if err:
            summary["error"] = err
            return summary
        summary["deleted"] = len(deleted)
        summary["on_shopify"] -= len([i for i in deleted if i not in failed])
        try:
            coll.delete_many({"product_id": pid, "gid": {"$in": deleted}})
        except Exception as exc:  # noqa: BLE001 -- the next pass prunes them as gone
            logger.warning("[SHOPIFY_PUSH] media ledger prune failed %s: %s", pid, exc)
    # 3. REORDER the IMS-owned media into IMS order, in the SLOTS they
    # already occupy (the attach appended its new media at the end): media
    # IMS does not own keeps its exact position, so a hero shot a human
    # placed first in the Shopify admin stays first.
    live = [r for r in plan["owned"] if r["id"] not in deleted] + minted
    by_url = {r["url"]: r["id"] for r in live if r["id"] not in failed}
    desired = [by_url[u] for u in photos if u in by_url]
    survivors = [i for i in nodes if i not in deleted]
    survivors += [r["id"] for r in minted if r["id"] not in survivors]
    want = set(desired)
    slots = [i for i, gid in enumerate(survivors) if gid in want]
    if desired and [survivors[i] for i in slots] != desired:
        try:
            body = await _graphql(
                db,
                _PRODUCT_REORDER_MEDIA,
                {
                    "id": product_gid,
                    "moves": [{"id": gid, "newPosition": str(slots[k])} for k, gid in enumerate(desired)],
                },
            )
            err = _user_errors_media(body, "productReorderMedia")
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
        if err:
            summary["error"] = err
            return summary
        summary["reordered"] = True
    return summary


def build_media_inputs(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Shopify CreateMediaInput[] from APPROVED product_images. Prefer the
    designer's edited asset; fall back to the source url."""
    out: List[Dict[str, Any]] = []
    for img in images:
        src = image_source_url(img)
        if not src:
            continue
        out.append(
            {
                "originalSource": src,
                "alt": img.get("alt_text") or "",
                "mediaContentType": "IMAGE",
            }
        )
    return out


async def _product_media(db, product_gid: str) -> List[Dict[str, Any]]:
    """The media a live product carries RIGHT NOW (_PRODUCT_MEDIA_QUERY: id,
    status and the CDN url of an image, in listing order) -- the pass's ONE
    read of the listing, for both doors. Raises on a GraphQL error body or a
    product Shopify does not know: an unknown listing must never read as 'no
    media' (the pass would then attach every photograph again)."""
    body = await _graphql(db, _PRODUCT_MEDIA_QUERY, {"id": product_gid})
    if not isinstance(body, dict):
        raise RuntimeError("malformed graphql response")
    if body.get("errors"):
        raise RuntimeError("graphql errors: %s" % str(body["errors"])[:300])
    product = (body.get("data") or {}).get("product")
    if product is None:
        raise RuntimeError("product %s not found on Shopify" % product_gid)
    return (product.get("media") or {}).get("nodes") or []


async def push_image(db, image: Dict[str, Any]) -> PushResult:
    """Press ONE APPROVED design-queue image onto its parent product's listing.
    DARK by default; LIVE behind the gates. Never raises.

    ONE identity, ONE writer, ONE predicate: a design-queue image is on
    Shopify iff the ledger holds a live doc for its source url in the
    parent's listing, and what a press does is read off the ledger by
    ``image_press_plan`` (the same call the sweep's skip and the
    pushed/pending counts make); the row itself carries no Shopify id. The
    LIVE press never calls productCreateMedia on its own -- it runs the SAME
    photo pass the product press runs (``sync_product_media`` in the DESIGN
    lane: the photo list is this row's url alone, ``design_row`` is the
    row), so:
      * an image already on the listing is a no-op (zero network, its gid in
        the payload) -- a re-press can never mint a second MediaImage;
      * a row whose asset was replaced has the old media tombstoned and
        deleted and the new one attached, exactly once -- and only THIS
        row's old asset: pressing one image never attaches or drops a
        sibling row's, so the press is exactly the image the human pressed;
      * the product's own photographs are never touched by this press -- not
        attached when the product press has not put them up, not dropped
        when IMS removed them, not reordered: those are the product press's
        calls, made under its own publish gate;
      * media IMS does not own on the listing is never touched, and a
        listing IMS owns nothing on is refused (adopt it first), never
        attached to blind.

    GUARDS: only an APPROVED image is push-eligible (the design queue gate).
    Anything else returns ok=False action=skip (Fail Loudly) without a network
    call. A product IMS would not publish (no photograph of its own --
    push_product's gate, same reason ``no_photo``) gets no design image
    either, nor does one push_product refuses as blocked from online (the
    same verdict, ``online_sync_blocked`` / ``block_status_unverifiable``).
    The parent product MUST already be on Shopify (ecom.shopify_product_id) --
    without it there is nothing to attach the media to; that is a skip too.

    ONE PASS PER PRODUCT: the whole press runs under the product's
    media_lease, and it presses the queue row AS IT IS NOW (re-read by
    image_id) -- the images sweep hands in rows it loaded up front, and a row
    deleted or re-pointed since must never be pressed from that copy."""
    iid = image.get("image_id")
    try:
        async with media_lease(db, image.get("product_id")) as renew:
            row = db["product_images"].find_one({"image_id": iid}) if iid else None
            if row is None:
                return PushResult(
                    mode=MODE_SIMULATED,
                    entity="image",
                    action="skip",
                    target_id=iid,
                    ok=False,
                    error="the design-queue row no longer exists -- nothing to press",
                    reason="row_gone",
                )
            return await _press_image(db, row, renew)
    except Exception as e:  # noqa: BLE001 -- MediaBusy, or the lease / row read failed: nothing sent
        return PushResult(
            mode=MODE_SIMULATED, entity="image", action="skip", target_id=iid, ok=False, error=str(e)
        )


async def _press_image(db, image: Dict[str, Any], renew: Callable[[], bool] = lambda: True) -> PushResult:
    """push_image's press, under the lease, of the row as the db holds it."""
    iid = image.get("image_id")
    pid = image.get("product_id")

    # WHAT THIS PRESS DOES is read off the parent twin and its ledger by
    # image_press_plan -- the one predicate the sweep's skip and the counts
    # share -- INCLUDING every refusal made before anything is sent: the Hub
    # Phase 5 push-lock (defense-in-depth, FIRST gate: an image attaches to
    # its parent product, so a push-locked brand's image must NEVER reach
    # Shopify either, even if the product got there before its brand was
    # locked; fail-CLOSED on a real match), the online block mirrored from
    # push_product (a product in an online_sync_blocked collection -- or
    # whose block status cannot be read -- is never written on Shopify, a
    # media attach included), an unfetchable url, the photo rule mirrored
    # from push_product, a parent that owns no listing and an unreadable
    # ledger.
    _parent, _img_lock, press = read_image_press(db, image)
    if press.get("reason") == "push_locked":
        return _blocked_result("image", iid, _img_lock)

    if str(image.get("status") or "").upper() != "APPROVED":
        return PushResult(
            mode=MODE_SIMULATED,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload={"status": image.get("status")},
            error="only APPROVED images are push-eligible",
        )

    # Resolve the parent product's Shopify gid (media attaches to a product).
    product_gid = _resolve_product_gid(db, pid)
    media = build_media_inputs([image])
    payload: Dict[str, Any] = {"productId": product_gid, "media": media}
    if press["action"] == "skip":
        # A skip before the dark/live split, zero network either way.
        return PushResult(
            mode=(
                MODE_BLOCKED
                if press["reason"] in ("no_photo", "online_sync_blocked", "block_status_unverifiable")
                else MODE_SIMULATED
            ),
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload=payload,
            error=press["error"],
            reason=press["reason"],
        )
    src = media[0]["originalSource"]
    live, reason = _live_or_reason(db)

    # ALREADY ON THE LISTING -> nothing to send, dark or live: this is the
    # check the 09-06 sync audit found missing, the one that stops a re-press
    # from attaching the same source url again. UNLESS this row's lane still
    # holds an asset it carried before it was replaced (a delete that
    # failed, or a lost attach of an old url): that press must run again to
    # take the old media down -- a no-op over an orphan is exactly the silent
    # success this door exists to end.
    have = press["gid"]
    if press["action"] == "noop":
        return PushResult(
            mode=MODE_LIVE if live else MODE_SIMULATED,
            entity="image",
            action="noop",
            target_id=iid,
            ok=True,
            shopify_id=have,
            payload={**payload, "media_gid": have},
            reason="already on the listing",
        )
    if press["drop"]:
        payload["drop"] = press["drop"]

    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="image",
            action=press["action"],
            target_id=iid,
            ok=True,
            shopify_id=have,
            payload=payload,
            reason=reason,
        )

    # THE DESIGN LANE: the pass governs the docs of THIS image_id only -- it
    # attaches this row's url and drops the asset the row held before it was
    # replaced. The product's own photographs and every other design row are
    # kept exactly where they are (see the ownership note). An asset to drop
    # that another APPROVED row of this product sources too is that row's
    # image as well: it is HANDED to that row's lane, never deleted from
    # under it.
    try:
        heirs: Dict[str, str] = {}
        if press["drop"]:
            for r in db["product_images"].find({"product_id": pid}):
                u = image_source_url(r)
                if (
                    u in press["drop"]
                    and r.get("image_id")
                    and r.get("image_id") != iid
                    and str(r.get("status") or "").upper() == "APPROVED"
                ):
                    heirs.setdefault(u, str(r["image_id"]))
        summary = await sync_product_media(
            db, pid, product_gid, design_row=image, heirs=heirs, renew=renew
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action="update" if have else "create",
            target_id=iid,
            ok=False,
            payload=payload,
            error=str(e),
        )
    # THE FACT LIVES IN THE LEDGER: read it back rather than trust the pass.
    # A media that landed on Shopify but is not recorded live would be
    # re-checked by the next press, so that is a loud failure with the
    # minted gid kept for reconcile (attached_map), never a silent ok=True.
    try:
        new_gid = image_media_gid(_resolve_product_doc(db, pid), image, media_rows(db, pid))
    except Exception:  # noqa: BLE001 -- unreadable: not recorded, as far as this press knows
        new_gid = None
    minted = {r["url"]: r["id"] for r in summary.get("attached_map") or []}.get(src)
    error = summary.get("error")
    if not new_gid and not error:
        if src in (summary.get("held") or []):
            error = (
                "an earlier attach of this image may still be landing on Shopify "
                "-- press again in 15 minutes"
            )
        elif minted:
            error = (
                "media attached on Shopify (%s) but the online_media write-back "
                "failed -- the next press settles it by file name" % minted
            )
        elif summary.get("hands_off"):
            error = (
                "hands off: IMS owns none of the %d media on this listing -- "
                "adopt them first (scripts/adopt_shopify_media_map.py)"
                % int(summary.get("on_shopify") or 0)
            )
        else:
            error = "the photo pass attached nothing for this image"
    return PushResult(
        mode=MODE_LIVE,
        entity="image",
        # 'update' only when the url was already mapped and this press ran
        # to drop the replaced asset; every attach (or attempt) is a create.
        action="update" if have and not minted else "create",
        target_id=iid,
        ok=not error,
        shopify_id=new_gid or minted,
        payload=payload,
        photos=summary,
        error=error,
        code=summary.get("code"),
    )


def _user_errors_media(body: Dict[str, Any], field: str = "productCreateMedia") -> Optional[str]:
    """The media mutations (productCreateMedia / productDeleteMedia /
    productReorderMedia) use `mediaUserErrors` (not `userErrors`)."""
    if not isinstance(body, dict):
        return "malformed graphql response"
    if body.get("errors"):
        return f"graphql errors: {str(body['errors'])[:300]}"
    field_obj = (body.get("data") or {}).get(field) or {}
    ue = field_obj.get("mediaUserErrors") or []
    if ue:
        return f"mediaUserErrors: {str(ue)[:300]}"
    return None


def _resolve_product_doc(db, product_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Load the parent catalog_products doc (for the push-lock brand check on an
    image). Fail-soft -> None."""
    if not product_id or db is None:
        return None
    try:
        return db["catalog_products"].find_one({"id": product_id})
    except Exception:  # noqa: BLE001
        return None


def _resolve_product_gid(db, product_id: Optional[str]) -> Optional[str]:
    """Look up the parent catalog_products' ecom.shopify_product_id (the gid the
    image media attaches to). A SIZE VARIANT's twin resolves to None whatever
    it carries: it owns no listing, so its image must never attach to the
    PARENT's (a twin repair that stamps the parent gid on a child twin would
    otherwise route the child's APPROVED photo onto the parent's media through
    push_image / the images sweep). Fail-soft -> None."""
    if not product_id:
        return None
    try:
        doc = db["catalog_products"].find_one({"id": product_id})
        if doc is None or is_variant_of(doc):
            return None
        gid = (doc.get("ecom") or {}).get("shopify_product_id")
        return _as_shopify_gid(gid, "Product") if gid else None
    except Exception:  # noqa: BLE001
        return None
