#!/usr/bin/env python3
"""
bulk_create_datacatalog_glossaries.py

Sequentially create glossary entries, each associated with its own Entry Group (Data Catalog v2).
No threads. Uses api_call_utils.fetch_api_response for HTTP calls.

If an entry already exists (ALREADY_EXISTS / 409), the script treats that as
idempotent success and includes the entry in the created list.

This script writes a mapping file at ./datasets/_created_glossaries.txt where each
line contains:
    <entry_group_id>,<glossary_id>
so the importer can use the exact entry group for that glossary.
"""


import sys
import argparse
from pathlib import Path
from typing import Dict, Any, List, Tuple
import requests
import api_call_utils
import logging_utils

DATACATALOG_V2_BASE_URL = "https://datacatalog.googleapis.com/v2"
logger = logging_utils.get_logger()

# ----------------- CONFIG -----------------
USER_PROJECT = "project-with-100-glossaries"
LOCATION = "us"
# NOTE: We no longer use a single ENTRY_GROUP_ID for all glossaries.
# Instead we will create per-glossary entry groups named:
#   dc_glossary__bulk_import_{i}
ENTRY_GROUP_PREFIX = "dc_glossary_lavishg_migration_issue"   # must start with dc_glossary_
GLOSSARY_ID_PREFIX = "lavishg_migration_issue"
BASE_DISPLAY_NAME = "lavishg_migration_issue"  # displayName prefix
COUNT = 250                    # how many glossaries to create
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
OUTPUT_ROOT = Path("./datasets")
# ------------------------------------------

def create_entry_group(user_project: str, location: str, entry_group_id: str) -> bool:
    """
    Attempts to create the specified Entry Group.
    Returns True on success or if it already exists.
    Returns False on any other error.
    """
    create_eg_url = (
        f"{DATACATALOG_V2_BASE_URL}/projects/{user_project}/locations/{location}"
        f"/entryGroups?entry_group_id={entry_group_id}"
    )
    logger.info(f"Checking for or creating entry group: {entry_group_id} in project {user_project}...")
    response = api_call_utils.fetch_api_response(
        requests.post,
        create_eg_url,
        user_project,
        None  # No payload
    )

    if response["error_msg"]:
        error_json = response.get('json', {}).get('error', {}) or {}
        # Datacatalog may indicate ALREADY_EXISTS in different keys; be defensive.
        status_str = error_json.get("status")
        code = error_json.get("code")
        msg = response.get("error_msg", "")
        if status_str == 'ALREADY_EXISTS' or code == 409 or ("already exists" in (msg or "").lower()):
            logger.warning(f"Entry group '{entry_group_id}' already exists. Proceeding.")
            return True
        else:
            logger.error(f"Failed to create entry group '{entry_group_id}': {response['error_msg']} | Full Response: {response.get('json')}")
            return False
    else:
        logger.info(f"Successfully created entry group: {entry_group_id}")
        return True


def _create_single_glossary(
    user_project: str,
    location: str,
    entry_group_id: str,
    entry_id: str,
    display_name: str,
) -> Tuple[str, Dict[str, Any]]:
    """
    Create a single glossary Entry under the provided Entry Group.
    Uses a simple "core_aspects/business_context" payload shape.
    Returns (entry_id, response_dict_from_api_call_utils).
    """
    base_url = (
        f"{DATACATALOG_V2_BASE_URL}/projects/{user_project}/locations/{location}"
        f"/entryGroups/{entry_group_id}/entries"
    )
    full_url = f"{base_url}?entry_id={entry_id}"

    # payload shaped to hold a displayName/contacts/description in an aspect-like structure
    payload = {
        "entry_type": "glossary",
        "display_name": display_name,
        "core_aspects": {
            "business_context": {
                "aspect_type": "business_context",
                "json_content": {
                    "description": f"{display_name}",
                    "contacts": [
                        "mary.sue@company.com",
                        "john.doe@company.com"
                    ]
                }
            }
        }
    }

    response = api_call_utils.fetch_api_response(
        requests.post,
        full_url,
        user_project,
        payload
    )

    logger.info("Create entry response for %s under %s: %s", entry_id, entry_group_id, response.get("json") or response.get("error_msg"))
    return entry_id, response


