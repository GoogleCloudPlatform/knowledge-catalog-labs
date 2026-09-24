"""
Unit tests for api_layer.py

Test coverage:
- Entry ID parsing (_parse_entry_id_components)
- Entry links pagination (_fetch_entry_links_page)
- Authentication (authenticate_dataplex)
- Location management (list_supported_locations, resolve_regions_to_query)
- Glossary operations (list_glossary_terms, get_glossary)
- Entry operations (lookup_entry_links_for_term)
"""

import sys
import socket
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call, PropertyMock
import pytest
from googleapiclient.errors import HttpError

# Import the module
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from utils import api_layer


# ============================================================================
# ENTRY ID PARSING TESTS
# ============================================================================

class TestParseEntryIdComponents:
    """Test _parse_entry_id_components function"""
    
    def test_parses_standard_entry_id(self):
        """Parse standard entry ID format - returns tuple (project, location, entry_group, entry_id)"""
        entry_id = 'projects/my-project/locations/us-central1/entryGroups/my-group/entries/my-entry'
        
        project, location, entry_group, entry_id_parsed = api_layer.parse_entry_name(entry_id)
        
        assert project == 'my-project'
        assert location == 'us-central1'
        assert entry_group == 'my-group'
        assert entry_id_parsed == 'my-entry'
    
    def test_parses_global_location(self):
        """Parse entry with global location"""
        entry_id = 'projects/proj/locations/global/entryGroups/eg/entries/e'
        
        project, location, entry_group, entry_id_parsed = api_layer.parse_entry_name(entry_id)
        
        assert location == 'global'
        assert entry_group == 'eg'
        assert entry_id_parsed == 'e'
    
    def test_handles_special_characters(self):
        """Parse entry with special characters in names"""
        entry_id = 'projects/my-project-123/locations/us-west1/entryGroups/group_1/entries/entry_a-b'
        
        project, location, entry_group, entry_id_parsed = api_layer.parse_entry_name(entry_id)
        
        assert project == 'my-project-123'
        assert location == 'us-west1'
        assert entry_group == 'group_1'
        assert entry_id_parsed == 'entry_a-b'
    
    def test_raises_on_invalid_format(self):
        """Invalid format should raise InvalidEntryIdFormatError"""
        from utils.error import InvalidEntryIdFormatError
        
        with pytest.raises(InvalidEntryIdFormatError):
            api_layer.parse_entry_name('invalid-format')
    
    def test_raises_on_empty_string(self):
        """Empty string should raise InvalidEntryIdFormatError"""
        from utils.error import InvalidEntryIdFormatError
        
        with pytest.raises(InvalidEntryIdFormatError):
            api_layer.parse_entry_name('')


# ============================================================================
# ENTRY LINKS PAGINATION TESTS
# ============================================================================

class TestFetchEntryLinksPage:
    """Test _fetch_entry_links_page function"""
    
    def test_fetches_single_page(self, monkeypatch):
        """Fetch single page of results"""
        mock_api_response = {
            'json': {
                'entryLinks': [{'name': 'link1'}, {'name': 'link2'}]
            },
            'error_msg': None
        }
        monkeypatch.setattr(api_layer, 'fetch_api_response', lambda **kwargs: mock_api_response)
        
        entry_links, next_token, error_msg = api_layer._fetch_entry_links_page(
            'projects/p/locations/us/entryGroups/eg/entries/e', 'p', 'us', 'billing-project'
        )
        
        assert len(entry_links) == 2
        assert error_msg is None
    
    def test_handles_empty_response(self, monkeypatch):
        """Handle empty response from API"""
        mock_api_response = {
            'json': {},
            'error_msg': None
        }
        monkeypatch.setattr(api_layer, 'fetch_api_response', lambda **kwargs: mock_api_response)
        
        entry_links, next_token, error_msg = api_layer._fetch_entry_links_page(
            'projects/p/locations/us/entryGroups/eg/entries/e', 'p', 'us', 'billing-project'
        )
        
        assert entry_links == []
        assert next_token is None
        assert error_msg is None


