"""Fase 9 — approving is shared by three roles, reopening is not.

The asymmetry is a product decision, and a decision that only lives in a route
decorator is one forgotten decorator away from being reversed. So it is tested in
three places, and each answers a different question:

* **the service** refuses a principal and a vice-principal *before any write* — the
  row is not touched, not merely left with the wrong status. That is what protects
  any future caller that forgets the decorator;
* **the route** keeps `admin_sekolah_required` on the reopen door while the decide
  and close doors carry the three roles, and the page renders the reopen control
  only for the reader who may use it: a button that would refuse its reader teaches
  people that buttons lie;
* **the rule is uniform for every kind of closure.** There are two provenances in
  this codebase — the admin's `deactivate` (which now writes the canonical `closed`)
  and the join-request flow from migration 063 — and they are the *same state*, so
  they must answer to the same reopen rule. A rule that only covered the flow this
  feature added would be the exact leak the canonicalisation exists to close.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services import school_membership as membership
from tests.unit.membership_fixtures import FakeDb, membership as mrow, person

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "app" / "routes" / "membership_routes.py"
TEMPLATES = ROOT / "app" / "templates"


def _closed_by_admin(**extra) -> dict:
    """A closure from the admin's own offboarding path: `deactivate` was run."""
    return mrow(school_id="sc-1", status="closed", closed_by="a-1",
                closed_at="2026-09-01T00:00:00+00:00", **extra)


def _closed_by_request_flow(**extra) -> dict:
    """The same state, reached from the join-request flow (migration 063).

    No `closed_by`: the flow that closed it is not a person pressing a button.
    """
    return mrow(school_id="sc-1", status="closed", **extra)


# ── the asymmetry, at the service ────────────────────────────────────────────

class TestOnlyTheSchoolAdminReopens:
    def test_the_reopen_roles_are_a_strict_subset_of_the_approver_roles(self):
        assert membership.REOPEN_ROLES == ("admin_sekolah",)
        assert membership.may_reopen("principal") is False
        assert membership.may_reopen("vice_principal") is False
        assert set(membership.REOPEN_ROLES) < set(membership.APPROVER_ROLES), (
            "reopening is one role and approving is three; that difference is the "
            "decision, so it is asserted rather than described")

    @pytest.mark.parametrize("role", ["principal", "vice_principal", "guru",
                                      "murid", "super_admin", None])
    def test_any_other_role_is_refused_and_writes_nothing(self, role):
        db = FakeDb(teacher_school_membership=[_closed_by_admin()])
        out = membership.reopen_membership(db, "sc-1", "u-1", actor_id="a-2",
                                           actor_role=role)
        assert out == {"ok": False, "reason": "not_authorised"}
        assert db.writes == [], f"{role} changed a row on a refused reopen"
        assert db.row("teacher_school_membership", school_id="sc-1")["status"] == "closed"

    def test_the_school_admin_reopens_it(self):
        db = FakeDb(teacher_school_membership=[_closed_by_admin()])
        out = membership.reopen_membership(db, "sc-1", "u-1", actor_id="a-1",
                                           actor_role="admin_sekolah")
        assert out["ok"] is True
        row = db.row("teacher_school_membership", school_id="sc-1")
        assert row["status"] == "active" and row["reopened_by"] == "a-1"
        assert row["reopened_at"]

    def test_reopening_keeps_the_record_of_the_closure(self):
        """A reopen is a new fact, not an erasure of the old one — so the payload
        does not carry the closure's fields at all."""
        db = FakeDb(teacher_school_membership=[_closed_by_admin()])
        membership.reopen_membership(db, "sc-1", "u-1", actor_id="a-1",
                                     actor_role="admin_sekolah")
        payload = db.write_payloads("teacher_school_membership", "update")[0]
        assert "closed_at" not in payload and "closed_by" not in payload
        assert db.row("teacher_school_membership", school_id="sc-1")["closed_at"]

    @pytest.mark.parametrize("status", ["active", "invited"])
    def test_a_row_that_is_not_closed_is_not_reopened(self, status):
        db = FakeDb(teacher_school_membership=[mrow(school_id="sc-1", status=status)])
        out = membership.reopen_membership(db, "sc-1", "u-1", actor_id="a-1",
                                           actor_role="admin_sekolah")
        assert out == {"ok": False, "reason": "not_closed_or_missing"}
        assert db.writes == []

    def test_a_membership_in_another_school_is_not_reopened(self):
        db = FakeDb(teacher_school_membership=[_closed_by_admin()])
        out = membership.reopen_membership(db, "sc-9", "u-1", actor_id="a-1",
                                           actor_role="admin_sekolah")
        assert out["ok"] is False and db.writes == []


