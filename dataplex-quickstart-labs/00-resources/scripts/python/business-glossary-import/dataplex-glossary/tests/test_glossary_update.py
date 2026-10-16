"""
Unit tests for the glossary update tooling (import/glossary-update.py, utils/glossary_update.py).

Test coverage:
- parse_sheet: headers (optional, case-insensitive Operation column), delete rows, the full
  import's row validators, duplicate rows
- build_plan: create / update / unchanged detection (fields, overview, contacts, custom
  aspects), deletes (whole categories, missing ids, ambiguous ids), parent, cycle, depth and
  size validation
- schedule_category_operations: parents first, swapped categories through the glossary root
- apply_plan: order of the API calls, update masks, aspect writes after the entry appears,
  conflicts, failures, already-existing and already-deleted resources
- api_layer write helpers
- glossary-import.py: the Operation column (delete rows are refused)
- glossary-export.py: values are written as RAW
- glossary-update.py: dry run, confirmation and the end-to-end flow with mocked APIs
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import api_layer, glossary_update as gu


def _load_module(module_name, relative_path):
    """Load one of the hyphenated standalone scripts as a module."""
    spec = importlib.util.spec_from_file_location(module_name, str(Path(__file__).parent.parent / relative_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


glossary_update_script = _load_module("glossary_update_script", "import/glossary-update.py")
glossary_import = _load_module("glossary_import_for_update_tests", "import/glossary-import.py")
glossary_export = _load_module("glossary_export_for_update_tests", "export/glossary-export.py")

GLOSSARY = gu.GlossaryRef("my-project", "global", "my-glossary")
GLOSSARY_NAME = "projects/my-project/locations/global/glossaries/my-glossary"
HEADERS = [
    "id", "parent", "display_name", "description", "overview", "type",
    "contact1_email", "contact1_name", "contact2_email", "contact2_name",
    "label1_key", "label1_value", "label2_key", "label2_value",
]


def sheet_row(node_id, node_type="TERM", parent="", display_name=None, description="", overview="",
              contact1=("", ""), contact2=("", ""), label1=("", ""), label2=("", "")):
    return [
        node_id, parent, display_name if display_name is not None else node_id.title(), description,
        overview, node_type, contact1[0], contact1[1], contact2[0], contact2[1],
        label1[0], label1[1], label2[0], label2[1],
    ]


def real_validator():
    processor = glossary_update_script.UpdateSheetProcessor(
        "https://docs.google.com/spreadsheets/d/x", f"dp-glossaries/{GLOSSARY_NAME}", MagicMock()
    )
    return glossary_update_script.make_row_validator(processor)


def parse(rows, headers=HEADERS):
    return gu.parse_sheet([headers] + rows, real_validator())


def live(node_id, node_type="TERM", parent="", display_name=None, description="", labels=None, update_time="t1"):
    collection = "terms" if node_type == "TERM" else "categories"
    return gu.LiveNode(
        node_type=node_type, node_id=node_id, parent=parent,
        display_name=display_name if display_name is not None else node_id.title(),
        description=description, labels=labels or {},
        resource_name=f"{GLOSSARY_NAME}/{collection}/{node_id}", update_time=update_time,
    )


def live_map(*nodes):
    return {node.key: node for node in nodes}


def entry(node_type, node_id, overview="", contacts=(), custom=None, update_time="e1"):
    aspects = {}
    if overview:
        aspects["655216118709.global.overview"] = {"data": {"content": overview}}
    if contacts:
        aspects["655216118709.global.contacts"] = {
            "data": {"identities": [{"role": "steward", "id": email, "name": name} for email, name in contacts]}
        }
    aspects["655216118709.global.glossary-term-aspect"] = {"data": {}}
    for key, data in (custom or {}).items():
        aspects[key] = {"data": data}
    return {
        "name": GLOSSARY.entry_name(node_type, node_id),
        "entryType": "projects/655216118709/locations/global/entryTypes/glossary-term",
        "aspects": aspects,
        "updateTime": update_time,
    }


def plan_for(rows, live_nodes, sheet_aspects=None, entries=None):
    parsed, errors = parse(rows)
    assert errors == []
    if entries is None:
        entries = {key: entry(*key) for key in gu.keys_needing_entries(parsed, live_nodes)}
    return gu.build_plan(parsed, live_nodes, sheet_aspects or {}, entries)


def change_for(plan, node_id, node_type="TERM"):
    matches = [change for change in plan.changes if change.key == (node_type, node_id)]
    assert len(matches) == 1, plan.changes
    return matches[0]


def http_error(status, status_name=""):
    resp = MagicMock()
    resp.status = status
    resp.reason = "error"
    body = {"error": {"message": "error"}}
    if status_name:
        body["error"]["status"] = status_name
    return HttpError(resp, json.dumps(body).encode())


def plan_with_operations(rows, live_nodes):
    """Plan a sheet that has the Operation column (rows already include its value)."""
    parsed, errors = gu.parse_sheet([HEADERS + ["Operation"]] + rows, real_validator())
    assert errors == []
    entries = {key: entry(*key) for key in gu.keys_needing_entries(parsed, live_nodes)}
    return gu.build_plan(parsed, live_nodes, {}, entries)


def upsert_row(*args, **kwargs):
    return sheet_row(*args, **kwargs) + [""]


def delete_row(node_id, node_type=""):
    return [node_id] + [""] * 4 + [node_type] + [""] * 8 + ["delete"]


# ============================================================================
# Project number in entry names
# ============================================================================

class TestProjectNumber:

    def test_entry_name_uses_the_project_number(self):
        glossary = gu.GlossaryRef("my-project", "global", "g", project_number="123")
        assert glossary.entry_name("TERM", "t") == (
            "projects/123/locations/global/entryGroups/@dataplex/entries/"
            "projects/123/locations/global/glossaries/g/terms/t"
        )
        assert glossary.entry_name("CATEGORY", "c").endswith("/projects/123/locations/global/glossaries/g/categories/c")
        assert glossary.resource_name("TERM", "t") == "projects/my-project/locations/global/glossaries/g/terms/t"

    def test_number_in_the_url_is_used_as_is(self, monkeypatch):
        monkeypatch.setattr(api_layer, "get_glossary", lambda *args: pytest.fail("no API call expected"))
        assert gu.resolve_project_number(MagicMock(), gu.GlossaryRef("123", "global", "g")) == "123"

    def test_number_comes_from_the_glossary_name(self, monkeypatch):
        monkeypatch.setattr(api_layer, "get_glossary",
                            lambda service, name: {"name": "projects/456/locations/global/glossaries/g"})
        monkeypatch.setattr(api_layer, "get_project_number", lambda *args: pytest.fail("not needed"))
        assert gu.resolve_project_number(MagicMock(), gu.GlossaryRef("my-project", "global", "g")) == "456"

    def test_falls_back_to_resource_manager(self, monkeypatch):
        monkeypatch.setattr(api_layer, "get_glossary",
                            lambda service, name: {"name": "projects/my-project/locations/global/glossaries/g"})
        monkeypatch.setattr(api_layer, "get_project_number", lambda project, user_project: "789")
        assert gu.resolve_project_number(MagicMock(), gu.GlossaryRef("my-project", "global", "g")) == "789"

    def test_unknown_number_is_an_error(self, monkeypatch):
        monkeypatch.setattr(api_layer, "get_glossary",
                            lambda service, name: {"name": "projects/my-project/locations/global/glossaries/g"})

        def denied(project, user_project):
            raise RuntimeError("permission denied")
        monkeypatch.setattr(api_layer, "get_project_number", denied)
        with pytest.raises(ValueError, match="Use the project number in --glossary-url"):
            gu.resolve_project_number(MagicMock(), gu.GlossaryRef("my-project", "global", "g"))


# ============================================================================
# parse_sheet
# ============================================================================

class TestParseSheet:

    def test_parses_rows_with_contacts_and_labels(self):
        rows, errors = parse([sheet_row("revenue", parent="finance", description=" Net ", overview="<p>x</p>",
                                        contact1=("a@x.com", "Ann"), label1=("tier", "gold"))])
        assert errors == []
        row = rows[0]
        assert (row.node_type, row.node_id, row.parent) == ("TERM", "revenue", "finance")
        assert row.description == "Net"  # trimmed, as in the full import
        assert row.contacts == [("a@x.com", "Ann")]
        assert row.labels == {"tier": "gold"}
        assert row.operation == ""

    def test_operation_column_is_optional_and_case_insensitive(self):
        rows, errors = parse([sheet_row("revenue") + [""], sheet_row("cost") + ["DELETE"]], HEADERS + ["Operation"])
        assert errors == []
        assert [(row.node_id, row.operation) for row in rows] == [("revenue", ""), ("cost", "delete")]

    def test_delete_row_needs_only_an_id(self):
        rows, errors = parse([["old-term"] + [""] * 13 + ["delete"]], HEADERS + ["operation"])
        assert errors == []
        assert rows[0].is_delete and rows[0].node_type == ""

    def test_delete_row_without_id_is_an_error(self):
        _, errors = parse([[""] * 5 + ["TERM"] + [""] * 8 + ["delete"]], HEADERS + ["Operation"])
        assert "needs the 'id'" in errors[0]

    def test_invalid_operation_value(self):
        _, errors = parse([sheet_row("revenue") + ["update"]], HEADERS + ["Operation"])
        assert "Invalid 'Operation' value 'update'" in errors[0]

    def test_uses_the_full_import_validators(self):
        _, errors = parse([sheet_row("Bad Id"), sheet_row("ok", contact1=("not-an-email", "N"))])
        assert len(errors) == 2
        assert errors[0].startswith("Row 2:") and "Invalid 'id' format" in errors[0]
        assert errors[1].startswith("Row 3:") and "contact1_email" in errors[1]

    def test_missing_and_unknown_headers(self):
        _, errors = parse([sheet_row("x")], [h for h in HEADERS if h != "overview"] + ["notes"])
        assert "missing column(s): overview" in errors[0] and "unknown column(s): notes" in errors[0]

    def test_duplicate_rows_are_errors(self):
        _, errors = parse([sheet_row("revenue"), sheet_row("revenue")])
        assert "also in row 2" in errors[0]

    def test_same_id_for_a_term_and_a_category_is_allowed(self):
        rows, errors = parse([sheet_row("finance"), sheet_row("finance", node_type="CATEGORY")])
        assert errors == [] and len(rows) == 2

    def test_upsert_and_delete_of_the_same_id_is_an_error(self):
        _, errors = parse([sheet_row("revenue") + [""], ["revenue"] + [""] * 13 + ["delete"]], HEADERS + ["Operation"])
        assert "also in row 2" in errors[0]

    def test_blank_rows_are_ignored(self):
        rows, errors = parse([sheet_row("revenue"), [""] * 14, []])
        assert errors == [] and len(rows) == 1

    def test_empty_sheet(self):
        assert gu.parse_sheet([], real_validator()) == ([], ["The glossary sheet is empty."])


# ============================================================================
# build_plan
# ============================================================================

class TestBuildPlan:

    def test_new_row_is_created_with_its_aspects(self):
        plan = plan_for([sheet_row("margin", parent="finance", overview="o", contact1=("b@x.com", "Bo"))],
                        live_map(live("finance", "CATEGORY")),
                        sheet_aspects={"margin": {"projects/my-project/locations/global/aspectTypes/gov": {"tier": "1"}}})
        change = change_for(plan, "margin")
        assert change.action == gu.ACTION_CREATE
        assert set(change.aspects) == {
            "dataplex-types.global.overview", "dataplex-types.global.contacts", "my-project.global.gov"
        }
        assert change.aspects["dataplex-types.global.contacts"]["data"]["identities"] == [
            {"role": "steward", "name": "Bo", "id": "b@x.com"}
        ]

    def test_identical_row_is_unchanged(self):
        plan = plan_for([sheet_row("revenue", description="Net")], live_map(live("revenue", description="Net ")))
        assert change_for(plan, "revenue").action == gu.ACTION_UNCHANGED
        assert not plan.has_changes

    def test_changed_fields_are_detected(self):
        plan = plan_for(
            [sheet_row("revenue", parent="finance", display_name="Revenue (net)", label1=("tier", "gold"))],
            live_map(live("revenue"), live("finance", "CATEGORY")),
        )
        change = change_for(plan, "revenue")
        assert change.action == gu.ACTION_UPDATE
        assert change.fields == ["display_name", "labels", "parent"]
        assert change.aspects == {}

    def test_blank_display_name_matches_the_id(self):
        plan = plan_for([sheet_row("revenue", display_name="")], live_map(live("revenue", display_name="revenue")))
        assert change_for(plan, "revenue").action == gu.ACTION_UNCHANGED

    def test_overview_and_contacts_changes(self):
        entries = {("TERM", "revenue"): entry("TERM", "revenue", overview="old", contacts=[("a@x.com", "Ann")])}
        plan = plan_for([sheet_row("revenue", overview="new")], live_map(live("revenue")), entries=entries)
        change = change_for(plan, "revenue")
        assert change.action == gu.ACTION_UPDATE and change.fields == []
        assert change.aspects["dataplex-types.global.overview"] == {"data": {"content": "new"}}
        # A blank contacts column clears the contacts.
        assert change.aspects["dataplex-types.global.contacts"] == {"data": {"identities": []}}
        assert change.entry_update_time == "e1"

    def test_custom_aspects_are_merged_field_by_field(self):
        live_custom = {"123.global.gov": {"tier": "1", "owner": {"email": "a@x.com", "team": "fin"}}}
        entries = {("TERM", "revenue"): entry("TERM", "revenue", custom=live_custom)}
        sheet_aspects = {"revenue": {"projects/123/locations/global/aspectTypes/gov": {"owner": {"email": "b@x.com"}}}}
        plan = plan_for([sheet_row("revenue")], live_map(live("revenue")), sheet_aspects, entries)
        change = change_for(plan, "revenue")
        assert change.aspects == {
            "123.global.gov": {"data": {"tier": "1", "owner": {"email": "b@x.com", "team": "fin"}}}
        }

    def test_matching_custom_aspect_is_unchanged(self):
        entries = {("TERM", "revenue"): entry("TERM", "revenue", custom={"123.global.gov": {"tier": "1"}})}
        sheet_aspects = {"revenue": {"projects/123/locations/global/aspectTypes/gov": {"tier": "1"}}}
        plan = plan_for([sheet_row("revenue")], live_map(live("revenue")), sheet_aspects, entries)
        assert change_for(plan, "revenue").action == gu.ACTION_UNCHANGED

    def test_custom_aspect_matched_by_project_number(self):
        entries = {("TERM", "revenue"): entry("TERM", "revenue", custom={"123.global.gov": {"tier": "1"}})}
        sheet_aspects = {"revenue": {"projects/my-project/locations/global/aspectTypes/gov": {"tier": "2"}}}
        parsed, _ = parse([sheet_row("revenue")])
        plan = gu.build_plan(parsed, live_map(live("revenue")), sheet_aspects, entries,
                             same_project=lambda a, b: {a, b} == {"123", "my-project"} or a == b)
        assert change_for(plan, "revenue").aspects == {"123.global.gov": {"data": {"tier": "2"}}}

    def test_unreadable_entry_is_an_error(self):
        plan = plan_for([sheet_row("revenue")], live_map(live("revenue")), entries={("TERM", "revenue"): None})
        assert "could not read the catalog entry" in plan.errors[0]

    def test_terms_not_in_the_sheet_are_untouched(self):
        plan = plan_for([sheet_row("revenue")], live_map(live("revenue"), live("cost"), live("finance", "CATEGORY")))
        assert [change.node_id for change in plan.changes] == ["revenue"]

    def test_missing_parent_is_an_error(self):
        plan = plan_for([sheet_row("margin", parent="nope")], {})
        assert "parent category 'nope' is neither in the sheet nor in the glossary" in plan.errors[0]

    def test_parent_created_in_the_same_sheet(self):
        plan = plan_for([sheet_row("margin", parent="new-cat"), sheet_row("new-cat", node_type="CATEGORY")], {})
        assert plan.errors == []

    def test_cycle_is_an_error(self):
        nodes = live_map(live("a", "CATEGORY"), live("b", "CATEGORY", parent="a"))
        plan = plan_for([sheet_row("a", node_type="CATEGORY", parent="b")], nodes)
        assert any("cycle" in error for error in plan.errors)

    def test_term_can_be_in_a_third_level_category(self):
        nodes = live_map(live("c1", "CATEGORY"), live("c2", "CATEGORY", parent="c1"), live("c3", "CATEGORY", parent="c2"))
        plan = plan_for([sheet_row("deep", parent="c3")], nodes)
        assert plan.errors == []

    def test_categories_are_nested_at_most_three_deep(self):
        nodes = live_map(live("c1", "CATEGORY"), live("c2", "CATEGORY", parent="c1"), live("c3", "CATEGORY", parent="c2"))
        plan = plan_for([sheet_row("c4", node_type="CATEGORY", parent="c3")], nodes)
        assert "category 'c4' would be 4 levels below the glossary" in plan.errors[0]

    def test_moving_a_category_checks_everything_under_it(self):
        nodes = live_map(live("c1", "CATEGORY"), live("c2", "CATEGORY", parent="c1"),
                         live("x", "CATEGORY"), live("y", "CATEGORY", parent="x"), live("t", parent="y"))
        plan = plan_for([sheet_row("x", node_type="CATEGORY", parent="c2")], nodes)
        assert any("category 'y' would be 4 levels below the glossary" in error for error in plan.errors)
        assert any("term 't' would be 5 levels below the glossary" in error for error in plan.errors)

    def test_moving_a_category_with_terms_to_the_third_level_is_allowed(self):
        nodes = live_map(live("c1", "CATEGORY"), live("c2", "CATEGORY", parent="c1"),
                         live("x", "CATEGORY"), live("t", parent="x"))
        plan = plan_for([sheet_row("x", node_type="CATEGORY", parent="c2")], nodes)
        assert plan.errors == []

    def test_untouched_deep_nodes_are_not_reported(self):
        nodes = live_map(live("c1", "CATEGORY"), live("c2", "CATEGORY", parent="c1"),
                         live("c3", "CATEGORY", parent="c2"), live("deep", parent="c3"), live("revenue"))
        plan = plan_for([sheet_row("revenue", description="changed")], nodes)
        assert plan.errors == []

    def test_deep_node_that_stays_in_place_is_not_reported(self):
        # A full sheet of a glossary that is deeper than the full import allows.
        nodes = live_map(live("c1", "CATEGORY"), live("c2", "CATEGORY", parent="c1"),
                         live("c3", "CATEGORY", parent="c2"), live("deep", parent="c3"))
        plan = plan_for([sheet_row("c1", "CATEGORY"), sheet_row("c2", "CATEGORY", parent="c1"),
                         sheet_row("c3", "CATEGORY", parent="c2"),
                         sheet_row("deep", parent="c3", description="changed")], nodes)
        assert plan.errors == []
        assert change_for(plan, "deep").fields == ["description"]

    def test_size_limit(self, monkeypatch):
        monkeypatch.setattr(gu, "GLOSSARY_MAX_TERMS", 2)
        plan = plan_for([sheet_row("t3")], live_map(live("t1"), live("t2")))
        assert "would have 3 terms" in plan.errors[0]


class TestPlanDeletes:

    def _rows(self, *rows):
        return [list(row) + [op] for row, op in rows]

    def _plan(self, rows, nodes):
        parsed, errors = gu.parse_sheet([HEADERS + ["Operation"]] + rows, real_validator())
        assert errors == []
        entries = {key: entry(*key) for key in gu.keys_needing_entries(parsed, nodes)}
        return gu.build_plan(parsed, nodes, {}, entries)

    def test_delete_term(self):
        plan = self._plan([["revenue"] + [""] * 13 + ["delete"]], live_map(live("revenue"), live("cost")))
        assert [(c.action, c.node_id) for c in plan.changes] == [("DELETE", "revenue")]

    def test_deleting_a_category_deletes_everything_inside_it(self):
        nodes = live_map(live("fin", "CATEGORY"), live("sub", "CATEGORY", parent="fin"),
                         live("t1", parent="fin"), live("t2", parent="sub"), live("other"))
        plan = self._plan([["fin"] + [""] * 4 + ["CATEGORY"] + [""] * 8 + ["delete"]], nodes)
        deleted = {(c.node_type, c.node_id): c.reason for c in plan.by_action(gu.ACTION_DELETE)}
        assert deleted == {
            ("CATEGORY", "fin"): "",
            ("CATEGORY", "sub"): "inside category 'fin'",
            ("TERM", "t1"): "inside category 'fin'",
            ("TERM", "t2"): "inside category 'fin'",
        }

    def test_missing_id_is_skipped_with_a_warning(self):
        plan = self._plan([["gone"] + [""] * 13 + ["delete"]], {})
        assert plan.errors == [] and "nothing to delete" in plan.warnings[0]
        assert plan.changes[0].action == gu.ACTION_SKIP

    def test_ambiguous_id_needs_a_type(self):
        nodes = live_map(live("fin", "CATEGORY"), live("fin"))
        plan = self._plan([["fin"] + [""] * 13 + ["delete"]], nodes)
        assert "Set 'type'" in plan.errors[0]

    def test_updating_something_inside_a_deleted_category_is_an_error(self):
        nodes = live_map(live("fin", "CATEGORY"), live("t1", parent="fin"))
        plan = self._plan([
            ["fin"] + [""] * 4 + ["CATEGORY"] + [""] * 8 + ["delete"],
            sheet_row("t1", parent="fin", description="x") + [""],
        ], nodes)
        assert any("deleted by this sheet" in error for error in plan.errors)

    def test_new_row_under_a_deleted_category_is_an_error(self):
        nodes = live_map(live("fin", "CATEGORY"))
        plan = self._plan([
            ["fin"] + [""] * 4 + ["CATEGORY"] + [""] * 8 + ["delete"],
            sheet_row("new", parent="fin") + [""],
        ], nodes)
        assert "parent category 'fin' is deleted by this sheet" in plan.errors[0]


class TestDeletesAfterMoves:

    def test_moving_a_term_out_of_a_deleted_category_keeps_it(self):
        nodes = live_map(live("old", "CATEGORY"), live("new", "CATEGORY"),
                         live("t", parent="old"), live("t2", parent="old"))
        plan = plan_with_operations([upsert_row("t", parent="new"), delete_row("old", "CATEGORY")], nodes)
        assert plan.errors == []
        assert {(c.action, c.node_id) for c in plan.changes} == {
            ("UPDATE", "t"), ("DELETE", "old"), ("DELETE", "t2"),
        }

    def test_moving_a_category_out_keeps_everything_in_it(self):
        nodes = live_map(live("old", "CATEGORY"), live("keep", "CATEGORY", parent="old"),
                         live("k1", parent="keep"), live("gone", parent="old"))
        plan = plan_with_operations([upsert_row("keep", node_type="CATEGORY"), delete_row("old", "CATEGORY")], nodes)
        assert plan.errors == []
        assert {c.node_id for c in plan.by_action(gu.ACTION_DELETE)} == {"old", "gone"}

    def test_row_left_inside_a_deleted_category_is_an_error(self):
        nodes = live_map(live("fin", "CATEGORY"), live("t1", parent="fin"))
        plan = plan_with_operations([delete_row("fin", "CATEGORY"), upsert_row("t1", parent="fin")], nodes)
        assert "parent category 'fin' is deleted by this sheet" in plan.errors[0]

    def test_deleted_category_does_not_block_a_move(self):
        # a > d would be 4 levels deep under c > b, but d is deleted first.
        nodes = live_map(live("c", "CATEGORY"), live("b", "CATEGORY", parent="c"),
                         live("a", "CATEGORY"), live("d", "CATEGORY", parent="a"))
        plan = plan_with_operations([upsert_row("a", node_type="CATEGORY", parent="b"), delete_row("d", "CATEGORY")],
                                    nodes)
        assert plan.errors == []

    def test_split_category_deletes(self):
        nodes = live_map(live("old", "CATEGORY"), live("sub", "CATEGORY", parent="old"), live("t", parent="old"),
                         live("other", "CATEGORY"))
        plan = plan_with_operations([upsert_row("t"), delete_row("old", "CATEGORY"), delete_row("other", "CATEGORY")],
                                    nodes)
        early, late = gu.split_category_deletes(plan.by_action(gu.ACTION_DELETE), nodes)
        assert [c.node_id for c in early] == ["sub", "other"]
        assert [c.node_id for c in late] == ["old"]


# ============================================================================
# schedule_category_operations
# ============================================================================

class TestScheduleCategories:

    def test_parents_are_created_first(self):
        plan = plan_for([sheet_row("child", node_type="CATEGORY", parent="top"),
                         sheet_row("top", node_type="CATEGORY")], {})
        steps = gu.schedule_category_operations(plan.changes, {})
        assert [(step.change.node_id, step.parent) for step in steps] == [("top", ""), ("child", "top")]

    def test_swapping_categories_goes_through_the_root(self):
        nodes = live_map(live("a", "CATEGORY"), live("b", "CATEGORY", parent="a"))
        plan = plan_for([sheet_row("a", node_type="CATEGORY", parent="b"),
                         sheet_row("b", node_type="CATEGORY", parent="")], nodes)
        assert plan.errors == []
        steps = gu.schedule_category_operations(plan.changes, nodes)
        assert [(step.change.node_id, step.parent, step.final) for step in steps] == [
            ("b", "", True), ("a", "b", True)
        ]

    def test_deadlock_is_broken_by_parking_a_category_at_the_root(self, monkeypatch):
        # Model two moves that each need the other one first: x can only go under z once z is
        # under p, and z can only go under p once x has left q.
        def valid(tree):
            return not (tree["x"] == "z" and tree["z"] != "p") and not (tree["z"] == "p" and tree["x"] == "q")
        monkeypatch.setattr(gu, "_category_tree_is_valid", valid)
        nodes = live_map(live("p", "CATEGORY"), live("q", "CATEGORY", parent="p"),
                         live("x", "CATEGORY", parent="q"), live("z", "CATEGORY"))
        plan = plan_for([sheet_row("x", node_type="CATEGORY", parent="z"),
                         sheet_row("z", node_type="CATEGORY", parent="p")], nodes)
        assert plan.errors == []
        steps = gu.schedule_category_operations(plan.changes, nodes)
        assert [(step.change.node_id, step.parent, step.final) for step in steps] == [
            ("x", "", False), ("z", "p", True), ("x", "z", True)
        ]

    def test_unresolvable_order_is_an_error(self, monkeypatch):
        monkeypatch.setattr(gu, "_category_tree_is_valid", lambda tree: tree.get("x") != "z")
        nodes = live_map(live("x", "CATEGORY"), live("z", "CATEGORY"))
        plan = plan_for([sheet_row("x", node_type="CATEGORY", parent="z")], nodes)
        assert "Could not find a valid order" in plan.errors[0]  # reported by build_plan (and so by --dry-run)
        with pytest.raises(ValueError, match="Could not find a valid order"):
            gu.schedule_category_operations(plan.changes, nodes)


# ============================================================================
# apply_plan
# ============================================================================

class FakeApi:
    """Records the api_layer write calls made by apply_plan."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.entries_after_polls = 0
        self.fail = {}
        self.current_update_time = {}
        self.resources = {}  # node id -> resource returned by get (besides updateTime)
        monkeypatch.setattr(api_layer, "create_glossary_resource", self.create)
        monkeypatch.setattr(api_layer, "update_glossary_resource", self.update)
        monkeypatch.setattr(api_layer, "delete_glossary_resource", self.delete)
        monkeypatch.setattr(api_layer, "update_entry_aspects", self.update_aspects)
        monkeypatch.setattr(api_layer, "get_glossary_resource", self.get)
        monkeypatch.setattr(api_layer, "lookup_entry", self.lookup)
        self.polls = 0

    def _maybe_fail(self, key):
        if key in self.fail:
            raise self.fail[key]

    def create(self, service, glossary_name, node_type, node_id, body):
        self.calls.append(("create", node_type, node_id, body["parent"]))
        self._maybe_fail(("create", node_id))
        return {}

    def update(self, service, name, node_type, body, mask):
        node_id = name.rsplit("/", 1)[-1]
        self.calls.append(("update", node_type, node_id, tuple(mask), body["parent"]))
        self._maybe_fail(("update", node_id))
        return {"updateTime": "t2"}

    def delete(self, service, name, node_type):
        node_id = name.rsplit("/", 1)[-1]
        self.calls.append(("delete", node_type, node_id))
        self._maybe_fail(("delete", node_id))

    def update_aspects(self, service, entry_name, aspects, entry_type):
        self.calls.append(("aspects", entry_name.rsplit("/", 1)[-1], tuple(sorted(aspects))))

    def get(self, service, name, node_type):
        node_id = name.rsplit("/", 1)[-1]
        return {**self.resources.get(node_id, {}), "updateTime": self.current_update_time.get(node_id, "t1")}

    def lookup(self, service, entry_name, project_location):
        self.polls += 1
        if self.polls <= self.entries_after_polls:
            return None
        node_id = entry_name.rsplit("/", 1)[-1]
        return {"name": entry_name, "entryType": "t", "updateTime": "e1", "id": node_id}


