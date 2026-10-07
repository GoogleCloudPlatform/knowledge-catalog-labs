"""Sheet Utility Functions - Google Sheets API operations and data transformations."""

import os
from typing import Any, Dict, List, Tuple
from google.auth import default
from googleapiclient.discovery import build

from utils import logging_utils
from utils.constants import (
    ASPECTS_SHEET_NAME,
    ASPECTS_SHEET_NAME_ENV_VAR,
    ASPECT_SHEET_HEADERS,
    ENTRYLINK_TYPE_PATTERN,
    ENTRY_REFERENCE_TYPE_SOURCE,
    ENTRY_REFERENCE_TYPE_TARGET,
    SPREADSHEET_URL_PATTERN,
)
from utils.error import InvalidSpreadsheetURLError, SheetsAPIError
from utils.retry_utils import execute_with_retry, is_network_error

logger = logging_utils.get_logger()


def authenticate_sheets() -> build:
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
    column_widths = [(0, 120), (1, 450), (2, 450), (3, 200)]
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
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': row_count, 'startColumnIndex': 0, 'endColumnIndex': 4},
            'cell': {'userEnteredFormat': {'wrapStrategy': 'WRAP'}},
            'fields': 'userEnteredFormat.wrapStrategy'
        }
    })
    
    requests.append({
        'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 4},
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


def entry_links_to_rows(entry_links: List[Dict[str, Any]]) -> List[List[str]]:
    """Convert EntryLinks to spreadsheet row format."""
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
            _add_entry_link_to_rows(spreadsheet_rows, link_type_name, source_ref, target_ref)
    
    if redacted_link_count > 0:
        logger.info(f"Skipped {redacted_link_count} redacted entrylink(s) during export")
            
    return spreadsheet_rows


def _add_entry_link_to_rows(
    rows: List[List[str]], 
    link_type: str, 
    source_ref: Dict[str, Any], 
    target_ref: Dict[str, Any]
) -> None:
    """Add a single entry link as a row to the spreadsheet data."""
    entry_link_row = [
        link_type,
        source_ref.get('name', ''),
        target_ref.get('name', ''),
        source_ref.get('path', '')
    ]
    rows.append(entry_link_row)


def extract_column_indices(spreadsheet_data: List[List[str]]) -> Tuple[int, int, int, int]:
    """Extract column indices from spreadsheet headers."""
    normalized_headers = [header.lower().strip() for header in spreadsheet_data[0]]
    try:
        type_column_idx = normalized_headers.index('entry_link_type')
        source_column_idx = normalized_headers.index('source_entry')
        target_column_idx = normalized_headers.index('target_entry')
        path_column_idx = normalized_headers.index('source_path') if 'source_path' in normalized_headers else -1
    except ValueError as column_error:
        logger.error(f"Required column not found in spreadsheet: {column_error}")
        raise ValueError(f"Spreadsheet must have required columns: {column_error}")
    return type_column_idx, source_column_idx, target_column_idx, path_column_idx


def _is_row_valid(data_row: List[str], row_number: int, required_max_idx: int) -> bool:
    """Check if a data row has sufficient columns."""
    if len(data_row) <= required_max_idx:
        logger.warning(f"Row {row_number} has insufficient columns, skipping")
        return False
    return True


def _create_entry_link_dict(
    data_row: List[str], 
    type_idx: int, 
    source_idx: int, 
    target_idx: int, 
    path_idx: int
) -> Dict[str, str]:
    """Create an entry link dictionary from a data row."""
    source_path = ''
    if path_idx >= 0 and len(data_row) > path_idx:
        source_path = data_row[path_idx].strip()
    
    return {
        'entry_link_type': data_row[type_idx].strip(),
        'source_entry': data_row[source_idx].strip(),
        'target_entry': data_row[target_idx].strip(),
        'source_path': source_path
    }


def rows_to_entry_link_dicts(
    spreadsheet_data: List[List[str]], 
    type_idx: int, 
    source_idx: int, 
    target_idx: int, 
    path_idx: int
) -> List[Dict[str, str]]:
    """Convert spreadsheet rows to entry link dictionaries."""
    entry_link_dicts = []
    required_max_idx = max(type_idx, source_idx, target_idx)
    
    for row_number, data_row in enumerate(spreadsheet_data[1:], start=2):
        if not _is_row_valid(data_row, row_number, required_max_idx):
            continue
        
        entry_link_dict = _create_entry_link_dict(data_row, type_idx, source_idx, target_idx, path_idx)
        
        if not entry_link_dict['source_entry'] or not entry_link_dict['target_entry']:
            logger.warning(f"Row {row_number} missing source or target entry, skipping")
            continue
        
        entry_link_dicts.append(entry_link_dict)
    
    return entry_link_dicts


# =============================================================================
# Aspect sheet (Sheet 2) helpers
# =============================================================================


def resolve_aspects_sheet_name(sheet_name: str = None) -> str:
    """Resolve the worksheet name holding the aspect rows.

    Precedence: explicit argument > GLOSSARY_ASPECTS_SHEET_NAME env var >
    the ASPECTS_SHEET_NAME default ('Sheet2').

    Args:
        sheet_name: Explicit worksheet name, if the caller has one.

    Returns:
        The worksheet name to use for aspect rows.
    """
    if sheet_name:
        return sheet_name
    return os.environ.get(ASPECTS_SHEET_NAME_ENV_VAR, '').strip() or ASPECTS_SHEET_NAME


def sheet_exists(sheets_service, spreadsheet_id: str, title: str) -> bool:
    """Check whether a worksheet with the given title exists.

    Args:
        sheets_service: The Google Sheets API service object.
        spreadsheet_id: The spreadsheet ID.
        title: The worksheet title to look for.

    Returns:
        True when the worksheet exists, False when it does not or when the
        metadata call fails.
    """
    try:
        metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    except Exception as metadata_error:
        logger.warning(f"Could not read spreadsheet metadata: {metadata_error}")
        return False

    for sheet in metadata.get('sheets', []):
        if sheet.get('properties', {}).get('title') == title:
            return True
    return False


def ensure_sheet_exists(sheets_service, spreadsheet_id: str, title: str) -> int:
    """Return the sheetId of a worksheet, creating the tab when it is absent.

    Args:
        sheets_service: The Google Sheets API service object.
        spreadsheet_id: The spreadsheet ID.
        title: The worksheet title.

    Returns:
        The numeric sheetId of the existing or newly created worksheet.

    Raises:
        SheetsAPIError: If the worksheet cannot be read or created.
    """
    def _do_ensure():
        metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in metadata.get('sheets', []):
            properties = sheet.get('properties', {})
            if properties.get('title') == title:
                return properties.get('sheetId')

        logger.info(f"Creating worksheet '{title}' in spreadsheet {spreadsheet_id}")
        response = sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': [{'addSheet': {'properties': {'title': title}}}]}
        ).execute()
        replies = response.get('replies', [])
        if replies and 'addSheet' in replies[0]:
            return replies[0]['addSheet']['properties']['sheetId']
        return None

    try:
        return execute_with_retry(
            _do_ensure, f"Ensure sheet '{title}' in {spreadsheet_id}", is_retryable=is_network_error
        )
    except Exception as ensure_error:
        logger.error(f"Error ensuring worksheet '{title}' exists: {ensure_error}")
        raise SheetsAPIError(f"Error ensuring worksheet '{title}' exists: {ensure_error}")


def rows_to_aspect_rows(spreadsheet_data: List[List[str]]) -> List['AspectRow']:
    """Convert raw Sheet 2 values into AspectRow objects.

    The header row is validated leniently: the three canonical headers are
    detected case-insensitively in any order. Blank rows are skipped.

    Args:
        spreadsheet_data: Raw values as returned by the Sheets API, header first.

    Returns:
        A list of AspectRow objects, empty when the sheet is empty or
        header-only.
    """
    from utils.models import AspectRow

    if not spreadsheet_data or not spreadsheet_data[0]:
        return []

    headers = [str(header).strip() for header in spreadsheet_data[0]]
    normalized = [header.lower() for header in headers]

    def _index_of(*candidates: str) -> int:
        for candidate in candidates:
            if candidate in normalized:
                return normalized.index(candidate)
        return -1

    id_idx = _index_of('id', 'term_id', 'term id')
    name_idx = _index_of('aspect name', 'aspect_name', 'aspectname')
    value_idx = _index_of('aspect value', 'aspect_value', 'aspectvalue')

    if id_idx < 0 or name_idx < 0 or value_idx < 0:
        logger.warning(
            f"Aspects sheet header does not match {ASPECT_SHEET_HEADERS}. "
            f"Actual headers: {headers}. Ignoring the aspects sheet."
        )
        return []

    aspect_rows = []
    for row_number, data_row in enumerate(spreadsheet_data[1:], start=2):
        def _cell(index: int) -> str:
            return str(data_row[index]).strip() if 0 <= index < len(data_row) else ''

        aspect_row = AspectRow(
            term_id=_cell(id_idx),
            aspect_name=_cell(name_idx),
            aspect_value=_cell(value_idx),
            row_number=row_number,
        )
        if aspect_row.is_empty():
            continue
        aspect_rows.append(aspect_row)

    return aspect_rows


def read_aspect_rows(spreadsheet_url: str, sheet_name: str = None) -> List['AspectRow']:
    """Read the aspects sheet (Sheet 2) from a spreadsheet URL.

    Missing, empty and header-only aspect sheets are all treated as "no
    aspects": an informational line is logged and an empty list is returned, so
    single-sheet spreadsheets keep working exactly as before.

    Args:
        spreadsheet_url: The Google Sheets URL.
        sheet_name: Optional worksheet name override. Defaults to
            GLOSSARY_ASPECTS_SHEET_NAME or 'Sheet2'.

    Returns:
        A list of AspectRow objects (possibly empty).
    """
    target_sheet_name = resolve_aspects_sheet_name(sheet_name)
    sheets_service = authenticate_sheets()
    spreadsheet_id = get_spreadsheet_id(spreadsheet_url)

    if not sheet_exists(sheets_service, spreadsheet_id, target_sheet_name):
        logger.info(
            f"No '{target_sheet_name}' worksheet found; continuing without custom aspects."
        )
        return []

    try:
        raw_values = read_from_sheet(sheets_service, spreadsheet_id, 'A:C', target_sheet_name)
    except SheetsAPIError as read_error:
        logger.warning(
            f"Could not read '{target_sheet_name}': {read_error}. Continuing without custom aspects."
        )
        return []

    aspect_rows = rows_to_aspect_rows(raw_values)
    logger.info(f"Read {len(aspect_rows)} aspect row(s) from '{target_sheet_name}'.")
    return aspect_rows


def write_aspect_rows(
    sheets_service,
    spreadsheet_id: str,
    row_data: List[List[str]],
    sheet_name: str = None,
) -> str:
    """Write aspect rows to the aspects sheet, creating the tab when needed.

    Args:
        sheets_service: The Google Sheets API service object.
        spreadsheet_id: The spreadsheet ID.
        row_data: Rows to write, header row included.
        sheet_name: Optional worksheet name override.

    Returns:
        The worksheet name that was written to.

    Raises:
        SheetsAPIError: If the write fails.
    """
    target_sheet_name = resolve_aspects_sheet_name(sheet_name)
    logger.debug(
        f"[WRITE ASPECT SHEET] Request: spreadsheet_id={spreadsheet_id}, "
        f"rows={len(row_data)}, sheet_name={target_sheet_name}"
    )

    sheet_id = ensure_sheet_exists(sheets_service, spreadsheet_id, target_sheet_name)

    def _do_write():
        sheets_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=f"'{target_sheet_name}'!A:ZZ"
        ).execute()
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{target_sheet_name}'!A1",
            valueInputOption='USER_ENTERED', body={'values': row_data}
        ).execute()
        if sheet_id is not None:
            _apply_aspect_sheet_formatting(sheets_service, spreadsheet_id, sheet_id, len(row_data))
        return target_sheet_name

    try:
        result = execute_with_retry(
            _do_write, f"Write aspect sheet {spreadsheet_id}", is_retryable=is_network_error
        )
        logger.debug(f"[WRITE ASPECT SHEET] Response: wrote {len(row_data)} rows")
        return result
    except Exception as write_error:
        logger.error(f"Error writing aspects to spreadsheet: {write_error}")
        raise SheetsAPIError(f"Error writing aspects to spreadsheet: {write_error}")


