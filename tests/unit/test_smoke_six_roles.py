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
from types import SimpleNamespace

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

    def test_every_role_signs_in_through_the_one_page_with_its_own_hint(self):
        """The merge made the two doors aliases of one page.

        This used to assert `/auth/login-user` for the two officials, which was the
        door they belonged on while there were two. The page is one now, and a gate
        that signs in through an alias proves the *alias* while the page every reader
        uses goes unverified — so the roster names the merged URL with the `?role=`
        hint the app's own `login_door_for` hands that role.
        """
        sm = smoke()

        assert set(sm.LOGIN_PATHS.values()) == {
            f"/auth/sign-in?role={role}" for role in SIX}
        assert sm.LOGIN_ALIASES == ("/auth/login", "/auth/login-user"), (
            "the published URLs are no longer walked by the gate, so a release could "
            "break a link a school has already handed out and still report PASS")

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
                 "check_admin_write", "check_aliases"):
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
                     "check_admin_write", "check_aliases"):
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
                     "check_admin_write", "check_aliases"):
            monkeypatch.setattr(sm, name, lambda *a, **k: None)
        monkeypatch.setattr(sys, "argv", ["smoke_test.py"])
        assert sm.main() == 0


class TestTheAliasContract:
    """The two published URLs: a GET forwards, and a POST signs in.

    Both halves are asserted because only one of them is obvious. A GET that 404s
    breaks a bookmark or a link on a page the school already has — visible. A POST
    answered with a forward is the quieter failure: the credentials are thrown away
    and the reader is handed an empty form, which looks like a wrong password. So
    this is driven through a fake HTTP layer that can answer a POST with a *form*.
    """

    BASE = "http://smoke.invalid"

    def _drive(self, monkeypatch, answers):
        """Run `check_aliases` against canned responses, keyed by (method, path)."""
        sm = smoke()
        base = self.BASE

        class Response:
            def __init__(self, status, location="", text=""):
                self.status_code = status
                self.headers = {"Location": location} if location else {}
                self.text = text

        class Boom(Exception):
            pass

        class Session:
            def __init__(self):
                self.headers = {}

            def _answer(self, method, url):
                answer = answers[(method, url[len(base):])]
                return Response(*answer) if isinstance(answer, tuple) else answer

            def get(self, url, **kwargs):
                return self._answer("GET", url)

            def post(self, url, **kwargs):
                return self._answer("POST", url)

        monkeypatch.setattr(sm, "requests", SimpleNamespace(
            Session=Session, RequestException=Boom))

        res = sm.Result()
        sm.check_aliases(self.BASE, sm.Account("guru", "g@x", "pw"), res)
        return res

    FORM = 'name="csrf-token" content="tok"'

    def _happy(self, overrides=None):
        answers = {
            ("GET", "/auth/login"): (302, "/auth/sign-in?role=guru"),
            ("GET", "/auth/login-user"): (302, "/auth/sign-in"),
            ("GET", "/auth/sign-in"): (200, "", self.FORM),
            ("POST", "/auth/login-user"): (302, "/teacher/dashboard"),
        }
        answers.update(overrides or {})
        return answers

    def test_both_published_urls_forward_and_a_post_still_signs_in(self, monkeypatch):
        res = self._drive(monkeypatch, self._happy())

        assert not res.failures, res.failures
        assert res.checked >= 3, "the alias contract was not actually walked"

    def test_a_post_answered_with_the_form_is_a_failure(self, monkeypatch):
        """The reader's credentials were dropped on the way."""
        res = self._drive(monkeypatch, self._happy({
            ("POST", "/auth/login-user"): (200, "", self.FORM)}))

        assert any("POST" in msg for msg in res.failures), (
            "a POST to a published URL that re-rendered the form instead of signing "
            "the reader in was reported as a pass")

    def test_an_alias_that_stops_forwarding_is_a_failure(self, monkeypatch):
        res = self._drive(monkeypatch, self._happy({
            ("GET", "/auth/login"): (404, "", "not found")}))

        assert any("/auth/login" in msg for msg in res.failures), (
            "a published URL that no longer forwards was reported as a pass")

    def test_a_page_without_its_csrf_token_is_a_failure(self, monkeypatch):
        res = self._drive(monkeypatch, self._happy({
            ("GET", "/auth/sign-in"): (200, "", "<html></html>")}))

        assert any("csrf" in msg for msg in res.failures), (
            "the alias contract was checked without a token to post with")


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


#: The installer is what writes `/etc/scangrade-smoke.conf` on a real box.
INSTALLER = ROOT / "deploy" / "install-auto-deploy.sh"


class TestTheInstallerArmsEveryRole:
    """A gate that is never *configured* for a role cannot fail on that role.

    Measured on the live box, 2026-10-02: the release smoke test reported
    ``— 4 role(s)`` and passed, while ``/demo`` offered six. The cause was here,
    not in the gate: `scangrade-deploy.sh` already forwards all six and
    `smoke_test.py` already checks all six, but the installer's conf template
    named only four, so the two officials were never signed in against on any
    release — and a drifted principal or vice-principal credential could ship
    without a single red check. The box was repaired by hand; these guard the
    template so a fresh box cannot be born under-armed again.
    """

    def test_the_conf_template_names_all_six_roles(self):
        src = INSTALLER.read_text(encoding="utf-8")
        missing = [role for role in SIX if f"SMOKE_{role.upper()}=" not in src]
        assert not missing, (
            f"the installer never writes {missing} into the smoke conf, so those "
            "roles would never be signed in against on a release")

    def test_the_credential_check_passes_all_six_through(self):
        """The `case` arm decides which keys reach `--check-credentials`.

        A role that is written into the conf but filtered out here is *worse*
        than one that was never written: the file looks complete, and the arming
        step proves four logins while six are offered.
        """
        src = INSTALLER.read_text(encoding="utf-8")
        line = next((ln for ln in src.splitlines()
                     if ln.strip().startswith("case \"$key\" in")), None)
        assert line, "the credential-check filter moved; update this rule"
        missing = [role for role in SIX if f"SMOKE_{role.upper()}" not in line]
        assert not missing, (
            f"the arming check omits {missing}, so `SMOKE_ENFORCE=true` could be "
            "set without those logins ever being proven")
