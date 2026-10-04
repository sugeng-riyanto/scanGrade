"""A pupil's "my connection dropped three times" must be readable from the record.

`attempt_session_events` (migration 035) has been written since the audit-trail phase:
`attempt_status.record_event` stamps a row per transition — `attempt_started`,
`connection_gap`, `connection_resumed`, `attempt_locked`, `attempt_resumed`,
`attempt_finalized` — and `attempt_status.timeline()` can read one attempt back.

What was missing is the *rendering*: `timeline()` has **no caller**, so the record the
pupil's claim is about exists and reaches nobody. A teacher answering "did your
connection really drop?" had to open the database.

This file holds three things so the timeline cannot become a claim of its own:

* **the description is honest** — a gap is named a gap and carries its own length, and a
  kind nobody anticipated is shown as itself rather than silently dropped;
* **the read is scoped** — the caller's authority is the exams it already holds (the same
  set `exam_codes` uses), `school_id` is required, and an empty authority selects nothing;
* **both dashboards render it** — the session page (which a teacher reaches from their own
  exam) and the invigilation pages (teacher and official), so the invigilator holding the
  sitting can read it too.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "attempt_timeline.py"
SESSION_TEMPLATE = ROOT / "app" / "templates" / "teacher" / "session_review.html"
TEACHER_INVIG = ROOT / "app" / "templates" / "teacher" / "invigilation.html"
OFFICIAL_INVIG = ROOT / "app" / "templates" / "principal" / "invigilation.html"
TEACHER_ROUTES = ROOT / "app" / "routes" / "teacher.py"


# ── the service exists and describes rather than judges ──────────────────────

class TestTheTimelineDescribesWhatHappened:
    def test_the_service_exists(self):
        assert SERVICE.exists(), "no timeline service was written"

    def test_every_recorded_kind_has_a_reader_facing_label(self):
        from app.services import attempt_status, attempt_timeline

        for kind in attempt_status.EVENT_KINDS:
            assert kind in attempt_timeline.EVENT_LABELS, (
                f"{kind} is recorded but the page has no label for it, so a pupil's "
                f"transition would render as a raw machine name")
            label = attempt_timeline.EVENT_LABELS[kind]
            assert label.get("id") and label.get("en"), (
                f"{kind} is missing a bilingual label")

    def test_an_unanticipated_kind_is_shown_as_itself(self):
        from app.services import attempt_timeline

        described = attempt_timeline.describe("something_new")
        assert "something_new" in described, (
            "a kind this release does not know is dropped instead of shown, so the "
            "record quietly loses a transition")

    def test_a_gap_carries_its_own_length(self):
        from app.services import attempt_timeline

        row = {"kind": "connection_gap", "meta": {"gap_seconds": 137},
               "server_at": "2026-09-21T08:00:00Z"}
        entry = attempt_timeline.entry(row)
        assert "137" in entry["detail"] or "2" in entry["detail"], (
            "a gap is described without how long it lasted, which is the only thing "
            "the claim is about")

    def test_a_description_is_not_a_verdict(self):
        from app.services import attempt_timeline

        words = " ".join(attempt_timeline.EVENT_LABELS[k]["id"] + " " +
                         attempt_timeline.EVENT_LABELS[k]["en"]
                         for k in attempt_timeline.EVENT_LABELS).lower()
        for judgement in ("curang", "mencurigakan", "cheat", "suspicious", "guilty"):
            assert judgement not in words, (
                f"the timeline labels a transition '{judgement}', which is a verdict "
                f"the record cannot support")


# ── the read is scoped to what the caller already holds ──────────────────────

class _FakeQuery:
    def __init__(self, table, rows, calls):
        self.table, self.rows, self.calls = table, rows, calls
        self.filters = []

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append((column, list(values)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self.calls.append((self.table, list(self.filters)))
        rows = list(self.rows)
        for column, value in self.filters:
            if isinstance(value, list):
                rows = [r for r in rows if r.get(column) in value]
            else:
                rows = [r for r in rows if r.get(column) == value]
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    def __init__(self, tables):
        self.tables = tables
        self.calls = []

    def table(self, name):
        return _FakeQuery(name, self.tables.get(name, []), self.calls)


def _reads():
    return {
        "submissions": [
            {"id": "a1", "exam_id": "e1", "student_id": "s1"},
            {"id": "a2", "exam_id": "e2", "student_id": "s2"},
        ],
        "students": [
            {"id": "s1", "profiles": {"full_name": "Ayu"}},
            {"id": "s2", "profiles": {"full_name": "Budi"}},
        ],
        "attempt_session_events": [
            {"attempt_id": "a1", "seq": 1, "kind": "attempt_started",
             "server_at": "2026-09-21T08:00:00Z", "meta": {}},
            {"attempt_id": "a1", "seq": 2, "kind": "connection_gap",
             "server_at": "2026-09-21T08:10:00Z",
             "meta": {"gap_seconds": 137}},
            {"attempt_id": "a1", "seq": 3, "kind": "connection_resumed",
             "server_at": "2026-09-21T08:12:17Z", "meta": {}},
            {"attempt_id": "a2", "seq": 1, "kind": "attempt_locked",
             "server_at": "2026-09-21T09:00:00Z", "meta": {}},
        ],
    }


class TestTheReadStaysInsideTheCallersAuthority:
    def test_the_service_exists_for_a_school_scoped_read(self):
        from app.services import attempt_timeline

        assert callable(attempt_timeline.for_exam)

    def test_an_empty_authority_selects_nothing(self):
        from app.services import attempt_timeline

        client = _FakeSupabase(_reads())
        out = attempt_timeline.for_exam(client, "school-1", exam_ids=[])
        assert out == {}, (
            "an empty authority selected rows; a reader who holds no exam must see "
            "no timeline, not every timeline")
        assert not client.calls, "an empty authority still touched the database"

    def test_only_the_held_exams_are_read(self):
        from app.services import attempt_timeline

        client = _FakeSupabase(_reads())
        out = attempt_timeline.for_exam(client, "school-1", exam_ids=["e1"])
        assert set(out) == {"s1"}, (
            "an exam the caller does not hold leaked into the timeline")
        assert "s2" not in out

    def test_the_school_is_required_and_never_optional(self):
        from app.services import attempt_timeline

        import inspect

        params = inspect.signature(attempt_timeline.for_exam).parameters
        assert "school_id" in params, "school_id is not a parameter"
        assert params["school_id"].default is inspect.Parameter.empty, (
            "school_id has a default, so a caller can read without naming a school")

    def test_the_events_are_read_by_attempt_not_by_exam(self):
        from app.services import attempt_timeline

        client = _FakeSupabase(_reads())
        attempt_timeline.for_exam(client, "school-1", exam_ids=["e1"])
        event_reads = [f for table, f in client.calls
                       if table == "attempt_session_events"]
        assert event_reads, "the event table was never read"
        assert any(column == "attempt_id" for filters in event_reads
                   for column, _ in filters), (
            "the event table was not read by attempt_id, which is the column it has")
        assert not any(column == "exam_id" for filters in event_reads
                       for column, _ in filters), (
            "the event table was read by an exam_id column it does not carry")

    def test_the_timeline_comes_back_in_sequence(self):
        from app.services import attempt_timeline

        client = _FakeSupabase(_reads())
        out = attempt_timeline.for_exam(client, "school-1", exam_ids=["e1"])
        assert [e["kind"] for e in out["s1"]["entries"]] == [
            "attempt_started", "connection_gap", "connection_resumed"], (
            "the transitions are not in the order they happened, so the story the "
            "pupil tells cannot be read from the record")

    def test_a_read_that_fails_is_an_empty_timeline_not_an_exception(self):
        from app.services import attempt_timeline

        class _Boom:
            def table(self, name):
                raise RuntimeError("Server disconnected")

        assert attempt_timeline.for_exam(_Boom(), "school-1", exam_ids=["e1"]) == {}


# ── both dashboards render it ────────────────────────────────────────────────

class TestBothDashboardsRenderTheTimeline:
    def test_the_session_page_shows_a_timeline(self):
        text = SESSION_TEMPLATE.read_text(encoding="utf-8")
        assert "timeline" in text.lower(), (
            "the session page a teacher reaches from their own exam shows no timeline")
        assert "timelineFor" in text or "timelines" in text, (
            "the session page has no binding for the timeline")

    def test_the_session_page_is_told_about_the_timeline(self):
        src = TEACHER_ROUTES.read_text(encoding="utf-8")
        assert "timeline" in src, (
            "the sessions-data endpoint never includes the timeline, so the page "
            "has nothing to render")

    @pytest.mark.parametrize("template", [TEACHER_INVIG, OFFICIAL_INVIG])
    def test_the_invigilation_pages_show_a_timeline(self, template):
        text = template.read_text(encoding="utf-8")
        assert "timeline" in text.lower(), (
            f"{template.name} shows no timeline, so the invigilator holding the "
            f"sitting cannot read the record either")

    def test_the_invigilation_route_reads_the_timeline(self):
        src = TEACHER_ROUTES.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "invigilation_duties"),
                  None)
        assert fn is not None, "invigilation_duties moved"
        body = ast.get_source_segment(src, fn) or ""
        assert "timeline" in body, (
            "the teacher invigilation page's route reads no timeline")

    def test_every_new_string_is_bilingual(self):
        for template in (SESSION_TEMPLATE, TEACHER_INVIG, OFFICIAL_INVIG):
            text = template.read_text(encoding="utf-8")
            if template is SESSION_TEMPLATE:
                # The session page is Indonesian-only by design (printed surfaces
                # are excluded from the toggle); it must not pretend otherwise.
                assert "content_lang = 'id'" in text
                continue
            assert "t('" in text, f"{template.name} carries no i18n helper"
