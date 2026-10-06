"""API Layer for Dataplex Glossary Operations."""

import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests
from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from . import api_call_utils, business_glossary_utils, logging_utils
from .api_call_utils import fetch_api_response
from .constants import (
    API_CALL_DELAY_SECONDS,
    BIGQUERY_FQN_PATTERN,
    BIGQUERY_SYSTEM_ENTRY_GROUP,
    CLOUD_RESOURCE_MANAGER_BASE_URL,
    DATAPLEX_BASE_URL,
    ENTRY_NAME_PATTERN,
    EXCLUDED_LOCATIONS,
    PAGE_SIZE,
    PLAIN_BIGQUERY_ENTRY_ID_PATTERN,
    PROJECT_PATTERN,
    TERM_NAME_PATTERN,
)
from .error import (
    AmbiguousTermError,
    DataplexAPIError,
    EntryFQNNotFoundError,
    GlossaryNotFoundError,
    InvalidEntryIdFormatError,
    InvalidTermIdentifierError,
    InvalidTermNameError,
    TermNameMismatchError,
    TermNotFoundError,
    TransientAPIError,
)
from .retry_utils import RETRYABLE_HTTP_STATUS_CODES, execute_with_retry, is_retryable_google_api_error

logger = logging_utils.get_logger()

# Module-level caches shared across worker threads. Values are filled through
# _get_or_fetch; a value of None records a lookup that found nothing, and a
# _Failure records a lookup that failed for a reason retrying won't fix.
_locations_cache: Dict[str, List[str]] = {}
_glossary_cache: Dict[str, Dict] = {}
_term_cache: Dict[str, Dict] = {}
_project_glossaries_cache: Dict[str, List[Dict]] = {}
_glossary_terms_cache: Dict[str, Dict[str, Dict]] = {}  # glossary name -> {term ID: term}
_fqn_to_entry_cache: Dict[str, Optional[Dict]] = {}
_entry_to_fqn_cache: Dict[str, Optional[str]] = {}
_project_id_to_number_cache: Dict[str, str] = {}
_project_number_to_id_cache: Dict[str, str] = {}
# Entry names confirmed to exist while resolving sheet rows (no need to look them up again).
_known_entry_names: set = set()
# (project ID, dataset ID) -> location of the BigQuery dataset's entries, learned from resolved entries.
_bigquery_dataset_locations: Dict[tuple, str] = {}

_MISSING = object()
_key_locks: Dict[tuple, threading.RLock] = {}
_key_locks_guard = threading.Lock()


class _Failure:
    """A cached lookup failure; its error is raised again for later lookups of the same key."""
    __slots__ = ('error',)

    def __init__(self, error: Exception):
        self.error = error


def clear_caches():
    """Clear all in-memory caches (useful between batch runs and in unit tests)."""
    for cache in (
        _locations_cache, _glossary_cache, _term_cache, _project_glossaries_cache,
        _glossary_terms_cache, _fqn_to_entry_cache, _entry_to_fqn_cache,
        _project_id_to_number_cache, _project_number_to_id_cache, _known_entry_names,
        _bigquery_dataset_locations,
    ):
        cache.clear()
    with _key_locks_guard:
        _key_locks.clear()


def is_transient_error(error: BaseException) -> bool:
    """Whether `error`, or an error it was raised from, is a network, 429 or 5xx failure.

    API calls are already retried for up to MAX_RETRY_DURATION_SECONDS, so such an error means
    the service or the connection stayed unavailable. The errors that api_layer raises while
    handling another error keep it as their __context__ (or __cause__), which is checked too.
    """
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, TransientAPIError) or is_retryable_google_api_error(current):
            return True
        pending.extend((current.__cause__, current.__context__))
    return False


def _lock_for(cache: dict, key: Any) -> threading.RLock:
    """Return the lock that serializes fetching `key` into `cache`."""
    lock_key = (id(cache), key)
    with _key_locks_guard:
        lock = _key_locks.get(lock_key)
        if lock is None:
            lock = _key_locks[lock_key] = threading.RLock()
        return lock


def _get_or_fetch(cache: dict, key: Any, fetch: Callable[[], Any]) -> Any:
    """Return cache[key], calling fetch() at most once per key across threads.

    Different keys are fetched in parallel. If fetch() raises, the error is cached and raised
    again by later calls for the same key, unless it is transient (see is_transient_error):
    then nothing is cached and a later call tries again.
    """
    value = cache.get(key, _MISSING)
    if value is _MISSING:
        with _lock_for(cache, key):
            value = cache.get(key, _MISSING)
            if value is _MISSING:
                try:
                    value = fetch()
                except Exception as e:
                    if not is_transient_error(e):
                        cache[key] = _Failure(e)
                    raise
                cache[key] = value
    if isinstance(value, _Failure):
        # Drop the previous traceback: re-raising the same error object would otherwise add
        # frames to it on every call.
        raise value.error.with_traceback(None)
    return value


