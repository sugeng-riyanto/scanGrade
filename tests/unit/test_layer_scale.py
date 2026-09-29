"""One layer scale for the whole app, and no hand-picked z-index anywhere.

The exam page got a named layer scale first, because it is the page with the most
overlays and because three of them had silently come to share `9999`. But the
scale only lived in that page's own `<style>`: every other page still picked a
height by eye, and the same two failures the scale removes were therefore still
live everywhere else —

* two panels handed the same height (the app's sidebar, dropdown and corner
  badges were all `z-50`, so which one covered which was decided by whichever
  happened to be written later), and
* a modal at a lower number than a toast (`z-50` under `z-[100]` on one page, the
  reverse on another), so the same two things stacked differently per page.

So the scale now lives in `app/static/css/theme.css` — the one stylesheet every
page loads, after `tailwind.css`, so its classes win a tie with a utility — and
every page reads it by name.

What is asserted, and why each assertion is here:

1. The scale is declared by exactly one file, and its entries are exactly the
   three named groups below. A new layer that is not in a group fails here rather
   than being added to a fourth stack nobody compares with the others.
2. The two themes declare the same layers, value for value. A layer that only the
   light theme declared would move when the theme changed — the class of bug that
   `test_dark_theme_contrast` pins for the colour tokens.
3. Each group is strictly ascending with no height used twice. Within a group the
   line above paints over the one before it, so an entry out of order silently
   reverses a decision, and a shared height decides by document order instead.
4. Every entry has a `.sg-layer-<name>` class taking its own line, so a template
   can name any layer and cannot be handed another one by a typo.
5. No template, and no script this app ships, writes a stacking height as a
   number — not a Tailwind `z-50` or `z-[100]`, not `z-index: 50` in a rule or a
   `style` attribute, not `zIndex = "99999"` from JavaScript. This is the
   assertion the whole file exists for: the others can all hold while one page
   quietly stacks a panel by hand.
6. Leaving the stack is a named state too. One region in this app *should* be out
   of every stack — the shell's sidebar, which at desktop width *is* the column
   (`lg:static`) rather than an overlay. That transition carried a hand-picked
   `lg:z-auto`, which is not a number and so slipped past assertion 5 — the last
   escape of exactly the kind the rest of this file closes. `auto` is now a
   declared state beside the scale, taken by name, and a hand-picked reset
   (`z-auto`, `z-index: auto`) is swept for the same way a height is.
7. Every panel that leaves the flow says where it belongs. Assertions 5 and 6
   catch a height written by hand and a height removed by hand; neither can see a
   panel that names no height *at all*, which is the same defect one step quieter —
   it paints in document order, so whether it covers the control beside it is
   decided by whichever of the two a later edit moves. So the sweep is positive
   now: `fixed` and `sticky` name a layer, and a full-cover `absolute` overlay
   names one unless a positioned ancestor confines it to a box. Three shapes,
   because a panel is written three ways — as a class, as a stylesheet rule, and as
   a script — and a name is only accepted if the scale declares it.

   Two of those three need the containment read from outside themselves. A rule's
   confinement can live in the markup that uses it rather than in its own selector
   (`#alignment-overlay` is a bare id rule held by the `relative` box around it on
   the scanner page), so the rule sweep asks the markup sweep which names are only
   ever used inside a positioned ancestor. And a script that builds a node and
   detaches it in the same synchronous pass is not a panel at all — the clipboard
   fallback on `/demo` uses a scratch `<textarea>` positioned only to be selected,
   and it is gone before a click can reach it.
"""
from __future__ import annotations

import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
THEME_CSS = ROOT / "app" / "static" / "css" / "theme.css"
TEMPLATES = ROOT / "app" / "templates"
SCRIPTS = ROOT / "app" / "static" / "js"

