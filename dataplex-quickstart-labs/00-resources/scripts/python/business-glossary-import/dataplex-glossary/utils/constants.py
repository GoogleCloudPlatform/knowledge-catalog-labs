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

# --- BigQuery Candidate Locations ---
BIGQUERY_CANDIDATE_LOCATIONS = ["us", "us-central1", "eu", "us-east1", "us-west1", "global"]

# --- Google Sheets Headers ---
ENTRYLINK_SHEET_HEADERS = ["Entry link type", "Source Name", "Source ID", "Column", "Target Name", "Target ID"]
LEGACY_ENTRYLINK_SHEET_HEADERS = ["entry_link_type", "source_entry", "target_entry", "source_path"]
FOUR_COLUMN_ENTRYLINK_SHEET_HEADERS = ["Entry link type", "Source", "Column", "Target"]

TYPE_HEADER_ALIASES = ["entry link type", "entry_link_type", "link_type", "type"]
SOURCE_ID_HEADER_ALIASES = ["source id", "source_id", "sourceid", "source resource name", "source entry id"]
SOURCE_NAME_HEADER_ALIASES = [
    "source name", "sourcename", "source display name", "sourcedisplayname",
    "source", "source_entry", "sourceentry",
]
TARGET_ID_HEADER_ALIASES = ["target id", "target_id", "targetid", "target resource name", "target entry id"]
TARGET_NAME_HEADER_ALIASES = [
    "target name", "targetname", "target display name", "targetdisplayname",
    "target", "target_entry", "targetentry",
]
COLUMN_HEADER_ALIASES = ["column", "column name", "source_path", "sourcepath", "path"]