"""Default-deny ScanMeet school add-on entitlement, in three answers rather than two.

Only the server-side service-role client should read this table. Never accept a
client-supplied school identifier as proof of ownership; use the exam/session's
school_id and existing role/school authorization first.

**Why a third answer exists.** `active()` used to answer `False` for both "this
school may not use media" and "the query did not work", which is the right
default for *issuing* something — a token minted on a failed read is a camera
nobody granted. It is the wrong answer for *revoking* something, which is the
other half of the same decision (see `live_entitlement_sweep`): a Supabase blip
would be indistinguishable from a school whose add-on was switched off, and a
sweep built on that two-valued answer would end every lesson on the box during
an outage. The distinction is therefore named rather than inferred:

* ``ACTIVE``   — a row for this school, `enabled`, `trial`/`paid`, not expired.
* ``INACTIVE`` — a definitive *no*: no row, disabled, unpaid, or an expiry that
  is absent, unparseable, or naive. The row is present (or absent) and says so.
* ``UNKNOWN``  — the read itself failed (transport, or migration 075 not applied).
  Nothing may be concluded from it in either direction.

`active()` is `state() == ACTIVE`, so every existing caller keeps exactly the
behaviour it had, and the sweep has the answer it needs.
"""
from __future__ import annotations
from datetime import datetime, timezone

TABLE = "scanmeet_school_entitlements"

ACTIVE = "active"
INACTIVE = "inactive"
UNKNOWN = "unknown"


def _judge(row, now: datetime | None) -> str:
    """A row that was read successfully: entitled, or definitively not."""
    if row.get("enabled") is not True or row.get("status") not in ("trial", "paid"):
        return INACTIVE
    expiry = row.get("valid_until")
    if not isinstance(expiry, str) or not expiry:
        # No deadline on a paid add-on is a configuration nobody may rely on;
        # the token path has always refused it, so it is a *no*, not an unknown.
        return INACTIVE
    try:
        deadline = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return INACTIVE
    if deadline.tzinfo is None:
        # A naive timestamp is ambiguous by an offset; refusing it is the same
        # answer the session timeouts give a naive stamp.
        return INACTIVE
    return ACTIVE if deadline > (now or datetime.now(timezone.utc)) else INACTIVE


def state(db, school_id: str, *, now: datetime | None = None) -> str:
    """``ACTIVE`` / ``INACTIVE`` / ``UNKNOWN`` for one school.

    An empty school id is ``INACTIVE``: it names nobody, so it is not an
    unreadable answer — the request itself told us there is nothing to grant.
    """
    if not school_id:
        return INACTIVE
    try:
        rows = (db.table(TABLE)
                .select("enabled,status,valid_until")
                .eq("school_id", str(school_id)).limit(1).execute().data or [])
    except Exception:  # noqa: BLE001 - transport, or migration 075 not applied
        # Deliberately not logged as a licence decision: it is not one. The sweep
        # reports the schools it could not read, by id, so an operator sees the
        # difference between "lapsed" and "unreadable" without reading a log.
        return UNKNOWN
    if not rows:
        return INACTIVE
    return _judge(rows[0], now)


def active(db, school_id: str, *, now: datetime | None = None) -> bool:
    """May this school use media at all? Default deny, exactly as before.

    Missing migration, transport failure, invalid expiry => False.
    """
    return state(db, school_id, now=now) == ACTIVE


__all__ = ["TABLE", "ACTIVE", "INACTIVE", "UNKNOWN", "state", "active"]
