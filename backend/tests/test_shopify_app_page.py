"""The page Shopify admin frames for the "IMS 2.0 Sync" app (GET /api/v1/shopify/app).

* the app-load HMAC gate: a valid signed load for OUR shop shows the status
  panel; missing / tampered / stale / wrong-shop loads get the welcome only;
* framing: this route alone carries CSP frame-ancestors (admin.shopify.com +
  the verified shop) and no X-Frame-Options; another route keeps DENY;
* every status line fails soft to "Could not check." (amber), never a 500;
* the route is PUBLIC in rbac_policy (the RBAC doc parity test covers the doc).

No Shopify network: every reader the status calls is monkeypatched.
"""

import asyncio
import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from api.routers import online_store_push, shopify_app
from api.services import rbac_policy as rbac
from api.services import shopify_live_sync as live_sync
from api.services import shopify_push
from tests.strict_fakes import StrictDB

SECRET = "test-client-secret"
SHOP = "bokaro-better-vision.myshopify.com"
PATH = "/api/v1/shopify/app"


def _sign(params, secret=SECRET):
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _load(**over):
    """A Shopify app-load query string, signed AFTER the overrides."""
    params = {
        "embedded": "1",
        "host": "YWRtaW4uc2hvcGlmeS5jb20vc3RvcmUvYm9rYXJvLWJldHRlci12aXNpb24",
        "id_token": "eyJhbGciOiJIUzI1NiJ9.eyJzaG9wIjoieCJ9.c2ln",
        "locale": "en",
        "session": "0a1b2c3d",
        "shop": SHOP,
        "timestamp": str(int(time.time())),
    }
    params.update(over)
    return dict(params, hmac=_sign(params))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("SHOPIFY_STORE_URL", SHOP)
    shopify_app._cache.update(items=None, at=0.0, checked=None)
    yield
    shopify_app._cache.update(items=None, at=0.0, checked=None)


@pytest.fixture
def canned(monkeypatch):
    calls = []

    async def _items(db, host):
        calls.append(host)
        return [("ok", "Shopify connection", "CANNED-STATUS-LINE")]

    monkeypatch.setattr(shopify_app, "status_items", _items)
    return calls


# ---------------------------------------------------------------------------
# The HMAC gate
# ---------------------------------------------------------------------------


def test_valid_load_shows_status_and_frames_for_admin_and_the_shop(client, canned):
    r = client.get(PATH + "?" + urlencode(_load()))
    assert r.status_code == 200
    assert "Connection status" in r.text and "CANNED-STATUS-LINE" in r.text
    assert shopify_app.WELCOME in r.text
    assert 'href="https://app.uniparallel.com" target="_blank"' in r.text
    csp = r.headers["content-security-policy"]
    assert f"frame-ancestors https://admin.shopify.com https://{SHOP}" in csp
    assert "script-src" not in csp and "default-src 'none'" in csp
    assert "x-frame-options" not in r.headers
    assert canned == ["testserver"]


@pytest.mark.parametrize(
    "query",
    [
        pytest.param({}, id="no-params"),
        pytest.param({k: v for k, v in _load().items() if k != "hmac"}, id="missing-hmac"),
        pytest.param(dict(_load(), hmac="0" * 64), id="wrong-hmac"),
        pytest.param(dict(_load(), locale="fr"), id="tampered-after-signing"),
        pytest.param(_load(timestamp=str(int(time.time()) - 301)), id="stale"),
        pytest.param(_load(timestamp="soon"), id="bad-timestamp"),
        pytest.param(_load(shop="someone-else.myshopify.com"), id="wrong-shop"),
        pytest.param(dict(_load(), hmac=_sign({"shop": SHOP}, secret="other")), id="other-secret"),
    ],
)
def test_unverified_load_gets_the_welcome_only(client, canned, query):
    r = client.get(PATH + "?" + urlencode(query))
    assert r.status_code == 200
    assert shopify_app.WELCOME in r.text and "Open IMS" in r.text
    assert "Connection status" not in r.text and "CANNED-STATUS-LINE" not in r.text
    assert canned == []  # no status read at all
    csp = r.headers["content-security-policy"]
    assert csp.endswith("frame-ancestors https://admin.shopify.com")
    assert "myshopify" not in csp  # nothing about our config leaks


