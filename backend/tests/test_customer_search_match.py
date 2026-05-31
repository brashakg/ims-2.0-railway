"""Unit tests for the search `match` annotation that labels account-holder vs
family-member (patient) hits with the same token semantics as the query."""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-key-for-customer-search")

from api.routers.customers import _annotate_customer_matches  # noqa: E402


def _mahesh():
    return {
        "customer_id": "C1",
        "name": "Mahesh Kumar Gupta",
        "mobile": "9810000001",
        "patients": [
            {"patient_id": "P1", "name": "Alka Gupta", "mobile": "9810000002"},
            {"patient_id": "P2", "name": "Suresh Gupta", "mobile": "9810000003"},
        ],
    }


def test_patient_only_when_account_name_misses():
    [c] = _annotate_customer_matches([_mahesh()], "Alka")
    assert c["match"]["account"] is False
    assert c["match"]["matched_patient_ids"] == ["P1"]


def test_all_tokens_excludes_sibling_sharing_one_token():
    [c] = _annotate_customer_matches([_mahesh()], "Alka Gupta")
    # "Suresh Gupta" matches only "gupta" -> must NOT be a match
    assert c["match"]["matched_patient_ids"] == ["P1"]


def test_surname_hits_account_and_all_patients():
    [c] = _annotate_customer_matches([_mahesh()], "Gupta")
    assert c["match"]["account"] is True
    assert set(c["match"]["matched_patient_ids"]) == {"P1", "P2"}


def test_match_by_patient_mobile():
    [c] = _annotate_customer_matches([_mahesh()], "9810000002")
    assert c["match"]["account"] is False
    assert c["match"]["matched_patient_ids"] == ["P1"]


def test_empty_query_marks_account_only():
    [c] = _annotate_customer_matches([_mahesh()], "")
    assert c["match"]["account"] is True
    assert c["match"]["matched_patient_ids"] == []


def test_handles_missing_patients_key():
    [c] = _annotate_customer_matches([{"name": "Solo", "mobile": "9000000000"}], "Solo")
    assert c["match"] == {"account": True, "matched_patient_ids": []}
