"""Collection push must send a GraphQL CollectionSortOrder enum, never the
REST spelling ("manual", "best-selling") that Shopify rejected live (91 of 95
collections failed)."""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest

from api.services.shopify_push.collections import (
    build_collection_input,
    graphql_sort_order,
)

REST_TO_ENUM = {
    "manual": "MANUAL",
    "best-selling": "BEST_SELLING",
    "alpha-asc": "ALPHA_ASC",
    "alpha-desc": "ALPHA_DESC",
    "price-asc": "PRICE_ASC",
    "price-desc": "PRICE_DESC",
    "created": "CREATED",
    "created-desc": "CREATED_DESC",
}


@pytest.mark.parametrize("rest,enum", REST_TO_ENUM.items())
def test_rest_values_map(rest, enum):
    assert graphql_sort_order(rest) == enum
    assert graphql_sort_order(rest.replace("-", "_")) == enum
    assert graphql_sort_order(rest.upper()) == enum
    assert graphql_sort_order(f"  {rest.title()} ") == enum


@pytest.mark.parametrize("enum", REST_TO_ENUM.values())
def test_enum_passes_through(enum):
    assert graphql_sort_order(enum) == enum


@pytest.mark.parametrize("bad", ["sideways", "relevance", "", None, 5])
def test_unknown_is_none(bad):
    assert graphql_sort_order(bad) is None


@pytest.mark.parametrize("rest,enum", REST_TO_ENUM.items())
def test_payload_carries_enum(rest, enum):
    inp = build_collection_input({"title": "T", "handle": "t", "sort_order": rest})
    assert inp["sortOrder"] == enum


def test_unknown_sort_order_omitted_from_payload():
    inp = build_collection_input({"title": "T", "handle": "t", "sort_order": "sideways"})
    assert "sortOrder" not in inp
    assert inp["title"] == "T"
