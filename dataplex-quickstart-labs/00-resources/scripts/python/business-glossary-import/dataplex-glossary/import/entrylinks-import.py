"""EntryLink Import Utility - Imports EntryLinks into Dataplex from Google Sheets."""

import os
import select
import sys
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from typing import Callable, Dict, Iterable, List, Optional, Tuple

curr_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(curr_dir))
sys.path.append(os.path.dirname(os.path.dirname(curr_dir)))

from utils import api_layer, argument_parser, business_glossary_utils, constants, file_utils, gcs_dao, import_utils, logging_utils, sheet_utils
from utils.constants import (
    ARCHIVE_DIRECTORY,
    DP_LINK_TYPE_DEFINITION,
    DP_LINK_TYPE_RELATED,
    DP_LINK_TYPE_SYNONYM,
    ENTRY_REFERENCE_TYPE_SOURCE,
    ENTRY_REFERENCE_TYPE_TARGET,
    MAX_WORKERS,
    PROCESSED_DIRECTORY,
    SOURCE_ENTRY_PATTERN,
)
from utils.error import TermNameMismatchError
from utils.models import EntryLink, EntryReference, SpreadsheetRow

logger = logging_utils.get_logger()

USER_INPUT_TIMEOUT = 60  # seconds
MAX_LISTED_ITEMS = 20  # longer lists are written in full to the log file only


def _read_user_input_with_select(timeout: int) -> str:
    """Read user input using select for timeout support."""
    ready_streams, _, _ = select.select([sys.stdin], [], [], timeout)
    if ready_streams:
        return sys.stdin.readline().strip()
    print()  # newline after timeout
    return ''


def get_user_input_with_timeout(prompt: str, timeout: int = USER_INPUT_TIMEOUT) -> str:
    """Get user input with a timeout. Returns empty string on timeout."""
    print(prompt, end='', flush=True)
    return _read_user_input_with_select(timeout)


def _log_items(items: List[str], log: Callable[[str], None]) -> None:
    """Log a list of items, showing at most MAX_LISTED_ITEMS on the console (all of them go to the log file)."""
    for item in items[:MAX_LISTED_ITEMS]:
        log(f"  - {item}")
    for item in items[MAX_LISTED_ITEMS:]:
        logger.debug(f"  - {item}")
    if len(items) > MAX_LISTED_ITEMS:
        log(f"  ... and {len(items) - MAX_LISTED_ITEMS} more (see the log file)")


def _format_failed_rows(failed_rows: List[Tuple[int, str]]) -> List[str]:
    """Format (row number, reason) pairs, ordered by row number."""
    return [f"Row {row_number}: {reason}" for row_number, reason in sorted(failed_rows)]


def confirm_import(failed_rows: List[Tuple[int, str]], missing_entries: Iterable[str]) -> bool:
    """Report rows that can't be imported and referenced entries that don't exist, then ask to continue.

    Returns:
        True if there is nothing to report or the user chooses to continue, False otherwise.
    """
    missing_entries = sorted(missing_entries)
    if not failed_rows and not missing_entries:
        return True

    if failed_rows:
        logger.warning(f"{len(failed_rows)} row(s) can't be imported and will be skipped:")
        _log_items(_format_failed_rows(failed_rows), logger.warning)
    if missing_entries:
        logger.warning(f"{len(missing_entries)} referenced entry(ies) were not found in Dataplex; "
                       f"entry links that use them may fail during import:")
        _log_items(missing_entries, logger.warning)

    user_response = get_user_input_with_timeout("Continue with import? [y/N]: ")
    if not user_response.lower().startswith('y'):
        logger.info("Import aborted.")
        return False
    return True


def _get_existing_archive_files(archive_dir: str) -> List[str]:
    """Get list of existing JSON files in archive directory."""
    if not os.path.exists(archive_dir):
        return []
    return [filename for filename in os.listdir(archive_dir) if filename.endswith('.json')]


def _remove_archive_files(archive_dir: str, filenames: List[str]) -> None:
    """Remove specified files from archive directory."""
    for filename in filenames:
        file_path = os.path.join(archive_dir, filename)
        try:
            os.remove(file_path)
            logger.debug(f"Removed: {filename}")
        except Exception as remove_error:
            logger.error(f"Failed to remove {filename}: {remove_error}")


