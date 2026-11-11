"""
IMS 2.0 - what image are these BYTES, really?  (audit L1-F41)
=============================================================
The product-image doors used to trust a LABEL: the multipart upload trusted
the browser's declared Content-Type, the re-host trusted the remote server's
header. HTML labelled image/jpeg was stored and served as a product photo, and
staff saw "uploaded" for a permanently broken image.

`image_mime_of(content)` answers from the bytes alone:
  * the file signature names the format (JPEG, PNG, WebP, GIF, HEIC/HEIF,
    AVIF - the image types file_store.ALLOWED_MIME_TYPES permits);
  * a cheap structural check then refuses a file that only STARTS like an
    image or was cut short:
      JPEG      decoded by Pillow at 1/8 scale (bounded memory; a truncated or
                corrupt stream fails to decode)
      PNG       the chunk list must start with IHDR, hold image data and end
                with IEND inside the file (no pixel decode; chunk checksums
                are not enforced - browsers show such files)
      GIF       Pillow parses the header (a cut-short GIF is NOT detected)
      WebP      Pillow parses the header, and the RIFF length must fit inside
                the file
      HEIC/HEIF/AVIF   the ISO-BMFF `meta` box must be present and every box
                must fit inside the file. No decoder for these is relied on,
                so their pixel data is NOT decoded.
Returns the canonical mime of what the bytes ARE, or None. Callers store and
serve THAT mime, never the label.

Pillow is a pinned dependency (requirements.txt). If it cannot be imported the
signature and container checks still run and the decode step is skipped with a
warning - an upload must not start failing because a wheel is missing.

ASCII only (Windows cp1252).
"""

from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_HEIC_BRANDS = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs"}
)
_AVIF_BRANDS = frozenset({b"avif", b"avis"})
_HEIF_BRANDS = frozenset({b"mif1", b"msf1"})

# How far into the file the ftyp brand list is read. A real ftyp box is a few
# dozen bytes (8 header + major + minor + a handful of 4-byte brands); 256
# bytes holds 60 compatible brands. Without the cap a crafted box claiming the
# whole file made the scan hold every 4-byte slot of a 25 MB upload at once.
_FTYP_SCAN_BYTES = 256
# Top-level ISO-BMFF boxes walked before the file is refused (see _boxes_fit).
_MAX_TOP_BOXES = 256


def _signature_mime(content: bytes) -> Optional[str]:
    """The format the leading bytes announce, or None."""
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content[4:8] == b"ftyp":
        # Major brand + the compatible brands listed in the ftyp box. The
        # box's size field is the uploader's own number, so it only ever
        # SHORTENS the scan: the brands are read inside a small fixed window.
        size = int.from_bytes(content[:4], "big")
        end = min(size, len(content), _FTYP_SCAN_BYTES)
        brands = {content[8:12]} | {content[i : i + 4] for i in range(16, end, 4)}
        if brands & _AVIF_BRANDS:
            return "image/avif"
        if brands & _HEIC_BRANDS:
            return "image/heic"
        if brands & _HEIF_BRANDS:
            return "image/heif"
    return None


def _boxes_fit(content: bytes) -> bool:
    """ISO-BMFF (HEIC/HEIF/AVIF): the file must carry its `meta` box, and no
    top-level box may run past the end of the file - a cut-short upload leaves
    its last box overhanging. A real image has a handful of top-level boxes
    (ftyp, meta, mdat, ...): a file with more than _MAX_TOP_BOXES is refused
    instead of walked, so a file of millions of empty boxes costs nothing."""
    pos, end, seen_meta = 0, len(content), False
    for _ in range(_MAX_TOP_BOXES):
        if pos + 8 > end:
            return seen_meta
        size = int.from_bytes(content[pos : pos + 4], "big")
        seen_meta = seen_meta or content[pos + 4 : pos + 8] == b"meta"
        if size == 0:  # "this box runs to the end of the file"
            return seen_meta
        if size == 1:  # 64-bit size follows the type
            size = int.from_bytes(content[pos + 8 : pos + 16], "big")
        if size < 8:  # not a box header: trailing bytes, stop walking
            return seen_meta
        if pos + size > end:
            return False
        pos += size
    return False


def _png_complete(content: bytes) -> bool:
    """PNG: chunks from IHDR, through some IDAT, to an IEND that lies inside
    the file. A cut-short PNG has no IEND; padding after a signature has no
    IHDR."""
    pos, end, seen_idat = 8, len(content), False
    while pos + 12 <= end:
        kind = content[pos + 4 : pos + 8]
        if pos == 8 and kind != b"IHDR":
            return False
        pos += 12 + int.from_bytes(content[pos : pos + 4], "big")
        if pos > end:
            return False
        if kind == b"IEND":
            return seen_idat
        seen_idat = seen_idat or kind == b"IDAT"
    return False


def _decodes(content: bytes, mime: str) -> bool:
    """Pillow integrity check for JPEG (bounded decode), GIF and WebP (header)."""
    try:
        from PIL import Image  # noqa: PLC0415 - lazy: never an import-time dep
    except Exception:  # noqa: BLE001  # pragma: no cover - Pillow is pinned
        logger.warning("[IMAGE_CONTENT] Pillow unavailable; decode check skipped")
        return True
    try:
        with Image.open(io.BytesIO(content)) as im:
            if mime == "image/jpeg":
                # 1/8-scale decode: reads the whole stream, ~1/64 the memory.
                im.draft("RGB", (1, 1))
                im.load()
            # GIF / WebP: open() has parsed the header.
        return True
    except Exception:  # noqa: BLE001 - any decode failure means "not an image"
        return False


def image_mime_of(content: bytes) -> Optional[str]:
    """The canonical image mime these bytes ARE, or None when they are not a
    complete image of a supported type. See the module docstring."""
    mime = _signature_mime(content or b"")
    if mime is None:
        return None
    if mime in ("image/heic", "image/heif", "image/avif"):
        ok = _boxes_fit(content)
    elif mime == "image/png":
        ok = _png_complete(content)
    else:
        ok = _decodes(content, mime)
        if mime == "image/webp":
            ok = ok and int.from_bytes(content[4:8], "little") + 8 <= len(content)
    return mime if ok else None
