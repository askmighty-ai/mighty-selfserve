"""
Unit tests for category schema lookup (_get_category_schema).

Verifies that every supported account source resolves to the expected category,
that unknown sources return None, and that all priority_fields strings are non-empty.
"""

import pytest
from app import _get_category_schema, _CATEGORY_SCHEMAS


class TestCategoryLookup:
    def test_delta_is_travel_loyalty(self):
        schema = _get_category_schema("delta")
        assert schema is not None
        assert schema["name"] == "travel loyalty program"

    def test_southwest_is_travel_loyalty(self):
        assert _get_category_schema("southwest")["name"] == "travel loyalty program"

    def test_marriott_is_travel_loyalty(self):
        assert _get_category_schema("marriott")["name"] == "travel loyalty program"

    def test_hilton_is_travel_loyalty(self):
        assert _get_category_schema("hilton")["name"] == "travel loyalty program"

    def test_amex_is_credit_card(self):
        schema = _get_category_schema("amex")
        assert schema is not None
        assert schema["name"] == "credit card"

    def test_chase_is_credit_card(self):
        assert _get_category_schema("chase")["name"] == "credit card"

    def test_xfinity_is_utilities(self):
        schema = _get_category_schema("xfinity")
        assert schema is not None
        assert "utility" in schema["name"]

    def test_netflix_is_subscription(self):
        assert _get_category_schema("netflix")["name"] == "subscription service"

    def test_fidelity_is_banking(self):
        assert _get_category_schema("fidelity")["name"] == "financial account"

    def test_pamf_is_health(self):
        assert _get_category_schema("pamf")["name"] == "healthcare account"

    def test_amazon_is_shopping(self):
        assert _get_category_schema("amazon")["name"] == "retail or rewards account"

    def test_unknown_source_returns_none(self):
        assert _get_category_schema("unknown_site_xyz") is None

    def test_empty_string_returns_none(self):
        assert _get_category_schema("") is None

    def test_none_returns_none(self):
        assert _get_category_schema(None) is None  # type: ignore[arg-type]


class TestSchemaCompleteness:
    def test_all_schemas_have_priority_fields(self):
        for category, schema in _CATEGORY_SCHEMAS.items():
            assert schema.get("priority_fields"), \
                f"Category '{category}' has empty priority_fields"

    def test_all_schemas_have_name(self):
        for category, schema in _CATEGORY_SCHEMAS.items():
            assert schema.get("name"), f"Category '{category}' missing name"

    def test_all_schemas_have_sources(self):
        for category, schema in _CATEGORY_SCHEMAS.items():
            assert schema.get("sources"), f"Category '{category}' has no sources"

    def test_travel_loyalty_priority_includes_key_terms(self):
        schema = _CATEGORY_SCHEMAS["travel_loyalty"]
        pf = schema["priority_fields"].lower()
        for term in ("elite", "miles", "certificate", "upgrade"):
            assert term in pf, f"Travel loyalty priority_fields missing '{term}'"

    def test_credit_card_priority_includes_key_terms(self):
        schema = _CATEGORY_SCHEMAS["credit_card"]
        pf = schema["priority_fields"].lower()
        for term in ("payment", "balance", "autopay"):
            assert term in pf, f"Credit card priority_fields missing '{term}'"

    def test_no_source_appears_in_two_categories(self):
        """A source key must belong to exactly one category."""
        seen: dict[str, str] = {}
        for category, schema in _CATEGORY_SCHEMAS.items():
            for source in schema["sources"]:
                assert source not in seen, \
                    f"Source '{source}' appears in both '{seen[source]}' and '{category}'"
                seen[source] = category
