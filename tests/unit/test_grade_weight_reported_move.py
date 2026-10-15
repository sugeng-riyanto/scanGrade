"""A distribution that would move an already-reported mark by more than a point.

`test_grade_weight_kkm.py` pinned the **fail**: a released mark that would read as
passing today and as failing after the save. That is the sharpest movement, but it
is not the only one an admin has to decide about. A pupil whose published 62 would
be recomputed to 68 has not started failing — they have had a number their parents
already read change by six points, which is a different mark rather than the same
one rounded twice. The page counted those pupils ("*N* of them are already released
to pupils") and listed them in a table with a badge, which told the admin they had
moved but never that any of them had moved *far*.

So the warning is the same read asked the question a count cannot answer, and this
file pins it in the four places it can mislead:

* **the marks that are eligible** — released only. An unreleased mark is work in
  progress, and warning about it is how a warning trains the admin to click past it;
* **the threshold** — *more than* a point, on the difference of the two marks as the
  pupil sees them (``compute`` rounds both to one decimal), so exactly one point is
  not more than one, and a movement under the threshold is not a warning;
* **the provenance of the number** — it is one constant, it is tested before any
  caller's movement threshold (or a small-but-real change would be filtered away),
  and it travels in the payload, because a page that promises a number it does not
  count with is how this class of defect reads;
* **the door and the page** — the warning rides on the same read-only impact read,
  the panel names the pupils with the direction and the size, and each save asks for
  the figures of the policy it is about to write before it writes it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import grade_weighting as gw
from tests.unit.test_invigilation import _DB
#: The page is checked as a **render** as well as a reading: `data-impact-reported`
#: is only markup until a request walks the chain, and the door that walks it (the
#: sign-in, the session, the admin gate, the template) belongs to the end-to-end
#: suite. Borrowing its signed-in client is one dependency instead of a second copy
#: of the fake database that would drift from it.
#: `doors` and `db` come with it: pytest resolves a fixture's own dependencies in
#: the namespace that *requests* it, so the fixture is only reachable with the two
#: it stands on imported beside it.
from tests.unit.test_grade_weight_page_e2e import (  # noqa: F401
    as_admin, db, doors)

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"
BASE_HTML = ROOT / "app" / "templates" / "base.html"

#: The save check is JavaScript, so the part that decides whether the admin is
#: stopped is *run* under node against a faked network, not described. The rest of
#: the suite reads the page; this executes it.
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the save check")

SCHOOL = "s1"
YEAR, LAST_YEAR = "y1", "y0"

#: The saved school default: Tugas 40%, UTS 60%.
SAVED = {"gc1": 40, "gc2": 60}
#: The same default retyped the other way round, which is the change under test.
TYPED = {"gc1": 60, "gc2": 40}

FISIKA = [{"subject_id": "su1", "name": "Fisika"}]
BIOLOGI = [{"subject_id": "su2", "name": "Biologi"}]


def _tables() -> dict:
    """One school, one subject, and movements that mislead if read wrong.

    Under the saved default (40/60) and the typed one (60/40), by hand — with
    ``d = 0.2 x (Tugas - UTS)`` the movement of every pupil here:

    * **Ana** (released) — Tugas 80, UTS 50: **62.0 → 68.0**, +6.0. A published
      mark that would rise by six points;
    * **Budi** (released) — Tugas 50, UTS 80: **68.0 → 62.0**, −6.0. The same
      distance, downward, so a warning that only looks one way is caught;
    * **Citra** (unreleased) — the same marks as Ana: +6.0 on a mark the pupil
      cannot open yet, so she is a movement and not a warning;
    * **Dedi** (released) — Tugas 70, UTS 60: **64.0 → 66.0**, +2.0. Over the line
      and smaller than the two above it, so the order is checkable;
    * **Eka** (released) — Tugas 70, UTS 65: **67.0 → 68.0**, **+1.0 exactly**:
      "more than a point" is not "at least a point";
    * **Fajar** (released) — Tugas 70, UTS 67: **68.2 → 68.8**, +0.6: a movement
      (it clears the impact threshold) that is not a warning;
    * **Ana, last year** — a Tugas of 100 under the *same* component. It would make
      her saved mark 66.0 rather than 62.0, so the movement proves the year scope.
    """
    return {
        "schools": [{"id": SCHOOL}],
        "profiles": [
            {"id": "p1", "school_id": SCHOOL, "role": "murid", "full_name": "Ana Wijaya"},
            {"id": "p2", "school_id": SCHOOL, "role": "murid", "full_name": "Budi Santoso"},
            {"id": "p3", "school_id": SCHOOL, "role": "murid", "full_name": "Citra Lestari"},
            {"id": "p4", "school_id": SCHOOL, "role": "murid", "full_name": "Dedi Kurnia"},
            {"id": "p5", "school_id": SCHOOL, "role": "murid", "full_name": "Eka Putri"},
            {"id": "p6", "school_id": SCHOOL, "role": "murid", "full_name": "Fajar Nugroho"},
        ],
        "classes": [{"id": "c1", "school_id": SCHOOL, "name": "10A", "grade_level": "10"}],
        "students": [
            {"id": pid, "school_id": SCHOOL, "class_id": "c1", "status": "active",
             "profiles": {"full_name": name},
             "classes": {"name": "10A", "grade_level": "10"}}
            for pid, name in (("p1", "Ana Wijaya"), ("p2", "Budi Santoso"),
                              ("p3", "Citra Lestari"), ("p4", "Dedi Kurnia"),
                              ("p5", "Eka Putri"), ("p6", "Fajar Nugroho"))
        ],
        "subjects": [
            {"id": "su1", "school_id": SCHOOL, "name": "Fisika", "is_active": True},
            {"id": "su2", "school_id": SCHOOL, "name": "Biologi", "is_active": True},
        ],
        "grade_component_type": [
            {"id": "gc1", "school_id": SCHOOL, "name": "Tugas", "is_active": True,
             "sort_order": 1, "default_weight": 40},
            {"id": "gc2", "school_id": SCHOOL, "name": "UTS", "is_active": True,
             "sort_order": 2, "default_weight": 60},
        ],
        # Nothing on file, so the school default binds Fisika and Biologi alike.
        "grade_weight_config": [],
        # No KKM rows either: the pass line is the app default of 70, which none of
        # these pupils is sitting on, so this file measures the movement alone.
        "subject_kkm": [],
        "exams": [
            {"id": "e1", "school_id": SCHOOL, "subject_id": "su1",
             "school_year_id": YEAR, "grade_component_type_id": "gc1"},
            {"id": "e2", "school_id": SCHOOL, "subject_id": "su1",
             "school_year_id": YEAR, "grade_component_type_id": "gc2"},
            {"id": "e0", "school_id": SCHOOL, "subject_id": "su1",
             "school_year_id": LAST_YEAR, "grade_component_type_id": "gc1"},
        ],
        "submissions": [
            {"id": "b1", "exam_id": "e1", "student_id": "p1", "score": 80,
             "is_published": True, "status": "published"},
            {"id": "b2", "exam_id": "e2", "student_id": "p1", "score": 50,
             "is_published": True, "status": "published"},
            {"id": "b3", "exam_id": "e1", "student_id": "p2", "score": 50,
             "is_published": True, "status": "published"},
            {"id": "b4", "exam_id": "e2", "student_id": "p2", "score": 80,
             "is_published": True, "status": "published"},
            {"id": "b5", "exam_id": "e1", "student_id": "p3", "score": 80,
             "is_published": False, "status": "submitted"},
            {"id": "b6", "exam_id": "e2", "student_id": "p3", "score": 50,
             "is_published": False, "status": "submitted"},
            {"id": "b7", "exam_id": "e1", "student_id": "p4", "score": 70,
             "is_published": True, "status": "published"},
            {"id": "b8", "exam_id": "e2", "student_id": "p4", "score": 60,
             "is_published": True, "status": "published"},
            {"id": "b9", "exam_id": "e1", "student_id": "p5", "score": 70,
             "is_published": True, "status": "published"},
            {"id": "b10", "exam_id": "e2", "student_id": "p5", "score": 65,
             "is_published": True, "status": "published"},
            {"id": "b11", "exam_id": "e1", "student_id": "p6", "score": 70,
             "is_published": True, "status": "published"},
            {"id": "b12", "exam_id": "e2", "student_id": "p6", "score": 67,
             "is_published": True, "status": "published"},
            {"id": "b13", "exam_id": "e0", "student_id": "p1", "score": 100,
             "is_published": True, "status": "published"},
        ],
    }


def _impact(subjects=None, weights=None, **kw) -> dict:
    return gw.class_impact(_DB(_tables()), SCHOOL,
                           FISIKA if subjects is None else subjects,
                           YEAR, TYPED if weights is None else weights, **kw)


def _names(out: dict) -> list[str]:
    return [r["name"] for r in out["reported"]["pupils"]]


# ── the mark: released, and far enough ───────────────────────────────────────

class TestTheMove:
    def test_a_released_mark_that_would_move_more_than_a_point_is_named(self):
        rows = {r["name"]: r for r in _impact()["reported"]["pupils"]}
        assert set(rows) == {"Ana Wijaya", "Budi Santoso", "Dedi Kurnia"}, (
            "the pupils whose published marks would move by more than a point are "
            "not the ones named")
        ana = rows["Ana Wijaya"]
        assert (ana["saved"], ana["live"], ana["delta"]) == (62.0, 68.0, 6.0)
        assert ana["subject_name"] == "Fisika" and ana["class_name"] == "10A", (
            "a warning an admin cannot place is a warning they cannot act on")

    def test_a_fall_is_warned_about_as_loudly_as_a_rise(self):
        rows = {r["name"]: r for r in _impact()["reported"]["pupils"]}
        assert rows["Budi Santoso"]["delta"] == -6.0, (
            "a published mark that would drop six points was not warned about")

    def test_the_biggest_move_is_first(self):
        assert _names(_impact()) == ["Ana Wijaya", "Budi Santoso", "Dedi Kurnia"], (
            "the list must lead with the largest movement, or the admin reads noise "
            "first")

    def test_an_unreleased_mark_is_a_movement_and_not_a_warning(self):
        out = _impact()
        assert "Citra Lestari" not in _names(out), (
            "an unreleased mark is work in progress; warning about it trains the "
            "admin to click past the warning")
        citra = {r["name"]: r for r in out["pupils"]}["Citra Lestari"]
        assert citra["delta"] == 6.0 and citra["released"] is False, (
            "the fixture no longer separates 'moved' from 'moved on a published mark'")

    def test_a_movement_of_exactly_one_point_is_not_more_than_a_point(self):
        out = _impact()
        assert "Eka Putri" not in _names(out), (
            "a published mark moving by exactly the limit was warned about; the "
            "threshold is 'more than', and both marks are one-decimal numbers")
        eka = {r["name"]: r for r in out["pupils"]}["Eka Putri"]
        assert eka["delta"] == 1.0, "the fixture no longer sits exactly on the limit"

    def test_a_movement_under_the_limit_is_not_a_warning(self):
        out = _impact()
        assert "Fajar Nugroho" not in _names(out)
        fajar = {r["name"]: r for r in out["pupils"]}["Fajar Nugroho"]
        assert fajar["delta"] == 0.6, (
            "the block is narrower than the movement list, and this is the row that "
            "proves it")

    def test_it_is_not_the_movement_count_wearing_a_hat(self):
        out = _impact()
        assert out["counts"]["reported"] == 5, (
            "five released marks move at all, which is what the panel already said")
        assert out["reported"]["count"] == 3, (
            "the warning must count only the ones that move far enough, or it is the "
            "number the page already showed")

    def test_a_caller_movement_threshold_cannot_hide_it(self):
        """``min_delta`` is a display floor; this warning is not a movement."""
        out = _impact(min_delta=5.0)
        assert "Dedi Kurnia" not in {r["name"] for r in out["pupils"]}, (
            "the fixture no longer hides a +2.0 movement behind a 5.0 floor")
        assert "Dedi Kurnia" in _names(out), (
            "a caller's movement threshold filtered a published mark moving two "
            "points out of the warning")

    def test_another_years_paper_is_not_weighed(self):
        ana = {r["name"]: r for r in _impact()["reported"]["pupils"]}["Ana Wijaya"]
        assert ana["saved"] == 62.0, (
            "last year's Tugas was weighed this year: her saved mark would be 66.0")

    def test_the_scope_still_decides_whether_a_pupil_is_weighed_at_all(self):
        out = _impact(subjects=BIOLOGI)
        assert out["reported"]["count"] == 0 and out["reported"]["pupils"] == [], (
            "a subject nobody has a marked paper in produced a movement")
        assert out["reported"]["checked"] == 0

    def test_retyping_the_saved_weights_warns_about_nobody(self):
        block = _impact(weights=SAVED)["reported"]
        assert block["count"] == 0 and block["pupils"] == []
        assert block["checked"] == 5, (
            "the released pairs were not even looked at, so an empty list would prove "
            "nothing")


# ── the number: one constant, in the payload, above the floor ────────────────

class TestTheThreshold:
    def test_the_limit_is_one_point_and_travels_with_the_figures(self):
        block = _impact()["reported"]
        assert block["limit"] == gw.REPORTED_MOVE_LIMIT == 1.0, (
            "the warning is measured against a number the page has to guess")

    def test_the_limit_is_an_argument_so_the_boundary_is_testable(self):
        """The fixture moves 6.0 (twice), 2.0, 1.0 and 0.6 — so three limits pin it."""
        assert _names(_impact(reported_limit=7.0)) == [], (
            "the threshold is not what the caller asked for")
        assert _names(_impact(reported_limit=6.0)) == [], (
            "'more than' is not 'at least': a movement equal to the limit was warned "
            "about")
        assert _names(_impact(reported_limit=2.0)) == ["Ana Wijaya", "Budi Santoso"], (
            "raising the limit did not drop the smallest warning")

    def test_the_denominator_counts_every_released_pair(self):
        assert _impact()["reported"]["checked"] == 5, (
            "an empty list has to read as 'none moved that far', which needs the "
            "released pairs it was tested against")

    def test_the_cap_keeps_the_biggest_move_and_the_count_does_not_shrink(self):
        block = _impact(limit=1)["reported"]
        assert block["count"] == 3 and block["shown"] == 1, (
            "the headline count must not shrink to the cap")
        assert block["pupils"][0]["name"] == "Ana Wijaya", (
            "the cap dropped the pupil whose mark moved the most")

    def test_an_empty_scope_names_nobody_and_reads_nothing(self):
        db = _DB(_tables())
        out = gw.class_impact(db, SCHOOL, [], YEAR, TYPED)
        assert out["reported"] == {"pupils": [], "count": 0, "shown": 0, "checked": 0,
                                   "limit": gw.REPORTED_MOVE_LIMIT}
        assert db.log == [], "an empty scope still read the school"

    def test_the_warning_is_a_read(self):
        db = _DB(_tables())
        gw.class_impact(db, SCHOOL, FISIKA, YEAR, TYPED)
        assert db.log, "the impact read nothing at all"
        assert {op for op, _t, _p, _f in db.log} == {"select"}, (
            "the reported-mark warning writes; it must only read")


# ── the page: warned before saving, and the pupils named ─────────────────────

def _html() -> str:
    return WEIGHTS_HTML.read_text(encoding="utf-8")


def _impact_block() -> str:
    html = _html()
    assert "data-preview-impact" in html, "the page has no impact panel"
    return html.split("data-preview-impact", 1)[1].split("</table>", 1)[0]


def _banner() -> str:
    """The warning's **own** element.

    The panel block runs on into the movement table, which has `row.saved` and
    `row.live` columns of its own — so a guard scoped to the panel is satisfied by
    the table below the banner even with the banner emptied. Everything that is
    about the warning is read here.
    """
    html = _html()
    at = html.index("data-impact-reported")
    return html[html.rindex("<div", 0, at):].split("</div>", 1)[0]


def _method(header: str) -> str:
    return _html().split(header, 1)[1].split("\n        },", 1)[0]


# ── the check, as the browser sees it ────────────────────────────────────────

HARNESS = r"""
const fs = require('fs');
const M = eval('({' + fs.readFileSync(process.argv[2], 'utf8') + '})');

