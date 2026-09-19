"""One page, one URL — and the device preview is where the lie showed up.

`/tools/device-preview` derives its list from the app's own URL map, so a page
that exists under two prefixes appears twice: the school-admin section listed
`/admin/dashboard` *and* `/admin-sekolah/dashboard`, `/admin/students` *and*
`/admin-sekolah/students`, and so on for classes, teachers and messages. Two
buttons, one name each, and the only thing separating them was a `title` tooltip
— unreachable on the phone the preview exists to check.

The fix is a single table: `app/utils/legacy_urls.py` maps each moved URL to the
page that owns that content now, `create_app` registers a **308** per entry, and
the preview reads the *same* keys to exclude them. So these tests are about the
two ways that can rot:

  * a redirect that points somewhere that does not exist (a rename with no map
    entry), and
  * a page quietly reappearing under the legacy prefix, which would put the
    duplicate back on a page nobody re-reads.

308 rather than 301 is deliberate and asserted: it preserves the method, so a
bookmarked form POST is re-sent to the page that owns it rather than becoming a
GET that drops its body.

Mutation-checked, 5/5 injected defects caught: a legacy page route added back
under `/admin/`, the 308 downgraded to a 302, a redirect target renamed without
the map, the preview keeping its own copy of which URLs moved, and a guru-only
page filed back under the school-admin section.
"""
from pathlib import Path

import pytest

from app import create_app
from app.services import device_preview as preview
from app.utils.legacy_urls import LEGACY_ADMIN_PAGES, legacy_endpoint

ROOT = Path(__file__).resolve().parents[2]

# The two prefixes that own content: school-scoped, and platform-wide.
ROLE_PREFIXES = ("/admin-sekolah/", "/super-admin/")


@pytest.fixture(scope="module")
def app():
    return create_app("testing")


@pytest.fixture(scope="module")
def rules(app):
    """Rule -> every method it answers, *unioned* across rules with that path.

    `/admin/school` has two: the 308 for GET and the surviving handler for POST.
    A plain dict comprehension keeps only whichever the map happened to yield
    last, which would hide exactly the pair this fixture is needed to check.
    """
    methods = {}
    for rule in app.url_map.iter_rules():
        methods.setdefault(str(rule.rule), set()).update(rule.methods or set())
    return methods


def _slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


# ── the table and the router agree ───────────────────────────────

class TestTheTableIsTheRouteTable:
    def test_every_moved_url_is_registered(self, rules):
        missing = [old for old in LEGACY_ADMIN_PAGES if old not in rules]
        assert not missing, f"legacy_urls lists URLs the app does not route: {missing}"

    def test_every_canonical_target_exists(self, rules):
        """A rename that misses the table leaves a 308 pointing into nothing.

        That is worse than the duplicate it replaced: a duplicate is unusable, a
        broken redirect is a dead end that looks like a working link until it is
        followed.
        """
        missing = [new for new in LEGACY_ADMIN_PAGES.values() if new not in rules]
        assert not missing, f"redirect targets that are not routes: {missing}"

    def test_no_target_is_itself_a_legacy_url(self, rules):
        """No chains: a 308 to another 308 is two round-trips and two caches."""
        chained = [new for new in LEGACY_ADMIN_PAGES.values() if new in LEGACY_ADMIN_PAGES]
        assert not chained, f"legacy URLs redirecting to legacy URLs: {chained}"

    def test_every_target_lives_under_a_role_prefix(self, rules):
        """The canonical home is the role that owns the content, not `/admin`."""
        stray = [new for new in LEGACY_ADMIN_PAGES.values()
                 if not new.startswith(ROLE_PREFIXES)]
        assert not stray, f"redirect targets outside a role prefix: {stray}"


# ── the redirect itself ──────────────────────────────────────────

