#!/usr/bin/env python3
"""EntryLink Export Utility - Exports EntryLinks from Dataplex glossary terms to Google Sheets."""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils import api_layer, argument_parser, business_glossary_utils, logging_utils, sheet_utils, error
from utils.constants import ENTRYLINK_SHEET_HEADERS, MAX_WORKERS, SYMMETRIC_LINK_TYPES
from utils.retry_utils import is_network_error

logger = logging_utils.get_logger()

SHEET_HEADERS = ENTRYLINK_SHEET_HEADERS


def _build_deduplication_key(entry_link_row: list) -> tuple:
    """Build a unique key for detecting duplicate rows.

    Row format: [link_type, source_name, source_id, column, target_name, target_id].
    Synonym and related links have no direction, so their source and target are compared unordered.
    """
    link_type, source_name, source_id, column, target_name, target_id = entry_link_row
    source_key = (source_name, source_id)
    target_key = (target_name, target_id)
    if link_type in SYMMETRIC_LINK_TYPES:
        return (link_type, tuple(sorted([source_key, target_key])), column)
    return (link_type, source_key, target_key, column)


def deduplicate_entry_links(entry_links: list) -> list:
    """Remove duplicate entry links from the list."""
    processed_link_keys = set()
    unique_entry_links = []

    for entry_link_row in entry_links:
        dedup_key = _build_deduplication_key(entry_link_row)
        if dedup_key not in processed_link_keys:
            processed_link_keys.add(dedup_key)
            unique_entry_links.append(entry_link_row)

    return unique_entry_links


def fetch_entry_links_for_region(term_entry_name: str, region: str, billing_project: str) -> list:
    """Fetch entry links from a single region."""
    try:
        region_links = api_layer.lookup_entry_links_for_term(term_entry_name, billing_project, location=region)
        return region_links or []
    except Exception as region_error:
        logger.warning(f"Region {region} failed for '{term_entry_name}': {region_error}")
        return []


def _resolve_regions_for_glossary(glossary_resource_name: str, billing_project: str) -> list:
    """Resolve which regions to query for a glossary's entry links."""
    glossary_location = business_glossary_utils.extract_location_from_name(glossary_resource_name)
    try:
        return api_layer.resolve_regions_to_query(glossary_location, billing_project)
    except Exception as resolve_error:
        logger.error(f"Failed to resolve regions for glossary '{glossary_resource_name}': {resolve_error}")
        return []


def _fetch_links_from_regions_parallel(term_entry_name: str, regions: list, billing_project: str) -> list:
    """Fetch entry links from multiple regions in parallel."""
    collected_links = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        region_futures = {
            executor.submit(fetch_entry_links_for_region, term_entry_name, region, billing_project): region
            for region in regions
        }
        for completed_future in as_completed(region_futures):
            collected_links.extend(completed_future.result())
    return collected_links


def fetch_entry_links_for_term(glossary_term: dict, regions_to_query: list, billing_project: str) -> list:
    """Fetch all entry links for a term across relevant regions."""
    if not regions_to_query:
        return []

    term_name = glossary_term["name"]
    try:
        project_id = business_glossary_utils.extract_project_id_from_name(term_name)
        project_number = api_layer.get_project_number(project_id, billing_project)
    except Exception as e:
        logger.debug(f"Could not resolve project number for '{term_name}': {e}")
        project_number = ""

    term_entry_name = business_glossary_utils.generate_entry_name_from_term_name(
        term_name, project_number=project_number
    )
    return _fetch_links_from_regions_parallel(term_entry_name, regions_to_query, billing_project)


def fetch_all_entry_links(glossary_terms: list, regions_to_query: list, billing_project: str) -> list:
    """Fetch entry links for all terms in parallel."""
    all_entry_links = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        term_futures = {
            executor.submit(fetch_entry_links_for_term, term, regions_to_query, billing_project): term
            for term in glossary_terms
        }
        for completed_future in as_completed(term_futures):
            all_entry_links.extend(completed_future.result())
    return all_entry_links


def deduplicate_raw_entry_links(entry_links: list) -> list:
    """Remove duplicate entry links (a link is returned once for every term it references)."""
    processed_link_keys = set()
    unique_entry_links = []

    for entry_link in entry_links:
        # Each reference carries its type, so sorting only makes the order of synonym and
        # related references irrelevant; the direction of definition links is kept.
        dedup_key = (
            entry_link.get('entryLinkType', '').rsplit('/', 1)[-1],
            tuple(sorted(
                (ref.get('name', ''), ref.get('path', ''), ref.get('type', ''))
                for ref in entry_link.get('entryReferences', [])
            )),
        )
        if dedup_key not in processed_link_keys:
            processed_link_keys.add(dedup_key)
            unique_entry_links.append(entry_link)

    return unique_entry_links


def convert_entry_links_to_rows(entry_links: list, billing_project: str) -> list:
    """Convert entry links to sheet rows, skipping duplicate and redacted links.

    Resolving display names and FQNs takes API calls, so links are converted in parallel,
    each worker thread using its own Dataplex client.
    """
    unique_entry_links = deduplicate_raw_entry_links(entry_links)
    logger.debug(f"Deduplicated {len(entry_links)} -> {len(unique_entry_links)} fetched entry links")

    visible_entry_links = []
    for entry_link in unique_entry_links:
        if sheet_utils.is_redacted_entry_link(entry_link):
            logger.debug(f"Skipping redacted entrylink: {entry_link.get('name', 'unknown')}")
        else:
            visible_entry_links.append(entry_link)
    redacted_link_count = len(unique_entry_links) - len(visible_entry_links)
    if redacted_link_count > 0:
        logger.info(f"Skipped {redacted_link_count} redacted entrylink(s) during export")

    def to_row(entry_link: dict):
        return sheet_utils.entry_link_to_row(entry_link, api_layer.get_dataplex_service(), billing_project)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        rows = list(executor.map(to_row, visible_entry_links))
    return [row for row in rows if row is not None]


