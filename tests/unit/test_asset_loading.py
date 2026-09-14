"""A library loaded twice silently resets its globals.

base.html loads Tailwind, Alpine, htmx, Chart.js, Inter and Font Awesome for every
page. When a page template loads one of them *again*, the second copy replaces the
global the first one set up — and nothing errors. Both real cases found so far
looked like something else:

* ``teacher/analytics.html`` loaded Chart.js a second time from a CDN. It replaced
  ``window.Chart`` and discarded the dark-mode label colours base.html had set, so
  the charts silently fell back to ``#666`` on a dark card (2.94:1).
* ``shared/comms.html`` did the same thing, for the same reason.

Both were fixed; this file is the general rule those two were breaking, so the
next page cannot repeat it. The check parses rather than greps because a tag split
across lines is invisible to a line-oriented search — which is exactly how the
duplicate in ``analytics.html`` was missed the first time.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
BASE = TEMPLATES / "base.html"

TAG = re.compile(r"<(script|link)\b([^>]*)>", re.I | re.S)
SRC = re.compile(r'\bsrc\s*=\s*"([^"]*)"', re.I)
HREF = re.compile(r'\bhref\s*=\s*"([^"]*)"', re.I)
REL = re.compile(r'\brel\s*=\s*"([^"]*)"', re.I)
EXTENDS = re.compile(r"\{%-?\s*extends\s+[\"']([^\"']+)[\"']")

# The library a remote URL belongs to, by the substring that identifies it. Used
# only against remote URLs — a local filename is free to contain "chart".
LIBRARY_HINTS = {
    "Alpine.js": "alpine",
    "htmx": "htmx",
    "Chart.js": "chart",
    "Tailwind": "tailwind",
    "Font Awesome": "font-awesome",
    "Font Awesome": "fontawesome",
    "Inter": "inter.",
}


def _assets(text: str) -> list[tuple[str, int, str]]:
    """(kind, line, url) for every script src and stylesheet link in ``text``."""
    out = []
    for m in TAG.finditer(text):
        kind, attrs = m.group(1).lower(), m.group(2)
        url = None
        if kind == "script":
            hit = SRC.search(attrs)
            url = hit.group(1) if hit else None
        else:
            rel, href = REL.search(attrs), HREF.search(attrs)
            if href and rel and "stylesheet" in rel.group(1).lower():
                url = href.group(1)
        if url:
            line = text.count("\n", 0, m.start()) + 1
            out.append((kind, line, url.strip()))
    return out


def _pages() -> list[Path]:
    return sorted(p for p in TEMPLATES.rglob("*.html"))


def _base_urls() -> set[str]:
    return {url for _, _, url in _assets(BASE.read_text(encoding="utf-8"))}


def _extends_base(text: str) -> bool:
    m = EXTENDS.search(text)
    return bool(m) and m.group(1).endswith("base.html")


def test_the_audit_sees_a_tag_split_across_lines():
    """The reason this file parses instead of grepping. A line-oriented search
    reads the tag below as two unrelated fragments and reports nothing."""
    sample = '<script\n        src="/static/vendor/chart.umd.min.js"></script>'
    assert _assets(sample) == [("script", 1, "/static/vendor/chart.umd.min.js")]
    assert [u for _, _, u in _assets('<link rel="stylesheet" href="/a.css">')] == ["/a.css"]
    # An anchor is not an asset, and a non-stylesheet link is not either.
    assert _assets('<a href="/privacy">x</a>') == []
    assert _assets('<link rel="icon" href="/static/icon.svg">') == []


def test_no_page_reloads_a_library_base_html_already_provides():
    base = _base_urls()
    assert base, "base.html must load the shared libraries"

    offenders = []
    for path in _pages():
        if path == BASE:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _extends_base(text):
            continue
        for kind, line, url in _assets(text):
            if url in base:
                offenders.append(f"{path.relative_to(ROOT)}:{line}  <{kind}> {url}")

    assert not offenders, (
        "these templates extend base.html, which already loads the asset, so the "
        "second copy replaces the global base.html set up. Remove the tag — the "
        "inherited copy is enough:\n  " + "\n  ".join(offenders))


def test_no_library_is_loaded_from_a_remote_host():
    """Offline-first: the school WiFi drops, and a CDN URL is a blank page or a
    dead chart. It is also how the duplicate above got in — the CDN copy was
    easier to paste than the vendored one."""
    offenders = []
    for path in _pages():
        for kind, line, url in _assets(path.read_text(encoding="utf-8", errors="replace")):
            if not url.lower().startswith(("http://", "https://")):
                continue
            for library, hint in LIBRARY_HINTS.items():
                if hint in url.lower():
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{line}  {library} from {url}")
                    break

    assert not offenders, (
        "these pages fetch a library over the network; every one of them is "
        "vendored under /static/vendor/ and must be loaded from there:\n  "
        + "\n  ".join(offenders))


def test_no_template_loads_the_same_asset_twice():
    """Within one template, `{% include %}` can make a partial appear several
    times. A <script> inside such a partial would run once per inclusion."""
    offenders = []
    for path in _pages():
        counts: dict[str, int] = {}
        for _, _, url in _assets(path.read_text(encoding="utf-8", errors="replace")):
            counts[url] = counts.get(url, 0) + 1
        for url, n in counts.items():
            if n > 1:
                offenders.append(f"{path.relative_to(ROOT)}: {url} x{n}")

    assert not offenders, (
        "one template loads the same asset more than once:\n  " + "\n  ".join(offenders))


def test_partials_bring_no_assets_of_their_own():
    """A partial is included by a page that already extends base.html. If it
    carried a <script>, every inclusion would re-run it — and wb_toolbar.html is
    included four times by its page."""
    offenders = []
    for path in _pages():
        text = path.read_text(encoding="utf-8", errors="replace")
        if _extends_base(text) or "<html" in text.lower():
            continue
        for kind, line, url in _assets(text):
            offenders.append(f"{path.relative_to(ROOT)}:{line}  <{kind}> {url}")

    assert not offenders, (
        "these are partials, so their assets belong to the page that includes "
        "them:\n  " + "\n  ".join(offenders))


def test_a_standalone_page_loads_what_it_uses():
    """The inverse failure: a page with no `extends` inherits nothing, so a
    directive it uses with nothing to interpret it is simply inert markup.

    Two conditions, not one. Whether a page renders its own document is decided
    by looking for the tag in the file's text, and text includes prose: a comment
    explaining which element carries `lang` made `landing.html` — which extends
    base.html and inherits Alpine from it — read as standalone. A page that
    extends base.html cannot be one, whatever it says about itself, so it is
    skipped before the text is examined. That also makes the failure message
    below true: it claims these pages do not extend base.html.
    """
    offenders = []
    for path in _pages():
        text = path.read_text(encoding="utf-8", errors="replace")
        if path == BASE or _extends_base(text) or "<html" not in text.lower():
            continue
        urls = " ".join(u for _, _, u in _assets(text)).lower()
        if re.search(r"\bx-data=|\bx-init=|@click=", text) and "alpine" not in urls:
            offenders.append(f"{path.relative_to(ROOT)} uses Alpine but never loads it")
        if re.search(r"\bhx-(get|post|put|delete)=", text) and "htmx" not in urls:
            offenders.append(f"{path.relative_to(ROOT)} uses htmx but never loads it")

    assert not offenders, (
        "these pages do not extend base.html, so nothing else loads this for "
        "them:\n  " + "\n  ".join(offenders))


INJECTS_SCRIPT = re.compile(r"createElement\(\s*['\"]script['\"]\s*\)")

# Runtime-injected scripts are invisible to every check above, because a static
# scan cannot see a URL assembled in JavaScript — and the one site that exists
# builds it from a Jinja variable. It is Midtrans Snap.js, which used to be
# loaded from the provider and is now served from `app/static/vendor/midtrans/`
# with the CDN only as a fallback (see tests/unit/test_snap_vendored.py), so the
# old justification — "a payment SDK that must come from the provider" — no
# longer applies and a new site must not borrow it.
#
# The rule stands anyway, for a different reason: the URL is still assembled in
# JavaScript, so nothing here can see it. Pinning the list means the next one
# has to be argued for rather than slipping in beside it.
KNOWN_RUNTIME_SCRIPTS = {
    "admin_sekolah/payment.html",  # Midtrans Snap.js, local-first with CDN fallback
}


def test_runtime_script_injection_is_a_short_known_list():
    found = set()
    for path in _pages():
        text = path.read_text(encoding="utf-8", errors="replace")
        if INJECTS_SCRIPT.search(text) or re.search(r"\bdocument\.write\(", text):
            found.add(str(path.relative_to(TEMPLATES)).replace("\\", "/"))

    new = found - KNOWN_RUNTIME_SCRIPTS
    assert not new, (
        "these templates build a <script> at runtime, where the static checks in "
        "this file cannot see whether it duplicates a library base.html already "
        "loads. Prefer vendoring the file under app/static/vendor/ (that is what "
        "Snap.js does); if it genuinely must be fetched at runtime, add it to "
        "KNOWN_RUNTIME_SCRIPTS with the reason:\n  " + "\n  ".join(sorted(new)))


def test_the_standalone_exemption_is_real():
    """monitor.html loads Chart.js on its own, which the rule above has to allow —
    it does not extend base.html. If it ever did, this file would be letting a
    real duplicate through."""
    text = (TEMPLATES / "monitor.html").read_text(encoding="utf-8")
    assert "<html" in text.lower()
    assert not _extends_base(text)
    assert "chart.umd.min.js" in text
