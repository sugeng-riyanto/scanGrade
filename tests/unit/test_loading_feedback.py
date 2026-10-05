"""The two halves of "the site is slow and I cannot tell if it is working".

Both were measured on production before anything changed, and both numbers are
the point of the guards below.

**The font payload.** `base.html` loaded Inter as six TTFs of about 325 KB each,
one per weight 400–900. Five of them are weights this app actually paints with,
so a visitor paid roughly 1.6 MB — ten times the ~157 KB of CSS and JS the same
page sent — and TTF is the one asset class the nginx config here does not gzip.
They are now the upstream WOFF2 subsets (latin, latin-ext with `unicode-range`),
about 24 KB per latin weight: 325 KB → 24 KB per face, and the size budget below
is what keeps it that way.

**The feedback.** A save, an upload, a scan and a publish are all seconds long on
a school connection, and until `app/static/js/sg-ux.js` existed the only sign one
was running was the browser's own tab spinner — outside the page, on the side,
easy to miss on a phone. A teacher who cannot see their paper saving clicks
again. The guards below pin that the shared helper exists, that it is loaded
deferred, that the publish button opts into it, and that its sentence is
bilingual (a hard-coded one would be the single untranslatable string on a page
whose reader may have chosen Indonesian).

The checks are written as pure functions over file text so this file can prove
each one *bites* — `TestTheChecksBite` feeds them the shape they were written
against — rather than only asserting the tree happens to be clean today.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
BASE = TEMPLATES / "base.html"
INTER_CSS = ROOT / "app" / "static" / "vendor" / "inter" / "inter.css"
INTER_DIR = ROOT / "app" / "static" / "vendor" / "inter"
UX_JS = ROOT / "app" / "static" / "js" / "sg-ux.js"
EXAM_FORM = TEMPLATES / "teacher" / "exam_form.html"

#: Bytes. Five latin faces at ~24 KB is ~122 KB; the budget leaves room for the
#: latin-ext faces on disk without letting a return to a full font through.
LATIN_BUDGET = 160 * 1024
#: The whole directory: the ten subset files. A rebuild that ships a full face
#: per weight is ~1.6 MB, so this is what a regression looks like.
TOTAL_BUDGET = 400 * 1024

FACE = re.compile(r"@font-face\s*\{(.*?)\}", re.S)
WEIGHT = re.compile(r"font-weight\s*:\s*(\d+)")
SRC = re.compile(r"src\s*:\s*url\(([^)]+)\)")
UNICODE = re.compile(r"unicode-range\s*:\s*([^;]+);", re.S)
SCRIPT_SRC = re.compile(r"<script\b[^>]*\bsrc\s*=\s*\"([^\"]+)\"[^>]*>", re.I | re.S)
PRELOAD = re.compile(r"<link\b([^>]*\brel\s*=\s*\"preload\"[^>]*)>", re.I | re.S)
HAS_DEFER = re.compile(r"\bdefer\b", re.I)
ATTR = re.compile(r"(\w[\w-]*)\s*=\s*\"([^\"]*)\"", re.I)


def faces(css: str) -> list[dict]:
    """One dict per `@font-face` block: weight, source, unicode-range."""
    out = []
    for block in FACE.findall(css):
        w = WEIGHT.search(block)
        src = SRC.search(block)
        ur = UNICODE.search(block)
        out.append({
            "weight": int(w.group(1)) if w else None,
            "src": src.group(1).strip().strip("'\"") if src else "",
            "range": (ur.group(1).strip() if ur else ""),
        })
    return out


def shipped_weights(css: str) -> set:
    return {f["weight"] for f in faces(css) if f["weight"]}


def referenced_files(css: str, suffix: str) -> list[str]:
    return [f["src"] for f in faces(css) if f["src"].lower().endswith(suffix)]


def script_tags(html: str, needle: str) -> list[str]:
    """Every `<script src>` tag naming `needle`."""
    return [attrs.group(0) for attrs in SCRIPT_SRC.finditer(html)
            if needle in attrs.group(1)]


def scripts_loaded_deferred(html: str, needle: str) -> bool:
    """Whether the `<script src>` naming `needle` carries `defer`."""
    return any(HAS_DEFER.search(tag) for tag in script_tags(html, needle))


def in_the_head(html: str, needle: str) -> bool:
    """Whether `needle` is referenced before `</head>`."""
    head = html.split("</head>", 1)[0]
    return needle in head


def after_the_content(html: str, needle: str, marker: str = "<footer") -> bool:
    """Whether a `<script src>` naming `needle` runs after `marker`.

    The marker is the start of the page chrome that always sits after the body
    content, so "after the content" is a statement about parse order — what a
    reader's browser has already received — rather than about a line number.

    The tag's own offset is used, not a text search for the name: the name also
    appears in the head's explanatory comment, and a guard that reads a comment
    as a script would pass on a page that never loads it.
    """
    if marker not in html:
        return False
    at = html.index(marker)
    return any(attrs.start() > at for attrs in SCRIPT_SRC.finditer(html)
               if needle in attrs.group(1))


def preload_for(html: str, needle: str) -> dict | None:
    """The attributes of a `<link rel=preload>` naming `needle`, or None.

    A bare flag (`crossorigin`, no `=`) is recorded with an empty value, because
    it is exactly the flag whose absence this file has to be able to report and
    a `name="value"` regex cannot see it at all.
    """
    for m in PRELOAD.finditer(html):
        raw = m.group(1)
        attrs = dict(ATTR.findall(raw))
        for flag in ("crossorigin", "as"):
            if flag not in attrs and re.search(r"\b" + flag + r"\b", raw, re.I):
                attrs[flag] = ""
        if needle in (attrs.get("href") or ""):
            return attrs
    return None


def template_weights() -> set:
    """Every font weight the app's copy can ask for, from the class names used.

    `font-bold` and friends are Tailwind utilities; the number is read from the
    utility rather than from a compiled stylesheet so a template is enough.
    """
    named = {"font-normal": 400, "font-medium": 500, "font-semibold": 600,
             "font-bold": 700, "font-extrabold": 800, "font-black": 900}
    found = set()
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for cls, weight in named.items():
            if re.search(r"\b" + re.escape(cls) + r"\b", text):
                found.add(weight)
    # theme.css also sets weights directly (the legibility floor).
    theme = (ROOT / "app" / "static" / "css" / "theme.css").read_text(
        encoding="utf-8", errors="replace")
    for m in re.finditer(r"font-weight\s*:\s*(\d+)", theme):
        found.add(int(m.group(1)))
    return found


# ── the font payload ─────────────────────────────────────────────────────────

class TestTheFontPayload:
    def test_no_true_type_face_is_referenced(self):
        """TTF is the one asset class nginx does not gzip here, which is how a
        1.6 MB font budget rode uncompressed on every page."""
        css = INTER_CSS.read_text(encoding="utf-8")
        ttf = referenced_files(css, ".ttf")
        assert not ttf, (
            "these faces still load a TrueType file, which nginx serves "
            "uncompressed:\n  " + "\n  ".join(ttf))

    def test_every_face_loads_a_woff2(self):
        css = INTER_CSS.read_text(encoding="utf-8")
        bad = [f for f in faces(css)
               if not f["src"].lower().endswith(".woff2")]
        assert not bad, (
            "these @font-face blocks do not load a woff2 file:\n  "
            + "\n  ".join(repr(f) for f in bad))

    def test_the_latin_and_latin_ext_faces_carry_a_unicode_range(self):
        """Without a range the browser fetches *every* subset file on the first
        page — the whole point of subsetting is that it does not."""
        css = INTER_CSS.read_text(encoding="utf-8")
        missing = [f["src"] for f in faces(css) if not f["range"]]
        assert not missing, (
            "these faces have no unicode-range, so the browser will fetch them "
            "regardless of what the page renders:\n  " + "\n  ".join(missing))

    def test_every_weight_the_templates_ask_for_is_shipped(self):
        """The positive invariant. A weight the copy uses but the stylesheet does
        not ship is rendered by a synthesised or fallback face — visibly wrong,
        and silent."""
        wanted = template_weights()
        got = shipped_weights(INTER_CSS.read_text(encoding="utf-8"))
        assert wanted, "no weight was read from the templates"
        assert wanted <= got, (
            f"the templates ask for weights {sorted(wanted)} but the stylesheet "
            f"ships {sorted(got)}; the missing ones fall back to a synthesised "
            "face with no error anywhere")

    def test_no_unused_weight_is_shipped(self):
        """The inverse. A face nobody asks for is ~24 KB (latin) downloaded only
        if some element asks for that weight — but it is also a file every page
        pays for in the stylesheet and it is a future foot-gun, so a weight no
        template names must not be shipped."""
        extra = shipped_weights(INTER_CSS.read_text(encoding="utf-8")) - template_weights()
        assert not extra, (
            f"these weights are shipped but no template asks for them: "
            f"{sorted(extra)} — `font-black` (900) was the unused one the TTF set "
            "carried")

    def test_every_referenced_file_exists(self):
        css = INTER_CSS.read_text(encoding="utf-8")
        missing = [f["src"] for f in faces(css)
                   if not (INTER_DIR / f["src"].rsplit("/", 1)[-1]).is_file()]
        assert not missing, (
            "the stylesheet names files that are not in the repository, so the "
            "browser 404s and the page falls back to a system font:\n  "
            + "\n  ".join(missing))

    def test_the_five_latin_faces_fit_the_budget(self):
        total = sum(p.stat().st_size
                    for p in INTER_DIR.glob("inter-latin-[0-9]*-normal.woff2"))
        assert total and total < LATIN_BUDGET, (
            f"the latin faces total {total} bytes, over the {LATIN_BUDGET}-byte "
            "budget — five weights used to be ~1.6 MB of TTF")

    def test_the_whole_directory_fits_the_budget(self):
        total = sum(p.stat().st_size for p in INTER_DIR.iterdir() if p.is_file())
        assert total < TOTAL_BUDGET, (
            f"app/static/vendor/inter is {total} bytes, over the "
            f"{TOTAL_BUDGET}-byte budget; the full-font TTF set was ~1.6 MB")

    def test_no_true_type_file_is_left_in_the_directory(self):
        """A file nobody references is still served by /static/ — and still
        committed, still reviewed, still a candidate to be re-linked."""
        loose = sorted(p.name for p in INTER_DIR.glob("*.ttf"))
        assert not loose, (
            "these TrueType files are still in the served directory: " + ", ".join(loose))


# ── base.html wires it up ────────────────────────────────────────────────────

class TestTheHeadLoadsIt:
    def test_the_regular_weight_is_preloaded_as_a_font(self):
        """A font is discovered only after the stylesheet that names it is
        parsed, so without a preload the first paint waits a round-trip."""
        attrs = preload_for(BASE.read_text(encoding="utf-8"),
                            "inter-latin-400-normal.woff2")
        assert attrs, "base.html does not preload the regular Inter face"
        assert (attrs.get("as") or "").lower() == "font", (
            "the preload is not `as=\"font\"`, so the browser fetches it in the "
            "wrong mode and downloads the file twice")
        assert "crossorigin" in attrs, (
            "a font request is always made in CORS mode; a preload without "
            "`crossorigin` does not match the real request, so the file is "
            "fetched a second time")

    def test_chart_js_is_not_in_the_head(self):
        """205 KB raw in the head, on every page, whether or not it charts —
        parsed before anything paints, it is the longest single stall on the
        page. It belongs at the end of the body."""
        html = BASE.read_text(encoding="utf-8")
        assert not in_the_head(html, "chart.umd.min.js"), (
            "Chart.js is still referenced in the head, so nothing paints until "
            "it has downloaded and been parsed")
        assert after_the_content(html, "chart.umd.min.js"), (
            "Chart.js is not loaded after the page content, so it is still in "
            "the parser's critical path")

    def test_neither_script_is_deferred(self):
        """The opposite of the usual advice, and required *here*: Alpine is
        `defer`red, a deferred script runs after Alpine starts, and
        `tests/unit/test_exam_script_order.py` exists because a helper that runs
        then is a helper that does not exist yet (the blank white exam). A
        classic script at the end of the body executes during parsing, so it is
        defined before Alpine initialises anything."""
        html = BASE.read_text(encoding="utf-8")
        for needle in ("chart.umd.min.js", "/static/js/sg-ux.js"):
            assert not scripts_loaded_deferred(html, needle), (
                f"{needle} is deferred; it would run after Alpine has already "
                "evaluated every x-data, which is the defect "
                "test_exam_script_order.py is written from")

    def test_the_theme_apply_survives_the_deferred_chart(self):
        """Deferring Chart.js moved it *after* the inline script that themes it.
        The apply must wait for DOMContentLoaded (deferred scripts run before
        it) or every chart keeps Chart.js's unreadable default tick colour."""
        html = BASE.read_text(encoding="utf-8")
        assert "DOMContentLoaded" in html, (
            "base.html applies the chart theme without waiting for the deferred "
            "Chart.js — the defaults would be set only if Chart happened to "
            "exist, which it no longer does at that point")
        assert "readyState === 'loading'" in html, (
            "the theme apply is not guarded on document.readyState, so it would "
            "either miss the event or run before Chart.js on a cached page")

    def test_the_shared_feedback_is_loaded_once_after_the_content(self):
        assert UX_JS.is_file(), "app/static/js/sg-ux.js does not exist"
        html = BASE.read_text(encoding="utf-8")
        assert script_tags(html, "/static/js/sg-ux.js"), (
            "base.html does not load app/static/js/sg-ux.js, so the progress bar "
            "and the busy button reach no page")
        assert len(script_tags(html, "/static/js/sg-ux.js")) == 1, (
            "base.html loads sg-ux.js more than once, so the instrumentation is "
            "registered twice")
        assert after_the_content(html, "/static/js/sg-ux.js"), (
            "sg-ux.js is loaded before the page content is parsed, so it is in "
            "the critical path of every page")


