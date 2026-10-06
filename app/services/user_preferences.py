"""The choices that should follow a student to the next device, kept on the profile.

Theme, language and the alert level were held in `localStorage`, which is per
browser. The same student at the library laptop, then the lab machine, then their
phone met the defaults every time, so a choice made during one paper was gone by
the next — and the strip that offers the switches had no memory of its own.

The profile is the one record every device shares, and the session already reads
it once per login (`app/utils/auth.py::_fetch_session`), so the choice rides along
at **no extra round-trip**. Only a *change* costs a write, which is rare.

Storage shape: one JSON object on `profiles.preferences`, so a later preference
does not need a column of its own.

    {"theme": "dark", "lang": "en", "alert_volume": 0.5, "alert_muted": false,
     "text_scale": 1.15}

Two rules, and both exist because the client is not trusted:

* only the keys the app defines are kept — a request cannot write an unrelated key
  into the column;
* each value is checked against its own domain (a theme is `dark`/`light`, a
  language is `id`/`en`, the volume is a number clamped to `[0, 1]`, the mute is a
  real boolean, the text scale is one of the steps the A-/A+ control can reach) —
  a string is never accepted where a boolean belongs, so `"false"` cannot read as
  `True`.

`text_scale` is the one preference with two reasons to exist: the pupil exam page's
"Perbesar/Perkecil Teks" control *and* the general low-vision size the accessibility
work asked for. They are one mechanism rather than two, which is why the value is a
closed set of steps and not a free number — the control is a pair of buttons, so a
value between two steps could not be reached by pressing either one.

`normalize()` is used for both a client patch and the value read back, so a column
that somehow holds an older shape is cleaned on the way out rather than trusted.
"""
from __future__ import annotations

from app.utils.helpers import row_or_none

THEME = "theme"
LANG = "lang"
ALERT_VOLUME = "alert_volume"
ALERT_MUTED = "alert_muted"
TEXT_SCALE = "text_scale"

#: The whole vocabulary. A key outside this set is dropped rather than stored.
KEYS = (THEME, LANG, ALERT_VOLUME, ALERT_MUTED, TEXT_SCALE)

_THEMES = {"dark", "light"}
_LANGS = {"id", "en"}

#: The steps the pupil exam page's A-/A+ control moves through. `app/static/js/exam-view.js`
#: carries the same list and `tests/unit/test_exam_view_zoom.py` pins the two together,
#: so a value the server accepts is always a value one of the buttons can reach.
TEXT_SCALES = (0.85, 1.0, 1.15, 1.3, 1.5)


def _clean_volume(value):
    """A real number in ``[0, 1]``, or ``None`` for anything else.

    ``bool`` is excluded on purpose: it is a subclass of ``int`` in Python, so
    ``True`` would otherwise be stored as a volume of ``1.0``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number:  # NaN
        return None
    return max(0.0, min(1.0, number))


def _clean_text_scale(value):
    """One of the steps, or ``None``.

    A near-miss is *not* rounded into a step: the control could not have produced
    it, so accepting it would leave the page showing a scale neither button can
    reach and the next press jumping somewhere unexpected. ``bool`` is excluded for
    the same reason as the volume — ``True`` is an ``int`` in Python.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number:  # NaN
        return None
    for step in TEXT_SCALES:
        if abs(number - step) < 1e-9:
            return step
    return None


def normalize(patch) -> dict:
    """Only the keys and values this app defines, from whatever it was handed.

    Accepts a client patch or a stored object; a non-dict (including ``None``) is
    an empty preference set rather than an error, because a page with no
    preferences is a normal state and not a failure.
    """
    if not isinstance(patch, dict):
        return {}

    out = {}
    if patch.get(THEME) in _THEMES:
        out[THEME] = patch[THEME]
    if patch.get(LANG) in _LANGS:
        out[LANG] = patch[LANG]

    volume = _clean_volume(patch.get(ALERT_VOLUME))
    if volume is not None:
        out[ALERT_VOLUME] = volume

    muted = patch.get(ALERT_MUTED)
    if isinstance(muted, bool):
        out[ALERT_MUTED] = muted

    scale = _clean_text_scale(patch.get(TEXT_SCALE))
    if scale is not None:
        out[TEXT_SCALE] = scale

    return out


def load(supabase, user_id) -> dict:
    """What is stored for this user, or ``{}``.

    Best-effort: a read that fails is an empty preference set, never an error a
    page has to handle — nothing here is important enough to refuse a render.
    """
    try:
        row = row_or_none(
            supabase.table("profiles")
            .select("preferences")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception:
        return {}
    return normalize((row or {}).get("preferences") or {})


def save(supabase, user_id, patch) -> dict:
    """Merge ``patch`` into the stored set and return the merged object.

    Read-modify-write rather than a blind overwrite: the toggles arrive one key at
    a time (the theme from one button, the language from another), so a write that
    replaced the whole object would erase the choice made a moment earlier.

    The write is confirmed by the **row**, not by the reply. This box raises
    `RemoteProtocolError: Server disconnected` often enough that the repo documents
    it as normal, and it is raised on the reply: the `UPDATE` has already
    committed. Believing that error would answer 500 for a preference the profile
    already holds — a choice the next device can see, reported to the device that
    made it as a failure. So a failed update is followed by one read: if the row now holds
    the patch, the write landed and its value is returned; if it does not, the
    failure is real and is raised, because a write that never landed must not be
    reported as a successful one either.
    """
    clean = normalize(patch)
    if not clean:
        return load(supabase, user_id)

    merged = load(supabase, user_id)
    merged.update(clean)
    try:
        supabase.table("profiles").update({"preferences": merged}).eq("id", user_id).execute()
        return merged
    except Exception:
        confirmed = load(supabase, user_id)
        if all(confirmed.get(key) == value for key, value in clean.items()):
            return confirmed
        raise
