"""The exam page on a tablet, in both themes.

Two complaints, one page:

* **A tablet got a desktop's layout.** The page had exactly two layouts — one
  from `min-width: 1024px` and one from `max-width: 768px` — so a tablet in
  portrait (768–1023px), and a tablet in landscape narrower than 1024 (which is
  most of them), was drawn by the desktop rules: cursor-sized targets on a
  finger's device. The tablet range is now its own block, not a scale of either
  neighbour, and the controls a pupil reaches for during a paper carry the same
  44px floor `.tap-44` already answers to.
* **The page's own `<style>` was written light-only.** The rest of the app
  follows the theme through Tailwind utilities and the token remap, but this
  block paints white boxes and pale greys by hand, and in dark mode a pale box
  under the theme's own light `--text` is light-on-light. The guard below is the
  general one, because the specific diff is the thing that rots: *every* rule in
  this page that paints a light background must either be repainted for the dark
  theme or be one of the surfaces that is deliberately light in both.

The exception is the interesting part. The drawing surfaces stay light on
purpose — an answer canvas is paper, the ink on it is dark, and a dark sheet would
hide the answer (the same reasoning that keeps the OMR mockup light). What has to
follow the theme there is the *type*: without pinning it, the `<textarea>` on the
paper inherited the dark theme's light text and painted white on white.
"""
from __future__ import annotations

import re
from pathlib import Path

EXAM_PAGE = Path(__file__).resolve().parents[2] / "app" / "templates" / "student" / "take_exam.html"

#: Surfaces that are *paper* rather than chrome, or that are the accent colour in
#: both themes. Each is a decision, not an oversight: a drawing sheet must stay
#: light because the ink is dark, and an orange active state is the same orange
#: in either theme.
DELIBERATELY_LIGHT = {
    ".math-tools-toggle.active",       # the accent, identical in both themes
    ".opt-btn.selected",               # the accent
    ".draw-toolbar button.active",     # the accent
    ".text-box .box-handle",           # the accent
    ".full-canvas-wrap",               # paper: the ink is dark
    ".text-box textarea",              # paper: the ink is dark (colour is pinned)
    ".text-box textarea:focus",        # the same box, focused
}

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
LIGHT_BG = re.compile(
    r"background(?:-color)?\s*:\s*(#fff(?:fff)?\b|white\b|rgba\(\s*255\s*,\s*255\s*,\s*255"
    r"|#f8fafc|#f3f4f6|#fff7ed|#f9fafb)", re.I)


def source() -> str:
    return EXAM_PAGE.read_text(encoding="utf-8")


def style_block() -> str:
    page = source()
    start = page.index("<style>") + len("<style>")
    return page[start:page.index("</style>", start)]


def rules(css: str) -> list[tuple[str, str]]:
    # Comments first. The page is heavily annotated, and a comment between one
    # rule and the next is otherwise read as part of the next rule's selector —
    # which made this very suite miss the rules it was added to check.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for group, body in RULE.findall(css):
        group = " ".join(group.split())
        if not group or group.startswith("@"):
            continue
        # One entry per selector, so a rule that repaints several boxes under
        # `.dark` is checked for each of them rather than only the first.
        for selector in group.split(","):
            selector = selector.strip()
            if selector:
                out.append((selector, body))
    return out


def dark_selectors(css: str) -> set[str]:
    return {sel.replace(".dark ", "", 1).strip()
            for sel, _ in rules(css) if sel.startswith(".dark ")}


# ── the tablet range ─────────────────────────────────────────────────────────

class TestTheTabletRangeIsItsOwn:
    def test_the_page_still_has_its_two_neighbours(self):
        """The gap is defined by the two patterns around it; if either moves, the
        tablet block is measuring the wrong interval."""
        css = style_block()
        assert "@media (min-width: 1024px)" in css, "the desktop pattern moved"
        assert "@media (max-width: 768px)" in css, "the phone pattern moved"

    def test_the_range_covered_by_neither_now_has_a_block(self):
        css = style_block()
        assert "@media (min-width: 769px) and (max-width: 1023px)" in css, (
            "the 769–1023px range — a tablet, in either orientation — is still "
            "drawn by the desktop rules")

    def test_the_tablet_block_carries_the_touch_floor(self):
        css = style_block()
        block = css[css.index("@media (min-width: 769px) and (max-width: 1023px)"):]
        block = block[:block.index("@media", 1)] if "@media" in block[10:] else block
        for selector in (".opt-btn", ".q-btn", ".draw-toolbar button", ".tool-ctrl-btn"):
            assert selector in block, f"the tablet block never sizes {selector}"
        assert block.count("min-height: 44px") >= 1 and "min-width: 44px" in block, (
            "the tablet block does not apply the 44px floor the rest of the app "
            "answers to")


# ── dark mode ────────────────────────────────────────────────────────────────

class TestNoLightOnlyControlSurvivesDarkMode:
    def test_every_light_background_is_repainted_or_deliberate(self):
        """The general guard: a new white box on this page fails here unless it is
        repainted for the dark theme or added to the short, explained list."""
        css = style_block()
        repainted = dark_selectors(css)
        offenders = []
        for selector, body in rules(css):
            if not LIGHT_BG.search(body):
                continue
            # A rule already scoped to `.dark` is a repaint, not an offender.
            if selector.startswith(".dark "):
                continue
            plain = selector.replace(".dark ", "")
            if plain in DELIBERATELY_LIGHT:
                continue
            if any(part.strip() in repainted or part.strip() in DELIBERATELY_LIGHT
                   for part in selector.split(",")):
                continue
            if plain in repainted:
                continue
            offenders.append(selector)
        assert not offenders, (
            "these rules paint a light background and are not repainted for the "
            "dark theme, so their text is the theme's own light colour on white:\n"
            + "\n".join(f"  {o}" for o in offenders))

    def test_the_chrome_is_repainted_from_the_tokens(self):
        css = style_block()
        for selector, token in (
            (".dark .math-tools-toggle", "var(--bg-card)"),
            (".dark .draw-toolbar button", "var(--bg-card)"),
            (".dark .opt-btn:not(.selected):hover", "var(--bg-hover)"),
            (".dark .exam-qcol", "var(--bg-card)"),
            (".dark .pdf-container", "var(--bg-subtle)"),
        ):
            rule = next((body for sel, body in rules(css) if sel == selector), None)
            assert rule is not None, f"{selector} is missing"
            assert token in rule, (
                f"{selector} is repainted from a colour of its own instead of the "
                f"theme token {token}")

    def test_the_paper_stays_paper_and_its_ink_is_pinned(self):
        """The deliberate exception, asserted so it cannot be 'fixed' by mistake:
        the drawing sheet keeps its light background, and the type on it is
        pinned dark rather than following the theme's light `--text`."""
        css = style_block()
        sheet = next((body for sel, body in rules(css) if sel == ".dark .full-canvas-wrap"),
                     None)
        assert sheet is not None, "the drawing sheet has no dark-mode rule"
        assert LIGHT_BG.search(sheet), (
            "the drawing sheet was inverted — the dark ink drawn on it would be "
            "invisible")
        box = next((body for sel, body in rules(css) if sel == ".dark .text-box textarea"),
                   None)
        assert box is not None, "the text box has no dark-mode rule"
        assert re.search(r"color\s*:\s*#1[0-9a-f]{5}", box, re.I), (
            "the text box on the paper follows the theme's text colour, so it is "
            "white text on white paper in dark mode")
