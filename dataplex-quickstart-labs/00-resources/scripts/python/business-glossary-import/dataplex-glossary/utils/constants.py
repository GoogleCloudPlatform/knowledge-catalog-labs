"""Constants used by the Business Glossary Migration Tool."""

import re

# --- URLs ---
DATACATALOG_BASE_URL = "https://datacatalog.googleapis.com/v2"
DATAPLEX_BASE_URL = "https://dataplex.googleapis.com/v1"
SEARCH_BASE_URL = "https://datacatalog.googleapis.com/v1/catalog:search"
CLOUD_RESOURCE_MANAGER_BASE_URL = "https://cloudresourcemanager.googleapis.com/v3"

# --- Dataplex Entry Group Constants ---
DATAPLEX_SYSTEM_ENTRY_GROUP = "@dataplex"
BIGQUERY_SYSTEM_ENTRY_GROUP = "@bigquery"

# --- Regex Patterns ---
# Glossary and Term Patterns
GLOSSARY_URL_PATTERN = re.compile(r".*dp-glossaries/projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/glossaries/(?P<glossary_id>[^/?#]+).*")
GLOSSARY_NAME_PATTERN = re.compile(r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/glossaries/(?P<glossary_id>[^/?#&]+)")
TERM_NAME_PATTERN = re.compile(r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/glossaries/(?P<glossary_id>[^/]+)/terms/(?P<term_id>[^/]+)")
CATEGORY_NAME_PATTERN = re.compile(r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/glossaries/(?P<glossary_id>[^/?#]+)/categories/(?P<category_id>[^/]+)")

# Entry Patterns
ENTRY_NAME_PATTERN = re.compile(r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/entryGroups/(?P<entry_group>[^/]+)/entries/(?P<entry_id>.*)")
CATALOG_ENTRY_PATTERN = re.compile(r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/entryGroups/(?P<entry_group>[^/]+)/entries/.*")

# EntryLink Patterns
ENTRYLINK_NAME_PATTERN = re.compile(r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/entryGroups/(?P<entry_group>[^/]+)/entryLinks/(?P<entrylink_id>[^/]+)")
ENTRYLINK_TYPE_PATTERN = re.compile(r"projects/(?:655216118709|dataplex-types)/locations/global/entryLinkTypes/(?P<link_type>[^/]+)")

# Source Entry Pattern (for extracting project/location/entryGroup from full entry paths)
SOURCE_ENTRY_PATTERN = re.compile(r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/entryGroups/(?P<entry_group>[^/]+)/entries/")

# Google Sheets Pattern (captures optional gid parameter for specific sheet)
SPREADSHEET_URL_PATTERN = re.compile(r"https://docs\.google\.com/spreadsheets/d/(?P<spreadsheet_id>[^/]+)(?:.*[?&#]gid=(?P<gid>\d+))?")

# Validation Patterns
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$")
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
PARENT_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
LABEL_PATTERN = re.compile(r"^[a-z0-9_-]+$")

# Project Pattern
PROJECT_PATTERN = re.compile(r"projects/(?P<project_number>\d+)")

# --- Location Constants ---
# Location type identifiers
LOCATION_TYPE_REGIONAL = "regional"
LOCATION_TYPE_GLOBAL = "global"
LOCATION_TYPE_MULTI_REGIONAL = "multi_regional"

# Multi-regional location identifiers
# NOTE: Multi-regional locations ('us', 'eu') are distinct location types,
# NOT aggregations of regional endpoints. 'us' is NOT 'us-central1' + 'us-east1'.
MULTI_REGION_US = "us"
MULTI_REGION_EU = "eu"

# Set of multi-regional identifiers (for quick lookup)
MULTI_REGIONAL_LOCATIONS = {MULTI_REGION_US, MULTI_REGION_EU}


# --- Dataplex Constants ---
# Dataplex Aspects
ASPECT_CONTACTS = "contacts"
ASPECT_OVERVIEW = "overview"

# Dataplex Link Types
DP_LINK_TYPE_DEFINITION = "definition"
DP_LINK_TYPE_RELATED = "related"
DP_LINK_TYPE_SYNONYM = "synonym"

# Entry Reference Types
ENTRY_REFERENCE_TYPE_SOURCE = "SOURCE"
ENTRY_REFERENCE_TYPE_TARGET = "TARGET"

# Dataplex Entry Types / Aspect Prefixes
DP_TYPE_GLOSSARY_CATEGORY = "glossary-category"
DP_TYPE_GLOSSARY_TERM = "glossary-term"
DP_TYPE_GLOSSARY = "glossary"
ASPECT_TYPE_CATEGORY = "glossary-category-aspect"
ASPECT_TYPE_TERM = "glossary-term-aspect"

# --- General Constants ---
CATEGORIES = "categories"
TERMS = "terms"
MAX_DESC_SIZE_BYTES = 120 * 1024
MAX_WORKERS = 5
PAGE_SIZE = 1000

# Throttling: 240ms delay between consecutive lookupEntryLinks API calls.
# With 5 threads, each thread effectively waits 1200ms (240ms * 5),
# yielding ~250 QPM — safely within the 500 QPM per-project/user/region quota.
API_CALL_DELAY_SECONDS = 0.24

# Minimum delay between consecutive searchEntries calls across all threads: at most 600 per
# minute, below the default Dataplex search quota of 900 per minute per project per user
# (1,200 per minute per project).
SEARCH_API_CALL_DELAY_SECONDS = 0.1

# Minimum delay between consecutive lookupEntry calls across all threads: at most 600 per minute,
# well within the default quota for reading entries.
ENTRY_READ_API_CALL_DELAY_SECONDS = 0.1

# Symmetric link types (A,B) and (B,A) are equivalent
SYMMETRIC_LINK_TYPES = {"synonym", "related"}
PROJECT_NUMBER = "655216118709"

# Unlaunched prod locations and non-catalog dual-region storage IDs
EXCLUDED_LOCATIONS = ["asia-southeast3", "eur3", "nam5", "nam7"]

# -- BACKOFF Constants ---
MAX_ATTEMPTS = 10
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 300
MAX_RETRY_DURATION_SECONDS = 600  # 10 minutes total retry window for transient errors

# --- Filesystem Constants ---
LOGS_DIRECTORY = "logs"
SUMMARY_DIRECTORY = "summary"
ARCHIVE_DIRECTORY = "archive"
PROCESSED_DIRECTORY = "processed"

MAX_BUCKETS = 20
MAX_POLLS = 12*12  # 12 hours
POLL_INTERVAL_MINUTES = 5
QUEUED_TIMEOUT_MINUTES = 10

LINK_TYPES = {
    DP_LINK_TYPE_DEFINITION: 'projects/dataplex-types/locations/global/entryLinkTypes/definition',
    DP_LINK_TYPE_SYNONYM: 'projects/dataplex-types/locations/global/entryLinkTypes/synonym',
    DP_LINK_TYPE_RELATED: 'projects/dataplex-types/locations/global/entryLinkTypes/related'
}

# --- BigQuery Patterns ---
# Plain BigQuery FQN: bigquery:project.dataset or bigquery:project.dataset.table
BIGQUERY_FQN_PATTERN = re.compile(r"^bigquery:(?P<project_id>[^.:`]+)\.(?P<dataset_id>[^.:`]+)(?:\.(?P<table_id>[^.:`]+))?$")
# Entry ID of a BigQuery dataset or table entry in the @bigquery entry group
BIGQUERY_ENTRY_ID_PATTERN = re.compile(r"bigquery\.googleapis\.com/projects/(?P<project_id>[^/]+)/datasets/(?P<dataset_id>[^/]+)(?:/[^/]+/(?P<table_id>[^/]+))?$")
# Entry ID of a BigQuery dataset or table whose names need no backticks in its FQN, which is then
# bigquery:project.dataset[.table]. Other entries have other FQNs: date-sharded tables (entry IDs
# ending in '@BigQueryDateShardedTable'), routines, and names with reserved characters.
PLAIN_BIGQUERY_ENTRY_ID_PATTERN = re.compile(
    r"^bigquery\.googleapis\.com/projects/(?P<project_id>[a-z][a-z0-9-]*)"
    r"/datasets/(?P<dataset_id>[A-Za-z0-9_]+)(?:/tables/(?P<table_id>[A-Za-z0-9_-]+))?$"
)

# --- Google Sheets Headers ---
ENTRYLINK_SHEET_HEADERS = ["Entry link type", "Source Name", "Source ID", "Column", "Target Name", "Target ID"]

# Accepted (lowercased) header names. The snake_case aliases are the headers
# written by earlier versions of the export (entry_link_type, source_entry,
# target_entry, source_path), so those sheets can still be imported.
TYPE_HEADER_ALIASES = ["entry link type", "entry_link_type"]
SOURCE_NAME_HEADER_ALIASES = ["source name", "source_entry"]
SOURCE_ID_HEADER_ALIASES = ["source id"]
COLUMN_HEADER_ALIASES = ["column", "source_path"]
TARGET_NAME_HEADER_ALIASES = ["target name", "target_entry"]
TARGET_ID_HEADER_ALIASES = ["target id"]

# =============================================================================
# Aspect Sheet (Sheet 2) Constants
# =============================================================================
# Custom Dataplex Aspects are carried in a SECOND sheet/CSV with a normalized
# (id, Aspect name, Aspect value) schema, leaving the 14-column glossary sheet
# (Sheet 1) completely untouched.

ASPECT_ID_COLUMN = "id"
ASPECT_NAME_COLUMN = "Aspect name"
ASPECT_VALUE_COLUMN = "Aspect value"

ASPECT_SHEET_HEADERS = [ASPECT_ID_COLUMN, ASPECT_NAME_COLUMN, ASPECT_VALUE_COLUMN]

# Default worksheet/tab name holding the aspect rows. Can be overridden at
# runtime with the GLOSSARY_ASPECTS_SHEET_NAME environment variable (the
# glossary import/export scripts are prompt-driven rather than argparse-driven).
ASPECTS_SHEET_NAME = "Sheet2"
ASPECTS_SHEET_NAME_ENV_VAR = "GLOSSARY_ASPECTS_SHEET_NAME"

# --- Aspect identifier patterns ---
# Full resource-path form:
#   projects/{project}/locations/{location}/aspectTypes/{aspect_type}/{field}
ASPECT_RESOURCE_PATTERN = re.compile(
    r"^projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)"
    r"/aspectTypes/(?P<aspect_type_id>[^/]+)(?:/(?P<field_name>.+))?$"
)

# Aspect type resource without a field:
#   projects/{project}/locations/{location}/aspectTypes/{aspect_type}
ASPECT_TYPE_RESOURCE_PATTERN = re.compile(
    r"^projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)"
    r"/aspectTypes/(?P<aspect_type_id>[^/]+)$"
)

# Matches a Google Cloud location id. Deliberately strict: every regional id
# ends with a digit ('us-central1', 'europe-west4', 'northamerica-northeast1'),
# which is what lets us tell 'my-project.us-central1.custom-gov.tier'
# (qualified) apart from 'custom-gov.owner.email' (dotted record sub-field).
LOCATION_ID_PATTERN = re.compile(r"^(global|us|eu|asia|[a-z]+-[a-z]+\d+)$")

# --- System vs custom aspects ---
# System aspects are already represented by dedicated Sheet 1 columns (or are
# purely structural) and MUST NOT be duplicated into Sheet 2.
#
# Matching is done on the aspect type id alone, whatever the project and
# location. This mirrors the Dataplex glossary import validation
# (ValidateGlossaryTaxonomyItemFn), which treats any aspect whose type id is
# 'overview' or 'contacts' as the built-in aspect and rejects the 'glossary-*'
# structural aspects regardless of their project. It also covers both project
# spellings: export sees the numeric project number
# ('655216118709.global.overview') while import writes the friendly project id
# ('dataplex-types.global.overview').
ASPECT_TYPE_GLOSSARY = "glossary-aspect"

SYSTEM_ASPECT_TYPE_IDS = frozenset({
    ASPECT_OVERVIEW,
    ASPECT_CONTACTS,
    ASPECT_TYPE_TERM,
    ASPECT_TYPE_CATEGORY,
    ASPECT_TYPE_GLOSSARY,
})

# Projects that own the built-in (1P) Dataplex aspect types.
SYSTEM_ASPECT_PROJECTS = frozenset({PROJECT_NUMBER, "dataplex-types"})

# --- AspectType MetadataTemplate datatypes ---
# See google/cloud/dataplex/v1/catalog.proto (AspectType.MetadataTemplate).
ASPECT_FIELD_TYPE_STRING = "string"
ASPECT_FIELD_TYPE_INT = "int"
ASPECT_FIELD_TYPE_BOOL = "bool"
ASPECT_FIELD_TYPE_DOUBLE = "double"
ASPECT_FIELD_TYPE_DATETIME = "datetime"
ASPECT_FIELD_TYPE_ENUM = "enum"
ASPECT_FIELD_TYPE_ARRAY = "array"
ASPECT_FIELD_TYPE_MAP = "map"
ASPECT_FIELD_TYPE_RECORD = "record"

ASPECT_FIELD_TYPES = frozenset({
    ASPECT_FIELD_TYPE_STRING,
    ASPECT_FIELD_TYPE_INT,
    ASPECT_FIELD_TYPE_BOOL,
    ASPECT_FIELD_TYPE_DOUBLE,
    ASPECT_FIELD_TYPE_DATETIME,
    ASPECT_FIELD_TYPE_ENUM,
    ASPECT_FIELD_TYPE_ARRAY,
    ASPECT_FIELD_TYPE_MAP,
    ASPECT_FIELD_TYPE_RECORD,
})

# Accepted spellings when coercing a cell into a bool.
BOOL_TRUE_LITERALS = frozenset({"true", "yes", "y", "t"})
BOOL_FALSE_LITERALS = frozenset({"false", "no", "n", "f"})
# '1'/'0' are only treated as booleans when the AspectType schema declares the
# field as 'bool'; with no schema available they coerce to int (numeric wins).
BOOL_NUMERIC_TRUE_LITERALS = frozenset({"1"})
BOOL_NUMERIC_FALSE_LITERALS = frozenset({"0"})

INTEGER_LITERAL_PATTERN = re.compile(r"^[+-]?\d+$")
DECIMAL_LITERAL_PATTERN = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")

# Maximum nesting depth (in dotted path segments below the aspect type) that is
# flattened into its own Sheet 2 row. Anything deeper is serialized as JSON.
# e.g. 'custom-gov.owner.email' is flattened; {'owner': {'meta': {...}}} yields
# 'custom-gov.owner.meta' holding a JSON object string.
MAX_ASPECT_FLATTEN_DEPTH = 2


# =============================================================================
# Glossary Update (import/glossary-update.py) Constants
# =============================================================================
# The update script reads the same sheet as the full import (glossary-import.py):
# these 14 columns, plus an optional Operation column.
GLOSSARY_SHEET_HEADERS = [
    "id", "parent", "display_name", "description", "overview", "type",
    "contact1_email", "contact1_name", "contact2_email", "contact2_name",
    "label1_key", "label1_value", "label2_key", "label2_value",
]

# Optional column. Its header is matched case-insensitively ('Operation').
# Blank: create the row's term/category, or update it when it changed.
# 'delete': delete the term/category (a category is deleted with everything in it).
OPERATION_COLUMN = "operation"
OPERATION_DELETE = "delete"

GLOSSARY_TERM_TYPE = "TERM"
GLOSSARY_CATEGORY_TYPE = "CATEGORY"

# A glossary holds at most 5,000 terms and 200 categories.
GLOSSARY_MAX_TERMS = 5000
GLOSSARY_MAX_CATEGORIES = 200
# The Dataplex API rejects a category creation or move that nests categories more than 3 deep
# (CreateGlossaryCategory / UpdateGlossaryCategory). Terms can be in any category, so a term
# can be up to 4 levels below the glossary: glossary > category > category > category > term.
SERVER_MAX_CATEGORY_DEPTH = 3

# Aspect keys written by the update script (the full import uses the same keys).
OVERVIEW_ASPECT_KEY = "dataplex-types.global.overview"
CONTACTS_ASPECT_KEY = "dataplex-types.global.contacts"
CONTACT_ROLE_STEWARD = "steward"

# Dataplex quotas (standard tier) for the calls made by the update script, per project and region:
#   - glossary writes (create/update/delete of terms and categories): 250 per minute per project
#     and 100 per minute per user;
#   - glossary reads (get/list of terms and categories): 500 per minute per project and 200 per
#     minute per user;
#   - catalog entry writes (entries.patch, for aspects): 1,500 per minute per project.
# The delays below are the minimum time between two such calls across all threads, chosen to stay
# under the per-user quotas. Calls that still get a 429 (quota exceeded) are retried with
# exponential backoff (see retry_utils.execute_with_retry).
DEFAULT_GLOSSARY_WRITES_PER_MINUTE = 90  # see --max-qpm in glossary-update.py
GLOSSARY_WRITE_API_CALL_DELAY_SECONDS = 60 / DEFAULT_GLOSSARY_WRITES_PER_MINUTE
GLOSSARY_READ_API_CALL_DELAY_SECONDS = 60 / 180
ENTRY_WRITE_API_CALL_DELAY_SECONDS = 60 / 600

# After creating a term or category, Dataplex creates its catalog entry asynchronously (usually
# within seconds, sometimes after more than a minute). Its aspects (overview, contacts, custom
# aspects) can only be written once the entry exists.
ENTRY_WAIT_TIMEOUT_SECONDS = 180
# A term or category created this recently whose entry can't be read yet is waited for (up to
# ENTRY_WAIT_TIMEOUT_SECONDS) before planning, e.g. when the update is run again right after
# creating it. An older one without a readable entry is reported (missing permission).
ENTRY_PENDING_WINDOW_SECONDS = 15 * 60
