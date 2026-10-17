"""What a rendered page must never show — swept over every document the fixture gate renders.

The suite is full of rules about what a page *carries*: 352 suites, and the ones that read
rendered output assert presence between three and twenty-four times. Presence is silent about
everything else, and the failures that reach a reader are opposite in shape:

* a **missing** element goes red in the suite;
* an **extra** one passes every `in html` assertion there is. `print/report_card.html` printed
  a stray `%}` under its buttons for a month on exactly that logic — `tests/unit/test_template_artifacts.py`
  exists for it and says so: "presence is checked in a dozen places; **absence needs its own
  test**".

That file sweeps the *templates* (lexed, 148 files, no fixture needed). This one sweeps the
**documents**: every cell `deploy/render_fixture_gate.py` can render — the eight fixture calls
against the five report documents, rendered through the real app — read back as what a person
sees. The two are complements, and the division is the same one test_template_artifacts.py
states: a template is where a mangled edit lands, a document is where a value lands.

The reading is by **text node**, via the standard library's parser, and that is the
measurement's own finding rather than a style choice. A naive `<[^>]*>` strip splits a tag on
a `>` inside an attribute: `@htmx:response-error.window="notify(t('Terjadi kesalahan', …), 'error')"`
left its tail behind as if it were copy, and the first version of the marker list reported two
`t('` "leaks" in `teacher/reports.html` and `teacher/analysis_student.html` that no reader can
see — they are attribute bindings, which is where every `t()` pair in this app lives. A marker
a reader sees is in a text node; markup is not copy. `alt`/`title`/`placeholder` are folded in
because copy is written into them too.

**The honest limit is the marker list**, and it was measured before it was written
(`.freebuff/measure_page_markers.py` in the working thread): every marker below reports **zero**
hits across today's corpus, so a rule that fires is a defect and not a page that is merely
unusual. Two candidates were measured and *dropped* for the reason the i18n tool records about
`label` and `para` — `True`/`False` is a question type and an answer column on
`teacher/analysis_student.html` ("Q3 · True / False"), and `undefined`/`null` are ordinary
JavaScript in `teacher/reports.html`'s script bodies, which this reader does not read at all.
A guard that fires on correct copy gets deleted, so the list stays short and each entry says
what it would mean.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "render_fixture_gate.py"
THEME_GATE = ROOT / "deploy" / "theme_gate.sh"

#: A marker is `(name, pattern, what it means)`. Every one of these is a value that only a
#: server-side object can produce, or copy that was never meant to ship — never a phrase
#: either language of this app writes on purpose. Each was measured at zero hits on the
#: current corpus; `TestTheMarkerListIsHonest` keeps that from being an accident of today.
MARKERS: tuple[tuple[str, str, str], ...] = (
    ("jinja-closer", r"%}", "a tag whose opener an edit removed renders its closer as text"),
    ("jinja-expr", r"\{\{", "an unrendered expression reached the page"),
    ("jinja-stmt", r"\{%", "an unrendered statement reached the page"),
    ("jinja-comment", r"\{#", "an unrendered comment reached the page"),
    ("python-none", r"\bNone\b", "a Python `None` was printed instead of a value"),
    ("jinja-undefined", r"\bUndefined\b", "Jinja's undefined object itself was printed"),
    ("undefined-error", r"UndefinedError", "an undefined-key error's own text reached the page"),
    ("python-nan", r"\bNaN\b", "a float that is not a number was printed as a number"),
    ("python-nan-lower", r"\bnan\b", "a float that is not a number was printed as a number"),
    ("python-inf", r"\binf\b", "an unbounded float was printed as a number"),
    ("python-infinity", r"\bInfinity\b", "an unbounded float was printed as a number"),
    ("js-object", r"\[object Object\]", "a JS object was stringified instead of serialised"),
    ("js-undefined", r"\bundefined\b", "a JS value never set was printed as one"),
    ("js-null", r"\bnull\b", "a JS null was printed as if it were a value"),
    ("traceback", r"Traceback \(most recent call last\)",
     "a traceback is on the page a reader sees"),
    ("jinja2-module", r"jinja2\.", "a Jinja module path leaked into the copy"),
    ("werkzeug-module", r"werkzeug\.", "a Werkzeug path leaked into the copy"),
    ("sqlalchemy-module", r"sqlalchemy\.", "an infrastructure path leaked into the copy"),
    ("i18n-pair-leak", r"t\('", "a `t('id','en')` binding rendered as copy instead of text"),
    ("i18n-plain-leak", r"sgT\(", "an `sgT()` call rendered as copy instead of text"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{8,}", "a signed token is on a page a reader can read"),
    ("supabase-role", r"service_role", "the service role name is in the markup"),
    ("supabase-secret", r"sb_secret", "a Supabase secret's own name is in the markup"),
    ("private-key", r"BEGIN [A-Z ]*PRIVATE KEY", "a private key is on the page"),
    ("credential-line", r"Password:\s*\S+",
     "a password was pasted into a name field and printed back — the `identity_names` glitch"),
    ("credential-pair", r"Email:[^\n]{0,40}Password",
     "the issued-credentials line reached a page as copy"),
    ("server-path", r"/home/[a-z]+/", "an absolute server path leaked into the copy"),
    ("windows-path", r"[A-Z]:\\[A-Za-z]", "a Windows path leaked into the copy"),
    ("lorem", r"lorem", "placeholder copy shipped"),
    ("todo", r"\bTODO\b", "a TODO marker shipped as copy"),
    ("fixme", r"\bFIXME\b", "a FIXME marker shipped as copy"),
    ("xxx", r"\bXXX\b", "an XXX marker shipped as copy"),
    ("placeholder", r"PLACEHOLDER", "a PLACEHOLDER marker shipped as copy"),
    ("tbd", r"\bTBD\b", "a TBD marker shipped as copy"),
)


class _Text(HTMLParser):
    """Reader text: data from outside `<script>` and `<style>`.

    `convert_charrefs` is on, so `&amp;` is read as the character a reader sees rather than
    as the entity a template wrote.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._opaque = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._opaque += 1
        for name, value in attrs:
            if name in ("alt", "title", "placeholder") and value:
                self.chunks.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._opaque:
            self._opaque -= 1

    def handle_data(self, data):
        if not self._opaque:
            self.chunks.append(data)