def check_and_clean_archive_folder(archive_dir: str) -> bool:
    """Check if archive folder has existing files and prompt user to clean."""
    existing_json_files = _get_existing_archive_files(archive_dir)
    if not existing_json_files:
        return True

    file_count = len(existing_json_files)
    logger.warning(f"Found {file_count} existing file(s) in archive folder from a previous incomplete import")

    clear_response = get_user_input_with_timeout("Do you want to clear the archive folder and start a fresh import? [y/N]: ")
    if clear_response.lower().startswith('y'):
        _remove_archive_files(archive_dir, existing_json_files)
        logger.info("Archive folder cleared. Proceeding with fresh import.")
        return True

    continue_response = get_user_input_with_timeout("Continue using existing files? [y/N]: ")
    if continue_response.lower().startswith('y'):
        logger.info("Continuing with existing files in archive folder.")
        return True

    logger.info("Import aborted.")
    return False


def _collect_unique_entry_references(entrylinks: List[EntryLink]) -> List:
    """Collect all unique entry references from entrylinks."""
    unique_references = []
    processed_entry_names = set()

    for entrylink in entrylinks:
        for entry_ref in entrylink.entryReferences:
            if entry_ref.name and entry_ref.name not in processed_entry_names:
                processed_entry_names.add(entry_ref.name)
                unique_references.append(entry_ref)

    return unique_references


def _lookup_and_check_entry(entry_ref, missing_entries: set, failed_entries: set) -> None:
    """Lookup a single entry and track if missing or failed.

    Creates its own dataplex_service to ensure thread safety (httplib2 is not thread-safe).
    Uses sets for thread-safe accumulation (set.add is GIL-atomic in CPython).
    """
    entry_name = entry_ref.name

    try:
        project_id, location, _, _ = api_layer.parse_entry_name(entry_name)
    except Exception:
        logger.warning(f"Invalid entry name format: {entry_name}")
        return

    project_location = f"projects/{project_id}/locations/{location}"

    # Create thread-local service instance (httplib2 is not thread-safe)
    dataplex_service = api_layer.authenticate_dataplex()

    try:
        result = api_layer.lookup_entry(dataplex_service, entry_name, project_location)
        if result is None:
            # Entry genuinely not found (404)
            missing_entries.add(entry_name)
            logger.debug(f"Entry not found (404): {entry_name}")
    except Exception as e:
        # Network/SSL error - entry lookup failed, not necessarily missing
        failed_entries.add(entry_name)
        logger.error(f"Failed to lookup entry (network error): {entry_name}: {e}")


