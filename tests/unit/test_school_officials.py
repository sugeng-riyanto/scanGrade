"""Kepala sekolah dan wakil kepala sekolah: peran yang membaca, bukan mengelola.

Hari ini hierarki berhenti di `admin_sekolah` — satu akun yang memegang akun,
data, dan operasional teknis sekolah. Tidak ada peran untuk kepala sekolah, yang
tugasnya *mengawasi*: melihat laporan sekolahnya tanpa memperoleh wewenang
mengubah apa pun. Yang diuji di sini bukan satu tombol, melainkan keseluruhan
jalur yang membuat peran itu nyata:

* **namanya sah** — `profiles_role_check` (migrasi 038) menerimanya, sebab CHECK
  lama menolak baris `principal` dan meninggalkan akun setengah jadi yang bisa
  login tanpa punya peran;
* **pintunya benar** — ia masuk lewat `/auth/login-user`, seperti guru dan murid,
  bukan lewat pintu admin;
* **rumahnya ada** — login yang mendarat di 404 adalah login yang gagal;
* **ia tidak mendapat wewenang tulis** — dekorator `admin_sekolah_required`
  harus menolaknya;
* **demonya bisa dimatikan** — kartu `/demo` yang tidak punya cabang render
  melewatkan dirinya sendiri dengan tenang, dan toggle super admin yang tidak
  membaca daftar yang sama akan menampilkan kotak centang untuk kartu yang tidak
  ada.

Alasan bentuknya begini: hampir semua kegagalan di atas *diam* — tidak ada
traceback, hanya akun yang tidak bisa masuk atau kartu yang tidak muncul. Karena
itu yang diukur adalah kaitan antar-bagian (daftar peran ↔ route ↔ template ↔
seed), bukan sekadar keberadaan satu string.
"""
from __future__ import annotations

import pathlib
import re
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MIGRATION = ROOT / "supabase" / "migrations" / "038_school_officials_roles.sql"
DEMO_TEMPLATE = (ROOT / "app" / "templates" / "demo.html").read_text(encoding="utf-8")
BASE_TEMPLATE = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
LOGIN_ROUTE = (ROOT / "app" / "routes" / "auth.py").read_text(encoding="utf-8")
MANAGE = (ROOT / "manage.py").read_text(encoding="utf-8")

OFFICIAL_ROLES = ("principal", "vice_principal")


# ── 1. nama perannya sah di database ─────────────────────────────────────────

