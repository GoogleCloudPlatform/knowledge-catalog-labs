"""
Business Glossary Utility Functions

Common utility functions for working with Dataplex Glossary resources.
"""

# Standard library imports
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Local imports
from utils.constants import (
    ASPECT_FIELD_TYPE_ARRAY,
    ASPECT_FIELD_TYPE_BOOL,
    ASPECT_FIELD_TYPE_DATETIME,
    ASPECT_FIELD_TYPE_DOUBLE,
    ASPECT_FIELD_TYPE_ENUM,
    ASPECT_FIELD_TYPE_INT,
    ASPECT_FIELD_TYPE_MAP,
    ASPECT_FIELD_TYPE_RECORD,
    ASPECT_FIELD_TYPE_STRING,
    ASPECT_RESOURCE_PATTERN,
    BIGQUERY_ENTRY_ID_PATTERN,
    BOOL_FALSE_LITERALS,
    BOOL_NUMERIC_FALSE_LITERALS,
    BOOL_NUMERIC_TRUE_LITERALS,
    BOOL_TRUE_LITERALS,
    DATAPLEX_SYSTEM_ENTRY_GROUP,
    DECIMAL_LITERAL_PATTERN,
    GLOSSARY_NAME_PATTERN,
    INTEGER_LITERAL_PATTERN,
    LOCATION_ID_PATTERN,
    MAX_ASPECT_FLATTEN_DEPTH,
    SYSTEM_ASPECT_TYPE_IDS,
    TERM_NAME_PATTERN,
)
from utils.error import (
    InvalidAspectIdentifierError,
    InvalidTermIdentifierError,
    InvalidTermNameError,
)
from utils.models import ParsedAspectIdentifier, ParsedTermIdentifier


def extract_glossary_name(url: str) -> str:
    """Extract the glossary resource name from a Dataplex URL or resource name.

    Searches for 'projects/{project}/locations/{location}/glossaries/{glossary}'
    pattern anywhere in the input string.
    """
    match = GLOSSARY_NAME_PATTERN.search(url)
    if match:
        return f"projects/{match.group('project_id')}/locations/{match.group('location_id')}/glossaries/{match.group('glossary_id')}"

    raise ValueError(
        f"Could not extract glossary resource from: {url}. "
        f"Expected format: 'projects/{{project}}/locations/{{location}}/glossaries/{{glossary}}'"
    )


def generate_entry_name_from_term_name(term_name: str, project_number: str = "") -> str:
    """
    Generates a Dataplex entry ID from a glossary term name.

    Args:
        term_name: The full term name in format:
                   projects/{project}/locations/{location}/glossaries/{glossary}/terms/{term}
        project_number: Optional numeric project number. If provided, used for the inner entry ID.
    Returns:
        The generated entry ID in format:
        projects/{project}/locations/{location}/entryGroups/@dataplex/entries/projects/{project_number_or_id}/locations/{location}/glossaries/{glossary}/terms/{term}
    """
    match = TERM_NAME_PATTERN.match(term_name)
    if not match:
        raise InvalidTermNameError(f"Invalid term name format: {term_name}")

    project_id = match.group('project_id')
    location_id = match.group('location_id')
    glossary_id = match.group('glossary_id')
    term_id = match.group('term_id')

    inner_project = project_number if project_number else project_id
    outer_project = project_number if project_number else project_id

    return (
        f"projects/{outer_project}/locations/{location_id}/entryGroups/{DATAPLEX_SYSTEM_ENTRY_GROUP}/entries/"
        f"projects/{inner_project}/locations/{location_id}/glossaries/{glossary_id}/terms/{term_id}"
    )


def extract_project_id_from_name(resource_name: str) -> str:
    """
    Extracts the project ID from a Dataplex resource name (glossary, term, category, entry).
    """
    project_pattern = re.compile(r"projects/(?P<project_id>[^/]+)")
    match = project_pattern.search(resource_name)
    if match:
        return match.group('project_id')
    raise ValueError(
        f"Could not extract project from resource name: {resource_name}. "
        f"Expected format containing 'projects/{{project}}'"
    )


def extract_location_from_name(resource_name: str) -> str:
    """
    Extracts the location from a Dataplex resource name (glossary, term, category, entry).
    """
    # Generic pattern to extract location from any resource name
    location_pattern = re.compile(r"projects/[^/]+/locations/(?P<location_id>[^/]+)")

    match = location_pattern.search(resource_name)
    if match:
        return match.group('location_id')

    raise ValueError(
        f"Could not extract location from resource name: {resource_name}. "
        f"Expected format containing 'projects/{{project}}/locations/{{location}}'"
    )