# Global throttle lock for lookupEntryLinks API calls.
# Ensures a minimum delay of API_CALL_DELAY_SECONDS (240ms) between
# consecutive calls across all threads, keeping within the 500 QPM quota.
_entry_links_throttle_lock = threading.Lock()
_last_entry_links_call_time = 0.0


def initialize_locations_cache(user_project: str) -> List[str]:
    """Pre-fetch and cache the list of supported locations.
    
    Args:
        user_project: The project ID to fetch locations for.
    
    Returns:
        List of all supported location IDs.
    """
    all_locations = list_supported_locations(user_project)
    logger.debug(f"Initialized locations cache with {len(all_locations)} locations")
    return all_locations


def authenticate_dataplex() -> build:
    """Authenticate with Dataplex API."""
    logger.debug("Authenticating with Dataplex API using Application Default Credentials...")
    try:
        creds, _ = default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        service = build('dataplex', 'v1', credentials=creds, cache_discovery=False)
        logger.debug("Dataplex API service built successfully")
        return service
    except Exception as e:
        logger.error(f"Dataplex auth error: {e}")
        raise DataplexAPIError(f"Dataplex auth error: {e}")


_thread_local = threading.local()


def get_dataplex_service() -> build:
    """Get or create a thread-local Dataplex service instance to prevent SSL socket corruption in multi-threaded execution."""
    if not hasattr(_thread_local, 'dataplex_service') or _thread_local.dataplex_service is None:
        _thread_local.dataplex_service = authenticate_dataplex()
    return _thread_local.dataplex_service


def list_glossary_terms(dataplex_service: build, glossary_name: str) -> List[Dict]:
    """Lists terms from a Dataplex glossary with pagination support."""
    all_terms = []
    logger.debug(f"Request: glossaries.terms.list(parent={glossary_name})")
    
    terms_request = dataplex_service.projects().locations().glossaries().terms().list(
        parent=glossary_name, pageSize=1000
    )
    
    while terms_request:
        try:
            page_response = execute_with_retry(
                terms_request.execute, 
                f"List glossary terms for {glossary_name}"
            )
        except Exception as terms_error:
            raise DataplexAPIError(f"Error while listing glossary terms for {glossary_name}: {terms_error}")

        all_terms.extend(page_response.get('terms', []))
        terms_request = dataplex_service.projects().locations().glossaries().terms().list_next(
            terms_request, page_response
        )
    
    logger.debug(f"Response: {len(all_terms)} terms for {glossary_name}")
    return all_terms

def parse_entry_name(entry_name: str) -> tuple:
    """Parse a full entry resource name to extract project, location, entry group, and entry ID."""
    match = ENTRY_NAME_PATTERN.match(entry_name)
    if not match:
        logger.error(f"Invalid entry name format: {entry_name}")
        raise InvalidEntryIdFormatError(f"Invalid entry name format: {entry_name}")
    return (
        match.group('project_id'),
        match.group('location_id'),
        match.group('entry_group'),
        match.group('entry_id'),
    )


def _throttle_entry_links_call():
    """Enforce minimum delay between consecutive lookupEntryLinks API calls.
    
    With API_CALL_DELAY_SECONDS=0.24s and 5 threads, each thread effectively
    waits ~1.2s, yielding ~250 QPM — safely within the 500 QPM quota.
    """
    global _last_entry_links_call_time
    with _entry_links_throttle_lock:
        now = time.time()
        elapsed = now - _last_entry_links_call_time
        if elapsed < API_CALL_DELAY_SECONDS:
            time.sleep(API_CALL_DELAY_SECONDS - elapsed)
        _last_entry_links_call_time = time.time()


def _fetch_entry_links_page(
    term_entry_name: str, 
    project_id: str, 
    location_id: str, 
    billing_project: str, 
    page_token: str = None
) -> tuple:
    """Fetch a single page of entry links. Returns (entry_links, next_page_token, error_msg).
    
    Applies throttling to stay within the 500 QPM lookupEntryLinks quota.
    """
    _throttle_entry_links_call()
    lookup_url = build_entry_link_lookup_url(term_entry_name, project_id, location_id, page_token=page_token)
    
    api_response = fetch_api_response(
        method=requests.get,
        url=lookup_url,
        project_id=billing_project
    )
    
    if api_response.get('error_msg'):
        return [], None, api_response['error_msg']
    
    response_data = api_response.get('json', {})
    page_entry_links = response_data.get('entryLinks', [])
    next_page_token = response_data.get('nextPageToken')
    
    return page_entry_links, next_page_token, None


