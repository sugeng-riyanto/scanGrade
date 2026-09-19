"""The device preview has to stay a claim about what was *actually* looked at.

A responsive defect is usually invisible to a test and obvious to a human at
320 px, so `/tools/device-preview` renders every main page at 320 / 375 / 768 px
side by side. That makes the page itself a claim, and the two ways such a claim
goes wrong are both silent:

  1. **It stops covering pages.** A list typed out by hand goes stale the moment
     a route is added, and the preview keeps looking complete.
  2. **It stops saying what it leaves out.** A URL silently dropped from the
     list (a JSON endpoint that looks like a page, a download, a print artefact)
     is indistinguishable on screen from a page that was checked and passed.

So the list is derived from the app's own URL map, and every URL it does not
render is on the page with its reason in both languages. These tests pin those
two properties, plus the access rule: an internal tool is for staff, and a
pupil sitting an exam must not be able to open one.
"""
import re
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import pytest

from app import create_app
from app.services import device_preview as preview

ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROUTE = ROOT / "app" / "routes" / "tools.py"
PAGE_TEMPLATE = ROOT / "app" / "templates" / "tools" / "device_preview.html"

SESSION = {
    "user_id": "user-1",
    "email": "guru@example.test",
    "name": "Guru",
    "role": "guru",
    "school_id": "school-1",
    "class_id": None,
    "status": "active",
}

PAGE_URL = "/tools/device-preview"


@pytest.fixture(scope="module")
def app():
    """One app for the read-only checks: building it is the slow part here."""
    return create_app("testing")


@pytest.fixture(autouse=True)
def _clear_session_cache():
    """The session cache is keyed on the token, so a cached guru would leak.

    Every client here authenticates with the literal token "fake-token"; without
    this, the role a previous test asked for would be replayed for the next one.
    """
    from app.utils import kv_cache
    kv_cache._local.clear()
    yield
    kv_cache._local.clear()


def get_as(app, role, url=PAGE_URL):
    """A GET whose session resolves to *role*, without touching Supabase."""
    client = app.test_client()
    with patch("app.utils.auth._fetch_session",
               return_value=dict(SESSION, role=role)):
        return client.get(url, headers={
            "Authorization": "Bearer fake-token",
            "Accept": "text/html",
        })


# ── the list comes from the app, not from this module ────────────

class TestTheListComesFromTheApp:
    def test_a_route_added_tomorrow_is_already_in_the_list(self):
        """The property that makes the page stay true without anyone remembering."""
        # Its own app: this adds a rule, and a shared map would carry it into the
        # other tests, where an extra page is noise rather than a finding.
        app = create_app("testing")
        app.add_url_rule("/teacher/brand-new-page", "brand_new_page", lambda: "ok")
        pages = {p["url"]: p for p in preview.preview_pages(app)}

        assert "/teacher/brand-new-page" in pages, (
            "a new route did not appear in the device preview — the list is being "
            "typed out somewhere instead of derived from the URL map")
        page = pages["/teacher/brand-new-page"]
        # A readable name and the right section, both derived: a new page must not
        # need an edit here to be findable in the sidebar.
        assert page["label"] == "Brand New Page"
        # Unless it collides, in which case the name says where it lives. Checked
        # on the same synthetic app so the assertion cannot drift with the app.
        app.add_url_rule("/teacher/classes/brand-new-page", "brand_new_page_two",
                         lambda: "ok")
        labels = {p["url"]: p["label"] for p in preview.preview_pages(app)
                  if p["url"].endswith("brand-new-page")}
        assert labels == {"/teacher/brand-new-page": "Teacher / Brand New Page",
                          "/teacher/classes/brand-new-page": "Classes / Brand New Page"}, labels
        assert page["section"] == "teacher"

    def test_every_listed_url_is_a_real_rule(self, app):
        rules = {str(rule.rule) for rule in app.url_map.iter_rules()}
        for page in preview.preview_pages(app):
            assert page["url"] in rules

    def test_a_page_is_listed_exactly_once(self, app):
        urls = [p["url"] for p in preview.preview_pages(app)]
        assert len(urls) == len(set(urls)), "a page is listed twice"

    def test_every_page_reaches_a_section(self, app):
        """Grouped output must not lose a page the flat list carries."""
        flat = {p["url"] for p in preview.preview_pages(app)}
        grouped = {p["url"] for grp in preview.preview_sections(app) for p in grp["pages"]}
        assert flat == grouped

    def test_two_pages_in_one_section_never_share_a_name(self, app):
        """A name that collides is a button nobody can tell from its neighbour.

        The school-admin section used to be exactly this — two dashboards, two
        imports — and the longer name is what made them distinguishable on a
        phone. The duplicates are 308s now, so the real app has no collision left;
        this stays as the net for the next pair someone adds, and
        `test_the_school_admin_section_holds_each_page_once` is the test that the
        *duplicates* stay gone.
        """
        seen = {}
        for page in preview.preview_pages(app):
            key = (page["section"], page["label"])
            assert key not in seen, (
                f"{page['url']} and {seen[key]} both show as {page['label']!r} in "
                f"section {page['section']!r}")
            seen[key] = page["url"]

    def test_only_the_colliding_pages_get_a_longer_name(self):
        """Synthetic, because the real collision this described is now a 308.

        It used to assert on `/admin/dashboard` and `/admin-sekolah/dashboard`,
        which is the duplicate the legacy redirects removed. Pointing a *naming*
        test at pages that no longer both exist would leave it passing without
        checking anything, so the pair is built here instead — same shape, no
        dependency on the app staying broken.
        """
        app = create_app("testing")
        app.add_url_rule("/teacher/brand-new-page", "collide_a", lambda: "ok")
        app.add_url_rule("/teacher/classes/brand-new-page", "collide_b", lambda: "ok")
        labels = {p["url"]: p["label"] for p in preview.preview_pages(app)}
        # Neither collided with anything else, so neither name grew.
        assert labels["/teacher/dashboard"] == "Dashboard"
        # And the two that collided now say which one they are.
        assert labels["/teacher/brand-new-page"] == "Teacher / Brand New Page"
        assert labels["/teacher/classes/brand-new-page"] == "Classes / Brand New Page"

    def test_a_deeper_name_reads_as_a_path(self):
        assert preview.page_label("/admin/dashboard", 2) == "Admin / Dashboard"
        assert preview.page_label("/teacher/exams/new", 2) == "Exams / New"
        assert preview.page_label("/", 2) == "Landing"

    def test_a_section_name_is_bilingual(self, app):
        for group in preview.preview_sections(app):
            assert group["id_name"] and group["en"], group["id"]


