"""Sheet Utility Functions - Google Sheets API operations and data transformations."""

from typing import Any, Dict, List, Tuple
from google.auth import default
from googleapiclient.discovery import build

from utils import api_layer, business_glossary_utils, logging_utils
from utils.constants import (
    COLUMN_HEADER_ALIASES,
    ENTRYLINK_TYPE_PATTERN,
    ENTRY_REFERENCE_TYPE_SOURCE,
    ENTRY_REFERENCE_TYPE_TARGET,
    SOURCE_ID_HEADER_ALIASES,
    SOURCE_NAME_HEADER_ALIASES,
    SPREADSHEET_URL_PATTERN,
    TARGET_ID_HEADER_ALIASES,
    TARGET_NAME_HEADER_ALIASES,
    TYPE_HEADER_ALIASES,
)
from utils.error import InvalidSpreadsheetURLError, SheetsAPIError
from utils.retry_utils import execute_with_retry, is_network_error

logger = logging_utils.get_logger()


def authenticate_sheets() -> Any:
    """Authenticate with Google Sheets API with retry for transient errors."""
    def _do_auth():
        logger.debug("[SHEETS AUTH] Authenticating with Google Sheets API...")
        credentials, _ = default(scopes=['https://www.googleapis.com/auth/spreadsheets'])
        logger.debug("[SHEETS AUTH] Authenticated successfully.")
        return build('sheets', 'v4', credentials=credentials)

    try:
        return execute_with_retry(_do_auth, "Sheets authentication", is_retryable=is_network_error)
    except Exception as auth_error:
        logger.error(f"Sheets auth error: {auth_error}")
        raise SheetsAPIError(f"Sheets auth error: {auth_error}")


def get_spreadsheet_id(spreadsheet_url: str) -> str:
    """Extract spreadsheet ID from URL."""
    url_match = SPREADSHEET_URL_PATTERN.match(spreadsheet_url)
    if not url_match:
        raise InvalidSpreadsheetURLError(f"Invalid spreadsheet URL: {spreadsheet_url}")
    return url_match.group('spreadsheet_id')


def get_sheet_gid(spreadsheet_url: str) -> str:
    """Extract sheet gid from URL if present."""
    url_match = SPREADSHEET_URL_PATTERN.match(spreadsheet_url)
    if url_match:
        return url_match.group('gid')
    return None


def get_sheet_name_from_gid(sheets_service, spreadsheet_id: str, target_gid: str) -> str:
    """Get sheet name from gid by looking up spreadsheet metadata."""
    try:
        spreadsheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet_info in spreadsheet_metadata.get('sheets', []):
            sheet_properties = sheet_info.get('properties', {})
            if str(sheet_properties.get('sheetId')) == str(target_gid):
                return sheet_properties.get('title')
        logger.warning(f"Sheet with gid={target_gid} not found, using first sheet")
        return None
    except Exception as metadata_error:
        logger.warning(f"Error getting sheet name from gid: {metadata_error}")
        return None


def _get_first_sheet_name(sheets_service, spreadsheet_id: str) -> str:
    """Get the name of the first sheet in a spreadsheet."""
    try:
        spreadsheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        return spreadsheet_metadata['sheets'][0]['properties']['title']
    except Exception:
        return 'Sheet1'


def get_sheet_name_for_url(spreadsheet_url: str) -> str:
    """Get the sheet name for a spreadsheet URL."""
    sheets_service = authenticate_sheets()
    spreadsheet_id = get_spreadsheet_id(spreadsheet_url)

    sheet_gid = get_sheet_gid(spreadsheet_url)
    if sheet_gid:
        sheet_name = get_sheet_name_from_gid(sheets_service, spreadsheet_id, sheet_gid)
        if sheet_name:
            return sheet_name

    return _get_first_sheet_name(sheets_service, spreadsheet_id)