def create_bulk_glossaries_sequential(
    user_project: str,
    location: str,
    entry_group_prefix: str = ENTRY_GROUP_PREFIX,
    glossary_id_prefix: str = GLOSSARY_ID_PREFIX,
    display_name_prefix: str = BASE_DISPLAY_NAME,
    count: int = COUNT,
) -> List[Tuple[str, str]]:
    """
    Create `count` glossary entries sequentially — each gets its own entry group.
    Returns list of tuples (entry_group_id, glossary_id) that were successfully created.
    Treat ALREADY_EXISTS as success.
    """
    created_entries: List[Tuple[str, str]] = []
    failed_entries: List[Tuple[str, Any]] = []

    logger.info("Starting sequential creation of %d glossaries...", count)

    for i in range(1, count + 1):
        entry_group_id = f"{entry_group_prefix}_{i}"
        glossary_id = f"{glossary_id_prefix}_{i}"
        display_name = f"{display_name_prefix}_{i}"

        # Ensure entry group exists (idempotent)
        eg_ok = create_entry_group(user_project, location, entry_group_id)
        if not eg_ok:
            failed_entries.append((entry_group_id, f"Failed to create entry group {entry_group_id}"))
            logger.error("Skipping glossary %s because entry group %s could not be created.", glossary_id, entry_group_id)
            continue

        # Create glossary entry under entry_group_id
        attempt = 0
        backoff = INITIAL_BACKOFF
        success = False
        last_resp = None

        while attempt <= MAX_RETRIES and not success:
            attempt += 1
            entry_id, resp = _create_single_glossary(user_project, location, entry_group_id, glossary_id, display_name)
            last_resp = resp

            if resp["error_msg"]:
                # inspect server json for retryable errors and ALREADY_EXISTS
                error_json = resp.get("json", {}).get("error", {}) or {}
                status_str = error_json.get("status")
                code = error_json.get("code")
                msg = resp.get("error_msg")

                # Treat ALREADY_EXISTS as idempotent success
                if status_str == "ALREADY_EXISTS" or code == 409 or ("already exists" in (msg or "").lower()):
                    logger.warning("Entry %s already exists under %s. Treating as success (idempotent).", glossary_id, entry_group_id)
                    created_entries.append((entry_group_id, glossary_id))
                    success = True
                    break

                logger.warning("Attempt %d for %s under %s failed: %s", attempt, glossary_id, entry_group_id, msg)
                if code == 429 or (code is not None and code >= 500):
                    if attempt <= MAX_RETRIES:
                        logger.info("Transient error for %s (HTTP %s). Backing off %.1fs then retrying...", glossary_id, code, backoff)
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    else:
                        logger.error("Exceeded retries for %s under %s (transient).", glossary_id, entry_group_id)
                        break
                else:
                    # non-retryable other than ALREADY_EXISTS
                    logger.error("Permanent error creating %s under %s: %s", glossary_id, entry_group_id, msg)
                    break
            else:
                # success
                created_entries.append((entry_group_id, glossary_id))
                success = True

        if not success and last_resp:
            failed_entries.append((glossary_id, last_resp))

    logger.info("Finished. Successfully created %d/%d entries.", len(created_entries), count)

    # persist created mapping file: entry_group_id,glossary_id per line
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    created_file = OUTPUT_ROOT / "_created_glossaries.txt"
    with created_file.open("w", encoding="utf-8") as fh:
        for eg, gid in created_entries:
            fh.write(f"{eg},{gid}\n")
    logger.info("Wrote created glossary mappings to %s", created_file)

    if failed_entries:
        logger.error("Some entries failed to create (see logs). First failures: %s", failed_entries[:5])

    return created_entries


def main(args=None):
    parser = argparse.ArgumentParser(description="Bulk create Data Catalog glossaries.")
    parser.add_argument("--project", required=True, help="GCP project id")
    parser.add_argument("--location", default="us", help="Location (default: us)")
    parser.add_argument("--count", type=int, default=250, help="Number of glossaries to create")
    parser.add_argument("--entry-group-prefix", default=ENTRY_GROUP_PREFIX, help="Entry group prefix")
    parser.add_argument("--glossary-id-prefix", default=GLOSSARY_ID_PREFIX, help="Glossary id prefix")
    parser.add_argument("--display-name-prefix", default=BASE_DISPLAY_NAME, help="Display name prefix")
    parser.add_argument("--output-root", default=str(OUTPUT_ROOT), help="Output root directory")
    parsed_args = parser.parse_args(args)

    if not parsed_args.entry_group_prefix.startswith("dc_glossary_"):
        logger.error("FATAL: ENTRY_GROUP_PREFIX '%s' must start with the prefix 'dc_glossary_'.", parsed_args.entry_group_prefix)
        sys.exit(1)

    try:
        created = create_bulk_glossaries_sequential(
            user_project=parsed_args.project,
            location=parsed_args.location,
            entry_group_prefix=parsed_args.entry_group_prefix,
            glossary_id_prefix=parsed_args.glossary_id_prefix,
            display_name_prefix=parsed_args.display_name_prefix,
            count=parsed_args.count,
        )

        print(f"Created {len(created)} entries (entry_group,glossary). Sample:")
        for eg, g in created[:10]:
            print(" -", eg, ",", g)

    except Exception as e:
        logger.error("An uncaught error occurred: %s", e, exc_info=True)
        if args is None:
            sys.exit(1)
        else:
            raise

if __name__ == "__main__":
    main()
