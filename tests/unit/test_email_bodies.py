"""The mail a school actually reads: one professional body, in two languages, twice.

Measured before this file existed, the user-facing mail was four hand-written strings
in four modules:

* a reset code inside **box-drawing characters** (`┌─────┐`) — charming in a terminal
  and monospaced-garbage in every mail client;
* an activation code in a one-off orange HTML blob of its own;
* a super-admin password reset and a payment receipt as bare paragraphs;
* and **two** independent SMTP clients — `notification_service.send_email` opened its
  own `smtplib` connection with its own `From`/`Reply-To`, so the credential resolver,
  the alias names and the reply address all had a second implementation to disagree
  with.

The guards here are about the *class* of defect, not the wording:

* every body is **bilingual** — the product is, and a parent reading an English-only
  reset mail is a support ticket;
* every HTML body ships a **plain-text alternative**, because a mail client that
  refuses HTML must still be able to read the code, and a one-part HTML mail is the
  shape spam filters score highest;
* the code and the name are **escaped into the layout**, never concatenated into it:
  a pupil called `<b>Budi</b>` is a name, not markup;
* the reply address is on the product's own domain, not the sending Gmail;
* and there is **one sender** (`smtp_settings.send`), so the credit that was just made
  readable under either `.env` name is the credit every mail uses.
"""

import email
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.services import email_bodies  # noqa: E402

KINDS = ("reset_code", "activation_code", "password_reset_by_admin", "payment_success",
         "registration_received")

#: A phrase only the *English* half carries, per kind. Checking for the word
#: "English" would pass on the label alone — and did: the first version of this guard
#: survived a mutation that stopped rendering `{english}` entirely, because the
#: layout's own divider says "English" whether or not any English follows it.
ENGLISH_MARKERS = {
    "reset_code": "reset the password",
    "activation_code": "has been approved",
    "password_reset_by_admin": "administrator has reset",
    "payment_success": "we received your payment",
    "registration_received": "has been received",
}


def _build(kind: str) -> dict:
    if kind == "reset_code":
        return email_bodies.reset_code(name="Budi Santoso", code="AB12CD")
    if kind == "activation_code":
        return email_bodies.activation_code(
            name="Ibu Sari", school_name="SMPN 1 Bandung", code="ZX90PQ",
            expires_at="1 Januari 2027")
    if kind == "password_reset_by_admin":
        return email_bodies.password_reset_by_admin(name="Bapak Andi", new_password="Rahasia#123")
    if kind == "registration_received":
        return email_bodies.registration_received(
            name="Ibu Sari", school_name="SMPN 1 Bandung", npsn="12345678", position="Guru")
    return email_bodies.payment_success(
        name="Ibu Ratna", plan_name="12 Bulan", starts="1 Oktober 2026",
        ends="1 Oktober 2027", login_url="https://scangrade.web.id/auth/login")


# ── 1. what every body owes the reader ───────────────────────────────────────

class TestEveryBody:
    @pytest.mark.parametrize("kind", KINDS)
    def test_it_carries_a_subject_two_parts_and_the_brand(self, kind):
        out = _build(kind)
        assert out["subject"].strip(), "a mail with no subject is spam"
        assert "ScanGrade" in out["subject"]
        assert out["html"].strip() and out["text"].strip()
        assert "ScanGrade" in out["html"] and "ScanGrade" in out["text"]

    @pytest.mark.parametrize("kind", KINDS)
    def test_it_is_bilingual(self, kind):
        """Both halves are *content*, not the word "English" on a divider."""
        out = _build(kind)
        marker = ENGLISH_MARKERS[kind]
        for part in ("html", "text"):
            assert marker in out[part], (
                f"the {part} half carries no English text, so a reader who does not "
                f"read Indonesian is left with a language they cannot use (looked for "
                f"{marker!r})")
        for part in ("html", "text"):
            assert "Yth" in out[part] or "Halo" in out[part], (
                f"the {part} half has no Indonesian greeting, and that is the language "
                f"the school reads")

    @pytest.mark.parametrize("kind", KINDS)
    def test_it_leaves_no_unrendered_braces(self, kind):
        out = _build(kind)
        assert "{" not in out["html"] and "}" not in out["html"], (
            "an f-string placeholder survived into the body the reader gets")

    @pytest.mark.parametrize("kind", KINDS)
    def test_it_is_a_complete_html_document(self, kind):
        out = _build(kind)
        head = out["html"].lstrip().lower()
        assert head.startswith("<!doctype html") or head.startswith("<html"), head[:40]
        assert "</html>" in out["html"].lower()

    @pytest.mark.parametrize("kind", KINDS)
    def test_it_names_the_product_s_own_domain_and_says_it_is_automated(self, kind):
        out = _build(kind)
        assert email_bodies.BRAND_URL in out["html"]
        assert "noreply@" in out["html"], (
            "a one-way message has to name the address it came from, or a reply "
            "disappears into the sending Gmail")