const ROWS = [
  {student_id: 'p1', subject_id: 'su1', name: 'Ana Wijaya', class_name: '10A',
   subject_name: 'Fisika', saved: 62.0, live: 68.0, delta: 6.0},
  {student_id: 'p2', subject_id: 'su1', name: 'Budi Santoso', class_name: '10A',
   subject_name: 'Fisika', saved: 68.0, live: 62.0, delta: -6.0},
];
// The shape the door hands Alpine: the whole impact payload, of which the check
// reads `reported`.
const OVER = {reported: {pupils: ROWS, count: 2, shown: 2, checked: 5, limit: 1.0}};
const CLEAR = {reported: {pupils: [], count: 0, shown: 0, checked: 5, limit: 1.0}};

let dialogs = [];
let answer = true;
globalThis.confirm = (text) => { dialogs.push(text); return answer; };

// Only the network is faked: the method under test, and the URL it asks for, are
// the page's own.
function fresh(payload, opts) {
  const o = Object.assign({
    lang: (opts && opts.lang) || 'en',
    impact: payload, impactDone: true, impactSignature: '', impactLoading: false,
    impactNote: '',
  }, M);
  o.loads = 0;
  o.fails = !!(opts && opts.fails);
  o.loadImpact = function (url) {
    o.loads += 1;
    if (o.fails) { return Promise.resolve(false); }
    o.impact = payload; o.impactSignature = url; o.impactDone = true;
    return Promise.resolve(true);
  };
  return o;
}

