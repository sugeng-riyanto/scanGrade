"""What a distribution change does to the class, not to one sample.

`test_grade_weight_compare.py` pinned the comparison of one seeded pupil across
their subjects. A mark, though, is changed for a **class**: the school default binds
every subject that never saved its own row, so a change to it moves a whole cohort,
and the question that decides a save is who moves and by how much.

This file pins the class impact end to end, in the four places it can mislead:

* **the scope** (`grade_weighting.affected_subjects`) — a default change covers the
  subjects that *follow* the default, and a subject that keeps its own row is not
  affected at all. Reporting movement there would invent it.
* **the arithmetic** — both sides are :func:`compute`: the saved policy for the
  subject against the weights being typed. Every number below is checkable by hand
  from the fixture.
* **the reading** — only pupils who **have** a mark are counted, a movement smaller
  than the threshold is not a movement, another year's paper and another school's
  pupils are never read, a mark already released to the pupil is flagged, and a
  school-wide read pages past PostgREST's 1000-row window instead of quietly
  answering with a truncated cohort.
* **the wiring** — the door is an admin-only **read** whose scope is derived from
  the session's school, and the page asks for it deliberately rather than on every
  keystroke.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services import grade_weighting as gw
from app.utils import breadcrumbs
from tests.unit.test_invigilation import _DB

ROOT = Path(__file__).resolve().parents[2]
ADMIN_SCHOOL = ROOT / "app" / "routes" / "admin_sekolah.py"
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"

SCHOOL, OTHER = "s1", "s2"
YEAR, LAST_YEAR = "y1", "y0"

#: The saved school default: Tugas 40%, UTS 60%.
SAVED = {"gc1": 40, "gc2": 60}
#: The same default retyped the other way round.
TYPED = {"gc1": 60, "gc2": 40}


def _tables() -> dict:
    """One school, three pupils, and papers that mislead if read wrong.

    By hand, under the saved default (40/60) and the typed one (60/40):

    * **Ana in Fisika** — Tugas 80, UTS 70: 74.0 → **76.0** (+2.0), and her Fisika
      paper *is* released, so this is a mark she can open;
    * **Budi in Fisika** — only a UTS of 50: 30.0 → **20.0** (−10.0), unreleased;
    * **Ana and Budi in Biologi** — one Tugas each (90, 60), and Biologi keeps its
      **own** row (Tugas 100%), so a default change must not move either.
    """
    return {
        "profiles": [
            {"id": "p1", "school_id": SCHOOL, "role": "murid", "full_name": "Ana Wijaya"},
            {"id": "p2", "school_id": SCHOOL, "role": "murid", "full_name": "Budi Santoso"},
            {"id": "p3", "school_id": SCHOOL, "role": "murid", "full_name": "Ana Alumni"},
            {"id": "p9", "school_id": OTHER, "role": "murid", "full_name": "Ana Negeri Lain"},
        ],
        "schools": [{"id": SCHOOL}, {"id": OTHER}],
        "classes": [{"id": "c1", "school_id": SCHOOL, "name": "10A"},
                    {"id": "c2", "school_id": SCHOOL, "name": "10B"}],
        "students": [
            {"id": "p1", "school_id": SCHOOL, "class_id": "c1", "status": "active",
             "profiles": {"full_name": "Ana Wijaya"}, "classes": {"name": "10A"}},
            {"id": "p2", "school_id": SCHOOL, "class_id": "c2", "status": "active",
             "profiles": {"full_name": "Budi Santoso"}, "classes": {"name": "10B"}},
            {"id": "p3", "school_id": SCHOOL, "class_id": "c1", "status": "alumni",
             "profiles": {"full_name": "Ana Alumni"}, "classes": {"name": "10A"}},
            {"id": "p9", "school_id": OTHER, "class_id": "c9", "status": "active",
             "profiles": {"full_name": "Ana Negeri Lain"}, "classes": {"name": "9Z"}},
        ],
        "subjects": [
            {"id": "su1", "school_id": SCHOOL, "name": "Fisika", "is_active": True},
            {"id": "su2", "school_id": SCHOOL, "name": "Biologi", "is_active": True},
            {"id": "su3", "school_id": SCHOOL, "name": "Kimia", "is_active": False},
            {"id": "su9", "school_id": OTHER, "name": "Fisika", "is_active": True},
        ],
        "grade_component_type": [
            {"id": "gc1", "school_id": SCHOOL, "name": "Tugas", "is_active": True,
             "sort_order": 1, "default_weight": 40},
            {"id": "gc2", "school_id": SCHOOL, "name": "UTS", "is_active": True,
             "sort_order": 2, "default_weight": 60},
        ],
        # Biologi keeps its own row, so the default does not bind it.
        "grade_weight_config": [
            {"id": "w1", "school_id": SCHOOL, "subject_id": "su2", "school_year_id": YEAR,
             "component_id": "gc1", "weight_percent": 100, "is_active": True},
        ],
        "exams": [
            {"id": "e1", "school_id": SCHOOL, "subject_id": "su1", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "e2", "school_id": SCHOOL, "subject_id": "su1", "school_year_id": YEAR,
             "grade_component_type_id": "gc2"},
            {"id": "e3", "school_id": SCHOOL, "subject_id": "su2", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "e4", "school_id": SCHOOL, "subject_id": "su1", "school_year_id": LAST_YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "e5", "school_id": OTHER, "subject_id": "su9", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
        ],
        "submissions": [
            {"id": "b1", "exam_id": "e1", "student_id": "p1", "score": 80,
             "is_published": True, "status": "published"},
            {"id": "b2", "exam_id": "e2", "student_id": "p1", "final_score": 70,
             "is_published": False, "status": "submitted"},
            {"id": "b3", "exam_id": "e2", "student_id": "p2", "score": 50,
             "is_published": False, "status": "submitted"},
            {"id": "b4", "exam_id": "e3", "student_id": "p1", "score": 90,
             "is_published": True, "status": "published"},
            {"id": "b5", "exam_id": "e3", "student_id": "p2", "score": 60,
             "is_published": True, "status": "published"},
            {"id": "b6", "exam_id": "e4", "student_id": "p1", "score": 100,
             "is_published": True, "status": "published"},
            {"id": "b7", "exam_id": "e5", "student_id": "p9", "score": 100,
             "is_published": True, "status": "published"},
        ],
    }


def _subjects(scope: str, subject_id=None) -> list[dict]:
    return gw.affected_subjects(_DB(_tables()), SCHOOL, YEAR, scope, subject_id)


# ── the scope: which subjects a change actually touches ──────────────────────

class TestTheScope:
    def test_a_default_change_covers_the_subjects_that_follow_the_default(self):
        assert [s["name"] for s in _subjects("default")] == ["Fisika"], (
            "a subject with its own row was reported as affected by the default")

    def test_an_inactive_subject_is_never_affected(self):
        assert all(s["subject_id"] != "su3" for s in _subjects("default")), (
            "a retired subject would be weighed against no policy the admin has")

    def test_a_subject_change_covers_exactly_that_subject(self):
        assert _subjects("subject", "su1") == [{"subject_id": "su1", "name": "Fisika"}]
        assert _subjects("subject", "su2") == [{"subject_id": "su2", "name": "Biologi"}]

    def test_an_unknown_scope_weighs_nothing(self):
        assert _subjects("") == [] and _subjects("everything") == [], (
            "an unknown scope must not fall back to the whole school")

    def test_another_schools_subject_is_not_this_schools(self):
        assert _subjects("subject", "su9") == []

    def test_the_school_is_a_required_argument(self):
        assert gw.affected_subjects(_DB(_tables()), "", YEAR, "default") == []


# ── the arithmetic: who moves, and by how much ───────────────────────────────

class TestTheMovement:
    def _impact(self, subjects=None, weights=None, **kw):
        return gw.class_impact(_DB(_tables()), SCHOOL,
                               subjects if subjects is not None else _subjects("default"),
                               YEAR, SAVED if weights is None else weights, **kw)

    def test_it_names_the_pupils_whose_mark_would_move(self):
        out = self._impact(weights=TYPED)
        by_name = {r["name"]: r for r in out["pupils"]}
        assert set(by_name) == {"Ana Wijaya", "Budi Santoso"}, (
            "the impact does not name the pupils it would move")
        # Ana: 80 x 60% + 70 x 40% = 48 + 28 = 76.0, was 80 x 40% + 70 x 60% = 74.0.
        assert (by_name["Ana Wijaya"]["saved"], by_name["Ana Wijaya"]["live"],
                by_name["Ana Wijaya"]["delta"]) == (74.0, 76.0, 2.0)
        # Budi has only a UTS of 50: 50 x 40% = 20.0, was 50 x 60% = 30.0.
        assert (by_name["Budi Santoso"]["saved"], by_name["Budi Santoso"]["live"],
                by_name["Budi Santoso"]["delta"]) == (30.0, 20.0, -10.0)

    def test_the_biggest_move_is_first(self):
        out = self._impact(weights=TYPED)
        assert [r["delta"] for r in out["pupils"]] == [-10.0, 2.0], (
            "the list must lead with the largest movement, or the admin reads noise first")

    def test_the_counts_add_up(self):
        counts = self._impact(weights=TYPED)["counts"]
        assert counts["pairs"] == 2, "both marked pairs must be evaluated"
        assert counts["moved"] == 2 and counts["up"] == 1 and counts["down"] == 1
        assert counts["pupils_marked"] == 2 and counts["pupils_moved"] == 2
        assert counts["max_up"] == 2.0 and counts["max_down"] == -10.0

    def test_the_saved_policy_moves_nothing(self):
        out = self._impact(weights=SAVED)
        assert out["counts"]["moved"] == 0 and out["pupils"] == [], (
            "re-typing the saved weights reported a movement")
        assert out["counts"]["pairs"] == 2, "the pupils must still be counted"

    def test_an_empty_distribution_falls_back_to_the_simple_mean(self):
        """No weights at all is the roster's fallback, so it is reported, not refused.

        Budi's single UTS of 50 is 50.0 under the simple mean, against 30.0 weighted.
        """
        out = self._impact(weights={})
        budi = [r for r in out["pupils"] if r["name"] == "Budi Santoso"]
        assert budi and budi[0]["live"] == 50.0, (
            "the fallback mean was not computed for the pupils without weights")

    def test_a_movement_below_the_threshold_is_not_a_movement(self):
        """Ana moves -0.7, Budi -0.5: with a 0.6 floor only Ana is reported."""
        out = self._impact(weights={"gc1": 40, "gc2": 59}, min_delta=0.6)
        assert [r["name"] for r in out["pupils"]] == ["Ana Wijaya"], (
            "a movement under the threshold was reported as a move")
        assert out["counts"]["moved"] == 1 and out["counts"]["pairs"] == 2

    def test_a_mark_already_shown_to_the_pupil_is_flagged(self):
        out = self._impact(weights=TYPED)
        by_name = {r["name"]: r for r in out["pupils"]}
        assert by_name["Ana Wijaya"]["released"] is True, (
            "a mark the pupil can already open must be marked as released")
        assert by_name["Budi Santoso"]["released"] is False
        assert out["counts"]["reported"] == 1

    def test_the_typed_total_travels_so_a_caller_can_say_it_does_not_add_up(self):
        assert self._impact(weights=TYPED)["typed_total"] == 100
        assert self._impact(weights={"gc1": 30, "gc2": 30})["typed_total"] == 60

    def test_the_shown_rows_are_capped_but_the_counts_are_not(self):
        out = self._impact(weights=TYPED, limit=1)
        assert out["shown"] == 1 and len(out["pupils"]) == 1
        assert out["pupils"][0]["name"] == "Budi Santoso", "the cap must keep the biggest"
        assert out["counts"]["moved"] == 2 and out["counts"]["pupils_moved"] == 2, (
            "the headline count must not shrink to the cap")

    def test_each_subject_carries_its_own_tally(self):
        rows = {s["subject_id"]: s for s in self._impact(weights=TYPED)["subjects"]}
        assert rows["su1"] == {"subject_id": "su1", "name": "Fisika", "evaluated": 2,
                               "moved": 2, "up": 1, "down": 1, "reported": 1}

    def test_a_subject_that_keeps_its_own_row_does_not_move_with_the_default(self):
        """The whole point of the scope: Biologi is not affected, so it is not weighed."""
        out = self._impact(subjects=_subjects("subject", "su1"), weights=TYPED)
        assert all(r["subject_id"] == "su1" for r in out["pupils"])
        # And weighing Biologi against its *own* saved row moves nothing.
        biologi = self._impact(subjects=_subjects("subject", "su2"), weights={"gc1": 100})
        assert biologi["counts"]["moved"] == 0
        assert biologi["counts"]["pairs"] == 2, "Ana and Budi are still evaluated"

    def test_a_paper_of_another_year_is_not_weighed(self):
        """Ana's last-year 100 would make her saved Fisika mark 78.0, not 74.0."""
        ana = {r["name"]: r for r in self._impact(weights=TYPED)["pupils"]}["Ana Wijaya"]
        assert ana["saved"] == 74.0, "another year's paper was weighed this year"

    def test_a_pupil_of_another_school_is_never_in_the_answer(self):
        out = self._impact(weights=TYPED)
        assert all(r["student_id"] != "p9" for r in out["pupils"])
        assert out["counts"]["pairs"] == 2, "a foreign paper was weighed"

    def test_a_subject_with_no_marked_paper_is_empty_not_a_zero(self):
        out = self._impact(subjects=[{"subject_id": "su3", "name": "Kimia"}], weights=TYPED)
        assert out["counts"]["pairs"] == 0 and out["pupils"] == []

    def test_an_empty_scope_is_not_a_full_school_read(self):
        db = _DB(_tables())
        out = gw.class_impact(db, SCHOOL, [], YEAR, TYPED)
        assert out["counts"]["pairs"] == 0 and out["pupils"] == []
        assert db.log == [], "an empty scope still read the school"

    def test_it_reads_nothing_but_rows(self):
        db = _DB(_tables())
        gw.class_impact(db, SCHOOL, _subjects("default"), YEAR, TYPED)
        assert db.log, "the impact read nothing at all"
        assert {op for op, _t, _p, _f in db.log} == {"select"}, (
            "the impact computation writes; it must only read")

    def test_it_scopes_the_papers_to_the_school_and_the_year(self):
        src = (ROOT / "app" / "services" / "grade_weighting.py").read_text(encoding="utf-8")
        body = src.split("def class_impact(")[1].split("\ndef ")[0]
        assert 'eq("school_id", school_id)' in body and '.in_("subject_id", ids)' in body, (
            "the papers are not bounded by the school and the affected subjects")
        assert 'eq("school_year_id", year_id)' in body, (
            "another year's papers would be weighed")
        assert '.in_("exam_id", list(exam_subject))' in body, (
            "the graded rows are not bounded by the papers just read")
        assert "compute(" in body, "the impact invented an arithmetic instead of reusing compute"