def _apply_aspect_sheet_formatting(
    sheets_service, spreadsheet_id: str, sheet_id: int, row_count: int
) -> None:
    """Apply formatting to the aspects sheet.

    Mirrors _apply_sheet_formatting: fixed column widths, wrapped text, a bold
    header row and auto-resized rows -- sized for [id, Aspect name, Aspect value].
    """
    column_widths = [(0, 220), (1, 300), (2, 450)]
    column_count = len(ASPECT_SHEET_HEADERS)
    requests = []

    for col_index, width in column_widths:
        requests.append({
            'updateDimensionProperties': {
                'range': {
                    'sheetId': sheet_id, 'dimension': 'COLUMNS',
                    'startIndex': col_index, 'endIndex': col_index + 1
                },
                'properties': {'pixelSize': width},
                'fields': 'pixelSize'
            }
        })

    requests.append({
        'repeatCell': {
            'range': {
                'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': row_count,
                'startColumnIndex': 0, 'endColumnIndex': column_count
            },
            'cell': {'userEnteredFormat': {'wrapStrategy': 'WRAP'}},
            'fields': 'userEnteredFormat.wrapStrategy'
        }
    })

    requests.append({
        'repeatCell': {
            'range': {
                'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1,
                'startColumnIndex': 0, 'endColumnIndex': column_count
            },
            'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
            'fields': 'userEnteredFormat.textFormat.bold'
        }
    })

    requests.append({
        'autoResizeDimensions': {
            'dimensions': {
                'sheetId': sheet_id, 'dimension': 'ROWS', 'startIndex': 0, 'endIndex': row_count
            }
        }
    })

    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={'requests': requests}
    ).execute()

