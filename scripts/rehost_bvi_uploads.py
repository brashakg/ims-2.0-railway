#!/usr/bin/env python3
"""
IMS 2.0 -- BVI Phase 4b: image RE-HOST (durable-ify /uploads before BVI shutdown)
=================================================================================
THE SINGLE PIECE THAT GATES BVI SHUTDOWN.

The PIM migration (scripts/migrate_bvi_pim.py::map_image) copies image URL
STRINGS only. Any BVI image whose URL is a local-disk path ("/uploads/<file>")
is served off the BVI Next.js container's own disk -- so the instant the BVI
container stops, that URL 404s and the product loses its photo (and Shopify can
never pull it). This script copies those bytes to DURABLE object storage and
rewrites product_images.url to the new durable URL, BEFORE BVI dies.

WHAT IT DOES (per product_images row)
-------------------------------------
  1. CLASSIFY the stored url:
       DURABLE     = https on a known durable host (the configured S3/R2 public
                     base, or cdn.shopify.com). Survives BVI shutdown -> SKIP.
       NON-DURABLE = a relative /uploads/... path, OR an absolute URL on an
                     ephemeral BVI host (uniparallel.com / bettervision.in /
                     localhost, plus any host in BVI_FETCH_ALLOWED_HOSTS such as
                     the BVI *.up.railway.app domain). Dies with BVI -> RE-HOST.
  2. FETCH the bytes from the resolvable source (the live BVI base URL given via
     BVI_PUBLIC_BASE_URL, joined to the /uploads path; or the absolute URL when
     it is already on the allowlisted BVI host).
  3. object_storage.put() the bytes to durable storage (provider via
     IMAGE_STORAGE_PROVIDER -- use s3 for the cutover).
  4. REWRITE product_images.url to the new durable url; KEEP the old url under
     original_url for audit (only if original_url is not already set).
  5. AUDIT-LOG each rehost (action IMAGE_REHOST) -- old url host CLASS -> new url.

SSRF SAFETY (binding)
---------------------
We only ever fetch bytes from an EXPLICIT allowlist of BVI source hosts
(BVI_PUBLIC_BASE_URL + BVI_FETCH_ALLOWED_HOSTS). A row whose url resolves to a
host outside that allowlist is SKIPPED + logged (never fetched) -- no
attacker-controlled URL in the DB can make us fetch an arbitrary internal
endpoint. A relative /uploads/... path is only fetchable when BVI_PUBLIC_BASE_URL
is set (and that base is, by construction, the trusted BVI origin).

IDEMPOTENT + DRY-RUN DEFAULT
----------------------------
  * --dry-run is the DEFAULT: report only, write NOTHING (no fetch, no put, no
    DB update, no audit row). It reports how many rows WOULD be rehosted.
  * --commit performs the fetch + put + url rewrite + audit row.
  * Re-running is safe: a row already DURABLE is skipped, so a second --commit
    run is a no-op on already-rehosted rows.

PARITY EXIT-GATE (the shutdown gate)
------------------------------------
After --commit, the script counts the product_images rows that are STILL
NON-DURABLE and prints the count. It exits NON-ZERO if any remain, so the
cutover runbook can gate: "BVI shutdown ONLY when this reports 0 non-durable
URLs." In --dry-run it reports how many WOULD be rehosted (and exits 0).

Exit codes:
  0 = dry-run OK, OR commit finished with 0 non-durable rows remaining (GATE PASS)
  1 = fatal: missing env / cannot connect / driver missing (FAIL LOUD)
  2 = commit ran but NON-durable rows REMAIN (GATE NOT MET -- do NOT shut down BVI)

SAFETY / SECRETS
----------------
  * Never hardcodes/logs/prints a connection string or any secret -- status lines
    print "SET" / "NOT SET" only. Never logs image bytes.
  * Fails LOUD on connect failure (missing env / unreachable Mongo -> exit 1).
  * pymongo + httpx are lazy-imported so a missing driver yields a clear message.
  * No emojis -- Windows cp1252 safe.

The PURE logic (classify_url, plan_rewrite, count_non_durable, the exit-code
decision) has no I/O and is unit-tested without a DB or network
(see backend/tests/test_rehost_bvi_uploads.py).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("rehost_bvi_uploads")


# ===========================================================================
# Classification result codes (pure, stable -- used by report + tests)
# ===========================================================================

# A url that already lives on a durable host -- nothing to do.
DURABLE = "DURABLE"
# A relative /uploads/... path -- needs re-host, fetchable via BVI base URL.
LOCAL_UPLOADS = "LOCAL_UPLOADS"
# An absolute URL on an ephemeral BVI host -- needs re-host, fetchable directly.
EPHEMERAL_BVI = "EPHEMERAL_BVI"
# An absolute URL on a host we neither trust as durable NOR allow as a fetch
# source -- SKIPPED (SSRF guard); flagged so the operator can investigate.
UNKNOWN_HOST = "UNKNOWN_HOST"
# Blank / missing url -- nothing fetchable; flagged.
BLANK = "BLANK"

# The result codes that mean "this row will not survive BVI shutdown". Both
# UNKNOWN_HOST and BLANK are counted as non-durable for the gate (they are NOT
# proven durable), but only LOCAL_UPLOADS / EPHEMERAL_BVI are re-hostable.
NON_DURABLE_CODES = frozenset({LOCAL_UPLOADS, EPHEMERAL_BVI, UNKNOWN_HOST, BLANK})
REHOSTABLE_CODES = frozenset({LOCAL_UPLOADS, EPHEMERAL_BVI})


# ===========================================================================
# Host allowlists (explicit + configurable)
# ===========================================================================

# Durable hosts (a url here SURVIVES BVI shutdown). cdn.shopify.com is always
# durable; the configured S3/R2 public base is added at runtime from
# IMAGE_S3_PUBLIC_BASE. Owners can extend via DURABLE_IMAGE_HOSTS (comma-list).
_DEFAULT_DURABLE_HOSTS = (
    "cdn.shopify.com",
)

# Ephemeral BVI hosts (a url here DIES with BVI -- must be re-hosted, but IS a
# trusted fetch source). Owners can extend via BVI_FETCH_ALLOWED_HOSTS.
_DEFAULT_BVI_HOSTS = (
    "uniparallel.com",
    "www.uniparallel.com",
    "bettervision.in",
    "www.bettervision.in",
    "localhost",
    "127.0.0.1",
)


def _split_env_hosts(raw: Optional[str]) -> List[str]:
    """Parse a comma-separated host list from an env var into lowercase hosts.
    Tolerates full URLs ('https://x.com/y' -> 'x.com') and bare hosts."""
    if not raw:
        return []
    out: List[str] = []
    for tok in str(raw).split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if "://" in tok:
            tok = urlparse(tok).netloc or tok
        # strip any path/port-less leftovers
        tok = tok.split("/", 1)[0].strip()
        if tok:
            out.append(tok)
    return out


def _host_of(url: str) -> str:
    """Lowercase hostname of an absolute URL, '' for a relative path / blank."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def build_durable_hosts(
    *,
    s3_public_base: Optional[str] = None,
    extra: Optional[List[str]] = None,
) -> Tuple[str, ...]:
    """The set of hosts that count as DURABLE. Always includes cdn.shopify.com;
    adds the host of the configured S3/R2 public base + any operator extras."""
    hosts = list(_DEFAULT_DURABLE_HOSTS)
    base_host = _host_of((s3_public_base or "").strip())
    if base_host:
        hosts.append(base_host)
    if extra:
        hosts.extend(h.lower() for h in extra if h)
    # dedupe, keep order
    seen: set = set()
    uniq: List[str] = []
    for h in hosts:
        if h and h not in seen:
            seen.add(h)
            uniq.append(h)
    return tuple(uniq)


