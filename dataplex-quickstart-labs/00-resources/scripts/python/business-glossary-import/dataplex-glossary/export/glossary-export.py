from google.auth import default
from googleapiclient.discovery import build
import os
import re
import sys
import logging
from typing import List, Dict, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils import business_glossary_utils, sheet_utils
from utils.constants import ASPECT_SHEET_HEADERS

# --- Configure logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Custom Exception Classes ---
class InvalidSpreadsheetURLError(Exception):
    """Raised when the provided spreadsheet URL is invalid."""
    pass

class InvalidGlossaryNameError(Exception):
    """Raised when the provided glossary name is invalid."""
    pass

class DataplexAPIError(Exception):
    """Raised when there is an error interacting with the Dataplex API."""
    pass

class SheetsAPIError(Exception):
    """Raised when there is an error interacting with the Google Sheets API."""
    pass

class NoCategoriesFoundError(Exception):
    """Raised when no categories are found for the given glossary."""
    pass

class NoTermsFoundError(Exception):
    """Raised when no terms are found for the given glossary."""
    pass

class InvalidTermNameError(Exception):
    """Raised when term name is invalid."""
    pass

class InvalidCategoryNameError(Exception):
    """Raised when Category name is invalid."""
    pass

class InvalidEntryIdFormatError(Exception):
    """Raised when the entry ID format is invalid."""
    pass

GLOSSARY_URL_PATTERN = re.compile(r".*dp-glossaries/projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/glossaries/(?P<glossary_id>[^/?#]+).*")
TERM_NAME_REGEX_PATTERN = re.compile(r"projects/([^/]+)/locations/([^/]+)/glossaries/([^/]+)/terms/([^/]+)")
CATERGORY_NAME_REGEX_PATTERN = re.compile(r"projects/(?P<project_id>[^/]+)/locations/(?P<location_id>[^/]+)/glossaries/(?P<glossary_id>[^/?#]+)/categories/(?P<category_id>[^/]+)")
GLOSSARY_NAME_REGEX_PATTERN = re.compile(r"projects/([^/]+)/locations/([^/]+)/glossaries/([^/]+)")
ENTRY_NAME_REGEX_PATTERN = re.compile(r"projects/([^/]+)/locations/([^/]+)/entryGroups/@dataplex/entries/.*")
SPREAD_SHEET_REGEX_PATTERN = re.compile(r"https://docs\.google\.com/spreadsheets/d/([^/]+)")

# Expected format for the glossary URL (for error messages)
EXPECTED_GLOSSARY_URL_FORMAT = "any_url_containing/dp-glossaries/projects/<project_id>/locations/<location_id>/glossaries/<glossary_id>"

ID_COLUMN = "id"
PARENT_COLUMN = "parent"
DISPLAY_NAME_COLUMN = "display_name"
DESCRIPTION_COLUMN = "description"
OVERVIEW_COLUMN = "overview"
TYPE_COLUMN = "type"
CONTACT1_EMAIL_COLUMN = "contact1_email"
CONTACT1_NAME_COLUMN = "contact1_name"
CONTACT2_EMAIL_COLUMN = "contact2_email"
CONTACT2_NAME_COLUMN = "contact2_name"
LABEL1_KEY_COLUMN = "label1_key"
LABEL1_VALUE_COLUMN = "label1_value"
LABEL2_KEY_COLUMN = "label2_key"
LABEL2_VALUE_COLUMN = "label2_value"
OVERVIEW_ASPECT_ID = "655216118709.global.overview"
CONTACTS_ASPECT_ID = "655216118709.global.contacts"

SHEET_HEADERS = [ID_COLUMN, PARENT_COLUMN, DISPLAY_NAME_COLUMN, DESCRIPTION_COLUMN, OVERVIEW_COLUMN, TYPE_COLUMN, CONTACT1_EMAIL_COLUMN, CONTACT1_NAME_COLUMN, CONTACT2_EMAIL_COLUMN, CONTACT2_NAME_COLUMN, LABEL1_KEY_COLUMN, LABEL1_VALUE_COLUMN, LABEL2_KEY_COLUMN, LABEL2_VALUE_COLUMN]

