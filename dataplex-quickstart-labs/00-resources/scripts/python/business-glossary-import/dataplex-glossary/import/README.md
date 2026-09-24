# Import Utilities

This directory contains import utilities for Dataplex business glossary data.

## Prerequisites

Before running the scripts, ensure your local environment and Google Cloud project are correctly configured.

### 1. Install Dependencies

```bash
pip3 install -r requirements.txt
```

### 2. Required APIs

Ensure the following APIs are enabled in the Google Cloud project you will use:

- Dataplex API
- Cloud Resource Manager API
- Cloud Storage API
- Google Sheets API

You can enable them by visiting the APIs & Services dashboard in the Google Cloud Console, or run:

```bash
gcloud services enable dataplex.googleapis.com
gcloud services enable cloudresourcemanager.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable sheets.googleapis.com
```

### 3. GCS Buckets

For the import operation, create one or more empty Google Cloud Storage buckets. The script uses these buckets as a staging area for the import files.

Grant the following roles to the Dataplex service account (`service-PROJECT_NUMBER@gcp-sa-dataplex.iam.gserviceaccount.com`) on each GCS bucket:

| Role | Description |
|---|---|
| Storage Object Creator (`roles/storage.objectCreator`) | Allows creating objects. |
| Storage Object Admin (`roles/storage.objectAdmin`) | Full control over objects. |

### 4. Google Sheets Setup

Prepare a Google Sheet that will be used for the import:

Share the Google Sheet with the service account (`SA_EMAIL`) as a **Viewer** so it has read access.

*   **Glossary Import**: The sheet should contain the following header row:
    `id, parent, display_name, description, overview, type, contact1_email, contact1_name, contact2_email, contact2_name, label1_key, label1_value, label2_key, label2_value`
*   **EntryLinks Import**: The sheet should contain the following 6 columns in the header row:
    `Entry link type, Source Name, Source ID, Column, Target Name, Target ID`
    *   `Entry link type` (or `entry_link_type`) - Type of link: `definition`, `related`, or `synonym`
    *   `Source Name` (or `Source`) - For `definition` links, the data asset FQN (e.g., `bigquery:project.dataset.table`). For `synonym`/`related` links, the 4-part term display identifier `<project>.<location>.<glossaryDisplayName>.<termDisplayName>`. Full Dataplex entry resource names are also accepted for backward compatibility.
    *   `Source ID` (optional) - For `synonym`/`related` links, the unique short `term_id` of the source glossary term (used for exact term lookup when present). For `definition` links, the short ID of the source asset.
    *   `Column` (or `source_path`) - (Optional) Specific column/field name (e.g., `order_id` or `Schema.order_id`). Leave empty for table-level definitions and synonym/related links.
    *   `Target Name` (or `Target`) - The 4-part term display identifier `<project>.<location>.<glossaryDisplayName>.<termDisplayName>`. Full Dataplex entry resource names are also accepted for backward compatibility.
    *   `Target ID` (optional) - The unique short `term_id` of the target glossary term (used for exact term lookup within the glossary identified by `Target Name`).

### Authentication

Use the following commands to authenticate yourself first, then create Application Default Credentials (ADC) by impersonating the service account used by the scripts.

The service account must have the required IAM roles, and your user account must have `roles/iam.serviceAccountTokenCreator` on that service account.

```bash
# Authenticate your user account
gcloud auth login

# Impersonate the service account for ADC with required scopes
SA_EMAIL="<service-account-emailid>"
gcloud auth application-default login \
  --impersonate-service-account="${SA_EMAIL}" \
  --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets"

# (Optional) Export environment variable for additional safety
export GOOGLE_IMPERSONATE_SERVICE_ACCOUNT="${SA_EMAIL}"
```

---

## 1. Glossary Import

