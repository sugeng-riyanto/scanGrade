"""What the outbound mailbox actually did, so a broken one is seen before a pupil is.

The settings page used to answer "can this box send mail?" with
``bool(user and password)`` — which is *configured*, not *working*. A box holding the
wrong app password reported "Email is ready to send" and then failed every reset with
the only trace a ``logger.warning`` in a journal that needs a shell to read; the first
person to find out was the user who could not get back into their account. Measured on
the deployment this was written for, precisely that: a valid-looking credential, a relay
saying 535, and nobody able to see it.

So the sender writes down what happened, and two pages read it. Three design decisions:

* **It lives in ``system_settings``, the same row space the credential itself uses.**
  One key, service-role only (migration 026) — no migration to apply, nothing a page can
  render a secret from, and it survives a restart and a redeploy. The value is one JSON
  document, so the whole ledger is a single read and a single write.
* **It is recorded by `smtp_settings.send` rather than at the four call sites.** That is
  the one function every mail path already goes through — the reset code, the school
  activation, the super-admin reset, the payment receipt, the test send — so this covers
  *every* attempt, including one added later. A report assembled at call sites is a
  report of the call sites somebody remembered.
* **It can never break a mail.** Every function here swallows its own failures: a store
  that cannot be reached yields ``STATE_UNKNOWN``, and a send whose ledger write fails
  still returns what the relay said. A reporting feature that turns a working reset into
  an error page is worse than no reporting.

What is kept, and what is deliberately not: the outcome, the recipient, the subject, the
relay's own words (truncated) and the time. **Never the body and never a code** — a reset
code in a settings row is a credential in a place nobody audits — so the record is built
from an allow-list of keys and `tests/unit/test_mail_ledger.py` asserts what was
*persisted* is that allow-list rather than trusting the construction.

``since`` is the honest answer to "how long": the timestamp of the first failure in the
current run, kept across further failures and cleared by the first success. Without it a
mail path broken since breakfast reads exactly like one that broke a minute ago.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: The `system_settings` key this module owns. Deliberately not in
#: `smtp_settings.ALLOWED_KEYS`: that tuple is the credential set the settings page may
#: write, and this is a record, not a setting.
TABLE = "system_settings"
KEY = "mail_delivery"

#: The outcome of one attempt. `skipped` is its own state, not a flavour of `failed`:
#: "there is no credential at all" and "the relay said 535" have different fixes, and an
#: operator who reads one as the other changes the wrong thing.
STATE_SENT = "sent"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"
STATE_UNKNOWN = "unknown"
STATES = (STATE_SENT, STATE_FAILED, STATE_SKIPPED)

#: How many attempts are kept. Bounded because this is a record an operator reads, not a
#: mail log: a week of successes would push the one failure off the page.
KEEP = 12

#: Truncated because a relay's refusal can carry a whole TLS transcript, and it would be
#: stored on every failure and rendered into every visit.
DETAIL_MAX = 200
FIELD_MAX = 200

#: Bumped when the stored shape changes, so a reader can tell an old document from a
#: new one instead of guessing from missing keys.
VERSION = 1


def get_supabase():
    """The service client, resolved lazily so a request-less context still works."""
    from app.utils.auth import get_supabase as _get_supabase
    return _get_supabase()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(value, limit: int) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _entry(state: str, to: str, subject: str, detail: str) -> dict:
    """One attempt, from an allow-list of keys — see the module docstring.

    Built here rather than assembled at the call site so there is exactly one place that
    decides what a record may contain, and it is the place the allow-list assertion in
    the suite reads.
    """
    return {
        "at": _now(),
        "state": state if state in STATES else STATE_UNKNOWN,
        "to": _clip(to, FIELD_MAX),
        "subject": _clip(subject, FIELD_MAX),
        "detail": _clip(detail, DETAIL_MAX),
    }


def _read() -> dict:
    """The stored document, or ``{}``. Never raises: absence is a valid state."""
    try:
        rows = (get_supabase().table(TABLE).select("key, value").eq("key", KEY)
                .execute().data or [])
    except Exception as exc:  # noqa: BLE001 — the caller must not be a page's 500
        logger.debug("mail ledger unreadable: %s", exc)
        return {}
    for row in rows:
        if row.get("key") != KEY:
            continue
        try:
            data = json.loads(row.get("value") or "{}")
        except (TypeError, ValueError) as exc:
            logger.debug("mail ledger holds something that is not JSON: %s", exc)
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _write(data: dict) -> None:
    try:
        get_supabase().table(TABLE).upsert(
            {"key": KEY, "value": json.dumps(data, sort_keys=True)},
            on_conflict="key",
        ).execute()
    except Exception as exc:  # noqa: BLE001 — see "can never break a mail"
        logger.debug("mail ledger not written: %s", exc)


def record(*, state: str, to: str, subject: str, detail: str = "") -> None:
    """Write down one attempt. Best-effort, and deliberately without a return value.

    No return value on purpose: there is nothing a caller could do with a failure that
    would be better than carrying on, and a boolean would invite a caller to try.
    """
    try:
        entry = _entry(state, to, subject, detail)
        previous = _read()
        if entry["state"] == STATE_SENT:
            # A success ends the run: the alarm is about *now*, not about history.
            since = None
        else:
            # Kept across further failures, so "since 09:12" stays 09:12 through the
            # fourth attempt instead of moving to the time of the last one.
            since = previous.get("since") or entry["at"]
        recent = [entry] + [row for row in (previous.get("recent") or [])
                            if isinstance(row, dict)]
        _write({"version": VERSION, "last": entry, "recent": recent[:KEEP],
                "since": since})
    except Exception as exc:  # noqa: BLE001 — the mail path owns this function's failure
        logger.debug("mail ledger did not record an attempt: %s", exc)


def snapshot() -> dict:
    """The ledger, shaped for a page. Never raises; an unreadable store is `unknown`.

    ``broken`` is the one boolean the pages branch on: the most recent attempt did not
    go out. It is not "something failed at some point" — a mail path that failed once
    this morning and has worked since is not broken, and reporting it as broken is how
    an alarm stops being read.
    """
    data = _read()
    last = data.get("last") if isinstance(data.get("last"), dict) else None
    recent = [row for row in (data.get("recent") or []) if isinstance(row, dict)]
    state = last["state"] if last and last.get("state") in STATES else STATE_UNKNOWN
    since = data.get("since") or None

    failures = 0
    if since:
        for row in recent:
            # Newest-first, and every stamp is the same fixed-width UTC format, so a
            # string comparison is an ordering.
            if str(row.get("at") or "") >= since and row.get("state") != STATE_SENT:
                failures += 1

    return {
        "state": state,
        "since": since,
        "broken": state in (STATE_FAILED, STATE_SKIPPED),
        "attempts": len(recent),
        "failures": failures,
        "last": last,
        "recent": recent,
    }