class TestTheMigrationAdmitsThem:
    def test_it_keeps_every_role_that_already_existed(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        constraint = sql.split("ADD CONSTRAINT profiles_role_check", 1)[1]
        constraint = constraint.split(";", 1)[0]
        for role in ("super_admin", "admin_sekolah", "guru", "murid",
                     *OFFICIAL_ROLES):
            assert f"'{role}'" in constraint, f"{role} is not in the new CHECK"

    def test_it_reads_and_writes_a_constraint_rather_than_rows(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "DROP CONSTRAINT IF EXISTS profiles_role_check" in sql, (
            "without the DROP, re-running the migration fails on a duplicate name")
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql, re.I), (
            "the migration must be additive: real accounts live in this table")
        # A role the CHECK drops would be silently invalidated rather than
        # reported — the constraint cannot be widened in place.
        assert "profiles_role_check" in sql.split("ADD CONSTRAINT", 1)[0], (
            "the CHECK is widened by replacing it, so the old one must go first")

    def test_it_enumerates_the_vocabulary_in_one_place(self):
        """The four roles must be listed once, not twice with a subset each."""
        sql = MIGRATION.read_text(encoding="utf-8")
        assert sql.count("ADD CONSTRAINT profiles_role_check") in (1, 2), (
            "one ADD, or one inside each branch of an idempotence guard")


# ── 2. peta peran: satu definisi, dibaca semua pintu ─────────────────────────

class TestTheRoleVocabulary:
    def test_the_user_door_is_the_one_that_serves_them(self):
        from app.utils.auth import USER_ROLES, login_door_for

        for role in OFFICIAL_ROLES:
            assert role in USER_ROLES, (
                f"{role} must sign in through the user door, like guru and murid")
            assert login_door_for(role) == "/auth/login-user"

    def test_each_role_has_one_home(self):
        from app.utils.auth import dashboard_for

        assert dashboard_for("principal") == "/principal/dashboard"
        assert dashboard_for("vice_principal") == "/vice-principal/dashboard"
        # The roles that already had a home keep it: this map replaced three
        # copies of the same dict, so a change here is a change to login.
        assert dashboard_for("guru") == "/teacher/dashboard"
        assert dashboard_for("murid") == "/student/dashboard"
        assert dashboard_for("admin_sekolah") == "/admin-sekolah/dashboard"
        assert dashboard_for("super_admin") == "/super-admin/dashboard"

    def test_the_path_they_are_opening_names_their_own_door(self):
        """An expired session on /principal/... must offer the user door."""
        from app.utils.auth import login_door_for

        assert login_door_for(None, "/principal/dashboard") == "/auth/login-user"
        assert login_door_for(None, "/vice-principal/dashboard") == "/auth/login-user"
        assert login_door_for(None, "/admin-sekolah/dashboard") == "/auth/login"

    def test_they_have_a_session_timeout_of_their_own(self):
        from app.utils.auth import SESSION_TIMEOUTS

        for role in OFFICIAL_ROLES:
            assert role in SESSION_TIMEOUTS, (
                "an unlisted role silently falls back to the default window")

    def test_neither_of_them_can_reach_an_admin_route(self):
        """Read-only means read-only: the admin decorator must refuse them."""
        from app.utils.auth import _check_roles

        for role in OFFICIAL_ROLES:
            assert not _check_roles(role, ("admin_sekolah",)), (
                f"{role} must not pass admin_sekolah_required")
            assert not _check_roles(role, ("super_admin",))
            assert _check_roles(role, OFFICIAL_ROLES), (
                f"{role} must pass the shared officials decorator")


# ── 3. pintu login dan rumah setelah masuk ───────────────────────────────────

class TestTheLoginDoor:
    def test_the_user_door_reads_the_shared_vocabulary(self):
        """A second hard-coded tuple is how the two doors drift apart."""
        body = LOGIN_ROUTE.split("def login_user", 1)[1]
        assert 'role not in ("guru", "murid")' not in body, (
            "login_user hard-codes the roles instead of reading USER_ROLES")
        assert "USER_ROLES" in body, (
            "the door must admit exactly the roles that belong on it")

    def test_both_new_roles_have_a_route(self, app):
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert "/principal/dashboard" in rules
        assert "/vice-principal/dashboard" in rules

    def test_the_dashboard_asks_for_a_session(self, app):
        """Signed out, it must send the reader to their own door, not a 404."""
        client = app.test_client()
        for path in ("/principal/dashboard", "/vice-principal/dashboard"):
            got = client.get(path, follow_redirects=False)
            assert got.status_code in (301, 302, 303, 308), (
                f"{path} answered {got.status_code} instead of a door")
            assert "/auth/login" in got.headers.get("Location", "")

    def _render(self, app, role: str, **context) -> str:
        """The whole page, the way the route renders it.

        `base.html` reads the reader's name and address for its topbar, so those
        are set too: a page rendered without them fails in the chrome rather than
        in the page, which is exactly the failure this test is looking for.
        """
        from flask import g

        template = app.jinja_env.get_template("principal/dashboard.html")
        with app.test_request_context(f"/principal/dashboard"):
            g.user_role = role
            g.user_id = "pr-1"
            g.user_school_id = "sch-1"
            g.user_name = "Kepala Sekolah"
            g.user_email = "kepsek@example.org"
            g.tz_offset = 7
            return template.render(
                lang="id", role=role, school={"name": "SMP N 1"},
                stats={"teachers": 4, "students": 2, "classes": 5, "exams": 3},
                exams=[{"id": "ex-1", "title": "Ulangan Matematika"}],
                **context)

    def test_the_page_renders_for_a_principal(self, app):
        """The template is rendered, so a missing variable is not a blank cell."""
        html = self._render(app, "principal")
        assert "SMP N 1" in html
        assert "Ulangan Matematika" in html

    def test_the_page_names_the_reader_for_each_role(self, app):
        """A vice principal must not read a page headed 'Kepala Sekolah'.

        Asserted on the *title*, not on "the two renders differ" and not on the
        presence of the words anywhere in the page. Both of those pass for the
        wrong reason: the breadcrumb in `base.html` already prints the reader's
        area name from `g.user_role`, so a template that hard-coded "Kepala
        Sekolah" as its own heading still produced two different pages with the
        right words somewhere in them.
        """
        seen = {role: self._render(app, role) for role in OFFICIAL_ROLES}
        assert "<title>Kepala Sekolah - ScanGrade</title>" in seen["principal"], (
            "the principal's page does not name the principal")
        assert "<title>Wakil Kepala Sekolah - ScanGrade</title>" in seen["vice_principal"], (
            "a vice principal reads a page titled for the principal")
        assert "Wakil Kepala Sekolah" in seen["vice_principal"]


# ── 4. navigasi dan breadcrumb ───────────────────────────────────────────────

class TestTheChrome:
    def test_the_sidebar_knows_both_roles(self):
        """Both officials share one branch, and it must name both.

        A single branch keyed on `g.user_role in (...)` rather than two `elif`s,
        because the menu is identical for the two: what differs is the page, and
        the page names the reader itself.
        """
        assert "g.user_role in ('principal', 'vice_principal')" in BASE_TEMPLATE, (
            "the sidebar has no branch for the two school officials")
        assert "/principal/dashboard" in BASE_TEMPLATE
        assert "/vice-principal/dashboard" in BASE_TEMPLATE

    def test_the_breadcrumb_area_follows_the_reader(self):
        for role in OFFICIAL_ROLES:
            assert f"'{role}':" in BASE_TEMPLATE, (
                "the breadcrumb area table has no entry for {role}".format(role=role))


# ── 5. demo: kartu, toggle, dan super admin ──────────────────────────────────

class TestTheDemo:
    def test_both_roles_are_demo_items(self):
        from app.services.demo_settings import ROLE_ITEMS

        for role in OFFICIAL_ROLES:
            assert f"demo_{role}" in ROLE_ITEMS, (
                f"demo_{role} is not an item, so no page can draw or switch it")

    def test_a_switched_off_card_is_absent_not_blank(self):
        from app.services import demo_settings

        role_keys = [f"demo_{role}" for role in OFFICIAL_ROLES]
        blob = {key: False for key in role_keys}
        blob["role_order"] = ",".join(role_keys)
        # Off means absent from the page: `/demo` loops `demo_items`, so a card
        # that is switched off is not in the list it draws from.
        assert not [key for key in demo_settings.demo_items(blob, "roles")
                    if key in role_keys]
        # ...and still present on the settings page, which is the only place it
        # can be switched back on.
        order = demo_settings.demo_order(blob, "roles")
        assert order[:2] == role_keys, order
        assert set(order) == set(demo_settings.ROLE_ITEMS), order

    def test_every_item_has_a_card_to_draw(self):
        """A key with no branch renders nothing, silently."""
        from app.services.demo_settings import ROLE_ITEMS

        for key in ROLE_ITEMS:
            assert f"'{key}'" in DEMO_TEMPLATE, (
                f"{key} is a demo item but /demo has no branch for it")

    def test_the_card_offers_the_user_door(self):
        for role in OFFICIAL_ROLES:
            assert f"/auth/login-user?role={role}" in DEMO_TEMPLATE, (
                f"the {role} card does not offer the door that role belongs on")

    def test_the_credentials_on_the_page_are_the_seeded_ones(self):
        """The card is only a demo if the account behind it exists."""
        for role in OFFICIAL_ROLES:
            email = re.search(rf"{role}_\w+@scan-grade\.app", DEMO_TEMPLATE)
            assert email, f"/demo shows no account for {role}"
            assert email.group(0) in MANAGE, (
                f"{email.group(0)} is on /demo but the seed never creates it")


# ── 6. CRUD: sekolah yang membuat dan mencabutnya ────────────────────────────

class TestTheSchoolAdminsCrud:
    def test_the_service_refuses_a_role_it_does_not_own(self):
        from app.services import school_officials

        assert set(school_officials.OFFICIAL_ROLES) == set(OFFICIAL_ROLES)
        with pytest.raises(ValueError):
            school_officials.validate_role("admin_sekolah")

    def test_creating_an_official_writes_a_profile_with_that_role(self):
        from app.services import school_officials

        written = {}

        class _Table:
            def __init__(self, name):
                self._name = name

            def upsert(self, row):
                written[self._name] = row
                return self

            def execute(self):
                return SimpleNamespace(data=[], count=0)

            def insert(self, row):
                written[self._name] = row
                return self

            def delete(self):
                return self

            def eq(self, *a, **k):
                return self

        supabase = SimpleNamespace(
            table=lambda name: _Table(name),
            auth=SimpleNamespace(admin=SimpleNamespace(
                create_user=lambda payload: SimpleNamespace(
                    user=SimpleNamespace(id="uid-1")))),
        )
        uid = school_officials.create_official(
            supabase, school_id="sch-1", role="principal",
            full_name="Kepala Sekolah", email="kepsek@example.org",
            password="secret123")
        assert uid == "uid-1"
        assert written["profiles"]["role"] == "principal"
        assert written["profiles"]["school_id"] == "sch-1"

    def _fake(self, row, calls):
        """A PostgREST stand-in that records every write it is asked to make.

        `profiles` is the table every role in the platform lives in, so the only
        thing standing between \"rename official X\" and rewriting a row in
        another school is `_assert_own`'s school comparison. This fake answers the
        read so that comparison is the *only* thing that can refuse — a fake that
        errored out would let the test pass while the check was gone.
        """
        class _Query:
            def __init__(self, name):
                self.name = name
                self.single_read = False

            def select(self, *a, **k):
                return self

            def eq(self, *a, **k):
                return self

            def single(self):
                self.single_read = True
                return self

            def execute(self):
                return SimpleNamespace(data=row if self.single_read else [], count=0)

            def update(self, patch):
                calls.append(("update", self.name, patch))
                return self

            def delete(self):
                calls.append(("delete", self.name))
                return self

            def upsert(self, payload):
                calls.append(("upsert", self.name, payload))
                return self

        return SimpleNamespace(
            table=lambda name: _Query(name),
            auth=SimpleNamespace(admin=SimpleNamespace(
                delete_user=lambda uid, **k: calls.append(("delete_user", uid)))))

    def test_it_refuses_an_official_from_another_school(self):
        """`profiles` holds every role in the platform, this school's admin included.

        Without the `school_id` comparison, \"delete official <id>\" deletes whatever
        row you named — and the id comes from a form.
        """
        from app.services import school_officials

        calls = []
        supabase = self._fake({"id": "off-1", "role": "principal",
                               "school_id": "school-other",
                               "full_name": "Kepala Sekolah Lain"}, calls)

        with pytest.raises(school_officials.OfficialError):
            school_officials.update_official(supabase, "off-1", "school-mine",
                                             full_name="Nama Baru")
        with pytest.raises(school_officials.OfficialError):
            school_officials.delete_official(supabase, "off-1", "school-mine")

        assert not [c for c in calls if c[0] in ("update", "delete", "upsert",
                                                 "delete_user")], (
            f"another school's official was written to: {calls}")

    def test_it_refuses_a_row_that_is_not_an_official(self):
        """Pointing the route at a teacher's id must not rename or delete a teacher."""
        from app.services import school_officials

        calls = []
        supabase = self._fake({"id": "t-1", "role": "guru",
                               "school_id": "school-mine", "full_name": "Bu Siti"},
                              calls)

        with pytest.raises(school_officials.OfficialError):
            school_officials.delete_official(supabase, "t-1", "school-mine")
        assert not [c for c in calls if c[0] in ("delete", "delete_user")], calls

    def test_the_routes_are_admin_only_and_school_scoped(self):
        src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(
            encoding="utf-8")
        for route in ("/officials", "/officials/create",
                      "/officials/<official_id>/edit",
                      "/officials/<official_id>/delete"):
            assert route in src, f"{route} is not registered"
        section = src.split('route("/officials/create"', 1)[1][:400]
        assert "admin_sekolah_required" in section, (
            "creating an official must be the school admin's own door")
        assert "subscription_write_required" in section, (
            "an expired subscription must not create accounts")

    def test_the_school_page_lists_them(self):
        src = (ROOT / "app" / "templates" / "admin_sekolah" /
               "officials.html").read_text(encoding="utf-8")
        assert "data-official-row" in src, (
            "the CRUD page needs a handle per row for its own delete/edit")


# ── 7. seed: akun demo harus benar-benar ada ─────────────────────────────────

class TestTheSeed:
    def test_every_demo_school_offers_both_officials(self):
        from manage import DEMO_SCHOOLS

        assert DEMO_SCHOOLS, "no demo schools to check"
        for school in DEMO_SCHOOLS:
            officials = school.get("officials") or []
            roles = {o["role"] for o in officials}
            assert roles == set(OFFICIAL_ROLES), (
                f"{school['name']} seeds {sorted(roles)} instead of both "
                "officials")
            for official in officials:
                assert official["password"] == "demo123", (
                    "the /demo page prints one password for every account")
