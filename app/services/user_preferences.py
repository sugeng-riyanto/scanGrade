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

    {"theme": "dark", "lang": "en", "alert_volume": 0.5, "alert_muted": false}

Two rules, and both exist because the client is not trusted:

* only the keys the app defines are kept — a request cannot write an unrelated key
  into the column;
* each value is checked against its own domain (a theme is `dark`/`light`, a
  language is `id`/`en`, the volume is a number clamped to `[0, 1]`, the mute is a
  real boolean) — a string is never accepted where a boolean belongs, so `"false"`
  cannot read as `True`.

`normalize()` is used for both a client patch and the value read back, so a column
that somehow holds an older shape is cleaned on the way out rather than trusted.
"""
from __future__ import annotations

from app.utils.helpers import row_or_none

THEME = "theme"
LANG = "lang"
ALERT_VOLUME = "alert_volume"
ALERT_MUTED = "alert_muted"

#: The whole vocabulary. A key outside this set is dropped rather than stored.
KEYS = (THEME, LANG, ALERT_VOLUME, ALERT_MUTED)

_THEMES = {"dark", "light"}
_LANGS = {"id", "en"}


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
    """
    clean = normalize(patch)
    if not clean:
        return load(supabase, user_id)

    merged = load(supabase, user_id)
    merged.update(clean)
    supabase.table("profiles").update({"preferences": merged}).eq("id", user_id).execute()
    return merged