def _write_entry_links_to_sheet(entry_links: list, spreadsheet_url: str, sheets_service) -> str:
    """Write entry links to spreadsheet and return sheet name."""
    spreadsheet_id = sheet_utils.get_spreadsheet_id(spreadsheet_url)
    target_sheet_name = sheet_utils.get_sheet_name_for_url(spreadsheet_url)
    export_data = [SHEET_HEADERS] + entry_links
    return sheet_utils.write_to_sheet(sheets_service, spreadsheet_id, export_data, sheet_name=target_sheet_name)


def _clear_sheet_with_headers(spreadsheet_url: str, sheets_service) -> str:
    """Clear sheet and write only headers. Returns sheet name."""
    spreadsheet_id = sheet_utils.get_spreadsheet_id(spreadsheet_url)
    target_sheet_name = sheet_utils.get_sheet_name_for_url(spreadsheet_url)
    return sheet_utils.write_to_sheet(sheets_service, spreadsheet_id, [SHEET_HEADERS], sheet_name=target_sheet_name)


def _cache_listed_terms(glossary_resource_name: str, glossary_terms: list, billing_project: str) -> None:
    """Cache the listed terms, so resolving their display names for the rows needs no API calls."""
    try:
        project_id = business_glossary_utils.extract_project_id_from_name(glossary_resource_name)
    except ValueError as e:
        logger.debug(f"Not caching the terms of '{glossary_resource_name}': {e}")
        return
    try:
        project_number = api_layer.get_project_number(project_id, billing_project)
    except Exception as e:
        logger.debug(f"Could not resolve project number for '{glossary_resource_name}': {e}")
        project_number = ""
    api_layer.cache_glossary_terms(glossary_terms, project_id, project_number)


def export_entry_links(glossary_resource_name: str, spreadsheet_url: str, billing_project: str) -> bool:
    """Export all EntryLinks from a glossary to Google Sheets."""
    dataplex_service = api_layer.authenticate_dataplex()
    sheets_service = sheet_utils.authenticate_sheets()
    api_layer.initialize_locations_cache(billing_project)

    glossary_terms = api_layer.list_glossary_terms(dataplex_service, glossary_resource_name)
    if not glossary_terms:
        logger.warning("No terms found in the glossary")
        _clear_sheet_with_headers(spreadsheet_url, sheets_service)
        return False
    _cache_listed_terms(glossary_resource_name, glossary_terms, billing_project)

    regions_to_query = _resolve_regions_for_glossary(glossary_resource_name, billing_project)
    if not regions_to_query:
        logger.error("Unable to find regions for glossary")
        _clear_sheet_with_headers(spreadsheet_url, sheets_service)
        return False

    all_entry_links = fetch_all_entry_links(glossary_terms, regions_to_query, billing_project)
    entry_link_rows = convert_entry_links_to_rows(all_entry_links, billing_project)
    if not entry_link_rows:
        logger.info("No entry links found")
        _clear_sheet_with_headers(spreadsheet_url, sheets_service)
        return False

    unique_entry_links = deduplicate_entry_links(entry_link_rows)
    logger.debug(f"Deduplicated {len(entry_link_rows)} -> {len(unique_entry_links)} rows")

    sheet_name = _write_entry_links_to_sheet(unique_entry_links, spreadsheet_url, sheets_service)
    logger.info(f"Data exported to sheet: '{sheet_name}' ({len(unique_entry_links)} entry links)")
    return True


def _handle_export_exception(exception: Exception) -> int:
    """Handle exceptions during export and return exit code."""
    if isinstance(exception, KeyboardInterrupt):
        logger.info("Export cancelled by user")
        return 1
    if isinstance(exception, (error.DataplexAPIError, error.SheetsAPIError)):
        logger.error(f"Export failed: {exception}")
        return 1
    if isinstance(exception, (error.InvalidSpreadsheetURLError, error.InvalidGlossaryNameError)):
        logger.error(f"Invalid input: {exception}")
        return 1
    if is_network_error(exception):
        logger.error("Network error. Check your connection and try again.")
        return 1

    logger.error(f"Export failed: {type(exception).__name__}: {exception}")
    logger.debug("Full exception:", exc_info=True)
    return 1


def _log_export_arguments(parsed_args) -> None:
    """Log the parsed export arguments."""
    logger.debug("Export Arguments:")
    logger.debug(f"glossary_url: {parsed_args.glossary_url}")
    logger.debug(f"spreadsheet_url: {parsed_args.spreadsheet_url}")
    logger.debug(f"user_project: {parsed_args.user_project}")


def _run_export() -> int:
    """Execute the export workflow and return exit code."""
    logging_utils.setup_file_logging()
    parsed_args = argument_parser.get_export_entrylinks_arguments()
    _log_export_arguments(parsed_args)

    glossary_resource_name = business_glossary_utils.extract_glossary_name(parsed_args.glossary_url)
    logger.info(f"Starting EntryLink Export for: {glossary_resource_name}")

    export_successful = export_entry_links(glossary_resource_name, parsed_args.spreadsheet_url, parsed_args.user_project)
    logger.info("Export completed successfully" if export_successful else "No EntryLinks found to export")
    return 0


def main() -> int:
    """Main entry point."""
    try:
        return _run_export()
    except KeyboardInterrupt:
        logger.info("Export cancelled by user")
        os._exit(130)
    except Exception as export_exception:
        return _handle_export_exception(export_exception)


if __name__ == "__main__":
    sys.exit(main())