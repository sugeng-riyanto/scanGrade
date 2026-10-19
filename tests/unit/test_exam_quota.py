"""Ujian mana yang menghabiskan kuota tahunan sekolah, dan kapan kuota itu menahan.

Tiga keluhan yang jadi asal berkas ini, semuanya terukur di produksi:

* kuota ditagihkan pada **setiap** penyimpanan. `@require_subscription("create_exam")`
  duduk di `/teacher/exams/new`, dan route itu menulis kertas yang sudah ada sama
  seringnya dengan yang baru — jadi begitu kuota habis, guru tidak bisa lagi menyimpan
  draft, menyunting soal, atau mem-publish ujian yang sudah ada;
* yang dihitung adalah **semua** baris `exams` tahun ini. Draft yang ditinggalkan dan
  template (bentuk, bukan kertas) ikut memakan jatah, dan tidak ada satu tempat pun di
  UI yang menunjukkan sisa kuota sebelum simpan gagal;
* satu sekolah di produksi ber-tier `basic` karena aktivasi tunai tanpa plan, jadi
  batasnya 10/tahun tanpa ada yang tahu.

Yang dijaga di sini: hitungannya kertas nyata saja, ceknya ada di cabang *insert*,
dan halaman ujian menunjukkan angkanya. Nilai `TIER_LIMITS` sendiri tidak diubah
(trial 5, basic 10, pro/enterprise tak terbatas) — `tests/test_stage345.py` yang
memegang angka itu.

Supabase-nya palsu tapi **menghormati filternya**, supaya penyaring draft benar-benar
diuji: kalau `neq("status", "draft")` hilang dari kueri, draft ikut terhitung dan uji
ini merah.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import subscription_service as subs

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"
PAGE = ROOT / "app" / "templates" / "teacher" / "exams.html"
SERVICE = ROOT / "app" / "services" / "subscription_service.py"


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── supabase palsu: merekam filter, dan menyaring seperti PostgREST ──────────

class _Table:
    def __init__(self, rows, seen):
        self._rows, self._seen, self._filters = rows, seen, []

    def select(self, *args, **kwargs):
        self._seen["select"] = args[0] if args else ""
        return self

    def _filter(self, kind, column, value):
        self._seen.setdefault(kind, []).append((column, value))
        self._filters.append((kind, column, value))
        return self

    def eq(self, column, value):
        return self._filter("eq", column, value)

    def neq(self, column, value):
        return self._filter("neq", column, value)

    def gte(self, column, value):
        return self._filter("gte", column, value)

    def order(self, *args, **kwargs):
        return self

    def _matches(self, row):
        for kind, column, value in self._filters:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "neq" and actual == value:
                return False
            if kind == "gte" and str(actual or "") < str(value):
                return False
        return True

    def execute(self):
        return type("R", (), {"data": [r for r in self._rows if self._matches(r)]})()


class _Fake:
    def __init__(self, rows):
        self.rows, self.seen = rows, {}

    def table(self, name):
        self.seen["table"] = name
        return _Table(self.rows, self.seen)


def _quota(monkeypatch, rows, tier="basic"):
    fake = _Fake(rows)
    monkeypatch.setattr(subs, "get_supabase", lambda: fake)
    monkeypatch.setattr(subs, "get_tier_for_school", lambda school_id: tier)
    return subs.exam_quota("sch-1"), fake.seen


def _paper(template=False, status="active"):
    return {"id": "e", "is_template": template, "status": status,
            "school_id": "sch-1", "created_at": "2026-05-05T00:00:00+00:00"}


# ── apa yang memakan jatah ───────────────────────────────────────────────────

class TestWhatSpendsTheYear:
    def test_drafts_and_templates_do_not(self, monkeypatch):
        """Satu tahun terbuang oleh draft yang ditinggalkan bila draft ikut dihitung."""
        rows = ([_paper() for _ in range(7)]
                + [_paper(status="draft") for _ in range(3)]
                + [_paper(template=True) for _ in range(2)])
        quota, _ = _quota(monkeypatch, rows)
        assert quota["used"] == 7, "draft/template ikut dihitung"
        assert quota["remaining"] == 3

    def test_a_null_template_column_is_not_spend(self, monkeypatch):
        """Baris yang ditulis sebelum kolomnya ada membawa NULL, bukan false."""
        quota, _ = _quota(monkeypatch, [{"id": "e", "is_template": None,
                                         "status": "active", "school_id": "sch-1",
                                         "created_at": "2026-05-05T00:00:00+00:00"}])
        assert quota["used"] == 1, "kertas tanpa kolom itu berhenti dihitung"

    def test_the_query_is_scoped_to_the_school_this_year_and_real_papers(self, monkeypatch):
        quota, seen = _quota(monkeypatch, [])
        assert seen["table"] == "exams"
        assert ("school_id", "sch-1") in seen["eq"]
        assert ("status", subs.DRAFT_STATUS) in seen["neq"]
        column, since = seen["gte"][0]
        assert column == "created_at" and re.match(r"^\d{4}-01-01T", since), seen["gte"]

    def test_another_schools_papers_are_not_ours(self, monkeypatch):
        rows = [_paper(), dict(_paper(), school_id="sch-2"),
                dict(_paper(), school_id="sch-2")]
        quota, _ = _quota(monkeypatch, rows)
        assert quota["used"] == 1

    def test_the_count_is_the_schools_not_one_teachers(self):
        """Tidak ada filter `teacher_id`: paketnya milik sekolah."""
        body = source(SERVICE)
        quota_body = body.split("def exam_quota", 1)[1].split("def quota_message", 1)[0]
        assert "teacher_id" not in quota_body

    def test_an_unlimited_plan_has_no_remaining_to_report(self, monkeypatch):
        quota, _ = _quota(monkeypatch, [_paper() for _ in range(50)], tier="pro")
        assert quota["unlimited"] is True
        assert quota["limit"] is None and quota["remaining"] is None

    def test_a_failed_count_does_not_become_a_refusal(self, monkeypatch):
        """Supabase yang tidak menjawab bukan bukti sekolah kehabisan kuota."""
        def boom():
            raise RuntimeError("no connection")

        monkeypatch.setattr(subs, "get_supabase", boom)
        monkeypatch.setattr(subs, "get_tier_for_school", lambda school_id: "basic")
        quota = subs.exam_quota("sch-1")
        assert quota["used"] == 0 and quota["remaining"] == 10

    def test_the_tier_it_was_given_is_not_looked_up_again(self, monkeypatch):
        """Route sudah tahu tiernya; satu pembaca tier, bukan dua kueri."""
        def refuse(school_id):
            raise AssertionError("tier dibaca dua kali")

        monkeypatch.setattr(subs, "get_supabase", lambda: _Fake([]))
        monkeypatch.setattr(subs, "get_tier_for_school", refuse)
        assert subs.exam_quota("sch-1", tier="basic")["limit"] == 10


# ── kapan kuota menahan ──────────────────────────────────────────────────────

def _stub_quota(monkeypatch, **quota):
    monkeypatch.setattr(subs, "get_supabase", lambda: _Fake([]))
    monkeypatch.setattr(subs, "exam_quota", lambda school_id, tier=None: quota)


class TestTheCap:
    def test_a_full_basic_plan_is_refused_with_the_number(self, monkeypatch):
        _stub_quota(monkeypatch, tier="basic", limit=10, used=10, remaining=0,
                    unlimited=False)
        allowed, message = subs.check_feature_limit("sch-1", "create_exam")
        assert allowed is False
        assert "Kuota ujian tahun ini (10)" in message and "Upgrade paket" in message

    def test_one_below_the_cap_is_allowed(self, monkeypatch):
        _stub_quota(monkeypatch, tier="basic", limit=10, used=9, remaining=1,
                    unlimited=False)
        assert subs.check_feature_limit("sch-1", "create_exam") == (True, "")

    def test_unlimited_plans_are_never_refused(self, monkeypatch):
        _stub_quota(monkeypatch, tier="pro", limit=None, used=99, remaining=None,
                    unlimited=True)
        assert subs.check_feature_limit("sch-1", "create_exam") == (True, "")

    def test_the_quota_is_asked_once_and_only_when_the_row_is_new(self):
        """Ceknya di cabang insert; menyimpan kertas yang sudah ada tidak ditahan.

        Decorator di pintu route adalah cacat aslinya: route ini menulis kertas
        lama sama seringnya dengan yang baru.
        """
        body = source(TEACHER)
        route = body.split('@teacher_bp.route("/exams/new"', 1)[1].split("\ndef ", 1)[0]
        assert "@require_subscription" not in route, (
            "kuota kembali menjadi decorator, jadi setiap simpan kertas lama ditahan")
        handler = body.split("def exam_form():", 1)[1]
        guard = handler.index("if not draft_row:")
        assert 'check_feature_limit(school_id, "create_exam")' in handler[guard:guard + 300]
        # Sebelum baris apa pun ditulis, dan sesudah id draft lama dibaca.
        assert handler.index("_owned_draft(") < guard
        assert guard < handler.index('supabase.table("exams").insert(data)')


# ── halaman yang menunjukkan sisanya ─────────────────────────────────────────

class TestThePageShowsTheAllowance:
    def test_the_list_hands_the_page_the_allowance(self):
        route = source(TEACHER).split("def my_exams():", 1)[1].split("\n@teacher_bp", 1)[0]
        assert "exam_quota=quota" in route, "halaman ujian tidak menerima kuotanya"
        assert "exam_quota(" in route, "angkanya dihitung di template, bukan di pembaca ini"

    def test_the_page_prints_the_allowance_it_receives(self, app):
        html = _render_page(app, {"tier": "basic", "limit": 10, "used": 8,
                                  "remaining": 2, "unlimited": False})
        page = source(PAGE)
        for name in ("used", "remaining", "limit"):
            assert f"exam_quota.{name}" in page, f"halaman tidak memakai {name}"
        # The numbers are in the binding the page hands Alpine, and the labels carry
        # both languages: this is a pinned page whose labels are `t()` pairs.
        assert "8 of 10 used" in html and "2 left" in html, html[:600]
        assert "Kuota ujian tahun ini" in html and "Exam quota this year" in html
        assert 'data-exam-quota-used="8"' in html

    def test_a_full_plan_says_so_on_the_page_a_paper_is_started_from(self, app):
        html = _render_page(app, {"tier": "basic", "limit": 10, "used": 10,
                                  "remaining": 0, "unlimited": False})
        block = _quota_block(html)
        assert "Plan is full" in block and "Paket sudah penuh" in block
        assert "border-rose-200" in block, "keadaan penuh tidak dibedakan dari yang lega"
        assert "left" not in block, "halaman menjanjikan sisa yang tidak ada"

    def test_an_unlimited_plan_says_that_rather_than_a_fraction(self, app):
        html = _render_page(app, {"tier": "pro", "limit": None, "used": 40,
                                  "remaining": None, "unlimited": True})
        block = _quota_block(html)
        assert "Unlimited on this plan" in block
        assert "None" not in block, "halaman mencetak angka yang tidak ada"

    def test_the_page_without_a_quota_renders_nothing_extra(self, app):
        """Halaman lain yang memakai template ini tidak boleh pecah."""
        html = _render_page(app, None)
        assert "Kuota ujian tahun ini" not in html

    def test_the_labels_are_pairs_and_declare_their_language(self):
        """Halaman pinned: setiap bagian yang berganti bahasa menyatakan bahasanya.

        Bentuk yang sama dengan baris jumlah ujian di atas blok ini — `t()` bertanda
        `:lang="lang"`. Literal Indonesia saja akan menurunkan cakupan halaman ini di
        bawah floor-nya, dan gerbang cakupan bahasa menolak rilis karena itu.
        """
        block = source(PAGE).split("{% if exam_quota %}", 1)[1].split("<!-- Header -->", 1)[0]
        assert "Kuota ujian tahun ini" in block, "blok kuotanya tidak ditemukan"
        assert "Exam quota this year" in block, "labelnya tidak punya separuh Inggris"
        # *Every* label, not "at least four": a floor let one of the seven lose its
        # pair and still pass (measured — the mutation check's last entry). The three
        # counts are one number, or a label switches without a pair, or switches
        # without declaring the language it switched to.
        assert block.count("x-text=\"") == block.count("x-text=\"t("), (
            "ada label di blok ini yang bukan pasangan `t()`", block)
        assert block.count(":lang=\"lang\"") == block.count("x-text=\""), (
            "ada label yang berganti bahasa tanpa menyatakan bahasanya", block)


def _quota_block(html: str) -> str:
    """Just the allowance note out of the page, so a test cannot match chrome.

    Read on the rendered page rather than on the template: the numbers are handed to
    Alpine in a binding, so what a test may assert about is the binding itself.
    """
    assert "data-exam-quota-used" in html, "halaman tidak merender blok kuotanya"
    marker = html.index("data-exam-quota-used")
    return html[html.rindex("<div", 0, marker):html.index("</div>", marker)]


def _render_page(app, quota):
    """Halaman ujian, dirender seperti route-nya merender: dengan sesi guru."""
    from flask import g

    with app.test_request_context("/teacher/exams"):
        g.user_id = "tea-1"
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        return app.jinja_env.get_template("teacher/exams.html").render(
            exams=[], unassigned_ids=set(), ambiguous_ids=set(), exam_quota=quota)


# ── pintu simpan, dijalankan: kuota penuh tidak menahan kertas lama ──────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    """A Supabase stand-in that records what was written and honours its filters."""

    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters, self.mode, self.payload = [], "select", None

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload, *, returning="representation"):
        self.mode, self.payload = "update", payload
        return self

    def _matches(self, row):
        return all(str(row.get(k)) == str(v) for k, v in self.filters)

    def execute(self):
        self.store.calls.append((self.table, self.mode))
        if self.mode == "insert":
            self.store.inserted.append((self.table, self.payload))
            row = dict(self.payload)
            row.setdefault("id", "new-1")
            return _Resp([row])
        rows = self.store.rows.get(self.table, [])
        return _Resp([r for r in rows if self._matches(r)])


class _FakeSb:
    def __init__(self, rows=None):
        self.rows, self.inserted, self.calls = rows or {}, [], []

    def table(self, name):
        return _Query(self, name)

    def writes(self, table="exams"):
        """Every write the route made to a table, in order."""
        return [m for t, m in self.calls if t == table and m in ("insert", "update")]


def _raw(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


#: The school is at its cap in both cases below: the point is that the *same*
#: refusal stops a new paper and does not stop a paper that already exists.
FULL = ("Kuota ujian tahun ini (10) telah terpenuhi. "
        "Upgrade paket untuk batas tak terbatas.")

DRAFT = {"id": "draft-1", "teacher_id": "t-1", "school_id": "s1",
         "status": "draft", "is_published": False}

#: The row that lets a cross-school role file here. `filing_school_for_request` reads
#: it in the same request, and `None` (unreadable) and `[]` (nothing recorded) are the
#: two answers a write refuses to guess from.
MEMBERSHIP = {"user_id": "t-1", "school_id": "s1", "status": "active"}


class TestTheSaveDoorWithAFullQuota:
    def _post(self, app, monkeypatch, *, draft_id):
        from flask import g, get_flashed_messages

        from app.routes import teacher
        from app.services import school_membership as membership

        sb = _FakeSb(rows={"exams": [dict(DRAFT)],
                           "teacher_school_membership": [dict(MEMBERSHIP)]})
        monkeypatch.setattr(teacher, "get_supabase", lambda: sb)
        # The filing guard reads the membership rows through the service's own client
        # selector, not the route's, so both have to be the fake — otherwise the rows
        # come back unreadable and the request is refused before the quota is asked.
        monkeypatch.setattr(membership, "_client", lambda: sb)
        monkeypatch.setattr(
            teacher, "check_feature_limit",
            lambda school_id, feature, extra=None: (False, FULL))
        data = {"title": "UTS", "subject": "MTK", "total_questions": "5",
                "start_at": "2026-09-03T09:00", "end_at": "2026-09-04T09:00",
                "action": "save_draft"}
        if draft_id:
            data["draft_id"] = draft_id
        with app.test_request_context("/teacher/exams/new", method="POST", data=data):
            g.user_id = "t-1"
            g.user_role = "guru"
            g.user_school_id = "s1"
            g.user_home_school_id = "s1"
            g.tz_offset = 7
            g.show = {}
            _raw(teacher.exam_form)()
            flashed = [message for _category, message in
                       get_flashed_messages(with_categories=True)]
        return sb, flashed

    def test_the_same_full_quota_refuses_a_paper_that_does_not_exist(self, app, monkeypatch):
        sb, flashed = self._post(app, monkeypatch, draft_id=None)
        assert any("Kuota ujian tahun ini" in message for message in flashed), (
            "kuota penuh tidak menahan kertas baru", flashed)
        assert sb.writes() == [], "kertas yang ditolak tetap mencapai tabel exams"

    def test_an_existing_paper_is_saved_with_the_quota_full(self, app, monkeypatch):
        """The reported bug, run: editing a paper was refused like minting one."""
        sb, flashed = self._post(app, monkeypatch, draft_id="draft-1")
        assert not [m for m in flashed if "Kuota" in m], (
            "menyimpan kertas yang sudah ada ditahan kuota", flashed)
        assert "update" in sb.writes(), "kertas lama tidak ditulis sama sekali"
        assert "insert" not in sb.writes(), "kertas lama menjadi baris baru"
