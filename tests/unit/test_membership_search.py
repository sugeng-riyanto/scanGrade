"""Fase 6 — the destination search, the queue, and where a decision may be taken.

The rules under test are the ones a page can get wrong while looking right:

* a school whose classes have not been registered yet is **listed**, with the stage
  said in words. A hidden school cannot be applied to, and a brand-new destination is
  exactly the one likeliest to have no classes — so the row is kept and the *stage*
  is what is missing;
* a class read that fails costs every school its stage and nothing else; a school
  read that fails reports nothing rather than a partial list;
* the decision belongs to the **destination** school and to three roles, which is
  the same three the schema's `decided_role` CHECK allows — asserted against the SQL,
  because two vocabularies for one authority is how a decision gets recorded under a
  role the database will not accept.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import school_membership as membership
from tests.unit.membership_fixtures import FakeDb, membership as mrow, person, school

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_063 = (ROOT / "supabase" / "migrations"
                 / "063_school_membership_requests.sql")
TEMPLATES = ROOT / "app" / "templates"


# ── the stage a school serves, from its own classes ──────────────────────────

class TestTheStageComesFromTheSchoolsOwnClasses:
    def test_roman_and_arabic_grades_read_the_same(self):
        assert membership.jenjang_label(["VII"]) == "SMP"
        assert membership.jenjang_label(["7"]) == "SMP"
        assert membership.jenjang_label(["vii"]) == "SMP"

    def test_a_school_that_spans_stages_says_both(self):
        assert membership.jenjang_label(["6", "7"]) == "SD+SMP"
        assert membership.jenjang_label(["10", "11"]) == "SMA"

    def test_no_classes_is_none_and_not_a_guess(self):
        """`None` is a real state: the page turns it into words, not into a blank."""
        assert membership.jenjang_label([]) is None
        assert membership.jenjang_label(None) is None
        assert membership.jenjang_label(["", "   "]) is None

    def test_a_grade_outside_the_ranges_is_reported_as_itself(self):
        assert membership.jenjang_label(["13"]) == "13"


# ── the search list ──────────────────────────────────────────────────────────

class TestNoSchoolIsHidden:
    def test_a_school_with_no_classes_is_listed_without_a_stage(self):
        db = FakeDb(schools=[school("sc-new", name="SMA Negeri Baru")],
                    classes=[], teacher_school_membership=[])
        found = membership.school_search(db, "")
        assert [s["name"] for s in found] == ["SMA Negeri Baru"]
        assert found[0]["jenjang"] is None

    def test_a_class_read_that_fails_costs_every_school_its_stage_only(self):
        db = FakeDb(schools=[school("sc-1", name="SMP A"), school("sc-2", name="SMA B")],
                    classes=[{"school_id": "sc-1", "grade_level": "7"}])
        db.fail.add("classes")
        found = membership.school_search(db, "")
        assert sorted(s["name"] for s in found) == ["SMA B", "SMP A"]
        assert all(s["jenjang"] is None for s in found)

    def test_a_school_read_that_fails_reports_nothing(self):
        db = FakeDb(schools=[school("sc-1")])
        db.fail.add("schools")
        assert membership.school_search(db, "") == []

    def test_a_school_the_teacher_belongs_to_is_a_flag_not_a_removal(self):
        db = FakeDb(schools=[school("sc-1"), school("sc-2", npsn="87654321")],
                    classes=[], teacher_school_membership=[])
        found = membership.school_search(db, "", member_ids={"sc-1"})
        assert [s["name"] for s in found] == ["SMP Negeri 1", "SMP Negeri 1"]
        assert [s["member"] for s in found] == [True, False]

    def test_the_search_matches_name_npsn_and_city(self):
        db = FakeDb(schools=[school("sc-1", name="SMP Satu", npsn="11111111",
                                    city="Bandung"),
                             school("sc-2", name="SMA Dua", npsn="22222222",
                                    city="Surabaya")],
                    classes=[], teacher_school_membership=[])
        assert [s["id"] for s in membership.school_search(db, "surabaya")] == ["sc-2"]
        assert [s["id"] for s in membership.school_search(db, "2222")] == ["sc-2"]
        assert [s["id"] for s in membership.school_search(db, "smp saat")] == []
        assert len(membership.school_search(db, "")) == 2

    def test_the_limit_is_applied_to_the_filtered_list(self):
        db = FakeDb(schools=[school(f"sc-{i}", name=f"Sekolah {i}")
                             for i in range(5)],
                    classes=[], teacher_school_membership=[])
        assert len(membership.school_search(db, "", limit=2)) == 2


# ── the page a teacher actually opens ────────────────────────────────────────

@pytest.fixture()
def render_teacher_page(app):
    """Render the page the way its route does, with a request context for `url_for`."""
    def _render(**context):
        from flask import g

        with app.test_request_context("/teacher/membership"):
            g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
            g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
            g.user_school_id, g.user_class_id = "sch-1", None
            return app.jinja_env.get_template("teacher/membership.html").render(
                **context)
    return _render


def _teacher_context(schools, **extra):
    ind, eng = membership.consent_texts()
    return dict(schools=schools, my_requests=[], q="", status_code="",
                consent_id=ind, consent_en=eng,
                consent_version=membership.CONSENT_DOCUMENT_VERSION,
                consent_sha256=membership.consent_sha256(), **extra)


def test_the_page_keeps_a_classless_school_and_says_the_stage_is_unrecorded(
        render_teacher_page):
    """The fallback is on the *row*, next to the school it describes."""
    html = render_teacher_page(**_teacher_context([
        {"id": "sc-new", "name": "SMA Negeri Baru", "npsn": "99999999",
         "city": "Bogor", "status": "active", "jenjang": None, "member": False},
    ]))
    assert "SMA Negeri Baru" in html, (
        "a school with no registered classes was dropped from the results")
    assert "Jenjang belum terdata" in html and "Level not recorded yet" in html, (
        "the missing stage is not said in words, so the cell renders as an empty box")


def test_the_page_still_shows_a_stage_that_exists(render_teacher_page):
    html = render_teacher_page(**_teacher_context([
        {"id": "sc-1", "name": "SMP A", "npsn": "11111111", "city": "Bandung",
         "status": "active", "jenjang": "SMP", "member": False},
    ]))
    assert ">SMP<" in html
    assert "Jenjang belum terdata" not in html


def test_the_page_prints_the_consent_text_it_records(render_teacher_page):
    """The stored evidence is about the text the teacher was shown — so the page
    prints the service's own strings rather than a second copy of the copy."""
    ind, eng = membership.consent_texts()
    html = render_teacher_page(**_teacher_context([]))
    assert ind in html and eng in html
    assert membership.CONSENT_DOCUMENT_VERSION in html


