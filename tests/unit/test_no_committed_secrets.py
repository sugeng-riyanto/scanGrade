"""A repository anyone can read must not carry a key that bypasses row-level security.

`deploy/bootstrap.sh` wrote the box's `.env` with a Supabase **service-role** JWT
spelled out in the script. That key is the one credential the whole design leans
on `RLS` to survive: it bypasses every policy, so its home is the box's `.env` and
nowhere else — not a shell script, not a doc, and certainly not the public repo.
The same block also hard-coded `FLASK_SECRET_KEY`, the value that signs session
cookies and CSRF tokens, so anyone reading the repo could forge a session.

The guard is deliberately about the *shape of a live key*, not one project's
value: it scans every tracked file for a JWT whose payload says `service_role`,
and separately refuses a hard-coded Flask secret. A fake fixture is not a finding —
`app/config.py` carries a `_TEST_JWT` whose role is `service_role` but whose
signature is two characters, and no real key has one — so the guard only counts a
signature long enough to be real. That keeps it honest in both directions: it
cannot pass because it looked at nothing, and it cannot fail on a placeholder that
grants nothing.
"""
from __future__ import annotations

import base64
import json
import pathlib
import re

from tests.unit.git_env import git

REPO = pathlib.Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO / "deploy" / "bootstrap.sh"
GATE = REPO / "deploy" / "theme_gate.sh"
PRE_COMMIT = REPO / "deploy" / "git-hooks" / "pre-commit"

#: A three-part JWT. The signature must be long enough to be a real one: a
#: two-character stand-in used as a test fixture grants nothing and is not a leak.
JWT = re.compile(
    r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{20,})\b")


def _role_of(token: str) -> str | None:
    """The `role` claim of a JWT payload, or None if it cannot be read."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get("role")
    except Exception:
        return None


def _tracked_text_files():
    listed = git("ls-files", cwd=str(REPO), check=True).stdout.splitlines()
    for rel in listed:
        path = REPO / rel.strip()
        try:
            if path.is_file():
                yield rel.strip(), path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


class TestNoLiveServiceKeyIsCommitted:
    def test_no_tracked_file_carries_a_service_role_jwt(self):
        offenders = []
        for rel, text in _tracked_text_files():
            for token in JWT.findall(text):
                if _role_of(token) == "service_role":
                    offenders.append(rel)
                    break
        assert not offenders, (
            "a service-role key bypasses every RLS policy — it belongs in the box's "
            f".env and nowhere else, and it is committed in: {sorted(set(offenders))}"
        )

    def test_the_scan_would_recognise_a_real_one(self):
        """A guard that cannot fail is not a guard: prove the detector fires."""
        # Same claims, but a signature long enough to be a real key.
        fake = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0." + "A" * 43
        assert _role_of(fake) == "service_role"
        assert JWT.findall(fake), "the detector must match a real-shaped key"

    def test_a_short_placeholder_is_not_treated_as_a_leak(self):
        assert not JWT.findall(
            "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.QA")


class TestBootstrapTakesItsSecretsFromTheEnvironment:
    def test_no_hard_coded_service_key(self):
        text = BOOTSTRAP.read_text(encoding="utf-8")
        assert not re.search(r"(?m)^SUPABASE_SERVICE_KEY=eyJ", text), (
            "the bootstrap must not spell out a service-role key")

    def test_no_hard_coded_flask_secret(self):
        text = BOOTSTRAP.read_text(encoding="utf-8")
        # The heredoc's own `FLASK_SECRET_KEY=$FLASK_SECRET_KEY` is an expansion,
        # not a value; only a literal (one that does not begin with `$`) is a leak.
        assert not re.search(r"(?m)^FLASK_SECRET_KEY=(?!\$)\S", text), (
            "a hard-coded Flask secret key lets anyone who reads the repo forge a "
            "session cookie or a CSRF token")

    def test_the_secrets_are_required_from_the_environment(self):
        text = BOOTSTRAP.read_text(encoding="utf-8")
        for name in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
                     "FLASK_SECRET_KEY"):
            assert f'${{{name}:?' in text, (
                f"{name} must be demanded from the environment with a message, so a "
                f"box cannot silently boot with a value from the repository")


class TestTheDeployGateRunsTheScan:
    """A scan nobody runs is not a gate.

    The service-role key is the one credential the whole design leans on RLS to
    survive, and the Flask secret signs every session and CSRF token, so the scan
    has to run on *every* release — not only when somebody remembers the unit
    suite. `deploy/theme_gate.sh` is what the pre-commit hook and the VPS
    auto-deploy both run before a release is allowed through, so the scan belongs
    in its list of checks.
    """

    def test_the_gate_lists_the_scan(self):
        src = GATE.read_text(encoding="utf-8")
        listed = [ln for ln in src.splitlines() if ln.startswith("TESTS=")]
        assert listed, "the gate no longer lists the checks it runs"
        assert "tests/unit/test_no_committed_secrets.py" in listed[0], (
            "the committed-secret scan is not in the gate's list, so a release "
            "carrying a service-role key or a literal Flask secret would ship "
            "unexamined")

    def test_the_scan_is_armed_against_deletion(self):
        """The gate's own arming list has to be built from that same line.

        Otherwise the line could lose the scan and the gate would still report
        green: it would be checking one fewer thing and saying nothing.
        """
        src = GATE.read_text(encoding="utf-8")
        assert 'ARMAMENT="$TESTS' in src, (
            "the gate no longer derives its arming list from the checks it runs, "
            "so removing this scan would drop the check instead of refusing the "
            "release")

    def test_the_gate_names_the_secret_it_refuses(self):
        src = GATE.read_text(encoding="utf-8")
        assert "service-role" in src or "service_role" in src, (
            "a reader told this release was refused has to see which secret the "
            "scan found: the gate's own text never names it")

    def test_the_pre_commit_hook_fires_when_a_script_changes(self):
        """The real leak lived in a shell script, which the hook's filter ignored.

        The hook only runs the gate when the staged paths match its filter, so a
        filter that skips `deploy/*.sh` lets the very commit that put a
        service-role key into `deploy/bootstrap.sh` through unchallenged.
        """
        src = PRE_COMMIT.read_text(encoding="utf-8")
        trigger = [ln for ln in src.splitlines() if "grep -qE" in ln and "CHANGED" in ln]
        assert trigger, "the hook no longer filters the staged files"
        match = re.search(r"grep -qE '([^']+)'", trigger[0])
        assert match, "the hook's filter is not a single-quoted pattern"
        pattern = match.group(1)
        assert re.search(pattern, "deploy/bootstrap.sh"), (
            "the hook would not run the gate for the commit that first leaked the "
            "service-role key; its filter is:\n    " + pattern)