def lookup_entry_links_for_term(
    term_entry_name: str, 
    billing_project: str,
    location: Optional[str] = None
) -> Optional[List[Dict]]:
    """Looks up EntryLinks for a glossary term with pagination."""
    target_location = location or "unknown"
    try:
        project_id, entry_location, _, _ = parse_entry_name(term_entry_name)
        target_location = location if location else entry_location        
        all_entry_links = []
        current_page_token = None
        
        while True:
            page_links, next_token, error_message = _fetch_entry_links_page(
                term_entry_name, project_id, target_location, billing_project, current_page_token
            )            
            if error_message:
                logger.error(f"Error looking up entry links at {target_location} for {term_entry_name}: {error_message}")
                break            
            if page_links:
                all_entry_links.extend(page_links)            
            current_page_token = next_token
            if not current_page_token:
                break
        
        logger.debug(f"Entry links found for {term_entry_name} in {target_location}: {all_entry_links}")
        return all_entry_links if all_entry_links else None
        
    except Exception as lookup_error:
        logger.error(f"Error while looking up entry links for {term_entry_name} in {target_location}: {lookup_error}")
        return None


def build_entry_link_lookup_url(
    term_entry_name: str, 
    project_id: str, 
    location_id: str, 
    page_size: int = PAGE_SIZE,
    page_token: Optional[str] = None
) -> str:
    """Builds the lookupEntryLinks API URL with pagination parameters."""
    url = f"{DATAPLEX_BASE_URL}/projects/{project_id}/locations/{location_id}:lookupEntryLinks?entry={term_entry_name}&pageSize={page_size}"
    if page_token:
        url += f"&pageToken={page_token}"
    return url

def lookup_entry(dataplex_service: build, entry_name: str, project_location_name: str) -> Optional[Dict]:
    """Looks up an entry using the Dataplex API."""
    logger.debug(f"Request: lookupEntry(entry={entry_name}, location={project_location_name})")
    try:
        request = dataplex_service.projects().locations().lookupEntry(
            name=project_location_name, entry=entry_name, view="ALL"
        )
        response = execute_with_retry(request.execute, f"Lookup entry {entry_name}")
        logger.debug(f"Response: found entry {response.get('name', 'N/A')}")
        return response
    except HttpError as e:
        status_code = e.resp.status if hasattr(e, 'resp') else None
        if status_code == 404:
            return None
        if status_code in (401, 403):
            logger.warning(f"Permission denied for entry {entry_name}")
            return None
        raise


def _get_project_url(project_id: str) -> str:
    """Builds the Cloud Resource Manager project URL."""
    return f"{CLOUD_RESOURCE_MANAGER_BASE_URL}/projects/{project_id}"


def _fetch_project_info(project_id: str, user_project: str) -> dict:
    """Calls the Cloud Resource Manager API and returns the project JSON payload.

    Raises:
        TransientAPIError: If the call kept failing with a network, 429 or 5xx error.
        DataplexAPIError: If the call failed for another reason (e.g. permission denied).
    """
    url = _get_project_url(project_id)
    response = api_call_utils.fetch_api_response(requests.get, url, user_project)
    if response["error_msg"]:
        message = f"Failed to fetch project info for '{project_id}': {response['error_msg']}"
        body = response.get("json")
        status = (body.get("error") or {}).get("code") if isinstance(body, dict) else None
        if body is None or status in RETRYABLE_HTTP_STATUS_CODES:
            raise TransientAPIError(message)
        raise DataplexAPIError(message)
    return response.get("json", {})


def _extract_project_number_from_info(project_info: dict) -> str:
    """Extracts the numeric project number from the project info 'name' field."""
    name = project_info.get("name", "")
    match = PROJECT_PATTERN.search(name)
    if match:
        return match.group('project_number')
    raise DataplexAPIError(f"Project number not found in project info: {project_info}")


def get_project_number(project_id: str, user_project: str = "") -> str:
    """Fetches the numeric project number from the project ID (cached)."""
    if not project_id:
        return ""
    if project_id.isdigit():
        return project_id

    def fetch() -> str:
        project_info = _fetch_project_info(project_id, user_project or project_id)
        proj_number = _extract_project_number_from_info(project_info)
        if project_info.get("projectId"):
            _project_number_to_id_cache[proj_number] = project_info["projectId"]
        return proj_number

    return _get_or_fetch(_project_id_to_number_cache, project_id, fetch)


def get_project_id_from_number(project_identifier: str, user_project: str = "") -> str:
    """Resolves a numeric project number to its alphanumeric project ID (cached).

    If the project cannot be read, logs a warning once and keeps using the number.
    """
    if not project_identifier:
        return ""
    if not project_identifier.isdigit():
        return project_identifier

    def fetch() -> str:
        try:
            project_info = _fetch_project_info(project_identifier, user_project or project_identifier)
        except Exception as e:
            logger.warning(f"Could not resolve project ID for number '{project_identifier}': {e}")
            return project_identifier
        proj_id = project_info.get("projectId")
        if not proj_id:
            logger.warning(f"Could not resolve project ID for number '{project_identifier}': no projectId in response")
            return project_identifier
        _project_id_to_number_cache[proj_id] = project_identifier
        return proj_id

    return _get_or_fetch(_project_number_to_id_cache, project_identifier, fetch)