def build_bvi_hosts(
    *,
    public_base: Optional[str] = None,
    extra: Optional[List[str]] = None,
) -> Tuple[str, ...]:
    """The set of trusted BVI fetch-source hosts (ephemeral but allowlisted)."""
    hosts = list(_DEFAULT_BVI_HOSTS)
    base_host = _host_of((public_base or "").strip())
    if base_host:
        hosts.append(base_host)
    if extra:
        hosts.extend(h.lower() for h in extra if h)
    seen: set = set()
    uniq: List[str] = []
    for h in hosts:
        if h and h not in seen:
            seen.add(h)
            uniq.append(h)
    return tuple(uniq)


# ===========================================================================
# PURE: classify a url -- no I/O, fully unit-tested
# ===========================================================================

def classify_url(
    url: Any,
    *,
    durable_hosts: Tuple[str, ...],
    bvi_hosts: Tuple[str, ...],
) -> str:
    """Classify a stored image url into one of the result codes.

    Order of decision:
      blank/None                              -> BLANK
      relative path (no scheme)               -> LOCAL_UPLOADS  (fetch via BVI base)
      absolute http(s) on a durable host      -> DURABLE        (skip)
      absolute http(s) on an allowlisted BVI host -> EPHEMERAL_BVI (re-host)
      anything else (unknown host / scheme)   -> UNKNOWN_HOST   (SSRF-skip)

    PURE: depends only on its inputs. Host matching is exact OR a dot-suffix
    match (so 'foo.cdn.shopify.com' matches 'cdn.shopify.com') -- never a bare
    substring (so 'cdn.shopify.com.evil.test' does NOT match).
    """
    if url is None:
        return BLANK
    s = str(url).strip()
    if not s:
        return BLANK

    scheme = urlparse(s).scheme.lower()
    if scheme not in ("http", "https"):
        # No (or non-web) scheme. A leading-slash or bare relative path is a BVI
        # /uploads-style local path; data:/file:/ftp: etc. are not fetchable.
        low = s.lower()
        if low.startswith("/") or low.startswith("uploads/") or low.startswith("public/"):
            return LOCAL_UPLOADS
        if scheme in ("data", "file", "ftp", "gopher"):
            return UNKNOWN_HOST
        # A bare relative path (e.g. "img/x.png") -- treat as local uploads-ish.
        if "://" not in s and not scheme:
            return LOCAL_UPLOADS
        return UNKNOWN_HOST

    host = _host_of(s)
    if not host:
        return UNKNOWN_HOST
    if _host_matches(host, durable_hosts):
        return DURABLE
    if _host_matches(host, bvi_hosts):
        return EPHEMERAL_BVI
    return UNKNOWN_HOST