#: The scale's groups. Every entry has to be in exactly one of these, and within
#: one group the entries paint in the order they are listed.
#:
#: * ``app``    — the shell every page shares: nothing here competes with a paper.
#:   ``over`` is anything drawn over the page but under the shell's own drawer —
#:   a menu, or the backdrop that dims the page behind the drawer.
#: * ``paper``  — a sitting, where the page has overlays of its own.
#: * ``canvas`` — inside a ``.full-canvas-wrap``, a stacking context of its own.
GROUPS: dict[str, list[str]] = {
    "app": ["local", "sticky", "header", "over", "drawer", "badge", "dialog",
            "notice"],
    "paper": ["paper", "bar", "qstrip", "float", "rail", "watermark", "scrim",
              "strip", "modal", "strip-over", "gate"],
    "canvas": ["canvas-tool", "canvas-grip", "canvas-item", "canvas-item-ctl"],
}

#: The order the scale is written in, so the file reads as the list it claims to
#: be rather than as three groups interleaved.
DECLARATION_ORDER = [name for group in GROUPS.values() for name in group]

#: Every way a stacking height can be written as a number. Each is a defect with
#: the same consequence — a height kept in step by hand — so each is swept for.
RAW_PATTERNS = {
    "a Tailwind utility": r"\bz-\[[0-9]+\]|\bz-[0-9]{1,3}\b",
    "a CSS declaration": r"z-index\s*:\s*-?[0-9]+",
    "a script assignment": r"zIndex\s*[:=]\s*['\"]?-?[0-9]+",
}

#: The one state that is not a height. `z-index` does not apply to a
#: `position: static` element, so a region the shell lays out as a column is in
#: no stack at all — and giving it a height would be a lie. Naming the state
#: keeps the reset in the scale, where a template can read it, instead of a
#: hand-picked `z-auto` that a later panel could copy.
OUT_TOKEN = "--sg-layer-out"
OUT_CLASS = "sg-layer-out-lg"

#: Every way a height can be *removed* by hand instead of named. Same defect as a
#: number: a decision kept in step by hand, and one no height sweep can see.
RESET_PATTERNS = {
    "a Tailwind utility": r"\bz-auto\b",
    "a CSS declaration": r"z-index\s*:\s*(?:auto|initial|unset)\b",
    "a script assignment": r"zIndex\s*[:=]\s*['\"]?(?:auto|initial|unset)\b",
}


def css() -> str:
    return THEME_CSS.read_text(encoding="utf-8")


def block(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css())
    assert match, f"no `{selector}` block in {THEME_CSS.name}"
    return match.group(1)


def scale(selector: str = ":root") -> dict[str, int]:
    """The scale as one selector declares it, in declaration order."""
    pairs = re.findall(r"--sg-layer-([a-z0-9-]+)\s*:\s*(\d+)\s*;", block(selector))
    assert pairs, f"`{selector}` declares no `--sg-layer-*` heights"
    return {name: int(value) for name, value in pairs}


def swept_files() -> list[Path]:
    """Every file that can stack something: templates, the stylesheet, scripts."""
    found = sorted(TEMPLATES.rglob("*.html")) + [THEME_CSS]
    found += sorted(p for p in SCRIPTS.glob("*.js") if "vendor" not in p.parts)
    assert len(found) > 50, (
        f"only {len(found)} files found to sweep, so this test may be checking "
        "nothing")
    return found


def raw_heights(text: str) -> list[str]:
    out = []
    for label, pattern in RAW_PATTERNS.items():
        for match in re.finditer(pattern, text):
            out.append(f"{label}: {match.group(0)!r}")
    return out


def hand_picked_resets(text: str) -> list[str]:
    out = []
    for label, pattern in RESET_PATTERNS.items():
        for match in re.finditer(pattern, text):
            out.append(f"{label}: {match.group(0)!r}")
    return out


def test_the_scale_is_one_list_of_three_named_groups():
    """One file, three groups, and nothing outside them. A layer added to a group
    is compared with the layers around it; a layer in no group is compared with
    nothing, which is how two stacks drift apart."""
    declared = set(scale())
    expected = set(DECLARATION_ORDER)
    assert declared == expected, (
        f"the scale declares {sorted(declared - expected)} which no group names, "
        f"or is missing {sorted(expected - declared)}")


def test_the_scale_is_declared_in_the_groups_it_listed():
    """The declared order is the list the docstring describes, so reading the file
    top-down is reading the stack bottom-up."""
    assert list(scale()) == DECLARATION_ORDER, (
        f"the scale is declared in a different order than its groups: "
        f"{list(scale())}")