# ── the reading: a school-wide read must not stop at 1000 rows ───────────────

class _Page:
    """A query that honours ``range`` — the fake that proves the paging."""

    def __init__(self, rows, log):
        self.rows, self.log = rows, log
        self.start, self.end = 0, None

    def range(self, start, end):
        self.start, self.end = start, end
        self.log.append((start, end))
        return self

    def execute(self):
        class _Res:
            data = None
        out = _Res()
        out.data = self.rows[self.start:self.end + 1]
        return out


class TestThePagedRead:
    def test_it_reads_past_one_thousand_rows(self):
        log = []
        rows, capped = gw._paged_rows(lambda: _Page(list(range(1, 101)), log),
                                      page=10, cap=1000)
        assert len(rows) == 100 and rows[99] == 100 and not capped, (
            "the read stopped short of the data")
        assert log[:10] == [(0, 9), (10, 19), (20, 29), (30, 39), (40, 49),
                            (50, 59), (60, 69), (70, 79), (80, 89), (90, 99)]
        assert log[10:] == [(100, 109)], (
            "the end of the data is learned by asking once more, not by guessing")

    def test_a_short_page_ends_the_loop(self):
        log = []
        rows, capped = gw._paged_rows(lambda: _Page(list(range(1, 8)), log),
                                      page=10, cap=1000)
        assert len(rows) == 7 and not capped and log == [(0, 9)], (
            "a short page is the end of the data, not a reason to ask again")

    def test_the_row_cap_is_reported_rather_than_rounded_away(self):
        log = []
        rows, capped = gw._paged_rows(lambda: _Page(list(range(1, 101)), log),
                                      page=10, cap=25)
        assert len(rows) == 25 and capped is True, (
            "a capped read must say so; the counts it feeds may be short")
        assert log[-1][0] == 20, "the last page must stop at the cap"

    def test_a_failed_page_keeps_what_it_had_and_says_so(self):
        class _Boom(_Page):
            def execute(self):
                raise RuntimeError("supabase hiccup")

        rows, capped = gw._paged_rows(lambda: _Boom([], []), page=10, cap=100)
        assert rows == [] and capped is False, "an unreadable page is an empty read, not a crash"

    def test_a_fresh_query_is_built_for_every_page(self):
        """Reusing one builder while moving its range is how a page gets skipped."""
        built = []

        def build():
            built.append(1)
            return _Page(list(range(1, 21)), [])

        rows, _capped = gw._paged_rows(build, page=10, cap=1000)
        # Two pages of ten, then the probe that comes back empty.
        assert len(rows) == 20 and len(built) == 3, (
            "the same query object was reused for the next page")


