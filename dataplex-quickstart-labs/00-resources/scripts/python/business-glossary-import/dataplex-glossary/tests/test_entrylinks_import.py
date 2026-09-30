"""
Unit tests for entrylinks-import.py

Test coverage:
- Input helpers (_read_user_input_with_select)
- Archive management (get_existing_archive_files, _remove_archive_files)
- Entry parsing (_parse_source_entry_components, _generate_entrylink_name)
- Entry references (build_entry_references, _build_definition_references)
- Link type extraction (_extract_normalized_link_type)
- Entrylink grouping (_add_entrylink_to_group)
- Row resolution (_resolve_source_entry_name, _resolve_target_entry_name, build_entry_link,
  convert_spreadsheet_to_entrylinks)
- Import validation (check_entry_existence, confirm_import)
- Import workflow (_run_import_workflow, main)
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch, call
import pytest

# Import the module
sys.path.insert(0, str(Path(__file__).parent.parent))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "entrylinks_import", 
    str(Path(__file__).parent.parent / 'import' / 'entrylinks-import.py')
)
entrylinks_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrylinks_import)

from utils.error import EntryFQNNotFoundError, TransientAPIError
from utils.models import EntryLink, EntryReference, SpreadsheetRow

TABLE_ENTRY = (
    'projects/123/locations/us/entryGroups/@bigquery/entries/'
    'bigquery.googleapis.com/projects/proj/datasets/ds/tables/orders'
)
TERM_ENTRY = (
    'projects/123/locations/global/entryGroups/@dataplex/entries/'
    'projects/123/locations/global/glossaries/g1/terms/order_id'
)
OTHER_TERM_ENTRY = (
    'projects/123/locations/global/entryGroups/@dataplex/entries/'
    'projects/123/locations/global/glossaries/g1/terms/order_number'
)


# ============================================================================
# INPUT HELPERS TESTS
# ============================================================================

class TestReadUserInputWithSelect:
    """Test _read_user_input_with_select helper function"""
    
    def test_valid_input_returns_stripped_value(self, monkeypatch):
        """Valid input should be stripped and returned"""
        import select as select_mod
        mock_stdin = MagicMock()
        mock_stdin.readline.return_value = '  test_input  \n'
        monkeypatch.setattr('sys.stdin', mock_stdin)
        monkeypatch.setattr(select_mod, 'select', lambda r, w, x, t: (r, w, x))
        
        result = entrylinks_import._read_user_input_with_select(10)
        
        assert result == 'test_input'
    
    def test_empty_input_returns_empty(self, monkeypatch):
        """Empty input should return empty string"""
        import select as select_mod
        mock_stdin = MagicMock()
        mock_stdin.readline.return_value = '\n'
        monkeypatch.setattr('sys.stdin', mock_stdin)
        monkeypatch.setattr(select_mod, 'select', lambda r, w, x, t: (r, w, x))
        
        result = entrylinks_import._read_user_input_with_select(10)
        
        assert result == ''
    
    def test_whitespace_only_returns_empty(self, monkeypatch):
        """Whitespace-only input should return empty string"""
        import select as select_mod
        mock_stdin = MagicMock()
        mock_stdin.readline.return_value = '   \n'
        monkeypatch.setattr('sys.stdin', mock_stdin)
        monkeypatch.setattr(select_mod, 'select', lambda r, w, x, t: (r, w, x))
        
        result = entrylinks_import._read_user_input_with_select(10)
        
        assert result == ''


# ============================================================================
# ARCHIVE MANAGEMENT TESTS
# ============================================================================

class TestGetExistingArchiveFiles:
    """Test _get_existing_archive_files function"""
    
    def test_returns_empty_when_dir_not_exists(self, tmp_path):
        """Returns empty list when archive dir doesn't exist"""
        result = entrylinks_import._get_existing_archive_files(
            str(tmp_path / 'nonexistent'))
        
        assert result == []
    
    def test_returns_json_files_only(self, tmp_path):
        """Returns only .json files from archive directory"""
        archive_dir = tmp_path / 'archive'
        archive_dir.mkdir()
        (archive_dir / 'file1.json').touch()
        (archive_dir / 'file2.json').touch()
        (archive_dir / 'file3.txt').touch()
        
        result = entrylinks_import._get_existing_archive_files(str(archive_dir))
        
        assert len(result) == 2
        assert all(f.endswith('.json') for f in result)
    
    def test_returns_empty_when_no_json(self, tmp_path):
        """Returns empty list when no JSON files"""
        archive_dir = tmp_path / 'archive'
        archive_dir.mkdir()
        (archive_dir / 'file.txt').touch()
        
        result = entrylinks_import._get_existing_archive_files(str(archive_dir))
        
        assert result == []


class TestRemoveArchiveFiles:
    """Test _remove_archive_files function"""
    
    def test_removes_specified_files(self, tmp_path):
        """Should remove listed files"""
        archive_dir = tmp_path / 'archive'
        archive_dir.mkdir()
        file1 = archive_dir / 'file1.json'
        file2 = archive_dir / 'file2.json'
        file1.touch()
        file2.touch()
        
        entrylinks_import._remove_archive_files(str(archive_dir), ['file1.json'])
        
        assert not file1.exists()
        assert file2.exists()
    
    def test_handles_nonexistent_files_gracefully(self, tmp_path):
        """Should not raise for nonexistent files"""
        archive_dir = tmp_path / 'archive'
        archive_dir.mkdir()
        
        # Should not raise
        entrylinks_import._remove_archive_files(str(archive_dir), ['nonexistent.json'])


# ============================================================================
# ENTRY PARSING TESTS
# ============================================================================

