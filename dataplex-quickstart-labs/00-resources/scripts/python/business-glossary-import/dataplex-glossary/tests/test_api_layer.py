"""
Unit tests for api_layer.py

Test coverage:
- Entry ID parsing (_parse_entry_id_components)
- Entry links pagination (_fetch_entry_links_page)
- Authentication (authenticate_dataplex)
- Location management (list_supported_locations, resolve_regions_to_query)
- Glossary operations (list_glossary_terms, get_glossary)
- Entry operations (lookup_entry_links_for_term)
- Resolution for the entry links sheets (resolve_term_entry_to_display_identifier, get_entry_fqn,
  lookup_term_by_display_identifier, lookup_entry_by_fqn)
- Thread-safe caching (_get_or_fetch)
"""

import sys
import socket
import threading
import time
import traceback
from pathlib import Path
from unittest.mock import ANY, MagicMock, Mock, patch, call, PropertyMock
import httplib2
import pytest
from googleapiclient.errors import HttpError

# Import the module
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from utils import api_layer
from utils.error import (
    AmbiguousTermError,
    DataplexAPIError,
    EntryFQNNotFoundError,
    GlossaryNotFoundError,
    InvalidTermIdentifierError,
    TermNameMismatchError,
    TermNotFoundError,
    TransientAPIError,
)


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


class TestCacheGlossaryTerms:
    """Test cache_glossary_terms: get_term serves listed terms without fetching them."""

    TERM = {'name': 'projects/my-proj/locations/global/glossaries/g1/terms/t1', 'displayName': 'Revenue'}

    def setup_method(self):
        api_layer.clear_caches()

    @staticmethod
    def _terms_get(service):
        return service.projects().locations().glossaries().terms().get

    @pytest.mark.parametrize('project', ['my-proj', '123'])
    def test_serves_listed_terms_by_project_id_or_number(self, project):
        service = MagicMock()
        api_layer.cache_glossary_terms([self.TERM], 'my-proj', '123')

        term = api_layer.get_term(service, f'projects/{project}/locations/global/glossaries/g1/terms/t1')

        assert term['displayName'] == 'Revenue'
        self._terms_get(service).assert_not_called()

    def test_serves_terms_listed_with_project_number_by_project_id(self):
        service = MagicMock()
        term = dict(self.TERM, name='projects/123/locations/global/glossaries/g1/terms/t1')
        api_layer.cache_glossary_terms([term], 'my-proj', '123')

        assert api_layer.get_term(service, self.TERM['name'])['displayName'] == 'Revenue'
        self._terms_get(service).assert_not_called()

    def test_still_fetches_terms_that_were_not_listed(self):
        service = MagicMock()
        self._terms_get(service)().execute.return_value = {'displayName': 'Other'}
        self._terms_get(service).reset_mock()
        api_layer.cache_glossary_terms([self.TERM], 'my-proj', '123')

        term = api_layer.get_term(service, 'projects/my-proj/locations/global/glossaries/g2/terms/t1')

        assert term['displayName'] == 'Other'
        self._terms_get(service).assert_called_once()

    def test_skips_terms_without_a_term_resource_name(self):
        api_layer.cache_glossary_terms([{'name': 'term1'}, {}], 'my-proj', '123')

        assert api_layer._term_cache == {}


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

    ENTRY_NAME = (
        'projects/my-proj/locations/global/entryGroups/@dataplex/entries/'
        'projects/my-proj/locations/global/glossaries/sales_glossary/terms/revenue_term'
    )

    def setup_method(self):
        api_layer.clear_caches()

    def _service(self, glossaries):
        mock_service = MagicMock()
        mock_service.projects().locations().glossaries().get().execute.return_value = {
            'name': 'projects/my-proj/locations/global/glossaries/sales_glossary',
            'displayName': 'Sales Glossary'
        }
        mock_service.projects().locations().glossaries().terms().get().execute.return_value = {
            'name': 'projects/my-proj/locations/global/glossaries/sales_glossary/terms/revenue_term',
            'displayName': 'Revenue'
        }
        mock_service.projects().locations().glossaries().list().execute.return_value = {'glossaries': glossaries}
        mock_service.projects().locations().glossaries().list_next.return_value = None
        return mock_service

    def test_resolves_dataplex_entry_to_display_identifier(self):
        mock_service = self._service([
            {'name': 'projects/my-proj/locations/global/glossaries/sales_glossary', 'displayName': 'Sales Glossary'},
            {'name': 'projects/my-proj/locations/global/glossaries/hr', 'displayName': 'HR'},
        ])

        identifier = api_layer.resolve_term_entry_to_display_identifier(mock_service, self.ENTRY_NAME)
        assert identifier == 'my-proj.global.Sales Glossary.Revenue'

    def test_uses_glossary_id_when_another_glossary_has_the_same_display_name(self):
        """The import can't tell glossaries with the same display name apart, so the export uses the ID"""
        mock_service = self._service([
            {'name': 'projects/my-proj/locations/global/glossaries/sales_glossary', 'displayName': 'Sales Glossary'},
            {'name': 'projects/my-proj/locations/global/glossaries/sales_glossary_v2', 'displayName': ' Sales Glossary'},
        ])

        identifier = api_layer.resolve_term_entry_to_display_identifier(mock_service, self.ENTRY_NAME)
        assert identifier == 'my-proj.global.sales_glossary.Revenue'

    def test_keeps_display_name_when_glossaries_cannot_be_listed(self):
        mock_service = self._service([])
        mock_service.projects().locations().glossaries().list().execute.side_effect = _http_error(403)

        identifier = api_layer.resolve_term_entry_to_display_identifier(mock_service, self.ENTRY_NAME)
        assert identifier == 'my-proj.global.Sales Glossary.Revenue'