# ── 2. the secrets, and the names ────────────────────────────────────────────

class TestTheImportantBits:
    def test_the_reset_code_is_in_both_parts(self):
        out = email_bodies.reset_code(name="Budi", code="AB12CD")
        assert "AB12CD" in out["html"] and "AB12CD" in out["text"]
        assert "10" in out["html"], "a code with no lifetime is a code kept for ever"

    def test_the_new_password_is_in_both_parts_and_asks_to_be_changed(self):
        out = email_bodies.password_reset_by_admin(name="Andi", new_password="Rahasia#123")
        assert "Rahasia#123" in out["html"] and "Rahasia#123" in out["text"]
        assert re.search(r"ubah|change", out["html"], re.I)

    def test_the_activation_code_is_in_both_parts_with_its_expiry(self):
        out = email_bodies.activation_code(name="Sari", school_name="SMPN 1",
                                           code="ZX90PQ", expires_at="1 Januari 2027")
        assert "ZX90PQ" in out["html"] and "ZX90PQ" in out["text"]
        assert "1 Januari 2027" in out["html"]

    def test_the_payment_receipt_names_the_plan_and_the_window(self):
        out = email_bodies.payment_success(name="Ratna", plan_name="12 Bulan",
                                           starts="1 Oktober 2026", ends="1 Oktober 2027",
                                           login_url="https://scangrade.web.id/auth/login")
        for field in ("12 Bulan", "1 Oktober 2026", "1 Oktober 2027"):
            assert field in out["html"] and field in out["text"]

    def test_a_name_is_text_and_never_markup(self):
        """The name comes from an import sheet, so it is data — not template."""
        out = email_bodies.reset_code(name="<script>alert(1)</script>", code="AB12CD")
        assert "<script>" not in out["html"]
        assert "&lt;script&gt;" in out["html"], (
            "the name was interpolated raw, so a school's own sheet can inject markup "
            "into a mail the app sends")

    def test_a_code_is_escaped_too(self):
        """It is generated today, but nothing in the type says so."""
        out = email_bodies.reset_code(name="Budi", code="<b>A1</b>")
        assert "<b>A1</b>" not in out["html"]
        assert "&lt;b&gt;" in out["html"]

    def test_a_subject_cannot_be_broken_by_a_newline(self):
        out = email_bodies.activation_code(name="Sari", school_name="SMPN\n1",
                                           code="ZX90PQ", expires_at="besok")
        assert "\n" not in out["subject"] and "\r" not in out["subject"], (
            "a header with a newline in it is how a body is smuggled into a subject")


# ── 3. one sender, and a plain-text half that really travels ────────────────

