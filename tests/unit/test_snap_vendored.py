"""Snap.js is served from our own origin, with the CDN as a fallback.

Why this needs a test rather than a comment.

The payment page is the one page where a blocked third-party request means the
school cannot pay at all — and the schools using this sit behind connections
that are slow at best and filtered at worst, which is the same reason Alpine,
Chart.js, htmx and the Inter font are already vendored in `app/static/vendor/`.

Vendoring Snap.js is not just "download the file and point at it", because the
SDK finds its own `<script>` tag by matching that tag's URL:

    function c(t){return (t.indexOf(i)>-1 || t.indexOf("veritrans.co.id")>-1)
                       && (t.indexOf("snap.js")>-1 || t.indexOf("snap.min.js")>-1)}

where `i` is the API host taken from the build (`https://app.midtrans.com` for
production, `.../app.sandbox.midtrans.com` for sandbox). It then reads
`data-client-key` off the tag it found, and that key is what goes into the
payment iframe's URL as `client_key=`.

The API host carries a scheme, so it can never appear in a same-origin path —
which leaves the bare `veritrans.co.id` substring as the only thing that makes a
self-hosted copy recognisable. That is why the directory is named after it.

Measured in a browser, same file, only the path differing:

    /static/vendor/midtrans/sandbox/veritrans.co.id/snap.js
        iframe → .../v4/popup?origin_host=http://127.0.0.1:5000&client_key=PROBE-CLIENT-KEY#/
    /static/tmp_no_marker/snap.js
        iframe → .../v4/popup?origin_host=http://127.0.0.1:5000#/

No error is raised in the second case. The SDK loads, `snap.embed()` resolves,
and the iframe simply comes up as an unidentified merchant. A rename would be
caught by nothing — hence these assertions.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
VENDOR = ROOT / "app" / "static" / "vendor" / "midtrans"
PAYMENT = TEMPLATES / "admin_sekolah" / "payment.html"

# The two builds differ ONLY in the host they hardcode, so they are not
# interchangeable: shipping the sandbox build to production would send live
# payments to the sandbox (where they silently do not count), and the reverse
# would take a school's real card details into test mode.
BUILDS = {
    "production": "https://app.midtrans.com",
    "sandbox": "https://app.sandbox.midtrans.com",
}

HOST_CONST = re.compile(r'"Oe":"(https://[^"]+)"')
LEGACY_HOST = "veritrans.co.id"

# the matcher's second half, verbatim from the SDK's source
SNAP_FILENAMES = ("snap.js", "snap.min.js")


def _vendor_file(build: str) -> Path:
    return VENDOR / build / LEGACY_HOST / "snap.js"


def _recognises(url: str, src: str) -> bool:
    """Mirror of the SDK's `c()` matcher, read out of the SDK itself.

    Derived rather than hardcoded so that an SDK update which changes what it
    accepts makes this test fail instead of quietly passing on a stale rule.
    """
    host = HOST_CONST.search(src)
    accepted = [m.group(1) for m in [host] if m] + [LEGACY_HOST]
    return (any(marker in url for marker in accepted)
            and any(name in url for name in SNAP_FILENAMES))


# ── the vendored files ───────────────────────────────────────────

def test_both_builds_are_vendored_and_are_real_files():
    for build in BUILDS:
        path = _vendor_file(build)
        assert path.exists(), (
            f"{path.relative_to(ROOT)} is missing. The payment page loads this "
            "before trying the CDN, so a missing file is a payment page that "
            "depends on app.midtrans.com again.")
        size = path.stat().st_size
        assert size > 10_000, (
            f"{path.relative_to(ROOT)} is only {size} bytes, which is far too "
            "small to be Snap.js — a truncated download or an HTML error page "
            "saved under a .js name.")
        src = path.read_text(encoding="utf-8", errors="replace")
        assert "window.snap" in src, (
            f"{path.relative_to(ROOT)} does not define window.snap, so it is "
            "not the SDK the payment page expects.")


def test_each_build_hardcodes_its_own_host():
    """The builds are not interchangeable, and nothing else would notice.

    Without this, copying the sandbox file over the production one — an easy
    mistake during an upgrade, since the filenames are identical — produces a
    production page whose payments go to the sandbox.
    """
    for build, host in BUILDS.items():
        src = _vendor_file(build).read_text(encoding="utf-8", errors="replace")
        found = HOST_CONST.search(src)
        assert found, (
            f"{_vendor_file(build).relative_to(ROOT)} has no `\"Oe\"` host "
            "constant; the SDK's build layout changed and this test needs "
            "re-deriving rather than deleting.")
        assert found.group(1) == host, (
            f"the {build} file points at {found.group(1)!r}, expected {host!r} "
            "— the two builds are swapped.")


# ── the template that loads them ─────────────────────────────────

def _template() -> str:
    return PAYMENT.read_text(encoding="utf-8")


def _source_order() -> list[str]:
    """The order of the runtime source list, as read from `SOURCES`.

    Order is the whole point, so it is asserted on the array itself. Reading
    the first occurrence of each name in the file would compare the `{% set %}`
    declarations instead, which say nothing about which one loads first.
    """
    arr = re.search(r"var SOURCES\s*=\s*\[(.*?)\]\s*;", _template(), re.S)
    assert arr, "the page no longer builds a `SOURCES` list"
    return re.findall(r"'([^']*)'", arr.group(1))


def test_the_page_loads_the_local_copy_before_the_cdn():
    order = _source_order()
    assert len(order) == 2, f"expected exactly two sources, got {order}"
    assert "snap_local" in order[0], (
        "the local source must be first: the whole point is that the page works "
        f"when app.midtrans.com cannot be reached, but the order is {order}")
    assert "snap_remote" in order[1], (
        f"the second source must be the CDN, but the order is {order}")

    text = _template()
    assert text.count("load(0)") == 1, \
        "the loader must start at the first source"
    assert re.search(r"load\(i \+ 1\)", text), \
        "a failed source must advance to the next one"


def test_the_loader_falls_back_on_both_kinds_of_failure():
    """`onerror` covers a missing file; it does not cover a 200 that is not JS.

    A captive portal or a proxy error page answers 200 with HTML, so the script
    element fires `load` and `window.snap` stays undefined. Treating that as
    success is how a school sees a pay button that never enables.
    """
    text = _template()
    assert "s.onerror" in text, "a missing local file must fall back to the CDN"
    assert "!window.snap" in text, (
        "a response that is not the SDK must also fall back — `onload` alone "
        "cannot tell the difference")


def test_the_local_path_satisfies_the_sdks_own_matcher():
    """The load-bearing assertion in this file.

    Snap.js reads `data-client-key` off whichever <script> tag it recognises as
    its own. Sell the local path short — drop the `veritrans.co.id` directory,
    rename it, flatten it — and the key is silently empty: the iframe comes up
    with no `client_key` and no error is raised anywhere.
    """
    text = _template()
    local_var = re.search(
        r"\{%\s*set\s+snap_local\s*=\s*(.+?)\s*%\}", text, re.S)
    assert local_var, "the page no longer defines `snap_local`"

    build_dir = re.search(r"'vendor/midtrans/'\s*~\s*\((.+?)\)\s*~", local_var.group(1), re.S)
    assert build_dir, (
        "cannot read how the build directory is chosen; this test derived its "
        "expectation from that expression")
    # the expression picks 'production' or 'sandbox'; take both
    choices = re.findall(r"'([a-z]+)'", build_dir.group(1))
    assert set(choices) == set(BUILDS), f"unexpected build names: {choices}"

    for build in BUILDS:
        src = _vendor_file(build).read_text(encoding="utf-8", errors="replace")
        url = f"/static/vendor/midtrans/{build}/{LEGACY_HOST}/snap.js"
        assert _recognises(url, src), (
            f"the local path {url!r} would NOT be recognised as Snap.js's own "
            f"script tag, so `data-client-key` is never read and the payment "
            f"iframe loads without a merchant. The URL must contain either the "
            f"API host or {LEGACY_HOST!r}, and end in one of {SNAP_FILENAMES}. "
            f"The {LEGACY_HOST!r} directory in the path is what this test "
            f"exists to protect.")
        listed = build in local_var.group(1)
        assert listed, f"the template does not offer a {build} build"


def test_the_client_key_rides_on_the_tag_we_append():
    """It cannot be passed as an option or as a call argument — the SDK reads it
    from the matching tag's attribute, once, at load."""
    text = _template()
    assert re.search(r"setAttribute\(\s*'data-client-key'\s*,\s*CLIENT_KEY\s*\)", text), (
        "the injected tag must carry `data-client-key`; without it the SDK "
        "initialises with an empty key and the iframe URL has no `client_key`")


def test_the_page_still_has_a_visible_failure_state():
    """If both sources fail the teacher must be told, not left with a dead
    button that looks merely slow."""
    text = _template()
    assert "Gagal memuat" in text, (
        "there must be a user-visible message once every source has failed")
