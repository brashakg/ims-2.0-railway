"""
IMS 2.0 - Catalogue PDF generator  (Share collection as PDF)
============================================================
Build a branded, print-ready A4 product catalogue PDF from EITHER a collection
(its resolved members -- manual CUSTOM membership OR SMART rule matches) OR an
explicit hand-picked list of product_ids. The owner downloads the file and shares
it with customers over WhatsApp / email.

TWO INDEPENDENT TOGGLES (owner request):
  * include_details -- a compact block of the product's key category attributes
    (shape / material / colour / size ...) + a short description.
  * include_mrp     -- the MRP (and a struck-through MRP + offer price when the
    product carries an offer).

DESIGN NOTES
  * reportlab (pure-python, no native system libs -> Railway-safe) builds the
    PDF; Pillow normalises fetched images (any format -> RGB JPEG, downscaled) so
    a product photo embeds cleanly and a broken/absent image degrades to a
    placeholder box -- a bad image can NEVER break the PDF (fail-soft everywhere).
  * The rupee amount is rendered "Rs 1,500" (ASCII) rather than the U+20B9 glyph:
    the built-in PDF fonts do not carry the rupee sign, so ASCII guarantees a
    correct render in every viewer without shipping a font asset.
  * The layout builder (`build_product_rows`) is a PURE function returning
    structured rows -- unit-tested directly, independent of PDF encoding.
  * No emoji (Windows cp1252).

This module is SELF-CONTAINED: it does not import the collections router (another
branch edits it) -- it talks to the repository + the shared smart-rule resolver.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Cap the number of product cards a single PDF renders. 100+ products paginate
# fine, but an unbounded set (a 4,000-product smart collection) would produce a
# huge file + a slow render; the cap keeps it bounded (the caller is told when
# the set was truncated).
MAX_PRODUCTS = 500

# Default brand shown on the cover when the caller resolves no store/brand name.
DEFAULT_BRAND = "Better Vision"

# Per-image fetch timeout + total concurrency for the image pre-fetch.
_IMG_TIMEOUT_S = 8.0
_IMG_CONCURRENCY = 8
# Longest edge (px) an embedded image is downscaled to (keeps the PDF small).
_IMG_MAX_EDGE = 420


# ===========================================================================
# Row building (PURE -- unit-tested without any PDF encoding)
# ===========================================================================

# Ordered (label, [candidate keys]) for the optional details block. Each value is
# read from the product's top level first, then its `attributes` sub-doc. First
# non-blank candidate wins; blanks are skipped; the block is capped (see below).
_DETAIL_SPEC: List[Tuple[str, List[str]]] = [
    ("Model", ["model", "model_number", "model_no"]),
    ("Shape", ["shape", "frame_shape"]),
    ("Material", ["frame_material", "material"]),
    ("Frame Colour", ["frame_colour", "frame_color"]),
    ("Colour", ["colour", "color"]),
    ("Lens Colour", ["lens_colour", "lens_color"]),
    ("Lens", ["lens_material", "lens_type"]),
    ("Size", ["size", "frame_size", "lens_width"]),
    ("Gender", ["gender"]),
    ("HSN", ["hsn", "hsn_code"]),
]

_MAX_DETAIL_LINES = 6
_MAX_DESC_CHARS = 220


def _clean_str(v: Any) -> str:
    """Trim + stringify a value; None/blank -> ''."""
    if v is None:
        return ""
    s = str(v).strip()
    return s


def _attr(product: Dict, key: str) -> Any:
    """Read a field from the product top level, then its `attributes` sub-doc."""
    if key in product and product.get(key) not in (None, ""):
        return product.get(key)
    attrs = product.get("attributes")
    if isinstance(attrs, dict) and attrs.get(key) not in (None, ""):
        return attrs.get(key)
    return None


def _num(v: Any) -> Optional[float]:
    """Coerce to a positive float, else None."""
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _pricing(product: Dict) -> Dict:
    p = product.get("pricing")
    return p if isinstance(p, dict) else {}


def _mrp_of(product: Dict) -> Optional[float]:
    pr = _pricing(product)
    return _num(product.get("mrp")) or _num(pr.get("mrp"))


def _offer_of(product: Dict) -> Optional[float]:
    pr = _pricing(product)
    return (
        _num(product.get("offer_price"))
        or _num(pr.get("offer_price"))
        or _num(product.get("selling_price"))
        or _num(pr.get("selling_price"))
    )


def _name_of(product: Dict) -> str:
    name = (
        _clean_str(product.get("title"))
        or _clean_str(product.get("name"))
        or _clean_str(product.get("model"))
    )
    if name:
        return name
    # Compose from brand + model as a last resort.
    composed = " ".join(
        s for s in (_clean_str(product.get("brand")), _clean_str(product.get("model"))) if s
    )
    return composed or _clean_str(product.get("sku")) or "Product"


def _brand_of(product: Dict) -> str:
    return _clean_str(product.get("brand")) or _clean_str(_attr(product, "brand")) or ""


def _image_url_of(product: Dict) -> Optional[str]:
    """The best single image URL for a product (mirrors the list_products alias:
    singular image_url, else the first of the images[] array, else `image`)."""
    url = _clean_str(product.get("image_url"))
    if url:
        return url
    imgs = product.get("images")
    if isinstance(imgs, list):
        for it in imgs:
            if isinstance(it, str) and it.strip():
                return it.strip()
            if isinstance(it, dict):
                cand = _clean_str(it.get("url") or it.get("src"))
                if cand:
                    return cand
    single = _clean_str(product.get("image"))
    return single or None


def _description_of(product: Dict) -> str:
    """A short plain-text description. Prefers short_description; falls back to
    description; strips HTML tags from an html-only source. Capped."""
    raw = (
        _clean_str(product.get("short_description"))
        or _clean_str(product.get("description"))
    )
    if not raw:
        html = _clean_str(product.get("description_html"))
        if html:
            raw = re.sub(r"<[^>]+>", " ", html)
            raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > _MAX_DESC_CHARS:
        raw = raw[: _MAX_DESC_CHARS - 1].rstrip() + "…"
    return raw


def _details_of(product: Dict) -> List[Tuple[str, str]]:
    """Curated, de-duplicated (label, value) pairs from the product's populated
    attributes. Category is always first when present; capped at
    _MAX_DETAIL_LINES."""
    out: List[Tuple[str, str]] = []
    seen_labels: set = set()

    category = _clean_str(product.get("category"))
    if category:
        out.append(("Category", category))
        seen_labels.add("Category")

    for label, keys in _DETAIL_SPEC:
        if label in seen_labels:
            continue
        for k in keys:
            val = _clean_str(_attr(product, k))
            if val:
                out.append((label, val))
                seen_labels.add(label)
                break
        if len(out) >= _MAX_DETAIL_LINES:
            break
    return out[:_MAX_DETAIL_LINES]


def build_product_rows(
    products: List[Dict],
    *,
    include_details: bool = False,
    include_mrp: bool = True,
) -> List[Dict]:
    """Shape raw product docs into display rows for the PDF (PURE function).

    Every row ALWAYS carries brand + name + image (raw url or None). The optional
    keys are present ONLY when their toggle is on, so a test can assert toggle
    behaviour structurally:
      * include_mrp     -> the `mrp` key (and `offer_price` when an offer exists).
      * include_details -> the `details` list (+ `description` when present).
    """
    rows: List[Dict] = []
    for p in products or []:
        if not isinstance(p, dict):
            continue
        row: Dict[str, Any] = {
            "product_id": p.get("product_id") or p.get("id") or p.get("sku"),
            "sku": _clean_str(p.get("sku")),
            "brand": _brand_of(p),
            "name": _name_of(p),
            "image": _image_url_of(p),
        }
        if include_mrp:
            mrp = _mrp_of(p)
            offer = _offer_of(p)
            row["mrp"] = mrp
            # Only surface an offer when it is a genuine discount off the MRP.
            if offer is not None and (mrp is None or offer < mrp):
                row["offer_price"] = offer
        if include_details:
            row["details"] = _details_of(p)
            desc = _description_of(p)
            if desc:
                row["description"] = desc
        rows.append(row)
    return rows


# ===========================================================================
# Product resolution (collection members OR explicit product_ids)
# ===========================================================================


def _strip_id(doc: Dict) -> Dict:
    return {k: v for k, v in doc.items() if k != "_id"}


def _product_union(db) -> List[Dict]:
    """UNION of the `products` spine + `catalog_products`, de-duped by SKU with the
    SPINE WINNING (so governed tags/attrs are present for the smart resolver).
    Fail-soft -> []."""
    if db is None:
        return []
    by_sku: Dict[str, Dict] = {}
    order: List[str] = []
    for coll_name in ("catalog_products", "products"):
        try:
            cursor = db[coll_name].find({})
        except Exception:  # noqa: BLE001
            continue
        for doc in cursor:
            if not isinstance(doc, dict):
                continue
            sku = doc.get("sku")
            if not sku:
                continue
            if sku not in by_sku:
                order.append(sku)
            by_sku[sku] = _strip_id(doc)
    return [by_sku[s] for s in order]


def _detail_by_sku(db, skus: List[str]) -> Dict[str, Dict]:
    """sku -> rich product doc, looked up in `products` then `catalog_products`
    (spine wins). Fail-soft -> {}."""
    out: Dict[str, Dict] = {}
    if db is None or not skus:
        return out
    for coll_name in ("products", "catalog_products"):
        try:
            for d in db[coll_name].find({"sku": {"$in": list(skus)}}):
                sku = d.get("sku")
                if sku and sku not in out:
                    out[sku] = _strip_id(d)
        except Exception:  # noqa: BLE001
            continue
    return out


def resolve_collection_products(db, collection_id: str) -> Optional[List[Dict]]:
    """Resolve a collection's effective, ordered product docs.

    CUSTOM -> its manual membership SKUs in position order.
    SMART  -> the shared ecom_smart_rules resolver over the product union.
    Returns None ONLY when the collection does not exist (so the caller 404s);
    a real-but-empty collection returns []. Fail-soft -> [] on any read error."""
    if db is None or not collection_id:
        return None
    try:
        from database.repositories import EcomCollectionRepository

        repo = EcomCollectionRepository(db["ecom_collections"])
        doc = repo.get_by_id(collection_id)
    except Exception:  # noqa: BLE001
        return None
    if doc is None:
        return None

    ctype = str(doc.get("collection_type") or "CUSTOM").upper()
    try:
        if ctype == "SMART":
            from . import ecom_smart_rules

            rules = ecom_smart_rules.normalize_rules(doc.get("rules") or [])
            disjunctive = bool(doc.get("disjunctive", False))
            skus = ecom_smart_rules.resolve_skus(
                _product_union(db), rules, disjunctive=disjunctive, limit=MAX_PRODUCTS
            )
        else:
            members = sorted(
                (doc.get("products") or []),
                key=lambda p: int((p or {}).get("position", 0) or 0),
            )
            skus = [m.get("sku") for m in members if m.get("sku")][:MAX_PRODUCTS]
    except Exception:  # noqa: BLE001
        return []

    detail = _detail_by_sku(db, skus)
    # Preserve membership order; a sku with no catalog row still appears (sku-only)
    # so a catalog gap never silently drops a member.
    return [detail.get(sku, {"sku": sku}) for sku in skus]


def resolve_products_by_ids(db, product_ids: List[str]) -> List[Dict]:
    """Resolve explicit product_ids to docs (catalog_products then products spine),
    PRESERVING the caller's order. Fail-soft -> []."""
    if db is None or not product_ids:
        return []
    ids = [str(pid).strip() for pid in product_ids if str(pid or "").strip()][:MAX_PRODUCTS]
    if not ids:
        return []
    found: Dict[str, Dict] = {}
    for coll_name in ("catalog_products", "products"):
        try:
            for d in db[coll_name].find({"product_id": {"$in": ids}}):
                pid = d.get("product_id")
                if pid and pid not in found:
                    found[pid] = _strip_id(d)
        except Exception:  # noqa: BLE001
            continue
    return [found[pid] for pid in ids if pid in found]


