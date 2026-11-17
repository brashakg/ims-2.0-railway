"""
Audit L1-F33: Google Ads reporting must use a served API version, read the
documented reply, and say honestly whether a channel is not connected, failed
to answer, or simply has no campaigns.

Reproduction (audit lane1/ad-provider-contract-probe.py): the adapter called
the retired /v17; a documented reply (int64 costMicros as a JSON string)
produced zero rows; a timeout and a valid empty report both said "No ad
account credentials configured"; a second page of Meta insights was ignored.

Every HTTP call here goes to a stub client: no Google, Meta or other network
request is made, and only synthetic config values are used.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_audit_b04_ad_providers.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import httpx  # noqa: E402
import pytest  # noqa: E402

from api.services import ad_providers  # noqa: E402

_CFG = {
    "customer_id": "123-456-7890",
    "developer_token": "SYNTHETIC",
    "client_id": "SYNTHETIC",
    "client_secret": "SYNTHETIC",
    "refresh_token": "SYNTHETIC",
    "ad_account_id": "111",
    "access_token": "SYNTHETIC",
}
_REQ = httpx.Request("GET", "https://stub.invalid/")


def _google_reply(cost_micros="100000000", currency="INR", conversions=2.0, **metrics):
    result = {
        "campaign": {"id": "AUDIT-CAMPAIGN", "name": "Synthetic only"},
        "metrics": {
            "costMicros": cost_micros,
            "impressions": "1000",
            "clicks": "10",
            "conversions": conversions,
            "ctr": 0.01,
            **metrics,
        },
    }
    if currency is not None:
        result["customer"] = {"currencyCode": currency}
    return [{"results": [result]}]


@pytest.fixture
def stub(monkeypatch):
    """Stub transport + synthetic config. `stub.google` / `stub.meta` are
    callables (url, kwargs) -> httpx.Response, or raise."""

    class Stub:
        calls = []
        cfg = dict(_CFG)
        token_status = 200
        google = staticmethod(lambda url, kw: httpx.Response(200, request=_REQ, json=_google_reply()))
        meta = staticmethod(lambda url, kw: httpx.Response(200, request=_REQ, json={"data": []}))

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            Stub.calls.append(("POST", url, kwargs))
            if "oauth2.googleapis.com" in url:
                return httpx.Response(
                    Stub.token_status, request=_REQ, json={"access_token": "SYNTHETIC"}
                )
            return Stub.google(url, kwargs)

        async def get(self, url, **kwargs):
            Stub.calls.append(("GET", url, kwargs))
            return Stub.meta(url, kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        ad_providers, "_load_integration_config", lambda db, kind: dict(Stub.cfg)
    )
    return Stub


def _fetch(channel):
    return asyncio.run(
        ad_providers.fetch_ad_performance(None, "2026-11-01", "2026-11-04", channel=channel)
    )


def _report_urls(stub):
    return [url for method, url, _kw in stub.calls if "googleads.googleapis.com" in url]


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def test_google_report_does_not_call_the_retired_v17(stub):
    _fetch("google")
    (url,) = _report_urls(stub)
    assert "/v17/" not in url
    assert url == (
        "https://googleads.googleapis.com/"
        f"{ad_providers.GOOGLE_ADS_API_VERSION}/customers/1234567890/googleAds:searchStream"
    )
    assert ad_providers.GOOGLE_ADS_API_VERSION == "v26"


def test_a_stored_api_version_does_not_move_the_pin(stub):
    """The version is a code pin. A value on the integrations doc is not a
    setting and must not steer the request URL."""
    stub.cfg["api_version"] = "v17"
    _fetch("google")
    assert f"/{ad_providers.GOOGLE_ADS_API_VERSION}/customers/" in _report_urls(stub)[0]


# ---------------------------------------------------------------------------
# The documented reply shape
# ---------------------------------------------------------------------------


def test_documented_string_micros_reply_yields_the_row(stub):
    """int64 fields arrive as JSON strings (Google Ads REST JSON mapping)."""
    r = _fetch("google")
    assert r.google_configured is True and r.google_error == ""
    (row,) = r.rows
    assert row.campaign_id == "AUDIT-CAMPAIGN" and row.status == "ok"
    assert row.spend == 100.0          # 100,000,000 micros of an INR account
    assert row.currency == "INR"
    assert row.impressions == 1000 and row.clicks == 10 and row.conversions == 2
    assert row.ctr == 1.0
    assert row.cpl == 50.0             # spend / conversions, derived here
    assert r.total_spend == 100.0


def test_numeric_micros_reply_reads_the_same(stub):
    stub.google = lambda url, kw: httpx.Response(
        200, request=_REQ, json=_google_reply(cost_micros=100000000)
    )
    assert _fetch("google").rows[0].spend == 100.0


def test_fractional_conversions_are_one_figure_everywhere(stub):
    """Decision D10: the Leads column, the cost per lead and the totals come
    from the SAME number. (The row used to show 2 leads beside a cost per
    lead worked out on 2.5.)"""
    stub.google = lambda url, kw: httpx.Response(
        200, request=_REQ, json=_google_reply(conversions=2.5)
    )
    r = _fetch("google")
    (row,) = r.rows
    assert row.conversions == 2.5
    assert row.cpl == 40.0             # 100 / 2.5
    assert r.total_conversions == 2.5 and r.total_cpl == 40.0


@pytest.mark.parametrize("conversions", [0, 0.004, 0.4, 0.5, 1, 2.5, "3", 7.0])
def test_a_row_never_shows_no_leads_beside_a_cost_per_lead(stub, conversions):
    """The reviewers' complaint, as an invariant: cost per lead is non-zero
    exactly when the leads shown are, and it is spend / the leads shown."""
    stub.google = lambda url, kw: httpx.Response(
        200, request=_REQ, json=_google_reply(conversions=conversions)
    )
    r = _fetch("google")
    (row,) = r.rows
    assert (row.cpl > 0) == (row.conversions > 0), (row.conversions, row.cpl)
    if row.conversions > 0:
        assert row.cpl == round(row.spend / row.conversions, 2)
    assert r.total_conversions == row.conversions and r.total_cpl == row.cpl


def test_under_half_a_lead_is_shown_as_what_google_reported(stub):
    stub.google = lambda url, kw: httpx.Response(
        200, request=_REQ, json=_google_reply(conversions=0.4)
    )
    (row,) = _fetch("google").rows
    assert (row.conversions, row.cpl) == (0.4, 250.0)   # was (0, 250.0)


def test_the_route_serves_fractional_leads(client, auth_headers, stub):
    stub.google = lambda url, kw: httpx.Response(
        200, request=_REQ, json=_google_reply(conversions=2.5)
    )
    resp = client.get(
        "/api/v1/marketing/ad-performance?from=2026-11-01&to=2026-11-04&channel=google",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows"][0]["conversions"] == 2.5 and body["rows"][0]["cpl"] == 40.0
    assert body["total_conversions"] == 2.5 and body["total_cpl"] == 40.0


def test_the_report_asks_google_for_the_account_currency(stub):
    _fetch("google")
    query = next(kw["json"]["query"] for _m, url, kw in stub.calls if "googleads" in url)
    assert "customer.currency_code" in query


def test_inr_account_spend_is_not_multiplied_by_a_dollar_rate(stub):
    stub.cfg["usd_inr_rate"] = 84.0
    assert _fetch("google").rows[0].spend == 100.0


def test_usd_account_spend_uses_the_configured_rate(stub):
    stub.cfg["usd_inr_rate"] = 90.0
    stub.google = lambda url, kw: httpx.Response(
        200, request=_REQ, json=_google_reply(currency="USD")
    )
    (row,) = _fetch("google").rows
    assert row.spend == 9000.0 and row.currency == "INR"


@pytest.mark.parametrize("currency", ["EUR", None])
def test_an_unknown_account_currency_is_an_error_not_a_guessed_number(stub, currency):
    stub.google = lambda url, kw: httpx.Response(
        200, request=_REQ, json=_google_reply(currency=currency)
    )
    r = _fetch("google")
    assert r.rows == [] and r.total_spend == 0.0
    assert r.google_configured is True
    assert "currency" in r.google_error.lower()


# ---------------------------------------------------------------------------
# Honest states: not connected / failed / empty
# ---------------------------------------------------------------------------


def test_a_valid_empty_report_is_connected_with_no_campaigns(stub):
    stub.google = lambda url, kw: httpx.Response(200, request=_REQ, json=[])
    r = _fetch("google")
    assert r.rows == []
    assert r.google_configured is True
    assert r.google_error == ""
    assert "No ad account credentials configured" not in r.note


def test_a_timeout_is_a_fetch_error_not_missing_credentials(stub):
    def boom(url, kw):
        raise httpx.ReadTimeout("synthetic outage", request=_REQ)

    stub.google = boom
    r = _fetch("google")
    assert r.rows == []
    assert r.google_configured is True
    assert "did not answer" in r.google_error
    assert "No ad account credentials configured" not in r.note
    assert r.google_error in r.note


def test_rejected_sign_in_is_a_fetch_error(stub):
    stub.token_status = 400
    r = _fetch("google")
    assert r.google_configured is True
    assert "sign-in" in r.google_error and "400" in r.google_error
    assert _report_urls(stub) == [], "no report call without a token"


@pytest.mark.parametrize("status", [404, 429, 500])
def test_report_http_errors_are_fetch_errors(stub, status):
    stub.google = lambda url, kw: httpx.Response(status, request=_REQ, json={"error": {}})
    r = _fetch("google")
    assert r.google_configured is True
    assert str(status) in r.google_error


def test_no_credentials_still_reads_as_not_connected(stub):
    stub.cfg = {}
    r = _fetch(None)
    assert r.google_configured is False and r.meta_configured is False
    assert r.google_error == "" and r.meta_error == ""
    assert "No ad account credentials configured" in r.note
    assert stub.calls == []


def test_error_text_never_carries_a_credential(stub):
    stub.cfg.update(developer_token="SECRET-DEV", refresh_token="SECRET-REFRESH")
    stub.google = lambda url, kw: httpx.Response(
        403, request=_REQ, text="developer token SECRET-DEV is not approved"
    )
    r = _fetch("google")
    assert "SECRET" not in r.google_error and "SECRET" not in r.note


# ---------------------------------------------------------------------------
# Meta: every page, or an honest error
# ---------------------------------------------------------------------------


def _meta_row(n, currency="INR"):
    return {
        "campaign_id": f"META-{n}",
        "campaign_name": f"Synthetic {n}",
        "spend": "100",
        "impressions": "1000",
        "clicks": "10",
        "actions": [{"action_type": "lead", "value": "2"}],
        **({"account_currency": currency} if currency is not None else {}),
    }


def _meta_one(stub, **row):
    stub.meta = lambda url, kw: httpx.Response(
        200, request=_REQ, json={"data": [{**_meta_row(1), **row}]}
    )
    return _fetch("meta")


# Decision D10: Meta spend is in the AD ACCOUNT's currency, read from the
# report itself - the same rule as Google (INR as-is, USD at the saved rate,
# anything else an error). It used to be shown as rupees whatever it was.


def test_the_meta_report_asks_for_the_account_currency(stub):
    _fetch("meta")
    (call,) = [kw for m, _url, kw in stub.calls if m == "GET"]
    assert "account_currency" in call["params"]["fields"].split(",")


def test_meta_inr_account_spend_is_shown_as_it_is(stub):
    r = _meta_one(stub)
    (row,) = r.rows
    assert (row.spend, row.currency, row.conversions, row.cpl) == (100.0, "INR", 2, 50.0)
    assert r.meta_error == ""


def test_meta_usd_account_spend_uses_the_saved_rate(stub):
    (row,) = _meta_one(stub, account_currency="USD").rows
    assert (row.spend, row.cpl) == (8400.0, 4200.0)     # default rate 84
    stub.cfg = {**_CFG, "usd_inr_rate": 90}
    try:
        (row,) = _meta_one(stub, account_currency="usd").rows
        assert (row.spend, row.cpl, row.currency) == (9000.0, 4500.0, "INR")
    finally:
        stub.cfg = dict(_CFG)


@pytest.mark.parametrize("currency", ["AED", "EUR", "", None])
def test_meta_any_other_account_currency_is_an_error_not_a_wrong_figure(stub, currency):
    row = dict(_meta_row(1, currency=currency))
    stub.meta = lambda url, kw: httpx.Response(200, request=_REQ, json={"data": [row]})
    r = _fetch("meta")
    assert r.rows == [] and r.total_spend == 0.0
    assert r.meta_configured is True
    assert "currency" in r.meta_error and "INR and USD" in r.meta_error
    if currency:
        assert currency in r.meta_error
    else:
        assert "was not reported" in r.meta_error


def test_one_unreadable_meta_currency_fails_the_whole_channel(stub):
    """A partial total is never shown: one row in a currency that cannot be
    shown in rupees fails the channel, not just that row."""
    stub.meta = lambda url, kw: httpx.Response(
        200, request=_REQ,
        json={"data": [_meta_row(1), _meta_row(2, currency="AED")]},
    )
    r = _fetch("meta")
    assert r.rows == [] and "AED" in r.meta_error


def test_google_and_meta_word_the_currency_refusal_the_same_way(stub):
    stub.google = lambda url, kw: httpx.Response(
        200, request=_REQ, json=_google_reply(currency="AED")
    )
    row = _meta_row(1, currency="AED")
    stub.meta = lambda url, kw: httpx.Response(200, request=_REQ, json={"data": [row]})
    r = _fetch(None)
    assert r.google_error.replace("Google Ads", "X") == r.meta_error.replace("Meta Ads", "X")


def test_meta_insights_follow_every_page(stub):
    next_url = "https://graph.facebook.com/v20.0/act_111/insights?after=CURSOR"

    def meta(url, kw):
        if url == next_url:
            return httpx.Response(200, request=_REQ, json={"data": [_meta_row(2)]})
        return httpx.Response(
            200, request=_REQ,
            json={"data": [_meta_row(1)], "paging": {"next": next_url}},
        )

    stub.meta = meta
    r = _fetch("meta")
    assert [row.campaign_id for row in r.rows] == ["META-1", "META-2"]
    assert r.total_spend == 200.0
    assert [url for m, url, _kw in stub.calls if m == "GET"][1] == next_url


def test_meta_next_page_on_another_host_is_not_followed(stub):
    """The audit fixture: a next link that is not Meta's. The token must not
    be sent there, and a partial total must not be shown as the whole."""
    stub.meta = lambda url, kw: httpx.Response(
        200, request=_REQ,
        json={"data": [_meta_row(1)], "paging": {"next": "https://audit.invalid/next"}},
    )
    r = _fetch("meta")
    assert len([c for c in stub.calls if c[0] == "GET"]) == 1
    assert r.rows == [] and r.meta_configured is True
    assert r.meta_error


def test_meta_timeout_and_empty_report_are_told_apart(stub):
    r = _fetch("meta")
    assert r.meta_configured is True and r.meta_error == "" and r.rows == []

    def boom(url, kw):
        raise httpx.ConnectTimeout("synthetic outage", request=_REQ)

    stub.meta = boom
    r = _fetch("meta")
    assert r.meta_configured is True and "did not answer" in r.meta_error


def test_one_channel_failing_does_not_hide_the_other(stub):
    def boom(url, kw):
        raise httpx.ReadTimeout("synthetic outage", request=_REQ)

    stub.google = boom
    stub.meta = lambda url, kw: httpx.Response(200, request=_REQ, json={"data": [_meta_row(1)]})
    r = _fetch(None)
    assert [row.channel for row in r.rows] == ["meta"]
    assert r.google_configured is True and r.google_error
    assert r.meta_configured is True and r.meta_error == ""


def test_channel_filter_leaves_the_other_channel_unasked(stub):
    r = _fetch("google")
    assert r.meta_configured is False and r.meta_error == ""
    assert not [c for c in stub.calls if c[0] == "GET"]


# ---------------------------------------------------------------------------
# The route carries the error to the screen
# ---------------------------------------------------------------------------


def test_route_returns_the_channel_errors(client, auth_headers, monkeypatch):
    async def fake(db, from_date, to_date, channel=None):
        return ad_providers.AdPerformanceResult(
            google_configured=True,
            google_error="Google Ads did not answer in time.",
            note="Google Ads did not answer in time.",
        )

    monkeypatch.setattr(ad_providers, "fetch_ad_performance", fake)
    r = client.get(
        "/api/v1/marketing/ad-performance?from=2026-11-01&to=2026-11-04",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["google_configured"] is True
    assert body["google_error"] == "Google Ads did not answer in time."
    assert body["meta_error"] == ""
