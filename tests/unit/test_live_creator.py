"""Live instructor ownership stays pinned to one verified publisher identity."""
from flask import Flask, g
from app.routes import live


def test_creator_can_moderate_but_other_school_admin_cannot_take_over(monkeypatch):
    app = Flask(__name__)
    session = {
        "id": "room-id", "status": "active",
        "exam_id": "exam-1", "created_by": "teacher-a",
    }
    exam = {
        "id": "exam-1", "teacher_id": "teacher-a", "school_id": "school-1",
    }
    monkeypatch.setattr(live, "_exam", lambda *_: exam)
    with app.test_request_context():
        g.user_school_id = "school-1"
        g.user_role = "guru"
        g.user_id = "teacher-a"
        assert live._check(None, session, staff_only=True) == "teacher"
        g.user_role = "admin_sekolah"
        g.user_id = "admin-b"
        assert live._access(None, exam, staff_only=True) == "teacher"
        assert live._check(None, session, staff_only=True) is None


def test_students_still_use_existing_exam_sitting_check(monkeypatch):
    app = Flask(__name__)
    session = {
        "id": "room-id", "status": "active",
        "exam_id": "exam-1", "created_by": "teacher-a",
    }
    exam = {
        "id": "exam-1", "teacher_id": "teacher-a", "school_id": "school-1",
    }
    monkeypatch.setattr(live, "_exam", lambda *_: exam)
    monkeypatch.setattr(live, "exam_sitting_allowed",
                        lambda *_: (True, ""))
    with app.test_request_context():
        g.user_school_id = "school-1"
        g.user_role = "murid"
        g.user_id = "student-x"
        assert live._check(None, session) == "student"
        g.user_school_id = "school-2"
        assert live._check(None, session) is None


def test_session_without_creator_refused_for_teacher(monkeypatch):
    app = Flask(__name__)
    session = {"status": "active", "exam_id": "exam-1"}
    exam = {"id": "exam-1", "teacher_id": "teacher-a", "school_id": "school-1"}
    monkeypatch.setattr(live, "_exam", lambda *_: exam)
    with app.test_request_context():
        g.user_school_id = "school-1"
        g.user_role = "guru"
        g.user_id = "teacher-a"
        assert live._check(None, session, staff_only=True) is None
