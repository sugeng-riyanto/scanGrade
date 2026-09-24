"""How many days a new school's free trial lasts, in one place.

Why this exists
---------------
``trial_settings`` holds one number, and three flows need it:

* approving a school registration (``/admin/registration-requests/<id>/approve``);
* a cash activation by the super admin (``/super-admin/activation-codes``);
* the trial sentence on a school's own subscription page.

Two of the three read the table. The approval flow — the door almost every school
actually arrives through — wrote a hard-coded 14, so the page that sets the number
had no effect on the trials it claims to set. A settings page whose value never
reaches the thing it configures is not a settings page.

Two things the table cannot answer for itself, both settled here:

* **which row.** The table has no uniqueness, so more than one row is insertable
  and ``.limit(1)`` then returns whichever one Postgres feels like — the settings
  page edits one row while a grant site reads the other. This is not hypothetical:
  measured on production, the table held **fifteen** rows, fourteen of them seeds
  from ``012_subscription_system.sql`` whose ``ON CONFLICT DO NOTHING`` has no
  conflict target and so re-inserted on every setup run. The number the operator
  had set (30) sat on the two rows written *last* and carrying the *lowest* ids,
  and the unordered read returned a June seed: the page said 30, approvals granted
  14. So the rule below is **the row a human last wrote** — greatest ``updated_at``,
  ``id`` breaking ties — rather than the newest row by id, which is a different
  question and got the live table wrong. ``NULLS LAST`` because Postgres sorts
  NULLs *first* under ``DESC`` and a row with no timestamp is not the most recent
  change. (``supabase/migrations/034_trial_settings_singleton.sql`` resolves the
  duplicates and forbids more; this file works with or without it, which is what
  makes that migration safe to apply at any time — and `describe` *reports* the
  duplicates, so the state that produced that mismatch cannot stay invisible.)
* **nothing stored.** The row can be deleted (that is what the reset action does)
  or the read can fail. The built-in default answers both, which is what stops a
  reset from meaning "no trial at all".

The *effective* value — stored override, else default — is what a grant must use,
and `get_trial_days` is the only thing that decides it.
"""
from flask import current_app

#: The length when nothing is stored: what every school got before this setting
#: existed, what the schema seeds the row with, and what a failed read falls back
#: to. Deliberately not 0 — an unreadable table must not silently grant nothing.
DEFAULT_TRIAL_DAYS = 14

#: The bounds the column and the form agree on. A value outside them is not a
#: policy, it is a typo: 0 already means "no trial", so a negative has no meaning
#: to add, and beyond a year a trial is indistinguishable from a giveaway.
MIN_TRIAL_DAYS = 0
MAX_TRIAL_DAYS = 365

TABLE = "trial_settings"


def _client(supabase=None):
    """The Supabase client to read through, or the app's own when none is given.

    Passed in explicitly by the routes so a test can drive them against a fake;
    the default keeps the service usable from a Jinja global or a script.
    """
    return supabase if supabase is not None else current_app.extensions["supabase"]