def product_ids_to_skus(db, product_ids: List[str]) -> List[str]:
    """Map picked product_ids -> their SKUs (spine then catalog), preserving order
    and dropping ids that have no SKU. Used when saving a hand-picked selection as
    a temporary CUSTOM collection (manual membership is SKU-keyed). Fail-soft."""
    docs = resolve_products_by_ids(db, product_ids)
    skus: List[str] = []
    seen: set = set()
    for d in docs:
        sku = _clean_str(d.get("sku"))
        if sku and sku not in seen:
            skus.append(sku)
            seen.add(sku)
    return skus


# ===========================================================================
# Image pre-fetch (async, bounded, fail-soft)
# ===========================================================================


def _file_id_from_path(url: str) -> Optional[str]:
    """Extract a GridFS file id from a self-hosted image path so it can be served
    IN-PROCESS (no network hop back to ourselves). Matches /products/image/<id>
    and /files/<id> shapes. Returns None for anything else."""
    m = re.search(r"/(?:products/image|files|file)/([^/?#]+)", url)
    return m.group(1) if m else None


def _fetch_local_bytes(url: str) -> Optional[bytes]:
    """Pull a self-hosted product image straight from the file store (GridFS).
    Fail-soft -> None."""
    fid = _file_id_from_path(url)
    if not fid:
        return None
    try:
        from .file_store import get_file_store

        store = get_file_store()
        if store is None:
            return None
        rec = store.get(fid, require_kind="product_image")
        if rec is None:
            rec = store.get(fid)  # older images may lack the kind tag
        return rec[0] if rec else None
    except Exception:  # noqa: BLE001
        return None