class TestGetEntryFQN:
    """Test get_entry_fqn."""

    ENTRY = 'projects/my-proj/locations/us/entryGroups/@bigquery/entries/entry1'
    FQN = 'bigquery:my-proj.ds.table1'

    def setup_method(self):
        api_layer.clear_caches()

    def test_fetches_and_caches_fqn(self, monkeypatch):
        mock_service = MagicMock()
        lookup = Mock(return_value={'name': self.ENTRY, 'fullyQualifiedName': self.FQN})
        monkeypatch.setattr(api_layer, 'lookup_entry', lookup)

        assert api_layer.get_entry_fqn(mock_service, self.ENTRY, 'user-proj') == self.FQN
        assert api_layer.get_entry_fqn(mock_service, self.ENTRY, 'user-proj') == self.FQN
        lookup.assert_called_once_with(mock_service, self.ENTRY, 'projects/user-proj/locations/us')

    def test_looks_up_once_under_user_project(self, monkeypatch):
        """The request's project is only billed, so a failed read is not retried under the entry's project."""
        lookup = Mock(return_value=None)
        warnings = []
        monkeypatch.setattr(api_layer, 'lookup_entry', lookup)
        monkeypatch.setattr(api_layer.logger, 'warning', warnings.append)

        assert api_layer.get_entry_fqn(MagicMock(), self.ENTRY, 'user-proj') == self.ENTRY
        assert [c.args[2] for c in lookup.call_args_list] == ['projects/user-proj/locations/us']
        assert warnings == [
            f"Could not read entry {self.ENTRY} (not found, or no permission to read it); "
            "writing the entry name instead of its FQN."
        ]

    def test_uses_entry_project_without_user_project(self, monkeypatch):
        lookup = Mock(return_value={'name': self.ENTRY, 'fullyQualifiedName': self.FQN})
        monkeypatch.setattr(api_layer, 'lookup_entry', lookup)

        assert api_layer.get_entry_fqn(MagicMock(), self.ENTRY, '') == self.FQN
        lookup.assert_called_once_with(ANY, self.ENTRY, 'projects/my-proj/locations/us')

    def test_returns_entry_name_and_warns_once_without_fqn(self, monkeypatch):
        lookup = Mock(return_value={'name': self.ENTRY})
        warnings = []
        monkeypatch.setattr(api_layer, 'lookup_entry', lookup)
        monkeypatch.setattr(api_layer.logger, 'warning', warnings.append)

        assert api_layer.get_entry_fqn(MagicMock(), self.ENTRY, 'user-proj') == self.ENTRY
        assert api_layer.get_entry_fqn(MagicMock(), self.ENTRY, 'user-proj') == self.ENTRY
        lookup.assert_called_once()  # The second call is cached.
        assert warnings == [f"Entry {self.ENTRY} has no fullyQualifiedName; writing the entry name instead."]

    @pytest.mark.parametrize('entry_id, fqn', [
        ('bigquery.googleapis.com/projects/my-proj/datasets/Sales_ds/tables/orders-2024', 'bigquery:my-proj.Sales_ds.orders-2024'),
        ('bigquery.googleapis.com/projects/my-proj/datasets/Sales_ds', 'bigquery:my-proj.Sales_ds'),
    ])
    def test_builds_fqn_of_plain_bigquery_entries_without_lookup(self, monkeypatch, entry_id, fqn):
        lookup = Mock()
        monkeypatch.setattr(api_layer, 'lookup_entry', lookup)
        entry_name = f'projects/123/locations/us/entryGroups/@bigquery/entries/{entry_id}'

        assert api_layer.get_entry_fqn(MagicMock(), entry_name, 'user-proj') == fqn
        lookup.assert_not_called()

    @pytest.mark.parametrize('entry_group, entry_id', [
        ('@bigquery', 'bigquery.googleapis.com/projects/my-proj/datasets/ds/tables/events_@BigQueryDateShardedTable'),
        ('@bigquery', 'bigquery.googleapis.com/projects/my-proj/datasets/ds/models/churn'),
        ('@bigquery', 'bigquery.googleapis.com/projects/my-proj/datasets/ds/routines/clean'),
        ('@bigquery', 'bigquery.googleapis.com/projects/my-proj/datasets/ds/tables/order items'),
        ('@bigquery', 'bigquery.googleapis.com/projects/my-proj/datasets/ds/tables/café'),
        ('@bigquery', 'bigquery.googleapis.com/projects/example.com:my-proj/datasets/ds/tables/t'),
        ('@bigquery', 'bigquery.googleapis.com/projects/123/datasets/ds/tables/t'),
        ('my-group', 'bigquery.googleapis.com/projects/my-proj/datasets/ds/tables/t'),
    ])
    def test_looks_up_fqn_of_other_entries(self, monkeypatch, entry_group, entry_id):
        entry_name = f'projects/my-proj/locations/us/entryGroups/{entry_group}/entries/{entry_id}'
        lookup = Mock(return_value={'name': entry_name, 'fullyQualifiedName': 'looked:up'})
        monkeypatch.setattr(api_layer, 'lookup_entry', lookup)

        assert api_layer.get_entry_fqn(MagicMock(), entry_name, 'user-proj') == 'looked:up'
        lookup.assert_called_once()


def _term_entry(glossary_id, term_id, project='123'):
    """Entry name of a term of project my-proj (number 123) in location global."""
    return (
        f'projects/{project}/locations/global/entryGroups/@dataplex/entries/'
        f'projects/{project}/locations/global/glossaries/{glossary_id}/terms/{term_id}'
    )