def list_supported_locations(billing_project: str, dataplex_service=None, force_refresh: bool = False) -> List[str]:
    """Lists all supported Dataplex locations for a project with caching."""
    global _locations_cache

    if not force_refresh and billing_project in _locations_cache:
        return _locations_cache[billing_project]

    if dataplex_service is None:
        dataplex_service = authenticate_dataplex()

    try:
        parent_resource = f"projects/{billing_project}"
        logger.debug(f"Request: locations.list(name={parent_resource})")

        locations_request = dataplex_service.projects().locations().list(name=parent_resource)
        response = execute_with_retry(locations_request.execute, f"List locations for {billing_project}")
        locations = [loc.get('locationId') for loc in response.get('locations', []) if loc.get('locationId')]

        _locations_cache[billing_project] = locations
        logger.debug(f"Response: {len(locations)} locations for {billing_project}")
        return locations

    except Exception as locations_error:
        logger.error(f"Error while listing supported locations: {locations_error}")
        raise DataplexAPIError(f"Error while listing supported locations: {locations_error}")


def resolve_regions_to_query(location: str, user_project: str) -> List[str]:
    """Resolve location to regions for entry link queries.

    For 'global' location, returns all supported locations for fanout.
    For specific locations, returns just that location.
    """
    if location.lower() == "global":
        return [location for location in list_supported_locations(user_project) if location not in EXCLUDED_LOCATIONS]
    return [location]


def get_glossary(dataplex_service: build, glossary_name: str) -> Dict:
    """Fetch a glossary resource by name with in-memory caching."""
    def fetch() -> Dict:
        logger.debug(f"Request: glossaries.get(name={glossary_name})")
        try:
            request = dataplex_service.projects().locations().glossaries().get(name=glossary_name)
            return execute_with_retry(request.execute, f"Get glossary {glossary_name}")
        except Exception as e:
            raise DataplexAPIError(f"Error fetching glossary {glossary_name}: {e}")

    return dict(_get_or_fetch(_glossary_cache, glossary_name, fetch))


def get_term(dataplex_service: build, term_name: str) -> Dict:
    """Fetch a term resource by name with in-memory caching."""
    def fetch() -> Dict:
        logger.debug(f"Request: glossaries.terms.get(name={term_name})")
        try:
            request = dataplex_service.projects().locations().glossaries().terms().get(name=term_name)
            return execute_with_retry(request.execute, f"Get term {term_name}")
        except Exception as e:
            raise DataplexAPIError(f"Error fetching term {term_name}: {e}")

    return dict(_get_or_fetch(_term_cache, term_name, fetch))


def cache_glossary_terms(terms: List[Dict], project_id: str, project_number: str = "") -> None:
    """Cache listed terms of one project for get_term, so they are not fetched again.

    Entry links name a term's project by ID or by number, so each term is cached under both.
    """
    for term in terms:
        match = TERM_NAME_PATTERN.match(term.get('name') or '')
        if not match:
            continue
        term_path = (
            f"locations/{match.group('location_id')}/glossaries/{match.group('glossary_id')}"
            f"/terms/{match.group('term_id')}"
        )
        for project in {match.group('project_id'), project_id, project_number} - {''}:
            _term_cache[f"projects/{project}/{term_path}"] = dict(term)


def list_glossaries(dataplex_service: build, parent: str) -> List[Dict]:
    """Lists all glossaries under a project/location with pagination and in-memory caching."""
    def fetch() -> List[Dict]:
        all_glossaries = []
        logger.debug(f"Request: glossaries.list(parent={parent})")
        try:
            request = dataplex_service.projects().locations().glossaries().list(
                parent=parent, pageSize=1000
            )
            while request:
                response = execute_with_retry(request.execute, f"List glossaries for {parent}")
                all_glossaries.extend(response.get('glossaries', []))
                request = dataplex_service.projects().locations().glossaries().list_next(request, response)
        except Exception as e:
            raise DataplexAPIError(f"Error listing glossaries for {parent}: {e}")
        return all_glossaries

    return list(_get_or_fetch(_project_glossaries_cache, parent, fetch))


