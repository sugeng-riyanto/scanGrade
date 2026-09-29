"""The rule a forced password change is held to, and the defaults it exists to catch.

Why this is a module rather than four `if`s in the route
--------------------------------------------------------
A school prints a login card, the pupil signs in with what is on it, and the app then
refuses every other page until the password is replaced — that is the whole point of
`must_change_password`. The rule therefore runs on the one page a locked-out user can
reach, for four roles, and a mistake in it is a user who cannot get back in at all.
Keeping it pure means the rule can be checked against every shape of input without an
HTTP request, a session, or Supabase.

The default passwords are not hypothetical
------------------------------------------
``siswa123`` is the password ``app/services/student_import.py`` writes when an import
sheet leaves the password column empty, and ``guru123`` is the same for teachers. A
pupil who \"changes\" their card password to ``siswa123`` has not changed it — it is
the value the whole school already knows, and on a shared or leaked import sheet it is
the *most* guessable of all. So the well-known defaults are refused by name, and the
list lives here rather than only in the length rule.

What is deliberately **not** checked
------------------------------------
No composition rules (an uppercase, a digit, a symbol). The passwords this app issues
are 12 random characters, and a rule that demands three character classes from a
fourteen-year-old produces ``Password1!`` — measured advice, not taste: length is the
only requirement that survives being memorised. The floor is 8, one below the 12 this
app already generates so a school's own policy can be stricter without the app
fighting it.

One refusal at a time, in a fixed order
---------------------------------------
The caller shows a single sentence, so the order decides *which* sentence. It is
ordered by what the person can act on fastest: a missing field, then the confirmation
that does not match (a typo they can see), then \"that is the password you already
have\", then the blocklist, then length. Returning a list would push the ordering into
the template, where it would drift from this one.

The blocklist sits **before** the length rule on purpose: ``guru123`` — one of the
defaults this app hands out — is seven characters, so a length-first rule would answer
\"too short\" for the very value it exists to name. Every entry here gets the sentence
that says what it actually is.
"""

from __future__ import annotations

#: The floor, one below the 12 characters this app generates.
MIN_LENGTH = 8

#: Defaults this codebase itself hands out, plus the usual suspects. Lowercased on
#: comparison, because `Siswa123` is the same secret as `siswa123`.
KNOWN_DEFAULTS = frozenset({
    # written by this app when an import sheet leaves the password blank
    "siswa123",
    "guru123",
    # written by other places in this app for demo and seed accounts
    "password",
    "password123",
    "admin123",
    "scangrade",
    "scan-grade",
    # the classic keyboard walks
    "12345678",
    "123456789",
    "qwerty123",
    "iloveyou",
    "letmein1",
})

def account_fields(email: str) -> dict:
    """The profile fields every account created with an *issued* password carries.

    Written once because three creators need it (student, teacher, official) and
    forgetting one fails silently: the account simply never asks its owner to
    replace the password, which is indistinguishable from the feature working.

    ``profiles.email`` is the mirror described in migration 040 — a derived copy, so
    a school's own sheet can be built in one query instead of paging
    ``auth.admin.list_users()`` at 50 accounts a page. Login never reads it.
    """
    return {
        "email": (email or "").strip().lower() or None,
        "must_change_password": True,
    }


#: Every key :func:`change_problem` can answer with, declared as one tuple.
#:
#: Declared rather than derived, for the reason the rule lives here at all: the answer
#: is a *key*, so nothing else in the tree ever calls ``auth_error("change_weak")`` and
#: the catalogue's own guard — which reads those calls — cannot see these. Naming the
#: whole set here is what lets it check that the catalogue carries one message per
#: answer (a key the catalogue does not hold renders as *nothing*, in both languages),
#: and it is what "one refusal at a time, in a fixed order" is a permutation of.
REFUSALS: tuple[str, ...] = (
    "all_required",
    "password_mismatch",
    "change_same",
    "change_weak",
    "change_short",
)


#: ``None`` means the change may proceed. Anything else is a key in
#: ``app/utils/auth_messages.py``, so the page shows both languages.
def change_problem(current: str, new: str, confirm: str) -> str | None:
    """The first reason this change cannot be made, or ``None``.

    Keys, not sentences: the language lives in the browser, so the words belong to
    the catalogue every other auth message comes from.
    """
    current = (current or "").strip()
    new = (new or "").strip()
    confirm = (confirm or "").strip()

    if not current or not new or not confirm:
        return "all_required"
    if new != confirm:
        return "password_mismatch"
    if new == current:
        return "change_same"
    # Before length: some of the defaults this app issues (`guru123`) are shorter than
    # the floor, and \"too short\" would be the wrong sentence for a value the school
    # has already printed on a card.
    if new.lower() in KNOWN_DEFAULTS:
        return "change_weak"
    if len(new) < MIN_LENGTH:
        return "change_short"
    return None
