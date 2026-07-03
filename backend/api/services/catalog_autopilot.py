"""
IMS 2.0 - Catalog Autopilot (Phase 1b)
======================================
Given a BRAND + MODEL (+ optional color/size), find candidate product matches
from prioritised sources, score them, and let a human approve before anything
is published. This module holds the PURE, testable brain (model normalization,
confidence scoring, copyright gating) PLUS a real pluggable source-adapter
framework with live (credential/network-gated) adapters.

Source priority (highest first), per the operator's requirement:
  1. The brand's own regional/country site (e.g. Ray-Ban India) - AUTHORIZED
  2. Supplier/dealer portals we have accounts on (myluxottica) - AUTHORIZED
  3. Our own online catalog (BVI) for dedup/enrich               - AUTHORIZED
  4. Marketplaces / competitors (Amazon, Flipkart, Google)       - UNVERIFIED

Phase 1 shipped the pipeline skeleton + a WORKING internal source (our own BVI
e-commerce catalog) and fail-soft scaffolds for the credentialed web sources.
Phase 1b turns those scaffolds into a real SourceAdapter framework: every
source is an adapter with a uniform interface (name/label/source_class/
priority/is_enabled/search). The web adapters fetch with httpx and parse with a
STDLIB-ONLY scrape helper (no bs4/lxml). run_search builds an ordered registry,
runs only enabled adapters in priority order, dedupes across sources, scores,
and sorts. Live sources stay creds/network-gated and FULLY fail-soft: any
network/parse/auth/missing-dep/missing-creds condition yields [] (never raises).

Copyright stance: images from AUTHORIZED sources may be used; images from
UNVERIFIED sources are SPECS-ONLY unless a reviewer explicitly confirms rights.
Editing/cropping does NOT clear copyright, so we never auto-use unverified art.
"""

from __future__ import annotations

import asyncio
import html
import inspect
import json
import logging
import os
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

# httpx is an installed dependency, but guard the import so a missing httpx
# simply disables the web adapters (they report is_enabled()=False) instead of
# breaking module import.
try:
    import httpx  # type: ignore
except Exception:  # noqa: BLE001  # pragma: no cover - import guard
    httpx = None  # type: ignore

logger = logging.getLogger(__name__)

AUTHORIZED = "AUTHORIZED"
UNVERIFIED = "UNVERIFIED"

# Confidence weights. Visual similarity (vision model) lands in Phase 2; in
# Phase 1 we score on text signals and renormalize over the factors we can
# actually assess, so a brand+model-only query still scores cleanly.
WEIGHTS = {"model": 0.45, "color": 0.25, "size": 0.15, "brand": 0.15}


def normalize_model(value: Any) -> str:
    """Canonical model key: uppercase alphanumerics only.
    'B 4291', 'b-4291' and 'B4291' all collapse to 'B4291'."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def score_candidate(query: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    """Weighted confidence in [0,1] + a per-field matched map. A query field
    that wasn't provided is treated as neutral (doesn't penalise)."""
    matched: Dict[str, bool] = {}
    parts: List[tuple] = []

    qm = normalize_model(query.get("model"))
    cm = normalize_model(cand.get("model"))
    if qm:
        if cm and (qm == cm):
            s = 1.0
        elif cm and (qm in cm or cm in qm):
            s = 0.6
        else:
            s = 0.0
        matched["model"] = s >= 0.6
        parts.append((WEIGHTS["model"], s))

    qc = _norm(query.get("color"))
    if qc:
        cc = _norm(cand.get("color")) + " " + _norm(cand.get("color_name"))
        s = 1.0 if qc and qc in cc else 0.0
        matched["color"] = s == 1.0
        parts.append((WEIGHTS["color"], s))

    qs = normalize_model(query.get("size"))
    if qs:
        cs = normalize_model(cand.get("size"))
        s = 1.0 if cs and (qs == cs or qs in cs) else 0.0
        matched["size"] = s == 1.0
        parts.append((WEIGHTS["size"], s))

    qb = _norm(query.get("brand"))
    if qb:
        cb = _norm(cand.get("brand"))
        s = 1.0 if cb and (qb == cb or qb in cb or cb in qb) else 0.0
        matched["brand"] = s == 1.0
        parts.append((WEIGHTS["brand"], s))

    total_w = sum(w for w, _ in parts)
    score = round(sum(w * s for w, s in parts) / total_w, 4) if total_w else 0.0
    return {"score": score, "matched": matched}


def image_use_allowed(source_class: str, rights_confirmed: bool = False) -> bool:
    """AUTHORIZED sources -> images usable. UNVERIFIED -> only if a reviewer
    explicitly confirmed we have the right to use the image."""
    if source_class == AUTHORIZED:
        return True
    return bool(rights_confirmed)


# ---------------------------------------------------------------------------
# Stdlib page-scrape helper (NO third-party deps - html.parser only).
# Best-effort extraction of {title, description, specs, usp, image_urls} from
# raw HTML. Fully fail-soft: malformed HTML returns whatever it could glean.
# ---------------------------------------------------------------------------

# A reasonable desktop UA so brand sites don't reject us as an obvious bot.
_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HTTP_TIMEOUT = float(os.getenv("AUTOPILOT_HTTP_TIMEOUT", "12.0"))
# Cap how much HTML we parse so a giant page can't blow up memory/CPU.
_MAX_HTML_BYTES = 2_000_000


def _network_disabled() -> bool:
    """Hard kill-switch for the network-backed adapters. Lets tests/CI (and an
    operator) keep brand-site/marketplace scraping from EVER hitting the wire,
    regardless of other config. Truthy values: 1/true/yes/on."""
    return os.getenv("AUTOPILOT_DISABLE_NETWORK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class _ProductHTMLParser(HTMLParser):
    """Collect signals a product page exposes: <title>, meta description/og,
    spec tables (<th>/<td> or <dt>/<dd> pairs), and image URLs. Pure stdlib."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str = ""
        self.description: str = ""
        self.og_title: str = ""
        self.og_description: str = ""
        self.image_urls: List[str] = []
        self.specs: Dict[str, str] = {}
        # Anchor links (href + visible text) so a SEARCH-RESULTS page can be
        # mined for the top product-page links (Autopilot v2 multi-candidate).
        self.links: List[Dict[str, str]] = []
        self._in_title = False
        # Pending spec key/value cells while we walk a definition/table row.
        self._cell_tag: Optional[str] = None
        self._cell_text: List[str] = []
        self._pending_key: str = ""
        # Pending anchor while we walk <a>...</a>.
        self._link_href: Optional[str] = None
        self._link_text: List[str] = []

    # -- helpers -----------------------------------------------------------
    def _attrs(self, attrs: List[tuple]) -> Dict[str, str]:
        return {k.lower(): (v or "") for k, v in attrs}

    def _add_image(self, url: str) -> None:
        url = (url or "").strip()
        if not url or url.startswith("data:"):
            return
        low = url.lower()
        if not (
            low.startswith("http://")
            or low.startswith("https://")
            or low.startswith("//")
        ):
            return
        if url not in self.image_urls and len(self.image_urls) < 12:
            self.image_urls.append(url)

    # -- HTMLParser hooks --------------------------------------------------
    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            content = a.get("content") or ""
            if name == "description" and content:
                self.description = self.description or content.strip()
            elif name in ("og:title",) and content:
                self.og_title = self.og_title or content.strip()
            elif name in ("og:description",) and content:
                self.og_description = self.og_description or content.strip()
            elif name in ("og:image", "twitter:image") and content:
                self._add_image(content)
        elif tag == "img":
            self._add_image(
                a.get("src") or a.get("data-src") or a.get("data-original") or ""
            )
        elif tag == "a":
            href = (a.get("href") or "").strip()
            if href and len(self.links) < 300:
                self._link_href = href
                self._link_text = []
        elif tag in ("th", "td", "dt", "dd"):
            self._cell_tag = tag
            self._cell_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "a":
            if self._link_href is not None:
                text = re.sub(r"\s+", " ", "".join(self._link_text)).strip()
                self.links.append({"href": self._link_href, "text": text[:120]})
                self._link_href = None
                self._link_text = []
        elif tag in ("th", "td", "dt", "dd") and tag == self._cell_tag:
            text = re.sub(r"\s+", " ", "".join(self._cell_text)).strip()
            # th/dt = label cell; td/dd = value cell paired with the last label.
            if tag in ("th", "dt"):
                self._pending_key = text[:60]
            elif tag in ("td", "dd"):
                if self._pending_key and text and len(self.specs) < 40:
                    self.specs[self._pending_key] = text[:200]
                self._pending_key = ""
            self._cell_tag = None
            self._cell_text = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._cell_tag is not None:
            self._cell_text.append(data)
        if self._link_href is not None:
            self._link_text.append(data)