class TestTheOldUrlAnswers308:
    @pytest.mark.parametrize("old,new", sorted(LEGACY_ADMIN_PAGES.items()))
    def test_the_page_redirects_permanently_to_its_new_home(self, app, old, new):
        response = app.test_client().get(old)
        assert response.status_code == 308, (
            f"{old} answered {response.status_code}; 301 would turn a bookmarked "
            f"POST into a GET and drop its body, so the method must be preserved")
        assert response.headers["Location"] == new

    def test_a_query_string_survives_the_move(self, app):
        """Dropping `?page=3` lands the reader on a page that looks fine and is not."""
        response = app.test_client().get("/admin/compliance/logs?page=3&days=30")
        assert response.headers["Location"] == "/super-admin/logs?page=3&days=30"

    def test_the_write_that_stayed_is_still_routed(self, rules):
        """`/admin/school` moved as a *page*; its POST did not move.

        The GET rule is the 308, and it must not have taken the write with it —
        the legacy form's field names are not the new form's, so a re-posted body
        would land on a route that cannot read it.
        """
        assert "GET" in rules["/admin/school"]
        assert "POST" in rules["/admin/school"]


# ── the duplicate cannot come back ───────────────────────────────

class TestNoPageIsServedByTwoAdminPrefixes:
    def test_the_legacy_prefix_serves_nothing_a_role_prefix_serves(self, app):
        """The exact defect: `/admin/dashboard` and `/admin-sekolah/dashboard`.

        Both were listed by the preview and both rendered a dashboard. Compared by
        the last URL segment, which is what made them look like the same page to
        the person reading the preview — and which is what a new duplicate would
        look like again.
        """
        pages = [p["url"] for p in preview.preview_pages(app)]
        legacy = {_slug(u) for u in pages if u.startswith("/admin/")}
        canonical = {_slug(u) for u in pages if u.startswith(ROLE_PREFIXES)}

        assert not (legacy & canonical), (
            "one page is reachable under /admin/ and under a role prefix again: "
            f"{sorted(legacy & canonical)} — add it to LEGACY_ADMIN_PAGES and delete "
            "the legacy handler, or the device preview lists two of it")

    def test_the_preview_does_not_list_a_moved_url(self, app):
        listed = {p["url"] for p in preview.preview_pages(app)}
        assert not (listed & set(LEGACY_ADMIN_PAGES)), (
            "a 308 is in the preview's page list — it would render as a blank frame")

    def test_the_school_admin_section_holds_only_school_admin_pages(self, app):
        """The second way this section filled up with two of everything.

        The `/admin/*` duplicates are 308s now, but the section still listed
        `/students/import` beside `/admin-sekolah/import` — a guru-only page whose
        blueprint happens to sit under `/students/`, so it was filed with the
        school admin and read as a second Import. `_unique_labels` hid the
        collision by lengthening both names, which is why this is asserted here
        rather than left to the naming test.
        """
        section = next(grp for grp in preview.preview_sections(app) if grp["id"] == "admin")
        foreign = [p["url"] for p in section["pages"]
                   if not p["url"].startswith("/admin-sekolah/")]
        assert not foreign, (
            f"the school-admin section lists pages from another role's prefix: "
            f"{foreign} — file them in the section their role reads")


# ── and the preview says why they are missing ────────────────────

class TestThePreviewExplainsTheExclusions:
    def test_every_moved_url_is_excluded_with_a_reason(self, app):
        reasons = {e["url"]: (e["reason_id"], e["reason_en"])
                   for e in preview.preview_exclusions(app)}
        for old in LEGACY_ADMIN_PAGES:
            assert old in reasons, (
                f"{old} is neither rendered nor explained — the only way the page "
                "can lie about what it covers")
            assert reasons[old] == preview.LEGACY_PAGE_REASON

    def test_the_reason_is_bilingual(self):
        assert all(part.strip() for part in preview.LEGACY_PAGE_REASON)

    def test_the_redirect_and_the_exclusion_come_from_one_table(self):
        """Structural: a second copy of this list is how the two drift.

        The preview must *read* the table rather than restate it, so a path cannot
        be registered as a redirect here and advertised as a page there.
        """
        source = (ROOT / "app" / "services" / "device_preview.py").read_text(encoding="utf-8")
        assert "LEGACY_ADMIN_PAGES" in source, (
            "the preview no longer reads the redirect table — it is keeping its own "
            "copy of which URLs moved")
        for old in LEGACY_ADMIN_PAGES:
            assert f'"{old}"' not in source, (
                f"{old} is hardcoded in the preview as well as in legacy_urls")

    def test_the_endpoint_names_are_unique(self):
        names = [legacy_endpoint(old) for old in LEGACY_ADMIN_PAGES]
        assert len(names) == len(set(names)), (
            "two moved URLs share an endpoint name, so one silently replaces the "
            "other in `view_functions`")
