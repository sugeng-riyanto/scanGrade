"""The backfill must reconstruct what it can prove, and refuse to guess the rest.

It runs against rows that stand behind marks already awarded, so the two things
worth pinning are: it writes NOTHING unless `--apply` is given, and it never
invents a year. Everything ambiguous comes back as a named entry for a human.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "backfill_enrollment.py"

_spec = importlib.util.spec_from_file_location("backfill_enrollment", SCRIPT)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

YEAR = {"id": "y-2026", "name": "2026/2027", "start_date": "2026-07-01",
        "end_date": "2027-06-30", "is_active": True}
OLD_YEAR = {"id": "y-2025", "name": "2025/2026", "start_date": "2025-07-01",
            "end_date": "2026-06-30", "is_active": False}


def _class(cid, name="8A", year=None):
    return {"id": cid, "name": name, "school_year_id": year}


def _exam(eid, class_ids, created_at):
    return {"id": eid, "class_ids": class_ids, "created_at": created_at}


def test_a_class_with_a_year_is_left_alone():
    plan = backfill.plan_school("s-1", [YEAR], [_class("c-1", year="y-2025")], [], [])
    assert plan["class_year"]["c-1"] == "y-2025"
    assert plan["classes_to_date"] == {}


def test_a_class_is_dated_by_its_own_papers():
    plan = backfill.plan_school("s-1", [YEAR], [_class("c-1")],
                                [_exam("e-1", ["c-1"], "2026-09-01T08:00:00Z")], [])
    assert plan["class_year"]["c-1"] == "y-2026"


def test_papers_spanning_two_years_are_ambiguous_not_guessed():
    plan = backfill.plan_school(
        "s-1", [OLD_YEAR, YEAR], [_class("c-1")],
        [_exam("e-1", ["c-1"], "2025-09-01T08:00:00Z"),
         _exam("e-2", ["c-1"], "2026-09-01T08:00:00Z")], [])
    assert plan["class_year"]["c-1"] is None
    assert plan["ambiguous"], "a class spanning years must be reported"


def test_a_class_with_no_paper_is_ambiguous_by_default():
    plan = backfill.plan_school("s-1", [YEAR], [_class("c-1")], [], [])
    assert plan["class_year"]["c-1"] is None
    assert plan["assumed"] == []


def test_the_active_year_is_only_assumed_when_asked():
    plan = backfill.plan_school("s-1", [YEAR], [_class("c-1")], [], [],
                                assume_active_year=True)
    assert plan["class_year"]["c-1"] == "y-2026"
    assert plan["assumed"] == [{"name": "8A", "year": "2026/2027"}]


def test_the_assumption_does_not_override_a_spanning_paper():
    """A class whose papers disagree is a real question, not a missing year."""
    plan = backfill.plan_school(
        "s-1", [OLD_YEAR, YEAR], [_class("c-1")],
        [_exam("e-1", ["c-1"], "2025-09-01T08:00:00Z"),
         _exam("e-2", ["c-1"], "2026-09-01T08:00:00Z")], [],
        assume_active_year=True)
    assert plan["class_year"]["c-1"] is None
    assert plan["assumed"] == []


def test_a_pupil_in_an_ambiguous_class_is_reported_not_enrolled():
    pupils = [{"id": "st-1", "class_id": "c-1"}]
    plan = backfill.plan_school("s-1", [YEAR], [_class("c-1")], [], pupils)
    assert plan["enrollments"] == []
    assert plan["unresolved"][0]["reason"] == "class has no year"


def test_a_pupil_whose_class_is_not_on_this_roster_is_reported():
    pupils = [{"id": "st-1", "class_id": "c-elsewhere"}]
    plan = backfill.plan_school("s-1", [YEAR], [_class("c-1")], [], pupils)
    assert plan["enrollments"] == []
    assert "roster" in plan["unresolved"][0]["reason"]


def test_a_pupil_with_a_dated_class_is_enrolled():
    pupils = [{"id": "st-1", "class_id": "c-1"}]
    plan = backfill.plan_school(
        "s-1", [YEAR], [_class("c-1")],
        [_exam("e-1", ["c-1"], "2026-09-01T08:00:00Z")], pupils)
    assert plan["enrollments"] == [{"student_id": "st-1", "class_id": "c-1",
                                    "school_year_id": "y-2026"}]


def test_the_two_class_pointers_are_merged_and_conflicts_named():
    students = [{"id": "st-1", "class_id": "c-1"}, {"id": "st-2", "class_id": None}]
    profiles = [{"id": "st-1", "class_id": "c-2"}, {"id": "st-2", "class_id": "c-3"},
                {"id": "st-3", "class_id": "c-9"}]
    merged, conflicts = backfill.merge_pupils(students, profiles)
    by_id = {m["id"]: m["class_id"] for m in merged}
    assert by_id["st-2"] == "c-3", "the pointer that exists wins"
    assert by_id["st-3"] == "c-9", "a pupil with no students row is still a pupil"
    assert conflicts[0]["student_id"] == "st-1"


def test_apply_is_off_unless_asked():
    """The default must be a dry run; --apply is opt-in.

    Read from the source rather than by running `main` — main connects to the
    database first thing, and a guard that needs the network is not a guard.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"--apply", action="store_true"' in src
    assert "if args.apply:" in src or "if not args.apply:" in src
    # The write helper is only ever called behind the flag.
    calls = [line for line in src.splitlines() if "apply_plan(" in line]
    assert all("def " in line or line.strip().startswith("written = ") for line in calls), (
        "apply_plan is called outside the guarded path")
