"""
Unit tests for sheet_utils.py

Test coverage:
- Sheet name helpers (_get_first_sheet_name, _get_first_sheet_info, _build_sheet_range)
- Link type extraction (_extract_link_type)
- Reference finding (_find_source_and_target_refs)
- Entry link creation (_create_entry_link_dict)
- Spreadsheet operations (get_spreadsheet_id, authenticate_sheets, read_from_sheet, write_to_sheet)
- Data conversion (entry_link_to_row, is_redacted_entry_link, rows_to_entry_link_dicts)
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
# ENTRY LINK DICT CREATION TESTS
# ============================================================================

class TestCreateEntryLinkDict:
    """Test _create_entry_link_dict function"""
    
    def test_creates_dict_from_row(self):
        """Create entry link dict from row"""
        row = ['definition', 'source_name', 'src_id', 'order_id', 'target_name', 'tgt_id']

        result = sheet_utils._create_entry_link_dict(row, 0, 1, 2, 3, 4, 5, row_number=7)

        assert result == {
            'entry_link_type': 'definition',
            'source_name': 'source_name',
            'source_id': 'src_id',
            'column': 'order_id',
            'target_name': 'target_name',
            'target_id': 'tgt_id',
            'row_number': '7',
        }
    
    def test_handles_missing_path(self):
        """Handle row without path column"""
        row = ['related', 'source_name', '', '', 'target_name', '']
        
        result = sheet_utils._create_entry_link_dict(row, 0, 1, -1, -1, 4, -1)
        
        assert result['column'] == ''
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

DEFINITION_TYPE = 'projects/dataplex-types/locations/global/entryLinkTypes/definition'
SYNONYM_TYPE = 'projects/dataplex-types/locations/global/entryLinkTypes/synonym'
BQ_TABLE_ENTRY = (
    'projects/123/locations/us/entryGroups/@bigquery/entries/'
    'bigquery.googleapis.com/projects/proj/datasets/ds/tables/tbl'
)
TERM1_ENTRY = 'projects/123/locations/global/entryGroups/@dataplex/entries/projects/123/locations/global/glossaries/g/terms/t1'
TERM2_ENTRY = 'projects/123/locations/global/entryGroups/@dataplex/entries/projects/123/locations/global/glossaries/g/terms/t2'


class TestEntryLinkToRow:
    """Test entry_link_to_row function"""

    def test_converts_link_to_row_without_service(self):
        """Without a Dataplex service, Name cells hold the raw entry names"""
        entry_link = {
            'entryLinkType': DEFINITION_TYPE,
            'entryReferences': [
                {'type': 'SOURCE', 'name': BQ_TABLE_ENTRY, 'path': 'Schema.order_id'},
                {'type': 'TARGET', 'name': TERM1_ENTRY}
            ]
        }
        
        result = sheet_utils.entry_link_to_row(entry_link)
        
        assert result == ['definition', BQ_TABLE_ENTRY, 'proj.ds.tbl', 'order_id', TERM1_ENTRY, 't1']

    def test_definition_link_resolves_source_fqn_and_target_display_name(self, monkeypatch):
        """Definition links: FQN for the source, display identifier for the target term"""
        calls = []

        def fake_fqn(service, entry, user_project):
            calls.append(('fqn', entry, user_project))
            return 'bigquery:proj.ds.tbl'

        def fake_display(service, entry, user_project):
            calls.append(('display', entry, user_project))
            return 'proj.global.Sales.Order ID'

        monkeypatch.setattr(sheet_utils.api_layer, 'get_entry_fqn', fake_fqn)
        monkeypatch.setattr(sheet_utils.api_layer, 'resolve_term_entry_to_display_identifier', fake_display)
        entry_link = {
            'entryLinkType': DEFINITION_TYPE,
            'entryReferences': [
                {'type': 'SOURCE', 'name': BQ_TABLE_ENTRY, 'path': 'Schema.user_id'},
                {'type': 'TARGET', 'name': TERM1_ENTRY}
            ]
        }

        result = sheet_utils.entry_link_to_row(entry_link, dataplex_service=Mock(), user_project='user-proj')

        assert result == ['definition', 'bigquery:proj.ds.tbl', 'proj.ds.tbl', 'user_id', 'proj.global.Sales.Order ID', 't1']
        assert calls == [('fqn', BQ_TABLE_ENTRY, 'user-proj'), ('display', TERM1_ENTRY, 'user-proj')]

    def test_synonym_link_resolves_both_terms_and_has_no_column(self, monkeypatch):
        """Synonym/related links: both sides are terms and the Column cell stays empty"""
        def fail_fqn(*args):
            raise AssertionError("get_entry_fqn must not be called for term references")

        monkeypatch.setattr(sheet_utils.api_layer, 'get_entry_fqn', fail_fqn)
        monkeypatch.setattr(
            sheet_utils.api_layer, 'resolve_term_entry_to_display_identifier',
            lambda service, entry, user_project: f"proj.global.G.{entry.split('/')[-1].upper()}"
        )
        entry_link = {
            'entryLinkType': SYNONYM_TYPE,
            'entryReferences': [
                {'name': TERM1_ENTRY, 'path': 'Schema.ignored'},
                {'name': TERM2_ENTRY}
            ]
        }

        result = sheet_utils.entry_link_to_row(entry_link, dataplex_service=Mock(), user_project='user-proj')

        assert result == ['synonym', 'proj.global.G.T1', 't1', '', 'proj.global.G.T2', 't2']

    def test_falls_back_to_raw_name_when_resolution_fails(self, monkeypatch):
        """A name that can't be resolved is written as the raw entry name, with a warning"""
        warnings = []
        monkeypatch.setattr(sheet_utils.logger, 'warning', warnings.append)
        monkeypatch.setattr(sheet_utils.api_layer, 'get_entry_fqn', lambda s, r, p: 'bigquery:proj.ds.tbl')

        def fail_resolve(*args):
            raise RuntimeError("Permission denied")

        monkeypatch.setattr(sheet_utils.api_layer, 'resolve_term_entry_to_display_identifier', fail_resolve)
        entry_link = {
            'entryLinkType': DEFINITION_TYPE,
            'entryReferences': [
                {'type': 'SOURCE', 'name': BQ_TABLE_ENTRY, 'path': 'Schema.c'},
                {'type': 'TARGET', 'name': TERM1_ENTRY}
            ]
        }

        result = sheet_utils.entry_link_to_row(entry_link, dataplex_service=MagicMock())

        assert result == ['definition', 'bigquery:proj.ds.tbl', 'proj.ds.tbl', 'c', TERM1_ENTRY, 't1']
        assert len(warnings) == 1
        assert TERM1_ENTRY in warnings[0] and 'Permission denied' in warnings[0]
    
    def test_returns_none_for_invalid_link_type(self):
        """Links with an unknown link type can't be written as a row"""
        entry_link = {
            'entryLinkType': 'invalid',
            'entryReferences': [
                {'type': 'SOURCE', 'name': 'source'},
                {'type': 'TARGET', 'name': 'target'}
            ]
        }
        
        assert sheet_utils.entry_link_to_row(entry_link) is None

    @pytest.mark.parametrize('references', [[], [{'name': TERM1_ENTRY}]])
    def test_returns_none_without_source_and_target(self, references):
        """Links without both a source and a target can't be written as a row"""
        entry_link = {'entryLinkType': SYNONYM_TYPE, 'entryReferences': references}

        assert sheet_utils.entry_link_to_row(entry_link) is None


