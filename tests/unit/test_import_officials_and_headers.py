"""Import/upload for teachers, principals and vice principals.

Measured before this file existed: a teacher sheet whose employee number was not
pure digits (`GT-001`, `1987.0101`) fell through every positional branch in
``_import_teachers`` and was read as *"email = the subject column"* — so the row
was handed a subject name as an email address and the whole sheet failed, while
the student sheet (numeric NISN) imported fine. And a school official had no
importer at all: the only way to add a head teacher was the Officials page, one
form submit each.

The guards here hold the header-driven reading and the officials importer, and the
dispatch that routes a "Kepala Sekolah" / "Wakil Kepala Sekolah" / "Pejabat" sheet
to it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from app.routes import admin_sekolah as adm


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        return self

    def ilike(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows, count=len(self.rows))


class _Sb:
    def table(self, name):
        return _Query([])


def _sheet(title, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    return ws


@pytest.fixture
def captured(monkeypatch):
    """Record what the importers hand the account creators."""
    calls = {"teachers": [], "officials": []}
    monkeypatch.setattr(adm, "create_teacher_account",
                        lambda supabase, **kw: (calls["teachers"].append(kw), "uid")[1])
    from app.services import school_officials
    monkeypatch.setattr(school_officials, "create_official",
                        lambda supabase, **kw: (calls["officials"].append(kw), "uid")[1])
    monkeypatch.setattr(adm, "_get_email_domain", lambda sid: "sekolah.id")
    return calls


class TestTeacherSheetReadByHeader:
    def test_an_alphanumeric_nip_keeps_email_and_subject_in_their_columns(self, captured):
        ws = _sheet("Guru", [
            ["NIP", "Nama Lengkap", "Email", "Mata Pelajaran", "No. HP"],
            ["GT-001", "Budi Santoso", "budi@sekolah.id", "Matematika", "0812"],
        ])
        adm._import_teachers(ws, "S1", _Sb(), {"errors": []})

        call = captured["teachers"][0]
        assert call["employee_id"] == "GT-001"
        assert call["full_name"] == "Budi Santoso"
        assert call["email"] == "budi@sekolah.id", (
            "the subject column was read as the email — the alphanumeric-NIP bug")

    def test_a_float_nip_is_not_printed_with_a_decimal(self, captured):
        ws = _sheet("Guru", [
            ["NIP", "Nama Lengkap", "Email", "Mata Pelajaran"],
            [19870101.0, "Siti Rahma", "siti@sekolah.id", "Fisika"],
        ])
        adm._import_teachers(ws, "S1", _Sb(), {"errors": []})
        assert captured["teachers"][0]["employee_id"] == "19870101"

    def test_a_generated_email_is_used_when_the_column_is_blank(self, captured):
        ws = _sheet("Guru", [
            ["NIP", "Nama Lengkap", "Email"],
            ["GT-002", "Tanpa Email", ""],
        ])
        adm._import_teachers(ws, "S1", _Sb(), {"errors": []})
        assert "@sekolah.id" in captured["teachers"][0]["email"]


class TestOfficialSheets:
    def test_a_dedicated_principal_sheet_fixes_the_role(self, captured):
        ws = _sheet("Kepala Sekolah", [
            ["Nama Lengkap", "Email"],
            ["Drs. Hasan", "hasan@sekolah.id"],
        ])
        results = {"officials": 0, "errors": []}
        adm._import_officials(ws, "S1", _Sb(), results, default_role="principal")
        assert results["officials"] == 1
        assert captured["officials"][0]["role"] == "principal"

    def test_a_combined_pejabat_sheet_reads_the_role_column(self, captured):
        ws = _sheet("Pejabat", [
            ["Jabatan", "Nama Lengkap", "Email", "No. HP"],
            ["Kepala Sekolah", "Drs. Hasan", "hasan@sekolah.id", ""],
            ["Wakil Kepala Sekolah", "Rina Marlina", "rina@sekolah.id", "0813"],
        ])
        results = {"officials": 0, "errors": []}
        adm._import_officials(ws, "S1", _Sb(), results, default_role=None)
        roles = [c["role"] for c in captured["officials"]]
        assert roles == ["principal", "vice_principal"], roles

    def test_a_row_with_an_unknown_role_is_skipped_not_guessed(self, captured):
        ws = _sheet("Pejabat", [
            ["Jabatan", "Nama Lengkap", "Email"],
            ["Bendahara", "Orang Lain", "x@sekolah.id"],
        ])
        results = {"officials": 0, "errors": []}
        adm._import_officials(ws, "S1", _Sb(), results, default_role=None)
        assert results["officials"] == 0
        assert captured["officials"] == []


def _upload(app, monkeypatch, wb, calls):
    """POST a workbook to /admin-sekolah/import with every importer stubbed."""
    import io
    from app.utils import auth as authmod

    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": "U1", "email": "a@x", "name": "Admin", "role": "admin_sekolah",
        "school_id": "S1", "status": "active",
    })
    monkeypatch.setattr(adm, "get_supabase", lambda: _Sb())
    monkeypatch.setattr(adm, "_school_id", lambda: "S1")
    monkeypatch.setattr(adm, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(adm, "_import_students",
                        lambda ws, sid, sb, res: calls.setdefault("students", ws.title))
    monkeypatch.setattr(adm, "_import_teachers",
                        lambda ws, sid, sb, res: calls.setdefault("teachers", ws.title))
    monkeypatch.setattr(adm, "_import_subjects",
                        lambda ws, sid, sb, res: calls.setdefault("subjects", ws.title))
    monkeypatch.setattr(adm, "_import_officials",
                        lambda ws, sid, sb, res, default_role=None:
                        calls.setdefault(default_role or "pejabat", ws.title))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    client = app.test_client()
    client.set_cookie("access_token", "tok")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf"
    return client.post("/admin-sekolah/import",
                       data={"file": (buf, "data.xlsx")},
                       content_type="multipart/form-data",
                       headers={"X-CSRF-Token": "csrf"})


class TestImportDispatch:
    def test_each_named_sheet_reaches_its_importer(self, app, monkeypatch):
        wb = Workbook()
        wb.active.title = "Murid"
        wb.create_sheet("Guru")
        wb.create_sheet("Kepala Sekolah")
        wb.create_sheet("Wakil Kepala Sekolah")
        wb.create_sheet("Mata Pelajaran")
        calls: dict = {}
        _upload(app, monkeypatch, wb, calls)
        assert calls == {
            "students": "Murid", "teachers": "Guru",
            "principal": "Kepala Sekolah",
            "vice_principal": "Wakil Kepala Sekolah",
            "subjects": "Mata Pelajaran",
        }, calls

    def test_a_combined_pejabat_sheet_does_not_fix_the_role(self, app, monkeypatch):
        wb = Workbook()
        wb.active.title = "Pejabat"
        calls: dict = {}
        _upload(app, monkeypatch, wb, calls)
        assert calls == {"pejabat": "Pejabat"}, (
            "a combined officials sheet must let its Role column decide")


class TestHeaderDetection:
    def test_headers_are_matched_ignoring_case_and_punctuation(self):
        ws = _sheet("Guru", [["No. HP", "Nama  Lengkap", "NIP", "MATA PELAJARAN"]])
        columns = adm._header_columns(ws)
        assert columns["phone"] == 0
        assert columns["name"] == 1
        assert columns["nip"] == 2
        assert columns["subject"] == 3
