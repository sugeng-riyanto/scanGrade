"""Dark mode has to be measured, not eyeballed.

There are 114 templates and ~440 colour utilities, and they are written in light
mode: `text-slate-600` on `bg-white` is the default shape of a card here. Rather
than a `dark:` variant on every element, the stylesheet base.html links
(app/static/css/theme.css) remaps those light utilities
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
#
# These rules used to live in a `<style>` block inside base.html. They are a
# file now — app/static/css/theme.css, linked from base.html — because an
# inline block rides inside every page: it cannot be cached, nginx re-gzips it
# on each request, and a phone on a weak signal re-downloads it on every
# navigation. The rules are the same rules, so the checks below read the same
# CSS from its new home rather than relaxing by a single assertion.
#
# tests/unit/test_theme_stylesheet.py is the guard for that move: it fails if
# the block is ever inlined into base.html again, if the file is linked before
# tailwind.css (whose utilities it overrides), or if a Jinja tag appears in it
# (which would silently stop it from being a static file at all).
THEME_CSS = ROOT / "app" / "static" / "css" / "theme.css"


def style_block() -> str:
    assert THEME_CSS.is_file(), (
        f"{THEME_CSS.relative_to(ROOT)} is missing — the app's tokens, dark-mode "
        "remap and legibility floor live there, and base.html links it")
    return THEME_CSS.read_text(encoding="utf-8")


CSS = style_block()


def tokens(selector: str) -> dict[str, str]:
    """The `--custom` properties declared for one selector."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"no {selector} block in {THEME_CSS.relative_to(ROOT)}"
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


def test_the_light_input_boundary_is_visible_in_light_mode():
    """The mirror of the dark check, and now held to the same 3:1.

    Light mode identifies a field by its border, not by its fill: the fill is
    white on a #f1f5f9 page, so 1.06:1 — nothing at all. An earlier version of
    this test asked for only 1.4:1 and called the gap "the pre-existing look",
    which was an exemption wearing a design note, and the border of the day
    (#cbd5e1) did not even clear the 1.4 it claimed to test (1.36:1 against the
    page). WCAG 1.4.11 wants 3:1 for a boundary that carries meaning; this one
    is the only thing that says "this is a field", so it carries it.
    """
    light = tokens(":root")

    assert contrast(light["input-border"], light["input"]) >= 3.0, (
        "the field's fill is 1.06:1 against the page, so the border is the only "
        "thing identifying the control")
    assert contrast(light["input-border"], light["bg-body"]) >= 3.0


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
    assert match, f"no `@media {condition}` block in {THEME_CSS.relative_to(ROOT)}"
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


# ── a new page cannot opt out of the theme by accident ───────────

EXTENDS = re.compile(r"""\{%-?\s*extends\s+["']([^"']+)["']""")

# Everything above assumes the page inherits base.html, because that is where
# the remap lives. A template with its own `<html>` inherits none of it: it keeps
# the light utilities it was written with, on whatever background it declares.
# That is a silent failure of the same kind — nothing errors, and the page is
# unreadable in dark mode.
#
# So a standalone page has to be *declared*, with a reason. Two kinds are
# legitimate: a document meant for paper (it pins its own light colours because
# dark ink on white paper is the point), and a screen page that carries its own
# dark handling. Which one a file is, is read from the file rather than asserted
# here, so the list cannot be used to bless an ordinary page that simply forgot.
STANDALONE_PAGES: dict[str, str] = {
    "monitor.html":
        "operator wall display — deliberately always dark, so it carries its own "
        "palette instead of following the reader's theme",
    "print/report_card.html":
        "printed report card — a document for paper, not a screen",
    "student/result_detail_pdf.html":
        "the PDF export's HTML, rendered to a file and never browsed",
    "teacher/print_exam_report.html":
        "printed per-exam results sheet — a document for paper, not a screen",
}
PAPER_MARKER = "@page"
DARK_MARKER = re.compile(r"prefers-color-scheme|\.dark\b")


def _standalone_documents() -> dict[str, str]:
    """Templates that render a whole document, excluding base.html itself."""
    found = {}
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "<html" not in text.lower() or path == BASE:
            continue
        match = EXTENDS.search(text)
        if match and match.group(1).endswith("base.html"):
            continue
        found[str(path.relative_to(ROOT / "app" / "templates")).replace("\\", "/")] = text
    return found


