"""One learner's report as a file of its own: a PDF, a workbook, a CSV.

The page at `/teacher/analysis/<exam>/report/student/<student>` is the document a
teacher reads, and its only export used to be the browser's print dialog — which
produces whatever the *screen* happens to be (chrome and all), cannot be produced
at all on a phone, and gives thirty children's reports one filename between them.
These three files are built on the server from the payload the page renders, so a
number on the sheet a parent is handed cannot disagree with the screen it came
from. What is held here is what makes that true:

* **one payload** — the builders take `exam_report.learner()` and nothing else, so
  there is no second grading path to drift;
* **the shared copy is not the teacher's copy** — the answer key is dropped by the
  *builder* when `public=True`, not hidden by markup, and the banner says what was
  withheld instead of claiming the child's name was;
* **the door is an address the app serves** — built from the route's own base, so a
  route that forgets it writes no door rather than a broken one;
* **the filename is header-safe** — it names the child (thirty files, thirty names)
  and survives a name written with a slash, a quote or a control character.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = (ROOT / "app" / "routes" / "public.py").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
PAGE = (ROOT / "app" / "templates" / "teacher" / "analysis_student.html").read_text(
    encoding="utf-8")

from app.services import analysis_report as ar       # noqa: E402
from app.services import exam_report as er           # noqa: E402
from app.services import item_analysis               # noqa: E402
from app.services import learner_report as lr        # noqa: E402

EXAM_ID = "exam-1"
STUDENT = "stu-1"

#: A paper with one question of each kind, so every branch of the level table and
#: the question table is exercised: a keyed choice, an *unkeyed* one, a true/false,
#: an essay the teacher marked, and a matching pair.
EXAM = {
    "id": EXAM_ID, "code": "MID-1", "title": "Mid Semester 1", "subject": "IPA",
    "class_name": "VIII-A", "school_name": "SMP Negeri 1 ScanGrade",
    "teacher_name": "Budi", "passing_score": 60, "total_questions": 5,
    "question_weights": {str(i): 20.0 for i in range(5)},
    "answer_key": {"0": "A", "2": "true", "3": "essay",
                   "4": {"pairs": [{"left": "air", "right": "cair"}]}},
    "question_types": {"0": "mcq", "1": "mcq", "2": "true_false", "3": "essay",
                       "4": "match"},
}

#: id, name, answers, teacher's marks, final score. `stu-3` carries a name that
#: cannot be spelled in ASCII, because that is what the filename has to survive.
SUBJECTS = [
    ("stu-1", "Bella Safira", {"0": "A", "2": "true",
                               "4": {"pairs": [{"left": "air", "right": "cair"}]}},
     {"3": 100}, 100.0),
    ("stu-2", "Ahmad Pratama", {"0": "B", "2": "false"}, {}, 20.0),
    ("stu-3", "Ñoño Pratama", {"2": "true"}, {"3": 50}, 60.0),
]


def submissions():
    return [{"student_id": sid, "student_name": name, "answers": answers,
             "teacher_feedback": {"scores": scores}, "final_score": final}
            for sid, name, answers, scores, final in SUBJECTS]


def who_of(sid=STUDENT, **kw):
    return er.learner(item_analysis.analyse(EXAM, submissions()), EXAM, sid, **kw)


@pytest.fixture
def who():
    return who_of()


def _rows(who, public):
    return lr._question_rows(who, "id", public)


def _pdf_text(pdf: bytes) -> str:
    import fitz

    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return " ".join(span["text"] for page in doc
                        for block in page.get_text("dict")["blocks"]
                        for line in block.get("lines", []) for span in line["spans"])


# ── the three documents ──────────────────────────────────────────────────────

class TestTheFilesThemselves:

    def test_the_csv_carries_every_section_the_page_has(self, who):
        text = lr.learner_csv(who, EXAM, "id")
        labels = ar.labels("id")
        for key in ("learner_title", "learner_position", "learner_outcomes",
                    "learner_levels", "learner_questions", "learner_strengths",
                    "learner_weaknesses", "learner_next", "learner_statement",
                    "learner_method"):
            assert labels[key] in text, f"the CSV has no {key} section"

    def test_the_csv_uses_crlf_so_excel_opens_it_as_rows(self, who):
        text = lr.learner_csv(who, EXAM, "id")
        assert "\r\n" in text
        assert "\n" not in text.replace("\r\n", ""), \
            "a bare LF in a CSV is one long row in some spreadsheet readers"

    def test_the_workbook_reuses_the_class_workbooks_sheet_names(self, who):
        """One exam, two workbooks, and a reader who has both: a sheet named
        differently in each is the same table under two names."""
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(lr.learner_xlsx(who, EXAM, lang="id")))
        assert book.sheetnames == [ar.SHEET_NAMES["summary"]["id"],
                                   ar.SHEET_NAMES["items"]["id"]]

    def test_the_workbook_draws_this_learners_share_beside_the_classs(self, who):
        """The one comparison a picture settles faster than a table. Anchored on
        the cells it plots, so correcting a mark moves the bar."""
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(lr.learner_xlsx(who, EXAM, lang="id")))
        sheet = book[ar.SHEET_NAMES["summary"]["id"]]
        assert len(sheet._charts) == 1, "the learner workbook lost its chart"
        chart = sheet._charts[0]
        assert chart.y_axis.scaling.min == 0 and chart.y_axis.scaling.max == 100, \
            "an axis that rescales makes one child's 40% look nearly full"
        # The two series read the two share columns — the figures printed beside
        # the bar, so correction moves it.
        share, class_share = lr._LEVEL_COLUMN["share"], lr._LEVEL_COLUMN["class_share"]
        assert share < class_share, "the chart plots the two shares as one series"
        refs = " ".join(str(series.val.numRef.f) for series in chart.series)
        assert f"${chr(64 + share)}$" in refs, \
            f"the learner's own share is not plotted: {refs}"
        assert f"${chr(64 + class_share)}$" in refs, \
            f"the class share is not plotted: {refs}"

    def test_a_learner_with_no_level_gets_no_empty_chart_frame(self, who):
        """A chart with no series renders as a blank frame, which reads as a
        rendering fault rather than as "nothing here was scoreable"."""
        from openpyxl import load_workbook

        bare = dict(who, levels=[])
        book = load_workbook(io.BytesIO(lr.learner_xlsx(bare, EXAM, lang="id")))
        assert book[ar.SHEET_NAMES["summary"]["id"]]._charts == []

    def test_the_pdf_is_a_document_about_this_one_learner(self, who):
        pdf = lr.learner_pdf(who, EXAM, lang="id")
        assert pdf[:5] == b"%PDF-"
        text = _pdf_text(pdf)
        assert "Bella Safira" in text, "the filed document does not name the child"
        assert "Mid Semester 1" in text
        # The two bars are drawn, and the legend says which is whose.
        assert "Capaian murid" in text and "Capaian kelas" in text

    def test_the_three_builders_survive_a_learner_with_nothing_in_them(self):
        """Every list empty, every total absent — the shapes a paper that was
        collected but never answered produces. None of them may raise, and none
        of them may print a raw `None` into a document a family reads."""
        bare = {
            "who": {"student_id": "s", "name": "Tanpa Jawaban", "pct": None,
                    "rank": 1, "percentile_rank": None, "measure": None,
                    "tied": False, "band": None},
            "class": {"learners": 1, "mean": None, "kkm": None, "configured": False},
            "totals": {"credited": None, "possible": None}, "counts": {},
            "states": {}, "levels": [], "questions": [], "strengths": [],
            "weaknesses": [], "remediation": [],
            "statement": {"head": ("", ""), "body": ("", "")}, "gap": {},
        }
        assert "None" not in lr.learner_csv(bare, EXAM, "id")
        assert "None" not in _pdf_text(lr.learner_pdf(bare, EXAM, lang="id"))
        assert lr.learner_xlsx(bare, EXAM, lang="id")[:2] == b"PK"


# ── the copy that leaves the building ────────────────────────────────────────

class TestTheSharedCopyIsNotTheTeachers:

    def test_the_key_is_dropped_by_the_builder_not_hidden_by_the_markup(self, who):
        teacher = _rows(who, False)
        shared = _rows(who, True)
        assert any(row["key"] for row in teacher), \
            "the teacher's copy has no key at all, so this test is blind"
        assert all(row["key"] == "" for row in shared), \
            "a shared row still carries the key"
        # The learner's answer survives on both: it is theirs, not the paper's.
        assert [row["answer"] for row in shared] == [r["answer"] for r in teacher]

    def test_the_column_is_absent_from_the_shared_csv(self, who):
        labels = ar.labels("id")
        shared = lr.learner_csv(who_of(with_key=False), EXAM, "id", public=True)
        header = next(line for line in shared.splitlines()
                      if line.startswith(labels["no"] + ","))
        assert labels["key"] not in header, f"the shared CSV publishes a key column: {header}"
        assert labels["key"] in next(
            line for line in lr.learner_csv(who, EXAM, "id").splitlines()
            if line.startswith(labels["no"] + ","))

    def test_the_column_is_absent_from_the_shared_pdf(self, who):
        """The filed copy of a learner's report is the one most likely to be
        forwarded, so the third document is checked too — and in both directions,
        because a PDF that simply lost its answer-key column altogether would
        satisfy half of this."""
        labels = ar.labels("id")
        head = f'{labels["learner_answer"]} {labels["key"]}'
        assert head in _pdf_text(lr.learner_pdf(who, EXAM, lang="id")), \
            "the teacher's PDF has no answer-key column, so this test is blind"
        assert head not in _pdf_text(
            lr.learner_pdf(who_of(with_key=False), EXAM, lang="id", public=True)), \
            "the shared PDF publishes the answer-key column"

    def test_the_column_is_absent_from_the_shared_workbook(self, who):
        from openpyxl import load_workbook

        labels = ar.labels("id")
        shared = load_workbook(io.BytesIO(
            lr.learner_xlsx(who_of(with_key=False), EXAM, lang="id", public=True)))
        columns = [cell.value for cell in
                   shared[ar.SHEET_NAMES["items"]["id"]][1]]
        assert labels["key"] not in columns, columns
        teacher = load_workbook(io.BytesIO(lr.learner_xlsx(who, EXAM, lang="id")))
        assert labels["key"] in [cell.value for cell in
                                 teacher[ar.SHEET_NAMES["items"]["id"]][1]]

    def test_the_banner_says_what_was_withheld(self, who):
        """The class report's banner claims **student names** are not included,
        and it is a lie on a file named after the child and printing their name.
        What this copy withholds is the key."""
        labels = ar.labels("id")
        shared = lr.learner_csv(who_of(with_key=False), EXAM, "id", public=True)
        assert labels["learner_shared"] in shared
        assert labels["shared"] not in shared, \
            "the learner's file repeats the class banner, which is false here"

    def test_the_legend_says_which_bar_is_whose(self, who):
        """Two bars in two inks and no words: a reader who has to guess has been
        shown a decoration rather than a comparison."""
        labels = ar.labels("id")
        text = _pdf_text(lr.learner_pdf(who, EXAM, lang="id"))
        assert labels["learner_share"] in text and labels["learner_class_share"] in text
        # And the scale, so "80" is not read as a fraction of nothing.
        assert "0–100" in text and labels["share"] in text

    def test_the_payload_a_link_resolves_has_no_key_in_it_at_all(self):
        payload = who_of(with_key=False)
        assert all(not q.get("key") for q in payload["questions"]), \
            "the belt: the payload itself is built without a key"

    def test_the_shared_route_asks_for_the_redacted_builder(self):
        """Read from the *call*, not the prose: the docstring says `public=True`,
        so a search over the whole function stays green after the call itself was
        flipped — which is exactly the mutation this guard exists to catch."""
        body = PUBLIC.split("def _shared_learner_file", 1)[1].split(
            "@public_bp.route", 1)[0]
        call = body.split('"""', 2)[2]
        assert "public=True" in call, "the shared file route stopped redacting"
        assert "with_key=False" in call


