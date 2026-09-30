"""What a mail needs *besides* a good body to land in the inbox.

Reported as *"make it not in spam, it is important or in inbox"*. A body cannot do
that alone: a message `smtplib` builds by hand arrives with no `Date`, no
`Message-ID` and no statement that it is machine-generated, and those three absences
are among the first things a filter scores. None of them is visible in the rendered
HTML a developer looks at, which is exactly why they were missing.

What each header is for, and why it is not decoration:

* **`Date`** — a message with no date is one no filter can age, and it is what a mail
  client shows instead of the sent time;
* **`Message-ID`** — most relays add one, and a message that reaches a filter without
  it looks like a forged or replayed one. It must be unique per message, and its
  domain should be the sender's, not a placeholder;
* **`Auto-Submitted: auto-generated`** (RFC 3834) plus
  **`X-Auto-Response-Suppress: All`** — the standard way to say "this is transactional,
  no human wrote it", which keeps a reset code out of the *bulk* folder and keeps
  vacation responders from answering it;
* **`Importance`/`X-Priority`** — the "important" half of the request: a one-time
  credential or a receipt is urgent to its reader and should be marked so;
* and **never `Precedence: bulk`** — the header people add thinking it means "bulk
  mail" is precisely what routes a transactional message to the bulk tab.

The bodies are checked here too, because a filter reads the subject line: no shouting,
no `!!!`, no "URGENT".
"""

import email
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.services import email_bodies, smtp_settings  # noqa: E402

RESOLVED = {
    "host": "smtp.example", "port": 587, "user": "scangrade9@gmail.com",
    "password": "p", "sender": "ScanGrade <scangrade9@gmail.com>",
    "reply_to": "noreply@scangrade.web.id", "source": "environment", "configured": True,
}


class _Server:
    """The smallest SMTP stand-in that records what was handed to it."""

    sent: list = []

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

    def sendmail(self, sender, to, raw):
        _Server.sent.append(raw)


@pytest.fixture
def sends(monkeypatch):
    """Every message this test sends, parsed back out of the wire format."""
    _Server.sent = []
    monkeypatch.setattr(smtp_settings.smtplib, "SMTP", _Server)
    monkeypatch.setattr(smtp_settings, "resolve", lambda: dict(RESOLVED))

    def send(subject="Subject", html="<p>hi</p>", text="hi", **kwargs):
        ok, error = smtp_settings.send("someone@school.id", subject, html,
                                       html=True, text=text, **kwargs)
        assert ok is True, error
        return email.message_from_string(_Server.sent[-1])

    return send


# ── 1. the headers a filter scores first ─────────────────────────────────────

class TestTheHeaders:
    def test_it_carries_a_date_and_a_message_id(self, sends):
        msg = sends()
        assert msg.get("Date"), "a message with no date cannot be aged by a filter"
        msg_id = msg.get("Message-ID") or ""
        assert msg_id.startswith("<") and msg_id.endswith(">"), msg_id
        assert "@" in msg_id, "a Message-ID without a domain looks forged"

    def test_the_message_id_is_the_sender_s_domain(self, sends):
        msg = sends()
        domain = msg["Message-ID"].split("@", 1)[1].strip(">")
        assert domain.endswith("gmail.com"), (
            "the id should belong to the mailbox that sent it, not a placeholder that "
            "disagrees with From")

    def test_two_messages_do_not_share_an_id(self, sends):
        first, second = sends(), sends()
        assert first["Message-ID"] != second["Message-ID"], (
            "a repeated Message-ID is what a filter reads as a replayed message")

    def test_it_says_it_is_machine_generated(self, sends):
        msg = sends()
        assert msg.get("Auto-Submitted") == "auto-generated", (
            "RFC 3834 is how a transactional message says so; without it a reset code "
            "is scored like bulk mail and vacation responders answer it")
        assert msg.get("X-Auto-Response-Suppress") == "All"

    def test_an_important_mail_is_marked_important(self, sends):
        msg = sends(important=True)
        assert (msg.get("Importance") or "").lower() == "high"
        assert msg.get("X-Priority", "").startswith("1"), (
            "the 'important' half of the request: a one-time credential is urgent to "
            "its reader")

    def test_an_ordinary_mail_is_not_shouted(self, sends):
        """The deploy alerts go through the same sender and must stay quiet."""
        msg = sends(important=False)
        assert msg.get("Importance") is None and msg.get("X-Priority") is None

    def test_it_never_marks_itself_bulk(self, sends):
        msg = sends(important=True)
        assert "Precedence" not in msg, (
            "`Precedence: bulk` is what puts a transactional message in the bulk tab; "
            "it is added by hand, so this is the guard that stops it being added")

    def test_from_carries_a_display_name(self, sends):
        msg_from = sends().get("From")
        assert msg_from
        assert "<" in msg_from and msg_from.split("<")[0].strip(), (
            "a bare address is how a school's filter reads a mail as automated noise")

    def test_reply_to_is_still_the_product_s_own_domain(self, sends):
        assert sends().get("Reply-To") == "noreply@scangrade.web.id"


