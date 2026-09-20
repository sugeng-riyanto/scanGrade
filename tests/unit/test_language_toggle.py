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

# A page builds its own language from one of exactly two places: localStorage
# (the original copy-pasted form) or `document.documentElement.lang`. Matching on
# the *source* rather than on one literal spelling is what closes the hole this
# guard had: `/tools/device-preview` declared
# `lang: document.documentElement.lang === 'id' ? 'id' : 'en'` and no test said a
# word, because the old pattern only knew the localStorage form.
#
# A `lang:` whose value comes from another object (`lang: d.lang || lang`) is data
# being carried through a form, not a component property shadowing the scope, so
# it is deliberately not matched.
DECLARATION = re.compile(r"\blang:\s*(?:localStorage|document)\b")
# Either comparison counts as consuming the scope: a binding that reads
# `lang === 'id'` is just as broken by a shadow as one that reads `lang === 'en'`.
CONSUMES = re.compile(r"\blang\s*===?\s*'(?:en|id)'")
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
    # A partial has no scope of its own — Jinja inlines it wherever it is used, so
    # a macro in `auth/_chrome.html` reads the caller's `lang` and `t()`. The
    # underscore is the convention that says "this is included, not rendered", and
    # the per-page check below is what keeps a partial from being used somewhere
    # with no scope: every page that imports one still has to extend base.html.
    orphans = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not CONSUMES.search(text):
            continue
        if path == BASE or EXTENDS_BASE.search(text) or path.name.startswith("_"):
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
    assert "<html lang=\"{{ _content_lang or default_lang|default('en', true) }}\"" in SOURCE, \
        "the document language must come from the same default as the scope"


def test_the_page_language_attribute_is_restored_on_load():
    """`<html lang="id">` is static, so a user whose stored choice is English has
    to be corrected on init — otherwise the document claims to be Indonesian
    while showing English."""
    init = re.search(r"init\(\)\s*\{(.*?)\n        \}", SOURCE, re.S)
    assert init, "base.html has no init()"
    assert "documentElement.lang" in init.group(1), \
        "init() must set documentElement.lang from the stored choice"


# ── a page says which language its own copy is in ────────────────
#
# The toggle translates what was written as a pair. It cannot translate copy that
# was never translated, and a page whose words are Indonesian under
# `<html lang="en">` is read aloud with an English voice: the words are right, the
# pronunciation is wrong, and nothing reports a failure. Such a page declares
# `content_lang = 'id'` — before `{% extends %}`, which is where Jinja runs it —
# and keeps that declaration whatever the reader chose.
#
# The decision is cheap and this test makes it mandatory: a new page is either
# translated, or English already, or it says out loud that its copy is
# Indonesian. `audit_page_lang.py --templates` measures the copy and exits 1 when
# a page declines to decide.
DECLARES_ID = re.compile(r"{%-?\s*set\s+content_lang\s*=\s*'id'\s*%}")

# Copy that was written in English in the first place, so there is nothing to
# declare. Measured, not assumed: `--templates` scores this one 0 Indonesian
# markers against 12 English ones.
ENGLISH_COPY = {
    "tools/generate_answer_sheet.html",
}

# The four documents that render their own `<html>` and cannot follow a toggle:
# they are Indonesian, they say so, and that is the whole point of listing them.
PRINT_DOCUMENTS = (
    "monitor.html",
    "print/report_card.html",
    "student/result_detail_pdf.html",
    "teacher/print_exam_report.html",
)


def _pages_extending_base():
    return [p for p in TEMPLATES
            if EXTENDS_BASE.search(p.read_text(encoding="utf-8", errors="replace"))
            and not p.name.startswith("_")]


def test_every_page_says_which_language_its_own_copy_is_in():
    undecided = []
    for path in _pages_extending_base():
        rel = path.relative_to(ROOT / "app" / "templates").as_posix()
        if rel in TRANSLATED or rel in ENGLISH_COPY:
            continue
        if not DECLARES_ID.search(path.read_text(encoding="utf-8", errors="replace")):
            undecided.append(rel)

    assert not undecided, (
        "these pages neither switch nor say which language their copy is in, so a "
        "screen reader guesses. Translate the page and put it on TRANSLATED, or "
        "declare `{% set content_lang = 'id' %}` above its `{% extends %}`:\n  "
        + "\n  ".join(undecided))


