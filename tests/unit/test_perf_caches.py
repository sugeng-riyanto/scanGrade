"""Tests for the per-request caches that keep 330 concurrent users responsive.

Each Supabase round-trip costs ~100-165 ms from this deployment, so the goal of
these caches is purely to remove *repeated* round-trips from the hot path —
without letting a stale role, a revoked token, or an expired subscription slip
through.
"""
import base64
import json
import time

import pytest
from flask import Flask, g

from app.utils import kv_cache
from app.utils import auth as authmod
from app.decorators.security import require_school_access
from app.services import midtrans_service as mid


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch):
    """No Redis in tests, and no cached state leaking between tests."""
    monkeypatch.setattr("app.utils.rate_limiter._get_redis_conn", lambda: None)
    kv_cache._local.clear()
    yield
    kv_cache._local.clear()


def make_token(exp_offset=3600, sub="u1"):
    """A structurally valid JWT whose payload carries `sub` and `exp`."""
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': sub, 'exp': int(time.time()) + exp_offset})}.sig"


def session_payload(**over):
    data = {
        "user_id": "u1", "email": "a@b.c", "name": "A",
        "role": "guru", "school_id": "school-A", "status": "active",
    }
    data.update(over)
    return data


# ── Session cache ─────────────────────────────────────────────────

class TestSessionCache:
    def test_resolves_once_within_ttl(self, monkeypatch):
        """Five requests must cost one token+profile round-trip, not five."""
        calls = {"n": 0}

        def fake_fetch(token):
            calls["n"] += 1
            return session_payload()

        monkeypatch.setattr(authmod, "_fetch_session", fake_fetch)
        tok = make_token()
        for _ in range(5):
            data = authmod._session_for(tok)
        assert calls["n"] == 1
        assert data["role"] == "guru"
        assert data["school_id"] == "school-A"

    def test_different_tokens_are_cached_separately(self, monkeypatch):
        calls = {"n": 0}

        def fake_fetch(token):
            calls["n"] += 1
            return session_payload()

        monkeypatch.setattr(authmod, "_fetch_session", fake_fetch)
        authmod._session_for(make_token(sub="a"))
        authmod._session_for(make_token(sub="b"))
        assert calls["n"] == 2

    def test_invalidate_forces_a_refetch(self, monkeypatch):
        """Logout must not leave a working token in the cache."""
        calls = {"n": 0}

        def fake_fetch(token):
            calls["n"] += 1
            return session_payload()

        monkeypatch.setattr(authmod, "_fetch_session", fake_fetch)
        tok = make_token()
        authmod._session_for(tok)
        authmod._session_for(tok)
        assert calls["n"] == 1

        authmod.invalidate_session(tok)
        authmod._session_for(tok)
        assert calls["n"] == 2

    def test_ttl_zero_disables_the_cache(self, monkeypatch):
        calls = {"n": 0}

        def fake_fetch(token):
            calls["n"] += 1
            return session_payload()

        monkeypatch.setattr(authmod, "_fetch_session", fake_fetch)
        monkeypatch.setattr(authmod, "_session_ttl", lambda: 0)
        tok = make_token()
        for _ in range(3):
            authmod._session_for(tok)
        assert calls["n"] == 3

    def test_expired_token_is_rejected_even_when_cached(self, monkeypatch):
        """The `exp` check must run before the cache lookup, so an expired
        token can't ride a cache entry written while it was still valid."""
        monkeypatch.setattr(
            authmod, "_fetch_session",
            lambda token: pytest.fail("an expired token must not be fetched"))

        tok = make_token(exp_offset=-60)
        kv_cache.cache_set(authmod._session_key(tok), session_payload(), 300)

        with pytest.raises(Exception):
            authmod._session_for(tok)

    def test_cached_session_populates_flask_g(self, monkeypatch):
        monkeypatch.setattr(authmod, "_fetch_session", lambda token: session_payload(role="murid"))
        tok = make_token()
        app = Flask(__name__)
        with app.test_request_context("/"):
            authmod._apply_session(authmod._session_for(tok), tok)
            assert g.user_id == "u1"
            assert g.user_role == "murid"
            assert g.user_school_id == "school-A"
            assert g.user_token == tok


# ── Subscription gate cache ───────────────────────────────────────

