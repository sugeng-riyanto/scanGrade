"""Retrying the one GoTrue answer that means "the server hiccupped", not "no".

Reported live from ``/admin-sekolah/teachers``::

    Gagal: Database error creating new user

That is GoTrue's own wording, and it is its **generic** database answer: the
insert into ``auth.users`` raised and was rolled back. It says nothing about the
teacher being added and nothing about the form — a probe against the live project
created and deleted an account through the very same route moments later, and all
three shapes of ``create_user`` landed. What the operator was shown, then, was a
*transient* failure dressed up as a permanent one: the school tries again and it
works, but the message told them their data was wrong.

Why repeating a create is safe
------------------------------
The asymmetry :mod:`app.utils.supabase_retry` reasons about — a lost reply does
not say whether the statement ran, so an insert must never be re-sent — does
**not** apply here, and the difference is worth stating rather than assumed.
GoTrue answers ``Database error creating new user`` *after* its insert raised, so
the transaction rolled back: unlike a lost TCP reply, this is a statement that
did not run. A retry therefore cannot turn one teacher into two. (If an attempt
had actually landed, the next one would not see the database error at all — it
would see "already registered", which this module deliberately does not retry.)

What the reader is left with
----------------------------
An exhausted retry is not reported in GoTrue's words. The operator is told the
account was not created and that repeating is the answer — the two facts the
generic wording leaves them to guess — while the original text is kept in the
exception's message, because a sentence invented for a failure nobody anticipated
is how the only clue disappears.
"""

from __future__ import annotations

import time
from typing import Callable

from app.errors import ScanGradeException
from app.utils import auth_health

#: How many times a create is attempted in total before the operator is told.
#: Small on purpose: this is a hiccup, not an outage, and a school admin is
#: waiting in front of the page.
ATTEMPTS = 3

#: The pause before each retry, in seconds. Rising, so three attempts are not
#: three requests fired at a server that is already struggling.
BACKOFF = (0.5, 1.5)

#: GoTrue's generic database answers, for signup and for admin create. Matched on
#: the text rather than the exception class because that is all GoTrue carries
#: through every client version this app has seen, and because a caller may have
#: wrapped it.
TRANSIENT_MESSAGES = (
    "database error creating new user",
    "database error saving new user",
)

NOT_MADE = (
    "Server akun sedang tidak dapat menyimpan pengguna baru, jadi akun ini belum "
    "dibuat. Coba lagi sebentar lagi."
)


def is_transient(exc: BaseException) -> bool:
    """Whether ``exc`` is the server hiccupping rather than refusing the data."""
    text = str(exc).lower()
    return any(message in text for message in TRANSIENT_MESSAGES)


class AccountNotCreated(ScanGradeException):
    """The create never landed, and repeating it may well work."""

    def __init__(self, original: BaseException):
        super().__init__(
            f"account create failed after {ATTEMPTS} attempts: {original}",
            error_code="AUTH_CREATE_TRANSIENT",
            user_message=NOT_MADE,
            details={"attempts": ATTEMPTS, "original": str(original)},
        )


def create_user_with_retry(attempt: Callable[[], object], *,
                           attempts: int = ATTEMPTS,
                           backoff: tuple[float, ...] = BACKOFF,
                           sleep: Callable[[float], None] = time.sleep):
    """Run ``attempt`` until it succeeds or the server stops hiccupping.

    ``attempt`` is a callable rather than an arguments dict so that each creator
    keeps its own ``create_user`` call, in its own ``try``, under the rollback
    that sweeps a half-made account. Moving the call in here would hide it from
    ``tests/unit/test_account_creation_orphans.py``, which walks the source and
    requires every site to be covered.
    """
    last: BaseException | None = None
    for index in range(attempts):
        try:
            result = attempt()
        except Exception as exc:  # noqa: BLE001 -- re-raised below
            if not is_transient(exc):
                raise
            last = exc
            if index + 1 < attempts:
                # Recorded the moment the hiccup is seen, before the retry: a retry
                # that then succeeds is exactly the signal a status page needs — the
                # operator sees only the success, and the box stays on record.
                auth_health.record_retry(str(exc))
                pause = backoff[index] if index < len(backoff) else backoff[-1]
                sleep(pause)
            continue
        if index == 0:
            # A create that landed on the first try is the one outcome that heals a
            # marker another worker left, so a recovered box stops being amber.
            auth_health.record_clean()
        return result
    auth_health.record_exhausted(str(last))
    raise AccountNotCreated(last)
