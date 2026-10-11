"""A distribution that would drop an already-reported mark below the KKM.

`test_grade_weight_impact.py` pinned what a weight change does to the class: who
moves and by how much. A movement, though, is not the same question as a **fail**.
A pupil whose mark was published at 76 and would be recomputed to 74 has lost two
points; a pupil in a grade whose KKM is 75 has just been turned from pass to fail
on a number their parents already saw. The second is the one an admin must be
stopped for, and a count that says "one pupil" is not enough to act on — the pupil
has to be named.

This file pins the pass-line warning in the four places it can mislead:

* **the resolver** (`subject_kkm.resolve_with_source`) — a grade override wins, the
  subject's general mark is the fallback, and a subject with nothing on file reads
  the app default **and says so**, so a page never presents the default as a
  standard the school chose;
* **the crossing** — only a mark that is **released**, that reads as passing today
  and would read as failing after the save. An already-failing mark is not a new
  finding, an unreleased mark is work in progress, and the pupil's own grade level
  decides which KKM applies;
* **the arithmetic's provenance** — the line comes from `subject_kkm`, read once and
  bounded by the school and the year, and the crossing is tested before any caller's
  movement threshold, so a small pass-to-fail is never filtered away;
* **the door and the page** — the warning rides on the same read-only impact read,
  the panel names the pupils, and each save asks for the figures of the policy it is
  about to write before it writes it.
"""
from __future__ import annotations

from pathlib import Path

from app.services import grade_weighting as gw
from app.services import subject_kkm as kkm
from tests.unit.test_invigilation import _DB

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "app" / "routes" / "admin_sekolah.py"
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"

SCHOOL = "s1"
YEAR = "y1"

#: The saved school default: Tugas 40%, UTS 60%.
SAVED = {"gc1": 40, "gc2": 60}
#: The same default retyped the other way round, which is the change under test.
TYPED = {"gc1": 60, "gc2": 40}

FISIKA = [{"subject_id": "su1", "name": "Fisika"}]
BIOLOGI = [{"subject_id": "su2", "name": "Biologi"}]


