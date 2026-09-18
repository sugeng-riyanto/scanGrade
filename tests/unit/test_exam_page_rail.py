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
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image
from flask import Flask

ROOT = Path(__file__).resolve().parents[2]
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
BASE_PAGE = ROOT / "app" / "templates" / "base.html"
STUDENT_ROUTE = ROOT / "app" / "routes" / "student.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to execute the getter")


def source(path):
    return path.read_text(encoding="utf-8")

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


# ── the phone ────────────────────────────────────────────────────────────────
#
# A student sits the exam on a phone. Measured at a real 320px viewport, the page
# spent its width and height on chrome instead of on the answer: the toolbar
# wrapped to 205px of an 800px screen, the collapsed column charged 42px of a
# 320px screen for a button nobody had pressed, and the fifth option of every
# multiple-choice question was pushed onto a line of its own. Each of those is
# a few numbers in a stylesheet, which is exactly what a later edit will not miss.

# The option row's own content width on a 320px screen, measured after the
# changes below. Five 44px targets plus four gaps have to fit inside it.
ROW_WIDTH_AT_320 = 244

# Where the paper's left edge lands on a phone: 16px of page padding plus 16px
# of paper padding. A floating control inside that gutter covers no paper.
PAPER_GUTTER_AT_320 = 35


def _phone_stylesheet():
    """The page's `@media (max-width: 640px)` rules — where a phone's layout
    lives, as opposed to the rules a laptop sees."""
    source = EXAM_PAGE.read_text(encoding="utf-8")
    style = re.search(r"<style>(.*?)</style>", source, re.S)
    assert style, "the exam page no longer carries its own stylesheet"
    blocks = re.findall(r"@media\s*\(\s*max-width:\s*640px\s*\)\s*\{(.*?)\n\}",
                        style.group(1), re.S)
    assert blocks, "the exam page has no phone stylesheet"
    return "\n".join(blocks)


def _declarations(css, selector):
    """The body of `selector { … }`, whitespace-normalised ('' when absent)."""
    # Comments go first: this stylesheet explains each rule above it, and a
    # comment left in place is absorbed into the selector it precedes.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r"(?m)^\s*([^{}]+?)\s*\{([^{}]*)\}", css):
        if selector in [s.strip() for s in m.group(1).split(",")]:
            return " ".join(m.group(2).split())
    return ""


def _pixels(declarations, prop):
    m = re.search(rf"(?:^|;)\s*{re.escape(prop)}:\s*([\d.]+)px", declarations)
    return float(m.group(1)) if m else None


def test_five_options_fit_on_one_line_on_the_narrowest_phone():
    """Five options at the 44px floor need 244px of row; at the wide-screen gap
    of 8px they need 252 and the fifth wraps — so a five-option question read as
    two answers instead of one, on every question."""
    template = EXAM_PAGE.read_text(encoding="utf-8")
    assert 'class="opt-row flex flex-wrap gap-2"' in template, (
        "the option row lost the hook the phone stylesheet targets")

    css = _phone_stylesheet()
    gap = _pixels(_declarations(css, ".opt-row"), "gap")
    floor = _pixels(_declarations(css, ".opt-btn"), "min-width")
    assert gap is not None and floor is not None, "the phone no longer tightens the option row"
    assert floor >= 44, "an option bubble is below the 44px floor a thumb hits"
    assert 5 * floor + 4 * gap <= ROW_WIDTH_AT_320, (
        f"five options need {5 * floor + 4 * gap}px but the row is {ROW_WIDTH_AT_320}px wide "
        f"at 320px, so the fifth one wraps onto its own line")
    assert _pixels(_declarations(css, ".opt-btn"), "min-height") >= 44


def test_the_collapsed_column_costs_the_paper_no_width():
    """The strip's only job is to bring the column back, and it was a flex item:
    it charged a phone 30px plus the 12px gap whether or not the student ever
    opened the column. Out of the flow it costs nothing, and it lives in the
    gutter the page's own padding leaves, so it covers no paper either."""
    css = _phone_stylesheet()
    strip = _declarations(css, ".exam-qstrip")
    assert strip, "the collapsed column is back in the flow on a phone"
    assert "position: fixed" in strip, (
        "the strip still takes layout width, which is the paper's width")

    left = _pixels(strip, "left")
    width = _pixels(strip, "width")
    assert left is not None and width is not None
    assert left + width <= PAPER_GUTTER_AT_320, (
        f"the floating strip reaches {left + width}px, past the paper's edge at "
        f"{PAPER_GUTTER_AT_320}px — it now covers the answer it floats over")


