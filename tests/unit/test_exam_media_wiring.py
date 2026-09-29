"""The wiring of app-served question media, held to what each half must do.

The service and the two routes are covered next door, against fakes. This file is
about the halves that only exist in a template or in the shared JS, where nothing
runs unless the markup and the handler agree:

* **`exam-media.js` is executed, not grepped.** The app's own `/media/<token>` URL
  is relative, and `new URL()` throws on a relative string — so whether a hosted
  file plays at all turns on one early return in `audioSources`. A source check
  cannot tell a working return from a broken one, so the function is run in node
  over four links: the app's own URL, a protocol-relative impostor, a Drive file,
  and a school's own host.
* **The pupil's page** must build the player from what the server handed over
  (`url`, `kind`) while still reading the two legacy shapes — a bare string and a
  pasted link — and must keep the YouTube frame reachable, because papers written
  before this change still point at YouTube.
* **The builder's page** must post the storage path and the kind it was uploaded
  as, carry the session's csrf token on the upload (a write without one is refused
  403 before the handler runs), and say plainly that a paper which has not been
  saved yet has nowhere to put a file.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MEDIA_JS = ROOT / "app" / "static" / "js" / "exam-media.js"
TAKE_EXAM = ROOT / "app" / "templates" / "student" / "take_exam.html"
EXAM_FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
TEACHER_ROUTE = ROOT / "app" / "routes" / "teacher.py"
STUDENT_ROUTE = ROOT / "app" / "routes" / "student.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the media JS")


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. the shared JS, run ───────────────────────────────────────────────────

def _audio_sources(links: list[str]) -> list[list[str]]:
    script = source(MEDIA_JS) + (
        "\nconsole.log(JSON.stringify(" + json.dumps(links) +
        ".map(sgExamMedia.audioSources)));\n")
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          timeout=60, cwd=str(ROOT))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_an_app_served_url_is_already_the_source():
    """The app's own `/media/<token>` needs no rewriting, and rewriting it is how a
    hosted file ends up with no source at all: `new URL('/media/x')` throws."""
    assert _audio_sources(["/media/eyJwIjoiYSJ9.sig"]) == [["/media/eyJwIjoiYSJ9.sig"]]


@needs_node
def test_a_protocol_relative_link_is_not_mistaken_for_one_of_ours():
    """`//host/path` starts with a slash and is not ours — passing it through would
    hand a question's audio to whoever wrote the link."""
    assert _audio_sources(["//evil.example/clip.mp3"]) == [[]]


@needs_node
def test_the_pasted_link_shapes_still_resolve_as_they_did():
    sources = _audio_sources([
        "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrSt/view",
        "https://school.example/media/bell.mp3",
    ])
    assert len(sources[0]) == 2, "the Drive fallback endpoint was dropped"
    assert "drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOpQrSt" in sources[0][0]
    assert sources[1] == ["https://school.example/media/bell.mp3"]


# ── 2. the pupil's page ─────────────────────────────────────────────────────

def test_the_exam_page_plays_a_hosted_file_and_still_reads_both_legacy_shapes():
    html = source(TAKE_EXAM)
    assert "rawMedia.url" in html, "the hosted URL the server handed over is ignored"
    assert re.search(r"hostedKind\s*=\s*typeof rawMedia === 'object' \? \(rawMedia\.kind",
                     html), "the uploaded file's kind is not read, so a video is drawn as audio"
    assert "rawMedia.audio" in html, "the pasted audio link stopped being read"
    assert "rawMedia.youtube" in html, "the pasted YouTube link stopped being read"
    assert re.search(r"typeof rawMedia === 'string' \? rawMedia", html), (
        "the oldest shape — a bare string entry — stopped being read")


def test_the_exam_page_draws_a_hosted_video_in_the_page():
    html = source(TAKE_EXAM)
    block = re.search(r"<template x-if=\"\(questions\[i\]\|\|\{\}\)\.video\">.*?</template>",
                      html, re.S)
    assert block, "a hosted video has no player, so an uploaded video plays nowhere"
    assert "<video" in block.group(0) and ":src=\"(questions[i]||{}).video\"" in block.group(0)
    # The frame itself, not the name: `sgExamMedia.youtubeEmbed` also appears in the
    # page's data builder, so a substring check passes while the player is gone.
    frame = re.search(r'x-if="\(questions\[i\]\|\|\{\}\)\.youtubeEmbed">.*?<iframe',
                      html, re.S)
    assert frame, "the pasted-YouTube player was removed from the exam page"


