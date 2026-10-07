"""API Layer for Dataplex Glossary Operations."""

import os
import sys
import threading
import time
from typing import Dict, List, Optional

import requests
from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from . import api_call_utils, logging_utils
from .api_call_utils import fetch_api_response
from .constants import (
    CLOUD_RESOURCE_MANAGER_BASE_URL,
    DATAPLEX_BASE_URL,
    ENTRY_NAME_PATTERN,
    EXCLUDED_LOCATIONS,
    PAGE_SIZE,
    PROJECT_PATTERN,
    API_CALL_DELAY_SECONDS,
)
from .error import *
from .retry_utils import execute_with_retry, is_retryable_google_api_error

logger = logging_utils.get_logger()

_locations_cache: Dict[str, List[str]] = {}
_aspect_type_cache: Dict[str, Optional[Dict]] = {}


def clear_caches() -> None:
    """Clear all in-memory caches (useful between batch runs and in unit tests)."""
    global _locations_cache, _aspect_type_cache
    _locations_cache.clear()
    _aspect_type_cache.clear()


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
        loc = target_location if 'target_location' in dir() else location or 'unknown'
        logger.error(f"Error while looking up entry links for {term_entry_name} in {loc}: {lookup_error}")
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
    """Calls the Cloud Resource Manager API and returns the project JSON payload."""
    url = _get_project_url(project_id)
    response = api_call_utils.fetch_api_response(requests.get, url, user_project)
    if response["error_msg"]:
        raise DataplexAPIError(f"Failed to fetch project info for '{project_id}': {response['error_msg']}")
    return response.get("json", {})


def _extract_project_number_from_info(project_info: dict) -> str:
    """Extracts the numeric project number from the project info 'name' field."""
    name = project_info.get("name", "")
    match = PROJECT_PATTERN.search(name)
    if match:
        return match.group('project_number')
    raise DataplexAPIError(f"Project number not found in project info: {project_info}")


def get_project_number(project_id: str, user_project: str) -> str:
    """Fetches the project number from the project ID (composed from smaller helpers)."""
    project_info = _fetch_project_info(project_id, user_project)
    return _extract_project_number_from_info(project_info)


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


def get_aspect_type(
    dataplex_service: build, aspect_type_resource: str, user_project: str = ""
) -> Optional[Dict]:
    """Fetch an AspectType resource by name, with in-memory caching.

    Permission and not-found failures are treated as "schema unavailable"
    rather than fatal: the caller then falls back to heuristic value coercion.
    Negative results are cached too, so a missing AspectType is only looked up
    once per run.

    Args:
        dataplex_service: The Dataplex API service object.
        aspect_type_resource: Full AspectType resource name, i.e.
            'projects/{project}/locations/{location}/aspectTypes/{aspect_type}'.
        user_project: Optional project used for billing/quota attribution.

    Returns:
        The AspectType resource dict, or None when it cannot be read.
    """
    if not aspect_type_resource:
        return None

    if aspect_type_resource in _aspect_type_cache:
        return _aspect_type_cache[aspect_type_resource]

    logger.debug(f"Request: aspectTypes.get(name={aspect_type_resource})")
    try:
        request = dataplex_service.projects().locations().aspectTypes().get(
            name=aspect_type_resource
        )
        response = execute_with_retry(
            request.execute, f"Get aspect type {aspect_type_resource}"
        )
        _aspect_type_cache[aspect_type_resource] = response
        return response
    except HttpError as http_error:
        status_code = http_error.resp.status if hasattr(http_error, 'resp') else None
        if status_code in (401, 403, 404):
            logger.warning(
                f"Could not read AspectType {aspect_type_resource} (HTTP {status_code}). "
                f"Falling back to heuristic value coercion."
            )
            _aspect_type_cache[aspect_type_resource] = None
            return None
        logger.warning(
            f"Error fetching AspectType {aspect_type_resource}: {http_error}. "
            f"Falling back to heuristic value coercion."
        )
        _aspect_type_cache[aspect_type_resource] = None
        return None
    except Exception as aspect_type_error:
        logger.warning(
            f"Error fetching AspectType {aspect_type_resource}: {aspect_type_error}. "
            f"Falling back to heuristic value coercion."
        )
        _aspect_type_cache[aspect_type_resource] = None
        return None


def _flatten_metadata_template(
    template: Dict, prefix: str = "", field_types: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """Flatten an AspectType MetadataTemplate into a {field_path: type} map.

    Record sub-fields are recorded under their dotted path ('owner.email') so
    they line up with the dotted 'Aspect name' values used in Sheet 2.

    Args:
        template: A MetadataTemplate node.
        prefix: Dotted path accumulated so far.
        field_types: Accumulator dict (created on first call).

    Returns:
        Mapping of dotted field path to declared MetadataTemplate type.
    """
    if field_types is None:
        field_types = {}
    if not isinstance(template, dict):
        return field_types

    for record_field in template.get('recordFields', []) or []:
        field_name = record_field.get('name')
        if not field_name:
            continue
        field_path = f"{prefix}.{field_name}" if prefix else field_name
        field_type = (record_field.get('type') or '').lower()
        if field_type:
            field_types[field_path] = field_type
        if record_field.get('recordFields'):
            _flatten_metadata_template(record_field, field_path, field_types)

    return field_types


def get_aspect_field_types(
    dataplex_service: build, aspect_type_resource: str, user_project: str = ""
) -> Dict[str, str]:
    """Return the declared field types for an AspectType.

    Args:
        dataplex_service: The Dataplex API service object.
        aspect_type_resource: Full AspectType resource name.
        user_project: Optional project used for billing/quota attribution.

    Returns:
        Mapping of dotted field path to MetadataTemplate type (e.g.
        {'tier': 'string', 'is_pii': 'bool', 'owner.email': 'string'}). Empty
        when the AspectType cannot be read, which signals heuristic coercion.
    """
    aspect_type = get_aspect_type(dataplex_service, aspect_type_resource, user_project)
    if not aspect_type:
        return {}
    return _flatten_metadata_template(aspect_type.get('metadataTemplate', {}) or {})