def resolve_term_entry_to_display_identifier(
    dataplex_service: build, term_entry_name: str, user_project: str = ""
) -> str:
    """Resolves a Dataplex term entry resource name into '<project>.<location>.<glossaryDisplayName>.<termDisplayName>'."""
    term_resource_name = business_glossary_utils.extract_term_resource_from_entry_name(term_entry_name)

    match = TERM_NAME_PATTERN.match(term_resource_name)
    if not match:
        raise InvalidTermNameError(f"Invalid term resource name: {term_resource_name}")

    inner_project = match.group('project_id')
    location_id = match.group('location_id')
    glossary_id = match.group('glossary_id')

    # Resolve to alphanumeric project ID if possible
    outer_project, _, _, _ = parse_entry_name(term_entry_name)
    if outer_project and not outer_project.isdigit():
        display_project = outer_project
    elif inner_project.isdigit():
        display_project = get_project_id_from_number(inner_project, user_project or inner_project)
    else:
        display_project = inner_project

    glossary_resource_name = f"projects/{inner_project}/locations/{location_id}/glossaries/{glossary_id}"
    glossary = get_glossary(dataplex_service, glossary_resource_name)
    term = get_term(dataplex_service, term_resource_name)

    glossary_display_name = glossary.get('displayName') or glossary_id
    term_display_name = term.get('displayName') or match.group('term_id')

    return business_glossary_utils.format_term_display_identifier(
        display_project, location_id, glossary_display_name, term_display_name
    )


def _plain_bigquery_fqn(entry_resource_name: str) -> Optional[str]:
    """The FQN of a BigQuery dataset or table entry if it follows from the entry name, else None."""
    match = ENTRY_NAME_PATTERN.match(entry_resource_name)
    if not match or match.group('entry_group') != BIGQUERY_SYSTEM_ENTRY_GROUP:
        return None
    bigquery_match = PLAIN_BIGQUERY_ENTRY_ID_PATTERN.fullmatch(match.group('entry_id'))
    if not bigquery_match:
        return None
    return "bigquery:" + ".".join(
        part for part in bigquery_match.group('project_id', 'dataset_id', 'table_id') if part
    )


def get_entry_fqn(dataplex_service: build, entry_resource_name: str, user_project: str) -> str:
    """Resolve an entry resource name to its Fully Qualified Name (FQN) with caching.

    The FQNs of BigQuery datasets and tables with plain names are built from the entry name.
    Other entries are looked up under the user project first, then under the entry's own project.
    Returns the entry resource name itself (warning once) if the entry has no FQN or cannot be read.
    """
    plain_fqn = _plain_bigquery_fqn(entry_resource_name)
    if plain_fqn:
        return plain_fqn

    def fetch() -> Optional[str]:
        project_id, location_id, _, _ = parse_entry_name(entry_resource_name)
        entry_dict = None
        for project in dict.fromkeys(p for p in (user_project, project_id) if p):
            entry_dict = lookup_entry(dataplex_service, entry_resource_name, f"projects/{project}/locations/{location_id}")
            if entry_dict:
                break
        fqn = entry_dict.get("fullyQualifiedName") if entry_dict else None
        if not fqn:
            logger.warning(f"Could not retrieve fullyQualifiedName for entry {entry_resource_name}, falling back to entry name.")
        return fqn or None

    return _get_or_fetch(_entry_to_fqn_cache, entry_resource_name, fetch) or entry_resource_name


def _get_glossary_terms(dataplex_service: build, glossary_name: str) -> Dict[str, Dict]:
    """Return {term ID: term} for all terms of a glossary (cached)."""
    def fetch() -> Dict[str, Dict]:
        terms = list_glossary_terms(dataplex_service, glossary_name)
        return {t['name'].split('/')[-1]: t for t in terms if t.get('name')}

    return _get_or_fetch(_glossary_terms_cache, glossary_name, fetch)


def _find_term_by_id(terms: Dict[str, Dict], term_id: str) -> Optional[Dict]:
    """Find a term by ID, preferring an exact match over a case-insensitive one."""
    if term_id in terms:
        return terms[term_id]
    folded = term_id.casefold()
    return next((term for tid, term in terms.items() if tid.casefold() == folded), None)


def _term_display_name(term: Dict) -> str:
    """The term's display name, or its ID when it has none (as written by the export)."""
    return (term.get('displayName') or '').strip() or term['name'].split('/')[-1]


def _matches_term_display_name(term: Dict, term_parts: List[str]) -> bool:
    """Whether any non-empty term part of a sheet name is exactly the term's display name (case-sensitive)."""
    display_name = _term_display_name(term)
    return any(part == display_name for part in term_parts if part)


def _glossary_match_rank(glossary: Dict, part: str) -> Optional[int]:
    """How closely the glossary part of a sheet name names the glossary (lower is closer), or None.

    0: its exact display name; 1: its exact glossary ID; 2: either of them, ignoring case.
    """
    display_name = (glossary.get('displayName') or '').strip()
    glossary_id = glossary['name'].split('/')[-1]
    if part == display_name:
        return 0
    if part == glossary_id:
        return 1
    if part.casefold() in (display_name.casefold(), glossary_id.casefold()):
        return 2
    return None