async def _fetch_one(client, url: str) -> Optional[bytes]:
    u = (url or "").strip()
    if not u:
        return None
    if u.startswith("data:"):
        try:
            import base64

            b64 = u.split(",", 1)[1]
            return base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return None
    if u.startswith("http://") or u.startswith("https://"):
        try:
            resp = await client.get(u)
            if resp.status_code == 200 and resp.content:
                return resp.content
        except Exception:  # noqa: BLE001
            return None
        return None
    # A relative / self-hosted path -> serve from GridFS in-process.
    return _fetch_local_bytes(u)


async def fetch_images(rows: List[Dict]) -> Dict[int, bytes]:
    """Pre-fetch the RAW image bytes for each row, keyed by row index. Bounded
    concurrency + per-request timeout; a failure yields NO entry for that index
    (the renderer draws a placeholder). NEVER raises."""
    out: Dict[int, bytes] = {}
    targets = [(i, r.get("image")) for i, r in enumerate(rows) if r.get("image")]
    if not targets:
        return out
    try:
        import httpx
    except Exception:  # noqa: BLE001
        httpx = None  # type: ignore

    sem = asyncio.Semaphore(_IMG_CONCURRENCY)

    async def _run(client, idx: int, url: str) -> None:
        async with sem:
            data = await _fetch_one(client, url)
        if data:
            out[idx] = data

    if httpx is not None:
        try:
            async with httpx.AsyncClient(
                timeout=_IMG_TIMEOUT_S, follow_redirects=True
            ) as client:
                await asyncio.gather(
                    *[_run(client, i, u) for i, u in targets], return_exceptions=True
                )
        except Exception:  # noqa: BLE001
            pass
    else:
        # No httpx -> only self-hosted (GridFS) images resolve.
        for i, u in targets:
            data = _fetch_local_bytes(u)
            if data:
                out[i] = data
    return out


