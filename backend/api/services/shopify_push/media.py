"""Shopify push -- media

Images/media: the `product_photo_urls` photo predicate, attaching
photos on create, media inputs, `push_image` and its resolve/write-back
helpers.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit
import os
import re

from agents.nexus_providers import _as_shopify_gid

from ._shared import (
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
    Those rows push on their own, LATER press (push_image), and a photo that
    arrives after the product is already visible does not protect the
    storefront. A product whose only photo lives in the design queue is refused
    rather than published bare -- conservative, and the operator fixes it by
    putting the photo on the product."""
    out: List[str] = []
    public_base = (os.getenv("PUBLIC_API_BASE_URL") or "").strip().rstrip("/")

    def _add(value: Any) -> None:
        if isinstance(value, dict):
            value = value.get("url") or value.get("src")
        if not isinstance(value, str):
            return
        url = value.strip()
        if public_base and url.startswith(_APP_IMAGE_PATH):
            url = public_base + url
        if url.lower().startswith(("http://", "https://")) and url not in out:
            out.append(url)

    _add(product.get("image_url"))
    imgs = product.get("images")
    if isinstance(imgs, (list, tuple)):
        for item in imgs:
            _add(item)
    _add(product.get("image"))
    return out


async def _attach_product_photos(
    db, product_gid: str, urls: List[str], alt: str = ""
) -> Dict[str, Any]:
    """LIVE-only: attach the product's photographs to the Shopify product in the
    SAME press that wrote the product (productCreateMedia). Fail-SOFT side
    channel -- reported on the result, never flips the push's ok -- but the
    caller WITHHOLDS the publish when nothing attached, so a media failure
    leaves an invisible product rather than a visible grey box.

    Returns ``media_map`` too -- the ``[{url, id}]`` pairs Shopify minted for
    the urls it was given, IN INPUT ORDER (productCreateMedia answers one node
    per input, in order) -- so the photo pass can record which Shopify media
    IMS owns and never attach the same photograph twice. ``alt`` is the
    design row's alt text (the design press); the product's own photos go up
    with alt ''."""
    media = build_media_inputs([{"url": u, "alt_text": alt} for u in urls])
    if not media:
        return {"attached": 0, "error": "no usable photograph"}
    try:
        body = await _graphql(
            db, _PRODUCT_CREATE_MEDIA, {"productId": product_gid, "media": media}
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft side channel
        # No answer: the photo may or may not have landed.
        return {"attached": 0, "error": str(e), "no_answer": True}
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
            "fetch_failed": True,
            "error": "Shopify could not fetch the photograph"
            + (": %s" % detail if detail else ""),
        }
    return {"attached": len(ok_nodes), "media_map": media_map}


# ---------------------------------------------------------------------------
# THE PHOTO PASS (sync audit gap #3, owner 2026-09-06): "replacing or removing
# a photo, and reordering, update Shopify instead of silently doing nothing."
#
# OWNERSHIP. IMS manages ONLY the media it attached itself, recorded on the
# twin as ``ecom.media_map = [{url: <IMS source url>, id: <MediaImage gid>}]``
# (written on attach, pruned on delete). Media that is on Shopify but not in
# the map -- the hand-uploaded photographs on the connector-created Ray-Ban
# Meta products, anything the design queue (push_image) attached, anything a
# human added in the Shopify admin -- is NEVER deleted or re-attached: it is
# counted as ``unmanaged`` and left exactly where it is. When IMS owns nothing
# on a product that already carries media, the pass keeps its hands off
# entirely (no attach either): that is today's behaviour for the products
# that went live before the map existed, and it is what stops a re-press from
# minting a duplicate of every photograph on them.
#
# ORDER OF OPERATIONS is attach -> delete -> reorder, and a failed step stops
# the pass: a replacement is on Shopify BEFORE the photo it replaces comes
# down, so a listing never loses its last photograph to a half-done pass.
# Before any delete the {product_id, media_gid, url, shopify_url, deleted_at}
# row goes to ``online_media_tombstones`` -- the never-lose-bytes lesson.
# ---------------------------------------------------------------------------

TOMBSTONES_COLLECTION = "online_media_tombstones"
MEDIA_LIMIT_CODE = "MEDIA_LIMIT_250"