def test_a_new_page_cannot_skip_the_theme_by_forgetting_to_extend_base():
    """The hole the utility check cannot see.

    `test_every_light_utility_the_templates_use_has_a_dark_rule` is satisfied by
    base.html alone, so a brand-new page with its own `<html>` passes it while
    rendering every colour untranslated. A page that renders a whole document on
    a screen therefore has to inherit base.html, be declared here, and be either
    a paper document or one that brings its own dark handling.
    """
    undeclared, no_palette = [], []

    for rel, text in _standalone_documents().items():
        if rel not in STANDALONE_PAGES:
            undeclared.append(rel)
            continue
        # Read from the file, not assumed: a declared paper document has to
        # actually be one, and a declared screen page has to handle dark itself.
        if PAPER_MARKER in text or DARK_MARKER.search(text):
            continue
        no_palette.append(rel)

    assert not undeclared, (
        "these templates render their own document, so they inherit none of the "
        "dark-mode remap in app/static/css/theme.css and every light class in them "
        "stays light. "
        "Either extend base.html, or add one with a reason to STANDALONE_PAGES:\n  "
        + "\n  ".join(sorted(undeclared)))

    assert not no_palette, (
        "these are declared standalone but do neither: no `@page` (so they are not "
        "paper) and no dark palette of their own (so they are unreadable in dark "
        "mode):\n  " + "\n  ".join(sorted(no_palette)))


def test_the_standalone_list_stays_honest():
    """Same rule as the exemption list: an entry has to name a reason and point ""
    "at a page that is genuinely standalone, so the list cannot grow into a place "
    "where failures are hidden."""
    declared = set(STANDALONE_PAGES)
    actual = set(_standalone_documents())

    stale = sorted(declared - actual)
    assert not stale, (
        "these are declared standalone but either no longer exist or now extend "
        "base.html — delete them rather than leave the list describing a tree that "
        "is not there:\n  " + "\n  ".join(stale))

    unexplained = sorted(name for name, why in STANDALONE_PAGES.items() if not why.strip())
    assert not unexplained, f"these standalone pages have no stated reason: {unexplained}"

    assert len(STANDALONE_PAGES) < 10, (
        "this list is growing large enough that it is becoming the way pages avoid "
        "the theme; a page that should extend base.html must do so")


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
    # Only a *prefixed* name has a variant. `partition(":")` hands back the whole
    # string in `variant` when there is no colon at all, which made this return
    # False for every plain utility — so the coverage rule below only ever saw
    # `hover:` classes and none of the ones the templates actually paint with.
    if ":" in name:
        variant, rest = name.split(":", 1)
        if variant != "hover":
            return False                      # focus/active accents are saturated hues
    else:
        rest = name
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


# ══ Light mode is held to the same standard ══════════════════════════════════
#
# Everything above asks whether a *token* or a *dark remap* is readable. None of
# it looks at the colours a template paints together, and in light mode there is
# no remap to inspect: the palette in the compiled stylesheet is what ships. So
# `bg-amber-100 text-amber-600` — a warning badge, thirteen of them in the tree —
# was 2.86:1 and nothing here could see it, because `text-amber-600` is perfect
# on a white card and fails only on a tint of its own hue.
#
# The dark half of the story is a remap, so the light half is a correction: a
# rule in that stylesheet that names *both* halves of one pair. That is also what makes
# it fixable rather than just reportable — the accent steps down a shade or two
# and the tint stays as designed.

STYLESHEET = ROOT / "app" / "static" / "css" / "tailwind.css"
LIGHT_SHEET = STYLESHEET.read_text(encoding="utf-8")

_CSS_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_CLASS = re.compile(r"\.((?:[A-Za-z0-9_-]|\\.)+)")
_COLOUR_PROPERTY = {"bg": ("background-color",), "text": ("color",)}


def _declared_colour(body: str) -> str | None:
    """The colour a declaration block paints, as `#rrggbb`."""
    match = re.search(r"rgb\(\s*(\d+)\s+(\d+)\s+(\d+)", body)
    if match:
        return "#%02x%02x%02x" % tuple(int(channel) for channel in match.groups())
    match = re.search(r"#([0-9a-fA-F]{6})\b", body)
    return f"#{match.group(1)}" if match else None