def test_the_scale_is_declared_by_exactly_one_file():
    """A second declaration is a second source of truth — and a page-local copy is
    precisely what this work removed."""
    declaring = []
    for path in sorted(TEMPLATES.rglob("*.html")) + sorted(
            (ROOT / "app" / "static" / "css").glob("*.css")):
        if re.search(r"--sg-layer-[a-z0-9-]+\s*:", path.read_text(encoding="utf-8")):
            declaring.append(path.relative_to(ROOT).as_posix())
    assert declaring == ["app/static/css/theme.css"], (
        f"the layer scale is declared in {declaring}; it is one list in one file")


def test_both_themes_declare_the_same_layers():
    """A layer a theme does not declare resolves to nothing, so its panel keeps
    the light height in dark mode. Same rule as the colour tokens."""
    light, dark = scale(":root"), scale(".dark")
    assert light == dark, (
        f"the themes disagree about the stack: "
        f"{ {k: (light.get(k), dark.get(k)) for k in set(light) | set(dark) if light.get(k) != dark.get(k)} }")


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_each_group_is_strictly_ascending_and_has_no_shared_height(group):
    """Within a group the line above paints over the line before it, so the values
    must ascend — and no two may share a height, because then document order
    decides and the decision is no longer written down."""
    levels = {name: scale()[name] for name in GROUPS[group]}
    values = list(levels.values())
    assert len(set(values)) == len(values), (
        f"two {group} layers share a height, so one silently paints over the "
        f"other: {levels}")
    assert values == sorted(values), (
        f"the {group} scale is declared out of order, so 'the line above' no "
        f"longer means 'paints over': {levels}")


@pytest.mark.parametrize("name", DECLARATION_ORDER)
def test_every_layer_has_a_class_that_takes_its_own_line(name):
    """A template takes a layer by class, so every declared layer needs one — and
    it must read its *own* token, since a class pointing at another line is a
    panel stacked by a typo."""
    match = re.search(r"\.sg-layer-" + re.escape(name) + r"\s*\{([^}]*)\}", css())
    assert match, (
        f"no `.sg-layer-{name}` rule, so a template cannot name that layer")
    assert f"var(--sg-layer-{name})" in match.group(1), (
        f"`.sg-layer-{name}` does not take its own line: {match.group(1)!r}")


def test_nothing_anywhere_chooses_a_stacking_height_by_eye():
    """The assertion this file exists for. Every panel, drawer, dropdown, toast,
    badge and canvas tool names a line of the scale; a number written anywhere is
    one kept in step by hand, and it is what let three overlays share `9999`."""
    offenders = []
    for path in swept_files():
        text = path.read_text(encoding="utf-8")
        for hit in raw_heights(text):
            offenders.append(f"{path.relative_to(ROOT).as_posix()}: {hit}")
    assert not offenders, (
        "these files pick a stacking height as a number instead of naming a "
        "layer of the scale:\n  " + "\n  ".join(offenders))


def test_the_sweep_would_notice_a_number_if_one_came_back():
    """The sweep is only worth having if it bites, and the three shapes are three
    different defects, so each is exercised here rather than assumed."""
    for sample in ('class="fixed inset-0 z-50"', 'z-index: 9999;',
                   'input.style.zIndex = "99999"', '<div class="z-[100]">'):
        assert raw_heights(sample), f"the sweep misses a real shape: {sample}"
    for innocent in ('class="sg-layer-dialog"', 'z-index: var(--sg-layer-modal);',
                     'zIndex = "var(--sg-layer-notice)"'):
        assert not raw_heights(innocent), (
            f"the sweep flags a named layer as a number: {innocent}")


def out_state() -> tuple[str, str]:
    """What each theme declares `--sg-layer-out` to be."""
    pattern = re.escape(OUT_TOKEN) + r"\s*:\s*([a-z-]+)\s*;"
    light = re.search(pattern, block(":root"))
    dark = re.search(pattern, block(".dark"))
    assert light, f"`{OUT_TOKEN}` is not declared beside the scale"
    assert dark, f"`{OUT_TOKEN}` is declared in the light theme only"
    return light.group(1), dark.group(1)


