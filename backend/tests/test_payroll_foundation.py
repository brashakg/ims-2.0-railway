"""
Payroll Phase 1 foundation tests
================================
Covers the entity model, the Structured-CTC salary master, and the
state-aware Professional Tax (PT) slab config + lookup.
"""

import uuid

from api.routers.payroll import pt_for, DEFAULT_PT_SLABS


# ---------------------------------------------------------------------------
# pt_for() — pure statutory lookup (no DB, the critical correctness path)
# ---------------------------------------------------------------------------


def test_pt_for_jharkhand_annual_basis():
    jh = DEFAULT_PT_SLABS["JH"]  # ANNUAL basis
    assert pt_for(jh, 20000, 6) == 0      # 2.40L/yr -> nil
    assert pt_for(jh, 35000, 6) == 100    # 4.20L/yr -> 100
    assert pt_for(jh, 55000, 6) == 150    # 6.60L/yr -> 150
    assert pt_for(jh, 75000, 6) == 175    # 9.00L/yr -> 175
    assert pt_for(jh, 100000, 6) == 208   # 12.0L/yr -> 208 (cap)


def test_pt_for_maharashtra_monthly_male():
    mh = DEFAULT_PT_SLABS["MH"]  # MONTHLY basis, gender-aware
    assert pt_for(mh, 7000, 6, "MALE") == 0
    assert pt_for(mh, 9000, 6, "MALE") == 175
    assert pt_for(mh, 15000, 6, "MALE") == 200
    # February top-slab override (+100)
    assert pt_for(mh, 15000, 2, "MALE") == 300


def test_pt_for_maharashtra_female_threshold():
    mh = DEFAULT_PT_SLABS["MH"]
    assert pt_for(mh, 20000, 6, "FEMALE") == 0    # women nil up to 25,000
    assert pt_for(mh, 30000, 6, "FEMALE") == 200
    assert pt_for(mh, 30000, 2, "FEMALE") == 300  # February override


def test_pt_for_unknown_gender_defaults_to_general_slab():
    mh = DEFAULT_PT_SLABS["MH"]
    # gender-aware state + unknown gender -> the general (male) slab applies
    assert pt_for(mh, 9000, 6, "ANY") == 175


def test_pt_for_empty_or_missing_doc_is_zero():
    assert pt_for(None, 50000, 6) == 0
    assert pt_for({}, 50000, 6) == 0


# ---------------------------------------------------------------------------
# Entity CRUD (integration via TestClient)
# ---------------------------------------------------------------------------


def _entity_payload(name="Test Entity"):
    return {
        "name": name,
        "legal_name": name + " Pvt Ltd",
        "pan": "ABCDE1234F",
        "gstins": [
            {"gstin": "20ABCDE1234F1ZE", "state_code": "20", "state_name": "Jharkhand"}
        ],
        "pf": {"registered": True, "establishment_code": "JHRAN1234567"},
        "esi": {"registered": True, "code": "12345678901234567"},
        "pt_registrations": [{"state_code": "JH", "registration_number": "PT-JH-001"}],
    }