# ── the door ────────────────────────────────────────────────────────────────

def _body(name: str) -> str:
    """A route function **with its own decorators**, which the guards have to read.

    Reading them from whichever function follows (the older shape of this helper)
    let "is it admin-only" pass on a door that was not: the decorator it matched
    belonged to the neighbour.
    """
    src = ADMIN_SCHOOL.read_text(encoding="utf-8-sig")
    start = src.index(f"def {name}(")
    lines = src[:start].splitlines()
    if lines and not lines[-1].strip():
        lines.pop()
    decorators: list[str] = []
    while lines and lines[-1].strip().startswith("@"):
        decorators.insert(0, lines.pop())
    head = "".join(line + "\n" for line in decorators)
    match = re.search(r"\r?\ndef ", src[start:])
    return head + (src[start:start + match.start()] if match else src[start:])


class TestTheDoor:
    def test_the_impact_door_is_admin_only_and_scopes_by_the_session(self):
        body = _body("admin_grade_class_impact")
        assert "@admin_sekolah_required" in body, "the impact read is not admin-only"
        assert "_school_id()" in body, "the school must come from the session"
        assert 'request.args.get("school' not in body, (
            "the school must never be a value the query can set")

    def test_the_caller_cannot_widen_the_scope(self):
        body = _body("admin_grade_class_impact")
        assert "affected_subjects(" in body, (
            "the affected subjects must be derived from the scope, not named by the URL")
        assert 'request.args.get("subjects' not in body, (
            "the URL can name a subject list, so another school's subjects could be weighed")

    def test_a_subject_of_another_school_is_refused(self):
        body = _body("admin_grade_class_impact")
        assert 'table("subjects")' in body and 'eq("school_id", sid)' in body, (
            "a foreign subject must be refused, not weighed")
        assert "404" in body

    def test_an_unknown_scope_is_refused(self):
        body = _body("admin_grade_class_impact")
        assert '"default"' in body and '"subject"' in body and "400" in body

    def test_the_typed_weights_are_checked_against_this_schools_components(self):
        body = _body("_typed_weights")
        assert "component_ids(" in body, (
            "a component the school does not own would be weighed as if it did")
        assert "403" in body and "400" in body, (
            "a foreign component and a malformed weight must be told apart")

    def test_the_impact_door_is_a_read_on_a_get(self):
        src = ADMIN_SCHOOL.read_text(encoding="utf-8-sig")
        block = src.split('route("/grade-weights/class-impact"')[1].split("\n@admin_sekolah_bp.route")[0]
        assert "methods=" not in block, "the impact read must be a GET: it stores nothing"
        body = _body("admin_grade_class_impact")
        for writer in (".insert(", ".update(", ".delete("):
            assert writer not in body, f"the impact door writes ({writer})"

    def test_the_impact_readback_is_not_a_page_of_its_own(self):
        assert "class-impact" in breadcrumbs.MACHINE, (
            "the JSON readback would appear in the trail as if it were a page")