def test_a_page_that_can_switch_does_not_freeze_its_language():
    """The two halves of this contract cannot both be true of one page.

    Declaring Indonesian on a translated page would freeze the document language
    where the reader can actually switch the copy — the same defect as a page
    declaring its own `lang` scope, one level up.
    """
    frozen = [rel for rel in TRANSLATED
              if (ROOT / "app" / "templates" / rel).exists()
              and DECLARES_ID.search(
                  (ROOT / "app" / "templates" / rel).read_text(
                      encoding="utf-8", errors="replace"))]

    assert not frozen, (
        "these pages are translated *and* declared Indonesian, so the toggle "
        "would change the copy while the document kept claiming Indonesian:\n  "
        + "\n  ".join(frozen))


def test_the_declaration_wins_over_the_toggle():
    """Switching to English must not make an Indonesian page *claim* English.

    Three places resolve the document language — the pre-paint script, `setLang()`
    and `init()` — and each has to read the page's declaration, or the cheapest
    one to forget becomes the one that ships.
    """
    assert 'data-content-lang="{{ _content_lang }}"' in SOURCE, \
        "the declaration has to reach the element the browser reads"
    assert "if(sgStoredLang&&!document.documentElement.dataset.contentLang)" in SOURCE, \
        "the pre-paint script must not overwrite an Indonesian declaration"

    set_lang = re.search(r"setLang\(next\)\s*\{(.*?)\n        \}", SOURCE, re.S)
    assert set_lang and "dataset.contentLang || next" in set_lang.group(1), \
        "setLang() must keep the declaration, not just the choice"

    init = re.search(r"init\(\)\s*\{(.*?)\n        \}", SOURCE, re.S)
    assert init and "docLang(this.lang)" in init.group(1), \
        "init() must resolve the same way setLang() does"


def test_the_print_documents_declare_indonesian():
    """They cannot honour the toggle — so they must not claim English."""
    for rel in PRINT_DOCUMENTS:
        text = (ROOT / "app" / "templates" / rel).read_text(
            encoding="utf-8", errors="replace")
        assert '<html lang="id"' in text, (
            f"{rel} prints Indonesian and renders its own <html>; it must say so")
        assert "extends" not in text.split("<html")[0], (
            f"{rel} is expected to be a standalone document")


def test_every_language_control_says_what_it_does_out_loud():
    """The visible label is a two-letter code, which announces nothing.

    A screen reader reads this button as "ID" or "EN" — the language it would
    switch *to* is exactly the information the listener needs and the one piece
    the text does not carry. `title` is not a substitute: on a touch device there
    is no hover, and a screen reader may ignore it entirely.
    """
    missing = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"setLang\(lang === 'id'", text):
            window = text[match.start():match.start() + 600]
            end = window.find("</button>")
            if end != -1:
                window = window[:end]
            if "aria-label" not in window:
                line = text[:match.start()].count("\n") + 1
                missing.append(f"{path.relative_to(ROOT).as_posix()}:{line}")

    assert not missing, (
        "these language controls have no accessible name beyond 'ID'/'EN', so a "
        "screen reader cannot say what they do:\n  " + "\n  ".join(missing))


