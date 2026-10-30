"""Exam entry states remain visible without granting a second publisher access."""
from inspect import unwrap
from flask import Flask, g, render_template
import pytest
from app.routes import live
from tests.unit.live_fixtures import FakeDB

EXAM={'id':'exam-1','school_id':'school-1','teacher_id':'teacher-a','scan_live_enabled':True}
SESSION={'id':'room-1','school_id':'school-1','exam_id':'exam-1','created_by':'teacher-a','status':'active'}

def actor(role='guru',uid='teacher-a',school='school-1'):
    g.user_role,g.user_id,g.user_school_id=role,uid,school

@pytest.mark.parametrize('role,uid,school,expected',[
 ('guru','teacher-a','school-1',200),('admin_sekolah','admin-a','school-1',200),
 ('guru','teacher-b','school-1',403),('admin_sekolah','admin-b','school-2',403),
])
def test_status_visible_to_authorized_admin_but_not_other_teacher_or_school(monkeypatch,role,uid,school,expected):
    app=Flask(__name__)
    db=FakeDB({'scan_live_sessions':[dict(SESSION)]})
    monkeypatch.setattr(live,'_off',lambda:None)
    monkeypatch.setattr(live,'get_supabase',lambda:db)
    monkeypatch.setattr(live,'_exam',lambda *_:EXAM)
    with app.test_request_context():
        actor(role,uid,school)
        result=app.make_response(unwrap(live.status)('exam-1'))
        assert result.status_code==expected
        if expected==200:
            body=result.get_json()
            assert body['enabled'] is True
            assert body['can_join'] is (uid=='teacher-a')
            assert 'no-store' in result.headers['Cache-Control']
            if uid!='teacher-a':
                assert live._check(db,SESSION,staff_only=True) is None

@pytest.mark.parametrize('available',[False,True])
def test_management_card_renders_even_when_service_is_off(app,available):
    with app.test_request_context('/teacher/exams/exam-1'):
        actor()
        html=render_template('live/exam_entry.html',exam=EXAM,scanmeet_enabled=available)
    assert 'id="scanmeet-settings"' in html
    assert ('data-entry-toggle' in html) is available
    assert '/student/exams/exam-1' in html
    assert 'data-entry-copy' in html and 'https://wa.me/' in html
    assert 'getUserMedia' not in html
    assert 'data-available="'+('1' if available else '0')+'"' in html


def test_embedded_panel_has_no_tab_link_and_sdk_is_data_not_executed(app):
    with app.test_request_context('/student/exams/exam-1'):
        actor('murid','student-a')
        html=render_template('live/student_panel.html',exam=EXAM)
    assert 'data-embedded="1"' in html and 'id="live-join"' in html
    assert 'data-sdk=' in html
    assert '<script' not in html and '<iframe' not in html and 'target=' not in html
    assert 'id="live-start"' not in html and 'id="live-end"' not in html

@pytest.mark.parametrize('available,preview,expected',[(False,False,False),(True,False,True),(True,True,False)])
def test_full_pupil_page_only_embeds_media_for_a_real_feature_enabled_sitting(app,available,preview,expected):
    from tests.unit.test_student_exam_kind_rendering import _render
    # The render helper deliberately uses Template.render, so context processors
    # do not inject flags: pass the same flag the actual route receives explicitly.
    html=_render(app,{'0':'mcq'},{},extra={'scanmeet_enabled':available,'preview':preview})
    assert ('id="scan-live"' in html) is expected
    assert ('js/scan-live-exam.js' in html) is expected
    assert '<script src="/static/vendor/livekit' not in html


def test_admin_can_switch_off_an_existing_teacher_room_without_receiving_tracks(monkeypatch):
    app=Flask(__name__)
    db=FakeDB({'exams':[dict(EXAM)],'scan_live_sessions':[dict(SESSION,room_name='sg_synthetic')]})
    ended=[]
    monkeypatch.setattr(live,'_off',lambda:None)
    monkeypatch.setattr(live,'get_supabase',lambda:db)
    monkeypatch.setattr(live,'_exam',lambda *_:db.rows['exams'][0])
    monkeypatch.setattr(live.livekit_gateway,'end_room',lambda config,name:ended.append(name))
    with app.test_request_context(method='POST',json={'enabled':False}):
        actor('admin_sekolah','admin-a')
        result=app.make_response(unwrap(live.set_exam_enabled)('exam-1'))
    assert result.status_code==200
    assert db.rows['exams'][0]['scan_live_enabled'] is False
    assert db.rows['scan_live_sessions'][0]['status']=='ended'
    assert ended==['sg_synthetic']