def _stylesheet_palette() -> dict[str, dict[str, str]]:
    """`bg-amber-100` / `text-amber-600` -> the colour the browser will use.

    The sheet is minified and Tailwind groups classes that share a declaration,
    so this walks rule blocks rather than lines — `.bg-slate-50` and
    `.bg-surface-50` are the same blue-grey and live in one block. A translucent
    class is skipped: its colour is a composite over whatever is behind it, which
    a static check cannot know.
    """
    palette: dict[str, dict[str, str]] = {kind: {} for kind in _COLOUR_PROPERTY}

    for selector, body in _CSS_BLOCK.findall(LIGHT_SHEET):
        for raw in _CSS_CLASS.findall(selector):
            name = raw.replace("\\:", ":").replace("\\/", "/").replace("\\.", ".")
            if "/" in name:
                continue
            if ":" in name:
                _, _, name = name.rpartition(":")       # drop the variant prefix
            for kind, properties in _COLOUR_PROPERTY.items():
                if not name.startswith(kind + "-") or name in palette[kind]:
                    continue
                if any(prop in body for prop in properties):
                    colour = _declared_colour(body)
                    if colour:
                        palette[kind][name] = colour
    return palette


LIGHT_PALETTE = _stylesheet_palette()

# `:where(.bg-amber-100).text-amber-600 { color: #b45309 }` in theme.css.
CORRECTION_RULE = re.compile(
    r"^\s*:where\(\.(bg-[a-z0-9-]+)\)\.(text-[a-z0-9-]+)\s*"
    r"\{\s*color:\s*(#[0-9a-fA-F]{6})\s*;\s*\}", re.M)

# WCAG 1.4.3 for text, 1.4.11 for a graphical object: an icon circle is not a
# sentence, so a glyph is not asked for the text minimum.
TEXT_MINIMUM = 4.5
GLYPH_MINIMUM = 3.0

_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")
_COLOUR_TOKEN = re.compile(
    r"^(?:(hover|focus|group-hover|focus-within|active|disabled):)?"
    r"(bg|text)-([a-z]+|white|black)(?:-(\d{2,3}))?(?:/(\d+))?$")


def light_corrections() -> dict[tuple[str, str], str]:
    """`(bg class, text class) -> colour` for the rules this file has to hold."""
    return {(bg, text): colour.lower()
            for bg, text, colour in CORRECTION_RULE.findall(CSS)}


def _strip_markup(fragment: str) -> str:
    """What a reader sees: no tags, no template expressions, no punctuation."""
    fragment = re.sub(r"<[^>]*>", "", fragment)
    fragment = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", fragment, flags=re.S)
    return re.sub(r"[\W_]+", "", fragment, flags=re.UNICODE)


def _element_text(source: str, tag_start: int) -> str | None:
    """Visible text inside the element whose tag opens at `tag_start`.

    `""` means the element carries none — an icon or a glyph, which WCAG 1.4.11
    holds to 3:1 rather than the 4.5:1 of 1.4.3. `None` means the tag could not
    be matched, and the caller has to assume the stricter rule: an unresolved
    span must never become a quiet pass.
    """
    opening = _TAG.match(source, tag_start)
    if not opening:
        return None
    if re.search(r"\bx-(?:text|html)\b", opening.group(3)):
        return "x"                       # the text arrives from Alpine, not markup
    tag = opening.group(2).lower()
    depth, inner_start = 1, opening.end()
    for element in _TAG.finditer(source, opening.end()):
        if element.group(2).lower() != tag:
            continue
        if element.group(1) == "/":
            depth -= 1
            if depth == 0:
                inner = source[inner_start:element.start()]
                if re.search(r"\bx-(?:text|html)\b", inner):
                    return "x"
                return _strip_markup(inner)
        elif not element.group(3).rstrip().endswith("/"):
            depth += 1
    return None


def _class_groups(attribute: str, alpine_binding: bool) -> list[str]:
    """The class lists in one attribute, one per branch.

    `:class="ok ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'"`
    holds two elements' worth of classes in one attribute, and crossing the
    branches invents pairs that never render together — that is where the
    phantom `bg-orange-500 text-stone-500` came from, out of an Alpine object
    whose two keys are alternatives. Every literal is therefore its own group,
    and a template's own `{% %}` splits the same way.
    """
    if alpine_binding:
        return [found or literal for found, literal
                in re.findall(r"'([^']*)'|\"([^\"]*)\"", attribute)]
    return re.split(r"\{%.*?%\}", attribute)


