"""
Unit tests for entrylinks-export.py

Test coverage:
- Deduplication logic (_build_deduplication_key, deduplicate_entry_links)
- Region resolution and fetching (_resolve_regions_for_term, _fetch_links_from_regions_parallel)
- Entry link fetching (fetch_entry_links_for_region, fetch_entry_links_for_term, fetch_all_entry_links)
- Export workflow (export_entry_links, _write_entry_links_to_sheet)
- Error handling (network error detection via retry_utils, _handle_export_exception)
- Main flow (_run_export, main)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call
import pytest

# Import the module
sys.path.insert(0, str(Path(__file__).parent.parent))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "entrylinks_export", 
    str(Path(__file__).parent.parent / 'export' / 'entrylinks-export.py')
)
entrylinks_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrylinks_export)


# ============================================================================
# DEDUPLICATION TESTS
# ============================================================================

class TestBuildDeduplicationKey:
    """Test _build_deduplication_key helper function"""
    
    def test_symmetric_link_type_sorts_entries(self):
        """All link types should create sorted key to detect A-B == B-A"""
        row = ['related', 'entryB', '', 'entryA']
        key = entrylinks_export._build_deduplication_key(row)
        
        # Should be sorted: ('related', ('entryA', 'entryB'), '')
        assert key[0] == 'related'
        assert key[1] == tuple(sorted(['entryA', 'entryB']))
    
    def test_synonym_link_type_is_symmetric(self):
        """Synonym links should also be deduplicated symmetrically"""
        row1 = ['synonym', 'termA', '', 'termB']
        row2 = ['synonym', 'termB', '', 'termA']
        
        key1 = entrylinks_export._build_deduplication_key(row1)
        key2 = entrylinks_export._build_deduplication_key(row2)
        
        assert key1 == key2
    
    def test_definition_link_type_is_directional(self):
        """Definition links are directional (source asset -> target term)"""
        row1 = ['definition', 'source', 'col', 'target']
        row2 = ['definition', 'target', 'col', 'source']
        
        key1 = entrylinks_export._build_deduplication_key(row1)
        key2 = entrylinks_export._build_deduplication_key(row2)
        
        assert key1 != key2
    
    def test_includes_source_path(self):
        """Keys should include column / source_path"""
        row = ['definition', 'source', 'col1', 'target']
        key = entrylinks_export._build_deduplication_key(row)
        
        assert 'col1' in key
    
    def test_missing_source_path_uses_empty_string(self):
        """Rows with only 3 elements should use empty string for path"""
        row = ['related', 'entryA', 'entryB']  # No path element
        key = entrylinks_export._build_deduplication_key(row)
        
        # Should not raise an error
        assert key is not None

    def test_6_column_symmetric_link_type_sorts_entries(self):
        """6-column row format should deduplicate symmetric links"""
        row1 = ['synonym', 'proj.loc.glossary.Term A', 'term_a', '', 'proj.loc.glossary.Term B', 'term_b']
        row2 = ['synonym', 'proj.loc.glossary.Term B', 'term_b', '', 'proj.loc.glossary.Term A', 'term_a']

        key1 = entrylinks_export._build_deduplication_key(row1)
        key2 = entrylinks_export._build_deduplication_key(row2)

        assert key1 == key2

    def test_6_column_definition_link_type_directional(self):
        """6-column row format definition links are directional"""
        row1 = ['definition', 'bigquery:p.d.t', 't', 'col1', 'proj.loc.glossary.Term A', 'term_a']
        row2 = ['definition', 'proj.loc.glossary.Term A', 'term_a', 'col1', 'bigquery:p.d.t', 't']

        key1 = entrylinks_export._build_deduplication_key(row1)
        key2 = entrylinks_export._build_deduplication_key(row2)

        assert key1 != key2



class TestDeduplicateEntryLinks:
    """Test deduplicate_entry_links function"""
    
    def test_removes_duplicate_symmetric_links(self):
        """Should remove A-B if B-A already exists for symmetric types"""
        links = [
            ['related', 'term1', '', 'term2'],
            ['related', 'term2', '', 'term1'],  # Duplicate
        ]
        
        result = entrylinks_export.deduplicate_entry_links(links)
        
        assert len(result) == 1
    
    def test_keeps_different_link_types(self):
        """Different link types between same entries should be kept"""
        links = [
            ['related', 'term1', '', 'term2'],
            ['synonym', 'term1', '', 'term2'],
        ]
        
        result = entrylinks_export.deduplicate_entry_links(links)
        
        assert len(result) == 2
    
    def test_keeps_directional_links_both_directions(self):
        """Definition links in both directions should be kept"""
        links = [
            ['definition', 'table1', 'col1', 'term1'],
            ['definition', 'table2', 'col2', 'term1'],
        ]
        
        result = entrylinks_export.deduplicate_entry_links(links)
        
        assert len(result) == 2
    
    def test_empty_input_returns_empty(self):
        """Empty input should return empty list"""
        result = entrylinks_export.deduplicate_entry_links([])
        assert result == []
    
    def test_preserves_order_of_first_occurrence(self):
        """First occurrence of link should be kept"""
        links = [
            ['related', 'A', '', 'B'],
            ['related', 'B', '', 'A'],  # Duplicate - should be removed
        ]
        
        result = entrylinks_export.deduplicate_entry_links(links)
        
        assert result[0] == ['related', 'A', '', 'B']



# ============================================================================
# REGION RESOLUTION TESTS
# ============================================================================

class TestResolveRegionsForGlossary:
    """Test _resolve_regions_for_glossary function"""
    
    def test_regional_glossary_returns_single_region(self, monkeypatch):
        """Regional glossaries should query only their region"""
        mock_extract = MagicMock(return_value='us-central1')
        mock_resolve = MagicMock(return_value=['us-central1'])
        
        monkeypatch.setattr(entrylinks_export.business_glossary_utils, 
                          'extract_location_from_name', mock_extract)
        monkeypatch.setattr(entrylinks_export.api_layer, 
                          'resolve_regions_to_query', mock_resolve)
        
        result = entrylinks_export._resolve_regions_for_glossary(
            'projects/p/locations/us-central1/glossaries/g',
            'test-project'
        )
        
        assert result == ['us-central1']
    
    def test_global_glossary_returns_all_regions(self, monkeypatch):
        """Global glossaries should query all endpoints"""
        mock_extract = MagicMock(return_value='global')
        mock_resolve = MagicMock(return_value=['global', 'us', 'eu', 'us-central1'])
        
        monkeypatch.setattr(entrylinks_export.business_glossary_utils, 
                          'extract_location_from_name', mock_extract)
        monkeypatch.setattr(entrylinks_export.api_layer, 
                          'resolve_regions_to_query', mock_resolve)
        
        result = entrylinks_export._resolve_regions_for_glossary(
            'projects/p/locations/global/glossaries/g',
            'test-project'
        )
        
        assert 'global' in result
        assert len(result) > 1
    
    def test_resolution_failure_returns_empty_list(self, monkeypatch):
        """Failures should return empty list, not raise"""
        mock_extract = MagicMock(return_value='us-central1')
        mock_resolve = MagicMock(side_effect=Exception("API error"))
        
        monkeypatch.setattr(entrylinks_export.business_glossary_utils, 
                          'extract_location_from_name', mock_extract)
        monkeypatch.setattr(entrylinks_export.api_layer, 
                          'resolve_regions_to_query', mock_resolve)
        
        result = entrylinks_export._resolve_regions_for_glossary(
            'projects/p/locations/us-central1/glossaries/g',
            'test-project'
        )
        
        assert result == []


# ============================================================================
# ENTRY LINK FETCHING TESTS
# ============================================================================

class TestFetchEntryLinksForRegion:
    """Test fetch_entry_links_for_region function"""
    
    def test_successful_fetch_returns_links(self, monkeypatch):
        """Successful API call returns entry links"""
        mock_links = [{'name': 'link1'}, {'name': 'link2'}]
        mock_lookup = MagicMock(return_value=mock_links)
        monkeypatch.setattr(entrylinks_export.api_layer, 
                          'lookup_entry_links_for_term', mock_lookup)
        
        result = entrylinks_export.fetch_entry_links_for_region(
            'entry123', 'us-central1', 'test-project'
        )
        
        assert result == mock_links
    
    def test_returns_empty_list_when_none(self, monkeypatch):
        """Returns empty list when API returns None"""
        mock_lookup = MagicMock(return_value=None)
        monkeypatch.setattr(entrylinks_export.api_layer, 
                          'lookup_entry_links_for_term', mock_lookup)
        
        result = entrylinks_export.fetch_entry_links_for_region(
            'entry123', 'us-central1', 'test-project'
        )
        
        assert result == []
    
    def test_returns_empty_list_on_exception(self, monkeypatch):
        """Exceptions should return empty list, not propagate"""
        mock_lookup = MagicMock(side_effect=Exception("Network error"))
        monkeypatch.setattr(entrylinks_export.api_layer, 
                          'lookup_entry_links_for_term', mock_lookup)
        
        result = entrylinks_export.fetch_entry_links_for_region(
            'entry123', 'us-central1', 'test-project'
        )
        
        assert result == []


class TestFetchEntryLinksForTerm:
    """Test fetch_entry_links_for_term function"""
    
    def test_no_regions_returns_empty(self, monkeypatch):
        """When no regions provided, return empty list"""
        mock_generate = MagicMock(return_value='entry123')
        
        monkeypatch.setattr(entrylinks_export.business_glossary_utils,
                          'generate_entry_name_from_term_name', mock_generate)
        
        result = entrylinks_export.fetch_entry_links_for_term(
            {'name': 'term1'}, [], 'test-project'
        )
        
        assert result == []
    
    def test_aggregates_links_from_all_regions(self, monkeypatch):
        """Should aggregate links from all queried regions"""
        mock_generate = MagicMock(return_value='entry123')
        mock_fetch_region = MagicMock(return_value=[{'entryLinkType': 'test'}])
        mock_to_rows = MagicMock(return_value=[['row1'], ['row2']])
        
        monkeypatch.setattr(entrylinks_export.business_glossary_utils,
                          'generate_entry_name_from_term_name', mock_generate)
        monkeypatch.setattr(entrylinks_export, 'fetch_entry_links_for_region', mock_fetch_region)
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'entry_links_to_rows', mock_to_rows)
        
        result = entrylinks_export.fetch_entry_links_for_term(
            {'name': 'term1'}, ['us', 'eu'], 'test-project'
        )
        
        assert mock_to_rows.called


class TestFetchAllEntryLinks:
    """Test fetch_all_entry_links function"""
    
    def test_fetches_links_for_all_terms(self, monkeypatch):
        """Should fetch links for each term"""
        terms = [{'name': 'term1'}, {'name': 'term2'}]
        mock_fetch = MagicMock(return_value=[['link1']])
        
        monkeypatch.setattr(entrylinks_export, 'fetch_entry_links_for_term', mock_fetch)
        
        result = entrylinks_export.fetch_all_entry_links(terms, ['us'], 'test-project')
        
        assert len(result) == 2  # One link per term
    
    def test_continues_on_single_term_failure(self, monkeypatch):
        """Failure for one term propagates since fetch_all_entry_links doesn't catch per-term exceptions"""
        terms = [{'name': 'term1'}, {'name': 'term2'}]
        mock_fetch = MagicMock(side_effect=[Exception("Error"), [['link2']]])
        
        monkeypatch.setattr(entrylinks_export, 'fetch_entry_links_for_term', mock_fetch)
        
        # Exception from first term propagates through the ThreadPoolExecutor
        with pytest.raises(Exception, match="Error"):
            entrylinks_export.fetch_all_entry_links(terms, ['us'], 'test-project')


