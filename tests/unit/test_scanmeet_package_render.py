from flask import g, render_template

from app.services import scanmeet_admin, scanmeet_packages
from tests.unit.scanmeet_package_fixtures import MemoryDB, SCHOOL


def actor():
    g.user_id, g.user_role, g.user_school_id = 'preview-actor', 'super_admin', SCHOOL['id']
    g.tz_offset = 7
    g.user_name, g.user_email = 'Operator demo', 'operator@example.invalid'


def test_admin_empty_license_renders_real_form_and_version(app, monkeypatch):
    db = MemoryDB(schools=[SCHOOL])
    monkeypatch.setitem(app.extensions, 'supabase', db)
    with app.test_request_context('/super-admin/scanmeet-schools?npsn=69893227'):
        actor()
        html = scanmeet_admin.dashboard(db, {'npsn': SCHOOL['npsn']}, render_template)
    assert 'id="licenseForm"' in html and 'name="version"' in html
    assert 'min-w-[760px]' in html and 'overflow-x-auto' in html
    assert 'scanmeet-management.js' in html and 'SMA Harapan Bangsa' in html


def test_offers_render_all_prices_and_limits_from_one_catalog(app, monkeypatch):
    monkeypatch.setitem(app.extensions, 'supabase', MemoryDB())
    with app.test_request_context('/scanmeet'):
        actor()
        html = render_template('scanmeet/offers.html', plans=scanmeet_packages.PLANS,
                               school=SCHOOL, license=None, pending=None, error='', can_request=True)
    assert '49.000' in html and '399.000' in html
    assert 'No session-count limit during the validity period' in html
    assert '>None<' not in html
    for code in ('trial','kelas','sekolah'):
        assert f'value="{code}"' in html
    assert 'name="_csrf_token"' in html and 'maxlength="500"' in html


def test_pupil_offer_has_no_package_request_form(app, monkeypatch):
    monkeypatch.setitem(app.extensions, 'supabase', MemoryDB())
    with app.test_request_context('/scanmeet'):
        actor()
        g.user_role = 'murid'
        html = render_template('scanmeet/offers.html', plans=scanmeet_packages.PLANS,
                               school=SCHOOL, license=None, pending=None, error='', can_request=False)
    assert 'data-request-form' not in html
    assert 'Minta guru atau pengelola sekolah' in html
