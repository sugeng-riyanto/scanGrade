"""The student's second way around the paper, and the page it is drawn beside.

Reported as: a question that runs across several pages is painful to answer — the
page strip lives inside the question you are already in, so moving between the
pages of the paper means switching question first. The fix is a rail of page
thumbnails in the left column, hideable, with the question list one tap away in
the same column.

Three things here are load-bearing and none of them is visible when it breaks:

1. **The rail must show thumbnails, not the pages.** It renders every page at
   once; pointed at the 150 dpi PNGs it would make a student download the whole
   exam again, which on a school WiFi is the difference between the rail being a
   convenience and a trap.
2. **A question switch must not throw away the page the student chose.** The
   watcher used to jump to the question's first page on every switch, so paging
   ahead inside a long question and then touching the question list yanked the
   reader back.
3. **The paper belongs beside the sidebar.** The exam page carried a stray
   ``</div>`` that closed the layout row early, which made the question column and
   the paper render one *above* the other. Nothing failed; the page simply was not
   the layout it was written to be. That is invisible in a diff and invisible in a
   rendered screenshot if you do not know what you are looking for, so it is
   asserted here.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image
from flask import Flask

ROOT = Path(__file__).resolve().parents[2]
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
STUDENT_ROUTE = ROOT / "app" / "routes" / "student.py"

from app.services.pdf_service import THUMB_WIDTH, ensure_page_thumbs, thumb_name  # noqa: E402


# ── the derivation both sides agree on ───────────────────────────────────

def test_the_thumbnail_name_is_derived_from_the_page_name():
    assert thumb_name("page_001.png") == "thumb_001.png"
    assert thumb_name("page_012.png") == "thumb_012.png"


def test_an_unexpected_name_still_gets_a_thumbnail_name():
    assert thumb_name("scan.png") == "thumb_scan.png"
    assert thumb_name("") == "thumb_"


def test_the_template_derives_the_same_name_as_the_service():
    """One contract, two languages.

    The service writes ``thumb_003.png`` next to ``page_003.png``; the rail builds
    the URL in JavaScript. If the two derivations drift, every thumbnail 404s and
    the ``@error`` fallback quietly serves full-size pages instead — the rail
    looks fine and costs megabytes.
    """
    template = EXAM_PAGE.read_text(encoding="utf-8")

    match = re.search(r"thumbFor\(url\)\s*\{\s*return String\(url \|\| ''\)\.replace\(/(.+?)/,", template)
    assert match, "the rail no longer derives a thumbnail URL"
    page, thumb = "page_009.png", "thumb_009.png"
    derived = re.sub(match.group(1), thumb.replace("\\", ""), page)
    assert derived == thumb, (
        f"the template derives {derived!r} where the service writes {thumb!r}")
    assert thumb_name(page) == thumb


# ── the thumbnails themselves ────────────────────────────────────────────

@pytest.fixture()
def exam_dir(tmp_path):
    """A miniature ``static/uploads/exams/<id>/`` with one page image."""
    directory = tmp_path / "static" / "uploads" / "exams" / "exam-1"
    directory.mkdir(parents=True)
    Image.new("RGB", (1240, 1754), "white").save(directory / "page_001.png", "PNG")
    return directory


def _app_rooted_at(path):
    app = Flask(__name__)
    app.root_path = str(path)
    return app


def test_a_thumbnail_is_written_next_to_its_page_and_is_much_smaller(exam_dir):
    page = exam_dir / "page_001.png"

    with _app_rooted_at(exam_dir.parents[3]).app_context():
        made = ensure_page_thumbs("exam-1", ["/static/uploads/exams/exam-1/page_001.png"])

    thumb = exam_dir / "thumb_001.png"
    assert made == 1
    assert thumb.exists(), "the rail has nothing to show"
    with Image.open(thumb) as im:
        assert im.width == THUMB_WIDTH
    assert thumb.stat().st_size < page.stat().st_size / 4, (
        "a thumbnail that is not markedly smaller defeats the point of having one")


def test_running_it_again_changes_nothing(exam_dir):
    """The exam route calls this on every load to backfill old exams, so it has
    to be free when there is nothing to do."""
    with _app_rooted_at(exam_dir.parents[3]).app_context():
        assert ensure_page_thumbs("exam-1", ["/static/uploads/exams/exam-1/page_001.png"]) == 1
        assert ensure_page_thumbs("exam-1", ["/static/uploads/exams/exam-1/page_001.png"]) == 0


def test_a_missing_page_is_skipped_and_a_missing_directory_is_not_a_crash(exam_dir):
    with _app_rooted_at(exam_dir.parents[3]).app_context():
        assert ensure_page_thumbs("exam-1", ["/static/uploads/exams/exam-1/page_777.png"]) == 0
        assert ensure_page_thumbs("no-such-exam", ["/static/uploads/exams/no-such-exam/page_1.png"]) == 0
        assert ensure_page_thumbs("exam-1", None) == 0


def test_the_exam_route_backfills_the_thumbnails_of_an_older_exam():
    """Without this call, every exam uploaded before the rail existed shows
    full-size images in it forever."""
    source = STUDENT_ROUTE.read_text(encoding="utf-8")

    assert "from app.services.pdf_service import ensure_page_thumbs" in source
    assert "ensure_page_thumbs(exam_id, exam.get(\"pdf_page_urls\"))" in source, (
        "the exam route no longer backfills thumbnails")


# ── the rail itself ──────────────────────────────────────────────────────

def test_the_rail_shows_thumbnails_of_every_page():
    template = EXAM_PAGE.read_text(encoding="utf-8")
    rail = template[template.index("x-for=\"(u, pi) in pages\""):]
    rail = rail[:rail.index("</template>")]

    assert ":src=\"thumbFor(u)\"" in rail, "the rail points at the full-size page images"
    assert "loading=\"lazy\"" in rail, "every page would load the moment the rail is shown"
    assert "@error" in rail and "$event.target.src = u" in rail, (
        "an exam uploaded before thumbnails existed would show a broken image")
    assert "goToPage(pi + 1)" in rail, "a thumbnail has to be a way to turn the page"


def test_choosing_a_page_follows_the_question_that_owns_it():
    """The student is thinking "page 7", not "question 3's page 1"."""
    template = EXAM_PAGE.read_text(encoding="utf-8")
    body = template[template.index("goToPage(p) {"):]
    body = body[:body.index("\n        },")]

    assert "const owner = this.pageOwner(p)" in body, "the page's question is never looked up"
    assert "this.currentQ = owner" in body, "choosing a page in another question leaves the wrong question open"


def test_switching_question_keeps_a_page_that_already_belongs_to_it():
    template = EXAM_PAGE.read_text(encoding="utf-8")
    watcher = template[template.index("this.$watch('currentQ'"):]
    watcher = watcher[:watcher.index("// The middle of an exam")]

    assert "rng.indexOf(this.page) === -1" in watcher, (
        "the watcher moves the paper on every question switch, which discards a "
        "page the student chose")


def test_the_chosen_layout_survives_the_next_exam():
    template = EXAM_PAGE.read_text(encoding="utf-8")

    for key in ("sg_exam_layout", "sg_exam_rail_mode", "sg_exam_rail_hidden", "sg_exam_nav_hidden"):
        assert key in template, f"{key} is never read or never written"
        assert template.count(key) >= 2, f"{key} is remembered but never read back"
    assert "_remember('sg_exam_layout'" in template


def test_the_column_can_be_hidden_and_brought_back():
    """Hiding the sidebar must not be a one-way door: the strip that restores it
    is the only reason hiding is safe to offer."""
    template = EXAM_PAGE.read_text(encoding="utf-8")

    assert "x-show=\"!chromeHidden\"" in template, "the column cannot be hidden"
    assert template.count("x-show=\"chromeHidden\"") >= 1, "nothing brings the column back"
    assert "toggleChrome()" in template
    # The rail's page list and the question list share one column, so hiding the
    # rail never hides the way to answer a question.
    assert "setRailMode('questions')" in template and "setRailMode('pages')" in template


def test_a_binding_names_values_not_methods():
    """`chromeHidden` was written as a method and used bare in `x-show`.

    A bare method name is a function object, which is truthy, so `!chromeHidden`
    read as false for ever: the left column was created, styled, and never shown —
    and every source-level assertion about the markup still passed. This walks
    every expression in the page and fails when it names a method without calling
    it.
    """
    template = EXAM_PAGE.read_text(encoding="utf-8")
    component = template[template.index("function examApp("):]
    methods = set(re.findall(r"^        (?:async )?([A-Za-z_$][\w$]*)\s*\([\w\s,=.$]*\)\s*\{",
                             component, re.M))
    methods.discard("init")  # Alpine's own hook, never bound in the markup

    offenders = []
    for expression in re.findall(r'(?:x-show|x-text|x-html|x-if|:class|:style)="([^"]*)"', template):
        for name in re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)(?!\s*\()", expression):
            if name in methods:
                offenders.append((name, expression[:60]))

    assert not offenders, (
        "these bindings read a method as a value, which is always truthy: "
        + "; ".join(f"{n} in {e!r}" for n, e in offenders))


def test_the_control_row_speaks_both_languages():
    template = EXAM_PAGE.read_text(encoding="utf-8")

    for pair in ("t('Klasik','Classic')", "t('Halaman','Pages')", "t('Soal','Questions')"):
        assert pair in template, f"{pair} left the exam page, so the toggle is half-translated"
    assert "t('Sembunyikan kolom','Hide column')" in template
    assert "t('Tampilkan kolom','Show column')" in template


# ── the layout the page is written to be ─────────────────────────────────

class _Tags(HTMLParser):
    VOID = {"img", "input", "br", "hr", "meta", "link", "source",
            "area", "base", "col", "embed", "param", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.unmatched = 0
        self.marks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = " ".join(attrs.get("class", "").split())
        if tag in ("div", "button", "form", "template"):
            self.marks.append((self.depth, tag, classes))
        if tag not in self.VOID:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        self.depth -= 1
        if self.depth < 0:
            self.unmatched += 1
        self.depth = max(0, self.depth)


def _content_block_marks():
    source = EXAM_PAGE.read_text(encoding="utf-8")
    block = re.search(r"{% block content %}(.*?){% endblock %}", source, re.S)
    assert block, "the exam page no longer defines a content block"
    # Jinja out: the branches wrap whole elements, so what is left is the DOM a
    # student is served either way.
    body = re.sub(r"{%}.+?{%}", "", re.sub(r"{[%#].*?[%#]}", "", block.group(1), flags=re.S), flags=re.S)
    parser = _Tags()
    parser.feed(body)
    return parser


def test_the_markup_of_the_exam_page_balances():
    """A stray end tag is not an error anywhere — Jinja renders it, the browser
    ignores it, and the layout silently becomes a different one."""
    parser = _content_block_marks()

    assert parser.unmatched == 0, f"{parser.unmatched} end tag(s) close nothing"
    assert parser.depth == 0, f"{parser.depth} element(s) are left open at the end of the block"


def test_the_paper_sits_beside_the_column_not_under_it():
    """The defect this test exists for: one extra ``</div>`` closed the layout row
    before the main content, so ``flex-1`` did nothing and the paper was laid out
    below the sidebar."""
    parser = _content_block_marks()
    layout = next(((d, c) for d, t, c in parser.marks if t == "div" and "flex gap-3" in c), None)
    main = next(((d, c) for d, t, c in parser.marks if t == "div" and "flex-1 min-w-0" in c), None)

    assert layout, "the exam page has no layout row"
    assert main, "the exam page has no main column"
    assert main[0] == layout[0] + 1, (
        "the main column is not a child of the layout row, so the sidebar and the "
        "paper are not side by side")
