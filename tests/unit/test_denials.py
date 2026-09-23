"""The tone of a refusal, held as a rule rather than reviewed by eye.

`"Akses ditolak"` and `"Tidak punya akses"` were this app's answers to somebody who
had followed a colleague's link. Both read as an accusation, and both left the reader
with nothing to do. The replacement says what the **thing** is — out of scope for
this account — and, where an action exists, names it.

That is easy to state and easy to lose one sentence at a time, so it is asserted over
the whole app rather than over the two files that happened to be fixed.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.utils import denials

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"

#: What the app must not say again. Each one makes the *reader* the subject of the
#: sentence, or tells them nothing about what to do next. `"Forbidden: …"` is absent
#: on purpose: that is the exception's own log line, which nobody reads as copy.
BLAMING = ("Akses ditolak", "Akses Ditolak", "Tidak punya akses",
           "Anda tidak memiliki izin", "tidak berhak", "dilarang mengakses")

#: The modules that answer a refusal, each of which must take its words from the
#: catalogue instead of writing its own. The catalogue itself is not here: it *is*
#: the words.
REFUSING_MODULES = (
    "errors.py",
    "decorators/security.py",
    "handlers/error_handlers.py",
    "routes/teacher.py",
    "routes/exam.py",
    "routes/admin.py",
    "routes/api.py",
)


def user_copy(text: str, suffix: str) -> str:
    """The text with its comments — and, for Python, its docstrings — removed.

    A guard that reads prose fires on a sentence: `403.html` explains in a `{# … #}`
    which wording it replaced, and `denials.py` documents the phrases it exists to
    kill. Both would trip a naive scan, and the fix would be a relaxed guard rather
    than a correct one. This repository has learned that lesson twice already.
    """
    text = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    if suffix == ".py":
        text = re.sub(r'"""(?:.|\n)*?"""', " ", text)
        text = re.sub(r"'''(?:.|\n)*?'''", " ", text)
        text = re.sub(r"#[^\n]*", " ", text)
    return text


def surfaces():
    yield from sorted(APP.rglob("*.py"))
    yield from sorted((APP / "templates").rglob("*.html"))


def test_no_surface_blames_the_reader():
    offenders = []
    for path in surfaces():
        text = user_copy(path.read_text(encoding="utf-8", errors="replace"),
                         path.suffix)
        for phrase in BLAMING:
            if phrase in text:
                offenders.append(f"{path.relative_to(ROOT)}: {phrase!r}")
    assert not offenders, (
        "these tell the reader they were refused rather than what the record is:\n  "
        + "\n  ".join(offenders))


def test_the_words_come_from_one_place():
    """A tone rule survives contact with a codebase only if there is one copy of it.
    A module that writes its own sentence is how the next harsh one arrives."""
    missing = [name for name in REFUSING_MODULES
               if "from app.utils import denials" not in
               (APP / name).read_text(encoding="utf-8")]
    assert not missing, f"these answer a refusal without the catalogue: {missing}"


def test_every_sentence_is_a_sentence():
    for name, value in vars(denials).items():
        if not name.isupper() or not isinstance(value, str):
            continue
        assert value.endswith("."), f"{name} is a fragment: {value!r}"
        assert len(value) >= 25, f"{name} is too curt to be polite: {value!r}"


def test_the_page_a_visitor_reads_says_it_softly():
    """`403.html` is what a browser gets (a JSON client gets the catalogue instead),
    and it is pinned to Indonesian: a `t()` pair here could never render its English
    half, and `deploy/i18n_coverage.py` refuses a page that pins while carrying
    pairs."""
    raw = (APP / "templates" / "errors" / "403.html").read_text(encoding="utf-8")
    page = user_copy(raw, ".html")

    assert "content_lang = 'id'" in raw, "the pin was lifted without translating it"
    assert "t('" not in page, (
        "a pair on a pinned page can never render its English half")
    assert "hubungi admin sekolah" in page, "the reader is given no way forward"
    assert "Kembali ke Beranda" in page


def test_the_rule_bites_on_the_defect_it_describes():
    """Pointed at the real strings, because a guard that cannot fail is a comment."""
    for phrase in BLAMING:
        assert phrase in user_copy(f'flash("{phrase}", "error")', ".py"), \
            f"the scan stopped seeing {phrase!r}"
    # …and the shapes that must stay legal: a log line, and a comment explaining the
    # wording that was replaced.
    assert "Forbidden" in user_copy('raise ForbiddenError(f"Forbidden: {r}")', ".py")
    assert BLAMING[0] not in user_copy("# was: Akses ditolak ke ujian ini", ".py")
    assert BLAMING[1] not in user_copy("{# Akses Ditolak was the old heading #}", ".html")