Performs bulk import of categories and terms into a Dataplex business glossary from Google Sheets. The sheet is parsed and validated, then converted into an import file compatible with the Dataplex CreateMetadataJob API. The converted file is uploaded to a GCS bucket provided by the user. Once the upload is successful, the script calls CreateMetadataJob to start the import job and prints the result on the terminal.

### Usage

```bash
python3 glossary-import.py
```

### Sheets file schema (Glossary)

The first line of the sheet should contain the following header:

`id, parent, display_name, description, overview, type, contact1_email, contact1_name, contact2_email, contact2_name, label1_key, label1_value, label2_key, label2_value`

Where:

*   `id` (required): Unique id for the term/category in the glossary.
*   `parent` (optional): The id of the parent for this term/category. If not provided, it becomes a direct child of the glossary. The id should be present in this sheet and should be an id of a category.
*   `display_name` (required): The display name of the term/category.
*   `description` (optional): A brief description of the term/category.
*   `overview` (optional): A rich text description of the term/category. It can contain HTML tags.
*   `type` (required): Whether this row represents a TERM or a CATEGORY. Only valid values are TERM or CATEGORY.
*   `contact1_email` (optional): Email id of the data steward for this term/category.
*   `contact1_name` (optional): Name of the data steward for this term/category.
*   `contact2_email` (optional): Email id of another data steward for this term/category.
*   `contact2_name` (optional): Name of another data steward for this term/category.
*   `label1_key` (optional): Label1's key.
*   `label1_value` (optional): Label1's value.
*   `label2_key` (optional): Label2's key.
*   `label2_value` (optional): Label2's value.

---

## 2. EntryLinks Import

Imports EntryLinks into Dataplex from Google Sheets. The script reads entry link definitions from the spreadsheet, validates that referenced entries exist in Dataplex, groups them by entry group and link type, creates import JSON files, uploads them to GCS buckets, and triggers Dataplex CreateMetadataJob import jobs.

### Usage

```bash
python3 entrylinks-import.py \
  --spreadsheet-url <SPREADSHEET_URL> \
  --buckets <BUCKET_LIST> \
  --user-project <PROJECT_ID>
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `--spreadsheet-url` | Yes | Google Sheets URL containing EntryLinks to import |
| `--buckets` | Yes | Comma-separated list of GCS bucket IDs for staging import files (e.g. `bucket-1,bucket-2`) |
| `--user-project` | Yes | Project ID to use for billing and API quota (e.g. `my-project-id`) |

### Example

```bash
python3 entrylinks-import.py \
  --spreadsheet-url "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=0" \
  --buckets "my-staging-bucket-1,my-staging-bucket-2" \
  --user-project "my-billing-project"
```

### Sheets file schema (EntryLinks)

The first row of the sheet should contain the following headers:

`Entry link type, Source Name, Source ID, Column, Target Name, Target ID`

Where:

*   `Entry link type` (required): Type of EntryLink. Valid values: `definition`, `synonym`, `related`.
*   `Source Name`:
    *   For `definition` links: Fully Qualified Name (FQN) of the data asset entry (e.g., `bigquery:project_id.dataset_id.table_name` or `custom:dataset_id.entry_name`). Full Dataplex entry resource names are also accepted.
    *   For `synonym` and `related` links: The 4-part term display identifier in format `<project>.<location>.<glossaryDisplayName>.<termDisplayName>`. Full Dataplex entry resource names are also accepted.
*   `Source ID` (optional): Short ID of the source asset or term.
*   `Column` (optional): Specific column/field name within the data asset (e.g., `order_id` or `user.address.zip`). Leave empty for whole-table/entry definitions and synonym/related links.
*   `Target Name`: The 4-part term display identifier in format `<project>.<location>.<glossaryDisplayName>.<termDisplayName>`. Full Dataplex entry resource names are also accepted.
*   `Target ID` (optional): Short ID of the target term.

*(Note: 4-column sheets `[Entry link type, Source, Column, Target]` and legacy full-entry-name sheets remain fully supported for backward compatibility.)*
