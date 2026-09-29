"""The school admin reads the school's reports at the school's own address.

`/admin-sekolah` is the school's area: its dashboard, its children, its teachers,
its subscription. The two report pages were the exception. The sidebar filed
"School Analytics" and "School Reports" under the admin's own *Reports* section
while the links went to `/teacher/analytics` and `/teacher/reports`, so the menu
entry, the address in the bar and the parent in the breadcrumb named three
different areas — and the one a school admin had to read was a teacher's.

The two oversight roles already read these pages at their own door
(`/principal/analytics`, `/vice-principal/analytics`); this is the school admin's.
The pages themselves are *shared*, not copied: `teacher/reports.html` and
`teacher/analytics.html` are rendered with their base paths pointed at the reader,
which is what keeps the school's numbers and the teacher's numbers one report.

Five things have to hold for this to be a move rather than a second link:

* the pages are **served** at `/admin-sekolah/reports` and `/admin-sekolah/analytics`
  (with the three exports beside the analytics page), and only an `admin_sekolah`
  may open them;
* the **sidebar** names that address, once, under the Reports section, bilingual,
  and no longer names the teacher's address in the admin's branch;
* the **breadcrumb** of each address is the admin area, and no crumb on it points
  into the teacher's area;
* the teacher hubs **no longer admit** `admin_sekolah`, so the old address is gone
  rather than merely less convenient;
* the reports template's own form and its reset link are built from the reader's
  base path, so the admin typing a date range is not sent back to a teacher page.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
BASE = (TEMPLATES / "base.html").read_text(encoding="utf-8")
REPORTS_HTML = (TEMPLATES / "teacher" / "reports.html").read_text(encoding="utf-8")
TEACHER_SRC = ROOT / "app" / "routes" / "teacher.py"
ADMIN_SRC = ROOT / "app" / "routes" / "admin_sekolah.py"

HUB = "/admin-sekolah/reports"
ANALYTICS = "/admin-sekolah/analytics"
EXPORTS = (f"{ANALYTICS}/download.csv",
           f"{ANALYTICS}/download.pdf",
           f"{ANALYTICS}/print")

#: The views that answer the five addresses, by the names the module gives them.
VIEWS = ("admin_reports", "admin_analytics", "admin_analytics_csv",
         "admin_analytics_pdf", "admin_analytics_print")


def _sidebar_blocks() -> dict[str, str]:
    """Each role's branch of the desktop sidebar, one branch per role."""
    opening = re.compile(r"<nav\b[^>]*>").search(BASE)
    end = BASE.find("</nav>", opening.end())
    parts = re.split(r"\{%\s*(?:el)?if\s+g\.user_role\s*==\s*'(\w+)'\s*%\}",
                     BASE[opening.end():end])
    return dict(zip(parts[1::2], parts[2::2]))


def _decorators(name: str) -> str:
    """The decorator text above one view in `admin_sekolah.py`."""
    source = ADMIN_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = source.splitlines()
    first = min(d.lineno for d in node.decorator_list)
    return "\n".join(lines[first - 1:node.lineno - 1])


def _seen(app, path: str, role: str = "admin_sekolah"):
    """A request context with the reader signed in, the way the route expects."""
    from flask import g

    context = app.test_request_context(path)
    context.push()
    g.user_id = "adm-1"
    g.user_name = "Admin Uji"
    g.user_email = "admin@example.test"
    g.user_role = role
    g.user_school_id = "sch-1"
    g.tz_offset = 7
    return context


# ── the addresses ────────────────────────────────────────────────────────────

