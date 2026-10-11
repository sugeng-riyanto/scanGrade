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

The exception is the interesting part, and it is a *token* rather than a literal
now. The drawing surfaces stay light on purpose — an answer canvas is paper, the ink
on it is dark, and a dark sheet would hide the answer (the same reasoning that keeps
the OMR mockup light). What has to follow the theme there is the *type*: the
`<textarea>` on the paper reads `--paper-ink`, without which it inherited the dark
theme's light text and painted white on white.

`tests/unit/test_theme_literals.py` owns the token half of that — it measures the
pair in both themes and sweeps the literals out of every page that draws a paper.
The checks here own the page: that it reads the token, and that it does so in a rule
serving both themes at once. That is what the sweep bought — the `.dark` block shrank
to the few neutrals the two themes genuinely do not share (a border that steps up a
shade, a hover whose light value is a warm tint), instead of restating every control.
"""
from __future__ import annotations

import re
from pathlib import Path

EXAM_PAGE = Path(__file__).resolve().parents[2] / "app" / "templates" / "student" / "take_exam.html"

#: The rules that paint a control from the theme's own tokens, one declaration
#: serving both themes. Asserted so the sweep cannot be undone by moving a colour
#: back into the `.dark` block — or by deleting the rule, which would make the
#: literal check pass by looking at nothing.
TOKEN_RULES = (
    (".exam-qcol", "var(--bg-card)"),
    (".math-tools-toggle", "var(--bg-card)"),
    (".draw-toolbar button", "var(--bg-card)"),
    (".draw-toolbar button:hover", "var(--bg-hover)"),
    (".full-canvas-wrap", "var(--paper)"),
    (".text-box textarea", "var(--paper)"),
)

#: The two rules that were on the old `DELIBERATELY_LIGHT` list and are not any
#: more, because the sweep removed the need for the list at all: the paper reads
#: `--paper` rather than a literal, so nothing here has to excuse it. The set's
#: other entries were the accent fills, which `LIGHT_BG` never matched — they were
#: dead weight in a list whose whole job is to be argued for, so they are gone.
PAPER_RULES = (".full-canvas-wrap", ".text-box textarea", ".text-box textarea:focus")

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


def declarations(css: str, selector: str) -> str:
    """Every declaration body for one selector, across every rule that names it.

    `.exam-qcol` sets a width in one rule and a background in another, so reading
    only the first rule would miss the declaration being looked for.
    """
    return " ".join(body for sel, body in rules(css) if sel == selector)


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
    def test_every_light_background_is_repainted_or_tokenised(self):
        """The general guard: a new pale box on this page fails here unless it is
        repainted for the dark theme.

        Since the sweep most rules need neither — they name the token in their base
        rule, which is why the `.dark` block is down to a few neutrals — so what is
        left for this to catch is a rule that spies a pale *literal* and has no dark
        counterpart (a tint, a wash).
        """
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
            if any(part.strip() in repainted for part in selector.split(",")):
                continue
            if plain in repainted:
                continue
            offenders.append(selector)
        assert not offenders, (
            "these rules paint a light background and are not repainted for the "
            "dark theme, so their text is the theme's own light colour on white. "
            "Read a token (`var(--bg-card)`, `var(--bg-hover)`, `var(--bg-subtle)`) "
            "instead, and the two themes are served by one declaration:\n"
            + "\n".join(f"  {o}" for o in offenders))

    def test_the_chrome_is_painted_from_the_tokens(self):
        """One declaration per control, for both themes.

        This replaced an assertion that a `.dark` rule repeats the token its base
        rule already names — which was an assertion that the theme has two places to
        keep in step, and that is precisely what the sweep removed.
        """
        css = style_block()
        for selector, token in TOKEN_RULES:
            body = declarations(css, selector)
            assert body, f"{selector} is missing — was the rule deleted?"
            assert token in body, (
                f"{selector} is painted from a colour of its own instead of the "
                f"theme token {token}: {body!r}")

    def test_the_dark_block_keeps_only_what_the_themes_do_not_share(self):
        """A rule left in `.dark` still has to name a token.

        The block is the page's one theme-specific region, so a literal is exactly
        what may not be hiding in it.
        """
        css = style_block()
        for selector, body in rules(css):
            if not selector.startswith(".dark "):
                continue
            assert "var(" in body, (
                f"{selector} repaints with a literal rather than a token: {body!r}")

    def test_the_paper_stays_paper_and_its_ink_is_pinned(self):
        """The deliberate exception, asserted so it cannot be 'fixed' by mistake.

        The drawing sheet is the one surface that stays *light in both themes* — the
        ink on it is dark, so a dark sheet would hide the answer — and it is a token
        now rather than a literal, which is what makes it measurable:
        `tests/unit/test_theme_literals.py` holds `--paper-ink` to AAA on `--paper` in
        both themes and holds `--paper` to being light in both. This checks the page
        reads it, and reads it for the type on the sheet too.
        """
        css = style_block()
        for selector in PAPER_RULES:
            body = declarations(css, selector)
            assert body, f"{selector} is missing — was the rule deleted?"
            assert "var(--paper)" in body, (
                f"{selector} does not take the sheet from --paper, so it is a "
                f"literal that is only right in light mode: {body!r}")

        box = declarations(css, ".text-box textarea")
        assert "var(--paper-ink)" in box, (
            "the text box on the paper does not pin its type, so it follows the "
            f"theme's light `--text` and is white text on white paper: {box!r}")