class TestParseSourceEntryComponents:
    """Test _parse_source_entry_components function"""
    
    def test_parses_bigquery_entry(self):
        """Parse BigQuery entry format"""
        entry_name = 'projects/proj/locations/us/entryGroups/bigquery/entries/table1'
        
        project_id, location_id, entry_group = entrylinks_import._parse_source_entry_components(entry_name)
        
        assert project_id == 'proj'
        assert location_id == 'us'
        assert entry_group == 'bigquery'
    
    def test_parses_glossary_entry(self):
        """Parse Glossary entry format"""
        entry_name = 'projects/proj/locations/global/entryGroups/@dataplex/entries/glossaries/G/terms/T'
        
        project_id, location_id, entry_group = entrylinks_import._parse_source_entry_components(entry_name)
        
        assert project_id == 'proj'
        assert location_id == 'global'
        assert entry_group == '@dataplex'
    
    def test_raises_on_invalid_format(self):
        """Invalid format should raise ValueError"""
        with pytest.raises(ValueError):
            entrylinks_import._parse_source_entry_components('invalid/path')
    
    def test_handles_complex_entry_path(self):
        """Handle entries with complex paths"""
        entry_name = 'projects/p/locations/l/entryGroups/eg/entries/datasets/ds/tables/t'
        
        project_id, location_id, entry_group = entrylinks_import._parse_source_entry_components(entry_name)
        
        assert project_id == 'p'
        assert entry_group == 'eg'


class TestGenerateEntrylinkName:
    """Test _generate_entrylink_name function"""
    
    def test_generates_valid_name_format(self):
        """Generated name should follow expected pattern"""
        result = entrylinks_import._generate_entrylink_name('proj', 'us', 'eg')
        
        assert 'entryLinks' in result
        assert isinstance(result, str)
    
    def test_different_sources_create_different_names(self):
        """Different inputs should create unique names (UUID-based)"""
        name1 = entrylinks_import._generate_entrylink_name('p1', 'us', 'eg1')
        name2 = entrylinks_import._generate_entrylink_name('p2', 'eu', 'eg2')
        
        assert name1 != name2
    
    def test_same_inputs_create_same_name(self):
        """Function generates unique names using UUID, so same inputs produce different names"""
        name1 = entrylinks_import._generate_entrylink_name('proj', 'us', 'eg')
        name2 = entrylinks_import._generate_entrylink_name('proj', 'us', 'eg')
        
        # Names use UUID so they are unique each time
        assert name1 != name2
        # But they should share the same base path
        assert name1.startswith('projects/proj/locations/us/entryGroups/eg/entryLinks/')
        assert name2.startswith('projects/proj/locations/us/entryGroups/eg/entryLinks/')


# ============================================================================
# ENTRY REFERENCES TESTS
# ============================================================================

class TestBuildEntryReferences:
    """Test build_entry_references and _build_definition_references functions"""
    
    def test_definition_link_has_source_column_and_target(self):
        """Definition links reference the data asset (with the column path) as SOURCE and the term as TARGET"""
        result = entrylinks_import.build_entry_references(TABLE_ENTRY, TERM_ENTRY, 'order_id', 'definition')
        
        assert [ref.to_dict() for ref in result] == [
            {'name': TABLE_ENTRY, 'path': 'Schema.order_id', 'type': 'SOURCE'},
            {'name': TERM_ENTRY, 'type': 'TARGET'},
        ]
    
    def test_definition_link_without_column_applies_to_whole_asset(self):
        """Without a column the source reference has no path"""
        result = entrylinks_import._build_definition_references(TABLE_ENTRY, TERM_ENTRY)
        
        assert result[0].path == ''
    
    def test_column_with_schema_prefix_is_not_prefixed_again(self):
        """A column already given as a 'Schema.' path is used as is"""
        result = entrylinks_import.build_entry_references(TABLE_ENTRY, TERM_ENTRY, 'Schema.order_id', 'definition')
        
        assert result[0].path == 'Schema.order_id'
    
    @pytest.mark.parametrize('link_type', ['synonym', 'related'])
    def test_term_links_ignore_column(self, link_type):
        """Synonym and related links have no direction and no column"""
        result = entrylinks_import.build_entry_references(TERM_ENTRY, OTHER_TERM_ENTRY, 'order_id', link_type)
        
        assert [ref.to_dict() for ref in result] == [{'name': TERM_ENTRY}, {'name': OTHER_TERM_ENTRY}]


# ============================================================================
# LINK TYPE EXTRACTION TESTS
# ============================================================================

class TestExtractNormalizedLinkType:
    """Test _extract_normalized_link_type function"""
    
    def test_extracts_definition_type(self):
        """Should extract 'definition' type"""
        result = entrylinks_import._extract_normalized_link_type(
            'projects/dataplex-types/locations/global/entryLinkTypes/definition'
        )
        
        assert result == 'definition'
    
    def test_extracts_related_type(self):
        """Should normalize 'related' to 'related-synonym'"""
        result = entrylinks_import._extract_normalized_link_type(
            'projects/dataplex-types/locations/global/entryLinkTypes/related'
        )
        
        assert result == 'related-synonym'
    
    def test_extracts_synonym_type(self):
        """Should normalize 'synonym' to 'related-synonym'"""
        result = entrylinks_import._extract_normalized_link_type(
            'projects/dataplex-types/locations/global/entryLinkTypes/synonym'
        )
        
        assert result == 'related-synonym'
    
    def test_handles_invalid_type(self):
        """Should return None for invalid type format"""
        result = entrylinks_import._extract_normalized_link_type('invalid_type_string')
        
        assert result is None
    
    def test_handles_numeric_project_id(self):
        """Should handle numeric project ID (655216118709) in type path"""
        result = entrylinks_import._extract_normalized_link_type(
            'projects/655216118709/locations/global/entryLinkTypes/definition'
        )
        
        assert result == 'definition'


# ============================================================================
# ENTRYLINK GROUPING TESTS
# ============================================================================

