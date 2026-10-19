"""The exam paper gets the width of its *device*, and only the exam paper does.

`base.html` wraps every page's content in one reading column — `p-4 lg:p-6
max-w-7xl mx-auto` — which is right for a page of prose and wrong for a paper. It
was measured on the fixture app at 1440px, on the pupil's own exam page and on the
teacher's preview of the same paper, and both readings were identical: the wrapper
was **1280px centred at x=74**, so **74px of the screen sat empty on each side**
(320px at 1920), while the paper itself got 906px and the question column started
at x=98. The sidebar was not the cause — `body.sg-exam` hides it and `<main>` is
already 1428px wide — the cap was.

The cap belongs to `base.html`, so the fix is the hook the two sides share: the
wrapper carries `sg-content`, and the exam page's own stylesheet turns the cap off
for a sitting (`body.sg-exam .sg-content`). Three properties are what this suite is
for, because each of them is how the change could go wrong:

* the hook has to be on **the element that carries the cap** — a hook one level
  away from the utility it is meant to beat would leave `max-w-7xl` winning, and
  the page would look exactly as it does today;
* the rule has to be **scoped to the sitting's body class**, or every other page
  in the app loses its reading column at once — 136 templates extend `base.html`;
* the **gutter has to stay**. Full width here means the device's, not bled to the
  bezel: the answer card's sticky offsets are measured from that 24px (its natural
  top in the flow is the page's 24px plus the bar's 74px and its 12px gap, which is
  the `110px` and `calc(100vh - 120px)` the page's own comments record).
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = ROOT / "app" / "templates" / "base.html"
EXAM = ROOT / "app" / "templates" / "student" / "take_exam.html"
TEMPLATES = ROOT / "app" / "templates"

#: The wrapper `base.html` builds, as one source line — the element both the cap
#: and the hook are supposed to be on.
WRAPPER_LINE = re.compile(r"<div class=\"([^\"]*max-w-7xl[^\"]*)\"")


#: Comments are read out before anything is asserted against the stylesheet, and
#: that is not tidiness: this page's comments are *why* each rule exists, so they
#: name `.sg-content` and `max-width` in prose. A scan that read them would find a
#: second, unscoped "rule" in a paragraph — the same trap the Tailwind name check
#: documents for a template's prose.
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def exam_css() -> str:
    """The exam page's own `<style>`, comments removed: where a page may restate the app."""
    return COMMENT.sub("", EXAM.read_text(encoding="utf-8"))


def uncapping_rule() -> re.Match | None:
    return re.search(r"(body\.sg-exam \.sg-content)\s*\{([^}]*)\}", exam_css())


# ── the hook is on the element the cap is on ─────────────────────────────────

class TestTheHookNamesTheElementThatCarriesTheCap:
    def test_base_html_puts_the_hook_on_the_reading_column(self):
        match = WRAPPER_LINE.search(BASE.read_text(encoding="utf-8"))
        assert match, "the reading column no longer names `max-w-7xl` in base.html"
        classes = match.group(1).split()
        assert "sg-content" in classes, (
            "the hook is not on the element carrying `max-w-7xl`, so a page cannot "
            "turn the cap off without an `!important` or a DOM-shape selector")
        # The two the rule has to beat, named here so removing one is a decision.
        assert "mx-auto" in classes and "max-w-7xl" in classes

    def test_the_two_sides_spell_the_hook_the_same_way(self):
        """A rename on one side stops the uncapping in silence.

        The hook is a string in two files and nothing else joins them: the wrapper
        would keep `max-w-7xl` and the exam page would go on naming a class no
        element carries, which renders exactly like today's page — 74px of empty
        screen each side, and no error anywhere. Every route that renders a page is
        signed-in, so the check is the name both sides must agree on; the browser
        reading of the real page is the suite's other half (see the file's
        docstring for the numbers).
        """
        classes = WRAPPER_LINE.search(BASE.read_text(encoding="utf-8")).group(1).split()
        hook = [name for name in classes if name.startswith("sg-content")]
        assert hook, "the reading column carries no `sg-content`-style hook"
        assert hook[0] in uncapping_rule().group(1), (
            "the exam page uncaps %r while the wrapper names %r"
            % (uncapping_rule().group(1), hook[0]))


# ── the sitting turns the cap off, and nothing else does ─────────────────────

class TestTheExamPageUncapsItsOwnColumn:
    def test_the_sitting_names_the_hook_and_the_cap(self):
        rule = uncapping_rule()
        assert rule, (
            "the exam page no longer uncaps the content column, so the paper is "
            "back to 74px of empty screen on each side at 1440px")
        body = rule.group(2)
        assert re.search(r"max-width:\s*none", body), (
            "`max-width: none` is what beats Tailwind's `max-w-7xl`")
        assert re.search(r"margin-left:\s*0", body) and re.search(
            r"margin-right:\s*0", body), (
            "`mx-auto` has to be answered too, or the column stays centred in the "
            "width it now has")

    def test_the_rule_is_scoped_to_the_sitting(self):
        selector = uncapping_rule().group(1)
        assert selector.startswith("body.sg-exam "), (
            "the cap is turned off for anything that merely carries the hook, so "
            "every other page loses its reading column")
        # And no *second* rule reaches the hook: the page may write one selector
        # for it, and every one of them has to name the sitting.
        for match in re.finditer(r"([^{}]*\.sg-content[^{}]*)\{([^}]*)\}", exam_css()):
            if "max-width" in match.group(2):
                assert match.group(1).strip().startswith("body.sg-exam "), (
                    "an unscoped rule uncaps the column as well: %r"
                    % match.group(1).strip())

    def test_no_other_template_asks_for_the_hook(self):
        """The hook is a door out of the reading column; one page may use it.

        A second page naming it would inherit a full-bleed layout nothing measured
        — and this is the guard that makes the coupling visible before somebody
        copies the class into a dashboard for the same reason the exam asked.
        """
        users = sorted(
            path.relative_to(TEMPLATES).as_posix()
            for path in TEMPLATES.rglob("*.html")
            if "sg-content" in path.read_text(encoding="utf-8", errors="replace"))
        assert users == ["base.html", "student/take_exam.html"], (
            "a template other than the exam page names the content hook: %s" % users)


# ── full width is the device's, not the bezel's ──────────────────────────────

class TestTheGutterStaysBecauseTheOffsetsAreMeasuredFromIt:
    def test_the_rule_leaves_the_padding_alone(self):
        body = uncapping_rule().group(2)
        assert "padding" not in body, (
            "the rule zeroed the page's 24px gutter; the answer card's sticky "
            "offsets are measured from it (110px in the flow, `calc(100vh - 120px)` "
            "for its own scroll box), so they would have to be re-measured with it")

    def test_the_offsets_that_depend_on_it_are_still_there(self):
        css = exam_css()
        assert "top: 84px" in css, "the answer card's stuck offset moved"
        assert "calc(100vh - 120px)" in css, "the answer card's own height moved"

    def test_the_reading_column_survives_for_every_other_page(self):
        classes = WRAPPER_LINE.search(BASE.read_text(encoding="utf-8")).group(1).split()
        assert "p-4" in classes and "lg:p-6" in classes and "mx-auto" in classes, (
            "the shared column lost its gutter or its centring for the pages that "
            "are not a sitting")