def scrape_product_page(raw_html: str) -> Dict[str, Any]:
    """Best-effort {title, description, specs{}, usp, image_urls} from raw HTML.
    Stdlib only, fully fail-soft (any parse error -> empty-ish dict)."""
    out: Dict[str, Any] = {
        "title": "",
        "description": "",
        "specs": {},
        "usp": "",
        "image_urls": [],
    }
    if not raw_html:
        return out
    try:
        text = raw_html[:_MAX_HTML_BYTES]
        parser = _ProductHTMLParser()
        parser.feed(text)
        try:
            parser.close()
        except Exception:  # noqa: BLE001
            pass
        title = (parser.title or parser.og_title or "").strip()
        title = re.sub(r"\s+", " ", html.unescape(title))
        description = (parser.description or parser.og_description or "").strip()
        description = re.sub(r"\s+", " ", html.unescape(description))
        specs = {
            html.unescape(k).strip(): html.unescape(v).strip()
            for k, v in parser.specs.items()
            if k and v
        }
        # USP = the first crisp sentence of the description, if any.
        usp = ""
        if description:
            usp = re.split(r"(?<=[.!?])\s+", description)[0].strip()[:160]
        out.update(
            {
                "title": title,
                "description": description,
                "specs": specs,
                "usp": usp,
                "image_urls": list(parser.image_urls),
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUTOPILOT] scrape_product_page failed: %s", e)
    return out


# Obvious non-product link filters for extract_product_links.
_LINK_ASSET_EXTS = (
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    ".ico",
    ".webp",
    ".pdf",
)
_LINK_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "#")


def extract_product_links(
    raw_html: str, base_url: str, model: str, limit: int = 5
) -> List[str]:
    """Best-effort product-page URLs mined from a SEARCH-RESULTS page.

    Autopilot v2: a brand-site search should yield SEVERAL candidates (one per
    product link), not just the search page's own metadata. Rules:
      - same host as the search page (never wander off-site),
      - skip assets / javascript: / mailto: / other search pages,
      - rank links whose href/anchor-text contain the normalized model FIRST,
        then product-ish paths (/p/, /product, *.html),
      - dedupe, cap at `limit`.
    Fully fail-soft: any parse problem -> [] (the caller falls back to scraping
    the search page itself as a single candidate).
    """
    if not raw_html or not base_url:
        return []
    try:
        parser = _ProductHTMLParser()
        parser.feed(raw_html[:_MAX_HTML_BYTES])
        try:
            parser.close()
        except Exception:  # noqa: BLE001
            pass
        base_host = urlparse(base_url).netloc.lower()
        base_clean = base_url.split("#", 1)[0]
        nmodel = normalize_model(model)
        primary: List[str] = []
        secondary: List[str] = []
        seen: set = set()
        for link in parser.links:
            href = (link.get("href") or "").strip()
            if not href or href.lower().startswith(_LINK_SKIP_SCHEMES):
                continue
            absu = urljoin(base_url, href)
            parsed = urlparse(absu)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc.lower() != base_host:
                continue
            path_low = (parsed.path or "").lower()
            if path_low.endswith(_LINK_ASSET_EXTS) or "/search" in path_low:
                continue
            clean = absu.split("#", 1)[0]
            if clean in seen or clean == base_clean:
                continue
            seen.add(clean)
            hay = normalize_model(href + " " + (link.get("text") or ""))
            if nmodel and nmodel in hay:
                primary.append(clean)
            elif any(
                seg in path_low for seg in ("/p/", "/product", "/products/")
            ) or path_low.endswith(".html"):
                secondary.append(clean)
        return (primary + secondary)[: max(0, int(limit))]
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUTOPILOT] extract_product_links failed: %s", e)
        return []


def _http_get(
    url: str, *, params: Optional[Dict[str, Any]] = None, client: Optional[Any] = None
) -> Optional[str]:
    """GET a URL and return the response text, or None on ANY failure
    (missing httpx, network error, non-200). Fully fail-soft."""
    if httpx is None or not url or _network_disabled():
        return None
    headers = {"User-Agent": _HTTP_USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}
    try:
        if client is not None:
            resp = client.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        else:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as c:
                resp = c.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            logger.warning("[AUTOPILOT] GET %s -> HTTP %s", url, resp.status_code)
            return None
        return resp.text
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUTOPILOT] GET %s failed: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Source providers (fail-soft). Each returns a list of candidate dicts.
# ---------------------------------------------------------------------------


def _ecommerce_configured() -> bool:
    return bool(os.getenv("ECOMMERCE_DATABASE_URL"))


