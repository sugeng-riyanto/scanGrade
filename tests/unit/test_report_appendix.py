"""One document a school files, instead of one report and thirty stapled pages.

The class report answers "how did the class do"; a school also has to keep a page
per child, and until now that meant downloading thirty files and putting them in a
folder. Two doors come out of that: the class PDF can carry every learner's own
page as a **portrait appendix**, and a **zip** hands out the same files one per
child. What is held here is everything that makes those two honest:

* **the class pages did not move.** The appendix is laid out through a second page
  template; the landscape section must be byte-for-byte the page it was, or the
  report a school already has changes shape when it asks for an appendix;
* **the appendix page *is* the child's file.** Same flowables as `learner_pdf`,
  checked by comparing page text, because a second builder is how the filed page
  and the handed-out page start disagreeing;
* **the index is in the document's own order** — the label says "in the order of
  this document's learner table", which is `analysis.people`, and that is asserted
  rather than assumed;
* **a shared report has no appendix at all.** A page per child inside a public
  link is the one failure this feature could have, and the refusal is at the
  builder, not in the route that happens to call it correctly today;
* **a cut says so.** The appendix and the zip both stop at `MAX_FILES`, and a list
  that ends quietly looks like a class of that size.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import fitz
import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = (ROOT / "app" / "routes" / "public.py").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
REPORT_PAGE = (ROOT / "app" / "templates" / "teacher" / "analysis_report.html").read_text(
    encoding="utf-8")
INDEX_PAGE = (ROOT / "app" / "templates" / "teacher" / "reports.html").read_text(
    encoding="utf-8")

from app.services import analysis_report as ar       # noqa: E402
from app.services import exam_report as er           # noqa: E402
from app.services import item_analysis               # noqa: E402
from app.services import learner_report as lr        # noqa: E402

EXAM = {
    "id": "exam-1", "code": "MID-1", "title": "Mid Semester 1", "subject": "IPA",
    "class_name": "VIII-A", "school_name": "SMP Negeri 1 ScanGrade",
    "teacher_name": "Budi", "passing_score": 60, "total_questions": 5,
    "question_weights": {str(i): 20.0 for i in range(5)},
    "answer_key": {"0": "A", "2": "true", "3": "essay",
                   "4": {"pairs": [{"left": "air", "right": "cair"}]}},
    "question_types": {"0": "mcq", "1": "mcq", "2": "true_false", "3": "essay",
                       "4": "match"},
}

#: id, name, answers, teacher's marks, final score — with one paper that carries
#: no profile at all, which is the shape a scan-only submission has.
PAPERS = [
    ("stu-1", "Bella Safira", {"0": "A", "2": "true",
                               "4": {"pairs": [{"left": "air", "right": "cair"}]}},
     {"3": 100}, 100.0),
    ("stu-2", "Ahmad Pratama", {"0": "B", "2": "false"}, {}, 20.0),
    ("stu-3", "Ñoño Pratama", {"2": "true"}, {"3": 50}, 60.0),
    (None, "Tanpa Profil", {"0": "A"}, {}, 20.0),
]


def submissions():
    return [{"student_id": sid, "student_name": name, "answers": answers,
             "teacher_feedback": {"scores": scores}, "final_score": final}
            for sid, name, answers, scores, final in PAPERS]


@pytest.fixture(scope="module")
def analysis():
    return item_analysis.analyse(EXAM, submissions())


@pytest.fixture(scope="module")
def payloads(analysis):
    """The appendix, in the order the class document lists its learners."""
    out = []
    for person in analysis.people:
        sid = getattr(person, "student_id", None)
        who = er.learner(analysis, EXAM, sid) if sid else None
        if who:
            out.append(who)
    return out


@pytest.fixture(scope="module")
def plain(analysis):
    return ar.analysis_pdf(analysis, EXAM, lang="id")


@pytest.fixture(scope="module")
def carried(analysis, payloads):
    return ar.analysis_pdf(analysis, EXAM, lang="id", appendix=payloads,
                           appendix_exam=EXAM)


def _appendix_start(pages: list[tuple[float, float, str]]) -> int:
    """The first appendix page, found by its own heading and not by counting.

    A count would be a second way of knowing where the appendix begins, and the
    first thing it would hide is an appendix that begins somewhere else.
    """
    label = ar.labels("id")["appendix"]
    for index, (_width, _height, text) in enumerate(pages):
        if label in _flat(text):
            return index
    raise AssertionError("the document has no appendix page")


def _open(pdf: bytes):
    return fitz.open(stream=pdf, filetype="pdf")


def _pages(pdf: bytes) -> list[tuple[float, float, str]]:
    with _open(pdf) as doc:
        return [(page.rect.width, page.rect.height, page.get_text())
                for page in doc]


def _text(pdf: bytes) -> str:
    return _flat(" ".join(text for _w, _h, text in _pages(pdf)))


def _flat(text: str) -> str:
    """One line of text, because a wrapped sentence is not a missing sentence.

    reportlab breaks a long `Paragraph` across lines and the extractor hands back
    those breaks as `\n`, so a search for the label as written fails on a document
    that says exactly that — the failure a reader of the test would put down to
    the wrong cause.
    """
    return re.sub(r"\s+", " ", text).strip()


# ── the class pages did not move ─────────────────────────────────────────────

class TestTheClassReportIsUnchanged:

    def test_the_landscape_section_is_the_page_it_always_was(self, plain, carried):
        before, after = _pages(plain), _pages(carried)
        assert len(after) > len(before)
        for index, (page_before, page_after) in enumerate(zip(before, after)):
            assert page_before == page_after, (
                f"asking for an appendix changed page {index} of the class report")

    def test_the_report_stays_landscape_and_the_appendix_is_portrait(self, plain,
                                                                     carried):
        landscape_plain = [p for p in _pages(plain) if p[0] > p[1]]
        assert len(landscape_plain) == len(_pages(plain)), \
            "the class report stopped being landscape"
        class_pages = _pages(carried)[:len(_pages(plain))]
        appendix = _pages(carried)[len(_pages(plain)):]
        assert all(w > h for w, h, _t in class_pages)
        assert appendix and all(h > w for w, h, _t in appendix), \
            "the appendix is not laid out as portrait pages"

    def test_a_document_with_no_appendix_gets_no_second_template(self, plain):
        """The ordinary report keeps the single-frame builder: the appendix is a
        capability, not a change to what every download already is."""
        assert all(w > h for w, h, _t in _pages(plain))
        assert ar.labels("id")["appendix"] not in _text(plain)


# ── the appendix is the children's own pages ─────────────────────────────────

class TestEveryLearnerIsInIt:

    def test_the_index_names_every_learner_that_has_a_page(self, carried, payloads):
        body = "\n".join(text for _w, _h, text in _pages(carried))
        for who in payloads:
            name = who["who"]["name"]
            assert name in body, f"{name} has no page in the appendix"
            assert body.count(name) >= 2, (
                f"{name} is not in the index *and* on their own page")

    def test_the_index_is_in_the_order_the_document_lists_learners(self, carried,
                                                                   analysis,
                                                                   payloads):
        """The label claims the index follows this document's learner table, so
        the claim is measured: `analysis.people` is that order, and `ranking` —
        which sorts by mark — is a different list the appendix must not use."""
        names = [who["who"]["name"] for who in payloads]
        index_page = _flat(_pages(carried)[_appendix_start(_pages(carried))][2])
        positions = [index_page.find(name) for name in names]
        assert all(position >= 0 for position in positions), \
            f"the index does not carry every learner's name: {index_page[:200]}"
        assert positions == sorted(positions), (
            "the index is not in the order the document's learner table uses")
        # The fixture is built so the two candidate orders disagree — `ranking`
        # sorts by mark, `people` is the order the answers arrived in. Without
        # that, an appendix sorted by rank would satisfy every line above.
        ranked = [row["name"] for row in er.ranking(analysis.people)]
        assert ranked != names, "this fixture cannot tell the two orders apart"

    def test_an_appendix_page_is_the_file_that_child_can_download(self, carried,
                                                                  payloads):
        """The whole point of sharing the story builder: the filed page and the
        handed-out page are the same document, checked page by page."""
        window = [text for _w, _h, text in _pages(carried)]
        for who in payloads:
            own = [text for _w, _h, text in _pages(lr.learner_pdf(who, EXAM,
                                                                  lang="id"))]
            assert own[0] in window, (
                f"{who['who']['name']}'s own page is not in the appendix")
            start = window.index(own[0])
            assert window[start:start + len(own)] == own, (
                "the appendix copy of a learner's page differs from their file")

    def test_a_paper_with_no_profile_gets_no_invented_page(self, carried, payloads):
        """The name *is* in the class report — it is a row of the item table, and
        the appendix does not get to hide it. What it must not get is a page of
        its own, which is why this reads the appendix half only."""
        assert "Tanpa Profil" not in [who["who"]["name"] for who in payloads], \
            "a paper with no profile was given a page"
        pages = _pages(carried)
        appendix = pages[_appendix_start(pages):]
        assert "Tanpa Profil" not in _flat(" ".join(t for _w, _h, t in appendix))

    def test_the_cut_is_printed_when_the_class_is_longer_than_the_cap(self, analysis):
        many = [er.learner(analysis, EXAM, "stu-2")] * (lr.MAX_FILES + 2)
        blob = ar.analysis_pdf(analysis, EXAM, lang="id", appendix=many,
                              appendix_exam=EXAM)
        text = _text(blob)
        assert ar.labels("id")["appendix_cut"].format(
            n=lr.MAX_FILES, total=lr.MAX_FILES + 2) in text, \
            "an appendix that was cut does not say so"
        assert lr.carried(many) == (many[:lr.MAX_FILES], 2)

    def test_the_index_says_which_copy_the_pages_came_from(self, carried):
        labels = ar.labels("id")
        assert labels["appendix"] in _text(carried)
        assert labels["appendix_note"] in _text(carried)


# ── a shared report never carries one ────────────────────────────────────────

class TestTheAppendixIsNeverPublic:

    def test_the_builder_refuses_an_appendix_on_a_public_report(self, analysis,
                                                                payloads, plain):
        shared = ar.analysis_pdf(analysis, EXAM, lang="id", public=True,
                                 appendix=payloads, appendix_exam=EXAM)
        plain_public = ar.analysis_pdf(analysis, EXAM, lang="id", public=True)
        assert _pages(shared) == _pages(plain_public), \
            "a shared report grew pages when an appendix was asked for"
        text = _text(shared)
        assert not any(who["who"]["name"] in text for who in payloads), \
            "a shared report published a learner's name"
        assert all(width > height for width, height, _t in _pages(shared)), \
            "a portrait page leaked into a shared report"

    def test_the_share_route_never_asks_for_one(self):
        """The refusal above is the belt; this is the brace. A route that starts
        passing `appendix=` to the shared download would be reaching for the one
        document that must not contain a child's page."""
        body = PUBLIC.split("def shared_analysis_file", 1)[1].split(
            "\n@public_bp.route", 1)[0]
        assert "appendix" not in body
        assert "learners.zip" not in body


