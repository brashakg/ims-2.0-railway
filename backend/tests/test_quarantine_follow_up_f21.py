"""F21-3: a piece set aside off sale (Quarantine screen, damaged customer
return) gets the promised follow-up -- the task agent (TASKMASTER) listens for
stock.quarantined and raises ONE task per piece, due in 7 days, to the shop's
store manager. No live Mongo: mongomock."""
import asyncio
from datetime import datetime, timedelta

import mongomock
import pytest

from agents import registry as R
from agents.implementations.taskmaster import TaskmasterAgent
from api.services import task_triggers


@pytest.fixture
def db(monkeypatch):
    R.AGENT_REGISTRY.clear()
    R._event_subscriptions.clear()
    database = mongomock.MongoClient()["ims_quarantine_follow_up"]
    database.stock_units.insert_one({"stock_id": "U1", "barcode": "BOK0001", "store_id": "S1"})
    monkeypatch.setattr(task_triggers, "_person_holding",
                        lambda role, store, **_: "u-mgr" if (role, store) == ("STORE_MANAGER", "S1") else None)
    yield database
    R.AGENT_REGISTRY.clear()
    R._event_subscriptions.clear()


EVENT = {"stock_id": "U1", "store_id": "S1", "reason": "CUSTOMER_RETURN_DAMAGED", "actor_id": "u1"}


def test_one_seven_day_follow_up_per_piece_for_the_shop_manager(db):
    agent = TaskmasterAgent(db=db)
    asyncio.run(agent.on_event("stock.quarantined", EVENT))
    asyncio.run(agent.on_event("stock.quarantined", EVENT))  # re-told: still one task
    tasks = list(db.tasks.find({}))
    assert len(tasks) == 1
    task = tasks[0]
    assert "BOK0001" in task["title"] and task["source_ref"] == "quarantine_follow_up:U1"
    assert task["assigned_to"] == "u-mgr" and task["store_id"] == "S1"
    assert task["status"] == "OPEN" and task["link"] == "/inventory/quarantine"
    due = task["due_at"] - datetime.now()
    assert timedelta(days=6, hours=23) < due <= timedelta(days=7)


def test_the_task_agent_is_subscribed_so_a_dispatch_reaches_it(db):
    R.initialize_registry(db=db)
    assert "taskmaster" in R._event_subscriptions["stock.quarantined"]
    asyncio.run(R.dispatch_event("stock.quarantined", EVENT, source="inventory_router"))
    assert db.tasks.count_documents({"source_ref": "quarantine_follow_up:U1"}) == 1
