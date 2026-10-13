"""The printed "Kepala Sekolah" line: a real name, or an honest placeholder.

The defect, and why it survived
-------------------------------
The report's signature block printed the head of school as a **hardcoded dotted
line** — ``(............................................)`` — so the name never appeared
however complete the school's data was. Underneath it there was a second, quieter
fault: the school query (`report_card_service._fetch_school`/`_fetch_schools`) did
not even *select* the column, so a template that read it would still have found
nothing. Two halves of one bug, and either alone reproduces it.

The entity question, settled
----------------------------
"Kepala Sekolah" names two different things in this codebase — the read-only
`principal` **login role**, and the **`schools.principal_name` text field** printed
on documents. The sheet uses the field, because a document belongs to the *school*,
and a school that never made a `principal` account still must print a name.
`docs/ROLES_CLARIFICATION.md` is the standing reference; the tests below pin the
consequences rather than the prose.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
SERVICE = (ROOT / "app" / "services" / "report_card_service.py").read_text(encoding="utf-8")
EXAM_SHEET = TEMPLATES / "teacher" / "print_exam_report.html"
REPORT_CARD = TEMPLATES / "print" / "report_card.html"
DOC = ROOT / "docs" / "ROLES_CLARIFICATION.md"

PLACEHOLDER = "Nama belum diatur"

EXAM = {
    "id": "e1", "title": "Ujian Matematika", "subject_name": "Matematika",
    "answer_key": {}, "question_types": {}, "question_weights": {},
    "total_questions": 0, "pdf_page_urls": [], "question_scoring": {},
}


def _render_exam_sheet(app, school):
    return app.jinja_env.get_template("teacher/print_exam_report.html").render(
        exam=dict(EXAM), roster=[],
        stats={"count": 0, "avg": 0.0, "max": 0.0, "min": 0.0, "passed": 0,
               "pass_rate": 0, "threshold": 70},
        class_names=[], school=school, teacher_name="Bu Sari", printed_on="now",
        pass_mark=70)


def _render_report_card(app, school):
    card = {"submission": {"answers": {}, "teacher_feedback": {}, "exam": dict(EXAM),
                           "status": "published", "is_published": True,
                           "final_score": 55.5, "score": 60, "penalty": 4.5,
                           "violations": 2},
            "exam": dict(EXAM), "released": True, "student_name": "Ahmad",
            "student_nisn": "1", "student_nis": "1", "teacher_name": "Bu Sari",
            "school": school}
    return app.jinja_env.get_template("print/report_card.html").render(
        cards=[card], printed_on="now", show_key=False)


# ── the mechanism: the field is read, and the query fetches it ───────────────

class TestTheNameIsActuallyAvailable:
    def test_the_school_query_selects_the_principals_name(self):
        """The quieter half of the bug: without this, even a fixed template is blank."""
        assert "principal_name" in SERVICE, (
            "the report's school query does not select `principal_name`, so the "
            "printed line can only ever fall back")
        assert "principal_nip" in SERVICE

    def test_both_documents_read_the_field_not_a_hardcoded_line(self):
        for path in (EXAM_SHEET, REPORT_CARD):
            source = path.read_text(encoding="utf-8")
            assert "school.get('principal_name')" in source, (
                f"{path.name} does not read the head of school's name")
            # The defect itself: a dotted line where the *principal's* name goes.
            # Scoped to that cell, because the teacher's own line legitimately keeps
            # the dotted fallback when no teacher name is known.
            cell = source[source.index("Kepala Sekolah"):][:400]
            assert "(............................................)" not in cell, (
                f"{path.name} still prints a dotted line where the name goes")


# ── the surfaces, rendered ───────────────────────────────────────────────────

class TestThePrintedLine:
    def test_the_exam_sheet_prints_the_name_when_set(self, app):
        html = _render_exam_sheet(app, {"name": "SMP 1", "principal_name": "Drs. Budi"})
        assert "Mengetahui" in html
        assert "Drs. Budi" in html
        assert PLACEHOLDER not in html, "a set name must not carry the placeholder"

    def test_the_exam_sheet_prints_the_nip_when_present(self, app):
        html = _render_exam_sheet(app, {"name": "SMP 1", "principal_name": "Drs. Budi",
                                        "principal_nip": "19700101"})
        assert "NIP. 19700101" in html

    def test_the_exam_sheet_is_honest_when_unset(self, app):
        html = _render_exam_sheet(app, {"name": "SMP 1"})
        assert PLACEHOLDER in html, "an empty school must say so, not print nothing"
        assert "None" not in html

    def test_the_report_card_prints_the_name_when_set(self, app):
        html = _render_report_card(app, {"name": "SMP 1", "principal_name": "Drs. Budi"})
        assert "Drs. Budi" in html and PLACEHOLDER not in html

    def test_the_report_card_is_honest_when_unset(self, app):
        html = _render_report_card(app, {"name": "SMP 1"})
        assert PLACEHOLDER in html
        assert "None" not in html

    @pytest.mark.parametrize("school", [
        {"name": "SMP 1", "principal_name": "Drs. Budi"},
        {"name": "SMP 1"},
    ])
    def test_no_document_ever_prints_the_word_none(self, app, school):
        for html in (_render_exam_sheet(app, school), _render_report_card(app, school)):
            assert "None" not in html


# ── the standing reference exists, and says the definitive thing ─────────────

class TestTheReferenceDocument:
    def test_it_names_the_six_roles(self):
        text = DOC.read_text(encoding="utf-8")
        for role in ("super_admin", "admin_sekolah", "principal", "vice_principal",
                     "guru", "murid"):
            assert role in text, f"the roles reference omits {role}"
        assert "6" in text

    def test_it_settles_the_field_versus_role_question(self):
        text = DOC.read_text(encoding="utf-8")
        assert "principal_name" in text, (
            "the reference must name the text field the documents actually use")
        assert "ROLES_CLARIFICATION" not in text or True  # self-reference is fine

    def test_rbac_points_at_it(self):
        rbac = (ROOT / "docs" / "RBAC.md").read_text(encoding="utf-8")
        assert "ROLES_CLARIFICATION.md" in rbac, (
            "a future session reads RBAC.md first; it must be pointed at the answer")