class TestIsRedactedEntryLink:
    """Test is_redacted_entry_link function"""

    def test_detects_redacted_reference(self):
        """A reference the caller can't view has a redacted ('*') name"""
        entry_link = {
            'entryLinkType': DEFINITION_TYPE,
            'entryReferences': [
                {'type': 'SOURCE', 'name': '***redacted***'},
                {'type': 'TARGET', 'name': TERM1_ENTRY}
            ]
        }

        assert sheet_utils.is_redacted_entry_link(entry_link) is True

    def test_visible_link_is_not_redacted(self):
        """Links whose references all have real names are not redacted"""
        entry_link = {
            'entryLinkType': SYNONYM_TYPE,
            'entryReferences': [{'name': TERM1_ENTRY}, {'name': TERM2_ENTRY}]
        }

        assert sheet_utils.is_redacted_entry_link(entry_link) is False
        assert sheet_utils.is_redacted_entry_link({}) is False


class TestRowsToEntryLinkDicts:
    """Test rows_to_entry_link_dicts function"""
    
    def test_converts_rows_to_dicts(self):
        """Convert rows to entry link dicts"""
        rows = [
            ['Entry link type', 'Source Name', 'Source ID', 'Column', 'Target Name', 'Target ID'],  # Header
            ['definition', 'bigquery:proj.ds.tbl', 'src1', 'order_id', 'proj.global.Sales.Order ID', 'tgt1']
        ]
        
        result = sheet_utils.rows_to_entry_link_dicts(rows, 0, 1, 2, 3, 4, 5)
        
        assert result == [{
            'entry_link_type': 'definition',
            'source_name': 'bigquery:proj.ds.tbl',
            'source_id': 'src1',
            'column': 'order_id',
            'target_name': 'proj.global.Sales.Order ID',
            'target_id': 'tgt1',
            'row_number': '2',
        }]
    
    def test_keeps_incomplete_rows_and_skips_blank_rows(self):
        """Incomplete rows are kept (the import reports them); blank rows are skipped"""
        rows = [
            ['Entry link type', 'Source Name', 'Source ID', 'Column', 'Target Name', 'Target ID'],
            ['definition', 'src1', '', 'order_id', 'tgt1', ''],
            ['', '', '', '', '', ''],                         # Blank row
            ['definition', '', '', 'order_id', 'tgt2', ''],   # Missing source
            [],                                               # Blank row (trimmed by Sheets)
            ['definition', 'src3', '', 'order_id', '', ''],   # Missing target
        ]
        
        result = sheet_utils.rows_to_entry_link_dicts(rows, 0, 1, 2, 3, 4, 5)
        
        assert [row['row_number'] for row in result] == ['2', '4', '6']
        assert result[1]['source_name'] == ''
        assert result[2]['target_name'] == ''
    
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

    def test_header_matching_ignores_case_and_whitespace(self):
        """Headers are matched case-insensitively, ignoring surrounding whitespace"""
        data = [
            ['ENTRY LINK TYPE', ' source name ', 'Source Id', 'COLUMN', 'target NAME', 'Target Id ']
        ]

        assert sheet_utils.extract_column_indices(data) == (0, 1, 2, 3, 4, 5)

    def test_rejects_four_column_source_target_headers(self):
        """A sheet with 'Source'/'Target' headers is rejected with the expected headers in the message"""
        data = [
            ['Entry link type', 'Source', 'Column', 'Target']
        ]

        with pytest.raises(ValueError) as error:
            sheet_utils.extract_column_indices(data)

        message = str(error.value)
        assert "'Source Name' or 'Source ID'" in message
        assert "'Target Name' or 'Target ID'" in message
        assert 'Expected headers' in message


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
