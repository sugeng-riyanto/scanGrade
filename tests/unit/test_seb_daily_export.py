import inspect
from io import BytesIO
from types import SimpleNamespace

from flask import Flask, g
from openpyxl import load_workbook
import pytest

from app.routes import seb


class ExamQuery:
    def __init__(self):
        self.filters = {}
        self.not_ = self

    def __getattr__(self, method):
        def chain(*args):
            self.filters.setdefault(method, []).append(args)
            return self
        return chain

    def execute(self):
        assert ("school_id", "school-1") in self.filters["eq"]
        assert ("is_published", True) in self.filters["eq"]
        assert self.filters["gte"] == [("start_at", "2026-11-24T17:00:00+00:00")]
        assert self.filters["lt"] == [("start_at", "2026-11-25T17:00:00+00:00")]
        exams = [{"id": "exam-1", "start_at": "2026-11-25T00:30:00Z",
                  "title": "=1+1", "subject": "Matematika", "require_seb": True,
                  "seb_config_key": "configured", "class_ids": ["b", "a", "other"]}]
        if ("require_seb", True) not in self.filters["eq"]:
            exams.extend([
                {"id": "exam-2", "start_at": "2026-11-25T02:00:00Z",
                 "title": "Ujian 2", "subject": "Bahasa Indonesia", "require_seb": False},
                {"id": "exam-3", "start_at": "2026-11-25T03:00:00Z",
                 "title": "Fisika", "subject": "Fisika", "require_seb": True},
            ])
        return SimpleNamespace(data=exams)



@pytest.mark.parametrize("output", ["xlsx", "whatsapp"])
def test_export_uses_school_day_and_only_reveals_the_quit_code(monkeypatch, output):
    app = Flask(__name__)
    query = ExamQuery()
    school = SimpleNamespace(select=lambda _: school, eq=lambda *_: school,
                             maybe_single=lambda: school,
                             execute=lambda: SimpleNamespace(data={"name": "SMP Harapan"}))
    monkeypatch.setattr(seb, "_supabase", lambda: SimpleNamespace(
        table=lambda name: query if name == "exams" else school))
    monkeypatch.setattr(seb, "school_classes", lambda _: [
        {"id": "b", "name": "IX-B"}, {"id": "a", "name": "IX-A"},
    ])
    decisions = []
    monkeypatch.setattr(seb, "_verdict", lambda exam, access:
                        decisions.append((exam["id"], access)) or {"ok": True})
    monkeypatch.setattr(seb.seb_service, "credentials_for", lambda db, exam_id: exam_id)

    def reveal(credential, kind):
        assert (credential, kind) == ("exam-1", "quit")
        return "001234"

    monkeypatch.setattr(seb.seb_service, "reveal", reveal)
    with app.test_request_context(method="POST", data={"date": "2026-11-25", "format": output}):
        g.user_school_id = "school-1"
        response = inspect.unwrap(seb.daily_exit_codes)()
        assert response.headers["Cache-Control"] == "private, no-store"
        assert decisions == [("exam-1", seb.seb_access.ACCESS_VIEW_PASSWORD)]
        if output == "whatsapp":
            english = response.get_json()["english_text"]
            assert "*SMP Harapan Exam Schedule | 25/11/2026 (WIB)*" in english
            assert "Class: IX-A, IX-B — SEB exit code: `001234`" in english
            assert "no SEB required" in english
            assert "SEB exit code unavailable" in english
            message = response.get_json()["text"]
            assert "*Jadwal Ujian SMP Harapan | 25/11/2026 (WIB)*" in message
            assert "Halo Bapak/Ibu Pengawas!" in message
            assert message.endswith("Semoga ujian berjalan lancar!")
            assert "07:30 WIB — Matematika — =1+1 — Kelas: IX-A, IX-B — kode SEB: `001234`" in message
            assert "09:00 WIB — Bahasa Indonesia — Ujian 2 — Kelas: belum tercatat — tanpa SEB" in message
            assert "10:00 WIB — Fisika — Kelas: belum tercatat — kode SEB belum tersedia" in message
        else:
            response.direct_passthrough = False
            sheet = load_workbook(BytesIO(response.get_data())).active
            assert [cell.value for cell in sheet[2]] == ["2026-11-25", "07:30", "=1+1", "001234"]
            assert sheet["C2"].data_type == "s"
