"""Default-deny ScanMeet school add-on entitlement.

Only the server-side service-role client should read this table. Never accept a
client-supplied school identifier as proof of ownership; use the exam/session's
school_id and existing role/school authorization first.
"""
from __future__ import annotations
from datetime import datetime, timezone


def active(db, school_id: str, *, now: datetime | None = None) -> bool:
    if not school_id:
        return False
    try:
        rows = (db.table("scanmeet_school_entitlements")
                .select("enabled,status,valid_until")
                .eq("school_id", str(school_id)).limit(1).execute().data or [])
        if not rows:
            return False
        row = rows[0]
        if row.get("enabled") is not True or row.get("status") not in ("trial", "paid"):
            return False
        expiry = row.get("valid_until")
        if not isinstance(expiry, str) or not expiry:
            return False
        deadline = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            return False
        return deadline > (now or datetime.now(timezone.utc))
    except (Exception):
        # Missing migration, transport failure, invalid expiry => never issue media.
        return False
