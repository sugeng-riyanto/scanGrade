"""Every pane that scrolls is handed the page's own bar, and none may opt out.

What was there
--------------
`theme.css` draws the bar, and the 33 `overflow-*auto|scroll` panes in the 16
templates inherit it — measured live on `/student/comms`: twelve panes, every one
resolving the same thumb and groove as the page bar. One pane family opted out.
`student/take_exam.html` drew its own bar inside the two blocks that make its
drawing toolbar swiped on a touch screen — the phone one and the tablet one — with
`scrollbar-width: thin`, `::-webkit-scrollbar { height: 3px }` and a hard-coded
`#d6d3d1` thumb.

Three things were wrong with it, and only the first is visible in the CSS:

* **3px is a third of the floor** the page bar answers to, on the pane a student
  swipes *during an exam*.
* **#d6d3d1 on the groove it inherits (#e8edf6) is 1.27:1** — measured live. A bar
  you cannot see is a bar you cannot grab, which is the entire reason the page bar
  was measured in the first place.
* **In dark mode it won.** `head_extra` is emitted *after* the stylesheet link, so
  at equal specificity the template's rule beats `.dark ::-webkit-scrollbar-thumb`;
  measured live with `.dark` on the document, the pane's bar came out
  rgb(214,211,209) — a pale light thumb inside a dark bar.

What this file holds
--------------------
* **One scale, one rule.** Size, groove and the three thumb states are `--sb-*`
  tokens declared in `:root` and remapped in `.dark`; the single sizing rule reads
  the size token, and no bar rule may contain a colour literal. A pane that wants a
  different bar has to write one — which is what fails here.
* **The theme comes from the element.** No bar rule is scoped by `.dark`: a
  descendant selector cannot paint the element that carries the class, and `<html>`
  is the element whose viewport bar the reader drags. Every pane resolves the
  tokens it inherits, so the two cannot be handed different values.
* **The scale is declared by the theme, not by a pane** — two blocks, and nowhere
  else, or a pane could redefine `--sb-thumb` and still be "using a token".
* **The panes are counted here**, so a sweep whose reader quietly stops finding
  them fails instead of passing on nothing.
* **A pane cannot narrow or hide itself in Firefox** (`scrollbar-width: thin` was
  the other half of the toolbar's opt-out).

The scale's own numbers and readability — the size floors, the resolved contrast in
both themes, the pill shape — are tests/unit/test_scrollbar_usability.py's job, and
are not repeated here: panes and the page read the same tokens, so one measurement
holds for both.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THEME_CSS = ROOT / "app" / "static" / "css" / "theme.css"
TEMPLATES = sorted((ROOT / "app" / "templates").rglob("*.html"))

#: The panes this file exists for: either axis, either keyword.
PANE_RE = re.compile(r"overflow-(?:y-)?(?:auto|scroll)")

#: The audit cannot silently become empty — a broken reader would otherwise read as
#: "no pane needs checking". Both numbers are the measured inventory, floored.
MIN_PANES = 25
MIN_PANE_FILES = 12

CSS = THEME_CSS.read_text(encoding="utf-8")
PLAIN = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def rules(css: str) -> list[tuple[str, str, str]]:
    """Every flat rule as `(at-rule context, selector, body)`.

    At-rules are unwrapped rather than skipped, and that is not a detail: the pane
    bar this file was written for lived inside `@media (max-width: 640px)`, so a
    reader that only looked at top-level rules would have found nothing to complain
    about. Brace-matched, because the blocks nest.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found: list[tuple[str, str, str]] = []
    stack: list[str] = []
    buf = ""
    i = 0
    while i < len(css):
        char = css[i]
        if char == "{":
            prelude, buf = buf.strip(), ""
            if prelude.startswith("@"):
                stack.append(prelude)
            else:
                depth, j = 1, i + 1
                while j < len(css) and depth:
                    depth += (css[j] == "{") - (css[j] == "}")
                    j += 1
                found.append((" ".join(stack), prelude, css[i + 1:j - 1]))
                i = j
                continue
        elif char == "}":
            buf = ""
            if stack:
                stack.pop()
        else:
            buf += char
        i += 1
    return found