def read_from_spreadsheet_url(spreadsheet_url: str, column_range: str = 'A:Z', sheet_name: str = None) -> List[List[str]]:
    """Read data from a Google Sheet URL, handling sheet gid if specified.

    If sheet_name is provided, use it directly instead of looking up from gid.
    """
    sheets_service = authenticate_sheets()
    spreadsheet_id = get_spreadsheet_id(spreadsheet_url)

    target_sheet_name = sheet_name
    if not target_sheet_name:
        sheet_gid = get_sheet_gid(spreadsheet_url)
        if sheet_gid:
            target_sheet_name = get_sheet_name_from_gid(sheets_service, spreadsheet_id, sheet_gid)

    return read_from_sheet(sheets_service, spreadsheet_id, column_range, target_sheet_name)


def _build_sheet_range(sheet_name: str, column_range: str) -> str:
    """Build the full range string for sheet API calls."""
    return f"'{sheet_name}'!{column_range}" if sheet_name else column_range


def read_from_sheet(sheets_service, spreadsheet_id: str, column_range: str = 'A:Z', sheet_name: str = None) -> List[List[str]]:
    """Read data from a Google Sheet with retry."""
    full_range = _build_sheet_range(sheet_name, column_range)
    logger.debug(f"[READ SHEET] Request: spreadsheet_id={spreadsheet_id}, range={full_range}")

    def _do_read():
        read_result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=full_range
        ).execute()
        return read_result.get('values', [])

    try:
        sheet_rows = execute_with_retry(_do_read, f"Read sheet {spreadsheet_id}", is_retryable=is_network_error)
        logger.debug(f"[READ SHEET] Response: {len(sheet_rows)} rows retrieved")
        return sheet_rows
    except Exception as read_error:
        logger.error(f"Error reading spreadsheet: {read_error}")
        raise SheetsAPIError(f"Error reading spreadsheet: {read_error}")


def _get_sheet_info(sheets_service, spreadsheet_id: str, sheet_name: str = None) -> tuple:
    """Get sheet name and ID. Uses provided name or defaults to first sheet."""
    metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = metadata.get('sheets', [])

    if sheet_name:
        for sheet in sheets:
            props = sheet.get('properties', {})
            if props.get('title') == sheet_name:
                return props['title'], props['sheetId']
        logger.warning(f"Sheet '{sheet_name}' not found, using first sheet")

    first_props = sheets[0]['properties']
    return first_props['title'], first_props['sheetId']


def write_to_sheet(sheets_service, spreadsheet_id: str, row_data: List[List[str]], start_cell: str = 'A1', sheet_name: str = None) -> str:
    """Write data to Google Sheet with formatting. Returns sheet name."""
    logger.debug(f"[WRITE SHEET] Request: spreadsheet_id={spreadsheet_id}, rows={len(row_data)}, sheet_name={sheet_name}")

    def _do_write():
        target_sheet_name, sheet_id = _get_sheet_info(sheets_service, spreadsheet_id, sheet_name)

        sheets_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=f"'{target_sheet_name}'!A:ZZ"
        ).execute()
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{target_sheet_name}'!{start_cell}",
            valueInputOption='USER_ENTERED', body={'values': row_data}
        ).execute()

        _apply_sheet_formatting(sheets_service, spreadsheet_id, sheet_id, len(row_data))
        return target_sheet_name

    try:
        result = execute_with_retry(_do_write, f"Write sheet {spreadsheet_id}", is_retryable=is_network_error)
        logger.debug(f"[WRITE SHEET] Response: wrote {len(row_data)} rows")
        return result
    except Exception as write_error:
        logger.error(f"Error writing to spreadsheet: {write_error}")
        raise SheetsAPIError(f"Error writing to spreadsheet: {write_error}")