def check_entry_existence(entrylinks: List[EntryLink]) -> tuple:
    """Check if entries exist using parallel lookups.

    Entries already confirmed to exist while resolving the sheet rows are not looked up again.

    Returns:
        tuple: (missing_entry_names, failed_entry_names)
            - missing_entry_names: Entries that returned 404 (don't exist)
            - failed_entry_names: Entries that failed due to network errors
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    unique_entry_refs = [
        ref for ref in _collect_unique_entry_references(entrylinks)
        if not api_layer.is_known_entry(ref.name)
    ]
    missing_entry_names = set()
    failed_entry_names = set()
    if not unique_entry_refs:
        return missing_entry_names, failed_entry_names

    logger.info(f"Validating {len(unique_entry_refs)} unique entries...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        lookup_futures = [
            executor.submit(_lookup_and_check_entry, ref, missing_entry_names, failed_entry_names)
            for ref in unique_entry_refs
        ]
        for completed_future in as_completed(lookup_futures):
            try:
                completed_future.result()
            except Exception as lookup_error:
                logger.error(f"Unexpected error during entry lookup: {lookup_error}")

    return missing_entry_names, failed_entry_names


def convert_spreadsheet_to_entrylinks(
    spreadsheet_url: str,
    sheet_name: str = None,
    dataplex_service=None,
    user_project: str = "",
    failed_rows: Optional[List[Tuple[int, str]]] = None
) -> List[EntryLink]:
    """Convert spreadsheet rows to EntryLink entries, in the order of the rows.

    Resolving the rows takes API calls, so rows are resolved in parallel, each worker thread
    using its own Dataplex client unless `dataplex_service` is given. Rows that can't be
    converted are left out; their (row number, reason) is appended to `failed_rows` when
    given (see build_entry_link).

    Raises:
        Exception: If resolving a row kept failing with network or server errors (see
            build_entry_link); rows whose resolution hasn't started yet are then skipped.
    """
    spreadsheet_data = sheet_utils.read_from_spreadsheet_url(spreadsheet_url, sheet_name=sheet_name)

    if not spreadsheet_data or len(spreadsheet_data) < 2:
        return []

    type_idx, source_name_idx, source_id_idx, column_idx, target_name_idx, target_id_idx = sheet_utils.extract_column_indices(spreadsheet_data)
    row_dicts = sheet_utils.rows_to_entry_link_dicts(
        spreadsheet_data, type_idx, source_name_idx, source_id_idx, column_idx, target_name_idx, target_id_idx
    )

    def to_entrylink(row_dict: Dict) -> Optional[EntryLink]:
        return build_entry_link(
            SpreadsheetRow.from_dict(row_dict),
            dataplex_service=dataplex_service or api_layer.get_dataplex_service(),
            user_project=user_project,
            failed_rows=failed_rows
        )

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    try:
        futures = [executor.submit(to_entrylink, row_dict) for row_dict in row_dicts]
        wait(futures, return_when=FIRST_EXCEPTION)
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    # After an error, rows whose resolution hasn't started yet are cancelled.
    executor.shutdown(cancel_futures=True)
    entrylinks = [future.result() for future in futures if not future.cancelled()]
    return [entrylink for entrylink in entrylinks if entrylink is not None]


def _parse_source_entry_components(source_entry: str) -> tuple:
    """Parse source entry to extract project, location, and entry group."""
    source_entry_match = SOURCE_ENTRY_PATTERN.match(source_entry)
    if not source_entry_match:
        logger.error(f"Invalid source entry format: {source_entry}")
        raise ValueError(f"Invalid source entry format: {source_entry}")

    return (
        source_entry_match.group('project_id'),
        source_entry_match.group('location_id'),
        source_entry_match.group('entry_group')
    )


def _generate_entrylink_name(project_id: str, location: str, entry_group: str) -> str:
    """Generate a unique entrylink name."""
    entrylink_base = f"projects/{project_id}/locations/{location}/entryGroups/{entry_group}"
    entrylink_id = business_glossary_utils.generate_entry_link_id()
    return f"{entrylink_base}/entryLinks/{entrylink_id}"


def _resolve_explicit_resource_name(resource_name: str, user_project: str = "") -> str:
    """Normalize an explicit Dataplex term or entry resource path."""
    if constants.TERM_NAME_PATTERN.match(resource_name):
        project_id = business_glossary_utils.extract_project_id_from_name(resource_name)
        project_number = api_layer.get_project_number(project_id, user_project) if user_project else ""
        return business_glossary_utils.generate_entry_name_from_term_name(resource_name, project_number=project_number)
    return api_layer.normalize_entry_name_project_number(resource_name, user_project)


def _full_resource_name(name: str, resource_id: str) -> str:
    """Return the full 'projects/...' resource name given in the ID or Name cell, if any."""
    for value in (resource_id, name):
        if value.startswith('projects/'):
            return value
    return ""


def _resolve_source_entry_name(
    source_name: str,
    link_type: str,
    source_id: str = "",
    dataplex_service=None,
    user_project: str = ""
) -> str:
    """Resolve the Source Name / Source ID cells to a Dataplex entry name.

    For definition links the source is a data asset, found by the FQN in Source Name
    (Source ID is informational). Otherwise it is a glossary term (see _resolve_term_entry_name).
    A full 'projects/...' resource name in either cell is used as is.
    """
    if link_type != DP_LINK_TYPE_DEFINITION:
        return _resolve_term_entry_name("Source", source_name, source_id, dataplex_service, user_project)

    full_name = _full_resource_name(source_name, source_id)
    if full_name:
        return _resolve_explicit_resource_name(full_name, user_project)
    if not source_name:
        raise ValueError("Source Name is required: the data asset's fully qualified name, e.g. 'bigquery:project.dataset.table'")
    if not dataplex_service:
        raise ValueError(f"Cannot resolve FQN '{source_name}' without Dataplex service")
    return api_layer.lookup_entry_by_fqn(dataplex_service, source_name, user_project)['name']


def _resolve_target_entry_name(
    target_name: str,
    target_id: str = "",
    dataplex_service=None,
    user_project: str = ""
) -> str:
    """Resolve the Target Name / Target ID cells (always a glossary term) to a Dataplex entry name."""
    return _resolve_term_entry_name("Target", target_name, target_id, dataplex_service, user_project)


def _resolve_term_entry_name(
    label: str,
    name: str,
    term_id: str,
    dataplex_service=None,
    user_project: str = ""
) -> str:
    """Resolve the Name / ID cells of a glossary term to the term's Dataplex entry name.

    Name ('<project>.<location>.<glossary>.<term>') identifies the glossary. ID, if given,
    identifies the term, and the term display name in Name (if any) must match it exactly;
    otherwise the term is found by its exact display name. A full 'projects/...' resource name in
    either cell is used as is.
    """
    full_name = _full_resource_name(name, term_id)
    if full_name:
        return _resolve_explicit_resource_name(full_name, user_project)
    if not name:
        raise ValueError(f"{label} Name is required: '<project>.<location>.<glossary>.<term>'")
    if not term_id and len(name.split(".")) < 4:
        raise ValueError(
            f"{label} ID is required when {label} Name has no term display name; "
            f"use '<project>.<location>.<glossary>.<term>' or put the term ID in {label} ID"
        )
    if not dataplex_service:
        raise ValueError(f"Cannot resolve term '{name}' without Dataplex service")
    try:
        return api_layer.lookup_term_by_display_identifier(dataplex_service, name, user_project, term_id=term_id)
    except TermNameMismatchError as mismatch:
        raise ValueError(
            f"{label} Name '{mismatch.term_name}' does not match {label} ID '{mismatch.term_id}' "
            f"('{mismatch.display_name}'). Update or clear {label} ID."
        ) from mismatch


def _record_failed_row(row_number: int, reason: str, failed_rows: Optional[List[Tuple[int, str]]]) -> None:
    """Record why a row can't be imported: in failed_rows if given, otherwise as a warning."""
    if failed_rows is None:
        logger.warning(f"Row {row_number} skipped: {reason}")
        return
    failed_rows.append((row_number, reason))
    logger.debug(f"Row {row_number} skipped: {reason}")


