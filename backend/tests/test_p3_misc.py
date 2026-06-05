"""
IMS 2.0 - P3 misc backlog items: OPS-14 / SEC-5 backend sanity tests
======================================================================
OPS-14: GET /settings/printers/available must return detection_supported=False
        and a guidance message instead of a silent empty list.
SEC-5:  Backend integration models accept snake_case field names (Pydantic
        rejects any camelCase payload -> the FE must send snake_case).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-key-p3-misc")

# ---------------------------------------------------------------------------
# OPS-14 -- available printers endpoint
# ---------------------------------------------------------------------------

def test_available_printers_returns_detection_supported_false():
    from api.routers.settings import list_available_printers

    user = {"user_id": "u1", "roles": ["SUPERADMIN"]}
    result = asyncio.run(list_available_printers(user))

    assert result["printers"] == [], "printers list must be empty (server has no LAN access)"
    assert result["detection_supported"] is False, "detection_supported must be False"
    assert result["detection_method"] == "qz_tray", "must document QZ Tray as the method"
    assert "message" in result and result["message"], "must carry a guidance message"


def test_available_printers_response_includes_qz_tray_in_message():
    from api.routers.settings import list_available_printers

    user = {"user_id": "u1", "roles": ["ADMIN"]}
    result = asyncio.run(list_available_printers(user))

    # Message must mention QZ Tray so the settings UI can render the install prompt.
    assert "QZ Tray" in result["message"] or "qz.io" in result["message"]


# ---------------------------------------------------------------------------
# SEC-5 -- backend Pydantic models must use snake_case field names
# (validates that the FE fix sending snake_case is the correct contract)
# ---------------------------------------------------------------------------

def test_razorpay_config_accepts_snake_case():
    from api.routers.admin import RazorpayConfig

    cfg = RazorpayConfig(key_id="rzp_live_abc", key_secret="secret", enabled=True)
    assert cfg.key_id == "rzp_live_abc"
    assert cfg.key_secret == "secret"


def test_razorpay_config_rejects_camel_case():
    from pydantic import ValidationError
    from api.routers.admin import RazorpayConfig

    with pytest.raises(ValidationError):
        # camelCase fields -> Pydantic v2 strict snake_case models must reject these
        RazorpayConfig(**{"keyId": "rzp_live_abc", "keySecret": "secret", "enabled": True})


def test_whatsapp_config_accepts_snake_case():
    from api.routers.admin import WhatsappConfig

    cfg = WhatsappConfig(
        api_key="wapi_key", phone_number_id="1234", business_id="biz1", enabled=True
    )
    assert cfg.api_key == "wapi_key"
    assert cfg.phone_number_id == "1234"
    assert cfg.business_id == "biz1"


def test_tally_config_accepts_snake_case():
    from api.routers.admin import TallyConfig

    cfg = TallyConfig(server_url="http://tally:9000", company_name="BV", enabled=True)
    assert cfg.server_url == "http://tally:9000"
    assert cfg.company_name == "BV"
    assert cfg.sync_interval == 60  # default


def test_shopify_config_accepts_snake_case():
    from api.routers.admin import ShopifyConfig

    cfg = ShopifyConfig(
        shop_url="bv.myshopify.com",
        api_key="apikey",
        api_secret="apisecret",
        access_token="token",
        enabled=True,
    )
    assert cfg.shop_url == "bv.myshopify.com"
    assert cfg.access_token == "token"


def test_sms_config_accepts_snake_case():
    from api.routers.admin import SmsConfig

    cfg = SmsConfig(provider="MSG91", api_key="sms_key", sender_id="BVOPTL", enabled=True)
    assert cfg.api_key == "sms_key"
    assert cfg.sender_id == "BVOPTL"
