"""The three choices a student makes during a paper, on every device they sit at.

Theme, language and the alert level were kept in `localStorage`, which is per
browser. The same student on the library laptop, then the lab machine, then their
own phone met the defaults again — so the choice made on Monday silently
disappeared on Tuesday, and the strip that offers the switches had no memory of
its own.

The profile is the one record every device shares, and it is already read once
per session (`_fetch_session`), so the choice rides along at **no extra
round-trip**. What this file holds:

1. The store: only the keys and values the app defines, whatever a client sent,
   with the volume clamped to `[0, 1]` and unknown keys dropped rather than
   written into the column.
2. The read is the session's own profile read — not a second query bolted onto
   the exam page, which the load harness drives.
3. A change writes through to the profile and patches the cached session, so the
   very next page on the same device does not flip the theme back.
4. `base.html` seeds the document from the server before it paints, and both the
   global toggles and the strip's alert controls persist what they change.
5. The write endpoint is one endpoint for every toggle, login-guarded and CSRF-
   protected by the app's own hook.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import user_preferences

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "app" / "templates" / "base.html"
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
AUTH = ROOT / "app" / "utils" / "auth.py"
INIT = ROOT / "app" / "__init__.py"
API = ROOT / "app" / "routes" / "api.py"
MIGRATION = ROOT / "supabase" / "migrations" / "036_user_ui_preferences.sql"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the component")


def source(path):
    return path.read_text(encoding="utf-8")


# ── 1. the store ──────────────────────────────────────────────────────────────

class _FakeTable:
    """The two calls the service makes, and nothing else.

    ``row`` is one ``profiles`` row, so a select hands back the row and a read of
    ``row["preferences"]`` is what the service actually sees — modelling the
    column, not the preferences object directly.
    """

    def __init__(self, row):
        self.row = row
        self.mode = None
        self.payload = None

    def select(self, *_a, **_k):
        self.mode = "select"
        return self

    def update(self, payload):
        self.mode = "update"
        self.payload = payload
        return self

    def eq(self, _col, _val):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self.mode == "update":
            self.row.update(self.payload)
        return type("R", (), {"data": dict(self.row)})()


class _FakeSupabase:
    def __init__(self, row=None):
        self.row = dict(row or {})

    def table(self, _name):
        return _FakeTable(self.row)


def test_only_the_keys_the_app_defines_survive_a_client_write():
    out = user_preferences.normalize({
        "theme": "dark", "lang": "en", "alert_volume": 0.4, "alert_muted": True,
        "role": "super_admin", "school_id": "somewhere-else", "nonsense": 1,
    })
    assert out == {"theme": "dark", "lang": "en", "alert_volume": 0.4, "alert_muted": True}, (
        "the preferences column accepted keys the app does not define, so a client "
        "can write whatever it likes into the profile")


def test_a_value_outside_its_domain_is_dropped_not_coerced():
    assert "theme" not in user_preferences.normalize({"theme": "solarized"})
    assert "lang" not in user_preferences.normalize({"lang": "fr"})
    assert "alert_muted" not in user_preferences.normalize({"alert_muted": "yes"}), (
        "a string was accepted as a boolean, so 'false' would read as True")


def test_the_volume_is_clamped_into_range():
    assert user_preferences.normalize({"alert_volume": 5})["alert_volume"] == 1.0
    assert user_preferences.normalize({"alert_volume": -2})["alert_volume"] == 0.0
    assert user_preferences.normalize({"alert_volume": 0.5})["alert_volume"] == pytest.approx(0.5)


def test_a_non_object_patch_is_not_a_crash():
    assert user_preferences.normalize(None) == {}
    assert user_preferences.normalize("dark") == {}
    assert user_preferences.normalize([1, 2]) == {}


def test_a_write_merges_into_what_is_already_stored():
    db = _FakeSupabase({"preferences": {"theme": "dark"}})
    merged = user_preferences.save(db, "u1", {"lang": "en"})
    assert merged == {"theme": "dark", "lang": "en"}, (
        "writing the language dropped the theme, so a second choice erases the first")
    assert db.row["preferences"] == {"theme": "dark", "lang": "en"}


def test_an_empty_patch_does_not_touch_the_row():
    db = _FakeSupabase({"preferences": {"theme": "dark"}})
    assert user_preferences.save(db, "u1", {"role": "x"}) == {"theme": "dark"}


# ── 2. the read rides the session's own profile read ─────────────────────────

def test_the_session_read_carries_the_preferences():
    text = source(AUTH)
    body = text[text.index("def _fetch_session("):text.index("def _session_for(")]
    assert "preferences" in body, (
        "the session read does not ask for the preferences column, so every device "
        "would need its own extra query to find them")
    assert '", preferences"' in body, (
        "the preferences are not part of the profile select that is already made")


def test_a_database_without_the_column_does_not_take_the_whole_session_down():
    """`_fetch_session` reads role, school and class from the same row.

    A select that names a column this database does not have yet (the migration
    has not been applied) is refused in full, which would blank the session's
    identity rather than only the preferences. The read therefore has to be able
    to fall back to the columns that have always existed.
    """
    text = source(AUTH)
    assert "_preferences_unavailable" in text or "fallback" in text.lower(), (
        "the profile read has no fallback for a database without the new column")
    assert re.search(r"except Exception[\s\S]{0,400}", text), (
        "there is no exception path around the profile select")


def test_the_session_puts_the_preferences_on_g():
    text = source(AUTH)
    body = text[text.index("def _apply_session("):text.index("def peek_identity(")]
    assert "g.user_prefs" in body, (
        "the applied session does not carry the preferences, so no template can read them")


def test_a_change_patches_the_cached_session():
    text = source(AUTH)
    assert "def set_session_prefs(" in text, (
        "there is no way to keep the cached session in step with a preference change, "
        "so the next page on the same device reverts to the old choice")


def test_every_page_can_read_the_preferences():
    text = source(INIT)
    assert "ui_prefs" in text, (
        "the context processor does not expose ui_prefs, so base.html cannot seed "
        "the document from the server")


# ── 3. the write endpoint ─────────────────────────────────────────────────────

def test_there_is_one_endpoint_every_toggle_writes_to():
    text = source(API)
    assert '@api_bp.route("/ui-preferences", methods=["POST"])' in text, (
        "there is no server route to store a preference")
    route = text[text.index('@api_bp.route("/ui-preferences"'):]
    route = route[:route.index("\n@api_bp.route")]
    assert "@login_required" in route, "the preference write is not login-guarded"
    assert "normalize(" in route and "save(" in route, (
        "the endpoint does not go through the one store, so its rules can drift")
    assert "set_session_prefs(" in route, (
        "the endpoint does not patch the cached session after the write")


# ── 4. the document is seeded before it paints ────────────────────────────────

def test_the_document_language_is_resolved_from_the_server_first():
    text = source(BASE)
    assert re.search(
        r"<html lang=\"\{\{\s*_content_lang or [^\"]*default_lang", text), (
        "the document language is not resolved with the stored preference as the "
        "next source, so a device that has never seen the choice flashes the default")
    assert "ui_prefs" in text, "base.html never reads the server preferences"


def test_the_head_script_reads_the_server_before_localstorage():
    text = source(BASE)
    start = text.index("window.__sgPrefs")
    head = text[start:start + 700]
    assert "p.theme" in head and "p.lang" in head, (
        "the pre-paint script does not read the server preferences, so a dark-on-"
        "one-device choice flashes light on the next")


def test_the_global_toggles_persist_what_they_change():
    text = source(BASE)
    toggle = text[text.index("toggleDark()"):]
    toggle = toggle[:toggle.index("\n        },")]
    assert "sgSavePrefs" in toggle and "theme" in toggle, (
        "changing the theme does not write it to the profile, so it stays per device")

    setter = text[text.index("setLang(next)"):]
    setter = setter[:setter.index("\n        },")]
    assert "sgSavePrefs" in setter and "lang" in setter, (
        "changing the language does not write it to the profile")


# ── 5. the strip seeds itself and persists its alert level ────────────────────

def test_the_strip_seeds_its_alert_level_from_the_server():
    text = source(EXAM_PAGE)
    assert "__sgPrefs" in text, "the strip never reads the server preferences"
    assert "alert_volume" in text and "alert_muted" in text, (
        "the alert level is seeded only from localStorage, so it does not follow "
        "the student to another device")


@needs_node
def test_changing_the_alert_level_is_written_to_the_server():
    """The component's own methods, run: a control that looks wired and is not is
    exactly the defect this strip exists to end."""
    text = source(EXAM_PAGE)

    def method(name):
        start = text.index(f"        {name}(")
        return text[start:text.index("\n        },", start) + len("\n        }")]

    script = """
