"""Login must not report a Supabase rate limit as a wrong password.

Measured on the production VPS: 15/15 sequential `sign_in_with_password` calls
succeed, but a simultaneous burst of 15 drops to 5/15 with
``AuthApiError: Request rate limit reached``. Supabase rate-limits sign-in per
IP, and every app login is made from the server, so all schools share one
bucket.

Both handlers caught that in a bare ``except`` and rendered "Email atau password
salah". Consequences: a student with a correct password is told it is wrong, so
they retry and deepen the limit; and the same block then charged the failure to
the school's login budget, banning everyone for 15 minutes. The real cause was
invisible in production.

These tests pin the two behaviours that fix that:
  1. only genuine credential failures consume the account/IP throttle;
  2. rate limits are retried with backoff instead of failing the user.

Since the auth door became bilingual the classified message is an ``(id, en)``
pair — the page renders both halves and lets Alpine choose, because the language
lives in localStorage where the server cannot see it. Each assertion below
therefore reads *both* halves: a message that is honest in one language and wrong
in the other is the same defect this file exists for, just harder to spot.
"""
import pytest
from unittest.mock import MagicMock

from app.routes import auth as authmod


def _pair(message):
    """The two halves of a classified message, or a loud failure.

    A bare string here means a route went back to a literal — which renders
    Indonesian in both modes and raises nothing, so it has to be an error in the
    test rather than something the next assertion happens to miss.
    """
    assert isinstance(message, (tuple, list)) and len(message) == 2, (
        f"a classified login message must be an (id, en) pair, got {message!r}")
    return message


class AuthError(Exception):
    """Stand-in for supabase's AuthApiError (carries status and code)."""

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


RATE_LIMITED = lambda: AuthError("Request rate limit reached", status=429)  # noqa: E731
BAD_CREDS = lambda: AuthError("Invalid login credentials", 400, "invalid_credentials")  # noqa: E731


# ── classification ───────────────────────────────────────────────

class TestClassifyLoginError:
    def test_wrong_password_is_reported_as_such(self):
        wrong, msg = authmod._classify_login_error(BAD_CREDS())
        assert wrong is True
        assert _pair(msg) == ("Email atau password salah", "Wrong email or password")

    def test_rate_limit_is_not_a_wrong_password(self):
        wrong, msg = authmod._classify_login_error(RATE_LIMITED())
        assert wrong is False, "must not charge this to the account's login budget"
        id_text, en_text = _pair(msg)
        assert "salah" not in id_text.lower()
        assert "wrong password" not in en_text.lower()
        assert "sibuk" in id_text.lower()
        assert "busy" in en_text.lower(), \
            "the English half must say the same thing, not the other diagnosis"

    def test_rate_limit_is_detected_without_a_status_code(self):
        """GoTrue's message alone must be enough — the status isn't always set."""
        wrong, _ = authmod._classify_login_error(AuthError("Request rate limit reached"))
        assert wrong is False

    def test_status_429_alone_is_enough(self):
        wrong, _ = authmod._classify_login_error(AuthError("Too many requests", status=429))
        assert wrong is False

    @pytest.mark.parametrize("exc", [
        AuthError("Internal server error", status=500),
        AuthError("Service unavailable", status=503),
        AuthError("Connection reset by peer"),
    ])
    def test_server_and_network_faults_are_transient(self, exc):
        wrong, msg = authmod._classify_login_error(exc)
        assert wrong is False
        id_text, en_text = _pair(msg)
        assert "sementara" in id_text.lower()
        assert "temporary" in en_text.lower()


# ── retry ────────────────────────────────────────────────────────

