"""A closed year's weights cannot be rewritten through the weights doors.

`test_year_lock.py` asks *whether* each write door is classified. This file asks the
separate question the classification rests on: for the doors this family excused
with "the route refuses a closed year itself", does it actually?

The reason the whole family matters is that a mark is **derived**, not stored:
`grade_weighting.compute(rows, weights)` takes the weight map as an argument and is
pure over it, so the weights a subject carries at read time *are* that year's marks.
There is no frozen copy for a late policy edit to leave alone, which makes a weights
write the sharpest possible edit to a finished year.

Three doors had no such check while their own sibling did:

* `/grade-weights/<subject_id>` reads the year **off the body** — so a caller could
  name an archived year and rewrite the distribution its marks come from, which is
  the one edit the close wizard exists to prevent;
* `/grade-weights/defaults` rewrites the fallback every subject without a
  distribution of its own inherits, in every year;
* `/grade-weights/components/<id>/update` can switch a component off, which drops it
  out of every weighting that names it.

And one door is deliberately left alone: adding a component carries no weight until
one is set, and `compute` places only components whose weight is above zero, so it
can move no mark. That is the reason its exemption gives, and it is asserted here so
the reason cannot quietly stop being true.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "app" / "routes" / "admin_sekolah.py"


def _source() -> str:
    return ADMIN.read_text(encoding="utf-8-sig")


def _body(name: str) -> str:
    """One view, from `def name(` to the next top-level def."""
    src = _source()
    start = src.index(f"def {name}(")
    match = re.search(r"\r?\ndef ", src[start:])
    return src[start:start + match.start()] if match else src[start:]


#: (the view, the write it performs). The write call is what has to come *after*
#: the question — a check that runs after the row is written is a check that
#: reports the damage rather than preventing it.
DOORS = (
    ("admin_grade_weight_save", "grade_weighting.save_config("),
    ("admin_grade_defaults_save", "grade_weighting.save_defaults("),
    ("admin_grade_component_update", "grade_weighting.update_component("),
)

REFUSES = re.compile(
    r"if\s+closed_reason:\s*\r?\n\s*return\s+jsonify\(\s*\{\s*\"error\"\s*:\s*"
    r"closed_reason\s*\}\s*\)\s*,\s*403")


class TestAClosedYearIsRefusedBeforeTheWrite:
    def test_every_door_carries_the_check(self):
        missing = [name for name, _write in DOORS if "write_refusal(" not in _body(name)]
        assert not missing, (
            "these weights doors write a distribution without asking whether the year "
            f"is closed, though each is exempted from the sweep on the grounds that it "
            f"does: {missing}")

    def test_the_check_is_about_the_year_actually_being_written_to(self):
        """`admin_grade_weight_save` names the year off the body, so the question has
        to be about *that* year — asking about the running one would leave an archived
        year writable by simply naming it."""
        body = _body("admin_grade_weight_save")
        assert "write_refusal(supabase, year_id)" in body, (
            "the subject door asks about some other year than the one it writes to")

    def test_the_question_comes_before_the_write(self):
        for name, write in DOORS:
            body = _body(name)
            put = body.find(write)
            assert put != -1, f"{name} no longer performs {write}"
            ask = body.find("write_refusal(")
            # Asserted separately from the ordering: `-1 < put` is true, so an
            # ordering-only assertion passes when there is no check at all.
            assert ask != -1, f"{name} never asks whether the year is closed"
            assert ask < put, (
                f"{name} asks whether the year is closed only after it has already "
                f"called {write}")

    def test_a_closed_year_is_answered_not_half_handled(self):
        for name, _write in DOORS:
            body = _body(name)
            assert REFUSES.search(body), (
                f"{name} finds a closed year but does not answer with a 403 refusal, "
                "so the caller cannot tell the write did not happen")


class TestTheInertDoorIsDeliberatelyLeftAlone:
    def test_adding_a_component_is_not_refused(self):
        """Its exemption says it can move no mark because a new component carries no
        weight until one is set. Refusing it would block a school from tidying its
        component list at any point after a year closes, for no gain."""
        body = _body("admin_grade_component_create")
        assert "write_refusal(" not in body, (
            "adding a component gained a closed-year refusal, so its exemption's "
            "reason is no longer the reason it is exempt")

    def test_a_weightless_component_cannot_move_a_mark(self):
        """The claim above, asserted against the arithmetic rather than restated: a
        component absent from the weight map contributes nothing to the final mark."""
        from app.services import grade_weighting as gw

        marked = gw.compute([("c-1", 80.0)], {"c-1": 100})
        unmarked = gw.compute([("c-1", 80.0), ("c-2", 10.0)], {"c-1": 100})
        assert marked["final"] == unmarked["final"] == 80.0
        assert unmarked["untagged"] == 1, (
            "a scored row on an unweighted component is counted and reported, not "
            "folded into the mark")