def test_leaving_the_stack_is_a_named_state_declared_beside_the_scale():
    """`out` is the one entry that is not a height, and it belongs to the same
    one list: a state declared elsewhere is a second source of truth, and one
    that only a theme declares would move when the theme changed."""
    light, dark = out_state()
    assert light == dark == "auto", (
        f"the out state must resolve to `auto` in both themes, not {light!r} / {dark!r}")


def test_the_out_state_is_declared_by_the_same_one_file():
    """Every file that could declare a layer, checked for a second declaration of
    the out state — the reset is one decision in one place, like the heights."""
    declaring = []
    for path in sorted(TEMPLATES.rglob("*.html")) + sorted(
            (ROOT / "app" / "static" / "css").glob("*.css")):
        if re.search(re.escape(OUT_TOKEN) + r"\s*:", path.read_text(encoding="utf-8")):
            declaring.append(path.relative_to(ROOT).as_posix())
    assert declaring == ["app/static/css/theme.css"], (
        f"the out state is declared in {declaring}; it is one decision in one file")


def test_the_out_state_has_a_class_that_takes_its_token():
    """A state a template cannot name is a state it will write by hand. The
    class must read the token, so a reset cannot be copied as a literal."""
    match = re.search(r"\." + re.escape(OUT_CLASS) + r"\s*\{([^}]*)\}", css())
    assert match, f"no `.{OUT_CLASS}` rule, so leaving the stack has no name"
    assert f"var({OUT_TOKEN})" in match.group(1), (
        f"`.{OUT_CLASS}` does not take its own state: {match.group(1)!r}")


def test_the_out_state_rides_the_breakpoint_the_shell_leaves_at():
    """The drawer is `lg:static` — Tailwind's `lg`, 1024px. The out state has to
    ride the same breakpoint, or the reset lands at a width where the element is
    still an overlay and the drawer drops behind the page."""
    assert re.search(
        r"@media\s*\(min-width:\s*1024px\)\s*\{[^@]*?\."
        + re.escape(OUT_CLASS) + r"\s*\{", css()), (
            f"`.{OUT_CLASS}` is not declared inside `@media (min-width: 1024px)`, "
            "the width at which the shell lays the drawer out as a column")