def search_internal_catalog(
    brand: str, model: str, limit: int = 25
) -> List[Dict[str, Any]]:
    """WORKING source: our own BVI e-commerce catalog. Answers 'do we already
    list this model online?' (dedup + enrichment). Read-only, fail-soft."""
    if not _ecommerce_configured():
        return []
    try:
        from .online_catalog import _connect  # lazy; shares the bridge conn
    except Exception:  # noqa: BLE001
        return []
    conn = _connect()
    if conn is None:
        return []
    nmodel = normalize_model(model)
    if not nmodel:
        return []
    out: List[Dict[str, Any]] = []
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.brand, p."modelNo", p."fullModelNo", p."productName",
                       p.status, p."shopifyProductId", p.category, p.shape, p.gender
                FROM "Product" p
                WHERE (%s = '' OR upper(p.brand) LIKE upper(%s))
                  AND upper(regexp_replace(
                        coalesce(p."fullModelNo", p."modelNo", ''),
                        '[^[:alnum:]]', '', 'g')) LIKE %s
                LIMIT %s
                """,
                (
                    brand or "",
                    f"%{brand}%" if brand else "%",
                    f"%{nmodel}%",
                    int(limit),
                ),
            )
            for r in cur.fetchall():
                out.append(
                    {
                        "source": "internal_bvi",
                        "source_class": AUTHORIZED,
                        "url": None,
                        "source_url": None,
                        "references": [],
                        "title": r[4] or f"{r[1]} {r[2] or ''}".strip(),
                        "brand": r[1],
                        "model": r[3] or r[2],
                        "color": None,
                        "size": None,
                        "image_urls": [],
                        "specs": {"category": r[7], "shape": r[8], "gender": r[9]},
                        "existing_status": r[5],
                        "existing_shopify_product_id": r[6],
                    }
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUTOPILOT] internal catalog search failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return out


# Brand -> regional site (India first). Extend freely; the BrandSiteAdapter
# fetches/searches these at run time (network attempted, fail-soft).
DEFAULT_BRAND_SITES: Dict[str, str] = {
    "RAY-BAN": "https://www.ray-ban.com/india",
    "RAYBAN": "https://www.ray-ban.com/india",
    "OAKLEY": "https://in.oakley.com",
    "VOGUE": "https://www.vogue-eyewear.com/in",
    "PERSOL": "https://www.persol.com/india",
}


def _url_q(value: str) -> str:
    """URL-encode a query value (stdlib)."""
    from urllib.parse import quote_plus

    return quote_plus(value or "")


def _candidate_skeleton(source: str, source_class: str) -> Dict[str, Any]:
    """A blank candidate matching search_internal_catalog's shape so every
    adapter returns the SAME keys (with optional description/usp extras)."""
    return {
        "source": source,
        "source_class": source_class,
        "url": None,
        # The EXACT page this candidate's data was scraped from (None for
        # DB-backed / AI-generated candidates). `references` lists every
        # {source, url} pair that contributed, so the operator can audit where
        # catalog data came from (Autopilot v2).
        "source_url": None,
        "references": [],
        "title": None,
        "brand": None,
        "model": None,
        "color": None,
        "size": None,
        "image_urls": [],
        "specs": {},
        "description": None,
        "usp": None,
    }


# Canonical category -> the word appended to source search queries so a
# category-aware job refines the query ("RB4105 sunglasses"). Unknown/blank
# categories fall back to the lower-cased words of the key (fail-soft).
CATEGORY_QUERY_WORDS: Dict[str, str] = {
    "SUNGLASS": "sunglasses",
    "FRAME": "eyeglasses",
    "OPTICAL_LENS": "optical lens",
    "CONTACT_LENS": "contact lenses",
    "COLORED_CONTACT_LENS": "colored contact lenses",
    "READING_GLASSES": "reading glasses",
    "WATCH": "watch",
    "SMARTWATCH": "smartwatch",
    "SMARTGLASSES": "smart glasses",
    "WALL_CLOCK": "wall clock",
    "ACCESSORIES": "accessories",
    "HEARING_AID": "hearing aid",
}


def _category_query_word(category: Any) -> str:
    """Human search word for a canonical category ('' when blank)."""
    key = _norm(category).replace(" ", "_")
    if not key:
        return ""
    return CATEGORY_QUERY_WORDS.get(key, key.replace("_", " ").lower())


# ---------------------------------------------------------------------------
# Source-adapter framework. Each adapter is uniform and FULLY fail-soft:
# is_enabled() reads env/creds (no network), search() may attempt the network
# but ANY failure -> [] (an adapter must never raise out).
# ---------------------------------------------------------------------------


class SourceAdapter:
    """Base class / protocol for a catalog source. Subclasses override
    is_enabled() and _search(); the public search() wraps _search() in a
    fail-soft guard so a buggy adapter can never break the pipeline."""

    name: str = "base"
    label: str = "Source"
    source_class: str = AUTHORIZED
    priority: int = 100

    def is_enabled(self) -> bool:
        """True when this adapter has what it needs to run (env/creds). Reads
        config only; never touches the network. Must not raise."""
        return False

    def reason(self) -> str:
        """Human-readable explanation surfaced in GET /sources."""
        return ""

    def _search(
        self,
        brand: str,
        model: str,
        color: str,
        size: str,
        limit: int,
        category: str = "",
    ) -> List[Dict[str, Any]]:
        """Real work; may hit the network. Subclasses implement this. The v2
        `category` kwarg is optional — search() only passes it to overrides
        that declare it, so legacy 5-arg adapters keep working unchanged."""
        raise NotImplementedError

    def search(
        self,
        brand: str,
        model: str,
        color: str = "",
        size: str = "",
        limit: int = 25,
        category: str = "",
    ) -> List[Dict[str, Any]]:
        """Fail-soft wrapper around _search(). ANY exception -> [] + a warning.
        Guarantees every returned candidate carries this adapter's source +
        source_class so downstream dedupe/scoring/copyright logic is correct.

        `category` (v2) is passed through ONLY to adapters whose _search
        declares it — older/third-party adapters with the 5-arg signature keep
        working unchanged (inspected, not duck-typed, so an adapter's own
        TypeError is never mistaken for a signature mismatch)."""
        try:
            try:
                accepts_category = (
                    "category" in inspect.signature(self._search).parameters
                )
            except (TypeError, ValueError):
                accepts_category = False
            if accepts_category:
                out = (
                    self._search(brand, model, color, size, limit, category=category)
                    or []
                )
            else:
                out = self._search(brand, model, color, size, limit) or []
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[AUTOPILOT] adapter %s raised (suppressed): %s", self.name, e
            )
            return []
        normalized: List[Dict[str, Any]] = []
        for cand in out:
            if not isinstance(cand, dict):
                continue
            cand.setdefault("source", self.name)
            cand.setdefault("source_class", self.source_class)
            cand["source"] = cand.get("source") or self.name
            cand["source_class"] = cand.get("source_class") or self.source_class
            # v2 reference audit fields — always present so the FE can render
            # a reference chip (None/[] for DB-backed or AI-generated rows).
            cand.setdefault("source_url", None)
            if not isinstance(cand.get("references"), list):
                cand["references"] = []
            normalized.append(cand)
        return normalized

    def status(self) -> Dict[str, Any]:
        """Registry-derived descriptor for GET /sources."""
        enabled = False
        try:
            enabled = bool(self.is_enabled())
        except Exception as e:  # noqa: BLE001
            logger.warning("[AUTOPILOT] adapter %s is_enabled raised: %s", self.name, e)
        return {
            "name": self.name,
            "label": self.label,
            "source_class": self.source_class,
            "priority": self.priority,
            "enabled": enabled,
            "reason": self.reason(),
        }


# --- Priority 1: the brand's own regional site (AUTHORIZED) -----------------

# Per-brand search paths the operator can tune later without code changes.
# The path is appended to the brand's base site from DEFAULT_BRAND_SITES
# (which already carries the region segment, e.g. ".../india"), so these are
# region-relative; {q} is the URL-encoded model query.
BRAND_SITE_SEARCH_PATHS: Dict[str, str] = {
    "RAY-BAN": "/search?q={q}",
    "RAYBAN": "/search?q={q}",
    "OAKLEY": "/search?q={q}",
    "VOGUE": "/search?q={q}",
    "PERSOL": "/search?q={q}",
}
# Fallback search path used when a brand has no explicit entry above.
BRAND_SITE_DEFAULT_SEARCH_PATH = "/search?q={q}"


class BrandSiteAdapter(SourceAdapter):
    """Resolve the brand's India/regional site via DEFAULT_BRAND_SITES, fetch
    the model's search/product page with httpx, and scrape title/specs/
    description/usp/image_urls. AUTHORIZED, so its images are usable."""

    name = "brand_site"
    label = "Brand regional site (India-first)"
    source_class = AUTHORIZED
    priority = 1

    def _site_for(self, brand: str) -> Optional[str]:
        return DEFAULT_BRAND_SITES.get(_norm(brand).replace(" ", ""))  # RAY-BAN/RAYBAN

    def is_enabled(self) -> bool:
        # Enabled when we know a site for this... but is_enabled() is called
        # without a brand. Treat "enabled" as "we have httpx + at least one
        # configured brand site"; per-brand resolution happens in _search.
        return (
            httpx is not None and bool(DEFAULT_BRAND_SITES) and not _network_disabled()
        )

    def reason(self) -> str:
        if httpx is None:
            return "httpx unavailable; brand-site scraping disabled."
        if _network_disabled():
            return "AUTOPILOT_DISABLE_NETWORK set; brand-site scraping off."
        return "Fetches the brand's India site at search time (fail-soft)."

    def _build_candidate(
        self, scraped: Dict[str, Any], page_url: str, brand, model, color, size
    ) -> Dict[str, Any]:
        cand = _candidate_skeleton(self.name, self.source_class)
        cand.update(
            {
                "url": page_url,
                "source_url": page_url,
                "references": [{"source": self.name, "url": page_url}],
                "title": scraped.get("title") or f"{brand} {model}".strip(),
                "brand": brand,
                "model": model,
                "color": color or None,
                "size": size or None,
                "image_urls": scraped.get("image_urls") or [],
                "specs": scraped.get("specs") or {},
                "description": scraped.get("description") or None,
                "usp": scraped.get("usp") or None,
            }
        )
        return cand

    def _search(self, brand, model, color, size, limit, category=""):
        base = self._site_for(brand)
        if not base:
            logger.warning(
                "[AUTOPILOT] brand_site: no configured site for brand %r", brand
            )
            return []
        key = _norm(brand).replace(" ", "")
        path = BRAND_SITE_SEARCH_PATHS.get(key, BRAND_SITE_DEFAULT_SEARCH_PATH)
        cat_word = _category_query_word(category)
        query = " ".join(p for p in (model, color, cat_word) if p)
        url = base.rstrip("/") + path.format(q=_url_q(query))
        body = _http_get(url)
        if not body:
            return []

        # v2 multi-candidate: mine the SEARCH-RESULTS page for the top product
        # links and scrape each one -> a candidate per product page (with its
        # exact source_url), so a search can surface 4-5 options, not 1.
        max_pages = int(os.getenv("AUTOPILOT_BRAND_SITE_MAX_PAGES", "5"))
        out: List[Dict[str, Any]] = []
        links = extract_product_links(
            body, url, model, limit=min(max_pages, int(limit))
        )
        for link in links:
            page = _http_get(link)
            if not page:
                continue
            scraped = scrape_product_page(page)
            if not (
                scraped.get("title")
                or scraped.get("specs")
                or scraped.get("image_urls")
            ):
                continue
            out.append(self._build_candidate(scraped, link, brand, model, color, size))
            if len(out) >= max_pages:
                break
        if out:
            return out

        # Fallback (legacy path): no usable product links — scrape the search
        # page itself (it often IS the product page for an exact-model query).
        scraped = scrape_product_page(body)
        if not (
            scraped.get("title") or scraped.get("specs") or scraped.get("image_urls")
        ):
            return []
        return [self._build_candidate(scraped, url, brand, model, color, size)]


# --- Priority 2: myLuxottica dealer portal (AUTHORIZED, authenticated) ------

# Portal endpoints/selectors kept as tunable module constants because we cannot
# test the live portal here. The operator adjusts these once creds are set on
# Railway. All are best-effort; any mismatch just yields [] (fail-soft).
MYLUXOTTICA_BASE_URL = os.getenv(
    "MYLUXOTTICA_BASE_URL", "https://my.essilorluxottica.com"
)
# Login / search paths stay env-tunable: the exact EssilorLuxottica portal paths
# are unknown and get tuned during live testing after the owner pastes creds.
MYLUXOTTICA_LOGIN_PATH = os.getenv("MYLUXOTTICA_LOGIN_PATH", "/auth/login")
MYLUXOTTICA_SEARCH_PATH = os.getenv(
    "MYLUXOTTICA_SEARCH_PATH", "/catalog/search?query={q}"
)
MYLUXOTTICA_LOGIN_USER_FIELD = "username"
MYLUXOTTICA_LOGIN_PASS_FIELD = "password"


class MyLuxotticaAdapter(SourceAdapter):
    """Authenticated dealer-portal scrape. Enabled only when both creds env
    vars are present. login() establishes an httpx.Client session; search()
    scrapes the catalog page. Cannot be tested live, so URLs/fields/selectors
    are module constants the operator can adjust. Fully fail-soft."""

    name = "myluxottica"
    label = "myLuxottica dealer portal"
    source_class = AUTHORIZED
    priority = 2

    @staticmethod
    def _creds() -> Dict[str, str]:
        """Read decrypted creds from Settings->Integrations (DB) with an env
        fallback. Imported lazily to avoid any import-order/circular issues.
        Fail-soft: any error -> {}."""
        try:
            from api.services import integration_config

            return integration_config.get_myluxottica_config() or {}
        except Exception:  # noqa: BLE001 - never break the adapter on config read
            return {}

    def is_enabled(self) -> bool:
        # Enabled purely on credential presence (matches the original contract +
        # the InternalCatalog/gating tests). httpx-missing / network-disabled are
        # runtime concerns: login()/_search() are fail-soft (return []), and
        # reason() surfaces them. Gating is_enabled on them would wrongly report
        # the source as unconfigured.
        creds = self._creds()
        return bool(creds.get("user") and creds.get("password"))

    def reason(self) -> str:
        if _network_disabled():
            return "AUTOPILOT_DISABLE_NETWORK set; myLuxottica scrape off."
        if httpx is None:
            return "httpx unavailable; myLuxottica scraping disabled."
        if self.is_enabled():
            return "Credentials present; authenticated portal scrape active."
        return (
            "Add myLuxottica dealer creds in Settings -> Integrations "
            "(or set MYLUXOTTICA_USER + MYLUXOTTICA_PASS) to enable."
        )

    def _base_url(self) -> str:
        """Portal base URL: DB config wins, else the module default."""
        return (self._creds().get("base_url") or MYLUXOTTICA_BASE_URL).rstrip("/")

    def login(self, client: Any) -> bool:
        """Best-effort form login on the shared client session. Returns True if
        the POST looked successful. Never raises."""
        creds = self._creds()
        user = creds.get("user") or ""
        pwd = creds.get("password") or ""
        if not (user and pwd) or httpx is None:
            return False
        url = self._base_url() + MYLUXOTTICA_LOGIN_PATH
        try:
            resp = client.post(
                url,
                data={
                    MYLUXOTTICA_LOGIN_USER_FIELD: user,
                    MYLUXOTTICA_LOGIN_PASS_FIELD: pwd,
                },
                headers={"User-Agent": _HTTP_USER_AGENT},
                timeout=HTTP_TIMEOUT,
            )
            return resp.status_code in (200, 201, 302, 303)
        except Exception as e:  # noqa: BLE001
            logger.warning("[AUTOPILOT] myluxottica login failed: %s", e)
            return False

    def _search(self, brand, model, color, size, limit, category=""):
        if not self.is_enabled() or httpx is None:
            return []
        base_url = self._base_url()
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                if not self.login(client):
                    logger.warning("[AUTOPILOT] myluxottica: login unsuccessful")
                    return []
                cat_word = _category_query_word(category)
                query = " ".join(p for p in (model, color, cat_word) if p)
                url = base_url + MYLUXOTTICA_SEARCH_PATH.format(
                    q=_url_q(query)
                )
                body = _http_get(url, client=client)
        except Exception as e:  # noqa: BLE001
            logger.warning("[AUTOPILOT] myluxottica session failed: %s", e)
            return []
        if not body:
            return []
        scraped = scrape_product_page(body)
        if not (
            scraped.get("title") or scraped.get("specs") or scraped.get("image_urls")
        ):
            return []
        cand = _candidate_skeleton(self.name, self.source_class)
        cand.update(
            {
                "url": base_url,
                "source_url": url,
                "references": [{"source": self.name, "url": url}],
                "title": scraped.get("title") or f"{brand} {model}".strip(),
                "brand": brand,
                "model": model,
                "color": color or None,
                "size": size or None,
                "image_urls": scraped.get("image_urls") or [],
                "specs": scraped.get("specs") or {},
                "description": scraped.get("description") or None,
                "usp": scraped.get("usp") or None,
            }
        )
        return [cand]


# --- Priority 3: our own online catalog (AUTHORIZED, DB-backed) -------------


class InternalCatalogAdapter(SourceAdapter):
    """Wrap the existing search_internal_catalog so the BVI catalog source
    participates in the registry uniformly."""

    name = "internal_bvi"
    label = "Our online catalog (dedup/enrich)"
    source_class = AUTHORIZED
    priority = 3

    def is_enabled(self) -> bool:
        return _ecommerce_configured()

    def reason(self) -> str:
        if self.is_enabled():
            return "Searches existing BVI products by brand + model."
        return "Set ECOMMERCE_DATABASE_URL to enable BVI dedup/enrich."

    def _search(self, brand, model, color, size, limit):
        return search_internal_catalog(brand, model, limit=limit)


# --- Priority 4: marketplaces / open web (UNVERIFIED, specs-only) -----------

MARKETPLACE_SEARCH_URL = "https://serpapi.com/search"
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"


class MarketplaceAdapter(SourceAdapter):
    """Specs-only web/marketplace search via a search API. Enabled when a
    search API key env exists (SERP_API_KEY, or GOOGLE_CSE_KEY+GOOGLE_CSE_CX).
    May return image_urls but they stay UNVERIFIED -> the router's copyright
    guard blocks auto image use. Fully fail-soft."""

    name = "marketplace"
    label = "Marketplaces / web (specs-only by default)"
    source_class = UNVERIFIED
    priority = 4

    @staticmethod
    def _websearch_config() -> Dict[str, Any]:
        """Read the web-search config from Settings->Integrations (DB) with an
        env fallback. Imported lazily to avoid import-order/circular issues.
        Fail-soft: any error -> {} (treated as unconfigured)."""
        try:
            from api.services import integration_config

            return integration_config.get_websearch_config() or {}
        except Exception:  # noqa: BLE001 - never break the adapter on config read
            return {}

    def _has_serp(self) -> bool:
        return self._websearch_config().get("provider") == "serpapi"

    def _has_cse(self) -> bool:
        return self._websearch_config().get("provider") == "google_cse"

    def is_enabled(self) -> bool:
        if _network_disabled():
            return False
        return bool(self._websearch_config().get("provider"))

    def reason(self) -> str:
        if _network_disabled():
            return "AUTOPILOT_DISABLE_NETWORK set; marketplace search off."
        if self.is_enabled():
            return "Search API key present; specs-only (images stay unverified)."
        return (
            "Add a Web Search key in Settings -> Integrations "
            "(or set GOOGLE_CSE_KEY+GOOGLE_CSE_CX / SERP_API_KEY) to enable."
        )

    def _search(self, brand, model, color, size, limit, category=""):
        if httpx is None or not self.is_enabled():
            return []
        cfg = self._websearch_config()
        provider = cfg.get("provider")
        cat_word = _category_query_word(category)
        q = " ".join(p for p in [brand, model, color, size, cat_word] if p).strip()
        if not q:
            return []
        items: List[Dict[str, Any]] = []
        try:
            # Prefer Google CSE (key + cx), then SerpAPI. Behaviour is identical
            # when only env vars are set (the loader falls back to env).
            if provider == "google_cse":
                body = _http_get(
                    GOOGLE_CSE_URL,
                    params={
                        "q": q,
                        "key": cfg.get("google_cse_key"),
                        "cx": cfg.get("google_cse_cx"),
                        "num": min(int(limit), 10),
                    },
                )
                items = self._parse_cse(body)
            elif provider == "serpapi":
                body = _http_get(
                    MARKETPLACE_SEARCH_URL,
                    params={
                        "q": q,
                        "api_key": cfg.get("serp_api_key"),
                        "num": min(int(limit), 10),
                    },
                )
                items = self._parse_serp(body)
        except Exception as e:  # noqa: BLE001
            logger.warning("[AUTOPILOT] marketplace search failed: %s", e)
            return []
        out: List[Dict[str, Any]] = []
        for it in items[: int(limit)]:
            cand = _candidate_skeleton(self.name, self.source_class)
            it_url = it.get("url")
            cand.update(
                {
                    "url": it_url,
                    "source_url": it_url,
                    "references": (
                        [{"source": self.name, "url": it_url}] if it_url else []
                    ),
                    "title": it.get("title"),
                    "brand": brand,
                    "model": model,
                    "color": color or None,
                    "size": size or None,
                    # Images may be present but remain UNVERIFIED (copyright guard).
                    "image_urls": it.get("image_urls") or [],
                    "specs": {},
                    "description": it.get("description") or None,
                    "usp": None,
                }
            )
            out.append(cand)
        return out

    @staticmethod
    def _parse_json(body: Optional[str]) -> Dict[str, Any]:
        if not body:
            return {}
        try:
            import json

            data = json.loads(body)
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _parse_serp(self, body: Optional[str]) -> List[Dict[str, Any]]:
        data = self._parse_json(body)
        out: List[Dict[str, Any]] = []
        for r in data.get("organic_results") or []:
            if not isinstance(r, dict):
                continue
            img = r.get("thumbnail")
            out.append(
                {
                    "url": r.get("link"),
                    "title": r.get("title"),
                    "description": r.get("snippet"),
                    "image_urls": [img] if img else [],
                }
            )
        return out

    def _parse_cse(self, body: Optional[str]) -> List[Dict[str, Any]]:
        data = self._parse_json(body)
        out: List[Dict[str, Any]] = []
        for r in data.get("items") or []:
            if not isinstance(r, dict):
                continue
            images: List[str] = []
            pagemap = r.get("pagemap") or {}
            cse_img = (
                (pagemap.get("cse_image") or []) if isinstance(pagemap, dict) else []
            )
            if cse_img and isinstance(cse_img[0], dict) and cse_img[0].get("src"):
                images = [cse_img[0]["src"]]
            out.append(
                {
                    "url": r.get("link"),
                    "title": r.get("title"),
                    "description": r.get("snippet"),
                    "image_urls": images,
                }
            )
        return out


# --- Priority 2: Claude AI product enrichment (AUTHORIZED, generated TEXT) ---

# The IMS GST categories the AI is allowed to choose from. Sourced from the
# canonical gst_rates table (single source of truth) so the AI's category always
# maps to a real IMS product category that POS can bill. Fail-soft to a curated
# optical-retail subset if the table can't be imported for any reason.
_AI_CATEGORY_FALLBACK = (
    "FRAME",
    "OPTICAL_LENS",
    "CONTACT_LENS",
    "COLORED_CONTACT_LENS",
    "READING_GLASSES",
    "SUNGLASS",
    "WATCH",
    "SMARTWATCH",
    "SMARTGLASSES",
    "WALL_CLOCK",
    "ACCESSORIES",
    "HEARING_AID",
)


def _ai_allowed_categories() -> List[str]:
    """Canonical IMS product categories for the AI to pick from. Reads the
    gst_rates master so the AI category is always one POS can price; fail-soft
    to a curated optical subset. Never raises."""
    try:
        from .gst_rates import GST_CATEGORY_TABLE

        # The canonical product categories (not the legacy aliases / short UI
        # codes / order-only item_types). Keep it to recognisable optical-retail
        # product types so the model isn't shown 60 near-duplicate keys.
        wanted = list(_AI_CATEGORY_FALLBACK)
        present = [c for c in wanted if c in GST_CATEGORY_TABLE]
        return present or list(_AI_CATEGORY_FALLBACK)
    except Exception:  # noqa: BLE001
        return list(_AI_CATEGORY_FALLBACK)


def _ai_client_importable() -> bool:
    """True if the shared agents Claude helper can be imported. Pure import
    probe (no network, no key check); is_enabled() ANDs this with the key."""
    try:
        from agents import claude_client  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _run_coro_sync(coro) -> Any:
    """Run an async coroutine to completion from this SYNC code path and return
    its result. run_search (and thus every adapter._search) is synchronous,
    while the Claude helper is async. Bridge fail-soft:

    - No running loop (the normal FastAPI threadpool / pytest case): drive the
      coroutine to completion on a throwaway loop via asyncio.run-style.
    - A loop already running in THIS thread (rare for our sync call sites):
      run the coroutine on a dedicated loop in a worker thread so we never call
      asyncio.run() re-entrantly (which would raise).
    Any failure -> the coroutine is closed and None is returned (never raises).
    """
    try:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is None:
            return asyncio.run(coro)

        # A loop is already running in this thread; execute the coroutine on a
        # separate thread with its own event loop to avoid re-entrancy.
        import concurrent.futures

        def _runner():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_runner).result()
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUTOPILOT] ai_enrich async bridge failed: %s", e)
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        return None


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _coerce_specs(value: Any) -> Dict[str, str]:
    """Flatten the AI's specs object to a {str: str} map, fail-soft."""
    out: Dict[str, str] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            if v is None:
                continue
            try:
                out[str(k)[:60]] = str(v)[:200]
            except Exception:  # noqa: BLE001
                continue
            if len(out) >= 40:
                break
    return out