def test_the_page_posts_the_current_consent_with_the_form(render_teacher_page):
    html = render_teacher_page(**_teacher_context([
        {"id": "sc-1", "name": "SMP A", "npsn": "11111111", "city": "Bandung",
         "status": "active", "jenjang": "SMP", "member": False},
    ]))
    assert f'name="document_sha256" value="{membership.consent_sha256()}"' in html, (
        "the request posts no consent hash, so the server can only refuse it")
    assert (f'name="document_version" value="{membership.CONSENT_DOCUMENT_VERSION}"'
            in html)


def test_the_page_is_reachable_from_the_teacher_dashboard():
    dashboard = (TEMPLATES / "teacher" / "dashboard.html").read_text(encoding="utf-8")
    assert 'href="/teacher/membership"' in dashboard, (
        "the destination search is reachable only by knowing its URL")
    assert "Keanggotaan Sekolah" in dashboard and "School Membership" in dashboard, (
        "the door is not a bilingual pair, so it does not follow the toggle")


# ── who may write a request, and who may decide it ───────────────────────────

def _consent():
    return dict(document_version=membership.CONSENT_DOCUMENT_VERSION,
                document_sha256=membership.consent_sha256())


class TestARequestIsWrittenOnceAndRefusedEarly:
    def test_a_role_that_cannot_be_cross_school_cannot_apply(self):
        db = FakeDb(schools=[school("sc-2")], teacher_school_membership=[])
        out = membership.create_request(db, "u-1", "sc-2", "murid", **_consent())
        assert out == {"ok": False, "reason": "not_authorised"}
        assert db.write_payloads("school_membership_request") == []

    def test_the_consent_must_be_the_current_document(self):
        db = FakeDb(schools=[school("sc-2")], teacher_school_membership=[])
        stale = membership.create_request(
            db, "u-1", "sc-2", "guru",
            document_version="membership-cross-school-2020-01",
            document_sha256=membership.consent_sha256())
        assert stale["reason"] == "consent_required"
        forged = membership.create_request(
            db, "u-1", "sc-2", "guru",
            document_version=membership.CONSENT_DOCUMENT_VERSION,
            document_sha256="0" * 64)
        assert forged["reason"] == "consent_required"

    def test_a_request_that_is_already_waiting_is_not_written_twice(self):
        db = FakeDb(schools=[school("sc-2")], teacher_school_membership=[],
                    school_membership_request=[{"id": "r-1", "teacher_id": "u-1",
                                                "target_school_id": "sc-2",
                                                "status": "pending"}])
        out = membership.create_request(db, "u-1", "sc-2", "guru", **_consent())
        assert out["reason"] == "already_pending"
        assert db.write_payloads("school_membership_request") == []

    def test_an_inactive_school_cannot_be_applied_to(self):
        db = FakeDb(schools=[school("sc-2", status="inactive")],
                    teacher_school_membership=[])
        out = membership.create_request(db, "u-1", "sc-2", "guru", **_consent())
        assert out["reason"] == "school_inactive"

    def test_a_successful_request_writes_the_consent_row_with_it(self):
        db = FakeDb(schools=[school("sc-2")], teacher_school_membership=[])
        out = membership.create_request(db, "u-1", "sc-2", "guru", **_consent())
        assert out["ok"] is True
        assert len(db.write_payloads("school_membership_request", "insert")) == 1
        consent = db.write_payloads("membership_consent_log", "insert")
        assert len(consent) == 1, (
            "the request exists without the record of what was agreed to — the one "
            "combination a UU PDP audit cannot accept")
        assert consent[0]["document_version"] == membership.CONSENT_DOCUMENT_VERSION

    def test_an_unreadable_membership_row_means_no_request(self):
        """Unknown is not a licence to write: the approval would have to guess."""
        db = FakeDb(schools=[school("sc-2")], teacher_school_membership=[])
        db.fail.add("teacher_school_membership")
        out = membership.create_request(db, "u-1", "sc-2", "guru", **_consent())
        assert out["reason"] in ("read_failed", "membership_closed")


