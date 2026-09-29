"""Question media served by us, on a link that dies with the sitting.

A question's media was two pasted URLs — a Drive link and a YouTube link — resolved in
the pupil's browser by `app/static/js/exam-media.js`. That arrangement has three
properties nobody chose: the file lives somewhere we cannot see (a Drive file can be
made private, moved or deleted after the paper is built), the bytes come from a third
party, and the link a pupil can open in DevTools is a *shareable* URL — a whole class
can sit the same paper off one pupil's screen.

What replaces it is a file uploaded to our own private bucket, handed to the page as a
URL that only that sitting can use:

* the page gets `/media/<token>`, and the token is a **signed claim** over the storage
  path, the user it was minted for, and an expiry — so a leaked URL is refused on the
  next session's cookie, and refused again once its minutes are up;
* the bytes never leave our stack through a public URL: the app mints a *short-lived
  signed URL* for its own request to Storage and streams the result, so the link in
  the page is never a link to Storage.

This module is the service half, and its tests are pure: no Storage, no network, no
request. The three properties worth pinning are the ones a plausible-looking
implementation gets wrong — a token that survives its expiry, a token that does not
name the user it was minted for, and an upload accepted for a file nobody can play.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from app.services import exam_media


EXAM = "11111111-2222-3333-4444-555555555555"
STUDENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PATH = f"{EXAM}/3/clip.mp3"


# ── 1. the token ────────────────────────────────────────────────────────────

def test_a_token_round_trips_and_names_what_it_was_minted_for(app):
    with app.app_context():
        token = exam_media.sign_media_token(path=PATH, subject=STUDENT, exam_id=EXAM)
        claims = exam_media.verify_media_token(token)

    assert claims is not None, "a token this module minted did not verify"
    assert claims["p"] == PATH, "the token does not carry the path it was minted for"
    assert claims["s"] == STUDENT, "the token does not name the user it was minted for"
    assert claims["x"] == EXAM, "the token is not bound to the paper"
    assert claims["e"] > 0


def test_a_token_does_not_outlive_its_minutes(app):
    """The whole point of the expiry: a URL that leaks mid-sitting is worthless after
    it, so what is leaked is one question's audio and not the paper's."""
    with app.app_context():
        token = exam_media.sign_media_token(path=PATH, subject=STUDENT, exam_id=EXAM,
                                            ttl=60, now=1_700_000_000)
        early = exam_media.verify_media_token(token, now=1_700_000_030)
        late = exam_media.verify_media_token(token, now=1_700_000_061)

    assert early is not None, "a token was refused inside its own lifetime"
    assert late is None, "a token outlived its expiry"


def test_a_tampered_token_verifies_to_nothing_rather_than_someone_elses_file(app):
    """Forging one has to be as hard as guessing the secret: the path is *payload*,
    and payload a caller can edit is a caller choosing which file to fetch."""
    with app.app_context():
        token = exam_media.sign_media_token(path=PATH, subject=STUDENT, exam_id=EXAM)
        head, _, tail = token.partition(".")
        forged = exam_media.b64(json.dumps(
            {"p": "someone-elses-exam/0/secret.mp3", "s": STUDENT, "x": EXAM,
             "e": 9_999_999_999}).encode()).decode() + "." + tail

        assert exam_media.verify_media_token(token) is not None
        assert exam_media.verify_media_token(forged) is None, (
            "an edited payload still verified — the signature does not cover it")
        assert exam_media.verify_media_token(head + "." + exam_media.b64(b"nope").decode()) is None


@pytest.mark.parametrize("junk", ["", ".", "not-a-token", "a.b.c", "..", "aaa."])
def test_junk_verifies_to_nothing_and_never_raises(app, junk):
    """This is reached straight off a URL, so every shape has to be a refusal."""
    with app.app_context():
        assert exam_media.verify_media_token(junk) is None


def test_a_different_secret_signs_a_different_token(app):
    """The secret is the app's own; a token from another deployment is not a token."""
    with app.app_context():
        token = exam_media.sign_media_token(path=PATH, subject=STUDENT, exam_id=EXAM)
        original = app.config["SECRET_KEY"]
        app.config["SECRET_KEY"] = original + "-rotated"
        try:
            assert exam_media.verify_media_token(token) is None, (
                "a token minted under the old secret still verified after rotation")
        finally:
            app.config["SECRET_KEY"] = original


def test_a_missing_secret_is_an_error_rather_than_a_token_anyone_can_forge(app):
    with app.app_context():
        original = app.config.get("SECRET_KEY")
        app.config["SECRET_KEY"] = ""
        try:
            with pytest.raises(RuntimeError):
                exam_media.sign_media_token(path=PATH, subject=STUDENT, exam_id=EXAM)
            assert exam_media.verify_media_token("y.z") is None
        finally:
            app.config["SECRET_KEY"] = original