def parse_days(raw) -> int:
    """A posted value as a number of days, or ``ValueError`` if it is not one.

    The only place a *stranger's* number is turned into a policy. A missing field
    means the default rather than an error — the form always posts one, so an
    absent value is a stale client, not a hostile one. Everything else must be a
    whole number inside the bounds; ``"30"``, ``30`` and ``" 30 "`` are the same
    answer, and ``"abc"``, ``"14.5"``, ``""`` and ``-1`` are refused so the page
    can say why instead of quietly storing a bound-clamped guess.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return DEFAULT_TRIAL_DAYS
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("not a whole number of days")
    if days < MIN_TRIAL_DAYS or days > MAX_TRIAL_DAYS:
        raise ValueError(
            f"outside the allowed range {MIN_TRIAL_DAYS}-{MAX_TRIAL_DAYS} days")
    return days


#: The row a human last wrote, with the id breaking ties. The ``.nullslast``
#: suffix is written into the column name because the client's own `nullsfirst`
#: flag cannot express it: leaving it False emits no suffix, and Postgres reads an
#: unqualified ``DESC`` as NULLS **FIRST** — a row with no timestamp would sort
#: ahead of every real one.
_ORDER = "updated_at.desc.nullslast"


def stored_row(supabase=None) -> dict | None:
    """The stored override, or ``None`` when there is none.

    The row the settings page edits is the row every grant reads: the one written
    *last* by a human, tie-broken by the greater id. See the module docstring for
    why recency and not newest-id — that choice was worth 16 days of trial on the
    live table.
    """
    try:
        res = (_client(supabase).table(TABLE).select("*")
               .order(_ORDER).order("id", desc=True).limit(1).execute())
    except Exception:
        return None
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def duplicate_rows(supabase=None) -> int:
    """How many *other* rows the table holds, or 0 when that cannot be read.

    Reported rather than repaired: deleting rows on a page read would be a write
    hiding inside a GET, and a count that cannot be taken is not a count of zero.
    """
    try:
        res = (_client(supabase).table(TABLE).select("id", count="exact")
               .limit(1).execute())
    except Exception:
        return 0
    total = getattr(res, "count", None)
    if total is None:
        return 0
    try:
        return max(0, int(total) - 1)
    except (TypeError, ValueError):
        return 0


def get_trial_days(supabase=None) -> int:
    """The length a **newly granted** trial must be.

    The stored override when there is a usable one, the built-in default
    otherwise. Nothing else in the codebase may decide this: every grant site
    calls this, which is the whole point of the module.
    """
    row = stored_row(supabase)
    if not row:
        return DEFAULT_TRIAL_DAYS
    try:
        return parse_days(row.get("trial_days"))
    except ValueError:
        # A stored value outside the bounds is not a policy anyone meant to set
        # (a hand-edit, a very old row). Refusing it loudly would take the whole
        # registration flow down with it, so it is read as unset instead.
        return DEFAULT_TRIAL_DAYS


def describe(supabase=None) -> dict:
    """What the settings page paints: the effective value, and where it came from.

    ``is_override`` is the difference between "you set this to 30" and "this is
    the built-in 14 and you have never opened this page", which is the one thing
    the old page could not say — it rendered the default as if it were a saved
    value, so a reset was indistinguishable from a save.
    """
    row = stored_row(supabase)
    days = get_trial_days(supabase)
    extra = duplicate_rows(supabase)
    if not row:
        return {"days": days, "is_override": False, "updated_by": None,
                "updated_at": None, "default": DEFAULT_TRIAL_DAYS,
                "duplicates": extra}
    return {
        "days": days,
        "is_override": True,
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
        "default": DEFAULT_TRIAL_DAYS,
        "stored_days": row.get("trial_days"),
        "duplicates": extra,
    }


def save(days, user_id, supabase=None) -> dict:
    """Store the override and return the new picture, as ``describe`` draws it.

    Insert-or-update against the newest row, which is what makes this the "C" and
    the "U" of the table's CRUD: the first save creates the row, every later one
    rewrites it. ``ValueError`` from `parse_days` is left to the caller — the
    route turns it into a message that names the bounds.
    """
    client = _client(supabase)
    value = parse_days(days)
    payload = {"trial_days": value, "updated_by": user_id,
               "updated_at": _now_iso()}
    row = stored_row(supabase)
    if row and row.get("id") is not None:
        client.table(TABLE).update(payload).eq("id", row["id"]).execute()
    else:
        client.table(TABLE).insert(payload).execute()
    return describe(supabase)


def reset(_user_id=None, supabase=None) -> bool:
    """Delete every stored row, so the built-in default applies again.

    This is the table's "D", and it is the only honest one a singleton settings
    row has: removing the override is exactly "stop configuring this". Deleting
    *every* row rather than the newest is deliberate — leaving rows the reader
    would ignore anyway is how a table accumulates values that look like settings
    and are not. Returns whether the stored value is gone, so a caller can tell a
    reset from a failed write.
    """
    client = _client(supabase)
    try:
        client.table(TABLE).delete().gte("id", 0).execute()
    except Exception:
        return False
    return stored_row(supabase) is None


def days_until(now, days=None, supabase=None):
    """``now`` plus the effective trial length — for callers that need the date.

    Here rather than at each call site for the same reason: a grant that computed
    the end date from one number while storing another is the defect this module
    was written to end.
    """
    from datetime import timedelta
    return now + timedelta(days=days if days is not None else get_trial_days(supabase))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
