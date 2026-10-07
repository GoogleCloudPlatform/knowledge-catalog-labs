"""
Unit tests for Dataplex Aspect support in the glossary export/import tooling.

Test coverage:
- parse_aspect_identifier (short / qualified / full resource path / malformed)
- coerce_aspect_value (string, int, float, bool spellings, RFC3339 datetime,
  JSON array, JSON object, comma-list)
- serialize_aspect_value round-trips
- is_system_aspect for BOTH key prefix styles (numeric project number and
  friendly project id)
- flatten_aspect_data / set_nested_aspect_field record handling
- Aggregation of multiple Sheet 2 rows into one aspect data dict
- Missing / empty / header-only Sheet 2 -> no aspects, no exception
- Aspect row validation errors (unknown id, malformed name, empties, dupes)
- Export with mixed system + custom aspects
- api_layer.get_aspect_type caching and permission fallback
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import api_layer, business_glossary_utils, sheet_utils
from utils.constants import ASPECTS_SHEET_NAME, ASPECT_SHEET_HEADERS
from utils.error import AspectValidationError, InvalidAspectIdentifierError
from utils.models import AspectRow, ParsedAspectIdentifier


def _load_module(module_name, relative_path):
    """Load one of the hyphenated standalone scripts as a module."""
    spec = importlib.util.spec_from_file_location(
        module_name, str(Path(__file__).parent.parent / relative_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


glossary_export = _load_module("glossary_export", "export/glossary-export.py")
glossary_import = _load_module("glossary_import", "import/glossary-import.py")


GLOSSARY_URL = (
    "https://console.cloud.google.com/dataplex/dp-glossaries/projects/my-project"
    "/locations/us-central1/glossaries/my-glossary"
)


# ============================================================================
# parse_aspect_identifier
# ============================================================================

class TestParseAspectIdentifier:
    """Tests for parse_aspect_identifier."""

    def test_short_form_uses_defaults(self):
        parsed = business_glossary_utils.parse_aspect_identifier(
            "custom-gov.tier", default_project="my-project", default_location="us-central1"
        )
        assert isinstance(parsed, ParsedAspectIdentifier)
        assert parsed.project_id == "my-project"
        assert parsed.location == "us-central1"
        assert parsed.aspect_type_id == "custom-gov"
        assert parsed.field_name == "tier"

    def test_qualified_form_overrides_defaults(self):
        parsed = business_glossary_utils.parse_aspect_identifier(
            "other-project.europe-west4.custom-gov.tier",
            default_project="my-project",
            default_location="us-central1",
        )
        assert parsed.project_id == "other-project"
        assert parsed.location == "europe-west4"
        assert parsed.aspect_type_id == "custom-gov"
        assert parsed.field_name == "tier"

    def test_qualified_form_global_location(self):
        parsed = business_glossary_utils.parse_aspect_identifier(
            "my-project.global.custom-gov.tier"
        )
        assert parsed.location == "global"
        assert parsed.aspect_type_id == "custom-gov"

    def test_full_resource_path(self):
        parsed = business_glossary_utils.parse_aspect_identifier(
            "projects/p1/locations/us-east1/aspectTypes/custom-gov/tier"
        )
        assert parsed.project_id == "p1"
        assert parsed.location == "us-east1"
        assert parsed.aspect_type_id == "custom-gov"
        assert parsed.field_name == "tier"
        assert parsed.aspect_type_resource == (
            "projects/p1/locations/us-east1/aspectTypes/custom-gov"
        )

    def test_dotted_record_subfield_is_not_mistaken_for_qualified(self):
        """'custom-gov.owner.email' must stay a record sub-field, not project.location."""
        parsed = business_glossary_utils.parse_aspect_identifier(
            "custom-gov.owner.email", default_project="my-project", default_location="global"
        )
        assert parsed.aspect_type_id == "custom-gov"
        assert parsed.field_name == "owner.email"
        assert parsed.field_path == ["owner", "email"]

    def test_deep_dotted_field_without_location_stays_short_form(self):
        parsed = business_glossary_utils.parse_aspect_identifier(
            "custom-gov.owner.address.zip", default_project="p", default_location="global"
        )
        assert parsed.aspect_type_id == "custom-gov"
        assert parsed.field_name == "owner.address.zip"

    def test_aspect_key_property(self):
        parsed = business_glossary_utils.parse_aspect_identifier(
            "custom-gov.tier", default_project="my-project", default_location="us-central1"
        )
        assert parsed.aspect_key == "my-project.us-central1.custom-gov"

    def test_whitespace_is_stripped(self):
        parsed = business_glossary_utils.parse_aspect_identifier("  custom-gov.tier  ")
        assert parsed.aspect_type_id == "custom-gov"
        assert parsed.field_name == "tier"

    @pytest.mark.parametrize("bad_name", ["", "   ", None])
    def test_empty_raises(self, bad_name):
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.parse_aspect_identifier(bad_name)

    def test_single_segment_raises(self):
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.parse_aspect_identifier("custom-gov")

    def test_empty_dotted_segment_raises(self):
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.parse_aspect_identifier("custom-gov..tier")

    def test_resource_path_without_field_raises(self):
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.parse_aspect_identifier(
                "projects/p/locations/global/aspectTypes/custom-gov"
            )

    def test_malformed_resource_path_raises(self):
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.parse_aspect_identifier("projects/p/aspectTypes/x/y")


# ============================================================================
# format_aspect_identifier
# ============================================================================

class TestFormatAspectIdentifier:
    """Tests for format_aspect_identifier."""

    def test_short_form(self):
        assert business_glossary_utils.format_aspect_identifier(
            "custom-gov", "tier"
        ) == "custom-gov.tier"

    def test_qualified_form(self):
        assert business_glossary_utils.format_aspect_identifier(
            "custom-gov", "tier", project_id="my-project", location="us-central1", qualified=True
        ) == "my-project.us-central1.custom-gov.tier"

    def test_dotted_field_preserved(self):
        assert business_glossary_utils.format_aspect_identifier(
            "custom-gov", "owner.email"
        ) == "custom-gov.owner.email"

    def test_missing_field_raises(self):
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.format_aspect_identifier("custom-gov", "")

    def test_qualified_without_project_raises(self):
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.format_aspect_identifier(
                "custom-gov", "tier", location="global", qualified=True
            )

    def test_round_trip_with_parse(self):
        formatted = business_glossary_utils.format_aspect_identifier("custom-gov", "owner.email")
        parsed = business_glossary_utils.parse_aspect_identifier(formatted, "p", "global")
        assert parsed.aspect_type_id == "custom-gov"
        assert parsed.field_name == "owner.email"


# ============================================================================
# coerce_aspect_value
# ============================================================================

class TestCoerceAspectValue:
    """Tests for coerce_aspect_value."""

    def test_plain_string(self):
        assert business_glossary_utils.coerce_aspect_value("HIGH") == "HIGH"

    def test_empty_string(self):
        assert business_glossary_utils.coerce_aspect_value("") == ""

    def test_none(self):
        assert business_glossary_utils.coerce_aspect_value(None) == ""

    def test_integer(self):
        assert business_glossary_utils.coerce_aspect_value("365") == 365

    def test_negative_integer(self):
        assert business_glossary_utils.coerce_aspect_value("-42") == -42

    def test_float(self):
        assert business_glossary_utils.coerce_aspect_value("3.14") == 3.14

    @pytest.mark.parametrize("raw", ["true", "TRUE", "True", "yes", "YES", "y", "t"])
    def test_bool_true_spellings(self, raw):
        assert business_glossary_utils.coerce_aspect_value(raw) is True

    @pytest.mark.parametrize("raw", ["false", "FALSE", "False", "no", "NO", "n", "f"])
    def test_bool_false_spellings(self, raw):
        assert business_glossary_utils.coerce_aspect_value(raw) is False

    def test_numeric_one_is_int_without_schema(self):
        """Without a schema, '1' is an int: numeric interpretation wins."""
        assert business_glossary_utils.coerce_aspect_value("1") == 1
        assert business_glossary_utils.coerce_aspect_value("1") is not True

    def test_numeric_one_is_bool_with_bool_schema(self):
        assert business_glossary_utils.coerce_aspect_value("1", "bool") is True
        assert business_glossary_utils.coerce_aspect_value("0", "bool") is False

    def test_rfc3339_datetime_stays_string(self):
        value = "2024-01-15T10:30:00Z"
        assert business_glossary_utils.coerce_aspect_value(value) == value
        assert business_glossary_utils.coerce_aspect_value(value, "datetime") == value

    def test_json_array(self):
        assert business_glossary_utils.coerce_aspect_value('["finance","core"]') == [
            "finance", "core"
        ]

    def test_json_object(self):
        assert business_glossary_utils.coerce_aspect_value('{"a":1}') == {"a": 1}

    def test_invalid_json_falls_back_to_string(self):
        assert business_glossary_utils.coerce_aspect_value("[not json") == "[not json"

    def test_comma_list_only_when_array_declared(self):
        assert business_glossary_utils.coerce_aspect_value("a,b,c") == "a,b,c"
        assert business_glossary_utils.coerce_aspect_value("a,b,c", "array") == ["a", "b", "c"]

    def test_json_array_wins_over_comma_split_for_array_type(self):
        assert business_glossary_utils.coerce_aspect_value('["a,b"]', "array") == ["a,b"]

    def test_declared_string_type_keeps_numeric_looking_value(self):
        assert business_glossary_utils.coerce_aspect_value("007", "string") == "007"

    def test_declared_enum_type_keeps_verbatim(self):
        assert business_glossary_utils.coerce_aspect_value("HIGH", "enum") == "HIGH"

    def test_declared_int_type(self):
        assert business_glossary_utils.coerce_aspect_value("365", "int") == 365

    def test_declared_double_type(self):
        assert business_glossary_utils.coerce_aspect_value("2", "double") == 2.0

    def test_declared_record_type_parses_json(self):
        assert business_glossary_utils.coerce_aspect_value('{"email":"a@b.c"}', "record") == {
            "email": "a@b.c"
        }

    def test_already_typed_values_pass_through(self):
        assert business_glossary_utils.coerce_aspect_value(True) is True
        assert business_glossary_utils.coerce_aspect_value(7) == 7
        assert business_glossary_utils.coerce_aspect_value(["a"]) == ["a"]

    def test_unknown_declared_type_falls_back_to_heuristics(self):
        assert business_glossary_utils.coerce_aspect_value("42", "mystery") == 42


# ============================================================================
# serialize_aspect_value
# ============================================================================

class TestSerializeAspectValue:
    """Tests for serialize_aspect_value."""

    def test_string(self):
        assert business_glossary_utils.serialize_aspect_value("HIGH") == "HIGH"

    def test_bool(self):
        assert business_glossary_utils.serialize_aspect_value(True) == "true"
        assert business_glossary_utils.serialize_aspect_value(False) == "false"

    def test_int_and_float(self):
        assert business_glossary_utils.serialize_aspect_value(365) == "365"
        assert business_glossary_utils.serialize_aspect_value(3.5) == "3.5"

    def test_none_becomes_empty(self):
        assert business_glossary_utils.serialize_aspect_value(None) == ""

    def test_list_becomes_compact_json(self):
        assert business_glossary_utils.serialize_aspect_value(
            ["finance", "core"]
        ) == '["finance","core"]'

    def test_dict_becomes_compact_json(self):
        assert business_glossary_utils.serialize_aspect_value({"a": 1}) == '{"a":1}'

    @pytest.mark.parametrize("value", [
        "HIGH", True, False, 365, 3.5, ["finance", "core"], {"a": 1},
    ])
    def test_round_trip(self, value):
        """serialize -> coerce returns an equal value."""
        serialized = business_glossary_utils.serialize_aspect_value(value)
        assert business_glossary_utils.coerce_aspect_value(serialized) == value

    def test_round_trip_datetime(self):
        value = "2024-01-15T10:30:00Z"
        assert business_glossary_utils.coerce_aspect_value(
            business_glossary_utils.serialize_aspect_value(value)
        ) == value


# ============================================================================
# is_system_aspect
# ============================================================================

class TestIsSystemAspect:
    """Tests for is_system_aspect across both key prefix styles."""

    @pytest.mark.parametrize("key", [
        "655216118709.global.overview",
        "655216118709.global.contacts",
        "655216118709.global.glossary-term-aspect",
        "655216118709.global.glossary-category-aspect",
    ])
    def test_numeric_project_number_prefix(self, key):
        assert business_glossary_utils.is_system_aspect(key) is True

    @pytest.mark.parametrize("key", [
        "dataplex-types.global.overview",
        "dataplex-types.global.contacts",
        "dataplex-types.global.glossary-term-aspect",
        "dataplex-types.global.glossary-category-aspect",
    ])
    def test_friendly_project_id_prefix(self, key):
        assert business_glossary_utils.is_system_aspect(key) is True

    @pytest.mark.parametrize("key", [
        "my-project.us-central1.custom-gov",
        "my-project.global.custom-gov",
        "655216118709.global.my-custom-aspect",
        "custom-gov",
    ])
    def test_custom_aspects_are_not_system(self, key):
        assert business_glossary_utils.is_system_aspect(key) is False

    def test_bare_aspect_type_id(self):
        assert business_glossary_utils.is_system_aspect("overview") is True
        assert business_glossary_utils.is_system_aspect("contacts") is True

    def test_resource_name_form(self):
        assert business_glossary_utils.is_system_aspect(
            "projects/dataplex-types/locations/global/aspectTypes/overview"
        ) is True
        assert business_glossary_utils.is_system_aspect(
            "projects/my-project/locations/global/aspectTypes/custom-gov"
        ) is False

    def test_empty_is_not_system(self):
        assert business_glossary_utils.is_system_aspect("") is False


# ============================================================================
# flatten_aspect_data / set_nested_aspect_field
# ============================================================================

class TestFlattenAspectData:
    """Tests for flatten_aspect_data."""

    def test_flat_scalars(self):
        assert business_glossary_utils.flatten_aspect_data(
            {"tier": "HIGH", "is_pii": True, "retention_days": 365}
        ) == [("tier", "HIGH"), ("is_pii", "true"), ("retention_days", "365")]

    def test_one_level_record_is_dotted(self):
        assert business_glossary_utils.flatten_aspect_data(
            {"owner": {"email": "a@b.c", "name": "A"}}
        ) == [("owner.email", "a@b.c"), ("owner.name", "A")]

    def test_deeper_nesting_becomes_json(self):
        """The documented boundary: depth 2 is dotted, deeper is JSON."""
        assert business_glossary_utils.flatten_aspect_data(
            {"owner": {"meta": {"x": 1}}}
        ) == [("owner.meta", '{"x":1}')]

    def test_arrays_are_json(self):
        assert business_glossary_utils.flatten_aspect_data(
            {"tags": ["finance", "core"]}
        ) == [("tags", '["finance","core"]')]

    def test_empty_dict(self):
        assert business_glossary_utils.flatten_aspect_data({}) == []

    def test_non_dict_input(self):
        assert business_glossary_utils.flatten_aspect_data("not a dict") == []

    def test_round_trip_through_nested_rebuild(self):
        original = {"tier": "HIGH", "owner": {"email": "a@b.c"}, "tags": ["x"]}
        rebuilt = {}
        for field_name, cell_value in business_glossary_utils.flatten_aspect_data(original):
            business_glossary_utils.set_nested_aspect_field(
                rebuilt,
                field_name.split('.'),
                business_glossary_utils.coerce_aspect_value(cell_value),
            )
        assert rebuilt == original


class TestSetNestedAspectField:
    """Tests for set_nested_aspect_field."""

    def test_flat_assignment(self):
        data = {}
        business_glossary_utils.set_nested_aspect_field(data, ["tier"], "HIGH")
        assert data == {"tier": "HIGH"}

    def test_nested_assignment_creates_intermediates(self):
        data = {}
        business_glossary_utils.set_nested_aspect_field(data, ["owner", "email"], "a@b.c")
        assert data == {"owner": {"email": "a@b.c"}}

    def test_sibling_nested_fields_merge(self):
        data = {}
        business_glossary_utils.set_nested_aspect_field(data, ["owner", "email"], "a@b.c")
        business_glossary_utils.set_nested_aspect_field(data, ["owner", "name"], "A")
        assert data == {"owner": {"email": "a@b.c", "name": "A"}}

    def test_empty_path_raises(self):
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.set_nested_aspect_field({}, [], "x")

    def test_scalar_collision_raises(self):
        data = {"owner": "someone"}
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.set_nested_aspect_field(data, ["owner", "email"], "a@b.c")


class TestAspectKeyHelpers:
    """Tests for extract_aspect_type_id and aspect_key_from_resource."""

    def test_extract_from_aspect_key(self):
        assert business_glossary_utils.extract_aspect_type_id(
            "655216118709.global.custom-gov"
        ) == "custom-gov"

    def test_extract_from_resource(self):
        assert business_glossary_utils.extract_aspect_type_id(
            "projects/p/locations/global/aspectTypes/custom-gov"
        ) == "custom-gov"

    def test_extract_from_bare_id(self):
        assert business_glossary_utils.extract_aspect_type_id("custom-gov") == "custom-gov"

    def test_key_from_resource(self):
        assert business_glossary_utils.aspect_key_from_resource(
            "projects/my-project/locations/us-central1/aspectTypes/custom-gov"
        ) == "my-project.us-central1.custom-gov"

    def test_key_from_resource_passthrough(self):
        assert business_glossary_utils.aspect_key_from_resource("already.a.key") == "already.a.key"

    def test_key_from_empty(self):
        assert business_glossary_utils.aspect_key_from_resource("") == ""


# ============================================================================
# models.AspectRow
# ============================================================================

class TestAspectRow:
    """Tests for the AspectRow model."""

    def test_from_dict_canonical_headers(self):
        row = AspectRow.from_dict(
            {"id": " term-1 ", "Aspect name": " custom-gov.tier ", "Aspect value": " HIGH "},
            row_number=2,
        )
        assert row.term_id == "term-1"
        assert row.aspect_name == "custom-gov.tier"
        assert row.aspect_value == "HIGH"
        assert row.row_number == 2

    def test_from_dict_snake_case_headers(self):
        row = AspectRow.from_dict(
            {"term_id": "term-1", "aspect_name": "custom-gov.tier", "aspect_value": "HIGH"}
        )
        assert row.term_id == "term-1"
        assert row.aspect_name == "custom-gov.tier"

    def test_from_dict_missing_keys(self):
        row = AspectRow.from_dict({})
        assert row.is_empty() is True

    def test_to_row(self):
        row = AspectRow(term_id="t", aspect_name="a.b", aspect_value="v")
        assert row.to_row() == ["t", "a.b", "v"]


# ============================================================================
# sheet_utils Sheet 2 helpers
# ============================================================================

class TestRowsToAspectRows:
    """Tests for sheet_utils.rows_to_aspect_rows."""

    def test_parses_canonical_sheet(self):
        rows = sheet_utils.rows_to_aspect_rows([
            ASPECT_SHEET_HEADERS,
            ["term-1", "custom-gov.tier", "HIGH"],
            ["term-1", "custom-gov.is_pii", "true"],
        ])
        assert len(rows) == 2
        assert rows[0].term_id == "term-1"
        assert rows[0].row_number == 2
        assert rows[1].row_number == 3

    def test_empty_sheet_returns_empty(self):
        assert sheet_utils.rows_to_aspect_rows([]) == []

    def test_header_only_returns_empty(self):
        assert sheet_utils.rows_to_aspect_rows([ASPECT_SHEET_HEADERS]) == []

    def test_blank_rows_are_skipped(self):
        rows = sheet_utils.rows_to_aspect_rows([
            ASPECT_SHEET_HEADERS,
            ["", "", ""],
            ["term-1", "custom-gov.tier", "HIGH"],
        ])
        assert len(rows) == 1
        assert rows[0].row_number == 3

    def test_short_rows_are_padded(self):
        rows = sheet_utils.rows_to_aspect_rows([
            ASPECT_SHEET_HEADERS,
            ["term-1", "custom-gov.tier"],
        ])
        assert rows[0].aspect_value == ""

    def test_case_insensitive_headers(self):
        rows = sheet_utils.rows_to_aspect_rows([
            ["ID", "ASPECT NAME", "ASPECT VALUE"],
            ["term-1", "custom-gov.tier", "HIGH"],
        ])
        assert len(rows) == 1

    def test_unrecognized_headers_ignored(self):
        assert sheet_utils.rows_to_aspect_rows([
            ["foo", "bar", "baz"],
            ["term-1", "custom-gov.tier", "HIGH"],
        ]) == []


class TestResolveAspectsSheetName:
    """Tests for the Sheet 2 name override."""

    def test_default(self, monkeypatch):
        monkeypatch.delenv("GLOSSARY_ASPECTS_SHEET_NAME", raising=False)
        assert sheet_utils.resolve_aspects_sheet_name() == ASPECTS_SHEET_NAME

    def test_explicit_argument_wins(self, monkeypatch):
        monkeypatch.setenv("GLOSSARY_ASPECTS_SHEET_NAME", "EnvSheet")
        assert sheet_utils.resolve_aspects_sheet_name("ArgSheet") == "ArgSheet"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("GLOSSARY_ASPECTS_SHEET_NAME", "Aspects")
        assert sheet_utils.resolve_aspects_sheet_name() == "Aspects"


class TestEnsureSheetExists:
    """Tests for sheet_utils.ensure_sheet_exists."""

    def test_returns_existing_sheet_id(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet2', 'sheetId': 99}}]
        }
        assert sheet_utils.ensure_sheet_exists(mock_sheets_client, 'sid', 'Sheet2') == 99
        mock_sheets_client.spreadsheets.return_value.batchUpdate.assert_not_called()

    def test_creates_missing_sheet(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet1', 'sheetId': 0}}]
        }
        mock_sheets_client.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {
            'replies': [{'addSheet': {'properties': {'title': 'Sheet2', 'sheetId': 42}}}]
        }
        assert sheet_utils.ensure_sheet_exists(mock_sheets_client, 'sid', 'Sheet2') == 42
        mock_sheets_client.spreadsheets.return_value.batchUpdate.assert_called_once()


class TestSheetExists:
    """Tests for sheet_utils.sheet_exists."""

    def test_true_when_present(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet2'}}]
        }
        assert sheet_utils.sheet_exists(mock_sheets_client, 'sid', 'Sheet2') is True

    def test_false_when_absent(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet1'}}]
        }
        assert sheet_utils.sheet_exists(mock_sheets_client, 'sid', 'Sheet2') is False

    def test_false_on_api_error(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.side_effect = (
            Exception("boom")
        )
        assert sheet_utils.sheet_exists(mock_sheets_client, 'sid', 'Sheet2') is False


class TestReadAspectRows:
    """Tests for sheet_utils.read_aspect_rows."""

    def test_missing_sheet_returns_empty(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet1'}}]
        }
        with patch.object(sheet_utils, 'authenticate_sheets', return_value=mock_sheets_client):
            rows = sheet_utils.read_aspect_rows(
                'https://docs.google.com/spreadsheets/d/abc123/edit'
            )
        assert rows == []

    def test_reads_rows_when_present(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet2', 'sheetId': 1}}]
        }
        mock_sheets_client.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            'values': [ASPECT_SHEET_HEADERS, ['term-1', 'custom-gov.tier', 'HIGH']]
        }
        with patch.object(sheet_utils, 'authenticate_sheets', return_value=mock_sheets_client):
            rows = sheet_utils.read_aspect_rows(
                'https://docs.google.com/spreadsheets/d/abc123/edit'
            )
        assert len(rows) == 1
        assert rows[0].aspect_name == 'custom-gov.tier'


class TestWriteAspectRows:
    """Tests for sheet_utils.write_aspect_rows."""

    def test_writes_to_target_sheet(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet2', 'sheetId': 7}}]
        }
        result = sheet_utils.write_aspect_rows(
            mock_sheets_client, 'sid', [ASPECT_SHEET_HEADERS, ['t', 'a.b', 'v']]
        )
        assert result == 'Sheet2'
        update_call = mock_sheets_client.spreadsheets.return_value.values.return_value.update
        assert update_call.call_args.kwargs['range'] == "'Sheet2'!A1"


# ============================================================================
# api_layer.get_aspect_type
# ============================================================================

class TestGetAspectType:
    """Tests for api_layer.get_aspect_type and get_aspect_field_types."""

    def setup_method(self):
        api_layer.clear_caches()

    def teardown_method(self):
        api_layer.clear_caches()

    def _mock_service(self, response=None, side_effect=None):
        service = MagicMock()
        get_request = service.projects.return_value.locations.return_value.aspectTypes.return_value.get
        if side_effect is not None:
            get_request.return_value.execute.side_effect = side_effect
        else:
            get_request.return_value.execute.return_value = response
        return service

    def test_fetches_and_caches(self):
        service = self._mock_service({'name': 'at', 'metadataTemplate': {}})
        resource = 'projects/p/locations/global/aspectTypes/custom-gov'

        first = api_layer.get_aspect_type(service, resource)
        second = api_layer.get_aspect_type(service, resource)

        assert first == second
        get_request = service.projects.return_value.locations.return_value.aspectTypes.return_value.get
        assert get_request.call_count == 1

    def test_empty_resource_returns_none(self):
        assert api_layer.get_aspect_type(MagicMock(), '') is None

    def test_permission_denied_returns_none_and_caches(self):
        from googleapiclient.errors import HttpError
        response = MagicMock()
        response.status = 403
        service = self._mock_service(side_effect=HttpError(response, b'denied'))
        resource = 'projects/p/locations/global/aspectTypes/custom-gov'

        assert api_layer.get_aspect_type(service, resource) is None
        assert api_layer.get_aspect_type(service, resource) is None
        get_request = service.projects.return_value.locations.return_value.aspectTypes.return_value.get
        assert get_request.call_count == 1

    def test_generic_error_returns_none(self):
        service = self._mock_service(side_effect=Exception("boom"))
        assert api_layer.get_aspect_type(
            service, 'projects/p/locations/global/aspectTypes/custom-gov'
        ) is None

    def test_field_types_flattened(self):
        service = self._mock_service({
            'name': 'at',
            'metadataTemplate': {
                'recordFields': [
                    {'name': 'tier', 'type': 'string'},
                    {'name': 'is_pii', 'type': 'bool'},
                    {'name': 'retention_days', 'type': 'int'},
                    {
                        'name': 'owner',
                        'type': 'record',
                        'recordFields': [{'name': 'email', 'type': 'string'}],
                    },
                ]
            },
        })
        field_types = api_layer.get_aspect_field_types(
            service, 'projects/p/locations/global/aspectTypes/custom-gov'
        )
        assert field_types == {
            'tier': 'string',
            'is_pii': 'bool',
            'retention_days': 'int',
            'owner': 'record',
            'owner.email': 'string',
        }

    def test_field_types_empty_when_unavailable(self):
        service = self._mock_service(side_effect=Exception("boom"))
        assert api_layer.get_aspect_field_types(
            service, 'projects/p/locations/global/aspectTypes/custom-gov'
        ) == {}


# ============================================================================
# Export: mixed system + custom aspects
# ============================================================================

def _entry_with_aspects(aspects):
    return {'aspects': aspects}


TERM = {
    'name': 'projects/my-project/locations/us-central1/glossaries/g/terms/term-customer-id',
    'displayName': 'Customer ID',
}

CATEGORY = {
    'name': 'projects/my-project/locations/us-central1/glossaries/g/categories/cat-core',
    'displayName': 'Core',
    'parent': 'projects/my-project/locations/us-central1/glossaries/g',
}

MIXED_ASPECTS = {
    '655216118709.global.overview': {'data': {'content': '<p>An overview</p>'}},
    '655216118709.global.contacts': {'data': {'identities': [
        {'role': 'steward', 'name': 'A', 'id': 'a@b.c'}
    ]}},
    '655216118709.global.glossary-term-aspect': {'data': {}},
    'my-project.us-central1.custom-gov': {'data': {
        'tier': 'HIGH', 'is_pii': True, 'retention_days': 365
    }},
}


class TestExportGetAspectsFromEntry:
    """Tests for glossary-export._get_aspects_from_entry."""

    def test_preserves_overview_and_identities_contract(self):
        result = glossary_export._get_aspects_from_entry(_entry_with_aspects(MIXED_ASPECTS))
        assert result['overview'] == '<p>An overview</p>'
        assert result['identities'] == [{'role': 'steward', 'name': 'A', 'id': 'a@b.c'}]

    def test_custom_aspects_collected(self):
        result = glossary_export._get_aspects_from_entry(_entry_with_aspects(MIXED_ASPECTS))
        assert list(result['custom'].keys()) == ['my-project.us-central1.custom-gov']

    def test_system_aspects_excluded_from_custom(self):
        result = glossary_export._get_aspects_from_entry(_entry_with_aspects(MIXED_ASPECTS))
        for key in result['custom']:
            assert 'overview' not in key
            assert 'contacts' not in key
            assert 'glossary-term-aspect' not in key

    def test_friendly_project_id_system_keys_also_excluded(self):
        aspects = {
            'dataplex-types.global.overview': {'data': {'content': 'x'}},
            'dataplex-types.global.contacts': {'data': {'identities': []}},
            'dataplex-types.global.glossary-category-aspect': {'data': {}},
            'my-project.global.custom-gov': {'data': {'tier': 'LOW'}},
        }
        result = glossary_export._get_aspects_from_entry(_entry_with_aspects(aspects))
        assert list(result['custom'].keys()) == ['my-project.global.custom-gov']

    def test_no_aspects(self):
        result = glossary_export._get_aspects_from_entry(_entry_with_aspects({}))
        assert result['custom'] == {}
        assert result['overview'] == ''
        assert result['identities'] == []

    def test_aspect_without_data_skipped(self):
        result = glossary_export._get_aspects_from_entry(
            _entry_with_aspects({'my-project.global.custom-gov': {}})
        )
        assert result['custom'] == {}


class TestExportAspectRows:
    """Tests for glossary-export._get_aspect_rows_for_entry."""

    def test_term_rows(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM, _entry_with_aspects(MIXED_ASPECTS)
        )
        assert rows == [
            ['term-customer-id', 'custom-gov.tier', 'HIGH'],
            ['term-customer-id', 'custom-gov.is_pii', 'true'],
            ['term-customer-id', 'custom-gov.retention_days', '365'],
        ]

    def test_category_rows(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            CATEGORY,
            _entry_with_aspects({'my-project.global.custom-gov': {'data': {'tier': 'LOW'}}}),
        )
        assert rows == [['cat-core', 'custom-gov.tier', 'LOW']]

    def test_record_fields_are_dotted(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({
                'my-project.global.custom-gov': {'data': {'owner': {'email': 'a@b.c'}}}
            }),
        )
        assert rows == [['term-customer-id', 'custom-gov.owner.email', 'a@b.c']]

    def test_arrays_are_json(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({
                'my-project.global.custom-gov': {'data': {'tags': ['finance', 'core']}}
            }),
        )
        assert rows == [['term-customer-id', 'custom-gov.tags', '["finance","core"]']]

    def test_no_custom_aspects_returns_empty(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({'655216118709.global.overview': {'data': {'content': 'x'}}}),
        )
        assert rows == []

    def test_unparseable_name_returns_empty(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            {'name': 'not-a-resource'}, _entry_with_aspects(MIXED_ASPECTS)
        )
        assert rows == []


class TestExportWriteAspectsToSheet:
    """Tests for glossary-export._write_aspects_to_sheet."""

    def test_no_rows_writes_nothing(self, mock_sheets_client):
        glossary_export._write_aspects_to_sheet(mock_sheets_client, 'sid', [])
        mock_sheets_client.spreadsheets.return_value.values.return_value.update.assert_not_called()

    def test_writes_header_and_rows(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet2', 'sheetId': 3}}]
        }
        glossary_export._write_aspects_to_sheet(
            mock_sheets_client, 'sid', [['term-1', 'custom-gov.tier', 'HIGH']]
        )
        update_call = mock_sheets_client.spreadsheets.return_value.values.return_value.update
        body = update_call.call_args.kwargs['body']
        assert body['values'][0] == ASPECT_SHEET_HEADERS
        assert body['values'][1] == ['term-1', 'custom-gov.tier', 'HIGH']
        assert update_call.call_args.kwargs['range'] == "'Sheet2'!A1"

    def test_clears_stale_rows_before_writing(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet2', 'sheetId': 3}}]
        }
        glossary_export._write_aspects_to_sheet(
            mock_sheets_client, 'sid', [['term-1', 'custom-gov.tier', 'HIGH']]
        )
        clear_call = mock_sheets_client.spreadsheets.return_value.values.return_value.clear
        clear_call.assert_called_once()
        assert clear_call.call_args.kwargs['range'] == "'Sheet2'!A:ZZ"

    def test_sheet1_write_path_unchanged(self, mock_sheets_client):
        """Without a sheet name the range stays the original bare 'A1'."""
        glossary_export._write_to_sheet(mock_sheets_client, 'sid', [['a']])
        update_call = mock_sheets_client.spreadsheets.return_value.values.return_value.update
        assert update_call.call_args.kwargs['range'] == 'A1'


# ============================================================================
# Import: Sheet 2 reading, validation and payload construction
# ============================================================================

def _make_processor():
    with patch.object(glossary_import, 'gspread'):
        processor = glossary_import.SheetProcessor(
            'https://docs.google.com/spreadsheets/d/abc123/edit', GLOSSARY_URL, MagicMock()
        )
    # Never reach out for AspectType schemas in unit tests.
    processor._dataplex_service = False
    return processor


def _mock_spreadsheet(values, raise_not_found=False):
    spreadsheet = MagicMock()
    if raise_not_found:
        spreadsheet.worksheet.side_effect = glossary_import.gspread.exceptions.WorksheetNotFound()
    else:
        worksheet = MagicMock()
        worksheet.get_all_values.return_value = values
        spreadsheet.worksheet.return_value = worksheet
    return spreadsheet


class TestImportReadAspects:
    """Tests for SheetProcessor.read_aspects."""

    def test_missing_sheet_is_a_no_op(self):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(
            _mock_spreadsheet(None, raise_not_found=True), {'term-1'}
        )
        assert is_valid is True
        assert aspects == {}

    def test_empty_sheet_is_a_no_op(self):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([]), {'term-1'})
        assert is_valid is True
        assert aspects == {}

    def test_header_only_sheet_is_a_no_op(self):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(
            _mock_spreadsheet([ASPECT_SHEET_HEADERS]), {'term-1'}
        )
        assert is_valid is True
        assert aspects == {}

    def test_rows_aggregate_into_one_data_dict(self):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tier', 'HIGH'],
            ['term-1', 'custom-gov.is_pii', 'true'],
            ['term-1', 'custom-gov.retention_days', '365'],
        ]), {'term-1'})

        assert is_valid is True
        resource = 'projects/my-project/locations/us-central1/aspectTypes/custom-gov'
        assert aspects == {
            'term-1': {resource: {'tier': 'HIGH', 'is_pii': True, 'retention_days': 365}}
        }

    def test_multiple_aspect_types_for_one_term(self):
        processor = _make_processor()
        _, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tier', 'HIGH'],
            ['term-1', 'custom-quality.score', '9'],
        ]), {'term-1'})
        assert len(aspects['term-1']) == 2

    def test_qualified_names_resolve_to_their_own_project(self):
        processor = _make_processor()
        _, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'other-project.europe-west4.custom-gov.tier', 'HIGH'],
        ]), {'term-1'})
        assert 'projects/other-project/locations/europe-west4/aspectTypes/custom-gov' in (
            aspects['term-1']
        )

    def test_record_subfields_rebuild_nested_dict(self):
        processor = _make_processor()
        _, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.owner.email', 'a@b.c'],
            ['term-1', 'custom-gov.owner.name', 'A'],
        ]), {'term-1'})
        resource = 'projects/my-project/locations/us-central1/aspectTypes/custom-gov'
        assert aspects['term-1'][resource] == {'owner': {'email': 'a@b.c', 'name': 'A'}}

    def test_json_values_are_parsed(self):
        processor = _make_processor()
        _, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tags', '["finance","core"]'],
        ]), {'term-1'})
        resource = 'projects/my-project/locations/us-central1/aspectTypes/custom-gov'
        assert aspects['term-1'][resource] == {'tags': ['finance', 'core']}

    def test_aspect_type_schema_drives_coercion(self):
        processor = _make_processor()
        processor._dataplex_service = MagicMock()
        with patch.object(
            glossary_import.api_layer, 'get_aspect_field_types',
            return_value={'flag': 'bool', 'tags': 'array'}
        ):
            _, aspects = processor.read_aspects(_mock_spreadsheet([
                ASPECT_SHEET_HEADERS,
                ['term-1', 'custom-gov.flag', '1'],
                ['term-1', 'custom-gov.tags', 'a,b'],
            ]), {'term-1'})
        resource = 'projects/my-project/locations/us-central1/aspectTypes/custom-gov'
        assert aspects['term-1'][resource] == {'flag': True, 'tags': ['a', 'b']}

    def test_schema_lookup_failure_falls_back_to_heuristics(self):
        processor = _make_processor()
        processor._dataplex_service = MagicMock()
        with patch.object(
            glossary_import.api_layer, 'get_aspect_field_types', side_effect=Exception("denied")
        ):
            is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
                ASPECT_SHEET_HEADERS,
                ['term-1', 'custom-gov.flag', '1'],
            ]), {'term-1'})
        resource = 'projects/my-project/locations/us-central1/aspectTypes/custom-gov'
        assert is_valid is True
        assert aspects['term-1'][resource] == {'flag': 1}


class TestImportAspectValidation:
    """Tests for aspect row validation."""

    def test_unknown_id_is_rejected(self, capsys):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['ghost-term', 'custom-gov.tier', 'HIGH'],
        ]), {'term-1'})
        assert is_valid is False
        assert aspects == {}
        assert "row 2" in capsys.readouterr().out

    def test_missing_id_is_rejected(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['', 'custom-gov.tier', 'HIGH'],
        ]), {'term-1'})
        assert is_valid is False
        assert "row 2" in capsys.readouterr().out

    def test_missing_aspect_name_is_rejected(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', '', 'HIGH'],
        ]), {'term-1'})
        assert is_valid is False
        assert "row 2" in capsys.readouterr().out

    def test_malformed_aspect_name_is_rejected(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'no-field-part', 'HIGH'],
        ]), {'term-1'})
        assert is_valid is False
        assert "row 2" in capsys.readouterr().out

    def test_duplicate_pair_is_rejected(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tier', 'HIGH'],
            ['term-1', 'custom-gov.tier', 'LOW'],
        ]), {'term-1'})
        assert is_valid is False
        assert "row 3" in capsys.readouterr().out

    def test_same_field_on_different_terms_is_allowed(self):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tier', 'HIGH'],
            ['term-2', 'custom-gov.tier', 'LOW'],
        ]), {'term-1', 'term-2'})
        assert is_valid is True
        assert len(aspects) == 2

    def test_system_aspect_is_rejected(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'dataplex-types.global.overview.content', '<p>x</p>'],
        ]), {'term-1'})
        assert is_valid is False
        assert "row 2" in capsys.readouterr().out


class TestImportConvertToImportItem:
    """Tests for merging custom aspects into the import payload."""

    def _row(self, row_id='term-customer-id', row_type='TERM'):
        return {
            glossary_import.ID_COLUMN: row_id,
            'type': row_type,
            glossary_import.DISPLAY_NAME_COLUMN_NAME: 'Customer ID',
            glossary_import.DESCRIPTION_COLUMN_NAME: 'desc',
            glossary_import.OVERVIEW_COLUMN_NAME: '<p>o</p>',
            glossary_import.CONTACT1_EMAIL_COLUMN_NAME: '',
            glossary_import.CONTACT1_NAME_COLUMN_NAME: '',
            glossary_import.CONTACT2_EMAIL_COLUMN_NAME: '',
            glossary_import.CONTACT2_NAME_COLUMN_NAME: '',
            glossary_import.LABEL1_KEY_COLUMN_NAME: '',
            glossary_import.LABEL1_VALUE_COLUMN_NAME: '',
            glossary_import.LABEL2_KEY_COLUMN_NAME: '',
            glossary_import.LABEL2_VALUE_COLUMN_NAME: '',
            glossary_import.ENTRY_NAME_COLUMN: 'entry-name',
        }

    def test_no_aspects_payload_unchanged(self):
        processor = _make_processor()
        item = processor._convert_to_import_item(self._row(), [])
        assert set(item['entry']['aspects'].keys()) == {
            'dataplex-types.global.glossary-term-aspect',
            'dataplex-types.global.overview',
            'dataplex-types.global.contacts',
        }

    def test_custom_aspect_merged_for_term(self):
        processor = _make_processor()
        processor.aspects_by_id = {
            'term-customer-id': {
                'projects/my-project/locations/us-central1/aspectTypes/custom-gov': {
                    'tier': 'HIGH', 'is_pii': True, 'retention_days': 365
                }
            }
        }
        aspects = processor._convert_to_import_item(self._row(), [])['entry']['aspects']

        assert aspects['my-project.us-central1.custom-gov'] == {
            'aspectType': 'projects/my-project/locations/us-central1/aspectTypes/custom-gov',
            'data': {'tier': 'HIGH', 'is_pii': True, 'retention_days': 365},
        }

    def test_system_aspects_not_clobbered(self):
        processor = _make_processor()
        processor.aspects_by_id = {
            'term-customer-id': {
                'projects/dataplex-types/locations/global/aspectTypes/overview': {
                    'content': 'HIJACKED'
                }
            }
        }
        aspects = processor._convert_to_import_item(self._row(), [])['entry']['aspects']

        assert aspects['dataplex-types.global.overview'] == {'data': {'content': '<p>o</p>'}}

    def test_custom_aspect_merged_for_category(self):
        processor = _make_processor()
        processor.aspects_by_id = {
            'cat-core': {
                'projects/my-project/locations/us-central1/aspectTypes/custom-gov': {'tier': 'LOW'}
            }
        }
        aspects = processor._convert_to_import_item(
            self._row(row_id='cat-core', row_type='CATEGORY'), []
        )['entry']['aspects']

        assert 'dataplex-types.global.glossary-category-aspect' in aspects
        assert aspects['my-project.us-central1.custom-gov']['data'] == {'tier': 'LOW'}

    def test_payload_is_json_serializable(self):
        processor = _make_processor()
        processor.aspects_by_id = {
            'term-customer-id': {
                'projects/my-project/locations/us-central1/aspectTypes/custom-gov': {
                    'tier': 'HIGH', 'owner': {'email': 'a@b.c'}, 'tags': ['x']
                }
            }
        }
        item = processor._convert_to_import_item(self._row(), [])
        assert json.loads(json.dumps(item))['entryLink'] is None

    def test_get_aspect_type_resources_returns_sorted_unique_resources(self):
        processor = _make_processor()
        processor.aspects_by_id = {
            'term-1': {
                'projects/my-project/locations/us-central1/aspectTypes/z-aspect': {'k': 'v'},
                'projects/my-project/locations/us-central1/aspectTypes/a-aspect': {'k': 'v'},
            },
            'term-2': {
                'projects/my-project/locations/us-central1/aspectTypes/a-aspect': {'k': 'v2'},
            },
        }
        assert processor.get_aspect_type_resources() == [
            'projects/my-project/locations/us-central1/aspectTypes/a-aspect',
            'projects/my-project/locations/us-central1/aspectTypes/z-aspect',
        ]

    def test_create_dataplex_metadata_job_includes_aspect_types_only_when_present(self, monkeypatch):
        captured_bodies = []

        class _FakeService:
            def projects(self):
                return self

            def locations(self):
                return self

            def metadataJobs(self):
                return self

            def create(self, parent, metadataJobId, body):
                captured_bodies.append(body)

                class _Req:
                    def execute(self_inner):
                        return {'name': 'operations/op-1', 'done': True}

                return _Req()

        monkeypatch.setattr(glossary_import.google.auth, 'default', lambda: ('creds', 'proj'))
        monkeypatch.setattr(glossary_import, 'build', lambda *a, **kw: _FakeService())

        # Without custom aspects -> scope only has glossaries
        glossary_import.create_dataplex_metadata_job(
            'my-project', 'us-central1', 'job-1', 'my-bucket',
            'projects/my-project/locations/us-central1/glossaries/my-glossary',
        )
        assert captured_bodies[-1]['import_spec']['scope'] == {
            'glossaries': 'projects/my-project/locations/us-central1/glossaries/my-glossary'
        }

        # With custom aspects -> scope includes aspect_types
        glossary_import.create_dataplex_metadata_job(
            'my-project', 'us-central1', 'job-2', 'my-bucket',
            'projects/my-project/locations/us-central1/glossaries/my-glossary',
            aspect_types=['projects/my-project/locations/us-central1/aspectTypes/custom-gov'],
        )
        assert captured_bodies[-1]['import_spec']['scope'] == {
            'glossaries': 'projects/my-project/locations/us-central1/glossaries/my-glossary',
            'aspect_types': ['projects/my-project/locations/us-central1/aspectTypes/custom-gov'],
        }

