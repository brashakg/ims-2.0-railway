"""
IMS 2.0 - SSRF-hardened external image fetch (image RE-HOST)
======================================================================
Product images pasted/imported from brand sites get hotlinked otherwise - a
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
  * The connection is BOUND to the addresses that were checked (L1-F32): the
    request is sent to the validated IP itself, with the original hostname
    kept as the Host header and as the TLS server name (SNI + certificate
    check). The hostname is never resolved a second time, so a DNS answer
    that changes between the check and the connect (rebinding) cannot steer
    the fetch to a private address. Proxy environment variables are ignored
    (trust_env=False): a proxy would do its own, unchecked, resolution.
  * Redirects are NOT auto-followed: each hop (max 3) is re-validated with the
    same host/IP checks, and bound the same way, before it is fetched.
  * The response Content-Type must be on the caller's image mime allowlist
    (a caller that judges the BYTES itself passes allowed_mimes=None).
  * The body is streamed with a hard byte cap - an over-size download aborts
    mid-stream (never buffers more than the cap).
  * Short timeout (AUTOPILOT_IMAGE_FETCH_TIMEOUT, default 10s).

Every door that reads a STORED image url on the server (the design-queue
auto-edit, the catalogue PDF photo pre-fetch, the /uploads/ re-host tool) goes
through read_stored_image below: http(s) through the guarded fetch above, and
anything else only as a file inside the server's own uploads folder.

No emojis in this file (Windows cp1252). Fail behaviour: raises
ImageFetchError with an http-ready status/detail; callers map it 1:1.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from contextlib import closing
from pathlib import Path
from typing import FrozenSet, List, Optional, Tuple
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


# NAT64 well-known prefix (RFC 6052): the low 32 bits are an IPv4 address.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _is_blocked_ip(ip_str: str) -> bool:
    """True when an address is NOT safe to fetch from the server side.

    `is_global` is False for every private / loopback / link-local (incl. the
    169.254.169.254 cloud metadata endpoint) / CGNAT / reserved / unspecified
    range, for both IPv4 and IPv6 - so anything non-global is blocked.
    Multicast is blocked EXPLICITLY: `is_global` is True for 224.0.0.0/4 and
    ff00::/8, and a fetch target must be a unicast host. An IPv6 address that
    only wraps an IPv4 one (IPv4-mapped, 6to4, NAT64) is judged on the IPv4
    address it reaches. An unparsable address is blocked too (fail closed).
    """
    try:
        ip = ipaddress.ip_address(ip_str.strip("[]"))
    except ValueError:
        return True
    if ip.version == 6:
        inner = ip.ipv4_mapped or ip.sixtofour
        if inner is None and ip in _NAT64:
            inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is not None:
            ip = inner
    return ip.is_multicast or not ip.is_global


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


def assert_url_allowed(url: str) -> List[str]:
    """SSRF gate for ONE url (called per redirect hop). Raises ImageFetchError
    when the scheme/host/any-resolved-IP is not safely fetchable.

    Returns the addresses that PASSED the check - the only addresses the
    caller may connect to for this url (see _open_bound)."""
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
        return [host]

    # Hostname: EVERY resolved address must be global unicast (one private
    # record in a mixed answer is enough to block - fail closed).
    addrs = _resolve_host(host)
    for addr in addrs:
        if _is_blocked_ip(addr):
            raise ImageFetchError(
                400, "Image URL resolves to a private/internal address (blocked)"
            )
    return addrs


_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "image/*,*/*;q=0.5",
}


def _open_bound(client, url: str, addrs: List[str]):
    """Open a streamed GET for `url` on one of its VALIDATED addresses (L1-F32).

    The request is sent to the IP itself, so the HTTP client never resolves
    the hostname again - a second lookup could answer differently from the one
    that was checked (DNS rebinding). The hostname stays as the Host header
    and, for https, as the TLS server name, so SNI and the certificate check
    still run against the name, not the IP. Addresses are tried in order,
    moving on only when one cannot be connected to (what a normal hostname
    connect does with a multi-address answer)."""
    import httpx  # noqa: PLC0415 - lazy, as in fetch_external_image

    target = httpx.URL(url)
    extensions = {}
    if target.scheme == "https" and addrs != [target.host]:
        extensions["sni_hostname"] = target.raw_host.decode("ascii")
    headers = {**_FETCH_HEADERS, "Host": target.netloc.decode("ascii")}
    last_error: Exception = ImageFetchError(502, "Could not fetch the image URL")
    for addr in addrs:
        request = client.build_request(
            "GET", target.copy_with(host=addr), headers=headers, extensions=extensions
        )
        try:
            return client.send(request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            last_error = e
    raise last_error


def fetch_external_image(
    url: str,
    *,
    allowed_mimes: Optional[FrozenSet[str]],
    max_bytes: int,
) -> Tuple[bytes, str, str]:
    """Fetch an external image with the full SSRF guard-rail.

    allowed_mimes=None skips ONLY the Content-Type header check, for a caller
    that decides from the bytes (the header is just the remote server's label).

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
        # us into the internal network via a redirect. The addresses that
        # passed are the ONLY ones this hop may connect to.
        addrs = assert_url_allowed(current)
        try:
            # trust_env=False: no proxy from the environment - a proxy would
            # resolve the hostname itself, outside the check above.
            with httpx.Client(
                timeout=timeout, follow_redirects=False, trust_env=False
            ) as client:
                with closing(_open_bound(client, current, addrs)) as resp:
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
                    if allowed_mimes is not None and mime not in allowed_mimes:
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


def _read_local_upload(path: str, max_bytes: int) -> bytes:
    """Read a stored LOCAL image path (what LocalDiskStorage.put returns, or a
    legacy /uploads/ path), and nothing else on the disk: after resolving
    `..` and links the file must sit inside the uploads folder."""
    roots = {
        Path("uploads").resolve(),
        Path(os.getenv("IMAGE_LOCAL_DIR", "uploads/edited")).resolve(),
    }
    try:
        real = Path(path.lstrip("/")).resolve()
        if not any(root in real.parents for root in roots) or not real.is_file():
            raise ImageFetchError(400, "Image path is not an uploaded file (blocked)")
        if real.stat().st_size > max_bytes:
            raise ImageFetchError(
                400, f"Image exceeds the {max_bytes // (1024 * 1024)} MB cap"
            )
        return real.read_bytes()
    except (OSError, ValueError) as e:
        raise ImageFetchError(400, "Image path is not an uploaded file") from e


def read_stored_image(url: str, *, max_bytes: int) -> bytes:
    """THE way the server reads a stored image url that staff could have typed
    (L1-F32, alternate routes). http(s) -> fetch_external_image (address check,
    bound connection, re-checked redirects, size cap); anything else -> a file
    inside the uploads folder only. Blocking: call it off the event loop.

    The Content-Type header is not judged here; a caller that passes the bytes
    on (to an editor, to storage) checks what they are."""
    u = (url or "").strip()
    if not u:
        raise ImageFetchError(400, "image has no source url")
    if u.lower().startswith(("http://", "https://")):
        return fetch_external_image(u, allowed_mimes=None, max_bytes=max_bytes)[0]
    return _read_local_upload(u, max_bytes)


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
