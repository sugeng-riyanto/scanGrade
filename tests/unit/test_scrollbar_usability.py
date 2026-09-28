"""The scrollbar is a control, so it is held to the bar the rest of the theme is
held to: seen, grabbed and read in both themes, on a mouse and on a finger.

What was there
--------------
`theme.css` drew `::-webkit-scrollbar { width: 5px }` over a *transparent*
track, with a slate thumb (#cbd5e1, #94a3b8 on hover; #3a4056 and #4a5470 on a
dark page) and no drag state at all. Five pixels is thinner than the inset of
any other control in the app, a transparent track gives nothing to aim at, and
nothing changes when you take hold of it. The control a reader touches most
often was the only one nobody had measured.

What this file measures
-----------------------
* **The scale, not two copies of it.** The size, the groove and the three thumb
  states are `--sb-*` tokens, declared once in `:root` and once in `.dark`, and
  every rule that draws a bar reads them. So this file asks the tokens the
  questions it used to ask the rules — and the numbers are unchanged: 12px, 16px
  for a finger, and 3:1 against the groove in both themes, the WCAG 1.4.11 bar
  `--input-border` already answers to in test_dark_theme_contrast.py.
* **Every state is declared** — thumb, hover and drag — so the bar answers the
  pointer instead of being a static stripe.
* **Front-to-back, both themes.** A token missing from one theme resolves to
  nothing, and the property then keeps the other theme's value: the same failure
  as an unpatched dark rule, reached from the other side.
* **Firefox as well as Chrome/Safari** — and the standard properties are gated
  to Firefox on purpose. A browser that honours `scrollbar-color` stops
  honouring `::-webkit-scrollbar` (Chrome 121+), so an ungated pair would drop
  Chrome to the platform's own width and quietly undo the sizing below. The
  gate is the point, not an accident: what Chrome draws is what these numbers
  say, and what Firefox draws is its native bar in our colours.

What moved elsewhere
--------------------
The app-wide half — that *every* pane in the app is handed this bar, that no
template may draw one of its own, and that no rule is scoped by `.dark` (a
descendant selector cannot paint the element that carries the class, which is the
defect this file once patched twice) — lives in tests/unit/test_scrollbar_panes.py.
This file owns the scale itself.
"""
from __future__ import annotations

import re
import pathlib

from tests.unit import test_dark_theme_contrast as theme

ROOT = pathlib.Path(__file__).resolve().parents[2]
CSS_PATH = ROOT / "app" / "static" / "css" / "theme.css"
CSS = CSS_PATH.read_text(encoding="utf-8")
PLAIN = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)

#: The page surfaces the bar sits on, so "can it be seen at all" is asked of the
#: real background rather than of the groove alone.
PAGE = {"light": "#f1f5f9", "dark": "#0f1117"}

#: The bar's own floor, and what a finger gets. 10px is already twice the 5px the
#: page shipped with; 14px is the small end of what a thumb can hold.
MIN_BAR = 10
MIN_TOUCH_BAR = 14

#: The whole scale: four colours and one size.
COLOURS = ("sb-track", "sb-thumb", "sb-thumb-hover", "sb-thumb-active")
SIZE = "sb-size"
SCALE = (SIZE,) + COLOURS