def style_blocks() -> dict[str, str]:
    """Each template's own `<style>` text, by path — what a page adds by hand.

    Jinja comments are removed first: `{# a note about scrollbars #}` inside a
    style block is prose, and a reader that parsed it as CSS would report a rule
    nobody wrote.
    """
    out = {}
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        css = "".join(m.group(1) for m in
                      re.finditer(r"<style[^>]*>(.*?)</style>", text, re.S))
        out[str(path.relative_to(ROOT))] = re.sub(r"\{#.*?#\}", "", css, flags=re.S)
    return out


def bar_rules(css: str) -> list[tuple[str, str, str]]:
    """The rules that draw a bar, wherever they are written."""
    return [r for r in rules(css) if "::-webkit-scrollbar" in r[1]]


def panes() -> list[tuple[str, int]]:
    """`(template, how many panes)` for the app's scroll containers."""
    found = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        hits = PANE_RE.findall(text)
        if hits:
            found.append((str(path.relative_to(ROOT)), len(hits)))
    return found


# ── the inventory: the sweep has something to sweep ─────────────────────────

def test_the_panes_are_counted_so_the_sweep_cannot_pass_on_nothing():
    """A pane list that silently empties would make every assertion below true of
    nothing at all, which is how this kind of guard dies."""
    found = panes()
    total = sum(n for _, n in found)
    assert len(found) >= MIN_PANE_FILES, (
        f"only {len(found)} template(s) contain a scrollable pane, {MIN_PANE_FILES} "
        f"is the measured floor — either the app lost its panes or this reader "
        f"stopped finding them: {found}")
    assert total >= MIN_PANES, (
        f"{total} pane(s) found; the measured inventory is {MIN_PANES} or more "
        f"across tables, code blocks, modals and sidebars: {found}")


def test_no_template_draws_a_bar_of_its_own():
    """The treatment lives in exactly one place. A page that paints its own bar
    does it with a literal — the tokens are the only other way, and they resolve to
    the same bar — and that pane then differs from the page beside it in a way
    nobody would look for."""
    for path, css in style_blocks().items():
        offenders = [sel for _ctx, sel, body in rules(css) if "scrollbar" in body]
        assert not offenders, (
            f"{path} draws its own scrollbar ({offenders}); the bar is the app's, "
            "declared once in theme.css and read from the --sb-* tokens")
        inline = re.findall(r'style="[^"]*scrollbar[^"]*"', css)
        assert not inline, f"{path} reshapes a pane's bar inline: {inline}"


# ── one scale, read by everything that draws a bar ──────────────────────────

def test_the_bar_reads_its_size_from_one_token():
    """Two axes, one rule, one token. A second rule that sets a width is a pane
    given its own bar — or the old design's media block, whose position in the
    stylesheet silently decided whether a phone got the bigger one."""
    sized = [r for r in bar_rules(PLAIN)
             if re.search(r"(?:^|[;{\s])(?:width|height)\s*:", r[2])]
    assert len(sized) == 1, (
        f"{len(sized)} rule(s) size a scrollbar; the size is one token read by one "
        f"rule: {[(s, b) for _c, s, b in sized]}")
    widths = re.findall(r"(?:width|height)\s*:\s*([^;]+)", sized[0][2])
    assert widths and all(w.strip() == "var(--sb-size)" for w in widths), (
        f"the bar's size is {widths} instead of var(--sb-size) — a literal cannot "
        "be raised for a finger by the touch block, and a pane that writes one is "
        "drawing its own bar")


