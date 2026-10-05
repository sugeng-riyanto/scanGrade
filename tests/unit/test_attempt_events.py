"""What the page saw while a pupil was on the paper, numbered in its own band.

`attempt_session_events` has existed since migration 035 and has been written by
one caller only: the server's own transitions (a gap, a resume, a lock, a media
play). The table's own comment says the rest — sync gaps, offline periods, answer
changes, per-question dwell — waits for the ingest phase, and until it lands those
metrics are `NULL` in `attempt_summary` and every dashboard built on them is blank.
This is that phase's door.

Two design points are the whole file:

* **The client numbers its own stream, in a band the server's transitions cannot
  reach.** The table's uniqueness is `UNIQUE (attempt_id, seq)`, and the server
  numbers transitions `1, 2, 3, …` from whatever the highest row is. If a pupil's
  page numbered itself from the same space, its next event could land on a number a
  server transition had just taken — the database would refuse one of the two, and
  the failure would be silent. So client events start at `CLIENT_EVENT_SEQ_BASE`,
  far above any transition, and a seq below it is refused outright rather than
  written into the space the server owns.

* **Retries must be free.** The page flushes batches over an unstable school
  network, so the same batch will arrive twice. Idempotency is the database's
  `UNIQUE (attempt_id, seq)`, not the client's promise: a replayed batch is sent
  with `ignore_duplicates`, and nothing is written the second time.

The endpoint is deliberately **not** a lock trigger: it records what happened and
decides nothing. Locking stays fullscreen/tab-switch, as `attempt_status` says.
"""
from __future__ import annotations

import inspect
import pathlib
from types import SimpleNamespace

import pytest

from app.services import attempt_events as E

ROOT = pathlib.Path(__file__).resolve().parents[2]
API = ROOT / "app" / "routes" / "api.py"


# ── a fake that models the database's own uniqueness ─────────────────────────

class FakeEvents:
    """`attempt_session_events` with `UNIQUE (attempt_id, seq)` for real."""

    def __init__(self, fail=False):
        self.rows: list[dict] = []
        self.fail = fail
        self.calls: list[dict] = []

    def table(self, name):
        assert name == "attempt_session_events", f"unexpected table {name}"
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False, **kw):
        self.calls.append({"rows": rows, "on_conflict": on_conflict,
                           "ignore_duplicates": ignore_duplicates})
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("db down")
        call = self.calls[-1]
        seen = {(r["attempt_id"], r["seq"]) for r in self.rows}
        for row in call["rows"]:
            key = (row["attempt_id"], row["seq"])
            if key in seen:
                continue                       # ON CONFLICT DO NOTHING
            self.rows.append(row)
            seen.add(key)
        return SimpleNamespace(data=[])


def event(seq, kind="tab_hidden", **kw):
    base = {"seq": seq, "kind": kind}
    base.update(kw)
    return base


# ── the band the client owns ─────────────────────────────────────────────────

class TestTheClientNumbersItsOwnBand:
    def test_the_band_starts_above_any_server_transition(self):
        assert E.CLIENT_EVENT_SEQ_BASE >= 1000, (
            "the band has to leave room for every transition the server writes"
        )

    def test_a_seq_below_the_band_is_refused(self):
        """Otherwise a client event could take a number a transition just took."""
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [event(1), event(5)])
        assert out["stored"] == 0
        assert [r["reason"] for r in out["rejected"]] == ["seq", "seq"]
        assert fake.rows == []

    def test_a_seq_in_the_band_is_written_as_sent(self):
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [event(E.CLIENT_EVENT_SEQ_BASE)])
        assert out["stored"] == 1
        assert fake.rows[0]["seq"] == E.CLIENT_EVENT_SEQ_BASE

    def test_the_vocabulary_is_closed(self):
        """A kind nobody defined is refused rather than stored and interpreted."""
        assert "tab_hidden" in E.CLIENT_EVENT_KINDS
        assert "went_offline" in E.CLIENT_EVENT_KINDS
        assert "answer_changed" in E.CLIENT_EVENT_KINDS
        assert "make_it_look_like_cheating" not in E.CLIENT_EVENT_KINDS


