"""Expiry sweep boundedness and explicit atomic-safety reporting contracts."""
import pytest
from app.services.live_expiry_cleanup import sweep_expired
from tests.unit.test_scanmeet_expiry_media_sweep import Database, NOW, OLD


def test_expiry_sweep_enforces_bounded_school_budget():
    db = Database()
    db.data["scanmeet_school_entitlements"] = [
        {"school_id": f"school-{i:03}", "enabled": True,
         "status": "trial", "valid_until": OLD} for i in range(60)
    ]
    db.data["scan_live_sessions"] = [
        {"id": f"session-{i:03}", "school_id": f"school-{i:03}",
         "room_name": f"room-{i:03}", "status": "active"} for i in range(60)
    ]
    deleted = []
    result = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: {"id": "queued"},
                           now=NOW, limit=5, max_schools=5)
    assert result["schools"] == 5
    assert result["has_more"] is True
    assert len(deleted) == 5
    assert result["atomic_revocation_verified"] is False


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, None])
def test_invalid_budget_is_rejected(bad):
    with pytest.raises(ValueError):
        sweep_expired(Database(), end_room=lambda x: None,
                      enqueue=lambda *a, **k: None, now=NOW,
                      max_schools=bad)


def test_atomic_renewal_fence_remains_explicit_release_blocker():
    report = sweep_expired(Database(), end_room=lambda x: None,
                           enqueue=lambda *a, **k: None, now=NOW)
    # Read-before-delete is not an atomic entitlement fence. Never claim GO.
    assert report["atomic_revocation_verified"] is False