def _load_signed_with_empty_secret():
    """What an attacker can mint when no secret is configured: HMAC keyed by ''."""
    params = {k: v for k, v in _load().items() if k != "hmac"}
    return dict(params, hmac=_sign(params, secret=""))


@pytest.mark.parametrize("unset", [None, "", "   "], ids=["unset", "blank", "whitespace"])
def test_no_client_secret_configured_means_no_status(client, canned, monkeypatch, unset):
    if unset is None:
        monkeypatch.delenv("SHOPIFY_CLIENT_SECRET")
    else:
        monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", unset)
    r = client.get(PATH + "?" + urlencode(_load_signed_with_empty_secret()))
    assert r.status_code == 200 and "Connection status" not in r.text and canned == []


def test_an_empty_secret_never_verifies():
    """The secret-present guard in verified_shop itself (not just the route's
    pre-check): a load signed with '' must not verify against ''."""
    signed = _load_signed_with_empty_secret()
    assert shopify_app.verified_shop(signed.items(), "", SHOP, time.time()) is None


def test_an_anonymous_hit_does_not_read_the_shop_config(client, monkeypatch):
    reads = []
    monkeypatch.setattr(shopify_app, "_resolve_shop", lambda db: reads.append(1) or SHOP)
    client.get(PATH)
    client.get(PATH + "?shop=" + SHOP)
    assert reads == []
    client.get(PATH + "?" + urlencode(_load()))
    assert reads == [1]


def test_status_text_is_escaped(client, monkeypatch):
    payloads = ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '"><svg onload=alert(1)>']

    async def _items(db, host):
        return [("ok", payloads[0], payloads[1]), ("bad", payloads[2], payloads[0] + payloads[1])]

    monkeypatch.setattr(shopify_app, "status_items", _items)
    r = client.get(PATH + "?" + urlencode(_load()))
    assert r.status_code == 200 and "Connection status" in r.text
    for raw in payloads:
        assert raw not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in r.text
    assert "&quot;&gt;&lt;svg onload=alert(1)&gt;" in r.text
    assert "<script" not in r.text.lower() and "<img" not in r.text and "<svg" not in r.text


def test_verified_shop_rules():
    now = time.time()
    good = _load()
    assert shopify_app.verified_shop(good.items(), SECRET, SHOP, now) == SHOP
    assert shopify_app.verified_shop(good.items(), SECRET, SHOP.upper(), now) == SHOP
    # A repeated key (or a second hmac) is refused outright.
    assert shopify_app.verified_shop(list(good.items()) + [("locale", "en")], SECRET, SHOP, now) is None
    assert shopify_app.verified_shop(list(good.items()) + [("hmac", good["hmac"])], SECRET, SHOP, now) is None
    assert shopify_app.verified_shop(good.items(), SECRET, "", now) is None
    assert shopify_app.verified_shop(good.items(), SECRET, SHOP, now + 301) is None
    # A non-ASCII hmac must not raise (compare_digest on str would).
    assert shopify_app.verified_shop(dict(good, hmac=chr(233) * 64).items(), SECRET, SHOP, now) is None
    # A value carrying '&' is escaped, so it cannot pose as two pairs.
    smuggled = {"shop": SHOP, "timestamp": str(int(now)), "a": "1&b=2"}
    as_two = {"shop": SHOP, "timestamp": str(int(now)), "a": "1", "b": "2"}
    forged = dict(smuggled, hmac=_sign(as_two))
    assert shopify_app.verified_shop(forged.items(), SECRET, SHOP, now) is None


# ---------------------------------------------------------------------------
# Framing headers: this route only
# ---------------------------------------------------------------------------