def build_entry_link(
    spreadsheet_row: SpreadsheetRow,
    dataplex_service=None,
    user_project: str = "",
    failed_rows: Optional[List[Tuple[int, str]]] = None
) -> Optional[EntryLink]:
    """Build EntryLink model from spreadsheet row data.

    Returns None if the row can't be imported (invalid type, missing cells or unresolved
    references). The reason is appended to `failed_rows` as (row number, reason) when
    given, and logged as a warning otherwise.

    Raises:
        Exception: If resolving the row kept failing with network or server errors
            (see api_layer.is_transient_error); the other rows would fail the same way.
    """
    try:
        link_type = spreadsheet_row.entry_link_type.lower()
        if link_type not in constants.LINK_TYPES:
            raise ValueError(f"Invalid entry link type '{spreadsheet_row.entry_link_type}'. "
                             f"Expected one of: {list(constants.LINK_TYPES.keys())}")
        source_entry = _resolve_source_entry_name(
            spreadsheet_row.source_name, link_type, source_id=spreadsheet_row.source_id,
            dataplex_service=dataplex_service, user_project=user_project
        )
        target_entry = _resolve_target_entry_name(
            spreadsheet_row.target_name, target_id=spreadsheet_row.target_id,
            dataplex_service=dataplex_service, user_project=user_project
        )
        project_id, location, entry_group = _parse_source_entry_components(source_entry)
    except Exception as row_error:
        if api_layer.is_transient_error(row_error):
            raise
        _record_failed_row(spreadsheet_row.row_number, str(row_error) or type(row_error).__name__, failed_rows)
        return None

    entrylink = EntryLink(
        name=_generate_entrylink_name(project_id, location, entry_group),
        entryLinkType=constants.LINK_TYPES[link_type],
        entryReferences=build_entry_references(source_entry, target_entry, spreadsheet_row.column, link_type)
    )

    logger.debug(f"input row: {spreadsheet_row}, output entrylink: {entrylink}")
    return entrylink


def _build_definition_references(source_entry: str, target_entry: str, column: str = "") -> List[EntryReference]:
    """Build entry references for definition link type."""
    return [
        EntryReference(
            name=source_entry,
            path=business_glossary_utils.format_source_path_from_column(column),
            type=ENTRY_REFERENCE_TYPE_SOURCE
        ),
        EntryReference(
            name=target_entry,
            path='',
            type=ENTRY_REFERENCE_TYPE_TARGET
        )
    ]


def build_entry_references(
    source_entry: str,
    target_entry: str,
    column: str = "",
    link_type: str = ""
) -> List[EntryReference]:
    """Build list of EntryReference models. The column is only used by definition links."""
    if link_type == DP_LINK_TYPE_DEFINITION:
        return _build_definition_references(source_entry, target_entry, column)

    return [
        EntryReference(name=source_entry),
        EntryReference(name=target_entry)
    ]