def owned_media(product: Dict[str, Any]) -> List[Dict[str, str]]:
    """The ``ecom.media_map`` rows IMS wrote on attach: ``[{url, id}]`` in IMS
    order. Pure; malformed rows dropped; never raises."""
    rows = (product.get("ecom") or {}).get("media_map")
    out: List[Dict[str, str]] = []
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("url") and r.get("id"):
                out.append({"url": str(r["url"]), "id": str(r["id"])})
    return out


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
) -> Dict[str, Any]:
    """PURE diff of IMS's ordered photo list against the media IMS owns on
    Shopify. ``shopify_media`` is the product's current media node list (the
    create/update response); None means UNKNOWN (the dark plan), in which
    case the stored map is trusted as-is.

    Returns {attach: [url], delete: [{url, id, shopify_url}], reorder: [gid]
    (the desired order of the IMS-owned media, [] when already in order),
    unmanaged: n, hands_off: bool, owned: [{url, id}] (the rows that survive
    the delete; the attach's new gids are not known until it runs)}."""
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
            {"url": r["url"], "id": r["id"], "shopify_url": cdn.get(r["id"])}
            for r in live_owned
            if r["url"] not in photos
        ]
    )
    delete_ids = {d["id"] for d in delete}
    keep = [r for r in live_owned if r["id"] not in delete_ids]
    desired = [by_url[u] for u in photos if u in by_url]
    reorder: List[str] = []
    if current_ids is not None and not hands_off:
        keep_ids = {r["id"] for r in keep}
        owned_now = [i for i in current_ids if i in keep_ids]
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


async def _delete_media(
    db, product_id: Optional[str], product_gid: str, rows: List[Dict[str, Any]]
) -> Optional[str]:
    """Take media IMS owns off a listing: tombstone every row, then
    productDeleteMedia. The error text, or None when done. No tombstone, no
    delete. Never raises."""
    try:
        _tombstone_media(db, product_id, rows)
        body = await _graphql(
            db,
            _PRODUCT_DELETE_MEDIA,
            {"productId": product_gid, "mediaIds": [r["id"] for r in rows]},
        )
        return _user_errors_media(body, "productDeleteMedia")
    except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
        return str(exc)


def _writeback_media_map(db, product_id: str, media_map: List[Dict[str, str]]) -> bool:
    """Persist ecom.media_map (read-merge-write of the ecom sub-doc, the
    _writeback_product idiom). NEVER touches locally_modified. Fail-soft;
    True when the twin now holds ``media_map``, False when it could not be
    located or written (the adoption runbook reports on it)."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return False
        ecom = dict(doc.get("ecom") or {})
        if ecom.get("media_map") == media_map:
            return True
        ecom["media_map"] = media_map
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_PUSH] media_map write-back failed %s: %s", product_id, exc)
        return False


def _in_ims_order(owned: List[Dict[str, str]], photos: List[str]) -> List[Dict[str, str]]:
    """The map as stored: one row per IMS photo that has a gid, in IMS order."""
    by_url = {r["url"]: r["id"] for r in owned}
    return [{"url": u, "id": by_url[u]} for u in photos if u in by_url]


async def sync_product_media(
    db,
    product: Dict[str, Any],
    product_gid: str,
    photos: List[str],
    shopify_media: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """LIVE-only (the caller has passed the gates): make the IMS-owned media on
    the Shopify product match ``photos`` -- attach what is missing, delete what
    IMS dropped, reorder to IMS order -- per the ownership rule above.
    Fail-soft summary, never raises: {attached, deleted, reordered, unmanaged,
    on_shopify (the media count after the pass -- the publish precondition),
    hands_off, error?, code?}."""
    pid = product.get("id") or product.get("product_id")
    plan = plan_product_media(product, photos, shopify_media)
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
    # 1. ATTACH what IMS has and Shopify lacks (the replacement lands first).
    if plan["attach"]:
        res = await _attach_product_photos(db, product_gid, plan["attach"])
        summary["attached"] = int(res.get("attached") or 0)
        summary["on_shopify"] += summary["attached"]
        owned.extend(res.get("media_map") or [])
        if pid and res.get("media_map"):
            _writeback_media_map(db, pid, _in_ims_order(owned, photos))
        if res.get("error"):
            summary["error"] = res["error"]
            return summary
    # 2. DELETE what IMS dropped -- tombstone first, then the call.
    if plan["delete"]:
        err = await _delete_media(db, pid, product_gid, plan["delete"])
        if err:
            summary["error"] = err
            return summary
        summary["deleted"] = len(plan["delete"])
        summary["on_shopify"] -= summary["deleted"]
        if pid:
            _writeback_media_map(db, pid, _in_ims_order(owned, photos))
    # 3. REORDER the IMS-owned media into IMS order, in the SLOTS they already
    # occupy (the attach appended its new media at the end): media IMS does
    # not own keeps its exact position, so a hero shot a human placed first
    # in the Shopify admin stays first.
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


def build_media_inputs(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Shopify CreateMediaInput[] from APPROVED product_images. Prefer the
    designer's edited asset; fall back to the source url."""
    out: List[Dict[str, Any]] = []
    for img in images:
        src = img.get("edited_url") or img.get("url")
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