class TestLookupTermByDisplayIdentifier:
    """Test lookup_term_by_display_identifier."""

    def setup_method(self):
        api_layer.clear_caches()

    @pytest.fixture
    def mock_glossaries(self, monkeypatch):
        """Returns a function that serves {glossary ID: (display name, {term ID: term display name})} in my-proj/global."""
        def install(glossaries):
            parent = 'projects/my-proj/locations/global'

            def list_glossaries(service, requested_parent):
                if requested_parent != parent:
                    return []
                return [
                    {'name': f'{parent}/glossaries/{glossary_id}', 'displayName': display_name}
                    for glossary_id, (display_name, _) in glossaries.items()
                ]

            def list_glossary_terms(service, glossary_name):
                _, terms = glossaries[glossary_name.split('/')[-1]]
                return [
                    {'name': f'{glossary_name}/terms/{term_id}', 'displayName': display_name}
                    for term_id, display_name in terms.items()
                ]

            monkeypatch.setattr(api_layer, 'get_project_number', lambda project_id, user_project='': '123')
            monkeypatch.setattr(api_layer, 'list_glossaries', list_glossaries)
            monkeypatch.setattr(api_layer, 'list_glossary_terms', list_glossary_terms)
        return install

    def lookup(self, identifier, term_id):
        return api_layer.lookup_term_by_display_identifier(MagicMock(), identifier, term_id=term_id)

    def test_resolves_term_by_glossary_display_name_and_term_id(self, mock_glossaries):
        mock_glossaries({'sales': ('Sales Glossary', {'order_total': 'Order Total'})})

        entry_name = self.lookup('my-proj.global.Sales Glossary.Order Total', 'order_total')

        assert entry_name == _term_entry('sales', 'order_total')
        assert api_layer.is_known_entry(entry_name)

    @pytest.mark.parametrize('term_id', ['', '   '])
    def test_resolves_term_by_display_name_without_term_id(self, mock_glossaries, term_id):
        mock_glossaries({'sales': ('Sales', {'order_total': 'Order Total', 'order_id': 'Order ID'})})

        assert self.lookup('my-proj.global.Sales.Order Total', term_id) == _term_entry('sales', 'order_total')

    def test_display_name_shared_by_several_terms_is_ambiguous(self, mock_glossaries):
        mock_glossaries({'sales': ('Sales', {'status_order': 'Status', 'status_customer': 'Status'})})

        with pytest.raises(AmbiguousTermError) as exc_info:
            self.lookup('my-proj.global.Sales.Status', '')
        assert 'matches more than one term (status_customer, status_order)' in str(exc_info.value)

    def test_display_name_match_is_case_sensitive(self, mock_glossaries):
        mock_glossaries({'sales': ('Sales', {'order_id': 'Order ID'})})

        with pytest.raises(TermNotFoundError) as exc_info:
            self.lookup('my-proj.global.Sales.order id', '')
        assert type(exc_info.value) is TermNotFoundError
        assert 'case-sensitive' in str(exc_info.value)

    def test_resolves_dotted_display_names_without_term_id(self, mock_glossaries):
        mock_glossaries({'finance': ('Finance v2.0', {'net_rev': 'Net.Revenue', 'rev': 'Revenue'})})

        assert self.lookup('my-proj.global.Finance v2.0.Net.Revenue', '') == _term_entry('finance', 'net_rev')

    def test_requires_term_id_without_term_display_name(self, mock_glossaries):
        mock_glossaries({'sales': ('Sales', {'order_total': 'Order Total'})})

        with pytest.raises(InvalidTermIdentifierError, match='no term display name'):
            self.lookup('my-proj.global.Sales', '')

    def test_term_id_selects_among_duplicate_term_display_names(self, mock_glossaries):
        mock_glossaries({'sales': ('Sales', {'status_order': 'Status', 'status_customer': 'Status'})})

        assert self.lookup('my-proj.global.Sales.Status', 'status_customer') == _term_entry('sales', 'status_customer')

    @pytest.mark.parametrize('finance_us_display_name', ['Finance.US', 'FINANCE.US'])
    def test_finds_term_in_glossary_whose_name_extends_another(self, mock_glossaries, finance_us_display_name):
        mock_glossaries({
            'finance': ('Finance', {'budget': 'Budget'}),
            'finance_us': (finance_us_display_name, {'revenue': 'Revenue'}),
        })

        assert self.lookup('my-proj.global.Finance.US.Revenue', 'revenue') == _term_entry('finance_us', 'revenue')

    @pytest.mark.parametrize('identifier, glossary_id', [
        ('my-proj.global.Finance.US.Revenue', 'finance_us'),
        ('my-proj.global.Finance.Revenue', 'finance'),
    ])
    def test_term_display_name_picks_glossary_when_both_have_term_id(self, mock_glossaries, identifier, glossary_id):
        mock_glossaries({
            'finance': ('Finance', {'revenue': 'Revenue'}),
            'finance_us': ('Finance.US', {'revenue': 'Revenue'}),
        })

        assert self.lookup(identifier, 'revenue') == _term_entry(glossary_id, 'revenue')

    def test_same_glossary_display_name_twice_is_ambiguous(self, mock_glossaries):
        mock_glossaries({
            'sales': ('Sales', {'order_id': 'Order ID'}),
            'sales_emea': ('Sales', {'order_id': 'Order ID'}),
        })

        with pytest.raises(AmbiguousTermError, match='Use the glossary ID'):
            self.lookup('my-proj.global.Sales.Order ID', 'order_id')

    @pytest.mark.parametrize('glossary_id', ['sales', 'sales_emea'])
    def test_glossary_id_selects_glossary(self, mock_glossaries, glossary_id):
        # 'sales' also matches the display name of both glossaries when case is ignored.
        mock_glossaries({
            'sales': ('Sales', {'order_id': 'Order ID'}),
            'sales_emea': ('Sales', {'order_id': 'Order ID'}),
        })

        assert self.lookup(f'my-proj.global.{glossary_id}.Order ID', 'order_id') == _term_entry(glossary_id, 'order_id')

    @pytest.mark.parametrize('glossary_name, glossary_id', [('Sales', 'sales_a'), ('sales', 'sales_b')])
    def test_exact_display_name_beats_case_insensitive_match(self, mock_glossaries, glossary_name, glossary_id):
        mock_glossaries({
            'sales_a': ('Sales', {'order_id': 'Order ID'}),
            'sales_b': ('sales', {'order_id': 'Order ID'}),
        })

        assert self.lookup(f'my-proj.global.{glossary_name}.Order ID', 'order_id') == _term_entry(glossary_id, 'order_id')

    def test_matches_glossary_name_and_term_id_ignoring_case(self, mock_glossaries):
        mock_glossaries({'sales': ('Sales', {'order_id': 'Order ID'})})

        assert self.lookup('my-proj.global.SALES.Order ID', 'ORDER_ID') == _term_entry('sales', 'order_id')

    @pytest.mark.parametrize('identifier, term_name', [
        ('my-proj.global.Sales.Order Number', 'Order Number'),
        ('my-proj.global.Sales.order id', 'order id'),  # Term display names are case-sensitive.
    ])
    def test_raises_when_term_display_name_does_not_match_term_id(self, mock_glossaries, identifier, term_name):
        mock_glossaries({'sales': ('Sales', {'order_id': 'Order ID'})})

        with pytest.raises(TermNameMismatchError) as exc_info:
            self.lookup(identifier, 'order_id')
        assert (exc_info.value.term_name, exc_info.value.term_id, exc_info.value.display_name) == (
            term_name, 'order_id', 'Order ID'
        )

    @pytest.mark.parametrize('identifier', ['my-proj.global.Sales.Order ID', 'my-proj.global.Sales'])
    def test_term_id_resolves_when_term_display_name_matches_or_is_omitted(self, mock_glossaries, identifier):
        mock_glossaries({'sales': ('Sales', {'order_id': 'Order ID'})})

        assert self.lookup(identifier, 'order_id') == _term_entry('sales', 'order_id')

    def test_resolves_display_names_containing_dots(self, mock_glossaries):
        mock_glossaries({'finance': ('Finance v2.0', {'net_rev': 'Net.Revenue'})})

        assert self.lookup('my-proj.global.Finance v2.0.Net.Revenue', 'net_rev') == _term_entry('finance', 'net_rev')

    @pytest.mark.parametrize('identifier', ['my-proj.global.Marketing.Order ID', 'other-proj.global.Sales.Order ID'])
    def test_raises_when_no_glossary_matches(self, mock_glossaries, identifier):
        mock_glossaries({'sales': ('Sales', {'order_id': 'Order ID'})})

        with pytest.raises(GlossaryNotFoundError):
            self.lookup(identifier, 'order_id')

    def test_raises_when_glossary_has_no_term_with_id(self, mock_glossaries):
        mock_glossaries({'sales': ('Sales', {'order_id': 'Order ID'})})

        with pytest.raises(TermNotFoundError) as exc_info:
            self.lookup('my-proj.global.Sales.Missing', 'missing')
        assert type(exc_info.value) is TermNotFoundError  # Not the AmbiguousTermError subclass.

    def test_keeps_project_id_when_project_number_is_unavailable(self, mock_glossaries, monkeypatch):
        mock_glossaries({'sales': ('Sales', {'order_id': 'Order ID'})})
        monkeypatch.setattr(api_layer, 'get_project_number', Mock(side_effect=DataplexAPIError('no access')))
        monkeypatch.setattr(api_layer.logger, 'warning', [].append)

        entry_name = self.lookup('my-proj.global.Sales.Order ID', 'order_id')

        assert entry_name == _term_entry('sales', 'order_id', project='my-proj')
        assert not api_layer.is_known_entry(entry_name)

    def test_raises_when_project_number_lookup_keeps_failing(self, mock_glossaries, monkeypatch):
        mock_glossaries({'sales': ('Sales', {'order_id': 'Order ID'})})
        monkeypatch.setattr(api_layer, 'get_project_number', Mock(side_effect=TransientAPIError('unavailable')))

        with pytest.raises(TransientAPIError):
            self.lookup('my-proj.global.Sales.Order ID', 'order_id')