def _host_matches(host: str, allow: Tuple[str, ...]) -> bool:
    """Exact host match OR dot-suffix (subdomain) match. Never substring."""
    host = (host or "").lower()
    for a in allow:
        a = (a or "").lower()
        if not a:
            continue
        if host == a or host.endswith("." + a):
            return True
    return False


def is_non_durable(code: str) -> bool:
    """True if a classification code means the row won't survive BVI shutdown."""
    return code in NON_DURABLE_CODES


def is_rehostable(code: str) -> bool:
    """True if we have a trusted, fetchable source for this row's bytes."""
    return code in REHOSTABLE_CODES


# ===========================================================================
# PURE: resolve the fetch URL (SSRF-safe) -- no network, unit-tested
# ===========================================================================

def resolve_fetch_url(
    url: str,
    code: str,
    *,
    bvi_public_base: Optional[str],
    bvi_hosts: Tuple[str, ...],
) -> Optional[str]:
    """Turn a stored url into a fetchable http(s) URL, or None if not safely
    fetchable. SSRF-safe: the returned URL is ALWAYS on an allowlisted BVI host.

    LOCAL_UPLOADS -> join the relative path onto bvi_public_base (required; the
                     base is the trusted origin). None if no base configured.
    EPHEMERAL_BVI -> the absolute url as-is (already verified on a BVI host).
    anything else -> None (never fetched).
    """
    if code == EPHEMERAL_BVI:
        # Defensive re-check: the host must STILL be on the BVI allowlist.
        if _host_matches(_host_of(url), bvi_hosts):
            return url
        return None
    if code == LOCAL_UPLOADS:
        base = (bvi_public_base or "").strip()
        if not base:
            return None
        # Normalise: ensure base has a scheme + a trailing slash for urljoin.
        if "://" not in base:
            base = "https://" + base
        if not base.endswith("/"):
            base = base + "/"
        path = str(url).strip()
        # urljoin needs the path relative (no leading slash) to honor the base
        # path, but BVI /uploads is host-root-relative -> use leading slash so
        # it resolves against the host root of the trusted base.
        if not path.startswith("/"):
            path = "/" + path.lstrip("/")
        fetch = urljoin(base, path)
        # FINAL SSRF gate: the resolved host MUST be on the BVI allowlist.
        if _host_matches(_host_of(fetch), bvi_hosts):
            return fetch
        return None
    return None


