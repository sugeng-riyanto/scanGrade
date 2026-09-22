"""Per-account rate limiting tests.

A school usually reaches the internet through a single NAT'd public IP. Keying
these throttles on the IP meant one busy user (or a whole class retrying) could
lock out everyone else behind the same address. They must be keyed on the
account the caller names, with only a loose per-IP backstop for abuse.
"""
import pytest
from flask import Flask

from app.utils import rate_limiter as rl


@pytest.fixture(autouse=True)
def _offline_and_clean(monkeypatch):
    """Force the in-memory backend and start each test from a clean slate."""
    monkeypatch.setattr(rl, "_get_redis_conn", lambda: None)
    monkeypatch.delenv("LOAD_TEST", raising=False)
    rl._limits.clear()
    yield
    rl._limits.clear()


# ── Core semantics ────────────────────────────────────────────────

class TestPerAccountLimits:
    def test_accounts_sharing_an_ip_do_not_block_each_other(self):
        """The whole point: one NAT'd IP must not be one shared bucket."""
        ip = "203.0.113.7"
        for i in range(3):            # three different accounts
            for _ in range(3):        # each consuming its full own quota
                allowed, _ = rl.check_account_limit(
                    "forgot_password", f"u{i}@school.id", ip=ip)
                assert allowed, f"account u{i} was blocked by another account's usage"

    def test_same_account_is_throttled_after_its_quota(self):
        ip = "203.0.113.7"
        for _ in range(3):
            assert rl.check_account_limit("forgot_password", "a@b.id", ip=ip)[0]
        allowed, retry = rl.check_account_limit("forgot_password", "a@b.id", ip=ip)
        assert not allowed
        assert retry > 0

    def test_account_key_is_case_insensitive(self):
        ip = "203.0.113.7"
        for _ in range(3):
            rl.check_account_limit("forgot_password", "A@B.id", ip=ip)
        allowed, _ = rl.check_account_limit("forgot_password", "a@b.ID", ip=ip)
        assert not allowed

    def test_ip_backstop_stops_one_host_cycling_accounts(self):
        """Rotating the account name must not buy unlimited attempts."""
        ip = "198.51.100.5"
        denied_at = None
        for i in range(80):
            allowed, _ = rl.check_account_limit("forgot_password", f"acct{i}@s.id", ip=ip)
            if not allowed:
                denied_at = i + 1
                break
        assert denied_at == 61, f"IP backstop fired at {denied_at}, expected 61"

    def test_backstop_is_wide_enough_for_a_class(self):
        """30 students on one IP, each verifying a couple of times."""
        ip = "203.0.113.9"
        allowed_count = 0
        for student in range(30):
            for _ in range(2):
                if rl.check_account_limit("verify_code", f"siswa{student}@s.id", ip=ip)[0]:
                    allowed_count += 1
        assert allowed_count == 60

    def test_empty_account_is_allowed_through(self):
        """The handler validates the input and reports its own error."""
        assert rl.check_account_limit("forgot_password", "", ip="1.2.3.4")[0]
        assert rl.check_account_limit("forgot_password", None, ip="1.2.3.4")[0]

    def test_load_test_mode_bypasses_limits(self, monkeypatch):
        monkeypatch.setenv("LOAD_TEST", "true")
        for _ in range(50):
            assert rl.check_account_limit("forgot_password", "a@b.id", ip="1.2.3.4")[0]


# ── Wiring ────────────────────────────────────────────────────────

class TestHookWiring:
    def test_account_limited_paths_are_skipped_by_the_ip_hook(self):
        for path in ("/auth/register", "/auth/forgot-password", "/auth/verify-reset-code"):
            assert path in rl._endpoint_self_limited
        assert "/auth/register" not in rl._exact_exempt

    def test_nat_hostile_groups_are_gone(self):
        """These per-IP groups were the bug; they must not creep back."""
        assert "register" not in rl.DEFAULT_LIMITS
        assert "reset_password" not in rl.DEFAULT_LIMITS


