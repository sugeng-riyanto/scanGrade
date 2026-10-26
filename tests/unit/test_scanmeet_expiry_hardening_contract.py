"""Security regression contracts for expiry cleanup; intentionally fail until hardened.

These tests must not be weakened to make CI green. Real LiveKit two-browser
revocation remains a separate staging release gate.
"""
from datetime import datetime, timedelta, timezone

from app.services.live_expiry_cleanup import sweep_expired
from tests.unit.test_scanmeet_expiry_media_sweep import Database, NOW, OLD


def test_expiry_sweep_enforces_a_bounded_school_budget():
    db = Database()
    db.data['scanmeet_school_entitlements'] = [
        {'school_id': f'school-{i:03}', 'enabled': True,
         'status': 'trial', 'valid_until': OLD} for i in range(60)
    ]
    db.data['scan_live_sessions'] = [
        {'id': f'session-{i:03}', 'school_id': f'school-{i:03}',
         'room_name': f'room-{i:03}', 'status': 'active'} for i in range(60)
    ]
    deleted = []
    result = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: {'id': 'queued'},
                           now=NOW, limit=5)
    # A single worker tick must have a finite upper bound; remaining work
    # must be explicitly reported rather than silently claiming completion.
    assert result['schools'] <= 5
    assert result.get('has_more') is True
    assert len(deleted) <= 5


def test_renewal_during_room_termination_cannot_delete_renewed_room():
    db = Database()
    original_table = db.table
    class RenewalDatabase:
        def table(self, name):
            return original_table(name)
    # This test is a contract marker: atomic entitlement/session fencing
    # is required before destructive LiveKit calls. Without an atomic
    # boundary, a renewal between the last check and deletion is unsafe.
    assert hasattr(__import__('app.services.live_expiry_cleanup', fromlist=['sweep_expired']),
                   'atomic_expiry_revocation_supported'), (
        'Implement an atomic renewal-vs-revocation fence before declaring GO'
    )