class TestNormalizeEntryNameProjectNumber:
    """Test normalize_entry_name_project_number."""

    ENTRY = 'projects/my-proj/locations/us/entryGroups/eg/entries/e1'

    def test_replaces_project_id_with_project_number(self, monkeypatch):
        monkeypatch.setattr(api_layer, 'get_project_number', lambda project_id, user_project='': '123')

        assert api_layer.normalize_entry_name_project_number(self.ENTRY) == (
            'projects/123/locations/us/entryGroups/eg/entries/e1'
        )

    def test_keeps_name_when_project_number_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(api_layer, 'get_project_number', Mock(side_effect=DataplexAPIError('no access')))

        assert api_layer.normalize_entry_name_project_number(self.ENTRY) == self.ENTRY

    def test_raises_when_project_number_lookup_keeps_failing(self, monkeypatch):
        monkeypatch.setattr(api_layer, 'get_project_number', Mock(side_effect=TransientAPIError('unavailable')))

        with pytest.raises(TransientAPIError):
            api_layer.normalize_entry_name_project_number(self.ENTRY)


TABLE_FQN = 'bigquery:my-proj.ds.orders'
DATASET_FQN = 'bigquery:my-proj.ds'


def _http_error(status):
    return HttpError(httplib2.Response({'status': status}), b'{}')