def _scan_class_pairs() -> tuple[dict[tuple[str, str], list[tuple[str, int, str]]], set[str]]:
    """Every background/text pair a single element is asked to paint together.

    Also returns the palette utilities seen, which is the set the coverage check
    has to measure: it comes from `class` attributes alone, so it cannot be
    satisfied by a class that only the stylesheet's own remap mentions.
    """
    pairs: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
    observed: set[str] = set()

    for path in TEMPLATES:
        source = path.read_text(encoding="utf-8", errors="replace")
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        for attribute in re.finditer(r'(?::)?class="([^"]*)"', source):
            judged = _element_text(source, source.rfind("<", 0, attribute.start()))
            kind = "text" if judged else ("icon" if judged == "" else "unresolved")
            line = source[:attribute.start()].count("\n") + 1
            for group in _class_groups(attribute.group(1), source[attribute.start()] == ":"):
                fills, inks = [], []
                for token in group.split():
                    match = _COLOUR_TOKEN.match(token)
                    if not match or match.group(1) or match.group(5):
                        continue         # a variant, or a fill whose composite is unknown
                    palette, shade = match.group(3), match.group(4)
                    if palette not in NEUTRALS and palette not in HUES \
                            and palette not in ("white", "black"):
                        continue
                    name = f"{match.group(2)}-{palette}" + (f"-{shade}" if shade else "")
                    observed.add(name)
                    (fills if match.group(2) == "bg" else inks).append(name)
                for fill in fills:
                    for ink in inks:
                        pairs.setdefault((fill, ink), []).append((relative, line, kind))
    return pairs, observed


ELEMENT_PAIRS, OBSERVED_UTILITIES = _scan_class_pairs()


def _required(sites: list[tuple[str, int, str]]) -> float:
    """The stricter tier a pair is held to, since one rule covers every site."""
    return GLYPH_MINIMUM if all(kind == "icon" for _, _, kind in sites) else TEXT_MINIMUM


def _is_saturated(fill: str) -> bool:
    """Is this fill one of the app's saturated button surfaces?

    The dark theme keeps `bg-<hue>-500/600/700` exactly as they are, and says so
    in `test_saturated_button_backgrounds_are_left_alone`; a white label on one
    is the same decision seen from the light side.
    """
    hue, _, shade = fill.removeprefix("bg-").rpartition("-")
    return hue in HUES and shade.isdigit() and int(shade) >= 400


def test_the_light_palette_is_read_from_the_stylesheet_that_ships():
    """No pair may be skipped for a colour the stylesheet does not declare.

    A class the sheet never declares paints nothing, and that has two causes: a
    template written after the last `npm run css:build`, or a reader here that
    tripped over a grouped selector. Either one makes the checks below pass by
    looking at fewer pairs, which is the exact failure mode this file exists to
    prevent — so the coverage is asserted before anything trusts it.
    """
    unresolved = sorted(
        name for name in OBSERVED_UTILITIES
        if name not in LIGHT_PALETTE[name.split("-", 1)[0]])

    assert not unresolved, (
        "these colour utilities appear in a template's class attributes but the "
        "compiled stylesheet never declares them, so they paint nothing and the "
        "pairs built from them cannot be measured — run `npm run css:build`:\n  "
        + "\n  ".join(unresolved))


def test_every_tint_and_the_text_painted_on_it_is_readable_in_light_mode():
    """The check that would have caught the amber badge.

    Held to WCAG 1.4.3 (4.5:1) where the element renders text and to 1.4.11
    (3:1) where it renders a glyph. A pair used both ways has to satisfy the
    stricter of the two, because one correction covers every site of the pair.

    A saturated fill with a white label is deliberately out of scope and is held
    by `test_the_saturated_fills_that_are_deliberate_keep_a_readable_label`.
    """
    corrections = light_corrections()
    offenders = []

    for (fill, ink), sites in ELEMENT_PAIRS.items():
        fill_colour = LIGHT_PALETTE["bg"].get(fill)
        ink_colour = LIGHT_PALETTE["text"].get(ink)
        if fill_colour is None or ink_colour is None:
            continue
        if ink in ("text-white", "text-black") and _is_saturated(fill):
            continue
        painted = corrections.get((fill, ink), ink_colour)
        needed = _required(sites)
        if contrast(painted, fill_colour) < needed:
            offenders.append((contrast(painted, fill_colour), fill, ink, needed, sites))

    offenders.sort(key=lambda row: row[0])
    assert not offenders, (
        "these background/text pairs are painted on one element and do not "
        "clear their contrast minimum in light mode. Darken the accent in the "
        "light-mode block in theme.css — the rule names both halves of the "
        "pair, so the tint can stay as designed:\n  "
        + "\n  ".join(
            f"{fill} + {ink} is {ratio:.2f}:1 (needs {needed}), "
            f"{len(sites)} site(s), first {sites[0][0]}:{sites[0][1]}"
            for ratio, fill, ink, needed, sites in offenders))


