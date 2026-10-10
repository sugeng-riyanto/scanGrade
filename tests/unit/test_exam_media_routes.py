"""The route half: who may fetch a question's media, and what may be uploaded.

Two routes, and each one is where a plausible implementation gives away the thing it
was built to protect:

* `GET /media/<token>` — the page's URL for one question's file. It has to refuse a
  session that is not the user the token was minted for, because the *point* of
  serving media through the app is that a URL copied out of DevTools is not a link a
  whole class can sit the paper off. It also has to refuse a token past its minutes
  and one whose payload was edited, and it must not become a 500 on either.
* `POST /teacher/exams/<id>/media` — the upload. It has to refuse a file nobody can
  play, one larger than we will serve, and a teacher who does not own the paper; and
  it must answer with the storage path (what gets saved) *and* an app URL (what the
  builder previews with), never a Storage URL.
"""
from __future__ import annotations

import io
import time
from types import SimpleNamespace

import pytest

from app.services import exam_media
from app.utils import auth as authmod


EXAM = "11111111-2222-3333-4444-555555555555"
OWNER = "teacher-1"
OTHER = "teacher-2"
PATH = f"{EXAM}/0/clip.mp3"


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeUpstream:
    def __init__(self, body: bytes = b"ID3-bytes"):
        self.status_code = 200
        self.headers = {"Content-Type": "audio/mpeg", "Content-Length": str(len(body))}
        self._body = body

    def iter_content(self, chunk_size=1):
        yield self._body

    def close(self):
        pass


class FakeQuery:
    def __init__(self, row):
        self._row = row

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def single(self):
        return self

    def maybe_single(self):
        # `_guard_exam` reads the paper with `maybe_single`; without it here the
        # fake would only be satisfied by an implementation that used `single`.
        return self

    def execute(self):
        return SimpleNamespace(data=self._row)


class FakeBucket:
    def __init__(self, log):
        self.log = log

    def upload(self, path, data, options):
        self.log.append(("upload", path, options))
        return {"path": path}


class FakeStorage:
    def __init__(self, log, existing=("exam-media",)):
        self.log = log
        self._existing = set(existing)

    def from_(self, name):
        return FakeBucket(self.log)

    def get_bucket(self, name):
        if name not in self._existing:
            raise Exception("not found")
        return {"name": name}

    def create_bucket(self, name, options):
        self.log.append(("create_bucket", name, options))
        self._existing.add(name)
        return {"name": name}


class FakeSupabase:
    def __init__(self, row, log=None):
        self._row = row
        self.log = log if log is not None else []
        self.storage = FakeStorage(self.log)

    def table(self, name):
        return FakeQuery(self._row)


def _login(monkeypatch, client, user_id: str, role: str = "guru",
           school_id: str = "school-1"):
    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": user_id, "email": f"{user_id}@x", "name": user_id, "role": role,
        "school_id": school_id, "status": "active",
    })
    # The audit trail is a write like any other; what these tests are about is the
    # upload and the token, so it is stubbed rather than left to reach a network.
    monkeypatch.setattr("app.routes.teacher.log_activity", lambda *a, **k: None,
                        raising=False)
    client.set_cookie("access_token", "tok")
    # Every write on this site carries the session's csrf token, and the upload is
    # a fetch from the builder's own page — so it has one too.
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-csrf-token"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "test-csrf-token"
    return client


# ── 1. the media route ──────────────────────────────────────────────────────

def test_media_without_a_session_is_a_login_redirect(app):
    from app.utils.auth import LOGIN_URL

    client = app.test_client()
    resp = client.get(f"/media/{'x' * 20}")
    assert resp.status_code == 302
    assert resp.headers["Location"].split("?")[0] == LOGIN_URL


def test_a_token_cannot_be_used_by_another_sitting(app, monkeypatch):
    """The property the whole change exists for: the URL in the page is worth nothing
    in someone else's browser."""
    with app.app_context():
        token = exam_media.sign_media_token(path=PATH, subject=OWNER, exam_id=EXAM)

    client = _login(monkeypatch, app.test_client(), user_id="someone-else")
    resp = client.get(f"/media/{token}")

    assert resp.status_code == 403, (
        "a pupil's signed media URL opened for another user — the leaked link the "
        "app-serving was meant to make worthless")


def test_the_sitting_it_was_minted_for_gets_the_bytes(app, monkeypatch):
    with app.app_context():
        token = exam_media.sign_media_token(path=PATH, subject=OWNER, exam_id=EXAM)
    monkeypatch.setattr(exam_media, "signed_download_url",
                        lambda supabase, path, ttl=None: f"https://storage.test/{path}")
    monkeypatch.setattr(exam_media, "open_media",
                        lambda url, range_header=None: FakeUpstream())
    monkeypatch.setattr("app.routes.media.get_supabase", lambda: object(), raising=False)
    monkeypatch.setattr("app.utils.auth.get_supabase", lambda: object(), raising=False)

    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.get(f"/media/{token}")

    assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
    assert resp.get_data() == b"ID3-bytes"
    # The app's own after_request appends its session policy to every authenticated
    # reply, so this is asserted as a property: *not* storable by a shared cache.
    cache = resp.headers["Cache-Control"]
    assert "no-store" in cache and "public" not in cache, (
        f"a signed, sitting-bound file was marked cacheable: {cache!r}")


