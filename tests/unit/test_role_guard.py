"""Staff-only endpoints must check the ROLE, not just the school.

``require_school_access`` answers "is this row from your school?". It does not
answer "are you allowed to do this at all". Endpoints guarded by school access
alone were reachable by every signed-in member of that school — students
included. ``/api/exams/<id>/report`` returned a whole class's names, NISN and
scores to any student who asked, and ``/api/transaction/status`` returned
payment records, activation code included, for an arbitrary order id.

These tests cover the decorator and pin the endpoints that carry it, so the
guard cannot quietly disappear again.
"""
import ast
import re
from pathlib import Path

import pytest
from flask import Flask, g, jsonify

from app.decorators.security import require_role, STAFF_ROLES

API_SRC = Path(__file__).resolve().parents[2] / "app" / "routes" / "api.py"


def _app_for(role):
    app = Flask(__name__)

    @app.route("/thing")
    @require_role(*STAFF_ROLES)
    def thing():
        return jsonify({"ok": True})

    @app.route("/api/thing")
    @require_role(*STAFF_ROLES)
    def api_thing():
        return jsonify({"ok": True})

    @app.before_request
    def _set_role():
        if role is not None:
            g.user_role = role

    return app


@pytest.mark.parametrize("role", ["guru", "admin_sekolah", "super_admin"])
def test_staff_roles_are_allowed(role):
    r = _app_for(role).test_client().get("/thing")
    assert r.status_code == 200


def test_a_student_is_refused():
    assert _app_for("murid").test_client().get("/thing").status_code == 403


def test_a_missing_role_is_refused():
    """Fail closed: no role means no access, never 'assume the best'."""
    assert _app_for(None).test_client().get("/thing").status_code == 403


def test_api_paths_get_a_json_error():
    r = _app_for("murid").test_client().get("/api/thing")
    assert r.status_code == 403
    assert r.is_json and r.get_json()["error"]


# ── the endpoints this test was written for ──────────────────────

GUARDED = {
    "exam_report": ("guru", "admin_sekolah", "super_admin"),
    "scan_essay": ("guru", "admin_sekolah", "super_admin"),
    "vision_canvas_ocr": ("guru", "admin_sekolah", "super_admin"),
    "scan_task_status": ("guru", "admin_sekolah", "super_admin"),
    "ai_test_key": ("guru", "admin_sekolah", "super_admin"),
    "transaction_status": ("admin_sekolah", "super_admin"),
    "redeem_activation_code": ("admin_sekolah", "super_admin"),
}


@pytest.mark.parametrize("func", sorted(GUARDED))
def test_endpoint_carries_a_role_guard(func):
    src = API_SRC.read_text(encoding="utf-8-sig")
    lines = src.splitlines()
    tree = ast.parse(src)

    target = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == func)
    decorators = "\n".join(lines[target.lineno - len(target.decorator_list) - 1:target.lineno - 1])

    assert "require_role" in decorators, (
        f"{func} has no @require_role — school access alone would let a murid "
        f"reach it"
    )
    # STAFF_ROLES is the common case; the payment endpoints name their roles.
    if "STAFF_ROLES" not in decorators:
        for role in GUARDED[func]:
            assert role in decorators, f"{func} is missing the {role!r} role"


def test_guard_sits_under_login_required():
    """Decorators run bottom-up, so role checks that sit ABOVE @login_required
    run before the session is applied to g and would see no role at all."""
    src = API_SRC.read_text(encoding="utf-8-sig")
    lines = src.splitlines()

    for m in re.finditer(r"^(?P<indent>\s*)@require_role\((?P<roles>[^)]*)\)\s*$",
                         src, re.MULTILINE):
        lineno = src[:m.start()].count("\n")
        above = lines[lineno - 1] if lineno else ""
        assert "login_required" in above, (
            f"@require_role on line {lineno + 1} is not under @login_required; "
            f"g.user_role would not be set yet"
        )