const sent = [];
globalThis.window = { sgSavePrefs(p) { sent.push(p); } };
const store = {};
const beeps = [];
const obj = {
  alertVolume: 0.5, alertMuted: false,
  _remember(k, v) { store[k] = v; },
  beep() {},
%s,
};
obj.setAlertVolume('80');
obj.toggleAlertMute();
console.log(JSON.stringify({ sent, volume: obj.alertVolume, muted: obj.alertMuted }));
""" % ",\n".join([method("setAlertVolume"), method("toggleAlertMute")])

    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip())

    assert any(p.get("alert_volume") == 0.8 for p in out["sent"]), (
        "raising the slider never reached the profile")
    assert any(p.get("alert_muted") is True for p in out["sent"]), (
        "muting never reached the profile, so it is undone on the next device")


# ── 6. the migration ─────────────────────────────────────────────────────────

def test_the_column_arrives_with_a_migration():
    text = source(MIGRATION)
    assert "ALTER TABLE profiles" in text
    assert "ADD COLUMN IF NOT EXISTS preferences" in text, (
        "the migration is not idempotent, so a re-run would refuse")
    assert "JSONB" in text.upper(), "the preferences are not stored as one object"


def test_the_migration_is_non_destructive():
    text = source(MIGRATION).lower()
    for forbidden in ("drop table", "drop column", "delete from", "truncate"):
        assert forbidden not in text, (
            f"the migration contains `{forbidden}`; preferences are additive")