def test_an_expired_token_is_a_refusal_not_a_500(app, monkeypatch):
    with app.app_context():
        token = exam_media.sign_media_token(path=PATH, subject=OWNER, exam_id=EXAM,
                                            ttl=60, now=1_700_000_000)

    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.get(f"/media/{token}")

    assert resp.status_code in (403, 410), (
        "an expired media token was not refused cleanly")


def test_an_edited_token_is_a_refusal_not_a_500(app, monkeypatch):
    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.get("/media/not.a.token")
    assert resp.status_code in (400, 403, 404), resp.status_code


def test_a_range_request_reaches_the_route_as_a_206(app, monkeypatch):
    with app.app_context():
        token = exam_media.sign_media_token(path=PATH, subject=OWNER, exam_id=EXAM)

    def fake_stream(supabase, path, range_header=None):
        assert range_header == "bytes=4-8", "the Range header was dropped by the route"
        return exam_media.stream_media(
            supabase, path, range_header=range_header)

    monkeypatch.setattr(exam_media, "signed_download_url",
                        lambda supabase, path, ttl=None: "https://storage.test/x")

    class Partial:
        status_code = 206
        headers = {"Content-Type": "audio/mpeg", "Content-Range": "bytes 4-8/100",
                   "Content-Length": "5"}

        def iter_content(self, chunk_size=1):
            yield b"bytes"

        def close(self):
            pass

    monkeypatch.setattr(exam_media, "open_media",
                        lambda url, range_header=None: Partial())
    monkeypatch.setattr("app.utils.auth.get_supabase", lambda: object(), raising=False)

    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.get(f"/media/{token}", headers={"Range": "bytes=4-8"})

    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == "bytes 4-8/100"


# ── 2. the upload ───────────────────────────────────────────────────────────

def test_a_teacher_uploads_a_file_and_gets_a_path_and_an_app_url(app, monkeypatch):
    log = []
    supabase = FakeSupabase({"id": EXAM, "teacher_id": OWNER}, log)
    monkeypatch.setattr("app.routes.teacher.get_supabase", lambda: supabase)

    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.post(
        f"/teacher/exams/{EXAM}/media",
        data={"kind": "audio", "file": (io.BytesIO(b"ID3" + b"\0" * 64), "clip.mp3")},
        content_type="multipart/form-data")

    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    entry = resp.get_json()
    assert entry["kind"] == "audio"
    assert entry["file"].startswith(f"{EXAM}/"), entry
    assert entry["url"].startswith("/media/"), (
        "the builder was handed something other than an app URL to preview with")
    assert entry["bytes"] == 67

    with app.app_context():
        claims = exam_media.verify_media_token(entry["url"].rsplit("/", 1)[-1])
    assert claims["p"] == entry["file"] and claims["s"] == OWNER and claims["x"] == EXAM

    uploads = [c for c in log if c[0] == "upload"]
    assert len(uploads) == 1, f"the file did not reach storage exactly once: {log}"
    assert uploads[0][2].get("content-type") == "audio/mpeg", (
        "the object was stored without the type a browser needs to play it")