def test_the_shells_drawer_names_the_out_state_instead_of_a_reset():
    """The element this whole change is for: the sidebar leaves the stack at
    desktop width, and it must say so by name."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    sidebar = re.search(r"<aside[^>]*sg-app-sidebar[^>]*>", base)
    assert sidebar, "no `sg-app-sidebar` in base.html, so this guard checks nothing"
    tag = sidebar.group(0)
    assert OUT_CLASS in tag, (
        "the shell's drawer does not name the out state; it leaves the stack by "
        f"hand instead: {tag!r}")


def test_nothing_chooses_to_leave_the_stack_by_hand():
    """Assertion 6, the one this addition exists for. A panel that leaves the
    stack names the state; `z-auto` from a template is the same defect as a
    hand-written height, one level down, and it was invisible to the height
    sweep because `auto` is not a number."""
    offenders = []
    for path in swept_files():
        text = path.read_text(encoding="utf-8")
        for hit in hand_picked_resets(text):
            offenders.append(f"{path.relative_to(ROOT).as_posix()}: {hit}")
    assert not offenders, (
        "these files leave the stack by hand instead of naming the out state:\n  "
        + "\n  ".join(offenders))


def test_the_reset_sweep_would_notice_a_reset_if_one_came_back():
    """The three shapes are three different defects, so each is exercised rather
    than assumed — and the named state must not be flagged as one of them."""
    for sample in ('class="fixed inset-y-0 lg:z-auto"', "z-index: auto;",
                   'input.style.zIndex = "auto"', "z-index: initial;"):
        assert hand_picked_resets(sample), f"the sweep misses a real reset: {sample}"
    for innocent in (f"{OUT_TOKEN}: auto;", f"z-index: var({OUT_TOKEN});",
                     f'zIndex = "var({OUT_TOKEN})"', "position: static"):
        assert not hand_picked_resets(innocent), (
            f"the sweep flags the named state as a hand-picked reset: {innocent}")


# ── panels that leave the flow ───────────────────────────────────────────────
#
# Assertion 5 catches a height *written by hand*. It cannot see a panel that names
# no height at all, and that is the quieter half of the same defect: an element out
# of the flow at `auto` paints in document order, so whether it covers the control
# beside it is decided by whichever of the two a later edit happens to move. The
# numeric sweep never asked the other question, so the app still had panels outside
# the scale: the whiteboard's stage, the OMR scanner's viewfinder, the canvases of
# the exam and grading papers, the ink layer of the printed report card, and two
# sticky action cards.
#
# So the sweep is positive now. Every panel that leaves the flow says where it
# belongs, or is confined to a box that bounds it:
#
# * a `fixed` panel is anchored to the viewport, so no box can confine it — it names
#   a layer;
# * a `sticky` panel is pinned over the content it scrolls past — it names one too;
# * a full-cover `absolute` overlay covers either the page or its containing block.
#   With no positioned ancestor it is over the page and must name a layer; with one,
#   that box is the whole of what it can cover, which is what every canvas,
#   viewfinder and print layer in this app relies on.
#
# A name is only accepted if the scale declares it, so a typo fails here instead of
# shipping as a panel that stacks by document order.

#: Positioning utilities, and the class form of `isolation`.
POSITION_TOKENS = {"relative", "absolute", "fixed", "sticky"}

#: The whole-box shapes: Tailwind's `inset-0`, or width and height both 100%.
COVER_TOKEN = "inset-0"

#: The attributes a class or a position can arrive in. Alpine's `:class`/`:style`
#: are the ones this app actually uses; the `x-bind:`/`::` spellings are the same
#: attribute by another name, and an overlay written in one of them is the same
#: overlay.
CLASS_ATTRIBUTES = ("class", ":class", "class:list", "x-bind:class", "::class")
STYLE_ATTRIBUTES = ("style", ":style", "x-bind:style", "::style")

#: The names that are not layers: the declared out state, and its class.
NON_LAYER_NAMES = {OUT_TOKEN.replace("--sg-layer-", ""),
                   OUT_CLASS.replace("sg-layer-", "")}

#: A layer name wherever it is written — `class="… sg-layer-dialog …"`, or
#: `z-index: var(--sg-layer-strip);`.
LAYER = re.compile(r"sg-layer-([a-z0-9-]+)")

#: The scale, read once: the sweeps below ask about it per element.
SCALE_CACHE = scale()


def style_position(text: str) -> str | None:
    match = re.search(r"position\s*:\s*(relative|absolute|fixed|sticky)", text)
    return match.group(1) if match else None


def full_box(text: str) -> bool:
    """The shapes that cover the box they are in."""
    tight = re.sub(r"\s+", "", text).lower()
    if "inset:0" in tight:
        return True
    return "width:100%" in tight and "height:100%" in tight


def token_set(value: str) -> set[str]:
    """The class tokens an attribute contributes. An Alpine expression is a string
    of its own, so quotes and punctuation are separators rather than part of a
    name — `:class="open ? 'fixed inset-0' : ''"` leaves two real tokens."""
    return set(re.sub(r"['\"()?:,+]", " ", value).split())


def declarations(text: str) -> list[tuple[str, str]]:
    """(selector, body) for every rule in a stylesheet, or in a CSS string."""
    return [(" ".join(selector.split()), body)
            for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text)]


def selector_classes(selector: str) -> set[str]:
    return set(re.findall(r"\.([A-Za-z0-9_-]+)", selector))


def selector_ids(selector: str) -> set[str]:
    return set(re.findall(r"#([A-Za-z0-9_-]+)", selector))


@lru_cache(maxsize=1)
def positioning_names() -> frozenset[str]:
    """Classes and ids whose own rule declares a position. An element naming one of
    them holds a containing block, so an `absolute` child is confined to it.

    Cached because the question is asked once per declaration in the stylesheet
    sweep, and the answer needs every swept file read.
    """
    names: set[str] = set()
    for path in swept_files():
        for selector, body in declarations(path.read_text(encoding="utf-8")):
            if style_position(body):
                names |= selector_classes(selector) | selector_ids(selector)
    return frozenset(names)


def bounding_selector(selector: str) -> bool:
    """Whether a rule's own selector places it inside a box: `.page-wrap .ink`
    cannot leave `.page-wrap`, so the layer of ink is the page's own."""
    parts = [part for part in re.split(r"\s+|>", selector) if part]
    names = positioning_names()
    for part in parts[:-1]:
        if selector_classes(part) & names or selector_ids(part) & names:
            return True
    return False