# ===========================================================================
# PURE: plan a single rewrite -- the $set patch -- no I/O, unit-tested
# ===========================================================================

def plan_rewrite(row: Dict, new_url: str) -> Dict:
    """Build the $set patch for a rehosted row. Sets the new durable url, marks
    locally_modified (so the Phase-5 push queue re-pushes), and PRESERVES the
    pre-rehost url under original_url for audit (only when not already set, so a
    re-run never clobbers the very first original)."""
    patch: Dict[str, Any] = {
        "url": new_url,
        "locally_modified": True,
        "rehosted_at": datetime.now(tz=timezone.utc),
    }
    if not row.get("original_url"):
        patch["original_url"] = row.get("url")
    return patch


# ===========================================================================
# PURE: parity count + exit-code decision -- no I/O, unit-tested
# ===========================================================================

def count_non_durable(codes: List[str]) -> int:
    """How many classification codes are non-durable (gate denominator)."""
    return sum(1 for c in codes if is_non_durable(c))


def decide_exit_code(*, dry_run: bool, non_durable_remaining: int) -> int:
    """The shutdown-gate exit code.

      dry-run                         -> 0 (report only; never gates)
      commit, 0 non-durable remaining -> 0 (GATE PASS -- safe to shut down BVI)
      commit, >0 non-durable remaining-> 2 (GATE NOT MET -- do NOT shut down BVI)
    """
    if dry_run:
        return 0
    return 0 if non_durable_remaining == 0 else 2


# ===========================================================================
# I/O: Mongo connection (mirrors bvi_parity_check / migrate_bvi_pim)
# ===========================================================================

def _resolve_mongo_url() -> Optional[str]:
    return os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")


def _mongo_connect(mongo_url: str, db_name: str):
    """Return (client, db) or (None, None). Pings to confirm connectivity."""
    try:
        from pymongo import MongoClient  # noqa: PLC0415 -- lazy import
    except ImportError as e:
        logger.error("pymongo is not installed (%s). pip install pymongo.", e)
        return None, None
    try:
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=10_000)
        client.admin.command("ping")
        return client, client[db_name]
    except Exception as e:  # noqa: BLE001
        logger.error("Mongo connect failed: %s", e)
        return None, None


# ===========================================================================
# I/O: byte fetch (httpx) -- only ever called with an SSRF-vetted URL
# ===========================================================================

def _fetch_bytes(fetch_url: str, *, timeout: float = 30.0) -> bytes:
    """Fetch image bytes from an http(s) URL. Raises on failure (caller records
    it per-item). The caller MUST have vetted fetch_url against the BVI allowlist
    (resolve_fetch_url) -- this function does not re-validate the host."""
    import httpx  # noqa: PLC0415 -- lazy import

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.get(fetch_url)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    data = resp.content
    if not data:
        raise RuntimeError("empty body")
    return data


def _guess_content_type(url: str) -> str:
    u = (url or "").split("?", 1)[0].lower()
    if u.endswith(".png"):
        return "image/png"
    if u.endswith(".webp"):
        return "image/webp"
    if u.endswith(".gif"):
        return "image/gif"
    if u.endswith(".svg"):
        return "image/svg+xml"
    return "image/jpeg"


def _ext_from_url(url: str) -> str:
    u = (url or "").split("?", 1)[0].lower()
    for ext in (".png", ".webp", ".gif", ".svg", ".jpeg", ".jpg"):
        if u.endswith(ext):
            return ext
    return ".jpg"


