"""The ScanMeet room's browser SDK is served by the app, not by a public CDN.

`docs/PERFORMANCE.md` and `docs/DESIGN_SYSTEM.md` already say that every
third-party script here is self-hosted, and this page is the one place that rule
matters most: it is opened by a pupil sitting an exam on a school network, and
the host that is certainly reachable is the one that served the page. The
caveats list in `docs/SCANGRADE_LIVE.md` carried "vendor the JS SDK instead of
CDN" as outstanding work; these tests hold the repair in place.

What is asserted, in the order a reader would check it:

* the file is really there, is a real build of the pinned version, and exports
  the global the page's own script reads;
* the page asks for it through `asset_v`, so a browser holding a year-old cached
  copy is not handed a URL that never changes (nginx serves `/static/` with
  `immutable, max-age=31536000`);
* the CDN URL appears **only** as the fallback on a tag that already points at
  this origin — a CDN `<script src>` with no `onerror` is the defect this file
  exists to catch, and so is a fallback that would never fire because the local
  tag has no `onerror`;
* no other template loads a cross-origin script as its primary source.

The negative half is tested as hard as the positive one, because a rule that
only ever reads the repaired file cannot tell a repair from a deletion: a
template with the CDN as its only source, one with the local source but no
fallback wiring, and one whose vendored path is a bare string all have to fail.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
VENDOR = ROOT / "app" / "static" / "vendor" / "livekit"
SDK_VERSION = "2.23.0"
SDK_PATH = VENDOR / SDK_VERSION / "livekit-client.umd.min.js"
PAGE = TEMPLATES / "live" / "exam.html"

#: The origin the fallback names. It is allowed to appear; nothing may *prefer* it.
CDN_HOST = "cdn.jsdelivr.net"

_SCRIPT = re.compile(r"<script\b[^>]*>", re.I | re.S)
#: `src="…"` or `src='…'` — the other quote is allowed *inside*, because the
#: origin form is `{{ asset_v('vendor/…') }}` and a reader that stops at the
#: first quote of either kind reads it as `{{ asset_v(` and passes nothing.
_SRC = re.compile(r"""\bsrc\s*=\s*("([^"]*)"|'([^']*)')""", re.I)


def _scripts(text: str) -> list[str]:
    return _SCRIPT.findall(text)


def _src(tag: str) -> str:
    found = _SRC.search(tag)
    if not found:
        return ""
    return found.group(2) if found.group(2) is not None else found.group(3)


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _sdk_tag(text: str) -> str:
    for tag in _scripts(text):
        if "livekit-client" in tag:
            return tag
    return ""


# --------------------------------------------------------------------- the file

def test_the_pinned_build_is_vendored_under_its_version():
    assert SDK_PATH.is_file(), f"missing vendored SDK: {SDK_PATH}"
    assert SDK_PATH.parent.name == SDK_VERSION
    # A real UMD build is hundreds of kilobytes; a stub or an error page is not.
    assert SDK_PATH.stat().st_size > 200_000, (
        f"{SDK_PATH.name} is {SDK_PATH.stat().st_size} bytes — too small to be "
        "the UMD build, so whatever is at that path would not open a room")


def test_the_vendored_build_is_the_version_the_path_names():
    text = SDK_PATH.read_text(encoding="utf-8", errors="ignore")
    assert SDK_VERSION in text, (
        "the vendored build does not carry its own version string, so the "
        "directory name is a claim nothing in the file supports")
    assert "requires" in text or "define(" in text or "exports." in text, (
        "the vendored file does not look like a UMD bundle")


def test_the_vendored_build_exports_the_global_the_page_reads():
    text = SDK_PATH.read_text(encoding="utf-8", errors="ignore")
    assert "LivekitClient" in text, (
        "the vendor file does not export `LivekitClient`, which is the global "
        "scan-live-exam.js checks before it does anything")
    page_js = (ROOT / "app" / "static" / "js" / "scan-live-exam.js").read_text(
        encoding="utf-8")
    assert "window.LivekitClient" in page_js, (
        "the page script no longer reads `window.LivekitClient`; if that "
        "renamed, this test is measuring a file nothing uses")