class Overlays(HTMLParser):
    """Every start tag that leaves the flow, and what it says about itself."""

    def __init__(self, file: str, positioned: set[str]):
        super().__init__(convert_charrefs=True)
        self.file = file
        self.positioned = positioned
        self.stack: list[tuple[str, bool]] = []      # tag, holds a containing block
        self.panels: list[dict] = []

    def handle_starttag(self, tag, attrs):
        self._consider(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._consider(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def _consider(self, tag, attrs) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        everything = " ".join(values.values())
        classes: set[str] = set()
        for name, value in values.items():
            if name in CLASS_ATTRIBUTES or name.endswith(":class"):
                classes |= token_set(value)
        styles = " ".join(value for name, value in values.items()
                          if name in STYLE_ATTRIBUTES or name.endswith(":style"))

        confined = any(holds for _, holds in self.stack)
        named = [name for name in LAYER.findall(everything) if name in SCALE_CACHE]

        kinds = []
        if "fixed" in classes or style_position(styles) == "fixed":
            kinds.append("fixed")
        if "sticky" in classes:
            kinds.append("sticky")
        if ("absolute" in classes and COVER_TOKEN in classes) or (
                style_position(styles) == "absolute" and full_box(styles)):
            kinds.append("cover")
        if kinds:
            self.panels.append({"file": self.file, "line": self.getpos()[0],
                                "tag": tag, "kinds": kinds, "named": named,
                                "confined": confined,
                                "names": sorted(classes
                                                | set(values.get("id", "").split()))})

        holds = bool(classes & POSITION_TOKENS) or style_position(styles) is not None
        holds = holds or bool((classes | set(values.get("id", "").split()))
                              & self.positioned)
        self.stack.append((tag, holds))


def _panels() -> list[dict]:
    """Every out-of-flow panel in every template, with what it names."""
    positioned = positioning_names()
    found: list[dict] = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        parser = Overlays(path.relative_to(ROOT).as_posix(), positioned)
        parser.feed(path.read_text(encoding="utf-8"))
        found.extend(parser.panels)
    return found


def panels() -> list[dict]:
    found = _panels()
    assert len(found) > 25, (
        f"only {len(found)} panels found to sweep, so this may be checking nothing")
    return found


@lru_cache(maxsize=1)
def confined_names() -> frozenset[str]:
    """Names that are used in markup *only* inside a positioned ancestor.

    A rule's confinement can come from the markup that uses it rather than from its
    own selector, so the rule sweep has to read the same containment the markup
    sweep does. A name that appears once confined and once loose is loose.
    """
    safe: set[str] = set()
    unsafe: set[str] = set()
    for panel in _panels():
        (safe if panel["confined"] else unsafe).update(panel["names"])
    return frozenset(safe - unsafe)


#: The element an inline style positions: the receiver in `ta.style.position =
#: "fixed"`. Nothing else can be a scratch node, because nothing else says which
#: element the height applies to.
RECEIVER_BEFORE = re.compile(r"([A-Za-z_$][\w$]*)\s*\.\s*style\s*\.\s*$")

#: A class list handed to a created element. A panel built at runtime can carry its
#: out-of-flow position in a class string rather than an inline style — `/demo`'s
#: clipboard toast does — and the markup sweep cannot see it, because the element is
#: not in the markup.
CLASS_STRING = re.compile(
    r"(?:className\s*=\s*|setAttribute\s*\(\s*['\"]class['\"]\s*,\s*)"
    r"(['\"])((?:(?!\1)[^\\])*)\1", re.S)


def statement_bounds(text: str, position: int) -> tuple[int, str]:
    """The block a script's positioning call lives in, and where it starts: the braces
    around it when it is inside a rule or a function, and otherwise the run of lines
    it shares."""
    depth = 0
    for index in range(position, -1, -1):
        if text[index] == "}":
            depth += 1
        elif text[index] == "{":
            if depth == 0:
                end = text.find("}", position)
                return index, text[index:end + 1] if end != -1 else text[index:]
            depth -= 1
    lines = text.splitlines()
    line = text.count("\n", 0, position)
    start = line
    while start > 0 and lines[start - 1].strip():
        start -= 1
    end = line
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    return text.index(lines[start]), "\n".join(lines[start:end + 1])


def statement_around(text: str, position: int) -> str:
    return statement_bounds(text, position)[1]


def detached_node(text: str, match: re.Match, start: int, block: str) -> bool:
    """Whether the element just positioned is itself detached in the same block.

    Read per element, not per block, because one block can do both: `/demo`'s
    clipboard helper positions a scratch `<textarea>` *and* builds a toast that
    stays. Exempting the whole block would blind the sweep to the toast — which is
    exactly what the first cut of this carve-out did.
    """
    window = text[max(0, match.start() - 60):match.start()]
    found = RECEIVER_BEFORE.search(window)
    if not found:
        return False
    receiver = re.escape(found.group(1))
    tail = text[match.end():start + len(block)]
    return bool(re.search(rf"\b{receiver}\s*\.\s*remove\s*\(\s*\)", tail)
                or re.search(rf"removeChild\s*\(\s*{receiver}\b", tail))


def test_every_panel_that_leaves_the_flow_says_where_it_belongs():
    """Assertion 7, for the panels written as classes."""
    offenders = []
    for panel in panels():
        if "cover" in panel["kinds"] and panel["confined"]:
            continue
        if not panel["named"]:
            offenders.append(f"{panel['file']}:{panel['line']} <{panel['tag']}> "
                             f"{'/'.join(panel['kinds'])} names no layer")
    assert not offenders, (
        "these panels leave the flow and name no layer, so what covers what is "
        "decided by document order:\n  " + "\n  ".join(offenders))


def test_every_panel_written_as_a_rule_says_where_it_belongs():
    """The same panels, written as CSS: `fixed` always, and a full cover unless the
    rule is already scoped inside the box that holds it."""
    offenders = []
    for path in swept_files():
        rel = path.relative_to(ROOT).as_posix()
        for selector, body in declarations(path.read_text(encoding="utf-8")):
            position = style_position(body)
            if position == "fixed":
                pass
            elif position == "absolute" and full_box(body):
                if bounding_selector(selector):
                    continue
            else:
                continue
            if any(name in SCALE_CACHE for name in LAYER.findall(body)):
                continue
            targets = selector_classes(selector) | selector_ids(selector)
            if targets and targets <= set(confined_names()):
                continue
            offenders.append(f"{rel}: `{selector[:60]}` is {position} and names no "
                             "layer")
    assert not offenders, (
        "these rules leave the flow and name no layer:\n  " + "\n  ".join(offenders))


def test_every_panel_written_as_a_script_says_where_it_belongs():
    """And the ones built at runtime, which the two sweeps above cannot see because
    the element did not exist until the script ran. There are two ways a script
    leaves the flow — `style.position = "fixed"`, and a class string handed to a
    created element — and the containment of neither is knowable statically, so both
    have to name a layer.
    """
    inline = re.compile(r"position\s*[:=]\s*['\"]?fixed")
    offenders = []
    for path in swept_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()

        for match in inline.finditer(text):
            start, block = statement_bounds(text, match.start())
            if any(name in SCALE_CACHE for name in LAYER.findall(block)):
                continue
            if detached_node(text, match, start, block):
                continue
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{rel}:{line} positions something fixed and names no "
                             "layer")

        for match in CLASS_STRING.finditer(text):
            classes = token_set(match.group(2))
            kinds = [kind for kind, present in (("fixed", "fixed" in classes),
                                                ("sticky", "sticky" in classes),
                                                ("a full cover",
                                                 "absolute" in classes
                                                 and COVER_TOKEN in classes))
                     if present]
            if not kinds:
                continue
            if any(name in SCALE_CACHE for name in LAYER.findall(match.group(2))):
                continue
            start, block = statement_bounds(text, match.start())
            if detached_node(text, match.start(), start, block):
                continue
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{rel}:{line} builds a panel that is {'/'.join(kinds)}"
                             " and names no layer")
    assert not offenders, (
        "these scripts build a panel with no layer:\n  " + "\n  ".join(offenders))


