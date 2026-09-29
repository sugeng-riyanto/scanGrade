"""What a person is told when an action did not go through.

Every admin page ends its write in the same shape — try it, flash the failure — and
until now the failure *was* the exception: the caught error interpolated straight into
the message, so what the operator read was whatever the library, the driver or a
traceback happened to say. That is where ``Gagal: Server disconnected`` came from on
``/admin-sekolah/profile``. That string is ``httpx``'s, it names something the operator
cannot act on, and what had actually happened was that the *reply* was lost — the row
may well have been written.

The tone of a refusal already lives in one place (:mod:`app.utils.denials`). This is the
same rule for a failure, and the classes are not treated alike on purpose:

* **an error this app raised for a person** — :class:`~app.errors.ScanGradeException`,
  :class:`~app.services.school_officials.OfficialError`, anything carrying
  ``user_message`` — is shown in its own words. That sentence exists because the
  technical one is wrong for a reader, and asking every call site to remember
  ``getattr(e, "user_message", str(e))`` is how four of thirty-four sites remembered it;
* **a lost reply** (:class:`httpx.TransportError`) says the outcome is not known and
  what to do about it. A dropped connection does not say whether the statement ran, and
  re-sending an insert is how one row becomes two — so the reader is told to look
  before repeating, which is the one instruction that is right either way;
* **a server answer** (:class:`postgrest.exceptions.APIError`) becomes a sentence plus
  the code, because the JSON body is a developer's and the code is the thing that names
  the refusal to whoever supports the school;
* **anything else** keeps the exception's own text, cut to one bounded line. That is
  deliberate rather than lazy: a sentence invented for a failure nobody anticipated
  would hide the only clue there is, and "we could not tell" is not the same answer as
  "it is fine".

Nothing here decides whether the write happened — that is ``supabase_retry``'s job, and
where it can settle a write this module never runs. Where it cannot, this is the
sentence the operator gets.
"""

from __future__ import annotations

import re

import httpx
from postgrest.exceptions import APIError

#: What a reader is told when the reply to their action was lost. Three things are
#: load-bearing: the outcome is *unknown* (not "failed"), the data is worth looking at,
#: and repeating the action is the last step rather than the first.
LOST_REPLY = ("Balasan dari server terputus, jadi belum bisa dipastikan perubahan ini "
              "tersimpan atau tidak. Muat ulang halaman dan periksa datanya sebelum "
              "mengulang.")

#: The prefix of a server refusal; the code is appended when PostgREST sent one.
SERVER_REFUSED = "Server menolak permintaan ini"

#: How much of an unrecognised failure a person is shown. Long enough for a real
#: sentence from a driver, short enough that a traceback cannot become the toast.
UNKNOWN_LIMIT = 200

_WHITESPACE = re.compile(r"\s+")


def _one_line(text: str, limit: int = UNKNOWN_LIMIT) -> str:
    """``text`` as a single bounded line — what can be printed in a toast.

    Whitespace is collapsed rather than stripped, because the usual way a library
    message arrives is a template with a newline and an indent in it, and the sentence
    is still worth reading once the layout is gone. A cut says so: silently showing the
    first 200 characters of a longer message is how half a sentence reads as a whole one.
    """
    flat = _WHITESPACE.sub(" ", str(text)).strip()
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "…"


def _postgrest_code(exc: APIError) -> str:
    """The code PostgREST answered with, when it sent one.

    Read off the exception rather than out of its text: ``str(APIError)`` is the raw
    JSON body, and parsing the body back out of a string to find a field the exception
    already carries would be a second reading that can only disagree with the first.
    """
    code = getattr(exc, "code", None)
    return code.strip() if isinstance(code, str) and code.strip() else ""


def sentence(exc: BaseException) -> str:
    """The one line a person is shown for ``exc``."""
    own_words = getattr(exc, "user_message", None)
    if isinstance(own_words, str) and own_words.strip():
        return _one_line(own_words)
    if isinstance(exc, httpx.TransportError):
        return LOST_REPLY
    if isinstance(exc, APIError):
        code = _postgrest_code(exc)
        return f"{SERVER_REFUSED} ({code})." if code else f"{SERVER_REFUSED}."
    return _one_line(str(exc))
