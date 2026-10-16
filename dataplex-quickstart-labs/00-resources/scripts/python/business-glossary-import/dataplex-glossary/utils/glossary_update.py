"""Incremental update of a Dataplex business glossary from a glossary sheet.

Used by import/glossary-update.py. The sheet has the same columns as the sheet of the full
import (glossary-import.py), plus an optional 'Operation' column:

* Rows whose Operation is blank are compared with the glossary: a term or category that
  doesn't exist yet is created, one that differs is updated, and one that matches is left
  unchanged.
* Rows whose Operation is 'delete' delete the term or category. A category is deleted
  together with everything in it.
* Terms and categories that aren't in the sheet are never touched, so the sheet can hold
  the whole glossary (with a few rows changed) or only the rows to change.

Unlike the full import, which replaces the whole glossary through a metadata import job,
the changes are applied with the glossary and entry APIs, one term or category at a time.

The module is split into pure planning functions (parse_sheet, build_plan,
schedule_category_operations), which make no API calls, and functions that read the
glossary or apply a plan through utils.api_layer.
"""

import copy
import csv
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from googleapiclient.errors import HttpError

from utils import api_layer, business_glossary_utils, logging_utils
from utils.constants import (
    CONTACT_ROLE_STEWARD,
    CONTACTS_ASPECT_KEY,
    DATAPLEX_SYSTEM_ENTRY_GROUP,
    ENTRY_WAIT_TIMEOUT_SECONDS,
    GLOSSARY_CATEGORY_TYPE,
    GLOSSARY_MAX_CATEGORIES,
    GLOSSARY_MAX_TERMS,
    GLOSSARY_SHEET_HEADERS,
    GLOSSARY_TERM_TYPE,
    ID_PATTERN,
    MAX_WORKERS,
    OPERATION_COLUMN,
    OPERATION_DELETE,
    OVERVIEW_ASPECT_KEY,
    SERVER_MAX_CATEGORY_DEPTH,
    SYSTEM_ASPECT_PROJECTS,
)

logger = logging_utils.get_logger()

TERM = GLOSSARY_TERM_TYPE
CATEGORY = GLOSSARY_CATEGORY_TYPE
NODE_TYPES = (TERM, CATEGORY)

# Planned actions
ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"
ACTION_UNCHANGED = "UNCHANGED"
ACTION_DELETE = "DELETE"
ACTION_SKIP = "SKIP"

# Result statuses
STATUS_DONE = "DONE"
STATUS_UNCHANGED = "UNCHANGED"
STATUS_SKIPPED = "SKIPPED"
STATUS_CONFLICT = "CONFLICT"
STATUS_FAILED = "FAILED"

# Term/category fields compared and written (update masks use these names).
FIELD_DISPLAY_NAME = "display_name"
FIELD_DESCRIPTION = "description"
FIELD_LABELS = "labels"
FIELD_PARENT = "parent"

_PARENT_CATEGORY_PATTERN = re.compile(r"/categories/(?P<category_id>[^/]+)$")

NodeKey = Tuple[str, str]  # (type, id), e.g. ('TERM', 'revenue')


# =============================================================================
# Data model
# =============================================================================

@dataclass
class GlossaryRef:
    """Identifies the glossary being updated."""
    project_id: str
    location_id: str
    glossary_id: str
    # The catalog entries of terms and categories are named with the project number, not the
    # project id (see resolve_project_number). Empty until resolved.
    project_number: str = ""

    @property
    def project_location(self) -> str:
        return f"projects/{self.project_id}/locations/{self.location_id}"

    @property
    def glossary_name(self) -> str:
        return f"{self.project_location}/glossaries/{self.glossary_id}"

    def resource_name(self, node_type: str, node_id: str) -> str:
        collection = "terms" if node_type == TERM else "categories"
        return f"{self.glossary_name}/{collection}/{node_id}"

    def parent_resource(self, parent_id: str) -> str:
        """The resource name of a parent category, or of the glossary when `parent_id` is empty."""
        return self.resource_name(CATEGORY, parent_id) if parent_id else self.glossary_name

    def entry_name(self, node_type: str, node_id: str) -> str:
        """The name of the catalog entry that holds the aspects of a term or category.

        Both the entry's project and the term/category name inside the entry id use the project
        number: an entry id with the project id doesn't name the entry.
        """
        project = self.project_number or self.project_id
        collection = "terms" if node_type == TERM else "categories"
        return (
            f"projects/{project}/locations/{self.location_id}/entryGroups/{DATAPLEX_SYSTEM_ENTRY_GROUP}/entries/"
            f"projects/{project}/locations/{self.location_id}/glossaries/{self.glossary_id}/{collection}/{node_id}"
        )


def resolve_project_number(dataplex_service, glossary: GlossaryRef) -> str:
    """The number of the glossary's project, needed to name the catalog entries.

    Taken from the glossary URL when it already has the number, otherwise from the glossary's
    resource name returned by Dataplex (which uses the number), otherwise from Resource Manager.

    Raises:
        DataplexAPIError: If the glossary can't be read.
        ValueError: If the project number can't be found.
    """
    if glossary.project_id.isdigit():
        return glossary.project_id
    name = api_layer.get_glossary(dataplex_service, glossary.glossary_name).get("name", "")
    parts = name.split("/")
    if len(parts) > 1 and parts[0] == "projects" and parts[1].isdigit():
        return parts[1]
    try:
        project_number = api_layer.get_project_number(glossary.project_id, glossary.project_id)
    except Exception as error:
        raise ValueError(
            f"Could not find the project number of '{glossary.project_id}' ({error}). Use the project "
            f"number in --glossary-url instead (projects/<number>/locations/...)."
        ) from error
    if not project_number:
        raise ValueError(f"Could not find the project number of '{glossary.project_id}'.")
    return project_number


@dataclass
class SheetRow:
    """One row of the glossary sheet."""
    row_number: int
    node_id: str
    node_type: str  # TERM or CATEGORY; may be empty on a delete row
    operation: str = ""  # '' or OPERATION_DELETE
    parent: str = ""  # parent category id, '' for the glossary itself
    display_name: str = ""
    description: str = ""
    overview: str = ""
    contacts: List[Tuple[str, str]] = field(default_factory=list)  # (email, name)
    labels: Dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> NodeKey:
        return (self.node_type, self.node_id)

    @property
    def is_delete(self) -> bool:
        return self.operation == OPERATION_DELETE


@dataclass
class LiveNode:
    """A term or category as it currently is in the glossary."""
    node_type: str
    node_id: str
    parent: str  # parent category id, '' for the glossary itself
    display_name: str
    description: str
    labels: Dict[str, str]
    resource_name: str
    update_time: str = ""

    @property
    def key(self) -> NodeKey:
        return (self.node_type, self.node_id)


