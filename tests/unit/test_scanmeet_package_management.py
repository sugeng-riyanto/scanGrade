from inspect import unwrap
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from flask import Flask, g
import pytest

from app.routes import scanmeet
from app.services import scanmeet_admin as admin, scanmeet_limits
from tests.unit.scanmeet_package_fixtures import MemoryDB, SCHOOL


def database(license_row=None):
    return MemoryDB(schools=[SCHOOL], scanmeet_school_entitlements=[license_row] if license_row else [],
                    scanmeet_plan_requests=[], scan_live_sessions=[])


def test_school_can_request_its_own_package_but_not_forge_school_or_price(monkeypatch):
    db = database()
    monkeypatch.setattr(scanmeet, "get_supabase", lambda: db)
    monkeypatch.setattr(scanmeet, "log_activity", Mock())
    with Flask(__name__).test_request_context(method="POST", data={
        "plan_code": "trial", "school_id": "school-b", "price": "0", "note": "Kelompok kecil",
    }):
        g.user_id, g.user_role, g.user_school_id = "teacher-a", "guru", "school-a"
        response, code = unwrap(scanmeet.request_package)()
        assert code == 201 and response.get_json()["ok"]
    row = db.rows[admin.REQUESTS][0]
    assert row["school_id"] == "school-a" and row["requester_id"] == "teacher-a"
    assert row["status"] == "pending" and "price" not in row
    assert not db.rows[admin.LICENSES]


@pytest.mark.parametrize("role", ["murid", "parent", "outsider"])
def test_pupils_and_unaffiliated_roles_cannot_request_or_activate_packages(role):
    with Flask(__name__).test_request_context(method="POST", data={"plan_code": "trial"}):
        g.user_id, g.user_role, g.user_school_id = "pupil", role, "school-a"
        _, code = unwrap(scanmeet.request_package)()
        assert code == 403


def test_duplicate_pending_request_never_creates_a_second_record(monkeypatch):
    db = database()
    db.rows[admin.REQUESTS] = [{"id": "request-1", "school_id": "school-a", "status": "pending"}]
    monkeypatch.setattr(scanmeet, "get_supabase", lambda: db)
    with Flask(__name__).test_request_context(method="POST", data={"plan_code": "trial"}):
        g.user_id, g.user_role, g.user_school_id = "teacher-a", "guru", "school-a"
        _, code = unwrap(scanmeet.request_package)()
        assert code == 409
    assert len(db.rows[admin.REQUESTS]) == 1


def test_admin_activation_is_scoped_audited_and_uses_server_catalog():
    db, audit = database(), Mock()
    result, code = admin.mutate(db, {"npsn": SCHOOL["npsn"], "action": "activate", "plan_code": "trial",
        "version": "", "session_limit_minutes": 99999, "price": 0}, "operator", {}, audit)
    assert code == 200 and result["ok"]
    row = db.rows[admin.LICENSES][0]
    assert row["school_id"] == "school-a" and row["session_limit_minutes"] == 45
    assert row["session_allowance"] == 1 and row["participant_limit"] == 40
    assert row["concurrent_room_limit"] == 1
    audit.assert_called_once()


def test_stale_admin_form_cannot_reset_session_usage():
    row = {"school_id": "school-a", "updated_at": "new-version", "sessions_used": 2}
    db = database(row)
    _, code = admin.mutate(db, {"npsn": SCHOOL["npsn"], "action": "activate", "plan_code": "trial", "version": "old-version"}, "operator", {}, Mock())
    assert code == 409 and db.rows[admin.LICENSES][0]["sessions_used"] == 2


def test_quota_reservation_racing_admin_save_loses_compare_and_swap():
    row = {"school_id": "school-a", "updated_at": "v1", "sessions_used": 2, "plan_code": "kelas"}
    db = database(row)
    def race(table, rows):
        if table == admin.LICENSES:
            rows[0].update(updated_at="v2", sessions_used=3)
    db.before_update = race
    _, code = admin.mutate(db, {"npsn": SCHOOL["npsn"], "action": "activate", "plan_code": "kelas",
        "payment_verified": "true", "version": "v1"}, "operator", {}, Mock())
    assert code == 409 and db.rows[admin.LICENSES][0]["sessions_used"] == 3


def test_plan_replacement_refuses_to_change_active_room_limits():
    db = database()
    db.rows["scan_live_sessions"] = [{"id": "session-a", "school_id": "school-a", "status": "active"}]
    _, code = admin.mutate(db, {"npsn": SCHOOL["npsn"], "action": "activate", "plan_code": "trial", "version": ""}, "operator", {}, Mock())
    assert code == 409 and not db.rows[admin.LICENSES]


