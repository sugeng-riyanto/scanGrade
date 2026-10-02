"""The name on the dashboard must be the profile's, not a stale Auth copy.

Reported: a vice principal who also teaches Chemistry showed a *glitched* name on
`/teacher/dashboard` — the greeting printed

    Akun berhasil dibuat. Email: ..., Password: 5Ahj@tMlQc7z

The stored identity was not corrupt in `profiles` (that row read
"Aji Wahyu Budiyanto"). It was `auth.users.user_metadata.full_name` that held the
message, and `_fetch_session` trusted *that* alone:

    "name": meta.get("full_name", "")

while the profile row it had just read — the one every rename writes — was ignored.
So any account ever renamed (or created from a pasted message) wears the old Auth
value on every page, forever. These guards pin the profile as the source of truth,
with Auth only as a fallback for a row that genuinely has no name.
"""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from app.utils import auth as authmod

ROOT = pathlib.Path(__file__).resolve().parents[2]
AUTH_PY = (ROOT / "app" / "utils" / "auth.py").read_text(encoding="utf-8")

GLITCH = "Akun berhasil dibuat. Email: viceprincipalshb@shb.sch.id, Password: 5Ahj@tMlQc7z"
REAL = "Aji Wahyu Budiyanto"


def _auth_user(meta_name):
    return SimpleNamespace(
        user=SimpleNamespace(
            id="u-1", email="aji.budiyanto@shb.sch.id",
            user_metadata={"full_name": meta_name, "role": "guru"},
        )
    )


def _fake_auth_client(meta_name):
    return SimpleNamespace(auth=SimpleNamespace(get_user=lambda token: _auth_user(meta_name)))


class _Q:
    def __init__(self, row):
        self._row = row

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self._row)


def _fake_supabase(profile):
    return SimpleNamespace(table=lambda name: _Q(profile))


def _patch(monkeypatch, meta_name, profile):
    monkeypatch.setattr(authmod, "get_auth_client",
                        lambda: _fake_auth_client(meta_name))
    monkeypatch.setattr(authmod, "get_supabase", lambda: _fake_supabase(profile))


def test_the_name_comes_from_the_profile_even_when_auth_metadata_is_garbled(monkeypatch):
    _patch(monkeypatch, GLITCH, {"full_name": REAL, "role": "guru",
                                 "school_id": "s-1", "status": "active"})
    data = authmod._fetch_session("token")
    assert data["name"] == REAL, (
        "the sessions still takes its name from Auth metadata, so an account that "
        "was renamed — or created from a pasted message — keeps showing the glitch")


def test_auth_metadata_is_still_the_fallback_when_the_profile_has_no_name(monkeypatch):
    _patch(monkeypatch, REAL, {"full_name": "", "role": "guru",
                               "school_id": "s-1", "status": "active"})
    data = authmod._fetch_session("token")
    assert data["name"] == REAL


def test_a_profile_without_a_full_name_column_still_yields_a_name(monkeypatch):
    """A row that never carried the column must fall back, not blank the greeting."""
    _patch(monkeypatch, REAL, {"role": "guru", "school_id": "s-1", "status": "active"})
    data = authmod._fetch_session("token")
    assert data["name"] == REAL


def test_the_session_read_actually_asks_for_full_name():
    """The source-level half: trusting the profile is worthless if the read does not
    name the column, and the profile columns list did not include `full_name`."""
    assert "full_name" in authmod._PROFILE_COLUMNS, (
        "the session read never selects profiles.full_name, so it cannot be the "
        "source of the display name")


# ── the same rule on the refresh path ────────────────────────────────────────

def test_the_refresh_path_takes_the_name_from_the_profile_too():
    """`_refresh_token` sets `g.user_name` from Auth metadata as well — and it has
    the profile row in hand two lines later. Written as a source guard because the
    refresh flow needs a live cookie and a fake Supabase session to exercise."""
    body = AUTH_PY.split("def _refresh_token(", 1)[1]
    assert 'g.user_name = res.user.user_metadata.get("full_name", "")' not in body, (
        "the refresh path still trusts Auth metadata for the display name")
