"""Dark mode has to be measured, not eyeballed.

There are 114 templates and ~440 colour utilities, and they are written in light
mode: `text-slate-600` on `bg-white` is the default shape of a card here. Rather
than a `dark:` variant on every element, base.html remaps those light utilities
onto the theme tokens, so a component that is readable in light mode stays
readable in dark mode. Two things can go wrong, and both are silent:

* **A token can fail contrast.** The previous `--text-dim` was `#64748b`, which
  is 4.34:1 on the page background — below AA, in light mode, today. Nobody
  would notice from a screenshot.
* **A utility can be left out of the remap.** The old block covered `bg-emerald-50`
  and `text-emerald-700` but not `bg-emerald-100`, which is the exact pair a badge
  uses — so the badge ended up light-on-light. Adding a class to a template later
  reintroduces that, and no contrast test would see it.

So this file pins both: the tokens are held to WCAG 2.1 AA against every surface
they are painted on, and every light-leaning utility the templates actually use
must have a dark rule. Exemptions are listed with a reason, because an exemption
is where this kind of check quietly stops working.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "app" / "templates" / "base.html"
SOURCE = BASE.read_text(encoding="utf-8")
TEMPLATES = sorted((ROOT / "app" / "templates").rglob("*.html"))


# ── WCAG contrast ────────────────────────────────────────────────────────────

def _linear(channel: int) -> float:
    srgb = channel / 255
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(hexcolor: str) -> float:
    value = hexcolor.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# ── reading the stylesheet ───────────────────────────────────────────────────

def style_block() -> str:
    match = re.search(r"<style>(.*?)</style>", SOURCE, re.S)
    assert match, "base.html has no <style> block"
    return match.group(1)


CSS = style_block()


def tokens(selector: str) -> dict[str, str]:
    """The `--custom` properties declared for one selector."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"no {selector} block in base.html"
    return {name: value.strip()
            for name, value in re.findall(r"--([a-z-]+)\s*:\s*([^;]+);", match.group(1))}


def dark_rules() -> str:
    """Only the rules this theme adds, so an unrelated `.card` cannot satisfy a check."""
    return "\n".join(line for line in CSS.splitlines()
                     if line.strip().startswith((":where(.dark)", ".dark")))


def covered_classes() -> set[str]:
    """Utility classes the dark rules target, unescaped back to template spelling.

    In CSS a class is written `.hover\\:bg-emerald-50`; the template says
    `hover:bg-emerald-50`. Getting this wrong would make every hover rule look
    absent.
    """
    found = set()
    for selector in re.findall(r"\.((?:[A-Za-z0-9_-]|\\.)+)", dark_rules()):
        name = selector.replace("\\:", ":").replace("\\/", "/")
        name = re.sub(r":(hover|focus|active|focus-within|disabled)$", "", name)
        if name != "dark":
            found.add(name)
    return found


REQUIRED_TOKENS = ("bg-body", "bg-card", "bg-subtle", "bg-hover", "text", "text-dim",
                   "text-muted", "border", "input", "input-border")


def test_both_themes_declare_the_same_tokens():
    """A token present in one theme but missing in the other resolves to nothing,
    so the property silently keeps its light value in dark mode."""
    light, dark = tokens(":root"), tokens(".dark")

    assert set(REQUIRED_TOKENS) <= set(light), f"missing in :root: {set(REQUIRED_TOKENS) - set(light)}"
    assert set(REQUIRED_TOKENS) <= set(dark), f"missing in .dark: {set(REQUIRED_TOKENS) - set(dark)}"
    assert set(light) == set(dark), "the two themes must declare the same tokens"


@pytest.mark.parametrize("theme,selector", [("dark", ".dark"), ("light", ":root")])
def test_text_tokens_meet_aa_on_every_surface(theme, selector):
    """`--text` carries body copy, so it is held to AAA (7:1) where it can be;
    the secondary tokens only have to clear AA."""
    palette = tokens(selector)
    surfaces = {name: palette[name] for name in ("bg-body", "bg-card", "bg-subtle",
                                                 "bg-hover")}

    for name, surface in surfaces.items():
        assert contrast(palette["text"], surface) >= 7.0, (
            f"{theme}: --text on --{name} is {contrast(palette['text'], surface):.2f}:1")
        for token in ("text-dim", "text-muted"):
            ratio = contrast(palette[token], surface)
            assert ratio >= 4.5, f"{theme}: --{token} on --{name} is {ratio:.2f}:1"