# ── the zip ──────────────────────────────────────────────────────────────────

class TestTheZipOfLearnerFiles:

    def _zip(self, payloads, **kw):
        return zipfile.ZipFile(io.BytesIO(lr.learners_zip(payloads, EXAM, **kw)))

    def test_one_file_per_child_named_after_them(self, payloads):
        with self._zip(payloads) as bundle:
            names = bundle.namelist()
        assert len(names) == len(payloads)
        assert len(set(names)) == len(names), "two children share one filename"
        for who, name in zip(payloads, names):
            assert who["who"]["name"].split()[0].isascii() or "Nono" in name
            assert name.startswith("laporan-") and name.endswith(".pdf")

    def test_each_entry_is_the_file_its_own_door_serves(self, payloads):
        """Not byte-for-byte — a PDF carries its build time, so two builds of one
        document never match — but page for page, which is the claim that matters:
        a teacher who sends the zip and a teacher who sends one file send the same
        document."""
        with self._zip(payloads) as bundle:
            for who, name in zip(payloads, bundle.namelist()):
                served = lr.learner_pdf(who, EXAM, lang="id")
                assert _pages(bundle.read(name)) == _pages(served), name

    def test_two_names_that_fold_together_are_numbered_not_dropped(self, payloads):
        clash = [dict(payloads[2], who=dict(payloads[2]["who"], name="Nono Pratama")),
                 payloads[2]]
        with self._zip(clash) as bundle:
            names = bundle.namelist()
        assert len(names) == 2 and len(set(names)) == 2, names
        with self._zip(clash) as bundle:
            assert all(bundle.read(name)[:5] == b"%PDF-" for name in names)

    def test_a_cut_zip_carries_the_note_that_says_so(self, analysis):
        many = [er.learner(analysis, EXAM, "stu-2")] * (lr.MAX_FILES + 1)
        with zipfile.ZipFile(io.BytesIO(lr.learners_zip(many, EXAM, lang="en"))) as z:
            names = z.namelist()
            note = z.read("catatan.txt").decode("utf-8")
        # MAX_FILES identical names collide, so the numbering is what makes them
        # distinct entries — the count is what matters here.
        assert len([n for n in names if n.endswith(".pdf")]) == lr.MAX_FILES
        assert ar.labels("en")["appendix_cut"].format(
            n=lr.MAX_FILES, total=lr.MAX_FILES + 1) in note

    def test_a_public_zip_carries_no_answer_key(self, payloads):
        """Both directions, because a zip that lost its answer-key column
        altogether would satisfy half of this — and the half it satisfies is the
        one that matters less than knowing the teacher's zip still has it.

        Read from the text layer and flattened: the header is a row of table
        cells, and reportlab hands the extractor a line break between them.
        """
        labels = ar.labels("id")
        head = f'{labels["learner_answer"]} {labels["key"]}'

        def text_of(bundle):
            return _flat(" ".join(
                " ".join(page.get_text() for page in
                         fitz.open(stream=bundle.read(name), filetype="pdf"))
                for name in bundle.namelist()))

        with self._zip(payloads, public=True) as bundle:
            shared = text_of(bundle)
        with self._zip(payloads) as bundle:
            teacher = text_of(bundle)
        assert head in teacher, \
            "the teacher's zip has no answer-key column, so this test is blind"
        assert head not in shared, \
            "the zip read as a public copy still published the answer key"


