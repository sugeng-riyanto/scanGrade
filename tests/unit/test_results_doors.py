"""The roster a teacher scrolls: one more row means one more child to open.

`/teacher/results` is the page a class is read on — thirty rows, "who scored
what" — and until now every row offered exactly one door, to the *submission*
(`/teacher/grade/<submission_id>`): one paper's marks. The page that says **why**
this child landed there is the learner's own report, and it was two clicks away,
through the item analysis and its roster. So the roster gets the same door the
grading queue has, in **both** halves of the partial — the desktop table row and
the mobile card, which is the half a teacher marking on a phone actually sees.

Four things make such a link lie, and each is held here:

* **the address is the one the app serves** — built from Flask's own `url_map`,
  blueprint prefix and all, so renaming the route without the template fails in
  the suite rather than 404ing on a classroom laptop;
* **a row with no profile gets no door** — the learner route is keyed on the
  profile id and answers 404 without one;
* **the all-exams view gets no door** — with no `exam_id` there is no report to
  address, and `href="/teacher/analysis//report/student/…"` is a link that looks
  whole and leads to a 404 (this is also why the suites that render this partial
  bare keep passing: the door is simply not written);
* **`Detail` stays the submission** — two different pages, so two doors rather
  than one button whose meaning depends on where it was pressed.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TABLE_PATH = ROOT / "app" / "templates" / "teacher" / "_results_table.html"
TABLE = TABLE_PATH.read_text(encoding="utf-8")
RESULTS = (ROOT / "app" / "templates" / "teacher" / "results.html").read_text(
    encoding="utf-8")

EXAM_ID = "exam-1"

#: The pair the label is written as. A literal "Laporan" here would be the one
#: string on the row that does not follow the toggle — and, because this partial's
#: coverage floor is 100% with nothing left to translate, the gate would drop with
#: it.
LABEL = "t('Laporan','Report')"

#: The anchor that carries the door, matched by its marker rather than by shape.
DOOR = re.compile(r"<a[^>]*data-learner-report[^>]*>.*?</a>", re.S)


#: The sentinel that means "derive one from the name". A `None` default cannot
#: say that: `None` is also the *value* under test — the row the app could not
#: hang on a profile — and the first version of this helper quietly replaced it
#: with a real id, so the test proved nothing.
_DERIVE = object()


def _row(name: str, student_id=_DERIVE) -> dict:
    """One submission as the partial reads it."""
    return {
        "id": f"sub-{name}",
        "student_id": f"stu-{name}" if student_id is _DERIVE else student_id,
        "student_name": name,
        "status": "graded",
        "score": 78,
        "final_score": 76,
        "penalty": 2,
        "submitted_at": "2026-09-19T02:00:00+00:00",
        "submitted_late": False,
        "answers": {},
        "teacher_feedback": {"scores": {}, "comments": {}},
    }


@contextlib.contextmanager
def _signed_in(app, path):
    from flask import g
    with app.test_request_context(path):
        g.user_id = "tea-1"
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        yield


def _render(app, rows, exam_id=EXAM_ID, is_scan_section=False):
    """The partial, rendered the way `/teacher/results` includes it.

    Inside a request context, because the page this partial belongs to carries a
    language toggle and its label is a `t()` pair.
    """
    with _signed_in(app, "/teacher/results?exam_id=exam-1"):
        return app.jinja_env.get_template("teacher/_results_table.html").render(
            sub_list=rows, is_scan_section=is_scan_section, exam_id=exam_id)


def _doors(html: str) -> list[str]:
    return re.findall(r'data-learner-report', html)


# ── the door is written, in both halves ──────────────────────────────────────

class TestTheRosterOpensTheLearnerReport:

    def test_both_halves_of_every_row_carry_it(self, app):
        """The desktop row and the mobile card are separate markup, and a door
        added to one of them is a door half the teachers never see."""
        html = _render(app, [_row("Ani"), _row("Budi")])
        assert len(_doors(html)) == 4, (
            "expected one door per row in each half of the partial")
        for name in ("Ani", "Budi"):
            assert (f'href="/teacher/analysis/{EXAM_ID}/report/student/stu-{name}"'
                    in html), f"no door for {name}"
        # Two rows, two learners: a door that reused the submission would name the
        # same id twice.
        assert len(set(re.findall(r"report/student/[\w-]+", html))) == 2

    def test_the_link_is_the_address_the_app_serves(self, app):
        """Built from `/teacher/analysis/<exam_id>/report/student/<student_id>`
        rather than from the template's text, so a renamed route fails here."""
        rule = next((r for r in app.url_map.iter_rules()
                     if r.endpoint.endswith("exam_analysis_student")), None)
        assert rule, "the learner's own report route is gone"
        served = rule.build({"exam_id": EXAM_ID, "student_id": "stu-Ani"},
                            append_unknown=False)[1]
        assert f'href="{served}"' in _render(app, [_row("Ani")])

    def test_the_door_opens_beside_the_page_and_beside_the_row(self, app):
        """A roster is a place a teacher is *in* — losing it to a report means
        finding the row again. Both halves open in a new tab, and neither is an
        inline control that posts."""
        for door in DOOR.findall(_render(app, [_row("Ani")])):
            assert 'target="_blank"' in door and 'rel="noopener"' in door
            assert "href=" in door

    def test_detail_is_still_the_submission(self, app):
        """Two different pages. `Detail` grades one paper; the door opens the
        learner's report, which is a different document about the same child."""
        html = _render(app, [_row("Ani")])
        assert 'href="/teacher/grade/sub-Ani"' in html
        assert 'data-learner-report' in html
        door = DOOR.findall(html)[0]
        assert "/teacher/grade/" not in door

    def test_the_label_is_a_pair_and_not_a_literal(self, app):
        """The partial renders inside `results.html`, which switches languages —
        and its own coverage floor is 100%, so a hard-coded Indonesian label is
        both a string that stops following the reader and a gate regression."""
        assert LABEL in TABLE
        assert 'x-text="' + LABEL + '"' in TABLE
        assert LABEL in _render(app, [_row("Ani")])


