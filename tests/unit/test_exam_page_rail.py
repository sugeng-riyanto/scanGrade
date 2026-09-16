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
