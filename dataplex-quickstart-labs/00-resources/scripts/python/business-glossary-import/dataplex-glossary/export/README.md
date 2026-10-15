# Export Utilities

This directory contains export utilities for Dataplex business glossary data.

## Prerequisites

Before running the scripts, ensure your local environment and Google Cloud project are correctly configured.

### 1. Install Dependencies

```bash
pip3 install -r requirements.txt
```

### 2. Service Account Setup

To access Google Sheets we would need to run the script using a Service Account.

**Identify/Create the Service Account:**

1. Go to the Google Cloud Console > IAM & Admin > Service Accounts.
2. You can use an existing service account or create a new one (e.g., `my-script-runner@YOUR_PROJECT_ID.iam.gserviceaccount.com`). Let's call this `SA_EMAIL`.

**Grant Your User the "Service Account Token Creator" Role:**

This is the crucial step that allows your account to impersonate the service account.

1. Stay on the "Service Accounts" page.
2. Find the service account you identified in Step 1. Click on it.
3. Go to the tab **"Principals with access"**.
4. Click the **"Grant Access"** button.
5. In the **"New principals"** field, enter your Google user account: `my-user1@google.com`.
6. In the **"Role"** dropdown, search for and select **"Service Account Token Creator"** (`roles/iam.serviceAccountTokenCreator`).
7. Click **"Save"**.

**Grant the following roles to the service account on the project:**

| Role |
|---|
| Dataplex Administrator |
| Dataplex Catalogue Admin |
| Dataplex Catalogue Editor |

### 3. Required APIs

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

### 4. Google Sheets Setup

Create a Google Sheet that will be used for the export:

Share the Google Sheet with the service account (`SA_EMAIL`) as an **Editor** so it can write values to the sheet.

*   **Glossary Export**: Create an empty Google Sheet (or use an existing one). The script will write to the first sheet.
*   **EntryLinks Export**: Create an empty Google Sheet (or use an existing one). The script will write to the first sheet.

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

### Required permissions (EntryLinks export)

The roles above cover the export. The individual permissions it uses are:

| Used for | Permissions | Predefined role that includes them |
|---|---|---|
| Quota and billing of the API calls, on `--user-project` | `serviceusage.services.use` | Service Usage Consumer (`roles/serviceusage.serviceUsageConsumer`) |
| Listing the glossary's terms and naming glossaries and terms in the sheet | `dataplex.glossaries.list`, `dataplex.glossaries.get`, `dataplex.glossaryTerms.list`, `dataplex.glossaryTerms.get` | Dataplex Catalog Viewer (`roles/dataplex.catalogViewer`) |
| Looking up the terms' entry links (`lookupEntryLinks`) and the FQNs of linked entries (`lookupEntry`, which checks access in the source system) | `dataplex.entries.get`, `dataplex.glossaryTerms.get`, and read access to the linked assets, e.g. `bigquery.tables.get` | Dataplex Catalog Viewer, BigQuery Metadata Viewer (`roles/bigquery.metadataViewer`) |
| Writing project IDs instead of project numbers | `resourcemanager.projects.get` on the projects of the glossaries and assets | Browser (`roles/browser`) |

Links that can't be read with these permissions are left out of the export or written with the raw entry name (see below).

---

## 1. Glossary Export

Exports categories and terms from a Dataplex business glossary into Google Sheets.

Values are written exactly as they are in Dataplex: Sheets doesn't convert them into numbers,
dates, booleans or formulas, so the sheet can be imported back (with `glossary-import.py`) or
used to update the glossary (with `glossary-update.py`) without changing them.

### Usage

```bash
python3 glossary-export.py
```

The script prompts for the Google Sheet URL and the glossary URL
(`.../dp-glossaries/projects/<project>/locations/<location>/glossaries/<glossary>`).
`<project>` can be the project ID or the project number. Dataplex stores glossary
entries under the project number, so the script resolves it with
`resourcemanager.projects.get` (included in the Browser role, `roles/browser`).
If it cannot, it logs a warning; in that case, pass the glossary URL with the
project number.

The export replaces the contents of the first tab (and of the aspects tab, see
below). It **fails without writing anything** if any term or category cannot be
read (transient errors are retried first). Glossary import deletes terms and
categories that are missing from the sheet, so a partial export must never be
re-imported.