# --- Helper Functions ---
def _get_spreadsheet_id(spreadsheet_url: str) -> str:
    """Extracts the spreadsheet ID from the URL.
    
    Args:
        spreadsheet_url: The URL of the Google Sheet.
    
    Returns:
        The spreadsheet ID.
    
    Raises:
        InvalidSpreadsheetURLError: If the spreadsheet URL is invalid.
    """
    match = SPREAD_SHEET_REGEX_PATTERN.match(spreadsheet_url)
    if not match:
        logging.error(f"Invalid spreadsheet URL: {spreadsheet_url}")
        raise InvalidSpreadsheetURLError(f"Invalid spreadsheet URL: {spreadsheet_url}")
    return match.group(1)

def _authenticate_dataplex() -> build:
    """Authenticates with the Dataplex API using Application Default Credentials.
    
    Returns:
        A Dataplex API service object.
    
    Raises:
        google.auth.exceptions.DefaultCredentialsError: If credentials cannot be found.
        DataplexAPIError: If there is an error during authentication.
    """
    try:
        dataplex_scopes = ['https://www.googleapis.com/auth/cloud-platform']
        dataplex_creds, _ = default(scopes=dataplex_scopes)
        return build('dataplex', 'v1', credentials=dataplex_creds)
    except Exception as e:
        logging.error(f"Error during Dataplex authentication: {e}")
        raise DataplexAPIError(f"Error during Dataplex authentication: {e}")

def _authenticate_sheets() -> build:
    """Authenticates with the Google Sheets API using Application Default Credentials.
    
    Returns:
        A Google Sheets API service object.
    
    Raises:
        google.auth.exceptions.DefaultCredentialsError: If credentials cannot be found.
        SheetsAPIError: If there is an error during authentication.
    """
    try:
        sheets_scopes = ['https://www.googleapis.com/auth/spreadsheets']
        sheets_creds, _ = default(scopes=sheets_scopes)
        return build('sheets', 'v4', credentials=sheets_creds)
    except Exception as e:
        logging.error(f"Error during Sheets authentication: {e}")
        raise SheetsAPIError(f"Error during Sheets authentication: {e}")

def _list_glossary_categories(dataplex_service: build, glossary_name: str) -> List[Dict]:
    """Lists categories from a Dataplex glossary.
    
    Args:
        dataplex_service: The Dataplex API service object.
        glossary_name: The full glossary name.
    
    Returns:
        A list of glossary categories.
    
    Raises:
      DataplexAPIError: If there is an error during the API call.
      NoCategoriesFoundError: If no categories are found.
    """
    try:
        request = dataplex_service.projects().locations().glossaries().categories().list(
            parent=glossary_name, pageSize=1000)

        categories = []
        while request:
            response = request.execute()
            if 'categories' in response:
                categories.extend(response['categories'])
            request = dataplex_service.projects().locations().glossaries().categories().list_next(request, response)
        if not categories:
            logging.warning(f"No categories found for {glossary_name}")
            raise NoCategoriesFoundError(f"No categories found for {glossary_name}")
        return categories
    except Exception as e:
        logging.error(f"Error while listing glossary categories: {e}")
        raise DataplexAPIError(f"Error while listing glossary categories: {e}")


def _list_glossary_terms(dataplex_service: build, glossary_name: str) -> List[Dict]:
    """Lists terms from a Dataplex glossary, handling pagination.

    Args:
        dataplex_service: The Dataplex API service object.
        glossary_name: The full glossary name.

    Returns:
        A list of glossary terms.

    Raises:
      DataplexAPIError: If there is an error during the API call.
      NoTermsFoundError: If no terms are found.
    """
    try:
        terms = []
        page_token = None
        while True:
            request = dataplex_service.projects().locations().glossaries().terms().list(
                parent=glossary_name, pageSize=1000, pageToken=page_token
            )
            response = request.execute()

            if 'terms' in response:
                terms.extend(response['terms'])
            
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        
        if not terms:
            logging.warning(f"No terms found for {glossary_name}")
            raise NoTermsFoundError(f"No terms found for {glossary_name}")
        return terms
    except Exception as e:
        logging.error(f"Error while listing glossary terms for {glossary_name}: {e}", exc_info=True)
        raise DataplexAPIError(f"Error while listing glossary terms for {glossary_name}: {e}")

def _extract_project_and_location_from_entry_id(entry_id: str) -> tuple:
    """Extracts project and location from an entry id.
    
    Args:
        entry_id: The entry id.
    
    Returns:
        A tuple with project_id and location_id
    
    Raises:
        InvalidEntryIdFormatError: If the entry id format is invalid
    """
    match = ENTRY_NAME_REGEX_PATTERN.match(entry_id)
    if not match:
        logging.error(f"Invalid entry id format: {entry_id}")
        raise InvalidEntryIdFormatError(f"Invalid entry id format: {entry_id}")
    project_id, location_id = match.groups()
    return project_id, location_id