_LISTING_UNREAD = (
    "IMS could not read the photos on the website listing, so it changed "
    "nothing. Press Publish again in a minute."
)
_NO_LISTING = (
    "The website has no listing for this product any more, so the photo has "
    "nowhere to go and IMS changed nothing. Pressing Publish again will not "
    "help: the product has to be put back on the website first."
)
_RECORDS_UNREAD = (
    "IMS could not read its own records for this product, so it changed "
    "nothing. Press Publish again in a minute."
)
_STILL_PROCESSING = (
    "Shopify is still processing a photo on this listing, so IMS cannot tell "
    "yet whether this photo is already there. It changed nothing; press "
    "Publish again in a few minutes."
)
_TWO_COPIES = (
    "This photo is on the website listing more than once and IMS cannot tell "
    "which copy is its own, so it changed nothing. Remove the extra copy in "
    "the Shopify admin, then press Publish again."
)
_SIBLING_SENT = (
    "Another design photo of this product with the same file name was sent to "
    "the website and IMS has not recorded it yet, so it cannot tell which copy "
    "is this one. It changed nothing; press Publish on the other photo first, "
    "then on this one."
)
_NO_ANSWER = (
    "IMS could not tell whether the photo went up: Shopify did not answer. "
    "Press Publish again in a minute; IMS looks for the photo on the listing "
    "first."
)
_NOT_CONFIRMED = (
    "The new photo was sent, but IMS cannot see it finished on the website "
    "listing yet, so the old photo stays up. Press Publish again in a few "
    "minutes."
)
_OLD_KEPT = (
    "The new photo is on the website. The old one stays up: IMS cannot be "
    "sure it put that photo there, so it never takes it down. Remove it in "
    "the Shopify admin if it should go."
)
_OLD_STUCK = (
    "The new photo is on the website, but IMS could not take the old one "
    "down. Press Publish again in a minute."
)
_NOT_RECORDED = (
    "IMS could not save where this photo is on the website listing. Press "
    "Publish again in a minute; IMS looks for the photo on the listing first."
)
_UNEXPECTED = (
    "Something went wrong inside IMS while publishing this photo. Check the "
    "website listing before pressing Publish again."
)
_CHECK_ADDRESS = "Check that the photo opens in a browser, then press Publish again."


def _cdn_url(node: Dict[str, Any]) -> str:
    """A media node's CDN url ('' until Shopify has finished it)."""
    return str((node.get("image") or {}).get("url") or "")


def _status(node: Dict[str, Any]) -> str:
    return str(node.get("status") or "").upper()


async def _listing_media(db, product_gid: str) -> Optional[List[Any]]:
    """READ-ONLY: the listing's media nodes as Shopify has them now, or None
    when Shopify has no such product. Raises when IMS could not read them (a
    transport failure, a GraphQL error body, an answer without the media
    list). The ONE reader of a listing's media: the design press and the
    adoption runbook (scripts/adopt_shopify_media_map.py)."""
    body = await _graphql(db, _PRODUCT_MEDIA_QUERY, {"id": product_gid})
    if body.get("errors"):
        raise ValueError("graphql errors: %s" % str(body["errors"])[:300])
    product = (body.get("data") or {}).get("product")
    if product is None:
        return None
    return list(product["media"]["nodes"])