### Sheets file schema (Glossary)

The first line of the sheet contains the following header:

`id, parent, display_name, description, overview, type, contact1_email, contact1_name, contact2_email, contact2_name, label1_key, label1_value, label2_key, label2_value`

Where:

*   `id` (required): Unique id for the term/category in the glossary.
*   `parent` (optional): The id parent for this term/category. If the paren is not provided then this will become direct child of the Glossary. the id provided should be present in this sheet and it should be an id of a category.
*   `display_name` (required): The display name of the term/category.
*   `description` (optional): A brief description of the term/category.
*   `overview` (optional): A rich text description of the term/category. It can contain html tags.
*   `type` (required): Describes whether this row represents a TERM or a CATEGORY. Only valid values for this is TERM or CATEGORY
*   `contact1_email` (optional): Email id of the data steward for this term/category.
*   `contact1_name` (optional): Name of the data steward for this term/category.
*   `contact2_email` (optional): Email id of another data steward for this term/category.
*   `contact2_name` (optional): Name of another data steward for this term/category.
*   `label1_key` (optional): Label1's key.
*   `label1_value` (optional): Label1's value.
*   `label2_key` (optional): Label2's key.
*   `label2_value` (optional): Label2's value.

### Sheets file schema (Aspects — second sheet)

Custom Dataplex **Aspects** on terms are exported to a **second worksheet**
(`Sheet2` by default), one row per aspect field:

`id, Aspect name, Aspect value`

Where:

*   `id` (required): The id of a term in Sheet 1. Custom aspects can only be
    set on terms, so only term aspects are exported. One term can have many
    aspect rows. If a category carries custom aspects (they can only be
    attached outside the Business Glossary UI, for example through the API),
    export logs a warning and skips them; re-importing the spreadsheet removes
    them from the category.
*   `Aspect name` (required): The aspect field identifier. Three forms are
    accepted on import. Export writes the short form for aspect types in the
    glossary's own project and location, and the qualified form for any other
    aspect type (for example a `global` aspect type used on a regional
    glossary, or one from a shared project), so that re-importing resolves to
    the same aspect type. It falls back to the resource-path form when the
    qualified form would be ambiguous (a project id containing `.` or `:`, or a
    location the importer does not recognize):
    *   Short — `<aspectTypeId>.<fieldName>`, e.g. `custom-gov.tier`
    *   Qualified — `<project>.<location>.<aspectTypeId>.<fieldName>`, e.g.
        `my-project.us-central1.custom-gov.tier`
    *   Full resource path —
        `projects/{project}/locations/{location}/aspectTypes/{aspectType}/{field}`

    When the project/location are omitted they default to the **target
    glossary's** project and location.

    A map field whose keys contain `.` is written as a single JSON object cell
    rather than as dotted sub-field rows, because dotted rows would rebuild a
    different structure on import.

    Values are written as plain text, so Sheets does not reinterpret them
    (`007` stays `007`, a value starting with `=` is not turned into a formula).
    Fields that are null or empty are not exported; on import, a missing row
    means the field is unset.
*   `Aspect value` (required): The scalar or serialized value (see the datatype
    table below).

#### Worked example

Sheet 1:

| id | parent | display_name | description | overview | type | ... |
|---|---|---|---|---|---|---|
| `term-customer-id` | `cat-core` | Customer ID | Unique customer key | `<p>…</p>` | TERM | … |

Sheet 2:

| id | Aspect name | Aspect value |
|---|---|---|
| `term-customer-id` | `custom-gov.tier` | `HIGH` |
| `term-customer-id` | `custom-gov.is_pii` | `true` |
| `term-customer-id` | `custom-gov.retention_days` | `365` |
| `term-customer-id` | `custom-gov.tags` | `["finance","core"]` |
| `term-customer-id` | `custom-gov.owner.email` | `alice@example.com` |

All five rows are combined into a **single** `custom-gov` aspect on the
`term-customer-id` entry.

#### Datatypes

Field types are the ones defined in the aspect type.