# ── what it leaves out is on the page ────────────────────────────

class TestNothingIsDroppedInSilence:
    def test_every_get_route_is_either_rendered_or_explained(self, app):
        """No route may be neither: that is the only way the page can lie."""
        rendered = {p["url"] for p in preview.preview_pages(app)}
        excluded = {e["url"] for e in preview.preview_exclusions(app)}

        assert not (rendered & excluded), "a URL is both rendered and excluded"
        for rule in app.url_map.iter_rules():
            if "GET" not in (rule.methods or set()):
                continue
            url = str(rule.rule)
            assert url in rendered or url in excluded, (
                f"{url} is in neither the preview nor its exclusion list — an "
                f"omission nobody can read is how a preview starts lying")

    def test_a_url_needing_an_id_says_so(self, app):
        reasons = {e["url"]: (e["reason_id"], e["reason_en"])
                   for e in preview.preview_exclusions(app)}
        assert "/teacher/exams/<exam_id>" in reasons, (
            "a parameterised route has no URL a frame can load, so it belongs in "
            "the exclusion list rather than being dropped")
        assert reasons["/teacher/exams/<exam_id>"] == preview.NEEDS_AN_ID

    def test_the_machine_endpoints_are_named_as_such(self, app):
        reasons = {e["url"]: e for e in preview.preview_exclusions(app)}
        for url in ("/health", "/metrics"):
            assert url in reasons
            assert reasons[url]["reason_id"] and reasons[url]["reason_en"]

    def test_an_api_prefix_is_excluded_as_json(self, app):
        """A JSON endpoint under a role's own prefix is where this list earns it."""
        sample = next((e for e in preview.preview_exclusions(app)
                       if e["url"].startswith("/api/") and "<" not in e["url"]), None)
        assert sample is not None, "no /api/ route at all to check the prefix rule"
        assert sample["reason_id"] == preview.SKIP_PREFIX["/api/"][0]
        assert sample["reason_en"] == preview.SKIP_PREFIX["/api/"][1]

    def test_every_exclusion_explains_itself_in_both_languages(self, app):
        exclusions = preview.preview_exclusions(app)
        assert exclusions, "no exclusions at all means the list is not honest"
        for e in exclusions:
            assert e["reason_id"].strip(), f"{e['url']} has no Indonesian reason"
            assert e["reason_en"].strip(), f"{e['url']} has no English reason"

    def test_the_landing_page_is_never_skipped(self):
        """`SKIP_EXACT["/"] = None` means "never skipped", not "no reason"."""
        assert preview.skip_reason("/") is None
        assert "/" in {p["url"] for p in preview.preview_pages(create_app("testing"))}


# ── the page and the module are one contract ─────────────────────