def normalize_id(name: str) -> str:
    """
    Converts a string to a valid Dataplex ID (lowercase, numbers, hyphens), starting with a letter.

    Args:
        name: The string to normalize

    Returns:
        A normalized ID suitable for Dataplex (lowercase, numbers, hyphens, starts with letter)

    Example:
        >>> normalize_id("My Special ID!")
        'my-special-id'
        >>> normalize_id("123-start-with-number")
        'g123-start-with-number'
    """
    if not name:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    # Ensure starts with a letter
    if not normalized or not normalized[0].isalpha():
        normalized = "g" + normalized
    return normalized


def generate_entry_link_id() -> str:
    """
    Generate a unique entry link ID that starts with a lowercase letter
    and contains only lowercase letters and numbers.
    """
    entrylink_id = 'g' + uuid.uuid4().hex
    return entrylink_id


def format_term_display_identifier(
    project_id: str, location: str, glossary_display_name: str, term_display_name: str
) -> str:
    """Format a human-readable term identifier string ('.'-separated).

    Example:
        >>> format_term_display_identifier("my-proj", "global", "Sales Glossary", "Revenue")
        'my-proj.global.Sales Glossary.Revenue'
    """
    return f"{project_id.strip()}.{location.strip()}.{glossary_display_name.strip()}.{term_display_name.strip()}"


def parse_term_display_identifier(identifier: str, allow_three_part: bool = False) -> ParsedTermIdentifier:
    """Parse a human-readable term identifier string into a ParsedTermIdentifier.

    Expected format: '<project>.<location>.<glossaryDisplayName>.<termDisplayName>'
    (or 3-part '<project>.<location>.<glossaryDisplayName>' when allow_three_part=True).

    Args:
        identifier: The term display identifier string.
        allow_three_part: If True, allows 3-part identifier without termDisplayName.

    Returns:
        ParsedTermIdentifier containing project_id, location, glossary_display_name, and term_display_name.

    Raises:
        InvalidTermIdentifierError: If the identifier has fewer than required segments or empty components.
    """
    if not identifier or not isinstance(identifier, str):
        raise InvalidTermIdentifierError(f"Invalid term identifier: '{identifier}'. Identifier must be a non-empty string.")

    cleaned = identifier.strip()
    parts = cleaned.split(".")
    min_parts = 3 if allow_three_part else 4
    if len(parts) < min_parts:
        raise InvalidTermIdentifierError(
            f"Invalid term identifier '{cleaned}'. Expected format: "
            f"'<project>.<location>.<glossaryDisplayName>.<termDisplayName>'"
        )

    project_id = parts[0].strip()
    location_id = parts[1].strip()
    glossary_display_name = parts[2].strip()
    term_display_name = ".".join(parts[3:]).strip() if len(parts) >= 4 else ""

    if not project_id or not location_id or not glossary_display_name or (not allow_three_part and not term_display_name):
        raise InvalidTermIdentifierError(
            f"Invalid term identifier '{cleaned}'. All components (project, location, glossary, term) must be non-empty."
        )

    return ParsedTermIdentifier(
        project_id=project_id,
        location=location_id,
        glossary_display_name=glossary_display_name,
        term_display_name=term_display_name,
    )


def extract_term_resource_from_entry_name(entry_name: str) -> str:
    """Extract the underlying glossary term resource name from a Dataplex term entry name.

    Example:
        >>> extract_term_resource_from_entry_name(
        ...     'projects/p/locations/l/entryGroups/@dataplex/entries/projects/p/locations/l/glossaries/g/terms/t'
        ... )
        'projects/p/locations/l/glossaries/g/terms/t'
    """
    pattern = re.compile(
        r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/entryGroups/@dataplex/entries/"
        r"(?P<term_resource>projects/[^/]+/locations/[^/]+/glossaries/[^/]+/terms/[^/]+)"
    )
    match = pattern.match(entry_name)
    if match:
        return match.group("term_resource")
    # If it's already a term resource name
    if TERM_NAME_PATTERN.match(entry_name):
        return entry_name
    raise InvalidTermNameError(f"Could not extract term resource from entry name: {entry_name}")


