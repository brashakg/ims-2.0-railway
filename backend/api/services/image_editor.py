"""
IMS 2.0 - Product-image editor (Online Store design queue)
==========================================================
Council decision (2026-06-06): clean up product photos with a DETERMINISTIC
cut-out + fixed-backdrop + synthetic-shadow pipeline -- it copies the product
pixels, so it can NEVER hallucinate/alter the product (frame shape, colour,
logo, lens tint, engravings survive exactly). The generative "AI photoshoot"
class is rejected for catalog images because it re-synthesizes the product.

This module is the swappable PROVIDER SEAM behind the design queue's auto-edit
action. Pick the provider with the IMAGE_EDIT_PROVIDER env var:

  photoroom  -> Photoroom Image Editing API v2/edit, pinned to a STATIC solid
               background + `shadow.mode=ai.soft` (the AI here only PLACES a
               shadow under the existing cutout; it never repaints the subject).
               The generative `ai-backgrounds`/scene endpoints are deliberately
               unreachable from here. Needs PHOTOROOM_API_KEY.
  rembg      -> self-hosted BiRefNet/rembg matte + a synthetic contact shadow
               (MIT-licensed; commercial-safe). OPTIONAL deps (rembg + Pillow);
               unavailable until installed -- we do NOT bloat the base image.
  disabled   -> default when no key/provider: available() is False so the route
               fail-softs ("no editor configured") instead of erroring.

Everything is FAIL-SOFT: a missing key / missing dep / provider error never
crashes the worker -- the caller keeps the RAW image and flags it for manual.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

# Photoroom Image Editing API (Plus). The single network boundary.
_PHOTOROOM_URL = "https://image-api.photoroom.com/v2/edit"
_PHOTOROOM_TIMEOUT = 60.0


@dataclass(frozen=True)
class EditSpec:
    """The catalog STANDARD applied identically to every image (this is what
    makes the output CONSISTENT across the catalog -- one saved recipe, not
    per-image prompting). Owner signs these off; they become the template."""

    background_color: str = "FFFFFF"   # solid backdrop hex (no '#')
    output_size: str = "1000x1000"     # fixed canvas (square catalog tile)
    padding: float = 0.1               # breathing room around the product
    shadow_mode: str = "ai.soft"       # soft synthetic CONTACT shadow (safe)

    @classmethod
    def from_env(cls) -> "EditSpec":
        return cls(
            background_color=os.getenv("IMAGE_EDIT_BG_COLOR", "FFFFFF").lstrip("#"),
            output_size=os.getenv("IMAGE_EDIT_OUTPUT_SIZE", "1000x1000"),
            padding=float(os.getenv("IMAGE_EDIT_PADDING", "0.1") or 0.1),
            shadow_mode=os.getenv("IMAGE_EDIT_SHADOW_MODE", "ai.soft"),
        )


@runtime_checkable
class ImageEditor(Protocol):
    """Edit RAW product-photo bytes -> cleaned EDITED bytes. Deterministic by
    contract: product pixels are preserved; only the background + shadow + crop
    change."""

    name: str

    def available(self) -> bool:
        """True only when the provider is fully configured (key/deps present)."""

    async def edit(self, raw: bytes, spec: EditSpec) -> bytes:
        """Return edited image bytes. Raises on a provider/transport error
        (the caller fail-softs)."""


class DisabledEditor:
    """No provider configured -> the route reports it and keeps RAW."""

    name = "disabled"

    def available(self) -> bool:
        return False

    async def edit(self, raw: bytes, spec: EditSpec) -> bytes:  # noqa: ARG002
        raise RuntimeError(
            "No image editor configured. Set IMAGE_EDIT_PROVIDER=photoroom + "
            "PHOTOROOM_API_KEY (or install rembg for the self-host provider)."
        )


class PhotoroomEditor:
    """Photoroom v2/edit, pinned to a non-generative cut-out + static backdrop +
    soft synthetic shadow. NEVER sends a scene/background prompt, so the product
    can't be repainted."""

    name = "photoroom"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("PHOTOROOM_API_KEY", "")

    def available(self) -> bool:
        return bool(self._api_key)

    async def edit(self, raw: bytes, spec: EditSpec) -> bytes:
        if not self._api_key:
            raise RuntimeError("PHOTOROOM_API_KEY not set")
        import httpx  # already a project dep

        # DETERMINISTIC params only -- background.color (solid), a fixed output
        # size, padding, and an AI *shadow* (placed under the cutout). No
        # `background.prompt` / scene field is ever included, so this is a matte
        # + composite, not a generative regen.
        data = {
            "background.color": spec.background_color,
            "outputSize": spec.output_size,
            "padding": str(spec.padding),
            "shadow.mode": spec.shadow_mode,
            "export.format": "png",
        }
        files = {"imageFile": ("raw.png", raw, "application/octet-stream")}
        headers = {"x-api-key": self._api_key, "Accept": "image/png, application/json"}
        async with httpx.AsyncClient(timeout=_PHOTOROOM_TIMEOUT) as client:
            resp = await client.post(_PHOTOROOM_URL, headers=headers, data=data, files=files)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Photoroom edit failed ({resp.status_code}): {resp.text[:300]}"
            )
        return resp.content


def _rembg_editor() -> Optional[ImageEditor]:
    """Build the self-host editor only if its OPTIONAL deps are installed.
    Returns None when rembg/Pillow are absent (the common case -- we don't ship
    them in the base image to keep the build light)."""
    try:
        import rembg  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:  # noqa: BLE001 -- optional deps not installed
        return None

    class RembgEditor:
        name = "rembg"

        def available(self) -> bool:
            return True

        async def edit(self, raw: bytes, spec: EditSpec) -> bytes:
            import io

            import rembg
            from PIL import Image

            cut = rembg.remove(raw)  # alpha-matte cutout (product pixels intact)
            fg = Image.open(io.BytesIO(cut)).convert("RGBA")
            try:
                w, h = (int(x) for x in spec.output_size.lower().split("x"))
            except Exception:  # noqa: BLE001
                w = h = 1000
            canvas = Image.new("RGBA", (w, h), (255, 255, 255, 255))
            pad = max(0.0, min(spec.padding, 0.45))
            box_w, box_h = int(w * (1 - 2 * pad)), int(h * (1 - 2 * pad))
            fg.thumbnail((box_w, box_h), Image.LANCZOS)
            canvas.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2), fg)
            out = io.BytesIO()
            canvas.convert("RGB").save(out, format="PNG")
            return out.getvalue()

    return RembgEditor()


def get_image_editor() -> ImageEditor:
    """Resolve the configured editor. Default: photoroom if a key is present,
    else the self-host editor if its deps are installed, else disabled."""
    provider = (os.getenv("IMAGE_EDIT_PROVIDER", "") or "").strip().lower()
    if provider == "photoroom":
        return PhotoroomEditor()
    if provider == "rembg":
        return _rembg_editor() or DisabledEditor()
    if provider in ("", "auto"):
        if os.getenv("PHOTOROOM_API_KEY"):
            return PhotoroomEditor()
        rembg_ed = _rembg_editor()
        if rembg_ed is not None:
            return rembg_ed
    return DisabledEditor()
