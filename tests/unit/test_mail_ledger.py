"""A mail path that is broken has to be visible before a locked-out user discovers it.

Requested: *"make a failed or skipped password-reset email visible to an operator on the
super-admin status pages, so a school whose mail cannot be sent is not discovered by a
locked-out user."*

Measured before this existed: the settings page's verdict was `configured`, which is
`bool(user and password)` — a box with the **wrong** app password reports "Email is
ready to send" and then every reset silently fails. The only trace was a `logger.warning`
in a journal nobody reads without a shell, and the first person to find out was the pupil
who could not get back into their account.

So the sender records what actually happened, and two pages read it:

* `app/services/mail_ledger.py` keeps the outcome of the last attempts in one
  `system_settings` row (service-role only, the same home the credential itself uses —
  no migration, and nothing a page can render a secret from);
* `smtp_settings.send` is the one place every mail path goes through, so recording there
  covers the reset code, the school activation, the super-admin reset, the payment
  receipt and the test send — including any path added later, which is the difference
  between a report and a report of the paths somebody remembered.

What is asserted, and why each is held rather than described:

* **the failure survives the request that caused it.** A warning in a log is not a
  record; the row is read back by a different process on a different page.
* **`skipped` is distinguished from `failed`.** "No credential at all" and "the relay
  said 535" are different problems with different fixes, and an operator who reads one
  as the other will change the wrong thing.
* **it says how long.** The first failure of a run is kept, so the page can say "since
  09:12, four attempts" rather than showing a single timestamp with no history.
* **a body cannot leak.** The record is built from an allow-list of keys — no message
  body, no reset code — and this suite asserts the persisted JSON *is* that allow-list
  rather than trusting the construction.
* **the ledger can never break the mail path.** A store that raises is swallowed; the
  send's own result is unchanged. A reporting feature that turns a working reset into a
  500 is worse than no reporting.
* **a success clears the alarm**, and the dashboard stays quiet when it is quiet —
  a banner that is always green stops meaning anything.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import mail_ledger, smtp_settings  # noqa: E402

EMAIL_SETTINGS = ROOT / "app" / "templates" / "super_admin" / "email_settings.html"
DASHBOARD = ROOT / "app" / "templates" / "super_admin" / "dashboard.html"
ROUTES = ROOT / "app" / "routes" / "super_admin.py"
AUTH = ROOT / "app" / "routes" / "auth.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _Resp:
    def __init__(self, data):
        self.data = data


class _Table:
    """A one-row key/value stand-in for ``system_settings``, with upsert-on-``key``."""

    def __init__(self, store, *, explode=False):
        self._store = store
        self._explode = explode
        self._keys = None
        self._row = None

    def select(self, _cols="*"):
        return self

    def in_(self, _col, keys):
        self._keys = list(keys)
        return self

    def eq(self, _col, value):
        self._keys = [value]
        return self

    def upsert(self, row, **_kw):
        if self._explode:
            raise RuntimeError("database is unhappy")
        self._row = dict(row)
        return self

    def execute(self):
        if self._explode:
            raise RuntimeError("database is unhappy")
        if self._row is not None:
            self._store[self._row["key"]] = self._row["value"]
            self._row = None
            return _Resp([{"key": k, "value": v} for k, v in self._store.items()])
        if self._keys is not None:
            return _Resp([{"key": k, "value": self._store[k]}
                          for k in self._keys if k in self._store])
        return _Resp([{"key": k, "value": v} for k, v in self._store.items()])


class _FakeSupabase:
    def __init__(self, store=None, *, explode=False):
        self.store = store if store is not None else {}
        self._explode = explode

    def table(self, _name):
        return _Table(self.store, explode=self._explode)


@pytest.fixture
def store(monkeypatch):
    """The ledger backed by an in-memory row, and what was written to it."""
    fake = _FakeSupabase()
    monkeypatch.setattr(mail_ledger, "get_supabase", lambda: fake)
    return fake.store


def _row(fake):
    return json.loads(fake[mail_ledger.KEY])


# ── 1. the record itself ─────────────────────────────────────────────────────

class TestTheRecord:
    def test_a_failure_is_read_back_by_another_reader(self, store):
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to="budi@school.id",
                           subject="Kode reset password", detail="535 BadCredentials")
        snap = mail_ledger.snapshot()
        assert snap["state"] == mail_ledger.STATE_FAILED
        assert snap["broken"] is True
        assert snap["last"]["to"] == "budi@school.id"
        assert "535" in snap["last"]["detail"], (
            "the relay's own words are the difference between a fixable report and "
            "'the email failed'")

    def test_nothing_recorded_is_unknown_and_not_an_alarm(self, store):
        snap = mail_ledger.snapshot()
        assert snap["state"] == mail_ledger.STATE_UNKNOWN
        assert snap["broken"] is False, (
            "a box that has never sent anything is reported as broken, which makes "
            "the very first alarm untrustworthy")
        assert snap["last"] is None and snap["recent"] == []

    def test_skipped_and_failed_are_different_states(self, store):
        mail_ledger.record(state=mail_ledger.STATE_SKIPPED, to="a@b.c", subject="s")
        assert mail_ledger.snapshot()["state"] == mail_ledger.STATE_SKIPPED
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to="a@b.c", subject="s",
                           detail="relay refused")
        assert mail_ledger.snapshot()["state"] == mail_ledger.STATE_FAILED
        assert mail_ledger.snapshot()["broken"] is True

    def test_it_says_how_long_the_run_of_failures_is(self, store, monkeypatch):
        """The clock is stepped by hand, because two records a few microseconds apart
        carry the same stamp — and a guard that cannot tell "since stayed" from
        "since was re-stamped" passes on the bug it was written for."""
        stamps = iter(["2026-09-30T09:12:00Z", "2026-09-30T09:47:00Z",
                       "2026-09-30T10:03:00Z"])
        monkeypatch.setattr(mail_ledger, "_now", lambda: next(stamps))
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to="a@b.c", subject="s")
        first = mail_ledger.snapshot()["since"]
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to="a@b.c", subject="s")
        second = mail_ledger.snapshot()
        assert first == "2026-09-30T09:12:00Z"
        assert second["since"] == first, (
            "each failure resets the clock, so a mail path broken since morning reads "
            "as if it broke a minute ago")
        assert second["last"]["at"] == "2026-09-30T09:47:00Z", (
            "the attempt's own time is gone, so the page cannot say when it last tried")
        assert second["failures"] == 2

    def test_a_success_clears_the_alarm(self, store):
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to="a@b.c", subject="s")
        mail_ledger.record(state=mail_ledger.STATE_SENT, to="a@b.c", subject="s")
        snap = mail_ledger.snapshot()
        assert snap["broken"] is False and snap["since"] is None
        assert snap["state"] == mail_ledger.STATE_SENT

    def test_the_history_is_bounded_and_newest_first(self, store):
        for i in range(mail_ledger.KEEP + 5):
            mail_ledger.record(state=mail_ledger.STATE_SENT, to=f"u{i}@b.c", subject="s")
        snap = mail_ledger.snapshot()
        assert len(snap["recent"]) == mail_ledger.KEEP
        assert snap["recent"][0]["to"] == f"u{mail_ledger.KEEP + 4}@b.c", (
            "the newest attempt is not first, so the page shows the oldest")

    def test_a_body_or_a_code_cannot_be_stored(self, store):
        """The record is an allow-list, asserted against what was *persisted*: a
        reset code in a settings row is a credential in a place nobody audits."""
        mail_ledger.record(state=mail_ledger.STATE_SENT, to="a@b.c", subject="Kode",
                           detail="ok")
        keys = set()
        for entry in [mail_ledger.snapshot()["last"]] + mail_ledger.snapshot()["recent"]:
            keys |= set(entry)
        assert keys <= {"at", "state", "to", "subject", "detail"}, (
            f"the record grew a field: {sorted(keys - {'at', 'state', 'to', 'subject', 'detail'})}"
            " — anything that carries a body or a code belongs nowhere near it")

    def test_a_long_relay_error_is_truncated(self, store):
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to="a@b.c", subject="s",
                           detail="x" * 5000)
        assert len(mail_ledger.snapshot()["last"]["detail"]) <= 400, (
            "a 5 KB relay transcript is stored on every failure and rendered into "
            "every visit to the page")


# ── 2. the ledger can never break the mail path ──────────────────────────────

class TestItCannotBreakAMail:
    def test_a_store_that_raises_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(mail_ledger, "get_supabase",
                            lambda: _FakeSupabase(explode=True))
        mail_ledger.record(state=mail_ledger.STATE_SENT, to="a@b.c", subject="s")
        assert mail_ledger.snapshot()["state"] == mail_ledger.STATE_UNKNOWN, (
            "the reader crashed on a store it could not reach, so the page 500s "
            "exactly when the operator needs it")

    def test_a_working_send_stays_working(self, monkeypatch):
        monkeypatch.setattr(smtp_settings, "resolve", lambda: {
            "host": "smtp.example", "port": 587, "user": "u@e", "password": "p",
            "sender": "ScanGrade <u@e>", "reply_to": "n@e", "source": "database",
            "configured": True})
        monkeypatch.setattr(mail_ledger, "get_supabase",
                            lambda: _FakeSupabase(explode=True))

        class _Smtp:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                pass

            def login(self, *a):
                pass

            def sendmail(self, *a):
                pass

        monkeypatch.setattr(smtp_settings.smtplib, "SMTP", _Smtp)
        ok, error = smtp_settings.send("a@b.c", "Subjek", "<p>x</p>", html=True)
        assert ok is True and error is None, (
            "a ledger write turned a sent mail into a failure — the reset the reader "
            "was waiting for would be reported as undeliverable")


# ── 3. the one sender records every outcome ──────────────────────────────────

class TestTheSenderRecords:
    @pytest.fixture
    def configured(self, monkeypatch):
        monkeypatch.setattr(smtp_settings, "resolve", lambda: {
            "host": "smtp.example", "port": 587, "user": "u@e", "password": "p",
            "sender": "ScanGrade <u@e>", "reply_to": "n@e", "source": "database",
            "configured": True})

    def test_an_unconfigured_box_records_skipped(self, store, monkeypatch):
        monkeypatch.setattr(smtp_settings, "resolve", lambda: {
            "host": "h", "port": 587, "user": "", "password": "", "sender": "",
            "reply_to": "n@e", "source": "environment", "configured": False})
        ok, _ = smtp_settings.send("budi@school.id", "Kode reset password", "x",
                                   html=True)
        assert ok is False
        snap = mail_ledger.snapshot()
        assert snap["state"] == mail_ledger.STATE_SKIPPED and snap["broken"] is True, (
            "a box with no credential sends nothing and says nothing, which is the "
            "case a locked-out pupil finds first")
        assert snap["last"]["to"] == "budi@school.id"
        assert snap["last"]["subject"] == "Kode reset password"

    def test_a_relay_refusal_records_failed(self, store, configured, monkeypatch):
        class _Smtp:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                pass

            def login(self, *a):
                raise RuntimeError("535 BadCredentials")

        monkeypatch.setattr(smtp_settings.smtplib, "SMTP", _Smtp)
        ok, error = smtp_settings.send("budi@school.id", "Kode reset password", "x",
                                       html=True)
        assert ok is False and "535" in error
        snap = mail_ledger.snapshot()
        assert snap["state"] == mail_ledger.STATE_FAILED
        assert "535" in snap["last"]["detail"], (
            "the page can say the mail failed but not why, which leaves the operator "
            "guessing between a typo in the password and the relay being down")

    def test_a_sent_mail_records_sent(self, store, configured, monkeypatch):
        class _Smtp:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                pass

            def login(self, *a):
                pass

            def sendmail(self, *a):
                pass

        monkeypatch.setattr(smtp_settings.smtplib, "SMTP", _Smtp)
        ok, _ = smtp_settings.send("budi@school.id", "Kode reset password", "x",
                                   html=True)
        assert ok is True
        assert mail_ledger.snapshot()["state"] == mail_ledger.STATE_SENT

    def test_the_test_send_is_recorded_too(self, store, monkeypatch):
        """"Prove it works" has to be able to clear the alarm, or the page keeps the
        operator in a state they have already resolved."""
        monkeypatch.setattr(smtp_settings, "resolve", lambda: {
            "host": "smtp.example", "port": 587, "user": "u@e", "password": "p",
            "sender": "ScanGrade <u@e>", "reply_to": "n@e", "source": "database",
            "configured": True})

        class _Smtp:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                pass

            def login(self, *a):
                pass

            def sendmail(self, *a):
                pass

        monkeypatch.setattr(smtp_settings.smtplib, "SMTP", _Smtp)
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to="x@y.z", subject="old")
        smtp_settings.send_test("admin@school.id")
        assert mail_ledger.snapshot()["broken"] is False, (
            "the test send did not reach the ledger, so a fixed mail path still "
            "shows as broken on the page that proved it fixed")


# ── 4. the pages an operator reads ───────────────────────────────────────────

@pytest.fixture
def page(app):
    """Render a super-admin template inside a request that has a session."""
    from flask import g

    def render(name, **context):
        with app.test_request_context("/super-admin/dashboard"):
            g.user_id, g.user_role = "sa-1", "super_admin"
            g.user_name, g.user_email = "Operator", "sa@scangrade.id"
            g.user_school_id, g.tz_offset, g.show = None, 7, {}
            g.must_change_password = False
            return app.jinja_env.get_template(name).render(**context)

    return render


HEALTHY = {"state": "sent", "since": None, "broken": False, "attempts": 3,
           "failures": 0, "recent": [], "last": {"at": "2026-09-30T10:00:00Z",
                                                 "state": "sent", "to": "a@b.c",
                                                 "subject": "Kode reset password",
                                                 "detail": ""}}
BROKEN = {"state": "failed", "since": "2026-09-30T09:12:00Z", "broken": True,
          "attempts": 4, "failures": 4,
          "last": {"at": "2026-09-30T09:20:00Z", "state": "failed",
                   "to": "budi@school.id", "subject": "Kode reset password",
                   "detail": "535 BadCredentials"},
          "recent": [{"at": "2026-09-30T09:20:00Z", "state": "failed",
                      "to": "budi@school.id", "subject": "Kode reset password",
                      "detail": "535 BadCredentials"}]}


class TestTheOperatorSeesIt:
    def test_the_settings_page_reports_the_observed_state_not_the_credential(
            self, page):
        """`configured` is `bool(user and password)`. A box with the wrong password
        is configured and broken, and that is the whole point of this work."""
        body = page("super_admin/email_settings.html", settings={}, configured=True,
                    source="database", password_set=True, mail=BROKEN)
        assert "535 BadCredentials" in body or "budi@school.id" in body, (
            "the page shows the credential is present and says nothing about the "
            "mail that has been failing for hours")

    def test_the_settings_page_says_since_when(self, page):
        body = page("super_admin/email_settings.html", settings={}, configured=True,
                    source="database", password_set=True, mail=BROKEN)
        assert "09:12" in body or "since" in body.lower(), (
            "a single timestamp with no run length cannot tell a blip from an outage")

    def _dash(self, page, mail):
        return page("super_admin/dashboard.html", total_schools=0, total_users=0,
                    total_teachers=0, total_students=0, total_exams=0, total_subs=0,
                    pending_requests=0, schools=[], recent_logs=[], requests=[],
                    mail=mail)

    def test_the_dashboard_shows_a_broken_mail_path(self, page):
        body = self._dash(page, BROKEN)
        assert 'data-mail-alert="broken"' in body, (
            "an operator who never opens the email settings page is told nothing")
        assert "budi@school.id" in body and "535" in body

    def test_the_dashboard_is_silent_when_the_mail_path_is_healthy(self, page):
        """Asserted on the card's own marker, not on the words 'email' or 'smtp': the
        sidebar is full of both, and a guard that reads them is satisfied by the menu."""
        body = self._dash(page, HEALTHY)
        assert "data-mail-alert" not in body, (
            "a healthy mail path takes up dashboard space, so the banner stops being "
            "a signal")
        assert "535" not in body and "budi@school.id" not in body

    def test_both_pages_are_handed_the_ledger(self):
        source = _text(ROUTES)
        assert source.count("mail_ledger.snapshot()") >= 2, (
            "one of the two pages renders from a template variable nothing fills, "
            "so it would show an empty card and read as healthy")

    def test_the_new_copy_is_bilingual(self):
        for path in (EMAIL_SETTINGS, DASHBOARD):
            text = _text(path)
            for match in re.finditer(r"x-text=\"t\('([^']*)','([^']*)'\)\"", text):
                assert match.group(1) and match.group(2), (
                    f"{path.name}: a translated string has an empty half")

    def test_the_reset_route_records_a_mail_it_could_not_even_build(self):
        """The visitor is told "we could not email you" while the ledger stays silent,
        which is the one failure an operator cannot see at all.

        Read on the handler's own boundaries — the `except` that logs the failure, up to
        the branch that tells the visitor — rather than a character window, which the
        comment explaining the record pushes the record out of."""
        source = _text(AUTH)
        at = source.index("Failed to send reset code")
        start = source.rindex("except Exception", 0, at)
        end = source.index("if not sent:", at)
        handler = source[start:end]
        assert "mail_ledger" in handler, (
            "a reset whose body could not be built is not recorded anywhere")


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