def extract_column_from_source_path(source_path: str) -> str:
    """Extract the clean column name from a source path (stripping 'Schema.' prefix).

    Example:
        >>> extract_column_from_source_path("Schema.order_id")
        'order_id'
        >>> extract_column_from_source_path("")
        ''
    """
    if not source_path:
        return ""
    cleaned = source_path.strip()
    if cleaned.startswith("Schema."):
        return cleaned[len("Schema."):]
    return cleaned


def format_source_path_from_column(column: str) -> str:
    """Format a column name into a Dataplex source path (prepending 'Schema.').

    Example:
        >>> format_source_path_from_column("order_id")
        'Schema.order_id'
        >>> format_source_path_from_column("Schema.order_id")
        'Schema.order_id'
        >>> format_source_path_from_column("")
        ''
    """
    if not column:
        return ""
    cleaned = column.strip()
    if not cleaned:
        return ""
    if not cleaned.startswith("Schema."):
        return f"Schema.{cleaned}"
    return cleaned


def extract_short_id(resource_or_entry_name: str) -> str:
    """Extract a concise short ID from a full Dataplex entry or glossary term resource name.

    Examples:
        >>> extract_short_id("projects/p/locations/l/glossaries/g/terms/my-term")
        'my-term'
        >>> extract_short_id("projects/p/locations/l/entryGroups/@dataplex/entries/projects/p/locations/l/glossaries/g/terms/my-term")
        'my-term'
        >>> extract_short_id("projects/p/locations/us/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/p/datasets/d/tables/orders")
        'p.d.orders'
        >>> extract_short_id("projects/p/locations/us/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/p/datasets/d")
        'p.d'
        >>> extract_short_id("projects/p/locations/l/entryGroups/my-group/entries/my-entry")
        'my-entry'
        >>> extract_short_id("my-term")
        'my-term'
    """
    if not resource_or_entry_name:
        return ""
    name = resource_or_entry_name.strip()

    # 1. BigQuery dataset/table entry: .../bigquery.googleapis.com/projects/<p>/datasets/<d>[/tables/<t>]
    bq_match = BIGQUERY_ENTRY_ID_PATTERN.search(name)
    if bq_match:
        parts = [bq_match.group("project_id"), bq_match.group("dataset_id")]
        if bq_match.group("table_id"):
            parts.append(bq_match.group("table_id"))
        return ".".join(parts)

    # 2. Glossary term resource or term entry: .../terms/<term_id>
    term_match = re.search(r"/terms/(?P<term_id>[^/]+)$", name)
    if term_match:
        return term_match.group("term_id")

    # 3. Any other entry or resource name: its last path segment
    return name.rsplit("/", 1)[-1]


# =============================================================================
# Aspect helpers (Sheet 2)
# =============================================================================


def is_system_aspect(aspect_type_key: str) -> bool:
    """Check whether an aspect key refers to a system (non-custom) aspect.

    System aspects are already represented in Sheet 1 (overview, contacts) or
    are purely structural (glossary-term-aspect, glossary-category-aspect,
    glossary-aspect), so they must never be duplicated into Sheet 2.

    Matching is done on the aspect type id alone, whatever the project and
    location. This is deliberate: the Dataplex glossary import treats any
    aspect whose type id is 'overview' or 'contacts' as the built-in aspect and
    rejects the 'glossary-*' structural aspects in any project, so accepting
    such an aspect here would only move the failure into the metadata job. It
    also covers both project spellings: export observes the numeric project
    number ('655216118709.global.overview') while import writes the friendly
    project id ('dataplex-types.global.overview').

    Args:
        aspect_type_key: Aspect key as found in an Entry's 'aspects' map, an
            AspectType resource name, or a bare aspect type id.

    Returns:
        True if the key refers to a system aspect, False otherwise.

    Example:
        >>> is_system_aspect("655216118709.global.overview")
        True
        >>> is_system_aspect("dataplex-types.global.overview")
        True
        >>> is_system_aspect("my-project.us-central1.overview")
        True
        >>> is_system_aspect("my-project.us-central1.custom-gov")
        False
    """
    if not aspect_type_key or not aspect_type_key.strip():
        return False
    return extract_aspect_type_id(aspect_type_key) in SYSTEM_ASPECT_TYPE_IDS


