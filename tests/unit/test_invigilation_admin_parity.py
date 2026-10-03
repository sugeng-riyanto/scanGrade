"""The school admin builds the same invigilation matrix the deputy does.

The schedule was built for the vice principal, and only the vice principal. A school
small enough that it never made a deputy account then had no way to staff a sitting
at all — the page exists, the service exists, and the door is on someone else's
prefix. That is the same gap the assessment calendar had, and it is closed the same
way: one page for two writers, with the form's own action coming from the caller
instead of being written dead into the template.

Two properties are pinned, and neither is "a button exists":

* **authority** — every write lives on `/admin-sekolah/*` behind
  ``@admin_sekolah_required``, so the page drawing the form is not what grants it;
* **one page** — the admin door renders ``principal/invigilation.html`` with the
  admin's own base, so the two writers cannot drift into two tables that disagree
  about who is on duty.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN_ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"
PRINCIPAL_ROUTES = ROOT / "app" / "routes" / "principal.py"
PAGE = ROOT / "app" / "templates" / "principal" / "invigilation.html"
BASE = ROOT / "app" / "templates" / "base.html"

#: The admin blueprint carries the `/admin-sekolah` prefix, so these are the route
#: strings as they appear in the decorator — the prefix is added at registration.
ADMIN_WRITES = (
    '"/invigilation/save"',
    '"/invigilation/<schedule_id>/assign"',
    '"/invigilation/assignments/<assignment_id>/remove"',
    '"/retake-requests/<request_id>/decide"',
)


def _route_blocks(source: str) -> list[str]:
    """Each route from its own decorator up to the next one."""
    starts = [m.start() for m in re.finditer(r"@\w*bp\.route\(", source)]
    starts.append(len(source))
    return [source[a:b] for a, b in zip(starts, starts[1:])]


class TestTheAdminHasItsOwnDoor:
    def test_the_admin_may_open_the_schedule(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        assert '"/invigilation"' in source, (
            "the admin has no invigilation door, so a school without a deputy cannot staff a sitting")
        block = next(b for b in _route_blocks(source)
                     if '"/invigilation"' in b and "def " in b)
        assert "@admin_sekolah_required" in block

    def test_every_write_is_on_the_admins_own_prefix(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        for path in ADMIN_WRITES:
            assert path in source, f"{path} is missing"
            block = next(b for b in _route_blocks(source)
                         if f"@admin_sekolah_bp.route({path}" in b)
            assert "methods=[\"POST\"]" in block, path
            assert "@admin_sekolah_required" in block, (
                f"{path} writes without the admin guard")
            assert "@vice_principal_required" not in block, (
                f"{path} kept the deputy's guard, so the admin door refuses")

    def test_the_admin_page_is_the_deputy_page(self):
        """One template, so the two writers cannot describe two different schedules."""
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        assert 'render_template(\n        "principal/invigilation.html"' in source or \
            '"principal/invigilation.html"' in source, (
            "the admin renders a second schedule page instead of the shared one")

    def test_no_admin_write_reads_the_school_from_the_request(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        for path in ADMIN_WRITES:
            block = next(b for b in _route_blocks(source)
                         if f"@admin_sekolah_bp.route({path}" in b)
            for stolen in ('request.form.get("school_id")',
                           'request.args.get("school_id")',
                           'request.values.get("school_id")'):
                assert stolen not in block, f"{path} reads the school from the request"


class TestTheFormKnowsItsWriter:
    def test_the_page_does_not_hardcode_the_deputys_prefix(self):
        """The deputy's paths are what the page was born with; the admin's must come
        from the caller, or the admin's form posts into a route that refuses it."""
        page = PAGE.read_text(encoding="utf-8")
        assert "invigilation_base" in page, (
            "the page still writes the deputy's prefix dead into its forms")
        # The literal deputy prefix may survive only as the default the caller passes.
        assert 'action="/vice-principal/invigilation/save"' not in page, (
            "the save form still points at the deputy's prefix")

    def test_the_caller_supplies_the_base(self):
        for path in (PRINCIPAL_ROUTES, ADMIN_ROUTES):
            source = path.read_text(encoding="utf-8")
            assert "invigilation_base" in source, (
                f"{path.name} renders the page without telling it where its forms post")


class TestTheAdminCanFindIt:
    def test_the_admin_sidebar_links_the_schedule(self):
        base = BASE.read_text(encoding="utf-8")
        opening = re.search(r"<nav\b[^>]*>", base)
        end = base.find("</nav>", opening.end())
        pieces = re.split(r"\{%\s*(?:el)?if\s+g\.user_role\s*(?:==\s*'(?P<eq>\w+)'|"
                          r"in\s*\((?P<in>[^)]+)\))\s*%\}", base[opening.end():end])
        branch = ""
        for index in range(1, len(pieces), 3):
            if (pieces[index] or pieces[index + 1]) == "admin_sekolah":
                branch = pieces[index + 2]
        assert branch, "no admin_sekolah branch in the sidebar"
        assert 'href="/admin-sekolah/invigilation"' in branch, (
            "an operator still has to know the admin invigilation URL")