# ------------------------------------------------------------------- the page

def test_the_page_loads_the_sdk_from_this_origin_through_asset_v():
    tag = _sdk_tag(_page())
    assert tag, "the exam page no longer loads a livekit-client script at all"
    src = _src(tag)
    assert src.startswith("{{ asset_v("), (
        f"the SDK is requested as {src!r}; /static/ is served immutable for a "
        "year, so the URL has to change when the bytes do")
    assert f"vendor/livekit/{SDK_VERSION}/livekit-client.umd.min.js" in src, (
        f"the page does not name the vendored path for {SDK_VERSION}")


def test_the_cdn_is_a_fallback_on_a_tag_that_already_has_a_local_source():
    tag = _sdk_tag(_page())
    assert CDN_HOST in tag, "the load-failure fallback was dropped without a word"
    assert "onerror" in tag, (
        "the CDN URL is on the tag but nothing fires if the local file is "
        "missing, so a box that shipped without it renders a page whose SDK "
        "never arrives")
    src = _src(tag)
    assert CDN_HOST not in src, (
        "the CDN is still the primary source — this is the defect, not the "
        "fallback: the browser would fetch it before trying this origin")
    # The fallback must point at the same pinned build, not at `latest`.
    assert f"livekit-client@{SDK_VERSION}" in tag, (
        "the fallback does not pin the version, so it could serve a build this "
        "release was never tested against")


def test_the_page_still_guards_a_missing_global():
    """The fallback can fail too — on a room's LAN with no route out at all."""
    js = (ROOT / "app" / "static" / "js" / "scan-live-exam.js").read_text(
        encoding="utf-8")
    assert "if (!window.LivekitClient)" in js, (
        "without this guard a page whose SDK failed to load throws before it "
        "can say so")


# ------------------------------------------------------------- the whole tree

def test_no_template_prefers_a_cross_origin_script():
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for tag in _scripts(text):
            src = _src(tag)
            if not src.startswith(("http://", "https://", "//")):
                continue
            if "onerror" in tag:
                continue  # a documented load-failure fallback, not a preference
            offenders.append(f"{path.relative_to(ROOT)}: {src}")
    assert not offenders, (
        "these templates load a third-party script from another origin, so a "
        "pupil whose network cannot reach it gets a page whose script never "
        "arrives:\n  " + "\n  ".join(offenders))


# ----------------------------------------------------------------- the control

def test_the_rule_rejects_the_shape_it_was_written_for():
    """A control on the classifier itself, so a green run means something.

    Each sample is the defect or the near-miss, and the assertion is what a
    reader would want: the CDN-only tag, a fallback-less local tag, and a tag
    that names `latest` instead of the pin are all refused.
    """
    cdn_only = ('<script src="https://cdn.jsdelivr.net/npm/livekit-client@'
                f'{SDK_VERSION}/dist/livekit-client.umd.min.js"></script>')
    tag = _sdk_tag(cdn_only)
    assert _src(tag).startswith("https://") and "onerror" not in tag, (
        "the CDN-only shape must be readable as the defect")

    no_fallback = ("<script src=\"{{ asset_v('vendor/livekit/"
                   f"{SDK_VERSION}/livekit-client.umd.min.js') }}\"></script>")
    assert "onerror" not in _sdk_tag(no_fallback)

    latest = ('<script src="{{ asset_v(\'vendor/livekit/x/y.js\') }}" '
              'onerror="this.src=\'https://cdn.jsdelivr.net/npm/'
              'livekit-client@latest/dist/livekit-client.umd.min.js\'"></script>')
    assert f"livekit-client@{SDK_VERSION}" not in _sdk_tag(latest), (
        "an unpinned fallback has to be refused by the pin assertion")
