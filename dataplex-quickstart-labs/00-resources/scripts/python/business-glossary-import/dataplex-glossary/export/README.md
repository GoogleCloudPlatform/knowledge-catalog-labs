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

---

## 1. Glossary Export

Exports categories and terms from a Dataplex business glossary into Google Sheets.

### Usage

```bash
python3 glossary-export.py
```

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

Custom Dataplex **Aspects** are exported to a **second worksheet** (`Sheet2` by
default) using a normalized 3-column schema. Sheet 1 above is **unchanged**.

`id, Aspect name, Aspect value`

Where:

*   `id` (required): Foreign key to the `id` column of Sheet 1. One term or
    category may own many aspect rows.
*   `Aspect name` (required): The aspect field identifier. Three forms are
    accepted on import; export always writes the short form:
    *   Short — `<aspectTypeId>.<fieldName>`, e.g. `custom-gov.tier`
    *   Qualified — `<project>.<location>.<aspectTypeId>.<fieldName>`, e.g.
        `my-project.us-central1.custom-gov.tier`
    *   Full resource path —
        `projects/{project}/locations/{location}/aspectTypes/{aspectType}/{field}`

    When the project/location are omitted they default to the **target
    glossary's** project and location.
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

All five rows are aggregated into a **single** `custom-gov` aspect on the
`term-customer-id` entry.

#### Datatypes

Types follow `AspectType.MetadataTemplate` in
`google/cloud/dataplex/v1/catalog.proto`.

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

When the AspectType can be read (`dataplex.projects.locations.aspectTypes.get`),
its `MetadataTemplate` drives the coercion. If it cannot be read (permission
denied or missing), a warning is logged and value types are inferred
heuristically.

#### System aspects are never exported to Sheet 2

`overview`, `contacts`, `glossary-term-aspect` and `glossary-category-aspect`
are already represented by Sheet 1 columns (or are purely structural), so they
are filtered out of Sheet 2. The filter matches the
`<location>.<aspectTypeId>` suffix, so both `655216118709.global.overview` and
`dataplex-types.global.overview` are recognized.

#### Backward compatibility

If the glossary has no custom aspects, **no second sheet is created and nothing
is written** — a single informational log line is emitted and the export is
byte-for-byte what it was before. Sheet 1's 14-column schema is unchanged.

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

The first row of the sheet contains the following header:

`entry_link_type, source_entry, target_entry, source_path`

Where:

*   `entry_link_type` (required): Type of EntryLink. Valid values: `definition`, `synonym`, `related`.
*   `source_entry` (required): Full Dataplex entry resource path for the source (e.g. `projects/my-project/locations/us/entryGroups/@bigquery/entries/my-entry`).
*   `target_entry` (required): Full Dataplex entry resource path for the target.
*   `source_path` (optional): Path within the source entry (e.g. a BigQuery column path like `Schema.Field1`). Populated for definition entrylinks.