def test_the_drawing_toolbar_is_one_row_on_a_phone():
    """Eight drafting tools at 44px wrapped to four rows, 205px of an 800px
    screen — a quarter of the paper spent on the toolbar. One scrolling row keeps
    every tool and gives the paper the height back."""
    template = EXAM_PAGE.read_text(encoding="utf-8")
    toolbar = re.search(r'class="(draw-toolbar[^"]*)"', template)
    assert toolbar, "the drawing toolbar is gone"
    assert "flex-wrap" not in toolbar.group(1), (
        "the toolbar names `flex-wrap` in its class list as well as in the "
        "stylesheet, so source order decides which wins")

    css = _phone_stylesheet()
    decls = _declarations(css, ".draw-toolbar")
    assert "flex-wrap: nowrap !important" in decls, "the phone toolbar still wraps"
    assert "overflow-x: auto" in decls, (
        "one row with no scroller hides every tool past the edge")
    assert _pixels(_declarations(css, ".draw-toolbar button"), "min-height") >= 44, (
        "a drawing tool is below the 44px floor a thumb hits")


def test_the_calculator_keys_meet_the_floor_on_both_axes():
    """Height alone was not enough: the header's close key measured 9px wide —
    and a key that is 44 tall and 9 wide is not a key."""
    css = _phone_stylesheet()
    keys = _declarations(css, ".sg-calc button")
    assert _pixels(keys, "min-height") >= 44, "calculator keys are under the thumb floor"
    assert _pixels(keys, "min-width") >= 44, (
        "calculator keys pass the height floor and fail the width one, which is how "
        "a 9px-wide close key shipped")


def test_the_exam_bar_compacts_on_a_phone():
    """The bar is sticky, so its height is a permanent tax: 154px of an 800px
    screen, held by four short rows. It is the timer and Submit, so it stays —
    it just stops spending a line on the padding."""
    template = EXAM_PAGE.read_text(encoding="utf-8")
    assert "sg-exam-head" in template and "sg-submit" in template, (
        "the phone has no hook to compact the exam bar with")
    css = _phone_stylesheet()
    bar = _declarations(css, ".sticky-topbar")
    assert bar, "the phone no longer compacts the exam bar"
    top, _right = (float(v) for v in re.search(r"padding:\s*([\d.]+)px\s+([\d.]+)px", bar).groups())
    assert top < 12, f"the phone bar still pays the desktop's 12px padding ({top}px)"


class _TextBindingWitness(HTMLParser):
    """Does any `x-text`/`x-html` sit on an element that also holds elements?

    The binding assigns ``textContent``, which deletes every child element. The
    two badges inside each question button were deleted that way on every load,
    and their directives — now attached to detached nodes — then threw an
    uncaught ``ReferenceError`` per question: 25 of them on a five-question exam,
    with the badges that say "this one is an essay" and "this one is on page 3"
    never rendering at all. Nothing failed; the console did.
    """

    BINDINGS = ("x-text", "x-html")

    def __init__(self):
        super().__init__()
        self.stack = []
        self.offenders = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for frame in self.stack:
            if frame[1]:
                frame[2] += 1
                break
        if tag in _Tags.VOID:
            return
        binding = next((b for b in self.BINDINGS if b in attrs), None)
        self.stack.append([tag, binding, 0, " ".join(attrs.get("class", "").split())])

    def handle_endtag(self, tag):
        if tag in _Tags.VOID:
            return
        while self.stack:
            frame = self.stack.pop()
            if frame[1] and frame[2]:
                self.offenders.append((frame[1], frame[0], frame[3][:44], frame[2]))
            if frame[0] == tag:
                break


def test_a_text_binding_never_sits_on_an_element_that_has_children():
    source = EXAM_PAGE.read_text(encoding="utf-8")
    body = re.sub(r"{[%#].*?[%#]}", "", source, flags=re.S)  # Jinja out
    witness = _TextBindingWitness()
    witness.feed(body)

    assert not witness.offenders, (
        "these bindings replace textContent, so they delete the children below "
        "them and their directives then run on detached nodes: "
        + "; ".join(f"{b} on <{t} class={c!r}> with {n} child element(s)"
                    for b, t, c, n in witness.offenders))


