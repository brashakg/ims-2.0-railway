"""
IMS 2.0 - only an admin closes a loyalty points exception (#1397 round 6).
==========================================================================
Closing the loyalty recovery exception waives the automatic points retry for
good (owner ruling 2026-11-08: "once an ADMIN closes the recovery exception").
A Store or Area Manager who reaches the bill's shop sees the task, but may not
make that waiver: completing or cancelling it answers 403 and writes nothing.
Driven through the real task routes (test_task_visibility's world).

No emoji (Windows cp1252).
"""
import pytest

from test_task_visibility import S1, _task, world  # noqa: F401  (fixture)

TID = "LOY-EXC"


def _loyalty_exception(db):
    db.tasks.insert_one(_task(TID, "u-admin", S1, source_ref="loyalty_reversal:ORD-9",
                              category="Accounts", priority="P1"))
    return db.tasks.find_one({"task_id": TID})


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER"])
def test_a_manager_sees_the_exception_but_cannot_close_it(world, role):
    as_role, db = world
    before = _loyalty_exception(db)
    client = as_role(role)
    assert client.get(f"/api/v1/tasks/{TID}").status_code == 200
    done = client.patch(f"/api/v1/tasks/{TID}/complete", json={"completion_notes": "looked at it"})
    assert done.status_code == 403, done.text
    assert "admin" in done.json()["detail"].lower()
    cancel = client.patch(f"/api/v1/tasks/{TID}", json={"status": "CANCELLED"})
    assert cancel.status_code == 403, cancel.text
    assert client.put(f"/api/v1/tasks/{TID}", json={"status": "CANCELLED"}).status_code == 403
    assert db.tasks.find_one({"task_id": TID}) == before


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_an_admin_closes_it(world, role):
    as_role, db = world
    _loyalty_exception(db)
    done = as_role(role).patch(f"/api/v1/tasks/{TID}/complete", json={"completion_notes": "settled by hand"})
    assert done.status_code == 200, done.text
    saved = db.tasks.find_one({"task_id": TID})
    assert saved["status"] == "COMPLETED" and saved["completed_by"] in ("u-admin", "u-super")


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_an_admin_cancels_it(world, role):
    as_role, db = world
    _loyalty_exception(db)
    assert as_role(role).patch(f"/api/v1/tasks/{TID}", json={"status": "CANCELLED"}).status_code == 200
    assert db.tasks.find_one({"task_id": TID})["status"] == "CANCELLED"


def test_a_manager_still_closes_an_ordinary_task(world):
    """Positive control: only the loyalty exception is narrowed."""
    as_role, db = world
    db.tasks.insert_one(_task("ORD-TASK", "u-mgr", S1))
    r = as_role("STORE_MANAGER").patch("/api/v1/tasks/ORD-TASK/complete", json={"completion_notes": "done"})
    assert r.status_code == 200, r.text