def split_aspect_key(aspect_key: str) -> Optional[Tuple[str, str, str]]:
    """Split an aspect key or AspectType resource name into its components.

    Aspect type ids and location ids never contain dots, so the last two dotted
    segments are the location and the aspect type id and everything before them
    is the project. This keeps domain-scoped projects ('google.com:my-proj')
    intact.

    Args:
        aspect_key: e.g. '655216118709.us-central1.custom-gov' or
            'projects/p/locations/l/aspectTypes/custom-gov'.

    Returns:
        A (project, location, aspect_type_id) tuple, or None when the key does
        not carry a project and a location.

    Example:
        >>> split_aspect_key("google.com:my-proj.global.custom-gov")
        ('google.com:my-proj', 'global', 'custom-gov')
    """
    if not aspect_key:
        return None
    cleaned = aspect_key.strip()

    resource_match = ASPECT_RESOURCE_PATTERN.match(cleaned)
    if resource_match:
        return (
            resource_match.group('project_id'),
            resource_match.group('location_id'),
            resource_match.group('aspect_type_id'),
        )

    parts = cleaned.rsplit('.', 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        return None
    return parts[0], parts[1], parts[2]


def _is_location_id(candidate: str) -> bool:
    """Return True when a dotted segment looks like a Google Cloud location id."""
    return bool(LOCATION_ID_PATTERN.match(candidate))


def parse_aspect_identifier(
    name: str, default_project: str = "", default_location: str = ""
) -> 'ParsedAspectIdentifier':
    """Parse an 'Aspect name' cell into its components.

    Three input shapes are accepted:

    1. Short:            '<aspect_type_id>.<field_name>'
                         e.g. 'custom-gov.tier'
    2. Qualified:        '<project>.<location>.<aspect_type_id>.<field_name>'
                         e.g. 'my-project.us-central1.custom-gov.tier'
    3. Full resource:    'projects/{p}/locations/{l}/aspectTypes/{at}/{field}'

    Record sub-fields are expressed with dotted field names
    ('custom-gov.owner.email'). Disambiguation between shapes 1 and 2 relies on
    LOCATION_ID_PATTERN: only a segment that looks like a real location id
    (global/us/eu/asia or a region ending in a digit) promotes the identifier to
    the qualified form, so 'custom-gov.owner.email' is read as aspect type
    'custom-gov' with field 'owner.email'. Shape 3 is never ambiguous; use it
    when the project id contains dots or ':', when the location is not matched
    by LOCATION_ID_PATTERN, or when a short-form field's first segment looks
    like a location ('custom-gov.us.tax.rate' reads as project 'custom-gov').

    The project, location and aspect type id are lowercased (Google Cloud
    project ids, locations and Dataplex aspect type ids are always lowercase),
    so 'Custom-Gov.tier' and 'custom-gov.tier' name the same aspect type. The
    field name keeps its case, like the AspectType's field names.

    Args:
        name: The raw 'Aspect name' cell value.
        default_project: Project to use when the identifier omits one, normally
            the target glossary's project.
        default_location: Location to use when the identifier omits one,
            normally the target glossary's location.

    Returns:
        A ParsedAspectIdentifier.

    Raises:
        InvalidAspectIdentifierError: If the identifier is empty, has no field
            component, or contains empty segments.
    """
    if not name or not isinstance(name, str) or not name.strip():
        raise InvalidAspectIdentifierError(
            "Invalid 'Aspect name': value must be a non-empty string."
        )

    cleaned = name.strip()

    def _dotted_segments(field_name: str) -> List[str]:
        segments = field_name.split('.')
        if any(not segment.strip() for segment in segments):
            raise InvalidAspectIdentifierError(
                f"Invalid 'Aspect name' '{cleaned}'. Dotted segments must not be empty."
            )
        return [segment.strip() for segment in segments]

    # Shape 3: full resource path.
    resource_match = ASPECT_RESOURCE_PATTERN.match(cleaned)
    if resource_match:
        field_name = (resource_match.group('field_name') or '').strip().strip('/')
        if not field_name:
            raise InvalidAspectIdentifierError(
                f"Invalid 'Aspect name' '{cleaned}'. A field name is required, e.g. "
                f"'projects/{{project}}/locations/{{location}}/aspectTypes/{{aspectType}}/{{field}}'."
            )
        return ParsedAspectIdentifier(
            project_id=resource_match.group('project_id').lower(),
            location=resource_match.group('location_id').lower(),
            aspect_type_id=resource_match.group('aspect_type_id').lower(),
            field_name='.'.join(_dotted_segments(field_name.replace('/', '.'))),
        )

    if '/' in cleaned:
        raise InvalidAspectIdentifierError(
            f"Invalid 'Aspect name' '{cleaned}'. Resource paths must look like "
            f"'projects/{{project}}/locations/{{location}}/aspectTypes/{{aspectType}}/{{field}}'."
        )

    parts = _dotted_segments(cleaned)

    if len(parts) < 2:
        raise InvalidAspectIdentifierError(
            f"Invalid 'Aspect name' '{cleaned}'. Expected at least "
            f"'<aspectTypeId>.<fieldName>'."
        )

    # Shape 2: qualified. Requires >= 4 segments AND a location-looking segment
    # in position 1, otherwise the leading segments belong to a dotted field.
    if len(parts) >= 4 and _is_location_id(parts[1].lower()):
        return ParsedAspectIdentifier(
            project_id=parts[0].lower(),
            location=parts[1].lower(),
            aspect_type_id=parts[2].lower(),
            field_name='.'.join(parts[3:]),
        )

    # Shape 1: short (possibly with a dotted record sub-field).
    return ParsedAspectIdentifier(
        project_id=(default_project or '').lower(),
        location=(default_location or '').lower(),
        aspect_type_id=parts[0].lower(),
        field_name='.'.join(parts[1:]),
    )


def format_aspect_identifier(
    aspect_type_id: str,
    field_name: str,
    project_id: str = "",
    location: str = "",
    qualified: bool = False,
) -> str:
    """Format an 'Aspect name' cell value.

    Args:
        aspect_type_id: The AspectType id, e.g. 'custom-gov'.
        field_name: Field within the aspect, dotted for record sub-fields.
        project_id: Project owning the AspectType. Only emitted when qualified.
        location: Location of the AspectType. Only emitted when qualified.
        qualified: When True, emit the 4-part
            '<project>.<location>.<aspectTypeId>.<fieldName>' form.

    Returns:
        The formatted aspect identifier.

    Raises:
        InvalidAspectIdentifierError: If aspect_type_id or field_name is empty,
            or if qualified output is requested without project/location.
    """
    if not aspect_type_id or not field_name:
        raise InvalidAspectIdentifierError(
            "Both 'aspect_type_id' and 'field_name' are required to format an aspect identifier."
        )

    if not qualified:
        return f"{aspect_type_id.strip()}.{field_name.strip()}"

    if not project_id or not location:
        raise InvalidAspectIdentifierError(
            "A qualified aspect identifier requires both 'project_id' and 'location'."
        )
    return (
        f"{project_id.strip()}.{location.strip()}."
        f"{aspect_type_id.strip()}.{field_name.strip()}"
    )


def _parses_back_to(
    cell: str,
    expected: Tuple[str, str, str, str],
    default_project: str,
    default_location: str,
) -> bool:
    """Return True when an 'Aspect name' cell parses back to the expected parts."""
    try:
        parsed = parse_aspect_identifier(cell, default_project, default_location)
    except InvalidAspectIdentifierError:
        return False
    return (
        parsed.project_id, parsed.location, parsed.aspect_type_id, parsed.field_name
    ) == expected


def format_aspect_identifier_for_sheet(
    project_id: str,
    location: str,
    aspect_type_id: str,
    field_name: str,
    default_project: str,
    default_location: str,
) -> str:
    """Format the shortest 'Aspect name' cell that imports back to the same field.

    Import resolves the short form against the TARGET glossary's project and
    location, so export may only use it for aspect types that live in the
    glossary's own project and location. Every candidate is checked by parsing
    it back with the same defaults the importer uses, which rules out any cell
    that would be read differently (a project id containing dots, a location
    the parser does not recognize, or a field whose first segment looks like a
    location). When no dotted form is safe, the unambiguous resource form is
    used.

    Candidates, in order of preference:

    1. Short:     '<aspect_type_id>.<field_name>' (same project and location only)
    2. Qualified: '<project>.<location>.<aspect_type_id>.<field_name>'
    3. Resource:  'projects/{p}/locations/{l}/aspectTypes/{at}/{field_name}'

    Args:
        project_id: Project owning the AspectType.
        location: Location of the AspectType.
        aspect_type_id: The AspectType id.
        field_name: Field within the aspect, dotted for record sub-fields.
        default_project: The glossary's project (what the importer defaults to).
        default_location: The glossary's location (what the importer defaults to).

    Returns:
        The formatted aspect identifier.

    Raises:
        InvalidAspectIdentifierError: If any component is empty.
    """
    if not (project_id and location and aspect_type_id and field_name):
        raise InvalidAspectIdentifierError(
            "project_id, location, aspect_type_id and field_name are all required to "
            "format an aspect identifier for the sheet."
        )

    expected = (project_id, location, aspect_type_id, field_name)
    candidates = []
    if project_id == default_project and location == default_location:
        candidates.append(format_aspect_identifier(aspect_type_id, field_name))
    candidates.append(
        format_aspect_identifier(
            aspect_type_id, field_name, project_id=project_id, location=location, qualified=True
        )
    )

    for candidate in candidates:
        if _parses_back_to(candidate, expected, default_project, default_location):
            return candidate

    return (
        f"projects/{project_id}/locations/{location}/aspectTypes/{aspect_type_id}/{field_name}"
    )


def _coerce_bool(raw: str, allow_numeric: bool = True) -> Optional[bool]:
    """Coerce a string into a bool, returning None when it is not boolean-like."""
    lowered = raw.strip().lower()
    if lowered in BOOL_TRUE_LITERALS:
        return True
    if lowered in BOOL_FALSE_LITERALS:
        return False
    if allow_numeric:
        if lowered in BOOL_NUMERIC_TRUE_LITERALS:
            return True
        if lowered in BOOL_NUMERIC_FALSE_LITERALS:
            return False
    return None


def _coerce_json(raw: str) -> Optional[Any]:
    """Parse a JSON array/object literal, returning None when it is not valid JSON."""
    stripped = raw.strip()
    if not stripped or stripped[0] not in ('[', '{'):
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return None


def _split_comma_list(raw: str, item_type: Optional[str] = None) -> List[Any]:
    """Split a comma-separated cell into a list of coerced scalar values.

    Args:
        raw: The cell value.
        item_type: The array's declared item type, if known. Each item is
            coerced to it ('007' stays a string for a string array).
    """
    stripped = raw.strip()
    if not stripped:
        return []
    return [coerce_aspect_value(item.strip(), item_type) for item in stripped.split(',')]


def _coerce_with_declared_type(raw: str, field_type: str, item_type: Optional[str] = None) -> Any:
    """Coerce a cell using the declared MetadataTemplate type.

    Falls back to heuristics whenever the declared type cannot be honoured, so
    a schema mismatch degrades gracefully instead of failing the whole import.
    """
    stripped = raw.strip()

    if field_type in (ASPECT_FIELD_TYPE_STRING, ASPECT_FIELD_TYPE_ENUM, ASPECT_FIELD_TYPE_DATETIME):
        return stripped

    if field_type == ASPECT_FIELD_TYPE_BOOL:
        coerced = _coerce_bool(stripped, allow_numeric=True)
        return coerced if coerced is not None else stripped

    if field_type == ASPECT_FIELD_TYPE_INT:
        if INTEGER_LITERAL_PATTERN.match(stripped):
            return int(stripped)
        return _coerce_without_type(stripped)

    if field_type == ASPECT_FIELD_TYPE_DOUBLE:
        if DECIMAL_LITERAL_PATTERN.match(stripped):
            return float(stripped)
        return _coerce_without_type(stripped)

    if field_type == ASPECT_FIELD_TYPE_ARRAY:
        parsed = _coerce_json(stripped)
        if isinstance(parsed, list):
            return parsed
        # Comma-separated fallback is only safe when the schema says 'array'.
        return _split_comma_list(stripped, item_type)

    if field_type in (ASPECT_FIELD_TYPE_MAP, ASPECT_FIELD_TYPE_RECORD):
        parsed = _coerce_json(stripped)
        if isinstance(parsed, dict):
            return parsed
        return _coerce_without_type(stripped)

    return _coerce_without_type(stripped)


def _coerce_without_type(raw: str) -> Any:
    """Coerce a cell using heuristics only (no AspectType schema available)."""
    stripped = raw.strip()
    if not stripped:
        return ""

    parsed_json = _coerce_json(stripped)
    if parsed_json is not None:
        return parsed_json

    # '1'/'0' are intentionally NOT treated as booleans here: without a schema,
    # numeric interpretation wins. Only explicit spellings become bools.
    coerced_bool = _coerce_bool(stripped, allow_numeric=False)
    if coerced_bool is not None:
        return coerced_bool

    if INTEGER_LITERAL_PATTERN.match(stripped):
        return int(stripped)

    if DECIMAL_LITERAL_PATTERN.match(stripped) and not INTEGER_LITERAL_PATTERN.match(stripped):
        return float(stripped)

    return stripped


def coerce_aspect_value(
    raw: Any, field_type: Optional[str] = None, item_type: Optional[str] = None
) -> Any:
    """Convert a raw 'Aspect value' cell into a typed Python value.

    When the AspectType's MetadataTemplate declares the field type, coercion is
    driven by that type. Otherwise heuristics are used:

    * ``true``/``false``/``yes``/``no`` (case-insensitive) -> bool
    * integer-looking            -> int
    * decimal-looking            -> float
    * leading ``[`` or ``{``     -> ``json.loads``
    * anything else              -> the original string

    Note: ``1``/``0`` become ints under the heuristics and bools only when
    ``field_type`` is ``bool``. Comma-separated values are only split into a
    list when ``field_type`` is ``array``.

    Args:
        raw: The raw cell value.
        field_type: Optional MetadataTemplate type ('string', 'int', 'bool',
            'double', 'datetime', 'enum', 'array', 'map', 'record').
        item_type: Optional item type of an 'array' field, used to coerce the
            items of a comma-separated list.

    Returns:
        The coerced value, or the original string when no coercion applies.
    """
    if raw is None:
        return ""
    if isinstance(raw, (bool, int, float, list, dict)):
        return raw
    if not isinstance(raw, str):
        return raw

    if field_type:
        return _coerce_with_declared_type(
            raw, field_type.strip().lower(), (item_type or '').strip().lower() or None
        )
    return _coerce_without_type(raw)


def resolve_field_type(
    field_types: Dict[str, str], field_path: List[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Return the declared (type, array item type) of a dotted field path.

    field_types comes from api_layer.get_aspect_field_types: record sub-fields
    are keyed by their dotted path ('owner.email'), map values by the map's
    path plus '.*' ('labels.*'), and array items by the array's path plus
    '[]' ('tags[]'). A map entry exported as its own row ('labels.code') is
    therefore resolved through 'labels.*'.

    Args:
        field_types: Mapping of field path to declared type.
        field_path: The field name split into its dotted segments.

    Returns:
        A (field_type, item_type) tuple; either is None when not declared.
    """
    resolved = ''
    for segment in field_path:
        exact = f"{resolved}.{segment}" if resolved else segment
        wildcard = f"{resolved}.*" if resolved else ''
        if exact in field_types:
            resolved = exact
        elif wildcard and wildcard in field_types:
            resolved = wildcard
        else:
            return None, None
    if not resolved:
        return None, None
    return field_types.get(resolved), field_types.get(f"{resolved}[]")


def serialize_aspect_value(value: Any) -> str:
    """Convert a typed aspect value into its 'Aspect value' cell representation.

    Args:
        value: The value taken from an Entry's aspect ``data`` dict.

    Returns:
        A string suitable for writing into a spreadsheet cell. Booleans become
        ``true``/``false``; lists and dicts become compact JSON; ``None``
        becomes an empty string; everything else is stringified.

    Example:
        >>> serialize_aspect_value(True)
        'true'
        >>> serialize_aspect_value(["finance", "core"])
        '["finance","core"]'
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    return str(value)


def flatten_aspect_data(
    data: Dict[str, Any], max_depth: int = MAX_ASPECT_FLATTEN_DEPTH
) -> List[Tuple[str, str]]:
    """Flatten an aspect ``data`` dict into (dotted_field_name, cell_value) pairs.

    Record and map fields are flattened into dotted sub-field rows for up to
    ``max_depth`` path segments (``owner.email``). Any dict still present at the
    depth limit is serialized as a compact JSON object string instead, which
    keeps deep structures lossless while keeping the common one-level case
    human-editable. A dict whose keys cannot be expressed as dotted segments
    (empty keys, or keys containing a '.', as map fields allow) is also kept as
    JSON, because flattening it would rebuild a different structure on import.

    Args:
        data: The aspect's ``data`` dictionary.
        max_depth: Maximum number of dotted segments to emit.

    Returns:
        A list of (field_name, serialized_value) tuples in insertion order.
    """
    flattened: List[Tuple[str, str]] = []

    def _can_flatten(value: Any) -> bool:
        return isinstance(value, dict) and bool(value) and all(
            isinstance(key, str) and key and '.' not in key for key in value
        )

    def _walk(current: Dict[str, Any], prefix: List[str]) -> None:
        for key, value in current.items():
            path = prefix + [str(key)]
            if _can_flatten(value) and len(path) < max_depth:
                _walk(value, path)
            else:
                flattened.append(('.'.join(path), serialize_aspect_value(value)))

    if not isinstance(data, dict):
        return flattened

    _walk(data, [])
    return flattened


def set_nested_aspect_field(data: Dict[str, Any], field_path: List[str], value: Any) -> None:
    """Assign a value into an aspect ``data`` dict following a dotted field path.

    Intermediate dictionaries are created as needed, which is what makes
    'custom-gov.owner.email' round-trip back into ``{'owner': {'email': ...}}``.
    An existing value is never overwritten: callers that need order-independent
    conflict detection across rows should also use find_conflicting_field_path.

    Args:
        data: The aspect ``data`` dict to mutate.
        field_path: Field name split into segments.
        value: The coerced value to assign.

    Raises:
        InvalidAspectIdentifierError: If field_path is empty, a segment
            collides with an existing scalar value, or the target field
            already holds a value or sub-fields.
    """
    if not field_path:
        raise InvalidAspectIdentifierError("Cannot assign an aspect value without a field name.")

    cursor = data
    for segment in field_path[:-1]:
        existing = cursor.get(segment)
        if existing is None:
            cursor[segment] = {}
        elif not isinstance(existing, dict):
            raise InvalidAspectIdentifierError(
                f"Conflicting aspect field '{'.'.join(field_path)}': "
                f"'{segment}' already holds a scalar value."
            )
        cursor = cursor[segment]

    leaf = field_path[-1]
    if leaf in cursor:
        raise InvalidAspectIdentifierError(
            f"Conflicting aspect field '{'.'.join(field_path)}': "
            f"'{leaf}' already has a value or sub-fields assigned."
        )
    cursor[leaf] = value


def find_conflicting_field_path(
    assigned_paths: Iterable[Tuple[str, ...]], new_path: Tuple[str, ...]
) -> Optional[Tuple[str, ...]]:
    """Find an already-assigned field path that overlaps a new one.

    Two paths overlap when they are equal or one is a prefix of the other
    ('owner' and 'owner.email'): both rows would write the same part of the
    aspect, so whichever ran last would silently win. Checking every pair makes
    the result independent of row order.

    Args:
        assigned_paths: Field paths already assigned for the same aspect.
        new_path: The field path about to be assigned.

    Returns:
        The first overlapping path, or None when there is no overlap.

    Example:
        >>> find_conflicting_field_path([("owner", "email")], ("owner",))
        ('owner', 'email')
        >>> find_conflicting_field_path([("owner", "email")], ("owner", "name")) is None
        True
    """
    for assigned in assigned_paths:
        shorter = min(len(assigned), len(new_path))
        if assigned[:shorter] == new_path[:shorter]:
            return assigned
    return None


def extract_aspect_type_id(aspect_key: str) -> str:
    """Extract the bare aspect type id from an aspect key or resource name.

    Args:
        aspect_key: e.g. '655216118709.global.overview' or
            'projects/p/locations/l/aspectTypes/custom-gov'.

    Returns:
        The aspect type id, or the input unchanged when it cannot be parsed.
    """
    if not aspect_key:
        return ""
    cleaned = aspect_key.strip()

    split_key = split_aspect_key(cleaned)
    if split_key:
        return split_key[2]

    return cleaned


def aspect_key_from_resource(aspect_type_resource: str) -> str:
    """Convert an AspectType resource name into an Entry aspects-map key.

    Args:
        aspect_type_resource: e.g.
            'projects/my-project/locations/us-central1/aspectTypes/custom-gov'.

    Returns:
        The dotted aspects-map key, e.g. 'my-project.us-central1.custom-gov'.
        Returns the input unchanged when it is not a resource name.
    """
    if not aspect_type_resource:
        return ""
    match = ASPECT_RESOURCE_PATTERN.match(aspect_type_resource.strip())
    if not match:
        return aspect_type_resource.strip()
    return (
        f"{match.group('project_id')}.{match.group('location_id')}."
        f"{match.group('aspect_type_id')}"
    )
