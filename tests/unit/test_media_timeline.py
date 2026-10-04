"""A replayed passage is a transition in the record, like a dropped connection.

`media_plays` charges a play against `attempt_session_events`, so a proctor could in
principle count the plays — but the *timeline* a staff reader actually opens knew
nothing about media. It labelled the connection and lifecycle transitions and rendered
a `media_play` row as a raw machine name with no question and no detail, so "the pupil
played the passage three times" was arithmetic nobody rendered, and a *pause* or the
*moment the allowance ran out* were never recorded at all.

This closes that: the play, the pause and the limit-reached moment are all transitions
in the same trail, and the timeline names each one with its question and its play
number.

Three rules keep it honest
--------------------------
**A description is not a verdict.** A pause is a pause and a limit is a limit. None of
these labels says why, and none is turned into a lock — the same rule every other
timeline label follows.

**The limit moment is derived on the server, never reported by the page.** It is the
charge that spends the last allowance — `remaining == 0` — so a pupil cannot invent one
by claiming it, and it cannot go missing because a page forgot to send it.

**A pause only describes.** Recording it charges nothing, so a pupil who fiddles with
the player cannot spend their own allowance by pausing.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "media_plays.py"
TIMELINE = ROOT / "app" / "services" / "attempt_timeline.py"
STUDENT_ROUTES = ROOT / "app" / "routes" / "student.py"
EXAM = ROOT / "app" / "templates" / "student" / "take_exam.html"


# ── a PostgREST stand-in, the same shape the media suite uses ───────────────

class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data or [])


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self._filters, self._op, self._payload = [], "select", None

    def select(self, columns="*", **kw):
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", dict(payload)
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
        if self._op == "insert":
            self.db.tables.setdefault(self.table, []).append(dict(self._payload))
            return _Res([dict(self._payload)])
        return _Res([dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)])


class _DB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def table(self, name):
        return _Query(self, name)


def _db(used=0):
    rows = [{"id": f"e{i}", "attempt_id": "att-1", "seq": i + 1, "kind": "media_play",
             "question_index": 0} for i in range(used)]
    return _DB(attempt_session_events=rows)


def _capture(monkeypatch):
    """Stand in the one writer, and keep every (kind, question_index) it got."""
    from app.services import attempt_status
    seen = []

    def record(supabase, attempt_id, kind, **kw):
        seen.append({"kind": kind, "question_index": kw.get("question_index"),
                     "meta": kw.get("meta") or {}})
        return True

    monkeypatch.setattr(attempt_status, "record_event", record, raising=False)
    return seen


# ── the labels ──────────────────────────────────────────────────────────────

class TestTheLabels:
    def test_every_media_kind_has_a_bilingual_timeline_label(self):
        from app.services import media_plays, attempt_timeline
        assert getattr(media_plays, "KINDS", ()), "media_plays names no kinds to label"
        for kind in media_plays.KINDS:
            assert kind in attempt_timeline.EVENT_LABELS, (
                f"{kind} is recorded but the timeline has no label for it, so a "
                f"pupil's media transition renders as a raw machine name")
            label = attempt_timeline.EVENT_LABELS[kind]
            assert label.get("id") and label.get("en"), f"{kind} is not bilingual"

    def test_a_media_label_is_not_a_verdict(self):
        from app.services import media_plays, attempt_timeline
        words = " ".join(
            attempt_timeline.EVENT_LABELS[k]["id"] + " " + attempt_timeline.EVENT_LABELS[k]["en"]
            for k in media_plays.KINDS).lower()
        for judgement in ("curang", "mencurigakan", "cheat", "suspicious"):
            assert judgement not in words


# ── the limit moment, derived on the server ─────────────────────────────────

class TestTheLimitMoment:
    def test_the_play_that_spends_the_last_allowance_is_marked(self, monkeypatch):
        from app.services import media_plays
        seen = _capture(monkeypatch)
        media_plays.record_play(_db(used=1), "att-1", 0, 2)   # second of two
        kinds = [s["kind"] for s in seen]
        assert "media_play" in kinds, "the charge itself was not recorded"
        assert "media_limit_reached" in kinds, (
            "the allowance ran out on this play and the timeline was not told")

    def test_a_play_that_leaves_an_allowance_records_no_limit_moment(self, monkeypatch):
        from app.services import media_plays
        seen = _capture(monkeypatch)
        media_plays.record_play(_db(used=0), "att-1", 0, 3)
        assert [s["kind"] for s in seen] == ["media_play"]

    def test_unlimited_never_records_a_limit_moment(self, monkeypatch):
        from app.services import media_plays
        seen = _capture(monkeypatch)
        media_plays.record_play(_db(used=9), "att-1", 0, media_plays.UNLIMITED)
        assert "media_limit_reached" not in [s["kind"] for s in seen]

    def test_the_limit_moment_carries_the_question_it_belongs_to(self, monkeypatch):
        from app.services import media_plays
        seen = _capture(monkeypatch)
        media_plays.record_play(_db(used=0), "att-1", 4, 1)    # last of one
        limit = next(s for s in seen if s["kind"] == "media_limit_reached")
        assert limit["question_index"] == 4, (
            "the limit moment does not say which question's media ran out")

    def test_a_refused_play_records_nothing(self, monkeypatch):
        from app.services import media_plays
        seen = _capture(monkeypatch)
        media_plays.record_play(_db(used=2), "att-1", 0, 2)    # already spent
        assert seen == [], "a refused play was still written to the trail"


# ── the pause, an observation that charges nothing ──────────────────────────

class TestThePause:
    def test_a_pause_is_recorded_under_its_own_kind(self, monkeypatch):
        from app.services import media_plays
        seen = _capture(monkeypatch)
        media_plays.record_pause(_db(), "att-1", 2)
        assert len(seen) == 1
        assert seen[0]["kind"] == "media_pause"
        assert seen[0]["question_index"] == 2, "the pause does not name its question"

    def test_a_pause_never_charges_a_play(self, monkeypatch):
        from app.services import media_plays
        seen = _capture(monkeypatch)
        media_plays.record_pause(_db(), "att-1", 0)
        assert "media_play" not in [s["kind"] for s in seen], (
            "pausing spent a play, so a pupil could exhaust their own allowance")

    def test_a_pause_write_that_fails_does_not_raise(self, monkeypatch):
        from app.services import media_plays, attempt_status
        monkeypatch.setattr(attempt_status, "record_event", lambda *a, **k: False, raising=False)
        assert media_plays.record_pause(_db(), "att-1", 0) is False

    def test_a_missing_attempt_records_nothing(self, monkeypatch):
        from app.services import media_plays
        seen = _capture(monkeypatch)
        assert media_plays.record_pause(_db(), None, 0) is False
        assert seen == []


# ── the timeline names each media transition ────────────────────────────────

class TestTheTimelineDetail:
    def test_a_play_names_the_question_and_the_play_number(self):
        from app.services import attempt_timeline
        row = {"kind": "media_play", "question_index": 4,
               "meta": {"limit": 3, "play": 2},
               "server_at": "2026-10-04T08:05:00Z"}
        entry = attempt_timeline.entry(row)
        assert "4" in entry["detail"], "a play does not say which question it was"
        assert "2" in entry["detail"] and "3" in entry["detail"], (
            "a play does not say which play of how many it was")
        assert entry["detail"] == attempt_timeline.entry(row, "id")["detail"]

    def test_the_detail_speaks_the_reader_language(self):
        from app.services import attempt_timeline
        row = {"kind": "media_play", "question_index": 4,
               "meta": {"limit": 3, "play": 2}, "server_at": "2026-10-04T08:05:00Z"}
        assert "Soal" in attempt_timeline.entry(row, "id")["detail"]
        assert "Question" in attempt_timeline.entry(row, "en")["detail"]

    def test_a_pause_names_its_question(self):
        from app.services import attempt_timeline
        row = {"kind": "media_pause", "question_index": 7, "meta": {},
               "server_at": "2026-10-04T08:06:00Z"}
        assert "7" in attempt_timeline.entry(row)["detail"]

    def test_a_limit_moment_names_its_question(self):
        from app.services import attempt_timeline
        row = {"kind": "media_limit_reached", "question_index": 7,
               "meta": {"limit": 1}, "server_at": "2026-10-04T08:07:00Z"}
        assert "7" in attempt_timeline.entry(row)["detail"]

    def test_a_media_row_still_carries_its_clock(self):
        from app.services import attempt_timeline
        row = {"kind": "media_play", "question_index": 1, "meta": {},
               "server_at": "2026-10-04T08:05:00Z"}
        assert attempt_timeline.entry(row)["at"] == "08:05"


# ── the door the page reports a pause through ───────────────────────────────

class TestThePauseRoute:
    def _route(self) -> str:
        src = STUDENT_ROUTES.read_text(encoding="utf-8")
        at = src.index('@student_bp.route("/media-pause"')
        nxt = src.find("bp.route(", at + 10)
        end = src.rfind("\n@", at, nxt) if nxt != -1 else len(src)
        return src[at:end if end != -1 else len(src)]

    def test_it_exists_and_is_a_logged_in_post(self):
        block = self._route()
        assert 'methods=["POST"]' in block
        assert "login_required" in block, "the pause door is not guarded"

    def test_it_calls_the_service(self):
        assert "media_plays" in self._route(), "the route re-implements the recording"

    def test_it_only_logs_an_ongoing_sitting_of_the_session(self):
        block = self._route()
        assert "ONGOING" in block, (
            "the pause door does not check the sitting is still going")
        assert 'eq("student_id"' in STUDENT_ROUTES.read_text(encoding="utf-8") or \
            "sitting_row" in block, (
            "the pause could be charged to somebody else's sitting")

    def test_it_takes_no_play_or_limit_from_the_request(self):
        block = self._route()
        for stolen in ('body.get("limit")', 'request.form.get("limit")',
                       'body.get("plays")', 'request.form.get("plays")'):
            assert stolen not in block, "the pause door reads an allowance off the wire"


class TestThePageReportsPauses:
    def test_the_page_reports_a_pause_to_the_server(self):
        html = EXAM.read_text(encoding="utf-8")
        assert "/student/media-pause" in html, (
            "the exam page never tells the server a pupil paused the media")

    def test_the_native_players_report_a_pause(self):
        html = EXAM.read_text(encoding="utf-8")
        assert re.search(r"@pause=", html), (
            "a native audio/video pause is not reported")

    def test_the_youtube_pause_button_reports_a_pause(self):
        html = EXAM.read_text(encoding="utf-8")
        body = html.split("comp.mediaPause = function", 1)[1].split("};", 1)[0]
        assert "mediaReportPause" in body or "mediaObserve" in body, (
            "the YouTube pause button does not report the pause")