class TestThePagesAreServedUnderTheAdminArea:
    @pytest.mark.parametrize("path", (HUB, ANALYTICS, *EXPORTS))
    def test_the_address_is_a_page_the_app_answers(self, app, path):
        rules = {str(rule.rule) for rule in app.url_map.iter_rules()}
        assert path in rules, (
            f"{path} is not a route — the sidebar would open a 404 in the "
            f"school's own area")

    def test_the_index_and_the_statistics_page_are_on_the_admin_blueprint(self):
        source = ADMIN_SRC.read_text(encoding="utf-8")
        for route in (HUB, ANALYTICS, *EXPORTS):
            literal = route[len("/admin-sekolah"):]
            assert f'@admin_sekolah_bp.route("{literal}")' in source, (
                f"{route} is not mounted on the school admin's blueprint")

    @pytest.mark.parametrize("view", VIEWS)
    def test_only_a_school_admin_may_open_them(self, view):
        block = _decorators(view)
        assert "admin_sekolah_required" in block, (
            f"{view} has no role guard: any signed-in member of the school "
            f"could read every mark in it\n{block}")
        assert "@login_required" not in block, (
            "a session-only guard answers 'is somebody signed in', and every "
            "signed-in member answers yes — a murid included")

    def test_the_guard_refuses_the_other_roles(self):
        """Read from the predicate the decorator itself asks, so this cannot
        pass while the route admits somebody else."""
        from app.utils.auth import _check_roles

        assert _check_roles("admin_sekolah", ("admin_sekolah",))
        for role in ("guru", "murid", "principal", "vice_principal", "super_admin"):
            assert not _check_roles(role, ("admin_sekolah",)), (
                f"{role} must not pass admin_sekolah_required")


# ── the menu entries ─────────────────────────────────────────────────────────

class TestTheSidebarNamesTheAdminArea:
    def test_the_admin_branch_offers_the_admin_addresses(self):
        branch = _sidebar_blocks()["admin_sekolah"]
        for path in (HUB, ANALYTICS):
            assert branch.count(f'href="{path}"') == 1, (
                f"the admin sidebar offers {path} "
                f"{branch.count(f'href=\"{path}\"')} times")

    def test_it_no_longer_offers_the_teachers_addresses(self):
        """The defect this request is about: the entry sat in the admin's own
        Reports section and opened a teacher's URL."""
        branch = _sidebar_blocks()["admin_sekolah"]
        for stale in ("/teacher/reports", "/teacher/analytics"):
            assert f'href="{stale}"' not in branch, (
                f"the admin sidebar still opens {stale}")

    def test_both_entries_are_bilingual(self):
        branch = _sidebar_blocks()["admin_sekolah"]
        for path in (HUB, ANALYTICS):
            link = branch.split(f'href="{path}"', 1)[1].split("</a>", 1)[0]
            assert re.search(r"t\('[^']+','[^']+'\)", link), (
                f"the {path} entry is not a bilingual pair: {link}")

    def test_they_sit_under_the_reports_section(self):
        branch = _sidebar_blocks()["admin_sekolah"]
        for path in (HUB, ANALYTICS):
            before = branch.split(f'href="{path}"', 1)[0]
            section = re.findall(
                r"nav-section-title[^>]*>\s*<span[^>]*x-text=\"t\('([^']+)'", before)
            assert section[-1] in ("Laporan", "Reports"), (
                f"the {path} entry is filed under {section[-1]!r}")

    def test_the_active_state_follows_the_new_address(self):
        branch = _sidebar_blocks()["admin_sekolah"]
        for path in (HUB, ANALYTICS):
            link = branch.split(f'href="{path}"', 1)[1].split("</a>", 1)[0]
            assert f"request.path == '{path}'" in link, (
                f"the {path} entry does not highlight itself, so a reader on "
                f"the page sees no menu item lit")


# ── the trail ────────────────────────────────────────────────────────────────

class TestTheBreadcrumbNamesTheAdminArea:
    @pytest.mark.parametrize("path,key", ((HUB, "reports"), (ANALYTICS, "analytics")))
    def test_the_trail_starts_at_the_admin_area(self, path, key):
        from app.utils import breadcrumbs

        trail = breadcrumbs.trail(path, "admin_sekolah")
        assert trail and trail[0]["kind"] == "area"
        assert trail[0]["key"] == "admin_sekolah", (
            f"{path} names {trail[0]['key']!r} as the area")
        assert trail[0]["path"] == "/admin-sekolah/dashboard"
        assert trail[-1]["key"] == key and trail[-1]["current"], (
            f"{path} ends at {trail[-1]['key']!r}, not the page being read")

    @pytest.mark.parametrize("path", (HUB, ANALYTICS))
    def test_no_crumb_points_into_the_teachers_area(self, path):
        from app.utils import breadcrumbs

        stray = [c["path"] for c in breadcrumbs.trail(path, "admin_sekolah")
                 if str(c.get("path", "")).startswith("/teacher")]
        assert not stray, (
            f"{path}'s trail walks into the teacher's area: {stray}")

    def test_the_words_are_in_the_templates_vocabulary(self):
        """The safety net in the chrome is `title()` of the URL — an English
        word in the one piece of chrome that follows the toggle. Both segments
        must be named, not fallen back to."""
        start = BASE.index("{% set _crumb_labels = {")
        labels = BASE[start:BASE.index("} %}", start)]
        for key in ("'reports'", "'analytics'"):
            assert key in labels, (
                f"{key} is not in the breadcrumb vocabulary, so the trail would "
                f"print the slug title-cased")


