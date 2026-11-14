"""
IMS 2.0 - Ad Performance Provider Seam
========================================
CRM-16: Marketing agency oversight dashboard.

Thin async clients for Google Ads and Meta (Facebook) Ads APIs.
Returns a normalised per-campaign shape so the router and UI are
provider-agnostic.

FAIL-SOFT contract
------------------
If creds are absent from the ``integrations`` collection (or the
collection is unavailable), every function returns a SIMULATED result
with ``status="not_configured"`` and NEVER raises. The router surfaces
a ``configured`` flag per channel so the frontend can show the
"connect" empty-state instead of a spinner.

Three states, never merged (audit L1-F33):
  not connected   no / incomplete creds -> sentinel row status="not_configured"
  fetch failed    creds present but the provider timed out / refused / sent a
                  reply we cannot read -> sentinel row status="error" carrying
                  a plain reason (never a credential or a provider body);
                  the channel reads configured=True with <channel>_error set
  no campaigns    a valid empty report -> [] ; configured=True, no error
A partial or guessed number is never shown: an unreadable currency or a page
that cannot be fetched fails the whole channel.

Credential resolution
---------------------
Credentials are resolved from the ``integrations`` MongoDB collection
(same pattern as nexus_providers._load_integration_config):

    { type: "google_ads",  enabled: true,  config: { customer_id, developer_token, client_id, client_secret, refresh_token } }
    { type: "meta_ads",    enabled: true,  config: { ad_account_id, access_token } }

No env vars for secrets -- they live in the DB integrations doc.

HTTP transport
--------------
Uses httpx (already a direct dependency).  Any optional SDK import
(google-ads, facebook-business) is guarded so a missing package never
crashes a cold start.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared config helper (mirrors nexus_providers._load_integration_config)
# ---------------------------------------------------------------------------


def _load_integration_config(db, integration_type: str) -> Dict[str, Any]:
    """Look up {type, enabled, config:{...}} for one integration. Returns {} if absent."""
    if db is None:
        return {}
    try:
        coll = db.get_collection("integrations")
        doc = coll.find_one({"type": integration_type.lower(), "enabled": True})
        if not doc:
            return {}
        # BUG-155: secrets are Fernet-encrypted at rest; decrypt for provider use.
        from api.services import cred_crypto

        return cred_crypto.decrypt_config(doc.get("config") or {})
    except Exception as exc:
        logger.debug("[AD_PROVIDERS] Config read failed for %s: %s", integration_type, exc)
        return {}


# ---------------------------------------------------------------------------
# Normalised data shapes
# ---------------------------------------------------------------------------


@dataclass
class CampaignRow:
    """One row in the ad-performance table (provider-agnostic)."""

    channel: str  # "google" | "meta"
    campaign_id: str
    campaign_name: str
    spend: float  # INR (a USD account's spend at the saved rate - _inr_rate)
    impressions: int
    clicks: int
    # Leads / form fills, as the provider reports them (Google's can be
    # fractional), to 2 places. THE figure: cpl and the totals divide by it.
    conversions: float
    ctr: float  # click-through rate 0-100 (%)
    cpl: float  # spend / conversions (0 when conversions == 0) - see _per_lead
    roas: float  # return-on-ad-spend (0 when spend == 0)
    currency: str = "INR"
    status: str = "ok"  # "ok" | "not_configured" | "error" (see module docstring)


@dataclass
class AdPerformanceResult:
    """Top-level result returned by the router."""

    rows: List[CampaignRow] = field(default_factory=list)
    total_spend: float = 0.0
    total_impressions: int = 0
    total_clicks: int = 0
    total_conversions: float = 0.0
    blended_roas: float = 0.0  # 0 when spend == 0
    total_cpl: float = 0.0  # 0 when conversions == 0
    google_configured: bool = False
    meta_configured: bool = False
    # Why a CONNECTED channel has no rows ("" = fetched fine). Plain words.
    google_error: str = ""
    meta_error: str = ""
    fetched_at: str = ""
    note: str = ""


def _compute_totals(result: AdPerformanceResult) -> None:
    """Re-derive aggregate fields from the rows list (mutates in place)."""
    if not result.rows:
        return
    result.total_spend = round(sum(r.spend for r in result.rows), 2)
    result.total_impressions = sum(r.impressions for r in result.rows)
    result.total_clicks = sum(r.clicks for r in result.rows)
    result.total_conversions = round(sum(r.conversions for r in result.rows), 2)
    result.total_cpl = _per_lead(result.total_spend, result.total_conversions)
    # blended_roas stays 0 -- requires revenue data from the ad accounts,
    # not yet available from the API surface; router can override if needed.


def _sentinel_rows(channel: str, campaign_id: str, name: str, status: str) -> List[CampaignRow]:
    return [
        CampaignRow(
            channel=channel,
            campaign_id=campaign_id,
            campaign_name=name,
            spend=0.0,
            impressions=0,
            clicks=0,
            conversions=0,
            ctr=0.0,
            cpl=0.0,
            roas=0.0,
            status=status,
        )
    ]


def _not_configured_rows(channel: str) -> List[CampaignRow]:
    """Placeholder sentinel row to signal a channel has no creds."""
    return _sentinel_rows(
        channel, "__not_configured__", "(no credentials configured)", "not_configured"
    )


def _error_rows(channel: str, reason: str) -> List[CampaignRow]:
    """Sentinel row: the channel HAS credentials but its report could not be
    fetched. `reason` is shown to the user - plain words, no secret, no
    provider response body."""
    return _sentinel_rows(channel, "__error__", reason, "error")


def _num(value: Any) -> float:
    """A metric as a number. Google Ads REST sends int64 fields (costMicros,
    impressions, clicks) as JSON STRINGS and doubles as numbers; Meta sends
    strings. A value that is not a number raises, and the caller reports the
    channel as failed rather than showing a zero."""
    return float(value or 0)


def _per_lead(spend: float, conversions: float) -> float:
    """Cost per lead from the SAME conversions figure the row (or the total)
    shows - never a different one, so a row cannot read 0 leads beside a
    non-zero cost per lead. 0 when there are no conversions."""
    return round(spend / conversions, 2) if conversions > 0 else 0.0


def _inr_rate(cfg: Dict[str, Any], currency: Any) -> Optional[float]:
    """Rupees per unit of an ad account's currency - THE rule for both
    providers. INR needs no conversion; USD uses the snapshot rate saved in
    the integration config (usd_inr_rate, default 84); any other currency, or
    none reported, is None: a rate is never guessed."""
    return {"INR": 1.0, "USD": float(cfg.get("usd_inr_rate", 84.0))}.get(
        str(currency or "").strip().upper()
    )


def _currency_error(provider: str, currency: Any) -> str:
    said = str(currency or "").strip().upper() or "was not reported"
    return (
        f"{provider} account currency {said}: only INR and USD accounts can be "
        "shown in rupees."
    )


# ---------------------------------------------------------------------------
# Google Ads provider
# ---------------------------------------------------------------------------

# Google Ads REST version. The adapter used /v17, which stopped serving on
# 2025-06-04 (audit L1-F33). v26 was released 2026-10-14 and is scheduled to
# sunset in November 2027 (developers.google.com/google-ads/api/docs/sunset-dates,
# read 2026-11-11). A code pin, like Meta's below: move it before that date.
GOOGLE_ADS_API_VERSION = "v26"

# customer.currency_code: the account's own currency - cost_micros is in it.
# Cost per lead is derived here (spend / conversions), so the unit of Google's
# own cost_per_conversion is never relied on.
_GOOGLE_REPORT_QUERY = """
SELECT
  campaign.id,
  campaign.name,
  campaign.status,
  customer.currency_code,
  metrics.cost_micros,
  metrics.impressions,
  metrics.clicks,
  metrics.conversions,
  metrics.ctr
