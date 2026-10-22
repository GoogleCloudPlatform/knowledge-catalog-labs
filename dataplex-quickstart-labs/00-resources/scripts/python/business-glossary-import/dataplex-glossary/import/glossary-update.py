"""Glossary update: apply only the changes in a glossary sheet to a Dataplex glossary.

The sheet is the same as for glossary-import.py (the glossary sheet, plus the optional custom
aspects sheet), with one optional extra column, 'Operation':

* Operation empty: the term/category is created when it doesn't exist, updated when it
  differs from the glossary, and left alone when it matches.
* Operation 'delete': the term/category is deleted; a category is deleted together with
  everything in it.

Terms and categories that aren't in the sheet are not touched. Unlike glossary-import.py,
which replaces the whole glossary with a metadata import job, this script changes only what
differs, with the glossary and entry APIs, so it needs no Cloud Storage bucket.

Usage:
    python3 glossary-update.py --spreadsheet-url <URL> --glossary-url <URL> [--dry-run] [--yes]

Exit codes: 0 when the changes were applied (or there was nothing to change, or it was a dry
run); 1 when the sheet has errors, the update was cancelled, or a change failed or had a
conflict; 2 for invalid arguments; 130 when interrupted with Ctrl+C.
"""

import argparse
import datetime
import importlib.util
import os
import sys
from typing import Dict, List, Optional, Set, Tuple

curr_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(curr_dir))
sys.path.append(os.path.dirname(os.path.dirname(curr_dir)))

import gspread
from google.auth import default

from utils import api_layer, argument_parser, glossary_update, logging_utils
from utils.constants import (
    DEFAULT_GLOSSARY_WRITES_PER_MINUTE,
    GLOSSARY_CATEGORY_TYPE,
    GLOSSARY_TERM_TYPE,
    MAX_WORKERS,
    SPREADSHEET_URL_PATTERN,
)

logger = logging_utils.get_logger()


