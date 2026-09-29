"""
Unit tests for entrylinks-export.py

Test coverage:
- Deduplication logic (_build_deduplication_key, deduplicate_entry_links, deduplicate_raw_entry_links)
- Region resolution and fetching (_resolve_regions_for_term, _fetch_links_from_regions_parallel)
- Entry link fetching (fetch_entry_links_for_region, fetch_entry_links_for_term, fetch_all_entry_links)
- Export workflow (export_entry_links, convert_entry_links_to_rows, _write_entry_links_to_sheet)
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

TERM_A = ['proj.global.Sales.Term A', 'term-a']
TERM_B = ['proj.global.Sales.Term B', 'term-b']
TABLE = ['bigquery:proj.ds.tbl', 'proj.ds.tbl']


def _row(link_type, source, target, column=''):
    """Build a row [type, source_name, source_id, column, target_name, target_id]."""
    return [link_type, source[0], source[1], column, target[0], target[1]]


class TestBuildDeduplicationKey:
    """Test _build_deduplication_key helper function"""
    
    @pytest.mark.parametrize('link_type', ['synonym', 'related'])
    def test_symmetric_link_types_ignore_direction(self, link_type):
        """Synonym and related links have no direction: A-B == B-A"""
        key1 = entrylinks_export._build_deduplication_key(_row(link_type, TERM_A, TERM_B))
        key2 = entrylinks_export._build_deduplication_key(_row(link_type, TERM_B, TERM_A))
        
        assert key1 == key2
    
    def test_definition_link_type_is_directional(self):
        """Definition links are directional (source asset -> target term)"""
        key1 = entrylinks_export._build_deduplication_key(_row('definition', TABLE, TERM_A, 'col1'))
        key2 = entrylinks_export._build_deduplication_key(_row('definition', TERM_A, TABLE, 'col1'))
        
        assert key1 != key2
    
    def test_includes_column(self):
        """Links to different columns are different"""
        key1 = entrylinks_export._build_deduplication_key(_row('definition', TABLE, TERM_A, 'col1'))
        key2 = entrylinks_export._build_deduplication_key(_row('definition', TABLE, TERM_A, 'col2'))
        
        assert key1 != key2

    def test_terms_with_same_display_name_are_distinct(self):
        """Rows that differ only by term ID (same display name) are different links"""
        same_name_other_term = [TERM_A[0], 'term-a-2']
        key1 = entrylinks_export._build_deduplication_key(_row('definition', TABLE, TERM_A))
        key2 = entrylinks_export._build_deduplication_key(_row('definition', TABLE, same_name_other_term))

        assert key1 != key2

    def test_terms_with_same_id_in_other_glossaries_are_distinct(self):
        """Rows that differ only by name (same term ID in another glossary) are different links"""
        same_id_other_glossary = ['proj.global.Finance.Term A', TERM_A[1]]
        key1 = entrylinks_export._build_deduplication_key(_row('synonym', TERM_A, TERM_B))
        key2 = entrylinks_export._build_deduplication_key(_row('synonym', same_id_other_glossary, TERM_B))

        assert key1 != key2


class TestDeduplicateEntryLinks:
    """Test deduplicate_entry_links function"""
    
    def test_removes_duplicate_symmetric_links(self):
        """Should remove A-B if B-A already exists for symmetric types"""
        links = [
            _row('related', TERM_A, TERM_B),
            _row('related', TERM_B, TERM_A),  # Duplicate
        ]
        
        result = entrylinks_export.deduplicate_entry_links(links)
        
        assert len(result) == 1
    
    def test_keeps_different_link_types(self):
        """Different link types between same entries should be kept"""
        links = [
            _row('related', TERM_A, TERM_B),
            _row('synonym', TERM_A, TERM_B),
        ]
        
        result = entrylinks_export.deduplicate_entry_links(links)
        
        assert len(result) == 2
    
    def test_keeps_definition_links_from_different_sources(self):
        """Definition links from different data assets to the same term should be kept"""
        links = [
            _row('definition', TABLE, TERM_A, 'col1'),
            _row('definition', ['bigquery:proj.ds.other', 'proj.ds.other'], TERM_A, 'col1'),
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
            _row('related', TERM_A, TERM_B),
            _row('related', TERM_B, TERM_A),  # Duplicate - should be removed
        ]
        
        result = entrylinks_export.deduplicate_entry_links(links)
        
        assert result == [_row('related', TERM_A, TERM_B)]


DEFINITION_TYPE = 'projects/dataplex-types/locations/global/entryLinkTypes/definition'
SYNONYM_TYPE = 'projects/dataplex-types/locations/global/entryLinkTypes/synonym'
TERM_A_ENTRY = 'projects/123/locations/global/entryGroups/@dataplex/entries/projects/123/locations/global/glossaries/g/terms/term-a'
TERM_B_ENTRY = 'projects/123/locations/global/entryGroups/@dataplex/entries/projects/123/locations/global/glossaries/g/terms/term-b'
TABLE_ENTRY = 'projects/123/locations/us/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/proj/datasets/ds/tables/tbl'


def _synonym_link(name, first, second, link_type=SYNONYM_TYPE):
    return {'name': name, 'entryLinkType': link_type, 'entryReferences': [{'name': first}, {'name': second}]}


def _definition_link(name, source, target, path=''):
    return {
        'name': name,
        'entryLinkType': DEFINITION_TYPE,
        'entryReferences': [
            {'name': source, 'path': path, 'type': 'SOURCE'},
            {'name': target, 'type': 'TARGET'},
        ],
    }


class TestDeduplicateRawEntryLinks:
    """Test deduplicate_raw_entry_links function"""

    def test_link_returned_for_each_term_is_kept_once(self):
        """lookupEntryLinks returns a synonym link for both of its terms"""
        link = _synonym_link('links/1', TERM_A_ENTRY, TERM_B_ENTRY)

        assert entrylinks_export.deduplicate_raw_entry_links([link, dict(link)]) == [link]

    def test_symmetric_references_in_either_order_are_duplicates(self):
        """Two synonym links between the same terms are the same link"""
        links = [
            _synonym_link('links/1', TERM_A_ENTRY, TERM_B_ENTRY),
            _synonym_link('links/2', TERM_B_ENTRY, TERM_A_ENTRY),
        ]

        assert entrylinks_export.deduplicate_raw_entry_links(links) == links[:1]

    def test_link_type_project_format_is_ignored(self):
        """The link type may be named with the project number or the project ID"""
        links = [
            _synonym_link('links/1', TERM_A_ENTRY, TERM_B_ENTRY),
            _synonym_link(
                'links/1', TERM_A_ENTRY, TERM_B_ENTRY,
                link_type='projects/655216118709/locations/global/entryLinkTypes/synonym'
            ),
        ]

        assert len(entrylinks_export.deduplicate_raw_entry_links(links)) == 1

    def test_distinct_links_are_kept(self):
        """Links to different columns, of different types or in the other direction are kept"""
        links = [
            _definition_link('links/1', TABLE_ENTRY, TERM_A_ENTRY, 'Schema.col1'),
            _definition_link('links/2', TABLE_ENTRY, TERM_A_ENTRY, 'Schema.col2'),
            _definition_link('links/3', TABLE_ENTRY, TERM_A_ENTRY),
            _definition_link('links/4', TERM_A_ENTRY, TABLE_ENTRY),
            _synonym_link('links/5', TABLE_ENTRY, TERM_A_ENTRY),
        ]

        assert entrylinks_export.deduplicate_raw_entry_links(links) == links



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


TERM_NAME = 'projects/my-proj/locations/us/glossaries/g/terms/t1'


class TestFetchEntryLinksForTerm:
    """Test fetch_entry_links_for_term function"""
    
    def test_no_regions_returns_empty(self, monkeypatch):
        """When no regions provided, return empty list without any API calls"""
        mock_get_number = MagicMock(return_value='123')
        mock_fetch_region = MagicMock()
        
        monkeypatch.setattr(entrylinks_export.api_layer, 'get_project_number', mock_get_number)
        monkeypatch.setattr(entrylinks_export, 'fetch_entry_links_for_region', mock_fetch_region)
        
        result = entrylinks_export.fetch_entry_links_for_term(
            {'name': TERM_NAME}, [], 'test-project'
        )
        
        assert result == []
        mock_get_number.assert_not_called()
        mock_fetch_region.assert_not_called()
    
    def test_aggregates_links_from_all_regions(self, monkeypatch):
        """Should return the raw links of all queried regions for the term's entry"""
        links_by_region = {'us': [{'name': 'links/us'}], 'eu': [{'name': 'links/eu'}]}
        mock_fetch_region = MagicMock(side_effect=lambda entry, region, project: links_by_region[region])
        
        monkeypatch.setattr(entrylinks_export.api_layer, 'get_project_number', MagicMock(return_value='123'))
        monkeypatch.setattr(entrylinks_export, 'fetch_entry_links_for_region', mock_fetch_region)
        
        result = entrylinks_export.fetch_entry_links_for_term(
            {'name': TERM_NAME}, ['us', 'eu'], 'test-project'
        )
        
        assert sorted(link['name'] for link in result) == ['links/eu', 'links/us']
        term_entry = 'projects/123/locations/us/entryGroups/@dataplex/entries/projects/123/locations/us/glossaries/g/terms/t1'
        mock_fetch_region.assert_has_calls(
            [call(term_entry, 'us', 'test-project'), call(term_entry, 'eu', 'test-project')], any_order=True
        )

    def test_uses_project_id_when_project_number_unavailable(self, monkeypatch):
        """If the project number can't be resolved, the term's entry name uses the project ID"""
        mock_fetch_region = MagicMock(return_value=[])

        monkeypatch.setattr(entrylinks_export.api_layer, 'get_project_number', MagicMock(side_effect=Exception("403")))
        monkeypatch.setattr(entrylinks_export, 'fetch_entry_links_for_region', mock_fetch_region)

        entrylinks_export.fetch_entry_links_for_term({'name': TERM_NAME}, ['us'], 'test-project')

        mock_fetch_region.assert_called_once_with(
            'projects/my-proj/locations/us/entryGroups/@dataplex/entries/projects/my-proj/locations/us/glossaries/g/terms/t1',
            'us', 'test-project'
        )