def _tables() -> dict:
    """One school, two subjects, and marks that mislead if read wrong.

    Under the saved default (40/60) and the typed one (60/40), by hand:

    * **Ana** (grade 9, Fisika) — Tugas 70, UTS 80: **76.0 → 74.0**, and grade 9
      passes Fisika at **75**. A released pass that becomes a fail;
    * **Budi** (grade 7, same marks) — 76.0 → 74.0 as well, but grade 7 has no
      override so its pass mark is the subject's general **70**: the same movement,
      and *not* a fail. That difference is the proof the level was read;
    * **Citra** (grade 9, same marks, unreleased) — would cross exactly as Ana does;
    * **Dedi** (grade 9) — Tugas 50 only: 20.0 → 30.0, already below the line and
      *rising*, so it is a movement and not a new fail;
    * **Eka** (grade 7) — Tugas 69.7, UTS 70.2: 70.0 → 69.9, a fail by a tenth,
      under the movement threshold a caller may set;
    * **Ana in Biologi** — Tugas 65, UTS 74: 70.4 → 68.6, against a subject with no
      KKM on file, so the line is the app default of 70.
    """
    return {
        "schools": [{"id": SCHOOL}],
        "profiles": [
            {"id": "p1", "school_id": SCHOOL, "role": "murid", "full_name": "Ana Wijaya"},
            {"id": "p2", "school_id": SCHOOL, "role": "murid", "full_name": "Budi Santoso"},
            {"id": "p3", "school_id": SCHOOL, "role": "murid", "full_name": "Citra Lestari"},
            {"id": "p4", "school_id": SCHOOL, "role": "murid", "full_name": "Dedi Kurnia"},
            {"id": "p5", "school_id": SCHOOL, "role": "murid", "full_name": "Eka Putri"},
        ],
        "classes": [
            {"id": "c9", "school_id": SCHOOL, "name": "9A", "grade_level": "9"},
            {"id": "c7", "school_id": SCHOOL, "name": "7A", "grade_level": "7"},
        ],
        "students": [
            {"id": "p1", "school_id": SCHOOL, "class_id": "c9", "status": "active",
             "profiles": {"full_name": "Ana Wijaya"},
             "classes": {"name": "9A", "grade_level": "9"}},
            {"id": "p2", "school_id": SCHOOL, "class_id": "c7", "status": "active",
             "profiles": {"full_name": "Budi Santoso"},
             "classes": {"name": "7A", "grade_level": "7"}},
            {"id": "p3", "school_id": SCHOOL, "class_id": "c9", "status": "active",
             "profiles": {"full_name": "Citra Lestari"},
             "classes": {"name": "9A", "grade_level": "9"}},
            {"id": "p4", "school_id": SCHOOL, "class_id": "c9", "status": "active",
             "profiles": {"full_name": "Dedi Kurnia"},
             "classes": {"name": "9A", "grade_level": "9"}},
            {"id": "p5", "school_id": SCHOOL, "class_id": "c7", "status": "active",
             "profiles": {"full_name": "Eka Putri"},
             "classes": {"name": "7A", "grade_level": "7"}},
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
        # Grade 9 passes Fisika at 75; every other grade at the subject's general 70.
        # Biologi has nothing on file at all.
        "subject_kkm": [
            {"id": "k1", "school_id": SCHOOL, "subject_id": "su1",
             "school_year_id": YEAR, "grade_level": "9", "kkm": 75},
            {"id": "k2", "school_id": SCHOOL, "subject_id": "su1",
             "school_year_id": YEAR, "grade_level": None, "kkm": 70},
        ],
        "exams": [
            {"id": "e1", "school_id": SCHOOL, "subject_id": "su1",
             "school_year_id": YEAR, "grade_component_type_id": "gc1"},
            {"id": "e2", "school_id": SCHOOL, "subject_id": "su1",
             "school_year_id": YEAR, "grade_component_type_id": "gc2"},
            {"id": "e3", "school_id": SCHOOL, "subject_id": "su2",
             "school_year_id": YEAR, "grade_component_type_id": "gc1"},
            {"id": "e4", "school_id": SCHOOL, "subject_id": "su2",
             "school_year_id": YEAR, "grade_component_type_id": "gc2"},
        ],
        "submissions": [
            {"id": "b1", "exam_id": "e1", "student_id": "p1", "score": 70,
             "is_published": True, "status": "published"},
            {"id": "b2", "exam_id": "e2", "student_id": "p1", "score": 80,
             "is_published": True, "status": "published"},
            {"id": "b3", "exam_id": "e1", "student_id": "p2", "score": 70,
             "is_published": True, "status": "published"},
            {"id": "b4", "exam_id": "e2", "student_id": "p2", "score": 80,
             "is_published": True, "status": "published"},
            {"id": "b5", "exam_id": "e1", "student_id": "p3", "score": 70,
             "is_published": False, "status": "submitted"},
            {"id": "b6", "exam_id": "e2", "student_id": "p3", "score": 80,
             "is_published": False, "status": "submitted"},
            {"id": "b7", "exam_id": "e1", "student_id": "p4", "score": 50,
             "is_published": True, "status": "published"},
            {"id": "b8", "exam_id": "e1", "student_id": "p5", "score": 69.7,
             "is_published": True, "status": "published"},
            {"id": "b9", "exam_id": "e2", "student_id": "p5", "score": 70.2,
             "is_published": True, "status": "published"},
            {"id": "b10", "exam_id": "e3", "student_id": "p1", "score": 65,
             "is_published": True, "status": "published"},
            {"id": "b11", "exam_id": "e4", "student_id": "p1", "score": 74,
             "is_published": True, "status": "published"},
        ],
    }


def _impact(subjects=None, weights=None, **kw) -> dict:
    return gw.class_impact(_DB(_tables()), SCHOOL,
                           FISIKA if subjects is None else subjects,
                           YEAR, TYPED if weights is None else weights, **kw)


def _by_name(out: dict) -> dict:
    return {r["name"]: r for r in out["kkm"]["pupils"]}


# ── the resolver: one order, and where the mark came from ────────────────────

class TestTheResolver:
    def test_a_grade_override_wins_over_the_subjects_general_mark(self):
        rows = [{"subject_id": "su1", "grade_level": None, "kkm": 70},
                {"subject_id": "su1", "grade_level": "9", "kkm": 75}]
        assert kkm.resolve_with_source(rows, "su1", "9") == (75, "grade")
        assert kkm.resolve_with_source(rows, "su1", 9) == (75, "grade"), (
            "the numeric level and the text level must be the same grade")

    def test_a_level_without_an_override_reads_the_subjects_general_mark(self):
        rows = [{"subject_id": "su1", "grade_level": None, "kkm": 70},
                {"subject_id": "su1", "grade_level": "9", "kkm": 75}]
        assert kkm.resolve_with_source(rows, "su1", "7") == (70, "subject")

    def test_a_subject_with_nothing_on_file_reads_the_default_and_says_so(self):
        assert kkm.resolve_with_source([], "su1", "9") == (kkm.DEFAULT_KKM, None), (
            "a missing mark must read as the app default and be labelled as such, "
            "never as a standard the school chose")
        rows = [{"subject_id": "su1", "grade_level": None, "kkm": 70}]
        assert kkm.resolve_with_source(rows, "su2", "9") == (kkm.DEFAULT_KKM, None), (
            "another subject's mark was applied to this one")

    def test_effective_resolves_through_the_same_order(self):
        """The single-subject read must agree with the class-wide one."""
        db = _DB(_tables())
        assert kkm.effective(db, SCHOOL, "su1", grade_level="9", year_id=YEAR) == 75
        assert kkm.effective(db, SCHOOL, "su1", grade_level="7", year_id=YEAR) == 70
        assert kkm.effective(db, SCHOOL, "su2", grade_level="9", year_id=YEAR) == 70


# ── the crossing: a released pass that would become a fail ───────────────────

class TestTheCrossing:
    def test_a_released_mark_that_would_fall_below_the_kkm_is_named(self):
        row = _by_name(_impact())["Ana Wijaya"]
        assert (row["saved"], row["live"], row["kkm"]) == (76.0, 74.0, 75)
        assert row["delta"] == -2.0
        assert row["subject_name"] == "Fisika" and row["class_name"] == "9A", (
            "a warning an admin cannot place is a warning they cannot act on")

    def test_the_pupils_own_grade_level_decides_which_kkm_applies(self):
        out = _impact()
        assert "Ana Wijaya" in _by_name(out), (
            "a grade-9 mark was not measured against grade 9's KKM of 75")
        assert "Budi Santoso" not in _by_name(out), (
            "grade 7 was measured against grade 9's KKM: the same 76.0 -> 74.0 moved, "
            "but 70 is the pass mark there")

    def test_an_unreleased_mark_is_not_a_warning(self):
        assert "Citra Lestari" not in _by_name(_impact()), (
            "an unreleased mark is work in progress; warning about it trains the "
            "admin to click past the warning")

    def test_an_already_failing_mark_is_not_a_new_fail(self):
        out = _impact()
        assert "Dedi Kurnia" not in _by_name(out), (
            "a mark already below the line was reported as a fall below it")
        assert out["counts"]["moved"] >= 1, "the movement is still reported as a movement"

    def test_an_app_default_kkm_is_reported_as_the_apps_and_not_the_schools(self):
        row = _by_name(_impact(subjects=BIOLOGI))["Ana Wijaya"]
        assert (row["saved"], row["live"]) == (70.4, 68.6)
        assert row["kkm"] == 70 and row["source"] is None, (
            "Biologi has no KKM on file, so the line is the app default of 70 — and "
            "the payload must say so rather than imply the school chose it")

    def test_a_crossing_is_reported_below_the_movement_threshold(self):
        out = _impact(min_delta=0.5)
        assert "Eka Putri" in _by_name(out), (
            "a 70.0 -> 69.9 fail was hidden by a movement threshold it never crossed")
        assert "Eka Putri" not in {r["name"] for r in out["pupils"]}, (
            "the fixture no longer separates 'moved' from 'would fail'")

    def test_the_counts_say_how_many_released_pairs_were_tested(self):
        block = _impact()["kkm"]
        assert block["count"] == 2 and block["shown"] == 2
        assert block["checked"] == 4, (
            "the denominator must count every released pair, so an empty list reads "
            "as 'nothing crosses' rather than 'nothing was looked at'")

    def test_the_cap_keeps_the_worst_fall_and_the_count_does_not_shrink(self):
        block = _impact(limit=1)["kkm"]
        assert block["count"] == 2 and block["shown"] == 1, (
            "the headline count must not shrink to the cap")
        assert block["pupils"][0]["name"] == "Ana Wijaya", (
            "the cap dropped the pupil furthest below the line")

    def test_retyping_the_saved_weights_crosses_nothing(self):
        block = _impact(weights=SAVED)["kkm"]
        assert block["count"] == 0 and block["pupils"] == []
        assert block["checked"] == 4, "the released marks were not even looked at"

    def test_an_empty_scope_crosses_nothing_and_reads_nothing(self):
        db = _DB(_tables())
        out = gw.class_impact(db, SCHOOL, [], YEAR, TYPED)
        assert out["kkm"] == {"pupils": [], "count": 0, "shown": 0, "checked": 0}
        assert db.log == [], "an empty scope still read the school"

    def test_the_kkm_read_is_bounded_by_the_school_and_the_year(self):
        src = (ROOT / "app" / "services" / "grade_weighting.py").read_text(encoding="utf-8")
        body = src.split("def class_impact(")[1].split("\ndef ")[0]
        assert "list_kkm(" in body and "year_id=year_id" in body, (
            "another year's KKM rows would decide this year's pass line")
        assert "resolve_with_source(" in body, (
            "the crossing invented a resolution instead of using the module's own")

    def test_the_warning_is_a_read(self):
        db = _DB(_tables())
        gw.class_impact(db, SCHOOL, FISIKA, YEAR, TYPED)
        assert db.log, "the impact read nothing at all"
        assert {op for op, _t, _p, _f in db.log} == {"select"}, (
            "the KKM warning writes; it must only read")


# ── the door ─────────────────────────────────────────────────────────────────

class TestTheDoor:
    def test_the_kkm_block_travels_out_through_the_impact_door(self):
        src = ADMIN.read_text(encoding="utf-8-sig")
        block = src.split("def admin_grade_class_impact(")[1]\
            .split("\n@admin_sekolah_bp.route")[0]
        assert "class_impact(" in block, "the door no longer computes the impact"
        assert "**grade_weighting.class_impact(" in block, (
            "the KKM block must be forwarded with the payload, not dropped")
        for writer in (".insert(", ".update(", ".delete("):
            assert writer not in block, f"the impact door writes ({writer})"


# ── the page: warned before saving, and the pupils named ─────────────────────

def _html() -> str:
    return WEIGHTS_HTML.read_text(encoding="utf-8")


def _impact_block() -> str:
    html = _html()
    assert "data-preview-impact" in html, "the page has no impact panel"
    return html.split("data-preview-impact", 1)[1].split("</table>", 1)[0]


def _method(header: str) -> str:
    return _html().split(header, 1)[1].split("\n        },", 1)[0]


class TestThePageWarnsBeforeSaving:
    def test_the_panel_lists_the_pupils_who_would_fail(self):
        block = _impact_block()
        assert "data-impact-kkm" in block, "the panel never mentions the KKM line"
        assert 'x-for="row in impact.kkm.pupils"' in block, "the crossing names no pupils"
        assert "row.name" in block and "row.kkm" in block, (
            "the warning must name the pupil and the mark they are measured against")
        assert "row.saved" in block and "row.live" in block, (
            "the warning must show where the mark lands, not only that it moved")

    def test_the_warning_says_when_the_kkm_is_the_apps_default(self):
        block = _impact_block()
        assert "row.source" in block and "KKM bawaan aplikasi" in block, (
            "an app-default pass mark would read as the school's own choice")

    def test_the_state_carries_the_kkm_block_so_the_panel_can_render(self):
        assert "kkm: {pupils: [], count: 0, shown: 0, checked: 0}" in _html(), (
            "impact.kkm is undefined until a read, so the panel would throw on load")

    def test_a_save_asks_for_the_policy_it_is_about_to_write(self):
        html = _html()
        assert "this.confirmKkm('default', null, this.defaults)" in html, (
            "saving the default is not checked against the default")
        assert "this.confirmKkm('subject', subjectId" in html, (
            "saving a subject row is not checked against that row")

    def test_the_check_names_the_pupils_and_lets_the_admin_stop(self):
        body = _method("confirmKkm(scope, subjectId, weights) {")
        assert "r.name" in body, "the confirmation does not name the pupils it is about"
        assert "confirm(" in body, "there is no way for the admin to stop"
        assert "r.live" in body and "r.subject_name" in body, (
            "the confirmation must say which subject and where the mark lands")

    def test_the_check_is_a_read_that_reuses_the_figures_it_already_has(self):
        body = _method("confirmKkm(scope, subjectId, weights) {")
        assert "post(" not in body, "the save check writes to the server"
        assert "impactUrl(" in body, (
            "the check asks a different question than the panel it shares figures with")
        assert "impactSignature !== url" in body, (
            "the figures are recomputed even when they already answer for this policy")

    def test_the_door_is_asked_once_for_the_policy_not_per_pupil(self):
        body = _method("confirmKkm(scope, subjectId, weights) {")
        assert body.count("await this.loadImpact") == 1, (
            "the policy being saved must be read once, not once per bridging pupil")

    def test_the_warning_copy_is_bilingual(self):
        block = _impact_block()
        for pair in ("t('KKM bawaan aplikasi','app default KKM')",
                     "t(' nilai yang sudah terbit akan turun di bawah KKM:',"):
            assert pair in block, f"the KKM warning lost its bilingual pair: {pair}"
        body = _method("confirmKkm(scope, subjectId, weights) {")
        for pair in ("this.t('Tetap simpan?', 'Save anyway?')",
                     "this.t('KKM','KKM')"):
            assert pair in body, f"the confirmation lost its bilingual pair: {pair}"