@pytest.mark.parametrize("theme,selector", [("dark", ".dark"), ("light", ":root")])
def test_field_text_and_placeholder_read_on_the_field(theme, selector):
    """A field is its own surface, painted `--input`: the value sits in `--text`
    and the placeholder in `--text-muted`. Leaving the placeholder to the browser
    gives slate-400 on the default white field — 2.54:1, measured live on the
    teacher's exam search box before this was fixed."""
    palette = tokens(selector)

    assert contrast(palette["text"], palette["input"]) >= 4.5, theme
    assert contrast(palette["text-muted"], palette["input"]) >= 4.5, theme


def test_each_theme_declares_its_colour_scheme():
    """`color-scheme` is what makes the browser draw its *own* chrome — scrollbars,
    dropdown lists, checkboxes, autofill — in the theme the page is in. Surfaces
    can be remapped perfectly and those still come out light on a dark page."""
    assert re.search(r":root\s*\{[^}]*color-scheme:\s*light", CSS), \
        "`:root` must declare `color-scheme: light`"
    assert re.search(r"\.dark\s*\{[^}]*color-scheme:\s*dark", CSS), \
        "`.dark` must declare `color-scheme: dark`"


def test_bare_form_controls_get_the_theme_field_colours():
    """140 of the 204 text/select/textarea elements in the templates carry layout
    classes only and rely on the browser's default white field, so the default has
    to be set once, centrally. Element-level specificity is the point: `.input`
    and any `bg-*`/`text-*` a template does apply are classes and must still win.
    """
    rules = dark_rules().splitlines()

    controls = [line for line in rules
                if re.search(r":is\(input,\s*select,\s*textarea\)", line)
                and "background-color: var(--input)" in line]
    assert controls, (
        "no dark rule gives bare input/select/textarea the theme field colour, so "
        "every unstyled field stays a white slab on a dark page")
    assert any("color: var(--text)" in line for line in controls), \
        "the bare-control rule must set the text colour too, not just the fill"

    assert any("::placeholder" in line and "var(--text-muted)" in line for line in rules), \
        "placeholders fall back to the browser's slate-400, which fails AA on any field"

    # Element-level, so a template's own classes still outrank it. `:where(.dark)`
    # is stripped first — it is where the theme class lives and contributes no
    # specificity — so any `.` left in the selector is a real class, and a rule
    # like `.card :is(input)` (0,1,1) would outrank a field's own `bg-white` (0,1,0)
    # and override a control that was deliberately styled.
    for line in controls:
        selector = re.sub(r":where\([^)]*\)", "", line.split("{")[0])
        assert "." not in selector, \
            f"this rule names a class and would beat a template's own styling: {line.strip()}"


def test_the_root_paints_its_own_text_colour():
    """Otherwise anything without a `text-*` class inherits the UA's black, which
    is unreadable on `.dark` — the same defect seen through another door."""
    assert re.search(r"body\s*\{[^}]*color:\s*var\(--text\)", CSS), \
        "body must paint `color: var(--text)` so unstyled text follows the theme"


def test_the_input_boundary_is_visible_in_dark_mode():
    """On a dark page the field fill is only 1.20:1 against the page, so the
    border is the only thing identifying the control — WCAG 1.4.11 wants 3:1.
    This is the boundary that carries meaning, which is why it is held here and
    decorative card borders are not."""
    dark = tokens(".dark")

    assert contrast(dark["input-border"], dark["input"]) >= 3.0
    assert contrast(dark["input-border"], dark["bg-body"]) >= 3.0


def test_the_light_input_boundary_matches_the_light_design():
    """Light mode identifies the field by its white fill on a grey page, which is
    the pre-existing look; the border only has to be present."""
    light = tokens(":root")

    assert contrast(light["input-border"], light["input"]) >= 1.4


