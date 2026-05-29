"""
Payroll run-flow tests (Phase 2)
================================
Exercises POST /payroll/run -> /run/rows -> /approve -> /lock end to end.
Each test scopes to a unique entity_id so the shared test DB stays isolated.
Integration-level: requires Mongo (CI provides a mongo:7.0 service).
"""

import uuid


def test_payroll_run_compute_approve_lock(client, auth_headers, db_live):
    entity = f"ent_run_{uuid.uuid4().hex[:8]}"
    e1 = f"RUN1-{uuid.uuid4().hex[:6]}"
    e2 = f"RUN2-{uuid.uuid4().hex[:6]}"

    assert client.post(
        "/api/v1/payroll/config",
        json={"employee_id": e1, "entity_id": entity, "basic": 20000, "hra": 8000,
              "conveyance": 1600, "medical": 1250, "special_allowance": 5000},
        headers=auth_headers,
    ).status_code == 201
    assert client.post(
        "/api/v1/payroll/config",
        json={"employee_id": e2, "entity_id": entity, "basic": 8000, "hra": 2000,
              "conveyance": 800, "special_allowance": 1200},
        headers=auth_headers,
    ).status_code == 201

    # Run (saves DRAFT). No store -> no PT resolved (pt = 0).
    r = client.post(
        "/api/v1/payroll/run",
        json={"month": 6, "year": 2099, "entity_id": entity},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    nets = {row["employee_id"]: row["net_salary"] for row in body["rows"]}
    assert nets[e1] == 34050   # 35850 gross - 1800 PF (gross > 21k -> no ESI)
    assert nets[e2] == 10950   # 12000 gross - 960 PF - 90 ESI

    # Register shows DRAFT
    rows = client.get(
        "/api/v1/payroll/run/rows",
        params={"month": 6, "year": 2099, "entity_id": entity},
        headers=auth_headers,
    ).json()
    assert rows["total"] == 2
    assert all(row["status"] == "DRAFT" for row in rows["rows"])

    # Approve
    ap = client.post(
        "/api/v1/payroll/approve",
        json={"month": 6, "year": 2099, "entity_id": entity},
        headers=auth_headers,
    )
    assert ap.status_code == 200
    assert ap.json()["approved"] == 2

    # Lock
    lk = client.post(
        "/api/v1/payroll/lock",
        json={"month": 6, "year": 2099, "entity_id": entity},
        headers=auth_headers,
    )
    assert lk.status_code == 200
    assert lk.json()["locked"] == 2

    rows2 = client.get(
        "/api/v1/payroll/run/rows",
        params={"month": 6, "year": 2099, "entity_id": entity},
        headers=auth_headers,
    ).json()
    assert all(row["status"] == "PAID" for row in rows2["rows"])

    # Re-run after lock -> rows are skipped (not overwritten)
    r2 = client.post(
        "/api/v1/payroll/run",
        json={"month": 6, "year": 2099, "entity_id": entity},
        headers=auth_headers,
    )
    assert all(row.get("skipped") for row in r2.json()["rows"])


def test_payroll_run_requires_finance_role(client, staff_headers):
    r = client.post(
        "/api/v1/payroll/run", json={"month": 6, "year": 2099}, headers=staff_headers
    )
    assert r.status_code == 403


def test_payroll_run_dry_run_does_not_persist(client, auth_headers, db_live):
    entity = f"ent_dry_{uuid.uuid4().hex[:8]}"
    e1 = f"DRY-{uuid.uuid4().hex[:6]}"
    client.post(
        "/api/v1/payroll/config",
        json={"employee_id": e1, "entity_id": entity, "basic": 15000},
        headers=auth_headers,
    )
    r = client.post(
        "/api/v1/payroll/run",
        json={"month": 7, "year": 2099, "entity_id": entity, "dry_run": True},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert r.json()["count"] == 1

    rows = client.get(
        "/api/v1/payroll/run/rows",
        params={"month": 7, "year": 2099, "entity_id": entity},
        headers=auth_headers,
    ).json()
    assert rows["total"] == 0  # dry run persisted nothing
