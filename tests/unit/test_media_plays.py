"""Question media is playable a limited number of times, and the server counts.

A paper's audio, YouTube embed or uploaded video is material a candidate studies
while they answer — a listening passage is often meant to be heard once. Nothing
enforced that: the exam page drew a player with no limit, so the media could be
replayed for the whole sitting and a refresh reset whatever the page had counted.

This is `app/services/media_plays.py`. Three facts fix the design:

* **The limit is per question and the teacher sets it** — a dropdown whose default
  is one play; `0` is the "Unlimited" branch, the same word the duration field uses.
* **The count lives on the server, per sitting** — one `media_play` event per charge
  against the attempt, so a refresh, a second tab or another device cannot reset it.
* **A write that cannot be recorded fails open** — the same rule the audit trail
  follows everywhere else: a database hiccup must never trap a pupil mid-exam.

The page turns the count into a locked player; the limit itself is the server's.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "media_plays.py"
TEACHER = ROOT / "app" / "routes" / "teacher.py"
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
EXAM = ROOT / "app" / "templates" / "student" / "take_exam.html"
STUDENT_ROUTES = ROOT / "app" / "routes" / "student.py"


# ── a PostgREST stand-in for the read side ──────────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data or [])


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, columns="*", **kw):
        self._select = columns
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _match(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self._filters)

    def execute(self):
        self.db.log.append((self._op, self.table, dict(self._payload or {}), list(self._filters)))
        if self._op == "insert":
            self.db.tables.setdefault(self.table, []).append(dict(self._payload))
            return _Res([dict(self._payload)])
        if self.db.break_table == self.table:
            raise RuntimeError("connection reset")
        return _Res([dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)])


class _DB:
    def __init__(self, break_table=None, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.log = []
        self.break_table = break_table

    def table(self, name):
        return _Query(self, name)


def _db(**over):
    tables = dict(attempt_session_events=[])
    tables.update(over)
    return _DB(**tables)


def _event(qi, kind="media_play"):
    return {"id": f"e{qi}", "attempt_id": "att-1", "seq": 1, "kind": kind,
            "question_index": qi}


# ── the limit, and what an unset one means ──────────────────────────────────

class TestTheLimit:
    def test_a_question_without_a_setting_allows_one_play(self):
        from app.services import media_plays
        assert media_plays.limit_for({}) == 1
        assert media_plays.limit_for(None) == 1
        assert media_plays.limit_for("not a dict") == 1

    def test_a_stored_number_is_read(self):
        from app.services import media_plays
        assert media_plays.limit_for({"plays": 3}) == 3
        assert media_plays.limit_for({"plays": "5"}) == 5

    def test_zero_is_unlimited(self):
        from app.services import media_plays
        assert media_plays.limit_for({"plays": 0}) == media_plays.UNLIMITED

    def test_a_value_outside_the_menu_falls_back_not_up(self):
        """A hand-posted `999` or a negative must not become a bigger allowance."""
        from app.services import media_plays
        assert media_plays.limit_for({"plays": 999}) == media_plays.DEFAULT_LIMIT
        assert media_plays.limit_for({"plays": -4}) == media_plays.DEFAULT_LIMIT
        assert media_plays.limit_for({"plays": "banyak"}) == media_plays.DEFAULT_LIMIT

    def test_the_menu_offers_one_by_default_and_an_unlimited_branch(self):
        from app.services import media_plays
        assert media_plays.DEFAULT_LIMIT == 1
        assert media_plays.UNLIMITED in media_plays.LIMIT_CHOICES
        assert 1 in media_plays.LIMIT_CHOICES


# ── the count, read from the sitting's own events ───────────────────────────

class TestTheCount:
    def test_it_counts_plays_per_question(self):
        from app.services import media_plays
        db = _db(attempt_session_events=[_event(0), _event(0), _event(2)])
        assert media_plays.used_by_question(db, "att-1") == {0: 2, 2: 1}

    def test_it_ignores_other_kinds(self):
        from app.services import media_plays
        db = _db(attempt_session_events=[_event(1, kind="connection_gap"), _event(1)])
        assert media_plays.used_by_question(db, "att-1") == {1: 1}

    def test_a_row_with_no_question_is_not_counted(self):
        from app.services import media_plays
        db = _db(attempt_session_events=[{"id": "x", "attempt_id": "att-1",
                                          "kind": "media_play", "question_index": None}])
        assert media_plays.used_by_question(db, "att-1") == {}

    def test_a_failed_read_is_no_plays_rather_than_an_error(self):
        from app.services import media_plays
        db = _DB(break_table="attempt_session_events", attempt_session_events=[])
        assert media_plays.used_by_question(db, "att-1") == {}


# ── charging one play ───────────────────────────────────────────────────────

class TestRecordPlay:
    def test_a_play_under_the_limit_is_allowed_and_counted(self, monkeypatch):
        from app.services import media_plays, attempt_status
        written = []
        monkeypatch.setattr(attempt_status, "record_event",
                            lambda *a, **k: written.append((a, k)) or True, raising=False)
        db = _db(attempt_session_events=[])
        out = media_plays.record_play(db, "att-1", 0, 3)
        assert out["allowed"] is True
        assert out["used"] == 1 and out["remaining"] == 2
        assert written, "an allowed play left no event"

    def test_the_last_allowed_play_leaves_nothing(self, monkeypatch):
        from app.services import media_plays, attempt_status
        monkeypatch.setattr(attempt_status, "record_event", lambda *a, **k: True, raising=False)
        db = _db(attempt_session_events=[_event(0), _event(0)])
        out = media_plays.record_play(db, "att-1", 0, 3)
        assert out["allowed"] is True and out["remaining"] == 0

    def test_a_play_past_the_limit_is_refused_and_writes_nothing(self, monkeypatch):
        from app.services import media_plays, attempt_status
        written = []
        monkeypatch.setattr(attempt_status, "record_event",
                            lambda *a, **k: written.append(1) or True, raising=False)
        db = _db(attempt_session_events=[_event(0), _event(0), _event(0)])
        out = media_plays.record_play(db, "att-1", 0, 3)
        assert out["allowed"] is False and out["remaining"] == 0
        assert written == [], "a refused play was still charged"

    def test_unlimited_never_refuses(self, monkeypatch):
        from app.services import media_plays, attempt_status
        monkeypatch.setattr(attempt_status, "record_event", lambda *a, **k: True, raising=False)
        db = _db(attempt_session_events=[_event(0) for _ in range(50)])
        out = media_plays.record_play(db, "att-1", 0, media_plays.UNLIMITED)
        assert out["allowed"] is True
        assert out["remaining"] is None, "unlimited must not report a number left"

    def test_a_write_that_fails_fails_open(self, monkeypatch):
        """A database hiccup must not trap a pupil mid-exam."""
        from app.services import media_plays, attempt_status
        monkeypatch.setattr(attempt_status, "record_event", lambda *a, **k: False, raising=False)
        out = media_plays.record_play(_db(), "att-1", 0, 1)
        assert out["allowed"] is True

    def test_the_limit_is_never_read_from_argument_as_a_string(self, monkeypatch):
        from app.services import media_plays, attempt_status
        monkeypatch.setattr(attempt_status, "record_event", lambda *a, **k: True, raising=False)
        db = _db(attempt_session_events=[_event(0)])
        out = media_plays.record_play(db, "att-1", 0, "2")
        assert out["allowed"] is True and out["limit"] == 2


# ── the teacher's field ─────────────────────────────────────────────────────

class TestTheTeacherField:
    def test_the_builder_offers_a_play_attempt_dropdown(self):
        html = FORM.read_text(encoding="utf-8")
        assert "media_plays_" in html, "the builder has no play-attempt field"
        assert re.search(r"name=\"'media_plays_' \+ i\"|:name=\"'media_plays_' \+ i\"", html), (
            "the play-attempt field is not bound to the question index")

    def test_the_route_stores_the_limit_beside_the_media(self):
        body = TEACHER.read_text(encoding="utf-8").split("def _question_media_from_form", 1)[1]
        body = body.split("\ndef ", 1)[0]
        assert "media_plays_" in body, (
            "the save route drops the play-attempt field, so no question can be limited")
        assert "limit_for" in body, (
            "the save route does not go through the service's own clamp")


# ── the pupil's page ────────────────────────────────────────────────────────

class TestThePupilPage:
    def test_the_page_is_told_the_used_counts(self):
        html = EXAM.read_text(encoding="utf-8")
        assert "MEDIA_USED" in html, (
            "the exam page never learns how many plays are already spent, so a "
            "refresh hands back the full allowance")

    def test_the_page_has_a_guard_and_a_locked_state(self):
        html = EXAM.read_text(encoding="utf-8")
        assert "sgMediaPlay" in html, "the page draws no play limit guard"
        assert "mediaLocked" in html, (
            "the page has no locked state for a spent allowance")

    def test_the_youtube_embed_can_be_maximised_and_restored(self):
        html = EXAM.read_text(encoding="utf-8")
        assert re.search(r"mediaMax|expandMedia|toggleMax", html), (
            "the YouTube embed cannot be enlarged")

    def test_the_route_passes_the_used_counts(self):
        src = STUDENT_ROUTES.read_text(encoding="utf-8")
        assert "used_by_question" in src, (
            "the exam route never reads the spent plays for the sitting")
        assert "media_used=media_used" in src, (
            "the spent plays are read but never handed to the page")


# ── the door that charges ───────────────────────────────────────────────────

class TestTheRoute:
    def _route(self):
        src = STUDENT_ROUTES.read_text(encoding="utf-8")
        at = src.index('@student_bp.route("/media-play')
        nxt = src.find("bp.route(", at + 10)
        end = src.rfind("\n@", at, nxt) if nxt != -1 else len(src)
        return src[at:end if end != -1 else len(src)]

    def test_it_exists_and_is_a_logged_in_post(self):
        block = self._route()
        assert 'methods=["POST"]' in block
        assert "login_required" in block, "the media-play door is not guarded"

    def test_it_calls_the_one_service(self):
        assert "media_plays" in self._route(), (
            "the route re-implements the counting instead of calling the service")

    def test_the_limit_is_never_taken_from_the_request(self):
        block = self._route()
        for stolen in ('body.get("limit")', 'request.form.get("limit")',
                       'body.get("plays")', 'request.form.get("plays")'):
            assert stolen not in block, (
                "the play allowance is read from the request, so a pupil can raise it")
