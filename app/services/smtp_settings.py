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

logger = logging.getLogger(__name__)

#: The keys this module owns in ``system_settings``. Anything else is not SMTP.
ALLOWED_KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from")


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
    password = stored_password or _env("SMTP_PASSWORD", "")
    sender = (store.get("smtp_from") or "").strip() or _env("SMTP_FROM", "") or user

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "sender": sender,
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


def send(to_email: str, subject: str, body: str, html: bool = False):
    """Send one message through the resolved mailbox. Returns ``(ok, error)``.

    A tuple rather than a bare bool because "it failed" and "it failed because the
    relay said 535" are different facts, and the settings page has to be able to
    show the second one.
    """
    settings = resolve()
    if not settings["configured"]:
        logger.warning("SMTP not configured — email to %s skipped", to_email)
        return False, "not configured"

    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings["sender"]
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
        return False, str(exc)
    logger.info("Email sent to %s (%s)", to_email, subject)
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