def _get_object_storage():
    """Resolve the configured durable object-storage backend (reuses the backend
    seam). Adds backend/ to sys.path so the script runs from a stripped checkout
    the same way migrate_bvi_pim does. Returns the storage object or None."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _backend = os.path.join(os.path.dirname(_here), "backend")
        if _backend not in sys.path:
            sys.path.insert(0, _backend)
        from api.services.object_storage import get_object_storage  # noqa: PLC0415

        return get_object_storage()
    except Exception as e:  # noqa: BLE001
        logger.error("[STORAGE] could not resolve object storage: %s", e)
        return None


def _storage_is_durable(storage) -> bool:
    """A storage backend is durable enough for the cutover only when it is the
    s3 provider AND actually configured (bucket + creds + boto3). Local-disk on
    the ephemeral Railway container is NOT durable -- Shopify still can't reach
    it."""
    if storage is None:
        return False
    try:
        return getattr(storage, "name", "") == "s3" and bool(storage.available())
    except Exception:  # noqa: BLE001
        return False


def _audit_rehost(
    db,
    *,
    image_id: str,
    product_id: str,
    old_code: str,
    new_url_host: str,
) -> None:
    """Append an IMAGE_REHOST audit row. Logs the old url's host CLASS and the
    new url's host -- NEVER the bytes, never a secret. Fail-soft."""
    if db is None:
        return
    try:
        coll = db["audit_logs"]
        coll.insert_one(
            {
                "log_id": f"AUD-{uuid.uuid4().hex[:12]}",
                "action": "IMAGE_REHOST",
                "entity_type": "product_image",
                "entity_id": image_id,
                "user_id": "system:rehost_bvi_uploads",
                "actor": "system:rehost_bvi_uploads",
                "source": "BVI_REHOST",
                "before_state": {"url_class": old_code, "product_id": product_id},
                "after_state": {"durable_host": new_url_host},
                "severity": "INFO",
                "timestamp": datetime.now(tz=timezone.utc),
            }
        )
    except Exception:  # noqa: BLE001 -- audit is best-effort, never blocks rehost
        logger.debug("[AUDIT] rehost audit row skipped", exc_info=True)


# ===========================================================================
# Runner
# ===========================================================================

def run_rehost(
    db,
    *,
    dry_run: bool,
    durable_hosts: Tuple[str, ...],
    bvi_hosts: Tuple[str, ...],
    bvi_public_base: Optional[str],
    storage,
    storage_durable: bool,
    limit: int = 0,
    sample_n: int = 5,
) -> Dict[str, Any]:
    """Scan product_images, classify + (when --commit) re-host non-durable rows.

    Returns a structured result dict (counts + the remaining-non-durable parity
    number). Fully fail-soft per-row: one bad image never aborts the batch.
    """
    result: Dict[str, Any] = {
        "dry_run": dry_run,
        "total": 0,
        "by_class": {},
        "candidates": 0,        # rehostable non-durable rows
        "rehosted": 0,
        "failed": 0,
        "skipped_unknown": 0,   # UNKNOWN_HOST -- SSRF-skipped, not fetched
        "blank": 0,
        "non_durable_remaining": 0,
        "samples": [],
    }

    coll = db["product_images"]
    projection = {"_id": 0, "image_id": 1, "product_id": 1, "url": 1, "original_url": 1}
    cursor = coll.find({}, projection)
    if limit and limit > 0:
        cursor = cursor.limit(limit)
    rows = list(cursor)
    result["total"] = len(rows)

    by_class: Dict[str, int] = {}
    # Track the post-run classification per row so the parity count reflects
    # reality: a successfully rehosted row becomes DURABLE; a skip/fail stays
    # non-durable.
    final_codes: List[str] = []
    samples: List[Dict[str, str]] = []

    for row in rows:
        url = row.get("url")
        code = classify_url(url, durable_hosts=durable_hosts, bvi_hosts=bvi_hosts)
        by_class[code] = by_class.get(code, 0) + 1

        if code == DURABLE:
            final_codes.append(DURABLE)
            continue

        if code == BLANK:
            result["blank"] += 1
            final_codes.append(BLANK)
            continue

        if code == UNKNOWN_HOST:
            # SSRF guard: a url on a host we don't trust is NEVER fetched.
            result["skipped_unknown"] += 1
            final_codes.append(UNKNOWN_HOST)
            host = _host_of(str(url))
            logger.warning(
                "[SKIP] image %s url on unknown host '%s' -- not fetched (SSRF guard)",
                row.get("image_id"), host or "(relative/none)",
            )
            final = UNKNOWN_HOST
            if len(samples) < sample_n:
                samples.append({"image_id": str(row.get("image_id")), "class": final})
            continue

        # code in REHOSTABLE_CODES (LOCAL_UPLOADS / EPHEMERAL_BVI)
        result["candidates"] += 1
        if len(samples) < sample_n:
            samples.append({"image_id": str(row.get("image_id")), "class": code})

        if dry_run:
            # Would rehost -> stays non-durable for now (report only).
            final_codes.append(code)
            continue

        # --- LIVE re-host ---
        if not storage_durable:
            # Cannot rehost without a durable backend -> stays non-durable.
            result["failed"] += 1
            final_codes.append(code)
            continue

        fetch_url = resolve_fetch_url(
            str(url), code, bvi_public_base=bvi_public_base, bvi_hosts=bvi_hosts
        )
        if not fetch_url:
            logger.warning(
                "[FAIL] image %s class=%s -- no SSRF-safe fetch url "
                "(set BVI_PUBLIC_BASE_URL?)",
                row.get("image_id"), code,
            )
            result["failed"] += 1
            final_codes.append(code)
            continue

        try:
            data = _fetch_bytes(fetch_url)
            image_id = str(row.get("image_id") or uuid.uuid4().hex)
            product_id = str(row.get("product_id") or "misc")
            key = f"bvi-rehost/{product_id}/{image_id}{_ext_from_url(str(url))}"
            new_url = storage.put(
                key, data, _guess_content_type(str(url))
            )
            patch = plan_rewrite(row, new_url)
            coll.update_one({"image_id": image_id}, {"$set": patch})
            _audit_rehost(
                db,
                image_id=image_id,
                product_id=product_id,
                old_code=code,
                new_url_host=_host_of(new_url) or "(relative)",
            )
            result["rehosted"] += 1
            # Re-classify the NEW url to confirm it is now durable for the gate.
            new_code = classify_url(
                new_url, durable_hosts=durable_hosts, bvi_hosts=bvi_hosts
            )
            final_codes.append(new_code)
            logger.info(
                "[REHOST] image %s  %s -> %s",
                image_id, code, _host_of(new_url) or "(relative)",
            )
        except Exception as e:  # noqa: BLE001 -- one bad image never aborts batch
            logger.warning("[FAIL] image %s rehost failed: %s", row.get("image_id"), e)
            result["failed"] += 1
            final_codes.append(code)

    result["by_class"] = by_class
    result["non_durable_remaining"] = count_non_durable(final_codes)
    result["samples"] = samples
    return result