class TestFetchAllEntryLinks:
    """Test fetch_all_entry_links function"""
    
    def test_fetches_links_for_all_terms(self, monkeypatch):
        """Should fetch links for each term"""
        terms = [{'name': 'term1'}, {'name': 'term2'}]
        mock_fetch = MagicMock(return_value=[{'name': 'link1'}])
        
        monkeypatch.setattr(entrylinks_export, 'fetch_entry_links_for_term', mock_fetch)
        
        result = entrylinks_export.fetch_all_entry_links(terms, ['us'], 'test-project')
        
        assert len(result) == 2  # One link per term
    
    def test_continues_on_single_term_failure(self, monkeypatch):
        """Failure for one term propagates since fetch_all_entry_links doesn't catch per-term exceptions"""
        terms = [{'name': 'term1'}, {'name': 'term2'}]
        mock_fetch = MagicMock(side_effect=[Exception("Error"), [{'name': 'link2'}]])
        
        monkeypatch.setattr(entrylinks_export, 'fetch_entry_links_for_term', mock_fetch)
        
        # Exception from first term propagates through the ThreadPoolExecutor
        with pytest.raises(Exception, match="Error"):
            entrylinks_export.fetch_all_entry_links(terms, ['us'], 'test-project')


class TestConvertEntryLinksToRows:
    """Test convert_entry_links_to_rows function"""

    @pytest.fixture
    def converted_links(self, monkeypatch):
        """Mock entry_link_to_row and record the (link name, user project) of every conversion.

        The link 'links/invalid' can't be represented as a row.
        """
        converted = []

        def mock_entry_link_to_row(entry_link, dataplex_service, user_project):
            converted.append((entry_link['name'], user_project))
            if entry_link['name'] == 'links/invalid':
                return None
            return ['synonym', entry_link['name'], 'source-id', '', 'target', 'target-id']

        monkeypatch.setattr(entrylinks_export.sheet_utils, 'entry_link_to_row', mock_entry_link_to_row)
        monkeypatch.setattr(entrylinks_export.api_layer, 'get_dataplex_service', MagicMock())
        return converted

    def test_converts_each_unique_visible_link_once(self, monkeypatch, converted_links):
        """Duplicate and redacted links are skipped, links without a row are dropped, order is kept"""
        info_messages = []
        monkeypatch.setattr(entrylinks_export.logger, 'info', info_messages.append)
        links = [
            _synonym_link('links/1', TERM_A_ENTRY, TERM_B_ENTRY),
            _synonym_link('links/1', TERM_A_ENTRY, TERM_B_ENTRY),  # Returned for both of its terms
            _synonym_link('links/redacted', TERM_A_ENTRY, 'projects/*/locations/*/entryGroups/*/entries/*'),
            _definition_link('links/invalid', TABLE_ENTRY, TERM_A_ENTRY, 'Schema.col1'),
            _definition_link('links/2', TABLE_ENTRY, TERM_B_ENTRY),
        ]

        rows = entrylinks_export.convert_entry_links_to_rows(links, 'test-project')

        assert [row[1] for row in rows] == ['links/1', 'links/2']
        assert sorted(converted_links) == [
            ('links/1', 'test-project'), ('links/2', 'test-project'), ('links/invalid', 'test-project')
        ]
        assert info_messages == ['Skipped 1 redacted entrylink(s) during export']

    def test_empty_input_returns_empty(self, converted_links):
        """No links means no rows and no conversions"""
        assert entrylinks_export.convert_entry_links_to_rows([], 'test-project') == []
        assert converted_links == []