@dataclass
class LiveAspects:
    """The aspects of a term's or category's catalog entry that the sheet can change."""
    entry_name: str
    entry_type: str
    overview: str
    contacts: List[Tuple[str, str]]
    custom: Dict[str, Dict]  # aspect key -> aspect data


@dataclass
class Change:
    """A planned change to one term or category."""
    action: str
    node_type: str
    node_id: str
    row_number: Optional[int] = None
    fields: List[str] = field(default_factory=list)  # term/category fields to write
    aspects: Dict[str, Dict] = field(default_factory=dict)  # aspect key -> {'data': ...}
    sheet_row: Optional[SheetRow] = None
    live: Optional[LiveNode] = None
    entry_name: str = ""
    entry_type: str = ""
    entry_update_time: str = ""
    reason: str = ""

    @property
    def key(self) -> NodeKey:
        return (self.node_type, self.node_id)

    def describe(self) -> str:
        """A one-line, human-readable description of the change."""
        details = []
        if self.action == ACTION_CREATE and self.sheet_row:
            details.append(f"parent: {self.sheet_row.parent or '(glossary)'}")
        if self.action == ACTION_UPDATE and self.fields:
            details.append("fields: " + ", ".join(self.fields))
        if self.aspects:
            details.append("aspects: " + ", ".join(_aspect_label(key) for key in sorted(self.aspects)))
        if self.reason:
            details.append(self.reason)
        row = f" (row {self.row_number})" if self.row_number else ""
        return f"{self.action:<9} {self.node_type:<8} {self.node_id}{row}" + (
            "  " + "; ".join(details) if details else ""
        )


@dataclass
class UpdatePlan:
    """The changes to apply, and the problems that prevent applying them."""
    changes: List[Change] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def by_action(self, action: str) -> List[Change]:
        return [change for change in self.changes if change.action == action]

    @property
    def has_changes(self) -> bool:
        return any(change.action in (ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE) for change in self.changes)

    def summary(self) -> str:
        counts = {action: len(self.by_action(action)) for action in (
            ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE, ACTION_UNCHANGED, ACTION_SKIP
        )}
        return (
            f"{counts[ACTION_CREATE]} to create, {counts[ACTION_UPDATE]} to update, "
            f"{counts[ACTION_DELETE]} to delete, {counts[ACTION_UNCHANGED]} unchanged, "
            f"{counts[ACTION_SKIP]} skipped"
        )


@dataclass
class ChangeResult:
    """The outcome of applying one change."""
    change: Change
    status: str
    message: str = ""


def _aspect_label(aspect_key: str) -> str:
    """'dataplex-types.global.overview' -> 'overview'; custom aspect keys are kept as they are."""
    if aspect_key == OVERVIEW_ASPECT_KEY:
        return "overview"
    if aspect_key == CONTACTS_ASPECT_KEY:
        return "contacts"
    return aspect_key


# =============================================================================
# Sheet parsing
# =============================================================================

RowValidator = Callable[[Dict[str, str], int], None]


def parse_sheet(values: List[List[str]], validate_row: RowValidator) -> Tuple[List[SheetRow], List[str]]:
    """Parse the glossary sheet (Sheet 1) into rows.

    Args:
        values: The sheet's cell values, header row first.
        validate_row: Validates a non-delete row (a dict of column name -> stripped value) and
            raises an exception with a message when it's invalid. glossary-update.py passes the
            full import's validators, so both scripts accept exactly the same rows.

    Returns:
        (rows, errors). Rows are returned only when there are no errors.
    """
    if not values or not values[0]:
        return [], ["The glossary sheet is empty."]

    headers = [str(header).strip() for header in values[0]]
    operation_indexes = [i for i, header in enumerate(headers) if header.lower() == OPERATION_COLUMN]
    if len(operation_indexes) > 1:
        return [], ["The glossary sheet has more than one 'Operation' column."]
    operation_index = operation_indexes[0] if operation_indexes else None

    column_headers = [header for i, header in enumerate(headers) if i != operation_index and header]
    missing = [header for header in GLOSSARY_SHEET_HEADERS if header not in column_headers]
    unknown = [header for header in column_headers if header not in GLOSSARY_SHEET_HEADERS]
    duplicated = sorted({header for header in column_headers if column_headers.count(header) > 1})
    if missing or unknown or duplicated:
        problems = []
        if missing:
            problems.append(f"missing column(s): {', '.join(missing)}")
        if unknown:
            problems.append(f"unknown column(s): {', '.join(unknown)}")
        if duplicated:
            problems.append(f"duplicated column(s): {', '.join(duplicated)}")
        return [], [
            "Invalid header row in the glossary sheet (" + "; ".join(problems) + "). Expected the "
            "columns " + ", ".join(GLOSSARY_SHEET_HEADERS) + ", and optionally 'Operation'."
        ]

    rows: List[SheetRow] = []
    errors: List[str] = []
    for row_number, raw_row in enumerate(values[1:], start=2):
        cells = {
            header: str(raw_row[i]).strip() if i < len(raw_row) else ""
            for i, header in enumerate(headers) if header
        }
        if not any(cells.values()):
            continue  # blank row
        operation = cells.get(headers[operation_index], "").lower() if operation_index is not None else ""
        try:
            rows.append(_parse_row(row_number, cells, operation, validate_row))
        except Exception as row_error:
            errors.append(f"Row {row_number}: {row_error}")

    errors.extend(_check_duplicate_rows(rows))
    return (rows if not errors else []), errors


def _parse_row(row_number: int, cells: Dict[str, str], operation: str, validate_row: RowValidator) -> SheetRow:
    """Build a SheetRow, raising ValueError (or the validator's exception) when the row is invalid."""
    node_id = cells.get("id", "")
    node_type = cells.get("type", "")

    if operation not in ("", OPERATION_DELETE):
        raise ValueError(
            f"Invalid 'Operation' value '{operation}'. Leave it empty to create or update the row, "
            f"or set it to '{OPERATION_DELETE}'."
        )

    if operation == OPERATION_DELETE:
        if not node_id:
            raise ValueError("A row marked 'delete' needs the 'id' of the term or category to delete.")
        if not ID_PATTERN.match(node_id):
            raise ValueError(f"Invalid 'id' '{node_id}' on a row marked 'delete'.")
        if node_type and node_type not in NODE_TYPES:
            raise ValueError(f"Invalid 'type' '{node_type}'. Expected one of: {', '.join(NODE_TYPES)}.")
        return SheetRow(row_number=row_number, node_id=node_id, node_type=node_type, operation=operation)

    validate_row(cells, row_number)
    contacts = [
        (cells.get(f"contact{i}_email", ""), cells.get(f"contact{i}_name", ""))
        for i in (1, 2) if cells.get(f"contact{i}_email", "")
    ]
    labels = {
        cells[f"label{i}_key"]: cells.get(f"label{i}_value", "")
        for i in (1, 2) if cells.get(f"label{i}_key", "") and cells.get(f"label{i}_value", "")
    }
    return SheetRow(
        row_number=row_number,
        node_id=node_id,
        node_type=node_type,
        parent=cells.get("parent", ""),
        display_name=cells.get("display_name", ""),
        description=cells.get("description", ""),
        overview=cells.get("overview", ""),
        contacts=contacts,
        labels=labels,
    )