class _FakeAuth:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def sign_in_with_password(self, payload):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Client:
    def __init__(self, outcomes):
        self.auth = _FakeAuth(outcomes)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Keep pacing and backoff from slowing the suite down."""
    monkeypatch.setattr(authmod, "_LOGIN_RETRY_BASE", 0)
    monkeypatch.setattr(authmod, "_LOGIN_SIGNIN_RATE", 0)  # no pacing delay
    # Small on purpose: the route tests exercise a *persistent* rate limit, and
    # at the real 60s budget each of them would sit here waiting a full minute.
    # The patience itself is asserted explicitly in TestSigninPacing.
    monkeypatch.setattr(authmod, "_LOGIN_WAIT_BUDGET", 0.2)
    monkeypatch.setattr(authmod.time, "sleep", lambda *_: None)
    # Force the LOCAL pacing path: the Redis slot queue needs a live server, and
    # a test must not depend on one (nor silently change rate when one is up).
    monkeypatch.setattr(authmod, "_pace_signin_redis", lambda interval: False)
    authmod._next_slot[0] = 0.0


class TestSignInWithRetry:
    def test_returns_immediately_on_success(self):
        client = _Client(["ok"])
        assert authmod._sign_in_with_retry(client, "a@b.c", "pw") == "ok"
        assert client.auth.calls == 1

    def test_retries_a_rate_limit_then_succeeds(self):
        client = _Client([RATE_LIMITED(), RATE_LIMITED(), "ok"])
        assert authmod._sign_in_with_retry(client, "a@b.c", "pw") == "ok"
        assert client.auth.calls == 3

    def test_does_not_retry_a_wrong_password(self):
        """Retrying bad credentials would multiply the load for no benefit."""
        client = _Client([BAD_CREDS(), "ok"])
        with pytest.raises(AuthError):
            authmod._sign_in_with_retry(client, "a@b.c", "pw")
        assert client.auth.calls == 1

    def test_gives_up_when_the_wait_budget_is_exhausted(self, monkeypatch):
        """A queue that never clears must eventually surface, not hang forever."""
        monkeypatch.setattr(authmod, "_LOGIN_WAIT_BUDGET", 0)
        client = _Client([RATE_LIMITED(), "ok"])
        with pytest.raises(AuthError):
            authmod._sign_in_with_retry(client, "a@b.c", "pw")
        assert client.auth.calls == 1


# ── pacing: stay under Supabase's refill rate instead of stampeding it ──

class TestSigninPacing:
    def test_consecutive_slots_are_spaced(self, monkeypatch):
        """Two immediate logins must not both hit Supabase at once.

        Supabase refills the token bucket at a fixed rate and rejects anything
        above it, so un-paced attempts are wasted work that ends in failures.
        """
        delays = []
        monkeypatch.setattr(authmod, "_LOGIN_SIGNIN_RATE", 2.0)  # one slot per 0.5s
        monkeypatch.setattr(authmod.time, "sleep", lambda d: delays.append(d))
        authmod._next_slot[0] = 0.0

        authmod._pace_signin()  # first slot: goes now
        authmod._pace_signin()  # second slot: must wait for the next slot

        assert delays, "the second login should have waited"
        assert delays[-1] > 0.4

    def test_pacing_can_be_disabled(self, monkeypatch):
        delays = []
        monkeypatch.setattr(authmod, "_LOGIN_SIGNIN_RATE", 0)
        monkeypatch.setattr(authmod.time, "sleep", lambda d: delays.append(d))
        authmod._pace_signin()
        authmod._pace_signin()
        assert delays == []

    def test_pacing_rate_is_configurable(self):
        """The ceiling is a moving target — it must be tunable without a code change."""
        assert authmod._LOGIN_SIGNIN_RATE >= 0
        assert authmod._LOGIN_WAIT_BUDGET > 0, \
            "a queued login needs patience, or raising the Supabase limit buys nothing"

    def test_redis_slot_is_preferred_so_the_rate_is_aggregate(self, monkeypatch):
        """The slot queue lives in Redis so workers share ONE rate.

        Pacing in-process only would multiply the rate by the worker count
        (3 workers x 8/s = 24/s) and recreate the stampede it exists to stop —
        so when Redis answers, the local queue must not also be consumed.
        """
        monkeypatch.setattr(authmod, "_LOGIN_SIGNIN_RATE", 8.0)
        seen = []
        monkeypatch.setattr(authmod, "_pace_signin_redis",
                            lambda interval: seen.append(interval) or True)
        monkeypatch.setattr(authmod.time, "sleep",
                            lambda d: pytest.fail("Redis reserved the slot; no local wait"))
        authmod._next_slot[0] = 0.0

        authmod._pace_signin()

        assert seen == [pytest.approx(0.125)], "interval must be 1/rate of the AGGREGATE limit"
        assert authmod._next_slot[0] == 0.0


# ── the route must not charge a rate limit to the login budget ───

@pytest.fixture
def route_app():
    from app import create_app
    return create_app("app.config.TestingConfig")


@pytest.fixture
def client(route_app):
    return route_app.test_client()


@pytest.fixture
def csrf(client):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-csrf-token"
    return {"X-CSRF-Token": "test-csrf-token"}


def _patch_auth(monkeypatch, error):
    client = MagicMock()
    client.auth.sign_in_with_password.side_effect = error
    monkeypatch.setattr(authmod, "get_auth_client", lambda: client)
    monkeypatch.setattr(authmod, "get_supabase", lambda: MagicMock())


def _spy_throttle(monkeypatch):
    seen = []

    def fake(scope, account, ip=None):
        seen.append((scope, account))
        return True, 0

    monkeypatch.setattr(authmod, "check_account_limit", fake)
    return seen


class TestLoginRouteFailureHandling:
    def test_rate_limit_shows_an_honest_message_and_is_not_counted(
        self, client, csrf, monkeypatch
    ):
        _patch_auth(monkeypatch, RATE_LIMITED())
        seen = _spy_throttle(monkeypatch)

        r = client.post("/auth/login-user",
                        data={"email": "siswa@sekolah.id", "password": "benar"},
                        headers=csrf)

        assert r.status_code == 200
        assert b"Email atau password salah" not in r.data, \
            "a rate limit must never be reported as a wrong password"
        assert b"sibuk" in r.data
        assert seen == [], \
            "a transient failure must not consume the account's login budget"

    def test_wrong_password_is_still_counted_against_the_account(
        self, client, csrf, monkeypatch
    ):
        _patch_auth(monkeypatch, BAD_CREDS())
        seen = _spy_throttle(monkeypatch)

        r = client.post("/auth/login-user",
                        data={"email": "Siswa@Sekolah.id", "password": "salah"},
                        headers=csrf)

        assert r.status_code == 200
        assert b"Email atau password salah" in r.data
        assert seen == [("login_failed", "siswa@sekolah.id")]

    def test_admin_login_behaves_the_same_way(self, client, csrf, monkeypatch):
        _patch_auth(monkeypatch, RATE_LIMITED())
        seen = _spy_throttle(monkeypatch)

        r = client.post("/auth/login",
                        data={"email": "admin@sekolah.id", "password": "benar"},
                        headers=csrf)

        assert r.status_code == 200
        assert b"Email atau password salah" not in r.data
        assert seen == []