def apply(plan, nodes, **kwargs):
    kwargs.setdefault("max_workers", 1)
    return gu.apply_plan(plan, GLOSSARY, nodes, service_factory=lambda: MagicMock(), sleep=lambda s: None, **kwargs)


class TestApplyPlan:

    def test_order_and_bodies(self, monkeypatch):
        api = FakeApi(monkeypatch)
        nodes = live_map(live("fin", "CATEGORY"), live("revenue", parent="fin"), live("old", "CATEGORY"),
                         live("old-term", parent="old"))
        parsed, _ = gu.parse_sheet([HEADERS + ["Operation"],
                                    sheet_row("emea", node_type="CATEGORY", parent="fin") + [""],
                                    sheet_row("margin", parent="emea", overview="o") + [""],
                                    sheet_row("revenue", parent="fin", description="new") + [""],
                                    ["old"] + [""] * 4 + ["CATEGORY"] + [""] * 8 + ["delete"]], real_validator())
        entries = {key: entry(*key) for key in gu.keys_needing_entries(parsed, nodes)}
        plan = gu.build_plan(parsed, nodes, {}, entries)
        assert plan.errors == []

        results = apply(plan, nodes)

        assert [call[:3] for call in api.calls] == [
            ("delete", "TERM", "old-term"),
            ("delete", "CATEGORY", "old"),
            ("create", "CATEGORY", "emea"),
            ("create", "TERM", "margin"),
            ("update", "TERM", "revenue"),
            ("aspects", "margin", ("dataplex-types.global.overview",)),
        ]
        assert api.calls[2][3] == f"{GLOSSARY_NAME}/categories/fin"
        assert api.calls[3][3] == f"{GLOSSARY_NAME}/categories/emea"
        assert api.calls[4][3:] == (("description",), f"{GLOSSARY_NAME}/categories/fin")
        assert {result.status for result in results} == {gu.STATUS_DONE}

    def test_waits_for_the_entry_of_a_new_term(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.entries_after_polls = 2
        plan = plan_for([sheet_row("margin", overview="o")], {})
        results = apply(plan, {})
        assert api.polls == 3
        assert results[0].status == gu.STATUS_DONE

    def test_entry_that_never_appears(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.entries_after_polls = 10 ** 6
        plan = plan_for([sheet_row("margin", overview="o")], {})
        results = apply(plan, {}, entry_wait_timeout=0)
        assert results[0].status == gu.STATUS_FAILED and "Run the update again" in results[0].message

    def test_conflict_skips_the_change(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.current_update_time["revenue"] = "changed-by-someone"
        plan = plan_for([sheet_row("revenue", description="new")], live_map(live("revenue")))
        results = apply(plan, live_map(live("revenue")))
        assert results[0].status == gu.STATUS_CONFLICT
        assert not any(call[0] == "update" for call in api.calls)

    def test_conflict_check_can_be_disabled(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.current_update_time["revenue"] = "changed-by-someone"
        plan = plan_for([sheet_row("revenue", description="new")], live_map(live("revenue")))
        results = apply(plan, live_map(live("revenue")), check_conflicts=False)
        assert results[0].status == gu.STATUS_DONE

    def test_aspect_only_update_checks_the_entry(self, monkeypatch):
        api = FakeApi(monkeypatch)
        entries = {("TERM", "revenue"): entry("TERM", "revenue", overview="old", update_time="e0")}
        plan = plan_for([sheet_row("revenue", overview="new")], live_map(live("revenue")), entries=entries)
        results = apply(plan, live_map(live("revenue")))
        assert results[0].status == gu.STATUS_CONFLICT  # FakeApi returns updateTime e1, plan read e0

    def test_failure_does_not_stop_other_changes(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.fail[("create", "a")] = http_error(400)
        plan = plan_for([sheet_row("a"), sheet_row("b")], {})
        results = apply(plan, {})
        assert [(r.change.node_id, r.status) for r in results] == [("a", "FAILED"), ("b", "DONE")]
        assert results[0].message.startswith("HTTP 400")

    def test_create_that_already_exists_with_the_sheet_values_is_done(self, monkeypatch):
        # An earlier attempt of the create succeeded, but its response was lost.
        api = FakeApi(monkeypatch)
        api.fail[("create", "a")] = http_error(409, "ALREADY_EXISTS")
        api.resources["a"] = {"displayName": "A", "description": "", "parent": GLOSSARY_NAME}
        results = apply(plan_for([sheet_row("a")], {}), {})
        assert results[0].status == gu.STATUS_DONE
        assert not any(call[0] == "update" for call in api.calls)

    def test_create_that_already_exists_with_other_values_is_a_conflict(self, monkeypatch):
        # Someone else created it since the glossary was read.
        api = FakeApi(monkeypatch)
        api.fail[("create", "a")] = http_error(409, "ALREADY_EXISTS")
        api.resources["a"] = {"displayName": "Someone else's", "parent": GLOSSARY_NAME}
        results = apply(plan_for([sheet_row("a", overview="o")], {}), {})
        assert results[0].status == gu.STATUS_CONFLICT
        assert not any(call[0] in ("update", "aspects") for call in api.calls)

    def test_create_that_already_exists_is_updated_without_the_conflict_check(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.fail[("create", "a")] = http_error(409, "ALREADY_EXISTS")
        results = apply(plan_for([sheet_row("a")], {}), {}, check_conflicts=False)
        assert results[0].status == gu.STATUS_DONE
        assert api.calls[1][:3] == ("update", "TERM", "a")

    def test_aborted_create_is_a_failure_not_an_existing_term(self, monkeypatch):
        # HTTP 409 also means ABORTED; the write helpers retry it, so one that gets here failed.
        api = FakeApi(monkeypatch)
        api.fail[("create", "a")] = http_error(409, "ABORTED")
        results = apply(plan_for([sheet_row("a")], {}), {})
        assert results[0].status == gu.STATUS_FAILED
        assert [call[0] for call in api.calls] == ["create"]

    def test_category_is_kept_when_something_inside_failed(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.fail[("delete", "t1")] = http_error(500)
        nodes = live_map(live("fin", "CATEGORY"), live("t1", parent="fin"))
        parsed, _ = gu.parse_sheet([HEADERS + ["Operation"], ["fin"] + [""] * 4 + ["CATEGORY"] + [""] * 8 + ["delete"]],
                                   real_validator())
        results = apply(gu.build_plan(parsed, nodes, {}, {}), nodes)
        statuses = {r.change.node_id: r.status for r in results}
        assert statuses == {"fin": gu.STATUS_SKIPPED, "t1": gu.STATUS_FAILED}
        assert ("delete", "CATEGORY", "fin") not in api.calls

    def test_already_deleted_is_skipped(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.fail[("delete", "t1")] = http_error(404)
        nodes = live_map(live("t1"))
        parsed, _ = gu.parse_sheet([HEADERS + ["Operation"], ["t1"] + [""] * 13 + ["delete"]], real_validator())
        results = apply(gu.build_plan(parsed, nodes, {}, {}), nodes)
        assert results[0].status == gu.STATUS_SKIPPED

    def test_two_step_move(self, monkeypatch):
        api = FakeApi(monkeypatch)
        nodes = live_map(live("a", "CATEGORY"), live("b", "CATEGORY", parent="a"))
        plan = plan_for([sheet_row("a", node_type="CATEGORY", parent="b", description="d"),
                         sheet_row("b", node_type="CATEGORY", parent="")], nodes)
        apply(plan, nodes)
        updates = [call for call in api.calls if call[0] == "update"]
        assert updates == [
            ("update", "CATEGORY", "b", ("parent",), GLOSSARY_NAME),
            ("update", "CATEGORY", "a", ("description", "parent"), f"{GLOSSARY_NAME}/categories/b"),
        ]

    def test_deletes_free_room_before_the_moves(self, monkeypatch):
        api = FakeApi(monkeypatch)
        nodes = live_map(live("c", "CATEGORY"), live("b", "CATEGORY", parent="c"),
                         live("a", "CATEGORY"), live("d", "CATEGORY", parent="a"))
        plan = plan_with_operations([upsert_row("a", node_type="CATEGORY", parent="b"), delete_row("d", "CATEGORY")],
                                    nodes)
        results = apply(plan, nodes)
        assert [call[:3] for call in api.calls] == [("delete", "CATEGORY", "d"), ("update", "CATEGORY", "a")]
        assert {result.status for result in results} == {gu.STATUS_DONE}

    def test_terms_are_deleted_before_new_terms_are_created(self, monkeypatch):
        api = FakeApi(monkeypatch)
        nodes = live_map(live("old"))
        apply(plan_with_operations([upsert_row("new"), delete_row("old", "TERM")], nodes), nodes)
        assert [call[:3] for call in api.calls] == [("delete", "TERM", "old"), ("create", "TERM", "new")]

    def test_category_is_deleted_after_moving_something_out_of_it(self, monkeypatch):
        api = FakeApi(monkeypatch)
        nodes = live_map(live("old", "CATEGORY"), live("new", "CATEGORY"),
                         live("t", parent="old"), live("t2", parent="old"))
        plan = plan_with_operations([upsert_row("t", parent="new"), delete_row("old", "CATEGORY")], nodes)
        results = apply(plan, nodes)
        assert [call[:3] for call in api.calls] == [
            ("delete", "TERM", "t2"), ("update", "TERM", "t"), ("delete", "CATEGORY", "old"),
        ]
        assert {result.status for result in results} == {gu.STATUS_DONE}

    def test_category_is_kept_when_moving_something_out_of_it_failed(self, monkeypatch):
        api = FakeApi(monkeypatch)
        api.fail[("update", "t")] = http_error(400)
        nodes = live_map(live("old", "CATEGORY"), live("new", "CATEGORY"), live("t", parent="old"))
        plan = plan_with_operations([upsert_row("t", parent="new"), delete_row("old", "CATEGORY")], nodes)
        statuses = {r.change.node_id: (r.status, r.message) for r in apply(plan, nodes)}
        assert statuses["t"][0] == gu.STATUS_FAILED
        assert statuses["old"] == (gu.STATUS_SKIPPED, "not deleted because term 't' inside it was not deleted or moved out")
        assert ("delete", "CATEGORY", "old") not in api.calls

    def test_plan_with_errors_is_refused(self):
        plan = gu.UpdatePlan(errors=["boom"])
        with pytest.raises(ValueError):
            apply(plan, {})


# ============================================================================
# api_layer write helpers
# ============================================================================

class TestApiLayerWrites:

    def test_create_term_and_category(self, monkeypatch):
        service = MagicMock()
        api_layer.create_glossary_resource(service, GLOSSARY_NAME, "TERM", "t1", {"displayName": "T"})
        terms = service.projects.return_value.locations.return_value.glossaries.return_value.terms.return_value
        terms.create.assert_called_once_with(parent=GLOSSARY_NAME, termId="t1", body={"displayName": "T"})
        api_layer.create_glossary_resource(service, GLOSSARY_NAME, "CATEGORY", "c1", {})
        categories = service.projects.return_value.locations.return_value.glossaries.return_value.categories.return_value
        categories.create.assert_called_once_with(parent=GLOSSARY_NAME, categoryId="c1", body={})

    def test_update_uses_a_comma_separated_mask(self):
        service = MagicMock()
        api_layer.update_glossary_resource(service, "name", "TERM", {"a": 1}, ["display_name", "parent"])
        terms = service.projects.return_value.locations.return_value.glossaries.return_value.terms.return_value
        terms.patch.assert_called_once_with(name="name", updateMask="display_name,parent", body={"a": 1})

    def test_update_entry_aspects(self):
        service = MagicMock()
        aspects = {"b.global.x": {"data": {}}, "a.global.y": {"data": {}}}
        api_layer.update_entry_aspects(service, "entry", aspects, "type")
        entries = service.projects.return_value.locations.return_value.entryGroups.return_value.entries.return_value
        entries.patch.assert_called_once_with(
            name="entry", updateMask="aspects", aspectKeys=["a.global.y", "b.global.x"],
            body={"name": "entry", "aspects": aspects, "entryType": "type"},
        )

    def test_list_glossary_categories_handles_pages_and_empty_glossaries(self):
        service = MagicMock()
        categories = service.projects.return_value.locations.return_value.glossaries.return_value.categories.return_value
        first = MagicMock()
        first.execute.return_value = {"categories": [{"name": "c1"}]}
        second = MagicMock()
        second.execute.return_value = {}
        categories.list.return_value = first
        categories.list_next.side_effect = [second, None]
        assert api_layer.list_glossary_categories(service, GLOSSARY_NAME) == [{"name": "c1"}]

    def test_http_error_status_name(self):
        assert api_layer.http_error_status_name(http_error(409, "ABORTED")) == "ABORTED"
        assert api_layer.http_error_status_name(http_error(409)) == ""
        assert api_layer.http_error_status_name(ValueError("not an HTTP error")) == ""

    @pytest.mark.parametrize("status_name, attempts", [("ABORTED", 2), ("ALREADY_EXISTS", 1)])
    def test_aborted_writes_are_retried_but_already_exists_is_not(self, monkeypatch, status_name, attempts):
        from utils import retry_utils
        monkeypatch.setattr(retry_utils.time, "sleep", lambda seconds: None)
        service = MagicMock()
        terms = service.projects.return_value.locations.return_value.glossaries.return_value.terms.return_value
        terms.create.return_value.execute.side_effect = [http_error(409, status_name), {"name": "t1"}]
        if attempts == 1:
            with pytest.raises(HttpError):
                api_layer.create_glossary_resource(service, GLOSSARY_NAME, "TERM", "t1", {})
        else:
            assert api_layer.create_glossary_resource(service, GLOSSARY_NAME, "TERM", "t1", {}) == {"name": "t1"}
        assert terms.create.return_value.execute.call_count == attempts

    def test_set_write_rate_limit(self, monkeypatch):
        monkeypatch.setattr(api_layer._write_rate_limiter, "min_interval_seconds", 1)
        api_layer.set_write_rate_limit(120)
        assert api_layer._write_rate_limiter.min_interval_seconds == 0.5
        with pytest.raises(ValueError):
            api_layer.set_write_rate_limit(0)


# ============================================================================
# glossary-import.py: Operation column
# ============================================================================

class TestFullImportOperationColumn:

    def _read(self, monkeypatch, values):
        spreadsheet = MagicMock()
        spreadsheet.sheet1.get_all_values.return_value = values
        client = MagicMock()
        client.open_by_url.return_value = spreadsheet
        monkeypatch.setattr(glossary_import.gspread, "authorize", lambda creds: client)
        processor = glossary_import.SheetProcessor(
            "https://docs.google.com/spreadsheets/d/x", f"https://x/dp-glossaries/{GLOSSARY_NAME}", MagicMock()
        )
        monkeypatch.setattr(processor, "read_aspects", lambda spreadsheet, known_ids: (True, {}))
        return processor.read_and_validate_data()

    def test_blank_operation_column_is_accepted(self, monkeypatch):
        valid, entries = self._read(monkeypatch, [HEADERS + ["Operation"], sheet_row("revenue") + [""]])
        assert valid and len(entries) == 1

    def test_delete_rows_are_refused(self, monkeypatch, capsys):
        valid, entries = self._read(monkeypatch, [HEADERS + ["Operation"], sheet_row("revenue") + ["delete"]])
        assert not valid and entries == []
        assert "glossary-update.py" in capsys.readouterr().out

    def test_invalid_operation_value_is_refused(self, monkeypatch, capsys):
        valid, _ = self._read(monkeypatch, [HEADERS + ["operation"], sheet_row("revenue") + ["update"]])
        assert not valid
        assert "Invalid 'Operation' value 'update'" in capsys.readouterr().out

    def test_sheet_without_operation_column_is_unchanged(self, monkeypatch):
        valid, entries = self._read(monkeypatch, [HEADERS, sheet_row("revenue")])
        assert valid and len(entries) == 1


# ============================================================================
# glossary-export.py: RAW values
# ============================================================================

def test_export_writes_the_glossary_sheet_as_raw_values():
    service = MagicMock()
    glossary_export._write_to_sheet(service, "sheet-id", [["id"], ["007"]])
    kwargs = service.spreadsheets.return_value.values.return_value.update.call_args.kwargs
    assert kwargs["valueInputOption"] == "RAW"


# ============================================================================
# glossary-update.py
# ============================================================================

class TestGlossaryUpdateScript:

    def _setup(self, monkeypatch, values, live_nodes, answer="y"):
        spreadsheet = MagicMock()
        spreadsheet.sheet1.get_all_values.return_value = values
        client = MagicMock()
        client.open_by_url.return_value = spreadsheet
        monkeypatch.setattr(glossary_update_script.gspread, "authorize", lambda creds: client)
        monkeypatch.setattr(glossary_update_script, "default", lambda scopes: (MagicMock(), "p"))
        monkeypatch.setattr(glossary_update_script.UpdateSheetProcessor, "read_aspects",
                            lambda self, spreadsheet, known_ids: (True, {}))
        monkeypatch.setattr(api_layer, "get_dataplex_service", lambda: MagicMock())
        monkeypatch.setattr(api_layer, "get_glossary",
                            lambda service, name: {"name": "projects/123456/locations/global/glossaries/my-glossary"})
        monkeypatch.setattr(gu, "fetch_live_nodes", lambda service, glossary: live_nodes)
        monkeypatch.setattr(gu, "fetch_entries", lambda glossary, keys, max_workers=5: {k: entry(*k) for k in keys})
        monkeypatch.setattr("builtins.input", lambda prompt: answer)
        return FakeApi(monkeypatch)

    def _args(self, tmp_path, *extra):
        return glossary_update_script.parse_arguments([
            "--spreadsheet-url", "https://docs.google.com/spreadsheets/d/abc/edit",
            "--glossary-url", f"https://console.cloud.google.com/dataplex/dp-glossaries/{GLOSSARY_NAME}",
            "--output-dir", str(tmp_path), *extra,
        ])

    def test_dry_run_changes_nothing(self, monkeypatch, tmp_path):
        api = self._setup(monkeypatch, [HEADERS, sheet_row("revenue", description="new"), sheet_row("cost")],
                          live_map(live("revenue"), live("cost")))
        assert glossary_update_script.run(self._args(tmp_path, "--dry-run")) == 0
        assert api.calls == []
        results_file = next(tmp_path.glob("glossary-update-my-glossary-*.csv"))
        lines = results_file.read_text().splitlines()
        assert lines[1].startswith("2,TERM,revenue,UPDATE,PLANNED,description")
        assert lines[2].startswith("3,TERM,cost,UNCHANGED,UNCHANGED")

    def test_entries_are_named_with_the_project_number(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, [HEADERS, sheet_row("revenue")], live_map(live("revenue")))
        seen = []

        def fetch_entries(glossary, keys, max_workers=5):
            seen.append(glossary.entry_name("TERM", "revenue"))
            return {k: entry(*k) for k in keys}
        monkeypatch.setattr(gu, "fetch_entries", fetch_entries)
        glossary_update_script.run(self._args(tmp_path, "--dry-run"))
        assert seen == [
            "projects/123456/locations/global/entryGroups/@dataplex/entries/"
            "projects/123456/locations/global/glossaries/my-glossary/terms/revenue"
        ]

    def test_applies_after_confirmation(self, monkeypatch, tmp_path):
        api = self._setup(monkeypatch, [HEADERS, sheet_row("revenue", description="new")], live_map(live("revenue")))
        assert glossary_update_script.run(self._args(tmp_path)) == 0
        assert api.calls[0][:3] == ("update", "TERM", "revenue")

    def test_cancelled_by_the_user(self, monkeypatch, tmp_path):
        api = self._setup(monkeypatch, [HEADERS, sheet_row("revenue", description="new")], live_map(live("revenue")),
                          answer="n")
        assert glossary_update_script.run(self._args(tmp_path)) == 1
        assert api.calls == []

    def test_deletes_need_typing_yes(self, monkeypatch, tmp_path):
        values = [HEADERS + ["Operation"], ["revenue"] + [""] * 13 + ["delete"]]
        api = self._setup(monkeypatch, values, live_map(live("revenue")), answer="y")
        assert glossary_update_script.run(self._args(tmp_path)) == 1
        assert api.calls == []

    def test_nothing_to_change(self, monkeypatch, tmp_path):
        api = self._setup(monkeypatch, [HEADERS, sheet_row("revenue")], live_map(live("revenue")))
        assert glossary_update_script.run(self._args(tmp_path)) == 0
        assert api.calls == []

    def test_sheet_errors_stop_before_reading_the_glossary(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, [HEADERS, sheet_row("Bad Id")], {})
        monkeypatch.setattr(gu, "fetch_live_nodes", lambda *a: pytest.fail("glossary read"))
        assert glossary_update_script.run(self._args(tmp_path)) == 1

    def test_uses_the_tab_in_the_url(self):
        spreadsheet = MagicMock()
        glossary_update_script.open_glossary_worksheet(spreadsheet, "https://docs.google.com/spreadsheets/d/abc/edit#gid=42")
        spreadsheet.get_worksheet_by_id.assert_called_once_with(42)
