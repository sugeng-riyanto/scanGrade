"""``/super-admin/demo-settings`` is a page, not an endpoint — and a save must fail loudly.

The route had a single unconditional ``return jsonify({"success": True})`` for both
verbs, with the ``render_template`` call sitting *after* it as dead code. Two
consequences, both invisible to a smoke test that only checks the status code:

1. Opening the page in a browser returned raw ``{"success": true}`` instead of the
   settings form, so the whole demo surface was unreachable.
2. A failed write was still reported as success, so the toggles appeared saved and
   silently reverted on the next load.

The GET path also read its row with ``.single()``, which raises instead of returning
empty when no row exists — an exception per visit on a page that is otherwise fine.
"""
from types import SimpleNamespace

import pytest

from app.routes import super_admin as supermod


# ── fakes ────────────────────────────────────────────────────────

class FakeSchoolSettings:
    """Chainable stand-in for the ``school_settings`` table with one row (id=1)."""

    def __init__(self, blob=None, fail_write=False):
        self.blob = blob
        self.fail_write = fail_write
        self.writes = []
        self._mode = "select"
        self._single = False

    def select(self, *a, **k):
        self._mode, self._single = "select", False
        return self

    def update(self, data):
        self._mode, self._payload = "update", data
        return self

    def insert(self, data):
        self._mode, self._payload = "insert", data
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def execute(self):
        if self._mode in ("update", "insert"):
            if self.fail_write:
                raise RuntimeError("relation does not exist")
            self.writes.append(self._payload)
            if "demo_settings" in self._payload:
                self.blob = self._payload["demo_settings"]
            return SimpleNamespace(data=[{"id": 1}])
        if self._single:
            # postgrest returns None — not a response carrying data=None — on no match
            return SimpleNamespace(data={"demo_settings": self.blob} if self.blob else None)
        return SimpleNamespace(data=[{"id": 1}] if self.blob else [])


class FakeSupabase:
    """Only ``school_settings`` is interesting; audit writes are accepted and dropped."""

    def __init__(self, settings):
        self.settings = settings

    def table(self, name):
        if name == "school_settings":
            return self.settings
        return FakeSchoolSettings()


# ── harness ──────────────────────────────────────────────────────

@pytest.fixture
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")


def _call(app, monkeypatch, settings, method="GET", data=None, path="/super-admin/demo-settings"):
    from flask import g

    captured = {}

    def _capture(name, **kw):
        captured["template"] = name
        captured["ctx"] = kw
        return "<rendered demo settings page>"

    monkeypatch.setattr(supermod, "render_template", _capture)
    app.extensions["supabase"] = FakeSupabase(settings)

    view = supermod.demo_settings.__wrapped__ if path.endswith("demo-settings") \
        else supermod.demo_settings_data.__wrapped__

    with app.test_request_context(path, method=method, data=data):
        g.user_id, g.user_role = "sa-1", "super_admin"
        captured["response"] = view()

    # A view may return ``(response, status)``; normalise before asserting.
    raw = captured["response"]
    captured["response"], captured["status"] = raw if isinstance(raw, tuple) else (raw, 200)
    return captured


# ── GET must render the page ─────────────────────────────────────

def test_get_renders_the_settings_page(app, monkeypatch):
    out = _call(app, monkeypatch, FakeSchoolSettings({"demo_enabled": True}))

    assert out.get("template") == "super_admin/demo_settings.html", \
        "GET returned JSON instead of the page — dead code after a return"
    assert out["ctx"]["settings"]["demo_enabled"] is True


def test_get_works_when_no_row_exists_yet(app, monkeypatch):
    """A fresh install has no ``school_settings`` row: ``.single()`` raised there."""
    out = _call(app, monkeypatch, FakeSchoolSettings(None))

    assert out["template"] == "super_admin/demo_settings.html"
    assert out["ctx"]["settings"] == {}


def test_data_endpoint_returns_the_blob(app, monkeypatch):
    out = _call(app, monkeypatch, FakeSchoolSettings({"demo_guru": True}),
                path="/super-admin/demo-settings/data")

    assert out["response"].get_json() == {"demo_guru": True}


def test_data_endpoint_returns_empty_object_without_row(app, monkeypatch):
    out = _call(app, monkeypatch, FakeSchoolSettings(None),
                path="/super-admin/demo-settings/data")

    assert out["response"].get_json() == {}


# ── POST must save, and must report failure ──────────────────────

def test_post_persists_and_echoes_settings(app, monkeypatch):
    db = FakeSchoolSettings({})
    out = _call(app, monkeypatch, db, method="POST",
                data={"demo_enabled": "true", "demo_guru": "true", "demo_murid": "false"})

    body = out["response"].get_json()
    assert body["success"] is True
    assert body["settings"]["demo_enabled"] is True
    assert body["settings"]["demo_murid"] is False
    assert db.writes[-1]["demo_settings"] == body["settings"]


def test_post_reports_a_failed_write(app, monkeypatch):
    """A swallowed write error made the toggles look saved and silently revert."""
    out = _call(app, monkeypatch, FakeSchoolSettings({}, fail_write=True),
                method="POST", data={"demo_enabled": "true"})

    assert out["status"] == 500
    assert out["response"].get_json()["success"] is False