def test_the_saturated_fills_that_are_deliberate_keep_a_readable_label():
    """The surface excluded above, still held to something.

    `bg-emerald-600 text-white` is 3.77:1 and stays that way: it is the app's
    button, and the dark theme deliberately keeps those fills saturated. What is
    not acceptable is a *grey* on one of them — that is neither the white-label
    convention nor a tint, and it would otherwise fall between the two rules.
    """
    offenders = []

    for (fill, ink), sites in ELEMENT_PAIRS.items():
        if not _is_saturated(fill) or ink in ("text-white", "text-black"):
            continue
        fill_colour = LIGHT_PALETTE["bg"].get(fill)
        ink_colour = LIGHT_PALETTE["text"].get(ink)
        if fill_colour is None or ink_colour is None:
            continue
        ratio = contrast(ink_colour, fill_colour)
        if ratio < _required(sites):
            offenders.append(f"{fill} + {ink} is {ratio:.2f}:1 at "
                             f"{sites[0][0]}:{sites[0][1]}")

    assert not offenders, (
        "these saturated fills carry a label that is neither white nor black, "
        "so they are excluded from the tint check and fail on their own:\n  "
        + "\n  ".join(offenders))


# A rule that names one background class and one text class: a correction, by
# shape rather than by the `:where()` form this file would like to see.
PAIR_SELECTOR = re.compile(r"\.bg-[a-z]+-\d+.*\.text-[a-z]+-\d+")
CANONICAL_CORRECTION = re.compile(r":where\(\.bg-[a-z0-9-]+\)\.text-[a-z0-9-]+$")


def _light_section() -> str:
    """The part of the sheet a light-mode rule may live in: above the dark theme."""
    dark_at = CSS.find("@media screen {")
    assert dark_at >= 0, "the dark theme is no longer in an `@media screen` block"
    return CSS[:dark_at]


def test_the_light_corrections_cannot_outrank_the_dark_theme():
    """Why every correction is written as `:where(.bg-x).text-y`.

    `:where()` contributes no specificity, so each rule counts as the single
    class `.text-y` — exactly what `:where(.dark) .text-y` in the remap counts
    as. With both at one class, the remap being later in the sheet is what lets
    dark mode win. Give the background half a real class and the dark theme
    silently inherits a light-mode accent.

    The rule is matched by *shape* — any selector naming a background class and
    a text class — not by the canonical form, so a correction that dodges the
    form is caught rather than skipped. The section boundary does the other half:
    a pair rule below `@media screen` would only apply on screen, and print, which
    drops the remap and keeps the light palette, would go back to unreadable.
    """
    pair_rules = [selector.strip() for selector, _ in _CSS_BLOCK.findall(_light_section())
                  if PAIR_SELECTOR.search(selector)]
    assert pair_rules, (
        "no light-mode corrections found above the dark theme — did the block "
        "move, or move inside `@media screen`, where print cannot see it?")

    for selector in pair_rules:
        assert CANONICAL_CORRECTION.search(selector), (
            "this rule names a background class without wrapping it in :where(), "
            "so it outranks `:where(.dark) .text-y` in the remap and dark mode "
            f"inherits the light-mode accent: {selector}")


def test_the_light_corrections_are_each_doing_work():
    """An unnecessary correction is a check that stopped running.

    Two ways one goes wrong: it names a pair no template paints (the list has
    become a place to park failures), or it names a pair that already cleared
    its minimum without it (the rule is dead weight that hides nothing). Either
    one is deleted, not left behind — the same rule the exemption lists above
    are held to.
    """
    corrections = light_corrections()

    unused = sorted(pair for pair in corrections if pair not in ELEMENT_PAIRS)
    assert not unused, (
        "these corrections name pairs no template paints — delete them rather "
        "than leave the block describing a tree that is not there:\n  "
        + "\n  ".join(f"{fill} + {ink}" for fill, ink in unused))

    dead = []
    for fill, ink in sorted(corrections):
        fill_colour = LIGHT_PALETTE["bg"].get(fill)
        ink_colour = LIGHT_PALETTE["text"].get(ink)
        if fill_colour is None or ink_colour is None:
            continue
        sites = ELEMENT_PAIRS[(fill, ink)]
        ratio = contrast(ink_colour, fill_colour)
        if ratio >= _required(sites):
            dead.append(f"{fill} + {ink} is {ratio:.2f}:1 without it")

    assert not dead, (
        "these corrections are not needed, so they only make the block look "
        "busier than the problem is — delete them:\n  " + "\n  ".join(dead))