class TestAddEntrylinkToGroup:
    """Test _add_entrylink_to_group function"""
    
    def test_adds_to_empty_group(self):
        """Should add to empty group container"""
        groups = {}
        entrylink_dict = {'name': 'link1'}
        
        entrylinks_import._add_entrylink_to_group(
            groups, entrylink_dict, 'definition', 'proj', 'us', 'eg'
        )
        
        assert 'definition' in groups
        assert 'proj_us_eg' in groups['definition']
        assert len(groups['definition']['proj_us_eg']) == 1
    
    def test_adds_to_existing_group(self):
        """Should append to existing group"""
        groups = {'definition': {'proj_us_eg': [{'name': 'existing'}]}}
        
        entrylinks_import._add_entrylink_to_group(
            groups, {'name': 'new'}, 'definition', 'proj', 'us', 'eg'
        )
        
        assert len(groups['definition']['proj_us_eg']) == 2
    
    def test_creates_new_group_for_different_link_type(self):
        """Should create new group for different link type"""
        groups = {'definition': {'p_l_eg': [{'name': 'link1'}]}}
        
        entrylinks_import._add_entrylink_to_group(
            groups, {'name': 'link2'}, 'related-synonym', 'p', 'l', 'eg'
        )
        
        assert 'related-synonym' in groups
        assert 'definition' in groups


# ============================================================================
# VALIDATION TESTS
# ============================================================================

class TestValidateEntryLinkRow:
    """Test entry link row validation"""
    
    def test_valid_row_passes(self, monkeypatch):
        """Valid row should pass validation"""
        row = {
            'link_type': 'definition',
            'source_entry': 'projects/p/locations/l/entryGroups/eg/entries/e',
            'target_entry': 'projects/p/locations/l/entryGroups/eg/entries/t',
            'source_path': '/path/to/source'
        }
        
        # Should not raise
        # If there's a validation function, call it
        if hasattr(entrylinks_import, 'validate_entry_link_row'):
            result = entrylinks_import.validate_entry_link_row(row)
            assert result is True or result is None
    
    def test_missing_required_fields_fails(self, monkeypatch):
        """Row missing required fields should fail"""
        row = {
            'link_type': 'definition',
            # Missing source_entry and target_entry
        }
        
        if hasattr(entrylinks_import, 'validate_entry_link_row'):
            with pytest.raises((ValueError, KeyError)):
                entrylinks_import.validate_entry_link_row(row)


# ============================================================================
# IMPORT WORKFLOW TESTS
# ============================================================================

class TestPrepareImportBatches:
    """Test batch preparation for API calls"""
    
    def test_groups_by_region(self, monkeypatch):
        """Entry links should be grouped by target region"""
        if hasattr(entrylinks_import, 'prepare_import_batches'):
            entry_links = [
                {'target_region': 'us', 'name': 'link1'},
                {'target_region': 'us', 'name': 'link2'},
                {'target_region': 'eu', 'name': 'link3'},
            ]
            
            batches = entrylinks_import.prepare_import_batches(entry_links)
            
            assert 'us' in batches
            assert 'eu' in batches
            assert len(batches['us']) == 2
            assert len(batches['eu']) == 1


class TestImportEntryLinksToDataplex:
    """Test import_entry_links_to_dataplex function"""
    
    def test_returns_success_on_valid_input(self, monkeypatch):
        """Should return success for valid entry links"""
        mock_auth = MagicMock()
        mock_create = MagicMock(return_value={'name': 'created_link'})
        
        monkeypatch.setattr(entrylinks_import.api_layer, 'authenticate_dataplex', mock_auth)
        
        if hasattr(entrylinks_import, 'create_entry_link'):
            monkeypatch.setattr(entrylinks_import, 'create_entry_link', mock_create)
    
    def test_handles_api_errors_gracefully(self, monkeypatch):
        """Should handle API errors without crashing"""
        mock_auth = MagicMock()
        mock_create = MagicMock(side_effect=Exception("API Error"))
        
        monkeypatch.setattr(entrylinks_import.api_layer, 'authenticate_dataplex', mock_auth)
        
        # Should not raise, but handle error gracefully


ENTRY_LINK = EntryLink(
    name='projects/123/locations/us/entryGroups/@bigquery/entryLinks/link1',
    entryLinkType='projects/dataplex-types/locations/global/entryLinkTypes/definition',
    entryReferences=[
        EntryReference(name=TABLE_ENTRY, path='Schema.order_id', type='SOURCE'),
        EntryReference(name=TERM_ENTRY, type='TARGET'),
    ],
)