class TestSchoolActiveCache:
    def test_cached_per_school(self, monkeypatch):
        calls = {"n": 0}

        def fake_sub(sid):
            calls["n"] += 1
            return {"status": "active"}

        monkeypatch.setattr(mid, "get_school_subscription", fake_sub)
        for _ in range(5):
            assert mid.is_school_active("school-A") is True
        assert calls["n"] == 1
        mid.is_school_active("school-B")
        assert calls["n"] == 2

    def test_false_result_is_also_cached(self, monkeypatch):
        """A cached 'false' must survive; using a truthiness check here would
        re-query on every write for exactly the schools that are blocked."""
        calls = {"n": 0}

        def fake_sub(sid):
            calls["n"] += 1
            return {"status": "expired"}

        monkeypatch.setattr(mid, "get_school_subscription", fake_sub)
        assert mid.is_school_active("school-X") is False
        assert mid.is_school_active("school-X") is False
        assert calls["n"] == 1

    def test_invalidate_refetches(self, monkeypatch):
        calls = {"n": 0}

        def fake_sub(sid):
            calls["n"] += 1
            return {"status": "active"}

        monkeypatch.setattr(mid, "get_school_subscription", fake_sub)
        mid.is_school_active("school-Y")
        mid.invalidate_school_active("school-Y")
        mid.is_school_active("school-Y")
        assert calls["n"] == 2

    def test_no_school_is_inactive(self):
        assert mid.is_school_active(None) is False


# ── RBAC school-access check ──────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table_name):
        self.db = db
        self.table_name = table_name
        self.cols = None

    def select(self, cols):
        self.cols = cols
        self.db.selects.append((self.table_name, cols))
        return self

    def eq(self, *a, **k):
        return self

    def single(self):
        return self

    def execute(self):
        if (self.table_name, self.cols) in self.db.raise_for:
            raise RuntimeError("relationship not embeddable")
        return _Resp(self.db.rows.get((self.table_name, self.cols)))


class _DB:
    def __init__(self, rows, raise_for=()):
        self.rows = rows
        self.raise_for = set(raise_for)
        self.selects = []

    def table(self, name):
        return _Query(self, name)


def _rbac_app():
    app = Flask(__name__)

    @app.get("/sub/<submission_id>")
    @require_school_access("submissions", "submission_id", ("exam_id", "exams"))
    def view(submission_id):
        return "ok"

    return app


class TestRbacSchoolAccess:
    def test_embedded_single_round_trip(self, monkeypatch):
        db = _DB({("submissions", "exam_id, exams(school_id)"):
                  {"exam_id": "e1", "exams": {"school_id": "school-A"}}})
        monkeypatch.setattr(authmod, "get_supabase", lambda: db)

        with _rbac_app().test_request_context("/sub/s1"):
            g.user_school_id = "school-A"
            assert _rbac_view() == "ok"

        assert len(db.selects) == 1, "expected one round-trip, not two"

    def test_falls_back_when_embed_unavailable(self, monkeypatch):
        """Correctness must not depend on the optimisation working."""
        db = _DB(
            {("submissions", "exam_id"): {"exam_id": "e1"},
             ("exams", "school_id"): {"school_id": "school-A"}},
            raise_for={("submissions", "exam_id, exams(school_id)")},
        )
        monkeypatch.setattr(authmod, "get_supabase", lambda: db)

        with _rbac_app().test_request_context("/sub/s1"):
            g.user_school_id = "school-A"
            assert _rbac_view() == "ok"

        assert db.selects[-2:] == [("submissions", "exam_id"), ("exams", "school_id")]

    def test_other_school_is_denied(self, monkeypatch):
        db = _DB({("submissions", "exam_id, exams(school_id)"):
                  {"exam_id": "e1", "exams": {"school_id": "school-B"}}})
        monkeypatch.setattr(authmod, "get_supabase", lambda: db)

        with _rbac_app().test_request_context("/sub/s1"):
            g.user_school_id = "school-A"
            with pytest.raises(Exception) as exc:
                _rbac_view()
            assert getattr(exc.value, "code", None) == 403


def _rbac_view():
    """Call the decorated view the way Flask does (by keyword)."""
    from flask import current_app
    return current_app.view_functions["view"](submission_id="s1")
