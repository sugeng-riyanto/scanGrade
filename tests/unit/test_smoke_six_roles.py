"""The smoke gate signs in as six roles, and a stale credential is a failure.

Phase 0 found two holes. The gate checked four roles, so a principal or vice
principal whose login was broken would never be noticed. And a *configured*
account that could not sign in was only a "note" in the gate path — the run
carried on and reported PASS, so a stale password silently stopped checking that
role. Production had exactly that: one role's password had drifted and the release
kept being waved through.

Both are fixed here. These tests drive the gate's own `main()` with a mocked HTTP
layer, so no network is touched: they assert the six-role roster, the wiring for
each role's area, and that a refused credential returns a failing exit code.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SMOKE_PY = ROOT / "deploy" / "smoke_test.py"

SIX = ("super_admin", "admin_sekolah", "principal", "vice_principal", "guru", "murid")


def smoke():
    spec = importlib.util.spec_from_file_location("sg_smoke_test_roles", SMOKE_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── the roster ───────────────────────────────────────────────────────────────


class TestTheRoster:
    def test_six_roles_are_checked(self):
        assert set(smoke().ROLES) == set(SIX), (
            "the smoke gate no longer covers all six roles; principal and vice "
            "principal would go unverified on every release")

    def test_every_matrix_names_every_role(self):
        sm = smoke()
        for name in ("ROLE_PAGES", "ROLE_AREAS", "FORBIDDEN", "LOGIN_PATHS"):
            assert set(getattr(sm, name)) == set(SIX), f"{name} is missing roles"

    def test_the_officials_sign_in_through_the_user_door(self):
        paths = smoke().LOGIN_PATHS
        assert paths["principal"] == "/auth/login-user"
        assert paths["vice_principal"] == "/auth/login-user"

    def test_the_officials_are_forbidden_from_higher_areas_only(self):
        forbidden = smoke().FORBIDDEN
        assert set(forbidden["principal"]) == {"super_admin", "admin_sekolah"}
        assert set(forbidden["vice_principal"]) == {"super_admin", "admin_sekolah"}
        # A teacher and a pupil have no business on either official page.
        assert {"principal", "vice_principal"} <= set(forbidden["guru"])
        assert {"principal", "vice_principal"} <= set(forbidden["murid"])


# ── the run, with a mocked HTTP layer ────────────────────────────────────────


def _run(monkeypatch, accounts, outcomes, *, check_credentials=False):
    sm = smoke()
    monkeypatch.setattr(sm, "creds_from_env", lambda: (accounts, []))
    monkeypatch.setattr(sm, "login", lambda session, base, acct, res: _login(res, outcomes))
    for name in ("check_pages", "check_isolation", "check_exam_sitting",
                 "check_admin_write"):
        monkeypatch.setattr(sm, name, lambda *a, **k: None)
    argv = ["smoke_test.py"] + (["--check-credentials"] if check_credentials else [])
    monkeypatch.setattr(sys, "argv", argv)
    return sm.main()


def _login(res, outcomes):
    outcome = outcomes["next"]
    if outcome == "ok":
        res.reachable = True
    return outcome


def _accounts(roles):
    return [smoke().Account(role, f"{role}@x", "pw") for role in roles]


class TestAStaleCredentialFailsTheGate:
    def test_one_refused_role_is_a_failure(self, monkeypatch):
        """The defect: a present-but-refused credential used to be a note."""
        outcomes = {"next": "ok"}

        def login(session, base, acct, res):
            res.reachable = True
            return "rejected" if acct.role == "guru" else "ok"

        sm = smoke()
        monkeypatch.setattr(sm, "creds_from_env", lambda: (_accounts(SIX), []))
        monkeypatch.setattr(sm, "login", login)
        for name in ("check_pages", "check_isolation", "check_exam_sitting",
                     "check_admin_write"):
            monkeypatch.setattr(sm, name, lambda *a, **k: None)
        monkeypatch.setattr(sys, "argv", ["smoke_test.py"])
        assert sm.main() == 1, (
            "a configured account that could not sign in did not fail the gate")

    def test_all_six_signing_in_passes(self, monkeypatch):
        def login(session, base, acct, res):
            res.reachable = True
            return "ok"

        sm = smoke()
        monkeypatch.setattr(sm, "creds_from_env", lambda: (_accounts(SIX), []))
        monkeypatch.setattr(sm, "login", login)
        for name in ("check_pages", "check_isolation", "check_exam_sitting",
                     "check_admin_write"):
            monkeypatch.setattr(sm, name, lambda *a, **k: None)
        monkeypatch.setattr(sys, "argv", ["smoke_test.py"])
        assert sm.main() == 0


class TestArmingNeedsEveryRole:
    def test_check_credentials_with_a_missing_role_fails(self, monkeypatch):
        def login(session, base, acct, res):
            res.reachable = True
            return "ok"

        sm = smoke()
        monkeypatch.setattr(sm, "creds_from_env",
                            lambda: (_accounts(["super_admin", "admin_sekolah",
                                                "guru", "murid"]), []))
        monkeypatch.setattr(sm, "login", login)
        monkeypatch.setattr(sys, "argv", ["smoke_test.py", "--check-credentials"])
        assert sm.main() == 1, (
            "arming accepted a config with principal/vice_principal missing, so "
            "the gate would be armed to check two roles it cannot sign in as")


class TestAMalformedCredentialIsNotSkipped:
    def test_a_malformed_entry_is_reported(self, monkeypatch):
        sm = smoke()
        monkeypatch.setenv("SMOKE_GURU", "not-an-email-and-password-pair")
        for role in SIX:
            if role != "guru":
                monkeypatch.setenv(f"SMOKE_{role.upper()}", f"{role}@x:pw")
        accounts, malformed = sm.creds_from_env()
        assert "guru" in malformed, (
            "a malformed credential was skipped silently instead of named")
        assert all(a.role != "guru" for a in accounts)