def test_no_bar_is_painted_with_a_literal_colour():
    """Every colour a bar draws is a token. This is the assertion the pane bar
    failed: #d6d3d1 was 1.27:1 on the groove it inherited, and in dark mode it beat
    the dark rule outright by source order."""
    for context, selector, body in bar_rules(PLAIN):
        literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", body)
        assert not literals, (
            f"{selector!r} paints a scrollbar with {literals} — a literal cannot "
            f"follow the theme and cannot be measured against the groove "
            f"(context: {context or 'top level'})")
        for prop in ("background", "background-color"):
            match = re.search(rf"{prop}\s*:\s*([^;]+)", body)
            if match and "var(--sb-" not in match.group(1):
                assert match.group(1).strip() in ("none", "transparent"), (
                    f"{selector!r} sets {prop}: {match.group(1).strip()} — every "
                    "bar colour is a --sb-* token")


def test_the_theme_comes_from_the_element_not_from_a_selector():
    """`<html>` carries `.dark`, and the viewport bar belongs to it. A rule scoped
    by `.dark ` is a *descendant* selector that cannot paint its own carrier — the
    defect the page bar had to patch twice — so there must be none left: the
    pseudo-element resolves the tokens from the element it belongs to, which makes
    the page bar and a pane structurally incapable of disagreeing."""
    scoped = [sel for _ctx, sel, _body in bar_rules(PLAIN) if ".dark" in sel]
    assert not scoped, (
        f"{scoped} scope a scrollbar by `.dark`; the bar resolves its theme from "
        "the --sb-* tokens on the element that carries the class")


def _targets_the_document(selector: str) -> bool:
    """Does every part of this selector's list name the document element?

    `:root`, `.dark`, and the touch raise's `:root:not(.dark)` / `:root:is(.dark)`
    all do — and a pane (`.draw-toolbar`, `.card`, `.code-block`) never can, which is
    the distinction this exists for.
    """
    parts = [p.strip() for p in selector.split(",") if p.strip()]
    return bool(parts) and all(p.startswith(":root") or p.startswith(".dark")
                               for p in parts)


def test_nothing_declares_the_scale_except_the_two_theme_blocks():
    """A pane that redeclares `--sb-thumb` gives itself a different bar while every
    colour is still \"a token\" — so the scale is declared by the theme, twice, and
    by nothing else."""
    for path, css in list(style_blocks().items()) + [("theme.css", PLAIN)]:
        for _context, selector, body in rules(css):
            declared = re.findall(r"(--sb-[a-z-]+)\s*:", body)
            if not declared:
                continue
            assert path == "theme.css", (
                f"{path}: a template declares {declared} — the scale is one place")
            assert _targets_the_document(selector), (
                f"{path}: {selector!r} declares {declared}; the scrollbar scale "
                "belongs to the theme and the document root, not to a pane")


def test_a_pane_cannot_narrow_or_hide_itself_in_firefox():
    """`scrollbar-width: thin` was the other half of the toolbar's opt-out, and
    Firefox is where it is legal: the standard pair inherits, so `thin` made one
    pane's bar thinner than the page's — and `none` hides it entirely, which on a
    toolbar means a row of tools with no sign that more exist."""
    offenders = []
    for sources in ({"theme.css": PLAIN}, style_blocks()):
        for path, css in sources.items():
            for context, selector, body in rules(css):
                for prop in ("scrollbar-width", "scrollbar-color"):
                    for value in re.findall(rf"{prop}\s*:\s*([^;]+)", body):
                        if "-moz-appearance" not in context:
                            offenders.append(
                                f"{path}: {selector} {{{prop}: {value.strip()}}}")
                        elif value.strip() in ("thin", "none"):
                            offenders.append(
                                f"{path}: {selector} {{{prop}: {value.strip()}}}")
    assert not offenders, (
        f"{offenders} narrow or hide a pane's bar; the standard pair is one pair, "
        "gated to Firefox, and it takes its colours from the scale")