class AIEnrichAdapter(SourceAdapter):
    """Generate catalog enrichment TEXT for a brand+model via one Claude call
    (reusing the shared agents Claude client - no new SDK dependency). This is
    the RELIABLE contributor: it needs no scraping and no dealer-portal creds,
    only ANTHROPIC_API_KEY. The generated copy is original TEXT, so it is
    AUTHORIZED to use; it never fabricates product image URLs (image_urls stays
    [] - images remain a scraping / authorized-source concern). Fully fail-soft:
    missing key / import error / API error / bad JSON -> [] (never raises)."""

    name = "ai_enrich"
    label = "AI product enrichment (Claude)"
    source_class = AUTHORIZED
    priority = 2

    def is_enabled(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY")) and _ai_client_importable()

    def reason(self) -> str:
        if not os.getenv("ANTHROPIC_API_KEY"):
            return "Set ANTHROPIC_API_KEY to enable Claude product enrichment."
        if not _ai_client_importable():
            return "agents Claude client unavailable; AI enrichment disabled."
        return "Claude generates catalog copy/specs from brand + model (no images)."

    def _build_prompt(self, brand, model, color, size, category="") -> tuple:
        cats = ", ".join(_ai_allowed_categories())
        descriptor = " ".join(
            p for p in [brand, model, color, size] if (p and str(p).strip())
        ).strip()
        system = (
            "You are an optical-retail catalog assistant. Given an eyewear "
            "product, return concise, factual catalog enrichment as STRICT JSON. "
            "Do NOT invent a product image URL. If you are not confident the "
            "exact product exists, set needs_review to true and confidence low."
        )
        category_hint = ""
        if category and str(category).strip():
            category_hint = (
                " The operator states this product's category is "
                + str(category).strip().upper()
                + "; use that category unless it is clearly impossible."
            )
        user = (
            "For the eyewear product "
            + (descriptor or f"{brand} {model}".strip())
            + "."
            + category_hint
            + " Return STRICT JSON with these keys:\n"
            '  "title": string,\n'
            '  "category": one of [' + cats + "],\n"
            '  "frame_shape": string,\n'
            '  "frame_material": string,\n'
            '  "lens_material": string,\n'
            '  "gender": string,\n'
            '  "description": string (2-3 sentences),\n'
            '  "usp": string (one line),\n'
            '  "specs": object of concise key facts,\n'
            '  "suggested_hsn": string,\n'
            '  "suggested_gst_rate": number,\n'
            '  "confidence": number between 0 and 1,\n'
            '  "needs_review": boolean (true if unsure of the exact product).'
        )
        return system, user

    def _to_candidate(
        self, data, brand, model, color, size
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(data, dict):
            return None
        cand = _candidate_skeleton(self.name, self.source_class)
        cand.update(
            {
                "title": _coerce_str(data.get("title")) or f"{brand} {model}".strip(),
                "brand": brand,
                "model": model,
                "color": (_coerce_str(color) or _coerce_str(data.get("color"))),
                "size": (_coerce_str(size) or _coerce_str(data.get("size"))),
                # AI MUST NOT fabricate image URLs - images stay an authorized-
                # source / scraping concern. Surfaced honestly as empty.
                "image_urls": [],
                "specs": _coerce_specs(data.get("specs")),
                "description": _coerce_str(data.get("description")),
                "usp": _coerce_str(data.get("usp")),
            }
        )
        # Carry the AI's enrichment fields on the candidate dict so the reviewer
        # UI / downstream can map category -> IMS GST and surface confidence.
        category = _coerce_str(data.get("category"))
        if category:
            cand["category"] = category.upper()
        # Pull a few common spec fields up to top-level when the model returned
        # them as discrete keys (harmless if absent).
        for key in ("frame_shape", "frame_material", "lens_material", "gender"):
            val = _coerce_str(data.get(key))
            if val:
                cand[key] = val
        hsn = _coerce_str(data.get("suggested_hsn"))
        if hsn:
            cand["suggested_hsn"] = hsn
        rate = data.get("suggested_gst_rate")
        if rate is not None:
            try:
                cand["suggested_gst_rate"] = float(rate)
            except (TypeError, ValueError):
                pass
        conf = data.get("confidence")
        if conf is not None:
            try:
                cand["confidence"] = float(conf)
            except (TypeError, ValueError):
                pass
        cand["needs_review"] = bool(data.get("needs_review", True))
        return cand

    def _search(self, brand, model, color, size, limit, category=""):
        if not self.is_enabled():
            return []
        try:
            from agents.claude_client import call_claude_json
        except Exception as e:  # noqa: BLE001
            logger.warning("[AUTOPILOT] ai_enrich import failed: %s", e)
            return []
        system, user = self._build_prompt(brand, model, color, size, category)
        max_tokens = int(os.getenv("AUTOPILOT_AI_MAX_TOKENS", "700"))
        # ONE Claude call. The helper already strips code fences + parses JSON
        # fail-soft (returns None on any failure), so we never see raw text.
        data = _run_coro_sync(call_claude_json(system, user, max_tokens=max_tokens))
        if data is None:
            return []
        cand = self._to_candidate(data, brand, model, color, size)
        return [cand] if cand else []


# ---------------------------------------------------------------------------
# Optional AI spec-mapping enrichment (v2, fail-soft)
# ---------------------------------------------------------------------------
# After scraping, when the job is category-aware AND ANTHROPIC_API_KEY is set,
# each top candidate's scraped title/description/free-form specs are sent to
# Claude together with the category's CANONICAL attribute field list (from the
# product_master CATEGORY_SPECS registry). Claude returns {attributeName:
# value}, attached to the candidate as `ai_attributes`. The FE merges these
# UNDER its deterministic synonym mapper (deterministic first, AI fills gaps).
# Strictly fail-soft: no key / import error / timeout / bad JSON -> {} and the
# candidate simply has no ai_attributes. No emojis in any of this (cp1252).


def _ai_spec_enrich_enabled() -> bool:
    """Gate for the spec-mapping enrichment: same contract as AIEnrichAdapter
    (env key + importable shared Claude client). Reads config only."""
    return bool(os.getenv("ANTHROPIC_API_KEY")) and _ai_client_importable()


def _category_field_names(category: Any) -> List[str]:
    """The canonical required+optional attribute keys for a category, from the
    product_master CATEGORY_SPECS registry. [] for unknown/blank (fail-soft)."""
    try:
        from .product_master import category_spec

        spec = category_spec(category)
        if spec is None:
            return []
        return list(spec.required) + list(spec.optional)
    except Exception:  # noqa: BLE001
        return []


def ai_map_specs_to_fields(
    category: Any,
    title: Any,
    description: Any,
    specs: Any,
    query: Dict[str, Any],
) -> Dict[str, str]:
    """ONE short Claude call: map scraped info onto the category's canonical
    attribute fields. Returns {attributeName: value} restricted to the
    category's declared field names; {} on ANY failure (never raises, never
    blocks long — timeout defaults to 8s via AUTOPILOT_AI_ENRICH_TIMEOUT)."""
    try:
        if not category or not _ai_spec_enrich_enabled():
            return {}
        field_names = _category_field_names(category)
        if not field_names:
            return {}
        try:
            from agents.claude_client import call_claude_json
        except Exception as e:  # noqa: BLE001
            logger.warning("[AUTOPILOT] ai spec-map import failed: %s", e)
            return {}
        try:
            specs_json = json.dumps(specs or {}, ensure_ascii=True, default=str)[:2000]
        except Exception:  # noqa: BLE001
            specs_json = "{}"
        system = (
            "You are an optical-retail catalog data mapper. Map scraped product "
            "information onto a FIXED set of catalog attribute fields. Respond "
            "with STRICT JSON whose keys are ONLY drawn from the allowed "
            "attribute names; omit any field you cannot fill confidently from "
            "the given data. Values are short strings without units (write 52, "
            "not '52 mm'). Never invent data that is not supported by the input."
        )
        user = (
            "Product category: " + str(category) + "\n"
            "Search query: brand="
            + str(query.get("brand") or "")
            + " model="
            + str(query.get("model") or "")
            + " color="
            + str(query.get("color") or "")
            + " size="
            + str(query.get("size") or "")
            + "\n"
            "Scraped title: " + str(title or "")[:200] + "\n"
            "Scraped description: " + str(description or "")[:600] + "\n"
            "Scraped specs JSON: " + specs_json + "\n"
            "Allowed attribute names: " + ", ".join(field_names) + "\n"
            "Return one JSON object mapping attribute name -> string value."
        )
        timeout = float(os.getenv("AUTOPILOT_AI_ENRICH_TIMEOUT", "8.0"))
        max_tokens = int(os.getenv("AUTOPILOT_AI_ENRICH_MAX_TOKENS", "400"))
        data = _run_coro_sync(
            call_claude_json(system, user, max_tokens=max_tokens, timeout=timeout)
        )
        if not isinstance(data, dict):
            return {}
        allowed = set(field_names)
        out: Dict[str, str] = {}
        for k, v in data.items():
            key = str(k).strip()
            if key not in allowed or v is None:
                continue
            val = str(v).strip()
            if val:
                out[key] = val[:200]
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUTOPILOT] ai_map_specs_to_fields failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Registry + orchestration
# ---------------------------------------------------------------------------


def build_registry() -> List[SourceAdapter]:
    """Ordered adapter registry (lower priority value runs first). The single
    source of truth for both run_search and GET /sources."""
    adapters = [
        BrandSiteAdapter(),
        AIEnrichAdapter(),
        MyLuxotticaAdapter(),
        InternalCatalogAdapter(),
        MarketplaceAdapter(),
    ]
    adapters.sort(key=lambda a: a.priority)
    return adapters


def _provider_status() -> List[Dict[str, Any]]:
    """What each source is and whether it's active right now, derived from the
    adapter registry. GET /sources surfaces this so the operator knows which
    sources will run. Order matches run order (by priority)."""
    return [a.status() for a in build_registry()]


def _ident_key(brand: str, model: str) -> str:
    """Brand+model identity, collapsed to uppercase alphanumerics (via
    normalize_model's rule) so 'Ray-Ban'/'rayban' and 'RB 4105'/'rb-4105' all
    unify. This is the cross-source part of the dedupe key."""
    return normalize_model(brand) + "|" + normalize_model(model)


def _dedupe_key(brand: str, model: str, source: str, source_url: str = "") -> str:
    """Per-candidate dedupe key = normalized brand + model + source (+ the
    exact source page URL when present). Including the source means a single
    source can't list the same product twice, while a DIFFERENT source offering
    the same model is still kept (useful for the dedup/enrich view - e.g. the
    authoritative brand-site row AND the 'already in our catalog' internal
    row). v2: a source that scrapes SEVERAL DISTINCT product pages (brand-site
    multi-candidate) keeps one candidate per page — the page URL disambiguates;
    candidates with no source_url collapse per-source exactly as before."""
    return (
        _ident_key(brand, model) + "|" + str(source or "") + "|" + str(source_url or "")
    )


def run_search(
    brand: str,
    model: str,
    color: str = "",
    size: str = "",
    limit: int = 25,
    category: str = "",
) -> Dict[str, Any]:
    """Run all ENABLED adapters in priority order, dedupe across the combined
    result set, score, and sort. Pure orchestration; persistence is the router's
    job. Every step is fail-soft - no exception escapes (a bad adapter just
    contributes nothing).

    v2: `category` (a canonical product category, e.g. "SUNGLASS") makes the
    job category-aware — it refines source queries, is stamped onto the query
    echo AND every candidate (so downstream mapping never guesses), and gates
    the optional AI spec-mapping enrichment."""
    query = {"brand": brand, "model": model, "color": color, "size": size}
    if category:
        query["category"] = category
    registry = build_registry()

    # Dedupe: first-seen wins. Because we walk the registry in priority order
    # (AUTHORIZED brand-site=1 ... UNVERIFIED marketplace=4), an exact-key
    # collision is won by the higher-priority / more-authoritative source.
    seen: Dict[str, Dict[str, Any]] = {}
    candidates: List[Dict[str, Any]] = []
    for adapter in registry:
        try:
            if not adapter.is_enabled():
                continue
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[AUTOPILOT] adapter %s is_enabled raised: %s", adapter.name, e
            )
            continue
        for cand in adapter.search(
            brand, model, color, size, limit=limit, category=category
        ):
            cand_brand = cand.get("brand") or brand
            cand_model = cand.get("model") or model
            cand_source = cand.get("source") or adapter.name
            dkey = _dedupe_key(
                cand_brand, cand_model, cand_source, cand.get("source_url") or ""
            )
            if dkey in seen:
                continue
            try:
                scored = score_candidate(query, cand)
            except Exception as e:  # noqa: BLE001
                logger.warning("[AUTOPILOT] scoring failed for %s: %s", adapter.name, e)
                scored = {"score": 0.0, "matched": {}}
            cand.update(scored)
            cand["source_priority"] = adapter.priority
            # v2: the operator-chosen category is authoritative — stamp it on
            # every candidate so the Add-Product mapper knows the category
            # without re-guessing from free text.
            if category:
                cand["category"] = category
            seen[dkey] = cand
            candidates.append(cand)

    # Sort by score desc, stable tie-break on source priority (lower first).
    candidates.sort(key=lambda c: (-c.get("score", 0), c.get("source_priority", 100)))

    # OPTIONAL AI assist (v2, strictly fail-soft): map the top scraped
    # candidates' free-form specs onto the category's canonical attribute
    # fields via one short Claude call each. Deterministic mapping happens on
    # the FE; these AI values only FILL GAPS there. Skipped entirely when no
    # key / no category; a per-candidate failure just leaves that candidate
    # without ai_attributes. Never blocks: short timeout, capped candidates.
    if category and candidates and _ai_spec_enrich_enabled():
        max_enrich = int(os.getenv("AUTOPILOT_AI_ENRICH_MAX", "3"))
        enriched = 0
        for cand in candidates:
            if enriched >= max_enrich:
                break
            if cand.get("source") == "ai_enrich":
                continue  # already AI-generated content
            if not (cand.get("title") or cand.get("specs") or cand.get("description")):
                continue
            attrs = ai_map_specs_to_fields(
                category,
                cand.get("title"),
                cand.get("description"),
                cand.get("specs"),
                query,
            )
            if attrs:
                cand["ai_attributes"] = attrs
            enriched += 1

    return {
        "query": query,
        "candidates": candidates,
        "sources": [a.status() for a in registry],
        "candidate_count": len(candidates),
    }