(async () => {
  const out = {};
  out.extracted = {url: typeof M.impactUrl, check: typeof M.confirmReportedMoves};

  // 1. nothing over the limit: no dialog, and the figures on screen already answer
  //    for this policy, so the check reads nothing.
  dialogs = []; answer = true;
  const a = fresh(CLEAR, {});
  a.impactSignature = a.impactUrl('default', null, {});
  out.clear = {answer: await a.confirmReportedMoves('default', null, {}),
               dialog: dialogs.length, loads: a.loads};

  // 2. the pupils are named, and the two halves of every movement are printed.
  dialogs = []; answer = true;
  const b = fresh(OVER, {});
  b.impactSignature = b.impactUrl('default', null, {});
  const named = await b.confirmReportedMoves('default', null, {});
  out.named = {answer: named, dialog: dialogs.length, text: dialogs[0] || ''};
  // …and asking again for the same policy costs no second read.
  const before = b.loads;
  await b.confirmReportedMoves('default', null, {});
  out.reused = {loads: b.loads - before};

  // 3. the admin can refuse it
  dialogs = []; answer = false;
  const c = fresh(OVER, {});
  c.impactSignature = c.impactUrl('default', null, {});
  out.cancelled = {answer: await c.confirmReportedMoves('default', null, {}),
                   dialog: dialogs.length};

  // 4. a read that failed is not a lock on the page
  dialogs = []; answer = false;
  const d = fresh(OVER, {fails: true});
  out.readFailed = {answer: await d.confirmReportedMoves('default', null, {}),
                    dialog: dialogs.length, loads: d.loads};

  // 5. the point comes out of the payload
  dialogs = []; answer = true;
  const e = fresh({reported: Object.assign({}, OVER.reported, {limit: 2.5})}, {});
  e.impactSignature = e.impactUrl('default', null, {});
  await e.confirmReportedMoves('default', null, {});
  out.limitFromPayload = {text: dialogs[0] || ''};

  // 6. the reader's language decides which half is shown
  dialogs = []; answer = true;
  const f = fresh(OVER, {lang: 'id'});
  f.impactSignature = f.impactUrl('default', null, {});
  await f.confirmReportedMoves('default', null, {});
  out.indonesian = {text: dialogs[0] || ''};

  console.log(JSON.stringify(out));
})();
"""


def _slice(text: str, start: str, end: str) -> str:
    """The template's own code between two anchors — never a rewrite of it."""
    i = text.index(start)
    j = text.index(end, i)
    assert j > i
    return text[i:j]