# ── validation ───────────────────────────────────────────────────────────────

class TestWhatIsRefused:
    def test_an_unknown_kind(self):
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [event(E.CLIENT_EVENT_SEQ_BASE, kind="nope")])
        assert out["stored"] == 0 and out["rejected"][0]["reason"] == "kind"

    def test_a_negative_duration(self):
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [
            event(E.CLIENT_EVENT_SEQ_BASE, duration_ms=-1)])
        assert out["rejected"][0]["reason"] == "duration"

    def test_an_absurd_duration(self):
        """A pupil cannot be away for longer than the paper could run."""
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [
            event(E.CLIENT_EVENT_SEQ_BASE, duration_ms=E.MAX_DURATION_MS + 1)])
        assert out["rejected"][0]["reason"] == "duration"

    def test_meta_that_is_not_an_object(self):
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [
            event(E.CLIENT_EVENT_SEQ_BASE, meta="a string")])
        assert out["rejected"][0]["reason"] == "meta"

    def test_meta_that_would_not_fit(self):
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [
            event(E.CLIENT_EVENT_SEQ_BASE, meta={"x": "y" * E.MAX_META_BYTES})])
        assert out["rejected"][0]["reason"] == "meta"

    def test_a_question_index_that_is_not_a_number(self):
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [
            event(E.CLIENT_EVENT_SEQ_BASE, question_index="three")])
        assert out["rejected"][0]["reason"] == "question"

    def test_an_event_that_is_not_an_object(self):
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", ["not an object"])
        assert out["rejected"][0]["reason"] == "not_an_object"

    def test_a_batch_larger_than_the_cap(self):
        fake = FakeEvents()
        many = [event(E.CLIENT_EVENT_SEQ_BASE + i) for i in range(E.MAX_EVENTS_PER_BATCH + 1)]
        out = E.record_batch(fake, "a-1", many)
        assert out["rejected"][0]["reason"] == "too_many"
        assert fake.rows == []

    def test_a_good_event_beside_a_bad_one_still_lands(self):
        """One malformed event must not cost the pupil the rest of the batch."""
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [
            event(E.CLIENT_EVENT_SEQ_BASE),
            event(E.CLIENT_EVENT_SEQ_BASE + 1, kind="nope"),
        ])
        assert out["stored"] == 1 and len(out["rejected"]) == 1
        assert len(fake.rows) == 1


# ── persistence ──────────────────────────────────────────────────────────────

class TestWhatIsWritten:
    def test_the_clients_clock_is_kept_and_the_servers_is_added(self):
        fake = FakeEvents()
        E.record_batch(fake, "a-1", [
            event(E.CLIENT_EVENT_SEQ_BASE, occurred_at="2026-10-05T01:00:00+00:00",
                  duration_ms=4200, offline=True, question_index=3)])
        row = fake.rows[0]
        assert row["occurred_at"] == "2026-10-05T01:00:00+00:00"
        assert row["server_at"], "the server must stamp its own clock too"
        assert row["duration_ms"] == 4200 and row["offline"] is True
        assert row["question_index"] == 3

    def test_the_insert_is_told_to_ignore_duplicates(self):
        fake = FakeEvents()
        E.record_batch(fake, "a-1", [event(E.CLIENT_EVENT_SEQ_BASE)])
        call = fake.calls[-1]
        assert call["ignore_duplicates"] is True
        assert "seq" in call["on_conflict"] and "attempt_id" in call["on_conflict"]

    def test_a_replayed_batch_writes_nothing_new(self):
        """The school network will deliver the same flush twice."""
        fake = FakeEvents()
        batch = [event(E.CLIENT_EVENT_SEQ_BASE), event(E.CLIENT_EVENT_SEQ_BASE + 1)]
        E.record_batch(fake, "a-1", batch)
        E.record_batch(fake, "a-1", batch)
        assert len(fake.rows) == 2, "a retry must not double the record"

    def test_a_write_that_fails_is_reported_not_raised(self):
        """Evidence must never cost a pupil their paper."""
        out = E.record_batch(FakeEvents(fail=True), "a-1",
                             [event(E.CLIENT_EVENT_SEQ_BASE)])
        assert out["stored"] == 0 and out["error"]

    def test_an_empty_batch_is_not_an_error(self):
        fake = FakeEvents()
        out = E.record_batch(fake, "a-1", [])
        assert out == {"stored": 0, "rejected": [], "error": None}
        assert fake.calls == []

    def test_nothing_is_written_without_an_attempt(self):
        fake = FakeEvents()
        out = E.record_batch(fake, None, [event(E.CLIENT_EVENT_SEQ_BASE)])
        assert out["stored"] == 0 and fake.calls == []


