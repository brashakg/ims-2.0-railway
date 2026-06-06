"""SEC-OPENAPI-PUBLIC: API docs/schema must be OFF in production (Railway /
ENVIRONMENT=production) unless EXPOSE_API_DOCS overrides; ON in dev/test.
SEC-WEBHOOK-WHATSAPP-FAILOPEN: the WhatsApp inbound POST must FAIL CLOSED (ack
200, no processing/dispatch) when no app secret is configured."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# --------------------------- SEC-OPENAPI ---------------------------
_DOC_ENVS = ("ENVIRONMENT", "RAILWAY_ENVIRONMENT", "RAILWAY_DEPLOYMENT_ID", "EXPOSE_API_DOCS")


def _clear(monkeypatch):
    for v in _DOC_ENVS:
        monkeypatch.delenv(v, raising=False)


def test_docs_on_in_dev(monkeypatch):
    from api.main import _should_disable_docs
    _clear(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert _should_disable_docs() is False


def test_docs_off_in_production(monkeypatch):
    from api.main import _should_disable_docs
    _clear(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _should_disable_docs() is True


def test_docs_off_on_railway(monkeypatch):
    from api.main import _should_disable_docs
    _clear(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert _should_disable_docs() is True


def test_expose_override_keeps_docs_on(monkeypatch):
    from api.main import _should_disable_docs
    _clear(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("EXPOSE_API_DOCS", "1")
    assert _should_disable_docs() is False


# --------------------------- SEC-WHATSAPP ---------------------------
_FORGED = {"entry": [{"changes": [{"value": {"messages": [
    {"from": "919100000000", "text": {"body": "hi"}, "type": "text"}
]}}]}]}


def _wa_client(monkeypatch, app_secret):
    from api.routers import webhooks as wh
    from api.services import integration_config as ic
    monkeypatch.setattr(ic, "get_whatsapp_config", lambda: {"app_secret": app_secret})
    app = FastAPI()
    app.include_router(wh.router, prefix="/webhooks")
    return TestClient(app)


def test_whatsapp_failclosed_when_no_secret(monkeypatch):
    # No secret -> sender cannot be authenticated -> ack 200 but DO NOT process.
    called = {"dispatch": False}
    from api.services import whatsapp_intents as wi

    async def _spy(**kw):
        called["dispatch"] = True
        return {}
    monkeypatch.setattr(wi, "dispatch_intent", _spy, raising=False)

    r = _wa_client(monkeypatch, "").post("/webhooks/whatsapp", json=_FORGED)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b.get("skipped") is True
    assert b.get("messages_processed") == 0
    assert b.get("skipped_reason") == "secret_not_configured"
    assert called["dispatch"] is False  # forged payload never processed


def test_whatsapp_bad_signature_rejected_when_secret_set(monkeypatch):
    r = _wa_client(monkeypatch, "topsecret").post(
        "/webhooks/whatsapp", json=_FORGED, headers={"X-Hub-Signature-256": "sha256=deadbeef"}
    )
    assert r.status_code == 401, r.text
