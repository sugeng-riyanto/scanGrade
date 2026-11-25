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
        return SimpleNamespace(data=[
            {"id": "exam-1", "start_at": "2026-11-25T00:30:00Z", "title": "=1+1"}
        ])


@pytest.mark.parametrize("output", ["xlsx", "whatsapp"])
def test_export_uses_school_day_and_only_reveals_the_quit_code(monkeypatch, output):
    app = Flask(__name__)
    query = ExamQuery()
    monkeypatch.setattr(seb, "_supabase", lambda: SimpleNamespace(table=lambda _: query))
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
            assert "07:30 WIB — =1+1 — kode: `001234`" in response.get_json()["text"]
        else:
            response.direct_passthrough = False
            sheet = load_workbook(BytesIO(response.get_data())).active
            assert [cell.value for cell in sheet[2]] == ["2026-11-25", "07:30", "=1+1", "001234"]
            assert sheet["C2"].data_type == "s"