def test_dark_mode_is_darker_than_light_mode():
    """A guard on the whole table: if someone pastes the light values into
    `.dark`, contrast still passes and every screenshot looks wrong."""
    light, dark = tokens(":root"), tokens(".dark")

    for token in ("bg-body", "bg-card", "bg-subtle", "bg-hover", "input"):
        assert luminance(dark[token]) < luminance(light[token]), token
    for token in ("text", "text-dim"):
        assert luminance(dark[token]) > luminance(light[token]), token


# ── the semantic tints ───────────────────────────────────────────────────────

def _rule_colour(hue: str, kind: str) -> list[str]:
    """Colours declared for `.bg-<hue>-50` / `.text-<hue>-…` inside the dark rules."""
    found = []
    for line in dark_rules().splitlines():
        if f".{kind}-{hue}-" not in line:
            continue
        for value in re.findall(r"#([0-9a-fA-F]{6})\b", line):
            found.append("#" + value)
        for value in re.findall(r"colour:\s*(#[0-9a-fA-F]{6})", line):
            found.append(value)
    return found


def tinted_hues() -> list[str]:
    """Hues whose tint carries a literal colour.

    The neutrals are painted with `var(--bg-subtle)`, not a hex, so they have no
    tint/accent pair to check here — their contrast is held by the token tests
    instead. Matching on `.bg-<hue>-50` alone would sweep them in and demand a
    hex they deliberately do not have.
    """
    found = set()
    for line in dark_rules().splitlines():
        if not re.search(r"background:\s*#[0-9a-fA-F]{6}", line):
            continue
        found.update(match.group(1) for match in re.finditer(r"\.bg-([a-z]+)-\d+", line))
    return sorted(found)


def test_every_hue_accent_reads_on_its_own_tint():
    """A badge is `bg-<hue>-100` + `text-<hue>-700`. Remapping only the text is
    what produced light-on-light, so the two halves are checked as a pair."""
    hues = tinted_hues()
    assert hues, "no hue tints found — the semantic block is missing"
    assert len(hues) >= 15, f"only {len(hues)} hues found — the block looks trimmed: {hues}"

    for hue in hues:
        tints = [c for c in _rule_colour(hue, "bg") if c]
        accents = [c for c in _rule_colour(hue, "text") if c]
        assert tints, f"{hue}: the tint background has no dark value"
        assert accents, f"{hue}: the accent text has no dark value"
        for tint in set(tints):
            for accent in set(accents):
                ratio = contrast(accent, tint)
                assert ratio >= 4.5, (
                    f"{hue}: {accent} on {tint} is {ratio:.2f}:1 — a badge would "
                    f"be unreadable")


def test_saturated_button_backgrounds_are_left_alone():
    """`bg-primary-600 text-white` is a button and must stay saturated. A shade
    rule (rather than a per-property one) would have lightened it and made the
    label invisible, which is the trap this design avoids."""
    rules = dark_rules()

    for hue in ("primary", "emerald", "red", "amber", "blue"):
        for shade in (500, 600, 700):
            assert f".bg-{hue}-{shade}" not in rules, (
                f"bg-{hue}-{shade} is a button background and must not be remapped")


# ── print must not inherit the dark theme ────────────────────────

def _css_no_comments() -> str:
    return re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def _media_body(css: str, condition: str) -> str:
    """The text inside `@media <condition> { ... }`, brace-matched."""
    match = re.search(r"@media\s+" + re.escape(condition) + r"\s*\{", css)
    assert match, f"no `@media {condition}` block in base.html"
    start = match.end() - 1
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start + 1:i]
    raise AssertionError(f"`@media {condition}` is never closed")


def test_the_dark_theme_is_scoped_to_the_screen():
    """Browsers drop background colours when printing but keep text colours, so a
    dark-mode user printing a result sheet got near-white text on white paper —
    a blank page. Every dark rule, tokens included, has to sit inside `@media
    screen` so print falls back to the light `:root` palette by itself.

    This is a structural check because the failure only shows up on paper: no
    screenshot of the running app can catch it.
    """
    css = _css_no_comments()
    inside_screen = _media_body(css, "screen")
    outside_screen = css.replace(inside_screen, "", 1)

    assert ":where(.dark)" in inside_screen, "the dark remaps are not in @media screen"
    assert ".dark {" in inside_screen, "the dark tokens are not in @media screen"

    leaked = re.findall(r":where\(\.dark\)[^{]*\{[^}]*\}", outside_screen)
    assert not leaked, (
        "these dark rules sit outside `@media screen` and would therefore be "
        "printed:\n  " + "\n  ".join(rule.strip()[:100] for rule in leaked))

    assert re.search(r"\.dark\s*\{[^}]*--bg-body", outside_screen) is None, \
        "the dark tokens are declared outside @media screen"