def _apply_sheet_formatting(sheets_service, spreadsheet_id: str, sheet_id: int, row_count: int) -> None:
    """Apply formatting to entrylinks sheet."""
    # [Entry link type (140px), Source Name (350px), Source ID (200px), Column (140px), Target Name (350px), Target ID (200px)]
    column_widths = [(0, 140), (1, 350), (2, 200), (3, 140), (4, 350), (5, 200)]
    requests = []

    for col_index, width in column_widths:
        requests.append({
            'updateDimensionProperties': {
                'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': col_index, 'endIndex': col_index + 1},
                'properties': {'pixelSize': width},
                'fields': 'pixelSize'
            }
        })

    requests.append({
        'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': row_count, 'startColumnIndex': 0, 'endColumnIndex': 6},
            'cell': {'userEnteredFormat': {'wrapStrategy': 'WRAP'}},
            'fields': 'userEnteredFormat.wrapStrategy'
        }
    })

    requests.append({
        'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 6},
            'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
            'fields': 'userEnteredFormat.textFormat.bold'
        }
    })

    requests.append({
        'autoResizeDimensions': {
            'dimensions': {'sheetId': sheet_id, 'dimension': 'ROWS', 'startIndex': 0, 'endIndex': row_count}
        }
    })

    sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': requests}).execute()


def _is_redacted_entry(entry_ref: Dict[str, Any]) -> bool:
    """
    Check if an entry reference is redacted (contains '*' in the name).

    Redacted entries occur when the user doesn't have permission to view
    the linked entry. These should be skipped during export.

    Args:
        entry_ref: Entry reference dictionary with 'name' field

    Returns:
        True if the entry is redacted, False otherwise
    """
    name = entry_ref.get('name', '')
    return '*' in name


def _extract_link_type(full_link_type: str) -> str:
    """Extract the link type name from the full link type path."""
    link_type_match = ENTRYLINK_TYPE_PATTERN.match(full_link_type)
    if not link_type_match:
        return None
    return link_type_match.group('link_type')


def _find_source_and_target_refs(entry_references: List[Dict]) -> tuple:
    """Find source and target entry references from the list."""
    source_ref = next(
        (ref for ref in entry_references if ref.get('type') == ENTRY_REFERENCE_TYPE_SOURCE),
        None
    )
    target_ref = next(
        (ref for ref in entry_references if ref.get('type') == ENTRY_REFERENCE_TYPE_TARGET),
        None
    )

    if source_ref and target_ref:
        return source_ref, target_ref

    # Fall back to using references in order for non-directional links
    first_ref = entry_references[0]
    second_ref = entry_references[1] if len(entry_references) > 1 else None
    return first_ref, second_ref


def entry_links_to_rows(
    entry_links: List[Dict[str, Any]],
    dataplex_service=None,
    user_project: str = ""
) -> List[List[str]]:
    """Convert EntryLinks to spreadsheet row format [Entry link type, Source Name, Source ID, Column, Target Name, Target ID]."""
    spreadsheet_rows = []
    redacted_link_count = 0

    for entry_link in entry_links:
        full_link_type = entry_link.get('entryLinkType', '')
        link_type_name = _extract_link_type(full_link_type)
        if not link_type_name:
            logger.warning(f"Invalid entryLinkType format: {full_link_type}")
            continue

        entry_references = entry_link.get('entryReferences', [])
        if not entry_references:
            continue

        if any(_is_redacted_entry(ref) for ref in entry_references):
            redacted_link_count += 1
            logger.debug(f"Skipping redacted entrylink: {entry_link.get('name', 'unknown')}")
            continue

        source_ref, target_ref = _find_source_and_target_refs(entry_references)

        if source_ref and target_ref:
            _add_entry_link_to_rows(
                spreadsheet_rows, link_type_name, source_ref, target_ref,
                dataplex_service=dataplex_service, user_project=user_project
            )

    if redacted_link_count > 0:
        logger.info(f"Skipped {redacted_link_count} redacted entrylink(s) during export")

    return spreadsheet_rows


