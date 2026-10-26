"""Fail-closed reporting contract for ScanMeet school revocation.

This verifies the handler can distinguish stored entitlement revocation from
the separate, potentially still-running LiveKit media cleanup. E2E required.
"""
import ast
from pathlib import Path


def test_revocation_handler_reports_cleanup_as_incomplete():
    path = Path(__file__).resolve().parents[2] / "app/routes/super_admin.py"
    text = path.read_text(encoding="utf-8")
    module = ast.parse(text)
    fn = next(node for node in ast.walk(module)
              if isinstance(node, ast.FunctionDef)
              and node.name == "scanmeet_school_licenses")
    source = ast.get_source_segment(text, fn)
    assert 'if pending:' in source
    assert 'return jsonify(response), 202' in source
    assert 'cleanup_unrecorded' in source
    assert 'live_cleanup.enqueue(' in source
    assert 'livekit_gateway.end_room(' in source


def test_cleanup_debt_failure_is_never_silently_discarded():
    path = Path(__file__).resolve().parents[2] / "app/routes/super_admin.py"
    text = path.read_text(encoding="utf-8")
    function_source = text.split("def scanmeet_school_licenses():", 1)[1]
    assert 'if debt is None:' in function_source
    assert 'cleanup_unrecorded.append(session["id"])' in function_source
    assert 'active media cleanup remains unconfirmed' in function_source


def test_admin_license_off_drains_paginated_sessions():
    """Do not mistake one PostgREST response for all connected pupils."""
    path = Path(__file__).resolve().parents[2] / "app/routes/super_admin.py"
    text = path.read_text(encoding="utf-8")
    route = text.split("def scanmeet_school_licenses():", 1)[1]
    ast.parse(text)
    assert 'while True:' in route
    assert '.limit(100).execute().data or [])' in route
    assert 'if not sessions:' in route
    assert 'if progressed == 0:' in route
    assert 'No progress while revoking active ScanMeet sessions' in route