class TestTheOneSender:
    def test_the_service_hands_the_plain_half_to_the_sender(self, monkeypatch):
        """Delegating the *call* is not enough — the alternative part must travel."""
        from app.services import notification_service, smtp_settings

        seen = {}

        def fake_send(to, subject, body, html=False, text=None, important=False):
            seen.update(to=to, subject=subject, body=body, html=html, text=text,
                        important=important)
            return True, None

        monkeypatch.setattr(smtp_settings, "send", fake_send)
        assert notification_service.send_email("a@b.c", "Subjek", "<p>halo</p>", text="halo") is True
        assert seen["html"] is True and seen["text"] == "halo"
        # The activation mail is a credential a whole school waits for, so the
        # "important" half has to survive the delegation as well — a sender that drops
        # it puts the mail back among the ones a filter batches.
        assert notification_service.send_email(
            "a@b.c", "Subjek", "<p>halo</p>", text="halo", important=True) is True
        assert seen["important"] is True

    def test_the_reset_mail_says_the_lifetime_the_server_enforces(self):
        """One number, derived — a sentence that promises ten minutes over a fifteen
        minute expiry is a lie the reader cannot check."""
        from app.routes import auth as auth_mod

        minutes = auth_mod.RESET_CODE_TTL_SECONDS // 60
        out = email_bodies.reset_code(name="Budi", code="AB12CD", minutes=minutes)
        assert f"{minutes} menit" in out["html"]
        source = (ROOT / "app" / "routes" / "auth.py").read_text(encoding="utf-8")
        assert "minutes=RESET_CODE_TTL_SECONDS // 60" in source, (
            "the route hard-codes a lifetime again, so the mail and the expiry can drift")

    def test_the_notification_service_no_longer_opens_its_own_connection(self):
        source = (ROOT / "app" / "services" / "notification_service.py").read_text(encoding="utf-8")
        assert "import smtplib" not in source and "MIMEMultipart" not in source, (
            "a second SMTP client is a second `From`, a second `Reply-To` and a second "
            "credential path — the exact drift the resolver exists to prevent")
        assert "smtp_settings" in source

    def test_send_carries_both_parts_in_one_multipart_alternative(self, monkeypatch):
        """The plain half must be *in* the message, first, as its own alternative."""
        from app.services import smtp_settings

        sent = {}

        class FakeServer:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                pass

            def login(self, *a):
                sent["login"] = a

            def sendmail(self, sender, to, raw):
                sent["raw"] = raw

        monkeypatch.setattr(smtp_settings.smtplib, "SMTP", FakeServer)
        monkeypatch.setattr(smtp_settings, "resolve", lambda: {
            "host": "smtp.example", "port": 587, "user": "u@example.id",
            "password": "p", "sender": "ScanGrade <u@example.id>",
            "reply_to": "noreply@scangrade.web.id", "source": "environment",
            "configured": True})

        ok, error = smtp_settings.send("someone@school.id", "Subject", "<p>Hai</p>",
                                       html=True, text="Hai")
        assert ok is True, error
        msg = email.message_from_string(sent["raw"])
        assert msg.get("Reply-To") == "noreply@scangrade.web.id"
        parts = [part.get_content_type() for part in msg.walk()]
        assert "text/plain" in parts and "text/html" in parts, (
            "a mail client that refuses HTML would show an empty message")
        plain = [p for p in msg.walk() if p.get_content_type() == "text/plain"][0]
        assert "Hai" in plain.get_payload(decode=True).decode("utf-8")

    def test_a_plain_only_send_is_unchanged(self, monkeypatch):
        """The old single-part call is still one part — nothing else regressed."""
        from app.services import smtp_settings

        sent = {}

        class FakeServer:
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
                sent["raw"] = raw

        monkeypatch.setattr(smtp_settings.smtplib, "SMTP", FakeServer)
        monkeypatch.setattr(smtp_settings, "resolve", lambda: {
            "host": "smtp.example", "port": 587, "user": "u@example.id",
            "password": "p", "sender": "u@example.id", "reply_to": "n@b.id",
            "source": "environment", "configured": True})
        ok, _ = smtp_settings.send("someone@school.id", "Subject", "plain body")
        assert ok is True
        msg = email.message_from_string(sent["raw"])
        assert msg.get_content_type() == "text/plain"


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