def _lookup_entry(dataplex_service: build, entry_id: str) -> Optional[Dict]:
    """Looks up an entry using the Dataplex API.

    Args:
        dataplex_service: The Dataplex API service object.
        entry_id: The entry ID to look up.

    Returns:
        The entry response as a dictionary, or None if an error occurs.

    Raises:
        DataplexAPIError: If there is an error during the API call.
    """
    try:
        project_id, location_id = _extract_project_and_location_from_entry_id(entry_id)
        name = f"projects/{project_id}/locations/{location_id}"
        request = dataplex_service.projects().locations().lookupEntry(name=name, entry=entry_id, view="ALL")
        response = request.execute()
        return response
    except Exception as e:
        logging.error(f"Error while looking up entry {entry_id}: {e}")
        raise DataplexAPIError(f"Error while looking up entry {entry_id}: {e}")

def _generate_entry_name_from_category_name(category_name: str) -> str:
    """Generates an entryId from a category name.
    
    Args:
        category_name: The full category name.
        
    Returns:
        The generated entryId.
        
    Raises:
        InvalidCategoryNameError: If the category name format is invalid.
    """
    match = CATERGORY_NAME_REGEX_PATTERN.match(category_name)
    if not match:
        logging.error(f"Invalid category name format: {category_name}")
        raise InvalidCategoryNameError(f"Invalid category name format: {category_name}")
    project_id, location_id, glossary_id, category_id = match.groups()
    return f"projects/{project_id}/locations/{location_id}/entryGroups/@dataplex/entries/projects/{project_id}/locations/{location_id}/glossaries/{glossary_id}/categories/{category_id}"

def _generate_entry_name_from_term_name(term_name: str) -> str:
    """Generates an entryId from a term name.
    
    Args:
        term_name: The full term name.
        
    Returns:
        The generated entryId.
        
    Raises:
        InvalidTermNameError: If the term name format is invalid.
    """
    match = TERM_NAME_REGEX_PATTERN.match(term_name)
    if not match:
        logging.error(f"Invalid term name format: {term_name}")
        raise InvalidTermNameError(f"Invalid term name format: {term_name}")
    project_id, location_id, glossary_id, term_id = match.groups()
    return f"projects/{project_id}/locations/{location_id}/entryGroups/@dataplex/entries/projects/{project_id}/locations/{location_id}/glossaries/{glossary_id}/terms/{term_id}"

def _get_sheet_row_for_category(category, entry):
    """Generates a sheet row from category and entry.
    
    Args:
        category: A Category.
        entry: An Entry representing a Category.
      
    Returns:
          an object containing values for the following keys
        ["id", "parent", "display_name", "description", "overview", "type", "contact1_email", "contact1_name", "contact2_email", "contact2_name", "label1_key", "label1_value", "label2_key", "label2_value"]
    """
    match = CATERGORY_NAME_REGEX_PATTERN.match(category.get("name"))
    if not match:
        logging.error(f"Invalid category name format: {category.get('name')}")
        raise InvalidCategoryNameError(f"Invalid category name format: {category.get('name')}")
  
    project_id, location_id, glossary_id, category_id = match.groups()
    sheet_data = {}
  
    # append id
    sheet_data["id"] = category_id

    # append parent id. if the parent is glossary then keep the parent empty
    if category.get("parent") == f"projects/{project_id}/locations/{location_id}/glossaries/{glossary_id}":
        sheet_data["parent"] = ""
    else:
        parent_match = CATERGORY_NAME_REGEX_PATTERN.match(category.get("parent"))
        if not parent_match:
            logging.error(f"Invalid parent: {category.get("parent")}")
        else:
            parent_category_id = parent_match.group("category_id")
            sheet_data["parent"] = parent_category_id

    # append display name
    sheet_data["display_name"] = category.get("displayName", "")

    # append description
    sheet_data["description"] = category.get("description", "")

    simplified_aspects = _get_aspects_from_entry(entry)

    # append overview
    if simplified_aspects["overview"]:
        sheet_data["overview"] = simplified_aspects["overview"]
  
    # append type
    sheet_data["type"] = "CATEGORY"

    # append contacts
    if simplified_aspects["identities"]:
        index = 1
        identities = simplified_aspects["identities"]
        for identity in identities:
            if (identity["name"] or identity["id"]):
                sheet_data[f"contact{index}_email"] = identity["id"]
                sheet_data[f"contact{index}_name"] = identity["name"]
                index += 1

    # append labels
    index=1
    for key, value in category.get("labels", {}).items():
        sheet_data[f"label{index}_key"] = key
        sheet_data[f"label{index}_value"] = value
        index += 1

    return sheet_data

