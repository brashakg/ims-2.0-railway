"""
IMS 2.0 - Prescription field/DB parity
======================================
A prescription captured at POS must land in the SAME prescriptions collection,
with the SAME field set, as one captured in the clinical Final-Rx. This locks
the parity contract: the canonical EyeData carries prism/base/acuity per eye,
and PrescriptionCreate carries a single ipd + lens_recommendation + next_checkup.

Runs against CI's mongo:7.0 (the session `client` fixture connects the DB). When
no DB is connected (local runs), the create endpoint returns a stub without the
stored doc, so the parity assertions are skipped rather than failing.
"""

import pytest


def test_prescription_create_persists_parity_fields(client, auth_headers):
    """POST a full spectacle Rx (incl. VA/prism/base/IPD/lens type) then GET it
    back and assert every parity field round-trips into the stored document."""
    payload = {
        # walk-in id (starts with "walkin-") -> create skips the customer-exists
        # check, so the test needs no seeded customer.
        "patient_id": "walkin-parity-test",
        "customer_id": "walkin-parity-test",
        "rx_kind": "SPECTACLE",
        "source": "TESTED_AT_STORE",  # admin token satisfies the optometrist gate
        "validity_months": 12,
        "right_eye": {
            "sph": "-1.00",
            "cyl": "-0.50",
            "axis": 90,
            "add": "0",
            "pd": "32",
            "prism": "2",
            "base": "IN",
            "acuity": "6/6",
        },
        "left_eye": {
            "sph": "-1.25",
            "cyl": "-0.25",
            "axis": 85,
            "add": "0",
            "pd": "31",
            "prism": "1",
            "base": "OUT",
            "acuity": "6/9",
        },
        "ipd": "63",
        "lens_recommendation": "Single Vision",
        "next_checkup": "2027-01-01",
    }

    created = client.post("/api/v1/prescriptions", json=payload, headers=auth_headers)
    assert created.status_code in (200, 201), created.text
    prescription_id = created.json().get("prescription_id")
    assert prescription_id

    fetched = client.get(
        f"/api/v1/prescriptions/{prescription_id}", headers=auth_headers
    )
    assert fetched.status_code == 200, fetched.text
    doc = fetched.json()

    # No DB connected (local) -> create returned a stub, get returns a stub with
    # no stored eyes. The parity assertions need a real Mongo; skip otherwise.
    if "right_eye" not in doc or not isinstance(doc.get("right_eye"), dict):
        pytest.skip("prescriptions repo unavailable (no DB) -- needs Mongo")

    # Per-eye parity fields (the canonical EyeData now carries these).
    assert doc["right_eye"].get("prism") == "2"
    assert doc["right_eye"].get("base") == "IN"
    assert doc["right_eye"].get("acuity") == "6/6"
    assert doc["left_eye"].get("prism") == "1"
    assert doc["left_eye"].get("base") == "OUT"
    assert doc["left_eye"].get("acuity") == "6/9"

    # Top-level parity fields.
    assert doc.get("ipd") == "63"
    assert doc.get("lens_recommendation") == "Single Vision"
    assert doc.get("next_checkup") == "2027-01-01"


def test_eyedata_schema_supports_parity_fields():
    """Unit-level guard (no DB): the EyeData + PrescriptionCreate models accept
    the parity fields, so a regression that drops them fails fast here."""
    from api.routers.prescriptions import EyeData, PrescriptionCreate

    eye = EyeData(sph="-1.00", cyl="-0.50", axis=90, prism="2", base="IN", acuity="6/6")
    assert eye.prism == "2"
    assert eye.base == "IN"
    assert eye.acuity == "6/6"

    rx = PrescriptionCreate(
        patient_id="p1",
        customer_id="c1",
        right_eye=eye,
        ipd="63",
        lens_recommendation="Single Vision",
        next_checkup="2027-01-01",
    )
    assert rx.ipd == "63"
    assert rx.lens_recommendation == "Single Vision"
    assert rx.next_checkup == "2027-01-01"