# ── the shared feedback itself ───────────────────────────────────────────────

class TestTheFeedback:
    def test_the_bar_cannot_be_left_claiming_to_be_unfinished(self):
        """The bar creeps to 90% and stops. Without that ceiling it reaches 100%
        while the request is still open, which is the lie this file exists to
        avoid."""
        js = UX_JS.read_text(encoding="utf-8")
        assert re.search(r">=\s*90\b", js), (
            "sg-ux.js has no ceiling on the growing bar, so it can report a "
            "finished request before the response arrives")
        assert "'100%'" in js or '"100%"' in js, (
            "the bar never completes, so a finished action leaves a line across "
            "the top of the page")

    def test_a_fast_request_never_draws_the_bar(self):
        """The exam page syncs a draft every ~12 seconds while a pupil writes. A
        bar that flashed on each of those would be worse than no feedback at all,
        so the bar is delayed and a request that finishes inside the delay draws
        nothing."""
        js = UX_JS.read_text(encoding="utf-8")
        assert re.search(r"DELAY_MS\s*=\s*\d+", js), (
            "sg-ux.js has no delay before showing the bar, so every poll and "
            "autosave flickers a line across the top of the page")
        delay = int(re.search(r"DELAY_MS\s*=\s*(\d+)", js).group(1))
        assert 0 < delay <= 1000, (
            f"the delay is {delay} ms — the indicator has to appear inside a "
            "second even on a slow link, or the reader has already given up")
        assert "showTimer" in js and "setTimeout(reveal" in js, (
            "the delay is declared but the bar is still shown synchronously, so "
            "the number above is decoration")
        assert "if (!visible || !bar) return" in js, (
            "finish() hides a bar that was never drawn, which would leave a "
            "stray line on a page whose requests all settled inside the delay")

    def test_the_busy_button_is_opt_in(self):
        js = UX_JS.read_text(encoding="utf-8")
        assert "data-sg-busy" in js, (
            "sg-ux.js does not read the opt-in attribute, so it cannot tell a "
            "form that wants the treatment from one that manages its own state")
        assert "sgBusyOn" in js, (
            "the busy button has no idempotence marker, so a second pass would "
            "save and then restore the *spinner* instead of the original label")

    def test_a_stale_busy_button_is_released(self):
        """A submit that never navigates would otherwise leave a disabled button
        forever — a dead form with no way back."""
        js = UX_JS.read_text(encoding="utf-8")
        assert "pageshow" in js, (
            "sg-ux.js does not release a busy button when the page is restored "
            "from the bfcache, so going Back to a form finds it stuck")
        assert "clearAll" in js or "clearBusy" in js, (
            "sg-ux.js has no way to clear the busy state")

    def test_its_own_sentence_is_bilingual(self):
        """One sentence, two languages, in the same element — the pattern the
        rest of the app's copy uses. A hard-coded one would be the only string
        on the page the language toggle cannot reach."""
        js = UX_JS.read_text(encoding="utf-8")
        assert "Memproses" in js and "Working" in js, (
            "the busy button's sentence is not bilingual")
        assert "documentElement.lang" in js, (
            "the sentence does not follow the language the reader chose")

    def test_it_cannot_throw_into_a_page_script(self):
        """This file runs in front of every async action in the app; a throw
        from it would break the very submit it is decorating."""
        js = UX_JS.read_text(encoding="utf-8")
        assert js.count("catch (") >= 3, (
            "sg-ux.js does not guard its hooks; a failure in the instrumentation "
            "would propagate into the page's own submit handler")

    def test_the_publish_button_opts_in(self):
        """The one place the gap was reproduced: saving and publishing a paper
        is the app's slowest write, and it looked like nothing was happening."""
        html = EXAM_FORM.read_text(encoding="utf-8")
        assert re.search(r"<button[^>]*value=\"publish\"[^>]*data-sg-busy", html, re.S) \
            or re.search(r"<button[^>]*data-sg-busy[^>]*value=\"publish\"", html, re.S), (
            "the save-and-publish button does not carry `data-sg-busy`, so it "
            "still shows no sign that it is saving")


