"""The sidebar reads in the order the job happens, and no role repeats a heading.

The menus were a list of pages in the order pages were built. A teacher met
"AI Grading" in its own section between building a paper and marking it, met
"Subjects" under *Tools* at the very bottom, and read the scoring guide before the
pages they use every day. A school admin had two sections both called "Academic"
with the subject list stranded between them; the super admin had two called "Data",
kept Privacy & Compliance under *Subscription*, and parked the destructive Reset
School inside *Tools*.

Reordering is invisible to every other guard — a menu that lost its shape still
renders — so the shape is pinned here: for each role, the sections in order and the
links under each one, in order. A link that moves, a heading that is duplicated, or
a section emptied out fails this file.

What this deliberately does *not* check is the words: `test_language_toggle.py`
owns the bilingual pairs, and `test_admin_reports_home.py` owns the admin's report
addresses.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")

#: The job-flow order each role should read, section heading then hrefs. The
#: teacher reads what they teach, then builds a paper, then marks it, then reads
#: the result — the order the work actually happens.
EXPECTED: dict[str, list[tuple[str, list[str]]]] = {
    "guru": [
        ("Utama", ["/teacher/dashboard", "/teacher/classes", "/teacher/subjects"]),
        ("Ujian", ["/teacher/exams", "/teacher/exams/new"]),
        ("Koreksi", ["/teacher/scan", "/teacher/grading", "/teacher/ai-settings",
                     "/teacher/retractions", "/teacher/penalty-appeals"]),
        ("Laporan", ["/teacher/results", "/teacher/analytics", "/teacher/reports"]),
        ("Komunikasi", ["/teacher/comms"]),
        ("Alat", ["/tools/generate-answer-sheet", "/tools/device-preview"]),
        ("Panduan", ["/guide/skor"]),
        ("Pengaturan", ["/teacher/settings"]),
    ],
    "admin_sekolah": [
        ("Utama", ["/admin-sekolah/dashboard", "/admin-sekolah/profile"]),
        ("Akademik", ["/admin-sekolah/school-years", "/admin-sekolah/classes",
                      "/admin-sekolah/subjects", "/admin-sekolah/promote",
                      "/admin-sekolah/invigilation"]),
        ("Pengguna", ["/admin-sekolah/teachers", "/admin-sekolah/students",
                      "/admin-sekolah/officials"]),
        ("Data", ["/admin-sekolah/import", "/admin-sekolah/accounts"]),
        ("Laporan", ["/admin-sekolah/analytics", "/admin-sekolah/reports"]),
        ("Komunikasi", ["/admin-sekolah/comms"]),
        ("Langganan", ["/admin-sekolah/subscription", "/admin-sekolah/invoices"]),
        ("Alat", ["/tools/generate-answer-sheet", "/tools/device-preview"]),
        ("Panduan", ["/guide/skor"]),
    ],
    "super_admin": [
        ("Super Admin", ["/super-admin/dashboard"]),
        ("Sekolah", ["/super-admin/schools", "/admin/registration-requests"]),
        ("Data", ["/super-admin/users", "/super-admin/exams",
                  "/super-admin/users/manage", "/super-admin/logs",
                  "/super-admin/feature-flags"]),
        ("Laporan", ["/teacher/analytics", "/teacher/reports"]),
        ("Langganan", ["/super-admin/midtrans", "/super-admin/activation-codes",
                       "/super-admin/plans", "/super-admin/pricing-settings",
                       "/super-admin/payment-fee-settings",
                       "/super-admin/trial-settings"]),
        ("Komunikasi", ["/super-admin/email-settings",
                        "/super-admin/whatsapp-settings", "/super-admin/comms"]),
        ("Demo", ["/super-admin/demo-settings"]),
        ("Alat", ["/tools/generate-answer-sheet", "/tools/device-preview"]),
        ("Panduan", ["/guide/skor"]),
        ("Sistem", ["/super-admin/privacy-settings", "/super-admin/file-management",
                    "/super-admin/omr-test", "/super-admin/deploy-status",
                    "/super-admin/reset-school-data"]),
    ],
    "murid": [
        ("Utama", ["/student/dashboard"]),
        ("Ujian", ["/student/exams", "/student/results"]),
        ("Panduan", ["/guide/skor"]),
        ("Komunikasi", ["/student/comms"]),
        ("Pengaturan", ["/student/settings"]),
    ],
}

#: The key `_split` files the shared officials branch under.
OFFICIAL = "'principal', 'vice_principal'"
OFFICIAL_HEADINGS = ["Utama", "Panduan"]

TITLE_RE = re.compile(r'nav-section-title[^>]*>\s*<span[^>]*x-text="t\(\'([^\']+)\'')
LINK_RE = re.compile(r'<a href="([^"]+)"')
MARKER_RE = re.compile(
    r"\{%\s*(?:el)?if\s+g\.user_role\s*(?:==\s*'(?P<eq>\w+)'|"
    r"in\s*\((?P<in>[^)]+)\))\s*%\}")


def _split() -> dict[str, str]:
    """role -> its slice of the desktop sidebar, cut at the next role marker."""
    opening = re.search(r"<nav\b[^>]*>", BASE)
    end = BASE.find("</nav>", opening.end())
    pieces = MARKER_RE.split(BASE[opening.end():end])
    roles: dict[str, str] = {}
    # split() keeps the two groups, so: [before, eq, in, text, eq, in, ...].
    for index in range(1, len(pieces), 3):
        eq, in_clause, text = pieces[index], pieces[index + 1], pieces[index + 2]
        roles[eq or in_clause] = text
    return roles


def _sections(branch: str) -> list[tuple[str, list[str]]]:
    """[(heading, [hrefs])], with the never-rendered whiteboard block cut off."""
    cut = branch.find("{% set _feat")
    if cut != -1:
        branch = branch[:cut]
    sections: list[tuple[str, list[str]]] = []
    for line in branch.splitlines():
        title = TITLE_RE.search(line)
        if title:
            sections.append((title.group(1), []))
            continue
        link = LINK_RE.search(line)
        if link and sections:
            sections[-1][1].append(link.group(1))
    return sections


def _report(sections: list[tuple[str, list[str]]]) -> str:
    return "\n".join(f"  {head}: {hrefs}" for head, hrefs in sections)


class TestEveryRoleReadsItsOwnOrder:
    @pytest.mark.parametrize("role", sorted(EXPECTED))
    def test_sections_and_links_are_in_the_pinned_order(self, role):
        got = _sections(_split()[role])
        assert got == EXPECTED[role], (
            f"the {role} sidebar lost its shape:\n"
            f"expected:\n{_report(EXPECTED[role])}\ngot:\n{_report(got)}")


class TestNoRoleRepeatsAHeading:
    @pytest.mark.parametrize("role", sorted(EXPECTED) + [OFFICIAL])
    def test_a_heading_appears_once_in_a_branch(self, role):
        heads = [head for head, _ in _sections(_split()[role])]
        assert len(heads) == len(set(heads)), (
            f"the {role} sidebar repeats a section heading: {heads}")

    def test_every_section_has_at_least_one_link(self):
        for role in EXPECTED:
            for head, hrefs in _sections(_split()[role]):
                assert hrefs, f"{role}: the {head!r} section is empty"


class TestTheSpecificDefectsStayClosed:
    def test_the_teacher_marks_papers_in_one_section(self):
        """AI grading is grading: it used to sit in a section of its own between
        building the paper and marking it."""
        sections = dict(_sections(_split()["guru"]))
        for href in ("/teacher/scan", "/teacher/grading", "/teacher/ai-settings"):
            assert href in sections["Koreksi"], f"{href} is not under Koreksi"

    def test_the_teachers_subjects_sit_with_what_they_teach_not_under_tools(self):
        sections = dict(_sections(_split()["guru"]))
        assert "/teacher/subjects" in sections["Utama"], (
            "the teacher's subject list is filed away from their teaching scope")
        assert "/teacher/subjects" not in sections.get("Alat", [])

    def test_the_admin_has_one_academic_section(self):
        heads = [head for head, _ in _sections(_split()["admin_sekolah"])]
        assert heads.count("Akademik") == 1, heads

    def test_the_super_admin_has_one_data_section(self):
        heads = [head for head, _ in _sections(_split()["super_admin"])]
        assert heads.count("Data") == 1, heads

    def test_the_destructive_reset_is_last_and_not_under_tools(self):
        sections = dict(_sections(_split()["super_admin"]))
        assert "/super-admin/reset-school-data" not in sections.get("Alat", []), (
            "Reset School is offered inside Tools, beside the answer-sheet printer")
        order = [href for _h, hrefs in _sections(_split()["super_admin"])
                 for href in hrefs]
        assert order[-1] == "/super-admin/reset-school-data", (
            "the destructive entry is not the last thing in the menu")

    def test_the_officials_share_one_short_branch(self):
        heads = [head for head, _ in _sections(_split()[OFFICIAL])]
        assert heads == OFFICIAL_HEADINGS, heads