def _prepare_image(raw: bytes) -> Optional[Tuple[io.BytesIO, int, int]]:
    """Normalise raw image bytes -> (JPEG BytesIO, width, height) via Pillow:
    any format decoded, RGBA/P flattened on white, downscaled to _IMG_MAX_EDGE.
    Fail-soft -> None (renderer draws a placeholder)."""
    try:
        from PIL import Image as PILImage

        im = PILImage.open(io.BytesIO(raw))
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = PILImage.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((_IMG_MAX_EDGE, _IMG_MAX_EDGE))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        buf.seek(0)
        return buf, im.width, im.height
    except Exception:  # noqa: BLE001
        return None


# ===========================================================================
# PDF rendering (reportlab platypus)
# ===========================================================================


def _money(v: Optional[float]) -> str:
    """Format an amount as 'Rs 1,500' (ASCII rupee -- built-in PDF fonts lack the
    U+20B9 glyph, so this renders correctly in every viewer)."""
    if v is None:
        return ""
    try:
        return "Rs {:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return ""


def slugify_filename(text: str, fallback: str = "catalogue") -> str:
    """Slugify a title into a safe download filename stem (no extension)."""
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return (s or fallback)[:80]


def render_catalogue_pdf(
    *,
    title: str,
    brand_name: str,
    rows: List[Dict],
    image_bytes: Optional[Dict[int, bytes]] = None,
    include_details: bool = False,
    include_mrp: bool = True,
    generated_on: Optional[datetime] = None,
    contact_line: str = "",
    truncated: bool = False,
) -> bytes:
    """Render the branded A4 catalogue and return the PDF bytes. Never raises for
    a per-product image problem (each embeds fail-soft to a placeholder)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        Image as RLImage,
        PageBreak,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    image_bytes = image_bytes or {}
    generated_on = generated_on or datetime.now()
    brand_name = brand_name or DEFAULT_BRAND
    title = (title or "Product Catalogue").strip() or "Product Catalogue"

    # House palette: muted greys with red as the single accent (matches the app
    # theme -- red only as the primary accent, never as a fill sea).
    red = colors.HexColor("#C1121F")
    ink = colors.HexColor("#1F2937")
    muted = colors.HexColor("#6B7280")
    hairline = colors.HexColor("#E5E7EB")
    placeholder_bg = colors.HexColor("#F3F4F6")

    styles = {
        "brand": ParagraphStyle(
            "brand", fontName="Helvetica-Bold", fontSize=26, textColor=red, leading=30
        ),
        "cover_title": ParagraphStyle(
            "cover_title", fontName="Helvetica-Bold", fontSize=20, textColor=ink,
            leading=24, spaceBefore=8,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta", fontName="Helvetica", fontSize=11, textColor=muted, leading=16
        ),
        "card_brand": ParagraphStyle(
            "card_brand", fontName="Helvetica-Bold", fontSize=8, textColor=red,
            leading=10, spaceAfter=1,
        ),
        "card_name": ParagraphStyle(
            "card_name", fontName="Helvetica-Bold", fontSize=9.5, textColor=ink,
            leading=11.5,
        ),
        "card_price": ParagraphStyle(
            "card_price", fontName="Helvetica-Bold", fontSize=9.5, textColor=ink,
            leading=12, spaceBefore=2,
        ),
        "card_detail": ParagraphStyle(
            "card_detail", fontName="Helvetica", fontSize=7.4, textColor=muted,
            leading=9.5,
        ),
        "card_desc": ParagraphStyle(
            "card_desc", fontName="Helvetica-Oblique", fontSize=7.2, textColor=muted,
            leading=9,
        ),
        "empty": ParagraphStyle(
            "empty", fontName="Helvetica", fontSize=12, textColor=muted, leading=18,
            spaceBefore=40,
        ),
    }

    page_w, page_h = A4
    margin = 15 * mm
    cols = 2 if include_details else 3
    content_w = page_w - 2 * margin
    gutter = 6 * mm
    card_w = (content_w - (cols - 1) * gutter) / cols
    img_box = card_w - 8  # image square side inside the card padding

    def _card(idx: int, row: Dict) -> Table:
        inner: List[Any] = []
        # Image (or placeholder).
        img_flowable: Any = None
        raw = image_bytes.get(idx)
        if raw:
            prepared = _prepare_image(raw)
            if prepared:
                buf, iw, ih = prepared
                if iw and ih:
                    scale = min(img_box / iw, img_box / ih)
                    try:
                        img_flowable = RLImage(
                            buf, width=iw * scale, height=ih * scale
                        )
                        img_flowable.hAlign = "CENTER"
                    except Exception:  # noqa: BLE001
                        img_flowable = None
        if img_flowable is None:
            ph = Table([[Paragraph("No image", styles["card_detail"])]],
                       colWidths=[img_box], rowHeights=[img_box])
            ph.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), placeholder_bg),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOX", (0, 0), (-1, -1), 0.5, hairline),
            ]))
            img_flowable = ph
        inner.append(img_flowable)
        inner.append(Spacer(1, 4))

        brand = row.get("brand")
        if brand:
            inner.append(Paragraph(_escape(brand), styles["card_brand"]))
        inner.append(Paragraph(_escape(row.get("name") or "Product"), styles["card_name"]))

        if include_mrp and "mrp" in row:
            mrp = row.get("mrp")
            offer = row.get("offer_price")
            if mrp is not None and offer is not None:
                price_html = (
                    '<strike><font color="#9CA3AF">%s</font></strike>&nbsp;&nbsp;'
                    '<font color="#C1121F">%s</font>'
                    % (_escape(_money(mrp)), _escape(_money(offer)))
                )
            elif offer is not None:
                price_html = '<font color="#C1121F">%s</font>' % _escape(_money(offer))
            elif mrp is not None:
                price_html = "MRP %s" % _escape(_money(mrp))
            else:
                price_html = ""
            if price_html:
                inner.append(Paragraph(price_html, styles["card_price"]))

        if include_details:
            for label, value in (row.get("details") or []):
                inner.append(
                    Paragraph(
                        "<b>%s:</b> %s" % (_escape(label), _escape(value)),
                        styles["card_detail"],
                    )
                )
            desc = row.get("description")
            if desc:
                inner.append(Spacer(1, 2))
                inner.append(Paragraph(_escape(desc), styles["card_desc"]))

        card = Table([[c] for c in inner], colWidths=[card_w - 4])
        card.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return card

    # Build the product grid as a Table (auto-paginates across pages).
    story: List[Any] = []

    # --- Cover ---
    story.append(Spacer(1, 30 * mm))
    story.append(Paragraph(_escape(brand_name), styles["brand"]))
    story.append(Paragraph(_escape(title), styles["cover_title"]))
    meta_bits = [
        generated_on.strftime("%d %b %Y"),
        "%d product%s" % (len(rows), "" if len(rows) == 1 else "s"),
    ]
    if truncated:
        meta_bits.append("showing the first %d" % MAX_PRODUCTS)
    story.append(Spacer(1, 6))
    story.append(Paragraph(" &nbsp;|&nbsp; ".join(_escape(b) for b in meta_bits),
                           styles["cover_meta"]))
    story.append(PageBreak())

    if not rows:
        story.append(Paragraph("No products in this selection.", styles["empty"]))
    else:
        cells: List[List[Any]] = []
        row_cells: List[Any] = []
        for idx, row in enumerate(rows):
            row_cells.append(_card(idx, row))
            if len(row_cells) == cols:
                cells.append(row_cells)
                row_cells = []
        if row_cells:
            while len(row_cells) < cols:
                row_cells.append("")
            cells.append(row_cells)

        grid = Table(
            cells,
            colWidths=[card_w + gutter / cols] * cols if False else [content_w / cols] * cols,
        )
        grid.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 0), (-1, -2), 0.5, hairline),
        ]))
        story.append(grid)

    # --- Page furniture (footer + running header after the cover) ---
    footer_left = "%s  -  %s" % (brand_name, contact_line) if contact_line else brand_name

    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(margin, 10 * mm, footer_left[:120])
        canvas.drawRightString(
            page_w - margin, 10 * mm, "Page %d" % canvas.getPageNumber()
        )
        canvas.setStrokeColor(hairline)
        canvas.setLineWidth(0.5)
        canvas.line(margin, 13 * mm, page_w - margin, 13 * mm)
        canvas.restoreState()

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=18 * mm,
        title=title,
        author=brand_name,
    )
    frame = Frame(
        margin, 18 * mm, content_w, page_h - margin - 18 * mm, id="body",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    from reportlab.platypus import PageTemplate

    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_on_page)])
    doc.build(story)
    return buf.getvalue()


def _escape(text: Any) -> str:
    """Escape the 3 XML-special chars for reportlab Paragraph markup."""
    s = "" if text is None else str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