def _get_sheet_row_for_term(term, entry):
    """Generates a sheet row from term and entry.

    Args:
        term: A Term.
        entry: An Entry representing a Term.      
    Returns:
        an object containing values for the following keys
        ["id", "parent", "display_name", "description", "overview", "type", "contact1_email", "contact1_name", "contact2_email", "contact2_name", "label1_key", "label1_value", "label2_key", "label2_value"]
    """
    sheet_data = {}
    match = TERM_NAME_REGEX_PATTERN.match(term.get("name"))
    if not match:
        logging.error(f"Invalid term name format: {term.get('name')}")
        raise InvalidTermNameError(f"Invalid term name format: {term.get('name')}")
    project_id, location_id, glossary_id, term_id = match.groups()
  
    # append id
    sheet_data["id"] = term_id

    # append parent id. if the parent is glossary then keep the parent empty
    if term.get("parent") == f"projects/{project_id}/locations/{location_id}/glossaries/{glossary_id}":
        sheet_data["parent"] = ""
    else:
        parent_match = CATERGORY_NAME_REGEX_PATTERN.match(term.get("parent"))
        if not parent_match:
            logging.error(f"Invalid parent: {term.get("parent")}")
        else:
            project_id, location_id, glossary_id, category_id = parent_match.groups()
            sheet_data["parent"] = category_id

    # append display name
    sheet_data["display_name"] = term.get("displayName", "")

    # append description
    sheet_data["description"] = term.get("description", "")

    simplified_aspects = _get_aspects_from_entry(entry)

    # append overview
    if simplified_aspects["overview"]:
        sheet_data["overview"] = simplified_aspects["overview"]
  
    # append type
    sheet_data["type"] = "TERM"

    # append contacts
    if simplified_aspects["identities"]:
        index = 1
        identities = simplified_aspects["identities"]
        for identity in identities:
            if (identity["name"] or identity["id"]):
                sheet_data[f"contact{index}_email"] = identity["id"]
                sheet_data[f"contact{index}_name"] = identity["name"]
                index += 1

    # append labels
    index=1
    for key, value in term.get("labels", {}).items():
        sheet_data[f"label{index}_key"] = key
        sheet_data[f"label{index}_value"] = value
        index += 1

    return sheet_data


def _get_aspects_from_entry(entry):
    """
        Extracts simplified aspects from an entry.
        This function simplifies the aspects of an entry to include only the overview and identities.

        The "overview" and "identities" keys are the long-standing contract used
        to populate the Sheet 1 columns. The additional "custom" key carries
        every NON-system aspect, keyed by its aspect key, for Sheet 2.

        System aspects (overview, contacts, glossary-term-aspect,
        glossary-category-aspect) are matched on their
        '<location>.<aspect_type_id>' suffix, because export observes the
        numeric project number ('655216118709.global.overview') while import
        writes the friendly project id ('dataplex-types.global.overview').

        Args:
            entry: An Entry object.
        Returns:
            A dictionary with simplified aspects containing "overview", "identities" and "custom".
    """
    simplified_aspects = {"overview": "", "identities": [], "custom": {}}
    aspects_map = entry["aspects"]
    # append overview
    if aspects_map and aspects_map.get(OVERVIEW_ASPECT_ID):
        overview = aspects_map[OVERVIEW_ASPECT_ID]
        if overview.get("data") and overview.get("data").get("content"):
            content = overview.get("data").get("content")
            simplified_aspects["overview"] = content
    # append contacts
    if aspects_map and aspects_map.get(CONTACTS_ASPECT_ID):
        contacts = aspects_map[CONTACTS_ASPECT_ID]
        if contacts.get("data") and contacts.get("data").get("identities"):
            simplified_aspects["identities"] = contacts.get("data").get("identities")
    # append custom (non-system) aspects
    for aspect_key, aspect in (aspects_map or {}).items():
        if business_glossary_utils.is_system_aspect(aspect_key):
            continue
        aspect_data = aspect.get("data") if isinstance(aspect, dict) else None
        if not aspect_data:
            continue
        simplified_aspects["custom"][aspect_key] = aspect_data
    return simplified_aspects