# ── the doors ────────────────────────────────────────────────────────────────

class TestTheDoors:

    def test_the_zip_is_a_route_the_app_serves(self, app):
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert "/teacher/analysis/<exam_id>/learners.zip" in rules

    def test_the_appendix_is_the_pdf_route_with_a_flag(self):
        """One route, one format: the appendix is the class PDF with pages added,
        so a second URL would be a second document to keep in step."""
        assert "download.pdf" in TEACHER
        body = TEACHER.split("def exam_analysis_pdf", 1)[1].split(
            "\ndef _learner_payloads", 1)[0]
        assert 'request.args.get("appendix")' in body
        assert "appendix=appendix" in body and "appendix_exam=cover" in body

    def test_the_appendix_download_is_named_apart_from_the_report(self):
        body = TEACHER.split("def exam_analysis_pdf", 1)[1].split(
            "\ndef _learner_payloads", 1)[0]
        assert '-lampiran.pdf' in body, \
            "the appendix download overwrites the class report in the same folder"

    def test_both_pages_offer_the_two_doors(self):
        for page, source in (("filed report", REPORT_PAGE), ("index", INDEX_PAGE)):
            assert "download.pdf?appendix=1" in source, \
                f"the {page} offers no class-plus-appendix document"
            assert "/learners.zip" in source, \
                f"the {page} offers no zip of learner files"
        assert "data-download=\"zip\"" in INDEX_PAGE
        assert "data-download=\"zip\"" in REPORT_PAGE

    def test_the_zip_reads_the_same_scope_the_pages_do(self):
        """The papers in the zip are the papers the index lists: both come from
        `analysis.people` through the same guard, so a zip cannot hold another
        school's children."""
        body = TEACHER.split("def exam_analysis_learners_zip", 1)[1].split(
            "\n@teacher_bp.route", 1)[0]
        assert "_analysis_of(" in body, "the zip route reads the exam unguarded"
        assert body.index("_analysis_of(") < body.index("learners_zip(")
        assert "_report_cover(" in body
        source = TEACHER.split("def _learner_payloads", 1)[1].split(
            "\ndef ", 1)[0]
        assert "for person in analysis.people" in source, \
            "the appendix walks something other than the analysis's own learners"
