"""One arithmetic for a final mark — and one place each result sheet states it.

Two defects this file measures.

**The arithmetic.** ``final = max(0, min(raw, 100) - penalty)`` was written out by
hand in four places besides `app/services/final_score.py`: the submit door, the
force-submit door, the recalculation pass, and the clock that ends a paper the
student never sent. They agreed only until one of them was edited — three of the
four omitted the 100 cap the fourth applied, so a "recalculated" paper could come
back with a number no report would ever show. Each calls the one function now.

**The duplicate.** A result sheet said the final mark *twice* — a headline box plus
a row/strip cell carrying the same value — so the one figure a reader checks was the
one figure printed in two places. The headline keeps it; the row shows the working
that produced it (raw, penalty, and how much of the recorded log the penalty came
from), in the vocabulary the teacher's review panel already uses.
"""
import re
from pathlib import Path

from app.services import final_score
from app.services.final_score import breakdown, display_parts, penalty_breakdown

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
TEMPLATES = APP / "templates"

#: Writes the final-mark arithmetic inline — what must not come back.
LEGACY = re.compile(r"round\((?:earned|raw|final)\b[^)]*-\s*(?:penalty|total_penalty)")

#: The modules that used to carry it, and the wrapper each must now call.
ROUTED = (
    "routes/api.py",
    "routes/student.py",
    "services/deadline_service.py",
)


# ── the arithmetic is one function and nobody rewrites it ────────────────────

def _sources():
    for path in APP.rglob("*.py"):
        yield path.relative_to(APP).as_posix(), path.read_text(encoding="utf-8")


def test_no_module_writes_the_final_arithmetic_by_hand():
    offenders = [rel for rel, src in _sources() if LEGACY.search(src)]
    assert offenders == [], (
        "these modules compute the final mark instead of calling final_score: "
        + ", ".join(offenders))


def test_the_four_doors_route_through_the_central_function():
    for rel in ROUTED:
        src = (APP / rel).read_text(encoding="utf-8")
        assert "final_breakdown(" in src, f"{rel} no longer calls the shared arithmetic"


def test_the_central_function_is_where_the_arithmetic_lives():
    src = (APP / "services" / "final_score.py").read_text(encoding="utf-8")
    assert "max(0.0, raw - penalty)" in src


# ── the four scenarios the formula has to answer ─────────────────────────────

def _events(count, ignored=()):
    return [{"id": str(i), "violation_type": "tab_switch",
             "status": "ignored" if i in ignored else "applied"}
            for i in range(count)]


def _picture(raw, events, base=5):
    part = penalty_breakdown(events, base=base)
    return breakdown(raw, part["total"]), part


def test_no_violation_leaves_the_mark_alone():
    parts, part = _picture(80.0, [])
    assert part["total"] == 0 and part["applied"] == 0 and part["recorded"] == 0
    assert parts["final"] == 80.0


def test_all_applied_is_the_ordinary_ladder():
    # 1st = warning(0), 2nd = 5, 3rd = 10, 4th = 15  -> 30
    parts, part = _picture(80.0, _events(4))
    assert part["applied"] == 4 and part["total"] == 30.0
    assert parts["final"] == 50.0


def test_ignoring_one_forgives_exactly_that_charge():
    # The ignored event is the 2nd, worth 5; the 3rd and 4th keep their own price.
    parts, part = _picture(80.0, _events(4, ignored={1}))
    assert part["applied"] == 3 and part["ignored"] == 1
    assert part["total"] == 25.0, "forgiving one charge must not re-level the rest"
    assert parts["final"] == 55.0


def test_ignoring_all_restores_the_raw_score():
    parts, part = _picture(80.0, _events(4, ignored={0, 1, 2, 3}))
    assert part["applied"] == 0 and part["total"] == 0.0
    assert parts["final"] == 80.0


def test_the_mark_is_capped_at_100_and_never_negative():
    assert _picture(120.0, [])[0]["raw"] == 100.0
    assert _picture(3.0, _events(4))[0]["final"] == 0.0, "a penalty may erase, not invert"


def test_a_paper_with_anti_cheat_off_charges_nothing():
    part = penalty_breakdown(_events(4), base=5, enabled=False)
    assert part["total"] == 0.0, "with anti-cheat off every charge is priced at zero"
    assert part["recorded"] == 4, "the events are still recorded — nothing is forgotten"