def test_each_half_of_a_message_pair_declares_its_language():
    """Both halves are in the DOM at once; Alpine only hides one.

    A screen reader reading the tree before Alpine runs — or with scripting off,
    where `x-cloak` is the only thing hiding anything — otherwise announces the
    message twice, in one voice, in two languages.
    """
    chrome = (ROOT / "app" / "templates" / "auth" / "_chrome.html").read_text(
        encoding="utf-8")
    assert '<span lang="id" x-show="lang!==\'en\'"' in chrome
    assert '<span lang="en" x-show="lang===\'en\'"' in chrome


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
    # Every role's landing page. These are the cards a user sees first and the
    # ones reported as "content di dalam cards belum eng/id": a card body here is
    # an Alpine binding, so the text-node-only sweep never looked at it.
    "student/dashboard.html",
    "teacher/dashboard.html",
    "admin/dashboard.html",
    "super_admin/dashboard.html",
    # The one page a super admin opens when a release has not arrived, and the
    # one that says whether the installed runner is the checkout's launcher —
    # read-only, no shell. Its copy is all pairs, so it belongs here.
    "super_admin/deploy_status.html",
    "admin_sekolah/dashboard.html",
    "admin_sekolah/import.html",
    # The exam builder. It is where a teacher spends the most time in the app —
    # title, classes, weightings, media, anti-cheat and the AI upload path are all
    # on this one page, so it was also the largest block of Indonesian left.
    "teacher/exam_form.html",
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
    # The capacity status page. A school reads it *before* deciding, next to the
    # landing page's capacity block, so it carries the same contract: the numbers
    # come from docs/measurements/ and the sentences come in both languages.
    "public/capacity.html",
    # The entry door. Every auth page renders `content_noauth`, which carries no
    # navbar — so the toggle in the authenticated chrome is simply absent, and the
    # first page a stranger ever meets would have been copy for one reader. Each
    # one carries its own control from `auth/_chrome.html`, the same convention the
    # landing page and /capacity use.
    "auth/login.html",
    "auth/login_user.html",
    "auth/register.html",
    "auth/forgot_password.html",
    "auth/verify_code.html",
    "auth/reset_password.html",
    "auth/set_new_password.html",
    "auth/activate.html",
    "auth/activate_success.html",
    "auth/register_success.html",
    "auth/reset_password_success.html",
    "auth/reset_success.html",
    # The device preview. It is an internal tool, but it is the page someone opens
    # *while* judging a layout, so a label in one language there is a label the
    # reviewer reads in the wrong one — and its own instructions are the copy that
    # explains what the three frames mean.
    "tools/device_preview.html",
    # The item analysis. A teacher reads it to decide whether a *question* worked,
    # and every word on it — the chart axes, the legs of the item map, the note
    # under each finding — is a catalogue entry, so the toggle translates the
    # whole page including the charts it redraws.
    "teacher/analysis.html",
]
# Partials are deliberately *not* on this list, and the assertion below says why:
# an entry has to extend base.html, because it is the page's own scope that owns
# `t()`. `teacher/_results_table.html` and `teacher/wb_toolbar.html` render inside
# translated pages and were found to carry hardcoded Indonesian by
# deploy/i18n_coverage.py; their copy is guarded by that tool's floor instead —
# adding them here would have meant relaxing this rule to admit a template with no
# toggle to honour.

