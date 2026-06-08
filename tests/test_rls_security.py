"""Integration tests for cross-school data isolation (RLS + decorators).

These tests verify that users from School A cannot access/modify
resources belonging to School B.

NOTE: These tests require a running Flask app with Supabase connection.
Run with: pytest tests/test_rls_security.py -v
"""

import json
import flask
import pytest
from unittest.mock import patch, MagicMock

# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def app():
    from app import create_app
    _app = create_app("app.config.TestingConfig")
    return _app


@pytest.fixture
def client(app):
    return app.test_client()


# ── Tests for @require_school_access decorator ────────────────

class TestRequireSchoolAccess:
    """Direct tests of the require_school_access decorator logic."""

    def test_direct_school_mismatch_returns_403(self, app):
        """When user_school_id differs from resource school_id → 403."""
        from app.decorators.security import require_school_access

        with app.test_request_context("/exams/exam-999"):
            flask.g.user_id = "teacher-a"
            flask.g.user_school_id = "school-a"
            flask.g.user_role = "guru"

            # We patch get_supabase to return a mock that returns school-b
            with patch("app.decorators.security.get_supabase") as mock_get_db:
                mock_db = MagicMock()
                mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {"school_id": "school-b"}
                mock_get_db.return_value = mock_db

                @require_school_access("exams", "exam_id")
                def fake_route(exam_id):
                    return "OK", 200

                resp, status = fake_route(exam_id="exam-999")
                assert status == 403

    def test_direct_school_match_passes(self, app):
        """When user_school_id matches resource school_id → pass through."""
        from app.decorators.security import require_school_access

        with app.test_request_context("/exams/exam-111"):
            flask.g.user_id = "teacher-a"
            flask.g.user_school_id = "school-a"
            flask.g.user_role = "guru"

            with patch("app.decorators.security.get_supabase") as mock_get_db:
                mock_db = MagicMock()
                mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {"school_id": "school-a"}
                mock_get_db.return_value = mock_db

                @require_school_access("exams", "exam_id")
                def fake_route(exam_id):
                    return "OK", 200

                resp, status = fake_route(exam_id="exam-111")
                assert status == 200

    def test_chained_school_mismatch_returns_403(self, app):
        """Submission → Exam → school_id: mismatch → 403."""
        from app.decorators.security import require_school_access

        with app.test_request_context("/grade/sub-999"):
            flask.g.user_id = "teacher-a"
            flask.g.user_school_id = "school-a"
            flask.g.user_role = "guru"

            with patch("app.decorators.security.get_supabase") as mock_get_db:
                mock_db = MagicMock()

                # First call: child table (submissions) returns exam_id
                child_mock = MagicMock()
                child_mock.single.return_value.execute.return_value.data = {"exam_id": "exam-999"}
                # Second call: parent table (exams) returns school-b
                parent_mock = MagicMock()
                parent_mock.single.return_value.execute.return_value.data = {"school_id": "school-b"}

                mock_db.table.side_effect = lambda t: {
                    "submissions": child_mock,
                    "exams": parent_mock,
                }.get(t, MagicMock())

                mock_get_db.return_value = mock_db

                @require_school_access("submissions", "submission_id", ("exam_id", "exams"))
                def fake_route(submission_id):
                    return "OK", 200

                resp, status = fake_route(submission_id="sub-999")
                assert status == 403


# ── API-level integration tests ───────────────────────────────

class TestCrossSchoolAPIAccess:
    """End-to-end tests via Flask test client (mocked Supabase)."""

    def _login(self, client, user_id="teacher-a", school_id="school-a", role="guru"):
        """Simulate login by setting g."""
        with client.application.app_context():
            flask.g.user_id = user_id
            flask.g.user_school_id = school_id
            flask.g.user_role = role
            flask.g.user_token = "fake-token"
            flask.g.user_email = f"{user_id}@test.com"
            flask.g.user_status = "active"

    @patch("app.routes.teacher.get_supabase")
    def test_teacher_cannot_access_exam_from_different_school(self, mock_get_db, client):
        """Teacher A gets 403 when fetching exam from School B."""
        import flask
        self._login(client, school_id="school-a")

        # Mock: exam belongs to school-b
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {"school_id": "school-b"}
        mock_get_db.return_value = mock_db

        resp = client.get("/teacher/exams/exam-from-school-b")
        assert resp.status_code == 403

    @patch("app.routes.teacher.get_supabase")
    def test_teacher_cannot_grade_submission_from_different_school(self, mock_get_db, client):
        """Teacher A gets 403 when grading a submission from School B."""
        self._login(client, school_id="school-a")

        mock_db = MagicMock()
        # Submissions → exam_id = "exam-b"
        child = MagicMock()
        child.single.return_value.execute.return_value.data = {"exam_id": "exam-b"}
        # Exams → school_id = "school-b"
        parent = MagicMock()
        parent.single.return_value.execute.return_value.data = {"school_id": "school-b"}

        def table_side_effect(t):
            return {"submissions": child, "exams": parent}.get(t, MagicMock())
        mock_db.table.side_effect = table_side_effect
        mock_get_db.return_value = mock_db

        resp = client.get("/teacher/grade/sub-from-school-b")
        assert resp.status_code == 403

    @patch("app.routes.admin_sekolah.get_supabase")
    def test_admin_cannot_delete_student_from_different_school(self, mock_get_db, client):
        """Admin A gets 403 when deleting a student from School B."""
        self._login(client, school_id="school-a", role="admin_sekolah")

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {"school_id": "school-b"}
        mock_get_db.return_value = mock_db

        resp = client.post("/admin-sekolah/students/student-from-school-b/delete")
        assert resp.status_code == 403


# ── Supabase RLS policy tests (requires real Supabase) ────────

@pytest.mark.skip(reason="Requires real Supabase connection with anon key")
class TestSupabaseRLSPolicies:
    """Verify RLS actually blocks cross-school queries at the DB level."""

    def test_rls_blocks_cross_school_exam_select(self):
        """Using anon key, teacher from school A cannot SELECT exams from school B."""
        # This test needs a real Supabase client with anon key
        pass

    def test_rls_blocks_cross_school_submission_select(self):
        """Using anon key, teacher from school A cannot SELECT submissions from school B."""
        pass
