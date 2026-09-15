"""The page a question is printed on is derived, not typed.

Reported as: the teacher has to fill a page field for every question before a
student can move around the paper. The field itself is load-bearing — the exam
page jumps the PDF to a question's first page when the student switches question,
and without it switching question leaves the paper where it was — but the *typing*
was never necessary: `pdf_to_markdown` splits the document into pages, and the
classification step then joined them back into one string and discarded the
boundaries.

These tests hold the derivation, and the two wirings that make it reach the
teacher's form, because the failure mode is silent: a question with no page just
leaves the field empty, which is exactly what the old code did.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "app" / "services" / "pdf_parser.py"
ROUTE = ROOT / "app" / "routes" / "teacher.py"
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"

from app.services.pdf_parser import question_pages  # noqa: E402


# ── the derivation ───────────────────────────────────────────────

def test_a_question_takes_the_page_its_opening_is_printed_on():
    pages = [
        "1. What is the capital of France?\nA. Paris\nB. Rome",
        "2. Explain photosynthesis.\n3. Name three noble gases.",
    ]
    questions = [
        {"number": 1, "type": "mcq", "text": "What is the capital of France?",
         "full_text": "What is the capital of France?\nA. Paris\nB. Rome"},
        {"number": 2, "type": "essay", "text": "Explain photosynthesis.",
         "full_text": "Explain photosynthesis."},
    ]

    question_pages(questions, pages)

    assert questions[0]["page"] == 1
    assert questions[1]["page"] == 2


def test_a_question_running_over_a_page_break_starts_where_it_starts():
    """The whole string is not on any single page, and matching it whole would
    leave a long question — the common case — with no page at all."""
    pages = [
        "1. Read the passage below and answer the question that follows.\n" + "lorem " * 80,
        "ipsum continued from the previous page, now the actual answer goes here.",
    ]
    questions = [{
        "number": 1, "type": "essay",
        "text": "Read the passage below and answer the question that follows.",
        "full_text": "Read the passage below and answer the question that follows.\n" + "lorem " * 80
                     + "\nipsum continued from the previous page, now the actual answer goes here.",
    }]

    question_pages(questions, pages)

    assert questions[0]["page"] == 1, "a question that spans two pages starts on the first"


def test_whitespace_and_line_breaks_do_not_hide_a_match():
    """The classifier re-flows text; the page markdown keeps the original line
    structure. A byte comparison would find nothing and report no page."""
    pages = ["1.   Hitung\n     nilai dari  2x + y = 5"]
    questions = [{"number": 1, "type": "mcq", "text": "Hitung nilai dari 2x + y = 5",
                  "full_text": "Hitung nilai dari 2x + y = 5"}]

    question_pages(questions, pages)

    assert questions[0]["page"] == 1


def test_a_question_that_cannot_be_found_gets_no_page():
    """Never a guess. An invented page sends a student to the wrong part of the
    paper, which is worse than leaving them to page through it."""
    pages = ["1. What is the capital of France?"]
    questions = [{"number": 1, "type": "mcq", "text": "A question from another document",
                  "full_text": "A question from another document"}]

    question_pages(questions, pages)

    assert "page" not in questions[0], "an unmatched question must stay empty"


def test_nothing_at_all_is_not_a_crash():
    assert question_pages([], []) == []
    assert question_pages([], ["page one"]) == []
    assert question_pages([{"number": 1, "text": "", "full_text": ""}], ["page one"]) == [
        {"number": 1, "text": "", "full_text": ""}]
    assert question_pages([{"number": 1, "text": "x", "full_text": "x"}], []) == [
        {"number": 1, "text": "x", "full_text": "x"}]


def test_the_pages_are_one_based():
    """They are shown to a student next to a page they can see, and compared
    against `page + 1` in the exam page's parser (`parsePageRange`)."""
    pages = ["first page", "second page", "third page"]
    questions = [{"number": 1, "text": "third page", "full_text": "third page"}]

    question_pages(questions, pages)

    assert questions[0]["page"] == 3


def test_the_first_page_wins_when_a_phrase_repeats():
    """A repeated instruction ("Explain your answer") must attach the question to
    where it is *asked*, so the earliest page containing the opening wins."""
    pages = ["Explain your answer.\n1. Explain your answer in full.", "Explain your answer."]
    questions = [{"number": 1, "text": "Explain your answer in full.",
                  "full_text": "Explain your answer in full."}]

    question_pages(questions, pages)

    assert questions[0]["page"] == 1


# ── the two wirings ──────────────────────────────────────────────

def test_the_parse_route_derives_the_pages():
    """A helper nobody calls is dead code, and this one is only reachable through
    the upload the teacher actually uses."""
    source = ROUTE.read_text(encoding="utf-8")

    assert "question_pages" in source, "the parse route does not call the derivation"
    assert re.search(r"question_pages\(\s*questions\s*,\s*parsed\.get\(\"pages\"\)", source), (
        "the derivation must receive the per-page markdown the parser already "
        "produced — joined markdown has no page boundaries to find")
    assert "question_pages" in source[source.index("@teacher_bp.route(\"/exams/parse-pdf\""):
                                       source.index("@teacher_bp.route(\"/exams/parse-pdf\"") + 6000], \
        "the import sits outside the route that uses it"


def test_the_builder_prefills_the_page_field_from_the_parse():
    """`question_pages` is a dict of ranges; the parse returns a page per question.
    Without the mapping the derivation reaches the browser and is dropped."""
    form = FORM.read_text(encoding="utf-8")

    assert "copy.pages = q.pages || (q.page ? String(q.page) : '')" in form, (
        "the builder no longer prefills the page field, so the teacher is back to "
        "typing a page for every question")
    assert "getPagesJson" in form, "the page field is never submitted"


def test_the_page_field_is_still_editable():
    """Derived is a default, not the truth: a question can span pages, and only the
    teacher can say so. Removing the input would break the range form."""
    form = FORM.read_text(encoding="utf-8")

    assert re.search(r":name=\"'pages_' \+ i\"\s+x-model=\"q\.pages\"", form), (
        "the page input must stay bound so a derived value can be corrected")


def test_the_page_field_is_optional():
    """The number is a convenience, not a precondition.

    A question with no page still works end to end: the student page simply does
    not turn the paper when that question is picked, and `pageOwner` reports no
    owner so the rail shows no badge. `required` therefore forced a teacher who
    did not know the page to invent one — and an invented page is worse than an
    empty one, because it sends the student to the wrong part of the paper.
    """
    form = FORM.read_text(encoding="utf-8")
    field = form[form.index(":name=\"'pages_' + i\""):]
    field = field[:field.index(">")]

    assert "required" not in field, "a page number is still demanded before the form can be saved"
    assert "x-text=\"t('Halaman PDF','PDF page')\"" in form, (
        "an unlabelled field cannot explain a value that is already filled in")
    assert "q._pageAuto" in form, "the form cannot tell a derived page from a typed one"
    assert "t('terisi otomatis','filled automatically')" in form