def _recorded_elsewhere(db, image: Dict[str, Any]) -> tuple:
    """What the rest of IMS holds on this image's product: (ids, flying).
    ids = the media the product's own photos (ecom.media_map) and its other
    design rows record or were answered for; one media, one owner, so such a
    media is never this row's copy, never taken down by this row and never a
    doubt about it. flying = the urls other design rows sent with no answer
    yet; a media named like one may be that row's photo, so it is a doubt.
    Raises when IMS cannot read its own records (push_image says so)."""
    pid = image.get("product_id")
    parent = db["catalog_products"].find_one({"id": pid}) or {}
    out = {r["id"] for r in owned_media(parent)}
    flying: List[str] = []
    me = (image.get("image_id"), image.get("url"))
    for row in db["product_images"].find({"product_id": pid}):
        if (row.get("image_id"), row.get("url")) == me:
            continue
        for key in ("shopify_image_id", "shopify_image_sent_id"):
            if row.get(key):
                out.add(str(row[key]))
        if row.get("shopify_image_sent") and not row.get("shopify_image_sent_id"):
            flying.append(str(row["shopify_image_sent"]))
    return out, flying


async def push_image(db, image: Dict[str, Any]) -> PushResult:
    """Online Store -> Design Queue -> Publish: put ONE APPROVED design photo on
    its parent product's Shopify listing. DARK by default; LIVE behind the
    gates. Never raises.

    GUARD: only an APPROVED image is push-eligible (the design queue gate).
    Anything else returns ok=False action=skip (Fail Loudly) without a network
    call. The parent product MUST already be on Shopify (ecom.shopify_product_id)
    -- without it there is nothing to attach the media to; that is a skip too.

    THE RECORD lives on the design row only (product_images, written by the
    press alone -- no catalogue save touches that collection):
    shopify_image_id = the media on the listing for this row; shopify_image_src
    = the url IMS uploaded it from (None when IMS cannot prove it uploaded
    it); shopify_image_sent = the url IMS is sending (noted BEFORE the attach)
    and shopify_image_sent_id = the media Shopify answered that send with;
    both cleared once the row records a media.

    PROOF that IMS uploaded a media is Shopify's answer to IMS's own attach:
    the recorded id with its src, or the noted sent id. A file name is not
    proof (a person's upload of the same file carries it) and neither is
    originalSource (Shopify's own storage copy, measured on prod 09-06), so a
    media found by file name alone is recorded with src None.

    LIVE, every press reads the listing first (``_listing_media``):
      1. The photo already there -- a media IMS uploaded from this url, else
         a media NO IMS record owns carrying the IMS file name (the CDN copy
         keeps it: ``_same_file``) -- is never attached again (action noop).
         A media the product's photos or another design row own is never this
         row's copy (one media, one owner).
      2. A replaced photo: the new one goes up first; the old one comes down
         only once a read shows the new one READY, and only when IMS uploaded
         it -- one found by file name stays, and the press says so once the
         new one is READY. The row records the new media only then.
      3. IMS's copy of this photo that Shopify could not fetch (FAILED) is
         taken off and the press says so; it never waits on a failed copy.
      4. When IMS cannot tell -- the read fails, two copies look alike, an
         unnamed media is still processing, another row's unanswered send
         carries the same file name, the attach got no answer -- it changes
         nothing more and says so in plain words.
    ponytail: a swap needs a second press while Shopify is still processing
    the new photo (no wait inside the press); add a short re-read loop if
    owners find that slow. Two presses at the same moment can both attach (no
    lock); the next press then says "more than once". Two same-named rows
    whose sends BOTH lost their answer hold each other ("press the other
    first") until one copy is removed in the Shopify admin."""

    iid = image.get("image_id")
    existing_gid = image.get("shopify_image_id")

    # Hub Phase 5 push-lock (defense-in-depth, FIRST gate): an image attaches to
    # its parent product, so a push-locked brand's image must NEVER reach Shopify
    # either. push_product is already blocked for a locked brand (so the parent is
    # normally never on Shopify), but this closes the legacy "product was on
    # Shopify before its brand got locked" gap. Fail-CLOSED on a real lock match.
    _parent = _resolve_product_doc(db, image.get("product_id"))
    if _parent is not None:
        _img_lock = push_lock_reason(db, "product", _parent)
        if _img_lock:
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
    action = "update" if existing_gid else "create"

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="image",
            action=action,
            target_id=iid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    if not product_gid:
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload=payload,
            error="parent product not on Shopify yet (push the product first)",
        )
    if not media:
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload=payload,
            error="no image url to push",
        )

    def _done(
        ok: bool, did: str, error: Optional[str] = None, gid: Optional[str] = None
    ) -> PushResult:
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action=did,
            target_id=iid,
            ok=ok,
            shopify_id=gid,
            payload=payload,
            error=error,
        )

    filt = _image_writeback_filter(image)

    def _note(**fields: Any) -> None:
        """The press's notes on its own row (what it sent). Fail-soft."""
        if not filt:
            return
        try:
            db["product_images"].update_one(filt, {"$set": fields})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_PUSH] image note failed %s: %s", filt, exc)

    try:
        want = media[0]["originalSource"]
        rec_src = image.get("shopify_image_src")
        sent_url = image.get("shopify_image_sent")
        sent_id = image.get("shopify_image_sent_id")
        try:
            elsewhere, flying = _recorded_elsewhere(db, image)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_PUSH] image records unread %s: %s", iid, exc)
            return _done(False, action, _RECORDS_UNREAD)
        try:
            nodes = await _listing_media(db, product_gid)
        except Exception as exc:  # noqa: BLE001 -- "could not read" is the answer
            logger.warning("[SHOPIFY_PUSH] media read failed %s: %s", product_gid, exc)
            return _done(False, action, _LISTING_UNREAD)
        if nodes is None:
            return _done(False, action, _NO_LISTING)
        listing = [n for n in nodes if isinstance(n, dict) and n.get("id")]
        by_id = {n["id"]: n for n in listing}
        # Media IMS uploaded for this row -> the url it uploaded it from.
        uploaded: Dict[str, str] = {}
        if existing_gid and rec_src:
            uploaded[existing_gid] = rec_src
        if sent_id and sent_url:
            uploaded[sent_id] = sent_url

        failed = [
            i
            for i, n in by_id.items()
            if _status(n) == "FAILED" and uploaded.get(i) == want and i not in elsewhere
        ]
        if failed:
            err = await _delete_media(
                db,
                image.get("product_id"),
                product_gid,
                [{"id": i, "url": want, "shopify_url": None} for i in failed],
            )
            if err:
                logger.warning("[SHOPIFY_PUSH] failed copy not taken off %s: %s", failed, err)
            elif existing_gid in failed:
                _writeback_image(db, image, None, None)
            else:
                _note(shopify_image_sent=None, shopify_image_sent_id=None)
            up = (
                existing_gid in by_id
                and existing_gid not in failed
                and _status(by_id[existing_gid]) != "FAILED"
            )
            return _done(
                False,
                action,
                "Shopify could not fetch this photo from its web address, so it "
                "is not on the website."
                + ("" if err else " IMS took the failed copy off the listing.")
                + (" The old photo stays up." if up else "")
                + " "
                + _CHECK_ADDRESS,
            )

        def _is_copy(n: Dict[str, Any]) -> bool:
            # One media, one owner: another record's media is never this
            # row's. A media IMS uploaded is this photo only when made from
            # this url; any other media when it carries the IMS file name. A
            # FAILED media is not a photograph.
            if _status(n) == "FAILED" or n["id"] in elsewhere:
                return False
            if n["id"] in uploaded:
                return uploaded[n["id"]] == want
            return _same_file(want, _cdn_url(n))

        copies = [n["id"] for n in listing if _is_copy(n)]
        mine = [c for c in copies if c in uploaded] or copies
        if len(mine) > 1:
            return _done(False, action, _TWO_COPIES)
        new_gid = mine[0] if mine else None
        if new_gid and new_gid not in uploaded and any(
            _same_file(f, _cdn_url(by_id[new_gid])) for f in flying
        ):
            return _done(False, action, _SIBLING_SENT)
        sent = None
        # The row's media, still on the listing, not this photo, no other
        # record's: it comes down when IMS uploaded it (old), else it stays
        # (kept). A failed one shows nothing, so it is never "kept up".
        prev = (
            existing_gid
            if existing_gid in by_id
            and existing_gid != new_gid
            and existing_gid not in elsewhere
            else None
        )
        shows = bool(prev) and _status(by_id[prev]) != "FAILED"
        old = prev if rec_src else None
        kept = prev if shows and not rec_src else None
        if new_gid is None:
            # A media Shopify has not named yet could be IMS's own copy from a
            # press whose answer was lost: wait for it rather than guess.
            if any(
                _status(n) in ("UPLOADED", "PROCESSING")
                and not _cdn_url(n)
                and n["id"] != existing_gid
                and n["id"] not in uploaded
                and n["id"] not in elsewhere
                for n in listing
            ):
                return _done(False, action, _STILL_PROCESSING)
            _note(shopify_image_sent=want, shopify_image_sent_id=None)
            res = await _attach_product_photos(
                db, product_gid, [want], alt=image.get("alt_text") or ""
            )
            if not res.get("media_map"):
                stays = " The old photo stays up." if shows else ""
                if res.get("no_answer"):
                    return _done(False, action, _NO_ANSWER + stays)
                logger.warning("[SHOPIFY_PUSH] design photo refused %s: %s", iid, res.get("error"))
                _note(shopify_image_sent=None)
                if res.get("fetch_failed"):
                    return _done(
                        False,
                        action,
                        "The photo did not go up: Shopify could not fetch it from "
                        "its web address.%s %s" % (stays, _CHECK_ADDRESS),
                    )
                return _done(False, action, "The photo did not go up: Shopify refused it.%s" % stays)
            new_gid = sent = res["media_map"][0]["id"]
            _note(shopify_image_sent_id=new_gid)
        if old or kept:
            if sent:
                try:
                    listing = [n for n in await _listing_media(db, product_gid) or [] if isinstance(n, dict)]
                except Exception as exc:  # noqa: BLE001 -- not confirmed is the answer
                    logger.warning("[SHOPIFY_PUSH] media re-read failed %s: %s", product_gid, exc)
                    listing = []
            if not any(n.get("id") == new_gid and _status(n) == "READY" for n in listing):
                return _done(False, action, _NOT_CONFIRMED, new_gid)
        if old:
            err = await _delete_media(
                db,
                image.get("product_id"),
                product_gid,
                [{"id": old, "url": rec_src, "shopify_url": _cdn_url(by_id[old]) or None}],
            )
            if err:
                logger.warning("[SHOPIFY_PUSH] old design photo not taken down %s: %s", old, err)
                return _done(False, action, _OLD_STUCK, new_gid)
        src = want if sent or uploaded.get(new_gid) == want else None
        if (new_gid, src) != (existing_gid, rec_src) or sent_url or sent_id:
            if not _writeback_image(db, image, new_gid, src):
                return _done(False, action, _NOT_RECORDED, new_gid)
        did = "update" if old else ("create" if sent else "noop")
        if kept:
            return _done(False, did, _OLD_KEPT, new_gid)
        return _done(True, did, gid=new_gid)
    except Exception:  # noqa: BLE001
        logger.exception("[SHOPIFY_PUSH] design photo press failed %s", iid)
        return _done(False, action, _UNEXPECTED)


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