def test_every_layer_name_is_one_the_scale_declares():
    """A name that is not on the scale resolves to nothing: the class exists, the
    panel does not, and only document order is left."""
    known = set(SCALE_CACHE) | NON_LAYER_NAMES
    offenders = []
    for path in swept_files():
        rel = path.relative_to(ROOT).as_posix()
        for name in LAYER.findall(path.read_text(encoding="utf-8")):
            if name not in known:
                offenders.append(f"{rel}: `sg-layer-{name}` is not a scale entry")
    assert not offenders, "\n  ".join(offenders)


def test_the_panel_sweep_would_notice_an_unnamed_panel():
    """A sweep is only worth having if it bites, and a panel is written three ways,
    so every shape the app actually stacks is exercised here rather than assumed:
    the whiteboard's palettes, the scanner's viewfinder, a modal, and a canvas
    surface pinned inside its box."""
    positioned = {"toolbar", "camera-area"}

    def sweep(markup: str) -> list[dict]:
        parser = Overlays("sample.html", positioned)
        parser.feed(markup)
        return parser.panels

    unnamed = [
        '<div class="fixed inset-0"></div>',
        '<div class="sticky top-20"></div>',
        '<div class="absolute inset-0"></div>',
        '<input style="position: fixed; left: 0">',
        '<div :class="open ? \'absolute inset-0\' : \'hidden\'"></div>',
        '<div class="fixed bottom-6 right-6"></div>',          # a corner panel
        '<svg class="absolute inset-0"></svg>',                # a viewfinder
    ]
    for sample in unnamed:
        found = sweep(sample)
        assert found and not found[0]["named"], f"the sweep misses: {sample}"

    innocent = [
        '<div class="fixed inset-0 sg-layer-dialog"></div>',   # a modal
        '<div class="sticky top-20 sg-layer-sticky"></div>',
        '<div class="relative"><div class="absolute inset-0"></div></div>',
        '<div class="toolbar"><div class="absolute inset-0"></div></div>',
        '<div class="absolute sg-layer-canvas-item"></div>',   # named, not covering
        '<div class="absolute top-4 left-0 sg-layer-over"></div>',
        '<div class="relative"></div>',
    ]
    for sample in innocent:
        found = sweep(sample)
        assert not found or found[0]["named"] or found[0]["confined"], (
            f"the sweep flags a panel that is named or confined: {sample}")

    # The shape the exam and grading papers rely on: a full-cover canvas, pinned
    # inside a box rather than named. It is the box that makes this safe, which is
    # why `position: relative` on `.full-canvas-wrap` is load-bearing.
    surface = sweep('<div class="relative">'
                    '<canvas style="position:absolute;inset:0;width:100%;height:100%">'
                    "</canvas></div>")
    assert surface and surface[0]["confined"] and not surface[0]["named"], (
        "the sweep either misses a full-cover canvas or demands a layer from one "
        "that is confined to its box")
    loose = sweep("<div><canvas style='position:absolute;inset:0'></canvas></div>")
    assert loose and not loose[0]["confined"], (
        "a full-cover canvas with no positioned ancestor reads as confined, so an "
        "overlay over the whole page would pass this sweep")


def test_a_rule_held_by_its_box_is_confined_by_the_markup_that_uses_it():
    """The scanner's `#alignment-overlay` is a bare id rule with no named layer, and
    it is safe only because the markup puts it inside `#camera-area`, which is
    positioned. The rule sweep reads that containment from the markup sweep, so if
    that box ever stops being positioned the same rule becomes an offender — which
    is the whole reason the two sweeps are allowed to know about each other.
    """
    assert "alignment-overlay" in confined_names(), (
        "the scanner's alignment overlay no longer reads as held by its box; either "
        "`#camera-area` stopped being positioned (and this overlay can now leave "
        "it) or the markup sweep stopped seeing it")