def _t_pair() -> str:
    """The mixin's own `t(id, en)` from base.html — the real one, not a copy."""
    for line in BASE_HTML.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("t(id, en)"):
            return line.strip() + "\n"
    raise AssertionError("base.html no longer defines t(id, en)")


def _run_check(tmp_path: Path) -> dict:
    """Run the real `impactUrl` and `confirmReportedMoves` under node."""
    methods = (_slice(_html(), "        impactUrl(scope, subjectId, weights) {",
                      "        loadImpact(targetUrl) {")
               + _slice(_html(),
                        "        async confirmReportedMoves(scope, subjectId, weights) {",
                        "        impactStale() {")
               + _t_pair())
    (tmp_path / "methods.js").write_text(methods, encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    done = subprocess.run([NODE, str(tmp_path / "harness.js"), str(tmp_path / "methods.js")],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


class TestThePageWarnsBeforeSaving:
    def test_the_panel_names_the_pupils_whose_published_mark_moves(self):
        block = _impact_block()
        assert "data-impact-reported" in block, (
            "the panel never mentions a movement on an already-reported mark")
        banner = _banner()
        assert 'x-for="row in impact.reported.pupils"' in banner, (
            "the warning names no pupils")
        for field in ("row.name", "row.saved", "row.live", "row.delta"):
            assert field in banner, f"the warning does not show {field}"
        assert "row.class_name" in banner and "row.subject_name" in banner, (
            "the warning must say which pupil, which class and which subject")

    def test_the_panel_shows_the_direction_as_well_as_the_size(self):
        assert "row.delta > 0 ? '+' : ''" in _banner(), (
            "a rise and a fall would read the same on the page")

    def test_the_panel_reads_the_threshold_out_of_the_payload(self):
        banner = _banner()
        assert "impact.reported.limit" in banner, (
            "the page promises a threshold it does not count with")
        assert "lebih dari satu" not in banner and "more than one" not in banner, (
            "the point is typed into the copy instead of read from the payload")

    def test_the_reported_warning_is_its_own_block(self):
        block = _impact_block()
        assert "impact.reported.count > 0" in block, (
            "the panel would show the block on a page with nothing to warn about")
        assert "impact.kkm.count > 0" in block, (
            "the KKM warning was replaced instead of joined")

    def test_the_state_carries_the_block_so_the_panel_can_render(self):
        assert "reported: {pupils: [], count: 0, shown: 0, checked: 0, limit: 0}" in _html(), (
            "impact.reported is undefined until a read, so the panel would throw on "
            "load")

    def test_a_save_asks_for_the_policy_it_is_about_to_write(self):
        html = _html()
        assert "this.confirmReportedMoves('default', null, this.defaults)" in html, (
            "saving the default is not checked against the default")
        assert "this.confirmReportedMoves('subject', subjectId" in html, (
            "saving a subject row is not checked against that row")

    def test_the_kkm_check_still_runs_on_both_paths(self):
        html = _html()
        for check in ("this.confirmKkm('default', null, this.defaults)",
                      "this.confirmKkm('subject', subjectId"):
            assert check in html, (
                f"the second warning replaced the first: {check} is gone")

    def test_the_check_names_the_pupils_and_lets_the_admin_stop(self):
        body = _method("confirmReportedMoves(scope, subjectId, weights) {")
        assert "r.name" in body, "the confirmation does not name the pupils it is about"
        assert "r.subject_name" in body and "r.live" in body and "r.saved" in body, (
            "the confirmation must say which subject and where the mark lands")
        assert "confirm(" in body, "there is no way for the admin to stop"
        assert "Tetap simpan?" in body and "Save anyway?" in body, (
            "the confirmation lost its bilingual question")

    def test_the_check_reads_the_threshold_from_the_payload(self):
        body = _method("confirmReportedMoves(scope, subjectId, weights) {")
        assert "block.limit" in body, (
            "the dialog names a threshold it did not read, so it can drift from the "
            "warning on the page")

    def test_the_check_is_a_read_that_reuses_the_figures_it_already_has(self):
        body = _method("confirmReportedMoves(scope, subjectId, weights) {")
        assert "post(" not in body, "the save check writes to the server"
        assert "impactUrl(" in body, (
            "the check asks a different question than the panel it shares figures with")
        assert "impactSignature !== url" in body, (
            "the figures are recomputed even when they already answer for this policy")
        assert body.count("await this.loadImpact") == 1, (
            "the policy being saved must be read once, not per warned pupil")

    def test_the_warning_reaches_the_page_through_the_real_door(self, as_admin):
        """The panel is markup the server renders, so one request has to carry it."""
        page = as_admin.get("/admin-sekolah/grade-weights")
        assert page.status_code == 200, page.status_code
        html = page.get_data(as_text=True)
        assert "data-impact-reported" in html, (
            "the warning never reaches the page an admin opens")
        # The banner's own element, attributes included: the marker sits after the
        # `x-show` that decides whether it paints, so the slice starts at the tag.
        head = html[:html.index("data-impact-reported")]
        rendered = html[head.rindex("<div"):].split("</div>", 1)[0]
        assert 'x-show="impact.reported.count > 0"' in rendered, (
            "the banner renders unconditionally, so it would show on an empty panel")
        assert 'x-for="row in impact.reported.pupils"' in rendered, (
            "Jinja ate the binding that lists the pupils")
        assert "impact.reported.limit" in rendered, (
            "the threshold stopped travelling to the page")
        assert "<script" not in rendered, (
            "Jinja escaped the markup into the page as text")

    # ── the check, executed ─────────────────────────────────────────────

    @needs_node
    def test_the_check_decides_what_the_page_says(self, tmp_path):
        """The four answers the save depends on, from the shipped method."""
        out = _run_check(tmp_path)

        assert out["extracted"] == {"url": "function", "check": "function"}, (
            "the harness is not running the page's own methods")

        # Nothing over the limit: no dialog at all, and the save goes ahead. The
        # figures on screen already answer for this policy, so nothing is re-read.
        assert out["clear"] == {"answer": True, "dialog": 0, "loads": 0}, out["clear"]

        named = out["named"]
        assert named["answer"] is True and named["dialog"] == 1, (
            "a save of a policy that moves published marks by more than a point "
            "went through without asking")
        for piece in ("Ana Wijaya", "Budi Santoso", "10A", "Fisika",
                      "62.0", "68.0", "+6.0", "-6.0"):
            assert piece in named["text"], (
                f"the dialog does not name {piece!r}, so the admin cannot tell which "
                "paper to look at")
        assert "point(s)" in named["text"] and "Save anyway?" in named["text"]

        assert out["reused"]["loads"] == 0, (
            "the same policy was read a second time for the second question")
        assert out["cancelled"] == {"answer": False, "dialog": 1}, (
            "a refused dialog did not stop the save")
        assert out["readFailed"] == {"answer": True, "dialog": 0, "loads": 1}, (
            "a preview that could not load became a lock on the page")

    @needs_node
    def test_the_point_in_the_dialog_is_the_payloads_own(self, tmp_path):
        out = _run_check(tmp_path)
        assert "2.5" in out["limitFromPayload"]["text"], (
            "the dialog names a threshold the payload did not carry")
        assert "1.0" not in out["limitFromPayload"]["text"]

    @needs_node
    def test_the_default_language_is_the_indonesian_half(self, tmp_path):
        out = _run_check(tmp_path)
        text = out["indonesian"]["text"]
        assert "Bobot ini menggeser nilai akhir yang sudah terbit lebih dari" in text
        assert "Tetap simpan?" in text, "the early return skips the reader's language"

    def test_the_warning_copy_is_bilingual(self):
        banner = _banner()
        for pair in ("t(' nilai yang sudah terbit bergerak lebih dari ',",
                     "t(' poin:',' point(s):')"):
            assert pair in banner, f"the warning lost its bilingual pair: {pair}"
        body = _method("confirmReportedMoves(scope, subjectId, weights) {")
        for pair in ("'These weights would move already-reported final marks by more than '",
                     "this.t('Tetap simpan?', 'Save anyway?')"):
            assert pair in body, f"the confirmation lost its bilingual pair: {pair}"