def test_a_file_nobody_can_play_is_refused_with_a_sentence(app, monkeypatch):
    supabase = FakeSupabase({"id": EXAM, "teacher_id": OWNER})
    monkeypatch.setattr("app.routes.teacher.get_supabase", lambda: supabase)

    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.post(
        f"/teacher/exams/{EXAM}/media",
        data={"kind": "audio", "file": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data")

    assert resp.status_code == 422, resp.status_code
    body = resp.get_json()
    assert body.get("error"), "the refusal had no sentence for the teacher"


def test_an_oversized_upload_is_refused_before_it_is_sent_to_storage(app, monkeypatch):
    log = []
    supabase = FakeSupabase({"id": EXAM, "teacher_id": OWNER}, log)
    monkeypatch.setattr("app.routes.teacher.get_supabase", lambda: supabase)
    monkeypatch.setitem(exam_media.KINDS["audio"], "max_bytes", 16)

    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.post(
        f"/teacher/exams/{EXAM}/media",
        data={"kind": "audio", "file": (io.BytesIO(b"x" * 512), "clip.mp3")},
        content_type="multipart/form-data")

    assert resp.status_code == 413, resp.status_code
    assert not [c for c in log if c[0] == "upload"], (
        "an oversized file was uploaded to Storage before being refused")


def test_a_teacher_may_not_attach_media_to_someone_elses_paper(app, monkeypatch):
    supabase = FakeSupabase({"id": EXAM, "teacher_id": OTHER})
    monkeypatch.setattr("app.routes.teacher.get_supabase", lambda: supabase)

    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.post(
        f"/teacher/exams/{EXAM}/media",
        data={"kind": "audio", "file": (io.BytesIO(b"ID3"), "clip.mp3")},
        content_type="multipart/form-data")

    assert resp.status_code == 403, resp.status_code


def test_a_missing_file_is_refused(app, monkeypatch):
    supabase = FakeSupabase({"id": EXAM, "teacher_id": OWNER})
    monkeypatch.setattr("app.routes.teacher.get_supabase", lambda: supabase)

    client = _login(monkeypatch, app.test_client(), user_id=OWNER)
    resp = client.post(f"/teacher/exams/{EXAM}/media", data={"kind": "audio"},
                       content_type="multipart/form-data")
    assert resp.status_code == 400, resp.status_code


# ── 3. what a save keeps ────────────────────────────────────────────────────
#
# The save route reads the storage path back out of the form, so that path is the
# one field where a hand-written post could choose which object a paper points at.

SRC = "99999999-8888-7777-6666-555555555555"


def _form_media(app, data, view_args):
    from flask import request as flask_request
    from app.routes.teacher import _question_media_from_form

    with app.test_request_context(f"/teacher/exams/{EXAM}", method="POST", data=data):
        flask_request.view_args = view_args
        return _question_media_from_form(0)


def test_a_question_may_only_point_at_its_own_papers_prefix(app):
    """The prefix check is the whole reason the path is read from the form: without
    it, a posted path could point every pupil's page at another school's object."""
    assert _form_media(app, {"media_file_0": f"{SRC}/0/clip.mp3"},
                       {"exam_id": EXAM}) == {}, (
        "a question was allowed to point at another paper's stored object")

    media = _form_media(app, {"media_file_0": f"{EXAM}/0/clip.mp3",
                              "media_kind_0": "video",
                              "media_name_0": "clip.mp3"},
                        {"exam_id": EXAM})
    # `plays` is the play allowance the media-plays phase added, and a record always
    # carries it (default 1). This guard is about the path prefix, so it names the
    # whole record rather than loosening to a subset.
    assert media == {"file": f"{EXAM}/0/clip.mp3", "kind": "video",
                     "name": "clip.mp3", "plays": 1}


def test_a_paper_that_does_not_exist_yet_cannot_attach_a_file(app):
    """`/exams/new` has no exam id, so there is no prefix for a path to be checked
    against — and a caller who could choose the prefix could choose the object."""
    assert _form_media(app, {"media_file_0": f"{EXAM}/0/clip.mp3"}, {}) == {}


def test_a_pasted_link_is_still_read_exactly_as_it_was(app):
    drive = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrSt/view"
    youtube = "https://youtu.be/dQw4w9WgXcQ"
    # The links themselves are untouched; the record also carries the play allowance
    # (default 1) the media-plays phase added.
    assert _form_media(app, {"audio_0": drive, "youtube_0": youtube},
                       {"exam_id": EXAM}) == {"audio": drive, "youtube": youtube,
                                              "plays": 1}


def test_duplicating_a_paper_moves_its_uploaded_media_with_it(app):
    """A copy starts out pointing at the original's paths. Playback would work, but
    the copy's *next save* refuses those paths — so the objects move at duplication
    time, and a file that cannot be moved is dropped rather than half-pointed."""
    from app.routes.teacher import _relocate_question_media

    copies = []

    class FakeStorage:
        def copy(self, source, target):
            copies.append((source, target))
            return {"path": target}

    class RefusingStorage:
        def copy(self, source, target):
            raise Exception("storage refused")

    supabase = SimpleNamespace(storage=SimpleNamespace(from_=lambda name: FakeStorage()))
    stored = {"0": {"file": f"{SRC}/0/clip.mp3", "kind": "audio"},
              "1": {"audio": "https://drive.google.com/file/d/X/view"}}

    with app.app_context():
        out = _relocate_question_media(supabase, SRC, EXAM, stored)

    assert copies == [(f"{SRC}/0/clip.mp3", f"{EXAM}/0/clip.mp3")]
    assert out["0"]["file"] == f"{EXAM}/0/clip.mp3"
    assert out["0"]["kind"] == "audio"
    assert out["1"] == stored["1"], "a pasted link was rewritten by the move"
    assert stored["0"]["file"] == f"{SRC}/0/clip.mp3", (
        "the source row was mutated in place")

    with app.app_context():
        refused = _relocate_question_media(
            SimpleNamespace(storage=SimpleNamespace(from_=lambda n: RefusingStorage())),
            SRC, EXAM, stored)
    assert "0" not in refused, (
        "a file that could not be copied was left pointing at the original")
    assert refused["1"] == stored["1"]