# ── the page ─────────────────────────────────────────────────────────────────

def _impact_block() -> str:
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    assert "data-preview-impact" in html, (
        "the page cannot show what a change does to the class")
    return html.split("data-preview-impact", 1)[1].split("</table>", 1)[0]


class TestThePageIsWired:
    def test_the_panel_names_the_movement_and_the_pupils(self):
        block = _impact_block()
        assert 't(\'Dampak ke kelas\',\'Impact on the class\')' in block
        assert "impact.counts.pupils_moved" in block and "impact.counts.pupils_marked" in block, (
            "the panel does not count the pupils who move")
        assert 'x-for="row in impact.pupils"' in block, "the panel names no pupils"
        assert "row.saved" in block and "row.live" in block, "the panel shows no before/after"

    def test_the_movement_is_coloured_by_direction(self):
        block = _impact_block()
        assert "row.delta > 0" in block and "row.delta.toFixed(1)" in block

    def test_a_released_mark_is_flagged_and_counted(self):
        block = _impact_block()
        assert "row.released" in block, (
            "a change to a mark the pupil can already open is not distinguished")
        assert "impact.counts.reported" in block, "the released count is not shown"

    def test_the_panel_says_when_nothing_moves(self):
        block = _impact_block()
        assert "data-impact-none" in block and "impact.counts.moved === 0" in block, (
            "an empty movement would look like a panel that failed to load")
        assert "data-impact-empty" in block and "impact.counts.pairs === 0" in block

    def test_the_panel_warns_when_the_weights_do_not_add_up(self):
        block = _impact_block()
        assert "impact.typed_total !== required" in block, (
            "figures computed from a distribution that does not total 100% must say so")
        assert "impact.capped" in block, (
            "a read that hit its cap must not present its counts as complete")

    def test_the_impact_is_asked_for_deliberately_not_while_typing(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        method = html.split("loadImpact(targetUrl) {", 1)[1].split("\n        },", 1)[0]
        assert "@click=\"loadImpact()\"" in html, (
            "there is no way to ask for the impact")
        assert "impactLoading" in method, "the button has no working state"
        for trigger in ("@input", "@change"):
            for chunk in html.split("data-preview-impact", 1)[1].split("</div>", 1)[0].split("<template"):
                assert trigger not in chunk.split("loadImpact()")[0] or "loadImpact()" not in chunk, (
                    "the impact re-reads on every keystroke")

    def test_a_stale_figure_is_labelled(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        method = html.split("impactStale() {", 1)[1].split("\n        },", 1)[0]
        assert "impactSignature !== this.impactQuery()" in method, (
            "a figure computed before the weights changed would read as current")

    def test_the_scope_follows_the_preview_and_is_sent_to_the_door(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        method = html.split("impactQuery() {", 1)[1].split("\n        },", 1)[0]
        assert "previewSubject === '__default__' ? 'default' : 'subject'" in method, (
            "the scope must follow the distribution being previewed")
        # The URL and its parameters live in the one serializer both the panel and
        # a save go through, so the figures shown and the figures a save is judged
        # by cannot be asked for differently.
        builder = html.split("impactUrl(scope, subjectId, weights) {", 1)[1]\
            .split("\n        },", 1)[0]
        assert "/admin-sekolah/grade-weights/class-impact?" in builder
        assert "w=" in builder, "the typed weights must travel to the door"
        assert "subject_id=" in builder

    def test_the_impact_is_a_read(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        for name in ("impactQuery() {", "loadImpact(targetUrl) {", "impactStale() {"):
            body = html.split(name, 1)[1].split("\n        },", 1)[0]
            assert "post(" not in body, f"{name} writes to the server"

    def test_the_rows_keep_the_servers_order(self):
        block = _impact_block()
        assert ".sort(" not in block, (
            "the page re-sorts the movements, so the biggest is no longer first")

    def test_the_impact_copy_is_bilingual(self):
        block = _impact_block()
        for pair in ("t('Dampak ke kelas','Impact on the class')",
                     "t('Hitung dampak','Check the impact')",
                     "t('Murid yang bergerak:','Pupils who would move:')",
                     "t('sudah terbit','released')", "t('Selisih','Change')"):
            assert pair in block, f"the impact panel lost its bilingual pair: {pair}"