class TestTheRuleIsUniformForEveryClosureKind:
    @pytest.mark.parametrize("closed", [_closed_by_admin, _closed_by_request_flow])
    def test_both_provenances_reopen_the_same_way(self, closed):
        db = FakeDb(teacher_school_membership=[closed()])
        out = membership.reopen_membership(db, "sc-1", "u-1", actor_id="a-1",
                                           actor_role="admin_sekolah")
        assert out["ok"] is True
        assert db.row("teacher_school_membership", school_id="sc-1")["status"] == "active"

    @pytest.mark.parametrize("closed", [_closed_by_admin, _closed_by_request_flow])
    def test_both_provenances_refuse_a_principal(self, closed):
        db = FakeDb(teacher_school_membership=[closed()])
        assert membership.reopen_membership(db, "sc-1", "u-1", actor_id="a-2",
                                            actor_role="principal")["ok"] is False
        assert db.writes == []

    @pytest.mark.parametrize("closed", [_closed_by_admin, _closed_by_request_flow])
    def test_both_provenances_cannot_be_re_entered(self, closed):
        """The way back is the reopen, for either provenance: neither may be
        side-stepped by a fresh application or by an approval."""
        db = FakeDb(schools=[{"id": "sc-1", "name": "SMP 1", "npsn": "1",
                              "city": "B", "status": "active"}],
                    teacher_school_membership=[closed()],
                    school_membership_request=[
                        {"id": "r-1", "teacher_id": "u-1", "target_school_id": "sc-1",
                         "status": "pending"}])
        assert membership.create_request(
            db, "u-1", "sc-1", "guru",
            document_version=membership.CONSENT_DOCUMENT_VERSION,
            document_sha256=membership.consent_sha256())["reason"] == "membership_closed"
        assert membership.decide_request(db, "sc-1", "r-1", "approved", actor_id="a-1",
                                         actor_role="principal")["reason"] == "membership_closed"
        assert db.writes == []


# ── the asymmetry, at the route ──────────────────────────────────────────────

