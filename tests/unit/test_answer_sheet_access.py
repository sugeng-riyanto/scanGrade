"""The answer-sheet tool builds a blank sheet to *print* — an internal tool.

`/tools/generate-answer-sheet` sat behind `login_required` alone, so any pupil
with a session could open the builder and POST to it, and download the very
answer sheet the exam is meant to be graded from. The rest of the tools
blueprint already answers this with `staff_required` (`/tools/device-preview`);
this route was the one that stayed on `login_required`.

These tests pin the rule the way `test_device_preview.py` does: staff get it, a
pupil is refused, an anonymous visitor is sent to the door — and the GET (the
builder page) *and* the POST (the PDF download) are both guarded, because a
decorator on the route covers one but a test that only opens the page would not
notice the other.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROUTE = ROOT / "app" / "routes" / "tools.py"

SESSION = {
    "user_id": "user-1",
    "email": "guru@example.test",
    "name": "Guru",
    "role": "guru",
    "school_id": "school-1",
    "class_id": None,
    "status": "active",
}

PAGE_URL = "/tools/generate-answer-sheet"


@pytest.fixture(autouse=True)
def _clear_session_cache():
    """Every client here authenticates with the literal token "fake-token".

    Without clearing the keyed session cache, the role a previous test asked for
    would be replayed for the next one.
    """
    from app.utils import kv_cache
    kv_cache._local.clear()
    yield
    kv_cache._local.clear()


def request_as(app, role, method="GET", json_body=None):
    """A request whose session resolves to *role*, without touching Supabase."""
    client = app.test_client()
    with patch("app.utils.auth._fetch_session", return_value=dict(SESSION, role=role)):
        headers = {"Authorization": "Bearer fake-token", "Accept": "text/html"}
        if method == "POST":
            return client.post(PAGE_URL, headers=headers,
                               json=json_body or {"total_questions": 50})
        return client.get(PAGE_URL, headers=headers)


class TestOnlyStaffReachTheAnswerSheetTool:
    @pytest.mark.parametrize("role", ["guru", "admin_sekolah", "super_admin"])
    def test_staff_may_open_the_builder(self, app, role):
        response = request_as(app, role)
        assert response.status_code == 200

    @pytest.mark.parametrize("role", ["guru", "admin_sekolah", "super_admin"])
    def test_staff_may_download_a_sheet(self, app, role):
        response = request_as(app, role, method="POST", json_body={"total_questions": 10})
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"

    def test_a_student_may_not_open_the_builder(self, app):
        response = request_as(app, "murid")
        assert response.status_code == 403, (
            "a pupil must not be able to open the answer-sheet builder")

    def test_a_student_may_not_download_a_sheet(self, app):
        response = request_as(app, "murid", method="POST", json_body={"total_questions": 10})
        assert response.status_code == 403, (
            "a pupil must not be able to download an answer sheet")
        assert response.mimetype != "application/pdf"

    def test_an_anonymous_visitor_is_sent_to_the_door(self, app):
        response = app.test_client().get(PAGE_URL)
        assert response.status_code in (301, 302, 303)
        assert "/auth/" in response.headers.get("Location", "")


class TestTheDecoratorIsTheSharedOne:
    def test_the_route_uses_staff_required_not_login_required(self):
        src = TOOLS_ROUTE.read_text(encoding="utf-8")
        start = src.index("def generate_answer_sheet_route(")
        # The decorators are the contiguous `@…` lines directly above the def.
        before = src[:start].rstrip().splitlines()
        decorators = []
        for line in reversed(before):
            if line.strip().startswith("@"):
                decorators.append(line.strip())
            elif line.strip() == "":
                continue
            else:
                break
        assert "@staff_required" in decorators, (
            "the answer-sheet route must use the blueprint's own staff_required:\n  "
            + "\n  ".join(reversed(decorators)))
        assert "@login_required" not in decorators, (
            "`login_required` alone lets a pupil open an internal tool")
