# Unit Tests for Dataplex Business Glossary EntryLinks Utility

This directory contains comprehensive unit tests for all 11 modules in the EntryLinks utility.

## Test Coverage

The test suite covers the following modules:

### Main Entry Points
1. **test_entrylinks_export.py** - Tests for entrylinks-export.py
   - `export_entry_links()` - Export EntryLinks from Dataplex to Google Sheets
   - `_cache_listed_terms()` - Reuse the listed terms when resolving their display names
   - `convert_entry_links_to_rows()` - Deduplicate fetched EntryLinks and convert them to rows
   - `main()` - Main entry point with argument parsing and error handling

2. **test_entrylinks_import.py** - Tests for entrylinks-import.py
   - `confirm_import()` - Report rows that can't be imported and missing entries, and ask to continue
   - `check_entry_existence()` - Verify entries exist in Dataplex
   - `lookup_entries()` - Batch lookup of entries
   - `convert_spreadsheet_to_entrylinks()` - Convert spreadsheet data to EntryLink models (rows resolved in parallel, in row order)
   - `build_entry_link()` - Build EntryLink from SpreadsheetRow
   - `build_entry_references()` - Build entry references with proper types
   - `extract_entrylink_components()` - Extract entry type and group from entry name
   - `group_entrylinks_by_entry_type_and_entry_group()` - Group for import organization
   - `group_entrylinks()` - Main grouping function
   - `main()` - Main import workflow

### API and Service Layers
3. **test_api_layer.py** - Tests for api_layer.py
   - `authenticate_dataplex()` - Dataplex API authentication
   - `authenticate_sheets()` - Google Sheets API authentication
   - `list_glossary_terms()` - List terms with pagination
   - `lookup_entry_links_for_term()` - Lookup EntryLinks for a term
   - `build_entry_lookup_url()` - Build API URL
   - `lookup_entry()` - Lookup single entry
   - `get_spreadsheet_id()` - Extract ID from URL
   - `read_from_sheet()` - Read spreadsheet data
   - `write_to_sheet()` - Write data to spreadsheet
   - `get_project_number()` - Fetch project number from project ID
   - `lookup_term_by_display_identifier()`, `lookup_entry_by_fqn()`, `get_entry_fqn()` - Resolve EntryLinks sheet cells to entries and back (cached, thread-safe)
   - `cache_glossary_terms()` - Serve listed terms from the cache instead of fetching them again
   - `is_transient_error()` - Detect network and server errors that outlasted the retries (these stop the import)
   - Helper functions for project operations

4. **test_dataplex_dao.py** - Tests for dataplex_dao.py
   - `get_dataplex_service()` - Get authenticated Dataplex service
   - `create_metadata_job()` - Create Dataplex metadata import job
   - `poll_metadata_job()` - Poll job status until completion
   - `create_and_monitor_job()` - Complete job lifecycle management

5. **test_gcs_dao.py** - Tests for gcs_dao.py
   - `prepare_gcs_bucket()` - Clear and prepare bucket for upload
   - `upload_to_gcs()` - Upload file to Cloud Storage
   - `clear_bucket()` - Remove all files from bucket
   - `check_metadata_job_creation_for_bucket()` - Verify permissions
   - `check_all_buckets_permissions()` - Batch permission checks

### Utility Modules
6. **test_argument_parser.py** - Tests for argument_parser.py
   - `extract_glossary_info_from_url()` - Parse glossary URL
   - `configure_export_entrylinks_argument_parser()` - Setup export args
   - `get_export_entrylinks_arguments()` - Parse export arguments
   - `configure_import_entrylinks_argument_parser()` - Setup import args
   - `get_import_entrylinks_arguments()` - Parse import arguments

7. **test_business_glossary_utils.py** - Tests for business_glossary_utils.py
   - `extract_glossary_name()` - Extract glossary name from URL
   - `generate_entry_name_from_term_name()` - Generate entry ID for term
   - `normalize_id()` - Normalize strings for use as IDs
   - `get_entry_link_id()` - Generate deterministic EntryLink ID

