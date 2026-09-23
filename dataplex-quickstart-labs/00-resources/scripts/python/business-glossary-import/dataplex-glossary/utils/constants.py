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

# Aspect keys as they appear in an Entry's 'aspects' map, e.g.
# '655216118709.global.overview' or 'dataplex-types.global.overview'.
ASPECT_KEY_PATTERN = re.compile(
    r"^(?P<project>[^.]+)\.(?P<location>[^.]+)\.(?P<aspect_type_id>.+)$"
)

# --- System vs custom aspects ---
# System aspects are already represented by dedicated Sheet 1 columns (or are
# purely structural) and MUST NOT be duplicated into Sheet 2.
#
# The project component of an aspect key differs by tool: export sees the
# numeric project number ('655216118709.global.overview') while import writes
# the friendly project id ('dataplex-types.global.overview'). Matching is
# therefore done on the '<location>.<aspect_type_id>' SUFFIX, never the full key.
SYSTEM_ASPECT_TYPE_IDS = frozenset({
    ASPECT_OVERVIEW,
    ASPECT_CONTACTS,
    ASPECT_TYPE_TERM,
    ASPECT_TYPE_CATEGORY,
})

SYSTEM_ASPECT_KEY_SUFFIXES = frozenset(
    f"{LOCATION_TYPE_GLOBAL}.{aspect_type_id}" for aspect_type_id in SYSTEM_ASPECT_TYPE_IDS
)

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