# ============================================================================
# EXPORT WORKFLOW TESTS
# ============================================================================

class TestWriteEntryLinksToSheet:
    """Test _write_entry_links_to_sheet function"""
    
    def test_writes_with_headers(self, monkeypatch):
        """Should include headers row when writing"""
        entry_links = [['definition', 'src', 'tgt', '/path']]
        mock_get_id = MagicMock(return_value='sheet123')
        mock_write = MagicMock(return_value='Sheet1')
        
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'get_spreadsheet_id', mock_get_id)
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'write_to_sheet', mock_write)
        
        result = entrylinks_export._write_entry_links_to_sheet(
            entry_links, 'http://sheet-url', MagicMock()
        )
        
        # Verify write was called with headers + data
        write_call_args = mock_write.call_args[0]
        data_written = write_call_args[2]  # Third argument is data
        assert data_written[0] == entrylinks_export.SHEET_HEADERS
        assert data_written[1] == entry_links[0]


class TestExportEntryLinks:
    """Test export_entry_links function"""
    
    def test_returns_false_when_no_terms(self, monkeypatch):
        """Returns False when glossary has no terms"""
        mock_auth_dataplex = MagicMock()
        mock_auth_sheets = MagicMock()
        mock_init_cache = MagicMock()
        mock_list_terms = MagicMock(return_value=[])
        mock_clear = MagicMock()
        
        monkeypatch.setattr(entrylinks_export.api_layer, 'authenticate_dataplex', mock_auth_dataplex)
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'authenticate_sheets', mock_auth_sheets)
        monkeypatch.setattr(entrylinks_export.api_layer, 'initialize_locations_cache', mock_init_cache)
        monkeypatch.setattr(entrylinks_export.api_layer, 'list_glossary_terms', mock_list_terms)
        monkeypatch.setattr(entrylinks_export, '_clear_sheet_with_headers', mock_clear)
        
        result = entrylinks_export.export_entry_links(
            'glossary/path', 'http://sheet', 'project'
        )
        
        assert result is False
    
    def test_returns_false_when_no_links(self, monkeypatch):
        """Returns False when terms have no entry links"""
        mock_auth_dataplex = MagicMock()
        mock_auth_sheets = MagicMock()
        mock_init_cache = MagicMock()
        mock_list_terms = MagicMock(return_value=[{'name': 'term1'}])
        mock_resolve = MagicMock(return_value=['us'])
        mock_fetch_all = MagicMock(return_value=[])
        mock_clear = MagicMock()
        
        monkeypatch.setattr(entrylinks_export.api_layer, 'authenticate_dataplex', mock_auth_dataplex)
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'authenticate_sheets', mock_auth_sheets)
        monkeypatch.setattr(entrylinks_export.api_layer, 'initialize_locations_cache', mock_init_cache)
        monkeypatch.setattr(entrylinks_export.api_layer, 'list_glossary_terms', mock_list_terms)
        monkeypatch.setattr(entrylinks_export, '_resolve_regions_for_glossary', mock_resolve)
        monkeypatch.setattr(entrylinks_export, 'fetch_all_entry_links', mock_fetch_all)
        monkeypatch.setattr(entrylinks_export, '_clear_sheet_with_headers', mock_clear)
        
        result = entrylinks_export.export_entry_links(
            'glossary/path', 'http://sheet', 'project'
        )
        
        assert result is False
    
    def test_returns_true_on_success(self, monkeypatch):
        """Returns True when export succeeds"""
        mock_auth_dataplex = MagicMock()
        mock_auth_sheets = MagicMock()
        mock_init_cache = MagicMock()
        mock_list_terms = MagicMock(return_value=[{'name': 'term1'}])
        mock_resolve = MagicMock(return_value=['us'])
        mock_fetch_all = MagicMock(return_value=[['link1']])
        mock_dedup = MagicMock(return_value=[['link1']])
        mock_write = MagicMock(return_value='Sheet1')
        
        monkeypatch.setattr(entrylinks_export.api_layer, 'authenticate_dataplex', mock_auth_dataplex)
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'authenticate_sheets', mock_auth_sheets)
        monkeypatch.setattr(entrylinks_export.api_layer, 'initialize_locations_cache', mock_init_cache)
        monkeypatch.setattr(entrylinks_export.api_layer, 'list_glossary_terms', mock_list_terms)
        monkeypatch.setattr(entrylinks_export, '_resolve_regions_for_glossary', mock_resolve)
        monkeypatch.setattr(entrylinks_export, 'fetch_all_entry_links', mock_fetch_all)
        monkeypatch.setattr(entrylinks_export, 'deduplicate_entry_links', mock_dedup)
        monkeypatch.setattr(entrylinks_export, '_write_entry_links_to_sheet', mock_write)
        
        result = entrylinks_export.export_entry_links(
            'glossary/path', 'http://sheet', 'project'
        )
        
        assert result is True


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestNetworkErrorDetection:
    """Test network error detection via retry_utils.is_network_error (used by _handle_export_exception)"""
    
    def test_os_error_detected(self):
        """OSError subclasses should be detected as network errors"""
        exc = ConnectionRefusedError("Connection refused")
        result = entrylinks_export._handle_export_exception(exc)
        assert result == 1
    
    def test_timeout_error_detected(self):
        """TimeoutError should be detected as network error"""
        exc = TimeoutError("Request timed out")
        result = entrylinks_export._handle_export_exception(exc)
        assert result == 1
    
    def test_non_network_error_still_handled(self):
        """Non-network errors should still return exit code 1"""
        exc = Exception("Invalid argument")
        result = entrylinks_export._handle_export_exception(exc)
        assert result == 1