FROM campaign
WHERE segments.date BETWEEN '{from_date}' AND '{to_date}'
  AND campaign.status != 'REMOVED'
ORDER BY metrics.cost_micros DESC
LIMIT 50
"""


async def google_ads_performance(
    db,
    date_range: Dict[str, str],  # {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}
) -> List[CampaignRow]:
    """
    Fetch campaign-level metrics from the Google Ads API (REST/GAQL).

    Returns sentinel rows (status="not_configured") when creds are absent.
    Never raises -- all errors are caught and logged.
    """
    cfg = _load_integration_config(db, "google_ads")
    if not cfg:
        logger.debug(
            "[AD_PROVIDERS] Google Ads: no integration config -- returning not_configured"
        )
        return _not_configured_rows("google")

    customer_id = cfg.get("customer_id", "").replace("-", "")
    developer_token = cfg.get("developer_token", "")
    client_id = cfg.get("client_id", "")
    client_secret = cfg.get("client_secret", "")
    refresh_token = cfg.get("refresh_token", "")

    if not all([customer_id, developer_token, client_id, client_secret, refresh_token]):
        logger.info(
            "[AD_PROVIDERS] Google Ads: incomplete credentials -- returning not_configured"
        )
        return _not_configured_rows("google")

    try:
        # Step 1: refresh the OAuth2 access token
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
        if token_resp.status_code != 200:
            logger.warning(
                "[AD_PROVIDERS] Google Ads token refresh failed: %s %s",
                token_resp.status_code,
                token_resp.text[:200],
            )
            return _error_rows(
                "google",
                f"Google did not accept the saved Google Ads sign-in "
                f"(HTTP {token_resp.status_code}). Reconnect the account.",
            )

        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            logger.warning("[AD_PROVIDERS] Google Ads token response missing access_token")
            return _error_rows(
                "google", "Google did not return an access token for the saved sign-in."
            )

        # Step 2: run the GAQL report (searchStream returns a JSON array of batches)
        gaql = _GOOGLE_REPORT_QUERY.format(
            from_date=date_range.get("from", ""),
            to_date=date_range.get("to", ""),
        )
        version = GOOGLE_ADS_API_VERSION
        url = (
            f"https://googleads.googleapis.com/{version}/customers/{customer_id}"
            "/googleAds:searchStream"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json={"query": gaql},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "developer-token": developer_token,
                    "Content-Type": "application/json",
                },
            )

        if resp.status_code != 200:
            logger.warning(
                "[AD_PROVIDERS] Google Ads GAQL call failed: %s %s",
                resp.status_code,
                resp.text[:300],
            )
            return _error_rows(
                "google",
                f"Google Ads answered HTTP {resp.status_code} "
                f"(API {version}); the report was not fetched.",
            )

        # cost_micros is in the ACCOUNT's currency (_inr_rate: INR as-is, USD
        # at the saved rate, anything else an error).
        rows: List[CampaignRow] = []

        for batch in resp.json():
            for result in batch.get("results", []):
                camp = result.get("campaign", {})
                metrics = result.get("metrics", {})
                currency = (result.get("customer") or {}).get("currencyCode")
                rate = _inr_rate(cfg, currency)
                if rate is None:
                    return _error_rows("google", _currency_error("Google Ads", currency))
                spend_inr = round(_num(metrics.get("costMicros")) / 1_000_000 * rate, 2)
                impressions = int(_num(metrics.get("impressions")))
                clicks = int(_num(metrics.get("clicks")))
                # A double: may be fractional. Shown as reported, to 2 places.
                conversions = round(_num(metrics.get("conversions")), 2)
                ctr = round(_num(metrics.get("ctr")) * 100, 4)
                cpl_inr = _per_lead(spend_inr, conversions)
                rows.append(
                    CampaignRow(
                        channel="google",
                        campaign_id=str(camp.get("id", "")),
                        campaign_name=str(camp.get("name", "Unknown")),
                        spend=spend_inr,
                        impressions=impressions,
                        clicks=clicks,
                        conversions=conversions,
                        ctr=ctr,
                        cpl=cpl_inr,
                        roas=0.0,
                        currency="INR",
                        status="ok",
                    )
                )
        logger.info("[AD_PROVIDERS] Google Ads: %d campaigns fetched", len(rows))
        return rows

    except httpx.TimeoutException:
        logger.warning("[AD_PROVIDERS] Google Ads timed out")
        return _error_rows("google", "Google Ads did not answer in time. Try again.")
    except Exception as exc:
        logger.error(
            "[AD_PROVIDERS] Google Ads fetch error (fail-soft): %s", exc, exc_info=True
        )
        return _error_rows("google", "The Google Ads report could not be read.")


# ---------------------------------------------------------------------------
# Meta (Facebook) Ads provider
# ---------------------------------------------------------------------------

# account_currency: the ad account's own currency - spend is in it.
_META_FIELDS = (
    "campaign_id,campaign_name,account_currency,spend,impressions,clicks,"
    "actions,cost_per_action_type"
)

_META_GRAPH = "https://graph.facebook.com"
# ponytail: 20 pages x 50 = 1,000 campaigns per period. Past that the channel
# reports an error instead of a partial total; raise the cap if it ever bites.
_META_MAX_PAGES = 20

_META_LEAD_ACTION_TYPES = frozenset(
    {
        "lead",
        "offsite_conversion.fb_pixel_lead",
        "onsite_conversion.lead_grouped",
        "leadgen.other",
    }
)


async def meta_ads_performance(
    db,
    date_range: Dict[str, str],
) -> List[CampaignRow]:
    """
    Fetch campaign-level metrics from the Meta Marketing API (Graph API v20).

    Returns sentinel rows (status="not_configured") when creds are absent.
    Never raises -- all errors are caught and logged.
    """
    cfg = _load_integration_config(db, "meta_ads")
    if not cfg:
        logger.debug(
            "[AD_PROVIDERS] Meta Ads: no integration config -- returning not_configured"
        )
        return _not_configured_rows("meta")

    ad_account_id = cfg.get("ad_account_id", "")
    access_token = cfg.get("access_token", "")

    if not ad_account_id or not access_token:
        logger.info(
            "[AD_PROVIDERS] Meta Ads: incomplete credentials -- returning not_configured"
        )
        return _not_configured_rows("meta")

    # Normalise account ID -- Graph API requires "act_<id>"
    if not ad_account_id.startswith("act_"):
        ad_account_id = f"act_{ad_account_id}"

    try:
        url = f"{_META_GRAPH}/v20.0/{ad_account_id}/insights"
        params: Dict[str, str] = {
            "fields": _META_FIELDS,
            "time_range": (
                f'{{"since":"{date_range.get("from", "")}","until":"{date_range.get("to", "")}"}}'
            ),
            "level": "campaign",
            "limit": "50",
            "access_token": access_token,
        }
        # Every page (paging.next), or an honest error: a first page shown as
        # the whole report understates the spend.
        data: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            for _page in range(_META_MAX_PAGES):
                if resp.status_code != 200:
                    logger.warning(
                        "[AD_PROVIDERS] Meta Ads API call failed: %s %s",
                        resp.status_code,
                        resp.text[:300],
                    )
                    return _error_rows(
                        "meta",
                        f"Meta Ads answered HTTP {resp.status_code}; "
                        "the report was not fetched.",
                    )
                body = resp.json()
                data.extend(body.get("data") or [])
                next_url = str((body.get("paging") or {}).get("next") or "")
                if not next_url:
                    break
                # The next link carries the access token: it is followed only
                # on Meta's own host.
                if not next_url.startswith(_META_GRAPH + "/"):
                    return _error_rows(
                        "meta",
                        "Meta Ads sent a next-page link that is not Meta's; "
                        "the report was not completed.",
                    )
                resp = await client.get(next_url)
            else:
                return _error_rows(
                    "meta",
                    f"Meta Ads has more than {_META_MAX_PAGES * 50} campaigns "
                    "in this period; the report was not completed.",
                )

        rows: List[CampaignRow] = []
        for item in data:
            # Spend is in the AD ACCOUNT's currency, which the report names
            # (account_currency). The same rule as Google (_inr_rate); it was
            # shown as rupees whatever the account's currency was.
            currency = item.get("account_currency")
            rate = _inr_rate(cfg, currency)
            if rate is None:
                return _error_rows("meta", _currency_error("Meta Ads", currency))
            spend = round(_num(item.get("spend")) * rate, 2)
            impressions = int(item.get("impressions", 0))
            clicks = int(item.get("clicks", 0))

            # Lead conversions from the nested actions list: one figure for
            # the Leads column and the cost per lead.
            leads = round(
                sum(
                    _num(action.get("value"))
                    for action in item.get("actions") or []
                    if action.get("action_type", "") in _META_LEAD_ACTION_TYPES
                ),
                2,
            )

            ctr = round((clicks / impressions * 100) if impressions > 0 else 0.0, 4)
            cpl = _per_lead(spend, leads)

            rows.append(
                CampaignRow(
                    channel="meta",
                    campaign_id=str(item.get("campaign_id", "")),
                    campaign_name=str(item.get("campaign_name", "Unknown")),
                    spend=spend,
                    impressions=impressions,
                    clicks=clicks,
                    conversions=leads,
                    ctr=ctr,
                    cpl=cpl,
                    roas=0.0,
                    currency="INR",
                    status="ok",
                )
            )
        logger.info("[AD_PROVIDERS] Meta Ads: %d campaigns fetched", len(rows))
        return rows

    except httpx.TimeoutException:
        logger.warning("[AD_PROVIDERS] Meta Ads timed out")
        return _error_rows("meta", "Meta Ads did not answer in time. Try again.")
    except Exception as exc:
        logger.error(
            "[AD_PROVIDERS] Meta Ads fetch error (fail-soft): %s", exc, exc_info=True
        )
        return _error_rows("meta", "The Meta Ads report could not be read.")


# ---------------------------------------------------------------------------
# Merged performance helper (used by the router)
# ---------------------------------------------------------------------------


async def fetch_ad_performance(
    db,
    from_date: str,
    to_date: str,
    channel: Optional[str] = None,  # None = both; "google" | "meta" = one
) -> AdPerformanceResult:
    """
    Merge Google + Meta rows into a single AdPerformanceResult.

    Always returns a valid object -- never raises.
    """
    date_range = {"from": from_date, "to": to_date}
    result = AdPerformanceResult(fetched_at=datetime.now(timezone.utc).isoformat())

    # None = the channel was not asked for (channel filter).
    google_rows: Optional[List[CampaignRow]] = None
    meta_rows: Optional[List[CampaignRow]] = None

    if channel in (None, "google"):
        try:
            google_rows = await google_ads_performance(db, date_range)
        except Exception as exc:
            logger.error(
                "[AD_PROVIDERS] Unexpected error from google_ads_performance: %s", exc
            )
            google_rows = _error_rows("google", "The Google Ads report could not be read.")

    if channel in (None, "meta"):
        try:
            meta_rows = await meta_ads_performance(db, date_range)
        except Exception as exc:
            logger.error(
                "[AD_PROVIDERS] Unexpected error from meta_ads_performance: %s", exc
            )
            meta_rows = _error_rows("meta", "The Meta Ads report could not be read.")

    def _state(rows: Optional[List[CampaignRow]]) -> tuple:
        """(configured, error) for one channel. Connected is about the
        credentials; whether the fetch worked is the error. An empty report
        from a connected account is neither."""
        first = rows[0].status if rows else ""
        if rows is None or first == "not_configured":
            return False, ""
        return True, rows[0].campaign_name if first == "error" else ""

    result.google_configured, result.google_error = _state(google_rows)
    result.meta_configured, result.meta_error = _state(meta_rows)

    # Sentinel rows (not connected / fetch failed) never reach the data table.
    result.rows = [
        r for r in (google_rows or []) + (meta_rows or []) if r.status == "ok"
    ]

    errors = [e for e in (result.google_error, result.meta_error) if e]
    if errors:
        result.note = " ".join(errors)
    elif not result.google_configured and not result.meta_configured:
        result.note = (
            "No ad account credentials configured. "
            "Add a 'google_ads' or 'meta_ads' integration doc to the "
            "integrations collection to activate live data."
        )

    _compute_totals(result)
    return result
