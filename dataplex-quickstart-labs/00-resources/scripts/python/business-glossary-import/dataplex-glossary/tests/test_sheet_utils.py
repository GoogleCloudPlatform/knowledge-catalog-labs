"""
Unit tests for sheet_utils.py

Test coverage:
- Sheet name helpers (_get_first_sheet_name, _get_first_sheet_info, _build_sheet_range)
- Link type extraction (_extract_link_type)
- Reference finding (_find_source_and_target_refs)
- Row validation (_is_row_valid)
- Entry link creation (_create_entry_link_dict)
- Spreadsheet operations (get_spreadsheet_id, authenticate_sheets, read_from_sheet, write_to_sheet)
- Data conversion (entry_links_to_rows, rows_to_entry_link_dicts)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call
import pytest

# Import the module
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from utils import sheet_utils
from utils.error import InvalidSpreadsheetURLError, SheetsAPIError


# ============================================================================
# SHEET NAME HELPERS TESTS
# ============================================================================

class TestGetFirstSheetName:
    """Test _get_first_sheet_name function"""
    
    def test_extracts_first_sheet_name(self):
        """Extract first sheet name from spreadsheet metadata"""
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {
            'sheets': [
                {'properties': {'title': 'Sheet1'}},
                {'properties': {'title': 'Sheet2'}}
            ]
        }
        
        result = sheet_utils._get_first_sheet_name(mock_service, 'spreadsheet_id')
        
        assert result == 'Sheet1'
    
    def test_returns_default_on_error(self):
        """Return 'Sheet1' when error occurs"""
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.side_effect = Exception("Error")
        
        result = sheet_utils._get_first_sheet_name(mock_service, 'spreadsheet_id')
        
        assert result == 'Sheet1'


class TestGetFirstSheetInfo:
    """Test _get_first_sheet_name function"""
    
    def test_extracts_sheet_info(self):
        """Extract sheet name from metadata"""
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {
            'sheets': [
                {'properties': {'title': 'Sheet1', 'sheetId': 123}}
            ]
        }
        
        name = sheet_utils._get_first_sheet_name(mock_service, 'spreadsheet_id')
        
        assert name == 'Sheet1'


# ============================================================================
# RANGE BUILDING TESTS
# ============================================================================

class TestBuildSheetRange:
    """Test _build_sheet_range function"""
    
    def test_builds_range_with_sheet_name(self):
        """Build range with sheet name"""
        result = sheet_utils._build_sheet_range('Sheet1', 'A:D')
        
        assert result == "'Sheet1'!A:D"
    
    def test_handles_none_sheet_name(self):
        """Handle None sheet name"""
        result = sheet_utils._build_sheet_range(None, 'A:D')
        
        assert result == 'A:D'
    
    def test_handles_sheet_name_with_spaces(self):
        """Sheet names with spaces should be quoted"""
        result = sheet_utils._build_sheet_range('My Sheet', 'A:Z')
        
        assert "'My Sheet'" in result


# ============================================================================
# LINK TYPE EXTRACTION TESTS
# ============================================================================

class TestExtractLinkType:
    """Test _extract_link_type function"""
    
    def test_extracts_definition_type(self):
        """Extract 'definition' link type from full path"""
        full_type = 'projects/dataplex-types/locations/global/entryLinkTypes/definition'
        
        result = sheet_utils._extract_link_type(full_type)
        
        assert result == 'definition'
    
    def test_extracts_related_type(self):
        """Extract 'related' link type"""
        full_type = 'projects/dataplex-types/locations/global/entryLinkTypes/related'
        
        result = sheet_utils._extract_link_type(full_type)
        
        assert result == 'related'
    
    def test_extracts_synonym_type(self):
        """Extract 'synonym' link type"""
        full_type = 'projects/dataplex-types/locations/global/entryLinkTypes/synonym'
        
        result = sheet_utils._extract_link_type(full_type)
        
        assert result == 'synonym'
    
    def test_returns_none_for_invalid(self):
        """Return None for invalid format"""
        result = sheet_utils._extract_link_type('invalid')
        
        assert result is None


# ============================================================================
# REFERENCE FINDING TESTS
# ============================================================================

class TestFindSourceAndTargetRefs:
    """Test _find_source_and_target_refs function"""
    
    def test_finds_source_and_target_by_type(self):
        """Find source and target by type field"""
        entry_refs = [
            {'type': 'SOURCE', 'name': 'source_entry'},
            {'type': 'TARGET', 'name': 'target_entry'}
        ]
        
        source, target = sheet_utils._find_source_and_target_refs(entry_refs)
        
        assert source['name'] == 'source_entry'
        assert target['name'] == 'target_entry'
    
    def test_fallback_to_order_for_non_directional(self):
        """Fall back to order when no type field"""
        entry_refs = [
            {'name': 'entry1'},
            {'name': 'entry2'}
        ]
        
        source, target = sheet_utils._find_source_and_target_refs(entry_refs)
        
        assert source['name'] == 'entry1'
        assert target['name'] == 'entry2'


# ============================================================================
# ROW VALIDATION TESTS
# ============================================================================

class TestIsRowValid:
    """Test _is_row_valid function"""
    
    def test_valid_row_passes(self):
        """Valid row should return True"""
        row = ['definition', 'source', 'target', '/path']
        required_max_idx = 2  # need at least 3 columns
        
        result = sheet_utils._is_row_valid(row, 1, required_max_idx)
        
        assert result is True
    
    def test_insufficient_columns_fails(self):
        """Row with insufficient columns should return False"""
        row = ['definition', 'source']  # Only 2 columns
        required_max_idx = 2  # need at least 3 columns
        
        result = sheet_utils._is_row_valid(row, 1, required_max_idx)
        
        assert result is False


# ============================================================================
# ENTRY LINK DICT CREATION TESTS
# ============================================================================

class TestCreateEntryLinkDict:
    """Test _create_entry_link_dict function"""
    
    def test_creates_dict_from_row(self):
        """Create entry link dict from row"""
        row = ['definition', 'source_name', 'src_id', '/path', 'target_name', 'tgt_id']
        
        result = sheet_utils._create_entry_link_dict(row, 0, 1, 2, 3, 4, 5)
        
        assert result['entry_link_type'] == 'definition'
        assert result['source_name'] == 'source_name'
        assert result['source_id'] == 'src_id'
        assert result['target_name'] == 'target_name'
        assert result['target_id'] == 'tgt_id'
        assert result['source_entry'] == 'src_id'
        assert result['target_entry'] == 'tgt_id'
        assert result['source_path'] == '/path'
    
    def test_handles_missing_path(self):
        """Handle row without path column"""
        row = ['related', 'source_name', '', '', 'target_name', '']
        
        result = sheet_utils._create_entry_link_dict(row, 0, 1, -1, -1, 4, -1)
        
        assert result['source_path'] == ''
        assert result['source_name'] == 'source_name'
        assert result['target_name'] == 'target_name'
    
    def test_strips_whitespace(self):
        """Whitespace should be stripped"""
        row = ['  definition  ', '  source  ', '  src_id  ', '  /path  ', '  target  ', '  tgt_id  ']
        
        result = sheet_utils._create_entry_link_dict(row, 0, 1, 2, 3, 4, 5)
        
        assert result['entry_link_type'] == 'definition'
        assert result['source_name'] == 'source'
        assert result['source_id'] == 'src_id'


# ============================================================================
# SPREADSHEET OPERATIONS TESTS
# ============================================================================

class TestGetSpreadsheetId:
    """Test get_spreadsheet_id function"""
    
    def test_extracts_from_standard_url(self):
        """Extract ID from standard Google Sheets URL"""
        url = 'https://docs.google.com/spreadsheets/d/abc123def456/edit'
        
        result = sheet_utils.get_spreadsheet_id(url)
        
        assert result == 'abc123def456'
    
    def test_extracts_from_url_with_gid(self):
        """Extract ID from URL with gid parameter"""
        url = 'https://docs.google.com/spreadsheets/d/xyz789/edit#gid=0'
        
        result = sheet_utils.get_spreadsheet_id(url)
        
        assert result == 'xyz789'
    
    def test_raises_on_invalid_url(self):
        """Invalid URL should raise InvalidSpreadsheetURLError"""
        with pytest.raises(InvalidSpreadsheetURLError):
            sheet_utils.get_spreadsheet_id('not-a-valid-url')


class TestAuthenticateSheets:
    """Test authenticate_sheets function"""
    
    def test_returns_client(self, monkeypatch):
        """Should return Sheets API client"""
        mock_credentials = MagicMock()
        mock_service = MagicMock()
        
        monkeypatch.setattr(sheet_utils, 'default', lambda scopes: (mock_credentials, 'project'))
        monkeypatch.setattr(sheet_utils, 'build', lambda *args, **kwargs: mock_service)
        
        result = sheet_utils.authenticate_sheets()
        
        assert result is mock_service
    
    def test_raises_on_auth_failure(self, monkeypatch):
        """Should raise SheetsAPIError on authentication failure"""
        def raise_error(scopes):
            raise Exception("Auth failed")
        
        monkeypatch.setattr(sheet_utils, 'default', raise_error)
        
        with pytest.raises(SheetsAPIError):
            sheet_utils.authenticate_sheets()


class TestReadFromSheet:
    """Test read_from_sheet function"""
    
    def test_reads_data(self):
        """Should read data from sheet"""
        mock_values = [
            ['link_type', 'source', 'target', 'source_path'],
            ['definition', 'src1', 'tgt1', '/path1']
        ]
        
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {
            'values': mock_values
        }
        
        result = sheet_utils.read_from_sheet(mock_service, 'spreadsheet_id', 'A:D')
        
        assert len(result) == 2
        assert result[0][0] == 'link_type'
    
    def test_returns_empty_for_empty_sheet(self):
        """Should return empty for empty sheet"""
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {}
        
        result = sheet_utils.read_from_sheet(mock_service, 'spreadsheet_id', 'A:D')
        
        assert result == []
    
    def test_raises_on_api_error(self):
        """Should raise SheetsAPIError on API error"""
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.side_effect = Exception("API Error")
        
        with pytest.raises(SheetsAPIError):
            sheet_utils.read_from_sheet(mock_service, 'id', 'A:D')


class TestWriteToSheet:
    """Test write_to_sheet function"""
    
    def test_writes_data(self):
        """Should write data to sheet"""
        data = [
            ['link_type', 'source', 'target', 'source_path'],
            ['definition', 'src1', 'tgt1', '/path1']
        ]
        
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {
            'sheets': [{'properties': {'title': 'Sheet1', 'sheetId': 0}}]
        }
        
        result = sheet_utils.write_to_sheet(mock_service, 'spreadsheet_id', data)
        
        assert result == 'Sheet1'
        mock_service.spreadsheets().values().clear.assert_called()
        mock_service.spreadsheets().values().update.assert_called()


# ============================================================================
# DATA CONVERSION TESTS
# ============================================================================

class TestEntryLinksToRows:
    """Test entry_links_to_rows function"""
    
    def test_converts_links_to_rows(self):
        """Convert entry links to row format [type, source_name, source_id, column, target_name, target_id]"""
        entry_links = [
            {
                'entryLinkType': 'projects/dataplex-types/locations/global/entryLinkTypes/definition',
                'entryReferences': [
                    {'type': 'SOURCE', 'name': 'source_entry', 'path': 'Schema.order_id'},
                    {'type': 'TARGET', 'name': 'target_entry'}
                ]
            }
        ]
        
        result = sheet_utils.entry_links_to_rows(entry_links)
        
        assert len(result) == 1
        assert result[0] == ['definition', 'source_entry', 'source_entry', 'order_id', 'target_entry', 'target_entry']

    def test_converts_links_with_dataplex_service(self, monkeypatch):
        """Convert entry links with FQN and display name resolution"""
        from utils import api_layer
        mock_service = Mock()
        monkeypatch.setattr(api_layer, 'get_entry_fqn', lambda s, entry, p: 'bigquery:proj.ds.tbl')
        monkeypatch.setattr(
            api_layer, 'resolve_term_entry_to_display_identifier',
            lambda s, entry, **kwargs: 'proj.global.Sales.Order ID'
        )

        entry_links = [
            {
                'entryLinkType': 'projects/dataplex-types/locations/global/entryLinkTypes/definition',
                'entryReferences': [
                    {'type': 'SOURCE', 'name': 'projects/p/locations/us/entryGroups/@bigquery/entries/e1', 'path': 'Schema.user_id'},
                    {'type': 'TARGET', 'name': 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t1'}
                ]
            }
        ]

        result = sheet_utils.entry_links_to_rows(entry_links, dataplex_service=mock_service, user_project='user-proj')

        assert len(result) == 1
        assert result[0] == ['definition', 'bigquery:proj.ds.tbl', 'e1', 'user_id', 'proj.global.Sales.Order ID', 't1']
    
    def test_skips_invalid_link_type(self):
        """Skip entry links with invalid link type"""
        entry_links = [
            {
                'entryLinkType': 'invalid',
                'entryReferences': [
                    {'type': 'SOURCE', 'name': 'source'},
                    {'type': 'TARGET', 'name': 'target'}
                ]
            }
        ]
        
        result = sheet_utils.entry_links_to_rows(entry_links)
        
        assert len(result) == 0
    
    def test_skips_redacted_entries(self):
        """Skip entry links with redacted entries"""
        entry_links = [
            {
                'entryLinkType': 'projects/dataplex-types/locations/global/entryLinkTypes/definition',
                'entryReferences': [
                    {'type': 'SOURCE', 'name': '***redacted***'},
                    {'type': 'TARGET', 'name': 'target_entry'}
                ]
            }
        ]
        
        result = sheet_utils.entry_links_to_rows(entry_links)
        
        assert len(result) == 0
    
    def test_handles_empty_input(self):
        """Empty input should return empty list"""
        result = sheet_utils.entry_links_to_rows([])
        
        assert result == []


class TestRowsToEntryLinkDicts:
    """Test rows_to_entry_link_dicts function"""
    
    def test_converts_rows_to_dicts(self):
        """Convert rows to entry link dicts"""
        rows = [
            ['Entry link type', 'Source Name', 'Source ID', 'Column', 'Target Name', 'Target ID'],  # Header
            ['definition', 'bigquery:proj.ds.tbl', 'src1', 'order_id', 'proj.global.Sales.Order ID', 'tgt1']
        ]
        
        result = sheet_utils.rows_to_entry_link_dicts(rows, 0, 1, 2, 3, 4, 5)
        
        assert len(result) == 1
        assert result[0]['entry_link_type'] == 'definition'
        assert result[0]['source_name'] == 'bigquery:proj.ds.tbl'
        assert result[0]['source_id'] == 'src1'
        assert result[0]['source'] == 'src1'
        assert result[0]['target_name'] == 'proj.global.Sales.Order ID'
        assert result[0]['target_id'] == 'tgt1'
        assert result[0]['target'] == 'tgt1'
        assert result[0]['column'] == 'order_id'
    
    def test_skips_rows_missing_source_or_target(self):
        """Should skip rows missing source or target"""
        rows = [
            ['Entry link type', 'Source Name', 'Source ID', 'Column', 'Target Name', 'Target ID'],
            ['definition', 'src1', '', 'order_id', 'tgt1', ''],
            ['definition', '', '', 'order_id', 'tgt2', ''],  # Missing source
            ['definition', 'src3', '', 'order_id', '', '']   # Missing target
        ]
        
        result = sheet_utils.rows_to_entry_link_dicts(rows, 0, 1, 2, 3, 4, 5)
        
        assert len(result) == 1
    
    def test_handles_empty_input(self):
        """Empty input should return empty list"""
        rows = [['header1', 'header2', 'header3', 'header4', 'header5', 'header6']]  # Only header
        
        result = sheet_utils.rows_to_entry_link_dicts(rows, 0, 1, -1, -1, 4, -1)
        
        assert result == []


# ============================================================================
# EXTRACT COLUMN INDICES TESTS
# ============================================================================

class TestExtractColumnIndices:
    """Test extract_column_indices function"""
    
    def test_extracts_new_header_indices(self):
        """Extract column indices from new 6-column headers"""
        data = [
            ['Entry link type', 'Source Name', 'Source ID', 'Column', 'Target Name', 'Target ID']
        ]

        type_idx, src_name_idx, src_id_idx, col_idx, tgt_name_idx, tgt_id_idx = sheet_utils.extract_column_indices(data)

        assert type_idx == 0
        assert src_name_idx == 1
        assert src_id_idx == 2
        assert col_idx == 3
        assert tgt_name_idx == 4
        assert tgt_id_idx == 5


    def test_extracts_legacy_header_indices(self):
        """Extract column indices from legacy headers"""
        data = [
            ['entry_link_type', 'source_entry', 'target_entry', 'source_path']
        ]
        
        type_idx, src_name_idx, src_id_idx, col_idx, tgt_name_idx, tgt_id_idx = sheet_utils.extract_column_indices(data)
        
        assert type_idx == 0
        assert src_name_idx == 1
        assert src_id_idx == -1
        assert col_idx == 3
        assert tgt_name_idx == 2
        assert tgt_id_idx == -1
    
    def test_handles_missing_path_column(self):
        """Handle missing column/source_path column"""
        data = [
            ['Entry link type', 'Source Name', 'Target Name']
        ]
        
        type_idx, src_name_idx, src_id_idx, col_idx, tgt_name_idx, tgt_id_idx = sheet_utils.extract_column_indices(data)
        
        assert type_idx == 0
        assert src_name_idx == 1
        assert tgt_name_idx == 2
        assert col_idx == -1
    
    def test_raises_on_missing_required_column(self):
        """Raise ValueError for missing required column"""
        data = [
            ['Entry link type', 'Source Name']  # Missing Target
        ]
        
        with pytest.raises(ValueError):
            sheet_utils.extract_column_indices(data)



# ============================================================================
# EDGE CASES TESTS
# ============================================================================

class TestEdgeCases:
    """Test edge cases"""
    
    def test_handles_unicode_in_entries(self):
        """Handle Unicode characters in entry names"""
        row = ['definition', 'source_éntrée', 'src_id', '/путь', 'target_δοκιμή', 'tgt_id']
        
        result = sheet_utils._create_entry_link_dict(row, 0, 1, 2, 3, 4, 5)
        
        assert result['source_name'] == 'source_éntrée'
        assert result['target_name'] == 'target_δοκιμή'
    
    def test_handles_empty_strings(self):
        """Handle empty strings in row"""
        row = ['', '', '', '', '', '']

        result = sheet_utils._create_entry_link_dict(row, 0, 1, 2, 3, 4, 5)

        assert result['entry_link_type'] == ''
        assert result['source_name'] == ''

    def test_rows_with_omitted_trailing_target_id_are_not_dropped(self):
        """Rows where Google Sheets trims an empty trailing Target ID column should not be dropped"""
        rows = [
            ['Entry link type', 'Source Name', 'Source ID', 'Column', 'Target Name', 'Target ID'],
            ['definition', 'bigquery:p.d.t', 't', 'col1', 'p.global.G.Term']  # length 5 (Target ID trimmed)
        ]

        result = sheet_utils.rows_to_entry_link_dicts(rows, 0, 1, 2, 3, 4, 5)
        assert len(result) == 1
        assert result[0]['source_name'] == 'bigquery:p.d.t'
        assert result[0]['target_name'] == 'p.global.G.Term'
        assert result[0]['target_id'] == ''
        assert result[0]['row_number'] == '2'

    def test_export_falls_back_to_raw_name_when_term_resolution_raises(self, monkeypatch):
        """_add_entry_link_to_rows should fall back to raw entry name if term resolution fails"""
        mock_service = MagicMock()
        monkeypatch.setattr(sheet_utils.api_layer, 'get_entry_fqn', lambda s, r, p: 'bigquery:p.d.t')

        def fail_resolve(*args, **kwargs):
            raise RuntimeError("Permission denied")

        monkeypatch.setattr(sheet_utils.api_layer, 'resolve_term_entry_to_display_identifier', fail_resolve)

        rows = []
        source_ref = {'name': 'projects/p/locations/us/entryGroups/@bigquery/entries/t', 'path': 'Schema.c'}
        target_ref = {'name': 'projects/p/locations/global/entryGroups/@dataplex/entries/projects/p/locations/global/glossaries/g/terms/term1'}
        sheet_utils._add_entry_link_to_rows(rows, 'definition', source_ref, target_ref, dataplex_service=mock_service)

        assert len(rows) == 1
        assert rows[0][0] == 'definition'
        assert rows[0][1] == 'bigquery:p.d.t'
        assert rows[0][4] == target_ref['name']
        assert rows[0][5] == 'term1'
