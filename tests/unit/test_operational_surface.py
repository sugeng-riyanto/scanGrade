"""Two things a public repo and a signed-in pupil could both reach, and should not.

The first is source hygiene: `flask_err.txt` and `flask_out.txt` are development
server logs. They were committed once and then `.gitignore`d, but a tracked file
is tracked whatever the ignore list says — so a fresh clone carries another
machine's dev-server output, and a developer who happens to keep their own there
would have it staged by the next `git add -A`. Removing them from the *index*
(they stay on disk) is the whole fix, and the guard has to ask git, not the
ignore list, because the ignore list is exactly what failed to keep them out.

The second is `/metrics`. It renders the box's own CPU, memory and disk — a
super-admin's reading. It was guarded on `@login_required`, so any signed-in
pupil could read the box's load by loading one URL, and `/metrics/processes`
right beside it already answers the same kind of question only to a super admin.
`/health` is its opposite and must *not* be hardened by mistake: the deploy
runner curls it with no session, so a login door there would take the release
gate's own probe offline.
"""
from __future__ import annotations

import pathlib

import pytest

from tests.unit.git_env import git

REPO = pathlib.Path(__file__).resolve().parents[2]
INIT = REPO / "app" / "__init__.py"

#: Written by every `flask run`; never source.
DEV_LOGS = ("flask_err.txt", "flask_out.txt")


class TestTheDevServerLogsAreNotSource:
    def test_neither_log_is_tracked(self):
        listed = git("ls-files", cwd=str(REPO), check=True).stdout.splitlines()
        tracked = {line.strip() for line in listed}
        for name in DEV_LOGS:
            assert name not in tracked, (
                f"{name} is committed to the repository; it is a dev-server log "
                f"and belongs on disk only")

    def test_the_ignore_list_still_covers_them(self):
        """The index entry was removed *because* the ignore list covers it — if
        that ever stops being true, removing the file is not the fix."""
        ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
        assert "flask_*.txt" in ignored, (
            "the fix for the tracked log depends on the ignore rule that keeps it "
            "from coming back")


class TestTheAppMetricsAreASuperAdminRead:
    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @staticmethod
    def _sign_in(monkeypatch, role):
        def session_for(token):                       # noqa: ARG001
            return {"user_id": "u-1", "role": role, "status": "active",
                    "email": "someone@example.id", "name": "Someone",
                    "school_id": None, "class_id": None}
        monkeypatch.setattr("app.utils.auth._session_for", session_for)

    @staticmethod
    def _present_a_token(client):
        client.set_cookie("access_token", "a-token")

    def test_a_student_is_sent_away(self, client, monkeypatch):
        self._sign_in(monkeypatch, "murid")
        self._present_a_token(client)
        r = client.get("/metrics")
        assert r.status_code == 302, "a pupil must not read the box's load"
        assert "/student/dashboard" in r.headers["Location"]

    def test_a_teacher_is_sent_away_too(self, client, monkeypatch):
        self._sign_in(monkeypatch, "guru")
        self._present_a_token(client)
        r = client.get("/metrics")
        assert r.status_code == 302
        assert "/teacher/dashboard" in r.headers["Location"]

    def test_a_super_admin_gets_the_reading(self, client, monkeypatch):
        self._sign_in(monkeypatch, "super_admin")
        self._present_a_token(client)
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/plain")
        assert b"scangrade_requests_total" in r.data

    def test_no_session_is_sent_to_a_login_door(self, client):
        r = client.get("/metrics")
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_the_route_carries_the_super_admin_guard(self):
        src = INIT.read_text(encoding="utf-8")
        block = src.split('@app.route("/metrics")', 1)[1]
        block = block.split("def metrics", 1)[0]
        assert "@super_admin_required" in block, (
            "the app metrics are a super-admin read; a bare @login_required hands "
            "the box's CPU, memory and disk to every signed-in pupil")


class TestHealthStaysAMachineDoor:
    def test_no_session_gets_the_commit_report(self, app):
        """The deploy runner's probe has no cookie: a login door here would take
        the release gate's own health check offline."""
        r = app.test_client().get("/health")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert "commit" in body

    def test_the_route_is_not_wrapped_in_a_login_guard(self):
        src = INIT.read_text(encoding="utf-8")
        block = src.split('@app.route("/health")', 1)[1]
        block = block.split("def health", 1)[0]
        assert "@login_required" not in block
        assert "@super_admin_required" not in block
