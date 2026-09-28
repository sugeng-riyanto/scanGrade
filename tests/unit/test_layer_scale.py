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
"""
from __future__ import annotations

import re
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
