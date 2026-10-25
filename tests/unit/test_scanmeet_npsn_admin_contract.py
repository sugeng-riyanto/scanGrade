"""No-paid-charge and mandatory license administration contract checks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_super_admin_npsn_management_is_access_controlled():
    routes = (ROOT / "app/routes/super_admin.py").read_text(encoding="utf-8")
    part = routes.split('def scanmeet_school_licenses():', 1)
    assert len(part) == 2
    assert '@_sa_required' in part[0][-180:]
    body = part[1]
    for required in ('school_id', 'npsn', 'log_activity(',
                     'livekit_gateway.end_room(', 'live_cleanup.enqueue(',
                     'scan_live_sessions', 'scanmeet_school_entitlements'):
        assert required in body
    assert 'payment_verified' in body
    assert 'enabled": False' in body


def test_entitlement_is_compulsory_at_all_media_issuance_boundaries():
    live = (ROOT / "app/routes/live.py").read_text(encoding="utf-8")
    assert live.count('live_school_entitlement.active(') >= 3


def test_db_entitlement_default_off_and_rls():
    schema = (ROOT / "supabase/migrations/075_scanmeet_school_entitlements.sql").read_text()
    assert "enabled boolean NOT NULL DEFAULT false" in schema
    assert "ENABLE ROW LEVEL SECURITY" in schema
    assert "REVOKE ALL" in schema
    assert "ON DELETE CASCADE" in schema
