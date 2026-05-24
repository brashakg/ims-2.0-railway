"""
Tests for the read-only integration status reporter.

Two contracts matter most:
  1. It NEVER leaks a credential value - only KEY/field-name presence.
  2. State reflects env presence, collection docs, and DISPATCH_MODE correctly.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.services.integration_status import build_integration_status  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Mongo handle - just enough surface for the reporter.
# ---------------------------------------------------------------------------
class _FakeColl:
    def __init__(self, docs):
        # docs: dict keyed by `type`
        self._docs = docs

    def find_one(self, query):
        t = query.get("type")
        doc = self._docs.get(t)
        return dict(doc) if doc else None


class _FakeDB:
    def __init__(self, docs):
        self._docs = docs

    def get_collection(self, name):
        return _FakeColl(self._docs)


def _by_id(report, integration_id):
    for item in report["integrations"]:
        if item["id"] == integration_id:
            return item
    raise AssertionError(f"integration {integration_id} not in report")


# ---------------------------------------------------------------------------
# Env-based integrations
# ---------------------------------------------------------------------------
def test_anthropic_dormant_when_key_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = build_integration_status(db=None)
    anthropic = _by_id(report, "anthropic")
    assert anthropic["configured"] is False
    assert anthropic["state"] == "dormant"
    # env key is reported by NAME with present=False, never a value
    keys = {k["key"]: k["present"] for k in anthropic["env_keys"]}
    assert keys["ANTHROPIC_API_KEY"] is False


def test_anthropic_active_when_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRETVALUE")
    report = build_integration_status(db=None)
    anthropic = _by_id(report, "anthropic")
    assert anthropic["configured"] is True
    # read-only integration -> active as soon as configured (no dispatch gate)
    assert anthropic["state"] == "active"
    assert anthropic["dispatch_gated"] is False


def test_msg91_whatsapp_dispatch_states(monkeypatch):
    monkeypatch.setenv("MSG91_API_KEY", "key")
    monkeypatch.setenv("MSG91_WHATSAPP_INTEGRATED_NUMBER", "12345")

    monkeypatch.setenv("DISPATCH_MODE", "off")
    wa = _by_id(build_integration_status(db=None), "msg91_whatsapp")
    assert wa["configured"] is True
    assert wa["state"] == "simulated"

    monkeypatch.setenv("DISPATCH_MODE", "test")
    wa = _by_id(build_integration_status(db=None), "msg91_whatsapp")
    assert wa["state"] == "test_only"

    monkeypatch.setenv("DISPATCH_MODE", "live")
    wa = _by_id(build_integration_status(db=None), "msg91_whatsapp")
    assert wa["state"] == "live"


def test_shiprocket_env_or_collection(monkeypatch):
    monkeypatch.delenv("SHIPROCKET_EMAIL", raising=False)
    monkeypatch.delenv("SHIPROCKET_PASSWORD", raising=False)
    monkeypatch.setenv("DISPATCH_MODE", "live")

    # neither env nor collection -> dormant
    report = build_integration_status(db=None)
    assert _by_id(report, "shiprocket")["configured"] is False

    # collection-only -> configured
    db = _FakeDB({
        "shiprocket": {
            "type": "shiprocket",
            "enabled": True,
            "config": {"email": "a@b.com", "password": "pw"},
        }
    })
    report = build_integration_status(db=db)
    sr = _by_id(report, "shiprocket")
    assert sr["configured"] is True
    assert sr["state"] == "live"  # dispatch_gated + mode live

    # env-only -> configured
    monkeypatch.setenv("SHIPROCKET_EMAIL", "x@y.com")
    monkeypatch.setenv("SHIPROCKET_PASSWORD", "pw2")
    report = build_integration_status(db=None)
    assert _by_id(report, "shiprocket")["configured"] is True


# ---------------------------------------------------------------------------
# Collection-based integrations
# ---------------------------------------------------------------------------
def test_razorpay_collection_configured(monkeypatch):
    db = _FakeDB({
        "razorpay": {
            "type": "razorpay",
            "enabled": True,
            "config": {"key_id": "rzp_id", "key_secret": "rzp_secret"},
        }
    })
    rp = _by_id(build_integration_status(db=db), "razorpay")
    assert rp["configured"] is True
    assert rp["state"] == "active"  # read-only pull, not dispatch-gated
    assert set(rp["collection"]["present_keys"]) >= {"key_id", "key_secret"}
    assert rp["collection"]["missing_required"] == []


def test_collection_doc_disabled_is_not_configured():
    db = _FakeDB({
        "razorpay": {
            "type": "razorpay",
            "enabled": False,
            "config": {"key_id": "x", "key_secret": "y"},
        }
    })
    rp = _by_id(build_integration_status(db=db), "razorpay")
    assert rp["configured"] is False
    assert rp["collection"]["enabled"] is False


def test_shopify_missing_required_key():
    db = _FakeDB({
        "shopify": {
            "type": "shopify",
            "enabled": True,
            "config": {"shop_url": "store.myshopify.com"},  # missing access_token
        }
    })
    sh = _by_id(build_integration_status(db=db), "shopify")
    assert sh["configured"] is False
    assert "access_token" in sh["collection"]["missing_required"]


# ---------------------------------------------------------------------------
# Non-wired / export-only
# ---------------------------------------------------------------------------
def test_tally_export_only():
    item = _by_id(build_integration_status(db=None), "tally")
    assert item["state"] == "export_only"
    assert item["configured"] is True


def test_gst_not_wired():
    item = _by_id(build_integration_status(db=None), "gst_portal")
    assert item["state"] == "not_wired"
    assert item["configured"] is False


# ---------------------------------------------------------------------------
# The critical safety contract: no secret VALUE ever appears in the report.
# ---------------------------------------------------------------------------
def test_no_secret_values_leak(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-TOPSECRET")
    monkeypatch.setenv("MSG91_API_KEY", "msg91-TOPSECRET")
    monkeypatch.setenv("SHIPROCKET_EMAIL", "owner-TOPSECRET@example.com")
    monkeypatch.setenv("SHIPROCKET_PASSWORD", "shipPW-TOPSECRET")
    db = _FakeDB({
        "razorpay": {
            "type": "razorpay",
            "enabled": True,
            "config": {"key_id": "rzp-TOPSECRET", "key_secret": "secret-TOPSECRET"},
        }
    })
    report = build_integration_status(db=db)
    blob = json.dumps(report)
    # Field NAMES like key_id are fine; VALUES must never appear.
    assert "TOPSECRET" not in blob


def test_top_level_summary_shape(monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "test")
    report = build_integration_status(db=None)
    assert report["dispatch_mode"] == "test"
    assert "generated_at" in report
    assert set(report["summary"].keys()) == {"total", "configured", "live"}
    assert report["summary"]["total"] == len(report["integrations"])
    assert isinstance(report["test_phone_set"], bool)