def test_a_subject_the_teacher_never_set_is_not_printed_as_the_word_none():
    """Jinja renders a `None` as the word ``None``. An exam whose teacher never
    filled in a subject therefore told the student ``None · 5 questions`` — and
    three of the four exams in this database had a null subject, so it was the
    normal case rather than an edge one."""
    templates = ROOT / "app" / "templates"
    allowed = {("student/dashboard.html", "{{ area.subject }}")}  # route substitutes "Umum"
    offenders = []
    for path in sorted(templates.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\{\{\s*([\w.]+)\s*\}\}", text):
            expr = m.group(1)
            if not expr.endswith(".subject"):
                continue
            before = text[max(0, m.start() - 120):m.start()]
            if "{% if " + expr + " %}" not in before:
                offenders.append((path.relative_to(templates).as_posix(), "{{ " + expr + " }}"))

    assert set(offenders) <= allowed, (
        "these templates print a subject that may be null as the word None: "
        + "; ".join(f"{f} {e}" for f, e in offenders))

    # the one exemption is only truthful while the route still substitutes one
    route = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")
    assert re.search(r'"subject":\s*exam\.get\("subject"\)\s*or\s*"Umum"', route), (
        "the dashboard's weak-area card now prints a null subject as None")


# ── the touch floor is a question about the finger, not the width ──────────
#
# Every mobile rule on this page was written under `@media (max-width: 640px)`,
# which is a proxy for "a finger is doing the tapping". Measured at 768px — a
# tablet, above the breakpoint, so none of those rules applied — the calculator's
# keys were **16px tall with an 8px-wide close button** and the drawing tools
# **25px**: the same defect on a bigger screen. The floor has to be asked as
# `pointer: coarse`, so a mouse desktop keeps its compact panel and any touch
# screen gets targets a finger can hit.

STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)


