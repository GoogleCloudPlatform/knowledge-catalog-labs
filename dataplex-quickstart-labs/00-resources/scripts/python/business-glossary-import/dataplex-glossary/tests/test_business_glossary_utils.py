"""
Unit tests for business_glossary_utils functions.
"""

import pytest
from utils import business_glossary_utils
from utils.error import InvalidTermIdentifierError, InvalidTermNameError
from utils.models import ParsedTermIdentifier


class TestFormatTermDisplayIdentifier:
    """Tests for format_term_display_identifier."""

    def test_basic_formatting(self):
        result = business_glossary_utils.format_term_display_identifier(
            "my-project", "global", "Sales Glossary", "Order ID"
        )
        assert result == "my-project.global.Sales Glossary.Order ID"

    def test_strips_whitespace(self):
        result = business_glossary_utils.format_term_display_identifier(
            "  my-project ", " global ", " Sales Glossary ", " Order ID "
        )
        assert result == "my-project.global.Sales Glossary.Order ID"

    def test_custom_delimiter(self):
        result = business_glossary_utils.format_term_display_identifier(
            "my-project", "global", "Sales Glossary", "Order ID", delimiter="/"
        )
        assert result == "my-project/global/Sales Glossary/Order ID"


class TestParseTermDisplayIdentifier:
    """Tests for parse_term_display_identifier."""

    def test_valid_four_part_identifier(self):
        result = business_glossary_utils.parse_term_display_identifier(
            "my-project.global.Sales Glossary.Order ID"
        )
        assert isinstance(result, ParsedTermIdentifier)
        assert result.project_id == "my-project"
        assert result.location == "global"
        assert result.glossary_display_name == "Sales Glossary"
        assert result.term_display_name == "Order ID"

    def test_term_with_dots_in_name(self):
        result = business_glossary_utils.parse_term_display_identifier(
            "my-project.global.Sales Glossary.Order.ID.v2"
        )
        assert result.project_id == "my-project"
        assert result.location == "global"
        assert result.glossary_display_name == "Sales Glossary"
        assert result.term_display_name == "Order.ID.v2"

    def test_custom_delimiter_slash(self):
        result = business_glossary_utils.parse_term_display_identifier(
            "my-project/us-central1/Finance/Net Revenue", delimiter="/"
        )
        assert result.project_id == "my-project"
        assert result.location == "us-central1"
        assert result.glossary_display_name == "Finance"
        assert result.term_display_name == "Net Revenue"

    def test_invalid_too_few_parts_raises_error(self):
        with pytest.raises(InvalidTermIdentifierError) as exc_info:
            business_glossary_utils.parse_term_display_identifier("my-project.global.OnlyThree")
        assert "Expected format" in str(exc_info.value)

    def test_empty_string_raises_error(self):
        with pytest.raises(InvalidTermIdentifierError):
            business_glossary_utils.parse_term_display_identifier("")

    def test_empty_component_raises_error(self):
        with pytest.raises(InvalidTermIdentifierError) as exc_info:
            business_glossary_utils.parse_term_display_identifier("my-project..Sales.Order")
        assert "non-empty" in str(exc_info.value)


class TestExtractTermResourceFromEntryName:
    """Tests for extract_term_resource_from_entry_name."""

    def test_extracts_from_dataplex_entry_name(self):
        entry_name = (
            "projects/my-proj/locations/global/entryGroups/@dataplex/entries/"
            "projects/my-proj/locations/global/glossaries/my-glossary/terms/my-term"
        )
        result = business_glossary_utils.extract_term_resource_from_entry_name(entry_name)
        assert result == "projects/my-proj/locations/global/glossaries/my-glossary/terms/my-term"

    def test_passes_through_direct_term_resource_name(self):
        term_resource = "projects/my-proj/locations/global/glossaries/my-glossary/terms/my-term"
        result = business_glossary_utils.extract_term_resource_from_entry_name(term_resource)
        assert result == term_resource

    def test_invalid_entry_name_raises_error(self):
        with pytest.raises(InvalidTermNameError):
            business_glossary_utils.extract_term_resource_from_entry_name(
                "projects/my-proj/locations/us/entryGroups/@bigquery/entries/some-table"
            )


class TestColumnExtractionAndFormatting:
    """Tests for extract_column_from_source_path and format_source_path_from_column."""

    def test_extract_column_strips_schema_prefix(self):
        assert business_glossary_utils.extract_column_from_source_path("Schema.order_id") == "order_id"
        assert business_glossary_utils.extract_column_from_source_path("Schema.user.address.zip") == "user.address.zip"

    def test_extract_column_handles_raw_column(self):
        assert business_glossary_utils.extract_column_from_source_path("order_id") == "order_id"

    def test_extract_column_handles_empty(self):
        assert business_glossary_utils.extract_column_from_source_path("") == ""
        assert business_glossary_utils.extract_column_from_source_path(None) == ""

    def test_format_column_prepends_schema_for_bigquery(self):
        assert business_glossary_utils.format_source_path_from_column("order_id", "@bigquery") == "Schema.order_id"
        assert business_glossary_utils.format_source_path_from_column("Schema.order_id", "@bigquery") == "Schema.order_id"

    def test_format_column_prepends_schema_for_custom_group(self):
        assert business_glossary_utils.format_source_path_from_column("custom_field", "custom_group") == "Schema.custom_field"

    def test_format_column_empty_returns_empty(self):
        assert business_glossary_utils.format_source_path_from_column("", "@bigquery") == ""
        assert business_glossary_utils.format_source_path_from_column(None, "@bigquery") == ""


class TestExtractShortId:
    """Tests for extract_short_id."""

    def test_extracts_from_term_resource_path(self):
        assert (
            business_glossary_utils.extract_short_id(
                "projects/my-proj/locations/global/glossaries/my-glossary/terms/term_123"
            )
            == "term_123"
        )

    def test_extracts_from_dataplex_term_entry_path(self):
        assert (
            business_glossary_utils.extract_short_id(
                "projects/my-proj/locations/global/entryGroups/@dataplex/entries/"
                "projects/12345/locations/global/glossaries/g1/terms/my_term"
            )
            == "my_term"
        )

    def test_extracts_from_colon_entry_identifier(self):
        assert (
            business_glossary_utils.extract_short_id(
                "projects/p/locations/l/entryGroups/@dataplex/entries/glossary:g.term:customer_id"
            )
            == "customer_id"
        )

    def test_extracts_from_bigquery_fqn(self):
        assert business_glossary_utils.extract_short_id("bigquery:my_proj.dataset.table_name") == "table_name"

    def test_extracts_from_bigquery_entry(self):
        assert (
            business_glossary_utils.extract_short_id(
                "projects/p/locations/us/entryGroups/@bigquery/entries/bigquery:p.d.orders"
            )
            == "orders"
        )

    def test_passes_through_plain_id(self):
        assert business_glossary_utils.extract_short_id("simple_id") == "simple_id"
        assert business_glossary_utils.extract_short_id("") == ""
        assert business_glossary_utils.extract_short_id(None) == ""
