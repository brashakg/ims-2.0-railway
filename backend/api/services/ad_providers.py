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
        return doc.get("config") or {}
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
    spend: float  # INR (converted from USD at snapshot rate if needed)
    impressions: int
    clicks: int
    conversions: int  # includes leads / form fills
    ctr: float  # click-through rate 0-100 (%)
    cpl: float  # cost-per-lead / cost-per-conversion (0 when conversions == 0)
    roas: float  # return-on-ad-spend (0 when spend == 0)
    currency: str = "INR"
    status: str = "ok"  # "ok" | "not_configured" | "simulated"


@dataclass
class AdPerformanceResult:
    """Top-level result returned by the router."""

    rows: List[CampaignRow] = field(default_factory=list)
    total_spend: float = 0.0
    total_impressions: int = 0
    total_clicks: int = 0
    total_conversions: int = 0
    blended_roas: float = 0.0  # 0 when spend == 0
    total_cpl: float = 0.0  # 0 when conversions == 0
    google_configured: bool = False
    meta_configured: bool = False
    fetched_at: str = ""
    note: str = ""


def _compute_totals(result: AdPerformanceResult) -> None:
    """Re-derive aggregate fields from the rows list (mutates in place)."""
    if not result.rows:
        return
    result.total_spend = round(sum(r.spend for r in result.rows), 2)
    result.total_impressions = sum(r.impressions for r in result.rows)
    result.total_clicks = sum(r.clicks for r in result.rows)
    result.total_conversions = sum(r.conversions for r in result.rows)
    if result.total_conversions > 0:
        result.total_cpl = round(result.total_spend / result.total_conversions, 2)
    # blended_roas stays 0 -- requires revenue data from the ad accounts,
    # not yet available from the API surface; router can override if needed.


def _not_configured_rows(channel: str) -> List[CampaignRow]:
    """Placeholder sentinel row to signal a channel has no creds."""
    return [
        CampaignRow(
            channel=channel,
            campaign_id="__not_configured__",
            campaign_name="(no credentials configured)",
            spend=0.0,
            impressions=0,
            clicks=0,
            conversions=0,
            ctr=0.0,
            cpl=0.0,
            roas=0.0,
            status="not_configured",
        )
    ]


# ---------------------------------------------------------------------------
# Google Ads provider
# ---------------------------------------------------------------------------

_GOOGLE_REPORT_QUERY = """
SELECT
  campaign.id,
  campaign.name,
  campaign.status,
  metrics.cost_micros,
  metrics.impressions,
  metrics.clicks,
  metrics.conversions,
  metrics.ctr,
  metrics.cost_per_conversion
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
        import httpx

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
            return _not_configured_rows("google")

        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            logger.warning("[AD_PROVIDERS] Google Ads token response missing access_token")
            return _not_configured_rows("google")

        # Step 2: run the GAQL report (searchStream returns newline-delimited JSON)
        gaql = _GOOGLE_REPORT_QUERY.format(
            from_date=date_range.get("from", ""),
            to_date=date_range.get("to", ""),
        )
        url = (
            f"https://googleads.googleapis.com/v17/customers/{customer_id}"
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
            return _not_configured_rows("google")

        # USD -> INR conversion (snapshot rate stored in config, default 84)
        fx_rate = float(cfg.get("usd_inr_rate", 84.0))
        rows: List[CampaignRow] = []

        for batch in resp.json():
            for result in batch.get("results", []):
                camp = result.get("campaign", {})
                metrics = result.get("metrics", {})
                spend_micros = metrics.get("costMicros", 0)
                spend_inr = round((spend_micros / 1_000_000) * fx_rate, 2)
                impressions = int(metrics.get("impressions", 0))
                clicks = int(metrics.get("clicks", 0))
                conversions = int(metrics.get("conversions", 0))
                ctr = round(float(metrics.get("ctr", 0)) * 100, 4)
                cpl_inr = round(
                    float(metrics.get("costPerConversion", 0)) * fx_rate, 2
                )
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

    except Exception as exc:
        logger.error(
            "[AD_PROVIDERS] Google Ads fetch error (fail-soft): %s", exc, exc_info=True
        )
        return _not_configured_rows("google")


# ---------------------------------------------------------------------------
# Meta (Facebook) Ads provider
# ---------------------------------------------------------------------------

_META_FIELDS = (
    "campaign_id,campaign_name,spend,impressions,clicks,actions,cost_per_action_type"
)

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
        import httpx

        url = f"https://graph.facebook.com/v20.0/{ad_account_id}/insights"
        params: Dict[str, str] = {
            "fields": _META_FIELDS,
            "time_range": (
                f'{{"since":"{date_range.get("from", "")}","until":"{date_range.get("to", "")}"}}'
            ),
            "level": "campaign",
            "limit": "50",
            "access_token": access_token,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(
                "[AD_PROVIDERS] Meta Ads API call failed: %s %s",
                resp.status_code,
                resp.text[:300],
            )
            return _not_configured_rows("meta")

        data = resp.json().get("data", [])
        rows: List[CampaignRow] = []
        for item in data:
            spend = round(float(item.get("spend", 0)), 2)
            impressions = int(item.get("impressions", 0))
            clicks = int(item.get("clicks", 0))

            # Extract lead conversions from the nested actions list
            leads = 0
            for action in item.get("actions") or []:
                if action.get("action_type", "") in _META_LEAD_ACTION_TYPES:
                    leads += int(float(action.get("value", 0)))

            ctr = round((clicks / impressions * 100) if impressions > 0 else 0.0, 4)
            cpl = round((spend / leads) if leads > 0 else 0.0, 2)

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

    except Exception as exc:
        logger.error(
            "[AD_PROVIDERS] Meta Ads fetch error (fail-soft): %s", exc, exc_info=True
        )
        return _not_configured_rows("meta")


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

    google_rows: List[CampaignRow] = []
    meta_rows: List[CampaignRow] = []

    if channel in (None, "google"):
        try:
            google_rows = await google_ads_performance(db, date_range)
        except Exception as exc:
            logger.error(
                "[AD_PROVIDERS] Unexpected error from google_ads_performance: %s", exc
            )
            google_rows = _not_configured_rows("google")

    if channel in (None, "meta"):
        try:
            meta_rows = await meta_ads_performance(db, date_range)
        except Exception as exc:
            logger.error(
                "[AD_PROVIDERS] Unexpected error from meta_ads_performance: %s", exc
            )
            meta_rows = _not_configured_rows("meta")

    def _is_configured(rows: List[CampaignRow]) -> bool:
        return bool(rows) and rows[0].status not in ("not_configured",)

    result.google_configured = _is_configured(google_rows)
    result.meta_configured = _is_configured(meta_rows)

    # Exclude the sentinel "__not_configured__" placeholder from the data table
    result.rows = [
        r
        for r in (google_rows + meta_rows)
        if r.campaign_id != "__not_configured__"
    ]

    if not result.google_configured and not result.meta_configured:
        result.note = (
            "No ad account credentials configured. "
            "Add a 'google_ads' or 'meta_ads' integration doc to the "
            "integrations collection to activate live data."
        )

    _compute_totals(result)
    return result
