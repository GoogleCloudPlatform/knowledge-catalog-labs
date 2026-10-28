"""
Unit tests for Dataplex Aspect support in the glossary export/import tooling.

Test coverage:
- parse_aspect_identifier (short / qualified / full resource path / malformed)
- coerce_aspect_value (string, int, float, bool spellings, RFC3339 datetime,
  JSON array, JSON object, comma-list)
- serialize_aspect_value round-trips
- is_system_aspect by aspect type id in any project (matching the Dataplex
  glossary import validation), for both project spellings
- split_aspect_key / format_aspect_identifier_for_sheet (export round trip)
- flatten_aspect_data / set_nested_aspect_field / find_conflicting_field_path
- Aggregation of multiple Sheet 2 rows into one aspect data dict
- Missing / empty / header-only Sheet 2 -> no aspects plus a warning
- Unreadable Sheet 2 or a bad header -> the import fails
- Aspect row validation errors (unknown id, malformed name, empties, canonical
  duplicates, overlapping fields, reserved aspect type ids)
- Custom aspects are for terms only: category ids are rejected, an id shared
  by a term and a category goes to the term with a warning, and export skips
  (and warns about) custom aspects on categories
- Export with mixed system + custom aspects, qualified names for aspect types
  outside the glossary's project/location, and stale Sheet 2 clearing
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
from utils.error import (
    AspectSheetHeaderError,
    AspectValidationError,
    InvalidAspectIdentifierError,
    SheetsAPIError,
)
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

    @pytest.mark.parametrize("name", [
        "Custom-Gov.Tier",
        "My-Project.US-Central1.Custom-Gov.Tier",
        "projects/My-Project/locations/US-Central1/aspectTypes/Custom-Gov/Tier",
    ])
    def test_project_location_and_type_are_lowercased_field_keeps_case(self, name):
        parsed = business_glossary_utils.parse_aspect_identifier(
            name, default_project="My-Project", default_location="US-Central1"
        )
        assert parsed.aspect_type_resource == (
            "projects/my-project/locations/us-central1/aspectTypes/custom-gov"
        )
        assert parsed.field_name == "Tier"

    @pytest.mark.parametrize("name", [
        "projects/p/locations/l/aspectTypes/at/.",
        "projects/p/locations/l/aspectTypes/at/owner..email",
        "projects/p/locations/l/aspectTypes/at/owner//email",
        "projects/p/locations/l/aspectTypes/at/owner/ /email",
    ])
    def test_resource_path_with_empty_field_segments_is_rejected(self, name):
        with pytest.raises(InvalidAspectIdentifierError, match="Dotted segments must not be empty"):
            business_glossary_utils.parse_aspect_identifier(name)

    def test_resource_path_nested_field_is_dotted(self):
        parsed = business_glossary_utils.parse_aspect_identifier(
            "projects/p/locations/l/aspectTypes/at/owner/email"
        )
        assert parsed.field_name == "owner.email"

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


class TestFormatAspectIdentifierForSheet:
    """Tests for format_aspect_identifier_for_sheet (export round trip)."""

    def _format(self, project, location, aspect_type_id, field_name,
                glossary_project="my-project", glossary_location="us-central1"):
        return business_glossary_utils.format_aspect_identifier_for_sheet(
            project, location, aspect_type_id, field_name,
            default_project=glossary_project, default_location=glossary_location,
        )

    def _round_trip(self, cell, glossary_project="my-project", glossary_location="us-central1"):
        parsed = business_glossary_utils.parse_aspect_identifier(
            cell, glossary_project, glossary_location
        )
        return parsed.project_id, parsed.location, parsed.aspect_type_id, parsed.field_name

    def test_same_project_and_location_uses_short_form(self):
        assert self._format("my-project", "us-central1", "custom-gov", "tier") == "custom-gov.tier"

    def test_global_aspect_type_on_regional_glossary_is_qualified(self):
        cell = self._format("my-project", "global", "custom-gov", "tier")
        assert cell == "my-project.global.custom-gov.tier"
        assert self._round_trip(cell) == ("my-project", "global", "custom-gov", "tier")

    def test_cross_project_aspect_type_is_qualified(self):
        cell = self._format("shared-gov", "global", "custom-gov", "tier")
        assert cell == "shared-gov.global.custom-gov.tier"
        assert self._round_trip(cell) == ("shared-gov", "global", "custom-gov", "tier")

    def test_domain_scoped_project_uses_resource_form(self):
        cell = self._format("google.com:gov", "global", "custom-gov", "owner.email")
        assert cell == "projects/google.com:gov/locations/global/aspectTypes/custom-gov/owner.email"
        assert self._round_trip(cell) == ("google.com:gov", "global", "custom-gov", "owner.email")

    def test_unrecognized_location_uses_resource_form(self):
        cell = self._format("shared-gov", "nam3", "custom-gov", "tier")
        assert cell == "projects/shared-gov/locations/nam3/aspectTypes/custom-gov/tier"
        assert self._round_trip(cell) == ("shared-gov", "nam3", "custom-gov", "tier")

    def test_field_that_looks_like_a_location_avoids_short_form(self):
        """'custom-gov.us.tax.rate' would be read as project 'custom-gov' in 'us'."""
        cell = self._format("my-project", "us-central1", "custom-gov", "us.tax.rate")
        assert cell == "my-project.us-central1.custom-gov.us.tax.rate"
        assert self._round_trip(cell) == ("my-project", "us-central1", "custom-gov", "us.tax.rate")

    def test_same_type_id_in_two_projects_stays_distinct(self):
        first = self._format("my-project", "us-central1", "custom-gov", "tier")
        second = self._format("other-project", "us-central1", "custom-gov", "tier")
        assert self._round_trip(first) != self._round_trip(second)

    def test_missing_component_raises(self):
        with pytest.raises(InvalidAspectIdentifierError):
            self._format("", "global", "custom-gov", "tier")


class TestSplitAspectKey:
    """Tests for split_aspect_key."""

    def test_numeric_project_key(self):
        assert business_glossary_utils.split_aspect_key("655216118709.global.overview") == (
            "655216118709", "global", "overview"
        )

    def test_domain_scoped_project_key(self):
        assert business_glossary_utils.split_aspect_key("google.com:gov.us-central1.custom-gov") == (
            "google.com:gov", "us-central1", "custom-gov"
        )

    def test_resource_name(self):
        assert business_glossary_utils.split_aspect_key(
            "projects/p/locations/eu/aspectTypes/custom-gov"
        ) == ("p", "eu", "custom-gov")

    @pytest.mark.parametrize("key", ["", "custom-gov", "a.b", "a..b"])
    def test_unsplittable_returns_none(self, key):
        assert business_glossary_utils.split_aspect_key(key) is None


# ============================================================================
# coerce_aspect_value
# ============================================================================

class TestResolveFieldType:
    """Tests for resolve_field_type and item-typed coercion."""

    FIELD_TYPES = {
        'tier': 'string',
        'owner': 'record',
        'owner.email': 'string',
        'labels': 'map',
        'labels.*': 'string',
        'scores': 'map',
        'scores.*': 'int',
        'contacts': 'map',
        'contacts.*': 'record',
        'contacts.*.email': 'string',
        'tags': 'array',
        'tags[]': 'string',
        'counts': 'array',
        'counts[]': 'int',
    }

    @pytest.mark.parametrize("path,expected", [
        (['tier'], ('string', None)),
        (['owner', 'email'], ('string', None)),
        (['labels'], ('map', None)),
        (['labels', 'code'], ('string', None)),
        (['scores', 'n'], ('int', None)),
        (['contacts', 'alice'], ('record', None)),
        (['contacts', 'alice', 'email'], ('string', None)),
        (['tags'], ('array', 'string')),
        (['counts'], ('array', 'int')),
        (['unknown'], (None, None)),
        (['owner', 'unknown'], (None, None)),
        ([], (None, None)),
    ])
    def test_resolution(self, path, expected):
        assert business_glossary_utils.resolve_field_type(self.FIELD_TYPES, path) == expected

    def test_record_field_wins_over_a_wildcard(self):
        field_types = {'m': 'map', 'm.*': 'int', 'm.special': 'string'}
        assert business_glossary_utils.resolve_field_type(field_types, ['m', 'special']) == ('string', None)

    @pytest.mark.parametrize("raw,item_type,expected", [
        ("007, yes", "string", ["007", "yes"]),
        ("1, 2", "int", [1, 2]),
        ("1, 0", "bool", [True, False]),
        ("007, yes", None, [7, True]),
    ])
    def test_comma_list_items_use_the_item_type(self, raw, item_type, expected):
        assert business_glossary_utils.coerce_aspect_value(raw, 'array', item_type) == expected

    def test_json_array_is_kept_as_written(self):
        assert business_glossary_utils.coerce_aspect_value('["007", 1]', 'array', 'string') == ["007", 1]


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

    @pytest.mark.parametrize("key", [
        "655216118709.global.glossary-aspect",
        "dataplex-types.global.glossary-aspect",
        "glossary-aspect",
    ])
    def test_glossary_aspect_is_system(self, key):
        """Dataplex rejects glossary-aspect on terms and categories."""
        assert business_glossary_utils.is_system_aspect(key) is True

    @pytest.mark.parametrize("key", [
        "my-project.us-central1.overview",
        "my-project.global.contacts",
        "projects/my-project/locations/us-central1/aspectTypes/overview",
        "google.com:gov.global.glossary-term-aspect",
    ])
    def test_reserved_ids_match_in_any_project_and_location(self, key):
        """Mirrors the backend, which matches these ids whatever the project."""
        assert business_glossary_utils.is_system_aspect(key) is True

    @pytest.mark.parametrize("key", [
        "my-project.global.data-overview",
        "my-project.global.overview-v2",
        "projects/my-project/locations/global/aspectTypes/my-contacts",
    ])
    def test_ids_that_only_contain_a_reserved_word_are_custom(self, key):
        assert business_glossary_utils.is_system_aspect(key) is False

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

    def test_map_with_dotted_keys_stays_json(self):
        """Flattening {'a.b': 1} would rebuild {'a': {'b': 1}} on import."""
        assert business_glossary_utils.flatten_aspect_data(
            {"labels": {"team.name": "core"}}
        ) == [("labels", '{"team.name":"core"}')]

    def test_map_with_empty_key_stays_json(self):
        assert business_glossary_utils.flatten_aspect_data(
            {"labels": {"": "x"}}
        ) == [("labels", '{"":"x"}')]

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

    def test_existing_subfields_are_not_overwritten(self):
        """'owner.email' then 'owner' must not silently drop the email."""
        data = {}
        business_glossary_utils.set_nested_aspect_field(data, ["owner", "email"], "a@b.c")
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.set_nested_aspect_field(data, ["owner"], "alice")
        assert data == {"owner": {"email": "a@b.c"}}

    def test_existing_leaf_is_not_overwritten(self):
        data = {}
        business_glossary_utils.set_nested_aspect_field(data, ["tier"], "HIGH")
        with pytest.raises(InvalidAspectIdentifierError):
            business_glossary_utils.set_nested_aspect_field(data, ["tier"], "LOW")
        assert data == {"tier": "HIGH"}


class TestFindConflictingFieldPath:
    """Tests for find_conflicting_field_path."""

    @pytest.mark.parametrize("assigned,new_path", [
        ([("owner",)], ("owner", "email")),
        ([("owner", "email")], ("owner",)),
        ([("tier",)], ("tier",)),
    ])
    def test_overlaps_are_reported(self, assigned, new_path):
        assert business_glossary_utils.find_conflicting_field_path(assigned, new_path) == assigned[0]

    @pytest.mark.parametrize("assigned,new_path", [
        ([("owner", "email")], ("owner", "name")),
        ([("tier",)], ("tier_v2",)),
        ([], ("tier",)),
    ])
    def test_disjoint_paths_are_allowed(self, assigned, new_path):
        assert business_glossary_utils.find_conflicting_field_path(assigned, new_path) is None


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

    def test_from_dict_headers_match_in_any_case(self):
        row = AspectRow.from_dict({"Id": "term-1", "ASPECT NAME": "custom-gov.tier", " aspect Value ": "HIGH"})
        assert (row.term_id, row.aspect_name, row.aspect_value) == ("term-1", "custom-gov.tier", "HIGH")

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

    def test_completely_blank_sheet_returns_empty(self):
        assert sheet_utils.rows_to_aspect_rows([["", ""], [], ["  "]]) == []

    @pytest.mark.parametrize("header", [
        ["foo", "bar", "baz"],
        ["id", "Aspect", "Value"],
        ["Category ID", "Aspect name", "Aspect value"],
    ])
    def test_unrecognized_headers_raise(self, header):
        """A mistyped header must not be read as 'no aspects' (that would wipe them)."""
        with pytest.raises(AspectSheetHeaderError):
            sheet_utils.rows_to_aspect_rows([header, ["term-1", "custom-gov.tier", "HIGH"]])

    def test_unrecognized_header_without_data_rows_raises(self):
        with pytest.raises(AspectSheetHeaderError):
            sheet_utils.rows_to_aspect_rows([["notes about this tab"]])

    def test_blank_header_with_data_raises(self):
        with pytest.raises(AspectSheetHeaderError):
            sheet_utils.rows_to_aspect_rows([["", "", ""], ["term-1", "custom-gov.tier", "HIGH"]])


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

    def test_api_error_raises_instead_of_reporting_absent(self, mock_sheets_client):
        """'Absent' would skip resetting a stale aspects tab and bring back removed aspects."""
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.side_effect = (
            Exception("boom")
        )
        with pytest.raises(SheetsAPIError):
            sheet_utils.sheet_exists(mock_sheets_client, 'sid', 'Sheet2')

    def test_network_errors_are_retried(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.side_effect = [
            ConnectionError("reset"), {'sheets': [{'properties': {'title': 'Sheet2'}}]},
        ]
        with patch('utils.retry_utils.time.sleep'):
            assert sheet_utils.sheet_exists(mock_sheets_client, 'sid', 'Sheet2') is True


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

    def test_values_are_written_raw(self, mock_sheets_client):
        """USER_ENTERED would let Sheets turn '007' into 7, '=x' into a formula, etc."""
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet2', 'sheetId': 7}}]
        }
        sheet_utils.write_aspect_rows(
            mock_sheets_client, 'sid', [ASPECT_SHEET_HEADERS, ['t', 'a.code', '007']]
        )
        update_call = mock_sheets_client.spreadsheets.return_value.values.return_value.update
        assert update_call.call_args.kwargs['valueInputOption'] == 'RAW'


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

    def test_field_types_include_map_and_array_item_types(self):
        service = self._mock_service({
            'name': 'at',
            'metadataTemplate': {
                'recordFields': [
                    {'name': 'labels', 'type': 'map', 'mapItems': {'name': 'v', 'type': 'string'}},
                    {'name': 'tags', 'type': 'array', 'arrayItems': {'name': 't', 'type': 'string'}},
                    {
                        'name': 'contacts', 'type': 'map',
                        'mapItems': {
                            'name': 'c', 'type': 'record',
                            'recordFields': [{'name': 'email', 'type': 'string'}],
                        },
                    },
                ]
            },
        })
        assert api_layer.get_aspect_field_types(
            service, 'projects/p/locations/global/aspectTypes/custom-gov'
        ) == {
            'labels': 'map', 'labels.*': 'string',
            'tags': 'array', 'tags[]': 'string',
            'contacts': 'map', 'contacts.*': 'record', 'contacts.*.email': 'string',
        }

    def test_transient_failure_is_not_cached(self):
        """A 503 that outlasted the retries must not hide the schema for the rest of the run."""
        from googleapiclient.errors import HttpError
        response = MagicMock()
        response.status = 503
        resource = 'projects/p/locations/global/aspectTypes/custom-gov'
        with patch.object(
            api_layer, 'execute_with_retry',
            side_effect=[HttpError(response, b'unavailable'), {'name': 'at'}],
        ) as execute:
            assert api_layer.get_aspect_type(MagicMock(), resource) is None
            assert api_layer.get_aspect_type(MagicMock(), resource) == {'name': 'at'}
        assert execute.call_count == 2

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

    def test_glossary_aspect_excluded(self):
        result = glossary_export._get_aspects_from_entry(_entry_with_aspects({
            '655216118709.global.glossary-aspect': {'data': {}},
        }))
        assert result['custom'] == {}

    def test_user_aspect_with_reserved_id_is_skipped_with_warning(self, caplog):
        with caplog.at_level('WARNING'):
            result = glossary_export._get_aspects_from_entry(_entry_with_aspects({
                '123456.us-central1.overview': {'data': {'summary': 'x'}},
            }))
        assert result['custom'] == {}
        assert "reserved aspect type id 'overview'" in caplog.text

    def test_builtin_system_aspects_do_not_warn(self, caplog):
        with caplog.at_level('WARNING'):
            glossary_export._get_aspects_from_entry(_entry_with_aspects(MIXED_ASPECTS))
        assert 'reserved aspect type id' not in caplog.text


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

    def test_record_fields_are_dotted(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({
                'my-project.us-central1.custom-gov': {'data': {'owner': {'email': 'a@b.c'}}}
            }),
        )
        assert rows == [['term-customer-id', 'custom-gov.owner.email', 'a@b.c']]

    def test_arrays_are_json(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({
                'my-project.us-central1.custom-gov': {'data': {'tags': ['finance', 'core']}}
            }),
        )
        assert rows == [['term-customer-id', 'custom-gov.tags', '["finance","core"]']]

    def test_global_aspect_type_on_regional_glossary_is_qualified(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({'my-project.global.custom-gov': {'data': {'tier': 'LOW'}}}),
        )
        assert rows == [['term-customer-id', 'my-project.global.custom-gov.tier', 'LOW']]

    def test_cross_project_aspect_type_is_qualified(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({'shared-gov.us-central1.custom-gov': {'data': {'tier': 'LOW'}}}),
        )
        assert rows == [['term-customer-id', 'shared-gov.us-central1.custom-gov.tier', 'LOW']]

    def test_project_numbers_are_resolved_before_comparing(self):
        """Entry aspect keys carry project numbers; the glossary name carries the id."""
        numbers = {'111': 'my-project', '222': 'shared-gov'}
        with patch.object(
            glossary_export.api_layer, 'get_project_id_from_number',
            side_effect=lambda project, user_project='': numbers.get(project, project),
        ):
            rows = glossary_export._get_aspect_rows_for_entry(
                TERM,
                _entry_with_aspects({
                    '111.us-central1.custom-gov': {'data': {'tier': 'HIGH'}},
                    '222.us-central1.custom-gov': {'data': {'tier': 'LOW'}},
                }),
            )
        assert rows == [
            ['term-customer-id', 'custom-gov.tier', 'HIGH'],
            ['term-customer-id', 'shared-gov.us-central1.custom-gov.tier', 'LOW'],
        ]

    def test_null_and_empty_fields_are_not_exported(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({'my-project.us-central1.custom-gov': {'data': {
                'tier': 'HIGH', 'note': '', 'owner': None, 'tags': [], 'meta': {}
            }}}),
        )
        assert rows == [
            ['term-customer-id', 'custom-gov.tier', 'HIGH'],
            ['term-customer-id', 'custom-gov.tags', '[]'],
            ['term-customer-id', 'custom-gov.meta', '{}'],
        ]

    def test_malformed_field_is_skipped_not_fatal(self, caplog):
        with caplog.at_level('WARNING'):
            rows = glossary_export._get_aspect_rows_for_entry(
                TERM,
                _entry_with_aspects({
                    'my-project.us-central1.custom-gov': {'data': {'': 'x', 'tier': 'HIGH'}}
                }),
            )
        assert rows == [['term-customer-id', 'custom-gov.tier', 'HIGH']]
        assert 'Skipping field' in caplog.text

    def test_explicit_glossary_project_and_location_win(self):
        rows = glossary_export._get_aspect_rows_for_entry(
            TERM,
            _entry_with_aspects({'my-project.global.custom-gov': {'data': {'tier': 'LOW'}}}),
            glossary_project_id='my-project',
            glossary_location_id='global',
        )
        assert rows == [['term-customer-id', 'custom-gov.tier', 'LOW']]

    def test_export_rows_import_back_to_the_same_aspects(self):
        """Export -> import must resolve every row to the aspect type it came from."""
        aspects = {
            'my-project.us-central1.custom-gov': {'data': {'tier': 'HIGH', 'owner': {'email': 'a@b.c'}}},
            'my-project.global.custom-gov': {'data': {'tier': 'LOW'}},
            'shared-gov.global.custom-gov': {'data': {'tier': 'MID'}},
        }
        rows = glossary_export._get_aspect_rows_for_entry(TERM, _entry_with_aspects(aspects))

        processor = _make_processor()
        is_valid, imported = processor.read_aspects(
            _mock_spreadsheet([ASPECT_SHEET_HEADERS] + rows), {'term-customer-id'}
        )

        assert is_valid is True
        assert imported == {'term-customer-id': {
            'projects/my-project/locations/us-central1/aspectTypes/custom-gov': {
                'tier': 'HIGH', 'owner': {'email': 'a@b.c'}
            },
            'projects/my-project/locations/global/aspectTypes/custom-gov': {'tier': 'LOW'},
            'projects/shared-gov/locations/global/aspectTypes/custom-gov': {'tier': 'MID'},
        }}

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


class TestExportLookupFailures:
    """A partial export must never be written: re-importing it would delete entries."""

    GLOSSARY = 'projects/my-project/locations/us-east1/glossaries/g'
    SHEET_URL = 'https://docs.google.com/spreadsheets/d/abc123/edit'

    def _run(self, lookup):
        terms = [
            {'name': f'{self.GLOSSARY}/terms/t1', 'parent': self.GLOSSARY},
            {'name': f'{self.GLOSSARY}/terms/t2', 'parent': self.GLOSSARY},
        ]
        categories = [{'name': f'{self.GLOSSARY}/categories/c1', 'parent': self.GLOSSARY}]
        with patch.object(glossary_export, '_authenticate_dataplex', return_value=MagicMock()), \
             patch.object(glossary_export, '_authenticate_sheets', return_value=MagicMock()), \
             patch.object(glossary_export, '_list_glossary_categories', return_value=categories), \
             patch.object(glossary_export, '_list_glossary_terms', return_value=terms), \
             patch.object(glossary_export, '_resolve_project_number', side_effect=lambda p: p), \
             patch.object(glossary_export, '_lookup_entry', side_effect=lookup), \
             patch.object(glossary_export, '_write_to_sheet') as write_sheet1, \
             patch.object(glossary_export, '_write_aspects_to_sheet') as write_sheet2:
            error = None
            try:
                glossary_export.list_and_write_glossary_taxonomy(self.SHEET_URL, self.GLOSSARY)
            except glossary_export.DataplexAPIError as e:
                error = e
        return error, write_sheet1, write_sheet2

    def test_one_failed_lookup_aborts_without_writing(self):
        def lookup(service, entry_name):
            if entry_name.endswith('/terms/t2'):
                raise glossary_export.DataplexAPIError('403 denied')
            return {'aspects': {}}
        error, write_sheet1, write_sheet2 = self._run(lookup)
        assert error is not None
        assert 'Could not read 1 of 3' in str(error)
        write_sheet1.assert_not_called()
        write_sheet2.assert_not_called()

    def test_empty_lookup_response_aborts_without_writing(self):
        error, write_sheet1, _ = self._run(lambda service, name: None)
        assert 'Could not read 3 of 3' in str(error)
        write_sheet1.assert_not_called()

    def test_all_lookups_succeed_writes_sheets(self):
        error, write_sheet1, write_sheet2 = self._run(lambda service, name: {'aspects': {}})
        assert error is None
        write_sheet1.assert_called_once()
        write_sheet2.assert_called_once()

    def test_lookup_entry_retries_transient_errors(self):
        from googleapiclient.errors import HttpError
        response = MagicMock()
        response.status = 503
        service = MagicMock()
        execute = service.projects.return_value.locations.return_value.lookupEntry.return_value.execute
        execute.side_effect = [HttpError(response, b'unavailable'), {'name': 'e'}]
        with patch('utils.retry_utils.time.sleep'):
            result = glossary_export._lookup_entry(
                service,
                'projects/p/locations/us-east1/entryGroups/@dataplex/entries/projects/1/locations/us-east1/glossaries/g/terms/t1',
            )
        assert result == {'name': 'e'}
        assert execute.call_count == 2


class TestExportCategoryAspects:
    """Custom aspects can only be set on terms, so category aspects are not exported."""

    def test_category_aspects_are_skipped_with_a_warning(self, caplog):
        aspects = {'my-project.us-east1.custom-gov': {'data': {'tier': 'HIGH'}}}
        with caplog.at_level('WARNING'):
            _, write_sheet1, write_sheet2 = TestExportLookupFailures()._run(
                lambda service, name: {'aspects': aspects}
            )
        write_sheet1.assert_called_once()
        aspect_rows = write_sheet2.call_args.args[2]
        assert aspect_rows == [
            ['t1', 'custom-gov.tier', 'HIGH'],
            ['t2', 'custom-gov.tier', 'HIGH'],
        ]
        assert "Category 'c1' has custom aspect(s) my-project.us-east1.custom-gov" in caplog.text
        assert "Re-importing this spreadsheet removes them" in caplog.text

    def test_category_without_custom_aspects_does_not_warn(self, caplog):
        with caplog.at_level('WARNING'):
            glossary_export._warn_about_category_custom_aspects(
                CATEGORY, _entry_with_aspects({'655216118709.global.overview': {'data': {'content': 'x'}}})
            )
        assert caplog.text == ''

class TestExportEntryNames:
    """Entry names must use the project number in their inner path (how Dataplex stores them)."""

    TERM_NAME = 'projects/my-project/locations/us-east1/glossaries/g/terms/t1'
    CATEGORY_NAME = 'projects/my-project/locations/us-east1/glossaries/g/categories/c1'

    def setup_method(self):
        glossary_export._unresolvable_projects.clear()

    def test_project_id_is_resolved_to_number_for_terms(self):
        with patch.object(glossary_export.api_layer, 'get_project_number', return_value='123'):
            name = glossary_export._generate_entry_name_from_term_name(self.TERM_NAME)
        assert name == (
            'projects/my-project/locations/us-east1/entryGroups/@dataplex/entries/'
            'projects/123/locations/us-east1/glossaries/g/terms/t1'
        )

    def test_project_id_is_resolved_to_number_for_categories(self):
        with patch.object(glossary_export.api_layer, 'get_project_number', return_value='123'):
            name = glossary_export._generate_entry_name_from_category_name(self.CATEGORY_NAME)
        assert name == (
            'projects/my-project/locations/us-east1/entryGroups/@dataplex/entries/'
            'projects/123/locations/us-east1/glossaries/g/categories/c1'
        )

    def test_project_number_url_is_unchanged_without_an_api_call(self):
        with patch.object(glossary_export.api_layer, '_fetch_project_info') as fetch:
            name = glossary_export._generate_entry_name_from_term_name(
                'projects/123/locations/us-east1/glossaries/g/terms/t1'
            )
        fetch.assert_not_called()
        assert '/entries/projects/123/locations/us-east1/glossaries/g/terms/t1' in name

    def test_lookup_failure_falls_back_to_project_id_and_warns_once(self, caplog):
        with patch.object(
            glossary_export.api_layer, 'get_project_number', side_effect=Exception('403')
        ) as get_number, caplog.at_level('WARNING'):
            first = glossary_export._generate_entry_name_from_term_name(self.TERM_NAME)
            glossary_export._generate_entry_name_from_category_name(self.CATEGORY_NAME)
        assert '/entries/projects/my-project/locations/' in first
        assert get_number.call_count == 1
        assert caplog.text.count('Could not resolve the project number') == 1


class TestExportWriteAspectsToSheet:
    """Tests for glossary-export._write_aspects_to_sheet."""

    def test_no_rows_writes_nothing(self, mock_sheets_client):
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet1', 'sheetId': 0}}]
        }
        glossary_export._write_aspects_to_sheet(mock_sheets_client, 'sid', [])
        mock_sheets_client.spreadsheets.return_value.values.return_value.update.assert_not_called()
        mock_sheets_client.spreadsheets.return_value.values.return_value.clear.assert_not_called()
        mock_sheets_client.spreadsheets.return_value.batchUpdate.assert_not_called()

    def test_no_rows_resets_existing_aspects_sheet(self, mock_sheets_client):
        """Leftover rows from an earlier export would bring back removed aspects."""
        mock_sheets_client.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet2', 'sheetId': 3}}]
        }
        glossary_export._write_aspects_to_sheet(mock_sheets_client, 'sid', [])
        values_api = mock_sheets_client.spreadsheets.return_value.values.return_value
        values_api.clear.assert_called_once()
        assert values_api.update.call_args.kwargs['body']['values'] == [ASPECT_SHEET_HEADERS]

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

    def test_sheet1_is_cleared_then_written(self, mock_sheets_client):
        """Leftover rows from a bigger earlier export would bring back deleted terms."""
        calls = []
        values_api = mock_sheets_client.spreadsheets.return_value.values.return_value
        values_api.clear.side_effect = lambda **kw: calls.append(('clear', kw['range'])) or MagicMock()
        values_api.update.side_effect = lambda **kw: calls.append(('update', kw['range'])) or MagicMock()
        glossary_export._write_to_sheet(mock_sheets_client, 'sid', [['a']])
        assert calls == [('clear', 'A:ZZ'), ('update', 'A1')]

    def test_sheet1_clear_failure_raises(self, mock_sheets_client):
        values_api = mock_sheets_client.spreadsheets.return_value.values.return_value
        values_api.clear.return_value.execute.side_effect = Exception('boom')
        with pytest.raises(glossary_export.SheetsAPIError):
            glossary_export._write_to_sheet(mock_sheets_client, 'sid', [['a']])
        values_api.update.assert_not_called()


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

    def test_missing_sheet_is_valid_with_warning(self, capsys):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(
            _mock_spreadsheet(None, raise_not_found=True), {'term-1'}
        )
        assert is_valid is True
        assert aspects == {}
        assert "will be REMOVED" in capsys.readouterr().out

    def test_empty_sheet_is_valid_with_warning(self, capsys):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([]), {'term-1'})
        assert is_valid is True
        assert aspects == {}
        assert "will be REMOVED" in capsys.readouterr().out

    def test_header_only_sheet_is_valid_with_warning(self, capsys):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(
            _mock_spreadsheet([ASPECT_SHEET_HEADERS]), {'term-1'}
        )
        assert is_valid is True
        assert aspects == {}
        assert "will be REMOVED" in capsys.readouterr().out

    def test_bad_header_fails_the_import(self, capsys):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
            ['id', 'Aspect', 'Value'],
            ['term-1', 'custom-gov.tier', 'HIGH'],
        ]), {'term-1'})
        assert is_valid is False
        assert aspects == {}
        assert "header" in capsys.readouterr().out

    def test_worksheet_api_error_fails_the_import(self, capsys):
        processor = _make_processor()
        spreadsheet = MagicMock()
        spreadsheet.worksheet.side_effect = Exception("HTTP 429")
        is_valid, aspects = processor.read_aspects(spreadsheet, {'term-1'})
        assert is_valid is False
        assert aspects == {}
        assert "HTTP 429" in capsys.readouterr().out

    def test_read_values_error_fails_the_import(self):
        processor = _make_processor()
        spreadsheet = MagicMock()
        spreadsheet.worksheet.return_value.get_all_values.side_effect = Exception("HTTP 503")
        is_valid, aspects = processor.read_aspects(spreadsheet, {'term-1'})
        assert is_valid is False
        assert aspects == {}

    def test_spreadsheet_open_error_fails_the_import(self):
        processor = _make_processor()
        with patch.object(
            glossary_import.gspread, 'authorize', side_effect=Exception("boom")
        ):
            is_valid, aspects = processor.read_aspects(None, {'term-1'})
        assert is_valid is False
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

    @pytest.mark.parametrize("value", ['', '   '])
    def test_missing_aspect_value_is_rejected(self, value, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tier', value],
        ]), {'term-1'})
        output = capsys.readouterr().out
        assert is_valid is False
        assert "row 2" in output
        assert "Aspect value" in output

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

    @pytest.mark.parametrize("second_name", [
        'my-project.us-central1.custom-gov.tier',
        'projects/my-project/locations/us-central1/aspectTypes/custom-gov/tier',
    ])
    def test_equivalent_spellings_are_duplicates(self, second_name, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tier', 'HIGH'],
            ['term-1', second_name, 'LOW'],
        ]), {'term-1'})
        assert is_valid is False
        assert "row 3" in capsys.readouterr().out

    def test_field_names_differing_only_in_case_are_distinct(self):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tier', 'HIGH'],
            ['term-1', 'custom-gov.Tier', 'LOW'],
        ]), {'term-1'})
        resource = 'projects/my-project/locations/us-central1/aspectTypes/custom-gov'
        assert is_valid is True
        assert aspects['term-1'][resource] == {'tier': 'HIGH', 'Tier': 'LOW'}

    @pytest.mark.parametrize("first,second", [
        (('custom-gov.owner', 'alice'), ('custom-gov.owner.email', 'a@b.c')),
        (('custom-gov.owner.email', 'a@b.c'), ('custom-gov.owner', 'alice')),
        (('custom-gov.owner.email', 'a@b.c'), ('custom-gov.owner', '{"name":"A"}')),
        (('custom-gov.owner', '{"name":"A"}'), ('custom-gov.owner.email', 'a@b.c')),
    ])
    def test_overlapping_fields_are_rejected_in_either_order(self, first, second, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', first[0], first[1]],
            ['term-1', second[0], second[1]],
        ]), {'term-1'})
        output = capsys.readouterr().out
        assert is_valid is False
        assert "row 3" in output
        assert "row 2" in output

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

    @pytest.mark.parametrize("aspect_name", [
        'my-project.us-central1.overview.summary',
        'overview.summary',
        'projects/my-project/locations/global/aspectTypes/contacts/identities',
        'dataplex-types.global.glossary-aspect.x',
        'my-project.us-central1.glossary-category-aspect.x',
    ])
    def test_reserved_aspect_type_ids_are_rejected_in_any_project(self, aspect_name, capsys):
        """The backend treats these ids as built-in whatever the project."""
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', aspect_name, 'x'],
        ]), {'term-1'})
        assert is_valid is False
        assert "reserved" in capsys.readouterr().out


class TestImportAspectsAreForTermsOnly:
    """Custom aspects can only be set on terms; a term and a category may share an id."""

    GOV = 'projects/my-project/locations/us-central1/aspectTypes/custom-gov'

    def test_category_id_is_rejected(self, capsys):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['cat-core', 'custom-gov.tier', 'HIGH'],
        ]), {'term-1'}, {'cat-core'})
        output = capsys.readouterr().out
        assert is_valid is False
        assert aspects == {}
        assert "'cat-core' in aspects sheet row 2 is a category" in output
        assert "only be set on terms" in output

    def test_id_shared_by_a_term_and_a_category_goes_to_the_term_with_a_warning(self, capsys):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['sales', 'custom-gov.tier', 'HIGH'],
            ['term-1', 'custom-gov.tier', 'LOW'],
        ]), {'sales', 'term-1'}, {'sales'})
        output = capsys.readouterr().out
        assert is_valid is True
        assert aspects == {'sales': {self.GOV: {'tier': 'HIGH'}}, 'term-1': {self.GOV: {'tier': 'LOW'}}}
        assert "'sales' in aspects sheet row 2 is both a term and a category" in output
        assert "applied to the term" in output
        assert "row 3" not in output

    def test_rejected_shared_id_row_is_not_reported_as_applied(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['sales', 'custom-gov.owner', 'alice'],
            ['sales', 'custom-gov.owner.email', 'a@b.c'],
        ]), {'sales'}, {'sales'})
        output = capsys.readouterr().out
        assert is_valid is False
        assert "row 2 is both a term and a category" in output
        assert "row 3 is both a term and a category" not in output
        assert "overlaps field 'owner' set in row 2" in output


class TestImportAspectNameCasingAndItemTypes:
    """Aspect type names are case-insensitive; map values and array items use their declared type."""

    GOV = 'projects/my-project/locations/us-central1/aspectTypes/custom-gov'

    def test_mixed_case_aspect_type_names_aggregate_into_one_aspect(self):
        processor = _make_processor()
        is_valid, aspects = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'Custom-Gov.tier', 'HIGH'],
            ['term-1', 'custom-gov.owner.email', 'a@b.c'],
            ['term-1', 'MY-PROJECT.US-CENTRAL1.CUSTOM-GOV.Level', 'LOW'],
        ]), {'term-1'})
        assert is_valid is True
        assert aspects == {'term-1': {self.GOV: {
            'tier': 'HIGH', 'owner': {'email': 'a@b.c'}, 'Level': 'LOW'
        }}}

    def test_same_field_in_different_casing_of_the_type_is_a_duplicate(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['term-1', 'custom-gov.tier', 'HIGH'],
            ['term-1', 'CUSTOM-GOV.tier', 'LOW'],
        ]), {'term-1'})
        assert is_valid is False
        assert "Duplicate" in capsys.readouterr().out

    def test_reserved_id_in_another_case_is_rejected(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS, ['term-1', 'Overview.summary', 'x'],
        ]), {'term-1'})
        assert is_valid is False
        assert "reserved" in capsys.readouterr().out

    def _read_with_schema(self, rows, field_types):
        processor = _make_processor()
        processor._dataplex_service = MagicMock()
        with patch.object(glossary_import.api_layer, 'get_aspect_field_types', return_value=field_types):
            return processor.read_aspects(_mock_spreadsheet([ASPECT_SHEET_HEADERS] + rows), {'term-1'})

    def test_map_entry_rows_use_the_map_value_type(self):
        is_valid, aspects = self._read_with_schema([
            ['term-1', 'custom-gov.labels.code', '007'],
            ['term-1', 'custom-gov.labels.flag', 'yes'],
            ['term-1', 'custom-gov.labels.expr', '[1, 2]'],
            ['term-1', 'custom-gov.scores.n', '42'],
        ], {'labels': 'map', 'labels.*': 'string', 'scores': 'map', 'scores.*': 'int'})
        assert is_valid is True
        assert aspects['term-1'][self.GOV] == {
            'labels': {'code': '007', 'flag': 'yes', 'expr': '[1, 2]'},
            'scores': {'n': 42},
        }

    def test_comma_separated_array_items_use_the_item_type(self):
        _, aspects = self._read_with_schema(
            [['term-1', 'custom-gov.tags', '007, yes']], {'tags': 'array', 'tags[]': 'string'}
        )
        assert aspects['term-1'][self.GOV] == {'tags': ['007', 'yes']}

    def test_export_import_round_trip_of_a_string_map(self):
        """A map<string> with number- and bool-looking values comes back unchanged."""
        data = {'labels': {'code': '007', 'n': '123', 'flag': 'true'}, 'tags': ['007', 'yes']}
        term = {'name': 'projects/my-project/locations/us-central1/glossaries/g/terms/term-1'}
        rows = glossary_export._get_aspect_rows_for_entry(
            term, _entry_with_aspects({'my-project.us-central1.custom-gov': {'data': data}})
        )
        is_valid, aspects = self._read_with_schema(rows, {
            'labels': 'map', 'labels.*': 'string', 'tags': 'array', 'tags[]': 'string',
        })
        assert is_valid is True
        assert aspects == {'term-1': {self.GOV: data}}

    def test_unknown_id_message_asks_for_a_term_id(self, capsys):
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['ghost', 'custom-gov.tier', 'HIGH'],
        ]), {'term-1'}, {'cat-core'})
        output = capsys.readouterr().out
        assert is_valid is False
        assert "Unknown 'id' 'ghost' in aspects sheet row 2" in output
        assert "id of a term" in output

    def test_category_ids_are_optional(self, capsys):
        """Without category ids, a non-term id is reported as unknown."""
        processor = _make_processor()
        is_valid, _ = processor.read_aspects(_mock_spreadsheet([
            ASPECT_SHEET_HEADERS,
            ['cat-core', 'custom-gov.tier', 'HIGH'],
        ]), {'term-1'})
        assert is_valid is False
        assert "Unknown 'id' 'cat-core'" in capsys.readouterr().out

    def test_read_and_validate_data_passes_term_and_category_ids(self):
        headers = [
            'id', 'parent', 'display_name', 'description', 'overview', 'type',
            'contact1_email', 'contact1_name', 'contact2_email', 'contact2_name',
            'label1_key', 'label1_value', 'label2_key', 'label2_value',
        ]

        def row(row_id, row_type):
            return [row_id, '', row_id.title(), '', '', row_type] + [''] * 8

        spreadsheet = MagicMock()
        spreadsheet.sheet1.get_all_values.return_value = [
            headers, row('sales', 'CATEGORY'), row('sales', 'TERM'), row('term-1', 'TERM'),
            row('cat-core', 'CATEGORY'),
        ]
        processor = _make_processor()
        with patch.object(glossary_import.gspread, 'authorize') as authorize, \
             patch.object(processor, 'read_aspects', return_value=(False, {})) as read_aspects:
            authorize.return_value.open_by_url.return_value = spreadsheet
            processor.read_and_validate_data()
        _, term_ids, category_ids = read_aspects.call_args.args
        assert term_ids == {'sales', 'term-1'}
        assert category_ids == {'sales', 'cat-core'}


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

    def test_category_never_gets_custom_aspects(self):
        """Custom aspects are for terms only, even if aspects_by_id has the id."""
        processor = _make_processor()
        processor.aspects_by_id = {
            'cat-core': {
                'projects/my-project/locations/us-central1/aspectTypes/custom-gov': {'tier': 'LOW'}
            }
        }
        aspects = processor._convert_to_import_item(
            self._row(row_id='cat-core', row_type='CATEGORY'), []
        )['entry']['aspects']

        assert set(aspects) == {
            'dataplex-types.global.glossary-category-aspect',
            'dataplex-types.global.overview',
            'dataplex-types.global.contacts',
        }

    def test_term_and_category_sharing_an_id_only_the_term_gets_aspects(self):
        processor = _make_processor()
        processor.aspects_by_id = {
            'sales': {'projects/my-project/locations/us-central1/aspectTypes/custom-gov': {'tier': 'X'}}
        }
        term = processor._convert_to_import_item(self._row(row_id='sales', row_type='TERM'), [])
        category = processor._convert_to_import_item(self._row(row_id='sales', row_type='CATEGORY'), [])
        assert term['entry']['aspects']['my-project.us-central1.custom-gov']['data'] == {'tier': 'X'}
        assert 'my-project.us-central1.custom-gov' not in category['entry']['aspects']

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

