"""
IMS 2.0 - Catalog Autopilot pure-logic tests
=============================================
Model normalization, weighted confidence scoring, and the copyright gate.
No DB / network needed.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.pop("ECOMMERCE_DATABASE_URL", None)

from api.services.catalog_autopilot import (  # noqa: E402
    AUTHORIZED,
    UNVERIFIED,
    image_use_allowed,
    normalize_model,
    run_search,
    score_candidate,
)


def test_normalize_model_collapses_separators():
    assert normalize_model("B 4291") == "B4291"
    assert normalize_model("b-4291") == "B4291"
    assert normalize_model("RX7140") == "RX7140"
    assert normalize_model(None) == ""


def test_score_exact_model_and_brand():
    q = {"brand": "Ray-Ban", "model": "RB4105"}
    c = {"brand": "Ray-Ban", "model": "RB4105"}
    out = score_candidate(q, c)
    assert out["score"] == 1.0
    assert out["matched"]["model"] is True
    assert out["matched"]["brand"] is True


def test_score_partial_model_match_lower():
    q = {"brand": "Ray-Ban", "model": "RB4105"}
    c = {"brand": "Ray-Ban", "model": "RB4105-OR"}  # contains -> 0.6 on model
    out = score_candidate(q, c)
    assert 0.5 < out["score"] < 1.0


def test_score_unprovided_fields_are_neutral():
    # color/size not provided -> they don't drag the score down.
    q = {"brand": "Oakley", "model": "OO9208"}
    c = {"brand": "Oakley", "model": "OO9208", "color": "Black", "size": "58"}
    out = score_candidate(q, c)
    assert out["score"] == 1.0


def test_score_wrong_model_zero():
    q = {"brand": "Ray-Ban", "model": "RB4105"}
    c = {"brand": "Ray-Ban", "model": "RB2140"}
    out = score_candidate(q, c)
    # only brand matches (weight 0.15 of 0.60 assessed) -> 0.25
    assert out["matched"]["model"] is False
    assert out["score"] < 0.5


def test_image_use_gate():
    assert image_use_allowed(AUTHORIZED) is True
    assert image_use_allowed(AUTHORIZED, False) is True
    assert image_use_allowed(UNVERIFIED, False) is False
    assert image_use_allowed(UNVERIFIED, True) is True


def test_run_search_failsoft_without_ecommerce():
    # No ECOMMERCE_DATABASE_URL -> internal source returns nothing, never raises.
    out = run_search("Ray-Ban", "RB4105")
    assert out["candidate_count"] == 0
    assert out["candidates"] == []
    assert any(s["name"] == "internal_bvi" for s in out["sources"])