def _media_blocks(css):
    """Every `@media (...)` block: its condition and its own body.

    Braces are counted rather than matched with a non-greedy regex — an `@media`
    holding another one would otherwise be read as ending at the inner block's
    first `}`, and the rules after it would silently vanish from the assertion.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # comments hold braces and colons
    blocks, i = [], 0
    while True:
        m = re.compile(r"@media\s*([^{]+)\{").search(css, i)
        if not m:
            return blocks
        depth, j = 0, m.end() - 1
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        blocks.append((" ".join(m.group(1).split()), css[m.end():j]))
        i = j + 1


def _page_media():
    source = EXAM_PAGE.read_text(encoding="utf-8")
    style = "\n".join(STYLE_BLOCK.findall(source))
    assert style.strip(), "the exam page's <style> block is gone"
    return _media_blocks(style)


def _declaration(body, selector, prop):
    """The value `selector` gives `prop`, or None.

    A selector may be named in more than one rule inside a block; the last wins,
    which is what the cascade does.
    """
    value = None
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        selectors = [s.strip() for s in m.group(1).split(",")]
        if selector not in selectors:
            continue
        for d in m.group(2).split(";"):
            if ":" not in d:
                continue
            name, _, val = d.partition(":")
            if name.strip() == prop:
                value = val.strip()
    return value


def _touch_blocks():
    return [(c, b) for c, b in _page_media() if "pointer: coarse" in c and "min-width" in c]


def test_a_touch_screen_above_the_phone_breakpoint_still_gets_the_44px_floor():
    blocks = _touch_blocks()
    assert blocks, (
        "no `@media (pointer: coarse)` block above the phone breakpoint: the "
        "width query is back to standing in for the finger, and a 768px tablet "
        "keeps the 16px calculator keys it was measured at")

    bodies = [(c, b) for c, b in blocks]
    for selector in (".sg-calc button", ".draw-toolbar button"):
        for axis in ("min-height", "min-width"):
            found = [v for _c, b in bodies if (v := _declaration(b, selector, axis))]
            assert any(v.replace("!important", "").strip() == "44px" for v in found), (
                f"{selector} has no 44px {axis} on a touch screen wider than 640px "
                f"(reads: {found!r}) — both axes, or a 44x8 control is not a control")

    # the option bubbles are tapped too, and they were the other 30px control
    assert any(_declaration(b, ".opt-btn", "min-height") for _c, b in bodies), (
        "the OMR bubbles lost their touch floor above the phone breakpoint")


def test_the_bigger_touch_targets_do_not_buy_their_size_with_rows():
    """At 768px the 44px floor wrapped the tools to three rows — 157px, a fifth
    of the screen, where the same tools at 25px fitted in two. Raising a target
    is not a reason to charge the paper more room: on touch the row is swiped.
    """
    for condition, body in _touch_blocks():
        assert _declaration(body, ".draw-toolbar", "flex-wrap") == "nowrap !important", (
            f"the touch toolbar wraps again under `{condition}`, so the 44px tools "
            "stack into rows instead of scrolling")
        assert (_declaration(body, ".draw-toolbar", "overflow-x") or "").startswith("auto"), (
            f"the touch toolbar stopped scrolling under `{condition}`, so a tool "
            "can be pushed off the row with no way to reach it")


def test_the_phone_rules_and_the_touch_rules_describe_the_same_floor():
    """Two blocks, one standard: a divergence means a phone and a tablet disagree
    about what a finger can hit, and only one of them can be right."""
    phone = [(c, b) for c, b in _page_media() if "max-width: 640px" in c]
    assert phone, "the phone media queries are gone"

    def floor(selector, prop, blocks):
        return {_declaration(b, selector, prop) for _c, b in blocks
                if _declaration(b, selector, prop)}

    for selector in (".sg-calc button", ".draw-toolbar button", ".opt-btn"):
        for axis in ("min-height", "min-width"):
            wide = floor(selector, axis, _touch_blocks())
            narrow = floor(selector, axis, phone)
            assert wide == narrow or not narrow, (
                f"{selector}'s {axis} is {narrow or 'unset'} on a phone but "
                f"{wide or 'unset'} on a touch tablet")


# ── where the controls that answer the paper are drawn ───────────────────
#
# A paper answer sheet is read with the sheet on the right and the bubbles in a
# column beside it on the left, both in view at once. That is what the "left"
# setting buys, and it is a setting rather than a replacement: on a phone a
# column beside the paper leaves the paper under a third of itself, which is the
# measurement that already put the question column behind a drawer there.

SIDE_LAYOUT_CLASSES = ("answer-bubbles", "answer-medium", "answer-wide")
SIDE_BREAKPOINT = "min-width: 641px"


def _media_with(condition):
    return [(c, b) for c, b in _page_media() if condition in c]


def _every_selector(blocks):
    """Every selector written anywhere in these blocks."""
    out = []
    for _condition, body in blocks:
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
            out.extend(s.strip() for s in m.group(1).split(","))
    return out


def _stylesheet_outside_the_wide_block():
    """The page's stylesheet with the `min-width: 641px` block cut out of it.

    Reading only the `@media` blocks is not enough, and this is the whole point:
    a rule written at the top level belongs to no block, so a scan of blocks
    cannot see it — which is how the first version of this layout shipped with
    its rules outside the query and a phone obeying them. The braces are counted
    rather than matched, because a nested block would end the slice early.
    """
    source = EXAM_PAGE.read_text(encoding="utf-8")
    style = re.sub(r"/\*.*?\*/", "", "\n".join(STYLE_BLOCK.findall(source)), flags=re.S)
    m = re.search(r"@media\s*\(\s*min-width:\s*641px\s*\)\s*\{", style)
    if not m:
        return style
    depth, j = 0, m.end() - 1
    while j < len(style):
        if style[j] == "{":
            depth += 1
        elif style[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return style[:m.start()] + style[j + 1:]


def test_the_side_layout_is_declared_only_where_it_can_be_paid_for():
    """Measured defect: the first version of this block scoped the *button* to
    641px and left the layout rules outside the query, so a phone with `left`
    stored obeyed it — at 375px the paper was **179px** wide with the answer
    strip holding 152 of the 375. A preference a device cannot honour has to be
    refused by the stylesheet, not only by the control that offers it.
    """
    wide = _media_with(SIDE_BREAKPOINT)
    assert wide, f"no `@media ({SIDE_BREAKPOINT})` block on the exam page"

    inside = set(_every_selector(wide))
    assert any("exam-stage--left" in s for s in inside), (
        "nothing puts the answers beside the paper at any width")

    # The other direction, over the whole stylesheet rather than the blocks:
    # nothing that changes *layout* may sit outside that query.
    rest = _stylesheet_outside_the_wide_block()
    outside = [s.strip() for s in re.findall(r"([^{}]+)\{", rest)
               if "exam-stage--left" in s or any(k in s for k in SIDE_LAYOUT_CLASSES)]
    assert not outside, (
        "these side-layout rules are outside the min-width query, so a phone "
        f"obeys a preference it has no room for: {outside}")


def test_the_control_is_offered_exactly_where_the_layout_exists():
    """A switch that does nothing is worse than no switch: the student taps it,
    the page does not change, and nothing says why."""
    wide = _media_with(SIDE_BREAKPOINT)
    assert any(_declaration(b, ".exam-side-ctl", "display") == "flex" for _c, b in wide), (
        "the side control is never shown, so the setting cannot be reached")
    # Outside the block the control may only be *hidden*: a `display: flex` there
    # is the switch that does nothing on the screen it cannot serve.
    rest = _stylesheet_outside_the_wide_block()
    hidden = _declaration(rest, ".exam-side-ctl", "display")
    assert hidden == "none", (
        f"the side control reads `display: {hidden}` outside the min-width query, so a "
        "phone is offered a layout it has no room for")

    template = EXAM_PAGE.read_text(encoding="utf-8")
    assert 'class="exam-side-ctl' in template, "the control is not in the markup"
    assert "setAnswersSide('left')" in template and "setAnswersSide('below')" in template, (
        "the control does not set both arrangements")


def test_the_setting_is_remembered_per_device():
    """Named in full on both sides. A substring check is satisfied by any other
    occurrence of the same expression — mutating the *read* key to
    `sg_exam_answers_side_old` leaves the string present and the setting dead."""
    template = EXAM_PAGE.read_text(encoding="utf-8")
    assert "localStorage.getItem('sg_exam_answers_side')" in template, (
        "the choice is never read back, so it is not remembered")
    assert "_remember('sg_exam_answers_side', this.answersSide)" in template, (
        "the choice is never written")


def test_both_halves_of_the_stage_move_together():
    """One wrapper, so all five kinds move with it. A page that renders the
    controls twice and hides one would leave a kind behind in the wrong place."""
    parser = _content_block_marks()
    by_class = {}
    for depth, tag, classes in parser.marks:
        for token in classes.split():
            by_class.setdefault(token, []).append((depth, tag))

    stage = by_class.get("exam-stage")
    assert stage and len(stage) == 1, "the exam page has no single layout stage"
    stage_depth = stage[0][0]
    for token in ("exam-paper", "exam-answers"):
        marks = by_class.get(token)
        assert marks, f"{token} is not in the markup, so the stage holds nothing on that side"
        assert marks[0][0] == stage_depth + 1, (
            f"{token} is not a child of the stage, so it does not move with it")

    template = EXAM_PAGE.read_text(encoding="utf-8")
    assert ":class=\"answerWidthClass\"" in template, (
        "the answer column no longer asks for the width its control needs")
    assert "answersSide === 'left' ? 'exam-stage--left' : ''" in template, (
        "the stage no longer takes the side layout as a whole class string")


def test_each_kind_gets_the_column_it_needs():
    """A strip of bubbles needs a fraction of what a matching board or an essay
    does. The class is chosen from whole literals per branch, because a name
    assembled in JavaScript compiles to nothing."""
    template = EXAM_PAGE.read_text(encoding="utf-8")
    body = re.search(r"get answerWidthClass\(\)\s*\{(.*?)\n        \},", template, re.S)
    assert body, "the getter that picks the answer column's width is gone"
    branches = {
        cls: next((line for line in body.group(1).splitlines() if f"'{cls}'" in line), "")
        for cls in SIDE_LAYOUT_CLASSES
    }
    assert "'essay'" in branches["answer-wide"], (
        "an essay is typed into and drawn on, so it needs the widest column")
    for kind in ("'match'", "'dragdrop'", "'ordering'"):
        assert kind in branches["answer-medium"], (
            f"a {kind.strip(chr(39))} question is drawn into the bubble strip's width")
    assert "return 'answer-bubbles';" in body.group(1), (
        "a choice or a true/false question has no width of its own")

    wide = _media_with(SIDE_BREAKPOINT)
    for cls in SIDE_LAYOUT_CLASSES:
        found = [b for _c, b in wide if _declaration(b, f".exam-stage--left .{cls}", "width")]
        assert found, f".{cls} has no width in the side layout"
    for cls in ("answer-medium", "answer-wide"):
        caps = [b for _c, b in wide if _declaration(b, f".exam-stage--left .{cls}", "max-width")]
        assert caps, f".{cls} can grow past the paper it is answering"
        for body_ in caps:
            cap = float(_declaration(body_, f".exam-stage--left .{cls}", "max-width").rstrip("%"))
            assert cap <= 50, (
                f".{cls} may take {cap}% of the row, so the paper is no longer the majority")


def test_the_bubbles_claim_the_column_they_are_given():
    """Measured defect: the option row is a flex item of a `flex items-center
    flex-wrap` line, so without `width: 100%` the row is only as wide as its
    widest button — 54px inside a 152px column — and `stretch` then has nothing
    to stretch to. The strip has to claim the column before it can fill it."""
    wide = _media_with(SIDE_BREAKPOINT)
    rows = [b for _c, b in wide
            if _declaration(b, ".exam-stage--left .exam-answers .opt-row", "width") == "100%"]
    assert rows, "the option row shrinks to its widest button inside the column"
    for body_ in rows:
        assert _declaration(body_, ".exam-stage--left .exam-answers .opt-row",
                            "flex-direction") == "column", (
            "A B C D E are laid out in a row, which is the arrangement the column exists to change")


def test_the_answers_follow_the_paper_while_it_scrolls():
    """A paper can be four times the height of the card beside it, and a column
    that scrolls out of sight is the scrolling this layout exists to remove."""
    wide = _media_with(SIDE_BREAKPOINT)
    sticky = [b for _c, b in wide
              if _declaration(b, ".exam-stage--left .exam-answers", "position") == "sticky"]
    assert sticky, "the answer column scrolls away from the question it answers"

    tops = [float(_declaration(b, ".exam-stage--left .exam-answers", "top").rstrip("px"))
            for b in sticky if _declaration(b, ".exam-stage--left .exam-answers", "top")]
    assert tops, "the sticky column has no `top`, so it sticks at 0 and hides under the exam bar"
    # Measured: the exam bar is 70px tall at 768 and 74px from 1024 up, one row.
    for top in tops:
        assert 78 <= top <= 96, (
            f"the card sticks at {top}px; the exam bar is 74px tall, so under that "
            "the card hides behind it and above it there is dead space")


def test_the_side_controls_speak_both_languages():
    template = EXAM_PAGE.read_text(encoding="utf-8")
    for pair in ("t('Bawah','Below')", "t('Kiri','Left')", "t('Jawaban','Answers')"):
        assert pair in template, f"{pair} left the answer-column control untranslated"


# ── the sitting is one room, and the door is the Submit button ───────────
#
# The ScanGrade chrome is three ways off the page a student is being assessed on
# — the sidebar, the top bar, and the phone's bottom nav — and on a 1366px laptop
# the sidebar alone was 256px of the width the paper and the options needed. All
# three are hidden for the sitting, which is also the honest reading of the
# anti-cheat this page enforces: leaving *is* a counted violation, so the app
# should not offer the door it is counting.
#
# The hooks are a contract, not a shape. A rule written as `body > div > aside`
# is turned off silently by one extra wrapper, and "the sidebar is hidden during
# an exam" is not a thing a test can see — so the regions are named, and this
# asserts the two files agree on the names.

APP_HOOKS = ("sg-app-sidebar", "sg-app-topbar", "sg-app-bottomnav")


def _style_of(path):
    """The page's stylesheet, comments removed.

    A comment is not a rule: the block above documents the DOM-shape rule it
    retired, and a scan that reads commentary fails on its own documentation.
    """
    return re.sub(r"/\*.*?\*/", "", "\n".join(STYLE_BLOCK.findall(source(path))), flags=re.S)


def _chrome_rule():
    """The selector list and the body of the `body.sg-exam` rule."""
    style = _style_of(EXAM_PAGE)
    m = re.search(r"([^{};]*body\.sg-exam[^{};]*)\{([^{}]*)\}", style)
    assert m, "the exam page has no `body.sg-exam` rule, so the app chrome stays on screen"
    return m.group(1), m.group(2)


class TestTheSittingHidesTheAppChrome:
    def test_the_page_asks_for_it_and_base_html_answers(self):
        base = source(BASE_PAGE)
        exam = source(EXAM_PAGE)

        assert "{% block body_class %}" in base, (
            "base.html offers no way for a page to name its own body, so this page "
            "cannot ask to be left alone")
        assert "{% block body_class %}sg-exam{% endblock %}" in exam, (
            "the exam page does not ask for the chrome-free body")
        # The hook has to be on the body tag, not merely somewhere in the file:
        # a class rendered into the wrong element hides nothing.
        body_tag = re.search(r"<body[^>]*>", base, re.S)
        assert body_tag and "body_class" in body_tag.group(0), (
            "the body_class block is not on the <body> tag, so the class never lands on it")

        selectors, declaration = _chrome_rule()
        for hook in APP_HOOKS:
            assert f".{hook}" in selectors, (
                f"the rule does not hide .{hook}, so that way off the page stays open")
            assert hook in base, (
                f"the exam page hides .{hook}, which base.html does not name — the two "
                "files have drifted apart and this rule hides nothing")
        assert "display: none" in declaration, (
            f"the rule names the chrome but does not hide it: {declaration.strip()}")

        # The sidebar's scrim is teleported to <body>, so it is not a child of the
        # aside and survives the selector above; left visible it dims the whole
        # sitting with nothing able to dismiss it.
        assert "sidebar-overlay" in selectors, (
            "a stray `sidebarOpen` leaves the sidebar's own scrim over the paper")

    def test_it_applies_at_every_width(self):
        """A phone hides the same chrome a laptop does — the phone's version of it
        is the bottom nav. Inside a media query this would only be true at one end."""
        selectors, _declaration = _chrome_rule()
        for _condition, body in _page_media():
            for hook in APP_HOOKS:
                assert hook not in body, (
                    f".{hook} is hidden inside a media query, so the chrome is back at "
                    "every other width")
        assert selectors.strip(), "the rule has no selectors"

    def test_the_language_control_a_sitting_needs_is_still_reachable(self):
        """The app's own language buttons live in the chrome this page hides. The
        exam page draws three of its own for exactly that reason, and hiding the
        chrome without them would leave bilingual copy nobody can switch."""
        exam = source(EXAM_PAGE)
        assert exam.count("setLang(lang === 'id' ? 'en' : 'id')") >= 3, (
            "the chrome-free exam does not carry its own language controls")


class TestTheSideColumnScrollsItself:
    """"Beside the paper" only beats "under it" if the options stay reachable.

    A matching board with a dozen pairs, a chip bank, or an essay canvas is taller
    than the window. With the column simply sticky, reaching the bottom of the
    options meant scrolling the whole page away from the question — the scrolling
    this layout exists to remove.
    """

    def test_it_keeps_its_own_scroll_box(self):
        wide = _media_with(SIDE_BREAKPOINT)
        sticky = [b for _c, b in wide
                  if _declaration(b, ".exam-stage--left .exam-answers", "position") == "sticky"]
        assert sticky, "the side layout is gone, so there is no column to scroll"
        for body_ in sticky:
            overflow = _declaration(body_, ".exam-stage--left .exam-answers", "overflow-y")
            assert overflow in ("auto", "scroll"), (
                f"the column reads `overflow-y: {overflow or 'visible'}`, so its bottom is "
                "only reachable by scrolling the question out of view")
            assert _declaration(body_, ".exam-stage--left .exam-answers", "max-height"), (
                "a scroll box with no ceiling is not a scroll box")

    #: Measured on the running page at 1280x800 with the side layout on: the card's
    #: natural top in the flow is 110px, while it *sticks* at 84px. The 26px between
    #: them is the page's own 24px of padding and the exam bar's 12px gap, counted a
    #: second time — which is why a ceiling derived from the stuck offset alone left
    #: the column's bottom 6px under the fold at 1280, 1024 and 1920 alike.
    FLOW_TOP = 110
    SHRINKAGE = (98, 70, 16)  # the same figure on a tablet: 98px, and 120 still covers it

    def test_the_ceiling_clears_both_where_it_sticks_and_where_it_starts(self):
        """`calc(100vh - N)` has to be larger than the card's natural top, not merely
        larger than its stuck offset: sized against the offset, the box is taller than
        the space below it on the page's opening position and the last option hides
        under the fold until the student scrolls the question away."""
        wide = _media_with(SIDE_BREAKPOINT)
        ceilings = []
        for _condition, body in wide:
            top = _declaration(body, ".exam-stage--left .exam-answers", "top")
            height = _declaration(body, ".exam-stage--left .exam-answers", "max-height")
            if not (top and height):
                continue
            stuck = float(top.rstrip("px"))
            m = re.fullmatch(r"calc\(100vh - (\d+)px\)", height.strip())
            assert m, f"the column's ceiling is {height}, which no viewport can measure"
            reserved = int(m.group(1))
            assert reserved >= self.FLOW_TOP + 4, (
                f"the ceiling reserves {reserved}px but the column starts {self.FLOW_TOP}px "
                "down the page, so its bottom sits under the fold")
            assert reserved >= stuck + 8, (
                f"the ceiling reserves {reserved}px for a bar that sticks at {stuck}px")
            assert reserved <= 160, (
                f"the ceiling reserves {reserved}px, which shrinks the column for nothing")
            ceilings.append((stuck, reserved))
        assert ceilings, "the side layout has no sized column at all"

    def test_the_measured_figures_in_the_comment_are_the_ones_that_bite(self):
        """The comment above the rule records three numbers. A stale comment is how
        the next reader reasons from a measurement that is no longer true, so the
        rule is held to the figures the file claims."""
        exam = source(EXAM_PAGE)
        for figure in (f"**{self.FLOW_TOP}px**", "84px", "100vh - 120px"):
            assert figure in exam, f"the comment no longer records {figure}"
        wide = _media_with(SIDE_BREAKPOINT)
        sizes = [_declaration(b, ".exam-stage--left .exam-answers", "max-height")
                 for _c, b in wide]
        assert "calc(100vh - 120px)" in sizes, (
            f"the rule and its own comment disagree: {sizes}")


class TestTheSideLayoutIsTheDefault:
    """The arrangement a paper answer sheet is read in is the one a student gets
    without choosing. The stylesheet refuses it below 641px, so a phone still
    reads `below` — and a stored choice still beats the default."""

    GETTER = re.compile(r"answersSide:\s*(\(function \(\) \{.*?\}\)\(\))", re.S)

    def _run(self, cases):
        m = self.GETTER.search(source(EXAM_PAGE))
        assert m, "the answer-column setting is not read from storage at all"
        script = (
            f"const CASES = {json.dumps(cases)};\n"
            # The template's own expression is an IIFE, so wrapping it in a thunk is
            # what lets each case read a different localStorage rather than the one
            # that happened to be installed when the script loaded.
            f"const read = () => ({m.group(1)});\n"
            "const out = CASES.map((c) => {\n"
            "  globalThis.localStorage = { getItem: () => {\n"
            "    if (c.throws) throw new Error('storage blocked');\n"
            "    return c.value;\n"
            "  } };\n"
            "  return read();\n"
            "});\n"
            "console.log(JSON.stringify(out));\n"
        )
        done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip())

    @needs_node
    def test_a_device_with_no_choice_gets_the_answer_sheet_arrangement(self):
        assert self._run([{"value": None}, {"value": ""}, {"value": "sideways"}]) == [
            "left", "left", "left"
        ]

    @needs_node
    def test_a_stored_choice_beats_the_default(self):
        assert self._run([{"value": "below"}, {"value": "left"}]) == ["below", "left"]

    @needs_node
    def test_storage_that_refuses_to_answer_still_draws_the_options(self):
        """A browser with localStorage blocked (private mode, a locked-down
        school device) must not end up with no setting at all."""
        assert self._run([{"throws": True}]) == ["left"]

    def test_the_setter_writes_what_the_getter_reads(self):
        """`setAnswersSide('below')` has to persist `below`: the getter is written
        as `=== 'below' ? 'below' : 'left'`, so a value it does not recognise — or
        a setter that normalises the other way — silently pins every device to
        the default and makes the control a switch that does nothing."""
        exam = source(EXAM_PAGE)
        assert "this.answersSide = (v === 'left') ? 'left' : 'below';" in exam, (
            "the setter does not normalise the same way the getter reads")
        assert "_remember('sg_exam_answers_side', this.answersSide)" in exam, (
            "the setter does not persist the choice")

    def test_the_default_is_safe_on_a_phone_because_the_stylesheet_says_so(self):
        """The default is now `left` on *every* device, including the one that
        cannot draw it: at 375px the column left the paper 179px. Nothing in the
        setting refuses it — only the `min-width: 641px` query does — so this is
        the assertion that keeps a phone from obeying the default."""
        wide = _media_with(SIDE_BREAKPOINT)
        assert any("exam-stage--left" in s for s in _every_selector(wide)), (
            "the side layout is gone, so a phone would get it or nothing")
        rest = _stylesheet_outside_the_wide_block()
        outside = [s.strip() for s in re.findall(r"([^{}]+)\{", rest)
                   if "exam-stage--left" in s or any(k in s for k in SIDE_LAYOUT_CLASSES)]
        assert not outside, (
            f"a phone now obeys the default it has no room for: {outside}")
