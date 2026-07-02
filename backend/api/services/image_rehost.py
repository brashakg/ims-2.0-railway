"""
IMS 2.0 - SSRF-hardened external image fetch (Autopilot image RE-HOST)
======================================================================
Catalog Autopilot finds product images on brand sites. Hotlinking them means a
product photo dies the day the brand site moves the file - so on "Use this"
the FE asks the backend to COPY the bytes into our own GridFS file store
(POST /products/image/from-url). That endpoint fetches an ARBITRARY,
operator-supplied URL, which is a classic SSRF hole - this module is the
hardened fetcher it must go through.

Guards (mirrors the allowlist discipline of scripts/rehost_bvi_uploads.py,
adapted for open-web brand sites where a host allowlist is not viable):
  * http/https ONLY (no file:/ftp:/data:/gopher:).
  * The host is resolved and EVERY resolved address must be a global unicast
    IP - private (10/8, 172.16/12, 192.168/16), loopback (127/8, ::1),
    link-local + cloud metadata (169.254/16 incl. 169.254.169.254, fe80::/10),
    carrier-grade NAT (100.64/10), unspecified/reserved/multicast are ALL
    blocked. IP-literal hosts are checked directly.
  * Redirects are NOT auto-followed: each hop (max 3) is re-validated with the
    same host/IP checks before it is fetched.
  * The response Content-Type must be on the caller's image mime allowlist.
  * The body is streamed with a hard byte cap - an over-size download aborts
    mid-stream (never buffers more than the cap).
  * Short timeout (AUTOPILOT_IMAGE_FETCH_TIMEOUT, default 10s).

No emojis in this file (Windows cp1252). Fail behaviour: raises
ImageFetchError with an http-ready status/detail; callers map it 1:1.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import FrozenSet, List, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# Hard cap on redirect hops (each hop is re-validated).
MAX_REDIRECTS = 3


# Fetch timeout (seconds). Short: this runs inline in a user action.
def _fetch_timeout() -> float:
    try:
        return float(os.getenv("AUTOPILOT_IMAGE_FETCH_TIMEOUT", "10.0"))
    except (TypeError, ValueError):
        return 10.0


class ImageFetchError(Exception):
    """A fetch that must be refused/failed; carries an http-ready status."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _is_blocked_ip(ip_str: str) -> bool:
    """True when an address is NOT safe to fetch from the server side.

    `is_global` is False for every private / loopback / link-local (incl. the
    169.254.169.254 cloud metadata endpoint) / CGNAT / reserved / multicast /
    unspecified range, for both IPv4 and IPv6 - so anything non-global is
    blocked. An unparsable address is blocked too (fail closed).
    """
    try:
        ip = ipaddress.ip_address(ip_str.strip("[]"))
    except ValueError:
        return True
    return not ip.is_global


def _resolve_host(host: str) -> List[str]:
    """All addresses a hostname resolves to (A + AAAA). Raises ImageFetchError
    on resolution failure. Split out so tests can monkeypatch resolution."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ImageFetchError(400, f"Could not resolve host '{host}'") from e
    addrs: List[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in addrs:
            addrs.append(addr)
    if not addrs:
        raise ImageFetchError(400, f"Host '{host}' resolved to no addresses")
    return addrs


def assert_url_allowed(url: str) -> None:
    """SSRF gate for ONE url (called per redirect hop). Raises ImageFetchError
    when the scheme/host/any-resolved-IP is not safely fetchable."""
    parsed = urlparse(url or "")
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ImageFetchError(400, "Only http/https image URLs are allowed")
    host = parsed.hostname or ""
    if not host:
        raise ImageFetchError(400, "Image URL has no host")

    # IP-literal host: check it directly (no DNS involved).
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False

    if is_literal:
        if _is_blocked_ip(host):
            raise ImageFetchError(
                400, "Image URL resolves to a private/internal address (blocked)"
            )
        return

    # Hostname: EVERY resolved address must be global unicast (one private
    # record in a mixed answer is enough to block - fail closed).
    for addr in _resolve_host(host):
        if _is_blocked_ip(addr):
            raise ImageFetchError(
                400, "Image URL resolves to a private/internal address (blocked)"
            )


def fetch_external_image(
    url: str,
    *,
    allowed_mimes: FrozenSet[str],
    max_bytes: int,
) -> Tuple[bytes, str, str]:
    """Fetch an external image with the full SSRF guard-rail.

    Returns (content, mime, final_url). Raises ImageFetchError on ANY refusal:
      400  blocked scheme/host/private-IP, non-image content-type, over-size,
           too many redirects, empty body
      502  upstream network error / non-200 terminal response
    """
    try:
        import httpx  # noqa: PLC0415 - lazy so a missing dep fails per-call
    except Exception as e:  # noqa: BLE001  # pragma: no cover - dep present
        raise ImageFetchError(503, "httpx unavailable on the server") from e

    current = (url or "").strip()
    if not current:
        raise ImageFetchError(400, "url is required")

    timeout = _fetch_timeout()
    for _hop in range(MAX_REDIRECTS + 1):
        # Re-validate EVERY hop - a safe first URL must not be able to bounce
        # us into the internal network via a redirect.
        assert_url_allowed(current)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                with client.stream(
                    "GET",
                    current,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"
                        ),
                        "Accept": "image/*,*/*;q=0.5",
                    },
                ) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location", "").strip()
                        if not location:
                            raise ImageFetchError(
                                502, "Upstream redirect with no Location header"
                            )
                        current = urljoin(current, location)
                        continue  # next hop re-validated at loop top
                    if resp.status_code != 200:
                        raise ImageFetchError(
                            502, f"Upstream returned HTTP {resp.status_code}"
                        )
                    mime = (
                        (resp.headers.get("content-type") or "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if mime not in allowed_mimes:
                        raise ImageFetchError(
                            400,
                            f"URL did not return an image (content-type '{mime or 'unknown'}')",
                        )
                    # Cheap early refusal when the server declares its size.
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise ImageFetchError(
                            400,
                            f"Image exceeds the {max_bytes // (1024 * 1024)} MB cap",
                        )
                    # Stream with a hard cap - abort mid-download if exceeded.
                    chunks: List[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ImageFetchError(
                                400,
                                f"Image exceeds the {max_bytes // (1024 * 1024)} MB cap",
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not content:
                        raise ImageFetchError(400, "URL returned an empty body")
                    return content, mime, current
        except ImageFetchError:
            raise
        except Exception as e:  # noqa: BLE001 - network/timeout/protocol errors
            logger.warning("[IMAGE-REHOST] fetch failed for a candidate url: %s", e)
            raise ImageFetchError(502, "Could not fetch the image URL") from e

    raise ImageFetchError(400, f"Too many redirects (max {MAX_REDIRECTS})")


def filename_from_url(url: str, mime: str) -> str:
    """A sane stored filename derived from the URL path (+ mime-based ext)."""
    path = urlparse(url or "").path or ""
    base = path.rsplit("/", 1)[-1].strip()
    # Keep it simple + safe: alnum, dot, dash, underscore only.
    base = "".join(ch for ch in base if ch.isalnum() or ch in "._-")[:80]
    ext_by_mime = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    ext = ext_by_mime.get((mime or "").lower(), ".img")
    if not base:
        return "autopilot-image" + ext
    if "." not in base:
        return base + ext
    return base