| Type | Cell representation (export) | Parsed as (import) |
|---|---|---|
| `string` | verbatim | verbatim |
| `enum` | verbatim | verbatim |
| `int` | numeric literal, e.g. `365` | `int` |
| `double` | numeric literal, e.g. `3.14` | `float` |
| `bool` | `true` / `false` | `true`/`false`/`yes`/`no` (case-insensitive); `1`/`0` only when the AspectType declares the field `bool` |
| `datetime` | RFC 3339 as returned, e.g. `2024-01-15T10:30:00Z` | verbatim string |
| `array` | compact JSON array, e.g. `["finance","core"]` | JSON array; a comma-separated list is also accepted **only** when the AspectType declares the field `array` |
| `record` / `map` | dotted sub-field rows (see below) | rebuilt into a nested object |

**Record/map flattening boundary.** One level of nesting is flattened into
dotted sub-field rows (`custom-gov.owner.email`). Anything **deeper** is written
as a compact JSON object string in a single cell — e.g. a value of
`{"owner": {"meta": {"x": 1}}}` exports as the row
`custom-gov.owner.meta` = `{"x":1}`. Both forms round-trip through import.

On import, each value is converted to the field's type; see the import
README for details.

#### System aspects are never exported to Sheet 2

`overview`, `contacts`, `glossary-term-aspect`, `glossary-category-aspect` and
`glossary-aspect` are already represented by Sheet 1 columns (or are purely
structural), so they are not written to Sheet 2. Dataplex Business Glossary
treats these aspect type ids as built-in in every project, so an aspect type of
your own with one of these ids cannot be imported through the glossary import;
the export logs a warning and skips it.

#### Glossaries without custom aspects

If the glossary has no custom aspects and the spreadsheet has no aspects tab,
**no aspects tab is created**. If the aspects tab already exists from an
earlier export, it is reset to just the header row: glossary import replaces
each term's aspects with the spreadsheet contents, so leftover rows would bring
back aspects that have since been removed. If the export cannot check whether
the tab exists, it fails rather than leave old rows behind.

#### Overriding the sheet name

The aspects worksheet defaults to `Sheet2`. To use a different tab, set:

```bash
export GLOSSARY_ASPECTS_SHEET_NAME="Aspects"
```


---

## 2. EntryLinks Export

Exports EntryLinks from Dataplex glossary terms into Google Sheets. For each term in the glossary, the script queries all relevant regional endpoints (or fans out to all endpoints for global glossaries), collects entry links, deduplicates symmetric links (synonym/related), and writes the results to the specified sheet.

### Usage

```bash
python3 entrylinks-export.py \
  --glossary-url <GLOSSARY_URL> \
  --spreadsheet-url <SPREADSHEET_URL> \
  --user-project <PROJECT_ID>
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `--glossary-url` | Yes | Dataplex Glossary URL to export EntryLinks from |
| `--spreadsheet-url` | Yes | Google Sheets URL to write the exported EntryLinks data |
| `--user-project` | Yes | Project ID to use for billing and API quota (e.g. `my-project-id`) |

### Example

```bash
python3 entrylinks-export.py \
  --glossary-url "https://console.cloud.google.com/dataplex/glossaries/my-glossary?project=my-project&location=us-central1" \
  --spreadsheet-url "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=0" \
  --user-project "my-billing-project"
```

### Sheets file schema (EntryLinks)

The first row of the sheet contains the following 6 column headers:

`Entry link type, Source Name, Source ID, Column, Target Name, Target ID`

Where:

*   `Entry link type`: Type of EntryLink: `definition`, `synonym` or `related`.
*   `Source Name`:
    *   For `definition` links: Fully Qualified Name (FQN) of the data asset (e.g. `bigquery:my-project.sales.orders`).
    *   For `synonym` and `related` links: The term display identifier `<project>.<location>.<glossaryDisplayName>.<termDisplayName>`.
*   `Source ID`:
    *   For `definition` links: Short ID of the data asset, for information only (e.g. `my-project.sales.orders` for a BigQuery table).
    *   For `synonym` and `related` links: The term ID of the source term (the `id` column of the glossary export).
*   `Column`: For `definition` links to a column, the column name (e.g. `order_id`). Empty for links to a whole data asset and for `synonym` and `related` links.
*   `Target Name`: The term display identifier `<project>.<location>.<glossaryDisplayName>.<termDisplayName>` of the target term.
*   `Target ID`: The term ID of the target term.

If a name can't be resolved (for example, a data asset without an FQN), the export logs a warning and writes the full Dataplex entry name instead. The EntryLinks import reads this format, so an exported sheet can be imported as is.
