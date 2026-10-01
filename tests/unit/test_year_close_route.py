"""The wizard is reachable, guarded, and writes nothing without a confirmation.

A year-close screen is the one place where a stray request can move a whole
school's pupils, so the guards here are about the door rather than the maths: the
route exists, it is behind the school-admin role, the form carries the
confirmation the route reads, and the page is linked from where an admin looks.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"
TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "year_close.html"
YEARS_PAGE = ROOT / "app" / "templates" / "admin_sekolah" / "school_years.html"


def test_the_route_is_registered():
    src = ROUTES.read_text(encoding="utf-8")
    assert '@admin_sekolah_bp.route("/school-years/close", methods=["GET", "POST"])' in src
    assert "def close_school_year():" in src


def test_the_route_is_behind_the_school_admin_role():
    src = ROUTES.read_text(encoding="utf-8")
    # the guard sits directly under the route decorator
    between = src.rsplit('@admin_sekolah_bp.route("/school-years/close"', 1)[1]
    assert "@admin_sekolah_required" in between.split("def close_school_year")[0]


def test_the_form_confirms_before_it_writes():
    page = TEMPLATE.read_text(encoding="utf-8")
    assert 'name="confirmed" value="1"' in page, (
        "the announce step and the write step are the same request without this")
    # The announce form (the one that only previews) must NOT carry `confirmed`,
    # or the first click would close the year on an un-reviewed plan.
    announce = page.split("Lihat Rencana")[0].rsplit("<form", 1)[-1]
    assert 'name="confirmed"' not in announce


def test_the_form_offers_the_four_outcomes_per_pupil():
    page = TEMPLATE.read_text(encoding="utf-8")
    assert 'name="outcome_{{ r.student_id }}"' in page
    for status in ("naik", "tinggal_kelas", "lulus", "pindah"):
        assert 'value="%s"' % status in page, status


def test_the_page_is_linked_from_the_academic_years_page():
    page = YEARS_PAGE.read_text(encoding="utf-8")
    assert "/admin-sekolah/school-years/close" in page


def test_the_template_compiles(app):
    """A Jinja syntax error is a 500 on the one page that runs once a year."""
    app.jinja_env.get_template("admin_sekolah/year_close.html")


def test_the_route_closes_only_after_recording():
    """Order matters: closing first and failing halfway strands the pupils."""
    src = ROUTES.read_text(encoding="utf-8")
    body = src.split("def close_school_year(")[1].split("\n# \u2500\u2500\u2500")[0]
    apply_at = body.index("academic_year.apply_close(")
    close_at = body.index("academic_year.close_year(")
    assert apply_at < close_at, (
        "the old year is closed before the pupils are recorded; a failure in "
        "between would leave the school with a closed year and no enrollments")