def _bq_entry(fqn, location='us-central1'):
    """The @bigquery entry of a BigQuery FQN such as 'bigquery:my-proj.ds.orders'."""
    project, dataset, *table = fqn[len('bigquery:'):].split('.')
    path = f'projects/{project}/datasets/{dataset}' + (f'/tables/{table[0]}' if table else '')
    return {
        'name': f'projects/{project}/locations/{location}/entryGroups/@bigquery/entries/bigquery.googleapis.com/{path}',
        'fullyQualifiedName': fqn,
    }


def _with_project_number(entry_name):
    """The entry name as returned by lookup_entry_by_fqn: its project replaced by the number 123."""
    return entry_name.replace('projects/my-proj/', 'projects/123/', 1)


def _fake_catalog(searchable=(), readable=(), get_error=None, search_delay=0):
    """A Dataplex service mock backed by lists of entries.

    searchEntries returns every `searchable` entry whose FQN contains the queried FQN, so the
    caller has to keep only the exact match. lookupEntry returns the `readable` entry with the
    requested name, raises `get_error` if given, and otherwise raises a 403, as the live API
    does for entries that don't exist.
    """
    service = MagicMock()
    readable_by_name = {entry['name']: entry for entry in readable}

    def search_entries(name, query, **kwargs):
        time.sleep(search_delay)
        fqn = query.split('"')[1]
        request = MagicMock()
        request.execute.return_value = {
            'results': [{'dataplexEntry': dict(e)} for e in searchable if fqn in e['fullyQualifiedName']]
        }
        return request

    def lookup_entry(name, entry, **kwargs):
        request = MagicMock()
        if get_error is not None:
            request.execute.side_effect = get_error
        elif entry in readable_by_name:
            request.execute.return_value = dict(readable_by_name[entry])
        else:
            request.execute.side_effect = _http_error(403)
        return request

    _search_mock(service).side_effect = search_entries
    _lookup_mock(service).side_effect = lookup_entry
    return service


def _search_mock(service):
    return service.projects.return_value.locations.return_value.searchEntries


def _lookup_mock(service):
    return service.projects.return_value.locations.return_value.lookupEntry


def _read_entries(service):
    """The entry names that lookupEntry was called for."""
    return [c.kwargs['entry'] for c in _lookup_mock(service).call_args_list]