def _get_aspect_rows_for_entry(term_or_category, entry) -> List[List[str]]:
    """Builds the Sheet 2 rows for a single term or category.

    Each custom aspect's ``data`` dict is flattened into one row per field,
    using the '<aspect_type_id>.<field_name>' short identifier. Record and map
    sub-fields are flattened one level into dotted names ('custom-gov.owner.email');
    anything deeper is serialized as a JSON object string.

    Args:
        term_or_category: A Term or Category resource dict.
        entry: The Entry representing that term or category.

    Returns:
        A list of [id, Aspect name, Aspect value] rows, empty when the entry
        carries no custom aspects.
    """
    resource_name = term_or_category.get("name", "")
    match = TERM_NAME_REGEX_PATTERN.match(resource_name) or CATERGORY_NAME_REGEX_PATTERN.match(resource_name)
    if not match:
        logging.warning(f"Could not determine id for aspect rows from: {resource_name}")
        return []
    resource_id = match.groups()[-1]

    simplified_aspects = _get_aspects_from_entry(entry)
    aspect_rows = []

    for aspect_key, aspect_data in simplified_aspects["custom"].items():
        aspect_type_id = business_glossary_utils.extract_aspect_type_id(aspect_key)
        for field_name, cell_value in business_glossary_utils.flatten_aspect_data(aspect_data):
            aspect_rows.append([
                resource_id,
                business_glossary_utils.format_aspect_identifier(aspect_type_id, field_name),
                cell_value,
            ])

    return aspect_rows


def _write_to_sheet(
    sheets_service: build,
    spreadsheet_id: str,
    data: List[List[str]],
    sheet_name: Optional[str] = None,
) -> None:
    """Writes data to a Google Sheet.
    
    Args:
        sheets_service: The Google Sheets API service object.
        spreadsheet_id: The ID of the spreadsheet.
        data: The data to write to the sheet.
        sheet_name: Optional worksheet name to target. When omitted the write
            is untargeted (range 'A1'), exactly as before, which keeps the
            Sheet 1 write path behaviorally unchanged.
    
    Raises:
        SheetsAPIError: If there is an error during the write operation.
    """
    target_range = f"'{sheet_name}'!A1" if sheet_name else 'A1'
    try:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=target_range,
            valueInputOption='USER_ENTERED',
            body={'values': data}
        ).execute()
        logging.info(f"Data written to spreadsheet: {spreadsheet_id}")
    except Exception as e:
        logging.error(f"Error while writing to spreadsheet: {e}")
        raise SheetsAPIError(f"Error while writing to spreadsheet: {e}")


def _write_aspects_to_sheet(
    sheets_service: build, spreadsheet_id: str, aspect_rows: List[List[str]]
) -> None:
    """Writes the collected custom aspect rows to the aspects sheet (Sheet 2).

    When there are no custom aspects, nothing is written and no tab is created:
    a single informational line is logged, so glossaries without custom aspects
    export exactly as they did before.

    The write clears the target tab first, so aspect rows removed since the
    previous export do not linger as orphans (which would later fail import
    validation with an unknown id).

    Args:
        sheets_service: The Google Sheets API service object.
        spreadsheet_id: The ID of the spreadsheet.
        aspect_rows: Rows of [id, Aspect name, Aspect value], header excluded.

    Raises:
        SheetsAPIError: If there is an error during the write operation.
    """
    if not aspect_rows:
        logging.info("No custom aspects found; skipping the aspects sheet.")
        return

    try:
        target_sheet_name = sheet_utils.write_aspect_rows(
            sheets_service, spreadsheet_id, [ASPECT_SHEET_HEADERS] + aspect_rows
        )
        logging.info(
            f"Wrote {len(aspect_rows)} custom aspect row(s) to '{target_sheet_name}'."
        )
    except Exception as e:
        logging.error(f"Error while writing aspects to spreadsheet: {e}")
        raise SheetsAPIError(f"Error while writing aspects to spreadsheet: {e}")