# ============================================================================
# AUTHENTICATION TESTS
# ============================================================================

class TestAuthenticateDataplex:
    """Test authenticate_dataplex function"""
    
    def test_returns_client_on_success(self, monkeypatch):
        """Should return API client on successful auth"""
        mock_credentials = MagicMock()
        mock_service = MagicMock()
        
        monkeypatch.setattr(api_layer, 'default', lambda scopes: (mock_credentials, 'project'))
        monkeypatch.setattr(api_layer, 'build', lambda *args, **kwargs: mock_service)
        
        result = api_layer.authenticate_dataplex()
        
        assert result is not None
    
    def test_raises_on_auth_failure(self, monkeypatch):
        """Should raise on authentication failure"""
        from utils.error import DataplexAPIError
        
        def raise_error(scopes):
            raise Exception("Auth failed")
        
        monkeypatch.setattr(api_layer, 'default', raise_error)
        
        with pytest.raises(DataplexAPIError):
            api_layer.authenticate_dataplex()


# ============================================================================
# GLOSSARY OPERATIONS TESTS
# ============================================================================

class TestListGlossaryTerms:
    """Test list_glossary_terms function"""
    
    def test_lists_terms_successfully(self, monkeypatch):
        """Should list glossary terms"""
        mock_terms = [{'name': 'term1'}, {'name': 'term2'}]
        mock_service = MagicMock()
        mock_service.projects().locations().glossaries().terms().list().execute.return_value = {
            'terms': mock_terms
        }
        mock_service.projects().locations().glossaries().terms().list_next.return_value = None
        
        result = api_layer.list_glossary_terms(mock_service, 'projects/p/locations/l/glossaries/g')
        
        assert len(result) == 2
    
    def test_handles_empty_glossary(self, monkeypatch):
        """Should handle glossary with no terms"""
        mock_service = MagicMock()
        mock_service.projects().locations().glossaries().terms().list().execute.return_value = {}
        mock_service.projects().locations().glossaries().terms().list_next.return_value = None
        
        result = api_layer.list_glossary_terms(mock_service, 'projects/p/locations/l/glossaries/g')
        
        assert result == []
    
    def test_paginates_through_results(self, monkeypatch):
        """Should handle paginated results"""
        mock_service = MagicMock()
        
        # First page with nextPageToken
        mock_service.projects().locations().glossaries().terms().list().execute.return_value = {
            'terms': [{'name': 'term1'}],
            'nextPageToken': 'token123'
        }
        # Mock list_next to return None (no more pages)
        mock_service.projects().locations().glossaries().terms().list_next.return_value = None
        
        result = api_layer.list_glossary_terms(mock_service, 'projects/p/locations/l/glossaries/g')
        
        # Should have terms from first page
        assert len(result) >= 1


# ============================================================================
# ENTRY OPERATIONS TESTS
# ============================================================================

class TestLookupEntryLinksForTerm:
    """Test lookup_entry_links_for_term function"""
    
    def test_returns_entry_links(self, monkeypatch):
        """Should return entry links for term"""
        mock_response = {
            'json': {
                'entryLinks': [
                    {'name': 'link1', 'entryLinkType': 'RELATED'},
                    {'name': 'link2', 'entryLinkType': 'DEFINITION'}
                ]
            }
        }
        
        monkeypatch.setattr(api_layer, 'fetch_api_response', lambda **kwargs: mock_response)
        
        entry_id = 'projects/test-project/locations/us-central1/entryGroups/eg/entries/e'
        result = api_layer.lookup_entry_links_for_term(entry_id, 'test-project')
        
        assert result is not None
    
    def test_handles_invalid_entry_id(self):
        """Should return None for invalid entry ID (exception is caught internally)"""
        result = api_layer.lookup_entry_links_for_term('invalid-entry', 'project')
        
        assert result is None


# ============================================================================
# REGION RESOLUTION TESTS
# ============================================================================