class TestOnlyTheseThreeRolesDecide:
    def test_the_service_and_the_schema_agree_on_who_may_decide(self):
        """One authority, two places that name it — so they are compared, not read."""
        sql = MIGRATION_063.read_text(encoding="utf-8")
        match = re.search(r"(?is)decided_role TEXT\s*CONSTRAINT[^,]*CHECK \((.*?)\)\s*,",
                          sql)
        assert match, "the schema no longer constrains who may decide"
        in_schema = {w.strip().strip("'") for w in re.findall(r"'(\w+)'", match.group(1))}
        assert in_schema == set(membership.APPROVER_ROLES), (
            f"the service allows {sorted(membership.APPROVER_ROLES)} but the schema "
            f"allows {sorted(in_schema)}")

    def test_a_teacher_cannot_read_the_queue(self):
        db = FakeDb(schools=[], teacher_school_membership=[])
        assert membership.pending_requests(db, "sc-1", "guru")["reason"] == "not_authorised"
        assert membership.school_members(db, "sc-1", "guru")["reason"] == "not_authorised"

    def test_the_queue_is_scoped_to_the_school_that_reads_it(self):
        db = FakeDb(
            school_membership_request=[
                {"id": "r-1", "teacher_id": "u-1", "target_school_id": "sc-1",
                 "status": "pending", "created_at": "2026-10-01T00:00:00+00:00",
                 "expires_at": "2026-10-31T00:00:00+00:00"},
                {"id": "r-2", "teacher_id": "u-2", "target_school_id": "sc-2",
                 "status": "pending", "created_at": "2026-10-01T00:00:00+00:00",
                 "expires_at": "2026-10-31T00:00:00+00:00"},
            ],
            profiles=[person("u-1", name="Budi Guru")])
        out = membership.pending_requests(db, "sc-1", "principal")
        assert out["ok"] is True
        assert [r["request_id"] for r in out["requests"]] == ["r-1"]
        assert out["requests"][0]["name"] == "Budi Guru"

    def test_a_request_in_another_school_reads_as_not_found(self):
        db = FakeDb(
            school_membership_request=[
                {"id": "r-2", "teacher_id": "u-2", "target_school_id": "sc-2",
                 "status": "pending"}],
            teacher_school_membership=[], profiles=[person("u-2")])
        out = membership.decide_request(db, "sc-1", "r-2", "approved",
                                        actor_id="a-1", actor_role="principal")
        assert out == {"ok": False, "reason": "not_found"}
        assert db.writes == [], "a foreign request was written to"

    def test_an_approval_grants_the_membership_in_the_same_call(self):
        db = FakeDb(
            schools=[school("sc-1")],
            school_membership_request=[
                {"id": "r-1", "teacher_id": "u-1", "target_school_id": "sc-1",
                 "status": "pending"}],
            teacher_school_membership=[], profiles=[person("u-1")])
        out = membership.decide_request(db, "sc-1", "r-1", "approved",
                                        actor_id="a-1", actor_role="vice_principal")
        assert out["ok"] is True
        assert db.row("school_membership_request", id="r-1")["status"] == "approved"
        granted = db.row("teacher_school_membership", user_id="u-1", school_id="sc-1")
        assert granted and granted["status"] == "active", (
            "the request reads approved while the access it promised does not exist")

    def test_a_rejection_writes_no_membership(self):
        db = FakeDb(
            school_membership_request=[
                {"id": "r-1", "teacher_id": "u-1", "target_school_id": "sc-1",
                 "status": "pending"}],
            teacher_school_membership=[], profiles=[person("u-1")])
        out = membership.decide_request(db, "sc-1", "r-1", "rejected",
                                        actor_id="a-1", actor_role="admin_sekolah",
                                        reason="Tidak ada formasi")
        assert out["ok"] is True
        assert db.write_payloads("teacher_school_membership") == []
        assert db.row("school_membership_request", id="r-1")["decision_reason"] == "Tidak ada formasi"

    def test_a_decided_request_is_not_decided_again(self):
        db = FakeDb(
            school_membership_request=[
                {"id": "r-1", "teacher_id": "u-1", "target_school_id": "sc-1",
                 "status": "rejected"}],
            teacher_school_membership=[])
        out = membership.decide_request(db, "sc-1", "r-1", "approved",
                                        actor_id="a-1", actor_role="admin_sekolah")
        assert out["reason"] == "not_found"
        assert db.writes == []


# ── the doors exist, at the addresses the pages link ─────────────────────────

def test_every_route_the_pages_link_is_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    for path in ("/teacher/membership", "/teacher/membership/request",
                 "/admin-sekolah/membership", "/admin-sekolah/membership-requests",
                 "/admin-sekolah/membership-requests/<request_id>/<decision>",
                 "/admin-sekolah/memberships/<user_id>/close",
                 "/admin-sekolah/memberships/<user_id>/reopen"):
        assert path in rules, f"{path} is linked but not registered"


def test_the_decision_pages_post_to_the_school_prefix():
    """A POST may not live under `/principal/*` — read-only there is structural."""
    for name in ("admin_sekolah/membership.html", "teacher/membership.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        for action in re.findall(r'action="([^"]+)"', text):
            assert not action.startswith("/principal/"), (
                f"{name} posts to {action}, and that prefix is read-only by test")