# ── the two reasons not to write it ─────────────────────────────────────────

class TestTheTwoReasonsThereIsNoDoor:

    def test_a_row_with_no_profile_gets_none(self, app):
        """A scan paper the app could not hang on a profile has no page to open —
        the learner route answers 404 for it — so the row keeps only `Detail`."""
        html = _render(app, [_row("Ani"), _row("Tanpa", student_id=None)])
        assert len(_doors(html)) == 2, "a paper with no profile was given a door"
        assert "report/student/None" not in html
        assert "report/student/" in html, "the row that has a profile lost its door too"

    def test_the_all_exams_view_gets_none(self, app):
        """The list without a chosen exam: no report to address. The door is
        guarded on `exam_id` as well, which is why a bare render of this partial —
        the shape several other suites use — is not a broken link."""
        html = _render(app, [_row("Ani")], exam_id=None)
        assert not _doors(html)
        assert "report/student" not in html

    def test_the_guard_names_both_conditions(self):
        """Source-level, because the rendering above cannot tell a guard that
        checks both from one that happens to be falsy in the fixture."""
        guards = [g.strip() for g in re.findall(r"\{%\s*if (.+?)%\}", TABLE)]
        assert "exam_id and s.student_id" in guards
        assert guards.count("exam_id and s.student_id") == 2, (
            "the desktop half and the mobile half must guard the same two things")


# ── the page that includes it ───────────────────────────────────────────────

def test_the_results_page_hands_the_partial_an_exam_to_address():
    """The door needs `exam_id`, and it comes from the include's context — so the
    page has to be the one that carries it. A partial guarded on a variable its
    only caller does not pass is a door that never renders."""
    includes = re.findall(r'\{%\s*with ([^%]*?)%\}\s*\{%\s*include '
                          r'"teacher/_results_table\.html"', RESULTS)
    assert len(includes) == 2, "the two sections (online, scan) both include it"
    for context in includes:
        assert "sub_list=" in context
    assert "exam_id" in RESULTS, "the page does not carry the exam it lists"


@pytest.mark.parametrize("half", ["desktop", "mobile"])
def test_the_halves_stay_distinguishable(half):
    """A guard on the *shape* rather than on the count: the mobile card's action
    row is a `flex` of pills and the desktop cell is a `whitespace-nowrap` cell,
    so a door added to one and copied into the other keeps its own styling."""
    marker = "mobile-only" if half == "mobile" else "desktop-only"
    body = TABLE.split(f'class="{marker}"', 1)[1]
    body = body.split(f'class="{("desktop-only" if half == "mobile" else "mobile-only")}"', 1)[0] \
        if f'class="{("desktop-only" if half == "mobile" else "mobile-only")}"' in body else body
    assert "data-learner-report" in body, f"the {half} half has no door"