class TestResolveRegionsToQuery:
    """Test resolve_regions_to_query function"""
    
    def test_global_returns_all_locations(self, monkeypatch):
        """Global location should return all available locations"""
        all_locations = ['global', 'us', 'eu', 'us-central1', 'eu-west1']
        
        monkeypatch.setattr(api_layer, 'list_supported_locations', lambda p: all_locations)
        
        result = api_layer.resolve_regions_to_query('global', 'my-project')
        
        assert result == all_locations
    
    def test_global_excludes_excluded_locations(self, monkeypatch):
        """Global location should filter out EXCLUDED_LOCATIONS"""
        all_locations = ['global', 'us', 'eu', 'asia-southeast3', 'us-central1']
        
        monkeypatch.setattr(api_layer, 'list_supported_locations', lambda p: all_locations)
        
        result = api_layer.resolve_regions_to_query('global', 'my-project')
        
        assert 'asia-southeast3' not in result
        assert result == ['global', 'us', 'eu', 'us-central1']
    
    def test_regional_returns_single_region(self, monkeypatch):
        """Regional location should return only that region"""
        result = api_layer.resolve_regions_to_query('us-central1', 'my-project')
        
        assert result == ['us-central1']
    
    def test_multiregion_returns_single_location(self, monkeypatch):
        """Multi-region (us, eu) should return just that location"""
        result = api_layer.resolve_regions_to_query('us', 'my-project')
        
        assert result == ['us']


# ============================================================================
# LOCATION FUNCTIONS TESTS
# ============================================================================

class TestInitializeLocationsCache:
    """Test initialize_locations_cache function"""
    
    def test_initializes_cache(self, monkeypatch):
        """Should initialize locations cache with all locations"""
        all_locations = ['global', 'us', 'eu', 'us-central1', 'eu-west1']
        
        monkeypatch.setattr(api_layer, 'list_supported_locations', lambda p: all_locations)
        
        result = api_layer.initialize_locations_cache('my-project')
        
        assert result == all_locations


# ============================================================================
# INTEGRATION-LIKE TESTS
# ============================================================================

class TestEndToEndFlows:
    """Test end-to-end API flows"""
    
    def test_list_terms_workflow(self, monkeypatch):
        """Test listing terms workflow"""
        mock_terms = [{'name': 'projects/p/locations/l/glossaries/g/terms/t1'}]
        
        mock_service = MagicMock()
        mock_service.projects().locations().glossaries().terms().list().execute.return_value = {
            'terms': mock_terms
        }
        mock_service.projects().locations().glossaries().terms().list_next.return_value = None
        
        # List terms
        terms = api_layer.list_glossary_terms(mock_service, 'projects/p/locations/l/glossaries/g')
        
        assert len(terms) == 1


# ============================================================================
# RESOLUTION & LOOKUP TESTS
# ============================================================================

class TestGetGlossaryAndTerm:
    """Test get_glossary and get_term functions with caching."""

    def setup_method(self):
        api_layer.clear_caches()

    def test_get_glossary_fetches_and_caches(self):
        mock_service = MagicMock()
        mock_get = mock_service.projects().locations().glossaries().get
        mock_get().execute.return_value = {
            'name': 'projects/p/locations/l/glossaries/g1',
            'displayName': 'Sales Glossary'
        }
        mock_get.reset_mock()

        glossary = api_layer.get_glossary(mock_service, 'projects/p/locations/l/glossaries/g1')
        assert glossary['displayName'] == 'Sales Glossary'
        # Second call should use cache
        glossary2 = api_layer.get_glossary(mock_service, 'projects/p/locations/l/glossaries/g1')
        assert glossary2['displayName'] == 'Sales Glossary'
        assert mock_get.call_count == 1

    def test_get_term_fetches_and_caches(self):
        mock_service = MagicMock()
        mock_get = mock_service.projects().locations().glossaries().terms().get
        mock_get().execute.return_value = {
            'name': 'projects/p/locations/l/glossaries/g1/terms/t1',
            'displayName': 'Order Total'
        }
        mock_get.reset_mock()

        term = api_layer.get_term(mock_service, 'projects/p/locations/l/glossaries/g1/terms/t1')
        assert term['displayName'] == 'Order Total'
        # Second call uses cache
        term2 = api_layer.get_term(mock_service, 'projects/p/locations/l/glossaries/g1/terms/t1')
        assert term2['displayName'] == 'Order Total'
        assert mock_get.call_count == 1