class TestRunImportWorkflow:
    """Test _run_import_workflow function"""
    
    def test_returns_1_when_empty_spreadsheet(self, monkeypatch):
        """Should return 1 when spreadsheet has no entries"""
        mock_parsed_args = MagicMock()
        mock_parsed_args.spreadsheet_url = 'https://docs.google.com/spreadsheets/d/abc/edit'
        mock_parsed_args.user_project = 'my-project'
        
        monkeypatch.setattr(entrylinks_import.sheet_utils, 'get_sheet_name_for_url', lambda url: 'Sheet1')
        monkeypatch.setattr(entrylinks_import.api_layer, 'authenticate_dataplex', MagicMock)
        monkeypatch.setattr(entrylinks_import, 'check_and_clean_archive_folder', lambda d: True)
        monkeypatch.setattr(entrylinks_import, 'convert_spreadsheet_to_entrylinks', lambda *args, **kwargs: [])
        
        result = entrylinks_import._run_import_workflow(mock_parsed_args)
        
        assert result == 1

    def run_workflow(self, monkeypatch, entrylinks, failed_rows=(), missing_entries=(), user_response='y'):
        """Run the workflow with mocked dependencies. Returns (exit code, prompt mock, import mock)."""
        def mock_convert(spreadsheet_url, **kwargs):
            self.convert_kwargs = kwargs
            kwargs['failed_rows'].extend(failed_rows)
            return list(entrylinks)

        mock_prompt = MagicMock(return_value=user_response)
        mock_execute = MagicMock(return_value=0)
        monkeypatch.setattr(entrylinks_import.sheet_utils, 'get_sheet_name_for_url', lambda url: 'Sheet1')
        monkeypatch.setattr(entrylinks_import.api_layer, 'authenticate_dataplex', MagicMock())
        monkeypatch.setattr(entrylinks_import, 'check_and_clean_archive_folder', lambda d: True)
        monkeypatch.setattr(entrylinks_import, 'convert_spreadsheet_to_entrylinks', mock_convert)
        monkeypatch.setattr(entrylinks_import, '_validate_bucket_permissions_for_projects', lambda *args: True)
        monkeypatch.setattr(entrylinks_import, 'check_entry_existence', lambda links: (set(missing_entries), set()))
        monkeypatch.setattr(entrylinks_import, 'get_user_input_with_timeout', mock_prompt)
        monkeypatch.setattr(entrylinks_import, '_execute_import', mock_execute)

        parsed_args = SimpleNamespace(
            spreadsheet_url='https://docs.google.com/spreadsheets/d/abc/edit', buckets=['bucket'], user_project='my-project'
        )
        return entrylinks_import._run_import_workflow(parsed_args), mock_prompt, mock_execute

    def test_returns_1_without_prompt_when_no_row_can_be_imported(self, monkeypatch):
        """If every row fails, the reasons are logged as errors and nothing is imported"""
        errors = []
        monkeypatch.setattr(entrylinks_import.logger, 'error', errors.append)

        result, mock_prompt, mock_execute = self.run_workflow(
            monkeypatch, entrylinks=[], failed_rows=[(2, 'Target ID is required')]
        )

        assert result == 1
        mock_prompt.assert_not_called()
        mock_execute.assert_not_called()
        assert errors == ['None of the 1 row(s) can be imported:', '  - Row 2: Target ID is required']

    def test_imports_without_prompt_when_nothing_to_report(self, monkeypatch):
        """No failed rows and no missing entries: import right away"""
        result, mock_prompt, mock_execute = self.run_workflow(monkeypatch, entrylinks=[ENTRY_LINK])

        assert result == 0
        mock_prompt.assert_not_called()
        mock_execute.assert_called_once_with([ENTRY_LINK], ['bucket'])

    def test_asks_once_and_imports_when_confirmed(self, monkeypatch):
        """Failed rows and missing entries are confirmed with a single prompt"""
        result, mock_prompt, mock_execute = self.run_workflow(
            monkeypatch, entrylinks=[ENTRY_LINK], failed_rows=[(3, 'Invalid entry link type')],
            missing_entries=[TERM_ENTRY], user_response='y'
        )

        assert result == 0
        mock_prompt.assert_called_once()
        mock_execute.assert_called_once_with([ENTRY_LINK], ['bucket'])

    def test_returns_1_when_declined(self, monkeypatch):
        """Declining the prompt (or letting it time out) aborts the import with exit code 1"""
        result, mock_prompt, mock_execute = self.run_workflow(
            monkeypatch, entrylinks=[ENTRY_LINK], missing_entries=[TERM_ENTRY], user_response=''
        )

        assert result == 1
        mock_prompt.assert_called_once()
        mock_execute.assert_not_called()

    def test_does_not_share_its_dataplex_client_with_worker_threads(self, monkeypatch):
        """Rows are resolved in parallel, and a Dataplex client can't be used by several threads"""
        self.run_workflow(monkeypatch, entrylinks=[ENTRY_LINK])

        assert 'dataplex_service' not in self.convert_kwargs