def _match_glossaries(glossaries: List[Dict], splits: List[tuple]) -> Dict[str, tuple]:
    """Return {glossary name: (rank, glossary, [term parts])} for glossaries named by a (glossary part, term part) split.

    `rank` is the glossary's best _glossary_match_rank over the splits; the term parts are those
    of the splits with that rank.
    """
    matched: Dict[str, tuple] = {}
    for glossary_part, term_part in splits:
        if not glossary_part:
            continue
        for glossary in glossaries:
            if not glossary.get('name'):
                continue
            rank = _glossary_match_rank(glossary, glossary_part)
            if rank is None:
                continue
            best = matched.get(glossary['name'])
            if best is None or rank < best[0]:
                matched[glossary['name']] = (rank, glossary, [term_part])
            elif rank == best[0]:
                best[2].append(term_part)
    return matched


def _resolve_term_by_id(
    dataplex_service: build, identifier: str, term_id: str, candidates: Dict[str, tuple]
) -> tuple:
    """Return (glossary, term) for the term with this ID in the best-matching candidate glossary.

    The term display name in the Name, if given, must be exactly the term's display name.
    """
    resolved = []
    for rank, glossary, term_parts in candidates.values():
        term = _find_term_by_id(_get_glossary_terms(dataplex_service, glossary['name']), term_id)
        if term:
            resolved.append((rank, glossary, term, term_parts))

    if not resolved:
        checked = ", ".join(candidates)
        noun = "glossary" if len(candidates) == 1 else "glossaries"
        raise TermNotFoundError(f"Term ID '{term_id}' not found in {noun} {checked}")
    best_rank = min(rank for rank, _, _, _ in resolved)
    resolved = [(glossary, term, term_parts) for rank, glossary, term, term_parts in resolved if rank == best_rank]
    if len(resolved) > 1:
        by_display_name = [r for r in resolved if _matches_term_display_name(r[1], r[2])]
        if len(by_display_name) != 1:
            matched = ", ".join(glossary['name'] for glossary, _, _ in resolved)
            raise AmbiguousTermError(
                f"'{identifier}' matches more than one glossary containing term ID '{term_id}' ({matched}). "
                f"Use the glossary ID instead of its display name, or put the full "
                f"'projects/.../glossaries/.../terms/{term_id}' resource name in the ID column."
            )
        resolved = by_display_name

    glossary, term, term_parts = resolved[0]
    named = [part for part in term_parts if part]
    if named and not _matches_term_display_name(term, named):
        raise TermNameMismatchError(named[0], term_id, _term_display_name(term))
    return glossary, term


def _resolve_term_by_display_name(
    dataplex_service: build, identifier: str, candidates: Dict[str, tuple]
) -> tuple:
    """Return (glossary, term) for the only term whose display name is exactly the term part of the Name."""
    if not any(part for _, _, term_parts in candidates.values() for part in term_parts):
        raise InvalidTermIdentifierError(
            f"'{identifier}' has no term display name: add it to the Name or put the term ID in the ID column"
        )

    resolved = []
    for rank, glossary, term_parts in candidates.values():
        for term in _get_glossary_terms(dataplex_service, glossary['name']).values():
            if _matches_term_display_name(term, term_parts):
                resolved.append((rank, glossary, term))

    if not resolved:
        checked = ", ".join(candidates)
        noun = "glossary" if len(candidates) == 1 else "glossaries"
        raise TermNotFoundError(
            f"No term with display name matching '{identifier}' in {noun} {checked} "
            f"(display names are case-sensitive)"
        )
    best_rank = min(rank for rank, _, _ in resolved)
    resolved = [(glossary, term) for rank, glossary, term in resolved if rank == best_rank]
    if len(resolved) > 1:
        several_glossaries = len({glossary['name'] for glossary, _ in resolved}) > 1
        term_ids = sorted(
            f"{glossary['name'].split('/')[-1]}/{term['name'].split('/')[-1]}" if several_glossaries
            else term['name'].split('/')[-1]
            for glossary, term in resolved
        )
        raise AmbiguousTermError(
            f"'{identifier}' matches more than one term ({', '.join(term_ids)}). "
            f"Put the term ID in the ID column."
        )
    return resolved[0]


