"""Editing a published exam must not un-publish it — and a replaced PDF must reach
the pupils who already have the paper open.

Reported: *"Jika Guru melakukan edit setting ujian atau mengganti upload pdf,
pastikan apa yang sudah di publish ke murid ikut update mengikuti setting
terbaru."* Two distinct defects hide behind that sentence, and neither is visible
in the builder while the teacher is looking at it.

**1. Saving an edit silently withdrew the paper.**
``exam_detail`` (the edit door) rebuilt the row with ``"is_published": action ==
"publish"`` and ``"status": "active" if action in ("save_active", "publish")``.
The builder's ordinary Save button posts ``action=save_active`` — so a teacher who
reopened a published paper to fix a typo and pressed **Save** wrote
``is_published = False`` over it. The paper vanished from every pupil's list, and
the teacher was shown "✅ Ujian berhasil disimpan." ``exam_sitting_allowed`` then
refused the pupils still sitting it ("Ujian ini belum tersedia."), because it
requires ``is_published``. The one control a teacher reaches for most is the one
that withdrew the exam.

The rule that belongs here is the one every other toggle in this app already
follows: **saving preserves the paper's publication; only an explicit publish (or
the visibility toggle) changes it.**

**2. A replaced PDF kept the same URL, so nobody re-read it.**
``upload_pdf`` renders the pages to fixed names under
``static/uploads/exams/<exam_id>/`` — ``page_001.png``, ``exam.pdf`` — and NGINX
serves ``/static/`` with ``expires 365d; Cache-Control: public, immutable``. A
teacher who replaced the PDF therefore wrote *new bytes over the same URL*, and a
pupil whose browser (or whose exam page, already open) held the old image kept
serving it. Replacing the paper is the whole point of the action; the URL has to
change when the content does.

The fix is a **generation directory** named from a hash of the PDF bytes, so the
basename the rail and the thumbnail derivation both depend on (``page_001.png``)
is unchanged while the path — and therefore the cache key — is new. Re-uploading
identical bytes keeps the same URLs (idempotent), which is the property that makes
a re-run free.
"""
import hashlib
import re
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
PDF_SERVICE = ROOT / "app" / "services" / "pdf_service.py"

from app.services import pdf_service  # noqa: E402


def _function_body(name: str) -> str:
    """One function's source, newline-agnostic.

    The tree is checked out with CRLF on Windows, so a naive ``"\ndef "`` slice
    runs to the end of the file and quietly asserts against every *other* function
    too — which is how a guard can pass while reading the wrong door.
    """
    source = TEACHER.read_text(encoding="utf-8")
    start = source.index(f"def {name}(")
    match = re.search(r"\r?\ndef ", source[start:])
    return source[start:start + match.start()] if match else source[start:]


# ── 1. saving an edit preserves the paper's publication ──────────────────────

PUBLISHED = {"status": "active", "is_published": True}
DRAFT = {"status": "draft", "is_published": False}


class TestTheEditDoorKeepsThePublication:
    def test_a_save_keeps_a_published_paper_published(self):
        """The defect: Save wrote `is_published = False` over a live paper."""
        from app.routes import teacher

        state = teacher._publication_state(PUBLISHED, "save_active")
        assert state["is_published"] is True, (
            "saving an edit withdrew the paper from every pupil")
        assert state["status"] == "active"

    def test_a_save_draft_also_keeps_a_published_paper_published(self):
        from app.routes import teacher

        assert teacher._publication_state(PUBLISHED, "save_draft")["is_published"] is True

    def test_a_draft_stays_a_draft_on_save(self):
        """The other direction: Save must not *publish* a paper by accident."""
        from app.routes import teacher

        state = teacher._publication_state(DRAFT, "save_active")
        assert state["is_published"] is False
        assert state["status"] == "draft"

    def test_an_explicit_publish_still_publishes(self):
        from app.routes import teacher

        for before in (DRAFT, PUBLISHED, {"status": "active", "is_published": False}):
            state = teacher._publication_state(before, "publish")
            assert state == {"status": "active", "is_published": True}, before

    def test_the_edit_door_asks_the_rule_rather_than_deciding_it_again(self):
        """One rule, one place. The bug was a second decision written inline."""
        call = _function_body("exam_detail")

        assert "_publication_state(" in call, (
            "exam_detail no longer routes its publication state through the rule")
        assert '"is_published": action == "publish"' not in call, (
            "the inline decision that withdrew published papers is back")


# ── 2. a replaced PDF gets a new URL ─────────────────────────────────────────