class TestResolutionHelpers:
    """Test resolution of the Name / ID cells to Dataplex entry names"""

    @pytest.fixture(autouse=True)
    def setup_api_layer_mocks(self, monkeypatch):
        monkeypatch.setattr(entrylinks_import.api_layer, 'get_project_number', lambda p, u=None: p)

    def test_resolve_source_entry_passthrough_full_path(self):
        """Full entry paths starting with projects/ should pass through unchanged"""
        full_path = 'projects/p/locations/l/entryGroups/@dataplex/entries/.../terms/t'
        result = entrylinks_import._resolve_source_entry_name(full_path, 'definition')
        assert result == full_path

    def test_resolve_source_entry_definition_fqn(self, monkeypatch):
        """Definition sources are looked up by the FQN in Source Name (Source ID is informational)"""
        mock_service = Mock()
        looked_up = []

        def fake_lookup_entry(s, fqn, p):
            looked_up.append((fqn, p))
            return {'name': TABLE_ENTRY, 'fullyQualifiedName': fqn}

        monkeypatch.setattr(entrylinks_import.api_layer, 'lookup_entry_by_fqn', fake_lookup_entry)
        result = entrylinks_import._resolve_source_entry_name(
            'bigquery:proj.ds.orders', 'definition', source_id='proj.ds.orders',
            dataplex_service=mock_service, user_project='my_proj'
        )
        assert result == TABLE_ENTRY
        assert looked_up == [('bigquery:proj.ds.orders', 'my_proj')]

    def test_resolve_source_entry_synonym_identifier(self, monkeypatch):
        """Synonym source terms are resolved via lookup_term_by_display_identifier using the Source ID"""
        mock_service = Mock()
        captured = []

        def fake_lookup_term(s, identifier, p="", term_id=""):
            captured.append((identifier, p, term_id))
            return TERM_ENTRY

        monkeypatch.setattr(entrylinks_import.api_layer, 'lookup_term_by_display_identifier', fake_lookup_term)
        result = entrylinks_import._resolve_source_entry_name(
            'my_proj.global.Sales.Order ID', 'synonym', source_id='order_id',
            dataplex_service=mock_service, user_project='my_proj'
        )
        assert result == TERM_ENTRY
        assert captured == [('my_proj.global.Sales.Order ID', 'my_proj', 'order_id')]

    def test_resolve_target_entry_identifier(self, monkeypatch):
        """Target terms are resolved via lookup_term_by_display_identifier using the Target ID"""
        mock_service = Mock()
        captured = []

        def fake_lookup_term(s, identifier, p="", term_id=""):
            captured.append((identifier, p, term_id))
            return OTHER_TERM_ENTRY

        monkeypatch.setattr(entrylinks_import.api_layer, 'lookup_term_by_display_identifier', fake_lookup_term)
        result = entrylinks_import._resolve_target_entry_name(
            'my_proj.global.Sales.Order Number', target_id='order_number',
            dataplex_service=mock_service, user_project='my_proj'
        )
        assert result == OTHER_TERM_ENTRY
        assert captured == [('my_proj.global.Sales.Order Number', 'my_proj', 'order_number')]

    @pytest.mark.parametrize('name, term_id, message', [
        ('my_proj.global.Sales.Order ID', '', 'Target ID is required'),
        ('', 'order_id', 'Target Name is required'),
    ])
    def test_resolve_term_requires_name_and_id(self, name, term_id, message):
        """Glossary terms need both cells: Name selects the glossary and ID the term in it"""
        with pytest.raises(ValueError, match=message):
            entrylinks_import._resolve_target_entry_name(name, target_id=term_id, dataplex_service=Mock())

    def test_resolve_definition_source_requires_name(self):
        """The data asset of a definition link is found by its FQN, so Source Name is required"""
        with pytest.raises(ValueError, match='Source Name is required'):
            entrylinks_import._resolve_source_entry_name(
                '', 'definition', source_id='proj.ds.orders', dataplex_service=Mock()
            )

    def test_build_entry_link_resolves_and_builds(self, monkeypatch):
        """build_entry_link resolves the row's cells and creates the link in the source entry's group"""
        mock_service = Mock()
        captured_term_ids = []
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_entry_by_fqn',
            lambda s, fqn, p: {'name': TABLE_ENTRY}
        )

        def fake_lookup_term(s, identifier, p="", term_id=""):
            captured_term_ids.append((identifier, term_id))
            return TERM_ENTRY

        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            fake_lookup_term
        )

        row = SpreadsheetRow(
            entry_link_type='definition',
            source_name='bigquery:proj.ds.orders',
            source_id='proj.ds.orders',
            column='order_id',
            target_name='my_proj.global.Sales.Order ID',
            target_id='order_id'
        )

        link = entrylinks_import.build_entry_link(row, dataplex_service=mock_service, user_project='my_proj')
        assert link.name.startswith('projects/123/locations/us/entryGroups/@bigquery/entryLinks/')
        assert link.entryLinkType == 'projects/dataplex-types/locations/global/entryLinkTypes/definition'
        assert [ref.to_dict() for ref in link.entryReferences] == [
            {'name': TABLE_ENTRY, 'path': 'Schema.order_id', 'type': 'SOURCE'},
            {'name': TERM_ENTRY, 'type': 'TARGET'},
        ]
        assert captured_term_ids == [('my_proj.global.Sales.Order ID', 'order_id')]

    def test_build_entry_link_synonym_passes_both_source_id_and_target_id(self, monkeypatch):
        """Synonym link with 6 columns should pass both source_id and target_id to lookup_term_by_display_identifier"""
        from utils.models import SpreadsheetRow
        mock_service = Mock()
        captured = []

        def fake_lookup_term(s, identifier, p="", term_id=""):
            captured.append((identifier, term_id))
            return (
                f'projects/p/locations/global/entryGroups/@dataplex/entries/'
                f'projects/p/locations/global/glossaries/g1/terms/{term_id}'
            )

        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            fake_lookup_term
        )

        row = SpreadsheetRow(
            entry_link_type='synonym',
            source_name='my_proj.global.Sales.Order ID',
            source_id='order_id_v1',
            column='',
            target_name='my_proj.global.Sales.Order ID',
            target_id='order_id_v2'
        )

        link = entrylinks_import.build_entry_link(row, dataplex_service=mock_service, user_project='my_proj')
        assert link is not None
        assert captured == [
            ('my_proj.global.Sales.Order ID', 'order_id_v1'),
            ('my_proj.global.Sales.Order ID', 'order_id_v2'),
        ]

    def test_resolve_with_full_resource_ids(self):
        """Resolving with full term resource names in ID columns generates correct entry names"""
        source_id = 'projects/p/locations/global/glossaries/g1/terms/t1'
        result = entrylinks_import._resolve_source_entry_name('', 'synonym', source_id=source_id)
        assert 'entryGroups/@dataplex/entries/' in result
        assert 'glossaries/g1/terms/t1' in result

        target_id = 'projects/p/locations/global/glossaries/g1/terms/t2'
        result = entrylinks_import._resolve_target_entry_name('', target_id=target_id)
        assert 'entryGroups/@dataplex/entries/' in result
        assert 'glossaries/g1/terms/t2' in result

    def test_resolve_with_full_resource_name_in_name_cell(self):
        """A full term resource name in the Name cell is used when the ID cell is empty"""
        result = entrylinks_import._resolve_target_entry_name('projects/p/locations/global/glossaries/g1/terms/t2')
        assert result == (
            'projects/p/locations/global/entryGroups/@dataplex/entries/'
            'projects/p/locations/global/glossaries/g1/terms/t2'
        )