def _bodies(css: str, opener: str) -> list[tuple[int, str]]:
    """Every `@media <opener> { ... }` in `css`, as (start offset, body).

    Brace-matched rather than regex-ended, because the bodies nest (a
    `@supports` inside a `@media`, a `::-webkit-scrollbar-thumb` inside that).
    The parentheses are optional in the pattern because half the conditions
    here are parenthesised (`(pointer: coarse)`, `(max-width: 640px)`) and half
    are not (`screen`, `print`) — requiring one or the other silently finds
    nothing, which reads as "the rule is missing" rather than "the parser is".
    """
    found = []
    for match in re.finditer(r"@media\s*\(?\s*" + re.escape(opener) + r"\s*\)?\s*\{", css):
        start = match.end() - 1
        depth = 0
        for i in range(start, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    found.append((match.start(), css[start + 1:i]))
                    break
    return found


def _supports_bodies(css: str, condition: str) -> list[tuple[int, str]]:
    """The same, for `@supports (<condition>) { ... }`."""
    assert "@supports" in css or condition not in css, condition
    return _bodies(css.replace("@supports", "@media"), condition)


def declarations_in(css: str, selector: str) -> str | None:
    """The body of the rule whose **selector list** contains `selector`.

    Membership in the list rather than equality with the whole selector, because
    a rule may name more than one carrier and a `re.escape(selector) + '{'`
    reader would report it missing.
    """
    for match in re.finditer(r"([^{}]*?)\{([^}]*)\}", css):
        parts = [p.strip() for p in match.group(1).split(",")]
        if selector in parts:
            return match.group(2)
    return None


def palette(theme_key: str) -> dict[str, str]:
    """The scale as one theme declares it."""
    return theme.tokens(":root" if theme_key == "light" else ".dark")


def resolve(value: str | None, theme_key: str) -> str | None:
    """A declaration's value with a `--sb-*` token substituted.

    The bar is one rule reading one scale, so what a theme paints is the token's
    value; reading the declaration would find `var(--sb-thumb)` and call the light
    and dark bars identical, which is exactly the trap the old two-rule design
    could fall into.
    """
    if value is None:
        return None
    match = re.fullmatch(r"var\(\s*--([a-z0-9-]+)\s*\)", value.strip())
    if not match:
        return value.strip()
    declared = palette(theme_key)
    assert match.group(1) in declared, (
        f"--{match.group(1)} is not declared for {theme_key}, so the bar resolves "
        "to nothing and keeps the other theme's value")
    return declared[match.group(1)]


def colour_of(body: str | None, theme_key: str) -> str | None:
    """The rule's background colour for one theme, tokens resolved."""
    if not body:
        return None
    match = re.search(r"background(?:-color)?\s*:\s*([^;]+)", body)
    return resolve(match.group(1), theme_key) if match else None


def size_of(body: str | None, theme_key: str = "light") -> int | None:
    """The rule's width in pixels for one theme, tokens resolved."""
    if not body:
        return None
    match = re.search(r"width\s*:\s*([^;]+)", body)
    value = resolve(match.group(1), theme_key) if match else None
    return int(value[:-2]) if value and re.fullmatch(r"\d+px", value) else None


def state(theme_key: str, pseudo: str) -> dict:
    """A scrollbar state as it resolves for one theme.

    The theme no longer picks a *rule* — there is one rule per state and it reads
    the scale — so it only decides which token value is substituted.
    """
    body = declarations_in(PLAIN, "::-webkit-scrollbar-" + pseudo)
    return {"selector": "::-webkit-scrollbar-" + pseudo, "body": body,
            "colour": colour_of(body, theme_key), "width": size_of(body, theme_key)}


def size_declarations() -> tuple[dict[str, str], dict[str, str]]:
    """`(the themed declarations, the finger's raise)`, selector -> value.

    Read with the `@media` wrappers unwrapped by hand, because the raise lives
    inside one: a reader that only saw top-level rules would report the size
    declared twice and never raised.
    """
    coarse_bodies = [body for _offset, body in _bodies(PLAIN, "pointer: coarse")]
    flat = PLAIN
    for body in coarse_bodies:
        flat = flat.replace(body, "", 1)

    def _scan(text: str) -> dict[str, str]:
        """Flat rules only, and the prelude may not contain `@`.

        Every declaration this test asks about is a flat rule; the brace-aware walk
        that sweeps at-rule nesting lives in test_scrollbar_panes.py, which needs it
        for the whole sheet. What matters here is that `@media screen {` is not read
        as a *selector* whose body happens to contain the dark block's declaration —
        it is the outermost rule of the theme, and reading it as a name would hide
        `.dark` from the only test that compares the two themes' widths.
        """
        found: dict[str, str] = {}
        for match in re.finditer(r"(?:^|[{};])\s*([^{}@]*?)\{([^{}]*--sb-size\s*:[^{}]*)\}",
                                 text, re.S):
            value = re.search(r"--sb-size\s*:\s*([^;]+)", match.group(2))
            assert value, f"a rule body carries `--sb-size` without declaring it: {match.group(2)!r}"
            for selector in match.group(1).split(","):
                if selector.strip():
                    found[selector.strip()] = value.group(1).strip()
        return found

    themed, raised = _scan(flat), {}
    for body in coarse_bodies:
        raised.update(_scan(body))
    return themed, raised


def reach(selector: str) -> int:
    """How strongly a selector binds, as the cascade orders it.

    Ids weigh most, then classes, attributes and pseudo-classes, then element
    names; `:where()` counts for nothing — which is the whole reason this file uses
    it. A functional pseudo-class counts as a pseudo-class and its **argument is
    discarded**, deliberately: counting the inside of `:is(.dark)` could make a name
    look stronger than it is, and a guard that believes a stronger name is a guard
    that passes a bar nothing ever applied. Blind in this direction it can only
    under-count, which fails loudly and is fixed by naming the carrier outright.
    """
    stripped = re.sub(r":where\([^)]*\)", "", selector)
    stripped = re.sub(r":(?:is|not|has)\([^)]*\)", ":_", stripped)
    ids = len(re.findall(r"#[\w-]+", stripped))
    classes = (len(re.findall(r"\.", stripped)) + len(re.findall(r"\[", stripped))
               + len(re.findall(r":(?!:)", stripped)))
    elements = len(re.findall(r"(?:^|[\s>+~,])([a-zA-Z][\w-]*)", stripped))
    return ids * 100 + classes * 10 + elements


# ── the bar is big enough to hit ─────────────────────────────────────────────

def test_the_bar_is_wider_than_the_stripe_it_replaced():
    """5px was the old value; anything at or under it is a regression."""
    body = declarations_in(PLAIN, "::-webkit-scrollbar")
    assert body is not None, "theme.css no longer styles ::-webkit-scrollbar"
    width = size_of(body)
    assert width is not None and width >= MIN_BAR, (
        f"the scrollbar is {width}px wide; the page shipped a 5px stripe and "
        f"this file holds the bar to {MIN_BAR}px or more")


def test_a_finger_gets_a_bigger_bar_than_a_mouse():
    """The touch block raises the *token*, and its selector list must name **every**
    rule that declares the size.

    Two designs, two failures, and this asks the general form of both. Resized by a
    second `::-webkit-scrollbar` rule inside the media block, the block's *position*
    decided everything: at equal specificity an earlier rule loses, silently. Raised
    by a `:root`-only token, a `.dark` declaration ties it on specificity and is
    written later, so every dark touch device keeps the mouse-sized bar — measured
    in a browser with the same cascade shape. So: whatever declares the size must
    be named by the raise, whether that is one carrier today or three tomorrow.
    """
    themed, raised = size_declarations()
    assert {":root", ".dark"} <= set(themed), (
        f"{sorted(themed)} declare --sb-size; both themes need it, or the property "
        "keeps the other theme's width")
    base_values = {themed[sel] for sel in (":root", ".dark")}
    assert len(base_values) == 1, (
        f"the width differs per theme ({themed}); only the finger's raise may "
        "change it")
    base = base_values.pop()
    assert re.fullmatch(r"\d+px", base) and int(base[:-2]) >= MIN_BAR, (
        f"the mouse bar is {base}; the floor is {MIN_BAR}px")

    assert raised, (
        "no `@media (pointer: coarse)` block raises --sb-size, so a phone gets the "
        "mouse-sized bar")
    touch = next(iter(raised.values()))
    assert re.fullmatch(r"\d+px", touch) and int(touch[:-2]) >= MIN_TOUCH_BAR, (
        f"the touch scrollbar is {touch}; a finger needs {MIN_TOUCH_BAR}px")
    assert int(touch[:-2]) > int(base[:-2]), "the touch bar is not wider than the mouse bar"

    strongest = max(reach(selector) for selector in raised)
    tied = sorted(selector for selector in themed if reach(selector) >= strongest)
    assert not tied, (
        f"{tied} declare --sb-size and the raise does not bind more strongly "
        f"(reaches {sorted({reach(s) for s in raised})}). A tie is decided by source "
        "order, and the `.dark` block is written later than this raise inside "
        "`@media screen` — so a tie means a dark touch device keeps the mouse-sized "
        "bar while the CSS reads as if the finger were answered")

    body = declarations_in(PLAIN, "::-webkit-scrollbar") or ""
    assert re.search(r"width\s*:\s*var\(--sb-size\)", body), (
        "the sizing rule does not read --sb-size, so raising the token for a "
        "finger changes nothing")
    assert re.search(r"height\s*:\s*var\(--sb-size\)", body), (
        "only one axis reads the token, so a horizontal bar — the one a pane with "
        "a wide table shows — keeps the mouse size on a phone")


# ── both themes draw a groove and a grab handle ──────────────────────────────

def test_both_themes_declare_the_whole_scale():
    """A token present in one theme but missing in the other resolves to nothing,
    so the property silently keeps the other theme's value.

    `--sb-size` is held to this too, and the reason is the mirror of the same
    cascade: `.dark` sits on the same element as `:root`, so its block ties the
    `pointer: coarse` raise on specificity while being written later. A missing dark
    size would resolve to the light one (harmless today, wrong the moment they
    differ), and a raise that forgets to name a carrier loses to it outright — that
    half is asserted in `test_a_finger_gets_a_bigger_bar_than_a_mouse`.
    """
    light, dark = palette("light"), palette("dark")
    for name in COLOURS:
        assert name in light, f"--{name} is not declared for light"
        assert name in dark, f"--{name} is not declared for dark"
        assert light[name] != dark[name], (
            f"--{name} is {light[name]} in both themes — a dark page would be "
            "handed the light bar")
    assert SIZE in light, f"--{SIZE} is not declared, so no bar has a width"
    assert SIZE in dark, (
        f"--{SIZE} is missing from `.dark`, so a dark page keeps the light width — "
        "and a token declared in one theme only is the failure this test exists for")
    assert light[SIZE] == dark[SIZE], (
        f"the width is a per-theme value ({light[SIZE]} vs {dark[SIZE]}); only the "
        "finger's raise — which must name both carriers — changes it")


def test_each_theme_paints_a_track_that_is_not_transparent():
    """The transparent track was one half of \"nothing to aim at\"."""
    for theme_key in ("light", "dark"):
        track = state(theme_key, "track")
        assert track["body"] is not None, f"{theme_key}: no scrollbar track"
        assert track["colour"] not in (None, "transparent", "rgba(0,0,0,0)"), (
            f"{theme_key}: the scrollbar track is {track['colour']} — there is "
            "no groove, only a thumb floating on the page")


def test_the_two_themes_do_not_share_a_thumb():
    """A dark page must not be handed the light bar (or the light one the dark),
    which is the whole defect this pair exists to catch."""
    light, dark = state("light", "thumb")["colour"], state("dark", "thumb")["colour"]
    assert light and dark, f"missing a thumb: light={light} dark={dark}"
    assert light != dark, f"both themes paint the thumb {light}"


def test_the_bar_is_drawn_as_a_pill_inset_into_its_groove():
    """A handle sitting in a channel, not a filled strip.

    Asserted as numbers rather than as the presence of the properties, because
    every half of this can go quietly wrong while the rule still reads right:
    `border-radius: 0` keeps a radius and is a rectangle, and dropping the
    transparent border makes the thumb fill the groove edge to edge while keeping
    the radius. The inset only works with `background-clip: padding-box`, or the
    border paints over the handle.
    """
    body = state("light", "thumb")["body"] or ""
    radius = re.search(r"border-radius\s*:\s*([^;]+)", body)
    assert radius, "the thumb has no border-radius, so it is a rectangle again"
    value = radius.group(1).strip()
    px = re.fullmatch(r"(\d+(?:\.\d+)?)px", value)
    assert value == "999px" or (px and float(px.group(1)) >= 4), (
        f"the thumb's radius is {value} — a pill needs a radius, not a round-off")
    border = re.search(r"(?:^|;)\s*border\s*:\s*([^;]+)", body)
    assert border and re.search(r"\d+px\s+solid\s+transparent", border.group(1)), (
        f"the thumb has no transparent inset ({border and border.group(1).strip()!r}), "
        "so it fills the groove edge to edge instead of sitting inside it")
    assert re.search(r"background-clip\s*:\s*padding-box", body), (
        "the inset has no `background-clip: padding-box`, so the border paints over "
        "the handle and the inset costs the pill its shape")


def test_every_state_exists_in_both_themes():
    """thumb / hover / drag — a bar that does not answer the pointer is a stripe."""
    for theme_key in ("light", "dark"):
        for pseudo in ("thumb", "thumb:hover", "thumb:active"):
            body = state(theme_key, pseudo)
            assert body["body"] is not None, (
                f"{theme_key}: no ::-webkit-scrollbar-{pseudo} rule")
            assert body["colour"], (
                f"{theme_key}: ::-webkit-scrollbar-{pseudo} resolves to no colour")


def test_the_dark_scale_stays_inside_the_screen_block():
    """Print keeps the light palette; a dark scrollbar token outside
    `@media screen` is the same leak test_dark_theme_contrast.py guards for
    cards."""
    assert re.search(r"\.dark\s*\{[^}]*--sb-thumb", PLAIN.split("@media screen")[0]) is None, (
        "a dark scrollbar token sits outside `@media screen` and would be printed")


# ── and it can be seen ───────────────────────────────────────────────────────

def test_the_thumb_reads_against_its_groove_in_both_themes():
    """WCAG 1.4.11, the bar `--input-border` is already held to: the thumb is
    the boundary that says how far the page goes."""
    for theme_key, page in PAGE.items():
        track = state(theme_key, "track")["colour"]
        assert re.match(r"^#[0-9a-f]{6}$", track or ""), (
            f"{theme_key}: the track ({track}) is not a measurable colour")
        for pseudo in ("thumb", "thumb:hover", "thumb:active"):
            body = state(theme_key, pseudo)
            assert re.match(r"^#[0-9a-f]{6}$", body["colour"] or ""), (
                f"{theme_key}: {body['selector']} has no plain colour "
                f"({body['colour']}) — an alpha or a gradient cannot be measured")
            ratio = theme.contrast(body["colour"], track)
            assert ratio >= 3.0, (
                f"{theme_key}: {body['selector']} is {ratio:.2f}:1 on its own "
                f"groove ({body['colour']} on {track}) — a scrollbar you cannot "
                "see is a scrollbar you cannot grab")
            on_page = theme.contrast(body["colour"], page)
            assert on_page >= 3.0, (
                f"{theme_key}: {body['selector']} is {on_page:.2f}:1 against the "
                f"page ({body['colour']} on {page})")


def test_the_dark_thumb_is_darker_than_the_light_one():
    """The same guard test_dark_theme_contrast.py applies to the tokens: paste
    the light values into `.dark` and every contrast still passes while every
    screenshot is wrong."""
    light = state("light", "thumb")["colour"]
    dark = state("dark", "thumb")["colour"]
    assert theme.luminance(dark) < theme.luminance(light), (
        f"the dark thumb ({dark}) is not darker than the light one ({light})")


# ── Firefox, without handing Chrome to the platform ──────────────────────────

def test_firefox_gets_the_standard_properties_for_both_themes():
    """The pair reads the same tokens, so the two engines cannot drift apart: a
    pane Firefox paints and a pane Chrome paints are the same bar.

    Asked of **each** gated rule, not of the gate's text as a whole: with two
    blocks inside it — `<html>` and `.dark` — a search over the concatenation is
    satisfied by whichever one still reads the scale, so one theme could be handed
    literals with this test green.
    """
    rules_in_gate = [m for body in (b for _, b in _supports_bodies(PLAIN, "-moz-appearance: none"))
                     for m in re.finditer(r"([^{}]+)\{([^}]*)\}", body)]
    assert rules_in_gate, "the standard pair is gone from the Firefox gate"

    named = set()
    for match in rules_in_gate:
        selector, decls = match.group(1).strip(), match.group(2).strip()
        if "scrollbar-color" not in decls:
            continue
        named.add(selector)
        assert re.search(r"scrollbar-color\s*:\s*var\(--sb-thumb\)\s+var\(--sb-track\)",
                         decls), (
            f"{selector} takes its pair from somewhere other than the scale "
            f"({decls!r}), so that theme's Firefox bar is not the measured one")
    assert named == {"html", ".dark"}, (
        f"the gate paints {sorted(named) or 'nothing'}; both carriers need the pair, "
        "or one theme falls back to the platform's grey bar")
    assert re.search(r"scrollbar-width\s*:\s*auto",
                     "".join(m.group(2) for m in rules_in_gate)), (
        "`scrollbar-color` without `scrollbar-width` leaves the width to chance")


def test_the_standard_properties_are_gated_to_firefox():
    """Chrome 121+ honours `scrollbar-color` *and* stops honouring
    `::-webkit-scrollbar` when it is set, so an ungated pair would silently
    replace the measured bar with the platform's own."""
    unsupported = PLAIN
    for _, body in _supports_bodies(PLAIN, "-moz-appearance: none"):
        unsupported = unsupported.replace(body, "", 1)
    assert not re.search(r"scrollbar-color\s*:", unsupported), (
        "`scrollbar-color` is declared outside the Firefox gate — Chrome "
        "121+ will drop `::-webkit-scrollbar` and use the platform default "
        "width instead of the measured one")