# ============================================================================
# EXPORT WORKFLOW TESTS
# ============================================================================

class TestWriteEntryLinksToSheet:
    """Test _write_entry_links_to_sheet function"""
    
    def test_writes_with_headers(self, monkeypatch):
        """Should include headers row when writing"""
        entry_links = [_row('definition', TABLE, TERM_A, 'col1')]
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
        """Returns False (and clears the sheet) when terms have no entry links"""
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
        mock_clear.assert_called_once()

    def test_returns_false_when_no_link_can_be_exported(self, monkeypatch):
        """Returns False (and clears the sheet) when all fetched links are skipped"""
        mock_auth_dataplex = MagicMock()
        mock_auth_sheets = MagicMock()
        mock_init_cache = MagicMock()
        mock_list_terms = MagicMock(return_value=[{'name': 'term1'}])
        mock_resolve = MagicMock(return_value=['us'])
        mock_fetch_all = MagicMock(return_value=[{'name': 'links/redacted'}])
        mock_convert = MagicMock(return_value=[])
        mock_clear = MagicMock()
        mock_write = MagicMock()

        monkeypatch.setattr(entrylinks_export.api_layer, 'authenticate_dataplex', mock_auth_dataplex)
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'authenticate_sheets', mock_auth_sheets)
        monkeypatch.setattr(entrylinks_export.api_layer, 'initialize_locations_cache', mock_init_cache)
        monkeypatch.setattr(entrylinks_export.api_layer, 'list_glossary_terms', mock_list_terms)
        monkeypatch.setattr(entrylinks_export, '_resolve_regions_for_glossary', mock_resolve)
        monkeypatch.setattr(entrylinks_export, 'fetch_all_entry_links', mock_fetch_all)
        monkeypatch.setattr(entrylinks_export, 'convert_entry_links_to_rows', mock_convert)
        monkeypatch.setattr(entrylinks_export, '_clear_sheet_with_headers', mock_clear)
        monkeypatch.setattr(entrylinks_export, '_write_entry_links_to_sheet', mock_write)

        result = entrylinks_export.export_entry_links(
            'glossary/path', 'http://sheet', 'project'
        )

        assert result is False
        mock_clear.assert_called_once()
        mock_write.assert_not_called()
    
    def test_returns_true_on_success(self, monkeypatch):
        """Returns True when export succeeds, writing the converted rows without duplicates"""
        rows = [_row('synonym', TERM_A, TERM_B), _row('synonym', TERM_B, TERM_A)]
        mock_auth_dataplex = MagicMock()
        mock_auth_sheets = MagicMock()
        mock_init_cache = MagicMock()
        mock_list_terms = MagicMock(return_value=[{'name': 'term1'}])
        mock_resolve = MagicMock(return_value=['us'])
        mock_fetch_all = MagicMock(return_value=[{'name': 'links/1'}])
        mock_convert = MagicMock(return_value=rows)
        mock_write = MagicMock(return_value='Sheet1')
        
        monkeypatch.setattr(entrylinks_export.api_layer, 'authenticate_dataplex', mock_auth_dataplex)
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'authenticate_sheets', mock_auth_sheets)
        monkeypatch.setattr(entrylinks_export.api_layer, 'initialize_locations_cache', mock_init_cache)
        monkeypatch.setattr(entrylinks_export.api_layer, 'list_glossary_terms', mock_list_terms)
        monkeypatch.setattr(entrylinks_export, '_resolve_regions_for_glossary', mock_resolve)
        monkeypatch.setattr(entrylinks_export, 'fetch_all_entry_links', mock_fetch_all)
        monkeypatch.setattr(entrylinks_export, 'convert_entry_links_to_rows', mock_convert)
        monkeypatch.setattr(entrylinks_export, '_write_entry_links_to_sheet', mock_write)
        
        result = entrylinks_export.export_entry_links(
            'glossary/path', 'http://sheet', 'project'
        )
        
        assert result is True
        mock_convert.assert_called_once_with([{'name': 'links/1'}], 'project')
        assert mock_write.call_args[0][0] == rows[:1]

    def test_caches_the_listed_terms(self, monkeypatch):
        """The listed terms are cached before their entry links are fetched"""
        terms = [{'name': 'term1'}]
        mock_cache = MagicMock()

        monkeypatch.setattr(entrylinks_export.api_layer, 'authenticate_dataplex', MagicMock())
        monkeypatch.setattr(entrylinks_export.sheet_utils, 'authenticate_sheets', MagicMock())
        monkeypatch.setattr(entrylinks_export.api_layer, 'initialize_locations_cache', MagicMock())
        monkeypatch.setattr(entrylinks_export.api_layer, 'list_glossary_terms', MagicMock(return_value=terms))
        monkeypatch.setattr(entrylinks_export, '_cache_listed_terms', mock_cache)
        monkeypatch.setattr(entrylinks_export, '_resolve_regions_for_glossary', MagicMock(return_value=[]))
        monkeypatch.setattr(entrylinks_export, '_clear_sheet_with_headers', MagicMock())

        entrylinks_export.export_entry_links('glossary/path', 'http://sheet', 'project')

        mock_cache.assert_called_once_with('glossary/path', terms, 'project')