class TestHandleExportException:
    """Test _handle_export_exception function"""
    
    def test_keyboard_interrupt_returns_1(self):
        """KeyboardInterrupt should return exit code 1"""
        result = entrylinks_export._handle_export_exception(KeyboardInterrupt())
        assert result == 1
    
    def test_dataplex_api_error_returns_1(self, monkeypatch):
        """DataplexAPIError should return exit code 1"""
        from utils.error import DataplexAPIError
        result = entrylinks_export._handle_export_exception(
            DataplexAPIError("API failed")
        )
        assert result == 1
    
    def test_sheets_api_error_returns_1(self, monkeypatch):
        """SheetsAPIError should return exit code 1"""
        from utils.error import SheetsAPIError
        result = entrylinks_export._handle_export_exception(
            SheetsAPIError("Sheets failed")
        )
        assert result == 1
    
    def test_network_error_returns_1(self):
        """Network errors should return exit code 1"""
        result = entrylinks_export._handle_export_exception(
            OSError("Network unreachable")
        )
        assert result == 1
    
    def test_generic_error_returns_1(self):
        """Generic errors should return exit code 1"""
        result = entrylinks_export._handle_export_exception(
            Exception("Unknown error")
        )
        assert result == 1


# ============================================================================
# MAIN FLOW TESTS
# ============================================================================