def test_print_pins_the_page_to_the_light_palette():
    """The token fallback covers the text; this covers the browsers that do print
    backgrounds, where a slate body would come out as a grey block."""
    print_block = _media_body(_css_no_comments(), "print")

    assert re.search(r"body\s*\{[^}]*background:\s*#fff", print_block), \
        "print must force a white page background"
    assert ".no-print" in print_block, \
        "print must keep the .no-print escape hatch the result sheet relies on"


# ── charts draw their own text, so they need the theme too ───────

CHART_PAGES = ["teacher/dashboard.html", "teacher/analytics.html",
               "super_admin/omr_test.html"]


def test_charts_take_their_colours_from_the_theme():
    """Chart.js defaults its tick colour to #666 — 2.9:1 on a dark card, the least
    readable text in the app. The fix belongs in one place, reading the same CSS
    tokens, not in each chart config."""
    assert "Chart.defaults.color" in SOURCE, \
        "base.html must set Chart.defaults.color from the theme"
    assert "--text-dim" in SOURCE, \
        "the chart colour must come from the token, not a second hardcoded hex"
    assert "MutationObserver" in SOURCE and "attributeFilter: ['class']" in SOURCE, \
        "chart re-theming must watch the theme class, so it survives a new toggle"


def test_no_chart_page_pins_a_light_only_colour():
    """An explicit `grid: { color: 'rgba(0,0,0,…)' }` in a chart config overrides
    the global default and disappears on a dark card."""
    offenders = []
    for rel in CHART_PAGES:
        path = ROOT / "app" / "templates" / rel
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(grid|ticks|legend)\s*:\s*\{[^}]*color\s*:\s*'rgba\(0,0,0", line):
                offenders.append(f"{rel}:{i}")

    assert not offenders, (
        "these chart configs pin a light-only colour, which Chart.js will use in "
        "dark mode too:\n  " + "\n  ".join(offenders))


# The rule that a page may not load Chart.js twice now lives in
# tests/unit/test_asset_loading.py, which applies it to every library base.html
# provides rather than to Chart.js alone. That duplicate resets Chart.defaults and
# throws away the theme set below, so the two files describe the same failure from
# different ends — the asset file owns the rule, this one owns the colours.


# ── coverage of the light utilities the templates actually use ───────────────

NEUTRALS = {"surface", "slate", "stone", "gray", "zinc", "neutral"}

# Exemptions are where a check like this quietly stops working, so the list is
# **empty**: every light utility that needs a dark rule has one. It stays here as
# a mechanism with a reason attached to it, and the test below refuses an entry
# whose class is not actually used — so a future exemption has to be argued for,
# not merely added to silence a failure. `needs_dark_rule()` is what keeps the
# list empty: the classes that are legitimately fine on dark (saturated button
# fills, `text-white` over them, translucent white overlays on the sidebar
# gradient) are excluded by kind and shade, not by exemption.
JUSTIFIED_EXEMPTIONS: dict[str, str] = {}

UTILITY = re.compile(
    r"\b(?:(hover|focus|group-hover|focus-within|active|disabled|dark):)?"
    r"(bg|text|border|divide|from|to|via)-"
    r"(white|black|[a-z]+)(?:-(\d{2,3}))?(?:/(\d+))?"
)
KINDS = ("bg", "text", "border", "divide", "from", "to", "via")


def used_utilities() -> dict[str, int]:
    """Every colour utility in the templates, keyed the way a template writes it."""
    found: dict[str, int] = {}
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for variant, kind, palette, shade, alpha in UTILITY.findall(text):
            if kind not in KINDS:
                continue
            if palette not in NEUTRALS and palette not in HUES:
                continue
            if variant == "dark":
                continue                     # an explicit override, not a light class
            name = f"{variant + ':' if variant else ''}{kind}-{palette}"
            if shade:
                name += f"-{shade}"
            if alpha:
                name += f"/{alpha}"
            found[name] = found.get(name, 0) + 1
    return found


