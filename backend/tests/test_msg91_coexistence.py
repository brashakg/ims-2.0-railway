"""
MSG91 + Coexistence messaging build - the four pieces, each probed on its
requirement (2026-08-30):

  1. PER-STORE SENDER RESOLUTION - each shop's own WhatsApp number is the
     sender on the wire; unmapped/absent store context falls back to the
     single default number; an env-only single-number deployment is
     unchanged.
  2. COEXISTENCE DOUBLE-ANSWER GUARD - the inbound auto-reply defaults OFF
     (a human on the shop phone answers); after_hours follows the store's
     working hours in IST, else the 21:00-09:00 window; opt-outs are still
     RECORDED when the reply is suppressed.
  3. TEMPLATE REGISTRY AS DATA - the send door reads {flow -> template name,
     language, variable order, category} through ONE lookup; an owner-mapped
     DB row beats the code seed; an UNMAPPED flow refuses honestly in every
     DISPATCH_MODE and never sends a guessed name.
  4. PREFLIGHT - integration_status carries a messaging preflight whose rows
     are honest and name the owner's next step; no credential value leaks.

Markers are invented, distinguishable, non-secret strings.
No emoji (Windows cp1252).
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database.connection as db_connection  # noqa: E402
from agents import providers  # noqa: E402
from api.services import cred_crypto  # noqa: E402
from api.services.integration_config import (  # noqa: E402
    get_msg91_config,
    resolve_whatsapp_sender,
)

MSG91_ENV = (
    "MSG91_API_KEY",
    "MSG91_WHATSAPP_INTEGRATED_NUMBER",
    "MSG91_SMS_TEMPLATE_ID",
    "MSG91_SENDER",
    "MSG91_STORE_NUMBERS",
    "MSG_AUTO_REPLY_MODE",
    "DLT_PE_ID",
    "MSG91_DLT_PE_ID",
    "TEST_PHONE",
)


# ---------------------------------------------------------------------------
# Fakes: a Mongo stand-in serving MULTIPLE collections, and an httpx recorder
# (same pattern as test_integration_credentials_wiring, which this build
# extends).
# ---------------------------------------------------------------------------


class _FakeColl:
    def __init__(self, docs):
        self._docs = list(docs)

    def find_one(self, flt, *a, **kw):
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in flt.items() if not isinstance(v, dict)):
                if flt.get("enabled") is True and not doc.get("enabled"):
                    continue
                return dict(doc)
        return None

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self._docs]


class _FakeDB:
    is_connected = True

    def __init__(self, collections):
        # collections: {name: [docs]}
        self._colls = {n: _FakeColl(d) for n, d in collections.items()}

    def get_collection(self, name):
        return self._colls.get(name)


def _plant_db(monkeypatch, **collections):
    """Install a fake Mongo. `whatsapp_cfg=` plants the ENCRYPTED integrations
    doc exactly the way PUT /settings/integrations/whatsapp writes it."""
    cfg = collections.pop("whatsapp_cfg", None)
    colls = dict(collections)
    if cfg is not None:
        colls.setdefault("integrations", []).append(
            {
                "type": "whatsapp",
                "enabled": True,
                "config": cred_crypto.encrypt_config(cfg),
            }
        )
    fake = _FakeDB(colls)
    monkeypatch.setattr(db_connection, "get_db", lambda: fake)
    return fake


def _no_db(monkeypatch):
    monkeypatch.setattr(db_connection, "get_db", lambda: None)


class _Recorder:
    def __init__(self):
        self.calls = []

    def client(self, status_code=200, body=None):
        rec = self

        class _Resp:
            def __init__(self):
                self.status_code = status_code
                self.text = ""

            def json(self):
                return body if body is not None else {"request_id": "req-fake"}

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kw):
                rec.calls.append(dict(kw, url=url))
                return _Resp()

        return _Client


@pytest.fixture
def rec():
    return _Recorder()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in MSG91_ENV:
        monkeypatch.delenv(key, raising=False)


_STORE_MAP = "BV-RAN-01:917000000001, WO-PUN-01:917000000002"


# ===========================================================================
# Piece 1 - per-store sender resolution
# ===========================================================================


def test_store_mapped_number_is_the_sender_on_the_wire(monkeypatch, rec):
    """An order queued at BV-RAN-01 must go out FROM that shop's own number -
    that is the whole point of Coexistence."""
    _plant_db(
        monkeypatch,
        whatsapp_cfg={
            "api_key": "MARKER-KEY",
            "whatsapp_number": "MARKER-DEFAULT-NUMBER",
            "store_numbers": _STORE_MAP,
        },
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    res = asyncio.run(
        providers.send_whatsapp(
            "+919999999999",
            "your order is ready",
            template_id="ORDER_DELIVERED",
            store_id="BV-RAN-01",
        )
    )

    assert res.status == "SENT", res
    assert rec.calls[0]["json"]["integrated_number"] == "917000000001"


def test_unmapped_store_falls_back_to_the_default_number(monkeypatch, rec):
    _plant_db(
        monkeypatch,
        whatsapp_cfg={
            "api_key": "MARKER-KEY",
            "whatsapp_number": "MARKER-DEFAULT-NUMBER",
            "store_numbers": _STORE_MAP,
        },
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    res = asyncio.run(
        providers.send_whatsapp(
            "+919999999999",
            "hi",
            template_id="ORDER_DELIVERED",
            store_id="STORE-WITH-NO-MAPPING",
        )
    )

    assert res.status == "SENT", res
    assert rec.calls[0]["json"]["integrated_number"] == "MARKER-DEFAULT-NUMBER"


def test_no_store_context_uses_the_default_number(monkeypatch, rec):
    _plant_db(
        monkeypatch,
        whatsapp_cfg={
            "api_key": "MARKER-KEY",
            "whatsapp_number": "MARKER-DEFAULT-NUMBER",
            "store_numbers": _STORE_MAP,
        },
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    res = asyncio.run(
        providers.send_whatsapp("+919999999999", "hi", template_id="ORDER_DELIVERED")
    )
    assert res.status == "SENT", res
    assert rec.calls[0]["json"]["integrated_number"] == "MARKER-DEFAULT-NUMBER"


def test_env_only_single_number_deployment_is_unchanged(monkeypatch, rec):
    """A deployment that only ever set the MSG91_* env vars (today's world)
    must behave exactly as before, store context or not."""
    _no_db(monkeypatch)
    monkeypatch.setenv("MSG91_API_KEY", "MARKER-ENV-KEY")
    monkeypatch.setenv("MSG91_WHATSAPP_INTEGRATED_NUMBER", "MARKER-ENV-NUMBER")
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    res = asyncio.run(
        providers.send_whatsapp(
            "+919999999999", "hi", template_id="ORDER_DELIVERED", store_id="BV-RAN-01"
        )
    )
    assert res.status == "SENT", res
    assert rec.calls[0]["json"]["integrated_number"] == "MARKER-ENV-NUMBER"
    assert get_msg91_config()["store_numbers"] == {}


def test_env_store_map_also_resolves(monkeypatch):
    """MSG91_STORE_NUMBERS is the env fallback for the map itself."""
    _no_db(monkeypatch)
    monkeypatch.setenv("MSG91_WHATSAPP_INTEGRATED_NUMBER", "MARKER-ENV-NUMBER")
    monkeypatch.setenv("MSG91_STORE_NUMBERS", _STORE_MAP)
    assert resolve_whatsapp_sender("WO-PUN-01") == "917000000002"
    assert resolve_whatsapp_sender("UNMAPPED") == "MARKER-ENV-NUMBER"
    assert resolve_whatsapp_sender(None) == "MARKER-ENV-NUMBER"


# ===========================================================================
# Piece 3 - template registry as data
# ===========================================================================


def test_unmapped_flow_refuses_in_every_mode_and_names_the_flow(monkeypatch, rec):
    """A flow with no template mapping must NEVER send a guessed name - not in
    live mode, not even as a SIMULATED success in off mode - and the error
    must name the flow so the queued row says why."""
    _plant_db(
        monkeypatch,
        whatsapp_cfg={"api_key": "MARKER-KEY", "whatsapp_number": "MARKER-NUM"},
    )
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    for mode in ("off", "live"):
        monkeypatch.setattr(providers, "DISPATCH_MODE", mode)
        res = asyncio.run(
            providers.send_whatsapp(
                "+919999999999", "hi", template_id="NO_SUCH_FLOW_XYZ"
            )
        )
        assert res.ok is False, (mode, res)
        assert res.status == "FAILED", (mode, res)
        assert "NO_SUCH_FLOW_XYZ" in (res.error or ""), (mode, res)
    assert rec.calls == [], "a guessed template name reached the wire"


def test_no_flow_key_at_all_also_refuses(monkeypatch, rec):
    _no_db(monkeypatch)
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())
    res = asyncio.run(providers.send_whatsapp("+919999999999", "hi"))
    assert res.status == "FAILED"
    assert "no WhatsApp template mapped" in (res.error or "")
    assert rec.calls == []


def test_owner_mapped_template_name_beats_the_seed(monkeypatch, rec):
    """The registry is DATA: a wa_template_name saved on the flow's
    notification_templates doc is the name on the wire, with its language."""
    _plant_db(
        monkeypatch,
        whatsapp_cfg={"api_key": "MARKER-KEY", "whatsapp_number": "MARKER-NUM"},
        notification_templates=[
            {
                "template_id": "ORDER_DELIVERED",
                "is_enabled": True,
                "wa_template_name": "bv_order_delivered_v2",
                "wa_language": "en_US",
                "wa_category": "utility",
            }
        ],
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    res = asyncio.run(
        providers.send_whatsapp("+919999999999", "hi", template_id="ORDER_DELIVERED")
    )
    assert res.status == "SENT", res
    tpl = rec.calls[0]["json"]["payload"]["template"]
    assert tpl["name"] == "bv_order_delivered_v2"
    assert tpl["language"]["code"] == "en_US"


def test_variable_order_comes_from_the_registry(monkeypatch, rec):
    """When the caller supplies the registry's variables, the payload carries
    body_1..body_n in REGISTRY order, not dict order."""
    _plant_db(
        monkeypatch,
        whatsapp_cfg={"api_key": "MARKER-KEY", "whatsapp_number": "MARKER-NUM"},
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "live")
    monkeypatch.setattr(providers.httpx, "AsyncClient", rec.client())

    # ORDER_DELIVERED registry order: customer_name, order_number, store_name.
    res = asyncio.run(
        providers.send_whatsapp(
            "+919999999999",
            "fallback text",
            template_id="ORDER_DELIVERED",
            variables={
                "store_name": "MARKER-STORE",
                "customer_name": "MARKER-NAME",
                "order_number": "MARKER-ORDER",
            },
        )
    )
    assert res.status == "SENT", res
    comps = rec.calls[0]["json"]["payload"]["template"]["to_and_components"][0][
        "components"
    ]
    assert comps["body_1"]["value"] == "MARKER-NAME"
    assert comps["body_2"]["value"] == "MARKER-ORDER"
    assert comps["body_3"]["value"] == "MARKER-STORE"


def test_simulated_send_proves_the_payload_shape(monkeypatch):
    """DISPATCH_MODE=off must still resolve template AND per-store sender and
    hand the built shape back - the whole build is testable dark."""
    _plant_db(
        monkeypatch,
        whatsapp_cfg={
            "api_key": "MARKER-KEY",
            "whatsapp_number": "MARKER-DEFAULT-NUMBER",
            "store_numbers": _STORE_MAP,
        },
    )
    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")

    res = asyncio.run(
        providers.send_whatsapp(
            "+919999999999",
            "ready for pickup",
            template_id="ORDER_DELIVERED",
            store_id="WO-PUN-01",
        )
    )
    assert res.status == "SIMULATED", res
    assert res.meta is not None, "SIMULATED path returned no payload proof"
    assert res.meta["template_name"] == "ORDER_DELIVERED"
    assert res.meta["language"] == "en"
    assert res.meta["category"] == "utility"
    assert res.meta["integrated_number"] == "917000000002"
    assert res.meta["components"]["body_1"]["value"] == "ready for pickup"


def test_every_queued_flow_key_is_seeded():
    """The enumerated flow keys that reach the send door today MUST resolve,
    or a currently-working flow starts refusing. Dies if a seed row is
    removed."""
    from api.services.notification_templates import resolve_wa_template

    flows = [
        "PRESCRIPTION_EXPIRY",
        "BIRTHDAY_WISH",
        "ANNUAL_CHECKUP_REMINDER",
        "ORDER_DELIVERED",
        "GOOGLE_REVIEW_REQUEST",
        "WALKOUT_RECOVERY",
        "REFERRAL_INVITE",
        "NPS_SURVEY",
        "CL_REORDER_REMINDER",
        "RX_PORTAL_OTP",
        "POOL_REDEEM_OTP",
        "repair_ready",
        "WORKSHOP_READY",
        "TASK_ESCALATION",
        "WA_INTENT_REPLY",
    ]
    for flow in flows:
        tpl = resolve_wa_template(flow)
        assert tpl is not None, f"flow {flow} lost its registry seed"
        assert tpl["template_name"], flow
        assert tpl["category"] in ("utility", "marketing", "auth"), flow


def test_drain_passes_the_rows_store_id(monkeypatch):
    """MEGAPHONE's drain is the biggest send call site: every row's store_id
    must reach the send door, or per-store sender resolution is dead on the
    highest-volume path."""
    from agents.implementations import megaphone as mega_mod

    seen = []

    async def _spy(phone, message, *, template_id=None, store_id=None, **kw):
        seen.append({"phone": phone, "template_id": template_id, "store_id": store_id})
        return providers.DispatchResult(ok=True, status="SIMULATED", channel="whatsapp")

    monkeypatch.setattr(mega_mod, "send_whatsapp", _spy)

    class _NotifColl:
        def __init__(self, rows):
            self.rows = rows
            self.updates = []

        def find(self, flt):
            class _Cur:
                def __init__(self, rows):
                    self._rows = rows

                def limit(self, n):
                    return self._rows[:n]

            return _Cur(self.rows)

        def update_one(self, flt, update):
            self.updates.append((flt, update))

    rows = [
        {
            "notification_id": "N1",
            "status": "PENDING",
            "channel": "WHATSAPP",
            "customer_phone": "919000000001",
            "message": "m1",
            "template_id": "ORDER_DELIVERED",
            "store_id": "BV-RAN-01",
            "scheduled_for": None,
        },
        {
            "notification_id": "N2",
            "status": "PENDING",
            "channel": "WHATSAPP",
            "customer_phone": "919000000002",
            "message": "m2",
            "template_id": "BIRTHDAY_WISH",
            "store_id": "WO-PUN-01",
            "scheduled_for": None,
        },
    ]
    agent = mega_mod.MegaphoneAgent(db=None)
    stats = asyncio.run(agent._drain_pending(_NotifColl(rows)))

    assert stats["attempted"] == 2, stats
    assert [s["store_id"] for s in seen] == ["BV-RAN-01", "WO-PUN-01"], seen


# ===========================================================================
# Piece 2 - Coexistence double-answer guard
# ===========================================================================


def test_auto_reply_defaults_off(monkeypatch):
    """Fresh deploy, no policy set anywhere: IMS must NOT answer next to the
    human on the shop phone."""
    _no_db(monkeypatch)
    from api.services.whatsapp_intents import auto_reply_allowed

    allowed, why = auto_reply_allowed("BV-RAN-01")
    assert allowed is False, why
    assert "off" in why


def test_auto_reply_always_mode_allows(monkeypatch):
    _no_db(monkeypatch)
    monkeypatch.setenv("MSG_AUTO_REPLY_MODE", "always")
    from api.services.whatsapp_intents import auto_reply_allowed

    allowed, why = auto_reply_allowed("BV-RAN-01")
    assert allowed is True, why


def test_after_hours_respects_the_stores_working_hours(monkeypatch):
    """Store open 10:00-20:00 IST: 15:00 is a human's shift (suppress);
    22:00 the shop phone is asleep (reply)."""
    import api.services.whatsapp_intents as wi

    monkeypatch.setenv("MSG_AUTO_REPLY_MODE", "after_hours")
    fake = _FakeDB(
        {"stores": [{"store_id": "BV-RAN-01", "working_hours": "10:00-20:00"}]}
    )
    monkeypatch.setattr(wi, "_get_db", lambda: fake)

    open_now = datetime(2026, 8, 30, 15, 0)  # naive == IST wall clock
    closed_now = datetime(2026, 8, 30, 22, 0)

    allowed, why = wi.auto_reply_allowed("BV-RAN-01", now=open_now)
    assert allowed is False, why
    assert "human" in why

    allowed, why = wi.auto_reply_allowed("BV-RAN-01", now=closed_now)
    assert allowed is True, why


def test_after_hours_falls_back_to_the_ist_quiet_window(monkeypatch):
    """No store hours stored anywhere -> the sane 21:00-09:00 IST window."""
    import api.services.whatsapp_intents as wi

    monkeypatch.setenv("MSG_AUTO_REPLY_MODE", "after_hours")
    monkeypatch.setattr(wi, "_get_db", lambda: None)

    assert wi.auto_reply_allowed("X", now=datetime(2026, 8, 30, 15, 0))[0] is False
    assert wi.auto_reply_allowed("X", now=datetime(2026, 8, 30, 23, 0))[0] is True
    assert wi.auto_reply_allowed("X", now=datetime(2026, 8, 30, 8, 0))[0] is True


def test_suppressed_auto_reply_still_records_the_opt_out(monkeypatch):
    """STOP must be RECORDED even when the reply is suppressed - the guard
    gates the SEND, never the compliance side effect."""
    from unittest.mock import patch

    import api.services.whatsapp_intents as wi

    _no_db(monkeypatch)  # auto_reply_mode resolves to default off
    recorded = []

    async def _fail_send(*a, **kw):  # any send attempt = double-answer bug
        raise AssertionError("auto-reply was sent while mode=off")

    with patch.object(wi, "_record_opt_out", lambda phone, cust: recorded.append(phone)), patch(
        "api.services.whatsapp_intents._lookup_customer_by_phone", return_value=None
    ), patch(
        "api.services.whatsapp_intents._phone_is_opted_out", return_value=False
    ), patch(
        "agents.providers.send_whatsapp", new=_fail_send
    ):
        result = asyncio.run(
            wi.dispatch_intent(
                phone="919888777666", text="STOP", button_payload=None, store_id="HQ"
            )
        )

    assert result["intent"] == "OPT_OUT"
    assert recorded == ["919888777666"], "opt-out was NOT recorded"
    assert result["reply_sent"] is False
    assert "reply_suppressed" in result


def test_always_mode_reply_rides_the_wa_intent_reply_flow(monkeypatch):
    """When allowed, the auto-reply goes through the send door WITH flow key
    and store context (one door, no bypass)."""
    from unittest.mock import patch

    import api.services.whatsapp_intents as wi

    _no_db(monkeypatch)
    monkeypatch.setenv("MSG_AUTO_REPLY_MODE", "always")
    calls = []

    async def _spy(phone, message, *, template_id=None, store_id=None, **kw):
        calls.append({"template_id": template_id, "store_id": store_id})
        return providers.DispatchResult(ok=True, status="SIMULATED", channel="whatsapp")

    with patch(
        "api.services.whatsapp_intents._lookup_customer_by_phone", return_value=None
    ), patch(
        "api.services.whatsapp_intents._phone_is_opted_out", return_value=False
    ), patch(
        "agents.providers.send_whatsapp", new=_spy
    ):
        result = asyncio.run(
            wi.dispatch_intent(
                phone="919888777666",
                text="help",
                button_payload=None,
                store_id="BV-RAN-01",
            )
        )

    assert result["reply_sent"] is True
    assert calls == [{"template_id": "WA_INTENT_REPLY", "store_id": "BV-RAN-01"}]


# ===========================================================================
# Piece 4 - messaging preflight
# ===========================================================================


def test_preflight_names_the_missing_stores_and_next_steps(monkeypatch):
    from api.services.integration_status import build_messaging_preflight

    monkeypatch.setattr(providers, "DISPATCH_MODE", "off")
    db = _plant_db(
        monkeypatch,
        whatsapp_cfg={
            "api_key": "MARKER-KEY",
            "whatsapp_number": "MARKER-DEFAULT-NUMBER",
            "store_numbers": "BV-RAN-01:917000000001",
        },
        stores=[
            {"store_id": "BV-RAN-01", "is_active": True},
            {"store_id": "WO-PUN-01", "is_active": True},
        ],
    )

    pf = build_messaging_preflight(db)
    rows = {r["id"]: r for r in pf["rows"]}

    assert set(rows) == {
        "creds",
        "default_number",
        "store_numbers",
        "templates",
        "dlt",
        "dispatch_mode",
        "test_phone",
    }
    assert rows["creds"]["ok"] is True
    assert rows["store_numbers"]["ok"] is False
    assert "WO-PUN-01" in rows["store_numbers"]["detail"], rows["store_numbers"]
    # Templates are all still on seed defaults -> honest not-ok with a step.
    assert rows["templates"]["ok"] is False
    assert rows["dispatch_mode"]["detail"] == "DISPATCH_MODE=off - nothing is sent to anyone"
    # Every not-ok row hands the owner a named next step.
    for r in pf["rows"]:
        if not r["ok"]:
            assert r["next_step"], f"row {r['id']} gives the owner no next step"
    # And no credential value leaks anywhere in the report.
    dumped = json.dumps(pf)
    assert "MARKER-KEY" not in dumped
    assert "917000000001" not in dumped, "a phone number leaked into the preflight"


def test_preflight_rides_the_integration_status_report(monkeypatch):
    from api.services.integration_status import build_integration_status

    _no_db(monkeypatch)
    report = build_integration_status(db=None)
    assert "messaging_preflight" in report
    assert {r["id"] for r in report["messaging_preflight"]["rows"]} >= {
        "creds",
        "dispatch_mode",
        "templates",
    }

def test_an_enable_toggle_cannot_wipe_a_stored_wa_template_mapping():
    """The Settings enable-toggle PUTs the BASE template shape (no wa_* keys).
    _template_payload must drop the absent mapping fields so the $set never
    writes wa_template_name: None over an owner-typed, Meta-APPROVED mapping
    -- the exact click he will make daily during go-live week. The verifier
    proved this guard shipped with no discriminating test: reverting it left
    every suite green. This test dies on that revert."""
    from api.routers.settings import NotificationTemplate, _template_payload

    toggle_shaped = NotificationTemplate(
        template_id="ORDER_DELIVERED",
        template_type="WHATSAPP",
        trigger_event="ORDER_DELIVERED",
        content="Your order is on its way",
        is_enabled=True,
    )
    payload = _template_payload(toggle_shaped)
    for key in ("wa_template_name", "wa_language", "wa_category", "wa_variables"):
        assert key not in payload, (
            f"{key} must be ABSENT from a toggle payload, or the $set wipes "
            f"the stored mapping: {payload.get(key)!r}"
        )

    # And a stored mapping survives the toggle end to end against a fake
    # collection: $set with the toggle payload must leave the owner's typed
    # mapping in place. Values are distinguishable on purpose.
    stored = {
        "template_id": "ORDER_DELIVERED",
        "wa_template_name": "order_delivered_v2_approved",
        "wa_language": "en",
        "wa_category": "utility",
        "wa_variables": ["customer_name", "order_no"],
        "is_enabled": False,
    }
    stored.update({k: v for k, v in payload.items()})
    assert stored["wa_template_name"] == "order_delivered_v2_approved"
    assert stored["wa_variables"] == ["customer_name", "order_no"]
    assert stored["is_enabled"] is True