def _pdf_bytes(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class _Upload:
    def __init__(self, data: bytes, name: str = "exam.pdf"):
        self._data = data
        self.filename = name

    def read(self):
        return self._data


def _app_rooted_at(root: Path) -> Flask:
    """A Flask app whose ``static/`` is ``root/static``.

    `upload_pdf` resolves everything from ``current_app.root_path``, so this is
    the smallest context that exercises the real renderer rather than a fake.
    """
    app = Flask(__name__)
    app.root_path = str(root)
    return app


def _upload_twice(tmp_path, first: bytes, second: bytes):
    with _app_rooted_at(tmp_path).app_context():
        a = pdf_service.upload_pdf(_Upload(first), "exam-1")
        b = pdf_service.upload_pdf(_Upload(second), "exam-1")
    return a, b


def test_replacing_the_pdf_changes_the_page_urls(tmp_path):
    """New bytes behind the same URL is exactly what `immutable` caches wrong."""
    a, b = _upload_twice(tmp_path, _pdf_bytes("the first paper"),
                         _pdf_bytes("a different paper"))

    assert a["page_urls"] and b["page_urls"]
    assert a["page_urls"] != b["page_urls"], (
        "the replaced paper kept the same page URLs, so a cached copy is still "
        "what the pupil sees")
    assert a["pdf_path"] != b["pdf_path"], (
        "the replaced paper kept the same PDF URL, so the download is stale")


def test_the_old_and_new_pages_both_exist_on_disk(tmp_path):
    """Replacing is non-destructive: the previous generation is not deleted."""
    a, b = _upload_twice(tmp_path, _pdf_bytes("one"), _pdf_bytes("two"))

    for url in (a["page_urls"][0], b["page_urls"][0], a["pdf_path"], b["pdf_path"]):
        assert (tmp_path / "static" / url.replace("/static/", "", 1)).exists(), url


def test_re_uploading_the_same_bytes_is_idempotent(tmp_path):
    """Same content, same URLs — so a re-run writes nothing new and a cache of it
    stays valid."""
    same = _pdf_bytes("the paper, unchanged")
    a, b = _upload_twice(tmp_path, same, same)

    assert a["page_urls"] == b["page_urls"]
    assert a["pdf_path"] == b["pdf_path"]
    assert a["page_urls"][0] == b["page_urls"][0]


def test_the_page_name_is_unchanged_so_the_rail_still_derives_a_thumbnail(tmp_path):
    """The basename is a contract with the page's JavaScript and `thumb_name`.

    A query-string cache-buster would have broken the rail's regex
    (`page_(\\d+)\\.png$`); a generation *directory* leaves the basename alone.
    """
    a, _b = _upload_twice(tmp_path, _pdf_bytes("one"), _pdf_bytes("two"))
    first = a["page_urls"][0]

    assert re.search(r"page_(\d+)\.png$", first), first
    assert pdf_service.thumb_name(first.rsplit("/", 1)[-1]).startswith("thumb_")


def test_the_generation_is_named_from_the_content_not_at_random(tmp_path):
    """A hash, so identical bytes land in the same directory and a re-upload is
    free; randomness would orphan a directory on every run."""
    data = _pdf_bytes("hashed")
    digest = hashlib.sha256(data).hexdigest()[:12]

    a, _b = _upload_twice(tmp_path, data, _pdf_bytes("other"))
    assert digest in a["page_urls"][0], (
        f"the page URL {a['page_urls'][0]!r} does not carry the content digest {digest!r}")


def test_the_student_page_still_finds_the_thumbnail_in_a_generation_dir():
    """One contract, two languages: the service writes `thumb_003.png` beside
    `page_003.png`, the rail builds the URL in JavaScript."""
    template = EXAM_PAGE.read_text(encoding="utf-8")
    match = re.search(r"thumbFor\(url\)\s*\{\s*return String\(url \|\| ''\)\.replace\(/(.+?)/,",
                      template)
    assert match, "the rail no longer derives a thumbnail URL"

    url = "/static/uploads/exams/e1/ab12cd34/page_009.png"
    derived = re.sub(match.group(1), "thumb_009.png".replace("\\", ""), url)
    assert derived == "/static/uploads/exams/e1/ab12cd34/thumb_009.png", derived


# ── 3. the thumbnail backfill must follow the stored URL ─────────────────────

def test_thumbs_are_written_beside_the_page_the_url_names(tmp_path):
    """The defect: the backfill rebuilt `<exam_id>/page_001.png` from the exam id,
    so every generation directory was invisible to it and the rail fell back to
    full-size pages."""
    directory = tmp_path / "static" / "uploads" / "exams" / "exam-1" / "ab12cd34"
    directory.mkdir(parents=True)
    Image.new("RGB", (1240, 1754), "white").save(directory / "page_001.png", "PNG")

    with _app_rooted_at(tmp_path).app_context():
        made = pdf_service.ensure_page_thumbs(
            "exam-1", ["/static/uploads/exams/exam-1/ab12cd34/page_001.png"])

    assert made == 1, "the backfill could not find a page stored in a generation dir"
    assert (directory / "thumb_001.png").exists()


def test_the_backfill_still_finds_a_page_stored_flat(tmp_path):
    """An exam uploaded before generation directories keeps working."""
    directory = tmp_path / "static" / "uploads" / "exams" / "exam-1"
    directory.mkdir(parents=True)
    Image.new("RGB", (1240, 1754), "white").save(directory / "page_001.png", "PNG")

    with _app_rooted_at(tmp_path).app_context():
        made = pdf_service.ensure_page_thumbs(
            "exam-1", ["/static/uploads/exams/exam-1/page_001.png"])

    assert made == 1
    assert (directory / "thumb_001.png").exists()


# ── 4. the reprocess route must read the paper it actually stored ────────────

def test_the_reprocess_route_reads_the_stored_pdf_url_not_a_fixed_path():
    """It used to open `<exam_id>/exam.pdf`; with generations the original lives
    one level deeper, so the route reported "No PDF source found" for every paper
    whose PDF had been replaced."""
    call = _function_body("exam_reprocess_pdf")

    assert "local_path_for_url(" in call or "pdf_url" in call, (
        "the reprocess route still assumes a fixed `<exam_id>/exam.pdf` path")
    assert '"pdf_url,pdf_page_urls,title"' in call.replace(" ", ""), (
        "the route no longer reads the stored pdf_url")


def test_the_url_to_path_resolver_is_one_shared_function():
    """Every surface that turns a stored `/static/...` URL into a file must agree,
    or one of them silently reads the wrong generation."""
    assert hasattr(pdf_service, "local_path_for_url")
    service = PDF_SERVICE.read_text(encoding="utf-8")
    assert "def local_path_for_url(" in service

    # And it is the function the backfill uses, not a second copy of the rule.
    assert "local_path_for_url(" in service.split("def ensure_page_thumbs(")[1]
