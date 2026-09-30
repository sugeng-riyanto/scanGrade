"""The demo-password repair button must be able to reach the accounts it repairs.

Measured on the live box, 2026-09-30: clicking "reset demo passwords" answered
``{"ok": 0, "results": [], "total": 0}`` — success, doing nothing. Two causes, both
proven here against the real view function:

* it iterated ``supabase.auth.admin.list_users()``, which answers with a **page** (50
  by default) of a listing of 800+; the demo accounts are nowhere near the front, so
  the loop matched nobody. The repo already has ``list_all_auth_users`` for exactly
  this, and other pages already use it;
* even once it could see them, its own account list omitted ``officials`` — the school
  principals and vice-principals the seed creates and ``/demo`` offers — so a repair
  would have left every official's login broken.

The view is called directly (its body, via ``__wrapped__``) with the seed and the
Supabase client stubbed, so this asserts behaviour rather than the shape of the
source: the paged listing must never be called, every seeded account including the
officials must be updated, and an account the database does not have must be reported
rather than silently counted as repaired.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from tests.conftest import app_instance
from app.routes import super_admin as sa

SUPER = {"email": "superadmin@scan-grade.app", "password": "superadmin123"}
SCHOOL = {
    "admin": {"email": "admin_smp@scan-grade.app", "password": "demo123"},
    "officials": [
        {"email": "principal_smp@scan-grade.app", "password": "demo123"},
        {"email": "vice_principal_smp@scan-grade.app", "password": "demo123"},
    ],
    "teachers": [
        {"email": "guru_mtk_smp@scan-grade.app", "password": "demo123"},
        {"email": "guru_ipa_smp@scan-grade.app", "password": "demo123"},
    ],
    "students": [{"email": "siswa1_smp@scan-grade.app", "password": "demo123"}],
}
ALL_EMAILS = {SUPER["email"]} | {
    u["email"] for u in [SCHOOL["admin"], *SCHOOL["officials"],
                         *SCHOOL["teachers"], *SCHOOL["students"]]}


class _Admin:
    """The two auth calls the route can make, and a record of what it made."""

    def __init__(self, calls):
        self._c = calls

    def update_user_by_id(self, uid, payload):
        self._c["updated"][uid] = payload

    def list_users(self, *a, **k):          # the paged one — must never be reached
        self._c["paged_calls"] += 1
        raise AssertionError(
            "the route called the paged list_users(); the demo accounts are not on "
            "its first page, so this is the bug, not the test")


class _Supabase:
    def __init__(self, calls):
        self.auth = SimpleNamespace(admin=_Admin(calls))


@pytest.fixture()
def calls():
    return {"updated": {}, "paged_calls": 0}


@pytest.fixture()
def seeded(monkeypatch, calls):
    """The route, with the seed and the auth listing under this test's control."""
    fake_manage = types.ModuleType("manage")
    fake_manage.DEMO_USERS = {"super_admin": SUPER}
    fake_manage.DEMO_SCHOOLS = [SCHOOL]
    monkeypatch.setitem(sys.modules, "manage", fake_manage)
    monkeypatch.setattr(sa, "get_supabase", lambda: _Supabase(calls))

    # 60 filler accounts in front of the demo ones: enough that a first page of 50
    # cannot see them, which is exactly what the box's listing looked like.
    def listing(*, per_page: int = 1000, **k):
        users = [SimpleNamespace(email=f"filler{i}@example.com", id=f"filler-{i}")
                 for i in range(60)]
        users += [SimpleNamespace(email=e, id=f"id-{e}") for e in sorted(ALL_EMAILS)]
        return users

    monkeypatch.setattr(sa, "list_all_auth_users", listing)
    return calls, listing


def _call_route():
    body = sa.reset_demo_passwords.__wrapped__()
    return body.get_json()


def test_it_updates_every_seeded_account_including_the_officials(seeded):
    calls, _ = seeded
    with app_instance().test_request_context("/super-admin/reset-demo-passwords",
                                             method="POST"):
        payload = _call_route()
    assert payload["total"] == len(ALL_EMAILS), payload
    assert payload["ok"] == len(ALL_EMAILS), (
        f"the repair did not reach every account: {payload}")
    assert calls["paged_calls"] == 0, (
        "the paged listing was called — the demo accounts are past its first page")
    got = {calls["updated"][f"id-{e}"]["password"] for e in ALL_EMAILS}
    assert got == {"demo123", "superadmin123"}, (
        f"the passwords written are not the seeded ones: {got}")
    officials = calls["updated"]["id-principal_smp@scan-grade.app"]["password"]
    assert officials == "demo123", (
        "the school officials were left out of the repair, so a principal's demo "
        "login stays broken while /demo offers it")


def test_an_account_the_database_does_not_have_is_reported_not_counted(seeded, monkeypatch):
    calls, _ = seeded

    def partial(*, per_page: int = 1000, **k):
        # Everyone but the vice-principal, which is how a half-seeded database looks.
        return [SimpleNamespace(email=e, id=f"id-{e}")
                for e in ALL_EMAILS
                if e != "vice_principal_smp@scan-grade.app"]

    monkeypatch.setattr(sa, "list_all_auth_users", partial)
    with app_instance().test_request_context("/super-admin/reset-demo-passwords",
                                             method="POST"):
        payload = _call_route()
    missing = [r for r in payload["results"] if r.get("error") == "no such auth user"]
    assert [r["email"] for r in missing] == ["vice_principal_smp@scan-grade.app"], payload
    assert payload["ok"] == len(ALL_EMAILS) - 1, (
        "a missing account was counted as repaired, which is the same lie the "
        "button told when it reached nobody at all")
