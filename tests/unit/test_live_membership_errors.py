"""Membership refusal is a clear 409, not an unavailable-media 503."""
from inspect import unwrap
from unittest.mock import Mock

from flask import Flask, g
import pytest

from app.routes import live


@pytest.mark.parametrize('reason,copy', [
    ('scanmeet_school_room_busy', 'ruang ScanMeet aktif'),
    ('scanmeet_session_allowance_exhausted', 'Jatah sesi'),
])
def test_package_refusals_leave_exam_running(monkeypatch, reason, copy):
    db = Mock()
    db.table.return_value.insert.return_value.execute.side_effect = RuntimeError(reason)
    monkeypatch.setattr(live, '_off', lambda: None)
    monkeypatch.setattr(live, 'get_supabase', lambda: db)
    monkeypatch.setattr(live, '_exam', lambda *a: {'id': 'exam-a', 'school_id': 'school-a'})
    monkeypatch.setattr(live, '_access', lambda *a, **kw: live.live_policy.LIVE_TEACHER)
    monkeypatch.setattr(live, '_one', lambda *a: None)
    monkeypatch.setattr(live.live_policy, 'client_ready', lambda *a: True)
    monkeypatch.setattr(live.live_school_entitlement, 'active', lambda *a: True)
    monkeypatch.setattr(live.live_exam_toggle, 'enabled', lambda *a: True)
    with Flask(__name__).test_request_context(method='POST'):
        g.user_id = 'teacher-a'
        response, code = unwrap(live.create_session)('exam-a')
    assert code == 409 and copy in response.get_json()['error']
    assert 'ujian tetap berjalan' in response.get_json()['error']
    assert all(call.args[0] == 'scan_live_sessions' for call in db.table.call_args_list)