# ── 2. what the page is handed ──────────────────────────────────────────────

def test_a_hosted_file_gets_a_url_and_a_pasted_link_is_left_exactly_as_it_was(app):
    """The legacy shape has to survive untouched: papers written before this change
    still paste a Drive or YouTube link, and those questions must keep playing."""
    stored = {
        "0": {"file": PATH, "name": "clip.mp3", "kind": "audio", "bytes": 1234},
        "1": {"audio": "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrSt/view"},
        "2": {"youtube": "https://youtu.be/dQw4w9WgXcQ"},
    }
    snapshot = json.loads(json.dumps(stored))

    with app.app_context():
        handed = exam_media.with_media_urls(stored, subject=STUDENT, exam_id=EXAM)
        token = handed["0"]["url"].rsplit("/", 1)[-1]
        claims = exam_media.verify_media_token(token)

    assert stored == snapshot, "the stored media was mutated in place"
    assert handed["0"]["url"].startswith("/media/"), (
        "a hosted file was not handed to the page as an app URL")
    assert claims["p"] == PATH and claims["s"] == STUDENT and claims["x"] == EXAM
    assert handed["1"] == stored["1"], "a pasted Drive link was rewritten or dropped"
    assert handed["2"] == stored["2"], "a pasted YouTube link was rewritten or dropped"
    assert "url" not in handed["1"] and "url" not in handed["2"]


def test_a_string_shaped_legacy_entry_is_left_alone(app):
    """`QUESTION_AUDIO[i]` has been a bare string in older papers; the page still
    reads that shape, so the service must not crash on it."""
    with app.app_context():
        handed = exam_media.with_media_urls({"0": "https://drive.google.com/file/d/XYZ/view"},
                                            subject=STUDENT, exam_id=EXAM)
    assert handed == {"0": "https://drive.google.com/file/d/XYZ/view"}


def test_every_hosted_question_in_one_paper_gets_its_own_token(app):
    with app.app_context():
        handed = exam_media.with_media_urls(
            {"0": {"file": f"{EXAM}/0/a.mp3"}, "1": {"file": f"{EXAM}/1/b.mp4"}},
            subject=STUDENT, exam_id=EXAM)
        first = exam_media.verify_media_token(handed["0"]["url"].rsplit("/", 1)[-1])
        second = exam_media.verify_media_token(handed["1"]["url"].rsplit("/", 1)[-1])

    assert first["p"] != second["p"], "two questions share one token"


# ── 3. what may be uploaded ─────────────────────────────────────────────────

def test_an_audio_upload_is_checked_for_shape_and_for_size():
    assert exam_media.validate_media_upload("audio", "clip.mp3", "audio/mpeg", 1000) is None
    assert exam_media.validate_media_upload("audio", "clip.ogg", "audio/ogg", 1000) is None

    assert exam_media.validate_media_upload("audio", "clip.txt", "text/plain", 10) is not None
    assert exam_media.validate_media_upload("audio", "clip.mp3", "audio/mpeg",
                                            exam_media.KINDS["audio"]["max_bytes"] + 1) is not None
    assert exam_media.validate_media_upload("audio", "", "audio/mpeg", 10) is not None, (
        "a nameless upload was accepted — there is no extension to judge it by")


def test_a_video_upload_has_its_own_gate_and_its_own_ceiling():
    assert exam_media.validate_media_upload("video", "clip.mp4", "video/mp4", 1000) is None
    assert exam_media.validate_media_upload("video", "clip.mp3", "audio/mpeg", 1000) is not None
    assert exam_media.validate_media_upload("video", "clip.mp4", "video/mp4",
                                            exam_media.KINDS["video"]["max_bytes"] + 1) is not None


def test_the_uploads_ceiling_cannot_exceed_what_the_app_lets_through(app):
    """Flask refuses a request body above `MAX_CONTENT_LENGTH` before any handler
    runs, so a ceiling above it is a promise the upload route cannot keep — the
    teacher sees a 413 with no sentence from us."""
    ceiling = app.config["MAX_CONTENT_LENGTH"]
    assert exam_media.KINDS["video"]["max_bytes"] < ceiling, (
        "a video ceiling at or above MAX_CONTENT_LENGTH would fail as a bare 413")


def test_an_unknown_kind_is_refused():
    assert exam_media.validate_media_upload("hologram", "x.mp4", "video/mp4", 10) is not None


