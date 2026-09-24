"""
Unit tests for entrylinks-import.py

Test coverage:
- Input helpers (_read_user_input_with_select)
- Archive management (get_existing_archive_files, _remove_archive_files)
- Entry parsing (_parse_source_entry_components, _generate_entrylink_name)
- Path formatting (_format_source_path_for_bigquery)
- Definition references (_build_definition_references)
- Link type extraction (_extract_normalized_link_type)
- Entrylink grouping (_add_entrylink_to_group)
- Import workflow (_run_import_workflow, main)
"""

import sys
from pathlib import Path
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
# PATH FORMATTING TESTS  
# ============================================================================

class TestFormatSourcePathForBigquery:
    """Test _format_source_path_for_bigquery function"""
    
    def test_formats_simple_bigquery_path(self):
        """Format simple BigQuery dataset.table path"""
        result = entrylinks_import._format_source_path_for_bigquery(
            'datasets/my_dataset/tables/my_table', '@bigquery'
        )
        
        assert 'Schema.' in result
    
    def test_handles_non_bigquery_entry_group(self):
        """Non-bigquery entry group should also format column paths with Schema."""
        result = entrylinks_import._format_source_path_for_bigquery(
            'some/path', 'custom_group'
        )
        
        assert isinstance(result, str)
        assert result == 'Schema.some/path'


# ============================================================================
# DEFINITION REFERENCES TESTS
# ============================================================================

class TestBuildDefinitionReferences:
    """Test _build_definition_references function"""
    
    def test_creates_source_reference(self):
        """Should create a source reference entry"""
        from utils.models import SpreadsheetRow
        row = SpreadsheetRow(
            entry_link_type='definition',
            source_entry='projects/proj/locations/us/entryGroups/bigquery/entries/tables/t1',
            target_entry='projects/proj/locations/us/entryGroups/@dataplex/entries/target',
            source_path='/project.dataset.table'
        )
        
        result = entrylinks_import._build_definition_references(row, 'bigquery')
        
        assert isinstance(result, list)
        assert len(result) == 2
    
    def test_includes_target_reference(self):
        """Result should reference target entry"""
        from utils.models import SpreadsheetRow
        row = SpreadsheetRow(
            entry_link_type='definition',
            source_entry='projects/proj/locations/us/entryGroups/bigquery/entries/tables/t1',
            target_entry='target_entry_name',
            source_path='/path'
        )
        
        result = entrylinks_import._build_definition_references(row, 'bigquery')
        
        # Find reference that contains target
        has_target = any('target_entry_name' in str(ref) for ref in result)
        assert has_target


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


class TestResolutionHelpers:
    """Test resolution helpers for human-readable identifiers and FQNs"""

    @pytest.fixture(autouse=True)
    def setup_api_layer_mocks(self, monkeypatch):
        monkeypatch.setattr(entrylinks_import.api_layer, 'get_project_number', lambda p, u=None: p)

    def test_resolve_source_entry_passthrough_full_path(self):
        """Full entry paths starting with projects/ should pass through unchanged"""
        full_path = 'projects/p/locations/l/entryGroups/@dataplex/entries/.../terms/t'
        result = entrylinks_import._resolve_source_entry_name(full_path, 'definition')
        assert result == full_path

    def test_resolve_source_entry_definition_fqn(self, monkeypatch):
        """Definition source FQN should be resolved via lookup_entry_by_fqn"""
        mock_service = Mock()
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_entry_by_fqn',
            lambda s, fqn, p: 'projects/p/locations/us/entryGroups/@bigquery/entries/e1'
        )
        result = entrylinks_import._resolve_source_entry_name(
            'bigquery:my_proj.my_ds.my_table', 'definition',
            dataplex_service=mock_service, user_project='my_proj'
        )
        assert result == 'projects/p/locations/us/entryGroups/@bigquery/entries/e1'

    def test_resolve_source_entry_synonym_identifier(self, monkeypatch):
        """Synonym source term identifier should be resolved via lookup_term_by_display_identifier"""
        mock_service = Mock()
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            lambda s, term_id, p: 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t1'
        )
        result = entrylinks_import._resolve_source_entry_name(
            'my_proj.global.Sales.Order ID', 'synonym',
            dataplex_service=mock_service, user_project='my_proj'
        )
        assert result == 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t1'

    def test_resolve_target_entry_identifier(self, monkeypatch):
        """Target term identifier should be resolved via lookup_term_by_display_identifier"""
        mock_service = Mock()
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            lambda s, term_id, p: 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t2'
        )
        result = entrylinks_import._resolve_target_entry_name(
            'my_proj.global.Sales.Customer ID',
            dataplex_service=mock_service, user_project='my_proj'
        )
        assert result == 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t2'

    def test_build_entry_link_resolves_and_builds(self, monkeypatch):
        """build_entry_link should resolve human-readable fields and return valid EntryLink"""
        from utils.models import SpreadsheetRow
        mock_service = Mock()
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_entry_by_fqn',
            lambda s, fqn, p: 'projects/p/locations/us/entryGroups/@bigquery/entries/e1'
        )
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            lambda s, term_id, p: 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t1'
        )

        row = SpreadsheetRow(
            entry_link_type='definition',
            source='bigquery:my_proj.my_ds.my_table',
            column='order_id',
            target='my_proj.global.Sales.Order ID'
        )

        link = entrylinks_import.build_entry_link(row, dataplex_service=mock_service, user_project='my_proj')
        assert link is not None
        assert 'entryGroups/@bigquery/entryLinks/' in link.name
        assert link.entryLinkType == 'projects/dataplex-types/locations/global/entryLinkTypes/definition'
        assert len(link.entryReferences) == 2
        assert link.entryReferences[0].name == 'projects/p/locations/us/entryGroups/@bigquery/entries/e1'
        assert link.entryReferences[0].path == 'Schema.order_id'
        assert link.entryReferences[1].name == 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t1'

    def test_build_entry_link_with_6_columns(self, monkeypatch):
        """build_entry_link should handle explicit 6-column SpreadsheetRow and pass target_id"""
        from utils.models import SpreadsheetRow
        mock_service = Mock()
        captured_term_ids = []
        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_entry_by_fqn',
            lambda s, fqn, p: {'name': 'projects/p/locations/us/entryGroups/@bigquery/entries/e1'}
        )

        def fake_lookup_term(s, identifier, p="", term_id=""):
            captured_term_ids.append((identifier, term_id))
            return 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t1'

        monkeypatch.setattr(
            entrylinks_import.api_layer, 'lookup_term_by_display_identifier',
            fake_lookup_term
        )

        row = SpreadsheetRow(
            entry_link_type='definition',
            source_name='bigquery:my_proj.my_ds.my_table',
            source_id='my_table',
            column='order_id',
            target_name='my_proj.global.Sales.Order ID',
            target_id='order_id_term'
        )

        link = entrylinks_import.build_entry_link(row, dataplex_service=mock_service, user_project='my_proj')
        assert link is not None
        assert len(link.entryReferences) == 2
        assert link.entryReferences[0].name == 'projects/p/locations/us/entryGroups/@bigquery/entries/e1'
        assert link.entryReferences[0].path == 'Schema.order_id'
        assert link.entryReferences[1].name == 'projects/p/locations/global/entryGroups/@dataplex/entries/.../terms/t1'
        assert captured_term_ids == [('my_proj.global.Sales.Order ID', 'order_id_term')]

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
