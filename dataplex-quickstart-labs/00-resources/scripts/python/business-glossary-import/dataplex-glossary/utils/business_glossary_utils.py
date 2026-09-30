"""
Business Glossary Utility Functions

Common utility functions for working with Dataplex Glossary resources.
"""

# Standard library imports
import re
import uuid

# Local imports
from utils.constants import (
    BIGQUERY_ENTRY_ID_PATTERN,
    DATAPLEX_SYSTEM_ENTRY_GROUP,
    GLOSSARY_NAME_PATTERN,
    TERM_NAME_PATTERN,
)
from utils.error import InvalidTermIdentifierError, InvalidTermNameError
from utils.models import ParsedTermIdentifier


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