def extract_entrylink_components(entrylink_name: str) -> tuple[str, str, str]:
    """Extract project_id, location, and entry_group from an entrylink name."""
    entrylink_name_match = constants.ENTRYLINK_NAME_PATTERN.match(entrylink_name)
    if not entrylink_name_match:
        logger.error(f"Invalid entryLink name format: {entrylink_name}")
        raise ValueError(f"Invalid entryLink name format: {entrylink_name}")

    return (
        entrylink_name_match.group('project_id'),
        entrylink_name_match.group('location_id'),
        entrylink_name_match.group('entry_group')
    )


def _extract_normalized_link_type(entrylink_type_name: str) -> str:
    """Extract and normalize link type from full type name."""
    link_type_match = constants.ENTRYLINK_TYPE_PATTERN.match(entrylink_type_name)
    if not link_type_match:
        return None

    link_type = link_type_match.group('link_type')
    if link_type in [DP_LINK_TYPE_RELATED, DP_LINK_TYPE_SYNONYM]:
        return 'related-synonym'  # Group both types together
    return link_type


def _add_entrylink_to_group(
    grouped_entrylinks: Dict,
    entrylink_dict: Dict,
    link_type: str,
    project_id: str,
    location: str,
    entry_group: str
) -> None:
    """Add an entrylink to the appropriate group."""
    group_key = f"{project_id}_{location}_{entry_group}"

    if link_type not in grouped_entrylinks:
        grouped_entrylinks[link_type] = {}
    if group_key not in grouped_entrylinks[link_type]:
        grouped_entrylinks[link_type][group_key] = []

    grouped_entrylinks[link_type][group_key].append(entrylink_dict)


def group_entrylinks_by_type_and_entry_group(entrylinks: List[EntryLink]) -> Dict[str, Dict[str, List[Dict]]]:
    """Group entrylinks by link type and project/location."""
    grouped_entrylinks = {}

    for entrylink in entrylinks:
        normalized_link_type = _extract_normalized_link_type(entrylink.entryLinkType)
        if not normalized_link_type:
            logger.warning(f"Invalid entryLinkType format: {entrylink.entryLinkType}")
            continue

        project_id, location, entry_group = extract_entrylink_components(entrylink.name)
        _add_entrylink_to_group(
            grouped_entrylinks,
            entrylink.to_dict(),
            normalized_link_type,
            project_id,
            location,
            entry_group
        )

    logger.debug(f"input: {entrylinks}, output: {grouped_entrylinks}")
    return grouped_entrylinks


def _get_archive_directory() -> str:
    """Get the path to the archive directory."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ARCHIVE_DIRECTORY)


def _get_processed_directory() -> str:
    """Get the path to the processed directory."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), PROCESSED_DIRECTORY)


def _extract_unique_entry_projects(entrylinks: List[EntryLink]) -> set:
    """Extract all unique project IDs from entrylinks."""
    unique_projects = set()
    for entrylink in entrylinks:
        project_id, _, _ = extract_entrylink_components(entrylink.name)
        unique_projects.add(project_id)
    return unique_projects


def _validate_bucket_permissions_for_projects(
    unique_projects: set,
    buckets: List[str],
    user_project: str
) -> bool:
    """Validate GCS bucket permissions for all entry projects. Reports all failures at once."""
    logger.debug(f"Found {len(unique_projects)} unique entry project(s): {unique_projects}")
    all_failures = []  # List of (project_id, project_number, failed_buckets)

    for project_id in unique_projects:
        project_number = api_layer.get_project_number(project_id, user_project)
        logger.debug(f"Checking bucket permissions for project: {project_id} (number: {project_number})")
        failed_buckets = gcs_dao.check_all_buckets_permissions(buckets, project_number)
        if failed_buckets:
            all_failures.append((project_id, project_number, failed_buckets))

    if not all_failures:
        logger.info("All bucket permission checks passed.")
        return True

    _log_permission_failures(all_failures)
    return False


def _log_permission_failures(failures: List[tuple]) -> None:
    """Log all permission failures in a clear format."""
    logger.error(f"GCS bucket permission check failed for {len(failures)} project(s):")
    for project_id, project_number, failed_buckets in failures:
        bucket_list = ', '.join(failed_buckets)
        logger.error(f"  - Project '{project_id}' (service account: service-{project_number}@gcp-sa-dataplex.iam.gserviceaccount.com)")
        logger.error(f"    Failed buckets: {bucket_list}")
    logger.error("Please grant the Dataplex service accounts the required permissions: "
                 "[storage.buckets.get, storage.objects.get, storage.objects.list].")