def lookup_term_by_display_identifier(
    dataplex_service: build, identifier: str, user_project: str = "", term_id: str = ""
) -> str:
    """Resolves a term reference from a sheet row to the Dataplex entry name of the term.

    `identifier` (the Name cell, '<project>.<location>.<glossaryDisplayName>.<termDisplayName>')
    selects the glossary. Glossary and term display names may contain dots, so every split point
    after the location is tried. The glossary part may be the glossary's display name or ID; an
    exact display name beats an exact ID, which beats a case-insensitive match.

    If `term_id` (the ID cell) is given, it selects the term; the term display name, if present in
    the Name, must then be exactly the display name of that term. If `term_id` is empty, the term
    is the one whose display name is exactly (case-sensitive) the term part of the Name.

    Raises:
        InvalidTermIdentifierError: If the name is malformed, or has no term display name and no term ID.
        TermNameMismatchError: If the term display name doesn't match the term found by ID.
        GlossaryNotFoundError: If no glossary matches the name.
        TermNotFoundError: If no matching glossary has the term.
        AmbiguousTermError: If the reference matches more than one term.
    """
    cleaned_term_id = (term_id or "").strip()
    parsed = business_glossary_utils.parse_term_display_identifier(identifier, allow_three_part=True)
    rest = identifier.strip().split(".", 2)[2]
    splits = [(rest[:i].strip(), rest[i + 1:].strip()) for i, char in enumerate(rest) if char == "."]
    splits.append((rest.strip(), ""))

    parent = f"projects/{parsed.project_id}/locations/{parsed.location}"
    candidates = _match_glossaries(list_glossaries(dataplex_service, parent), splits)
    if not candidates:
        raise GlossaryNotFoundError(
            f"No glossary in project '{parsed.project_id}' location '{parsed.location}' matches '{identifier}'"
        )

    if cleaned_term_id:
        _, term = _resolve_term_by_id(dataplex_service, identifier, cleaned_term_id, candidates)
    else:
        _, term = _resolve_term_by_display_name(dataplex_service, identifier, candidates)

    try:
        project_number = get_project_number(parsed.project_id, user_project or parsed.project_id)
    except Exception as e:
        if is_transient_error(e):
            raise
        logger.warning(f"Could not resolve numeric project number for '{parsed.project_id}', using project ID: {e}")
        project_number = ""
    entry_name = business_glossary_utils.generate_entry_name_from_term_name(
        term['name'], project_number=project_number
    )
    if project_number:
        _known_entry_names.add(entry_name)
    return entry_name


def normalize_entry_name_project_number(entry_name: str, user_project: str = "") -> str:
    """Normalize the outer project in a Dataplex entry resource name to numeric project number.

    Keeps the name as is if the project number can't be read, unless that failed with a
    network or server error (see is_transient_error), which is raised.
    """
    if not entry_name or not entry_name.startswith("projects/"):
        return entry_name
    parts = entry_name.split("/")
    if len(parts) >= 2:
        proj = parts[1]
        if not proj.isdigit():
            try:
                proj_num = get_project_number(proj, user_project or proj)
                parts[1] = str(proj_num)
                return "/".join(parts)
            except Exception as e:
                if is_transient_error(e):
                    raise
                logger.debug(f"Could not normalize project number for '{proj}': {e}")
    return entry_name


def _search_entry_by_fqn(dataplex_service: build, fqn: str, user_project: str) -> Optional[Dict]:
    """Search Dataplex Catalog for the entry whose fullyQualifiedName is exactly `fqn`.

    Raises:
        DataplexAPIError: If the search request fails.
    """
    sanitized_fqn = fqn.replace('"', '\\"')
    search_params = {
        'name': f"projects/{user_project}/locations/global",
        'query': f'fully_qualified_name="{sanitized_fqn}"',
    }
    bigquery_match = BIGQUERY_FQN_PATTERN.match(fqn)
    if bigquery_match:
        # Search the asset's own project, which may be outside the user project's organization.
        search_params['scope'] = f"projects/{bigquery_match.group('project_id')}"
    logger.debug(f"Request: searchEntries({search_params})")
    try:
        request = dataplex_service.projects().locations().searchEntries(**search_params)
        search_res = execute_with_retry(request.execute, f"Search entry by FQN {fqn}")
    except Exception as e:
        raise DataplexAPIError(f"Search for entry with FQN '{fqn}' failed: {e}")

    matches: Dict[str, Dict] = {}
    for result in (search_res or {}).get('results', []):
        dp_entry = result.get('dataplexEntry') or {}
        if dp_entry.get('fullyQualifiedName') == fqn and dp_entry.get('name'):
            matches.setdefault(dp_entry['name'], dp_entry)
    if len(matches) > 1:
        logger.warning(f"Found {len(matches)} entries with FQN '{fqn}': {sorted(matches)}. Using the first one.")
    return next(iter(matches.values()), None)


def _remember_bigquery_dataset_location(fqn: str, entry_name: str) -> None:
    """Record the location of a BigQuery dataset's entries, given one of its resolved dataset or table entries."""
    match = BIGQUERY_FQN_PATTERN.match(fqn)
    if not match:
        return
    _, location_id, entry_group, _ = parse_entry_name(entry_name)
    if entry_group == BIGQUERY_SYSTEM_ENTRY_GROUP:
        _bigquery_dataset_locations[match.group('project_id', 'dataset_id')] = location_id