def _code(path: Path, name: str) -> str:
    """One function's decorators and body as code, with its prose left behind.

    Read through `ast` rather than by slicing the file, because every one of these
    guards is a statement about what the *code* does — and the modules here explain
    themselves at length, so a slice reads the explanation. (A guard that fires on a
    sentence gets deleted, which is how the property it defended is lost.)
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:]
            return ast.unparse(node)
    raise AssertionError(f"{name} is gone from {path.name}")


def test_the_reopen_door_is_the_school_admins_alone():
    block = _code(ROUTES, "membership_reopen")
    assert "admin_sekolah_required" in block, (
        "the reopen route lost its role guard")
    # ...and the service is asked again, with the role — two layers, not one.
    assert "actor_role=" in block and "user_role" in block, (
        "the route no longer passes the role, so the service cannot refuse it")


def test_decide_and_close_stay_open_to_the_three_approver_roles():
    for name in ("membership_request_decide", "membership_close"):
        block = _code(ROUTES, name)
        assert "role_required" in block, f"{name} has no role guard"
        for role in ("admin_sekolah", "principal", "vice_principal"):
            assert role in block, f"{name} no longer admits {role}"
        assert "admin_sekolah_required" not in block, (
            f"{name} was narrowed to the school admin; the decision is shared")


def test_the_service_is_the_second_layer_and_refuses_before_writing():
    """A decorator protects the door it is written on. The service protects every
    caller — which is why the refusal is asserted there as well."""
    assert "reopen_membership(" in ROUTES.read_text(encoding="utf-8")
    body = _code(ROOT / "app" / "services" / "school_membership.py",
                 "reopen_membership")
    assert body.index("may_reopen") < body.index("table"), (
        "the role check must precede the write it guards")


def test_the_page_renders_the_reopen_control_only_for_the_school_admin(app):
    """Read as a rendered page, because a control is a claim about who may act."""
    from flask import g

    member = {"user_id": "u-1", "name": "Budi Guru", "email": "b@example.test",
              "school_role": "guru", "status": "closed", "joined_at": "2026-08-01T00:00:00+00:00",
              "closed_on": "2026-09-01", "reopened_at": None,
              "closed": True}

    def render(role, may_reopen):
        with app.test_request_context("/admin-sekolah/membership"):
            g.user_id, g.user_name, g.user_role = "a-1", "Admin", role
            g.user_email, g.tz_offset, g.show = "a@example.test", 7, {}
            g.user_school_id, g.user_class_id = "sch-1", None
            return app.jinja_env.get_template(
                "admin_sekolah/membership.html").render(
                    join_requests=[], members=[member], may_reopen=may_reopen,
                    may_approve=True, status_code="")

    admin = render("admin_sekolah", True)
    assert "Buka kembali" in admin and "Reopen" in admin, (
        "the school admin has no control to reopen a closed membership")
    assert 'action="/admin-sekolah/memberships/u-1/reopen"' in admin

    principal = render("principal", False)
    assert "Buka kembali" not in principal, (
        "a principal is shown a reopen button that would refuse them")
    assert "Hanya admin sekolah" in principal and "School admin only" in principal, (
        "the principal is not told who may reopen, so the missing control reads "
        "as a missing feature")


def test_the_members_read_returns_a_closed_row():
    """There has to be a row to reopen: a read that filtered to `active` would
    leave the reopen control with nothing to act on."""
    db = FakeDb(teacher_school_membership=[_closed_by_admin()],
                profiles=[person("u-1")])
    out = membership.school_members(db, "sc-1", "admin_sekolah")
    assert out["ok"] is True
    assert [m["status"] for m in out["members"]] == ["closed"]
    assert out["members"][0]["closed"] is True
    assert out["members"][0]["name"] == "Budi Guru"


def test_the_page_shows_a_closed_membership_and_the_closure_it_reports(app):
    from flask import g

    with app.test_request_context("/admin-sekolah/membership"):
        g.user_id, g.user_name, g.user_role = "a-1", "Admin", "admin_sekolah"
        g.user_email, g.tz_offset, g.show = "a@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", None
        html = app.jinja_env.get_template("admin_sekolah/membership.html").render(
            join_requests=[], may_reopen=True, may_approve=True, status_code="closed",
            members=[{"user_id": "u-1", "name": "Budi Guru",
                      "email": "b@example.test", "school_role": "guru",
                      "status": "closed", "joined_at": "2026-08-01T00:00:00+00:00",
                      "closed_on": "2026-09-01", "reopened_at": None,
                      "closed": True}])
    assert "Budi Guru" in html and "Ditutup" in html and "Closed" in html
    assert "Keanggotaan ditutup" in html, (
        "the outcome of a closure is not reported to the reader who closed it")


def test_the_queue_page_sits_behind_its_own_role_guard():
    block = _code(ROUTES, "membership_home")
    for role in ("admin_sekolah", "principal", "vice_principal"):
        assert role in block, "the membership page is reachable by roles that may not decide"


def test_the_two_doors_are_linked_from_the_sidebar():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert base.count('href="/admin-sekolah/membership"') >= 2, (
        "only one of the two branches that may decide has a door to the page")
    assert "Keanggotaan" in base and "Membership" in base