def reader_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return "\n".join(parser.chunks)


def gate():
    """Import `deploy/render_fixture_gate.py` — the corpus this file sweeps."""
    if not GATE_PATH.exists():
        pytest.fail("deploy/render_fixture_gate.py does not exist, so there is no corpus of "
                    "rendered documents to read")
    spec = importlib.util.spec_from_file_location("sg_render_fixture_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reading(app):
    """The whole matrix, measured once — the corpus, and what the corpus left out.

    Beside `cells` rather than inside it because one guard below asks about the
    *reading* and not about any page: which fixtures this interpreter could not look
    at at all. Below the floor a fixture file that will not parse is a property of the
    box, and `deploy/render_fixture_gate.py` owns that rule — the same rule
    `python_requires` states, and the one whose absence quarantined five commits on
    `main` while the site kept serving the release it already had.
    """
    module = gate()
    with app.test_request_context("/"):
        return module.measure(app)


@pytest.fixture(scope="module")
def cells(reading):
    """Every rendered document, once: `[(fixture, document, page), …]`, failures dropped.

    A cell whose render raised is already a finding in `test_render_fixture_gate.py`, and
    the gate prints its exception; it has no page to read for a marker.
    """
    module = gate()
    return [(fixture, document, page)
            for (fixture, document), page in sorted(reading.rendered.items())
            if not page.startswith(module.ERROR)]


# ── the reader ───────────────────────────────────────────────────────────────

class TestTheReaderReadsTextAndNotMarkup:
    """The three shapes that decide whether every marker below is worth having."""

    def test_a_marker_in_a_text_node_is_read(self):
        assert "None" in reader_text("<td>None</td>"), (
            "a value a reader sees is exactly what this guard is for")

    def test_a_marker_inside_an_attribute_binding_is_not(self):
        """The measured false positive: `t()` pairs live in attributes by design."""
        html = '<body @htmx:response-error.window="notify(t(\'Gagal\', \'Failed\'), \'error\')">'
        assert "t('" not in reader_text(html), (
            "an Alpine binding is markup, not copy — reading it flagged two correct pages")

    def test_a_marker_inside_a_script_body_is_not(self):
        html = "<script>var x = null; if (a > b) { z = t('id','en') }</script>"
        assert "null" not in reader_text(html) and "t('" not in reader_text(html), (
            "a script body is code; this marker means a *reader* was shown the value")

    def test_copy_written_into_alt_title_and_placeholder_is_read(self):
        html = '<img alt="TODO" title="FIXME" placeholder="PLACEHOLDER">'
        text = reader_text(html)
        assert "TODO" in text and "FIXME" in text and "PLACEHOLDER" in text, (
            "these three carry copy on this app's pages, so they are read")


# ── the corpus ───────────────────────────────────────────────────────────────

class TestTheCorpusIsReallyRendered:
    def test_the_sweep_has_documents_to_read(self, cells, reading):
        """An empty corpus is not a clean tree — it is a check that ran.

        The floor is the fixture count this interpreter can *look* at, not the
        registered one: below it a fixture whose own file will not parse contributes
        no cell, and counting it here would refuse the release for the interpreter
        the box happens to run — the shape that quarantined five commits on `main`.
        """
        documents = {document for _, document, _ in cells}
        expected = len(gate().FIXTURES) - len(gate().not_measurable_here(reading))
        assert len(cells) >= expected, (
            f"only {len(cells)} rendered cell(s) to read, and there are {expected} "
            f"fixtures this interpreter can look at")
        assert len(documents) >= 4, f"only {len(documents)} document(s) to read: {documents}"
        assert "print/report_card.html" in documents, (
            "the sheet a school hands a family is not in the corpus")

    def test_every_fixture_contributed_a_page(self, cells, reading):
        from_fixtures = {fixture for fixture, _, _ in cells}
        expected = {f.name for f in gate().FIXTURES} - gate().not_measurable_here(reading)
        assert from_fixtures == expected, (
            "a fixture this interpreter can look at rendered nothing at all: "
            f"{sorted(expected - from_fixtures)}")


# ── the sweep ────────────────────────────────────────────────────────────────

class TestNoRenderedDocumentShowsAMarker:
    @pytest.mark.parametrize("name,pattern,meaning", MARKERS, ids=[m[0] for m in MARKERS])
    def test_no_document_shows_this_marker(self, cells, name, pattern, meaning):
        rx = re.compile(pattern)
        found = []
        for fixture, document, page in cells:
            hit = rx.search(reader_text(page))
            if hit:
                found.append(f"{document} ({fixture}) shows {hit.group(0)!r}")
        assert not found, (
            f"{name}: {meaning} — " + "; ".join(found[:4])
            + (" …" if len(found) > 4 else ""))


# ── the list itself ──────────────────────────────────────────────────────────

#: Two samples per marker, because two failures look identical from the outside: a
#: pattern that matches nothing protects nothing, and a pattern that matches ordinary
#: copy is a guard somebody deletes. `FIRES_ON` is the defect, `QUIET_ON` is the copy
#: this app writes on purpose next to it — a percentage beside a closer, an email beside
#: the issued-credentials line.
FIRES_ON: dict[str, str] = {
    "jinja-closer": "</p>\n%}",
    "jinja-expr": "{{ student_name }}",
    "jinja-stmt": "{% endif %}",
    "jinja-comment": "{# a note #}",
    "python-none": "Nama: None",
    "jinja-undefined": "Kelas: Undefined",
    "undefined-error": "jinja2.exceptions.UndefinedError: 'student' is undefined",
    "python-nan": "Rata-rata: NaN",
    "python-nan-lower": "Rata-rata: nan",
    "python-inf": "Skor: inf",
    "python-infinity": "Skor: Infinity",
    "js-object": "Total: [object Object]",
    "js-undefined": "Rata-rata: undefined",
    "js-null": "Rata-rata: null",
    "traceback": "Traceback (most recent call last):",
    "jinja2-module": "jinja2.exceptions.UndefinedError",
    "werkzeug-module": "werkzeug.routing.BuildError",
    "sqlalchemy-module": "sqlalchemy.orm.attributes",
    "i18n-pair-leak": "t('Laporan','Reports')",
    "i18n-plain-leak": "sgT('Gagal memuat')",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.payload.signature",
    "supabase-role": "role: service_role",
    "supabase-secret": "key: sb_secret_abc123",
    "private-key": "-----BEGIN PRIVATE KEY-----",
    "credential-line": "Akun berhasil dibuat. Email: a@b.c, Password: 5Ahj@tMlQc7z",
    "credential-pair": "Akun berhasil dibuat. Email: a@b.c, Password: 5Ahj@tMlQc7z",
    "server-path": "/home/scangrade/app.log",
    "windows-path": "C:\\Users\\User\\repo",
    "lorem": "lorem ipsum dolor sit amet",
    "todo": "TODO: ganti ini",
    "fixme": "FIXME: salah hitung",
    "xxx": "XXX jangan dirilis",
    "placeholder": "PLACEHOLDER nama sekolah",
    "tbd": "TBD jadwal ujian",
}

QUIET_ON: dict[str, str] = {
    "jinja-closer": "Diskon 20% dari harga",
    "jinja-expr": "{ x: 1 }",
    "jinja-stmt": "100% benar",
    "jinja-comment": "# tanda pagar",
    "python-none": "Tidak ada catatan",
    "jinja-undefined": "Belum ditentukan",
    "undefined-error": "Kesalahan tidak dikenal",
    "python-nan": "Nana Suryana",
    "python-nan-lower": "Nanang Pratama",
    "python-inf": "info@sekolah.test",
    "python-infinity": "Tak terhingga",
    "js-object": "Object Storage sekolah",
    "js-undefined": "Didefinisikan ulang",
    "js-null": "Nullam",
    "traceback": "Penelusuran kesalahan",
    "jinja2-module": "modul jinja 2",
    "werkzeug-module": "werkzeug kunci asli",
    "sqlalchemy-module": "Kunci sekolah",
    "i18n-pair-leak": "Kata tidak diterjemahkan",
    "i18n-plain-leak": "singkatan sgT tidak dipakai",
    "jwt": "Kunci publik sekolah",
    "supabase-role": "peran layanan dipakai",
    "supabase-secret": "kunci rahasia",
    "private-key": "Kunci privat tidak ada",
    "credential-line": "Kata sandi baru diminta",
    "credential-pair": "Email: a@b.c",
    "server-path": "Beranda",
    "windows-path": "C:/Users/User/repo",
    "lorem": "Ringkasan hasil",
    "todo": "Tugas mingguan",
    "fixme": "Perbaikan nilai",
    "xxx": "Nilai X",
    "placeholder": "Nama tempat",
    "tbd": "Belum ditentukan",
}


class TestTheMarkerListIsHonest:
    def test_every_marker_has_both_samples(self):
        names = {name for name, _, _ in MARKERS}
        assert set(FIRES_ON) == names, f"a marker has no defect sample: {sorted(names - set(FIRES_ON))}"
        assert set(QUIET_ON) == names, (
            f"a marker has no 'correct copy' sample: {sorted(names - set(QUIET_ON))}")

    @pytest.mark.parametrize("name,pattern,meaning", MARKERS, ids=[m[0] for m in MARKERS])
    def test_each_marker_fires_on_its_defect(self, name, pattern, meaning):
        assert re.search(pattern, FIRES_ON[name]), (
            f"{name} does not match the defect it exists for ({FIRES_ON[name]!r}), so it "
            f"protects nothing: {meaning}")

    @pytest.mark.parametrize("name,pattern,meaning", MARKERS, ids=[m[0] for m in MARKERS])
    def test_each_marker_keeps_quiet_on_correct_copy(self, name, pattern, meaning):
        hit = re.search(pattern, QUIET_ON[name])
        assert not hit, (
            f"{name} fires on copy this app writes on purpose ({QUIET_ON[name]!r} → "
            f"{hit.group(0)!r}) — a guard that fires on correct copy gets deleted: {meaning}")


# ── the gate ─────────────────────────────────────────────────────────────────

class TestTheGateRunsThisSweep:
    """A marker only the suite reads is a marker a release can outrun.

    Scoped to the `TESTS=` assignment on purpose — the gate's header names this file too,
    and a check that reads the whole file is satisfied by the sentence describing the run
    after the run itself is deleted (the trap `test_i18n_coverage.py` records, and
    `test_template_artifacts.py` repeats).
    """

    def test_the_theme_gate_runs_this_file(self):
        line = next((line for line in THEME_GATE.read_text(encoding="utf-8").splitlines()
                     if line.startswith("TESTS=")), "")
        assert line, "the gate no longer lists any suite"
        listed = line.split("=", 1)[1].strip().strip('"').split()
        assert "tests/unit/test_rendered_absence.py" in listed, (
            "deploy/theme_gate.sh does not run this sweep, so a release can ship a page "
            f"showing a marker: {listed}")