# ── the teacher's own door no longer admits the school admin ─────────────────

class TestTheTeacherHubNoLongerAdmitsTheSchoolAdmin:
    """A second, equally working address is how a move becomes a preference:
    it is only real once the old one refuses."""

    @pytest.mark.parametrize("route", ["/reports", "/analytics",
                                       "/analytics/download.csv",
                                       "/analytics/download.pdf",
                                       "/analytics/print"])
    def test_the_route_admits_only_the_roles_without_a_door_of_their_own(self, route):
        source = TEACHER_SRC.read_text(encoding="utf-8")
        block = source.split(f'@teacher_bp.route("{route}")', 1)
        assert len(block) == 2, f"route {route} is gone"
        head = block[1][:400]
        match = re.search(r"@role_required\(([^)]*)\)", head)
        assert match, f"{route} lost its role guard"
        admitted = set(re.findall(r'"(\w+)"', match.group(1)))
        assert "admin_sekolah" not in admitted, (
            f"{route} still admits the school admin, so the school's own "
            f"address is an alternative rather than the address")
        assert admitted == {"guru", "super_admin"}, (
            f"{route} admits {sorted(admitted)}")


# ── the shared pages keep their own base path ────────────────────────────────

def _empty_report() -> dict:
    return {"rows": [], "bins": [], "lang": "id",
            "totals": {"exams": 0, "participants": 0, "mean": 0,
                       "pass_rate": 0, "sd": 0}}


class TestTheSharedPagePointsAtItsReader:
    def test_the_reports_template_takes_its_own_base_path(self):
        assert "reports_base|default('/teacher/reports'" in REPORTS_HTML, (
            "the reports index hardcodes its address, so an admin's filter form "
            "would submit back to a teacher page")
        assert 'action="/teacher/reports"' not in REPORTS_HTML
        assert '/teacher/reports?lang=' not in REPORTS_HTML

    def test_the_reports_route_hands_the_page_the_admin_base(self, app, monkeypatch):
        import app.routes.admin_sekolah as admin
        import app.services.analysis_scope as scope

        handed: dict = {}
        monkeypatch.setattr(admin, "get_supabase", lambda: object())
        monkeypatch.setattr(admin, "_scope_report", lambda *a, **k: _empty_report())
        monkeypatch.setattr(admin, "render_template",
                            lambda name, **kw: handed.update({"name": name, **kw}) or "")
        monkeypatch.setattr(scope, "scope_choices",
                            lambda *a, **k: {"schools": [], "teachers": []})
        monkeypatch.setattr(scope, "learners_in_scope", lambda *a, **k: [])

        context = _seen(app, HUB)
        try:
            inspect.unwrap(admin.admin_reports)()
        finally:
            context.pop()

        assert handed["name"] == "teacher/reports.html"
        assert handed.get("reports_base") == HUB, (
            f"the admin index was handed {handed.get('reports_base')!r} as its base")

    def test_the_analytics_route_hands_the_page_the_admin_base(self, app, monkeypatch):
        import app.routes.admin_sekolah as admin

        handed: dict = {}
        monkeypatch.setattr(admin, "get_supabase", lambda: object())
        monkeypatch.setattr(admin, "_scope_report", lambda *a, **k: _empty_report())
        monkeypatch.setattr(admin, "render_template",
                            lambda name, **kw: handed.update({"name": name, **kw}) or "")

        context = _seen(app, ANALYTICS)
        try:
            inspect.unwrap(admin.admin_analytics)()
        finally:
            context.pop()

        assert handed["name"] == "teacher/analytics.html"
        assert handed.get("analysis_base") == ANALYTICS, (
            f"the admin statistics page was handed "
            f"{handed.get('analysis_base')!r} as its base")

    def test_the_two_bases_are_the_two_addresses_the_tests_watch(self):
        """One constant each, so the sidebar, the index's form and the exports
        cannot name two different areas."""
        import app.routes.admin_sekolah as admin

        assert admin.ADMIN_REPORTS_BASE == HUB
        assert admin.ADMIN_ANALYTICS_BASE == ANALYTICS
