"""A cashier task stays cashier work when a manager names its owner."""

import pytest

from test_task_visibility import MANAGERS, S1, USERS, _task, uid, world


@pytest.mark.parametrize("role", ["SALES_STAFF", *MANAGERS])
def test_creator_or_manager_without_cashier_cannot_complete_cashier_work(world, role):
    as_role, db = world
    db.tasks.update_one({"task_id": "R-CASHIER"}, {"$set": {
        "assigned_by": USERS[role]["user_id"], "created_via": "manual",
    }})
    before = db.tasks.find_one({"task_id": "R-CASHIER"})
    response = as_role(role).patch("/api/v1/tasks/R-CASHIER/complete", json={"completion_notes": "finished"})
    assert response.status_code == 403, response.text
    assert db.tasks.find_one({"task_id": "R-CASHIER"}) == before


@pytest.mark.parametrize("door", ["patch", "put", "reassign", "escalate"])
def test_reassignment_preserves_cashier_requirement_for_named_sales_user(world, door):
    as_role, db = world
    manager = as_role("STORE_MANAGER")
    path = "/api/v1/tasks/R-CASHIER"
    if door in {"patch", "put"}:
        changed = getattr(manager, door)(path, json={"assigned_to": uid("SALES_STAFF")})
    elif door == "reassign":
        changed = manager.post(path + "/reassign", json={"assigned_to": uid("SALES_STAFF")})
    else:
        changed = manager.post(path + "/escalate", params={"escalate_to": uid("SALES_STAFF")})
    assert changed.status_code == 200, changed.text
    saved = db.tasks.find_one({"task_id": "R-CASHIER"})
    assert saved["assigned_to"] == uid("SALES_STAFF") and saved["required_role"] == "CASHIER"
    # The assigned person can follow the task, but cannot perform cashier completion.
    assert as_role("SALES_STAFF").get(path).status_code == 200
    refused = as_role("SALES_STAFF").patch(path + "/complete", json={"completion_notes": "finished"})
    assert refused.status_code == 403, refused.text
    assert db.tasks.find_one({"task_id": "R-CASHIER"})["status"] != "COMPLETED"
    # A manager can still reschedule the task.
    from test_task_visibility import FUTURE
    assert as_role("STORE_MANAGER").patch(path, json={"due_date": FUTURE.isoformat()}).status_code == 200


@pytest.mark.parametrize("explicit_cashier", [False, True])
def test_named_legacy_sales_cashier_needs_explicit_cashier_role(world, explicit_cashier):
    as_role, db = world
    roles = ["SALES_CASHIER"] + (["CASHIER"] if explicit_cashier else [])
    USERS["LEGACY"] = {"user_id": "u-legacy", "roles": roles, "store_ids": [S1], "active_store_id": S1}
    try:
        db.tasks.insert_one(_task("NAMED-CASH", "u-legacy", S1, required_role="CASHIER"))
        response = as_role("LEGACY").patch("/api/v1/tasks/NAMED-CASH/complete", json={"completion_notes": "finished"})
        assert response.status_code == (200 if explicit_cashier else 403), response.text
    finally:
        USERS.pop("LEGACY")


def test_legacy_reassignment_history_retains_cashier_requirement(world):
    as_role, db = world
    db.tasks.insert_one(_task("LEGACY-CASH", uid("SALES_STAFF"), S1))
    db.tasks.update_one({"task_id": "LEGACY-CASH"}, {"$set": {"history": [{
        "action": "reassigned", "from": "CASHIER", "to": uid("SALES_STAFF"),
    }]}})
    response = as_role("SALES_STAFF").patch("/api/v1/tasks/LEGACY-CASH/complete", json={"completion_notes": "finished"})
    assert response.status_code == 403, response.text


def test_cashier_sop_resolves_to_a_person_and_retains_its_requirement(world, monkeypatch):
    from api.routers import tasks
    as_role, db = world
    db.sop_templates.insert_one({"template_id": "CASH-SOP", "title": "Count cash", "is_active": True, "frequency": "DAILY",
        "store_id": S1, "assigned_roles": ["CASHIER"], "assigned_users": [], "steps": []})
    monkeypatch.setattr(tasks, "_sop_collection", lambda: db.sop_templates)
    assert as_role("STORE_MANAGER").post("/api/v1/tasks/auto-generate").status_code == 200
    task = db.tasks.find_one({"sop_template_id": "CASH-SOP"})
    assert task["assigned_to"] == uid("CASHIER") and task["required_role"] == "CASHIER"
    assert as_role("CASHIER").patch(f"/api/v1/tasks/{task['task_id']}/complete", json={"completion_notes": "counted cash"}).status_code == 200


@pytest.mark.parametrize("role", ["SALES_STAFF", "STORE_MANAGER", "ADMIN", "CASHIER"])
def test_cashier_sop_checklist_and_legacy_named_task_enforce_the_same_role(world, monkeypatch, role):
    from api.routers import tasks
    as_role, db = world
    db.sop_templates.insert_one({"template_id": "CASH-CHECK", "title": "Cash checklist",
        "assigned_roles": ["CASHIER"], "steps": [{"step_number": 1, "instruction": "Count cash"}]})
    monkeypatch.setattr(tasks, "_sop_collection", lambda: db.sop_templates)
    monkeypatch.setattr(tasks, "_sop_completions_collection", lambda: db.sop_completions)
    db.tasks.insert_one(_task("OLD-NAMED-SOP", USERS[role]["user_id"], S1, source="SOP", sop_template_id="CASH-CHECK"))
    task_response = as_role(role).patch("/api/v1/tasks/OLD-NAMED-SOP/complete", json={"completion_notes": "finished"})
    checklist_response = as_role(role).post("/api/v1/tasks/sop-checklist/item", json={
        "template_id": "CASH-CHECK", "step_number": 1, "completed": True, "store_id": S1,
    })
    expected = 200 if role == "CASHIER" else 403
    assert task_response.status_code == checklist_response.status_code == expected
    if expected == 403:
        assert db.sop_completions.count_documents({}) == 0
        assert db.tasks.find_one({"task_id": "OLD-NAMED-SOP"})["status"] == "OPEN"


@pytest.mark.parametrize("automatic", [False, True])
def test_escalation_never_completes_cashier_work_by_merging_a_twin(world, monkeypatch, automatic):
    from api.routers import tasks
    as_role, db = world
    db.tasks.update_one({"task_id": "R-CASHIER"}, {"$set": {"escalation_group": "CASH-GROUP"}})
    db.tasks.insert_one(_task("CASH-TWIN", USERS["STORE_MANAGER"]["user_id"], S1, escalation_group="CASH-GROUP"))
    monkeypatch.setattr(tasks, "resolve_escalation_target", lambda *args, **kwargs: USERS["STORE_MANAGER"])
    monkeypatch.setattr(tasks, "should_escalate", lambda task, **kwargs: (task["task_id"] == "R-CASHIER", "overdue"))
    async def no_notification(*args, **kwargs):
        return None
    monkeypatch.setattr(tasks, "notify_escalation", no_notification)
    path = "/api/v1/tasks/auto-escalate-overdue" if automatic else "/api/v1/tasks/R-CASHIER/escalate"
    response = as_role("STORE_MANAGER").post(path)
    assert response.status_code == 200, response.text
    saved = db.tasks.find_one({"task_id": "R-CASHIER"})
    assert saved["status"] == "ESCALATED" and saved["required_role"] == "CASHIER"