8. **test_file_utils.py** - Tests for file_utils.py
   - `ensure_dir()` - Create directories recursively
   - `parse_json_line()` - Parse single JSON line
   - `read_first_json_line()` - Read first line from file
   - `is_file_empty()` - Check if file is empty
   - `move_file_to_imported_folder()` - Move/delete processed files
   - `get_link_type()` - Extract link type from file
   - `get_entry_group()` - Extract entry group from file
   - `write_entrylinks_to_file()` - Write EntryLinks in JSON lines format

9. **test_sheet_utils.py** - Tests for sheet_utils.py
   - `entry_link_to_row()` - Convert an EntryLink to a spreadsheet row
   - `is_redacted_entry_link()` - Detect EntryLinks to entries the caller can't view
   - `extract_column_indices()` - Find column positions from headers
   - `rows_to_entry_link_dicts()` - Convert rows to EntryLink dictionaries

10. **test_import_utils.py** - Tests for import_utils.py
    - `create_import_json_files()` - Create grouped JSON files for import
    - `get_referenced_scopes()` - Extract project scopes from references
    - `extract_project_id_from_entrylink()` - Extract project ID from EntryLink
    - `process_import_file()` - Process single file through import pipeline
    - `_process_files_for_bucket()` - Worker function for bucket processing
    - `run_import_files()` - Orchestrate parallel import across buckets

11. **test_payloads.py** - Tests for payloads.py
    - `build_import_spec_base()` - Build base import specification
    - `extract_job_location_from_entry_group()` - Extract location for job
    - `build_definition_entrylink_payload()` - Build payload for definition links
    - `build_synonym_related_entrylink_payload()` - Build payload for synonym/related links
    - `build_entrylink_payload()` - Wrapper to choose payload type
    - `build_glossary_payload()` - Build payload for glossary import
    - `build_payload()` - Main payload builder

## Running the Tests

### Run All Tests
```bash
cd /Users/greesh/EL\ Utility/dataplex-labs/dataplex-quickstart-labs/00-resources/scripts/python/business-glossary-import/dataplex-glossary
python -m pytest tests/ -v
```

### Run Specific Test File
```bash
python -m pytest tests/test_entrylinks_export.py -v
```

### Run Specific Test Class
```bash
python -m pytest tests/test_api_layer.py::TestAuthentication -v
```

### Run Specific Test Method
```bash
python -m pytest tests/test_file_utils.py::TestParseJsonLine::test_parse_json_line_success -v
```

### Run with Coverage
```bash
python -m pytest tests/ --cov=. --cov-report=html
```

## Test Structure

Each test file follows a consistent structure:

1. **Imports** - Uses `importlib.util` to handle module files with hyphens
2. **Test Classes** - One class per function or logical group
3. **Setup Methods** - `setUp()` for common test fixtures
4. **Test Methods** - Multiple test cases per function covering:
   - Success cases
   - Failure cases
   - Edge cases (empty input, None, invalid data)
   - Error handling

## Mocking Strategy

The tests use `unittest.mock` extensively to:

- Mock external API calls (Dataplex, Sheets, GCS)
- Mock file I/O operations
- Mock authentication
- Isolate units under test

## Test Naming Convention

- `test_<function_name>_success` - Happy path
- `test_<function_name>_failure` - Expected failures
- `test_<function_name>_<edge_case>` - Specific edge cases

## Dependencies

The tests require:
- `unittest` (Python standard library)
- `pytest` (recommended test runner)
- `pytest-cov` (for coverage reports)

Install test dependencies:
```bash
pip install pytest pytest-cov
```

## Notes

- All tests use mocks to avoid actual API calls or file operations
- Temporary files/directories are created using `tempfile` module
- Import handling uses `importlib.util.spec_from_file_location()` to support filenames with hyphens
- Tests are designed to run independently and in any order
