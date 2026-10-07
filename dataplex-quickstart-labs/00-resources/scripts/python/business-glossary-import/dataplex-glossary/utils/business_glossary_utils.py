"""
Business Glossary Utility Functions

Common utility functions for working with Dataplex Glossary resources.
"""

# Standard library imports
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

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
    ASPECT_KEY_PATTERN,
    ASPECT_RESOURCE_PATTERN,
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
    SYSTEM_ASPECT_KEY_SUFFIXES,
    SYSTEM_ASPECT_TYPE_IDS,
    TERM_NAME_PATTERN,
)
from utils.error import InvalidAspectIdentifierError, InvalidTermNameError


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


def generate_entry_name_from_term_name(term_name: str) -> str:
    """
    Generates a Dataplex entry ID from a glossary term name.
    
    Args:
        term_name: The full term name in format:
                   projects/{project}/locations/{location}/glossaries/{glossary}/terms/{term}
    Returns:
        The generated entry ID in format:
        projects/{project}/locations/{location}/entryGroups/@dataplex/entries/projects/{project}/locations/{location}/glossaries/{glossary}/terms/{term}
    """
    match = TERM_NAME_PATTERN.match(term_name)
    if not match:
        raise InvalidTermNameError(f"Invalid term name format: {term_name}")
    
    project_id = match.group('project_id')
    location_id = match.group('location_id')
    glossary_id = match.group('glossary_id')
    term_id = match.group('term_id')
    
    return (
        f"projects/{project_id}/locations/{location_id}/entryGroups/{DATAPLEX_SYSTEM_ENTRY_GROUP}/entries/"
        f"projects/{project_id}/locations/{location_id}/glossaries/{glossary_id}/terms/{term_id}"
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


# =============================================================================
# Aspect helpers (Sheet 2)
# =============================================================================


def is_system_aspect(aspect_type_key: str) -> bool:
    """Check whether an aspect key refers to a system (non-custom) aspect.

    System aspects are already represented in Sheet 1 (overview, contacts) or
    are purely structural (glossary-term-aspect, glossary-category-aspect), so
    they must never be duplicated into Sheet 2.

    Matching is done on the '<location>.<aspect_type_id>' suffix because the
    project component differs between tools: export observes the numeric
    project number ('655216118709.global.overview') while import writes the
    friendly project id ('dataplex-types.global.overview').

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
        >>> is_system_aspect("my-project.us-central1.custom-gov")
        False
    """
    if not aspect_type_key:
        return False

    key = aspect_type_key.strip()

    resource_match = ASPECT_RESOURCE_PATTERN.match(key)
    if resource_match:
        return resource_match.group('aspect_type_id') in SYSTEM_ASPECT_TYPE_IDS

    if key in SYSTEM_ASPECT_TYPE_IDS:
        return True

    return any(key.endswith(f".{suffix}") or key == suffix for suffix in SYSTEM_ASPECT_KEY_SUFFIXES)


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
    'custom-gov' with field 'owner.email'.

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
    from utils.models import ParsedAspectIdentifier

    if not name or not isinstance(name, str) or not name.strip():
        raise InvalidAspectIdentifierError(
            "Invalid 'Aspect name': value must be a non-empty string."
        )

    cleaned = name.strip()

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
            project_id=resource_match.group('project_id'),
            location=resource_match.group('location_id'),
            aspect_type_id=resource_match.group('aspect_type_id'),
            field_name=field_name.replace('/', '.'),
        )

    if '/' in cleaned:
        raise InvalidAspectIdentifierError(
            f"Invalid 'Aspect name' '{cleaned}'. Resource paths must look like "
            f"'projects/{{project}}/locations/{{location}}/aspectTypes/{{aspectType}}/{{field}}'."
        )

    parts = cleaned.split('.')
    if any(not part.strip() for part in parts):
        raise InvalidAspectIdentifierError(
            f"Invalid 'Aspect name' '{cleaned}'. Dotted segments must not be empty."
        )
    parts = [part.strip() for part in parts]

    if len(parts) < 2:
        raise InvalidAspectIdentifierError(
            f"Invalid 'Aspect name' '{cleaned}'. Expected at least "
            f"'<aspectTypeId>.<fieldName>'."
        )

    # Shape 2: qualified. Requires >= 4 segments AND a location-looking segment
    # in position 1, otherwise the leading segments belong to a dotted field.
    if len(parts) >= 4 and _is_location_id(parts[1]):
        return ParsedAspectIdentifier(
            project_id=parts[0],
            location=parts[1],
            aspect_type_id=parts[2],
            field_name='.'.join(parts[3:]),
        )

    # Shape 1: short (possibly with a dotted record sub-field).
    return ParsedAspectIdentifier(
        project_id=default_project,
        location=default_location,
        aspect_type_id=parts[0],
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


def _split_comma_list(raw: str) -> List[Any]:
    """Split a comma-separated cell into a list of coerced scalar values."""
    stripped = raw.strip()
    if not stripped:
        return []
    return [coerce_aspect_value(item.strip()) for item in stripped.split(',')]


def _coerce_with_declared_type(raw: str, field_type: str) -> Any:
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
        return _split_comma_list(stripped)

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


def coerce_aspect_value(raw: Any, field_type: Optional[str] = None) -> Any:
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
        return _coerce_with_declared_type(raw, field_type.strip().lower())
    return _coerce_without_type(raw)


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
    human-editable.

    Args:
        data: The aspect's ``data`` dictionary.
        max_depth: Maximum number of dotted segments to emit.

    Returns:
        A list of (field_name, serialized_value) tuples in insertion order.
    """
    flattened: List[Tuple[str, str]] = []

    def _walk(current: Dict[str, Any], prefix: List[str]) -> None:
        for key, value in current.items():
            path = prefix + [str(key)]
            if isinstance(value, dict) and value and len(path) < max_depth:
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

    Args:
        data: The aspect ``data`` dict to mutate.
        field_path: Field name split into segments.
        value: The coerced value to assign.

    Raises:
        InvalidAspectIdentifierError: If field_path is empty, or a segment
            collides with an existing scalar value.
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

    cursor[field_path[-1]] = value


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

    resource_match = ASPECT_RESOURCE_PATTERN.match(cleaned)
    if resource_match:
        return resource_match.group('aspect_type_id')

    key_match = ASPECT_KEY_PATTERN.match(cleaned)
    if key_match:
        return key_match.group('aspect_type_id')

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