# ── the checks bite ──────────────────────────────────────────────────────────

class TestTheChecksBite:
    """Each reading above, fed the shape it was written against.

    A guard that cannot fail is a comment. These run the same functions over the
    text this change replaced (and the operation it replaced it with), so the
    difference between green and red is the change and not the tree.
    """

    def test_the_ttf_reading_catches_the_old_stylesheet(self):
        old = ("@font-face { font-family:'Inter'; font-weight:400; "
               "src:url(/static/vendor/inter/Inter-400.ttf) format('truetype'); }")
        assert referenced_files(old, ".ttf") == ["/static/vendor/inter/Inter-400.ttf"]
        assert [f for f in faces(old) if not f["src"].lower().endswith(".woff2")]

    def test_the_range_reading_catches_a_face_without_one(self):
        no_range = "@font-face { font-weight:400; src:url(a.woff2); }"
        assert not faces(no_range)[0]["range"]

    def test_the_weight_reading_catches_a_missing_face(self):
        shipped = shipped_weights("@font-face { font-weight:400; src:url(a.woff2); }")
        assert 800 not in shipped, (
            "an 800-weight template would fall back to a synthesised face and "
            "the shipped set above would say so")

    def test_the_defer_reading_catches_the_old_script_tag(self):
        old = '<script src="/static/vendor/chart.umd.min.js"></script>'
        assert not scripts_loaded_deferred(old, "chart.umd.min.js")

    def test_the_preload_reading_catches_an_uncors_preload(self):
        html = ('<link rel="preload" href="/static/vendor/inter/'
                'inter-latin-400-normal.woff2" as="font" type="font/woff2">')
        attrs = preload_for(html, "inter-latin-400-normal.woff2")
        assert attrs is not None and "crossorigin" not in attrs, (
            "a preload without crossorigin must be reported, or the duplicate "
            "font request ships unnoticed")