def test_archive_retains_trial_history(monkeypatch):
    row = {"school_id": "school-a", "updated_at": "v1", "trial_started_at": "2026-10-30T00:00:00Z"}
    db = database(row)
    monkeypatch.setattr(admin, "revoke_rooms", lambda *_: ([], []))
    result, code = admin.mutate(db, {"npsn": SCHOOL["npsn"], "action": "archive", "version": "v1"}, "operator", {}, Mock())
    assert code == 200 and result["ok"]
    saved = db.rows[admin.LICENSES][0]
    assert saved["archived_at"] and not saved["enabled"]
    assert saved["trial_started_at"] == row["trial_started_at"]


def test_dashboard_search_and_empty_state_have_real_totals():
    db = database()
    result = admin.dashboard(db, {"q": "missing"}, lambda template, **ctx: ctx)
    assert result["rows"] == [] and result["total"] == 0 and not result["error"]
    result = admin.dashboard(db, {"npsn": SCHOOL["npsn"]}, lambda template, **ctx: ctx)
    assert result["school"]["id"] == "school-a"
    assert result["license"]["effective_status"] == "inactive"


def test_deadline_expiry_writes_debt_before_ending_media_session(monkeypatch):
    now = datetime.now(timezone.utc)
    db = database({"school_id": "school-a", "enabled": True, "status": "paid",
        "valid_until": (now + timedelta(days=1)).isoformat()})
    db.rows["scan_live_sessions"] = [{"id": "room-a", "room_name": "sg_room", "school_id": "school-a", "status": "active", "expires_at": (now - timedelta(seconds=1)).isoformat()}]
    seen = []
    monkeypatch.setattr(scanmeet_limits.live_cleanup, "enqueue", lambda *a, **k: seen.append(db.rows["scan_live_sessions"][0]["status"]))
    result = scanmeet_limits.expire_sessions(db, now=now)
    assert result["ended"] == 1 and seen == ["active"]
    assert db.rows["scan_live_sessions"][0]["status"] == "ended"
    assert "submissions" not in db.calls


def test_failed_debt_write_leaves_expired_row_for_next_sweep(monkeypatch):
    db = database()
    db.rows["scan_live_sessions"] = [{"id": "room-a", "school_id": "school-a", "status": "active"}]
    monkeypatch.setattr(scanmeet_limits.live_cleanup, "enqueue", Mock(side_effect=RuntimeError("offline")))
    result = scanmeet_limits.expire_sessions(db)
    assert result["pending"] == 1 and db.rows["scan_live_sessions"][0]["status"] == "active"


@pytest.mark.parametrize("status,school_id,attached", [
    ("pending", "school-a", True), ("approved", "school-a", False),
    ("rejected", "school-a", False), ("pending", "school-b", False),
])
def test_review_link_never_leaves_closed_or_foreign_request_on_later_forms(status, school_id, attached):
    db = database()
    db.rows[admin.REQUESTS] = [{"id": "request-a", "school_id": school_id,
        "status": status, "plan_code": "kelas", "created_at": "2026-10-30T00:00:00Z"}]
    result = admin.dashboard(db, {"npsn": SCHOOL["npsn"], "request_id": "request-a"}, lambda template, **ctx: ctx)
    assert result["request_id"] == ("request-a" if attached else "")
    if attached:
        assert result["selected_plan"] == "kelas"


def test_direct_review_link_works_beyond_first_twenty_pending_requests():
    db = database()
    db.rows[admin.REQUESTS] = [{"id": f"request-{i}", "school_id": f"other-school-{i}",
        "status": "pending", "plan_code": "trial", "created_at": f"{i:03}"} for i in range(20)]
    db.rows[admin.REQUESTS].append({"id": "request-target", "school_id": "school-a",
        "status": "pending", "plan_code": "sekolah", "created_at": "999"})
    result = admin.dashboard(db, {"npsn": SCHOOL["npsn"], "request_id": "request-target"}, lambda template, **ctx: ctx)
    assert len(result["pending"]) == 20
    assert result["request_id"] == "request-target" and result["selected_plan"] == "sekolah"


def test_unavailable_direct_request_lookup_keeps_a_readable_page(monkeypatch):
    from tests.unit.scanmeet_package_fixtures import Query
    original = Query.execute
    def execute(query):
        if query.name == admin.REQUESTS and len(query.filters) == 3:
            raise RuntimeError("temporary transport failure")
        return original(query)
    monkeypatch.setattr(Query, "execute", execute)
    result = admin.dashboard(database(), {"npsn": SCHOOL["npsn"], "request_id": "request-target"}, lambda template, **ctx: ctx)
    assert result["school"]["id"] == "school-a"
    assert result["error"] and not result["request_id"]
