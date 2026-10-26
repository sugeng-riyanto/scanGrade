"""Integration guard: three safety PRs must survive in the SAME merged tree.

These contracts prevent an integration resolution from silently dropping the
strict NPSN check, truthful media cleanup response, or expiry maintenance hook.
Real connected-media E2E remains a separate release gate.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_integrated_super_admin_revocation_and_npsn():
    source = (ROOT / "app/routes/super_admin.py").read_text(encoding="utf-8")
    ast.parse(source)
    route = source.split("def scanmeet_school_licenses():", 1)[1]
    assert 're.fullmatch(r"[0-9]{8}", npsn)' in route
    assert 'livekit_gateway.end_room(' in route
    assert 'live_cleanup.enqueue(' in route
    assert 'cleanup_unrecorded.append(session["id"])' in route
    assert 'if pending:' in route
    assert 'return jsonify(response), 202' in route
    assert 'return jsonify(response)' in route


def test_integrated_expiry_maintenance_stays_enabled_with_flags_off():
    worker = (ROOT / "app/services/live_cleanup.py").read_text(encoding="utf-8")
    expiry = (ROOT / "app/services/live_expiry_cleanup.py").read_text(encoding="utf-8")
    ast.parse(worker)
    ast.parse(expiry)
    start = worker.split("def run_once(", 1)[1].split("def _loop(", 1)[0]
    assert 'live_expiry_cleanup.sweep_expired(' in start
    assert 'end_room=lambda room: livekit_gateway.end_room(config, room)' in start
    assert 'if not live_policy.feature_enabled(' not in start
    assert 'if not _confirmed_expired(db, school_id, moment)' in expiry
    assert 'cleanup_unrecorded' in expiry


def test_maintenance_cannot_claim_complete_revocation_on_expiry_failure():
    """A healthy deletion queue does not imply expiry-connected cameras are gone."""
    worker = (ROOT / "app/services/live_cleanup.py").read_text(encoding="utf-8")
    ast.parse(worker)
    section = worker.split("def run_once(", 1)[1].split("def _loop(", 1)[0]
    assert 'not expiry.get("measured")' in section
    assert 'expiry.get("errors")' in section
    assert 'expiry.get("cleanup_pending")' in section
    assert 'expiry.get("cleanup_unrecorded")' in section
    assert 'result["degraded"] = True' in section
    assert 'result["expiry_blocked"]' in section