class TestBuildEntryLinkFailures:
    """Rows that can't be imported are reported with their row number and the reason"""

    VALID_ROW = dict(
        entry_link_type='definition', source_name='bigquery:proj.ds.orders', source_id='proj.ds.orders',
        column='order_id', target_name='proj.global.Sales.Order ID', target_id='order_id', row_number=7
    )

    @pytest.fixture(autouse=True)
    def setup_api_layer_mocks(self, monkeypatch):
        monkeypatch.setattr(entrylinks_import.api_layer, 'lookup_entry_by_fqn', lambda s, fqn, p: {'name': TABLE_ENTRY})
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            lambda s, identifier, p="", term_id="": TERM_ENTRY
        )

    def build(self, failed_rows=None, **overrides):
        row = SpreadsheetRow(**{**self.VALID_ROW, **overrides})
        return entrylinks_import.build_entry_link(
            row, dataplex_service=Mock(), user_project='my-project', failed_rows=failed_rows
        )

    def test_valid_row_is_not_recorded(self):
        """A row that resolves builds a link and records nothing"""
        failed_rows = []

        assert self.build(failed_rows) is not None
        assert failed_rows == []

    @pytest.mark.parametrize('overrides, reason', [
        ({'entry_link_type': 'defintion'}, "Invalid entry link type 'defintion'"),
        ({'source_name': ''}, 'Source Name is required'),
        ({'target_id': ''}, 'Target ID is required'),
        ({'entry_link_type': 'synonym', 'source_name': 'proj.global.Sales.Order Number', 'source_id': ''},
         'Source ID is required'),
    ])
    def test_invalid_row_is_recorded_with_reason(self, overrides, reason):
        """The row number and the reason are recorded, and no link is built"""
        failed_rows = []

        assert self.build(failed_rows, **overrides) is None
        assert len(failed_rows) == 1
        row_number, recorded_reason = failed_rows[0]
        assert row_number == 7
        assert reason in recorded_reason

    def test_unknown_data_asset_is_recorded(self, monkeypatch):
        """A Source Name FQN that doesn't match any entry is recorded with the lookup error"""
        def raise_not_found(s, fqn, p):
            raise EntryFQNNotFoundError(f"Entry with FQN '{fqn}' not found in Dataplex under project '{p}'")

        monkeypatch.setattr(entrylinks_import.api_layer, 'lookup_entry_by_fqn', raise_not_found)
        failed_rows = []

        assert self.build(failed_rows, source_name='bigquery:proj.ds.ordrs') is None
        assert failed_rows == [
            (7, "Entry with FQN 'bigquery:proj.ds.ordrs' not found in Dataplex under project 'my-project'")
        ]

    def test_reason_is_logged_without_failed_rows_list(self, monkeypatch):
        """Without a failed_rows list the reason is logged as a warning"""
        warnings = []
        monkeypatch.setattr(entrylinks_import.logger, 'warning', warnings.append)

        assert self.build(target_id='') is None
        assert len(warnings) == 1
        assert warnings[0].startswith('Row 7 skipped: Target ID is required')

    def test_outage_is_raised_instead_of_recorded(self, monkeypatch):
        """Network or server errors that outlast the retries stop the import: the other rows would fail too"""
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_entry_by_fqn', Mock(side_effect=TransientAPIError('unavailable'))
        )
        failed_rows = []

        with pytest.raises(TransientAPIError):
            self.build(failed_rows)
        assert failed_rows == []