# ── the display reading, for a surface that has only the row ─────────────────

def test_display_parts_inverts_the_stored_arithmetic():
    parts = display_parts({"final_score": 70.0, "penalty": 5.0, "violations": 3})
    assert parts["raw"] == 75.0 and parts["penalty"] == 5.0 and parts["final"] == 70.0
    assert parts["recorded"] == 3 and parts["applied"] == 3 and parts["ignored"] == 0


def test_display_parts_falls_back_to_score_when_final_is_absent():
    parts = display_parts({"final_score": None, "score": 60.0, "penalty": 4.0,
                           "violations": 1})
    assert parts["final"] == 60.0 and parts["raw"] == 64.0


def test_display_parts_reports_a_floored_paper_without_inventing_a_raw():
    parts = display_parts({"final_score": 0.0, "penalty": 30.0, "violations": 4})
    assert parts["final"] == 0.0 and parts["raw"] == 30.0, "the bound, never a guess"


def test_display_parts_survives_a_shape_it_did_not_expect():
    assert display_parts(None)["final"] == 0.0
    assert display_parts({})["raw"] == 0.0


# ── the surfaces state the final once ────────────────────────────────────────

EXAM = {
    "id": "e1", "title": "Ujian Matematika", "subject": None, "subject_id": "s1",
    "answer_key": {}, "question_types": {}, "question_weights": {},
    "total_questions": 0, "pdf_page_urls": [], "question_scoring": {},
}


def _pupil_submission():
    return {"id": "sub-1", "exam_id": "e1", "student_id": "murid-1",
            "exam": dict(EXAM), "answers": {}, "teacher_feedback": {},
            "status": "published", "is_published": True,
            "final_score": 63.5, "score": 60.0, "penalty": 5.0, "violations": 4}


def test_the_printed_pdf_states_the_final_once_and_shows_the_working(app):
    parts = {"raw": 68.5, "penalty": 5.0, "final": 63.5,
             "recorded": 4, "applied": 3, "ignored": 1}
    html = app.jinja_env.get_template("student/result_detail_pdf.html").render(
        submission=_pupil_submission(), student_name="Ahmad", teacher_name="",
        school_info={}, merged_pages={}, parts=parts)

    assert "None" not in html, "the word `None` reached the sheet"
    # The duplicate cell is gone; the headline still states it, exactly once.
    assert "NILAI AKHIR" not in html, "the row still repeats the final mark"
    assert html.count('score-label">Nilai Akhir') == 1
    # The working is present, and it is the route's picture rather than a re-reading.
    assert "SKOR MENTAH" in html and "TOTAL PENALTI" in html
    assert "68.5" in html and "3 / 4" in html


def test_the_reported_numbers_survive_a_surface_that_only_has_the_row(app):
    """No `parts` passed: the sheet reads the stored row rather than blanking."""
    html = app.jinja_env.get_template("student/result_detail_pdf.html").render(
        submission=_pupil_submission(), student_name="Ahmad", teacher_name="",
        school_info={}, merged_pages={})
    assert "None" not in html
    assert "68.5" in html, "raw = final + penalty when the route does not pass it"


def test_the_report_card_drops_the_duplicate_final(app):
    card = {"submission": _pupil_submission(), "exam": dict(EXAM), "released": True,
            "student_name": "Ahmad", "student_nisn": "1", "student_nis": "1",
            "teacher_name": "", "school": {"name": "SMP 1"}}
    html = app.jinja_env.get_template("print/report_card.html").render(
        cards=[card], printed_on="now", show_key=False)

    assert "None" not in html
    assert '<div class="l">Nilai Akhir</div>' not in html, "the strip still repeats it"
    assert html.count('<div class="lbl">Nilai Akhir</div>') == 1, "the headline keeps it"
    assert "Skor MCQ" in html and "Pelanggaran" in html


def test_the_pupil_screen_shows_the_same_working():
    src = (TEMPLATES / "student" / "result_detail.html").read_text(encoding="utf-8")
    assert "score_parts(submission)" in src, "the screen derives its parts centrally"
    assert "p.raw" in src and "p.penalty" in src
    assert ">Final<" not in src, "the duplicate final card is back"


def test_the_pdf_template_names_the_formula_it_prints():
    src = (TEMPLATES / "student" / "result_detail_pdf.html").read_text(encoding="utf-8")
    assert "Nilai Akhir = maks(0, Skor Mentah" in src