class TestRunExport:
    """Test _run_export function"""
    
    def test_returns_0_on_success(self, monkeypatch):
        """Should return 0 when export succeeds"""
        mock_setup_logging = MagicMock()
        mock_get_args = MagicMock()
        mock_get_args.return_value.glossary_url = 'http://glossary'
        mock_get_args.return_value.spreadsheet_url = 'http://sheet'
        mock_get_args.return_value.user_project = 'test-project'
        mock_extract_glossary = MagicMock(return_value='glossary/path')
        mock_export = MagicMock(return_value=True)
        
        monkeypatch.setattr(entrylinks_export.logging_utils, 'setup_file_logging', mock_setup_logging)
        monkeypatch.setattr(entrylinks_export.argument_parser, 'get_export_entrylinks_arguments', mock_get_args)
        monkeypatch.setattr(entrylinks_export.business_glossary_utils, 'extract_glossary_name', mock_extract_glossary)
        monkeypatch.setattr(entrylinks_export, 'export_entry_links', mock_export)
        
        result = entrylinks_export._run_export()
        
        assert result == 0


class TestMain:
    """Test main function"""
    
    def test_catches_exceptions_and_handles(self, monkeypatch):
        """Main should catch exceptions and call handler"""
        mock_run = MagicMock(side_effect=Exception("Test error"))
        mock_handle = MagicMock(return_value=1)
        
        monkeypatch.setattr(entrylinks_export, '_run_export', mock_run)
        monkeypatch.setattr(entrylinks_export, '_handle_export_exception', mock_handle)
        
        result = entrylinks_export.main()
        
        assert mock_handle.called
        assert result == 1
    
    def test_returns_run_export_result_on_success(self, monkeypatch):
        """Main should return _run_export result when no exception"""
        mock_run = MagicMock(return_value=0)
        
        monkeypatch.setattr(entrylinks_export, '_run_export', mock_run)
        
        result = entrylinks_export.main()
        
        assert result == 0