def _load_glossary_import_module():
    """Load glossary-import.py, whose sheet validators and aspects-sheet reader are reused here.

    Reusing them means that both scripts accept exactly the same sheet.
    """
    spec = importlib.util.spec_from_file_location(
        "glossary_import", os.path.join(curr_dir, "glossary-import.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


glossary_import = _load_glossary_import_module()


class UpdateSheetProcessor(glossary_import.SheetProcessor):
    """The full import's sheet processor, with messages that fit an update."""

    @staticmethod
    def _warn_no_custom_aspects(sheet_name, reason):
        # The full import warns that custom aspects will be removed; an update leaves them as they are.
        print(f"{reason}; no custom aspects will be changed.")


def make_row_validator(processor) -> glossary_update.RowValidator:
    """Validate a glossary sheet row with the same checks as glossary-import.py."""
    return processor.validate_glossary_row


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply only the changes in a glossary sheet to a Dataplex business glossary.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--spreadsheet-url", required=True,
                        help="Google Sheets URL of the glossary sheet (the tab in the URL is used, the first tab otherwise)")
    parser.add_argument("--glossary-url", required=True,
                        help="URL or resource name of the glossary: any text containing "
                             "projects/<project>/locations/<location>/glossaries/<glossary>")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the changes and write the results file, without changing anything")
    parser.add_argument("--yes", action="store_true",
                        help="Apply the changes without asking for confirmation")
    parser.add_argument("--max-qpm", type=int, default=DEFAULT_GLOSSARY_WRITES_PER_MINUTE,
                        help="Maximum term/category creates, updates and deletes per minute (default: "
                             f"{DEFAULT_GLOSSARY_WRITES_PER_MINUTE}). Dataplex allows 100 per minute per user "
                             "and 250 per minute per project by default; calls over the quota are retried.")
    parser.add_argument("--skip-conflict-check", action="store_true",
                        help="Don't check that a term/category is unchanged since it was read before changing it")
    parser.add_argument("--output-dir", default=os.getcwd(),
                        help="Directory for the results file (default: the current directory)")
    args = parser.parse_args(argv)
    if args.max_qpm <= 0:
        parser.error("--max-qpm must be positive")
    return args


def open_glossary_worksheet(spreadsheet, spreadsheet_url: str):
    """The tab given by the URL's gid, or the first tab (as glossary-import.py).

    Raises:
        ValueError: If the spreadsheet has no tab with the URL's gid.
    """
    match = SPREADSHEET_URL_PATTERN.match(spreadsheet_url)
    gid = match.group("gid") if match else None
    if gid is None:
        return spreadsheet.sheet1
    try:
        return spreadsheet.get_worksheet_by_id(int(gid))
    except gspread.exceptions.WorksheetNotFound as error:
        raise ValueError(
            f"The spreadsheet has no tab with gid {gid} (from --spreadsheet-url). Open the glossary "
            f"tab in the browser and copy its URL, or remove '#gid=...' to use the first tab."
        ) from error


def delete_warning(plan: glossary_update.UpdatePlan) -> Optional[str]:
    """The warning shown with the plan when it deletes anything, or None."""
    deletes = plan.by_action(glossary_update.ACTION_DELETE)
    if not deletes:
        return None
    return (f"WARNING: {len(deletes)} term(s)/categor(ies) will be DELETED, together with their "
            f"entry links (definition, synonym and related links). This can't be undone.")


def confirm(plan: glossary_update.UpdatePlan) -> bool:
    """Ask before applying the plan; a plan with deletes needs 'yes' typed in full.

    Raises:
        ValueError: If there is no input to read the answer from (use --yes).
    """
    if plan.by_action(glossary_update.ACTION_DELETE):
        prompt, accepted = "\nType 'yes' to apply these changes, including the deletes: ", ("yes",)
    else:
        prompt, accepted = "\nApply these changes? [y/N]: ", ("y", "yes")
    try:
        answer = input(prompt).strip().lower()
    except EOFError as error:
        print()  # end the prompt's line
        raise ValueError("No answer to the confirmation prompt. Run with --yes to apply the changes.") from error
    return answer in accepted


def aspect_sheet_ids(rows: List[glossary_update.SheetRow]) -> Tuple[Set[str], Set[str]]:
    """The term ids and category ids that rows of the aspects sheet may reference.

    Rows marked 'delete' are included, so that a term can be deleted although the aspects
    sheet still has rows for it (they are dropped, see drop_aspects_of_deleted_terms). A
    delete row without a type may be a term. Category ids are passed separately so that
    a row for a category gets a clear error, or a warning when a term shares the id.
    """
    term_ids = {row.node_id for row in rows if row.node_type in (GLOSSARY_TERM_TYPE, "")}
    category_ids = {row.node_id for row in rows if row.node_type == GLOSSARY_CATEGORY_TYPE}
    return term_ids, category_ids


def drop_aspects_of_deleted_terms(
    rows: List[glossary_update.SheetRow], sheet_aspects: Dict[str, Dict[str, Dict]]
) -> Dict[str, Dict[str, Dict]]:
    """The custom aspects without those of the terms that the sheet deletes (with a warning).

    A term that is kept (not marked 'delete') keeps its aspects, also when a category with
    the same id is deleted.
    """
    kept_term_ids = {row.node_id for row in rows if not row.is_delete and row.node_type == GLOSSARY_TERM_TYPE}
    deleted_term_ids = {
        row.node_id for row in rows if row.is_delete and row.node_type in (GLOSSARY_TERM_TYPE, "")
    }
    ignored = sorted((deleted_term_ids - kept_term_ids) & set(sheet_aspects))
    for term_id in ignored:
        print(f"Warning: term '{term_id}' is marked 'delete'; its rows in the aspects sheet are ignored.")
    return {term_id: aspects for term_id, aspects in sheet_aspects.items() if term_id not in ignored}


def run(args: argparse.Namespace) -> int:
    project_id, location_id, glossary_id = argument_parser.extract_glossary_info_from_url(args.glossary_url)
    glossary = glossary_update.GlossaryRef(project_id, location_id, glossary_id)
    api_layer.set_write_rate_limit(args.max_qpm)
    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    results_path = os.path.join(args.output_dir, f"glossary-update-{glossary_id}-{timestamp}.csv")

    print(f"Reading the sheet for glossary {glossary.glossary_name}...")
    creds, _ = default(scopes=[
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ])
    processor = UpdateSheetProcessor(
        args.spreadsheet_url, f"dp-glossaries/{glossary.glossary_name}", creds
    )
    spreadsheet = gspread.authorize(creds).open_by_url(args.spreadsheet_url)
    values = open_glossary_worksheet(spreadsheet, args.spreadsheet_url).get_all_values()

    rows, errors = glossary_update.parse_sheet(values, make_row_validator(processor))
    if errors:
        _print_problems("The glossary sheet has errors; nothing was changed:", errors)
        return 1

    term_ids, category_ids = aspect_sheet_ids(rows)
    aspects_valid, sheet_aspects = processor.read_aspects(spreadsheet, term_ids, category_ids)
    if not aspects_valid:
        print("The aspects sheet has errors; nothing was changed.")
        return 1
    sheet_aspects = drop_aspects_of_deleted_terms(rows, sheet_aspects)

    print("Reading the glossary...")
    dataplex_service = api_layer.get_dataplex_service()
    glossary.project_number = glossary_update.resolve_project_number(dataplex_service, glossary)
    live_nodes = glossary_update.fetch_live_nodes(dataplex_service, glossary)
    entry_keys = glossary_update.keys_needing_entries(rows, live_nodes)
    if entry_keys:
        print(f"Reading the overview, contacts and aspects of {len(entry_keys)} existing term(s)/categor(ies)...")
    live_entries = glossary_update.fetch_entries(glossary, entry_keys, max_workers=MAX_WORKERS)
    pending = glossary_update.recently_created_without_entry(live_nodes, live_entries)
    if pending:
        print(f"Waiting for Dataplex to create the catalog entries of {len(pending)} recently created "
              f"term(s)/categor(ies)...")
        glossary_update.wait_for_entries(glossary, pending, live_entries)

    plan = glossary_update.build_plan(
        rows, live_nodes, sheet_aspects, live_entries,
        same_project=lambda project, other: api_layer.is_same_project(project, other, user_project=project_id),
    )
    for warning in plan.warnings:
        logger.warning(warning)
    if plan.errors:
        _print_problems("The sheet can't be applied to the glossary; nothing was changed:", plan.errors)
        return 1

    print(f"\nChanges for glossary {glossary.glossary_name}:")
    for line in glossary_update.format_plan(plan):
        print(f"  {line}")

    planned_results = [glossary_update.ChangeResult(change, "") for change in plan.changes]
    if not plan.has_changes:
        print("\nThe glossary already matches the sheet; nothing to change.")
        return 0
    warning = delete_warning(plan)
    if warning:
        print(f"\n{warning}")
    if args.dry_run:
        glossary_update.write_results_csv(results_path, planned_results, dry_run=True)
        print(f"\nDry run: nothing was changed. Planned changes written to {results_path}")
        return 0
    if not args.yes and not confirm(plan):
        print("Cancelled; nothing was changed.")
        return 1

    print("\nApplying the changes...")
    results = glossary_update.apply_plan(
        plan, glossary, live_nodes,
        max_workers=MAX_WORKERS,
        check_conflicts=not args.skip_conflict_check,
    )
    glossary_update.write_results_csv(results_path, results)
    return _report(results, results_path)


def _print_problems(title: str, problems: List[str]) -> None:
    logger.error(title)
    for problem in problems:
        logger.error(f"  - {problem}")


def _report(results: List[glossary_update.ChangeResult], results_path: str) -> int:
    counts: Dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    problems = [
        result for result in results
        if result.status in (glossary_update.STATUS_FAILED, glossary_update.STATUS_CONFLICT)
    ]
    for result in problems:
        logger.error(f"{result.status}: {result.change.describe()}: {result.message}")
    print("\nResult: " + ", ".join(f"{count} {status.lower()}" for status, count in sorted(counts.items())))
    print(f"Details written to {results_path}")
    if problems:
        print("Some changes were not applied. Fix the problems and run the update again: changes "
              "that were applied are now unchanged, so only the rest is applied.")
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    try:
        logging_utils.setup_file_logging()
        return run(parse_arguments(argv))
    except KeyboardInterrupt:
        logger.info("Update cancelled by user. Changes that were already applied are kept; run the "
                    "update again with the same sheet to apply the rest.")
        # os._exit, not sys.exit: worker threads may still be waiting on API calls.
        os._exit(130)
    except (gspread.exceptions.APIError, gspread.exceptions.SpreadsheetNotFound) as sheet_error:
        logger.error(f"Unable to read the spreadsheet: {sheet_error}")
        return 1
    except Exception as error:
        logger.error(f"Update stopped: {error}")
        logger.debug("Error details:", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