class TestListGlossaries:
    """Test list_glossaries function."""

    def setup_method(self):
        api_layer.clear_caches()

    def test_lists_and_caches_glossaries(self):
        mock_service = MagicMock()
        mock_list = mock_service.projects().locations().glossaries().list
        mock_list().execute.return_value = {
            'glossaries': [
                {'name': 'projects/p/locations/global/glossaries/g1', 'displayName': 'Glossary 1'},
                {'name': 'projects/p/locations/global/glossaries/g2', 'displayName': 'Glossary 2'}
            ]
        }
        mock_service.projects().locations().glossaries().list_next.return_value = None
        mock_list.reset_mock()

        glossaries = api_layer.list_glossaries(mock_service, 'projects/p/locations/global')
        assert len(glossaries) == 2
        assert glossaries[0]['displayName'] == 'Glossary 1'

        # Cached call
        cached = api_layer.list_glossaries(mock_service, 'projects/p/locations/global')
        assert len(cached) == 2
        assert mock_list.call_count == 1



class TestResolveTermEntryToDisplayIdentifier:
    """Test resolve_term_entry_to_display_identifier."""

    def setup_method(self):
        api_layer.clear_caches()

    def test_resolves_dataplex_entry_to_display_identifier(self):
        mock_service = MagicMock()
        mock_service.projects().locations().glossaries().get().execute.return_value = {
            'name': 'projects/my-proj/locations/global/glossaries/sales_glossary',
            'displayName': 'Sales Glossary'
        }
        mock_service.projects().locations().glossaries().terms().get().execute.return_value = {
            'name': 'projects/my-proj/locations/global/glossaries/sales_glossary/terms/revenue_term',
            'displayName': 'Revenue'
        }

        entry_name = (
            'projects/my-proj/locations/global/entryGroups/@dataplex/entries/'
            'projects/my-proj/locations/global/glossaries/sales_glossary/terms/revenue_term'
        )
        identifier = api_layer.resolve_term_entry_to_display_identifier(mock_service, entry_name)
        assert identifier == 'my-proj.global.Sales Glossary.Revenue'


class TestGetEntryFQN:
    """Test get_entry_fqn function."""

    def setup_method(self):
        api_layer.clear_caches()

    def test_fetches_and_caches_fqn(self, monkeypatch):
        mock_service = MagicMock()
        monkeypatch.setattr(
            api_layer, 'lookup_entry',
            lambda s, entry, parent: {'name': entry, 'fullyQualifiedName': 'bigquery:my-proj.ds.table1'}
        )
        entry_name = 'projects/my-proj/locations/us/entryGroups/@bigquery/entries/entry1'
        fqn = api_layer.get_entry_fqn(mock_service, entry_name, 'user-proj')
        assert fqn == 'bigquery:my-proj.ds.table1'

        # Second call uses cache
        fqn2 = api_layer.get_entry_fqn(mock_service, entry_name, 'user-proj')
        assert fqn2 == 'bigquery:my-proj.ds.table1'


