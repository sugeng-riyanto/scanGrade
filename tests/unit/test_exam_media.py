"""A link a teacher pastes has to become something a pupil's browser will play.

Both pages that show question media carried their own copy of the same two
functions — `youtubeEmbedUrl` and `gdriveDirect`, one pair in the teacher's builder
and another in the student's exam page — and nothing tested either. Two copies of
one rule is how the builder comes to show a teacher a player that the pupil's page
cannot produce; and with no test at all, the rule itself was never checked against
the links people actually paste.

It was wrong in two ways that matter for a listening paper:

* **Google Drive.** `gdriveDirect` rewrote a Drive link to
  ``docs.google.com/uc?export=download&id=…``. That endpoint stopped serving
  cross-site requests in January 2024 (Google issue 319531488), so the element goes
  silent with no console error and nothing on the page says why. The endpoint
  Drive's own download button uses, ``drive.usercontent.google.com/download`` with
  ``confirm=t``, is the one that serves the bytes — and it honours Range requests,
  so seeking inside a track works. Both are offered as candidate ``<source>``s now,
  in that order, because a media element walks the list and plays the first that
  loads.
* **A link it could not read at all.** ``https://drive.google.com/open?id=…``, a
  bare eleven-character YouTube id, ``youtube.com/live/…``, a mobile host and a
  ``t=`` offset were all either missed or dropped, and a missed link produced an
  empty `<iframe>` — a black rectangle where the question should be, with no
  sentence telling the pupil to fetch the proctor.

The module is one file read by both pages, and these tests run it under Node rather
than reading it, because the thing being checked is what the function *returns*:
``''`` and ``'https://…/embed/<id>'`` look equally fine to a regex.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "app" / "static" / "js" / "exam-media.js"
STUDENT_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
BUILDER_PAGE = ROOT / "app" / "templates" / "teacher" / "exam_form.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the module")

VID = "dQw4w9WgXcQ"
PLAIN_EMBED = f"https://www.youtube.com/embed/{VID}?rel=0&modestbranding=1"

FILE_ID = "1AbCdEfGhIjKlMnOpQrSt"
UA = f"https://drive.usercontent.google.com/download?id={FILE_ID}&export=download&confirm=t"
LEGACY = f"https://docs.google.com/uc?export=download&id={FILE_ID}"

#: Pasted link -> the embed URL the page must put in the iframe. `''` is a refusal,
#: and a refusal has to be visible: the page says so instead of drawing nothing.
YOUTUBE: dict[str, str] = {
    "https://youtu.be/dQw4w9WgXcQ": PLAIN_EMBED,
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ": PLAIN_EMBED,
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ": PLAIN_EMBED,
    "https://www.youtube.com/embed/dQw4w9WgXcQ": PLAIN_EMBED,
    "https://www.youtube.com/shorts/dQw4w9WgXcQ": PLAIN_EMBED,
    "https://www.youtube.com/live/dQw4w9WgXcQ": PLAIN_EMBED,
    "dQw4w9WgXcQ": PLAIN_EMBED,
    # A listening paper points at one verse, and the offset used to be dropped.
    "https://youtu.be/dQw4w9WgXcQ?t=90": PLAIN_EMBED + "&start=90",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=90s": PLAIN_EMBED + "&start=90",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&start=90": PLAIN_EMBED + "&start=90",
    "https://youtu.be/dQw4w9WgXcQ?t=1h2m3s": PLAIN_EMBED + "&start=3723",
    # Not a video: refused, so the page can say so.
    "https://www.youtube.com/playlist?list=PL1234567890": "",
    "https://www.youtube.com/watch?v=short": "",
    "https://example.com/watch?v=dQw4w9WgXcQ": "",
    "https://vimeo.com/123456789": "",
    "  ": "",
    "": "",
}

#: Pasted link -> the candidate sources, in the order the browser should try them.
AUDIO: dict[str, list[str]] = {
    f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing": [UA, LEGACY],
    f"https://drive.google.com/file/d/{FILE_ID}": [UA, LEGACY],
    f"https://drive.google.com/open?id={FILE_ID}": [UA, LEGACY],
    f"https://drive.google.com/uc?id={FILE_ID}&export=download": [UA, LEGACY],
    f"https://drive.google.com/uc?export=download&id={FILE_ID}": [UA, LEGACY],
    # A folder is not a file and a document is never audio: refused rather than
    # handed to the element to fail at in silence.
    f"https://drive.google.com/drive/folders/{FILE_ID}": [],
    f"https://docs.google.com/document/d/{FILE_ID}/edit": [],
    # Any other direct file is the teacher's own host: passed through untouched.
    "https://example.com/audio/soal1.mp3": ["https://example.com/audio/soal1.mp3"],
    "https://cdn.sekolah.sch.id/listening/track-2.ogg": [
        "https://cdn.sekolah.sch.id/listening/track-2.ogg"],
    "not a link": [],
    "": [],
}


def _ask(calls: list[tuple[str, str]]) -> list[object]:
    """Run the shipped module under Node and return what it answers, per call."""
    script = (
        "const fs = require('fs');\n"
        f"const src = fs.readFileSync({json.dumps(str(MODULE))}, 'utf8');\n"
        "eval(src + '\\n;globalThis.__m = sgExamMedia;');\n"
        "const m = globalThis.__m;\n"
        f"const calls = {json.dumps(calls)};\n"
        "console.log(JSON.stringify(calls.map(c => m[c[0]](c[1]))));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


@needs_node
@pytest.mark.parametrize("pasted", sorted(YOUTUBE))
def test_a_youtube_link_becomes_the_embed_a_pupil_can_watch(pasted):
    got = _ask([("youtubeEmbed", pasted)])[0]
    assert got == YOUTUBE[pasted], (
        f"{pasted!r} resolved to {got!r}; the pupil's page renders exactly this as "
        "the iframe source, and '' draws an empty one."
    )


@needs_node
@pytest.mark.parametrize("pasted", sorted(AUDIO))
def test_a_drive_link_becomes_sources_a_pupil_can_hear(pasted):
    got = _ask([("audioSources", pasted)])[0]
    assert got == AUDIO[pasted], (
        f"{pasted!r} resolved to {got!r}. The first candidate is the Drive endpoint "
        "that serves cross-site requests; the legacy one follows it as a fallback."
    )


@needs_node
def test_the_drive_id_survives_every_shape_the_share_button_produces():
    """One id, read out of the five shapes Drive's own UI hands out.

    The endpoints taken from it are only as good as the id, and an id missed here
    shows up as an empty player rather than as an error.
    """
    shapes = [k for k in AUDIO if "drive.google.com" in k and k not in (
        f"https://drive.google.com/drive/folders/{FILE_ID}",)]
    got = _ask([("driveId", s) for s in shapes])
    assert got == [FILE_ID] * len(shapes), (
        f"the id was read from some shapes and not others: {dict(zip(shapes, got))}"
    )


@needs_node
def test_a_drive_folder_and_a_google_document_are_refused_without_an_id():
    """Neither can ever be audio, so neither may reach the element as a URL."""
    got = _ask([
        ("driveId", f"https://drive.google.com/drive/folders/{FILE_ID}"),
        ("driveId", f"https://docs.google.com/document/d/{FILE_ID}/edit"),
        ("driveId", f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit"),
        ("driveId", f"https://drive.google.com/file/d/{FILE_ID}/view"),
    ])
    assert got == ["", "", "", FILE_ID], (
        "a folder or a document link was read as a playable file"
    )


# ── one implementation, read by both pages ──────────────────────────────────

class TestThereIsOnlyOneCopyOfTheRule:
    def test_the_module_exists_and_exports_both_readers(self):
        source = MODULE.read_text(encoding="utf-8")
        for name in ("youtubeEmbed", "audioSources", "youtubeId", "driveId"):
            assert name in source, f"the module does not offer {name}()"

    @pytest.mark.parametrize("page", [STUDENT_PAGE, BUILDER_PAGE])
    def test_neither_page_keeps_its_own_copy(self, page):
        """The defect, stated as a rule.

        Both pages defined `youtubeEmbedUrl`/`gdriveDirect` themselves. Two copies
        of one transformation is how the builder's preview and the pupil's page end
        up disagreeing, and the copy that gets fixed is whichever one is looked at.
        """
        source = page.read_text(encoding="utf-8")
        assert "gdriveDirect" not in source, (
            f"{page.name} still has a private copy of the Drive rewrite"
        )
        assert "youtubeEmbedUrl" not in source, (
            f"{page.name} still has a private copy of the YouTube rewrite"
        )

    @pytest.mark.parametrize("page", [STUDENT_PAGE, BUILDER_PAGE])
    def test_both_pages_load_the_module(self, page):
        assert "/static/js/exam-media.js" in page.read_text(encoding="utf-8"), (
            f"{page.name} binds media without loading the module that resolves it"
        )


class TestAnUnplayableLinkIsSaidOutLoud:
    """The silent failures were the worst of it — an empty player and no sentence."""

    def test_the_student_page_refuses_instead_of_drawing_nothing(self):
        source = STUDENT_PAGE.read_text(encoding="utf-8")
        assert source.count("data-media-refused") == 2, (
            "expected one refusal note for audio and one for video in the exam page"
        )
        assert "tidak bisa dimuat" in source and "cannot be loaded" in source, (
            "the refusal is not bilingual"
        )

    def test_the_players_are_gated_on_the_resolved_url(self):
        """`(q||{}).youtube` was truthy for a playlist link too, so the page drew an
        empty frame for it. The gate has to be the *resolved* value.

        Asserted as the whole `x-if` rather than on the word `youtubeEmbed`: the
        name is in this file five times — the two block conditions, the refusal
        condition and the builder that assigns it — so a substring check passes
        while the player itself has been put back on the pasted link.
        """
        source = STUDENT_PAGE.read_text(encoding="utf-8")
        assert 'x-if="(questions[i]||{}).youtubeEmbed"' in source, (
            "the video block is not gated on the resolved embed"
        )
        assert 'x-if="(questions[i]||{}).audioSources.length"' in source, (
            "the audio block is not gated on there being a source to play"
        )

    def test_the_builder_warns_a_teacher_before_the_exam_is_sat(self):
        """Both previews have to be bound to the *resolved* value, not merely have
        a warning element somewhere in the file."""
        source = BUILDER_PAGE.read_text(encoding="utf-8")
        assert "data-media-unplayable" in source, (
            "the builder shows a preview for a link it cannot play and says nothing"
        )
        assert 'x-show="q.audio && (!sgExamMedia.audioSources(q.audio).length || q._mediaFailed)"' in source, (
            "the builder's audio warning is not bound to the resolved sources"
        )
        assert 'x-show="q.youtube && !sgExamMedia.youtubeEmbed(q.youtube)"' in source, (
            "the builder's video warning is not bound to the resolved embed"
        )

    def test_a_file_that_was_there_and_is_now_gone_is_reported_too(self):
        """The second half of the same problem, and the one a resolving URL hides.

        A private or deleted Drive file resolves to a perfectly good-looking URL
        that then refuses to load. `<audio>` prints nothing of its own when every
        source fails, so without this handler the pupil is left with a player that
        does not play and no sentence — which is the exact silent dead end these
        tests exist to keep out. Both pages bind the element's own `error` event.
        """
        student = STUDENT_PAGE.read_text(encoding="utf-8")
        assert '@error="mediaFailed[i] = true"' in student, (
            "the exam page never hears that the audio it offered failed to load"
        )
        assert "|| mediaFailed[i])" in student, (
            "the exam page hears the failure and does not act on it"
        )
        # The flag has to be *declared*: `mediaFailed[i] = true` on an undeclared
        # property throws inside Alpine's handler, so the note would never appear
        # and nothing in the source would look wrong.
        assert re.search(r"mediaFailed\s*:\s*\{\},?", student), (
            "the failure flag is written to but never declared in the component"
        )
        builder = BUILDER_PAGE.read_text(encoding="utf-8")
        assert '@error="q._mediaFailed = true"' in builder, (
            "the builder preview stays silent when the file it points at is gone"
        )
        assert "|| q._mediaFailed)" in builder, (
            "the builder hears the failure and does not act on it"
        )
        assert '@input="q._mediaFailed = false"' in builder, (
            "the warning cannot clear, so fixing the link still shows it as broken"
        )


class TestTheStudentPageIsGivenTheLinksAtAll:
    """The handover, rendered rather than grepped.

    `QUESTION_AUDIO` is written by Jinja from `exam.question_audio`. A route that
    stopped selecting that column, or a template edit that dropped it from the
    context, would leave every player on the page empty — and the template source
    would still read perfectly. So the page is rendered with media attached and
    asked whether the links reached it.
    """

    def _render(self, app, question_audio):
        from flask import g

        with app.test_request_context("/student/exams/e1"):
            g.user_id = "stu-1"
            g.user_name = "Murid Uji"
            g.user_role = "murid"
            g.tz_offset = 7
            g.show = {}
            return app.jinja_env.get_template("student/take_exam.html").render(
                exam={"id": "e1", "title": "T", "total_questions": 1,
                      "duration_minutes": 30, "question_types": {},
                      "question_audio": question_audio},
                anti_cheat_config="{\"anti_cheat_enabled\": true}",
                exam_started_at=None, recovery_code="", question_options={},
                deadline=None, deadline_reason="", seconds_left=1800, window_end=None,
                away_grace_seconds=30, away_grace_chances=2)

    def test_the_links_the_teacher_saved_reach_the_pupil_page(self, app):
        audio = f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing"
        video = f"https://youtu.be/{VID}?t=90"
        html = self._render(app, {"0": {"audio": audio, "youtube": video}})
        assert "QUESTION_AUDIO" in html, "the page no longer reads the media map"
        assert FILE_ID in html, (
            "the Drive link the teacher saved never reached the page, so no player "
            "on it could ever be told what to play"
        )
        assert VID in html, "the YouTube link never reached the page"

    def test_a_question_with_no_media_carries_nothing(self, app):
        """The other direction: an exam without media must not render a refusal.

        The refusal notes are bound to a *link* having been set, so an empty map has
        to stay empty — otherwise every ordinary question would carry a warning
        about media that was never asked for.
        """
        html = self._render(app, {})
        assert "data-media-refused" in html          # the markup is there, gated
        assert "QUESTION_AUDIO = {}" in html, (
            "a paper with no media does not hand the page an empty map"
        )


class TestTheQuestionObjectsCarryTheResolvedMedia:
    """The student's players read the question object, so this is the handover."""

    def test_the_exam_page_resolves_each_question_once(self):
        source = STUDENT_PAGE.read_text(encoding="utf-8")
        assert "sgExamMedia.audioSources(" in source and "sgExamMedia.youtubeEmbed(" in source, (
            "the exam page never asks the module for the sources it binds"
        )

    def test_the_builder_preview_uses_the_same_module(self):
        source = BUILDER_PAGE.read_text(encoding="utf-8")
        assert "sgExamMedia.youtubeEmbed(" in source and "sgExamMedia.audioSources(" in source, (
            "the builder preview resolves links by a different rule than the exam page"
        )