def _check_duplicate_rows(rows: List[SheetRow]) -> List[str]:
    """Report terms/categories that appear in more than one row."""
    errors = []
    first_row_by_key: Dict[NodeKey, int] = {}
    for row in rows:
        keys = [row.key] if row.node_type else [(node_type, row.node_id) for node_type in NODE_TYPES]
        for key in keys:
            if key in first_row_by_key:
                errors.append(
                    f"Row {row.row_number}: {key[0].lower()} '{key[1]}' is also in row "
                    f"{first_row_by_key[key]}. Each term or category can appear in only one row."
                )
                break
        for key in keys:
            first_row_by_key.setdefault(key, row.row_number)
    return errors


# =============================================================================
# Reading the glossary
# =============================================================================

def _parent_id(parent_resource: str) -> str:
    """The category id of a parent resource name, or '' when the parent is the glossary."""
    match = _PARENT_CATEGORY_PATTERN.search(parent_resource or "")
    return match.group("category_id") if match else ""


def _live_node(node_type: str, resource: Dict) -> LiveNode:
    name = resource.get("name", "")
    return LiveNode(
        node_type=node_type,
        node_id=name.rsplit("/", 1)[-1],
        parent=_parent_id(resource.get("parent", "")),
        display_name=resource.get("displayName", ""),
        description=resource.get("description", ""),
        labels=dict(resource.get("labels", {}) or {}),
        resource_name=name,
        update_time=resource.get("updateTime", ""),
    )


def fetch_live_nodes(dataplex_service, glossary: GlossaryRef) -> Dict[NodeKey, LiveNode]:
    """Read all categories and terms of the glossary."""
    nodes: Dict[NodeKey, LiveNode] = {}
    for category in api_layer.list_glossary_categories(dataplex_service, glossary.glossary_name):
        node = _live_node(CATEGORY, category)
        nodes[node.key] = node
    for term in api_layer.list_glossary_terms(dataplex_service, glossary.glossary_name):
        node = _live_node(TERM, term)
        nodes[node.key] = node
    return nodes


def keys_needing_entries(rows: Iterable[SheetRow], live_nodes: Dict[NodeKey, LiveNode]) -> List[NodeKey]:
    """The existing terms/categories whose aspects must be read to compare them with the sheet."""
    return [row.key for row in rows if not row.is_delete and row.key in live_nodes]


