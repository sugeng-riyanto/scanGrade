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
* **3:1 against its own track, in both themes** — the WCAG 1.4.11 bar
  `--input-border` already answers to in test_dark_theme_contrast.py, and for
  the same reason: the thumb is the boundary that says how far the page goes.
* **A finger gets more than a mouse** (`pointer: coarse`), the rule `.tap-44`
  follows. The ordering matters as much as the number: a `pointer: coarse`
  block of equal specificity placed *above* the base rule loses to it, silently,
  and a phone then keeps the narrow bar while the CSS reads as if it did not.
* **Every state is declared** — thumb, hover and drag — so the bar answers the
  pointer instead of being a static stripe.
* **Firefox as well as Chrome/Safari** — and the standard properties are gated
  to Firefox on purpose. A browser that honours `scrollbar-color` stops
  honouring `::-webkit-scrollbar` (Chrome 121+), so an ungated pair would drop
  Chrome to the platform's own width and quietly undo the sizing below. The
  gate is the point, not an accident: what Chrome draws is what these numbers
  say, and what Firefox draws is its native bar in our colours.
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
#: real background rather than of the track alone.
PAGE = {"light": "#f1f5f9", "dark": "#0f1117"}

#: The bar's own floor, and what a finger gets. 10px is already twice the 5px the
#: page shipped with; 14px is the small end of what a thumb can hold.
MIN_BAR = 10
MIN_TOUCH_BAR = 14


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
    the dark rules are written as a list — `.dark::-webkit-scrollbar-thumb, .dark
    ::-webkit-scrollbar-thumb` — so that the page's own scrollbar and an inner one
    are painted by the same declaration. A `re.escape(selector) + '{'` reader
    reports those rules as missing.
    """
    for match in re.finditer(r"([^{}]*?)\{([^}]*)\}", css):
        parts = [p.strip() for p in match.group(1).split(",")]
        if selector in parts:
            return match.group(2)
    return None


def colour_of(body: str | None) -> str | None:
    """The first background colour in a rule body, lowercased."""
    if not body:
        return None
    match = re.search(r"background(?:-color)?\s*:\s*([^;]+)", body)
    return match.group(1).strip().lower() if match else None


def width_of(body: str | None) -> int | None:
    if not body:
        return None
    match = re.search(r"width\s*:\s*(\d+)\s*px", body)
    return int(match.group(1)) if match else None


SCREEN_OFFSET, SCREEN_BODY = _bodies(PLAIN, "screen")[0]
#: The light rules are everything the dark block does not own.
LIGHT_ONLY = PLAIN.replace(SCREEN_BODY, "", 1)


def state(theme_key: str, pseudo: str) -> dict:
    """A scrollbar state as the stylesheet declares it for one theme."""
    scope = LIGHT_ONLY if theme_key == "light" else SCREEN_BODY
    prefix = "" if theme_key == "light" else ".dark "
    body = declarations_in(scope, prefix + "::-webkit-scrollbar-" + pseudo)
    return {"selector": prefix + "::-webkit-scrollbar-" + pseudo,
            "body": body, "colour": colour_of(body), "width": width_of(body)}


# ── the bar is big enough to hit ─────────────────────────────────────────────

def test_the_bar_is_wider_than_the_stripe_it_replaced():
    """5px was the old value; anything at or under it is a regression."""
    body = declarations_in(LIGHT_ONLY, "::-webkit-scrollbar")
    assert body is not None, "theme.css no longer styles ::-webkit-scrollbar"
    width = width_of(body)
    assert width is not None and width >= MIN_BAR, (
        f"the scrollbar is {width}px wide; the page shipped a 5px stripe and "
        f"this file holds the bar to {MIN_BAR}px or more")


def test_a_finger_gets_a_bigger_bar_than_a_mouse():
    """`pointer: coarse`, the same question `.tap-44` asks — and the media block
    must come *after* the base rule, or it loses to it at equal specificity."""
    base_at = PLAIN.find("::-webkit-scrollbar")
    assert base_at != -1, "no ::-webkit-scrollbar rule at all"

    coarse = [(offset, width_of(declarations_in(body, "::-webkit-scrollbar")))
              for offset, body in _bodies(PLAIN, "pointer: coarse")
              if declarations_in(body, "::-webkit-scrollbar") is not None]
    assert coarse, (
        "no `@media (pointer: coarse)` rule resizes the scrollbar, so a phone "
        "gets the mouse-sized bar")
    for offset, width in coarse:
        assert width is not None and width >= MIN_TOUCH_BAR, (
            f"the touch scrollbar is {width}px; a finger needs {MIN_TOUCH_BAR}px")
        assert offset > base_at, (
            "the touch rule sits above the base ::-webkit-scrollbar rule and "
            "loses to it — the phone keeps the narrow bar and the CSS reads as "
            "if it did not")
    assert all(width > width_of(declarations_in(LIGHT_ONLY, "::-webkit-scrollbar"))
               for _, width in coarse), "the touch bar is not wider than the mouse bar"


# ── both themes draw a groove and a grab handle ──────────────────────────────

def test_each_theme_declares_a_track_that_is_not_transparent():
    """The transparent track was one half of "nothing to aim at"."""
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


def test_the_bar_is_drawn_as_a_pill_not_a_block():
    """`border-radius` plus an inset is what makes it read as a handle."""
    body = state("light", "thumb")["body"] or ""
    assert re.search(r"border-radius\s*:", body), (
        "the thumb has no border-radius, so it is a rectangle again")


def test_every_state_exists_in_both_themes():
    """thumb / hover / drag — a bar that does not answer the pointer is a stripe."""
    for theme_key in ("light", "dark"):
        for pseudo in ("thumb", "thumb:hover", "thumb:active"):
            assert state(theme_key, pseudo)["body"] is not None, (
                f"{theme_key}: no ::-webkit-scrollbar-{pseudo} rule")


def test_the_dark_rules_reach_the_page_bar_and_not_only_its_children():
    """`.dark ::-webkit-scrollbar-thumb` is a *descendant* selector, so it paints the
    scrollbars of elements **inside** the dark page and never the scrollbar of the
    element that carries `.dark` — which is `<html>`, and the viewport bar is the
    one the reader drags. Measured in a live browser before this guard existed,
    with `.dark` on the document: an inner scroller came out #5f6e91 on #1a1f31
    while the page bar stayed the light #6f819c on an #e8edf6 groove."""
    for pseudo in ("track", "thumb", "thumb:hover", "thumb:active", "corner"):
        document = declarations_in(SCREEN_BODY, ".dark::-webkit-scrollbar-" + pseudo)
        assert document is not None, (
            f"no `.dark::-webkit-scrollbar-{pseudo}` rule: `.dark ` with a space "
            "cannot paint the scrollbar of the element that carries `.dark`, so "
            "the page bar keeps the light palette in dark mode")
        inner = declarations_in(SCREEN_BODY, ".dark ::-webkit-scrollbar-" + pseudo)
        assert colour_of(document) == colour_of(inner), (
            "the page scrollbar and an inner one disagree in dark mode: "
            f"{colour_of(document)} vs {colour_of(inner)}")


def test_the_dark_bar_lives_inside_the_screen_block():
    """Print keeps the light palette; a dark scrollbar rule outside `@media
    screen` is the same leak test_dark_theme_contrast.py guards for cards."""
    outside = LIGHT_ONLY
    assert ".dark ::-webkit-scrollbar" not in outside, (
        "a dark scrollbar rule sits outside `@media screen` and would therefore "
        "be printed")


# ── and it can be seen ───────────────────────────────────────────────────────

def test_the_thumb_reads_against_its_track_in_both_themes():
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
                f"track ({body['colour']} on {track}) — a scrollbar you cannot "
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
    gated = "".join(body for _, body in _supports_bodies(PLAIN, "-moz-appearance: none"))
    assert re.search(r"scrollbar-color\s*:\s*#[0-9a-f]{6}", gated), (
        "no `scrollbar-color` inside the Firefox gate, so Firefox keeps its "
        "default grey bar")
    assert re.search(r"scrollbar-width\s*:", gated), (
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
