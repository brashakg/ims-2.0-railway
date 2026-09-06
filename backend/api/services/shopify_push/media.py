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
    logger,
    push_lock_reason,
)
from .transport import _graphql, _now
from .queries import (
    _MEDIA_LIMIT,
    _PRODUCT_CREATE_MEDIA,
    _PRODUCT_DELETE_MEDIA,
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
    db, product_gid: str, urls: List[str]
) -> Dict[str, Any]:
    """LIVE-only: attach the product's photographs to the Shopify product in the
    SAME press that wrote the product (productCreateMedia). Fail-SOFT side
    channel -- reported on the result, never flips the push's ok -- but the
    caller WITHHOLDS the publish when nothing attached, so a media failure
    leaves an invisible product rather than a visible grey box.

    Returns ``media_map`` too -- the ``[{url, id}]`` pairs Shopify minted for
    the urls it was given, IN INPUT ORDER (productCreateMedia answers one node
    per input, in order) -- so the photo pass can record which Shopify media
    IMS owns and never attach the same photograph twice."""
    media = build_media_inputs([{"url": u} for u in urls])
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


def match_media_to_photos(
    photos: List[str], shopify_media: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """PURE: pair each IMS photo url with the ONE Shopify media that positively
    identifies as that photograph -- the adoption rule for products that went
    live before ``ecom.media_map`` existed (scripts/adopt_shopify_media_map.py).

    A media is the photo when (either suffices, both are exact equality):
      R1  its ``originalSource.url`` IS the IMS url (the source we handed over);
      R3  its CDN file name IS the IMS url's file name (``_same_file``).
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
        url: [mid for mid, src, cdn in nodes if (src and src == url) or _same_file(url, cdn)]
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



async def push_image(db, image: Dict[str, Any]) -> PushResult:
    """Push ONE APPROVED product image to Shopify (productCreateMedia) onto its
    parent product. DARK by default; LIVE behind the gates with the returned
    MediaImage gid written back to shopify_image_id. Never raises.

    GUARD: only an APPROVED image is push-eligible (the design queue gate).
    Anything else returns ok=False action=skip (Fail Loudly) without a network
    call. The parent product MUST already be on Shopify (ecom.shopify_product_id)
    -- without it there is nothing to attach the media to; that is a skip too."""
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
    try:
        body = await _graphql(
            db, _PRODUCT_CREATE_MEDIA, {"productId": product_gid, "media": media}
        )
        err = _user_errors_media(body)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="image",
                action=action,
                target_id=iid,
                ok=False,
                payload=payload,
                error=err,
            )
        media_nodes = ((body.get("data") or {}).get("productCreateMedia") or {}).get(
            "media"
        ) or []
        new_gid = (media_nodes[0].get("id") if media_nodes else None) or existing_gid
        # Persist the MediaImage gid for idempotency. _writeback_image now takes
        # the WHOLE image doc so it can locate the row even when image_id is null
        # (the BVI-migrated docs) via the natural key (product_id + url). If it
        # STILL cannot persist, the media WAS created on Shopify but we have no
        # way to record it -> Fail Loudly (ok=False) instead of a silent success,
        # because a clean-looking ok=True on an un-recorded create is exactly what
        # let a re-run duplicate media. shopify_id is still returned so the audit
        # row captures the orphaned gid for manual reconcile.
        if new_gid:
            persisted = _writeback_image(db, image, new_gid)
            if not persisted:
                return PushResult(
                    mode=MODE_LIVE,
                    entity="image",
                    action=action,
                    target_id=iid,
                    ok=False,
                    shopify_id=new_gid,
                    payload=payload,
                    error=(
                        "media attached on Shopify (%s) but shopify_image_id "
                        "write-back failed: no stable image key (image_id or "
                        "product_id+url) to persist it -- manual reconcile "
                        "required to avoid a duplicate on re-push" % new_gid
                    ),
                )
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action=action,
            target_id=iid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action=action,
            target_id=iid,
            ok=False,
            payload=payload,
            error=str(e),
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
    image media attaches to). Fail-soft -> None."""
    if not product_id:
        return None
    try:
        doc = db["catalog_products"].find_one({"id": product_id})
        if doc is None:
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


def _writeback_image(db, image: Dict[str, Any], shopify_id: str) -> bool:
    """Persist shopify_image_id on the product_images doc. Returns True iff a row
    was actually located + written, False otherwise (no usable key, or a fail-soft
    error). The gid presence is the idempotency key (the image has no
    locally_modified flag), so a reliable write-back is what stops a re-push from
    duplicating media.

    Takes the WHOLE image doc (not just an id) so it can locate the row via the
    natural key when image_id is null -- the exact condition that made the
    BVI-migrated docs silently skip their write-back before this fix."""
    filt = _image_writeback_filter(image)
    if filt is None:
        return False
    try:
        res = db["product_images"].update_one(
            filt,
            {"$set": {"shopify_image_id": shopify_id, "updated_at": _now()}},
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