# ── 2. one header set for every path, not per call site ──────────────────────

class TestTheCallSites:
    """A user-facing mail goes out marked important; the internal alerts do not.

    Four paths send the first kind (`auth` reset, `notification_service` approval,
    `super_admin` reset, `midtrans` receipt) and one sends the second (`deploy_alert`).
    A path that forgets `important=True` silently loses the "important" half of the
    request, which is invisible in every rendered preview.
    """

    #: file -> the body builder whose sending call must carry the flag. Anchored on
    #: the credential itself rather than on the file's *first* `mail["html"]`, which
    #: made this a rule about the order of functions in a file: `app/routes/auth.py`
    #: now also sends a registration acknowledgement, and a guard that fires on the
    #: alphabetically earlier mail is not a guard about credentials.
    CREDENTIAL_CALLS = (
        ("app/routes/auth.py", "email_bodies.reset_code("),
        ("app/routes/super_admin.py", "email_bodies.password_reset_by_admin("),
        ("app/services/midtrans_service.py", "email_bodies.payment_success("),
    )

    @pytest.mark.parametrize("rel,marker", CREDENTIAL_CALLS)
    def test_the_credential_mails_are_marked_important(self, rel, marker):
        """The flag has to be in the *call* that sends the body, not nearby."""
        source = (ROOT / rel).read_text(encoding="utf-8")
        at = source.index(marker)
        assert "important=True" in source[at:at + 600], (
            f"{rel} sends the credential built by {marker} without the header that "
            f"marks it as one")

    def test_the_approval_mail_marks_it_important(self):
        """The *call* must carry the flag — the parameter's default is not the setting.

        Asking only whether the word appears in the file is satisfied by the
        signature `important: bool = False`, which is the one place it does nothing.
        """
        source = (ROOT / "app/services/notification_service.py").read_text(encoding="utf-8")
        at = source.index("email_bodies.activation_code(")
        assert "important=True" in source[at:at + 600], (
            "the school-activation mail is a credential too, and it is the one a whole "
            "school waits for")

    def test_the_deploy_alerts_stay_unmarked(self):
        source = (ROOT / "app/services/deploy_alert_service.py").read_text(encoding="utf-8")
        assert "important=True" not in source, (
            "an internal alert that shouts important is how the flag stops meaning "
            "anything for the mail it was added for")

    @pytest.mark.parametrize("kind", ("reset_code", "activation_code",
                                      "password_reset_by_admin", "payment_success"))
    def test_no_subject_shouts(self, kind):
        args = {
            "reset_code": dict(name="Budi", code="AB12CD"),
            "activation_code": dict(name="Sari", school_name="SMPN 1", code="ZX90PQ",
                                    expires_at="besok"),
            "password_reset_by_admin": dict(name="Andi", new_password="Rahasia#123"),
            "payment_success": dict(name="Ratna", plan_name="12 Bulan", starts="a",
                                    ends="b", login_url="https://scangrade.web.id"),
        }[kind]
        subject = getattr(email_bodies, kind)(**args)["subject"]
        assert subject == subject.strip()
        assert subject.upper() != subject, "an all-caps subject is a spam signal"
        assert "!" not in subject
        assert not re.search(r"\b(urgent|gratis|free|winner|act now)\b", subject, re.I)
        assert len(subject) <= 78, "a subject longer than a preview line reads as noise"


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