def _known_bigquery_table_location(fqn: str) -> Optional[str]:
    """The location of a BigQuery table FQN's dataset, if an entry of the dataset was resolved before."""
    match = BIGQUERY_FQN_PATTERN.match(fqn)
    if not match or not match.group('table_id'):
        return None
    return _bigquery_dataset_locations.get(match.group('project_id', 'dataset_id'))


def _read_bigquery_table_entry(dataplex_service: build, fqn: str, location_id: str) -> Optional[Dict]:
    """Read the entry of a BigQuery table FQN in `location_id`, or return None if it isn't there.

    Raises:
        EntryFQNNotFoundError: If reading the table entry is not permitted.
        DataplexAPIError: If reading the table entry fails for another reason.
    """
    project_id, dataset_id, table_id = BIGQUERY_FQN_PATTERN.match(fqn).group('project_id', 'dataset_id', 'table_id')
    entry_name = (
        f"projects/{project_id}/locations/{location_id}/entryGroups/{BIGQUERY_SYSTEM_ENTRY_GROUP}/entries/"
        f"bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
    )
    logger.debug(f"Request: entries.get(name={entry_name})")
    try:
        request = dataplex_service.projects().locations().entryGroups().entries().get(name=entry_name)
        return execute_with_retry(request.execute, f"Get BigQuery entry {entry_name}")
    except HttpError as e:
        status = getattr(e.resp, 'status', None)
        if status == 404:
            return None
        if status in (401, 403):
            raise EntryFQNNotFoundError(f"Permission denied reading entry '{entry_name}' for FQN '{fqn}': {e}")
        raise DataplexAPIError(f"Error reading entry '{entry_name}' for FQN '{fqn}': {e}")
    except Exception as e:
        raise DataplexAPIError(f"Error reading entry '{entry_name}' for FQN '{fqn}': {e}")


def _get_bigquery_table_entry(dataplex_service: build, fqn: str, user_project: str) -> Optional[Dict]:
    """Read a BigQuery table entry directly, in the location of its dataset's entry.

    Catalog search may not return tables created in the last few minutes; their dataset
    usually is searchable already and tells which location the table entry is in.

    Raises:
        EntryFQNNotFoundError: If reading the table entry is not permitted.
        DataplexAPIError: If reading the table entry fails for another reason.
    """
    match = BIGQUERY_FQN_PATTERN.match(fqn)
    if not match or not match.group('table_id'):
        return None
    project_id, dataset_id = match.group('project_id', 'dataset_id')
    try:
        dataset_entry = lookup_entry_by_fqn(dataplex_service, f"bigquery:{project_id}.{dataset_id}", user_project)
    except EntryFQNNotFoundError:
        return None
    _, location_id, _, _ = parse_entry_name(dataset_entry['name'])
    return _read_bigquery_table_entry(dataplex_service, fqn, location_id)


def lookup_entry_by_fqn(dataplex_service: build, fqn: str, user_project: str) -> Dict:
    """Looks up a Dataplex entry by its Fully Qualified Name (FQN), with positive and negative caching.

    Uses Dataplex Catalog search (exact FQN match only). BigQuery tables that search does not
    return yet are read directly in their dataset's location. Once an entry of a BigQuery dataset
    is found, the dataset's other tables are read directly first, and searched only if that
    finds nothing.

    Raises:
        EntryFQNNotFoundError: If no entry has this FQN, or it cannot be read.
        DataplexAPIError: If a lookup request fails.
    """
    def fetch() -> Optional[Dict]:
        entry, read_error = None, None
        known_location = _known_bigquery_table_location(fqn)
        if known_location:
            try:
                entry = _read_bigquery_table_entry(dataplex_service, fqn, known_location)
            except EntryFQNNotFoundError as e:
                read_error = e
        if not entry:
            # Search also finds BigQuery models, whose FQNs look like table FQNs.
            entry = _search_entry_by_fqn(dataplex_service, fqn, user_project)
        if not entry and not known_location:
            entry = _get_bigquery_table_entry(dataplex_service, fqn, user_project)
        if not entry and read_error:
            raise read_error
        if not entry or not entry.get('name'):
            return None
        _remember_bigquery_dataset_location(fqn, entry['name'])
        entry = dict(entry)
        entry['name'] = normalize_entry_name_project_number(entry['name'], user_project)
        _known_entry_names.add(entry['name'])
        return entry

    entry = _get_or_fetch(_fqn_to_entry_cache, fqn, fetch)
    if entry is None:
        raise EntryFQNNotFoundError(f"Entry with FQN '{fqn}' not found in Dataplex under project '{user_project}'")
    return dict(entry)


def is_known_entry(entry_name: str) -> bool:
    """Whether the entry was already confirmed to exist while resolving sheet rows."""
    return entry_name in _known_entry_names
