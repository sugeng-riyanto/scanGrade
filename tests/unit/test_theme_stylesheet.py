"""The app's own CSS is a file, and base.html links it — measurably.

base.html used to carry a 36 KB `<style>` block: the theme tokens, the dark-mode
remap over the Tailwind utilities, the legibility floor and the light-mode
contrast corrections. Every page therefore carried all of it *inside its HTML*.

That is invisible on a laptop on WiFi and expensive everywhere else:

* it cannot be cached — the browser re-downloads it with the page,
* nginx re-gzips it on **every** request, for a body a phone already has,
* and it is the difference between a 79 KB and a 43 KB student dashboard on a
  connection with two bars of signal.

It is `app/static/css/theme.css` now. Nothing about the CSS changed, which is
exactly why this file exists: the move is the kind that quietly reverts. Someone
adds a rule and pastes it back into base.html, where it works, is reviewed as a
one-line diff, and re-inflates every page in the app. The rules below fail on
that, and on the two other silent ways the move can break:

* **Order.** theme.css overrides Tailwind utility classes. Link it *before*
  tailwind.css and the dark-mode remap stops winning the cascade — in dark mode
  only, on the pages nobody in this repo looks at.
* **Versioning.** nginx serves /static/ as `immutable, max-age=31536000`. An
  unversioned stylesheet is therefore frozen in a browser for a year, so the fix
  ships to new devices only. `asset_v()` is what makes that cache safe.
"""
import re
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "app" / "templates" / "base.html"
THEME_CSS = ROOT / "app" / "static" / "css" / "theme.css"
TEMPLATES = sorted((ROOT / "app" / "templates").rglob("*.html"))
SOURCE = BASE.read_text(encoding="utf-8")
CSS = THEME_CSS.read_text(encoding="utf-8")

# Stylesheet links base.html asks for, in document order. Parsed as tags, not
# lines, so a tag split across lines is read the same way the browser reads it.
TAG = re.compile(r"<link\b[^>]*>", re.I | re.S)
HREF = re.compile(r"\bhref\s*=\s*\"([^\"]*)\"", re.I)
REL = re.compile(r"\brel\s*=\s*\"([^\"]*)\"", re.I)


def stylesheets() -> list[str]:
    urls = []
    for tag in TAG.finditer(SOURCE):
        rel, href = REL.search(tag.group(0)), HREF.search(tag.group(0))
        if rel and href and "stylesheet" in rel.group(1).lower():
            urls.append(href.group(1).strip())
    return urls


STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
TOKEN_DECLARED = re.compile(r"--(?:bg-body|bg-card|text-dim|input-border)\s*:")


# ── the block is out of the template ─────────────────────────────────────────

def test_base_html_carries_no_inline_stylesheet():
    """The whole reason for the move. An inline block cannot be cached, and the
    page pays for it on every navigation."""
    inline = STYLE_BLOCK.findall(SOURCE)
    assert not inline, (
        "base.html has an inline <style> block again. Put the rules in "
        "app/static/css/theme.css and link it: an inline block is re-sent "
        "inside every page and cannot be cached.\n  "
        + "\n  ".join(block.strip()[:120] for block in inline))


def test_no_template_redeclares_the_theme_tokens():
    """A page that pastes the token block back into itself renders in its own
    palette and drifts from the values measured in theme.css.

    Only token *declarations* (`--bg-body:`) count. base.html names `--text-dim`
    in the script that hands the palette to Chart.js, and that is the point of
    the token — reading it, not restating it.
    """
    offenders = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for block in STYLE_BLOCK.findall(text):
            if TOKEN_DECLARED.search(block):
                offenders.append(path.relative_to(ROOT).as_posix())
                break
    assert not offenders, (
        "these templates declare the theme tokens themselves; the palette lives "
        "once, in app/static/css/theme.css:\n  " + "\n  ".join(offenders))


# ── and it is linked, in the one order that works ────────────────────────────

