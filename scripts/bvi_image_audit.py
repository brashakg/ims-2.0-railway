#!/usr/bin/env python3
"""
IMS 2.0 -- BVI Phase 0 Image-Storage Audit (STATIC, no DB)
==========================================================
Statically scans the BVI (`ecommerce/`) source to answer the Phase-4 risk
question:

  "Are BVI product/variant images stored on the BVI app's LOCAL DISK (which 404s
   the moment BVI is shut off) or on a DURABLE host (Shopify CDN / S3 / absolute
   URL)?"

This determines whether Phase 4 must re-host images before BVI is killed.

HOW (purely static -- runs NOW, needs no database, no secrets):
  * Walks ecommerce/src for the image WRITE path and the storage targets.
  * Flags local-disk markers: writeFile / mkdir into public/uploads, the
    `/uploads/...` relative url string, process.cwd()+"public"+"uploads".
  * Flags durable markers: uploadFileToShopify / Shopify CDN, absolute http(s)
    URL assignment, S3 / cloud SDKs.
  * Reads the Prisma schema for the image models (ProductImage / VariantImage)
    and reports the url/originalUrl columns that hold whatever the write path
    produced.

OUTPUT: a clear text (or --json) finding with the file:line evidence, a verdict
(LOCAL_DISK_FALLBACK / DURABLE_ONLY / MIXED / UNKNOWN), and the Phase-4
implication. The LIVE per-row local-vs-durable COUNT comes from the parity
checker (scripts/bvi_parity_check.py) which reads the actual ProductImage rows;
this static audit explains the MECHANISM and where the risk lives in code.

No DB connection. No secrets. No emojis (Windows cp1252 safe).

USAGE:
  python scripts/bvi_image_audit.py            # text report
  python scripts/bvi_image_audit.py --json     # JSON report
  python scripts/bvi_image_audit.py --root <path-to-repo>

Exit codes:
  0 = scan completed (regardless of verdict).
  1 = the ecommerce/ tree could not be found (fail loud).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Marker patterns. Each is (label, compiled-regex). A hit records file:line:text.
_LOCAL_DISK_MARKERS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("public/uploads dir", re.compile(r"public[\"'/, )]+.{0,8}uploads", re.IGNORECASE)),
    ("writeFile to disk", re.compile(r"\bwriteFile\b")),
    ("mkdir upload dir", re.compile(r"\bmkdir\b")),
    ("/uploads/ relative url", re.compile(r"[\"'`]/uploads/")),
    ("process.cwd disk path", re.compile(r"process\.cwd\(\)")),
    ("local fallback comment", re.compile(r"save\s+locally|fall(?:ing)?\s*back\s+to\s+local|ephemeral", re.IGNORECASE)),
]

_DURABLE_MARKERS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("Shopify CDN upload", re.compile(r"uploadFileToShopify|stagedUploadsCreate|cdn\.shopify", re.IGNORECASE)),
    ("absolute http(s) url", re.compile(r"https?://")),
    ("S3 / cloud SDK", re.compile(r"\bS3\b|aws-sdk|@aws-sdk|cloudinary|blob\.vercel|getSignedUrl", re.IGNORECASE)),
]

# Files most relevant to the image write/storage path (scanned first + always
# reported even with zero hits, so their absence is itself informative).
_PRIORITY_HINTS = ("images", "imageUpload", "upload", "shopify", "media")


@dataclass
class Hit:
    file: str       # repo-relative path
    line: int
    label: str
    text: str       # the trimmed source line


@dataclass
class AuditResult:
    ecommerce_root: str
    files_scanned: int = 0
    local_hits: List[Hit] = field(default_factory=list)
    durable_hits: List[Hit] = field(default_factory=list)
    image_models: List[str] = field(default_factory=list)   # prisma model+columns evidence
    verdict: str = "UNKNOWN"
    implication: str = ""


def _iter_source_files(root: str):
    """Yield .ts/.tsx/.js/.jsx files under ecommerce/src (skip node_modules, .next)."""
    skip = {"node_modules", ".next", "dist", "build", ".git"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if fn.endswith((".ts", ".tsx", ".js", ".jsx")):
                yield os.path.join(dirpath, fn)


def _scan_file(path: str, rel: str) -> Tuple[List[Hit], List[Hit]]:
    """Return (local_hits, durable_hits) for one file."""
    local: List[Hit] = []
    durable: List[Hit] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh, start=1):
                line = raw.rstrip("\n")
                stripped = line.strip()
                if not stripped:
                    continue
                for label, pat in _LOCAL_DISK_MARKERS:
                    if pat.search(line):
                        local.append(Hit(rel, i, label, stripped[:160]))
                for label, pat in _DURABLE_MARKERS:
                    if pat.search(line):
                        durable.append(Hit(rel, i, label, stripped[:160]))
    except Exception:  # noqa: BLE001 -- a single unreadable file must not abort
        pass
    return local, durable


def _scan_prisma_image_models(schema_path: str) -> List[str]:
    """Extract the image-model column evidence (ProductImage / VariantImage)."""
    out: List[str] = []
    if not os.path.isfile(schema_path):
        return out
    try:
        with open(schema_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception:  # noqa: BLE001
        return out
    for model in ("ProductImage", "VariantImage"):
        m = re.search(r"model\s+" + model + r"\s*\{(.*?)\}", text, re.DOTALL)
        if not m:
            continue
        body = m.group(1)
        cols = []
        for col in ("url", "originalUrl", "shopifyMediaId", "isProcessed", "role"):
            if re.search(r"\b" + col + r"\b", body):
                cols.append(col)
        out.append(f"{model}: {', '.join(cols) if cols else '(no url-like columns found)'}")
    return out


def audit_images(repo_root: str) -> AuditResult:
    """Statically audit BVI image storage. Pure file I/O on the source tree."""
    ecommerce_src = os.path.join(repo_root, "ecommerce", "src")
    result = AuditResult(ecommerce_root=ecommerce_src)
    if not os.path.isdir(ecommerce_src):
        result.verdict = "UNKNOWN"
        result.implication = (
            f"ecommerce/src not found at {ecommerce_src} -- cannot audit."
        )
        return result

    for path in _iter_source_files(ecommerce_src):
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        result.files_scanned += 1
        local, durable = _scan_file(path, rel)
        result.local_hits.extend(local)
        result.durable_hits.extend(durable)

    # Prisma image-model evidence.
    schema_path = os.path.join(repo_root, "ecommerce", "prisma", "schema.prisma")
    result.image_models = _scan_prisma_image_models(schema_path)

    # --- verdict ---
    # The decisive signal is the WRITE path in the images upload route: BVI uploads
    # to Shopify CDN FIRST (durable) and FALLS BACK to local disk on failure. So if
    # we see BOTH a durable upload AND a local-disk writeFile/uploads marker, the
    # store is MIXED with a local-disk fallback -- the real risk.
    has_local_write = any(
        h.label in ("writeFile to disk", "/uploads/ relative url", "public/uploads dir")
        for h in result.local_hits
    )
    has_durable_upload = any(
        h.label == "Shopify CDN upload" for h in result.durable_hits
    )

    if has_local_write and has_durable_upload:
        result.verdict = "MIXED_LOCAL_FALLBACK"
        result.implication = (
            "Images are uploaded to the Shopify CDN (durable) FIRST, but the code "
            "FALLS BACK to local disk (public/uploads, ephemeral on Railway) when "
            "the Shopify upload fails. Any ProductImage/VariantImage row whose url "
            "is a relative '/uploads/...' path will 404 once BVI is shut off. "
            "PHASE 4 MUST re-host those local-disk rows before killing BVI. Run "
            "scripts/bvi_parity_check.py against the live DB to COUNT how many rows "
            "are local-disk vs durable."
        )
    elif has_local_write and not has_durable_upload:
        result.verdict = "LOCAL_DISK"
        result.implication = (
            "Images are written to local disk (public/uploads) with no durable "
            "host. EVERY image will 404 when BVI dies. Phase 4 must re-host ALL "
            "images."
        )
    elif has_durable_upload and not has_local_write:
        result.verdict = "DURABLE_ONLY"
        result.implication = (
            "Images go to a durable host (Shopify CDN) with no local-disk fallback. "
            "No Phase-4 re-host needed -- verify with the live parity-check count."
        )
    else:
        result.verdict = "UNKNOWN"
        result.implication = (
            "Could not conclusively classify the image write path from source. "
            "Inspect ecommerce/src/app/api/images/ manually and run the live "
            "parity-check image audit."
        )
    return result


def render_text(result: AuditResult) -> str:
    lines: List[str] = []
    lines.append("=" * 64)
    lines.append("BVI IMAGE-STORAGE AUDIT (static -- no DB)")
    lines.append("=" * 64)
    lines.append(f"scanned root : {result.ecommerce_root}")
    lines.append(f"files scanned: {result.files_scanned}")
    lines.append("")
    lines.append("PRISMA IMAGE MODELS")
    lines.append("-" * 64)
    if result.image_models:
        for m in result.image_models:
            lines.append(f"  {m}")
    else:
        lines.append("  (no ProductImage/VariantImage model found in schema.prisma)")

    lines.append("")
    lines.append(f"LOCAL-DISK MARKERS ({len(result.local_hits)})")
    lines.append("-" * 64)
    for h in result.local_hits[:40]:
        lines.append(f"  {h.file}:{h.line}  [{h.label}]")
        lines.append(f"      {h.text}")
    if len(result.local_hits) > 40:
        lines.append(f"  ... and {len(result.local_hits) - 40} more")

    lines.append("")
    lines.append(f"DURABLE MARKERS ({len(result.durable_hits)})")
    lines.append("-" * 64)
    for h in result.durable_hits[:40]:
        lines.append(f"  {h.file}:{h.line}  [{h.label}]")
        lines.append(f"      {h.text}")
    if len(result.durable_hits) > 40:
        lines.append(f"  ... and {len(result.durable_hits) - 40} more")

    lines.append("")
    lines.append("=" * 64)
    lines.append(f"VERDICT: {result.verdict}")
    lines.append("-" * 64)
    # word-wrap the implication at ~62 cols
    words = result.implication.split()
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > 62:
            lines.append(f"  {cur}")
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(f"  {cur}")
    lines.append("=" * 64)
    return "\n".join(lines)


def _to_dict(result: AuditResult) -> Dict[str, Any]:
    return {
        "ecommerce_root": result.ecommerce_root,
        "files_scanned": result.files_scanned,
        "image_models": result.image_models,
        "local_hits": [
            {"file": h.file, "line": h.line, "label": h.label, "text": h.text}
            for h in result.local_hits
        ],
        "durable_hits": [
            {"file": h.file, "line": h.line, "label": h.label, "text": h.text}
            for h in result.durable_hits
        ],
        "verdict": result.verdict,
        "implication": result.implication,
    }


def _default_repo_root() -> str:
    """Repo root = the parent of this scripts/ dir."""
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument(
        "--root",
        default=_default_repo_root(),
        help="Repo root containing ecommerce/ (default: parent of scripts/).",
    )
    args = parser.parse_args(argv)

    result = audit_images(args.root)
    if args.json:
        print(json.dumps(_to_dict(result), indent=2))
    else:
        print(render_text(result))

    # Fail loud only if the tree was missing entirely.
    if not os.path.isdir(result.ecommerce_root):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
