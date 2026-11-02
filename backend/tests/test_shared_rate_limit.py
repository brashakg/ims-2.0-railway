"""Every per-key limiter goes through THE one sliding window
(api/utils/rate_limit.allow, cache seam). One check per caller that its 429
still fires; colour suggestions are covered in test_suggest_colour_name.py.
"""

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.services.cache import cache  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_windows():
    cache.delete_pattern("rl:*")
    yield
    cache.delete_pattern("rl:*")


def test_webhook_limit_fires(monkeypatch):
    from api.routers import webhooks

    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_PER_MIN", "2")
    assert webhooks._check_webhook_rate_limit("razorpay", "1.2.3.4")
    assert webhooks._check_webhook_rate_limit("razorpay", "1.2.3.4")
    assert not webhooks._check_webhook_rate_limit("razorpay", "1.2.3.4")
    assert webhooks._check_webhook_rate_limit("shopify", "1.2.3.4")  # own bucket
    assert cache.get("rl:webhook:razorpay:1.2.3.4")  # in the shared window


def test_portal_tracking_limit_fires():
    from api.routers import portal

    for _ in range(3):
        portal._check_rate("track", "9.9.9.9", 3, "tracking")
    with pytest.raises(HTTPException) as e:
        portal._check_rate("track", "9.9.9.9", 3, "tracking")
    assert e.value.status_code == 429
    portal._check_rate("otp", "9.9.9.9", 3, "verification code")  # own bucket
    assert cache.get("rl:portal_track:9.9.9.9")


def test_vendor_portal_limit_fires(monkeypatch):
    from api.routers import vendor_portal

    monkeypatch.setattr(vendor_portal, "_PORTAL_RATE_LIMIT_PER_MIN", 2)
    vendor_portal._check_portal_rate("tok-1")
    vendor_portal._check_portal_rate("tok-1")
    with pytest.raises(HTTPException) as e:
        vendor_portal._check_portal_rate("tok-1")
    assert e.value.status_code == 429
    assert cache.get("rl:vendor_portal:tok-1")


def test_marketing_notification_limit_fires(monkeypatch):
    from api.routers import marketing

    monkeypatch.setattr(marketing, "_NOTIFICATION_LIMIT", 2)
    assert marketing._check_notification_rate("u1") is None
    assert marketing._check_notification_rate("u1") is None
    assert "Rate limit exceeded" in marketing._check_notification_rate("u1")
    assert marketing._check_notification_rate("u2") is None
    assert cache.get("rl:notify:u1")