# ── the door ─────────────────────────────────────────────────────────────────

class TestTheEndpoint:
    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @staticmethod
    def _sign_in(monkeypatch, role="murid"):
        def session_for(token):                       # noqa: ARG001
            return {"user_id": "u-1", "role": role, "status": "active",
                    "email": "pupil@example.id", "name": "Pupil",
                    "school_id": None, "class_id": None}
        monkeypatch.setattr("app.utils.auth._session_for", session_for)

    def _call(self, app, monkeypatch, *, row, status, batch):
        from app.routes import api as apimod
        monkeypatch.setattr(apimod, "get_supabase", lambda: SimpleNamespace())
        monkeypatch.setattr(E, "open_attempt", lambda *a, **k: row)
        monkeypatch.setattr(E, "sitting_status", lambda *a, **k: status)
        fn = inspect.unwrap(apimod.student_attempt_events)
        with app.test_request_context("/api/student/exams/e-1/events", method="POST",
                                     json={"events": batch}):
            from flask import g
            g.user_id, g.user_role = "u-1", "murid"
            result = fn("e-1")
        # A bare `jsonify` is a Response; an error return is `(Response, status)`.
        body, code = result if isinstance(result, tuple) else (result, 200)
        return body.get_json(), code

    ACTIVE = {"status": "active", "message_key": "attempt_active"}
    FINISHED = {"status": "finished", "message_key": "attempt_finished"}

    def test_a_sitting_with_no_attempt_is_answered_specifically(self, app, monkeypatch):
        resp, code = self._call(app, monkeypatch, row=None, status=self.ACTIVE,
                                batch=[event(E.CLIENT_EVENT_SEQ_BASE)])
        assert code == 404
        assert resp["reason"] == "no_attempt"

    def test_a_finished_sitting_is_refused_with_its_state(self, app, monkeypatch):
        resp, code = self._call(app, monkeypatch, row={"id": "a-1"},
                                status=self.FINISHED,
                                batch=[event(E.CLIENT_EVENT_SEQ_BASE)])
        assert code == 409
        assert resp["reason"] == "finished"
        assert resp["message_key"] == "attempt_finished"

    def test_an_active_sitting_is_answered_with_what_was_stored(self, app, monkeypatch):
        from app.routes import api as apimod
        seen = {}
        monkeypatch.setattr(
            E, "record_batch",
            lambda supa, attempt_id, events: seen.update(
                {"attempt_id": attempt_id, "n": len(events)})
            or {"stored": len(events), "rejected": [], "error": None})
        resp, code = self._call(app, monkeypatch, row={"id": "a-1"}, status=self.ACTIVE,
                                batch=[event(E.CLIENT_EVENT_SEQ_BASE)])
        assert code == 200
        assert resp["stored"] == 1
        assert seen == {"attempt_id": "a-1", "n": 1}

    def test_an_empty_batch_is_refused_before_any_read(self, app, monkeypatch):
        resp, code = self._call(app, monkeypatch, row={"id": "a-1"},
                                status=self.ACTIVE, batch=[])
        assert code == 400
        assert resp["reason"] == "no_events"

    def test_the_route_is_owned_sitting_only(self):
        src = API.read_text(encoding="utf-8")
        block = src.split('@api_bp.route("/student/exams/<exam_id>/events"', 1)
        assert len(block) == 2, "the endpoint is not registered at the path the page posts to"
        head = block[1].split("def student_attempt_events", 1)[0]
        assert "@login_required" in head
        assert '@open_year_required("exam_id")' in head, (
            "the id arrives in the path so the year lock can resolve it")

    def test_the_endpoint_decides_nothing_about_locking(self):
        """Recording is not judging: no lock call may appear in this door."""
        src = API.read_text(encoding="utf-8")
        body = src.split("def student_attempt_events", 1)[1]
        body = body.split("\n@api_bp.route", 1)[0]
        assert "resume_code" not in body and ".lock(" not in body


