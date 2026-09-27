"""Shopify push -- media

Images/media: the `product_photo_urls` photo predicate, the media diff pass
BOTH doors run (the product press and the design-queue press), media inputs
and `push_image`.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit
import os
import re

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
    same reason). Once pressed, a design-queue media is remembered on the
    same ``ecom.media_map`` as an ``image_id`` row -- its own lane, which the
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
    media_map row and the 'already on the listing' check all key on this one
    value, so a row can never be mapped under one spelling and looked up
    under another. Pure."""
    return _photo_url(image.get("edited_url") or image.get("url"))


def _listing_map(parent: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The media_map a design-queue press may consult: the parent twin's --
    or NOTHING for a size-variant twin, which owns no listing whatever its
    ecom carries (a twin repair that copies the parent's ecom copies the map
    too, and a copied row must not make the child's press a no-op that
    claims the parent's media). Pure."""
    if not parent or is_variant_of(parent):
        return []
    return owned_media(parent)


def image_press_plan(
    parent: Optional[Dict[str, Any]], image: Dict[str, Any], *, lock: Optional[str] = None
) -> Dict[str, Any]:
    """PURE: what a press of this design-queue row does, read off the parent
    twin and its ``ecom.media_map`` alone -- THE one predicate the press, the
    sweep's skip and the pushed/pending counts all ask, so they can never
    disagree about a row:
      gid     the MediaImage the row's source url maps to; None when the
              image is not on the listing (or the parent is a size-variant
              twin, which owns no listing);
      drop    the urls this row (its ``image_id``) still maps that are NOT
              its source url -- the asset it carried before it was replaced,
              whose delete has not happened yet (never one of the product's
              own photographs: that is the product's lane, see owned_media);
      action  'noop' (mapped, nothing to drop: the press makes zero calls),
              'update' (mapped, a drop still pending: the press runs the pass
              to take the old asset down), 'create' (not on the listing) or
              'skip' -- the press REFUSES before it sends anything, with
              ``reason`` (push_locked | no_url | no_photo | not_on_shopify)
              and ``error`` (the line the press reports). ``lock`` is the
              parent's push_lock_reason, a db fact the caller supplies.
    A refusal is part of the answer so a row the press will never send is
    not 'pending' forever and the sweep does not press it every run."""
    src = image_source_url(image)
    gid = {r["url"]: r["id"] for r in _listing_map(parent)}.get(src) if src else None
    drop = [r["url"] for r in image_lane_media(parent, image) if r["url"] != src]
    plan = {"gid": gid, "drop": drop, "action": "create" if not gid else ("update" if drop else "noop")}
    on_shopify = (
        parent is not None
        and not is_variant_of(parent)
        and bool((parent.get("ecom") or {}).get("shopify_product_id"))
    )
    if lock:
        skip = ("push_locked", "push-locked: " + lock)
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
    else:
        return plan
    return {**plan, "action": "skip", "reason": skip[0], "error": skip[1]}


def image_lane_media(parent: Optional[Dict[str, Any]], image: Dict[str, Any]) -> List[Dict[str, str]]:
    """The map rows a press of this design-queue row GOVERNS -- its lane: the
    media on the parent's listing stamped with its ``image_id`` (whatever url
    the row carries now). Only a press of this row can take them down, so the
    row must not be deleted while this is non-empty. Pure."""
    if not image.get("image_id"):
        return []
    return [r for r in _listing_map(parent) if _in_lane(r, image)]


def image_media_gid(parent: Optional[Dict[str, Any]], image: Dict[str, Any]) -> Optional[str]:
    """The MediaImage gid the parent twin's map holds for this design-queue
    row's source url, None when it is not on the listing (``image_press_plan``,
    the gid alone -- what a screen shows next to the row). Pure."""
    return image_press_plan(parent, image)["gid"]


async def _attach_product_photos(
    db, product_gid: str, urls: List[str], alts: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """LIVE-only: attach the product's photographs to the Shopify product in the
    SAME press that wrote the product (productCreateMedia). Fail-SOFT side
    channel -- reported on the result, never flips the push's ok -- but the
    caller WITHHOLDS the publish when nothing attached, so a media failure
    leaves an invisible product rather than a visible grey box.

    Returns ``media_map`` too -- the ``[{url, id}]`` pairs Shopify minted for
    the urls it was given, IN INPUT ORDER (productCreateMedia answers one node
    per input, in order) -- so the photo pass can record which Shopify media
    IMS owns and never attach the same photograph twice. ``alts`` (``{url:
    alt text}``) is the design queue's alt for its row; a product's own
    photograph carries alt '' (see match_media_to_photos on why)."""
    media = build_media_inputs([{"url": u, "alt_text": (alts or {}).get(u)} for u in urls])
    if not media:
        return {"attached": 0, "error": "no usable photograph"}
    try:
        body = await _graphql(
            db, _PRODUCT_CREATE_MEDIA, {"productId": product_gid, "media": media}
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft side channel
        return {"attached": 0, "error": str(e)}
    err = _user_errors_media(body)
    if err:
        return {"attached": 0, "error": err}
    nodes = ((body.get("data") or {}).get("productCreateMedia") or {}).get("media") or []
    # AN ID IS NOT A PHOTOGRAPH. Shopify mints the MediaImage id BEFORE it
    # fetches the bytes off the url, so counting ids would call a 404 image a
    # photograph and publish the empty grey box the rule exists to prevent.
    # A node Shopify has already marked FAILED is not a photo.
    #
    # ponytail: this only catches the failure Shopify reports in THIS response.
    # The fetch is asynchronous, so a node that is still PROCESSING here can go
    # FAILED minutes later and nothing re-reads it -- the residue the
    # online_sync_health parity/uploads audit is for. Upgrade path if it bites:
    # re-read `media(first:n){status}` on the next press and take the product
    # down rather than leave a grey box up.
    ok_nodes: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    media_map: List[Dict[str, str]] = []
    for i, n in enumerate(nodes):
        n = n or {}
        if not n.get("id"):
            continue
        if str(n.get("status") or "").upper() == "FAILED":
            failed.append(n)
            continue
        ok_nodes.append(n)
        # One node per input, in input order -- the ONLY way to learn which
        # gid belongs to which IMS url (the CDN url is not the source url).
        if len(nodes) == len(urls):
            media_map.append({"url": urls[i], "id": str(n["id"])})
    if failed and not ok_nodes:
        detail = "; ".join(
            str(e.get("details") or e.get("message") or e.get("code") or "")
            for n in failed
            for e in (n.get("mediaErrors") or [])
            if isinstance(e, dict)
        )
        return {
            "attached": 0,
            "error": "Shopify could not fetch the photograph"
            + (": %s" % detail if detail else ""),
        }
    return {"attached": len(ok_nodes), "media_map": media_map}


# ---------------------------------------------------------------------------
# THE PHOTO PASS (sync audit gap #3, owner 2026-09-06): "replacing or removing
# a photo, and reordering, update Shopify instead of silently doing nothing."
#
# OWNERSHIP. IMS manages ONLY the media it attached itself, recorded on the
# twin as ``ecom.media_map = [{url: <IMS source url>, id: <MediaImage gid>}]``.
# BOTH doors write it through the one writer (_writeback_media_map): the
# product press and the design-queue press (push_image) run this same pass,
# and the pass writes the map on EVERY run -- the rows still on the listing,
# what it minted, the rows whose delete has not happened yet -- so a media
# that left Shopify behind IMS's back (deleted in the admin, a lost delete
# response) is pruned on the next press of either door and the map converges.
#
# TWO LANES, ONE MAP. A map row is either the product's own photograph (no
# ``image_id``) or a design-queue media (it carries the queue row's
# ``image_id``, stamped when the design press attached it). Each press GOVERNS
# exactly one lane -- attaches into it, deletes from it, reorders it -- and
# KEEPS the other lane exactly where it is:
#   * the product press (and so the 01:00/09:00 sync) governs the own-photo
#     rows against product_photo_urls; it never attaches, deletes or reorders
#     a design row and never reads the design queue at all;
#   * the design press governs the rows of the ONE image_id it is pressing:
#     it attaches that row's url and drops the asset the row mapped before it
#     was replaced. It never attaches an own photograph the product press has
#     not put up yet, never drops one IMS removed, never reorders -- those are
#     the product press's calls, made under its own publish gate.
# A url that is one of the product's own photographs is the product's lane
# whichever door attached it -- decided BY URL on every read of the map
# (owned_media drops the image_id stamp of such a row, and the next write
# stores it that way), so a design asset later promoted to a product photo
# is never deleted by its old row's press, and removing that photo from the
# product still takes it down on Shopify.
# THE WRITE MERGES BY LANE: a pass writes the rows of the lane it governs and
# takes the other lane from what the twin holds at write time (not from the
# snapshot it planned on), so two presses interleaved on one product -- the
# 01:00/09:00 sweep loads its docs up front and then goes to the network per
# product; a human design press in that window -- cannot drop each other's
# rows and leave a live media unmanaged forever.
# Media that is on Shopify but not in the map -- the hand-uploaded photographs
# on the connector-created Ray-Ban Meta products, anything a human added in
# the Shopify admin -- is NEVER deleted or re-attached: it is counted as
# ``unmanaged`` and left exactly where it is. When IMS owns nothing on a
# product that already carries media, the pass keeps its hands off entirely
# (no attach either): that is today's behaviour for the products that went
# live before the map existed, and it is what stops a re-press from minting
# a duplicate of every photograph on them.
#
# ORDER OF OPERATIONS is attach -> delete -> reorder, and a failed step stops
# the pass: a replacement is on Shopify BEFORE the photo it replaces comes
# down, so a listing never loses its last photograph to a half-done pass.
# Before any delete the {product_id, media_gid, url, shopify_url, deleted_at}
# row goes to ``online_media_tombstones`` -- the never-lose-bytes lesson.
# ---------------------------------------------------------------------------

TOMBSTONES_COLLECTION = "online_media_tombstones"
MEDIA_LIMIT_CODE = "MEDIA_LIMIT_250"


def _map_row(r: Dict[str, Any]) -> Dict[str, str]:
    """ONE map row as stored: ``{url, id}`` plus ``image_id`` when the media is
    a design-queue press's (the lane marker -- see the ownership note). Pure."""
    row = {"url": str(r["url"]), "id": str(r["id"])}
    if r.get("image_id"):
        row["image_id"] = str(r["image_id"])
    return row


def owned_media(product: Dict[str, Any]) -> List[Dict[str, str]]:
    """The ``ecom.media_map`` rows IMS wrote on attach: ``[{url, id[, image_id]}]``
    in IMS order. THE LANE IS DECIDED HERE, BY URL: a row whose url is one of
    the product's own photographs (product_photo_urls) is the product's lane
    whichever door attached it, so its ``image_id`` stamp is dropped on read
    -- every plan, predicate and the map writer see one rule, and the next
    write stores the row that way. Pure; malformed rows dropped; never raises."""
    rows = (product.get("ecom") or {}).get("media_map")
    own = product_photo_urls(product)
    out: List[Dict[str, str]] = []
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("url") and r.get("id"):
                out.append(_map_row({**r, "image_id": None if str(r["url"]) in own else r.get("image_id")}))
    return out


def _in_lane(r: Dict[str, str], design_row: Optional[Dict[str, Any]]) -> bool:
    """Does a pass GOVERN this map row: the design press (``design_row``, the
    queue row being pressed) governs the rows stamped with ITS image_id; the
    product press (None) governs the rows without one. Pure."""
    if not design_row:
        return not r.get("image_id")
    return r.get("image_id") == str(design_row.get("image_id") or "")


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


def match_media_to_photos(
    photos: List[str],
    shopify_media: List[Dict[str, Any]],
    *,
    rules: tuple = ("exact",),
    product_gid: Optional[str] = None,
) -> Dict[str, Any]:
    """PURE: pair each IMS photo url with the ONE Shopify media that positively
    identifies as that photograph -- the adoption rule for products that went
    live before ``ecom.media_map`` existed (scripts/adopt_shopify_media_map.py).

    Under the default ``rules=('exact',)`` a media is the photo when (either
    suffices, both are exact equality):
      R1  its ``originalSource.url`` IS the IMS url (the source we handed over);
      R3  its CDN file name IS the IMS url's file name (``_same_file``).
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
    nodes = []
    for n in shopify_media or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        nodes.append(
            (
                str(n["id"]),
                str((n.get("originalSource") or {}).get("url") or "").strip(),
                str((n.get("image") or {}).get("url") or "").strip(),
            )
        )
    photos = list(dict.fromkeys(photos))
    hits: Dict[str, List[str]] = {
        url: [
            mid
            for mid, src, cdn in nodes
            if (exact and ((src and src == url) or _same_file(url, cdn)))
            or (connector and _connector_file(url, cdn, own_id))
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
        "unmanaged": [mid for mid, _s, _c in nodes if mid not in owned_ids],
        "names": {mid: _file_name(cdn) for mid, _s, cdn in nodes},
    }


def plan_product_media(
    product: Dict[str, Any],
    photos: List[str],
    shopify_media: Optional[List[Dict[str, Any]]] = None,
    *,
    design_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """PURE diff of IMS's ordered photo list against the media IMS owns on
    Shopify. ``shopify_media`` is the product's current media node list (the
    create/update response); None means UNKNOWN (the dark plan), in which
    case the stored map is trusted as-is.

    THE LANES (see the ownership note): without ``design_row`` this is the
    product press's plan and it governs the own-photo rows only (no
    ``image_id``); with ``design_row`` (the design-queue row being pressed)
    it governs the rows stamped with THAT row's ``image_id`` only. A row of
    the other lane is never attached (it is mapped), never deleted when
    ``photos`` omits it and never reordered -- kept, exactly as unmanaged
    media is, except that the map REMEMBERS it. A url ``photos`` names that
    is mapped in EITHER lane is on the listing and is not attached again.

    Returns {attach: [url], delete: [{url, id, shopify_url[, image_id]}],
    reorder: [gid] (the desired order of the IMS-owned media, [] when already
    in order), unmanaged: n, hands_off: bool, owned: [{url, id[, image_id]}]
    (the rows that survive the delete; the attach's new gids are not known
    until it runs)}."""
    def _governed(r: Dict[str, str]) -> bool:
        return _in_lane(r, design_row)

    owned = owned_media(product)
    cdn: Dict[str, Optional[str]] = {}
    if shopify_media is None:
        current_ids: Optional[List[str]] = None
        live_owned = owned
        unmanaged: List[str] = []
    else:
        current_ids = []
        for n in shopify_media:
            if isinstance(n, dict) and n.get("id"):
                current_ids.append(str(n["id"]))
                cdn[str(n["id"])] = (n.get("image") or {}).get("url")
        owned_ids = {r["id"] for r in owned}
        live_owned = [r for r in owned if r["id"] in current_ids]
        unmanaged = [i for i in current_ids if i not in owned_ids]
    hands_off = not live_owned and bool(unmanaged)
    by_url = {r["url"]: r["id"] for r in live_owned}
    attach = [] if hands_off else [u for u in photos if u not in by_url]
    delete = (
        []
        if hands_off
        else [
            {**r, "shopify_url": cdn.get(r["id"])}
            for r in live_owned
            if _governed(r) and r["url"] not in photos
        ]
    )
    delete_ids = {d["id"] for d in delete}
    keep = [r for r in live_owned if r["id"] not in delete_ids]
    desired = [by_url[u] for u in photos if u in by_url]
    reorder: List[str] = []
    if current_ids is not None and not hands_off:
        # Only the media ``photos`` names has a desired slot; a row the list
        # omits (the other lane) keeps its place, like unmanaged media.
        want = set(desired)
        owned_now = [i for i in current_ids if i in want]
        if owned_now != desired:
            reorder = desired
    return {
        "attach": attach,
        "delete": delete,
        "reorder": reorder,
        "unmanaged": len(unmanaged),
        "hands_off": hands_off,
        "owned": keep,
    }


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


def _writeback_media_map(db, product_id: str, media_map) -> bool:
    """Persist ecom.media_map (read-merge-write of the ecom sub-doc, the
    _writeback_product idiom). ``media_map`` is the list to store, or a
    function of the rows the twin holds NOW (owned_media of the doc this
    write reads) returning the list to store -- the pass's lane merge, so
    the other door's rows written since the pass took its snapshot survive
    (the ownership note). NEVER touches locally_modified. Fail-soft; True
    when the twin now holds the map, False when it could not be located or
    written (the adoption runbook reports on it)."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return False
        if callable(media_map):
            media_map = media_map(owned_media(doc))
        ecom = dict(doc.get("ecom") or {})
        # An absent map and an empty one are the same fact (owned_media reads
        # both as 'IMS owns nothing'): a pass that owns nothing never mints
        # an empty key on a twin the adoption runbook has not visited.
        if (ecom.get("media_map") or []) == media_map:
            return True
        ecom["media_map"] = media_map
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_PUSH] media_map write-back failed %s: %s", product_id, exc)
        return False


def _in_ims_order(rows: List[Dict[str, str]], order: List[str]) -> List[Dict[str, str]]:
    """The map as stored: the rows ``order`` names first, in that order (the
    product press: its own photographs in IMS order), then every other row
    in the order given -- the other lane, and a row whose delete is still
    pending, are kept, never governed here. A url appears once."""
    by_url = {r["url"]: r for r in rows}
    out = [_map_row(by_url[u]) for u in order if u in by_url]
    seen = {r["url"] for r in out}
    for r in rows:
        if r["url"] not in seen:
            out.append(_map_row(r))
            seen.add(r["url"])
    return out


async def sync_product_media(
    db,
    product: Dict[str, Any],
    product_gid: str,
    photos: List[str],
    shopify_media: List[Dict[str, Any]],
    *,
    design_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """LIVE-only (the caller has passed the gates): make the media IMS owns
    on the Shopify product, in the lane this pass governs, match ``photos``
    -- attach what is missing, delete what IMS dropped, reorder to IMS order
    -- per the ownership rule above. ``design_row`` (the design-queue row
    being pressed; ``photos`` is then that row's url alone) selects the
    design lane: the row this pass attaches is stamped with the queue row's
    ``image_id`` (unless its url is one of the product's own photographs --
    that media is the product's lane whichever door put it up) and carries
    the row's alt text; the asset the row mapped before is dropped.
    The map is written on EVERY pass, whatever happened (see the ownership
    note: a media gone from the listing is pruned, a row whose delete failed
    stays until it is off Shopify).
    Fail-soft summary, never raises: {attached, deleted, reordered, unmanaged,
    on_shopify (the media count after the pass -- the publish precondition),
    hands_off, attached_map? ([{url, id}] this pass minted), error?, code?}."""
    pid = product.get("id") or product.get("product_id")
    plan = plan_product_media(product, photos, shopify_media, design_row=design_row)
    current = [str(n["id"]) for n in shopify_media if isinstance(n, dict) and n.get("id")]
    summary: Dict[str, Any] = {
        "attached": 0,
        "deleted": 0,
        "reordered": False,
        "unmanaged": plan["unmanaged"],
        "hands_off": plan["hands_off"],
        "on_shopify": len(current),
    }
    if len(current) + len(plan["attach"]) > _MEDIA_LIMIT:
        summary["code"] = MEDIA_LIMIT_CODE
        summary["error"] = (
            "refused: %d media on Shopify + %d to attach exceeds the %d-per-product "
            "limit -- remove photographs before adding"
            % (len(current), len(plan["attach"]), _MEDIA_LIMIT)
        )
        return summary
    owned = list(plan["owned"])
    # A row leaves the map only AFTER its media is off Shopify: until step 2
    # has succeeded the rows planned for delete stay mapped (still owned, so
    # still deletable on the next press). A map that forgets a live media
    # makes an orphan no pass can ever see again -- and a design-queue
    # re-press would then read 'already on the listing' over that orphan.
    pending = [_map_row(d) for d in plan["delete"]]
    lane = str((design_row or {}).get("image_id") or "") or None
    own = product_photo_urls(product)
    alts = {image_source_url(design_row): design_row.get("alt_text")} if design_row else None
    # A row this pass planned on (its snapshot of the map) whose media the
    # listing no longer carries is DEAD: pruned whichever lane it is in. A
    # row the snapshot never held is the other door's, written since -- kept.
    dead = {r["id"] for r in owned_media(product) if r["id"] not in current}

    def _merge(stored: List[Dict[str, str]]) -> List[Dict[str, str]]:
        # THE MAP, BY LANE: this pass's lane from the pass, the other lane
        # from the twin as it is at write time (minus what this pass saw
        # dead). ponytail: a press racing another press of the SAME lane
        # still last-writer-wins; per-lane is what the two doors need. What
        # this pass MINTED is always its to record, whichever lane it is in
        # (a design press of an own photograph mints a product-lane row).
        minted = {r["id"] for r in summary.get("attached_map") or []}
        mine = [r for r in owned + pending if _in_lane(r, design_row) or r["id"] in minted]
        theirs = [r for r in stored if not _in_lane(r, design_row) and r["id"] not in dead]
        return _in_ims_order(theirs + mine, [] if design_row else photos)

    try:
        # 1. ATTACH what IMS has and Shopify lacks (the replacement lands first).
        if plan["attach"]:
            res = await _attach_product_photos(db, product_gid, plan["attach"], alts)
            summary["attached"] = int(res.get("attached") or 0)
            summary["on_shopify"] += summary["attached"]
            # The gids this pass minted, url by url, stamped with the design
            # row's image_id where the design press asked for it (never on an
            # own photograph) -- so a caller can still name a media that
            # landed on Shopify when the map write-back fails.
            minted = [
                _map_row({**r, "image_id": None if r["url"] in own else lane})
                for r in res.get("media_map") or []
            ]
            summary["attached_map"] = minted
            owned.extend(minted)
            if res.get("error"):
                summary["error"] = res["error"]
                return summary
        # 2. DELETE what IMS dropped -- tombstone first, then the call.
        if plan["delete"]:
            try:
                _tombstone_media(db, pid, plan["delete"])
                body = await _graphql(
                    db,
                    _PRODUCT_DELETE_MEDIA,
                    {"productId": product_gid, "mediaIds": [d["id"] for d in plan["delete"]]},
                )
                err = _user_errors_media(body, "productDeleteMedia")
            except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
                err = str(exc)
            if err:
                summary["error"] = err
                return summary
            summary["deleted"] = len(plan["delete"])
            summary["on_shopify"] -= summary["deleted"]
            pending = []
        # 3. REORDER the IMS-owned media into IMS order, in the SLOTS they
        # already occupy (the attach appended its new media at the end): media
        # IMS does not own keeps its exact position, so a hero shot a human
        # placed first in the Shopify admin stays first.
        by_url = {r["url"]: r["id"] for r in owned}
        desired = [by_url[u] for u in photos if u in by_url]
        deleted_ids = {d["id"] for d in plan["delete"]}
        survivors = [i for i in current if i not in deleted_ids]
        survivors += [r["id"] for r in owned if r["id"] not in survivors]
        slots = [i for i, gid in enumerate(survivors) if gid in set(desired)]
        owned_now = [survivors[i] for i in slots]
        if desired and owned_now != desired:
            try:
                body = await _graphql(
                    db,
                    _PRODUCT_REORDER_MEDIA,
                    {
                        "id": product_gid,
                        "moves": [
                            {"id": gid, "newPosition": str(slots[k])}
                            for k, gid in enumerate(desired)
                        ],
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
    finally:
        # THE MAP, ON EVERY PASS, whichever step it ended on: the rows still on
        # the listing (a media that left Shopify behind IMS's back is gone from
        # ``owned``: pruned), what this pass minted, and the rows whose delete
        # has not happened yet -- merged by lane over the twin as it is NOW
        # (_merge). The writer no-ops when nothing changed.
        if pid:
            _writeback_media_map(db, pid, _merge)


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
    """The media a live product carries RIGHT NOW (_PRODUCT_MEDIA_QUERY), in
    the node shape the product press reads off its create/update response --
    so the design-queue press can run the same pass. Raises on a GraphQL
    error body or a product Shopify does not know: an unknown listing must
    never read as 'no media' (the pass would then attach every photograph
    again -- the duplicate this whole door exists to stop)."""
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
    Shopify iff the parent twin's ``ecom.media_map`` maps its source url, and
    what a press does is read off that map by ``image_press_plan`` (the same
    call the sweep's skip and the pushed/pending counts make); the row itself
    carries no Shopify id. The LIVE press never calls productCreateMedia on
    its own -- it runs the SAME photo pass the product press runs
    (``sync_product_media`` in the DESIGN lane: ``photos`` is this row's url
    alone, ``design_row`` is the row, against the media the listing carries
    right now), so:
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
      * the map row is written through ``_writeback_media_map`` with the
        row's image_id (the design lane), so the product press keeps that
        media -- a design image reaches or leaves Shopify only through a
        press of its row;
      * media IMS does not own on the listing is never touched, and a
        listing IMS owns nothing on is refused (adopt it first), never
        attached to blind.

    GUARDS: only an APPROVED image is push-eligible (the design queue gate).
    Anything else returns ok=False action=skip (Fail Loudly) without a network
    call. A product IMS would not publish (no photograph of its own --
    push_product's gate, same reason ``no_photo``) gets no design image either.
    The parent product MUST already be on Shopify (ecom.shopify_product_id) --
    without it there is nothing to attach the media to; that is a skip too."""
    iid = image.get("image_id")

    # WHAT THIS PRESS DOES is read off the parent twin and its map by
    # image_press_plan -- the one predicate the sweep's skip and the counts
    # share -- INCLUDING every refusal made before anything is sent: the Hub
    # Phase 5 push-lock (defense-in-depth, FIRST gate: an image attaches to
    # its parent product, so a push-locked brand's image must NEVER reach
    # Shopify either, even if the product got there before its brand was
    # locked; fail-CLOSED on a real match), an unfetchable url, the photo
    # rule mirrored from push_product (a product with no photograph of its
    # own is never published, so it takes no design image either -- without
    # this a design press would run the pass over a listing the product press
    # refuses to touch), and a parent that owns no listing.
    _parent = _resolve_product_doc(db, image.get("product_id"))
    _img_lock = push_lock_reason(db, "product", _parent) if _parent is not None else None
    press = image_press_plan(_parent, image, lock=_img_lock)
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
    product_gid = _resolve_product_gid(db, image.get("product_id"))
    media = build_media_inputs([image])
    payload: Dict[str, Any] = {"productId": product_gid, "media": media}
    if press["action"] == "skip":
        # A skip before the dark/live split, zero network either way.
        return PushResult(
            mode=MODE_BLOCKED if press["reason"] == "no_photo" else MODE_SIMULATED,
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
    # from attaching the same source url again. UNLESS this row still maps
    # the asset it carried before it was replaced (a delete that failed):
    # that press must run again to take the old media down -- a no-op over
    # an orphan is exactly the silent success this door exists to end.
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

    # THE DESIGN LANE: the pass governs the rows of THIS image_id only --
    # it attaches this row's url and drops the asset the row mapped before
    # it was replaced. The product's own photographs and every other design
    # row are kept exactly where they are (see the ownership note).
    try:
        current = await _product_media(db, product_gid)
        summary = await sync_product_media(
            db, _parent, product_gid, [src], current, design_row=image
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
    # THE FACT LIVES ON THE TWIN: read the map back rather than trust the
    # pass. A media that landed on Shopify but is not in the stored map would
    # be attached again by the next press, so that is a loud failure with the
    # minted gid kept for reconcile (attached_map), never a silent ok=True.
    new_gid = image_media_gid(_resolve_product_doc(db, image.get("product_id")), image)
    minted = {r["url"]: r["id"] for r in summary.get("attached_map") or []}.get(src)
    error = summary.get("error")
    if not new_gid and not error:
        if minted:
            error = (
                "media attached on Shopify (%s) but the media_map write-back "
                "failed -- manual reconcile required to avoid a duplicate on "
                "re-push" % minted
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
