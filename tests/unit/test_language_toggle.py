"""The EN/ID toggle flipped its own label but never the page.

Reported as: "ada mode english(EN) dan mode Bahasa Indonesia(ID). Belum berjalan".

The header button lives in base.html's ``x-data``, but 35 page templates each
declared their own ``lang`` from localStorage. A nested ``x-data`` shadows the
outer scope, so the button mutated the *outer* ``lang`` — which is what its own
label reads, so the label flipped — while every string on the page kept reading
the shadowed inner copy. English only appeared after a manual reload, and it
looked like the translation was missing rather than the state being duplicated.

The fix is one source of truth: ``lang`` is declared once, on ``<body>``, and the
pages read it through Alpine's scope chain. These tests pin that, because the
duplicate declaration is the kind of thing a new page copy-pastes back in.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "app" / "templates").rglob("*.html"))
BASE = ROOT / "app" / "templates" / "base.html"
SOURCE = BASE.read_text(encoding="utf-8")

DECLARATION = re.compile(r"lang:\s*localStorage\.getItem\('sg_lang'\)")
CONSUMES = re.compile(r"\blang\s*===?\s*'en'")
EXTENDS_BASE = re.compile(r"""\{%-?\s*extends\s+["']base\.html["']""")


def _declaring_files() -> dict[str, int]:
    found = {}
    for path in TEMPLATES:
        n = len(DECLARATION.findall(path.read_text(encoding="utf-8", errors="replace")))
        if n:
            found[str(path.relative_to(ROOT)).replace("\\", "/")] = n
    return found


# ── one declaration ──────────────────────────────────────────────

def test_the_language_is_declared_in_exactly_one_place():
    declaring = _declaring_files()

    assert declaring == {"app/templates/base.html": 1}, (
        "`lang` must be declared once, in base.html. A page that declares its own "
        "shadows the header toggle, so the page keeps its previous language while "
        "the button label changes — the exact defect this file exists for:\n  "
        + "\n  ".join(f"{n}x  {p}" for p, n in sorted(declaring.items())))


def test_no_template_shadows_lang():
    """Stated separately from the count so the failure names the cause, not just
    a mismatch in a dict."""
    offenders = sorted(p for p in _declaring_files() if not p.endswith("templates/base.html"))

    assert not offenders, (
        "these templates declare their own `lang` and will ignore the toggle; "
        "delete the declaration and let them read base.html's scope:\n  "
        + "\n  ".join(offenders))


# ── the declaration is reachable from every consumer ─────────────

def test_every_consumer_of_lang_is_inside_the_base_scope():
    """Reading `lang` without declaring it only works because Alpine resolves a
    missing key from the enclosing scope — which requires the page to be rendered
    inside base.html's `<body>`. A standalone template would silently get
    `undefined`, and `undefined === 'en'` is false, so it would always render
    Indonesian with no error."""
    orphans = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not CONSUMES.search(text):
            continue
        if path == BASE or EXTENDS_BASE.search(text):
            continue
        orphans.append(str(path.relative_to(ROOT)).replace("\\", "/"))

    assert not orphans, (
        "these templates read `lang` but do not extend base.html, so they have no "
        "scope to read it from:\n  " + "\n  ".join(orphans))


def test_the_feature_is_actually_used():
    """Guards the opposite failure: deleting every consumer would also make the
    toggle 'unbroken'."""
    consumers = [p for p in TEMPLATES
                 if CONSUMES.search(p.read_text(encoding="utf-8", errors="replace"))]

    assert len(consumers) >= 15, (
        f"only {len(consumers)} template(s) switch on `lang` — the toggle would be "
        f"pointless: {[p.name for p in consumers]}")


# ── the toggle actually changes, and persists ────────────────────

def test_the_toggle_goes_through_setlang():
    """The old handler mutated the variable and wrote localStorage inline, which
    is how the two copies drifted apart. One setter keeps them in step."""
    assert "setLang(" in SOURCE, "base.html has no setLang()"
    assert re.search(r"@click\s*=\s*\"\s*setLang\(", SOURCE), \
        "the language button must call setLang(), not assign `lang` directly"

    body = re.search(r"setLang\(([^)]*)\)\s*\{(.*?)\n        \}", SOURCE, re.S)
    assert body, "setLang() has no body"
    implementation = body.group(2)
    assert "this.lang" in implementation, "setLang must assign the scope's lang"
    assert "localStorage.setItem('sg_lang'" in implementation, \
        "setLang must persist the choice"
    assert "documentElement.lang" in implementation, \
        "setLang must keep the document language in sync for screen readers"


def test_the_toggle_starts_from_the_stored_choice():
    """A stored choice wins; otherwise the app's English default applies.

    The default lives in base.html and is the same on every page — the landing
    route no longer asks for English, because asking would be a second source of
    truth that could disagree with this one. It stays a Jinja `default(..., true)`
    so a page that says nothing gets `en` rather than an empty string, which
    `t()` would read as "not English" by accident.
    """
    assert re.search(
        r"lang:\s*localStorage\.getItem\('sg_lang'\)\s*\|\|\s*"
        r"'\{\{\s*default_lang\|default\('en', true\)\s*\}\}'", SOURCE), \
        "the initial value must come from localStorage, then the page default"

    # The element the browser reads must be right *before* Alpine runs, or a
    # stored-Indonesian session flashes English and reports the wrong language to
    # a screen reader on the first pass.
    assert '<html lang="{{ default_lang|default(\'en\', true) }}"' in SOURCE, \
        "the document language must come from the same default as the scope"


def test_the_page_language_attribute_is_restored_on_load():
    """`<html lang="id">` is static, so a user whose stored choice is English has
    to be corrected on init — otherwise the document claims to be Indonesian
    while showing English."""
    init = re.search(r"init\(\)\s*\{(.*?)\n        \}", SOURCE, re.S)
    assert init, "base.html has no init()"
    assert "documentElement.lang" in init.group(1), \
        "init() must set documentElement.lang from the stored choice"


# ── coverage: the chrome on every page ───────────────────────────

def _t_calls(text: str) -> list[tuple[str, str]]:
    return re.findall(r"\bt\('([^']*)','([^']*)'\)", text)


def test_the_sidebar_has_no_hardcoded_labels_left():
    """The sidebar renders on every authenticated page, so a label that ignores
    the toggle is the most visible way for the mode to look broken."""
    leftover_items = re.findall(r"</i><span(?:\s[^>]*)?>([^<]+)</span></a>", SOURCE)
    leftover_sections = re.findall(
        r'<div class="nav-section-title[^"]*"[^>]*>([^<]+)</div>', SOURCE)

    leftovers = [s.strip() for s in leftover_items + leftover_sections]
    assert not leftovers, (
        "these base.html labels render the same text in both languages:\n  "
        + "\n  ".join(leftovers))


def test_the_navigation_translations_are_real():
    """A binding with an empty or identical English side looks like it works and
    demonstrates nothing; `Pengaturan Demo` is the one label that is the same
    word in both, so it is allowed explicitly rather than by omission."""
    same_by_design = {"Super Admin", "Data", "Demo", "Tools", "AI", "Midtrans",
                      "Audit Log", "Feature Flags", "WhatsApp", "Dashboard",
                      "Import Excel", "Scan OMR", "Whiteboard"}

    calls = _t_calls(SOURCE)
    assert len(calls) >= 100, f"only {len(calls)} t() bindings in base.html"

    empty = [pair for pair in calls if not pair[0].strip() or not pair[1].strip()]
    assert not empty, f"t() called with an empty side: {empty}"

    identical = {i for i, e in calls if i == e} - same_by_design
    assert not identical, (
        "these bindings translate to themselves — either the English side is a "
        "typo, or the label belongs in `same_by_design`:\n  "
        + "\n  ".join(sorted(identical)))


def test_the_greeting_carries_both_languages():
    """`Selamat {{ greeting() }}` is rendered by Jinja, so it stayed Indonesian.
    `greeting_en()` already existed and was used by exactly one of the three
    dashboards — this catches the next one that copies the old line."""
    offenders = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "greeting()" not in text:
            continue
        # a bound greeting either pairs both helpers in one t(), or switches on lang
        if "greeting_en()" in text:
            continue
        offenders.append(str(path.relative_to(ROOT)).replace("\\", "/"))

    assert not offenders, (
        "these templates greet in Indonesian only; pair `greeting_en()` with "
        "`greeting()` the way the other dashboards do:\n  " + "\n  ".join(offenders))


def test_the_dark_mode_labels_follow_the_toggle():
    rules = ["Terang", "Gelap"]
    for label in rules:
        assert f"t('{label}'" in SOURCE, f"the dark-mode label {label!r} is still hardcoded"


def test_server_rendered_counts_use_the_toggle():
    """Jinja interpolates a count once, server-side, so `{{ n }} ujian` stayed
    Indonesian in English mode. A count still followed by a bare lowercase word has
    not been bound.

    Only templates that extend base.html are policed: `monitor.html` is a
    standalone operations page with no header and therefore no language toggle, so
    there is no choice for it to honour.
    """
    bound_call = re.compile(r"\bt\(\s*'[^']*'\s*,\s*'[^']*'\s*\)")
    leftovers = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not EXTENDS_BASE.search(text):
            continue
        # Blank out the t(...) calls first: they legitimately contain the count
        # pattern on both sides, and matching those would report a bound string
        # as unbound.
        scan = bound_call.sub("t(...)", text)
        for m in re.finditer(r"\{\{[^}]*\|length[^}]*\}\}[ \t]+([a-z]+)", scan):
            leftovers.append(f"{path.name}: {{{{ ... }}}} {m.group(1)}")

    assert not leftovers, (
        "these counts are printed server-side and ignore the language choice:\n  "
        + "\n  ".join(leftovers))


def test_the_button_label_offers_the_other_language():
    """The label names the language you would switch *to*, and it reads the same
    `lang` the content does — which is exactly why the old split was visible as a
    flipping button over an unchanged page."""
    assert re.search(r"x-text=\"lang === 'id' \? 'EN' : 'ID'\"", SOURCE), \
        "the button must read EN while the UI is Indonesian, and ID otherwise"


# ── pages that must be fully bilingual ───────────────────────────

# Pages converted to the toggle, in the order they were done. The list is the
# contract: a page on it may not carry hardcoded Indonesian again, and adding a
# page is the deliberate act of translating it. Listing every template instead
# would just assert the whole app is translated, which it is not yet — that
# failure would be noise, and noise is how a guard gets switched off.
TRANSLATED = [
    "admin_sekolah/dashboard.html",
    "admin_sekolah/import.html",
    "admin_sekolah/students.html",
    "student/settings.html",
    "teacher/ai_settings.html",
    # The one page whose default is English rather than Indonesian, because it is
    # the only page a stranger sees. It is also the page with no authenticated
    # chrome, so it carries its own language button.
    "landing.html",
    # The three role tutorials and the public demo page. They are the pages a
    # school reads before it signs up, so they carry the same contract: no
    # hardcoded Indonesian left behind.
    "tutorial_guru.html",
    "tutorial_murid.html",
    "tutorial_admin_sekolah.html",
    "demo.html",
    # The exam paper itself. It is the page a student sits in front of for an
    # hour, and the one place the toggle was *unreachable*: the terms modal and
    # the fullscreen blocker both cover the whole page, so the navbar's button
    # sits behind them. The exam bar now carries its own control, and everything
    # the student reads during the paper — rules, overlays, tool labels, the
    # offline and penalty messages — follows the choice.
    "student/take_exam.html",
]

# Near-certain Indonesian markers, chosen as function words and domain nouns that
# have no English reading. `dan`, `yang`, `tidak`, `murid`, `ujian` and friends do
# not appear in English sentences, so a hit is a real hit — which is what keeps
# this from being the sort of check that flags `Kelas VII-A` and gets ignored.
INDONESIAN_MARKERS = {
    "yang", "dan", "atau", "tidak", "belum", "sudah", "akan", "untuk",
    "dengan", "dari", "pada", "adalah", "bisa", "harus", "jika", "saat",
    "setelah", "sebelum", "semua", "lain", "juga", "hanya", "lebih",
    "paling", "oleh", "agar", "karena", "serta", "para", "secara",
    "setiap", "melalui", "tanpa", "sesuai", "antara", "tersebut", "minimal",
    "maksimal", "silakan", "murid", "guru", "kelas", "ujian", "nilai",
    "sekolah", "mapel", "soal", "jawaban", "siswa", "pengumuman",
    "percakapan", "akun", "simpan", "hapus", "ubah", "tambah", "cari",
    "kirim", "lihat", "buat", "unggah", "unduh", "impor", "ekspor",
    "pilih", "batal", "tutup", "buka", "masuk", "keluar", "nama",
    "alamat", "tanggal", "waktu", "jumlah", "daftar", "hasil", "kembali",
    "lanjut", "selesai", "proses", "pengaturan", "wajib", "opsional",
    "berhasil", "gagal", "kosong", "halaman",
    # The label-and-instruction vocabulary these particular pages are made of.
    # Without it a string like "Panduan Import Data" carries no marker at all
    # ("panduan", "import" and "data" are all unremarkable) and would sit in the
    # Indonesian column unnoticed while every other string was converted.
    "panduan", "petunjuk", "keterangan", "catatan", "penting", "contoh",
    "klik", "kolom", "tombol", "formulir", "tautan", "isi", "pengguna",
    "kelola", "aturan", "laporan", "tertarik", "lanjutan", "masih", "telah",
    "sedang", "dapat", "dipakai", "digunakan", "dibuat", "dihapus", "gratis",
    "berbayar", "saldo", "tahun", "bulan", "hari", "menit", "label",
    # Privacy / compliance vocabulary on the student settings page. `privacy` and
    # `policy` are English words and deliberately absent; only the Indonesian
    # spellings are listed.
    "kebijakan", "ketentuan", "syarat", "privasi", "retensi", "pemrosesan",
    "persetujuan", "pribadi", "subjek", "penghapusan", "penyimpanan",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_T_CALL = re.compile(r"\bt\(\s*'[^']*'\s*,\s*'[^']*'\s*\)")
_TERNARY = re.compile(r"lang\s*===?\s*'en'\s*\?[^:]*:[^,\"'>]*")


def _visible_strings(text: str) -> list[str]:
    """Text nodes and user-facing attributes, with the bilingual ones removed.

    The t() calls and ternaries *contain* Indonesian on one side by design;
    scanning them unreduced would report every translated string as untranslated.
    Comments are dropped for the same reason: prose about the fix is not UI.
    """
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    text = _T_CALL.sub(" ", text)
    text = _TERNARY.sub(" ", text)
    found = re.findall(r">([^<>{}]+)<", text)
    found += re.findall(r'(?:placeholder|title|aria-label)="([^"]*)"', text)
    # Demo credentials are data, not copy. An address like
    # `guru_mtk_smp@scan-grade.app` is the same string in both languages, and its
    # local part contains a marker word, so scanning it would report the demo page
    # as untranslated forever.
    return [c for c in found if not _EMAIL.match(c.strip())]


def _markers_in(chunk: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(chunk)} & INDONESIAN_MARKERS


def _untranslated(rel: str) -> list[str]:
    path = ROOT / "app" / "templates" / rel
    out = []
    for chunk in _visible_strings(path.read_text(encoding="utf-8", errors="replace")):
        hit = _markers_in(chunk)
        if hit:
            out.append(f"{rel}: {sorted(hit)}  {chunk.strip()[:70]!r}")
    return out


# Strings taken verbatim from the pages above, before they were converted. A
# scanner that matches nothing satisfies every assertion that uses it, so the
# detector is checked against real text rather than a single synthetic sample.
INDONESIAN_CORPUS = [
    "Panduan Import Data",
    "Simpan data murid",
    "Belum ada murid",
    "Tambah Murid",
    "Nama Lengkap",
    "Hapus key ini?",
    "Cari NISN...",
    "Template sudah include NPSN, tahun ajaran, dan password random.",
    "Pengaturan",
    "Kebijakan Retensi Data",
    "Tempel key yang sudah dicopy ke kolom di bawah.",
    "Semua API Key",
]

# What the same pages render in English mode. None of these may be flagged, or
# the guard would fail on the very output it is meant to protect.
ENGLISH_CORPUS = [
    "Import Guide",
    "Save student data",
    "No students yet",
    "Add Student",
    "Full Name",
    "Delete this key?",
    "Search NISN...",
    "The template already includes NPSN, academic year, and a random password.",
    "Settings",
    "Data Retention Policy",
    "Paste the key you copied into the field below.",
    "All API Keys",
]


def test_the_indonesian_detector_flags_real_indonesian():
    missed = [s for s in INDONESIAN_CORPUS if not _markers_in(s)]
    assert not missed, (
        "the marker set no longer recognises real strings from these pages, so the "
        "guard below would pass on untranslated text:\n  " + "\n  ".join(missed))


def test_the_indonesian_detector_leaves_english_alone():
    """A detector that flags everything is as useless as one that flags nothing:
    it fails on the English half of every pair it just checked."""
    flagged = [s for s in ENGLISH_CORPUS if _markers_in(s)]
    assert not flagged, (
        "these English strings would be reported as untranslated:\n  "
        + "\n  ".join(flagged))


def test_already_bound_strings_are_not_reported():
    """The Indonesian half of a t() pair and of a lang ternary is a translation,
    not a leftover. Scanning without removing them reports every converted string."""
    bound = [
        "<p x-text=\"t('Simpan data murid','Save student data')\"></p>",
        "<p x-text=\"lang==='en'?'My Exams':'Daftar Ujian Saya'\"></p>",
        "<!-- Simpan data murid untuk nanti -->",
    ]
    for sample in bound:
        for chunk in _visible_strings(sample):
            assert not _markers_in(chunk), f"{chunk!r} was reported as untranslated"


def test_translated_pages_carry_no_hardcoded_indonesian():
    leftovers = []
    for rel in TRANSLATED:
        path = ROOT / "app" / "templates" / rel
        assert path.exists(), f"{rel} is on the translated list but does not exist"
        leftovers += _untranslated(rel)

    assert not leftovers, (
        "these strings render in Indonesian in both modes — bind them with "
        "t('Indonesia','English') or move the message through window.sgT() in a "
        "component method:\n  " + "\n  ".join(leftovers))


def test_every_page_opens_in_english_by_default():
    """The whole app opens in English, decided in exactly one place.

    `default_lang` survives as a per-route override so a page could ever ask for
    Indonesian — but nothing does, and that is the assertion. A route passing it
    would re-create the very thing this guards against: two places deciding the
    default, drifting apart. Pinning "no route overrides it" is also stronger
    than pinning "the landing route passes en", because the path that is *absent*
    cannot keep passing after base.html changes.
    """
    overrides = {
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in sorted((ROOT / "app").rglob("*.py"))
        if "default_lang" in path.read_text(encoding="utf-8", errors="replace")
    }

    assert overrides == set(), (
        "base.html decides the language for every page; a route that passes "
        f"`default_lang` introduces a second default: {sorted(overrides)}")
    assert "{{ default_lang|default('en', true) }}" in SOURCE, \
        "base.html must open in English"
    assert SOURCE.count("default_lang|default('en', true)") == 2, \
        ("both the <html lang> element and the Alpine scope must read the same "
         "default, or the button label and the page can disagree")


def test_the_translated_list_only_grows_with_intent():
    """Every entry is a page someone converted. Pinning the count makes a silently
    dropped entry visible, the same way the count guard above does for counts."""
    assert len(TRANSLATED) >= 6
    for rel in TRANSLATED:
        text = (ROOT / "app" / "templates" / rel).read_text(encoding="utf-8")
        assert EXTENDS_BASE.search(text), \
            f"{rel} does not extend base.html, so it has no toggle to honour"


def test_messages_built_outside_a_template_can_be_translated():
    """`t()` is a method on the body scope, so an x-data method (`this.pwMsg = ...`)
    cannot reach it — `this` there is the component, which does not own `t`.
    window.sgT() is the escape hatch for those, and it reads the <html lang>
    attribute that setLang() and the pre-Alpine script maintain."""
    assert "window.sgT = function" in SOURCE, \
        "base.html must expose sgT for component methods that build messages"
    assert "document.documentElement.lang === 'en'" in SOURCE, \
        "sgT must read the same language the templates do"
    # The attribute must be right before Alpine starts, or sgT reads the
    # hardcoded <html lang=\"id\"> on the first interaction of a stored-EN session.
    head = SOURCE.split("<head>", 1)[0]
    assert "document.documentElement.lang=sgStoredLang" in head, \
        "the stored language must be applied before Alpine, like the dark class is"
    assert "document.documentElement.lang = this.lang" in SOURCE, \
        "Alpine's init must keep the attribute in sync after a toggle"


def test_a_text_binding_never_escapes_a_quote():
    """A backslash in a text binding is a runtime error that renders nothing.

    The browser hands Alpine the attribute text verbatim, backslash and all, so an
    apostrophe written as backslash-quote closes the JavaScript string early. Alpine
    throws while evaluating, and the element is left **empty** — no message on the
    page, nothing a smoke test would notice, just one paragraph quietly missing.
    That is exactly what happened to the landing page's stability note, which is why
    this exists: the English had to be rephrased to avoid the apostrophe.

    A backslash has no legitimate use in a text binding, and none of the templates
    contain one, so the rule is absolute rather than a pattern to argue with.
    """
    binding = re.compile(r'(?:x-text|:placeholder|:title|:aria-label)="([^"]*)"')
    offenders = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in binding.finditer(text):
            if "\\" in match.group(1):
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}  {match.group(1)[:70]}")

    assert not offenders, (
        "these bindings contain a backslash, which the browser passes to Alpine "
        "verbatim — the expression throws and the element renders empty. Rephrase "
        "the string instead of escaping it:\n  " + "\n  ".join(offenders))


# ── the exam paper: a switch made mid-exam has to finish the job ──────────
#
# t() inside a template expression is re-evaluated when `lang` changes, which is
# why every translated page needed nothing more than the pair. The exam is the one
# page that also shows strings that were *stored* in component state before the
# click — the sync label, a violation banner, the device bar — and sgT() picked its
# language at the moment it built them. Nothing re-evaluates a stored string, so
# without a rebuild hook one click leaves half the exam bar in the old language.

def _exam() -> str:
    return (ROOT / "app" / "templates" / "student" / "take_exam.html").read_text(encoding="utf-8")


def _method_body(text: str, name: str) -> str:
    """The body of a component method; the templates indent them by 8 spaces."""
    match = re.search(
        rf"\n {{8}}{re.escape(name)}\([^)]*\)\s*\{{(.*?)\n {{8}}\}},", text, re.S)
    return match.group(1) if match else ""


def test_the_exam_page_rebuilds_what_it_stores_when_the_language_changes():
    """Everything the exam shows for longer than a click: the sync label, the
    violation banner and the device bar. All three must be reachable from the
    `lang` watcher, or the toggle is only half a feature on the one page a student
    sits in front of for an hour."""
    text = _exam()

    assert "$watch('lang'" in text, \
        "the exam page never reacts to a language switch"
    assert "relabelLocale" in text, "there is no rebuild hook for stored strings"

    # Statements that store an sgT-built string into component state. Comments are
    # removed first: this file discusses sgT() in prose right beside the calls, and
    # a scan that reads prose reports whichever label the paragraph happens to sit
    # above. `=(?!=)` keeps `this.x === y` from reading as an assignment.
    code = re.sub(r"//[^\n]*", " ", text)
    code = re.sub(r"<!--.*?-->", " ", code, flags=re.S)
    stored, pending = set(), ""
    for line in code.splitlines():
        pending += line + "\n"
        if ";" not in line:
            continue
        if "sgT(" in pending:
            stored |= set(re.findall(r"this\.(\w+)\s*=(?!=)", pending))
        pending = ""

    # What relabelLocale rebuilds, including the one level of methods it calls.
    chain = _method_body(text, "relabelLocale")
    for called in re.findall(r"this\.(\w+)\(", chain):
        chain += _method_body(text, called)
    rebuilt = set(re.findall(r"this\.(\w+)\s*=(?!=)", chain))

    # The device labels are re-probed rather than re-rendered from state, so they
    # are covered by the watcher re-running the two closures initStatusBar stashes.
    devices = {"connectionLabel", "audioInputLabel", "audioOutputLabel"}
    assert "_refreshConnection" in chain and "_refreshAudio" in chain, \
        ("relabelLocale must re-run the device probes, or the device bar keeps the "
         "language it was probed in (Cellular / '… terdeteksi')")

    bar = _method_body(text, "initStatusBar")
    for stash in ("this._refreshConnection = ", "this._refreshAudio = "):
        assert stash in bar, f"{stash.strip()} — the probe is discarded, not kept"

    missed = sorted(stored - rebuilt - devices)
    assert not missed, (
        "these labels are stored in the language they were built in, and nothing "
        "rebuilds them when the student switches mid-exam:\n  " + "\n  ".join(missed))


def test_no_exam_label_is_born_in_one_language():
    """`name: sgT(...)` inside x-data is assembled once, at page load, and sgT()
    does not re-read <html lang> afterwards — so the value can never follow the
    toggle. The exam bar's sync label was written exactly that way."""
    strangers = re.findall(r"^\s{8}(\w+):\s*[^,;\n]*sgT\(", _exam(), re.M)

    assert not strangers, (
        "this label is fixed at page load and cannot follow a mid-exam switch; "
        "store a key and rebuild it from the `lang` watcher instead:\n  "
        + "\n  ".join(strangers))


def test_the_language_control_is_reachable_while_the_paper_is_open():
    """The blockers are the point: the agreement modal is the first thing a student
    sees, and the fullscreen overlay covers the whole page — including the navbar,
    whose button is therefore unreachable at exactly the moment an English reader
    needs it. The exam carries its own control in all three places."""
    buttons = len(re.findall(r"""@click="setLang\(lang === 'id' \? 'en' : 'id'\)""", _exam()))

    assert buttons >= 3, (
        f"the exam has {buttons} language control(s); it needs one in the exam bar, "
        "one in the fullscreen overlay and one in the agreement modal, because each "
        "of the latter two covers the bar")
