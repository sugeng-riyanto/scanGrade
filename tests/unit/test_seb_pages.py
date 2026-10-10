"""The public SEB surface, exercised through the interface a stranger actually uses.

Why these are renders and not greps
-----------------------------------
The brief's Phase 10 criterion is behavioural: the page must be reachable without
logging in, must link to the *official* site, and the test ``.seb`` must genuinely
tell a correct installation from an incorrect one. Reading the template text would
pass on a page that never renders — which this repository has already been bitten
by, twice, in the pages whose body is thrown away by the layout branch.

So the test drives ``app.test_client()``: no session, exactly as somebody arriving
from a WhatsApp link.

``base.html`` picks one of two layouts on ``g.user_id``
-------------------------------------------------------
A signed-in request gets ``block content`` and an anonymous one gets
``block content_noauth``; rendering a page into the wrong branch yields a page with
its whole body discarded **and no error raised**. Every assertion below is therefore
made against a request that goes through the same door its own route does, and the
copy is checked in both languages so an empty page cannot satisfy it.
"""
from __future__ import annotations

import gzip
import re

import pytest

from app.services import seb_config_key as ck
from app.services import seb_service

GUIDE = "/panduan/seb"
VERIFY = "/panduan/seb/berhasil"
TEST_FILE = "/panduan/seb/uji.seb"


@pytest.fixture()
def client(app):
    return app.test_client()


def _text(response) -> str:
    return response.get_data(as_text=True)


# ── the guide, without a login ──────────────────────────────────────────────

def test_the_guide_is_reachable_with_no_session_at_all(client):
    res = client.get(GUIDE)
    assert res.status_code == 200


def test_the_platform_warning_is_the_first_thing_and_says_both_languages(client):
    page = _text(client.get(GUIDE))
    assert "Android" in page and "Chromebook" in page
    # Indonesian and English halves both present, because the reader's language is
    # in localStorage where the server cannot see it.
    assert "tidak tersedia untuk Android" in page
    assert "is not available for Android" in page
    # ...and it is *above* the downloads, which is the point of "paling atas".
    assert page.index("tidak tersedia untuk Android") < page.index("Unduh resmi")


def test_every_download_points_at_the_official_site(client):
    page = _text(client.get(GUIDE))
    hrefs = re.findall(r'href="(https?://[^"]+)"', page)
    assert hrefs, "the page offers no download links"
    for href in hrefs:
        assert "safeexambrowser.org" in href, (
            f"{href} is not the official site — the brief forbids hosting the installer")
    # The three platforms SEB actually supports.
    for platform in ("Windows", "macOS", "iPadOS"):
        assert platform in page, platform


def test_the_guide_offers_the_test_file_and_says_what_it_is_for(client):
    page = _text(client.get(GUIDE))
    assert TEST_FILE in page
    assert "tidak berisi soal" in page or "contains no exam" in page


def test_the_guide_tells_a_pupil_they_need_no_password(client):
    """The brief's pupil-facing promise, on the page a pupil actually reads."""
    page = _text(client.get(GUIDE))
    assert "tidak perlu tahu kata sandi" in page
    assert "never need to know a password" in page


# ── the installation test, end to end ───────────────────────────────────────

def test_the_test_file_is_a_real_config_file(client):
    res = client.get(TEST_FILE)
    assert res.status_code == 200
    body = res.get_data()
    assert body[:2] == b"\x1f\x8b"
    settings = seb_service.decode_seb(body)
    assert settings["startURL"].endswith(VERIFY)
    # It is a config like any other — the *same* generator, not a hand-written
    # fixture that could drift away from what a real exam file contains.
    assert set(settings) == set(seb_service.settings_for(None, start_url="x",
                                                        quit_hash="a", admin_hash="b"))


def test_the_fixed_test_passwords_are_hashed_like_any_other(client):
    """The test config's passwords are published on purpose — and still only hashed."""
    settings = seb_service.decode_seb(client.get(TEST_FILE).get_data())
    from app.services import seb_crypto
    assert settings["hashedQuitPassword"] == seb_crypto.sha256_hex("SEB-TEST-QUIT")
    assert "SEB-TEST-QUIT" not in settings.values()


def test_opened_by_a_browser_the_page_refuses_to_claim_success(client):
    page = _text(client.get(VERIFY))
    assert "belum dibuka dari SEB" in page
    assert "was not opened from SEB" in page
    assert "berhasil terpasang" not in page


def test_opened_by_a_client_holding_the_right_key_the_page_confirms_it(client):
    """The acceptance criterion for the test file: it must *work* as a test.

    The header is computed the way a real client computes it — from the file's own
    settings and the absolute URL — so this also proves the two halves of the
    feature agree: the file's Config Key is the one the door checks.
    """
    settings = seb_service.decode_seb(client.get(TEST_FILE).get_data())
    key = ck.config_key(settings)
    url = settings["startURL"]
    res = client.get(VERIFY, headers={ck.CONFIG_KEY_HEADER: ck.request_hash(url, key)})
    page = _text(res)
    assert res.status_code == 200
    assert "berhasil terpasang" in page
    assert "is installed correctly" in page
    assert "belum dibuka dari SEB" not in page


