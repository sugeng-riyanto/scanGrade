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