def test_other_routes_keep_their_framing_protection(client):
    r = client.get("/api/v1/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors" not in r.headers["content-security-policy"]
    assert r.headers["content-security-policy"].startswith("default-src 'self'")


def test_a_non_page_response_on_the_path_stays_deny(client):
    r = client.post(PATH)  # 405: FastAPI's answer, no frame-ancestors set
    assert r.status_code == 405
    assert r.headers["x-frame-options"] == "DENY"


# ---------------------------------------------------------------------------
# The status lines
# ---------------------------------------------------------------------------

STORES = [
    {"store_id": "S1", "store_code": "BV-BOK-01", "store_name": "Bokaro", "shopify_location_id": "gid://shopify/Location/1"},
    {"store_id": "S2", "store_code": "BV-DHN-01", "store_name": "Dhanbad", "shopify_location_id": "gid://shopify/Location/2"},
]
ALL_TOPICS = [shopify_push.webhooks._topic_enum(t) for t in shopify_push.CUTOVER_WEBHOOK_TOPICS]


@pytest.fixture
def happy(monkeypatch):
    """Every reader answers healthy; returns the StrictDB."""
    db = StrictDB()
    db.seed("webhook_inbox", [
        {"vendor": "shopify", "signature_verified": True, "received_at": "2026-10-18T03:00:00+00:00"},
        {"vendor": "shopify", "signature_verified": False, "received_at": "2026-10-18T05:00:00+00:00"},
    ])
    monkeypatch.setattr(shopify_app, "resolve_shopify_credentials",
                        lambda db, storefront_id="BV": {"shop_url": SHOP, "access_token": "t", "source": "oauth"})
    monkeypatch.setattr(shopify_push, "push_mode_status", lambda db: {
        "is_live": True, "writes_enabled": True, "dispatch_mode": "live",
        "stores_total": 2, "stores": STORES,
    })
    monkeypatch.setattr(shopify_push, "last_location_verdict",
                        lambda db, mapped: {"read": True, "dead": [], "stray": []})
    monkeypatch.setattr(live_sync, "live_sync_config", lambda: {"enabled": True})
    monkeypatch.setattr(live_sync, "last_run", lambda db, trigger=None: {
        "trigger": trigger, "status": "done", "mode": "LIVE", "finished_at": "2026-10-18T03:30:00+00:00",
        "pushed_ok": 4, "failed": 0, "awaiting_first_publish": 2, "stock": {"ok": True},
    })

    async def _webhooks(db, base, topics=None, apply=False, delete_conflicts=False):
        assert apply is False and delete_conflicts is False  # a read, never a mutation
        return {"ok": True, "topics": ALL_TOPICS, "existing": [
            {"topic": t, "callback_url": "https://ims-20-railway-production.up.railway.app/api/v1/webhooks/shopify"}
            for t in ALL_TOPICS
        ]}

    monkeypatch.setattr(shopify_push, "register_webhooks", _webhooks)
    monkeypatch.setattr(online_store_push, "_product_counts", lambda db: {"staged": 5, "pushed": 3, "pending": 1})

    async def _graphql(db, query, variables):
        assert "productsCount" in query and "mutation" not in query
        return {"data": {"productsCount": {"count": 3}}}

    monkeypatch.setattr(shopify_push, "_graphql", _graphql)
    return db


def _items(db):
    return {label: (level, line) for level, label, line in asyncio.run(shopify_app.status_items(db, "api.example"))}


def test_all_healthy_reads_green_in_plain_english(happy):
    items = _items(happy)
    assert len(items) == 6
    assert {lvl for lvl, _ in items.values()} == {"ok"}, items
    assert "2 of 2 shops linked: Bokaro, Dhanbad." in items["Shops linked to Shopify locations"][1]
    sync = items["Last scheduled sync"][1]
    assert "18 Oct 2026, 09:00 AM IST: 4 updated, 0 failed; 2 awaiting their first publish." in sync
    notes = items["Notifications from Shopify"][1]
    assert f"{len(ALL_TOPICS)} of {len(ALL_TOPICS)} notification types" in notes
    assert "08:30 AM IST" in notes  # the signature-verified row, not the later unverified one
    assert items["Products on the website"][1].startswith("Shopify has 3 products. IMS has 5 set up")


def test_the_sync_line_reads_the_scheduled_runs(happy, monkeypatch):
    seen = []
    monkeypatch.setattr(live_sync, "last_run", lambda db, trigger=None: seen.append(trigger))
    assert _items(happy)["Last scheduled sync"] == ("warn", "No scheduled sync has run yet.")
    assert seen == ["scheduled"]


def test_problems_turn_red(happy, monkeypatch):
    monkeypatch.setattr(shopify_push, "last_location_verdict", lambda db, mapped: {
        "read": True, "stray": [],
        "dead": [{"store_id": "S2", "reason": "deactivated in Shopify"}],
    })
    monkeypatch.setattr(live_sync, "last_run", lambda db, trigger=None: {
        "status": "done", "mode": "LIVE", "finished_at": "2026-10-18T03:30:00+00:00",
        "pushed_ok": 1, "failed": 2, "awaiting_first_publish": 0, "stock": {"ok": True},
    })
    items = _items(happy)
    assert items["Shops linked to Shopify locations"][0] == "bad"
    assert "Cannot sell online: Dhanbad (deactivated in Shopify)." in items["Shops linked to Shopify locations"][1]
    assert items["Last scheduled sync"][0] == "bad"


def test_a_notification_sent_elsewhere_does_not_count(happy, monkeypatch):
    async def _webhooks(db, base, topics=None, apply=False, delete_conflicts=False):
        existing = [{"topic": t, "callback_url": "https://api.uniparallel.com/api/v1/webhooks/shopify"}
                    for t in ALL_TOPICS[1:]]
        existing.append({"topic": ALL_TOPICS[0], "callback_url": "https://old-bvi.example/api/webhooks"})
        return {"ok": True, "topics": ALL_TOPICS, "existing": existing}

    monkeypatch.setattr(shopify_push, "register_webhooks", _webhooks)
    level, line = _items(happy)["Notifications from Shopify"]
    assert level == "warn"
    assert line.startswith(f"{len(ALL_TOPICS) - 1} of {len(ALL_TOPICS)} notification types are sent to IMS.")


def test_preview_mode_reads_amber_and_never_calls_shopify(happy, monkeypatch):
    monkeypatch.setattr(shopify_push, "push_mode_status", lambda db: {
        "is_live": False, "writes_enabled": False, "dispatch_mode": "live",
        "stores_total": 2, "stores": STORES,
    })

    async def _no_network(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("preview must not read Shopify")

    monkeypatch.setattr(shopify_push, "_graphql", _no_network)
    monkeypatch.setattr(shopify_push, "register_webhooks", _no_network)
    items = _items(happy)
    assert items["IMS writes to Shopify"] == (
        "warn", "PREVIEW only. Nothing is sent to Shopify (the IMS writes switch is off).")
    assert items["Products on the website"][0] == "warn"
    assert items["Notifications from Shopify"][0] == "warn"


def _boom(*a, **k):
    raise RuntimeError("reader down")


async def _aboom(*a, **k):
    raise RuntimeError("reader down")


@pytest.mark.parametrize(
    "target, attr, fake, labels",
    [
        (shopify_app, "resolve_shopify_credentials", _boom, ["Shopify connection"]),
        (shopify_push, "push_mode_status", _boom, ["IMS writes to Shopify", "Shops linked to Shopify locations",
                                                   "Notifications from Shopify", "Products on the website"]),
        (shopify_push, "last_location_verdict", _boom, ["Shops linked to Shopify locations"]),
        (live_sync, "last_run", _boom, ["Last scheduled sync"]),
        (shopify_push, "register_webhooks", _aboom, ["Notifications from Shopify"]),
        (online_store_push, "_product_counts", _boom, ["Products on the website"]),
        (shopify_push, "_graphql", _aboom, ["Products on the website"]),
    ],
)
def test_each_status_line_fails_soft(happy, monkeypatch, target, attr, fake, labels):
    monkeypatch.setattr(target, attr, fake)
    items = _items(happy)
    assert len(items) == 6
    for label in labels:
        assert items[label] == ("warn", "Could not check."), label
    assert all(lvl == "ok" for label, (lvl, _) in items.items() if label not in labels)


def test_no_database_still_renders_a_page(client, monkeypatch):
    monkeypatch.setattr(live_sync, "connected_db", lambda: None)
    monkeypatch.setattr(shopify_app, "resolve_shopify_credentials", lambda db, storefront_id="BV": None)
    monkeypatch.setattr(shopify_push, "push_mode_status", _boom)
    r = client.get(PATH + "?" + urlencode(_load()))
    assert r.status_code == 200
    assert "Connection status" in r.text and "Could not check." in r.text


def test_status_is_cached_for_a_minute(client, canned):
    q = PATH + "?" + urlencode(_load())
    client.get(q)
    client.get(q)
    assert len(canned) == 1
    shopify_app._cache["at"] -= shopify_app._CACHE_SECONDS
    client.get(q)
    assert len(canned) == 2


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_route_is_public_in_the_rbac_policy():
    assert rbac.policy_for("GET", PATH)["allowed"] == rbac.PUBLIC
    assert rbac.check_access("GET", PATH, []) is True