def test_the_exam_route_hands_a_url_signed_for_this_pupil_and_this_paper():
    text = source(STUDENT_ROUTE)
    call = re.search(r"with_media_urls\((.{0,200}?)\)\n", text, re.S)
    assert call, "the pupil's page is handed the stored media without a served URL"
    assert "subject=g.user_id" in call.group(1), (
        "the media URL is not bound to the pupil who is sitting, so it could be "
        "copied out of the page and opened by anyone")
    assert "exam_id=exam_id" in call.group(1), "the media URL is not bound to the paper"


# ── 3. the builder's page ───────────────────────────────────────────────────

def test_the_builder_posts_the_path_and_the_kind_it_uploaded_as():
    html = source(EXAM_FORM)
    assert "media_file_" in html and "media_kind_" in html, (
        "the storage path and kind are not posted, so the save route cannot store them")
    assert "q.fileKind" in html, "there is no way to say whether the file is audio or video"
    assert "uploadMedia(i," in html, "the upload control is not wired to a handler"
    assert re.search(r"file: m\.file \|\| ''", html), (
        "a stored upload is not read back into the builder, so a second save drops it")


def test_the_builder_upload_carries_the_session_token():
    """Every write goes through the app's global CSRF hook; a fetch without the
    token is refused 403 before the route runs."""
    html = source(EXAM_FORM)
    handler = re.search(r"async uploadMedia\(index, file\) \{.*?\n        \},", html, re.S)
    assert handler, "the upload handler is gone"
    body = handler.group(0)
    assert "X-CSRF-Token" in body, "the upload is posted without the session token"
    assert "csrf-token" in body, "the token is not read from the page's meta tag"
    assert "'Accept': 'application/json'" in body, (
        "the handler does not ask for JSON, so a refusal would arrive as a redirect")
    assert "endpoint" in body


def test_the_upload_endpoint_is_valid_js_on_a_paper_that_does_not_exist_yet(app):
    """Rendered, not grepped. Jinja autoescapes inside a `<script>`, so a branch
    that is not passed through `tojson` renders `&#34;` into the JS and takes the
    whole builder's script down — on the one page (`/exams/new`) whose upload
    control is disabled anyway, which is exactly why nobody would look."""
    line = re.search(r"^\s*const endpoint = (\{\{.*?\}\});", source(EXAM_FORM), re.M)
    assert line, "the upload endpoint is no longer assigned to a constant"

    with app.app_context():
        rendered = app.jinja_env.from_string(line.group(1))
        new_paper = rendered.render(exam=None).strip()
        edit_page = rendered.render(exam={"id": "abc-123"}).strip()

    assert new_paper == '""', (
        f"a paper with no id renders {new_paper!r} into the JS instead of an empty string")
    assert edit_page == '"/teacher/exams/abc-123/media"', edit_page
    assert "&#" not in new_paper and "&#" not in edit_page, "the endpoint was html-escaped"


def test_a_paper_that_has_not_been_saved_yet_is_told_so_instead_of_offered_an_upload():
    html = source(EXAM_FORM)
    block = re.search(r"\{\% if exam \%\}.*?\{\% else \%\}(.*?)\{\% endif \%\}",
                      html, re.S)
    assert block, "the builder offers an upload for a paper that has no storage yet"
    assert "Save the exam draft first" in block.group(1), (
        "the builder gives a new paper no explanation for the missing upload control")


def test_the_duplicate_route_moves_the_copies_media():
    text = source(TEACHER_ROUTE)
    assert "_relocate_question_media(" in text.split("def duplicate_exam")[1][:2000], (
        "duplicating a paper leaves the copy pointing at the original's media, which "
        "the copy's next save then refuses")


def test_the_builder_previews_from_the_app_and_never_from_storage():
    text = source(TEACHER_ROUTE)
    assert "exam_data[\"question_audio\"] = exam_media.with_media_urls(" in text, (
        "the builder is handed the stored path with no playable URL")
    assert "supabase.storage" not in text.split("def upload_exam_media")[1][:2500], (
        "the upload route reaches Storage directly instead of through the service, "
        "which is where a Storage URL could leak into a reply")
