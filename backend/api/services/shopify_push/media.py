"""Shopify push -- media

Images/media: the `product_photo_urls` photo predicate, attaching
photos on create, media inputs, `push_image` and its resolve/write-back
helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import os

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
from .queries import _PRODUCT_CREATE_MEDIA

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

    Only ever called when the Shopify product carries no media yet, so a
    re-press can never pile a duplicate copy of the same photo onto a live
    listing."""
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
    for n in nodes:
        n = n or {}
        if not n.get("id"):
            continue
        (failed if str(n.get("status") or "").upper() == "FAILED" else ok_nodes).append(n)
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
    return {"attached": len(ok_nodes)}


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


def _user_errors_media(body: Dict[str, Any]) -> Optional[str]:
    """productCreateMedia uses `mediaUserErrors` (not `userErrors`)."""
    if not isinstance(body, dict):
        return "malformed graphql response"
    if body.get("errors"):
        return f"graphql errors: {str(body['errors'])[:300]}"
    field_obj = (body.get("data") or {}).get("productCreateMedia") or {}
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