def test_entity_create_get_and_list(client, auth_headers):
    r = client.post(
        "/api/v1/entities",
        json=_entity_payload("Better Vision Chas-Bokaro"),
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    entity = r.json()["entity"]
    eid = entity["entity_id"]
    assert eid.startswith("ent_")
    assert entity["is_active"] is True
    assert "_id" not in entity

    r2 = client.get(f"/api/v1/entities/{eid}", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["entity"]["name"] == "Better Vision Chas-Bokaro"

    r3 = client.get("/api/v1/entities", headers=auth_headers)
    assert r3.status_code == 200
    assert eid in [e["entity_id"] for e in r3.json()["entities"]]


def test_entity_update(client, auth_headers):
    r = client.post(
        "/api/v1/entities", json=_entity_payload("Entity To Update"), headers=auth_headers
    )
    eid = r.json()["entity"]["entity_id"]
    r2 = client.put(
        f"/api/v1/entities/{eid}", json={"tan": "RANC01234E"}, headers=auth_headers
    )
    assert r2.status_code == 200
    assert r2.json()["entity"]["tan"] == "RANC01234E"


def test_entity_create_requires_admin(client, staff_headers):
    r = client.post("/api/v1/entities", json=_entity_payload(), headers=staff_headers)
    assert r.status_code == 403


def test_assign_missing_store_returns_404(client, auth_headers):
    r = client.post("/api/v1/entities", json=_entity_payload(), headers=auth_headers)
    eid = r.json()["entity"]["entity_id"]
    r2 = client.post(
        f"/api/v1/entities/{eid}/stores/NO-SUCH-STORE", headers=auth_headers
    )
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Salary config (Structured CTC)
# ---------------------------------------------------------------------------


def test_salary_config_create_get_update(client, auth_headers):
    emp = f"EMP-{uuid.uuid4().hex[:8]}"
    payload = {
        "employee_id": emp,
        "entity_id": "ent_test",
        "store_id": "BV-TEST-01",
        "designation": "Optometrist",
        "basic": 20000,
        "hra": 8000,
        "conveyance": 1600,
        "special_allowance": 5000,
        "pf_applicable": True,
        "pt_applicable": True,
    }
    r = client.post("/api/v1/payroll/config", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text

    r2 = client.get(f"/api/v1/payroll/config/{emp}", headers=auth_headers)
    assert r2.status_code == 200
    cfg = r2.json()["config"]
    assert cfg["basic"] == 20000
    assert "_id" not in cfg

    r3 = client.put(
        f"/api/v1/payroll/config/{emp}",
        json={"basic": 22000, "hra": 8800},
        headers=auth_headers,
    )
    assert r3.status_code == 200
    assert r3.json()["config"]["basic"] == 22000


def test_salary_config_duplicate_returns_409(client, auth_headers):
    emp = f"EMP-{uuid.uuid4().hex[:8]}"
    payload = {"employee_id": emp, "basic": 15000}
    assert (
        client.post("/api/v1/payroll/config", json=payload, headers=auth_headers).status_code
        == 201
    )
    assert (
        client.post("/api/v1/payroll/config", json=payload, headers=auth_headers).status_code
        == 409
    )


def test_salary_config_basic_must_be_positive(client, auth_headers):
    emp = f"EMP-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/v1/payroll/config", json={"employee_id": emp, "basic": 0}, headers=auth_headers
    )
    assert r.status_code == 422  # pydantic gt=0


def test_salary_config_bulk_upsert(client, auth_headers):
    e1 = f"EMP-{uuid.uuid4().hex[:8]}"
    e2 = f"EMP-{uuid.uuid4().hex[:8]}"
    payload = {
        "configs": [
            {"employee_id": e1, "basic": 18000, "hra": 7200},
            {"employee_id": e2, "basic": 25000, "hra": 10000},
        ]
    }
    r = client.post("/api/v1/payroll/config/bulk", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["created"] == 2


def test_salary_config_write_requires_finance_role(client, staff_headers):
    r = client.post(
        "/api/v1/payroll/config",
        json={"employee_id": "X", "basic": 1000},
        headers=staff_headers,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# PT slab endpoints
# ---------------------------------------------------------------------------


def test_pt_slabs_list_includes_jh_and_mh(client, auth_headers):
    r = client.get("/api/v1/payroll/pt-slabs", headers=auth_headers)
    assert r.status_code == 200
    codes = {s["state_code"] for s in r.json()["pt_slabs"]}
    assert {"JH", "MH"}.issubset(codes)


def test_pt_slabs_seed_then_get(client, auth_headers):
    r = client.post("/api/v1/payroll/pt-slabs/seed", headers=auth_headers)
    assert r.status_code == 201, r.text
    assert set(r.json()["states"]) == {"MH", "JH"}

    r2 = client.get("/api/v1/payroll/pt-slabs/JH", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["pt_slab"]["basis"] == "ANNUAL"


# ---------------------------------------------------------------------------
# Salary config single-GET store scope (cross-store PII / IDOR — BUG sibling
# of BUG-062). The single-record GET carries bank/PAN/UAN PII; a store-scoped
# role must not read an employee belonging to ANOTHER store by id.
# ---------------------------------------------------------------------------


def _store_manager_headers(store_id):
    """JWT for a STORE_MANAGER scoped to a single store."""
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": f"sm-{store_id}",
            "username": f"sm_{store_id}",
            "roles": ["STORE_MANAGER"],
            "store_ids": [store_id],
            "active_store_id": store_id,
        }
    )
    return {"Authorization": f"Bearer {token}"}


def test_salary_config_get_store_scoped_blocks_foreign_store(client, auth_headers):
    """A STORE_MANAGER of store A gets 404 (existence-hide) reading an employee
    whose salary config belongs to store B -- the PII leak this fix closes."""
    emp = f"EMP-{uuid.uuid4().hex[:8]}"
    payload = {
        "employee_id": emp,
        "store_id": "BV-STORE-B",
        "basic": 20000,
        "bank_account_no": "1234567890",
        "pan": "ABCDE1234F",
    }
    # Create as SUPERADMIN (cross-store; write is admin-only anyway).
    assert (
        client.post(
            "/api/v1/payroll/config", json=payload, headers=auth_headers
        ).status_code
        == 201
    )

    # STORE_MANAGER of a DIFFERENT store must be 404'd.
    r = client.get(
        f"/api/v1/payroll/config/{emp}", headers=_store_manager_headers("BV-STORE-A")
    )
    assert r.status_code == 404, r.text


def test_salary_config_get_same_store_manager_ok(client, auth_headers):
    """A STORE_MANAGER of the employee's OWN store can read the config (200)."""
    emp = f"EMP-{uuid.uuid4().hex[:8]}"
    payload = {"employee_id": emp, "store_id": "BV-STORE-A", "basic": 18000}
    assert (
        client.post(
            "/api/v1/payroll/config", json=payload, headers=auth_headers
        ).status_code
        == 201
    )
    r = client.get(
        f"/api/v1/payroll/config/{emp}", headers=_store_manager_headers("BV-STORE-A")
    )
    assert r.status_code == 200, r.text
    assert r.json()["config"]["basic"] == 18000


def test_salary_config_get_admin_cross_store_ok(client, auth_headers):
    """SUPERADMIN/ADMIN keep unrestricted cross-store read."""
    emp = f"EMP-{uuid.uuid4().hex[:8]}"
    payload = {"employee_id": emp, "store_id": "BV-STORE-B", "basic": 30000}
    assert (
        client.post(
            "/api/v1/payroll/config", json=payload, headers=auth_headers
        ).status_code
        == 201
    )
    # auth_headers is SUPERADMIN active on BV-TEST-01 -> still reads store-B emp.
    r = client.get(f"/api/v1/payroll/config/{emp}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["config"]["basic"] == 30000