def test_the_storage_path_is_scoped_and_a_filename_cannot_escape_it():
    path = exam_media.media_storage_path(EXAM, 3, "My Clip (final).mp3")
    assert path.startswith(f"{EXAM}/"), "the file is not scoped to its paper"
    assert path.endswith(".mp3")
    assert " " not in path and "(" not in path, f"the path is not URL-safe: {path!r}"

    escaped = exam_media.media_storage_path(EXAM, 3, "../../../etc/passwd.mp3")
    assert ".." not in escaped, f"a filename escaped the paper's prefix: {escaped!r}"
    assert escaped.startswith(f"{EXAM}/")


# ── 4. the bytes ────────────────────────────────────────────────────────────

class _Upstream:
    """What `requests.get(..., stream=True)` hands back, cut down to what we read."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "audio/mpeg",
                                   "Content-Length": str(len(body))}
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start:start + chunk_size]

    def close(self):
        self.closed = True


def _patch_upstream(monkeypatch, upstream):
    monkeypatch.setattr(exam_media, "signed_download_url",
                        lambda supabase, path, ttl=None: f"https://storage.test/{path}")
    monkeypatch.setattr(exam_media, "open_media",
                        lambda url, range_header=None: upstream)


def test_the_route_streams_what_storage_returns(app, monkeypatch):
    upstream = _Upstream(b"ID3-not-really-mp3")
    _patch_upstream(monkeypatch, upstream)

    with app.app_context():
        resp = exam_media.stream_media(object(), PATH)

    assert resp.status_code == 200
    assert resp.headers["Accept-Ranges"] == "bytes", (
        "without this a browser will not let a pupil seek inside a track")
    assert resp.headers["Content-Type"] == "audio/mpeg"
    assert resp.headers["Cache-Control"] == "private, no-store", (
        "a signed, sitting-bound file must not sit in a shared cache")
    assert b"".join(resp.response) == b"ID3-not-really-mp3"


def test_a_range_request_is_passed_through_as_a_206(app, monkeypatch):
    upstream = _Upstream(b"partial", status=206,
                         headers={"Content-Type": "audio/mpeg",
                                  "Content-Range": "bytes 100-106/9000",
                                  "Content-Length": "7"})
    _patch_upstream(monkeypatch, upstream)

    with app.app_context():
        resp = exam_media.stream_media(object(), PATH, range_header="bytes=100-106")

    assert resp.status_code == 206, "a range request was answered with the whole file"
    assert resp.headers["Content-Range"] == "bytes 100-106/9000"
    assert resp.headers["Accept-Ranges"] == "bytes"


def test_the_signed_url_is_minted_for_our_own_request_only(app, monkeypatch):
    """The page must never be handed a Storage URL: that is the link that can be
    shared, which is the arrangement this replaces."""
    seen = {}

    class FakeStorage:
        def create_signed_url(self, path, ttl):
            seen["path"], seen["ttl"] = path, ttl
            return {"signedURL": f"/object/sign/{path}?token=abc"}

    class FakeBucket:
        def create_signed_url(self, path, ttl):
            return FakeStorage().create_signed_url(path, ttl)

    supabase = SimpleNamespace(storage=SimpleNamespace(from_=lambda name: FakeBucket()))

    with app.app_context():
        url = exam_media.signed_download_url(supabase, PATH)

    assert seen["path"] == PATH
    assert seen["ttl"] == exam_media.SIGNED_URL_TTL_SECONDS
    assert seen["ttl"] <= 300, "the Storage link is not short-lived"
    assert url.endswith(f"/object/sign/{PATH}?token=abc")
    assert url.startswith("http"), "a relative Storage URL is not fetchable"


def test_an_absolute_signed_url_from_the_client_is_used_as_it_comes(app):
    """The client's reply shape has moved before (`signedURL` / `signedUrl`), and a
    URL that is already absolute must not be prefixed twice."""
    class FakeBucket:
        def create_signed_url(self, path, ttl):
            return {"signedUrl": f"https://project.supabase.co/storage/v1/object/sign/{path}?t=1"}

    supabase = SimpleNamespace(storage=SimpleNamespace(from_=lambda name: FakeBucket()))

    with app.app_context():
        url = exam_media.signed_download_url(supabase, PATH)
    assert url == f"https://project.supabase.co/storage/v1/object/sign/{PATH}?t=1"


def test_a_range_header_reaches_storage(app, monkeypatch):
    captured = {}

    def fake_get(url, headers=None, stream=None, timeout=None):
        captured["headers"] = headers or {}
        captured["stream"] = stream
        return _Upstream(b"bytes")

    monkeypatch.setattr(exam_media.requests, "get", fake_get)

    with app.app_context():
        exam_media.open_media("https://storage.test/x", range_header="bytes=0-9")

    assert captured["headers"]["Range"] == "bytes=0-9", (
        "the Range header never reached Storage, so seeking would re-download")
    assert captured["stream"] is True, "the upstream body was buffered in memory"
