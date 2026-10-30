"""No-paid-charge and mandatory license administration contract checks."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask, g

from app.routes import super_admin

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


@pytest.fixture
def license_view(monkeypatch):
    school = {"id": "pilot-school", "npsn": "69893227", "name": "Pilot school"}
    schools, licenses = MagicMock(), MagicMock()
    for query in (schools, licenses):
        query.select.return_value = query
        query.eq.return_value = query
        query.limit.return_value = query
    schools.execute.return_value = SimpleNamespace(data=[school])
    licenses.execute.return_value = SimpleNamespace(data=[])
    db = MagicMock()
    db.table.side_effect = lambda name: {
        "schools": schools, "scanmeet_school_entitlements": licenses,
    }[name]
    audit = MagicMock()
    monkeypatch.setattr(super_admin, "get_supabase", lambda: db)
    monkeypatch.setattr(super_admin, "log_activity", audit)
    monkeypatch.setattr(super_admin, "render_template", lambda template, **ctx: ctx)
    return SimpleNamespace(app=Flask(__name__), view=unwrap(super_admin.scanmeet_school_licenses),
                           schools=schools, licenses=licenses, audit=audit)


@pytest.mark.parametrize("npsn", ["69893227", "20623248", "00123456", " 69893227 "])
def test_real_license_lookup_accepts_eight_ascii_digits(license_view, npsn):
    with license_view.app.test_request_context(query_string={"npsn": npsn}):
        result = license_view.view()
    assert result["school"]["id"] == "pilot-school"
    license_view.schools.eq.assert_called_once_with("npsn", npsn.strip())


@pytest.mark.parametrize("npsn", ["", "1234567", "123456789", "1234567a",
                                  "１２３４５６７８", "\\dddddddd"])
def test_invalid_npsn_cannot_write_a_license(license_view, npsn):
    with license_view.app.test_request_context(method="POST", data={"npsn": npsn}):
        response, status = license_view.view()
        assert status == 400
        assert "NPSN" in response.get_json()["error"]
    license_view.schools.execute.assert_not_called()
    license_view.licenses.upsert.assert_not_called()


def test_valid_pilot_trial_is_saved_for_resolved_school_and_audited(license_view):
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with license_view.app.test_request_context(method="POST", data={
        "npsn": "69893227", "enabled": "true", "status": "trial", "valid_until": expiry,
    }):
        g.user_id = "operator"
        result = license_view.view()
    assert result is not None
    row = license_view.licenses.upsert.call_args.args[0]
    assert row["school_id"] == "pilot-school"
    assert row["approved_by"] == "operator"
    assert row["enabled"] is True and row["status"] == "trial"
    license_view.audit.assert_called_once()


def test_valid_npsn_does_not_bypass_paid_confirmation(license_view):
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with license_view.app.test_request_context(method="POST", data={
        "npsn": "69893227", "enabled": "true", "status": "paid", "valid_until": expiry,
    }):
        response, status = license_view.view()
        assert status == 400
        assert "payment confirmation" in response.get_json()["error"]
    license_view.licenses.upsert.assert_not_called()