def _app_with_hook():
    app = Flask(__name__)

    @app.get("/auth/forgot-password")
    def forgot():  # pragma: no cover - stub
        return "ok"

    @app.get("/auth/register")
    def register():  # pragma: no cover - stub
        return "ok"

    @app.get("/auth/reset-password")
    def reset():  # pragma: no cover - stub
        return "ok"

    rl.get_rate_limiter(app)
    return app


class TestHookBehaviour:
    def test_forgot_password_is_not_ip_limited_by_the_hook(self):
        """50 rapid hits from one IP must all pass — the route throttles the
        account itself instead."""
        client = _app_with_hook().test_client()
        codes = {client.get("/auth/forgot-password").status_code for _ in range(50)}
        assert codes == {200}

    def test_register_is_not_ip_limited_by_the_hook(self):
        client = _app_with_hook().test_client()
        codes = {client.get("/auth/register").status_code for _ in range(50)}
        assert codes == {200}

    def test_other_auth_paths_still_get_the_ip_guard(self):
        """The hook must remain a backstop everywhere else.

        Note the ceiling is now the per-IP FLOOD bucket, not the per-user one.
        Anonymous traffic from a school all arrives on one NAT'd address, so a
        per-user-sized cap here throttled a whole class (measured: 61 of 221
        requests from one IP with 44 users). It still has to stop a flood.
        """
        client = _app_with_hook().test_client()
        flood = rl.IP_FLOOD_LIMITS["auth"][0]

        under = [client.get("/auth/reset-password").status_code for _ in range(40)]
        assert set(under) == {200}, "a classroom's traffic must not be throttled"

        # Push past the flood ceiling so the backstop is proven, not assumed.
        codes = [client.get("/auth/reset-password").status_code for _ in range(flood + 5)]
        assert 429 in codes, "the hook must still stop a flood from one IP"
        assert rl.IP_FLOOD_LIMITS["auth"][0] > rl.DEFAULT_LIMITS["auth"][0]


# ── Routes actually use it ────────────────────────────────────────

class TestRoutesUsePerAccountLimits:
    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def csrf(self, client):
        """The app enforces CSRF on every POST — supply a valid session token."""
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-csrf-token"
        return {"X-CSRF-Token": "test-csrf-token"}

    @staticmethod
    def _deny(monkeypatch, captured, retry=600):
        def fake(scope, account, ip=None):
            captured["scope"] = scope
            captured["account"] = account
            return False, retry
        monkeypatch.setattr("app.routes.auth.check_account_limit", fake)

    def test_forgot_password_throttles_by_account(self, client, csrf, monkeypatch):
        captured = {}
        self._deny(monkeypatch, captured)
        r = client.post("/auth/forgot-password",
                        data={"email": "Siswa@School.id"}, headers=csrf)
        assert r.status_code == 200
        # Lower-cased, and the account — not the IP — is the key.
        assert captured == {"scope": "forgot_password", "account": "siswa@school.id"}
        assert b"Terlalu banyak" in r.data

    def test_register_throttles_by_school_npsn(self, client, csrf, monkeypatch):
        captured = {}
        self._deny(monkeypatch, captured, retry=3600)
        r = client.post("/auth/register", data={
            "npsn": "12345678",
            "school_name": "SMP Contoh",
            "wa": "08123456789",
            "email": "admin@school.id",
            "password": "rahasia1",
            "position": "Kepala Sekolah",
            "consent": "on",
        }, headers=csrf)
        assert r.status_code == 200
        assert captured == {"scope": "register", "account": "12345678"}
        assert b"Terlalu banyak" in r.data

    def test_verify_reset_code_throttles_by_account(self, client, csrf, monkeypatch):
        captured = {}
        self._deny(monkeypatch, captured)
        r = client.post("/auth/verify-reset-code",
                        data={"email": "Siswa@School.id", "code": "ABC123"},
                        headers=csrf)
        assert r.status_code == 200
        assert captured == {"scope": "verify_code", "account": "siswa@school.id"}
        assert b"Terlalu banyak" in r.data