# --- Main Function ---
def list_and_write_glossary_taxonomy(spreadsheet_url: str, glossary_name: str) -> None:
    """
    Orchestrates the process of listing Dataplex glossary categories and writing them to a Google Sheet.
    
    Args:
        spreadsheet_url: The URL of the Google Sheet.
        glossary_name: The full glossary name.
    
    Raises:
        Any of the custom exceptions or exceptions from the google libraries.
    """
    try:
        logging.info(f"Starting process for glossary: {glossary_name}, sheet: {spreadsheet_url}")
        # 1. Get Spreadsheet ID
        spreadsheet_id = _get_spreadsheet_id(spreadsheet_url)
        
        # 2. Authenticate to APIs
        dataplex_service = _authenticate_dataplex()
        sheets_service = _authenticate_sheets()

        # 3. List glossary categories
        categories = _list_glossary_categories(dataplex_service, glossary_name)

        # 4. List glossary terms
        terms = _list_glossary_terms(dataplex_service, glossary_name)

        entry_names_map = {}
        # 5. Get Category and Terms.
        for category in categories:
            category_name = category.get("name")
            if not category_name:
                logging.error("Category name is missing. Skipping this category.")
                continue
            entry_names_map[category_name] = _generate_entry_name_from_category_name(category_name)
        for term in terms:
            term_name = term.get("name")
            if not term_name:
                logging.error("Term name is missing. Skipping this term.")
                continue
            entry_names_map[term_name] = _generate_entry_name_from_term_name(term_name)

        # 6. Lookup entries and store results
        entry_id_to_entry_map = {}
        for entry_id, entry_name in entry_names_map.items():
            try:
                entry_response = _lookup_entry(dataplex_service, entry_name)
                if entry_response:
                    entry_id_to_entry_map[entry_id] = entry_response
            except DataplexAPIError as e:
                logging.warning(f"Skipping entry {entry_name} due to lookup error: {e}")
                continue

        sheet_data = [SHEET_HEADERS]
        aspect_rows = []
        # 7. Add Categories data in the sheet
        for category in categories:
            category_entry = entry_id_to_entry_map.get(category.get("name"))
            if not category_entry:
                logging.warning(f"No entry found for category: {category.get('name', '')}. Skipping this category.")
                continue
            category_row_data = _get_sheet_row_for_category(category, category_entry)
            category_row = []
            for header in SHEET_HEADERS:
                if category_row_data.get(header):
                    category_row.append(category_row_data[header])
                else:
                    category_row.append("")
            sheet_data.append(category_row)
            aspect_rows.extend(_get_aspect_rows_for_entry(category, category_entry))

        # 8. Add Terms data in the sheet
        for term in terms:
          term_entry = entry_id_to_entry_map.get(term.get("name"))
          if not term_entry:
            logging.warning(f"No entry found for term: {term.get('name', '')}. Skipping this term.")
            continue
          term_row_data = _get_sheet_row_for_term(term, term_entry)
          term_row = []
          for header in SHEET_HEADERS:
            if term_row_data.get(header):
              term_row.append(term_row_data[header])
            else:
              term_row.append("")
          sheet_data.append(term_row)
          aspect_rows.extend(_get_aspect_rows_for_entry(term, term_entry))
        
        # 9. Write data to the sheet
        _write_to_sheet(sheets_service, spreadsheet_id, sheet_data)

        # 10. Write custom aspects to the aspects sheet (no-op when there are none)
        _write_aspects_to_sheet(sheets_service, spreadsheet_id, aspect_rows)

        logging.info("Process completed successfully.")

    except (InvalidSpreadsheetURLError, InvalidGlossaryNameError, DataplexAPIError, SheetsAPIError, NoCategoriesFoundError) as e:
        logging.error(f"Operation failed: {e}")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        raise

if __name__ == "__main__":
    sheet_url = input("Enter the Google Sheet URL: ")
    glossary_url = input("Enter the glossary URL: ")
    sheet_url = sheet_url.strip()
    glossary_url = glossary_url.strip()
    
    if not sheet_url or not glossary_url:
        print("Both Google Sheet URL and glossary URL are required.")
        exit(1)
    
    # Validate inputs
    if not sheet_url.startswith("https://docs.google.com/spreadsheets/d/"):
        print("Invalid Google Sheet URL. Please provide a valid URL. It should start with 'https://docs.google.com/spreadsheets/d/'.")
        exit(1)
    
    match = GLOSSARY_URL_PATTERN.match(glossary_url)
    if not match:
        print(f"Invalid glossary URL format. Expected url format: {EXPECTED_GLOSSARY_URL_FORMAT}, but got: {glossary_url}")
        exit(1)
    project_id = match.group("project_id")
    location_id = match.group("location_id")
    glossary_id = match.group("glossary_id")
    glossary_name = f"projects/{project_id}/locations/{location_id}/glossaries/{glossary_id}"
    
    list_and_write_glossary_taxonomy(sheet_url, glossary_name)