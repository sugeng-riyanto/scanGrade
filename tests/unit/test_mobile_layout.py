"""Every page has to be readable and operable on a phone.

None of this is visible while writing a template on a wide screen, which is how
it got this bad: 546 uses of 8-11px text, ten credential cards that repeated the
same markup ten times and drifted to three different sizes, and copy buttons that
measured **13x28px** — the one control the demo page exists for, too small to hit
with a thumb.

Four rules, each one a defect that was measured rather than imagined:

1. **A legibility floor.** 9-11px classes render at >= 12px because
   app/static/css/theme.css says so, in one place, instead of 546 call sites
   being edited. A size below the floor must be listed here with the reason it
   is exempt.
2. **Wide tables scroll.** A 5-8 column table in a card that clips overflow
   hides the score and the action column off-screen with no way to reach them.
   That was true of 27 of the 58 tables in the app.
3. **Widths are mobile-first.** No unprefixed `w-[Npx]` above a small phone
   width; a fixed sidebar has to be `w-full md:w-[350px]`, not `w-[350px]`.
4. **Tap targets.** The demo page's copy buttons are at least 40px on a side.
5. **A two-column work area collapses.** The grading page gave a phone a 126px
   question rail *beside* the answer, leaving 205px — the student's handwriting
   and the two answer cells measured 171 and 79 there. The rail is a swipeable
   strip above the answer now.

What these tests cannot do is lay the page out at a phone width — this suite has
no browser. They encode the structural rules, and the live checks (computed
font sizes, measured button boxes) were run by hand in the browser and are
recorded in AGENTS.md.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
DEMO = TEMPLATES / "demo.html"
# The floor rules were a `<style>` block in base.html until they moved into the
# stylesheet base.html links. Reading the file base.html points at, rather than
# the template, is what keeps the check honest across that move: a re-inlined
# block would leave floor_rules() reading an empty file and failing there.
THEME_CSS = ROOT / "app" / "static" / "css" / "theme.css"


def app_templates():
    return sorted(TEMPLATES.rglob("*.html"))


COMMENT = re.compile(r"<!--.*?-->|/\*.*?\*/", re.S)


def rendered(path):
    """The template with comments blanked out — a comment paints no text.

    Comments that mention a size, or that name the classes the floor does not
    lift, are read by a naive scan as offences. They are replaced by
    the same number of newlines they contained, which keeps every offset and
    line number valid for the code around them — slicing positions out of a
    comment-stripped string while reading the raw one is how the first version of
    these tests reported a wrapped table as unwrapped.
    """
    text = path.read_text(encoding="utf-8")
    return COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)


# ── 1. the legibility floor ──────────────────────────────────────────────────

SIZE_CLASS = re.compile(r"text-\[(\d+(?:\.\d+)?)px\]")

# Size -> the reason this file is allowed to render text below the floor.
# An exemption is where this kind of check quietly stops working, so each one
# says what it is and why it is not a readability defect.
BELOW_FLOOR_EXEMPT = {
    "tools/generate_answer_sheet.html": (
        "OMR answer-sheet mockup: 4-5px draws a miniature of a printed sheet "
        "([QR], 'Student Name:'), where the size is part of the artefact"
    ),
}


def floor_rules():
    """{px size: replacement font-size} as the stylesheet declares them."""
    assert THEME_CSS.is_file(), (
        f"{THEME_CSS.relative_to(ROOT)} is missing — the floor lives there now")
    return {float(px): size for px, size in
            re.findall(r"\.text-\\\[(\d+)px\\\]\s*\{\s*font-size:\s*([\d.]+)px",
                       THEME_CSS.read_text(encoding="utf-8"))}


class TestTheLegibilityFloor:
    def test_the_stylesheet_declares_the_floor(self):
        rules = floor_rules()
        for px in (6, 7, 8, 9, 10, 11):
            assert px in rules, (
                f"nothing lifts text-[{px}px] to a readable size. The floor is what "
                "keeps 546 call sites from having to be edited by hand."
            )

    def test_the_floor_is_a_readable_size(self):
        for px, size in floor_rules().items():
            assert float(size) >= 10.0, f"text-[{px}px] maps to {size}px, still unreadable"
        assert float(floor_rules()[9]) >= 12.0, "the 9px UI captions must reach 12px"
        assert float(floor_rules()[10]) >= 12.0, "the 10px UI captions must reach 12px"

    def test_nothing_renders_below_the_floor_without_a_reason(self):
        offenders = []
        for path in app_templates():
            rel = path.relative_to(TEMPLATES).as_posix()
            for m in SIZE_CLASS.finditer(rendered(path)):
                px = float(m.group(1))
                if px < 10 and rel not in BELOW_FLOOR_EXEMPT and px not in floor_rules():
                    offenders.append(f"{rel}: {m.group(0)}")
        assert not offenders, (
            "these templates paint text below the floor with no exemption:\n  "
            + "\n  ".join(offenders[:12])
            + "\n\nEither raise the size, add a floor rule in theme.css, or add the "
            "file to BELOW_FLOOR_EXEMPT with the reason it is not a defect."
        )

    def test_every_exemption_is_used_and_explained(self):
        """An exemption that no longer applies is a hole nobody is watching."""
        for rel, reason in BELOW_FLOOR_EXEMPT.items():
            path = TEMPLATES / rel
            assert path.exists(), f"{rel} is exempted but does not exist"
            assert len(reason) > 40, f"{rel} has no real reason recorded"
            sizes = [float(m.group(1)) for m in SIZE_CLASS.finditer(rendered(path))]
            assert any(s < 10 and s not in floor_rules() for s in sizes), (
                f"{rel} is exempted from the floor but no longer uses an unfloored size"
            )


# ── 2. wide tables have to scroll, not clip ──────────────────────────────────

SCROLL_ANCESTOR = re.compile(r"overflow-x-auto|overflow-auto|overflow-x-scroll|table-responsive")

# Documents whose layout is a sheet of paper, printed or exported to PDF. A
# phone is not their target, and adding a scroller would change the print.
NOT_A_VIEWPORT = {
    "monitor.html",
    "print/report_card.html",
    "student/result_detail_pdf.html",
    "teacher/print_exam_report.html",
}


class TestWideTablesCanBeScrolled:
    def _tables(self, path):
        """(offset, is_print_only) for each real <table> in a template.

        A `<table` inside a JavaScript string is not markup — scan.html builds
        its bulk table that way, into a container that already scrolls.
        """
        text = rendered(path)
        for m in re.finditer(r"<table", text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start:text.find("\n", m.start())]
            js = line.lstrip().startswith(("let ", "const ", "var ", "+", '"', "'"))
            if js:
                continue
            # print-only blocks are for the printed copy of a screen page
            before = text[:m.start()]
            yield m.start(), before.rfind('class="print-only"') > before.rfind("{% endblock %}")

    def test_every_app_table_is_inside_a_scroll_container(self):
        offenders = []
        for path in app_templates():
            rel = path.relative_to(TEMPLATES).as_posix()
            if rel in NOT_A_VIEWPORT:
                continue
            text = rendered(path)
            for pos, print_only in self._tables(path):
                if print_only:
                    continue
                before = text[max(0, pos - 900):pos]
                if not SCROLL_ANCESTOR.search(before):
                    line = text.count("\n", 0, pos) + 1
                    offenders.append(f"{rel}:{line}")
        assert not offenders, (
            "these tables sit in a container that clips instead of scrolling, so the "
            "right-hand columns are unreachable on a phone:\n  " + "\n  ".join(offenders[:12])
            + "\n\nUse `overflow-x-auto` on the card and a `min-w-[...]` on the table."
        )

    def test_a_scrolling_table_keeps_a_usable_width(self):
        """Scrolling a squeezed table sideways is still unreadable.

        A `w-full` table inside `overflow-x-auto` does not scroll — it squeezes,
        wrapping every cell to two or three lines. The scroller only does its job
        once the table has a minimum width to overflow.
        """
        offenders = []
        for path in app_templates():
            rel = path.relative_to(TEMPLATES).as_posix()
            if rel in NOT_A_VIEWPORT:
                continue
            text = rendered(path)
            for pos, print_only in self._tables(path):
                if print_only:
                    continue
                # the `min-w` has to be on the opening tag of the table itself
                near = text[pos:text.find(">", pos) + 1]
                if "min-w-[" in near:
                    continue
                # only complain when the table is genuinely wide
                row = re.search(r"<tr[^>]*>(.*?)</tr>", text[pos:pos + 3000], re.S)
                cols = len(re.findall(r"<t[hd]", row.group(1))) if row else 0
                if cols >= 4:
                    line = text.count("\n", 0, pos) + 1
                    offenders.append(f"{rel}:{line} ({cols} cols)")
        assert not offenders, (
            "these tables scroll but have no minimum width, so a phone squeezes them "
            "into ~40px columns instead of scrolling:\n  " + "\n  ".join(offenders[:12])
            + "\n\nAdd `min-w-[<cols * ~110>px]` to the table."
        )


# ── 3. widths are mobile-first ───────────────────────────────────────────────

class TestWidthsAreMobileFirst:
    """`w-[350px]` alone overflows a 320px phone; `w-full md:w-[350px]` does not."""

    FIXED = re.compile(r"(?<![\w:-])w-\[(\d{3,})px\]")

    def test_no_unprefixed_fixed_width_larger_than_a_small_phone(self):
        offenders = []
        for path in app_templates():
            rel = path.relative_to(TEMPLATES).as_posix()
            for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                for m in self.FIXED.finditer(line):
                    if int(m.group(1)) > 320:
                        offenders.append(f"{rel}:{i} {m.group(0)}")
        assert not offenders, (
            "a fixed width wider than a small phone, with no breakpoint in front of "
            "it, cannot fit:\n  " + "\n  ".join(offenders[:10])
            + "\n\nPrefix it (md:w-[350px]) and give it a w-full fallback."
        )


# ── 4. a two-column work area has to collapse ────────────────────────────────

GRADE_DETAIL = TEMPLATES / "teacher" / "grade_detail.html"


class TestATwoColumnWorkAreaCollapsesOnAPhone:
    """The grading page put a rail *beside* the answer on a 375px screen.

    Measured in headless Chrome at 375px with the app's own session cookie: the
    question rail was `shrink-0` at a fixed width, so of the 343px available it
    took **126** and the answer pane was left **205** — the card the teacher
    marks was **171** wide, each of the two answer cells **79**, and a student's
    handwritten canvas the same 171. The rail is a swipeable strip above the
    answer now and the pane gets the full 343 (measured after: pane 343, card
    309, cells 149 — and desktop is unchanged at rail 126 / pane 838 / card 448).

    The breakpoint is `lg` (1024px) because that is where this page's sidebar
    becomes a sidebar; a rail that turns back into a column at `md` would sit
    beside the answer while the navigation still does not.
    """

    def body(self):
        assert GRADE_DETAIL.is_file(), f"{GRADE_DETAIL.name} is missing"
        return GRADE_DETAIL.read_text(encoding="utf-8")

    def test_the_row_stacks_below_the_sidebar_breakpoint(self):
        body = self.body()
        assert 'class="flex flex-col lg:flex-row gap-3"' in body, (
            "the grading row is not `flex-col lg:flex-row`: with a bare `flex` the rail "
            "takes a fixed slice of a phone's width and the answer pane is what is left."
        )
        rail = re.search(r'class="(shrink-0[^"]*)"[^>]*>\s*<div class="[^"]*sticky-questions',
                         body)
        assert rail and "w-full lg:w-auto" in rail.group(1), (
            "the rail wrapper is not `w-full lg:w-auto`, so it does not take the phone's "
            "width when the row stacks."
        )

    def test_the_rail_is_a_swipeable_strip_on_a_phone(self):
        body = self.body()
        card = re.search(r'class="([^"]*sticky-questions[^"]*)"', body)
        assert card, "the rail card is gone"
        cls = card.group(1)
        assert "flex" in cls and "lg:block" in cls, (
            f"the rail card is not `flex ... lg:block` (got: {cls}) — the phone needs one "
            "row, the desktop needs the tab row above a column of numbers."
        )
        assert "overflow-x-auto" in cls and "lg:overflow-visible" in cls, (
            "the rail cannot scroll sideways on a phone, so a 40-question exam's later "
            "numbers are unreachable."
        )
        assert "flex-wrap" not in cls, (
            "the strip wraps instead of scrolling. Measured on the exam page: a wrapped "
            "touch row cost three lines and a fifth of the screen, where a swipeable one "
            "cost none."
        )
        numbers = re.search(r'class="(flex flex-row[^"]*gap-1\.5[^"]*)"', body)
        assert numbers and "lg:flex-col" in numbers.group(1) and "shrink-0" in numbers.group(1), (
            "the number row is not `flex-row lg:flex-col ... shrink-0`: it would either "
            "squash the buttons on a phone or stack them on the desktop."
        )

    def test_the_rail_is_not_sticky_where_it_cannot_afford_to_be(self):
        """A second sticky bar on a phone leaves too little room for the essay.

        The header card above it is already `sticky` and wraps to ~174px at
        375px; a sticky rail under that leaves roughly half of a 900px screen.
        """
        body = self.body()
        assert re.search(
            r"@media\s*\(max-width:\s*1023px\)\s*\{[^}]*\.sticky-questions\s*\{\s*"
            r"position:\s*static", body, re.S), (
            "`.sticky-questions` is still sticky at phone widths — the essay being marked "
            "loses the screen to two stacked sticky bars."
        )
        assert re.search(r"@media\s*\(min-width:\s*1024px\)\s*\{[^}]*"
                         r"max-height:\s*calc\(100vh - 170px\)", body, re.S), (
            "the rail's height cap is not restored at `lg`, where it is a column."
        )

    def test_the_correction_tools_are_one_swipeable_row_on_a_finger(self):
        """Measured at 375px: `flex-wrap: wrap` made the tools **8 rows / 209px**.

        The paper being marked began at **y=652 of 900**, so only **248px** of it
        was on screen — a teacher scrolled past a quarter-screen of chrome to
        reach the work and then lost it again. `nowrap` + `overflow-x: auto`
        inside the coarse-pointer block makes it one row: measured after, the
        toolbar is **65px** and the paper is visible from y=508 (**392px**, +58%),
        and the row scrolls 1024px of tools sideways. It is the same rule the
        exam page's tool row follows, and for the same reason the 44px floor is
        what made it too wide: *raising a target is not a reason to charge the
        paper the room*.

        Read as a `pointer: coarse` rule on purpose. Asked by width instead, a
        mouse desktop would get a swipeable row of buttons it can already reach,
        and a touch tablet (768px, above every width breakpoint) would keep the
        eight rows — measured: with touch emulation off the toolbar is still
        `wrap` at 768px and 1280px.
        """
        body = self.body()
        coarse = re.search(r"@media\s*\(pointer:\s*coarse\)\s*\{(.*?)\n\}", body, re.S)
        assert coarse, "the coarse-pointer block is gone from the grading page"
        block = coarse.group(1)
        assert re.search(r"\.draw-toolbar\s*\{[^}]*flex-wrap:\s*nowrap[^}]*overflow-x:\s*auto",
                         block, re.S), (
            "the correction tools wrap instead of scrolling on a touch screen, so the "
            "paper they exist to mark is pushed off the screen."
        )
        assert re.search(r"\.draw-toolbar\s*>\s*\*\s*\{[^}]*flex:\s*0\s+0\s+auto", block), (
            "the tool row's items can still shrink, so `nowrap` squashes them below the "
            "44px floor instead of scrolling."
        )
        floor = self.body()
        assert re.search(r"\.draw-toolbar button\s*\{\s*min-height:\s*44px", floor), (
            "the tools lost their 44px floor, which is the reason the row cannot fit."
        )

    def test_the_rails_height_cap_is_answered_per_width_not_inline(self):
        """The rail's cap was `style="max-height:calc(100vh - 170px)"`.

        An inline style cannot be answered per width, so the cap written for the
        desktop column also applied to the phone's strip — where a capped,
        vertically-clipped rail is the wrong shape. It is in CSS behind a
        breakpoint now, which is also what let it gain `overflow-y: auto`: the
        cap had no scroll attached, so a 40-question rail painted its buttons
        past the card's own background instead of scrolling.

        This is deliberately *not* a ban on the pattern — four other templates
        cap a height inline on purpose (`base.html`'s sidebar at
        `calc(100vh - 8rem)`, which a phone never shows; the two monitor boxes
        at `320px`/`280px`, both of which set `overflow-y: auto` themselves).
        The rule that keeps is the one this page got wrong.
        """
        body = self.body()
        assert "style=\"max-height:calc(100vh - 170px)\"" not in body, (
            "the rail's height cap is inline again, so it cannot be dropped where the "
            "rail is a horizontal strip."
        )
        assert re.search(r"\.sticky-questions\s*\{[^}]*max-height[^}]*overflow-y:\s*auto",
                         body, re.S), (
            "the rail's cap has no `overflow-y: auto` with it: the cap then means \"paint "
            "out of the box\" rather than \"scroll\", and the buttons land on top of the "
            "answer."
        )


# ── 5. the demo page's own defect ────────────────────────────────────────────

class TestTheDemoCredentialsAreUsable:
    """The page exists so someone can copy an email and sign in on a phone."""

    def test_the_credentials_render_at_a_readable_size(self):
        body = DEMO.read_text(encoding="utf-8")
        for m in SIZE_CLASS.finditer(body):
            px = float(m.group(1))
            assert px >= 10 or px in floor_rules(), (
                f"demo.html uses {m.group(0)}, which no floor covers"
            )
        assert "text-sm font-mono" in body, (
            "the demo credentials are not set at 14px monospace — that was the reported "
            "defect: the address someone has to type was the smallest text on the page."
        )

    def test_the_copy_button_is_a_real_tap_target(self):
        body = DEMO.read_text(encoding="utf-8")
        assert "w-10 h-10" in body, (
            "the copy buttons are not 40px. At 13x28px they were too small to hit with a "
            "thumb, which is the one thing the page is for."
        )
        assert ":aria-label=" in body, (
            "the copy buttons have no accessible name: the icon is decorative, so a "
            "screen reader announces nothing."
        )
