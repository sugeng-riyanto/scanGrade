"""The school admin's upload/download page has no door into it.

Reported: *"belum bisa upload dan download account murid dan guru oleh admin
sekolah"*. The three pieces exist in this repository —

* `/admin-sekolah/accounts` (`admin_sekolah/accounts.html`): upload the account
  emails for pupils, teachers, the head and the deputy head, and download the
  template to fill in;
* `/admin-sekolah/emails/template` and `/admin-sekolah/emails/upload`, which the
  page's own two controls post to;
* `/admin-sekolah/students/login-cards` and `.../teachers/login-cards`, the sheet
  of passwords a school physically hands out;

— and one of them is not reachable. `grep -rn "admin-sekolah/accounts" app/`
found the route, the template that renders it, and **not one link to it**: the
page could only be opened by typing its URL. A page nobody can navigate to is,
for the person who reported it, a page that does not exist.

These tests hold the door open. They are deliberately about *reachability* rather
than about the handlers: the handlers have their own suites, and a page that
works but cannot be found fails the same way as one that is missing.

What they check:

* **the admin sidebar links to it**, so it is one click from every admin page;
* **the two pages it is about link to it** — a school looking at its pupils or
  its teachers is exactly who needs to fix a batch of emails, and that is where
  they will look;
* **the label switches language**, like every other nav entry, so the door is
  legible in both;
* **both controls the page exists for are on it** — upload and template download;
* and the same for the login cards, which are the "download account" half.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
BASE = TEMPLATES / "base.html"
ACCOUNTS = TEMPLATES / "admin_sekolah" / "accounts.html"
STUDENTS = TEMPLATES / "admin_sekolah" / "students.html"
TEACHERS = TEMPLATES / "admin_sekolah" / "teachers.html"
ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"

ACCOUNTS_PATH = "/admin-sekolah/accounts"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. the door ──────────────────────────────────────────────────────────────

class TestTheAccountsPageIsReachable:
    def test_the_admin_sidebar_links_to_it(self):
        nav = _text(BASE)
        assert f'href="{ACCOUNTS_PATH}"' in nav, (
            "the email & activation page is linked from nowhere, so a school can "
            "only reach it by typing its URL — which is how it was reported as "
            "'cannot upload or download accounts'")

    def test_the_nav_entry_names_the_page_it_opens(self):
        nav = _text(BASE)
        block = nav.split(ACCOUNTS_PATH, 1)[1].split("</a>", 1)[0]
        assert "x-text=\"t('" in block, (
            "the nav entry must switch languages like every other one, or the door "
            "is legible in only one of them")

    def test_the_route_still_exists(self):
        source = _text(ROUTES)
        assert re.search(r'@admin_sekolah_bp\.route\("/accounts"\)', source), (
            "the page is linked, so the route it links to must be there")


# ── 2. the two pages it is about must point at it ────────────────────────────

class TestThePagesAboutAccountsPointAtIt:
    @pytest.mark.parametrize("path", [STUDENTS, TEACHERS], ids=["students", "teachers"])
    def test_the_pupil_and_teacher_pages_link_to_it(self, path):
        text = _text(path)
        assert f'href="{ACCOUNTS_PATH}"' in text, (
            f"{path.name} is where a school notices a batch of wrong emails, and it "
            f"is the page the report came from — it has to link to the page that "
            f"fixes them")

    def test_the_label_switches_language_where_the_page_does(self):
        """The pupils' page is bilingual; the teachers' page is pinned to Indonesian.

        `admin_sekolah/teachers.html` opens with `{% set content_lang = 'id' %}`, and
        `deploy/i18n_coverage.py` *fails* a pinned template that carries a `t(a, b)`
        pair — a translated half there is copy no reader can reach. So the label has
        to match the page it lands on: a pair on the pupils' page, plain Indonesian
        on the teachers'.
        """
        students = _text(STUDENTS)
        window = students.split(f'href="{ACCOUNTS_PATH}"', 1)[0][-400:]
        assert "x-text=\"t('" in window, (
            f"the pupils' page is 100% bilingual, so its new label must be a pair: "
            f"{window[-200:]!r}")
        teachers = _text(TEACHERS)
        assert "{% set content_lang = 'id' %}" in teachers, (
            "this test's second half assumes the teachers' page is still pinned")
        window = teachers.split(f'href="{ACCOUNTS_PATH}"', 1)[0][-400:]
        assert "x-text=\"t('" not in window, (
            "a `t(a, b)` pair on a page pinned to one language is unreachable copy, "
            "and the i18n gate refuses it")


# ── 3. what the page is for is on the page ───────────────────────────────────

class TestThePageHasBothControls:
    def test_it_offers_the_upload(self):
        text = _text(ACCOUNTS)
        assert 'action="/admin-sekolah/emails/upload"' in text, (
            "the page's whole reason is the batch upload; without the form it is "
            "a description of a feature")
        assert 'name="file"' in text and "multipart/form-data" in text, (
            "an upload form without a file field or the right encoding posts "
            "nothing")

    def test_it_offers_the_template_download(self):
        assert 'href="/admin-sekolah/emails/template"' in _text(ACCOUNTS), (
            "the template is what the school fills in; no link, no upload")


class TestTheLoginCardsAreReachable:
    @pytest.mark.parametrize("path,action", [
        (STUDENTS, "/admin-sekolah/students/login-cards"),
        (TEACHERS, "/admin-sekolah/teachers/login-cards"),
    ], ids=["students", "teachers"])
    def test_the_page_offers_the_login_cards(self, path, action):
        text = _text(path)
        assert f'action="{action}"' in text, (
            f"{path.name} must offer the sheet a school hands out — the reset "
            f"password button creates the password, this is the only way to hand "
            f"it over")

    @pytest.mark.parametrize("path", [STUDENTS, TEACHERS], ids=["students", "teachers"])
    def test_both_resets_are_reachable(self, path):
        """The single row reset, and the bulk one beside it.

        The teacher bulk-reset route has existed with no button on any page: the
        page offered a per-row reset and a delete, and nothing in between. Reported
        as "reset password" not working — which is what an unreachable button is.
        """
        text = _text(path)
        assert re.search(r"/reset-password'", text), (
            f"{path.name} must keep the per-row reset button")
        assert "/bulk-reset-password" in text, (
            f"{path.name} must offer the bulk reset, not only the per-row one")
