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
*   **EntryLinks Import**: The sheet should contain the following header row (the format written by the EntryLinks export):
    `Entry link type, Source Name, Source ID, Column, Target Name, Target ID`
    See [Sheets file schema (EntryLinks)](#sheets-file-schema-entrylinks) for what each column contains.

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

### Required permissions (EntryLinks import)

The account the script runs as (the impersonated service account) needs:

| Used for | Permissions | Predefined role that includes them |
|---|---|---|
| Quota and billing of the API calls, on `--user-project` | `serviceusage.services.use` | Service Usage Consumer (`roles/serviceusage.serviceUsageConsumer`) |
| Finding data assets by FQN (Catalog search and `lookupEntry`), on `--user-project` | `dataplex.projects.search` | Dataplex Catalog Viewer (`roles/dataplex.catalogViewer`) |
| Reading the data assets' entries: `lookupEntry` checks access in the source system | Read access to each asset, e.g. `bigquery.tables.get` for BigQuery tables | BigQuery Metadata Viewer (`roles/bigquery.metadataViewer`) |
| Finding glossaries and terms by name, on the glossaries' projects | `dataplex.glossaries.list`, `dataplex.glossaries.get`, `dataplex.glossaryTerms.list`, `dataplex.glossaryTerms.get` | Dataplex Catalog Viewer (`roles/dataplex.catalogViewer`) |
| Translating project IDs to project numbers and back | `resourcemanager.projects.get` on the projects named in the sheet | Browser (`roles/browser`) |
| Creating the entry links with metadata import jobs | See [Import metadata](https://cloud.google.com/dataplex/docs/import-metadata) | |

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

The first row of the sheet should contain the following headers (the format written by the EntryLinks export):

`Entry link type, Source Name, Source ID, Column, Target Name, Target ID`

Where:

*   `Entry link type` (required): Type of EntryLink: `definition`, `synonym` or `related`.
*   `Source Name`, `Source ID`: The source of the link.
    *   For `definition` links, a data asset: `Source Name` (required) is its Fully Qualified Name (FQN), e.g. `bigquery:my-project.sales.orders`. `Source ID` is for information only and is not used by the import.
    *   For `synonym` and `related` links, a glossary term (see below).
*   `Column` (optional): For `definition` links, the column to link the term to (e.g. `order_id` or `Schema.order_id`). Leave empty to link the whole data asset. Not used for `synonym` and `related` links.
*   `Target Name`, `Target ID`: The target glossary term (see below).

A glossary term is given by its Name and ID cells:

*   Name (required): `<project>.<location>.<glossary>.<termDisplayName>`, where `<project>` is the project ID and `<glossary>` is the display name or ID of the glossary. It identifies the glossary and, when the ID is empty, the term.
*   ID (optional): The term ID (the `id` column of the glossary export). The export always fills it in.

How the term is found:

*   ID given: the term with this ID in the glossary. If the Name also has a term display name, it must exactly match that term's display name, or the row fails (e.g. `Target Name 'Net Revenue' does not match Target ID 'gross-revenue' ('Gross Revenue'). Update or clear Target ID.`). To rename the link target, change both cells or clear the ID.
*   ID empty: the term whose display name exactly matches the term display name in Name (case-sensitive). If several terms in the glossary have that display name, the row fails and lists their IDs; put the right one in the ID cell.
*   Name without a term display name (`<project>.<location>.<glossary>`): the ID is required.

Instead, the Name cell of a term or data asset can hold a full Dataplex resource name (starting with `projects/`), which is used as is: an entry name, or for a term, its resource name `projects/<project>/locations/<location>/glossaries/<glossary>/terms/<term>`. Use this when the project ID contains a dot (domain-scoped projects such as `example.com:my-project`), as the dot-separated Name can't express it. If the ID cell is filled in too, it must name the same term. Sheets written by earlier versions of the export (headers `entry_link_type, source_entry, target_entry, source_path`, with full entry names) can still be imported.

The ID cell of a term can also hold the term's full resource name, for example to choose between two glossaries with the same display name. If the Name is filled in too, it must name that same term (its project, location, glossary display name or ID, and exact term display name), or the row fails. For `definition` links, `Source ID` is ignored even if it holds a full resource name.

Data assets are found with Dataplex Catalog search, which may take a few minutes to include newly created assets (BigQuery tables that search doesn't return yet are read directly). If an asset isn't found, check its FQN or give its full entry name instead.

Before importing, the script lists the rows that can't be imported (with the reason) and the referenced entries that don't exist in Dataplex, and asks whether to continue without them.

Looking up the terms and data assets of the rows is retried for up to 10 minutes when it fails with network or server errors (HTTP 429 or 5xx). If it still fails, the script stops before importing anything, instead of listing every remaining row as one that can't be imported; run it again once the connection or service is back.
