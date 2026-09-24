#!/usr/bin/env python3
"""How much of every page's copy actually switches with the language toggle.

The app promises two languages: every visible string is bound as a pair
(`t('Indonesia','English')`, a `lang === 'en' ? … : …` ternary, a `['id','en']`
label pair, or a `{id:…, en:…}` catalogue entry) and the reader flips them. What
that promise does not have is a *number* — "30 pages are translated" says nothing
about the other half, and the sweep in `tests/unit/test_language_toggle.py` only
names leftovers on the pages it already lists. So translation progress was
invisible, and invisible progress is how it goes backwards.

The metric, per page:

    coverage = bilingual pairs / (bilingual pairs + leftover Indonesian strings)

Only copy that *needs* to switch is counted. A page whose copy is English-only
and hardcoded is neither a pair nor a leftover, so it does not score — it has no
toggle, which is a different defect with a different guard. A string already in a
pair is a numerator, never a denominator: counting it as both is how a coverage
number flatters itself.

Two floors, because a percentage alone can be gamed:

  * **pairs** — bilingual units are never *removed*. Exact, no slack. Deleting the
    Indonesian half of a pair to raise nothing is the failure this catches.
  * **coverage** — the percentage never falls. Slack is `PCT_SLACK`, and it is
    small on purpose: one new untranslated string on a 200-unit page is 0.5 pp,
    and anything larger is a decision rather than an accident.

Exit codes follow the other gates in this directory:

    0  no page regressed
    1  a confirmed regression
    2  could not measure — no baseline, or a page the baseline does not know
       about (a new page's floor has to be written deliberately, in the same
       spirit as the exact count in `test_the_translated_list_only_grows_with_intent`)

`--write-baseline` is how progress is recorded. It is deliberately *not* run by
the deploy: a gate that rewrites its own floor while a release is passing turns
a regression into the new yardstick, and writing inside the checkout mid-deploy
would leave the tree dirty for the next `git pull`.

Usage:
    python deploy/i18n_coverage.py                  # report + check
    python deploy/i18n_coverage.py --all            # list every page, not the worst
    python deploy/i18n_coverage.py --json out.json  # the whole table
    python deploy/i18n_coverage.py --write-baseline # record the floors
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
BASELINE = ROOT / "deploy" / "i18n_baseline.json"

# Percentage points a page may fall before it counts as a regression. One new
# untranslated string on a page with 200 units moves this by 0.5 pp.
PCT_SLACK = 0.5

# The documents that are Indonesian *by design* — a printed report card has no
# toggle to honour. Exactly the four AGENTS.md names as `lang="id"` standalone
# documents, and they are the only templates this tool does not gate: they do not
# extend base.html, so the property is checkable rather than asserted by hand
# (`test_the_standalone_documents_are_the_designed_ones`).
STANDALONE = (
    "monitor.html",
    "print/report_card.html",
    "student/result_detail_pdf.html",
    "teacher/print_exam_report.html",
)

# `{% set content_lang = 'id' %}` is **not** an exemption — it is the backlog.
# A page that pins itself says "my copy is Indonesian and a toggle cannot change
# that", which is honest and is why the pin exists; it is also a page with no
# version in the other language, so it belongs in this table at 0% where anyone
# can see it. Treating it as "pinned on purpose" is how the number stops meaning
# anything: the same marker would excuse 80 pages from the count.
PIN_DECLARED = re.compile(r"{%-?\s*set\s+content_lang\s*=\s*'id'\s*-?%}")

# ── the vocabulary, verbatim from tests/unit/test_language_toggle.py ─────────
#
# This is a copy, and a copy is a thing that drifts — so the test file pins it:
# `test_the_marker_vocabulary_matches_the_other_reader` fails if these two sets
# differ. Importing it from the test instead would make a deploy gate depend on
# the test suite, and importing the tool *into* the test would leave the guard
# with no independent reader to disagree with. A duplicated set plus an equality
# assertion is the one arrangement where a drift is impossible and neither
# reader depends on the other.
INDONESIAN_MARKERS = {
    "adalah", "agar", "akan", "aksi", "akun", "alamat", "antara", "atau", "aturan", "batal",
    "belum", "berbayar", "berhasil", "bisa", "buat", "buka", "bulan", "cari", "catatan",
    "contoh", "daftar", "dan", "dapat", "dari", "dengan", "dibuat", "didik", "digunakan",
    "dihapus", "dipakai", "draf", "ekspor", "formulir", "gagal", "gratis", "guru", "halaman",
    "hanya", "hapus", "hari", "harus", "hasil", "impor", "isi", "jawaban", "jika", "juga",
    "jumlah", "karena", "kebijakan", "kelas", "kelola", "keluar", "kembali", "ketentuan",
    "keterangan", "kirim", "klik", "kode", "kolom", "kosong", "lain", "lanjut", "lanjutan",
    "laporan", "lebih", "lihat", "maksimal", "mapel", "masih", "masuk", "melalui", "menit",
    "menunggu", "minimal", "mulai", "murid", "nama", "nilai", "oleh", "opsional", "pada",
    "paling", "panduan", "papan", "pemrosesan", "penalti", "pendidik", "pengaturan",
    "pengguna", "penghapusan", "penguasaan", "pengumpulan", "pengumuman", "penting",
    "penyimpanan", "percakapan", "perhatian", "persetujuan", "petunjuk", "pilih", "pribadi",
    "privasi", "proses", "rata", "retensi", "saat", "salah", "saldo", "sebelum", "secara",
    "sedang", "sekolah", "selesai", "semua", "serta", "sesuai", "setelah", "setiap", "silakan",
    "simpan", "siswa", "soal", "subjek", "sudah", "syarat", "tahun", "tambah", "tanggal",
    "tanpa", "tautan", "telah", "terakhir", "terbit", "terkini", "tersebut", "tersedia",
    "tertarik", "tidak", "tingkat", "tombol", "tren", "tutup", "ubah", "ujian", "unduh",
    "unggah", "untuk", "wajib", "waktu", "yang",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
# A route is not copy. `/api/pengumuman?limit=50` is the same string in both
# languages — the reason it reads as Indonesian is that the endpoint is named in
# Indonesian, which is a fact about the API and not about the page. Without this,
# every Alpine page was charged for its own `fetch()` calls: `shared/comms.html`
# alone carried 23 of them, and a page cannot translate a URL.
_PATH = re.compile(r"^/[A-Za-z0-9_\-./?=&%:+,]*$")
# A role slug is a value the database stores, not a word a screen says. `'guru'`
# is what a row contains and what an API payload carries; the screens that
# *display* a role do it through `roleLabel`, whose labels are pairs — so excusing
# the slug cannot hide a label, and the legacy spellings are here for the same
# reason the role vocabulary keeps them: an old payload still arrives with one.
_ROLE_SLUGS = frozenset({"guru", "murid", "admin_sekolah", "super_admin",
                         "teacher", "student", "admin"})


def is_data(literal: str) -> bool:
    """The literal is the same in both languages because it is not copy."""
    s = literal.strip()
    return bool(_EMAIL.match(s)) or bool(_PATH.match(s)) or s in _ROLE_SLUGS


_T_CALL = re.compile(r"\bt\(\s*'[^']*'\s*,\s*'[^']*'\s*\)")
_SGT_CALL = re.compile(r"\bsgT\(\s*'[^']*'\s*,\s*'[^']*'\s*\)")
_TERNARY = re.compile(
    r"""lang\s*===?\s*'en'\s*\?[^:]*:\s*(?:'[^']*'|`[^`]*`|[^,"'>]*)""")
_ALPINE_VALUE = re.compile(
    r"""\b(?:x-text|x-html|title|placeholder|aria-label|alt|value|x-data)\s*=\s*"([^"]*)\"""",
    re.I)
_SCRIPT_BODY = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
# Script and style bodies, for the text-node pass to skip. Style bodies were
# never copy; script bodies are read by `_SCRIPT_BODY` before this runs. Without
# this the pass read a JS *comment* as copy — an apostrophe in `// Alpine's …`
# opened a literal and base.html came back with one untranslated string.
_MARKUP_BODIES = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
_JS_LITERAL = re.compile(r"'([^'\\\n]*)'")
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
_PAIR_ARRAY = re.compile(r"\[\s*'([^']*)'\s*,\s*'([^']*)'\s*\]")
# `id`/`en`, or a *capitalised* `Id`/`En` suffix (`badgeId`/`badgeEn`). The
# `\w*` prefix is deliberately not applied to the lowercase forms: it made
# `murid:'Murid'` — a role slug, the same word in both languages — read as a
# catalogue key, which excused a real leftover on shared/comms.html.
_PAIR_KEY = re.compile(r"\b(?:id|en|\w*Id|\w*En)\s*:\s*'([^']*)'")


def markers_in(chunk: str) -> set:
    return {w.lower() for w in _WORD.findall(chunk)} & INDONESIAN_MARKERS


def english_partner(chunk: str) -> bool:
    return not markers_in(chunk)


def _strip_js_comments(region: str) -> str:
    """Line comments out, but only when no quote precedes them on the line.

    An Alpine value can hold a URL — `x-data="{ url: 'https://…' }"` — and `//`
    there is a string, not a comment. Cutting from it deletes the rest of the
    attribute, which is how a scanner starts *hiding* the strings it was written
    to find. (Same rule as the sweep's.)
    """
    kept = []
    for line in region.split("\n"):
        cut = line.find("//")
        if cut != -1 and not any(q in line[:cut] for q in "'\"`"):
            line = line[:cut]
        kept.append(line)
    return "\n".join(kept)


def counts(text: str) -> tuple:
    """(pairs, leftovers) for one template's text.

    Pairs are counted *before* they are removed, because the sweep's reduction
    deletes them: a page of 100 `t()` calls and no leftovers must score 100/100,
    not 0/0.
    """
    pairs = 0
    # Every literal the pair shapes bind, so the leftover pass can excuse them.
    excused = set()

    def _pair_array(m):
        nonlocal pairs
        if not english_partner(m.group(2)):
            # `['Sudah Dibaca','Belum Dibaca']` is an id→label table with no
            # English side, not a translation. Returning the match **unchanged**
            # is what keeps its two strings countable as leftovers; replacing it
            # with a blank (the first version did) deleted them from the report,
            # which is how a scanner starts hiding the copy it exists to find.
            return m.group(0)
        pairs += 1
        excused.update((m.group(1), m.group(2)))
        return " "

    def _pair_key(m):
        nonlocal pairs
        pairs += 1
        excused.add(m.group(1))
        return " "


    # An `x-data` catalogue can hold several entries; the array form is the one
    # this app writes for label pairs.
    arrays = _PAIR_ARRAY.sub(_pair_array, text)
    keys = _PAIR_KEY.sub(_pair_key, arrays)

    pairs += len(_T_CALL.findall(text)) + len(_SGT_CALL.findall(text)) \
        + len(_TERNARY.findall(text))

    leftovers = [c for c in _visible_strings(keys, excused) if not is_data(c)]
    return pairs, leftovers


def _visible_strings(text: str, excused: set) -> list:
    """Text nodes and the strings a card renders, minus the bilingual halves."""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = _JINJA.sub(lambda m: "\n" * m.group(0).count("\n") + " ", text)
    for pattern in (_T_CALL, _SGT_CALL, _TERNARY):
        text = pattern.sub(" ", text)
    found = []
    for m in _SCRIPT_BODY.finditer(text):
        found += _literals_in(_strip_js_comments(m.group(1)), excused)
    for region in _ALPINE_VALUE.findall(text):
        found += _literals_in(_strip_js_comments(region), excused)
    text = _MARKUP_BODIES.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    found += re.findall(r">([^<>{}]+)<", text)
    return [c for c in found if markers_in(c)]


def _literals_in(region: str, excused: set) -> list:
    for pattern in (_T_CALL, _SGT_CALL, _TERNARY):
        region = pattern.sub(" ", region)
    for m in _PAIR_ARRAY.finditer(region):
        if english_partner(m.group(2)):
            excused.update((m.group(1), m.group(2)))
    for m in _PAIR_KEY.finditer(region):
        excused.add(m.group(1))
    return [lit for lit in _JS_LITERAL.findall(region) if lit not in excused]


def coverage(pairs: int, leftover_count: int) -> float:
    total = pairs + leftover_count
    return 100.0 if total == 0 else round(pairs * 100.0 / total, 1)


def scan() -> dict:
    """{template: {pairs, leftovers, coverage, pinned, standalone}} for every one."""
    rows = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        rel = path.relative_to(TEMPLATES).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        pairs, leftovers = counts(text)
        pinned = bool(PIN_DECLARED.search(text))
        rows[rel] = {
            "pairs": pairs,
            "leftovers": len(leftovers),
            "coverage": coverage(pairs, len(leftovers)),
            "pinned": pinned,
            # **Frozen**: the page pins its language *and* carries pairs, so the
            # pairs can never render their English half — `lang` is frozen by the
            # pin, and `t()` reads `lang`. 25 templates are in this state today:
            # work started and the pin not yet lifted. It is history, not a
            # regression, so it is reported and not failed — but a page that is
            # *newly* frozen is a translated page being turned back into an
            # Indonesian one, and that does fail.
            "frozen": pinned and pairs > 0,
            "standalone": rel in STANDALONE,
        }
    return rows


def load_baseline() -> dict:
    if not BASELINE.is_file():
        return {}
    try:
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data.get("pages", {})


def check(rows: dict, floors: dict) -> tuple:
    """(regressions, unmeasurable) — two lists of human-readable lines."""
    regressed, unknown = [], []
    for rel, row in sorted(rows.items()):
        if row["standalone"]:
            continue
        floor = floors.get(rel)
        if floor is not None and row["frozen"] and not floor.get("frozen"):
            regressed.append(
                f"{rel}: now pins its language while carrying {row['pairs']} bilingual "
                f"pair(s) — the pin freezes them, so a translated page just stopped "
                f"switching. Unpin it (drop `content_lang = 'id'`) or remove the pairs")
            continue
        if floor is None:
            unknown.append(
                f"{rel}: no floor recorded (pairs={row['pairs']}, "
                f"coverage={row['coverage']}%)")
            continue
        if row["pairs"] < floor["pairs"]:
            regressed.append(
                f"{rel}: bilingual units fell {floor['pairs']} -> {row['pairs']} "
                f"— {floor['pairs'] - row['pairs']} string(s) stopped switching")
        elif row["coverage"] < floor["coverage"] - PCT_SLACK:
            regressed.append(
                f"{rel}: coverage fell {floor['coverage']}% -> {row['coverage']}% "
                f"(now {row['leftovers']} leftover string(s))")
    for rel in sorted(set(floors) - set(rows)):
        unknown.append(f"{rel}: floor recorded for a template that no longer exists")
    return regressed, unknown


def report(rows: dict, floors: dict, show_all: bool, limit: int) -> str:
    gated = {r: v for r, v in rows.items() if not v["standalone"]}
    pairs = sum(v["pairs"] for v in gated.values())
    left = sum(v["leftovers"] for v in gated.values())
    perfect = sum(1 for v in gated.values() if v["leftovers"] == 0 and v["pairs"])
    untranslated = sorted(r for r, v in gated.items() if v["pairs"] == 0)
    out = [
        f"i18n coverage: {len(gated)} template(s), {pairs} bilingual unit(s), "
        f"{left} leftover(s): {coverage(pairs, left)}% overall, "
        f"{perfect} with nothing left to translate, "
        f"{len(untranslated)} with no version in the other language yet"
    ]
    worst = sorted(gated.items(),
                   key=lambda kv: (kv[1]["coverage"], -kv[1]["leftovers"]))
    shown = worst if show_all else [kv for kv in worst if kv[1]["leftovers"]][:limit]
    if shown:
        out.append(f"  {'page':<44} {'pairs':>5} {'left':>5} {'cover':>7}  floor")
    for rel, row in shown:
        floor = floors.get(rel)
        mark = "" if row["leftovers"] == 0 else "  <-"
        out.append(f"  {rel:<44} {row['pairs']:>5} {row['leftovers']:>5} "
                   f"{row['coverage']:>6.1f}%  "
                   f"{(str(floor['coverage']) + '%') if floor else '-':>6}{mark}")
    rest = len(gated) - len(shown)
    if rest > 0 and not show_all:
        out.append(f"  ... {rest} more page(s) at 100% (--all lists every page)")
    frozen = sorted(r for r, v in gated.items() if v["frozen"])
    if frozen:
        out.append(f"  started but frozen by `content_lang = 'id'` ({len(frozen)}): the "
                   f"pairs on these pages cannot render their English half until the "
                   f"pin is lifted, which needs their leftovers translated first - "
                   f"{', '.join(frozen[:6])}{' ...' if len(frozen) > 6 else ''}")
    if untranslated and not show_all:
        out.append(f"  no version in the other language yet ({len(untranslated)}), "
                   f"by page count:")
        for r in untranslated:
            out.append(f"    {r}")
    standalone = sorted(r for r, v in rows.items() if v["standalone"])
    if standalone:
        out.append("  Indonesian by design, not gated (printed, no toggle): "
                   + ", ".join(standalone))
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report-only", action="store_true",
                        help="print the table and exit 0 whatever it says")
    parser.add_argument("--all", action="store_true", help="list every page")
    parser.add_argument("--limit", type=int, default=20,
                        help="how many pages to list unless --all")
    parser.add_argument("--json", metavar="PATH", help="write the whole table here")
    parser.add_argument("--write-baseline", action="store_true",
                        help="record today's numbers as the floors")
    parser.add_argument("--show", action="append", metavar="TEMPLATE", default=None,
                        help="print the leftover strings of a template (repeatable); "
                             "`--show all` prints every leftover in the tree")
    args = parser.parse_args(argv)

    rows = scan()

    if args.show:
        wanted = sorted(rows) if "all" in args.show else args.show
        total = 0
        for rel in wanted:
            path = TEMPLATES / rel
            if not path.is_file():
                print(f"  {rel}: no such template", file=sys.stderr)
                continue
            _, leftovers = counts(path.read_text(encoding="utf-8", errors="replace"))
            total += len(leftovers)
            for chunk in leftovers:
                print(f"{rel}: {sorted(markers_in(chunk))}  {chunk.strip()[:78]!r}")
        print(f"\n{total} leftover string(s) across {len(wanted)} template(s)")
        return 0

    if args.write_baseline:
        payload = {
            "note": ("floors: a page may not lose bilingual units, and its coverage "
                     "may not fall. Written by deploy/i18n_coverage.py "
                     "--write-baseline, one deliberate act at a time."),
            "pages": {r: {k: v[k] for k in ("pairs", "leftovers", "coverage", "frozen")}
                      for r, v in sorted(rows.items()) if not v["standalone"]},
        }
        # `newline="\n"` on purpose: a committed artifact has to be the same
        # bytes on the Windows checkout and on the Linux deploy. The default
        # translates `\n` to `\r\n` here, so the file came out CRLF and every
        # later `--write-baseline` on the other platform showed as a whole-file
        # diff.
        BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8",
                            newline="\n")
        print(f"i18n coverage: wrote {len(payload['pages'])} floor(s) to "
              f"{BASELINE.relative_to(ROOT)}")
        if args.json:
            Path(args.json).write_text(json.dumps(rows, indent=2) + "\n",
                                       encoding="utf-8", newline="\n")
        return 0

    floors = load_baseline()
    print(report(rows, floors, args.all, args.limit))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"pages": rows, "floors": floors}, indent=2) + "\n",
            encoding="utf-8", newline="\n")

    if args.report_only:
        return 0

    if not floors:
        print("i18n coverage: no baseline - cannot measure. "
              f"Run `python deploy/i18n_coverage.py --write-baseline`.", file=sys.stderr)
        return 2

    regressed, unknown = check(rows, floors)
    if regressed:
        print("\ni18n coverage: FAILED - this release translates less than the last "
              "one that passed.", file=sys.stderr)
        for line in regressed:
            print(f"  {line}", file=sys.stderr)
        print("\nBind the string as a pair (t('Indonesia','English')), or, if the "
              "loss is intended, record it deliberately:\n"
              "  python deploy/i18n_coverage.py --write-baseline", file=sys.stderr)
        return 1
    if unknown:
        print("\ni18n coverage: cannot compare - the baseline does not cover this "
              "working tree.", file=sys.stderr)
        for line in unknown:
            print(f"  {line}", file=sys.stderr)
        print("\nA new page's floor has to be written on purpose, so the number can "
              "never quietly start at zero:\n"
              "  python deploy/i18n_coverage.py --write-baseline", file=sys.stderr)
        return 2
    print("i18n coverage: OK - no page lost bilingual copy or fell below its floor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