# ===========================================================================
# CLI
# ===========================================================================

def _print_report(result: Dict[str, Any], *, dry_run: bool) -> None:
    logger.info("")
    logger.info("=" * 60)
    logger.info("BVI IMAGE RE-HOST  --  mode: %s", "DRY-RUN" if dry_run else "COMMIT")
    logger.info("=" * 60)
    logger.info("product_images scanned : %d", result["total"])
    logger.info("classification:")
    for code in (DURABLE, LOCAL_UPLOADS, EPHEMERAL_BVI, UNKNOWN_HOST, BLANK):
        logger.info("  %-14s : %d", code, result["by_class"].get(code, 0))
    logger.info("-" * 60)
    if dry_run:
        logger.info("WOULD re-host (candidates) : %d", result["candidates"])
        logger.info(
            "non-durable rows           : %d (would remain until --commit)",
            result["non_durable_remaining"],
        )
    else:
        logger.info("re-hosted                  : %d", result["rehosted"])
        logger.info("failed                     : %d", result["failed"])
        logger.info("skipped (unknown host)     : %d", result["skipped_unknown"])
        logger.info("blank url rows             : %d", result["blank"])
        logger.info("-" * 60)
        logger.info(
            "NON-DURABLE REMAINING      : %d  <-- SHUTDOWN GATE",
            result["non_durable_remaining"],
        )
    logger.info("=" * 60)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report only; write NOTHING (no fetch, no put, no DB update). DEFAULT.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actually fetch + re-host + rewrite url (disables --dry-run). "
        "Idempotent; safe to re-run.",
    )
    parser.add_argument(
        "--mongo-url",
        default=_resolve_mongo_url(),
        help="IMS Mongo URL (default: $MONGODB_URL / $MONGO_URL).",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("MONGO_DATABASE", "ims_2_0"),
        help="Mongo database name (default: $MONGO_DATABASE or ims_2_0).",
    )
    parser.add_argument(
        "--bvi-base-url",
        default=os.getenv("BVI_PUBLIC_BASE_URL"),
        help="Live BVI base URL to fetch /uploads from "
        "(default: $BVI_PUBLIC_BASE_URL). REQUIRED to re-host relative /uploads paths.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max product_images rows to scan (0 = all). For phased runs.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=5,
        help="How many sample candidate rows to record in the report (default: 5).",
    )
    args = parser.parse_args(argv)

    # --commit wins over the always-on --dry-run default.
    dry_run = not args.commit

    mongo_url = args.mongo_url
    s3_public_base = os.getenv("IMAGE_S3_PUBLIC_BASE")
    durable_hosts = build_durable_hosts(
        s3_public_base=s3_public_base,
        extra=_split_env_hosts(os.getenv("DURABLE_IMAGE_HOSTS")),
    )
    bvi_hosts = build_bvi_hosts(
        public_base=args.bvi_base_url,
        extra=_split_env_hosts(os.getenv("BVI_FETCH_ALLOWED_HOSTS")),
    )

    mode = "DRY-RUN (no writes)" if dry_run else "COMMIT (live re-host)"
    logger.info("=" * 60)
    logger.info("BVI Phase 4b image RE-HOST  --  mode: %s", mode)
    logger.info("mongo_url            : %s", "SET" if mongo_url else "NOT SET")
    logger.info("db                   : %s", args.db)
    logger.info("BVI_PUBLIC_BASE_URL  : %s", "SET" if args.bvi_base_url else "NOT SET")
    logger.info("IMAGE_S3_PUBLIC_BASE : %s", "SET" if s3_public_base else "NOT SET")
    logger.info("durable hosts        : %s", ", ".join(durable_hosts))
    logger.info("BVI fetch hosts      : %s", ", ".join(bvi_hosts))
    logger.info("=" * 60)

    if not mongo_url:
        logger.error(
            "FAIL LOUD: no IMS Mongo URL. Set MONGODB_URL or MONGO_URL "
            "(inject via `railway run`)."
        )
        return 1

    # Resolve durable storage (only strictly needed for --commit, but we report
    # its state in dry-run too so the operator can confirm before arming).
    storage = _get_object_storage()
    storage_durable = _storage_is_durable(storage)
    logger.info(
        "object storage       : %s (durable=%s)",
        getattr(storage, "name", "unresolved"), storage_durable,
    )
    if not dry_run and not storage_durable:
        logger.error(
            "FAIL LOUD: --commit needs a DURABLE object store. Set "
            "IMAGE_STORAGE_PROVIDER=s3 + IMAGE_S3_BUCKET / IMAGE_S3_ACCESS_KEY / "
            "IMAGE_S3_SECRET_KEY / IMAGE_S3_PUBLIC_BASE (and install boto3). "
            "Local-disk is NOT durable on the ephemeral container."
        )
        return 1
    if not dry_run and not args.bvi_base_url:
        # Not strictly fatal (some rows may be absolute EPHEMERAL_BVI urls), but
        # relative /uploads rows cannot be fetched without it -- warn loudly.
        logger.warning(
            "BVI_PUBLIC_BASE_URL is NOT set -- relative /uploads/ rows cannot be "
            "fetched and will REMAIN non-durable (gate will not pass)."
        )

    client, db = _mongo_connect(mongo_url, args.db)
    if db is None:
        logger.error("FAIL LOUD: cannot connect to IMS Mongo.")
        return 1
    logger.info("Mongo connected.")

    try:
        result = run_rehost(
            db,
            dry_run=dry_run,
            durable_hosts=durable_hosts,
            bvi_hosts=bvi_hosts,
            bvi_public_base=args.bvi_base_url,
            storage=storage,
            storage_durable=storage_durable,
            limit=args.limit,
            sample_n=args.sample,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    _print_report(result, dry_run=dry_run)

    exit_code = decide_exit_code(
        dry_run=dry_run, non_durable_remaining=result["non_durable_remaining"]
    )
    if dry_run:
        logger.info(
            "REMINDER: DRY-RUN. Re-run with --commit (durable storage armed) to "
            "re-host. Shutdown gate is enforced on the --commit run."
        )
    elif exit_code == 0:
        logger.info(
            "GATE PASS: 0 non-durable product_images remain. BVI is safe to shut down."
        )
    else:
        logger.error(
            "GATE NOT MET: %d non-durable product_images remain. "
            "Do NOT shut down BVI -- investigate + re-run.",
            result["non_durable_remaining"],
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