# Near-certain Indonesian markers, chosen as function words and domain nouns that
# have no English reading. `dan`, `yang`, `tidak`, `murid`, `ujian` and friends do
# not appear in English sentences, so a hit is a real hit — which is what keeps
# this from being the sort of check that flags `Kelas VII-A` and gets ignored.
INDONESIAN_MARKERS = {
    "yang", "dan", "atau", "tidak", "belum", "sudah", "akan", "untuk",
    "dengan", "dari", "pada", "adalah", "bisa", "harus", "jika", "saat",
    "setelah", "sebelum", "semua", "lain", "juga", "hanya", "lebih",
    "paling", "oleh", "agar", "karena", "serta", "secara",
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
    # Short Indonesian words an English sentence cannot contain. `kode` was added
    # with the auth door: "Kode salah. Coba lagi." and "Kode aktivasi ..." are
    # exactly the alerts a half-translated login page shows in English mode, and
    # neither carried a marker — the guard walked past both.
    "kode", "salah",
    "klik", "kolom", "tombol", "formulir", "tautan", "isi", "pengguna",
    "kelola", "aturan", "laporan", "tertarik", "lanjutan", "masih", "telah",
    "sedang", "dapat", "dipakai", "digunakan", "dibuat", "dihapus", "gratis",
    "berbayar", "saldo", "tahun", "bulan", "hari", "menit",
    # `label` and `para` are deliberately absent although both are Indonesian
    # words. They are also English — `label` is a form field (`p.append('label',
    # …)`), an HTML attribute and an object key (`k.label`); `para` is a
    # paragraph variable (`para_`, `para.id`). Matching them reported code as
    # untranslated copy, and a guard that fails on code is one somebody deletes.
    # The recall lost is nil: a real string that contains them (`Label Kelas`,
    # `Para Siswa`) still carries `kelas` / `siswa`.
    # Privacy / compliance vocabulary on the student settings page. `privacy` and
    # `policy` are English words and deliberately absent; only the Indonesian
    # spellings are listed.
    "kebijakan", "ketentuan", "syarat", "privasi", "retensi", "pemrosesan",
    "persetujuan", "pribadi", "subjek", "penghapusan", "penyimpanan",
    # Card vocabulary. Found by translating the five dashboards: a string like
    # "Aksi Cepat" or "Tren Nilai" carries no marker from the sets above, so the
    # guard walked past it while the card stayed Indonesian in English mode.
    # Each is Indonesian and has no English reading (unlike `label`/`para`).
    "aksi", "tersedia", "rata", "terakhir", "perhatian", "papan", "tingkat",
    "penguasaan", "penalti", "tren", "mulai", "terbit", "draf", "terkini",
    "menunggu", "pengumpulan", "pendidik", "didik",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_T_CALL = re.compile(r"\bt\(\s*'[^']*'\s*,\s*'[^']*'\s*\)")
# `lang === 'en' ? 'English' : 'Indonesia'` in full, both arms included. The
# old pattern stopped at the first quote after the colon — its class excluded `'`
# — so it blanked the English half and left the Indonesian one to be reported as
# a leftover on every page that used the idiom. Text nodes never showed it,
# because the colon sentence was inside an attribute; reading bindings exposed it.
_TERNARY = re.compile(
    r"""lang\s*===?\s*'en'\s*\?[^:]*:\s*(?:'[^']*'|`[^`]*`|[^,"'>]*)""")

# `window.sgT()` is the escape hatch for messages a component method builds (see
# the test at the bottom of this file). It is a translation, not a leftover.
_SGT_CALL = re.compile(r"\bsgT\(\s*'[^']*'\s*,\s*'[^']*'\s*\)")

# ── what a card actually shows ───────────────────────────────────
#
# Reading text nodes alone was not enough. A card body here is mostly an Alpine
# binding — `<p x-text="t('Total','Total')">`, or a value out of `x-data` that a
# later `x-text` renders — so an entire untranslated card can sit in a template
# with no text node to find. The sweep reads, as well:
#
#   * the string literals inside a binding that renders text (x-text, x-html,
#     title, placeholder, aria-label, alt, value) and inside `x-data`,
#   * the string literals in every <script>, which is where alert bodies, status
#     lines and `.textContent` writes live.
#
# It reads the *literals* rather than the whole expression, because an expression
# is mostly code: `k.label || k.provider` contains the Indonesian marker `label`
# while rendering nothing Indonesian, and reporting it would fail a page that is
# perfectly translated.
_ALPINE_VALUE = re.compile(
    r"""\b(?:x-text|x-html|title|placeholder|aria-label|alt|value|x-data)\s*=\s*"([^"]*)\"""",
    re.I,
)
_SCRIPT_BODY = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
# Script *and* style bodies, for the text-node pass to skip. Style bodies were
# never copy; script bodies are read by `_SCRIPT_BODY` before this runs.
_MARKUP_BODIES = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
_JS_LITERAL = re.compile(r"'([^'\\\n]*)'")
def _strip_js_comments(region: str) -> str:
    """Line comments out, but only when no quote precedes them on the line.

    An Alpine value can hold a URL — `x-data="{ url: 'https://…' }"` — and `//`
    there is a string, not a comment. Cutting from it would delete everything
    after it in the attribute, which is how a scanner starts *hiding* the strings
    it was written to find.
    """
    kept = []
    for line in region.split("\n"):
        cut = line.find("//")
        if cut != -1 and not any(q in line[:cut] for q in "'\"`"):
            line = line[:cut]
        kept.append(line)
    return "\n".join(kept)

# Jinja is blanked before anything is read, not skipped. Two defects hid behind
# the old behaviour: a text node that *contains* Jinja — `<p>Halo {{ nama }}!</p>`
# — never matched the text-node pattern at all, so the guard walked straight past
# it; and a t() call whose argument holds a Jinja ternary
# (`t('Kelas {{ x }} {{ 'a' if ok else 'b' }}', 'Class …')`) has quotes inside the
# braces, which made the literal reader split the pair and report both halves.
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)

