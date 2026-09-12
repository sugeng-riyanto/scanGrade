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


# ── Helpers ───────────────────────────────────────────────────

BEARER = {"Authorization": "Bearer fake-token", "Accept": "application/json"}


def mock_db(table_rows):
    """Supabase stand-in: table_rows maps table name → row (or list of rows).

    Mirrors the chains the app actually uses:
        db.table(t).select(...).eq(...).single().execute().data
        db.table(t).select(...).eq(...).maybe_single().execute().data
    """
    db = MagicMock()

    def _table(name):
        m = MagicMock()
        data = table_rows.get(name)
        m.select.return_value.eq.return_value.single.return_value.execute.return_value.data = data
        m.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = data
        m.select.return_value.eq.return_value.execute.return_value.data = data if isinstance(data, list) else ([data] if data else [])
        return m

    db.table.side_effect = _table
    return db


def auth_patches(table_rows, user_id="teacher-a", school_id="school-a", role="guru"):
    """Patch the auth layer so login_required resolves a fake signed-in user.

    Both login_required and require_school_access resolve the client through
    app.utils.auth, so a single patch of that module covers the whole request.
    """
    user = MagicMock()
    user.user.id = user_id
    user.user.email = f"{user_id}@test.com"
    user.user.user_metadata = {
        "role": role, "school_id": school_id, "full_name": "Test User",
    }
    auth_client = MagicMock()
    auth_client.auth.get_user.return_value = user

    db = mock_db({
        "profiles": {"id": user_id, "role": role, "school_id": school_id, "status": "active"},
        **table_rows,
    })

    return [
        patch("app.utils.auth.get_auth_client", return_value=auth_client),
        patch("app.utils.auth.get_supabase", return_value=db),
        patch("app.services.midtrans_service.is_school_active", return_value=True),
    ]


def enter(patches):
    for p in patches:
        p.start()
    return patches


def exit_patches(patches):
    for p in reversed(patches):
        p.stop()


# ── Tests for @require_school_access decorator ────────────────

class TestRequireSchoolAccess:
    """Direct tests of the require_school_access decorator logic."""

    def test_direct_school_mismatch_returns_403(self, app):
        """When user_school_id differs from resource school_id → 403."""
        from app.decorators.security import require_school_access

        # JSON callers get a 403 body; HTML callers get abort(403) so the
        # browser sees the rendered error page.
        with app.test_request_context("/exams/exam-999", headers={"Accept": "application/json"}):
            flask.g.user_id = "teacher-a"
            flask.g.user_school_id = "school-a"
            flask.g.user_role = "guru"

            # require_school_access resolves its client from app.utils.auth
            with patch("app.utils.auth.get_supabase") as mock_get_db:
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

        with app.test_request_context("/exams/exam-111", headers={"Accept": "application/json"}):
            flask.g.user_id = "teacher-a"
            flask.g.user_school_id = "school-a"
            flask.g.user_role = "guru"

            with patch("app.utils.auth.get_supabase") as mock_get_db:
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

        with app.test_request_context("/grade/sub-999", headers={"Accept": "application/json"}):
            flask.g.user_id = "teacher-a"
            flask.g.user_school_id = "school-a"
            flask.g.user_role = "guru"

            with patch("app.utils.auth.get_supabase") as mock_get_db:
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

    def _assert_school_block(self, resp):
        """403 must come from the school check, not from auth/CSRF noise."""
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.data[:300]}"
        body = resp.get_json() or {}
        assert "sekolah" in (body.get("error") or "").lower()

    def test_teacher_cannot_access_exam_from_different_school(self, client):
        """Teacher A gets 403 when fetching an exam owned by School B."""
        patches = enter(auth_patches({"exams": {"school_id": "school-b"}}))
        try:
            resp = client.get("/teacher/exams/exam-from-school-b", headers=BEARER)
        finally:
            exit_patches(patches)
        self._assert_school_block(resp)

    def test_teacher_can_access_own_school_exam(self, client):
        """Same request with a matching school must NOT be blocked by RBAC."""
        patches = enter(auth_patches({"exams": {"school_id": "school-a"}}))
        try:
            resp = client.get("/teacher/exams/exam-own-school", headers=BEARER)
        finally:
            exit_patches(patches)
        assert resp.status_code != 403, resp.data[:300]

    def test_teacher_cannot_grade_submission_from_different_school(self, client):
        """Teacher A gets 403 when grading a submission whose exam is School B's."""
        patches = enter(auth_patches({
            "submissions": {"exam_id": "exam-b"},
            "exams": {"school_id": "school-b"},
        }))
        try:
            resp = client.get("/teacher/grade/sub-from-school-b", headers=BEARER)
        finally:
            exit_patches(patches)
        self._assert_school_block(resp)

    def test_admin_cannot_delete_student_from_different_school(self, client):
        """Admin A gets 403 when deleting a student from School B."""
        patches = enter(auth_patches(
            {"students": {"school_id": "school-b"}},
            user_id="admin-a", role="admin_sekolah",
        ))
        try:
            resp = client.post("/admin-sekolah/students/student-from-school-b/delete", headers=BEARER)
        finally:
            exit_patches(patches)
        self._assert_school_block(resp)


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