class TestLookupEntryByFQN:
    """Test lookup_entry_by_fqn."""

    @pytest.fixture(autouse=True)
    def project_number(self, monkeypatch):
        api_layer.clear_caches()
        monkeypatch.setattr(api_layer, 'get_project_number', lambda project_id, user_project='': '123')

    def test_returns_exact_fqn_match_and_caches_it(self):
        table = _bq_entry(TABLE_FQN)
        service = _fake_catalog(searchable=[table, _bq_entry(TABLE_FQN + '_v2')])

        entry = api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')

        assert entry == {'name': _with_project_number(table['name']), 'fullyQualifiedName': TABLE_FQN}
        assert api_layer.is_known_entry(entry['name'])
        assert api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj') == entry
        _search_mock(service).assert_called_once_with(
            name='projects/user-proj/locations/global',
            query=f'fully_qualified_name="{TABLE_FQN}"',
            scope='projects/my-proj',
        )

    def test_searches_other_fqns_without_scope(self):
        custom = {
            'name': 'projects/my-proj/locations/us-central1/entryGroups/my-eg/entries/custom-01',
            'fullyQualifiedName': 'custom:my_ds.custom_01',
        }
        service = _fake_catalog(searchable=[custom])

        entry = api_layer.lookup_entry_by_fqn(service, 'custom:my_ds.custom_01', 'user-proj')

        assert entry['name'] == 'projects/123/locations/us-central1/entryGroups/my-eg/entries/custom-01'
        _search_mock(service).assert_called_once_with(
            name='projects/user-proj/locations/global',
            query='fully_qualified_name="custom:my_ds.custom_01"',
        )

    def test_reads_table_missing_from_search_in_dataset_location(self):
        table = _bq_entry(TABLE_FQN, location='asia-south1')
        service = _fake_catalog(searchable=[_bq_entry(DATASET_FQN, location='asia-south1')], readable=[table])

        entry = api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')

        assert entry['name'] == _with_project_number(table['name'])
        assert api_layer.is_known_entry(entry['name'])
        # Read with lookupEntry under the user project (source-system permissions), not entries.get.
        _lookup_mock(service).assert_called_once_with(name='projects/user-proj/locations/asia-south1', entry=table['name'])

    def test_reads_other_tables_of_a_found_dataset_directly(self):
        first, second = _bq_entry(f'{DATASET_FQN}.first'), _bq_entry(f'{DATASET_FQN}.second')
        service = _fake_catalog(searchable=[first], readable=[second])

        api_layer.lookup_entry_by_fqn(service, first['fullyQualifiedName'], 'user-proj')
        entry = api_layer.lookup_entry_by_fqn(service, second['fullyQualifiedName'], 'user-proj')

        assert entry['name'] == _with_project_number(second['name'])
        assert api_layer.is_known_entry(entry['name'])
        _search_mock(service).assert_called_once()  # For the first table only.
        assert _read_entries(service) == [second['name']]

    def test_unindexed_tables_cost_one_read_after_the_first_of_their_dataset(self):
        tables = [_bq_entry(f'{DATASET_FQN}.t{i}') for i in range(3)]
        service = _fake_catalog(searchable=[_bq_entry(DATASET_FQN)], readable=tables)

        for table in tables:
            entry = api_layer.lookup_entry_by_fqn(service, table['fullyQualifiedName'], 'user-proj')
            assert entry['name'] == _with_project_number(table['name'])

        # The first table is searched and then read in its dataset's location; the others are read directly.
        assert [c.kwargs['query'] for c in _search_mock(service).call_args_list] == [
            f'fully_qualified_name="{DATASET_FQN}.t0"',
            f'fully_qualified_name="{DATASET_FQN}"',
        ]
        assert _read_entries(service) == [t['name'] for t in tables]

    def test_searches_when_direct_read_finds_nothing(self):
        # A BigQuery model's FQN looks like a table's, but its entry is under /models/.
        model = {
            'name': 'projects/my-proj/locations/us-central1/entryGroups/@bigquery/entries/'
                    'bigquery.googleapis.com/projects/my-proj/datasets/ds/models/churn',
            'fullyQualifiedName': f'{DATASET_FQN}.churn',
        }
        service = _fake_catalog(searchable=[_bq_entry(TABLE_FQN), model])

        api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')
        entry = api_layer.lookup_entry_by_fqn(service, model['fullyQualifiedName'], 'user-proj')

        assert entry['name'] == _with_project_number(model['name'])
        _lookup_mock(service).assert_called_once()  # Reading '.../tables/churn' found nothing.

    def test_missing_table_of_a_found_dataset_is_negatively_cached(self):
        missing_fqn = f'{DATASET_FQN}.missing'
        service = _fake_catalog(searchable=[_bq_entry(TABLE_FQN)])

        api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')
        for _ in range(2):
            with pytest.raises(EntryFQNNotFoundError):
                api_layer.lookup_entry_by_fqn(service, missing_fqn, 'user-proj')

        _lookup_mock(service).assert_called_once()
        assert [c.kwargs['query'] for c in _search_mock(service).call_args_list] == [
            f'fully_qualified_name="{TABLE_FQN}"',
            f'fully_qualified_name="{missing_fqn}"',
        ]

    def test_permission_denied_on_table_of_a_found_dataset_is_reported(self):
        service = _fake_catalog(searchable=[_bq_entry(TABLE_FQN)], get_error=_http_error(403))

        api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')
        with pytest.raises(EntryFQNNotFoundError, match='not found, or permission denied'):
            api_layer.lookup_entry_by_fqn(service, f'{DATASET_FQN}.restricted', 'user-proj')

        assert _search_mock(service).call_count == 2  # Search was tried after the read was denied.

    def test_unknown_fqn_is_not_found_and_negatively_cached(self):
        service = _fake_catalog()

        for _ in range(2):
            with pytest.raises(EntryFQNNotFoundError):
                api_layer.lookup_entry_by_fqn(service, 'bigquery:missing.ds.tbl', 'user-proj')

        assert [c.kwargs['query'] for c in _search_mock(service).call_args_list] == [
            'fully_qualified_name="bigquery:missing.ds.tbl"',
            'fully_qualified_name="bigquery:missing.ds"',
        ]
        _lookup_mock(service).assert_not_called()

    def test_missing_table_entry_is_negatively_cached(self):
        service = _fake_catalog(searchable=[_bq_entry(DATASET_FQN)])

        for _ in range(2):
            with pytest.raises(EntryFQNNotFoundError):
                api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')

        _lookup_mock(service).assert_called_once()

    def test_permission_denied_on_table_entry_is_cached(self):
        service = _fake_catalog(searchable=[_bq_entry(DATASET_FQN)], get_error=_http_error(403))

        for _ in range(2):
            with pytest.raises(EntryFQNNotFoundError, match='not found, or permission denied'):
                api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')

        _lookup_mock(service).assert_called_once()

    def test_search_outage_is_not_cached(self, monkeypatch):
        monkeypatch.setattr(api_layer, 'execute_with_retry', lambda operation, name: operation())  # No retry delays.
        table = _bq_entry(TABLE_FQN)
        service = MagicMock()
        _search_mock(service).return_value.execute.side_effect = [
            _http_error(503), {'results': [{'dataplexEntry': table}]}
        ]

        with pytest.raises(DataplexAPIError) as exc_info:
            api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')
        assert api_layer.is_transient_error(exc_info.value)
        assert api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')['name'] == _with_project_number(table['name'])

    def test_other_table_read_errors_raise_api_error(self):
        service = _fake_catalog(searchable=[_bq_entry(DATASET_FQN)], get_error=_http_error(400))

        with pytest.raises(DataplexAPIError):
            api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')

    def test_search_failure_raises_api_error(self):
        service = MagicMock()
        _search_mock(service).return_value.execute.side_effect = _http_error(400)

        with pytest.raises(DataplexAPIError):
            api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')

    def test_concurrent_table_lookups_resolve_dataset_once(self):
        tables = [_bq_entry(f'{DATASET_FQN}.t{i}') for i in range(4)]
        # Slow searches make the threads overlap while they resolve the shared dataset.
        service = _fake_catalog(searchable=[_bq_entry(DATASET_FQN)], readable=tables, search_delay=0.05)
        results, errors = {}, []

        def lookup(fqn):
            try:
                results[fqn] = api_layer.lookup_entry_by_fqn(service, fqn, 'user-proj')['name']
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=lookup, args=(t['fullyQualifiedName'],)) for t in tables]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors
        assert results == {t['fullyQualifiedName']: _with_project_number(t['name']) for t in tables}
        queries = [c.kwargs['query'] for c in _search_mock(service).call_args_list]
        assert queries.count(f'fully_qualified_name="{DATASET_FQN}"') == 1