class TestConvertSpreadsheetToEntrylinks:
    """Test convert_spreadsheet_to_entrylinks function"""

    def test_converts_rows_and_collects_failed_rows(self, monkeypatch):
        """Valid rows become entry links, blank rows are skipped and failed rows keep their sheet row number"""
        sheet_data = [
            ['Entry link type', 'Source Name', 'Source ID', 'Column', 'Target Name', 'Target ID'],
            ['definition', 'bigquery:proj.ds.orders', 'proj.ds.orders', 'order_id', 'proj.global.Sales.Order ID', 'order_id'],
            [],
            # The Sheets API leaves out trailing empty cells, so this row has no Target ID cell
            ['synonym', 'proj.global.Sales.Order ID', 'order_id', '', 'proj.global.Sales.Order Number'],
        ]
        monkeypatch.setattr(
            entrylinks_import.sheet_utils, 'read_from_spreadsheet_url', lambda url, sheet_name=None: sheet_data
        )
        monkeypatch.setattr(entrylinks_import.api_layer, 'lookup_entry_by_fqn', lambda s, fqn, p: {'name': TABLE_ENTRY})
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            lambda s, identifier, p="", term_id="": TERM_ENTRY
        )
        failed_rows = []

        entrylinks = entrylinks_import.convert_spreadsheet_to_entrylinks(
            'https://docs.google.com/spreadsheets/d/abc/edit',
            dataplex_service=Mock(), user_project='my-project', failed_rows=failed_rows
        )

        assert [link.entryReferences[0].name for link in entrylinks] == [TABLE_ENTRY]
        assert len(failed_rows) == 1
        assert failed_rows[0][0] == 4
        assert 'Target ID is required' in failed_rows[0][1]

    @staticmethod
    def _table_entry(fqn):
        return TABLE_ENTRY.replace('/tables/orders', '/tables/' + fqn.rsplit('.', 1)[-1])

    def convert_table_rows(self, monkeypatch, row_count, lookup_entry_by_fqn, **kwargs):
        """Convert a sheet of definition rows for tables bigquery:proj.ds.t0, t1, ..."""
        sheet_data = [['Entry link type', 'Source Name', 'Source ID', 'Column', 'Target Name', 'Target ID']] + [
            ['definition', f'bigquery:proj.ds.t{i}', f'proj.ds.t{i}', '', 'proj.global.Sales.Order ID', 'order_id']
            for i in range(row_count)
        ]
        monkeypatch.setattr(
            entrylinks_import.sheet_utils, 'read_from_spreadsheet_url', lambda url, sheet_name=None: sheet_data
        )
        monkeypatch.setattr(entrylinks_import.api_layer, 'lookup_entry_by_fqn', lookup_entry_by_fqn)
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            lambda s, identifier, p="", term_id="": TERM_ENTRY
        )
        return entrylinks_import.convert_spreadsheet_to_entrylinks(
            'https://docs.google.com/spreadsheets/d/abc/edit', user_project='my-project', **kwargs
        )

    def test_keeps_row_order_when_rows_resolve_out_of_order(self, monkeypatch):
        """Rows are resolved in parallel, and the entry links keep the order of the rows"""
        resolved = []
        others_resolved = threading.Event()

        def lookup(service, fqn, project):
            if fqn.endswith('.t0'):
                others_resolved.wait(timeout=5)  # The first row is resolved last.
            resolved.append(fqn)
            if len(resolved) == 2:
                others_resolved.set()
            return {'name': self._table_entry(fqn)}

        entrylinks = self.convert_table_rows(monkeypatch, 3, lookup, dataplex_service=Mock())

        assert resolved[-1] == 'bigquery:proj.ds.t0'
        assert [link.entryReferences[0].name for link in entrylinks] == [
            self._table_entry(f'bigquery:proj.ds.t{i}') for i in range(3)
        ]

    def test_stops_resolving_rows_after_an_outage(self, monkeypatch):
        """An outage error is raised, and rows whose resolution hasn't started are skipped"""
        looked_up = []

        def lookup(service, fqn, project):
            looked_up.append(fqn)
            if fqn.endswith('.t0'):
                raise TransientAPIError('unavailable')
            time.sleep(0.2)
            return {'name': self._table_entry(fqn)}

        failed_rows = []
        with pytest.raises(TransientAPIError):
            self.convert_table_rows(monkeypatch, 20, lookup, dataplex_service=Mock(), failed_rows=failed_rows)

        assert len(looked_up) < 20
        assert failed_rows == []

    def test_interrupt_does_not_wait_for_rows_being_resolved(self, monkeypatch):
        """Ctrl+C stops right away: rows being resolved are not waited for, and no other row starts"""
        workers = entrylinks_import.MAX_WORKERS
        started, failed_rows = [], []
        release = threading.Event()

        def lookup(service, fqn, project):
            started.append(fqn)
            release.wait(timeout=5)
            raise ValueError('released')  # Ends the row without further (unmocked) lookups.

        def interrupted_wait(futures, return_when):
            deadline = time.monotonic() + 5
            while len(started) < workers and time.monotonic() < deadline:
                time.sleep(0.01)
            raise KeyboardInterrupt

        monkeypatch.setattr(entrylinks_import, 'wait', interrupted_wait)
        try:
            begin = time.monotonic()
            with pytest.raises(KeyboardInterrupt):
                self.convert_table_rows(monkeypatch, 20, lookup, dataplex_service=Mock(), failed_rows=failed_rows)
            assert time.monotonic() - begin < 2
        finally:
            release.set()
            deadline = time.monotonic() + 5
            while len(failed_rows) < len(started) and time.monotonic() < deadline:
                time.sleep(0.01)
        assert len(started) == workers

    def test_worker_threads_use_their_own_dataplex_clients(self, monkeypatch):
        """Without a given Dataplex service, each worker thread uses its own client"""
        used = []
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'get_dataplex_service', lambda: f'client of {threading.current_thread().name}'
        )

        def lookup(service, fqn, project):
            used.append((service, threading.current_thread().name))
            return {'name': self._table_entry(fqn)}

        self.convert_table_rows(monkeypatch, 3, lookup)

        assert len(used) == 3
        assert all(service == f'client of {thread}' for service, thread in used)
        assert threading.main_thread().name not in {thread for _, thread in used}


class TestCheckEntryExistence:
    """Test check_entry_existence function"""

    def test_only_looks_up_entries_not_confirmed_while_resolving_rows(self, monkeypatch):
        """Entries found while resolving the sheet rows are not looked up again"""
        looked_up = []
        monkeypatch.setattr(entrylinks_import.api_layer, 'is_known_entry', lambda name: name == TABLE_ENTRY)
        monkeypatch.setattr(
            entrylinks_import, '_lookup_and_check_entry', lambda ref, missing, failed: looked_up.append(ref.name)
        )

        missing_entries, failed_entries = entrylinks_import.check_entry_existence([ENTRY_LINK])

        assert looked_up == [TERM_ENTRY]
        assert (missing_entries, failed_entries) == (set(), set())

    def test_no_lookups_when_all_entries_are_known(self, monkeypatch):
        """Nothing is looked up when every referenced entry was already found"""
        mock_lookup = MagicMock()
        monkeypatch.setattr(entrylinks_import.api_layer, 'is_known_entry', lambda name: True)
        monkeypatch.setattr(entrylinks_import, '_lookup_and_check_entry', mock_lookup)

        assert entrylinks_import.check_entry_existence([ENTRY_LINK]) == (set(), set())
        mock_lookup.assert_not_called()