class TestLookupTermByDisplayIdentifier:
    """Test lookup_term_by_display_identifier."""

    def setup_method(self):
        api_layer.clear_caches()

    def test_resolves_valid_display_identifier(self, monkeypatch):
        mock_service = MagicMock()
        monkeypatch.setattr(api_layer, 'get_project_number', lambda p, u=None: p)
        monkeypatch.setattr(
            api_layer, 'list_glossaries',
            lambda s, parent: [{'name': 'projects/my-proj/locations/global/glossaries/g1', 'displayName': 'Sales Glossary'}]
        )
        monkeypatch.setattr(
            api_layer, 'list_glossary_terms',
            lambda s, glossary_name: [
                {'name': 'projects/my-proj/locations/global/glossaries/g1/terms/t1', 'displayName': 'Order Total'}
            ]
        )

        entry_name = api_layer.lookup_term_by_display_identifier(
            mock_service, 'my-proj.global.Sales Glossary.Order Total'
        )
        expected = (
            'projects/my-proj/locations/global/entryGroups/@dataplex/entries/'
            'projects/my-proj/locations/global/glossaries/g1/terms/t1'
        )
        assert entry_name == expected

    def test_raises_when_glossary_not_found(self, monkeypatch):
        from utils.error import GlossaryNotFoundError
        mock_service = MagicMock()
        monkeypatch.setattr(api_layer, 'list_glossaries', lambda s, parent: [])

        with pytest.raises(GlossaryNotFoundError):
            api_layer.lookup_term_by_display_identifier(
                mock_service, 'my-proj.global.NonExistent.Order Total'
            )

    def test_raises_when_term_not_found(self, monkeypatch):
        from utils.error import TermNotFoundError
        mock_service = MagicMock()
        monkeypatch.setattr(
            api_layer, 'list_glossaries',
            lambda s, parent: [{'name': 'projects/my-proj/locations/global/glossaries/g1', 'displayName': 'Sales'}]
        )
        monkeypatch.setattr(api_layer, 'list_glossary_terms', lambda s, g: [])

        with pytest.raises(TermNotFoundError):
            api_layer.lookup_term_by_display_identifier(
                mock_service, 'my-proj.global.Sales.MissingTerm'
            )

    def test_resolves_duplicate_display_name_using_term_id(self, monkeypatch):
        mock_service = MagicMock()
        monkeypatch.setattr(api_layer, 'get_project_number', lambda p, u=None: p)
        monkeypatch.setattr(
            api_layer, 'list_glossaries',
            lambda s, parent: [{'name': 'projects/my-proj/locations/global/glossaries/g1', 'displayName': 'Sales'}]
        )
        monkeypatch.setattr(
            api_layer, 'list_glossary_terms',
            lambda s, g: [
                {'name': 'projects/my-proj/locations/global/glossaries/g1/terms/status_order', 'displayName': 'Status'},
                {'name': 'projects/my-proj/locations/global/glossaries/g1/terms/status_customer', 'displayName': 'Status'},
            ]
        )

        entry_name = api_layer.lookup_term_by_display_identifier(
            mock_service, 'my-proj.global.Sales.Status', term_id='status_customer'
        )
        assert entry_name.endswith('/glossaries/g1/terms/status_customer')

    def test_raises_ambiguous_term_error_when_term_id_omitted_on_duplicate_display_names(self, monkeypatch):
        from utils.error import AmbiguousTermError
        mock_service = MagicMock()
        monkeypatch.setattr(api_layer, 'get_project_number', lambda p, u=None: p)
        monkeypatch.setattr(
            api_layer, 'list_glossaries',
            lambda s, parent: [{'name': 'projects/my-proj/locations/global/glossaries/g1', 'displayName': 'Sales'}]
        )
        monkeypatch.setattr(
            api_layer, 'list_glossary_terms',
            lambda s, g: [
                {'name': 'projects/my-proj/locations/global/glossaries/g1/terms/status_order', 'displayName': 'Status'},
                {'name': 'projects/my-proj/locations/global/glossaries/g1/terms/status_customer', 'displayName': 'Status'},
            ]
        )

        with pytest.raises(AmbiguousTermError):
            api_layer.lookup_term_by_display_identifier(
                mock_service, 'my-proj.global.Sales.Status'
            )

    def test_resolves_glossary_display_name_containing_dots(self, monkeypatch):
        mock_service = MagicMock()
        monkeypatch.setattr(api_layer, 'get_project_number', lambda p, u=None: p)
        monkeypatch.setattr(
            api_layer, 'list_glossaries',
            lambda s, parent: [{'name': 'projects/my-proj/locations/global/glossaries/g1', 'displayName': 'Finance v2.0'}]
        )
        monkeypatch.setattr(
            api_layer, 'list_glossary_terms',
            lambda s, g: [
                {'name': 'projects/my-proj/locations/global/glossaries/g1/terms/net_rev', 'displayName': 'Net.Revenue'}
            ]
        )

        entry_name = api_layer.lookup_term_by_display_identifier(
            mock_service, 'my-proj.global.Finance v2.0.Net.Revenue', term_id='net_rev'
        )
        assert entry_name.endswith('/glossaries/g1/terms/net_rev')