def fetch_entries(
    glossary: GlossaryRef,
    keys: List[NodeKey],
    max_workers: int = MAX_WORKERS,
    service_factory: Callable = None,
) -> Dict[NodeKey, Optional[Dict]]:
    """Look up the catalog entries of the given terms/categories in parallel.

    The value is None for an entry that doesn't exist or can't be read.
    """
    service_factory = service_factory or api_layer.get_dataplex_service

    def lookup(key: NodeKey) -> Optional[Dict]:
        return api_layer.lookup_entry(
            service_factory(), glossary.entry_name(*key), glossary.project_location
        )

    if not keys:
        return {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return dict(zip(keys, executor.map(lookup, keys)))


def _is_system_aspect_of_type(aspect_key: str, aspect_type_id: str) -> bool:
    split_key = business_glossary_utils.split_aspect_key(aspect_key)
    return bool(split_key) and split_key[0] in SYSTEM_ASPECT_PROJECTS and split_key[2] == aspect_type_id


def extract_live_aspects(entry: Dict) -> LiveAspects:
    """Pick the overview, contacts and custom aspects out of an entry returned by lookupEntry."""
    overview = ""
    contacts: List[Tuple[str, str]] = []
    custom: Dict[str, Dict] = {}
    for aspect_key, aspect in (entry.get("aspects") or {}).items():
        data = (aspect or {}).get("data") or {}
        if _is_system_aspect_of_type(aspect_key, "overview"):
            overview = data.get("content", "") or ""
        elif _is_system_aspect_of_type(aspect_key, "contacts"):
            contacts = [
                (identity.get("id", "") or "", identity.get("name", "") or "")
                for identity in data.get("identities", []) or []
                if identity.get("id") or identity.get("name")
            ]
        elif not business_glossary_utils.is_system_aspect(aspect_key):
            custom[aspect_key] = data
    return LiveAspects(
        entry_name=entry.get("name", ""),
        entry_type=entry.get("entryType", ""),
        overview=overview,
        contacts=contacts,
        custom=custom,
    )


# =============================================================================
# Planning
# =============================================================================

def build_plan(
    rows: List[SheetRow],
    live_nodes: Dict[NodeKey, LiveNode],
    sheet_aspects: Dict[str, Dict[str, Dict]],
    live_entries: Dict[NodeKey, Optional[Dict]],
    same_project: Callable[[str, str], bool] = lambda a, b: a == b,
) -> UpdatePlan:
    """Compare the sheet with the glossary and plan the changes. Makes no API calls.

    Args:
        rows: The parsed sheet rows.
        live_nodes: The glossary's current terms and categories (fetch_live_nodes).
        sheet_aspects: Custom aspects from the aspects sheet: id -> aspect type resource -> data.
        live_entries: The catalog entries of the existing terms/categories in the sheet
            (fetch_entries for keys_needing_entries).
        same_project: Whether two project ids/numbers name the same project, used to match
            the sheet's custom aspect types with the entry's aspect keys.
    """
    plan = UpdatePlan()
    deleted = _plan_deletes(rows, live_nodes, plan)

    upsert_rows = [row for row in rows if not row.is_delete]
    parents: Dict[NodeKey, str] = {key: node.parent for key, node in live_nodes.items() if key not in deleted}
    for row in upsert_rows:
        if row.key in deleted:
            plan.errors.append(
                f"Row {row.row_number}: {row.node_type.lower()} '{row.node_id}' is deleted by this "
                f"sheet ({deleted[row.key].reason or 'marked delete'}), so it can't also be created "
                f"or updated."
            )
            continue
        parents[row.key] = row.parent

    _validate_parents(upsert_rows, parents, deleted, plan)
    _validate_tree(upsert_rows, live_nodes, parents, plan)
    _validate_size(parents, plan)

    for row in upsert_rows:
        if row.key in deleted:
            continue
        plan.changes.append(_plan_upsert(row, live_nodes, sheet_aspects, live_entries, same_project, plan))

    plan.changes.extend(deleted.values())

    if not plan.errors:
        early_deletes, _ = split_category_deletes(plan.by_action(ACTION_DELETE), live_nodes)
        try:
            schedule_category_operations(plan.changes, live_nodes, {change.node_id for change in early_deletes})
        except ValueError as order_error:
            plan.errors.append(str(order_error))
    return plan


def _plan_deletes(rows: List[SheetRow], live_nodes: Dict[NodeKey, LiveNode], plan: UpdatePlan) -> Dict[NodeKey, Change]:
    """Resolve the delete rows, expanding each category to everything inside it.

    "Inside" is after the sheet's moves: a term or category that the sheet moves out of a
    deleted category is kept (it's moved before the category is deleted, see apply_plan).
    Rows of the sheet are never deleted implicitly: one that ends up inside a deleted category
    is reported by _validate_parents.
    """
    upsert_rows = {row.key: row for row in rows if not row.is_delete}
    final_parents = {key: node.parent for key, node in live_nodes.items()}
    final_parents.update({key: row.parent for key, row in upsert_rows.items()})
    children: Dict[str, List[NodeKey]] = {}
    for key, parent in final_parents.items():
        children.setdefault(parent, []).append(key)

    deleted: Dict[NodeKey, Change] = {}
    for row in rows:
        if not row.is_delete:
            continue
        candidate_types = [row.node_type] if row.node_type else list(NODE_TYPES)
        candidates = [(node_type, row.node_id) for node_type in candidate_types if (node_type, row.node_id) in live_nodes]
        if not candidates:
            kind = row.node_type.lower() if row.node_type else "term or category"
            plan.warnings.append(f"Row {row.row_number}: no {kind} '{row.node_id}' in the glossary; nothing to delete.")
            plan.changes.append(Change(
                action=ACTION_SKIP, node_type=row.node_type or "-", node_id=row.node_id,
                row_number=row.row_number, sheet_row=row, reason="not found in the glossary",
            ))
            continue
        if len(candidates) > 1:
            plan.errors.append(
                f"Row {row.row_number}: both a term and a category have the id '{row.node_id}'. "
                f"Set 'type' to TERM or CATEGORY to say which one to delete."
            )
            continue

        key = candidates[0]
        deleted.setdefault(key, Change(
            action=ACTION_DELETE, node_type=key[0], node_id=key[1], row_number=row.row_number,
            sheet_row=row, live=live_nodes[key],
        ))
        if key[0] == CATEGORY:
            # Deleting a category through the API doesn't delete what's inside it: its terms
            # and sub-categories are moved up a level instead. So everything inside is deleted
            # explicitly, before the category (see _blocking_item).
            # TODO: Revisit when the API's category delete changes. Today the moved-up contents
            # are also not replicated correctly for glossaries in the global location.
            for descendant in _descendants(key[1], children):
                if descendant in upsert_rows or descendant not in live_nodes:
                    continue  # a row of the sheet; reported by _validate_parents
                existing = deleted.get(descendant)
                if existing and existing.row_number and not existing.reason:
                    continue  # also marked 'delete' on its own row
                deleted[descendant] = Change(
                    action=ACTION_DELETE, node_type=descendant[0], node_id=descendant[1],
                    row_number=row.row_number, live=live_nodes[descendant],
                    reason=f"inside category '{key[1]}'",
                )
    return deleted


def _descendants(category_id: str, children: Dict[str, List[NodeKey]]) -> List[NodeKey]:
    """All terms and categories inside a category, at any depth."""
    result: List[NodeKey] = []
    pending = [category_id]
    seen: Set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for child in children.get(current, []):
            result.append(child)
            if child[0] == CATEGORY:
                pending.append(child[1])
    return result


def _validate_parents(
    upsert_rows: List[SheetRow], parents: Dict[NodeKey, str], deleted: Dict[NodeKey, Change], plan: UpdatePlan
) -> None:
    for row in upsert_rows:
        if not row.parent or row.key in deleted:
            continue
        parent_key = (CATEGORY, row.parent)
        if parent_key in parents:
            continue
        if parent_key in deleted:
            plan.errors.append(
                f"Row {row.row_number}: parent category '{row.parent}' is deleted by this sheet."
            )
        else:
            plan.errors.append(
                f"Row {row.row_number}: parent category '{row.parent}' is neither in the sheet nor "
                f"in the glossary."
            )


def _validate_tree(
    upsert_rows: List[SheetRow], live_nodes: Dict[NodeKey, LiveNode], parents: Dict[NodeKey, str], plan: UpdatePlan
) -> None:
    """Check cycles and depth for the terms/categories that the sheet creates or moves, and for
    everything under a moved category.

    Terms and categories that stay where they are are not checked, so a glossary that already
    breaks these rules can still be updated (also with a full sheet).
    """
    category_parents = {key[1]: parent for key, parent in parents.items() if key[0] == CATEGORY}
    placed_rows = [
        row for row in upsert_rows
        if row.key in parents and (row.key not in live_nodes or live_nodes[row.key].parent != row.parent)
    ]
    moved_categories = {row.node_id for row in placed_rows if row.node_type == CATEGORY}
    children: Dict[str, List[NodeKey]] = {}
    for key, parent in parents.items():
        children.setdefault(parent, []).append(key)

    affected: Dict[NodeKey, Optional[int]] = {row.key: row.row_number for row in placed_rows}
    for category_id in moved_categories:
        for descendant in _descendants(category_id, children):
            affected.setdefault(descendant, affected.get((CATEGORY, category_id)))

    reported_cycles: Set[str] = set()
    for key, row_number in affected.items():
        chain, has_cycle = _ancestor_chain(parents[key], category_parents)
        where = f"Row {row_number}: " if row_number else ""
        if has_cycle:
            cycle_id = min(chain) if chain else key[1]
            if cycle_id not in reported_cycles:
                reported_cycles.add(cycle_id)
                plan.errors.append(f"{where}the category parents form a cycle ({' -> '.join(chain)}).")
            continue
        # Same rule as the Dataplex API: categories are nested at most SERVER_MAX_CATEGORY_DEPTH
        # deep, and a term can be in any category.
        levels = len(chain) + 1  # levels below the glossary: ancestor categories + the node itself
        max_levels = SERVER_MAX_CATEGORY_DEPTH + (1 if key[0] == TERM else 0)
        if levels > max_levels:
            path = " > ".join(["(glossary)"] + list(reversed(chain)) + [key[1]])
            plan.errors.append(
                f"{where}{key[0].lower()} '{key[1]}' would be {levels} levels below the glossary ({path}); "
                f"a category can be at most {SERVER_MAX_CATEGORY_DEPTH} levels and a term at most "
                f"{SERVER_MAX_CATEGORY_DEPTH + 1} levels below the glossary."
            )


def _ancestor_chain(parent_id: str, category_parents: Dict[str, str]) -> Tuple[List[str], bool]:
    """The ancestor category ids from the parent up to the glossary, and whether there's a cycle."""
    chain: List[str] = []
    current = parent_id
    while current:
        if current in chain:
            return chain + [current], True
        chain.append(current)
        current = category_parents.get(current, "")
    return chain, False


def _validate_size(parents: Dict[NodeKey, str], plan: UpdatePlan) -> None:
    term_count = sum(1 for key in parents if key[0] == TERM)
    category_count = sum(1 for key in parents if key[0] == CATEGORY)
    if term_count > GLOSSARY_MAX_TERMS:
        plan.errors.append(
            f"After this update the glossary would have {term_count} terms; at most {GLOSSARY_MAX_TERMS} are allowed."
        )
    if category_count > GLOSSARY_MAX_CATEGORIES:
        plan.errors.append(
            f"After this update the glossary would have {category_count} categories; at most "
            f"{GLOSSARY_MAX_CATEGORIES} are allowed."
        )


def _plan_upsert(
    row: SheetRow,
    live_nodes: Dict[NodeKey, LiveNode],
    sheet_aspects: Dict[str, Dict[str, Dict]],
    live_entries: Dict[NodeKey, Optional[Dict]],
    same_project: Callable[[str, str], bool],
    plan: UpdatePlan,
) -> Change:
    live = live_nodes.get(row.key)
    row_sheet_aspects = sheet_aspects.get(row.node_id, {})

    if live is None:
        aspects = {}
        if row.overview:
            aspects[OVERVIEW_ASPECT_KEY] = {"data": {"content": row.overview}}
        if row.contacts:
            aspects[CONTACTS_ASPECT_KEY] = {"data": {"identities": _identities(row.contacts)}}
        for resource, data in row_sheet_aspects.items():
            aspects[business_glossary_utils.aspect_key_from_resource(resource)] = {"data": data}
        return Change(
            action=ACTION_CREATE, node_type=row.node_type, node_id=row.node_id, row_number=row.row_number,
            fields=[FIELD_DISPLAY_NAME, FIELD_DESCRIPTION, FIELD_LABELS, FIELD_PARENT],
            aspects=aspects, sheet_row=row,
        )

    fields = []
    # A blank display name makes Dataplex use the id, so blank and the id count as the same.
    if (row.display_name or row.node_id) != ((live.display_name or "").strip() or row.node_id):
        fields.append(FIELD_DISPLAY_NAME)
    if row.description != (live.description or "").strip():
        fields.append(FIELD_DESCRIPTION)
    if row.labels != live.labels:
        fields.append(FIELD_LABELS)
    if row.parent != live.parent:
        fields.append(FIELD_PARENT)

    change = Change(
        action=ACTION_UPDATE, node_type=row.node_type, node_id=row.node_id, row_number=row.row_number,
        fields=fields, sheet_row=row, live=live,
    )

    entry = live_entries.get(row.key)
    if entry is None:
        plan.errors.append(
            f"Row {row.row_number}: could not read the catalog entry of {row.node_type.lower()} "
            f"'{row.node_id}' (it doesn't exist or you don't have permission to read it), so its "
            f"overview, contacts and aspects can't be compared."
        )
    else:
        live_aspects = extract_live_aspects(entry)
        change.entry_name = live_aspects.entry_name
        change.entry_type = live_aspects.entry_type
        change.entry_update_time = entry.get("updateTime", "")
        change.aspects = _aspect_changes(row, row_sheet_aspects, live_aspects, same_project)

    if not change.fields and not change.aspects:
        change.action = ACTION_UNCHANGED
    return change


def _identities(contacts: List[Tuple[str, str]]) -> List[Dict[str, str]]:
    """Contacts aspect identities, written the same way as by the full import."""
    return [{"role": CONTACT_ROLE_STEWARD, "name": name, "id": email} for email, name in contacts]


def _aspect_changes(
    row: SheetRow,
    row_sheet_aspects: Dict[str, Dict],
    live_aspects: LiveAspects,
    same_project: Callable[[str, str], bool],
) -> Dict[str, Dict]:
    """The aspects of an existing term/category that differ from the sheet.

    A blank overview or contacts cell clears it, as in the full import. Custom aspects from the
    aspects sheet are merged into the entry's current aspect data field by field; custom
    aspects and fields that aren't in the aspects sheet are left as they are.
    """
    aspects: Dict[str, Dict] = {}
    if row.overview != (live_aspects.overview or "").strip():
        aspects[OVERVIEW_ASPECT_KEY] = {"data": {"content": row.overview}}
    if row.contacts != [(email.strip(), name.strip()) for email, name in live_aspects.contacts]:
        aspects[CONTACTS_ASPECT_KEY] = {"data": {"identities": _identities(row.contacts)}}

    for resource, sheet_data in row_sheet_aspects.items():
        live_key = _find_live_aspect_key(resource, live_aspects.custom, same_project)
        live_data = live_aspects.custom.get(live_key, {}) if live_key else {}
        merged = _deep_merge(live_data, sheet_data)
        if merged != live_data or not live_key:
            aspects[live_key or business_glossary_utils.aspect_key_from_resource(resource)] = {"data": merged}
    return aspects


def _find_live_aspect_key(
    aspect_type_resource: str, live_custom: Dict[str, Dict], same_project: Callable[[str, str], bool]
) -> Optional[str]:
    """The key of the entry's aspect of the given aspect type, if the entry has one."""
    sheet_key = business_glossary_utils.split_aspect_key(
        business_glossary_utils.aspect_key_from_resource(aspect_type_resource)
    )
    if not sheet_key:
        return None
    sheet_project, sheet_location, sheet_type_id = sheet_key
    for live_key in live_custom:
        split_key = business_glossary_utils.split_aspect_key(live_key)
        if not split_key:
            continue
        project, location, type_id = split_key
        if location == sheet_location and type_id == sheet_type_id and same_project(project, sheet_project):
            return live_key
    return None


def _deep_merge(base: Dict, overrides: Dict) -> Dict:
    """Merge `overrides` into a copy of `base`: nested records are merged, other values replaced."""
    merged = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


# =============================================================================
# Ordering category creations and moves
# =============================================================================

@dataclass
class CategoryStep:
    """Create or move one category. `final` is False for a temporary move to the glossary root."""
    change: Change
    parent: str
    final: bool = True


def schedule_category_operations(
    changes: List[Change], live_nodes: Dict[NodeKey, LiveNode], removed_categories: Iterable[str] = ()
) -> List[CategoryStep]:
    """Order category creations and moves so that each step is valid on its own.

    The API rejects a step that would create a cycle or a category chain deeper than
    SERVER_MAX_CATEGORY_DEPTH, even when the final tree is valid (e.g. two categories swapping
    places). Steps are applied as soon as they are valid; when no step is, one moving category
    first goes to the glossary root (always valid) and is moved to its final parent later.

    `removed_categories` are deleted before the creations and moves (see
    split_category_deletes), so they don't count.
    """
    removed = set(removed_categories)
    category_parents = {
        node.node_id: node.parent for key, node in live_nodes.items()
        if key[0] == CATEGORY and node.node_id not in removed
    }
    pending = [
        change for change in changes
        if change.node_type == CATEGORY and (
            change.action == ACTION_CREATE or (change.action == ACTION_UPDATE and FIELD_PARENT in change.fields)
        )
    ]
    steps: List[CategoryStep] = []
    parked: Set[str] = set()
    max_rounds = 2 * len(pending) + 2

    for _ in range(max_rounds):
        if not pending:
            break
        progressed = False
        for change in list(pending):
            target = change.sheet_row.parent
            if target and target not in category_parents:
                continue  # its parent is created by a later step
            trial = dict(category_parents)
            trial[change.node_id] = target
            if _category_tree_is_valid(trial):
                category_parents = trial
                steps.append(CategoryStep(change=change, parent=target))
                pending.remove(change)
                progressed = True
        if progressed:
            continue
        mover = next(
            (change for change in pending if change.action == ACTION_UPDATE
             and change.node_id not in parked and category_parents.get(change.node_id)),
            None,
        )
        if mover is None:
            break
        category_parents[mover.node_id] = ""
        parked.add(mover.node_id)
        steps.append(CategoryStep(change=mover, parent="", final=False))

    if pending:
        raise ValueError(
            "Could not find a valid order for these category changes: "
            + ", ".join(f"{change.node_id} (row {change.row_number})" for change in pending)
        )
    return steps


def _category_tree_is_valid(category_parents: Dict[str, str]) -> bool:
    """No cycles, and no category chain deeper than the API allows."""
    for category_id in category_parents:
        depth = 0
        current = category_id
        while current:
            depth += 1
            if depth > SERVER_MAX_CATEGORY_DEPTH or depth > len(category_parents):
                return False
            current = category_parents.get(current, "")
    return True


# =============================================================================
# Applying a plan
# =============================================================================

def apply_plan(
    plan: UpdatePlan,
    glossary: GlossaryRef,
    live_nodes: Dict[NodeKey, LiveNode],
    max_workers: int = MAX_WORKERS,
    check_conflicts: bool = True,
    entry_wait_timeout: float = ENTRY_WAIT_TIMEOUT_SECONDS,
    service_factory: Callable = None,
    sleep: Callable[[float], None] = time.sleep,
) -> List[ChangeResult]:
    """Apply a plan that has no errors.

    Order:
      1. Terms are deleted, then the categories whose contents are all deleted (deepest
         first). Deleting first frees room under the glossary's limits and depth for the
         creations and moves.
      2. Categories are created and moved, parents first (sequential), then the other
         category updates and the term creations and updates are applied (parallel).
      3. Overview, contacts and custom aspects are written.
      4. The categories that something was moved out of are deleted (deepest first), now
         that only deleted items were inside them.
    A category is not deleted while something inside it is still there (a delete or a move
    out of it failed), because the API would move it up a level instead of deleting it.

    A change that fails, or whose term/category was changed by someone else since it was read
    (with check_conflicts), is reported and skipped; the other changes are still applied.
    Running the same sheet again only applies what's left.
    """
    if plan.errors:
        raise ValueError("The plan has errors; fix them before applying it.")
    service_factory = service_factory or api_layer.get_dataplex_service
    applier = _PlanApplier(glossary, check_conflicts, entry_wait_timeout, service_factory, sleep)

    results: Dict[int, ChangeResult] = {}
    for change in plan.changes:
        if change.action == ACTION_UNCHANGED:
            results[id(change)] = ChangeResult(change, STATUS_UNCHANGED)
        elif change.action == ACTION_SKIP:
            results[id(change)] = ChangeResult(change, STATUS_SKIPPED, change.reason)

    deletes = plan.by_action(ACTION_DELETE)
    early_category_deletes, late_category_deletes = split_category_deletes(deletes, live_nodes)
    live_children = _children_by_parent(live_nodes)
    delete_results: Dict[NodeKey, ChangeResult] = {}

    def delete_categories(category_deletes: List[Change]) -> None:
        for change in category_deletes:
            outcome = (
                _blocking_item(change, live_children, delete_results, applier.moved)
                or applier.delete(change)
            )
            results[id(change)] = delete_results[change.key] = outcome

    # 1. Deletes: terms (parallel), then the categories with nothing to keep inside (sequential).
    term_deletes = [change for change in deletes if change.node_type == TERM]
    for change, outcome in _run_parallel(term_deletes, applier.delete, max_workers):
        results[id(change)] = delete_results[change.key] = outcome
    delete_categories(early_category_deletes)

    # 2. Categories: creations and moves in a valid order (sequential), then the other category
    #    updates and the terms (parallel).
    resource_changes = plan.by_action(ACTION_CREATE) + plan.by_action(ACTION_UPDATE)
    category_steps = schedule_category_operations(
        resource_changes, live_nodes, {change.node_id for change in early_category_deletes}
    )
    scheduled = {id(step.change) for step in category_steps}
    for step in category_steps:
        if id(step.change) in results:
            continue
        outcome = applier.write_resource(step.change, parent=step.parent, final=step.final)
        if outcome is not None:
            results[id(step.change)] = outcome
    other_category_updates = [
        change for change in resource_changes
        if change.node_type == CATEGORY and id(change) not in scheduled and change.fields
    ]
    term_changes = [change for change in resource_changes if change.node_type == TERM and change.fields]
    parallel = [change for change in other_category_updates + term_changes if id(change) not in results]
    for change, outcome in _run_parallel(parallel, applier.write_resource, max_workers):
        if outcome is not None:
            results[id(change)] = outcome

    # 3. Aspects (parallel), for the creations and updates that succeeded so far.
    with_aspects = [change for change in resource_changes if change.aspects and id(change) not in results]
    for change, outcome in _run_parallel(with_aspects, applier.write_aspects, max_workers):
        if outcome is not None:
            results[id(change)] = outcome

    for change in resource_changes:
        results.setdefault(id(change), ChangeResult(change, STATUS_DONE))

    # 4. The categories that something was moved out of (sequential).
    delete_categories(late_category_deletes)

    return [results[id(change)] for change in plan.changes]


def split_category_deletes(
    deletes: List[Change], live_nodes: Dict[NodeKey, LiveNode]
) -> Tuple[List[Change], List[Change]]:
    """Split the category deletes into those that can be applied before the creations and moves
    (everything inside them is deleted too) and those that must wait until the terms and
    categories that the sheet moves out of them have been moved. Each list is deepest first.
    """
    deleted_keys = {change.key for change in deletes}
    live_children = _children_by_parent(live_nodes)
    early: List[Change] = []
    late: List[Change] = []
    for change in deletes:
        if change.node_type != CATEGORY:
            continue
        inside = _descendants(change.node_id, live_children)
        (early if all(key in deleted_keys for key in inside) else late).append(change)

    def deepest_first(change: Change) -> int:
        return -_live_depth(change.node_id, live_nodes)

    return sorted(early, key=deepest_first), sorted(late, key=deepest_first)


def _children_by_parent(live_nodes: Dict[NodeKey, LiveNode]) -> Dict[str, List[NodeKey]]:
    children: Dict[str, List[NodeKey]] = {}
    for key, node in live_nodes.items():
        children.setdefault(node.parent, []).append(key)
    return children


def _run_parallel(changes: List[Change], func: Callable, max_workers: int) -> List[Tuple[Change, Optional[ChangeResult]]]:
    if not changes:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(zip(changes, executor.map(func, changes)))


def _live_depth(category_id: str, live_nodes: Dict[NodeKey, LiveNode]) -> int:
    depth = 0
    current = category_id
    seen: Set[str] = set()
    while current and current not in seen:
        seen.add(current)
        depth += 1
        node = live_nodes.get((CATEGORY, current))
        current = node.parent if node else ""
    return depth


def _blocking_item(
    category_change: Change,
    live_children: Dict[str, List[NodeKey]],
    delete_results: Dict[NodeKey, ChangeResult],
    moved: Set[NodeKey],
) -> Optional[ChangeResult]:
    """Don't delete a category when something inside it is still there.

    Deleting a category with children makes the API move the children up a level, so when a
    delete inside it failed (or was skipped because of a conflict), or something that the sheet
    moves out of it couldn't be moved, the category is kept.
    """
    pending = list(live_children.get(category_change.node_id, []))
    while pending:
        key = pending.pop()
        if key in moved:
            continue  # moved out of the category (with everything inside it)
        outcome = delete_results.get(key)
        if outcome is not None and outcome.status in (STATUS_DONE, STATUS_SKIPPED):
            if key[0] == CATEGORY:
                pending.extend(live_children.get(key[1], []))
            continue
        what = f"{key[0].lower()} '{key[1]}'"
        return ChangeResult(
            category_change, STATUS_SKIPPED,
            f"not deleted because {what} inside it was not deleted or moved out",
        )
    return None


class _PlanApplier:
    """Applies single changes; safe to call from several threads."""

    def __init__(self, glossary, check_conflicts, entry_wait_timeout, service_factory, sleep):
        self.glossary = glossary
        self.check_conflicts = check_conflicts
        self.entry_wait_timeout = entry_wait_timeout
        self.service_factory = service_factory
        self.sleep = sleep
        # Terms/categories whose parent was changed (to their final parent, or to the glossary
        # root for a two-step move): they're no longer inside the category they were in.
        self.moved: Set[NodeKey] = set()
        self._lock = threading.Lock()

    # --- term/category writes -------------------------------------------------

    def write_resource(self, change: Change, parent: Optional[str] = None, final: bool = True) -> Optional[ChangeResult]:
        """Create or update a term/category. Returns a result only when the change is finished early
        (failed or conflict); None means continue with its aspects."""
        service = self.service_factory()
        row = change.sheet_row
        target_parent = row.parent if parent is None else parent
        try:
            if change.action == ACTION_CREATE:
                body = self._resource_body(row, target_parent)
                try:
                    api_layer.create_glossary_resource(
                        service, self.glossary.glossary_name, change.node_type, change.node_id, body
                    )
                except HttpError as error:
                    if not _is_already_exists(error):
                        raise
                    return self._already_exists(service, change, body, target_parent)
                return None

            conflict = self._conflict(service, change)
            if conflict:
                return conflict
            fields = change.fields if final else [FIELD_PARENT]
            body = self._resource_body(row, target_parent)
            response = api_layer.update_glossary_resource(
                service, change.live.resource_name, change.node_type, body, fields
            )
            if change.live and isinstance(response, dict) and response.get("updateTime"):
                change.live.update_time = response["updateTime"]  # our own write isn't a conflict
            if FIELD_PARENT in fields:
                with self._lock:
                    self.moved.add(change.key)
            return None
        except Exception as error:
            logger.debug(f"Failed to write {change.node_type} {change.node_id}", exc_info=True)
            return ChangeResult(change, STATUS_FAILED, _error_message(error))

    def _already_exists(self, service, change: Change, body: Dict, parent_id: str) -> Optional[ChangeResult]:
        """Handle a create that failed because the term/category already exists.

        That happens when an earlier attempt of the same call succeeded but its response was
        lost (the call is retried), or when someone else created it since this run read the
        glossary. The first case leaves exactly the sheet's values; anything else is a conflict.
        """
        resource_name = self.glossary.resource_name(change.node_type, change.node_id)
        if not self.check_conflicts:
            logger.info(f"{change.node_type.lower()} '{change.node_id}' already exists; updating it.")
            api_layer.update_glossary_resource(
                service, resource_name, change.node_type, body,
                [FIELD_DISPLAY_NAME, FIELD_DESCRIPTION, FIELD_LABELS, FIELD_PARENT],
            )
            return None
        current = api_layer.get_glossary_resource(service, resource_name, change.node_type)
        if _matches_row(current, change.sheet_row, parent_id):
            return None
        return ChangeResult(change, STATUS_CONFLICT, _ALREADY_EXISTS_MESSAGE)

    def _resource_body(self, row: SheetRow, parent_id: str) -> Dict:
        """The term/category body: its sheet values, under `parent_id` ('' for the glossary)."""
        return {
            "displayName": row.display_name,
            "description": row.description,
            "labels": dict(row.labels),
            "parent": self.glossary.parent_resource(parent_id),
        }

    # --- aspects ----------------------------------------------------------------

    def write_aspects(self, change: Change) -> Optional[ChangeResult]:
        service = self.service_factory()
        try:
            entry_name, entry_type = change.entry_name, change.entry_type
            if change.action == ACTION_CREATE or not entry_name:
                entry = self._wait_for_entry(service, change)
                if entry is None:
                    return ChangeResult(
                        change, STATUS_FAILED,
                        f"created, but its catalog entry didn't appear within {self.entry_wait_timeout:.0f}s, "
                        f"so its overview/contacts/aspects weren't written. Run the update again to write them.",
                    )
                entry_name, entry_type = entry.get("name", ""), entry.get("entryType", "")
            elif not change.fields:
                # Only aspects change: check that nobody changed the entry since it was read. (When
                # fields were written, the term/category was checked, and our own write updates the
                # entry, so the entry can't be compared.)
                conflict = self._entry_conflict(service, change)
                if conflict:
                    return conflict
            api_layer.update_entry_aspects(service, entry_name, change.aspects, entry_type)
            return None
        except Exception as error:
            logger.debug(f"Failed to write aspects of {change.node_type} {change.node_id}", exc_info=True)
            prefix = "created, but writing its aspects failed: " if change.action == ACTION_CREATE else (
                "fields updated, but writing its aspects failed: " if change.fields else ""
            )
            return ChangeResult(change, STATUS_FAILED, prefix + _error_message(error))

    def _wait_for_entry(self, service, change: Change) -> Optional[Dict]:
        entry_name = self.glossary.entry_name(change.node_type, change.node_id)
        deadline = time.monotonic() + self.entry_wait_timeout
        delay = 1.0
        while True:
            entry = api_layer.lookup_entry(service, entry_name, self.glossary.project_location)
            if entry is not None:
                return entry
            if time.monotonic() + delay > deadline:
                return None
            self.sleep(delay)
            delay = min(delay * 2, 10.0)

    # --- deletes ----------------------------------------------------------------

    def delete(self, change: Change) -> ChangeResult:
        service = self.service_factory()
        try:
            conflict = self._conflict(service, change)
            if conflict:
                return conflict
            api_layer.delete_glossary_resource(service, change.live.resource_name, change.node_type)
            return ChangeResult(change, STATUS_DONE)
        except HttpError as error:
            if _status(error) == 404:
                return ChangeResult(change, STATUS_SKIPPED, "already deleted")
            return ChangeResult(change, STATUS_FAILED, _error_message(error))
        except Exception as error:
            return ChangeResult(change, STATUS_FAILED, _error_message(error))

    # --- conflict check ---------------------------------------------------------

    def _conflict(self, service, change: Change) -> Optional[ChangeResult]:
        """A CONFLICT result when the term/category changed since it was read."""
        if not self.check_conflicts or not change.live or not change.live.update_time:
            return None
        current = api_layer.get_glossary_resource(service, change.live.resource_name, change.node_type)
        if current.get("updateTime", "") != change.live.update_time:
            return ChangeResult(change, STATUS_CONFLICT, _CONFLICT_MESSAGE)
        return None

    def _entry_conflict(self, service, change: Change) -> Optional[ChangeResult]:
        """A CONFLICT result when the term's/category's catalog entry changed since it was read."""
        if not self.check_conflicts or not change.entry_update_time:
            return None
        current = api_layer.lookup_entry(service, change.entry_name, self.glossary.project_location)
        if current is None or current.get("updateTime", "") != change.entry_update_time:
            return ChangeResult(change, STATUS_CONFLICT, _CONFLICT_MESSAGE)
        return None


_CONFLICT_MESSAGE = (
    "changed in Dataplex since this run read it; not modified. Run the update again to compare "
    "with its current state."
)


_ALREADY_EXISTS_MESSAGE = (
    "already exists in the glossary with other values (created since this run read the "
    "glossary); not modified. Run the update again to compare with its current state."
)


def _status(error: HttpError) -> Optional[int]:
    return getattr(getattr(error, "resp", None), "status", None)


def _is_already_exists(error: HttpError) -> bool:
    """A 409 that means ALREADY_EXISTS. HTTP 409 also means ABORTED (a concurrent transaction),
    which the write helpers retry; one that persists is a failure, not an existing resource."""
    return _status(error) == 409 and api_layer.http_error_status_name(error) != "ABORTED"


def _matches_row(resource: Dict, row: SheetRow, parent_id: str) -> bool:
    """Whether a term/category resource has the sheet row's values (compared as in _plan_upsert)."""
    display_name = (resource.get("displayName") or "").strip() or row.node_id
    return (
        display_name == (row.display_name or row.node_id)
        and (resource.get("description") or "").strip() == row.description
        and dict(resource.get("labels") or {}) == row.labels
        and _parent_id(resource.get("parent", "")) == parent_id
    )


def _error_message(error: Exception) -> str:
    if isinstance(error, HttpError):
        reason = error._get_reason() if hasattr(error, "_get_reason") else str(error)
        return f"HTTP {_status(error)}: {reason}"
    return str(error) or type(error).__name__


# =============================================================================
# Reporting
# =============================================================================

def format_plan(plan: UpdatePlan, max_lines: int = 200) -> List[str]:
    """Human-readable lines describing the plan (unchanged rows are only counted)."""
    lines = [change.describe() for change in plan.changes if change.action != ACTION_UNCHANGED]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... and {len(lines) - max_lines} more (see the results file)"]
    return lines + [f"Summary: {plan.summary()}"]


def write_results_csv(path: str, results: List[ChangeResult], dry_run: bool = False) -> None:
    """Write one line per term/category: row, type, id, action, status, details.

    For a dry run the status is PLANNED for the changes that would be applied.
    """
    planned_status = {ACTION_UNCHANGED: STATUS_UNCHANGED, ACTION_SKIP: STATUS_SKIPPED}
    with open(path, "w", newline="", encoding="utf-8") as results_file:
        writer = csv.writer(results_file)
        writer.writerow(["row", "type", "id", "action", "status", "fields", "aspects", "details"])
        for result in results:
            change = result.change
            status = planned_status.get(change.action, "PLANNED") if dry_run else result.status
            writer.writerow([
                change.row_number or "",
                change.node_type,
                change.node_id,
                change.action,
                status,
                ",".join(change.fields) if change.action == ACTION_UPDATE else "",
                ",".join(_aspect_label(key) for key in sorted(change.aspects)),
                "; ".join(part for part in (change.reason, result.message) if part),
            ])