def test_base_html_links_the_stylesheet():
    urls = stylesheets()
    assert any("theme.css" in url for url in urls), (
        "base.html does not link css/theme.css. The tokens, the dark-mode remap "
        f"and the legibility floor are in {THEME_CSS.relative_to(ROOT)} — without "
        "the link a page renders unstyled, in both themes.\n  links: " + repr(urls))


def test_the_link_comes_after_tailwind():
    """theme.css overrides Tailwind utilities (that is how the dark remap and the
    contrast corrections work). Loaded first, every one of those rules loses to
    the utility it was written to override — with no error, and only in dark
    mode, which is the theme this repo tests last."""
    urls = stylesheets()
    order = [next((i for i, u in enumerate(urls) if name in u), None)
             for name in ("tailwind.css", "theme.css")]
    assert None not in order, f"both stylesheets must be linked; got {urls!r}"
    assert order[0] < order[1], (
        f"theme.css is linked before tailwind.css ({urls!r}). It overrides "
        "utilities, so it has to come second — the reversed order fails "
        "silently, in dark mode only.")


def test_the_theme_stylesheet_is_plain_css():
    """It is a static file, not a template. A Jinja tag in it would either be
    served literally (bleeding `{{ }}` into the browser) or be dropped by the
    static handler, depending on where it landed — and both look like CSS."""
    tags = re.findall(r"\{\{.*?\}\}|\{%.*?%\}", CSS, re.S)
    assert not tags, (
        f"{THEME_CSS.relative_to(ROOT)} contains Jinja: {tags[:3]!r}. It is "
        "served as a static file, which does not render template syntax.")


def test_the_stylesheet_still_holds_the_theme():
    """Guards the move from the other side: an empty or truncated file would
    satisfy every check above."""
    for marker in (":root", ".dark", "--bg-body", "--text-dim"):
        assert marker in CSS, (
            f"{marker} is not in {THEME_CSS.relative_to(ROOT)} — the extraction "
            "either lost part of the stylesheet or the file is not the one "
            "base.html links")


# ── the cache-busting that makes a one-year cache safe ───────────────────────

def test_the_stylesheets_are_requested_through_asset_v():
    """nginx serves /static/ with `immutable, max-age=31536000`. A stylesheet
    linked by a bare path is then frozen in a browser for a year: the next fix
    reaches only devices that had never loaded it, and no error says so."""
    urls = stylesheets()
    for name in ("css/tailwind.css", "css/theme.css"):
        hit = next((u for u in urls if name in u), None)
        assert hit, f"base.html does not link {name}"
        assert "asset_v(" in hit, (
            f"base.html links {hit!r} by hardcoded path. Ask for it through "
            "asset_v() so the URL changes when the bytes do — /static/ is "
            "served immutable for a year.")


def test_asset_v_changes_with_the_bytes_and_not_without_them(tmp_path):
    """The property the whole scheme rests on: same bytes, same URL (so the
    cache is used); different bytes, different URL (so the cache is escaped)."""
    from app.utils.asset_version import asset_v

    (tmp_path / "css").mkdir()
    sheet = tmp_path / "css" / "x.css"
    app = Flask(__name__, static_folder=str(tmp_path))

    sheet.write_text("a {}", encoding="utf-8")
    with app.app_context():
        before = asset_v("css/x.css")
        assert asset_v("css/x.css") == before, "an unchanged file must keep its URL"

    sheet.write_text("body { color: red; }", encoding="utf-8")
    with app.app_context():
        after = asset_v("css/x.css")
    assert before != after, "an edited stylesheet must get a new URL"


def test_asset_v_falls_back_instead_of_raising(tmp_path):
    """A missing stylesheet has to be a 404 in the network tab, not a 500 on the
    page: a broken palette is a bug to fix, an unrenderable page is an outage."""
    from app.utils.asset_version import asset_v

    app = Flask(__name__, static_folder=str(tmp_path))
    with app.app_context():
        assert asset_v("css/nope.css") == "/static/css/nope.css"