class TestLookupEntryByFQN:
    """Test lookup_entry_by_fqn."""

    def setup_method(self):
        api_layer.clear_caches()

    def test_resolves_and_caches_entry(self, monkeypatch):
        mock_service = MagicMock()
        monkeypatch.setattr(api_layer, 'get_project_number', lambda p, u=None: p)
        mock_entry = {
            'name': 'projects/my-proj/locations/us/entryGroups/@bigquery/entries/orders',
            'fullyQualifiedName': 'bigquery:my-proj.ds.orders'
        }
        mock_service.projects().locations().entryGroups().entries().get().execute.return_value = mock_entry

        entry = api_layer.lookup_entry_by_fqn(mock_service, 'bigquery:my-proj.ds.orders', 'user-proj')
        assert entry['name'] == 'projects/my-proj/locations/us/entryGroups/@bigquery/entries/orders'
        # Cached second call
        entry2 = api_layer.lookup_entry_by_fqn(mock_service, 'bigquery:my-proj.ds.orders', 'user-proj')
        assert entry2['name'] == 'projects/my-proj/locations/us/entryGroups/@bigquery/entries/orders'

    def test_resolves_custom_fqn_via_search_entries(self, monkeypatch):
        mock_service = MagicMock()
        monkeypatch.setattr(api_layer, 'get_project_number', lambda p, u=None: p)
        mock_entry = {
            'name': 'projects/my-proj/locations/us-central1/entryGroups/my-eg/entries/custom-01',
            'fullyQualifiedName': 'custom:my_ds.custom_01'
        }
        mock_service.projects().locations().searchEntries().execute.return_value = {
            'results': [{'dataplexEntry': mock_entry}]
        }

        entry = api_layer.lookup_entry_by_fqn(mock_service, 'custom:my_ds.custom_01', 'user-proj')
        assert entry['name'] == 'projects/my-proj/locations/us-central1/entryGroups/my-eg/entries/custom-01'
        assert entry['fullyQualifiedName'] == 'custom:my_ds.custom_01'
        # Cached second call
        entry2 = api_layer.lookup_entry_by_fqn(mock_service, 'custom:my_ds.custom_01', 'user-proj')
        assert entry2['name'] == 'projects/my-proj/locations/us-central1/entryGroups/my-eg/entries/custom-01'

    def test_raises_when_fqn_not_found_and_negatively_caches(self, monkeypatch):
        from utils.error import EntryFQNNotFoundError
        mock_service = MagicMock()
        monkeypatch.setattr(api_layer, 'get_project_number', lambda p, u=None: p)
        call_count = {'search': 0}

        def fake_search(*args, **kwargs):
            call_count['search'] += 1
            mock_req = MagicMock()
            mock_req.execute.return_value = {'results': []}
            return mock_req

        monkeypatch.setattr(api_layer, 'list_supported_locations', lambda p, s: ['us'])
        mock_service.projects().locations().entryGroups().entries().get().execute.side_effect = Exception("Not found")
        mock_service.projects().locations().searchEntries.side_effect = fake_search

        with pytest.raises(EntryFQNNotFoundError):
            api_layer.lookup_entry_by_fqn(mock_service, 'bigquery:missing.ds.tbl', 'user-proj')
        first_search_calls = call_count['search']
        assert first_search_calls == 1

        # Second lookup should immediately raise from negative cache without re-probing
        with pytest.raises(EntryFQNNotFoundError):
            api_layer.lookup_entry_by_fqn(mock_service, 'bigquery:missing.ds.tbl', 'user-proj')
        assert call_count['search'] == first_search_calls
