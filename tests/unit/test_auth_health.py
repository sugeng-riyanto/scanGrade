"""A box whose GoTrue hiccups constantly must be visible before a school reports it.

`create_user_with_retry` (`app/utils/auth_retry.py`) exists because GoTrue answers
`Database error creating new user` for a *transient* insert failure, and repeating
the create works. That retry is invisible: the operator sees success, and the only
trace of a box whose auth server is struggling is a school eventually saying "it
keeps failing". The retry count is the leading indicator, and nothing showed it.

So it is recorded where a page can see it, in the shape `lock_health` already
uses — a per-worker counter plus a marker every worker can read, because this box
runs three gevent workers and the page would otherwise show only whichever one it
landed on:

* **clean** — the store recorded nothing, so auth creates are landing first try.
* **recorded** — a create needed a retry since this worker started (or a marker
  says another worker did). Amber: the warning that arrives while nothing looks
  wrong.

Fail-open: no record, an unreadable marker, a read-only temp dir — each means
"nothing recorded", never an exception from inside an account create. And the
record must never live in the checkout, because the deploy runner refuses a dirty
tree.
"""
import json
import pathlib

from app.utils import auth_health


def _marker(tmp_path, **body):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_a_clean_worker_reports_nothing_on_record(tmp_path):
    auth_health.reset()
    reading = auth_health.state(path=tmp_path / "none.json")
    assert reading["key"] == auth_health.CLEAN
    assert reading["recorded"] is False
    assert reading["worker_retries"] == 0


def test_a_retry_is_counted_and_turns_the_state_amber(tmp_path):
    auth_health.reset()
    path = tmp_path / "auth.json"
    auth_health.record_retry("database error creating new user", path=path)
    reading = auth_health.state(path=path)
    assert reading["key"] == auth_health.RECORDED
    assert reading["recorded"] is True
    assert reading["worker_retries"] == 1
    assert reading["worker_exhausted"] == 0
    assert "database error" in reading["worker_reason"]

    auth_health.record_retry("database error saving new user", path=path)
    assert auth_health.state(path=path)["worker_retries"] == 2


def test_an_exhausted_create_is_counted_separately(tmp_path):
    auth_health.reset()
    path = tmp_path / "auth.json"
    auth_health.record_retry("database error creating new user", path=path)
    auth_health.record_exhausted("database error creating new user", path=path)
    reading = auth_health.state(path=path)
    assert reading["worker_retries"] == 1
    assert reading["worker_exhausted"] == 1


def test_the_first_and_last_retry_are_timestamped(tmp_path):
    auth_health.reset()
    path = tmp_path / "auth.json"
    auth_health.record_retry("a", path=path)
    first = auth_health.state(path=path)["worker_first_at"]
    auth_health.record_retry("b", path=path)
    reading = auth_health.state(path=path)
    assert reading["worker_first_at"] == first
    assert reading["worker_last_at"] is not None


def test_reset_forgets_the_worker_counts(tmp_path):
    auth_health.reset()
    auth_health.record_retry("a", path=tmp_path / "auth.json")
    auth_health.reset()
    assert auth_health.state(path=tmp_path / "none.json")["worker_retries"] == 0


# ── the cross-worker marker ───────────────────────────────────────────────────

def test_a_marker_from_another_worker_is_read_as_recorded(tmp_path):
    auth_health.reset()
    path = _marker(tmp_path, at="2026-09-01T00:00:00+00:00",
                   reason="database error creating new user", worker="9999")
    reading = auth_health.state(path=path)
    assert reading["key"] == auth_health.RECORDED
    assert reading["recorded"] is True
    assert reading["marker"]["key"] == "ok"
    assert reading["marker"]["reason"] == "database error creating new user"
    assert reading["marker"]["worker"] == "9999"


def test_a_marker_is_written_once_per_worker(tmp_path):
    auth_health.reset()
    path = tmp_path / "auth.json"
    auth_health.record_retry("first", path=path)
    auth_health.record_retry("second", path=path)
    assert path.exists()
    # First writer wins: the earliest record of an outage is the evidence.
    assert json.loads(path.read_text(encoding="utf-8"))["reason"] == "first"


