"""
Pytest configuration and shared fixtures for dataplex-glossary tests.

Provides common fixtures for:
- Mocked API clients (Dataplex, Sheets)
- Sample data structures (terms, entry links, glossaries)
- File system fixtures (temp directories, archive files)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def no_api_rate_limit_delays(monkeypatch):
    """The tests mock every API call, so the delays between calls only slow them down."""
    from utils import api_layer
    for limiter in (
        api_layer._entry_links_rate_limiter, api_layer._search_rate_limiter, api_layer._entry_read_rate_limiter
    ):
        monkeypatch.setattr(limiter, 'min_interval_seconds', 0)


# ============================================================================
# API CLIENT FIXTURES
# ============================================================================

@pytest.fixture
def mock_dataplex_client():
    """Create a mock Dataplex API client"""
    client = MagicMock()
    
    # Setup common method chains
    client.projects.return_value.locations.return_value.glossaries.return_value = MagicMock()
    client.projects.return_value.locations.return_value.entryGroups.return_value = MagicMock()
    
    return client


@pytest.fixture
def mock_sheets_client():
    """Create a mock Sheets API client"""
    client = MagicMock()
    
    # Setup spreadsheets method chain
    client.spreadsheets.return_value.values.return_value = MagicMock()
    client.spreadsheets.return_value.get.return_value = MagicMock()
    
    return client


@pytest.fixture
def mock_credentials():
    """Create mock Google credentials"""
    creds = MagicMock()
    creds.valid = True
    creds.expired = False
    return creds


# ============================================================================
# SAMPLE DATA FIXTURES
# ============================================================================

@pytest.fixture
def sample_term():
    """Create a sample glossary term"""
    return {
        'name': 'projects/test-project/locations/us-central1/glossaries/my-glossary/terms/test-term',
        'displayName': 'Test Term',
        'description': 'A test term for unit testing'
    }


@pytest.fixture
def sample_terms():
    """Create a list of sample terms"""
    return [
        {
            'name': 'projects/test-project/locations/us/glossaries/test-glossary/terms/term1',
            'displayName': 'Term One'
        },
        {
            'name': 'projects/test-project/locations/us/glossaries/test-glossary/terms/term2',
            'displayName': 'Term Two'
        },
        {
            'name': 'projects/test-project/locations/us/glossaries/test-glossary/terms/term3',
            'displayName': 'Term Three'
        }
    ]


@pytest.fixture
def sample_glossary():
    """Create a sample glossary"""
    return {
        'name': 'projects/test-project/locations/us-central1/glossaries/my-glossary',
        'displayName': 'My Glossary',
        'description': 'A glossary for testing'
    }


@pytest.fixture
def sample_entry_links():
    """Create sample entry links"""
    return [
        {
            'name': 'projects/p/locations/l/entryGroups/eg/entries/e1/entryLinks/link1',
            'entryLinkType': 'DEFINITION',
            'sourceEntry': 'projects/p/locations/l/entryGroups/eg/entries/source1',
            'targetEntry': 'projects/p/locations/l/entryGroups/eg/entries/target1',
            'definitionReferences': [
                {'sourcePath': '/project/dataset/table'}
            ]
        },
        {
            'name': 'projects/p/locations/l/entryGroups/eg/entries/e2/entryLinks/link2',
            'entryLinkType': 'RELATED',
            'sourceEntry': 'projects/p/locations/l/entryGroups/eg/entries/source2',
            'targetEntry': 'projects/p/locations/l/entryGroups/eg/entries/target2'
        },
        {
            'name': 'projects/p/locations/l/entryGroups/eg/entries/e3/entryLinks/link3',
            'entryLinkType': 'SYNONYM',
            'sourceEntry': 'projects/p/locations/l/entryGroups/eg/entries/source3',
            'targetEntry': 'projects/p/locations/l/entryGroups/eg/entries/target3'
        }
    ]


@pytest.fixture
def sample_entry_link_rows():
    """Create sample entry link rows (spreadsheet format)"""
    return [
        ['definition', 'source_entry1', 'target_entry1', '/path/to/table1'],
        ['related', 'source_entry2', 'target_entry2', ''],
        ['synonym', 'source_entry3', 'target_entry3', ''],
        ['definition', 'source_entry4', 'target_entry4', '/path/to/table2']
    ]


@pytest.fixture
def sample_spreadsheet_data():
    """Create sample spreadsheet data with headers"""
    return [
        ['link_type', 'source_entry', 'target_entry', 'source_path'],
        ['definition', 'src1', 'tgt1', '/path1'],
        ['related', 'src2', 'tgt2', ''],
        ['synonym', 'src3', 'tgt3', '']
    ]


# ============================================================================
# ENTRY NAME FIXTURES
# ============================================================================

@pytest.fixture
def sample_bigquery_entry():
    """Create a sample BigQuery entry name"""
    return 'projects/test-project/locations/us/entryGroups/@bigquery/entries/test_dataset/tables/test_table'


@pytest.fixture
def sample_glossary_entry():
    """Create a sample glossary entry name"""
    return 'projects/test-project/locations/global/entryGroups/@dataplex/entries/glossaries/my-glossary/terms/my-term'


@pytest.fixture
def sample_entry_components():
    """Create parsed entry components"""
    return {
        'project': 'test-project',
        'location': 'us-central1',
        'entry_group': '@bigquery',
        'entry': 'datasets/my_dataset/tables/my_table'
    }


# ============================================================================
# LOCATION FIXTURES
# ============================================================================

@pytest.fixture
def sample_locations():
    """Create sample locations"""
    return [
        {'locationId': 'global', 'name': 'projects/p/locations/global'},
        {'locationId': 'us', 'name': 'projects/p/locations/us'},
        {'locationId': 'eu', 'name': 'projects/p/locations/eu'},
        {'locationId': 'us-central1', 'name': 'projects/p/locations/us-central1'},
        {'locationId': 'us-west1', 'name': 'projects/p/locations/us-west1'},
        {'locationId': 'eu-west1', 'name': 'projects/p/locations/eu-west1'}
    ]


@pytest.fixture
def sample_regions():
    """Create sample region IDs"""
    return ['global', 'us', 'eu', 'us-central1', 'us-west1', 'eu-west1']


# ============================================================================
# FILE SYSTEM FIXTURES
# ============================================================================

@pytest.fixture
def temp_archive_dir(tmp_path):
    """Create temporary archive directory with files"""
    archive_dir = tmp_path / 'processed_imports'
    archive_dir.mkdir()
    
    # Create some archive files
    (archive_dir / 'import_2024-01-01.csv').write_text('data1')
    (archive_dir / 'import_2024-01-02.csv').write_text('data2')
    (archive_dir / 'readme.txt').write_text('not a csv')
    
    return archive_dir


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create temporary log directory"""
    log_dir = tmp_path / 'logs'
    log_dir.mkdir()
    return log_dir