# ── the three bilingual shapes this app writes ───────────────────
#
# An Indonesian literal in a binding or a script is a translation only when its
# partner is right there. These are the shapes that count, and the set is
# deliberately small enough to read:
#
#     t('Indonesia', 'English')          sgT('Indonesia', 'English')
#     ['Indonesia', 'English']           (a label pair, see take_exam's _syncLabels)
#     { id: 'Indonesia', en: 'English' } (a catalogue entry, see ai_settings)
#     { badgeId: '…', badgeEn: '…' }     (the same, keyed by suffix)
#
# The partner has to look English — `['Simpan','Batal']` is two Indonesian
# strings, not a translation, and must not be excused by looking like one.
_PAIR_ARRAY = re.compile(r"\[\s*'([^']*)'\s*,\s*'([^']*)'\s*\]")
_PAIR_KEY = re.compile(r"\b(?:id|en|\w*Id|\w*En)\s*:\s*'([^']*)'")
# The `\w*` prefix applies to the *capitalised* suffixes only. Applied to the
# lowercase ones it made `murid:'Murid'` — a role slug, the same word in both
# languages — read as a catalogue key, so the sweep excused a string that really
# does render untranslated in `shared/comms.html`'s role map. Found by comparing
# this reader with `deploy/i18n_coverage.py` template by template; the equality
# assertion that keeps them aligned lives in `tests/unit/test_i18n_coverage.py`.
_PAIR_KEY_COMMENTED_ABOVE = _PAIR_KEY


def _english_partner(chunk: str) -> bool:
    return not _markers_in(chunk)


def _literals_in(region: str) -> list[str]:
    """The literals a region renders, minus the Indonesian half of each pair."""
    for pattern in (_T_CALL, _SGT_CALL, _TERNARY):
        region = pattern.sub(" ", region)
    excused = set()
    for m in _PAIR_ARRAY.finditer(region):
        if _english_partner(m.group(2)):
            excused.update((m.group(1), m.group(2)))
    for m in _PAIR_KEY.finditer(region):
        excused.add(m.group(1))
    return [lit for lit in _JS_LITERAL.findall(region) if lit not in excused]


def _visible_strings(text: str) -> list[str]:
    """Text nodes and the strings a card renders, with the bilingual ones removed.

    The t() calls, sgT() calls, ternaries and pair shapes *contain* Indonesian on
    one side by design; scanning them unreduced would report every translated
    string as untranslated. Comments are dropped for the same reason: prose about
    the fix is not UI.
    """
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = _JINJA.sub(lambda m: "\n" * m.group(0).count("\n") + " ", text)
    text = _T_CALL.sub(" ", text)
    text = _SGT_CALL.sub(" ", text)
    text = _TERNARY.sub(" ", text)
    found = []
    for m in _SCRIPT_BODY.finditer(text):
        found += _literals_in(_strip_js_comments(m.group(1)))
    for region in _ALPINE_VALUE.findall(text):
        found += _literals_in(_strip_js_comments(region))
    # Script and style bodies are read *above*, so remove them before the
    # text-node pass. Leaving them in let a JS **comment** be read as copy: an
    # apostrophe in `// Alpine's \`t()\` …` opened a literal, the comment ran on
    # to the next `<`, and base.html came back as carrying an untranslated
    # string. A comment is not UI, and nothing is hidden by removing it here —
    # the pass above is where a script's own strings are read.
    text = _MARKUP_BODIES.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    found += re.findall(r">([^<>{}]+)<", text)
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
    """Every entry is a page someone converted, and the list is *the* contract.

    An exact count, not a floor. A floor of 6 passed while a page was deleted from
    the list, and deleting an entry is how a page silently stops being translated:
    the sweep then has nothing to check, so nothing fails, and nobody notices until
    a reader in the other language does. Bumping this number is the deliberate act
    that says "this page is translated now".
    """
    assert len(TRANSLATED) == 32, (
        f"{len(TRANSLATED)} pages are on the translated list. Bump this number when "
        f"you translate another one — and if you *removed* a page, put it back, "
        f"because dropping it turns the sweep off for that page: {TRANSLATED}")
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
    # `docLang()` is the one place that resolves choice vs. declared copy, so the
    # attribute, the pre-paint script and sgT() all read the same answer.
    assert "document.documentElement.lang = this.docLang(this.lang)" in SOURCE, \
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