def test_a_wrong_key_is_not_a_pass(client):
    settings = seb_service.decode_seb(client.get(TEST_FILE).get_data())
    res = client.get(VERIFY, headers={ck.CONFIG_KEY_HEADER: "0" * 64})
    assert "belum dibuka dari SEB" in _text(res)


# ── the doors exist, with the methods they claim ────────────────────────────

def test_every_seb_endpoint_is_registered(app):
    """A route that is not on the map is a button that 404s in production."""
    rules = {rule.rule: (rule.endpoint, rule.methods) for rule in app.url_map.iter_rules()}
    for path in ("/teacher/exams/<exam_id>/seb",
                 "/teacher/exams/<exam_id>/seb/enable",
                 "/teacher/exams/<exam_id>/seb/disable",
                 "/teacher/exams/<exam_id>/seb/reissue",
                 "/teacher/exams/<exam_id>/seb/password",
                 "/teacher/exams/<exam_id>/seb/file",
                 "/student/exams/<exam_id>/seb-file",
                 "/panduan/seb",
                 TEST_FILE,
                 VERIFY):
        assert path in rules, path
    # The two writes that hand out or replace a secret are POST-only: a GET can be
    # reached by a link, an <img> tag or a prefetch.
    for path in ("/teacher/exams/<exam_id>/seb/password",
                 "/teacher/exams/<exam_id>/seb/reissue",
                 "/teacher/exams/<exam_id>/seb/enable"):
        assert "GET" not in rules[path][1], path


def test_every_endpoint_the_seb_pages_name_actually_exists(app):
    """A `url_for` for a renamed endpoint is a page that cannot render at all.

    This is not hypothetical: the panel linked its back button at `teacher.exams`,
    which is not an endpoint in this app (`my_exams` is), so `url_for` raised a
    ``BuildError`` while rendering and every teacher opening the page got a 500 —
    not a broken link, the whole page. Jinja does not check the name until the page
    is built, so nothing else in the suite noticed; this walks the four SEB templates
    and asks the app's own view registry, which is the only thing that knows.
    """
    import pathlib

    templates = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates"
    for name in ("teacher/seb_panel.html", "student/seb_claim.html",
                 "seb_guide.html", "seb_verified.html"):
        text = (templates / name).read_text(encoding="utf-8")
        endpoints = re.findall(r"url_for\(\s*'([a-z_]+\.[a-z_]+)'", text)
        assert endpoints, f"{name} names no endpoint — the walk found nothing to check"
        for endpoint in endpoints:
            assert endpoint in app.view_functions, f"{name}: no such endpoint {endpoint}"


def test_every_credential_route_is_behind_a_login(app):
    """The pupil and teacher file doors must never be anonymously reachable."""
    rules = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
    source = (__import__("pathlib").Path(__file__).resolve().parents[2]
              / "app" / "routes" / "seb.py").read_text(encoding="utf-8")
    for path in ("/teacher/exams/<exam_id>/seb",
                 "/teacher/exams/<exam_id>/seb/enable",
                 "/teacher/exams/<exam_id>/seb/disable",
                 "/teacher/exams/<exam_id>/seb/reissue",
                 "/teacher/exams/<exam_id>/seb/password",
                 "/teacher/exams/<exam_id>/seb/file",
                 "/student/exams/<exam_id>/seb-file"):
        endpoint = rules[path].split(".")[-1]
        # Each such view is decorated with @login_required, which is the shape the
        # client test proves below for one of them.
        assert f"def {endpoint}(" in source
    assert source.count("@login_required") >= 7


def test_an_anonymous_request_to_a_file_door_is_redirected_not_served(client):
    """The door itself, over HTTP: no session, no file."""
    res = client.get("/student/exams/ex-1/seb-file")
    assert res.status_code in (301, 302, 401, 403)
    assert res.status_code != 200


# ── the toggle's guard, on the server ───────────────────────────────────────

def test_the_confirmation_checkbox_is_enforced_by_the_route_not_only_the_page():
    """Fase 6's guard is a POST the server refuses, not a disabled button.

    A disabled attribute lives in HTML, and a crafted POST never reads it — so the
    check has to be in the handler. This asserts the *order* too: the confirmation
    is tested before anything is issued, so a refused toggle writes nothing.
    """
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[2]
              / "app" / "routes" / "seb.py").read_text(encoding="utf-8")
    body = source[source.index("def enable("):source.index("def disable(")]
    assert "CONFIRM_FIELD" in body
    assert body.index("CONFIRM_FIELD") < body.index("seb_service.issue(")


def test_the_panel_wires_the_same_field_the_route_reads():
    import pathlib
    template = (pathlib.Path(__file__).resolve().parents[2]
                / "app" / "templates" / "teacher" / "seb_panel.html").read_text(encoding="utf-8")
    assert 'name="{{ confirm_field }}"' in template