# ============================================================================
# API RESPONSE FIXTURES
# ============================================================================

@pytest.fixture
def api_success_response():
    """Create a success API response"""
    return {'status': 'success'}


@pytest.fixture
def api_error_response():
    """Create an error API response mock"""
    error = MagicMock()
    error.resp = MagicMock()
    error.resp.status = 500
    error.reason = 'Internal Server Error'
    return error


@pytest.fixture
def api_rate_limit_response():
    """Create a rate limit API response mock"""
    error = MagicMock()
    error.resp = MagicMock()
    error.resp.status = 429
    error.reason = 'Rate Limit Exceeded'
    return error


@pytest.fixture
def api_not_found_response():
    """Create a not found API response mock"""
    error = MagicMock()
    error.resp = MagicMock()
    error.resp.status = 404
    error.reason = 'Not Found'
    return error


@pytest.fixture
def api_permission_denied_response():
    """Create a permission denied API response mock"""
    error = MagicMock()
    error.resp = MagicMock()
    error.resp.status = 403
    error.reason = 'Permission Denied'
    return error


# ============================================================================
# SPREADSHEET FIXTURES
# ============================================================================

@pytest.fixture
def sample_spreadsheet_url():
    """Create a sample Google Sheets URL"""
    return 'https://docs.google.com/spreadsheets/d/abc123def456/edit#gid=0'


@pytest.fixture
def sample_spreadsheet_id():
    """Create a sample spreadsheet ID"""
    return 'abc123def456'


@pytest.fixture
def sample_spreadsheet_metadata():
    """Create sample spreadsheet metadata"""
    return {
        'spreadsheetId': 'abc123def456',
        'properties': {
            'title': 'Entry Links Export'
        },
        'sheets': [
            {
                'properties': {
                    'sheetId': 0,
                    'title': 'Sheet1',
                    'index': 0
                }
            }
        ]
    }


# ============================================================================
# CONFIGURATION FIXTURES
# ============================================================================

@pytest.fixture
def user_project():
    """User project ID for testing (used for billing/quota)"""
    return 'test-project-123'


@pytest.fixture
def mock_args():
    """Create mock command line arguments"""
    args = MagicMock()
    args.glossary_url = 'https://console.cloud.google.com/dataplex/govern/glossary/projects/p/locations/l/glossaries/g'
    args.spreadsheet_url = 'https://docs.google.com/spreadsheets/d/abc123/edit'
    args.verbose = False
    args.dry_run = False
    return args


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def create_mock_http_error(status_code, reason='Error'):
    """Helper to create mock HTTP errors"""
    error = MagicMock()
    error.resp = MagicMock()
    error.resp.status = status_code
    error.reason = reason
    return error