# ── the doors ────────────────────────────────────────────────────────────────

def _render(app, download_base, public):
    """The page as each route hands it over: the teacher's, or the share link's."""
    import contextlib

    from flask import g

    payload = who_of(with_key=not public)
    ctx = (app.test_request_context(f"/r/{'a' * 20}") if public
           else app.test_request_context(
               f"/teacher/analysis/{EXAM_ID}/report/student/{STUDENT}"))
    with ctx:
        if not public:
            g.user_id, g.user_role = "tea-1", "guru"
            g.user_name, g.user_email, g.tz_offset, g.show = "Guru", "g@e.test", 7, {}
        return app.jinja_env.get_template("teacher/analysis_student.html").render(
            exam=EXAM, learner=payload, public_view=public, share=None,
            back_url="/", download_base=download_base, lang="id")


class TestTheFileRow:

    def test_the_three_documents_are_offered_as_addresses_the_app_serves(self, app):
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert ("/teacher/analysis/<exam_id>/report/student/<student_id>"
                "/download.<ext>") in rules
        assert "/r/<token>/download.<ext>" in rules
        html = _render(app, f"/teacher/analysis/{EXAM_ID}/report/student/{STUDENT}",
                       public=False)
        for ext in ("pdf", "xlsx", "csv"):
            assert (f'href="/teacher/analysis/{EXAM_ID}/report/student/{STUDENT}'
                    f'/download.{ext}"') in html, f"no {ext} door on the teacher's page"

    def test_the_shared_page_offers_the_shared_addresses(self, app):
        """A parent holding a link gets the file of the page they are reading —
        the same link prefix, so it cannot be the teacher's copy by accident."""
        html = _render(app, f"/r/{'a' * 20}", public=True)
        for ext in ("pdf", "xlsx", "csv"):
            assert f'href="/r/{"a" * 20}/download.{ext}"' in html
        assert f"/teacher/analysis/{EXAM_ID}/report/student/{STUDENT}/download." not in html, \
            "the shared page points at the teacher's route"

    def test_the_shared_page_renders_the_shared_copy(self, app):
        """The door's address says nothing about which *copy* the reader holds —
        the file route takes its own `public` argument — so the page is checked
        for the two marks that do: the share control is gone, and the method note
        is the one about a shared copy."""
        labels = ar.labels("id")
        html = _render(app, f"/r/{'a' * 20}", public=True)
        assert "data-share-card" not in html, \
            "a stranger is offered the share control"
        assert labels["learner_method_shared"] in html
        assert labels["learner_method_key"] not in html, \
            "the shared page claims a teacher's key column"

    def test_a_route_that_forgets_its_base_writes_no_door(self, app):
        """`download_base` is the route's own answer to \"which copy is this\".
        Absent, the page offers nothing — a half-built address is a link that
        looks whole and 404s."""
        html = _render(app, None, public=False)
        assert "/download." not in html

    def test_both_learner_routes_hand_the_page_its_base(self):
        assert ('download_base=f"/teacher/analysis/{exam_id}/report/student/'
                '{student_id}"') in TEACHER, \
            "the teacher's learner route does not tell the page where its files are"
        assert "download_base=f\"/r/{link['token']}\"" in PUBLIC, \
            "the shared learner route does not tell the page where its files are"

    def test_the_extension_is_refused_before_anything_is_read(self):
        """A path built from a caller's text is how a download route becomes a
        file-read route, so the whitelist runs first — asserted by *position*,
        because a guard that runs after the lookup has already read the exam."""
        body = TEACHER.split("def exam_analysis_student_file", 1)[1].split(
            "\ndef _learner_file", 1)[0]
        guard = body.index("if ext not in _LEARNER_FILES")
        assert guard < body.index("_analysis_of("), \
            "the extension is whitelisted after the exam has been read"
        assert body.index("_analysis_of(") < body.index("_report_cover(")

    def test_the_route_that_is_not_the_teacher_asks_for_the_redacted_copy(self):
        assert "public=False" in TEACHER.split("def exam_analysis_student_file", 1)[1], \
            "the teacher's file route stopped saying which copy it builds"


