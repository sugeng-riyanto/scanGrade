"""Pure white and pure black, out of the pages that draw a paper.

The exam page and the marking desk are the only pages in the app that carry a
hand-written `<style>` block for their own controls. A page-local block is not
reached by the utility remap in theme.css — a rule in it says `background: #fff`
in words — so the two things a theme change has to touch were written by hand in
five files:

* **a surface.** `background: white` on a toolbar button is *unreadable* in dark
  mode, because the label inherits the theme's own light `--text`: light on light,
  and nothing errors. This was live on `teacher/grade_detail.html` and
  `teacher/preview_exam.html`, neither of which had a dark block at all.
* **a label.** `color: white` on an accent fill is invisible to every other guard
  in the app, because the utility sweeps read `class="…"` attributes and this is
  not one.

So the four colours those pages need are named in theme.css — `--paper`,
`--paper-ink`, `--on-accent`, `--on-chrome` — and the pages read the token
instead of the literal. Naming them is what makes them auditable: a rule that
reads `var(--paper)` is *one declaration serving both themes*, so the `.dark`
block on the exam page shrank to the two border colours the themes genuinely do
not share, and the correction pages need no `.dark` block at all.

**The paper is not a surface**, and it is the one place a light value is right in
both themes: an answer canvas is the sheet the pupil drew on, the ink on it is
dark, and a sheet that followed the theme into black would hide the answer — the
reasoning that also keeps the OMR mockup light. `--paper-ink` is what makes that
safe: the type and the default pen on the sheet read *it* rather than inheriting
the theme's `--text`, which is the bug where a `<textarea>` on white paper came
out white on white.

`@media print` is the other place a pure white stays correct. Browsers drop
background colours and keep text colours, so a print rule has to pin the sheet
white by hand — theme.css does the same in its own `@media print`. The sweep
below exempts the whole *condition* rather than a list of rules, so a new print
rule is exempt without anyone adding it to a list.

Measured in both themes by the tokens test at the bottom, not chosen by eye.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.test_dark_theme_contrast import contrast, luminance, tokens

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"

#: The pages that draw a paper, with the reason each one is here. The list is
#: *checked against the tree* (below) rather than trusted, because the failure
#: this guard exists for is a page nobody remembered to add.
PAPER_PAGES: dict[str, str] = {
    "student/take_exam.html":
        "the pupil's own paper — the answer canvases, the drawing tools and the "
        "option buttons",
    "teacher/grade_detail.html":
        "the marking desk — the same canvases and toolbar, seen from the teacher's "
        "side, with the annotation boxes over the student's sheet",
    "teacher/grade_question.html":
        "marking one question at a time — it carries the same canvas convention "
        "as the marking desk",
    "student/result_detail.html":
        "the pupil's marked paper — the sheets and both kinds of annotation box",
    "teacher/preview_exam.html":
        "the author's read of the paper, drawn with the same boxes the pupil sees",
}

#: What makes a page one of these: its own CSS names a drawing surface. Used to
#: find the family, so a page added later cannot quietly skip the rule by not
#: being listed.
MARKERS = re.compile(
    r"full-canvas-wrap|essay-page-wrap|draw-toolbar|text-box|student-canvas"
    r"|tool-ctrl-btn|opt-btn")

#: A background or text colour, and nothing else: `box-shadow: … rgba(0,0,0,0.3)`
#: and `border: … rgba(255,255,255,0.15)` are a shadow and an edge, not a surface
#: or a label, and the app's own convention is to paint those with a translucent
#: black or white in both themes.
PAINT = re.compile(r"(?<![-a-z])(background|background-color|color)\s*:\s*([^;{}]+)",
                   re.I)

#: Pure, not merely light: a value that is `#fff`, `white`, `#000`, `black`, or
#: one of those as `rgb()`/`rgba()` at full alpha. A translucent white — the
#: `rgba(255,255,255,.94)` a print sheet uses, or a scrim — is a composite over
#: whatever is behind it, which this check cannot know and does not judge.
PURE = re.compile(
    r"""^(?:
        \#fff(?:fff)? | white | \#000(?:000)? | black
      | rgba?\(\s*255\s*,\s*255\s*,\s*255(?:\s*,\s*(?:1|1\.0+)\s*)?\)
      | rgba?\(\s*0\s*,\s*0\s*,\s*0(?:\s*,\s*(?:1|1\.0+)\s*)?\)
    )$""", re.I | re.X)

MEDIA_PRINT = re.compile(r"@media\s+print\b[^{]*\{", re.I)
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
COMMENT = re.compile(r"/\*.*?\*/", re.S)
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)


def _no_comments(css: str) -> str:
    return COMMENT.sub("", css)


def style_blocks(relative: str) -> str:
    """Every `<style>` block on a page, concatenated."""
    text = (TEMPLATES / relative).read_text(encoding="utf-8", errors="replace")
    return "\n".join(match.group(1) for match in STYLE_BLOCK.finditer(text))


def discovered_paper_pages() -> dict[str, str]:
    """The family, found by shape: a page whose own CSS draws a surface."""
    found: dict[str, str] = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        css = "\n".join(_no_comments(match.group(1))
                        for match in STYLE_BLOCK.finditer(
                            path.read_text(encoding="utf-8", errors="replace")))
        if MARKERS.search(css):
            found[str(path.relative_to(TEMPLATES)).replace("\\", "/")] = css
    return found


def without_print(css: str) -> str:
    """The CSS minus every `@media print { … }`, brace-matched."""
    out, index = [], 0
    while True:
        match = MEDIA_PRINT.search(css, index)
        if not match:
            out.append(css[index:])
            return "".join(out)
        out.append(css[index:match.start()])
        depth, cursor = 0, match.end() - 1
        while cursor < len(css):
            if css[cursor] == "{":
                depth += 1
            elif css[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        index = cursor + 1


def rules(css: str) -> list[tuple[str, str]]:
    """`(selector, body)` pairs, comments removed and selectors split apart."""
    pairs = []
    for group, body in RULE.findall(_no_comments(css)):
        group = " ".join(group.split())
        if not group or group.startswith("@"):
            continue
        for selector in group.split(","):
            selector = selector.strip()
            if selector:
                pairs.append((selector, body))
    return pairs


def painted(css: str, selector: str) -> str:
    """Every colour declaration under one selector, across every rule that names it.

    A selector appears more than once on these pages — `.exam-qcol` sets its width
    in one rule and its background in another, and the tablet block restates a
    control to raise it to 44px — so reading the *first* rule would miss the
    declaration being looked for and fail a page that is correct.
    """
    values = []
    for found, body in rules(css):
        if found == selector:
            values.extend(value.strip() for _, value in PAINT.findall(body))
    return " ".join(values)


# ── the family is the pages that draw a paper, not the ones I remembered ─────

def test_every_page_that_draws_a_paper_is_declared():
    """A page that grows a canvas and is not listed would skip every check below."""
    found = set(discovered_paper_pages())
    declared = set(PAPER_PAGES)

    assert not found - declared, (
        "these pages draw a paper in their own CSS but are not in PAPER_PAGES, so "
        "nothing here is looking at them — add them with a reason, or they keep "
        "whatever colours they were written with:\n  "
        + "\n  ".join(sorted(found - declared)))

    assert not declared - found, (
        "these are declared as paper pages but no longer draw one — delete the "
        "entry rather than leave the list describing a tree that is not there:\n  "
        + "\n  ".join(sorted(declared - found)))

    assert all(why.strip() for why in PAPER_PAGES.values()), \
        "every declared page has to say why it is one"


# ── no page paints a pure white or black ─────────────────────────────────────

def test_no_paper_page_paints_pure_white_or_black():
    """The sweep. A new `background: #fff` fails here.

    Print is exempt by condition, not by rule, and the reason is in the module
    docstring: a printed page has to pin its sheet white, because browsers drop
    background colours and keep text colours.
    """
    offenders = []
    for relative, css in discovered_paper_pages().items():
        for selector, body in rules(without_print(css)):
            for prop, value in PAINT.findall(body):
                value = value.split("!important")[0].strip()
                if PURE.match(value):
                    offenders.append(f"{relative}: {selector} {{{prop}: {value}}}")

    assert not offenders, (
        "these rules paint pure white or pure black, which is a surface or a label "
        "that cannot follow the theme. Read the role token instead — `--bg-card` for "
        "a control, `--paper`/`--paper-ink` for a sheet and what is written on it, "
        "`--on-accent`/`--on-chrome` for a label on an accent or on the always-dark "
        "chrome — rather than a value that is only right in light mode:\n  "
        + "\n  ".join(offenders))


def test_the_sweep_would_have_caught_what_it_removed():
    """The guard, pointed at the values it was written for.

    Without this the sweep could pass because it looks at nothing, which is how a
    check quietly stops working. `#fff` is what the pages said; `var(--paper)` is
    what they say now.
    """
    assert PURE.match("#fff") and PURE.match("#ffffff")
    assert PURE.match("white") and PURE.match("#000") and PURE.match("black")
    assert PURE.match("rgba(255, 255, 255, 1)")
    # Not pure, and this is the boundary that matters: the box on the paper was
    # `rgba(255,255,255,0.95)`, which composites over the sheet, so *this* sweep
    # does not catch it. The token check above is what does — which is why the fix
    # is a token rather than "stop being pure".
    assert not PURE.match("rgba(255, 255, 255, 0.95)")
    assert not PURE.match("rgba(255,255,255,0.15)")
    assert not PURE.match("rgba(0,0,0,0.3)")
    assert not PURE.match("var(--paper)")
    assert not PURE.match("#f8fafc")


# ── and the pages read the tokens, so deleting a rule is not a fix ────────────

#: `(page, selector, token)`: the declarations that carry the four roles. Asserted
#: rather than described, because removing the rule is the other way to make the
#: sweep above pass.
TOKEN_READERS: tuple[tuple[str, str, str], ...] = (
    ("student/take_exam.html", ".exam-qcol", "var(--bg-card)"),
    ("student/take_exam.html", ".math-tools-toggle", "var(--bg-card)"),
    ("student/take_exam.html", ".draw-toolbar button", "var(--bg-card)"),
    ("student/take_exam.html", ".draw-toolbar button:hover", "var(--bg-hover)"),
    ("student/take_exam.html", ".opt-btn.selected", "var(--on-accent)"),
    ("student/take_exam.html", ".math-tools-toggle.active", "var(--on-accent)"),
    ("student/take_exam.html", ".tool-ctrl-btn", "var(--on-chrome)"),
    ("student/take_exam.html", ".full-canvas-wrap", "var(--paper)"),
    ("student/take_exam.html", ".text-box textarea", "var(--paper)"),
    ("teacher/grade_detail.html", ".draw-toolbar button", "var(--bg-card)"),
    ("teacher/grade_detail.html", ".draw-toolbar button:hover", "var(--bg-hover)"),
    ("teacher/grade_detail.html", ".draw-toolbar button.active", "var(--on-accent)"),
    ("teacher/grade_detail.html", ".full-canvas-wrap", "var(--paper)"),
    ("teacher/grade_detail.html", ".text-box textarea", "var(--paper-ink)"),
    ("teacher/grade_detail.html", ".pdf-container", "var(--bg-subtle)"),
    ("student/result_detail.html", ".essay-page-wrap", "var(--paper)"),
    ("student/result_detail.html", ".text-box-overlay.teacher .box-content",
     "var(--paper-ink)"),
    ("teacher/preview_exam.html", ".text-box .box-content", "var(--paper-ink)"),
)


@pytest.mark.parametrize("page,selector,token", TOKEN_READERS)
def test_the_page_reads_the_token(page, selector, token):
    css = style_blocks(page)
    body = next((body for found, body in rules(css) if found == selector), None)
    assert body is not None, f"{page}: {selector} is missing — was the rule deleted?"

    found = painted(css, selector)
    assert token in found, (
        f"{page}: {selector} does not read {token} — a page-local rule is not reached "
        f"by the utility remap, so it has to name the token itself: {found!r}")


#: Every box that lies on the paper, which is where the white-on-white bug was.
#: These are asserted one by one rather than swept for, because the sweep at the
#: top of this file would *not* have caught it: the value was
#: `rgba(255,255,255,0.95)`, a translucent white, which composites over the sheet
#: and is deliberately not "pure". The fix had to be the token, so the token is
#: what is checked.
PAPER_BOXES: tuple[tuple[str, str], ...] = (
    ("student/take_exam.html", ".text-box textarea"),
    ("teacher/grade_detail.html", ".text-box textarea"),
    ("student/result_detail.html", ".text-box-overlay.student .box-content"),
    ("student/result_detail.html", ".text-box-overlay.teacher .box-content"),
    ("teacher/preview_exam.html", ".text-box .box-content"),
)


@pytest.mark.parametrize("page,selector", PAPER_BOXES)
def test_each_box_on_the_paper_pins_its_type(page, selector):
    """The box read `--paper-ink`, so it cannot paint light on the light sheet.

    Without the pin a `<textarea>` on the paper inherited the theme's `--text`,
    which is the same defect as the sheet itself being white: in dark mode the type
    and its background are both light and the answer is invisible.
    """
    found = painted(style_blocks(page), selector)
    assert "var(--paper-ink)" in found, (
        f"{page}: {selector} does not pin its type, so it follows the theme's "
        f"`--text` on a sheet that is light in both themes: {found!r}")


# ── the audit: both themes, measured ─────────────────────────────────────────

def test_paper_ink_reads_on_paper_in_both_themes():
    """AAA, because this is the type in the boxes the pupil types into.

    It is also the real check on `--paper`: if somebody inverts the sheet to make
    dark mode "properly dark", the ink on it stops reading and this fails.
    """
    for theme, selector in (("light", ":root"), ("dark", ".dark")):
        palette = tokens(selector)
        ratio = contrast(palette["paper-ink"], palette["paper"])
        assert ratio >= 7.0, (
            f"{theme}: --paper-ink on --paper is {ratio:.2f}:1 — the answer sheet "
            "keeps its dark ink in both themes, so its type has to stay readable")


def test_the_paper_is_a_sheet_in_both_themes():
    """Light, and stepped down in dark the way every other surface is.

    A sheet that followed the theme into black would hide the ink on it — the
    reason it is light at all. So this is the *only* token whose light and dark
    values are both light, and it is held to that rather than exempted from it.
    """
    light, dark = tokens(":root"), tokens(".dark")

    assert luminance(light["paper"]) >= 0.9, "--paper must be a light sheet in light mode"
    assert luminance(dark["paper"]) >= 0.5, "--paper must still be a light sheet in dark mode"
    assert luminance(dark["paper"]) < luminance(light["paper"]), (
        "--paper does not step down in dark mode, so it is a white glare there")


def test_the_chrome_label_reads_on_the_chrome():
    """The canvas tool dots are dark in both themes, so one value serves both."""
    chrome = "#1e293b"          # what `background: rgba(30,41,59,0.9)` composites to
    light, dark = tokens(":root"), tokens(".dark")

    for theme, palette in (("light", light), ("dark", dark)):
        ratio = contrast(palette["on-chrome"], chrome)
        assert ratio >= 4.5, (
            f"{theme}: --on-chrome on the chrome fill is {ratio:.2f}:1 — the glyph "
            "on a tool dot has to be readable whichever theme is on")


#: The accent fills these pages paint a label on, with what the app's convention
#: measures on each. `--on-accent` is held to the same bar the utility sweep holds
#: `bg-<hue>-600 text-white` to — a white or black label rather than 4.5:1 — because
#: this is the app's button surface and it stays saturated in both themes on
#: purpose (`test_dark_theme_contrast.test_saturated_button_backgrounds_are_left_alone`).
#: The floor is a pin, not a target: a *paler* orange or a duller label fails here,
#: which is the change this records.
ACCENT_FILLS = {
    "#f97316": 2.80,      # the selected option, the active tool, the toggle
    "#059669": 3.77,      # the teacher's active annotation tool
    "#ef4444": 3.76,      # the box delete control
}
ACCENT_FLOOR = 2.75


def test_the_accent_label_keeps_the_apps_convention():
    light, dark = tokens(":root"), tokens(".dark")
    label = light["on-accent"]

    assert label == dark["on-accent"], (
        "a label on an accent fill is the same in both themes, like the fill is")
    assert label.upper() in ("#FFFFFF", "#000000"), (
        f"--on-accent is {label}: the app's accent labels are white or black, which "
        "is what every `text-white` on a saturated fill already means")

    for fill, measured in ACCENT_FILLS.items():
        ratio = contrast(label, fill)
        assert luminance(fill) <= 0.35, (
            f"{fill} is not a saturated fill, so the label on it is not the app's "
            "accent convention but ordinary text and owes 4.5:1")
        assert ratio >= ACCENT_FLOOR, (
            f"--on-accent on {fill} is {ratio:.2f}:1, below the {ACCENT_FLOOR} this "
            f"convention last measured ({measured} on {fill}) — either the fill got "
            "paler or the label got duller")


def test_the_four_role_tokens_are_declared_in_both_themes():
    """A role declared in one theme only moves when the theme changes."""
    light, dark = tokens(":root"), tokens(".dark")

    for token in ("paper", "paper-ink", "on-accent", "on-chrome"):
        assert token in light, f"--{token} is missing from :root"
        assert token in dark, f"--{token} is missing from .dark"