def _handle_entry_validation_failures(failed_entries: List[str]) -> bool:
    """Handle failed entry lookups. Returns True if should abort."""
    if not failed_entries:
        return False
    logger.error(f"Cannot proceed: {len(failed_entries)} entry lookups failed due to network errors.")
    logger.error("Please check your network connection and try again.")
    return True


def _handle_import_exception(exception: Exception) -> int:
    """Handle exceptions during import and return exit code."""
    if isinstance(exception, KeyboardInterrupt):
        logger.info("Import cancelled by user")
        return 130

    if api_layer.is_transient_error(exception):
        logger.error(f"Import stopped: API calls kept failing with network or server errors after retrying: {exception}")
        logger.error("Please check your internet connection and try again.")
    else:
        logger.error(f"Unexpected error during import: {exception}")

    logger.debug("Error details:", exc_info=True)
    return 1


def _log_import_arguments(parsed_args) -> None:
    """Log the parsed import arguments."""
    logger.debug("Import Arguments:")
    logger.debug(f"  spreadsheet_url: {parsed_args.spreadsheet_url}")
    logger.debug(f"  buckets: {parsed_args.buckets}")
    logger.debug(f"  user_project: {parsed_args.user_project}")


def _run_import_workflow(parsed_args) -> int:
    """Execute the main import workflow."""
    _log_import_arguments(parsed_args)
    sheet_name = sheet_utils.get_sheet_name_for_url(parsed_args.spreadsheet_url)
    logger.info(f"Starting EntryLink import from sheet: '{sheet_name}'")

    api_layer.authenticate_dataplex()  # Fail early without Dataplex credentials.
    user_project = parsed_args.user_project
    logger.debug(f"Using user project for API quota: {user_project}")

    if not check_and_clean_archive_folder(_get_archive_directory()):
        return 1

    failed_rows: List[Tuple[int, str]] = []
    entrylinks = convert_spreadsheet_to_entrylinks(
        parsed_args.spreadsheet_url,
        sheet_name=sheet_name,
        user_project=user_project,
        failed_rows=failed_rows
    )
    if not entrylinks:
        if failed_rows:
            logger.error(f"None of the {len(failed_rows)} row(s) can be imported:")
            _log_items(_format_failed_rows(failed_rows), logger.error)
        else:
            logger.warning("Spreadsheet is empty or has no valid entries")
        return 1

    unique_projects = _extract_unique_entry_projects(entrylinks)
    if not _validate_bucket_permissions_for_projects(unique_projects, parsed_args.buckets, user_project):
        return 1

    missing_entries, failed_entries = check_entry_existence(entrylinks)
    if _handle_entry_validation_failures(failed_entries):
        return 1

    if not confirm_import(failed_rows, missing_entries):
        return 1

    return _execute_import(entrylinks, parsed_args.buckets)


def _execute_import(entrylinks: List[EntryLink], buckets: List[str]) -> int:
    """Group entrylinks, create import files, and run import jobs."""
    grouped_entrylinks = group_entrylinks_by_type_and_entry_group(entrylinks)

    archive_dir = _get_archive_directory()
    processed_dir = _get_processed_directory()
    file_utils.ensure_dir(archive_dir)
    file_utils.ensure_dir(processed_dir)

    import_files = import_utils.create_import_json_files(grouped_entrylinks, archive_dir)
    if not import_files:
        logger.warning("No files to process")
        return 1

    logger.info(f"Created {len(import_files)} import file(s). This will result in {len(import_files)} separate import job(s).")

    import_results = import_utils.run_import_files(import_files, buckets, processed_dir)
    return _report_import_results(import_results)


def _report_import_results(import_results: List[bool]) -> int:
    """Report import results and return exit code."""
    if import_results and all(import_results):
        logger.info("EntryLink Import Completed Successfully!")
        return 0
    logger.error("Some import jobs failed. Check logs for details.")
    return 1


def main():
    """Main entry point for EntryLink import."""
    try:
        logging_utils.setup_file_logging()
        parsed_args = argument_parser.get_import_entrylinks_arguments()
        return _run_import_workflow(parsed_args)
    except KeyboardInterrupt:
        # Use os._exit to bypass atexit handlers that would try to join threads
        logger.info("Import cancelled by user")
        os._exit(130)
    except Exception as import_exception:
        return _handle_import_exception(import_exception)


if __name__ == "__main__":
    sys.exit(main())