# ── the name in the header ───────────────────────────────────────────────────

class TestWhatTheFileIsCalled:

    def test_it_names_the_child_and_the_paper(self, who):
        name = lr.filename(who, EXAM, "pdf")
        assert "BellaSafira" in name and "MidSemester1" in name
        assert name.endswith(".pdf")
        assert lr.filename(who_of("stu-3"), EXAM, "pdf") != name, \
            "thirty children, one filename"

    @pytest.mark.parametrize("person", [
        'Ani/"..\\.."; drop.pdf', "Ani\r\nSet-Cookie: x=1", "Ω中", "   ", "Ani%00.pdf",
    ])
    def test_a_hostile_name_cannot_escape_the_header(self, who, person):
        """`Content-Disposition` is a header. A name with a quote, a slash or a
        newline in it is a response split, so the stem keeps only what an ASCII
        header reads literally."""
        name = lr.filename(dict(who, who={"name": person}), EXAM, "xlsx")
        assert name.endswith(".xlsx")
        assert name.isascii(), name
        assert re.fullmatch(r"laporan[A-Za-z0-9._-]*\.xlsx", name), name
        assert "/" not in name and "\\" not in name and '"' not in name
        assert "\r" not in name and "\n" not in name and " " not in name

    def test_a_name_that_folds_away_still_leaves_a_file(self, who):
        """A name with nothing ASCII in it, on an exam with no code: the file is
        still a file, and it is not named `.csv`."""
        assert lr.filename(dict(who, who={"name": "Ω中"}), {}, "csv") == "laporan.csv"
        # And the exam's own code is the fallback, so two learners on one paper
        # never collide into one name.
        assert lr.filename(dict(who, who={"name": "Ω中"}), EXAM, "csv") \
            != lr.filename(who, EXAM, "csv")