class TestThePageRendersWhatTheServiceBuilds:
    def test_the_route_passes_the_exclusions(self):
        route = TOOLS_ROUTE.read_text(encoding="utf-8")
        assert "preview_exclusions(" in route and "exclusions=preview_exclusions(" in route, (
            "the route must hand the exclusions to the template — a service that "
            "computes them for nobody is the silent omission again")

    def test_the_template_shows_each_one_with_its_reason(self):
        html = PAGE_TEMPLATE.read_text(encoding="utf-8")
        assert "exclusions | tojson" in html, "the exclusions never reach the page"
        assert "EXCLUSIONS" in html and "x-for=\"ex in EXCLUSIONS\"" in html
        # The reason is chosen by the same language the rest of the page uses, so
        # both halves have to be in the binding.
        assert "ex.reason_id" in html and "ex.reason_en" in html

    def test_the_queried_page_is_passed_as_json_not_into_a_quote(self):
        """The initial page comes from `?page=`, and it lands in a JS expression.

        `x-data="devicePreview('{{ selected }}')"` looks escaped and is not: the
        HTML parser decodes `&#39;` back to a quote before Alpine reads the
        attribute, so a crafted link ran its own code on a staff page. JSON
        encoding keeps the payload inside one string literal.
        """
        html = PAGE_TEMPLATE.read_text(encoding="utf-8")
        assert "x-data='devicePreview({{ selected | tojson }})'" in html, (
            "the initial page must be JSON-encoded *and* the attribute single-quoted: "
            "Flask's tojson leaves `\"` unescaped, so a double-quoted attribute is "
            "closed by the JSON's own quote")
        assert "devicePreview('{{ selected }}')" not in html, (
            "the query parameter is back inside a quote — HTML escaping does not "
            "protect a JavaScript context")

    def test_a_quote_in_the_query_parameter_cannot_end_the_expression(self, app):
        hostile = "/teacher/dashboard');alert(1)//"
        response = get_as(app, "guru", PAGE_URL + "?page=" + quote(hostile, safe=""))
        assert response.status_code == 200

        body = response.get_data(as_text=True)
        # The first `x-data` on the page is base.html's own scope, so start at the
        # one this page owns.
        assert "x-data='devicePreview(" in body
        attribute = body.split("x-data='", 1)[1]
        attribute = attribute.split("'", 1)[0]
        # The payload sits *inside* a JSON string literal, which is the only shape
        # that cannot become code; and `'` is escaped as `\u0027`, so it cannot end
        # the single-quoted attribute either.
        assert attribute.startswith('devicePreview("/teacher/dashboard'), (
            f"the parameter is not JSON-encoded: {attribute!r}")
        assert "\\u0027" in attribute, "the quote in the payload is not escaped"

    def test_the_component_does_not_shadow_the_base_language(self):
        """A page that declares its own `lang` freezes in the language it loaded in.

        `lang` lives in base.html's scope. Declaring it here *shadows* it: the
        header button keeps flipping the label, every binding on this page keeps
        reading the shadowing copy, and nothing raises — the page just stays in
        the wrong language. The watch has to read the inherited value, so the
        component carries `uiLang` instead.
        """
        html = PAGE_TEMPLATE.read_text(encoding="utf-8")
        assert "uiLang:" in html and "uiLang === 'id'" in html
        assert "this.$watch('lang'" in html, (
            "the rebuild hook must watch the inherited `lang`")
        # No `lang:` property of our own, and nothing read off one either.
        assert not re.search(r"^\s*lang:\s", html, re.M), (
            "the component declares `lang`, shadowing base.html's scope")
        assert "this.lang" not in html, "a method reads the shadowing property"

    def test_every_width_has_a_name_in_both_languages(self):
        for width in preview.DEVICE_WIDTHS:
            id_name, en = preview.DEVICE_NAMES[width]
            assert id_name and en, width


# ── staff only ───────────────────────────────────────────────────

class TestOnlyStaffReachIt:
    @pytest.mark.parametrize("role", ["guru", "admin_sekolah", "super_admin"])
    def test_staff_get_the_page(self, app, role):
        response = get_as(app, role)
        assert response.status_code == 200
        assert b"devicePreview(" in response.data, "the page rendered without its scope"

    def test_a_student_is_refused(self, app):
        response = get_as(app, "murid")
        assert response.status_code == 403, (
            "an internal tool is for staff; a pupil sitting an exam must not be "
            "able to open one")

    def test_an_anonymous_visitor_is_sent_to_the_door(self, app):
        response = app.test_client().get(PAGE_URL)
        assert response.status_code in (301, 302, 303)
        assert "/auth/" in response.headers.get("Location", "")

    def test_the_page_is_bilingual(self):
        """The contract list is the guard; this says which entry it is."""
        from tests.unit import test_language_toggle as toggle
        assert "tools/device_preview.html" in toggle.TRANSLATED
