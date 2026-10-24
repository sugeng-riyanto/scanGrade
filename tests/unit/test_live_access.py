"""Offline authorization and token contract tests for isolated ScanGrade Live."""
import sys
import types

from flask import Flask, g
from app.routes import live as routes
from app.services import live_policy, livekit_gateway


def test_off_is_404_even_when_livekit_is_not_installed(monkeypatch):
    monkeypatch.delenv("SCANGRADE_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("SCANGRADE_LIVE_EXAM_ENABLED", raising=False)
    app = Flask(__name__)
    with app.test_request_context("/api/live/exams/anything"):
        response, code = routes._off()
        assert code == 404
        assert response.get_json()["error"] == "ScanGrade Live is disabled"


def test_tenant_and_owner_enforced_for_teacher():
    app = Flask(__name__)
    exam = {"id": "exam", "teacher_id": "teacher-1", "school_id": "school-1"}
    with app.test_request_context():
        g.user_id = "teacher-1"
        g.user_role = "guru"
        g.user_school_id = "school-1"
        assert routes._access(None, exam, staff_only=True) == "teacher"
        g.user_school_id = "school-2"
        assert routes._access(None, exam, staff_only=True) is None
        g.user_school_id = "school-1"
        g.user_id = "teacher-2"
        assert routes._access(None, exam, staff_only=True) is None


def test_student_must_pass_existing_exam_access_guard(monkeypatch):
    app = Flask(__name__)
    exam = {"id": "exam", "teacher_id": "teacher-1", "school_id": "school-1"}
    with app.test_request_context():
        g.user_id = "student-1"
        g.user_role = "murid"
        g.user_school_id = "school-1"
        monkeypatch.setattr(routes, "exam_sitting_allowed",
                            lambda *_: (False, "denied"))
        assert routes._access(None, exam) is None
        monkeypatch.setattr(routes, "exam_sitting_allowed",
                            lambda *_: (True, ""))
        assert routes._access(None, exam) == "student"
        assert routes._access(None, exam, staff_only=True) is None


def test_join_token_grants_without_network(monkeypatch):
    grants_seen = []
    metadata_seen = []
    class FakeToken:
        def __init__(self, *_):
            pass
        def with_identity(self, identity):
            assert identity.startswith("p_")
            return self
        def with_metadata(self, data):
            metadata_seen.append(data)
            return self
        def with_grants(self, grants):
            grants_seen.append(grants)
            return self
        def with_ttl(self, ttl):
            assert ttl.total_seconds() == 300
            return self
        def to_jwt(self):
            return "signed-fixture"

    fake = types.ModuleType("livekit")
    fake.api = types.SimpleNamespace(
        AccessToken=FakeToken,
        VideoGrants=lambda **kw: types.SimpleNamespace(**kw),
    )
    monkeypatch.setitem(sys.modules, "livekit", fake)
    cfg = {"LIVEKIT_URL": "wss://example.invalid",
           "LIVEKIT_API_KEY": "test-key", "LIVEKIT_API_SECRET": "test-secret"}
    result = livekit_gateway.issue_join_token(
        cfg, "s1", "room-1", "u1", "student", supervisor_id="teacher-1")
    assert result["token"] == "signed-fixture"
    assert result["room"] == "room-1"
    assert result["mic_approved"] is False
    assert result["supervisor_identity"] == live_policy.participant_identity(cfg, "s1", "teacher-1")
    assert grants_seen[0].can_publish_sources == ["camera"]
    assert grants_seen[0].can_publish_data is False
    assert grants_seen[0].room_admin is False
    assert grants_seen[0].room_record is False
    assert metadata_seen[0] == '{"role":"student"}'
    livekit_gateway.issue_join_token(cfg, "s1", "room-1", "u1", "student", True, supervisor_id="teacher-1")
    assert grants_seen[1].can_publish_sources == ["camera", "microphone"]


def test_permissions_reject_unknown_role():
    try:
        live_policy.allowed_sources("stranger")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown role must not get publish permission")


def test_student_join_fails_closed_without_supervisor():
    cfg = {"LIVEKIT_URL": "wss://example.invalid",
           "LIVEKIT_API_KEY": "test-key", "LIVEKIT_API_SECRET": "test-secret"}
    try:
        livekit_gateway.issue_join_token(cfg, "s1", "room", "student", "student")
    except ValueError:
        pass
    else:
        raise AssertionError("student token must require an invigilator identity")