# ── the page that reports them ───────────────────────────────────────────────

class TestThePageReportsItsOwnObservations:
    PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"

    def test_the_page_posts_to_the_door_that_exists(self):
        body = self.PAGE.read_text(encoding="utf-8")
        assert "/api/student/exams/{{ exam.id }}/events" in body

    def test_the_numbers_start_in_the_band_the_server_reserves(self):
        body = self.PAGE.read_text(encoding="utf-8")
        assert "if (!(n >= 1000000)) n = 1000000;" in body, (
            "the page must number itself in the client band, or one of its events "
            "could take a number a server transition just took")

    def test_a_reload_continues_the_numbering_instead_of_reusing_it(self):
        """A refreshed page that restarted at the band would re-write numbers it
        already used, and the database would silently drop every one of them."""
        body = self.PAGE.read_text(encoding="utf-8")
        assert "localStorage.setItem(this._evSeqKey()" in body
        assert "localStorage.getItem(this._evSeqKey())" in body

    def test_every_kind_the_page_emits_is_one_the_server_accepts(self):
        import re
        body = self.PAGE.read_text(encoding="utf-8")
        emitted = set(re.findall(r"this\._ev\('([a-z_]+)'", body))
        assert emitted, "the page emits no events at all"
        unknown = sorted(emitted - set(E.CLIENT_EVENT_KINDS))
        assert not unknown, f"the server would refuse these: {unknown}"

    def test_the_buffer_flushes_on_a_cadence_and_when_it_fills(self):
        body = self.PAGE.read_text(encoding="utf-8")
        assert "SG_EVENT_FLUSH_MS" in body
        assert "this._evBuffer.length >= 40" in body, (
            "a full buffer must leave on its own, or the cadence becomes the only "
            "moment an event moves")

    def test_a_dropped_flush_never_disturbs_the_paper(self):
        body = self.PAGE.read_text(encoding="utf-8")
        block = body.split("flushEvents() {", 1)[1].split("// The student has gone", 1)[0]
        assert ".catch(" in block, "a failed flush must be swallowed"
        assert "resume_code" not in block and ".lock(" not in block

    def test_the_page_adds_no_new_visible_copy(self):
        """This is plumbing: a label here would need a bilingual pair for nothing."""
        body = self.PAGE.read_text(encoding="utf-8")
        block = body.split("        _evSeqKey()", 1)[1].split("        // The student has gone", 1)[0]
        assert "sgT(" not in block, "no user-visible string belongs in this buffer"
        assert "sgKind" not in block

    def test_the_page_reports_the_moments_the_metrics_need(self):
        body = self.PAGE.read_text(encoding="utf-8")
        for kind in ("tab_hidden", "tab_visible", "window_blur", "window_focus",
                     "went_offline", "came_online", "question_changed",
                     "answer_changed"):
            assert f"_ev('{kind}'" in body, f"nothing reports {kind}"