def _image_writeback_filter(image: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The Mongo filter that uniquely locates this image doc for a write-back.

    Prefers the primary key `image_id`. When it is missing/null (the BVI-migrated
    docs are stored with image_id=None) it falls back to the documented natural
    key product_id + url (ProductImageRepository: "Idempotent keys (never _id):
    image_id | product_id | variant_id"). Returns None when NEITHER is available
    -- there is then no safe way to target exactly one row, so the caller must
    fail loudly rather than write blindly."""
    iid = image.get("image_id")
    if iid:
        return {"image_id": iid}
    pid = image.get("product_id")
    url = image.get("url")
    if pid and url:
        return {"product_id": pid, "url": url}
    return None


def _writeback_image(
    db, image: Dict[str, Any], shopify_id: Optional[str], src: Optional[str]
) -> bool:
    """Record on the product_images doc the media on the listing for it
    (shopify_image_id) and the url IMS uploaded it from (shopify_image_src;
    None when IMS cannot prove it uploaded it), and clear the press's notes
    of a send (shopify_image_sent / _sent_id: the send is resolved) -- the
    design row's record, and this is its only writer. Returns True iff a
    row was actually located + written, False otherwise (no usable key, or a
    fail-soft error).

    Takes the WHOLE image doc (not just an id) so it can locate the row via the
    natural key when image_id is null -- the exact condition that made the
    BVI-migrated docs silently skip their write-back before this fix."""
    filt = _image_writeback_filter(image)
    if filt is None:
        return False
    try:
        res = db["product_images"].update_one(
            filt,
            {
                "$set": {
                    "shopify_image_id": shopify_id,
                    "shopify_image_src": src,
                    "shopify_image_sent": None,
                    "shopify_image_sent_id": None,
                    "updated_at": _now(),
                }
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[SHOPIFY_PUSH] image write-back failed {filt}: {e}"
        )
        return False
    # matched_count on real pymongo; MockCollection exposes modified_count only.
    touched = getattr(res, "matched_count", None)
    if touched is None:
        touched = getattr(res, "modified_count", 0)
    return bool(touched)