class TestCacheListedTerms:
    """Test _cache_listed_terms: the display names of the listed terms need no API calls."""

    GLOSSARY = 'projects/my-proj/locations/global/glossaries/g1'
    TERM = {'name': GLOSSARY + '/terms/t1', 'displayName': 'Revenue'}
    # Entry links name the term's project by number.
    TERM_ENTRY = (
        'projects/123/locations/global/entryGroups/@dataplex/entries/'
        'projects/123/locations/global/glossaries/g1/terms/t1'
    )

    def setup_method(self):
        entrylinks_export.api_layer.clear_caches()

    def test_resolving_a_linked_term_needs_no_term_or_project_calls(self, monkeypatch):
        api_layer = entrylinks_export.api_layer
        project_info = Mock(return_value={'name': 'projects/123', 'projectId': 'my-proj'})
        monkeypatch.setattr(api_layer, '_fetch_project_info', project_info)
        service = MagicMock()
        service.projects().locations().glossaries().get().execute.return_value = {'displayName': 'Sales'}
        terms_get = service.projects().locations().glossaries().terms().get

        entrylinks_export._cache_listed_terms(self.GLOSSARY, [self.TERM], 'billing-proj')
        identifier = api_layer.resolve_term_entry_to_display_identifier(service, self.TERM_ENTRY, 'billing-proj')

        assert identifier == 'my-proj.global.Sales.Revenue'
        terms_get.assert_not_called()
        project_info.assert_called_once_with('my-proj', 'billing-proj')

    def test_caches_terms_by_project_id_when_project_number_cannot_be_read(self, monkeypatch):
        api_layer = entrylinks_export.api_layer
        monkeypatch.setattr(
            api_layer, '_fetch_project_info', Mock(side_effect=entrylinks_export.error.DataplexAPIError('denied'))
        )
        service = MagicMock()

        entrylinks_export._cache_listed_terms(self.GLOSSARY, [self.TERM], 'billing-proj')

        assert api_layer.get_term(service, self.TERM['name'])['displayName'] == 'Revenue'
        service.projects().locations().glossaries().terms().get.assert_not_called()

    def test_ignores_glossary_names_without_a_project(self, monkeypatch):
        mock_cache = MagicMock()
        monkeypatch.setattr(entrylinks_export.api_layer, 'cache_glossary_terms', mock_cache)

        entrylinks_export._cache_listed_terms('glossary/path', [self.TERM], 'billing-proj')

        mock_cache.assert_not_called()


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