class TestConcurrentCaching:
    """Test _get_or_fetch, which fills the module caches from worker threads."""

    def setup_method(self):
        api_layer.clear_caches()

    def test_fetches_a_key_once_across_threads(self):
        cache, fetches, results = {}, [], []

        def fetch():
            fetches.append(1)
            time.sleep(0.1)  # Keep the other threads waiting for this key.
            return 'value'

        threads = [
            threading.Thread(target=lambda: results.append(api_layer._get_or_fetch(cache, 'key', fetch)))
            for _ in range(5)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert results == ['value'] * 5
        assert len(fetches) == 1

    def test_fetches_different_keys_in_parallel(self):
        cache, errors = {}, []
        # Each fetch waits for the other one, so this only passes if both run at the same time.
        both_fetching = threading.Barrier(2, timeout=5)

        def fetch_key(key):
            def fetch():
                both_fetching.wait()
                return key.upper()
            try:
                api_layer._get_or_fetch(cache, key, fetch)
            except threading.BrokenBarrierError as e:
                errors.append(e)

        threads = [threading.Thread(target=fetch_key, args=(key,)) for key in ('a', 'b')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors
        assert cache == {'a': 'A', 'b': 'B'}

    def test_does_not_cache_transient_errors(self):
        cache = {}
        fetch = Mock(side_effect=[TransientAPIError('service unavailable'), 'value'])

        with pytest.raises(TransientAPIError):
            api_layer._get_or_fetch(cache, 'key', fetch)
        assert 'key' not in cache
        assert api_layer._get_or_fetch(cache, 'key', fetch) == 'value'
        assert fetch.call_count == 2

    def test_caches_other_errors(self):
        cache = {}
        fetch = Mock(side_effect=[DataplexAPIError('permission denied'), 'value'])

        for _ in range(3):
            with pytest.raises(DataplexAPIError, match='permission denied'):
                api_layer._get_or_fetch(cache, 'key', fetch)
        fetch.assert_called_once()

    def test_raising_a_cached_error_again_does_not_grow_its_traceback(self):
        cache = {}
        fetch = Mock(side_effect=DataplexAPIError('permission denied'))
        traceback_lengths = []

        for _ in range(3):
            with pytest.raises(DataplexAPIError) as exc_info:
                api_layer._get_or_fetch(cache, 'key', fetch)
            traceback_lengths.append(len(traceback.extract_tb(exc_info.value.__traceback__)))

        assert traceback_lengths[1] == traceback_lengths[2]

    def test_caches_none(self):
        cache = {}
        fetch = Mock(return_value=None)

        assert api_layer._get_or_fetch(cache, 'key', fetch) is None
        assert api_layer._get_or_fetch(cache, 'key', fetch) is None
        fetch.assert_called_once()


def _raised_while_handling(error):
    """A DataplexAPIError raised while handling `error`, as the api_layer wrappers do."""
    try:
        try:
            raise error
        except Exception as e:
            raise DataplexAPIError(f'lookup failed: {e}')
    except DataplexAPIError as wrapper:
        return wrapper


class TestIsTransientError:
    """Test is_transient_error, which decides whether the import stops or skips a row."""

    @pytest.mark.parametrize('error', [
        TransientAPIError('unavailable'), _http_error(503), _http_error(429), socket.timeout('timed out'),
        ConnectionResetError(),
    ])
    def test_network_and_server_errors_are_transient(self, error):
        assert api_layer.is_transient_error(error)
        assert api_layer.is_transient_error(_raised_while_handling(error))

    @pytest.mark.parametrize('error', [
        _http_error(400), _http_error(403), _http_error(404), DataplexAPIError('permission denied'),
        EntryFQNNotFoundError('not found'), ValueError('Target ID is required'),
    ])
    def test_other_errors_are_not_transient(self, error):
        assert not api_layer.is_transient_error(error)
        assert not api_layer.is_transient_error(_raised_while_handling(error))

    def test_checks_the_cause_of_an_error(self):
        error = DataplexAPIError('lookup failed')
        error.__cause__ = _http_error(502)

        assert api_layer.is_transient_error(error)

    def test_stops_on_a_cyclic_chain(self):
        first, second = DataplexAPIError('first'), DataplexAPIError('second')
        first.__context__, second.__context__ = second, first

        assert not api_layer.is_transient_error(first)


class TestGetProjectNumberFailures:
    """Test how get_project_number reports and caches Cloud Resource Manager failures."""

    def setup_method(self):
        api_layer.clear_caches()

    @pytest.mark.parametrize('response', [
        {'json': None, 'error_msg': 'Network connectivity issue'},
        {'json': {'error': {'code': 503, 'message': 'unavailable'}}, 'error_msg': 'unavailable'},
        {'json': {'error': {'code': 429, 'message': 'quota exceeded'}}, 'error_msg': 'quota exceeded'},
    ])
    def test_outage_raises_transient_error_that_is_not_cached(self, monkeypatch, response):
        fetch = Mock(return_value=response)
        monkeypatch.setattr(api_layer.api_call_utils, 'fetch_api_response', fetch)

        for _ in range(2):
            with pytest.raises(TransientAPIError):
                api_layer.get_project_number('my-proj', 'user-proj')
        assert fetch.call_count == 2

    def test_permission_denied_is_cached(self, monkeypatch):
        fetch = Mock(return_value={'json': {'error': {'code': 403, 'message': 'denied'}}, 'error_msg': 'denied'})
        monkeypatch.setattr(api_layer.api_call_utils, 'fetch_api_response', fetch)

        for _ in range(2):
            with pytest.raises(DataplexAPIError, match='denied') as exc_info:
                api_layer.get_project_number('my-proj', 'user-proj')
            assert not api_layer.is_transient_error(exc_info.value)
        fetch.assert_called_once()


class TestRateLimiter:
    """Test _RateLimiter and that searchEntries and lookupEntry calls go through their limiters."""

    def setup_method(self):
        api_layer.clear_caches()

    def test_spaces_calls_across_threads(self):
        limiter = api_layer._RateLimiter(0.05)
        call_times = []

        def call():
            limiter.wait()
            call_times.append(time.monotonic())

        threads = [threading.Thread(target=call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        call_times.sort()
        gaps = [later - earlier for earlier, later in zip(call_times, call_times[1:])]
        assert len(gaps) == 3
        assert min(gaps) >= 0.045

    def test_waits_before_every_attempt(self, monkeypatch):
        def retry_once(operation, description):
            try:
                return operation()
            except HttpError:
                return operation()

        monkeypatch.setattr(api_layer, 'execute_with_retry', retry_once)
        limiter = Mock()
        request = Mock()
        request.execute.side_effect = [_http_error(503), {'ok': True}]

        assert api_layer._execute_rate_limited(limiter, request, 'test call') == {'ok': True}
        assert limiter.wait.call_count == 2

    def test_search_and_table_reads_are_rate_limited(self, monkeypatch):
        monkeypatch.setattr(api_layer, 'get_project_number', lambda project_id, user_project='': '123')
        search_limiter, read_limiter = Mock(), Mock()
        monkeypatch.setattr(api_layer, '_search_rate_limiter', search_limiter)
        monkeypatch.setattr(api_layer, '_entry_read_rate_limiter', read_limiter)
        table = _bq_entry(TABLE_FQN)
        service = _fake_catalog(searchable=[_bq_entry(DATASET_FQN)], readable=[table])

        api_layer.lookup_entry_by_fqn(service, TABLE_FQN, 'user-proj')

        assert search_limiter.wait.call_count == _search_mock(service).call_count == 2  # Table, then dataset.
        assert read_limiter.wait.call_count == _lookup_mock(service).call_count == 1

    def test_lookup_entry_is_rate_limited(self, monkeypatch):
        read_limiter = Mock()
        monkeypatch.setattr(api_layer, '_entry_read_rate_limiter', read_limiter)
        service = MagicMock()
        service.projects().locations().lookupEntry().execute.return_value = {'name': 'e'}

        assert api_layer.lookup_entry(service, 'e', 'projects/p/locations/us') == {'name': 'e'}
        read_limiter.wait.assert_called_once()

    @pytest.mark.parametrize('status', [401, 403, 404])
    def test_lookup_entry_returns_none_without_warning_when_missing_or_unreadable(self, monkeypatch, status):
        """lookupEntry answers a missing entry with 403 too; callers report these entries, so no warning here."""
        warnings = []
        monkeypatch.setattr(api_layer.logger, 'warning', warnings.append)
        service = MagicMock()
        service.projects().locations().lookupEntry().execute.side_effect = HttpError(
            Mock(status=status), b'{"error": {"message": "denied"}}'
        )

        assert api_layer.lookup_entry(service, 'e', 'projects/p/locations/us') is None
        assert warnings == []


class TestCheckTermMatchesDisplayIdentifier:
    """Test check_term_matches_display_identifier (a full term resource name in the ID cell)."""

    TERM = 'projects/my-proj/locations/global/glossaries/sales/terms/revenue'

    def setup_method(self):
        api_layer.clear_caches()

    @pytest.fixture
    def service(self):
        service = MagicMock()
        service.projects().locations().glossaries().get().execute.return_value = {
            'name': 'projects/my-proj/locations/global/glossaries/sales', 'displayName': 'Sales'
        }
        service.projects().locations().glossaries().terms().get().execute.return_value = {
            'name': self.TERM, 'displayName': 'Revenue'
        }
        return service

    @pytest.mark.parametrize('name', [
        'my-proj.global.Sales.Revenue',
        'my-proj.global.sales.Revenue',  # Glossary ID.
        'my-proj.global.SALES.Revenue',  # Glossary names ignore case.
        'my-proj.global.Sales',  # No term display name: the ID selects the term.
    ])
    def test_accepts_name_of_the_term(self, service, name):
        api_layer.check_term_matches_display_identifier(service, name, self.TERM, self.TERM)

    @pytest.mark.parametrize('name', [
        'my-proj.global.Sales.Net Revenue',  # Other term display name.
        'my-proj.global.Sales.revenue',  # Term display names are case-sensitive.
        'my-proj.global.HR.Revenue',  # Other glossary.
        'other-proj.global.Sales.Revenue',  # Other project.
        'my-proj.us.Sales.Revenue',  # Other location.
    ])
    def test_rejects_name_of_another_term(self, service, name):
        with pytest.raises(TermNameMismatchError) as exc_info:
            api_layer.check_term_matches_display_identifier(service, name, self.TERM, self.TERM)

        assert (exc_info.value.term_name, exc_info.value.term_id) == (name, self.TERM)
        assert exc_info.value.display_name == 'Sales.Revenue'

    @pytest.mark.parametrize('project_number, matches', [('123', True), ('456', False)])
    def test_compares_project_id_with_project_number(self, service, monkeypatch, project_number, matches):
        monkeypatch.setattr(api_layer, 'get_project_number', lambda project_id, user_project='': project_number)
        term = self.TERM.replace('projects/my-proj/', 'projects/123/')

        if matches:
            api_layer.check_term_matches_display_identifier(service, 'my-proj.global.Sales.Revenue', term, term)
        else:
            with pytest.raises(TermNameMismatchError):
                api_layer.check_term_matches_display_identifier(service, 'my-proj.global.Sales.Revenue', term, term)
