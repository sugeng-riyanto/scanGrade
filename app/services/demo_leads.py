"""Where a demo request goes: the inbox that reads it, and the WhatsApp that answers it.

The form on the landing page used to reach nobody. `POST /api/demo-request` wrote an
`audit_logs` row whose `entity_type` was `lead` and answered ``{"success": true}``, so
a school that asked for a demo saw a green tick and the team saw it in an audit
table only if somebody thought to look — which is not a notification. Everything this
module decides is therefore about *who is told*:

* **the inbox** is the mailbox the team actually reads, and it is *not* invented here.
  Stored `demo_request_email` wins (so it can be pointed elsewhere without a deploy),
  then the SMTP account itself — which is the address the deploy alerts already go to,
  and the one box on this deployment that is demonstrably watched — and only then the
  published `reply_to`, which is a ``noreply`` address by default and is the wrong
  answer for a lead. The order is the point: a default that lands in an unread mailbox
  is the same defect as no email, one layer quieter.

* **the WhatsApp number** is the same one the registration page shows as its contact
  badge (super admin → *WhatsApp*), read from the same row, so one screen decides both.
  Nothing is hard-coded and no second number exists to keep in step.

* **the message** is built server-side and travels as the ``text`` of a ``wa.me``
  link, which is why it lives in Python rather than in JavaScript: the same words are
  then testable, and the two copies that would drift (a JS template for the link and a
  Python one for the answer) never exist. It is Indonesian, because the line it opens
  is ScanGrade's own Indonesian support number, with both-language field labels so an
  English-reading visitor can see what they are about to send.

The email is sent **before** the visitor is handed to WhatsApp, so a send that fails
is reported rather than swallowed: the request is still in `audit_logs`, and the
response says whether the team was told.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote

from app.services import email_bodies, smtp_settings
from app.utils.logger import get_logger

logger = get_logger("demo_leads")

#: The ``system_settings`` key an operator can point leads with. Absent is normal.
INBOX_KEY = "demo_request_email"

#: Where the number shown as the registration page's WhatsApp badge lives, and the
#: row the super admin's WhatsApp page writes.
WHATSAPP_TABLE = "school_settings"
WHATSAPP_ROW = 1


def _supabase():
    from app.utils.auth import get_supabase
    return get_supabase()


def _setting(key: str) -> str:
    """One ``system_settings`` value, or ``""`` when unset or unreadable.

    Absence is a valid state here, not an error: this runs on the landing page's
    form, where a 500 is a lost lead.
    """
    try:
        rows = (_supabase().table("system_settings").select("value")
                .eq("key", key).execute().data or [])
        return (rows[0].get("value") or "").strip() if rows else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read %s: %s", key, exc)
        return ""


def inbox() -> str:
    """The address a demo request is mailed to. See the module docstring for the order."""
    stored = _setting(INBOX_KEY)
    if stored:
        return stored
    resolved = smtp_settings.resolve()
    # The mailbox that sends is the mailbox the deploy alerts land in, so it is read;
    # `reply_to` is the last resort and is deliberately not preferred over it.
    return (resolved.get("user") or "").strip() or (resolved.get("reply_to") or "").strip()


def whatsapp_number() -> str:
    """ScanGrade's contact number, digits only, or ``""`` when none is set."""
    try:
        row = (_supabase().table(WHATSAPP_TABLE)
               .select("whatsapp_number").eq("id", WHATSAPP_ROW)
               .execute().data or [])
        raw = (row[0].get("whatsapp_number") or "") if row else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read the contact number: %s", exc)
        return ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    # A leading `0` is a national prefix; `wa.me` wants the country code, and an
    # Indonesian number typed the way people write it is the likeliest input here.
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    return digits


def message(school_name: str, email: str = "", phone: str = "") -> str:
    """The prefilled WhatsApp message. Indonesian first, labels in both languages.

    Escaping empties out: a school name arriving as ``"`` or ``&`` is percent-encoded
    by :func:`quote` when the link is built, and a name typed with a newline has that
    newline collapsed to a space — a name must not be able to add a line that reads
    like part of the message.
    """
    clean = lambda value: " ".join(str(value or "").split())  # noqa: E731
    lines = ["Halo ScanGrade! Saya ingin minta demo.",
             f"Sekolah / School: {clean(school_name)}"]
    if clean(email):
        lines.append(f"Email: {clean(email)}")
    if clean(phone):
        lines.append(f"WhatsApp: {clean(phone)}")
    lines.append("Dikirim dari scangrade.web.id / Sent from scangrade.web.id")
    return "\n".join(lines)


def whatsapp_url(school_name: str, email: str = "", phone: str = "") -> str:
    """The ``wa.me`` link for this request, or ``""`` when no number is configured.

    ``api.whatsapp.com/send`` rather than ``wa.me`` because the registration badge
    already uses that host, and a deployment that only allows one of the two should
    not depend on which page the visitor came from.
    """
    number = whatsapp_number()
    if not number:
        return ""
    return ("https://api.whatsapp.com/send?phone=" + number
            + "&text=" + quote(message(school_name, email, phone)))


def _stamp() -> str:
    """When it arrived, written the way the reader's clock reads it (WIB)."""
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d %b %Y %H:%M WIB")


def notify(school_name: str, email: str, phone: str = "", message_text: str = "") -> dict:
    """Mail one lead to the team. Returns what happened, never raises.

    Four outcomes, the same shape `smtp_settings.send_test` uses, because "it did not
    go" is not one thing: nothing to send to, no mail credential on the box, the relay
    refused, or it went. The caller answers the visitor either way — a school must be
    able to reach a human through WhatsApp even when the box cannot send mail.
    """
    to = inbox()
    if not to:
        logger.warning("no demo inbox resolved — the request is only in audit_logs")
        return {"sent": False, "to": "", "why": "no_inbox"}

    body = email_bodies.demo_request(school_name=school_name, email=email,
                                     phone=phone, message=message_text,
                                     submitted_at=_stamp())
    ok, error = smtp_settings.send(to, body["subject"], body["html"], html=True,
                                   text=body["text"], reply_to=email)
    if ok:
        return {"sent": True, "to": to, "why": None}
    if not smtp_settings.resolve().get("configured"):
        return {"sent": False, "to": to, "why": "not_configured"}
    return {"sent": False, "to": to, "why": "send_failed", "detail": error}