def test_a_clean_success_clears_the_marker(tmp_path):
    auth_health.reset()
    path = _marker(tmp_path, at="2026-09-01T00:00:00+00:00", reason="x", worker="1")
    auth_health.record_clean(path=path, now=10_000.0)
    assert not path.exists()
    assert auth_health.state(path=path)["key"] == auth_health.CLEAN


def test_an_unreadable_marker_is_not_read_as_clean(tmp_path):
    """A marker that exists and cannot be parsed is a record, not silence."""
    auth_health.reset()
    path = tmp_path / "auth.json"
    path.write_text("{not json", encoding="utf-8")
    reading = auth_health.state(path=path)
    assert reading["marker"]["present"] is True
    assert reading["marker"]["key"] != "absent"


def test_a_missing_file_is_the_only_absent_case(tmp_path):
    auth_health.reset()
    reading = auth_health.state(path=tmp_path / "none.json")
    assert reading["marker"]["key"] == "absent"
    assert reading["recorded"] is False


def test_a_failed_write_never_raises(tmp_path):
    auth_health.reset()
    # A path whose parent is a file, not a directory: mkdir must fail, and the
    # create must still not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    auth_health.record_retry("x", path=blocker / "auth.json")
    assert auth_health.state(path=blocker / "auth.json")["worker_retries"] == 1


# ── the retry rides the record ────────────────────────────────────────────────

class _Transient(Exception):
    pass


def test_a_transient_failure_is_recorded_before_it_retries(tmp_path, monkeypatch):
    from app.utils import auth_retry

    auth_health.reset()
    monkeypatch.setattr(auth_health, "_STATE_FILE_OVERRIDE", tmp_path / "auth.json")
    calls = {"n": 0}

    def attempt():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Transient("Database error creating new user")
        return "created"

    result = auth_retry.create_user_with_retry(
        attempt, attempts=3, backoff=(0, 0), sleep=lambda s: None)
    assert result == "created"
    reading = auth_health.state(path=tmp_path / "auth.json")
    assert reading["worker_retries"] == 1


def test_an_exhausted_retry_is_counted(tmp_path, monkeypatch):
    from app.utils import auth_retry

    auth_health.reset()
    monkeypatch.setattr(auth_health, "_STATE_FILE_OVERRIDE", tmp_path / "auth.json")

    def attempt():
        raise _Transient("Database error creating new user")

    import pytest
    with pytest.raises(auth_retry.AccountNotCreated):
        auth_retry.create_user_with_retry(attempt, attempts=2, backoff=(0,),
                                          sleep=lambda s: None)
    reading = auth_health.state(path=tmp_path / "auth.json")
    assert reading["worker_exhausted"] == 1


def test_a_non_transient_failure_is_not_recorded(tmp_path, monkeypatch):
    from app.utils import auth_retry

    auth_health.reset()
    monkeypatch.setattr(auth_health, "_STATE_FILE_OVERRIDE", tmp_path / "auth.json")

    def attempt():
        raise _Transient("email already registered")

    import pytest
    with pytest.raises(_Transient):
        auth_retry.create_user_with_retry(attempt, attempts=3, backoff=(0, 0),
                                          sleep=lambda s: None)
    assert auth_health.state(path=tmp_path / "auth.json")["worker_retries"] == 0


def test_a_first_try_success_records_nothing_and_clears(tmp_path, monkeypatch):
    from app.utils import auth_retry

    auth_health.reset()
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"at": "2026-09-01T00:00:00+00:00", "reason": "x",
                                "worker": "1"}), encoding="utf-8")
    monkeypatch.setattr(auth_health, "_STATE_FILE_OVERRIDE", path)

    auth_retry.create_user_with_retry(lambda: "created", attempts=3,
                                      backoff=(0, 0), sleep=lambda s: None)
    assert auth_health.state(path=path)["worker_retries"] == 0
    assert not path.exists()


# ── it reaches the status page ────────────────────────────────────────────────

def test_the_status_route_passes_the_reading(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "app" / "routes" / "super_admin.py").read_text(encoding="utf-8")
    assert "auth_health" in src, "the status route never reads the auth record"
    assert "authretries=" in src, "the reading never reaches the template"


def test_the_status_page_shows_the_reading():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    html = (root / "app" / "templates" / "super_admin" / "deploy_status.html"
            ).read_text(encoding="utf-8")
    assert "authretries" in html, "the page ignores the auth record"
    assert "worker_retries" in html
