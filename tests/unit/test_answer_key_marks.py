"""What a question is worth, and the answer an essay is compared against.

`/teacher/exams/<id>/answer-keys` said, in its own copy, *"Kunci tipe soal lain
diatur di editor ujian"* — and for an essay that was not true: the page rendered
non-choice questions read-only, and an essay-only paper got a "no multiple choice"
card, no marks control, and (because the Save button lived inside the choice
branch) no way to save anything at all.

Two facts make this page the right home for both controls. The marks are the
denominator every score on the paper is divided by, so a question's key and its
worth are one decision, not two. And the essay reference belongs in
`exams.question_rubrics`, which is the column `ai_grading` already sends to the
model as `rubric` — a key typed here is a key the AI is given as the thing to
compare an answer against, with no second path to keep in step.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

EXAM = "exam-1"
TEACHER = "teacher-1"


class _FakeSupabase:
    def __init__(self):
        self.updates = []

    def table(self, name):
        fake = self

        class _Query:
            def update(self, payload):
                fake.updates.append(dict(payload))
                return self

            def eq(self, *a, **k):
                return self

            def execute(self):
                return SimpleNamespace(data=[])

        return _Query()


def _exam(**over):
    row = {
        "id": EXAM,
        "teacher_id": TEACHER,
        "school_id": "school-1",
        "title": "Ulangan Fotosintesis",
        # Question 0 is a choice question with a key; 1 and 2 are essays with no
        # key at all — the shape that had no editor.
        "question_types": json.dumps({"0": "mcq", "1": "essay", "2": "essay_text"}),
        "answer_key": json.dumps({"0": "B"}),
        "question_weights": json.dumps({"0": 10.0, "1": 15.2}),
        "question_rubrics": json.dumps({"1": "Sebutkan kloroplas dan tiga faktor."}),
        "total_questions": 3,
    }
    row.update(over)
    return row


@pytest.fixture
def env(monkeypatch, app):
    from app.routes import teacher as tmod
    from app.utils import auth as authmod
    from app.services import academic_year

    fake = _FakeSupabase()
    exam = _exam()

    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": TEACHER, "email": "t@x", "name": "t", "role": "guru",
        "school_id": "school-1", "status": "active",
    })
    monkeypatch.setattr(tmod, "get_supabase", lambda: fake)
    monkeypatch.setattr(tmod, "_guard_exam", lambda *a, **k: (exam, None))
    monkeypatch.setattr(tmod, "_recalculate_scores", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(tmod, "_invalidate_teacher_dashboard", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(academic_year, "year_of_exam", lambda *a, **k: "year-1")
    monkeypatch.setattr(academic_year, "write_refusal", lambda *a, **k: None)

    client = app.test_client()
    client.set_cookie("access_token", "tok")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tok-csrf"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "tok-csrf"
    return client, fake, exam


def _post(client, data):
    return client.post(f"/teacher/exams/{EXAM}/answer-keys", data=data)


# ── marks ───────────────────────────────────────────────────────────────────

class TestTheMarks:
    def test_a_question_gets_its_own_marks(self, env):
        client, fake, _ = env
        resp = _post(client, {"points_1": "12.5"})
        assert resp.status_code in (302, 303)
        assert fake.updates, "the marks edit wrote nothing"
        weights = json.loads(fake.updates[0]["question_weights"])
        assert weights["1"] == 12.5
        # Untouched questions keep what they had.
        assert weights["0"] == 10.0

    def test_an_empty_box_clears_the_marks(self, env):
        client, fake, _ = env
        _post(client, {"points_0": ""})
        weights = json.loads(fake.updates[0]["question_weights"])
        assert "0" not in weights, "an emptied box has to be the delete half of the CRUD"

    def test_marks_above_the_ceiling_are_refused_and_named(self, env):
        client, fake, _ = env
        _post(client, {"points_1": "1000"})
        assert fake.updates == []
        # The refusal names the question and the range.
        with client.session_transaction() as sess:
            flashes = sess.get("_flashes") or []
        text = " ".join(m for _c, m in flashes)
        assert "1" in text and "100" in text

    def test_a_comma_decimal_is_read(self, env):
        """An Indonesian keyboard writes 12,5 and the browser posts it verbatim."""
        client, fake, _ = env
        _post(client, {"points_2": "7,25"})
        assert json.loads(fake.updates[0]["question_weights"])["2"] == 7.25

    def test_saving_the_same_marks_writes_nothing(self, env):
        client, fake, _ = env
        _post(client, {"points_1": "15.2", "points_0": "10"})
        assert fake.updates == []


# ── the essay reference key ─────────────────────────────────────────────────

class TestTheEssayReference:
    def test_the_reference_is_stored_where_the_ai_reads_it(self, env):
        client, fake, _ = env
        _post(client, {"refkey_2": "Kloroplas; cahaya, CO2, suhu."})
        assert fake.updates, "the reference key wrote nothing"
        rubrics = json.loads(fake.updates[0]["question_rubrics"])
        assert rubrics["2"] == "Kloroplas; cahaya, CO2, suhu."
        # The existing one is kept, not replaced by the edit of another question.
        assert rubrics["1"] == "Sebutkan kloroplas dan tiga faktor."

    def test_clearing_the_reference_removes_it(self, env):
        client, fake, _ = env
        _post(client, {"refkey_1": "   "})
        assert "1" not in json.loads(fake.updates[0]["question_rubrics"])

    def test_it_lands_in_the_column_ai_grading_passes_as_the_rubric(self):
        """The claim the page makes, checked against the grader rather than stated.

        The box writes `exams.question_rubrics`, and that stays a claim on the page
        until the grader is shown reading the same column and handing it on as the
        rubric. The behavioural half — the teacher's words are the rubric that is
        sent — is in `test_ai_essay_grading`; this is the other half, that the bulk
        path's rubric *comes from* that column and is fetched with the rest of the
        exam, because PostgREST answers a column left out of a select with nothing
        rather than an error.
        """
        from pathlib import Path
        src = Path("app/services/ai_grading.py").read_text(encoding="utf-8")
        assert 'exam.get("question_rubrics"' in src, (
            "the bulk path no longer reads the column the page writes")
        assert "rubric=rubric" in src, (
            "the rubric is read but not handed to the grading call")
        fetched = [chunk for chunk in src.split(".select(")
                   if "question_rubrics" in chunk[:200]]
        assert fetched, (
            "the rubric column is read but never selected — an unselected column "
            "comes back as nothing, so every reference would grade as empty")


# ── the page ────────────────────────────────────────────────────────────────

class TestThePage:
    def test_every_question_gets_a_marks_box(self, env):
        client, _fake, _ = env
        html = client.get(f"/teacher/exams/{EXAM}/answer-keys").get_data(as_text=True)
        for index in ("0", "1", "2"):
            assert f'name="points_{index}"' in html, f"question {index} has no marks box"

    def test_an_essay_gets_a_reference_box(self, env):
        client, _fake, _ = env
        html = client.get(f"/teacher/exams/{EXAM}/answer-keys").get_data(as_text=True)
        assert 'name="refkey_1"' in html and 'name="refkey_2"' in html
        assert "Sebutkan kloroplas dan tiga faktor." in html, "the stored key is not shown"

    def test_an_essay_only_paper_still_has_a_save_button(self, monkeypatch, app):
        from app.routes import teacher as tmod
        from app.utils import auth as authmod
        from app.services import academic_year

        fake = _FakeSupabase()
        exam = _exam(question_types=json.dumps({"0": "essay", "1": "essay_text"}),
                     answer_key=json.dumps({}), total_questions=2,
                     question_weights=json.dumps({}), question_rubrics=json.dumps({}))
        monkeypatch.setattr(authmod, "_session_for", lambda token: {
            "user_id": TEACHER, "email": "t@x", "name": "t", "role": "guru",
            "school_id": "school-1", "status": "active"})
        monkeypatch.setattr(tmod, "get_supabase", lambda: fake)
        monkeypatch.setattr(tmod, "_guard_exam", lambda *a, **k: (exam, None))
        monkeypatch.setattr(tmod, "_recalculate_scores", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(tmod, "_invalidate_teacher_dashboard", lambda *a, **k: None,
                            raising=False)
        monkeypatch.setattr(academic_year, "year_of_exam", lambda *a, **k: "year-1")
        monkeypatch.setattr(academic_year, "write_refusal", lambda *a, **k: None)
        client = app.test_client()
        client.set_cookie("access_token", "tok")

        html = client.get(f"/teacher/exams/{EXAM}/answer-keys").get_data(as_text=True)

        assert 'name="refkey_0"' in html
        assert 'form="answer-form" type="submit"' in html, (
            "an all-essay paper has no save button — it used to live inside the "
            "multiple-choice branch")
        assert "Tidak ada soal pilihan ganda" in html