HUES = {"primary", "brand", "emerald", "red", "amber", "yellow", "orange", "green",
        "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia",
        "pink", "rose"}


def needs_dark_rule(name: str) -> bool:
    """Does this light utility paint something that would be wrong in dark mode?

    Judged by kind and shade, because the same palette and shade are used for
    both a background and text: `bg-primary-600` is a button, `text-primary-600`
    is a link on a card. A rule that treats them alike breaks one of them.
    """
    variant, _, rest = name.partition(":")
    if variant and variant not in ("hover",):
        return False                          # focus/active accents are saturated hues
    kind, _, tail = rest.partition("-")
    palette, _, shade = tail.partition("-")
    shade, _, alpha = shade.partition("/")
    numeric = int(shade) if shade.isdigit() else None

    if alpha and not (palette == "white" and kind == "bg"):
        return False                          # translucent tints composite correctly

    if kind in ("from", "to", "via"):
        if palette == "white":
            return True
        if palette in NEUTRALS:
            return numeric in (50, 100)
        return palette in HUES and numeric in (50, 100, 200)

    if palette == "white":
        # bg-white is every card and the remap covers /60 – /90; text-white and
        # border-white sit on saturated or dark surfaces.
        return kind == "bg" and (numeric is None or numeric in (60, 70, 80, 90))

    if kind == "bg":
        if palette in NEUTRALS:
            return numeric in (50, 100, 200, 300)
        return palette in HUES and numeric in (50, 100, 200)

    if kind == "text":
        if palette in NEUTRALS:
            return numeric in (200, 300, 400, 500, 600, 700, 800, 900)
        return palette in HUES and numeric in (200, 300, 400, 500, 600, 700, 800, 900)

    if kind in ("border", "divide"):
        if palette in NEUTRALS:
            return numeric in (50, 100, 200, 300)
        return palette in HUES and numeric in (100, 200, 300, 400)

    return False


def test_every_light_utility_the_templates_use_has_a_dark_rule():
    """The check that would have caught `bg-emerald-100` being left out."""
    covered = covered_classes()
    required = {name: count for name, count in used_utilities().items()
                if needs_dark_rule(name)}

    missing = sorted((name, count) for name, count in required.items()
                     if name not in covered and name not in JUSTIFIED_EXEMPTIONS)


    assert not missing, (
        "these light utilities are used in the templates but have no dark rule, so "
        "they keep their light colour on a dark page:\n  "
        + "\n  ".join(f"{name}  ({count}×)" for name, count in missing))


def test_the_exemption_list_does_not_grow_silently():
    """An exemption is where this check stops working, so each one has to name a
    reason and point at a class the templates actually use."""
    used = set(used_utilities())
    required = {name for name in used if needs_dark_rule(name)}

    stale = sorted(JUSTIFIED_EXEMPTIONS.keys() - used)
    assert not stale, (
        "this exemption is no longer needed (or never was) — delete it rather than "
        "leave a hole in the check:\n  " + "\n  ".join(stale))

    unexplained = sorted(name for name, why in JUSTIFIED_EXEMPTIONS.items() if not why.strip())
    assert not unexplained, f"these exemptions have no stated reason: {unexplained}"

    ineffective = sorted(JUSTIFIED_EXEMPTIONS.keys() - required)
    assert not ineffective, (
        "these exemptions are dead weight — the class does not need a dark rule in "
        "the first place, so exempting it hides nothing but adds to the list:\n  "
        + "\n  ".join(ineffective))

    assert len(JUSTIFIED_EXEMPTIONS) < 20, (
        "the exemption list has grown large enough that it is doing the work the "
        "remap should be doing")


def test_the_remap_does_not_shadow_explicit_dark_utilities():
    """`:where(.dark)` keeps these at one class of specificity, so a template that
    asks for `dark:bg-gray-900` (the whiteboard chrome) still gets it. An
    `!important` here would silently win instead."""
    rules = dark_rules()

    assert "!important" not in rules, (
        "an !important remap overrides a template's own dark: utility")
    for path in TEMPLATES:
        if "dark:" in path.read_text(encoding="utf-8", errors="replace"):
            assert ":where(.dark)" in rules
            break
