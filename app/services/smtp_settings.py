"""One mailbox, settable from the admin panel, never from a shell.

The password used to live only in ``EnvironmentFile=/opt/scangrade/.env``, which
the installer fills with ``SMTP_PASSWORD=`` — meaning a box could be "deployed"
and still unable to send a single reset email, with the only remedy being root on
the host. The app password in the checkout's own ``.env`` was rejected by Google
(535 BadCredentials), so that remedy did not even work from a developer machine.

So the credential now has a home the application itself can write: the
``system_settings`` key/value table (service-role only, migration 026), read here as
the *first* source and the environment as the fallback. That keeps one rule in one
place — the sender, the reset route, the notification service and the midtrans mail
all resolve through :func:`resolve` — instead of four copies of the same four
``config.get`` calls, which is how they drifted apart before.

The password is **write-only**: :func:`save` ignores a blank one (so leaving the
field empty keeps the stored secret) and :func:`load` is never handed to a
template. The settings page may say whether one is set; it must never render it.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from app import config as app_config
from app.services import mail_ledger

logger = logging.getLogger(__name__)

#: The keys this module owns in ``system_settings``. Anything else is not SMTP.
ALLOWED_KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_password",
                "smtp_from", "smtp_reply_to")

#: The name this app's mail wears when the operator leaves ``smtp_from`` blank.
#: A bare Gmail address in the ``From`` header is the shape a school's mail server
#: reads as bulk mail; the display name is the difference between "ScanGrade" and a
#: string of letters in an inbox.
DEFAULT_SENDER_NAME = "ScanGrade"

#: Where a reply goes. A reset code is a one-way message, and the sending mailbox
#: (``scangrade9@gmail.com``) is not watched: letting ``Reply-To`` default to it
#: invites a reply that nobody reads. An operator can override this on the settings
#: page, and ``SMTP_REPLY_TO`` is the environment fallback.
DEFAULT_REPLY_TO = "noreply@scangrade.web.id"


def _with_display_name(addr: str) -> str:
    """Give a bare address the display name this app's mail wears.

    A value the operator wrote with a name of its own — ``ScanGrade <a@b>`` — is left
    exactly as written, so the panel stays the place the identity is decided; only a
    naked address is wrapped, and only once.
    """
    addr = (addr or "").strip()
    if not addr or "<" in addr:
        return addr
    return f"{DEFAULT_SENDER_NAME} <{addr}>"


def get_supabase():
    """The service client, resolved lazily so a request-less context still works."""
    from app.utils.auth import get_supabase as _get_supabase
    return _get_supabase()


def load() -> dict:
    """The stored SMTP keys, or ``{}`` when none are set or the table is missing.

    Deliberately uncached. A 60-second memo looked free and was not: it made a
    credential saved in the panel invisible to the very next send, and it let a
    value read once outlive the configuration it described — the deploy-alert
    fallback kept reporting the account from before a config change. This is a
    single-row read on the mail path and on the settings page, neither of which is
    a hot render, so correctness is worth more than the round-trips.
    """
    store: dict = {}
    try:
        rows = (
            get_supabase()
            .table("system_settings")
            .select("key, value")
            .in_("key", list(ALLOWED_KEYS))
            .execute()
            .data
            or []
        )
        for row in rows:
            store[row["key"]] = row["value"]
    except Exception as exc:  # noqa: BLE001 — absence is a valid state, not a crash
        logger.warning("could not read stored SMTP settings: %s", exc)
    return store


def _env(name: str, default=""):
    """The configured value for ``name``, from the app config then the environment."""
    try:
        from flask import current_app
        value = current_app.config.get(name)
        if value not in (None, ""):
            return value
    except Exception:
        pass
    import os
    return os.getenv(name, default)


def resolve() -> dict:
    """The mailbox to send from: stored values first, environment as fallback.

    ``source`` names where the *password* came from, because that is the value this
    whole change exists to make settable, and it is the one an operator needs to
    know the provenance of. ``configured`` is the only honest answer to "can this
    box send mail at all".
    """
    store = load()
    stored_password = (store.get("smtp_password") or "").strip()

    host = (store.get("smtp_host") or "").strip() or _env("SMTP_HOST", "smtp.gmail.com")
    raw_port = (store.get("smtp_port") or "").strip() or _env("SMTP_PORT", 465)
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = 465
    user = (store.get("smtp_user") or "").strip() or _env("SMTP_EMAIL", "")
    # `_env` answers from the app config first, which is the alias-aware value —
    # but it can be the value as it was at *import*, and a `.env` edited since then
    # is exactly the case here. So the environment is asked last, and it is asked
    # under every name the password may carry (see `config.env_password`); a box
    # with the right secret under Gmail's own spelling must not report itself
    # unable to send mail.
    password = (stored_password or _env("SMTP_PASSWORD", "")
                or app_config.env_password())
    sender = _with_display_name(
        (store.get("smtp_from") or "").strip() or _env("SMTP_FROM", "") or user)
    reply_to = (
        (store.get("smtp_reply_to") or "").strip()
        or _env("SMTP_REPLY_TO", "")
        or DEFAULT_REPLY_TO
    )

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "sender": sender,
        "reply_to": reply_to,
        "source": "database" if stored_password else "environment",
        "configured": bool(user and password),
    }


def save(data: dict) -> list:
    """Store the SMTP keys present in ``data``; return the keys actually written.

    A blank ``smtp_password`` is skipped rather than stored, because the field is
    write-only and an empty submit must mean "keep the one you have" — not "erase
    the credential", which is exactly the failure that would be invisible until the
    next reset email silently failed.
    """
    client = get_supabase()
    saved: list = []
    for key in ALLOWED_KEYS:
        if key not in data:
            continue
        value = data.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if key == "smtp_password" and not value:
            continue
        client.table("system_settings").upsert(
            {"key": key, "value": value}, on_conflict="key"
        ).execute()
        saved.append(key)
    return saved


def _message_id_domain(sender: str) -> str:
    """The domain a `Message-ID` should be minted under: the sender's own.

    An id whose domain disagrees with `From` is one of the small inconsistencies a
    filter scores, and a placeholder (`localhost`, an empty domain) is worse than
    none at all.
    """
    address = (sender or "").rsplit("<", 1)[-1].strip("> ").strip()
    return address.split("@", 1)[1] if "@" in address else "scangrade.web.id"


def send(to_email: str, subject: str, body: str, html: bool = False,
         text: str | None = None, important: bool = False):
    """Send one message through the resolved mailbox. Returns ``(ok, error)``.

    A tuple rather than a bare bool because "it failed" and "it failed because the
    relay said 535" are different facts, and the settings page has to be able to
    show the second one.

    ``text`` is the plain-text alternative for an HTML ``body``, and it is not
    decoration: a client that refuses HTML must still be able to read a reset code,
    and a one-part HTML mail is the shape spam filters score highest. The two parts
    travel in one ``multipart/alternative``, plain first — the order is the client's
    preference hint, and the plain half is what a text-only reader is shown.

    The **headers below the body** are what decide whether any of it is read. A
    message `smtplib` builds by hand arrives with no `Date` and no `Message-ID` and
    with nothing saying it is machine-generated, and those absences are among the
    first things a filter scores — invisible in every rendered preview, which is why
    they were missing. `Auto-Submitted`/`X-Auto-Response-Suppress` are RFC 3834's way
    of saying "transactional, do not auto-answer", `important` marks a one-time
    credential as urgent, and `Precedence: bulk` is deliberately **never** set: it is
    the header that routes a transactional message to the bulk tab.
    """
    settings = resolve()
    if not settings["configured"]:
        logger.warning("SMTP not configured — email to %s skipped", to_email)
        # Written down, not only logged: this is the case a locked-out user discovers
        # first, and a warning in a journal needs a shell to read. See mail_ledger.
        mail_ledger.record(state=mail_ledger.STATE_SKIPPED, to=to_email,
                           subject=subject, detail="no SMTP credential is set")
        return False, "not configured"

    if html:
        msg = MIMEMultipart("alternative")
        if text:
            msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings["sender"]
    # A message with no date cannot be aged (nor shown with a sent time), and one
    # with no id looks replayed. Both are minted here rather than left to the relay.
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=_message_id_domain(settings["sender"]))
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    if important:
        msg["Importance"] = "high"
        msg["X-Priority"] = "1 (Highest)"
    # A reset code is one-way: the sending mailbox is not watched, so a reply must not
    # land in it. Set unconditionally, because a null header is what makes a mail
    # client answer the address in `From`.
    msg["Reply-To"] = settings["reply_to"]
    msg["To"] = to_email

    try:
        if settings["port"] == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings["host"], settings["port"], context=context) as server:
                server.login(settings["user"], settings["password"])
                server.sendmail(settings["user"], [to_email], msg.as_string())
        else:
            with smtplib.SMTP(settings["host"], settings["port"]) as server:
                server.starttls()
                server.login(settings["user"], settings["password"])
                server.sendmail(settings["user"], [to_email], msg.as_string())
    except Exception as exc:  # noqa: BLE001 — reported to the operator, not raised
        logger.error("SMTP send failed to %s: %s", to_email, exc)
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to=to_email,
                           subject=subject, detail=str(exc))
        return False, str(exc)
    logger.info("Email sent to %s (%s)", to_email, subject)
    # The success is recorded too, and it is not bookkeeping: it is what clears the
    # alarm on the status pages. A ledger of failures alone can only ever say "broken"
    # once and never "fixed", so an operator who has just proved the path works would
    # still be looking at a red card.
    mail_ledger.record(state=mail_ledger.STATE_SENT, to=to_email, subject=subject)
    return True, None


def send_test(to_email: str) -> dict:
    """Send a test message and name the outcome precisely.

    Four outcomes, because "no" is not one thing: there is nowhere to send, the box
    has no credential, the relay refused, or it went out. The page maps each to its
    own sentence so a green result cannot be mistaken for an optimistic one.
    """
    if not to_email:
        return {"outcome": "no_recipient", "detail": None}
    ok, error = send(
        to_email,
        "ScanGrade SMTP test",
        "This is a test from ScanGrade's email settings. If you can read this, "
        "password resets and approval emails will reach your inbox.",
    )
    if ok:
        return {"outcome": "sent", "detail": None}
    if not resolve()["configured"]:
        return {"outcome": "not_configured", "detail": error}
    return {"outcome": "send_failed", "detail": error}