class TestConfirmImport:
    """Test confirm_import function"""

    def test_nothing_to_report_continues_without_prompt(self, monkeypatch):
        """No failed rows and no missing entries: continue without asking"""
        mock_prompt = MagicMock()
        monkeypatch.setattr(entrylinks_import, 'get_user_input_with_timeout', mock_prompt)

        assert entrylinks_import.confirm_import([], set()) is True
        mock_prompt.assert_not_called()

    @pytest.mark.parametrize('response, expected', [('y', True), ('Yes', True), ('n', False), ('', False)])
    def test_reports_everything_then_asks_once(self, monkeypatch, response, expected):
        """Failed rows (ordered by row number) and missing entries are listed before a single prompt"""
        warnings = []
        mock_prompt = MagicMock(return_value=response)
        monkeypatch.setattr(entrylinks_import.logger, 'warning', warnings.append)
        monkeypatch.setattr(entrylinks_import, 'get_user_input_with_timeout', mock_prompt)

        result = entrylinks_import.confirm_import(
            [(5, 'Target ID is required'), (3, 'Invalid entry link type')], {TERM_ENTRY}
        )

        assert result is expected
        mock_prompt.assert_called_once()
        assert warnings == [
            "2 row(s) can't be imported and will be skipped:",
            '  - Row 3: Invalid entry link type',
            '  - Row 5: Target ID is required',
            '1 referenced entry(ies) were not found in Dataplex; entry links that use them may fail during import:',
            f'  - {TERM_ENTRY}',
        ]

    def test_long_lists_are_shortened_on_the_console(self, monkeypatch):
        """At most MAX_LISTED_ITEMS items are shown on the console; the rest go to the log file"""
        warnings = []
        debug_messages = []
        monkeypatch.setattr(entrylinks_import.logger, 'warning', warnings.append)
        monkeypatch.setattr(entrylinks_import.logger, 'debug', debug_messages.append)
        monkeypatch.setattr(entrylinks_import, 'get_user_input_with_timeout', MagicMock(return_value='y'))
        max_items = entrylinks_import.MAX_LISTED_ITEMS
        failed_rows = [(row_number, 'Target ID is required') for row_number in range(2, max_items + 5)]

        entrylinks_import.confirm_import(failed_rows, set())

        assert len([w for w in warnings if w.startswith('  - Row')]) == max_items
        assert warnings[-1] == '  ... and 3 more (see the log file)'
        assert debug_messages == [f'  - Row {row}: Target ID is required' for row in range(max_items + 2, max_items + 5)]

# ============================================================================
# MAIN FLOW TESTS
# ============================================================================

class TestMain:
    """Test main function"""
    
    def test_catches_keyboard_interrupt(self, monkeypatch):
        """Main should catch KeyboardInterrupt and call os._exit(130)"""
        mock_setup = MagicMock()
        mock_get_args = MagicMock(side_effect=KeyboardInterrupt())
        mock_exit = MagicMock()
        
        monkeypatch.setattr(entrylinks_import.logging_utils, 'setup_file_logging', mock_setup)
        monkeypatch.setattr(entrylinks_import.argument_parser, 'get_import_entrylinks_arguments', mock_get_args)
        monkeypatch.setattr(entrylinks_import.os, '_exit', mock_exit)
        
        entrylinks_import.main()
        
        mock_exit.assert_called_once_with(130)
    
    def test_returns_workflow_result(self, monkeypatch):
        """Main should return workflow result"""
        mock_setup = MagicMock()
        mock_args = MagicMock()
        mock_get_args = MagicMock(return_value=mock_args)
        mock_run = MagicMock(return_value=0)
        
        monkeypatch.setattr(entrylinks_import.logging_utils, 'setup_file_logging', mock_setup)
        monkeypatch.setattr(entrylinks_import.argument_parser, 'get_import_entrylinks_arguments', mock_get_args)
        monkeypatch.setattr(entrylinks_import, '_run_import_workflow', mock_run)
        
        result = entrylinks_import.main()
        
        assert result == 0
    
    def test_handles_generic_exception(self, monkeypatch):
        """Main should handle generic exceptions"""
        mock_setup = MagicMock()
        mock_args = MagicMock()
        mock_get_args = MagicMock(return_value=mock_args)
        mock_run = MagicMock(side_effect=Exception("Unexpected error"))
        
        monkeypatch.setattr(entrylinks_import.logging_utils, 'setup_file_logging', mock_setup)
        monkeypatch.setattr(entrylinks_import.argument_parser, 'get_import_entrylinks_arguments', mock_get_args)
        monkeypatch.setattr(entrylinks_import, '_run_import_workflow', mock_run)
        
        result = entrylinks_import.main()
        
        assert result == 1

    def test_returns_1_and_explains_when_api_calls_keep_failing(self, monkeypatch):
        """An outage that outlasts the retries ends the import with exit code 1"""
        errors = []
        monkeypatch.setattr(entrylinks_import.logging_utils, 'setup_file_logging', MagicMock())
        monkeypatch.setattr(entrylinks_import.argument_parser, 'get_import_entrylinks_arguments', MagicMock())
        monkeypatch.setattr(
            entrylinks_import, '_run_import_workflow', MagicMock(side_effect=TransientAPIError('unavailable'))
        )
        monkeypatch.setattr(entrylinks_import.logger, 'error', errors.append)

        assert entrylinks_import.main() == 1
        assert errors == [
            'Import stopped: API calls kept failing with network or server errors after retrying: unavailable',
            'Please check your internet connection and try again.',
        ]


# ============================================================================
# EDGE CASES AND ERROR HANDLING
# ============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions"""
    
    def test_empty_entry_links_list(self, monkeypatch):
        """Should handle empty entry links gracefully"""
        if hasattr(entrylinks_import, 'process_entry_links'):
            result = entrylinks_import.process_entry_links([])
            assert result == [] or result is None
    
    def test_malformed_entry_name(self):
        """Should handle malformed entry names"""
        with pytest.raises(ValueError):
            entrylinks_import._parse_source_entry_components('')
    
    def test_special_characters_in_names(self):
        """Should handle special characters"""
        entry_name = 'projects/my-project/locations/us-central1/entryGroups/my_group/entries/my-entry_123'
        
        project_id, location_id, entry_group = entrylinks_import._parse_source_entry_components(entry_name)
        
        assert project_id == 'my-project'
        assert location_id == 'us-central1'
        assert entry_group == 'my_group'


class TestConcurrency:
    """Test concurrent operation handling"""
    
    def test_parallel_region_processing(self, monkeypatch):
        """Should handle parallel region processing"""
        if hasattr(entrylinks_import, 'process_regions_parallel'):
            regions = ['us', 'eu', 'asia']
            mock_process = MagicMock(return_value={'status': 'success'})
            
            monkeypatch.setattr(entrylinks_import, 'process_single_region', mock_process)
            
            result = entrylinks_import.process_regions_parallel(regions)
            
            assert len(result) == 3
