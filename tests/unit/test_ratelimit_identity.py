"""The rate-limit hook must bucket per USER, not per IP.

Regression test for a production-scale bug. `get_rate_limiter` runs in
``before_request``, which happens *before* the view's ``login_required`` applies
the session to ``g``. So the hook's ``identity = user_id or ip`` always saw
``user_id is None`` and fell through to the IP — for authenticated requests too.

A school reaches the internet through one NAT'd address, so every student shared
a single 120 req/min bucket. Measured against production with 44 distinct
accounts from one IP: 61 of 221 requests were 429'd, including 27 of 27 settings
writes. Locally, without nginx at all, the same pattern reproduced — proving the
app layer, not the proxy, was the ceiling.

The fix resolves the caller from the session cache (cache-only: no Supabase call,
and a forged token can't provoke one) and keeps a separate, loose per-IP flood
bucket for everything that cannot be identified.
"""
import base64
import json
import time

import pytest
from flask import Flask

from app.utils import auth as authmod
from app.utils import kv_cache
from app.utils import rate_limiter as rlmod


def make_token(sub="u1", exp_offset=3600):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': sub, 'exp': int(time.time()) + exp_offset})}.sig"


def session_payload(user_id):
    return {"user_id": user_id, "email": f"{user_id}@b.c", "name": user_id,
            "role": "murid", "school_id": "school-A", "status": "active"}


@pytest.fixture(autouse=True)
def _no_redis_and_clean_state(monkeypatch):
    monkeypatch.setattr(rlmod, "_get_redis_conn", lambda: None)
    monkeypatch.setattr("app.utils.kv_cache._redis", lambda: None)
    kv_cache._local.clear()
    rlmod._limits.clear()
    yield
    kv_cache._local.clear()
    rlmod._limits.clear()


def warm_session(token, user_id):
    """Put a resolved session in the cache, as a real login would."""
    kv_cache.cache_set(authmod._session_key(token), session_payload(user_id), 300)


def _probe_app():
    """Minimal app carrying the real rate-limit hook."""
    app = Flask(__name__)
    app.secret_key = "test"

    @app.route("/_rl_probe")
    def probe():
        return "ok"

    app.config["REDIS_URL"] = ""
    rlmod.get_rate_limiter(app)
    return app


# ── peek_identity ────────────────────────────────────────────────

class TestPeekIdentity:
    def test_no_token_means_no_identity(self):
        with _probe_app().test_request_context("/_rl_probe"):
            assert authmod.peek_identity() is None

    def test_uses_the_cached_session(self):
        tok = make_token()
        warm_session(tok, "user-7")
        with _probe_app().test_request_context("/_rl_probe", headers={"Cookie": f"access_token={tok}"}):
            assert authmod.peek_identity() == "user-7"

    def test_never_hits_supabase(self, monkeypatch):
        """A forged token must not trigger lookups before any limit applies."""
        monkeypatch.setattr(
            authmod, "_fetch_session",
            lambda token: pytest.fail("peek_identity must be cache-only"))

        with _probe_app().test_request_context(
            "/_rl_probe", headers={"Cookie": "access_token=forged.token.sig"}
        ):
            assert authmod.peek_identity() is None

    def test_prefers_an_already_applied_session(self):
        with _probe_app().test_request_context("/_rl_probe"):
            from flask import g
            g.user_id = "from-g"
            assert authmod.peek_identity() == "from-g"


# ── the hook's bucketing ─────────────────────────────────────────

def _client_with_token(app, token):
    """A client that carries ``token`` as its access_token cookie.

    Werkzeug 3's test client ignores a hand-written ``Cookie`` header, so the
    cookie has to be set through its own API (one client per identity).
    """
    client = app.test_client()
    client.set_cookie("access_token", token)
    return client


class TestHookBucketing:
    def test_many_users_behind_one_ip_do_not_share_a_budget(self):
        """The bug: 3 users from one IP must get 3 budgets, not one."""
        app = _probe_app()

        clients = {}
        for i in range(3):
            tok = make_token(sub=f"user-{i}")
            warm_session(tok, f"user-{i}")
            clients[f"user-{i}"] = _client_with_token(app, tok)

        # Every client reports the same source address (127.0.0.1).
        allowed = {u: 0 for u in clients}
        blocked = {u: 0 for u in clients}
        for _ in range(130):
            for user, client in clients.items():
                r = client.get("/_rl_probe")
                if r.status_code == 429:
                    blocked[user] += 1
                else:
                    allowed[user] += 1

        limit = rlmod.DEFAULT_LIMITS["default"][0]
        for user in clients:
            assert allowed[user] == limit, (
                f"{user} got {allowed[user]}/{limit} — users are sharing a bucket, "
                f"so a NAT'd school throttles itself")
            assert blocked[user] == 130 - limit

        # The old behaviour capped the IP as a whole at `limit`.
        assert sum(allowed.values()) == limit * 3

    def test_anonymous_requests_get_the_loose_flood_bucket(self):
        """Anonymous traffic is still IP-keyed, but must not be capped at the
        per-user rate — the login page of a whole school comes from one IP."""
        client = _probe_app().test_client()
        results = [client.get("/_rl_probe").status_code for _ in range(200)]
        assert set(results) == {200}, f"anonymous flood bucket too tight: {set(results)}"

        assert rlmod.IP_FLOOD_LIMITS["default"][0] > rlmod.DEFAULT_LIMITS["default"][0]

    def test_ip_buckets_are_separate_per_address(self):
        """The fallback must key on the address, so two networks don't collapse
        into one shared bucket."""
        client = _probe_app().test_client()
        for ip in ("10.0.0.1", "10.0.0.2"):
            client.get("/_rl_probe", environ_overrides={"REMOTE_ADDR": ip})

        keys = " ".join(rlmod._limits.keys())
        assert "ip:10.0.0.1" in keys
        assert "ip:10.0.0.2" in keys

    def test_identity_and_flood_buckets_do_not_collide(self):
        """A user's budget and the IP's flood budget must be distinct keys."""
        app = _probe_app()
        tok = make_token()
        warm_session(tok, "user-9")

        _client_with_token(app, tok).get("/_rl_probe")
        app.test_client().get("/_rl_probe")

        keys = " ".join(rlmod._limits.keys())
        assert "u:user-9" in keys
        assert "ip:127.0.0.1" in keys