def _add_entry_link_to_rows(
    rows: List[List[str]],
    link_type: str,
    source_ref: Dict[str, Any],
    target_ref: Dict[str, Any],
    dataplex_service=None,
    user_project: str = ""
) -> None:
    """Add a single entry link as a row [type, source_name, source_id, column, target_name, target_id]."""
    source_raw = source_ref.get('name', '')
    target_raw = target_ref.get('name', '')
    path_raw = source_ref.get('path', '')

    if dataplex_service:
        if link_type == "definition":
            try:
                source_name = api_layer.get_entry_fqn(dataplex_service, source_raw, user_project)
            except Exception as err:
                logger.warning(f"Failed to resolve FQN for '{source_raw}', falling back to raw entry name: {err}")
                source_name = source_raw
            source_id = business_glossary_utils.extract_short_id(source_raw)
            column_val = business_glossary_utils.extract_column_from_source_path(path_raw)
            try:
                target_name = api_layer.resolve_term_entry_to_display_identifier(
                    dataplex_service, target_raw, user_project=user_project
                )
            except Exception as err:
                logger.warning(f"Failed to resolve display identifier for '{target_raw}', falling back to raw entry name: {err}")
                target_name = target_raw
            target_id = business_glossary_utils.extract_short_id(target_raw)
        else:
            try:
                source_name = api_layer.resolve_term_entry_to_display_identifier(
                    dataplex_service, source_raw, user_project=user_project
                )
            except Exception as err:
                logger.warning(f"Failed to resolve display identifier for '{source_raw}', falling back to raw entry name: {err}")
                source_name = source_raw
            source_id = business_glossary_utils.extract_short_id(source_raw)
            column_val = ""
            try:
                target_name = api_layer.resolve_term_entry_to_display_identifier(
                    dataplex_service, target_raw, user_project=user_project
                )
            except Exception as err:
                logger.warning(f"Failed to resolve display identifier for '{target_raw}', falling back to raw entry name: {err}")
                target_name = target_raw
            target_id = business_glossary_utils.extract_short_id(target_raw)
    else:
        source_name = source_raw
        source_id = business_glossary_utils.extract_short_id(source_raw)
        column_val = business_glossary_utils.extract_column_from_source_path(path_raw)
        target_name = target_raw
        target_id = business_glossary_utils.extract_short_id(target_raw)

    entry_link_row = [
        link_type,
        source_name,
        source_id,
        column_val,
        target_name,
        target_id
    ]
    rows.append(entry_link_row)


def _find_header_index(headers: List[str], candidates: List[str]) -> int:
    """Find the index of the first matching candidate in headers, or -1."""
    for candidate in candidates:
        if candidate in headers:
            return headers.index(candidate)
    return -1


def extract_column_indices(spreadsheet_data: List[List[str]]) -> Tuple[int, int, int, int, int, int]:
    """Extract column indices from spreadsheet headers (supporting 6-column and legacy 4-column headers).

    Returns:
        (type_col, source_name_col, source_id_col, column_col, target_name_col, target_id_col)
    """
    if not spreadsheet_data or not spreadsheet_data[0]:
        raise ValueError("Spreadsheet header row is empty.")

    normalized_headers = [header.lower().strip() for header in spreadsheet_data[0]]

    type_col = _find_header_index(normalized_headers, TYPE_HEADER_ALIASES)
    if type_col < 0:
        logger.error(f"Required column 'Entry link type' not found in headers: {spreadsheet_data[0]}")
        raise ValueError("Required column 'Entry link type' (or 'entry_link_type') not found in spreadsheet.")

    source_id_col = _find_header_index(normalized_headers, SOURCE_ID_HEADER_ALIASES)
    source_name_col = _find_header_index(normalized_headers, SOURCE_NAME_HEADER_ALIASES)
    if source_name_col < 0 and source_id_col < 0:
        logger.error(f"Required column 'Source Name' or 'Source ID' not found in headers: {spreadsheet_data[0]}")
        raise ValueError("Required column 'Source Name' (or 'Source ID') not found in spreadsheet.")

    target_id_col = _find_header_index(normalized_headers, TARGET_ID_HEADER_ALIASES)
    target_name_col = _find_header_index(normalized_headers, TARGET_NAME_HEADER_ALIASES)
    if target_name_col < 0 and target_id_col < 0:
        logger.error(f"Required column 'Target Name' or 'Target ID' not found in headers: {spreadsheet_data[0]}")
        raise ValueError("Required column 'Target Name' (or 'Target ID') not found in spreadsheet.")

    column_col = _find_header_index(normalized_headers, COLUMN_HEADER_ALIASES)

    return type_col, source_name_col, source_id_col, column_col, target_name_col, target_id_col


