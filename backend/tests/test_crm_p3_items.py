"""
Tests for CRM P3 backlog items:
  CRM-8  – Promo offer-template library (BOGO / COMBO / THRESHOLD)
  CRM-9  – Auto-trigger NPS on delivery
  CRM-12 – MEGAPHONE dispatches SCHEDULED campaigns on tick
  CRM-13 – Loyalty reward catalog (CRUD)
  CRM-15 – WhatsApp opt-in/out STOP ledger
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone


# ===========================================================================
# CRM-8: Promo offer-template library
# ===========================================================================


def _make_mock_db(collections=None):
    """Return a minimal mock DB with configurable per-collection state."""
    collections = collections or {}

    class _Coll:
        def __init__(self, name):
            self._name = name
            self._docs = list(collections.get(name, []))
            self.inserted = []
            self.updated = []
            self.deleted = []

        def find(self, q=None, _proj=None):
            docs = [dict(d) for d in self._docs]
            return _CursorMock(docs)

        def find_one(self, q=None, _proj=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in (q or {}).items()):
                    return dict(d)
            return None

        def insert_one(self, doc):
            self._docs.append(dict(doc))
            self.inserted.append(dict(doc))

        def update_one(self, flt, upd, **kw):
            for d in self._docs:
                if all(d.get(k) == v for k, v in flt.items()):
                    for k, v in (upd.get("$set") or {}).items():
                        d[k] = v
            r = MagicMock()
            r.modified_count = 1
            return r

        def delete_one(self, flt):
            before = len(self._docs)
            self._docs = [
                d for d in self._docs
                if not all(d.get(k) == v for k, v in flt.items())
            ]
            r = MagicMock()
            r.deleted_count = before - len(self._docs)
            return r

    class _CursorMock:
        def __init__(self, docs):
            self._docs = docs

        def sort(self, *_a, **_kw):
            return self

        def limit(self, n):
            self._docs = self._docs[:n]
            return self

        def __iter__(self):
            return iter(self._docs)

    class _DB:
        is_connected = True
        def __init__(self):
            self._colls = {}
        def get_collection(self, name):
            if name not in self._colls:
                # _Coll.__init__ already copies collections[name] into _docs
                self._colls[name] = _Coll(name)
            return self._colls[name]

    return _DB()


class TestPromoTemplates:
    """CRM-8: promo offer-template CRUD validation."""

    def test_bogo_missing_buy_quantity_raises(self):
        from fastapi import HTTPException
        from api.routers.campaigns import _validate_promo_template, PromoTemplateCreate

        req = PromoTemplateCreate(
            name="BOGO Test",
            type="BOGO",
            # missing buy_quantity / get_quantity
        )
        with pytest.raises(HTTPException) as exc_info:
            _validate_promo_template(req)
        assert exc_info.value.status_code == 422
        assert "buy_quantity" in exc_info.value.detail

    def test_combo_single_sku_raises(self):
        from fastapi import HTTPException
        from api.routers.campaigns import _validate_promo_template, PromoTemplateCreate

        req = PromoTemplateCreate(
            name="Combo Test",
            type="COMBO",
            sku_list=["SKU1"],  # only 1 SKU - need at least 2
        )
        with pytest.raises(HTTPException) as exc_info:
            _validate_promo_template(req)
        assert exc_info.value.status_code == 422

    def test_threshold_missing_min_value_raises(self):
        from fastapi import HTTPException
        from api.routers.campaigns import _validate_promo_template, PromoTemplateCreate

        req = PromoTemplateCreate(
            name="Threshold Test",
            type="THRESHOLD",
            threshold_discount_pct=10.0,
            # missing min_order_value
        )
        with pytest.raises(HTTPException) as exc_info:
            _validate_promo_template(req)
        assert exc_info.value.status_code == 422
        assert "min_order_value" in exc_info.value.detail

    def test_valid_bogo_passes(self):
        from api.routers.campaigns import _validate_promo_template, PromoTemplateCreate

        req = PromoTemplateCreate(
            name="Buy 2 Get 1",
            type="BOGO",
            buy_quantity=2,
            get_quantity=1,
        )
        _validate_promo_template(req)  # should not raise

    def test_valid_threshold_passes(self):
        from api.routers.campaigns import _validate_promo_template, PromoTemplateCreate

        req = PromoTemplateCreate(
            name="Spend 5000 get 15% off",
            type="THRESHOLD",
            min_order_value=5000.0,
            threshold_discount_pct=15.0,
        )
        _validate_promo_template(req)  # should not raise

    def test_valid_combo_passes(self):
        from api.routers.campaigns import _validate_promo_template, PromoTemplateCreate

        req = PromoTemplateCreate(
            name="Frames + Lens combo",
            type="COMBO",
            sku_list=["FRAME-001", "LENS-001"],
            combo_discount_pct=20.0,
        )
        _validate_promo_template(req)  # should not raise


# ===========================================================================
# CRM-9: Auto-trigger NPS on delivery
# ===========================================================================


class TestNpsTrigger:
    """CRM-9: auto-trigger NPS survey on order delivery."""

    @pytest.mark.asyncio
    async def test_nps_inserted_on_delivery(self, monkeypatch):
        """Happy path: NPS record is inserted for a customer with consent."""
        from api.services.nps_trigger import trigger_nps_on_delivery

        db = _make_mock_db({
            "customers": [{"customer_id": "C1", "name": "Test", "mobile": "9876543210", "marketing_consent": True}],
            "stores": [{"store_id": "S1", "name": "BV Ranchi"}],
            "nps_responses": [],
        })
        monkeypatch.setattr("api.services.nps_trigger._get_db", lambda: db)

        sent_notifications = []

        async def _mock_send(**kwargs):
            sent_notifications.append(kwargs)
            return {"notification_id": "N1"}

        # send_notification is imported inside the function body, so patch the
        # source module (notification_service) rather than the nps_trigger namespace.
        monkeypatch.setattr(
            "api.services.notification_service.send_notification", _mock_send
        )

        order = {"order_id": "ORD-001", "customer_id": "C1", "store_id": "S1"}
        actor = {"user_id": "staff-1", "active_store_id": "S1"}

        await trigger_nps_on_delivery(order, actor)

        nps_coll = db.get_collection("nps_responses")
        assert len(nps_coll._docs) == 1
        nps = nps_coll._docs[0]
        assert nps["customer_id"] == "C1"
        assert nps["order_id"] == "ORD-001"
        assert nps["status"] == "SENT"
        assert nps["auto_triggered"] is True
        assert len(sent_notifications) == 1
        assert sent_notifications[0]["template_id"] == "NPS_SURVEY"

    @pytest.mark.asyncio
    async def test_nps_skipped_opted_out(self, monkeypatch):
        """Opted-out customer must not receive an NPS survey."""
        from api.services.nps_trigger import trigger_nps_on_delivery

        db = _make_mock_db({
            "customers": [{"customer_id": "C2", "name": "Opt Out", "mobile": "9000000001", "marketing_consent": False}],
            "nps_responses": [],
        })
        monkeypatch.setattr("api.services.nps_trigger._get_db", lambda: db)

        order = {"order_id": "ORD-002", "customer_id": "C2", "store_id": "S1"}
        await trigger_nps_on_delivery(order, {})

        # No NPS inserted, no notification sent (opted out gate fires before send)
        assert db.get_collection("nps_responses")._docs == []

    @pytest.mark.asyncio
    async def test_nps_skipped_no_customer_id(self, monkeypatch):
        """Order without a customer_id is skipped silently."""
        from api.services.nps_trigger import trigger_nps_on_delivery

        db = _make_mock_db({"nps_responses": []})
        monkeypatch.setattr("api.services.nps_trigger._get_db", lambda: db)

        await trigger_nps_on_delivery({"order_id": "ORD-003"}, {})

        assert db.get_collection("nps_responses")._docs == []

    @pytest.mark.asyncio
    async def test_nps_idempotent_existing_order_nps(self, monkeypatch):
        """If an NPS for the same order already exists, do not create a second one."""
        from api.services.nps_trigger import trigger_nps_on_delivery

        existing = {
            "nps_id": "NPS-EXISTING",
            "order_id": "ORD-004",
            "customer_id": "C3",
            "status": "SENT",
            "survey_sent_at": datetime.now().isoformat(),
        }
        db = _make_mock_db({
            "customers": [{"customer_id": "C3", "mobile": "9111111111", "marketing_consent": True}],
            "nps_responses": [existing],
        })
        monkeypatch.setattr("api.services.nps_trigger._get_db", lambda: db)

        await trigger_nps_on_delivery({"order_id": "ORD-004", "customer_id": "C3", "store_id": "S1"}, {})

        # Still only 1 NPS doc (the existing one), no new one added
        assert len(db.get_collection("nps_responses")._docs) == 1

    @pytest.mark.asyncio
    async def test_nps_no_db_is_noop(self, monkeypatch):
        """When DB is unavailable, trigger must silently do nothing."""
        from api.services.nps_trigger import trigger_nps_on_delivery

        monkeypatch.setattr("api.services.nps_trigger._get_db", lambda: None)

        # Should not raise
        await trigger_nps_on_delivery({"order_id": "ORD-005", "customer_id": "C4"}, {})


# ===========================================================================
# CRM-12: MEGAPHONE dispatches SCHEDULED campaigns
# ===========================================================================


class TestMegaphoneScheduledCampaigns:
    """CRM-12: MEGAPHONE tick dispatches due ONE_TIME SCHEDULED campaigns."""

    @pytest.mark.asyncio
    async def test_execute_campaign_send_completes(self, monkeypatch):
        """_execute_campaign_send fans-out to the segment audience and marks campaign COMPLETED."""
        past = "2020-01-01T00:00:00+00:00"
        campaign = {
            "campaign_id": "CMP-001",
            "status": "ACTIVE",  # already claimed
            "schedule": {"kind": "ONE_TIME", "send_at": past},
            "template_id": "BIRTHDAY",
            "channels": ["WHATSAPP"],
            "store_id": "",
            "segment_key": "all_customers",
            "segment_params": {},
        }
        db = _make_mock_db({"campaigns": [campaign]})

        import agents.implementations.megaphone as meg_mod
        monkeypatch.setattr(meg_mod, "_shared_in_quiet_hours", lambda *_: False)

        from agents.implementations.megaphone import MegaphoneAgent
        agent = MegaphoneAgent(db=db)

        sent_calls = []

        async def _fake_send(**kw):
            sent_calls.append(kw)
            return {"notification_id": "N1"}

        with patch("api.services.campaign_segments.resolve_segment", return_value=[
            {"customer_id": "C1", "phone": "9876543210", "name": "Test"}
        ]):
            with patch("api.services.notification_service.send_notification", new=_fake_send):
                coll = db.get_collection("campaigns")
                await agent._execute_campaign_send(campaign, coll)

        # Campaign status should now be COMPLETED
        cmp = db.get_collection("campaigns").find_one({"campaign_id": "CMP-001"})
        assert cmp["status"] == "COMPLETED"
        assert len(sent_calls) == 1
        assert sent_calls[0]["template_id"] == "BIRTHDAY"

    @pytest.mark.asyncio
    async def test_execute_campaign_defers_in_dnd(self, monkeypatch):
        """_execute_campaign_send reverts to SCHEDULED when inside the DND window."""
        campaign = {
            "campaign_id": "CMP-DND",
            "status": "ACTIVE",
            "schedule": {"kind": "ONE_TIME", "send_at": "2020-01-01T00:00:00+00:00"},
            "template_id": "PROMO",
            "channels": ["WHATSAPP"],
            "store_id": "",
            "segment_key": "all_customers",
            "segment_params": {},
        }
        db = _make_mock_db({"campaigns": [campaign]})

        import agents.implementations.megaphone as meg_mod
        # Force DND
        monkeypatch.setattr(meg_mod, "_shared_in_quiet_hours", lambda *_: True)

        from agents.implementations.megaphone import MegaphoneAgent
        agent = MegaphoneAgent(db=db)

        coll = db.get_collection("campaigns")
        await agent._execute_campaign_send(campaign, coll)

        # Campaign must be reverted to SCHEDULED (not COMPLETED)
        cmp = db.get_collection("campaigns").find_one({"campaign_id": "CMP-DND"})
        assert cmp["status"] == "SCHEDULED"

    @pytest.mark.asyncio
    async def test_dispatch_returns_zero_when_no_due_campaigns(self, monkeypatch):
        """When the campaigns collection is empty, _dispatch_scheduled_campaigns returns 0."""
        db = _make_mock_db({"campaigns": []})

        from agents.implementations.megaphone import MegaphoneAgent
        agent = MegaphoneAgent(db=db)

        result = await agent._dispatch_scheduled_campaigns()
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_db_returns_zero(self):
        """When DB is None, _dispatch_scheduled_campaigns returns 0 (fail-soft)."""
        from agents.implementations.megaphone import MegaphoneAgent
        agent = MegaphoneAgent(db=None)

        result = await agent._dispatch_scheduled_campaigns()
        assert result == 0


# ===========================================================================
# CRM-13: Loyalty reward catalog
# ===========================================================================


class TestLoyaltyRewardCatalog:
    """CRM-13: loyalty_rewards CRUD via /loyalty/rewards."""

    def _make_app(self, db):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import jwt as pyjwt
        from api.routers import loyalty as loy_mod
        from api.routers import auth as auth_mod

        app = FastAPI()
        app.include_router(loy_mod.router, prefix="/loyalty")

        SECRET = "test"
        token = pyjwt.encode(
            {
                "user_id": "u1",
                "username": "admin",
                "roles": ["SUPERADMIN"],
                "active_store_id": "S1",
                "exp": 9999999999,
            },
            SECRET,
            algorithm="HS256",
        )
        auth_mod._SECRET_KEY = SECRET

        monkeydb = db

        def _fake_current_user():
            return {"user_id": "u1", "username": "admin", "roles": ["SUPERADMIN"], "active_store_id": "S1"}

        from api.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = _fake_current_user

        loy_mod._reward_db = lambda: monkeydb

        client = TestClient(app, raise_server_exceptions=False)
        return client, token

    def test_list_rewards_empty(self):
        db = _make_mock_db()
        client, token = self._make_app(db)
        r = client.get("/loyalty/rewards", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert data["rewards"] == []
        assert data["total"] == 0

    def test_create_and_list_reward(self):
        db = _make_mock_db()
        client, token = self._make_app(db)

        payload = {
            "name": "10% Discount",
            "type": "DISCOUNT",
            "point_cost": 500,
            "discount_pct": 10.0,
            "description": "Redeem 500 points for a 10% discount",
        }
        r = client.post("/loyalty/rewards", json=payload, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert data["reward"]["name"] == "10% Discount"
        assert data["reward"]["point_cost"] == 500
        assert data["reward"]["type"] == "DISCOUNT"

        # List should now return 1 reward
        r2 = client.get("/loyalty/rewards?active_only=false", headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 200
        assert r2.json()["total"] == 1


# ===========================================================================
# CRM-15: WhatsApp opt-in/out STOP ledger
# ===========================================================================


class TestWhatsappStopLedger:
    """CRM-15: /marketing/whatsapp-consent records events + flips marketing_consent."""

    def _make_app(self, db):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import jwt as pyjwt
        from api.routers import marketing as mkt_mod
        from api.routers import auth as auth_mod

        app = FastAPI()
        app.include_router(mkt_mod.router, prefix="/marketing")

        SECRET = "test"
        token = pyjwt.encode(
            {
                "user_id": "staff1",
                "username": "staff",
                "roles": ["STORE_MANAGER"],
                "active_store_id": "S1",
                "exp": 9999999999,
            },
            SECRET,
            algorithm="HS256",
        )
        auth_mod._SECRET_KEY = SECRET

        def _fake_current_user():
            return {"user_id": "staff1", "username": "staff", "roles": ["STORE_MANAGER"], "active_store_id": "S1"}

        from api.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = _fake_current_user

        mkt_mod._get_db = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        return client, token

    def test_opt_out_flips_marketing_consent(self):
        db = _make_mock_db({
            "customers": [{"customer_id": "C1", "name": "Ravi", "mobile": "9876543210", "marketing_consent": True}],
        })
        client, token = self._make_app(db)

        r = client.post(
            "/marketing/whatsapp-consent",
            json={"customer_id": "C1", "event": "OPT_OUT", "source": "CUSTOMER_REQUEST"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["marketing_consent"] is False
        assert data["event"] == "OPT_OUT"

        # Customer doc should now have marketing_consent=False
        cust = db.get_collection("customers").find_one({"customer_id": "C1"})
        assert cust["marketing_consent"] is False

        # Ledger row must be written
        ledger = db.get_collection("whatsapp_consent_ledger")._docs
        assert len(ledger) == 1
        assert ledger[0]["event"] == "OPT_OUT"

    def test_opt_in_flips_marketing_consent_to_true(self):
        db = _make_mock_db({
            "customers": [{"customer_id": "C2", "name": "Priya", "mobile": "9111111111", "marketing_consent": False}],
        })
        client, token = self._make_app(db)

        r = client.post(
            "/marketing/whatsapp-consent",
            json={"customer_id": "C2", "event": "OPT_IN", "source": "STAFF_ENTRY"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["marketing_consent"] is True

        cust = db.get_collection("customers").find_one({"customer_id": "C2"})
        assert cust["marketing_consent"] is True

    def test_stop_records_opt_out(self):
        db = _make_mock_db({
            "customers": [{"customer_id": "C3", "name": "Amit", "mobile": "9222222222", "marketing_consent": True}],
        })
        client, token = self._make_app(db)

        r = client.post(
            "/marketing/whatsapp-consent",
            json={"customer_id": "C3", "event": "STOP", "source": "INBOUND_STOP"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["marketing_consent"] is False

    def test_unknown_customer_returns_404(self):
        db = _make_mock_db()
        client, token = self._make_app(db)

        r = client.post(
            "/marketing/whatsapp-consent",
            json={"customer_id": "GHOST", "event": "OPT_OUT"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    def test_consent_history_returns_ledger_rows(self):
        existing_row = {
            "ledger_id": "CLE-001",
            "customer_id": "C4",
            "event": "OPT_OUT",
            "recorded_at": "2026-01-01T00:00:00",
        }
        db = _make_mock_db({
            "customers": [{"customer_id": "C4", "marketing_consent": False}],
            "whatsapp_consent_ledger": [existing_row],
        })
        client, token = self._make_app(db)

        r = client.get(
            "/marketing/whatsapp-consent/C4",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["customer_id"] == "C4"
        assert data["current_consent"] is False
        assert len(data["events"]) == 1
        assert data["events"][0]["event"] == "OPT_OUT"
