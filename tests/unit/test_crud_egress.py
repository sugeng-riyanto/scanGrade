"""Exercise the real PostgREST builders over HTTP without a live database."""
import json
import inspect

import httpx
import pytest
from flask import g
from postgrest import SyncPostgrestClient

from app.routes import teacher
from app.utils.supabase_retry import RetryingClient
from tests.unit.test_sync_authority import EXAM, NOW, iso, sync


@pytest.mark.parametrize("existing,omit_pages", [(False, False), (True, False), (True, True)])
def test_draft_writes_keep_answers_without_echoing_canvas(app, monkeypatch, existing, omit_pages):
    canvas = "data:image/png;base64," + "A" * 100_000
    row = {"id": "sub-1", "exam_id": "exam-1", "student_id": "stu-1",
           "status": "draft", "started_at": iso(NOW - 60),
           "updated_at": iso(NOW - 20),
           "answers": {"0": {"answer": "B", "pages": {"0": {"canvas": canvas}}}}}
    writes = []

    def serve(request):
        if request.method == "GET":
            if request.url.path.endswith("/exams"):
                return httpx.Response(200, json=dict(EXAM, question_types={"0": "essay_canvas"}))
            return httpx.Response(200, json=[row] if existing else [])
        # A real PostgREST minimal write returns no row representation.
        assert "return=minimal" in request.headers["prefer"]
        assert request.method == ("PATCH" if existing else "POST")
        if existing:
            assert request.url.params["id"] == "eq.sub-1"
        writes.append(json.loads(request.content))
        return httpx.Response(204)

    db = SyncPostgrestClient("https://db.invalid")
    db.session.close()
    db.session = httpx.Client(base_url="https://db.invalid",
                              transport=httpx.MockTransport(serve))
    try:
        body, status = sync(RetryingClient(db), monkeypatch, app, light=existing,
                            payload={"answers": {"0": {"answer": "A", **({} if omit_pages else {"pages": {}})}}} if existing else
                            {"answers": row["answers"]})
    finally:
        db.session.close()
    assert status == 200 and body["saved"] is True
    assert len(writes) == 1  # no write replay and no missing save
    assert writes[0]["answers"]["0"]["pages"]["0"]["canvas"] == canvas
    if existing:
        assert writes[0]["answers"]["0"]["answer"] == "A"
    else:
        assert writes[0]["student_id"] == "stu-1"
        assert writes[0]["exam_id"] == "exam-1"


@pytest.mark.parametrize("role,filter_key,filter_value", [
    ("guru", "teacher_id", "teacher-1"),
    ("admin_sekolah", "school_id", "school-1"),
])
def test_exam_cards_request_metadata_and_keep_scope(app, monkeypatch, role, filter_key, filter_value):
    paper = {"id": "exam-1", "title": "Paper", "status": "active",
             "is_published": True, "class_ids": ["class-1"],
             "question_types": {"0": "mcq"}, "answer_key": {"0": ["A", "B"]}}
    requests = []

    def serve(request):
        requests.append(request)
        columns = set(request.url.params["select"].split(","))
        assert "*" not in columns
        assert {"answer_key", "question_types", "class_ids", "anti_cheat_enabled"} <= columns
        assert not {"question_canvas", "question_texts", "question_stimuli"} & columns
        assert request.url.params[filter_key] == "eq." + filter_value
        return httpx.Response(200, json=[{k: v for k, v in paper.items() if k in columns}])

    db = SyncPostgrestClient("https://db.invalid")
    db.session.close()
    db.session = httpx.Client(base_url="https://db.invalid", transport=httpx.MockTransport(serve))
    monkeypatch.setattr(teacher, "get_supabase", lambda: RetryingClient(db))
    monkeypatch.setattr(teacher, "exam_quota", lambda sid: {})
    monkeypatch.setattr(teacher, "render_template", lambda name, **context: context)
    try:
        with app.test_request_context("/teacher/exams"):
            g.user_id, g.user_role, g.user_school_id = "teacher-1", role, "school-1"
            result = teacher.my_exams.__wrapped__()
    finally:
        db.session.close()
    assert len(requests) == 1
    assert result["exams"][0]["title"] == "Paper"
    assert result["ambiguous_ids"] == {"exam-1"}
    assert not result["unassigned_ids"]


def test_delete_exam_does_not_download_deleted_answers(app, monkeypatch):
    calls = []

    def serve(request):
        assert request.method == "DELETE"
        assert "return=minimal" in request.headers["prefer"]
        expected_filter = "id" if request.url.path.endswith("/exams") else "exam_id"
        assert request.url.params[expected_filter] == "eq.exam-1"
        calls.append(request.url.path)
        return httpx.Response(204)

    db = SyncPostgrestClient("https://db.invalid")
    db.session.close()
    db.session = httpx.Client(base_url="https://db.invalid", transport=httpx.MockTransport(serve))
    monkeypatch.setattr(teacher, "get_supabase", lambda: RetryingClient(db))
    monkeypatch.setattr(teacher, "_guard_exam", lambda *a, **kw: ({"id": "exam-1"}, None))
    monkeypatch.setattr(teacher, "_invalidate_teacher_dashboard", lambda: None)
    monkeypatch.setattr(teacher, "log_activity", lambda *a, **kw: None)
    try:
        with app.test_request_context("/teacher/exams/exam-1/delete", method="POST", json={}):
            g.user_id = "teacher-1"
            response = inspect.unwrap(teacher.delete_exam)("exam-1")
    finally:
        db.session.close()
    assert response.get_json() == {"success": True}
    assert calls == ["/violation_logs", "/exam_access_codes", "/analytics_cache",
                     "/submissions", "/exams"]