def _is_row_valid(data_row: List[str], row_number: int, required_max_idx: int) -> bool:
    """Check if a data row has sufficient columns."""
    if len(data_row) <= required_max_idx:
        logger.warning(f"Row {row_number} has insufficient columns, skipping")
        return False
    return True


def _create_entry_link_dict(
    data_row: List[str],
    type_idx: int,
    source_name_idx: int,
    source_id_idx: int,
    column_idx: int,
    target_name_idx: int,
    target_id_idx: int,
    row_number: int = 0,
) -> Dict[str, str]:
    """Create an entry link dictionary from a data row."""
    source_name = data_row[source_name_idx].strip() if source_name_idx >= 0 and len(data_row) > source_name_idx else ''
    source_id = data_row[source_id_idx].strip() if source_id_idx >= 0 and len(data_row) > source_id_idx else ''
    target_name = data_row[target_name_idx].strip() if target_name_idx >= 0 and len(data_row) > target_name_idx else ''
    target_id = data_row[target_id_idx].strip() if target_id_idx >= 0 and len(data_row) > target_id_idx else ''
    column_val = data_row[column_idx].strip() if column_idx >= 0 and len(data_row) > column_idx else ''

    effective_source = source_id if source_id else source_name
    effective_target = target_id if target_id else target_name

    result = {
        'entry_link_type': data_row[type_idx].strip() if len(data_row) > type_idx else '',
        'source_name': source_name,
        'source_id': source_id,
        'target_name': target_name,
        'target_id': target_id,
        'column': column_val,
        'source': effective_source,
        'target': effective_target,
        'source_entry': effective_source,
        'target_entry': effective_target,
        'source_path': column_val,
    }
    if row_number > 0:
        result['row_number'] = str(row_number)
    return result


def rows_to_entry_link_dicts(
    spreadsheet_data: List[List[str]],
    type_idx: int,
    source_name_idx: int,
    source_id_idx: int,
    column_idx: int,
    target_name_idx: int,
    target_id_idx: int
) -> List[Dict[str, str]]:
    """Convert spreadsheet rows to entry link dictionaries."""
    entry_link_dicts = []
    required_indices = [type_idx]
    source_candidates = [idx for idx in (source_name_idx, source_id_idx) if idx >= 0]
    if source_candidates:
        required_indices.append(min(source_candidates))
    target_candidates = [idx for idx in (target_name_idx, target_id_idx) if idx >= 0]
    if target_candidates:
        required_indices.append(min(target_candidates))
    required_max_idx = max(required_indices) if required_indices else 0

    for row_number, data_row in enumerate(spreadsheet_data[1:], start=2):
        if not _is_row_valid(data_row, row_number, required_max_idx):
            continue

        entry_link_dict = _create_entry_link_dict(
            data_row, type_idx, source_name_idx, source_id_idx, column_idx,
            target_name_idx, target_id_idx, row_number=row_number
        )

        if not (entry_link_dict['source_name'] or entry_link_dict['source_id']) or not (entry_link_dict['target_name'] or entry_link_dict['target_id']):
            logger.warning(f"Row {row_number} missing source or target entry, skipping")
            continue

        entry_link_dicts.append(entry_link_dict)

    return entry_link_dicts
