"""Both halves of an essay answer, where a teacher marks a class from.

`/teacher/grading/<exam_id>` is the queue: one card per essay question, one mark
per card, and a keyboard path through a whole class. It showed the *text* half of an
answer and nothing else, so a paper whose essays are answered on the canvas — which
is what a handwriting exam produces, and what the exam behind this report actually
is (five `essay_canvas` questions, every answer a drawing) — read "Tidak ada jawaban
teks" on every question while the pupil's handwriting sat unread in
`submissions.answers`. The only door to it was each pupil's own detail page.

Three things are held here, and they are different kinds of claim:

* **the pictures are not shipped with the queue.** The list endpoint answers for a
  whole class in one response and a stored canvas is 50-100 KB, so the drawings are
  fetched for the paper a teacher opened. The list is asserted to carry the count of
  drawn pages and no image at all, which is the difference between a page load and a
  download.
* **the drawing is composed onto the sheet it was written on.** The canvas holds ink
  on transparency; `paper` is resolved from the exam's own `pdf_page_urls` by the
  same index the exam page wrote the canvas under, so the page beside a question is
  the page the pupil wrote on.
* **the panel's own arithmetic is executed.** Opening on the printed page rather
  than on page one, remembering the page a teacher stepped to, and not re-fetching a
  paper that is already here are all JavaScript, so they are run under node against
  the shipped methods, the way `test_essay_score_autosave.py` runs the score box.
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "app" / "templates" / "teacher" / "grading_queue.html"
TEACHER = ROOT / "app" / "routes" / "teacher.py"

PAGE = TEMPLATE.read_text(encoding="utf-8")
ROUTES = TEACHER.read_text(encoding="utf-8")

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

EXAM = "exam-1"
TEACHER_ID = "teacher-1"
SUB = "sub-1"

#: Six sheets, so an index and a page number cannot be confused by accident: the
#: page a question is printed on is one more than its index everywhere below.
PAPER = [f"/static/uploads/exams/exam-1/page_{i + 1:03d}.png" for i in range(6)]

CANVAS = "data:image/png;base64," + "A" * 60

#: The shape the exam page writes: an essay answered in handwriting on the sheet the
#: question is printed on, plus a note typed onto a sheet of its own.
ANSWERS = {
    "16": {"type": "essay_canvas", "pages": {"2": {"canvas": CANVAS}}},
    "17": {"type": "essay_canvas", "pages": {"2": {"canvas": ""},
                                             "3": {"textBoxes": [
                                                 {"x_pct": 0.1, "y_pct": 0.2,
                                                  "text": "lihat grafik"}]}}},
    "18": {"type": "essay_canvas", "pages": {"0": {"canvas": "", "textBoxes": []}}},
    "19": {"type": "essay_text", "text": "Energi kinetik bergantung pada massa."},
    "20": {"type": "essay_canvas", "pages": {}},
}


# ── the route: what the panel is handed, and what it is refused ──────────────

class _FakeQuery:
    def __init__(self, owner, table):
        self.owner = owner
        self.table = table
        self.columns = ""
        self._single = False
        self._write = None
        self._eq = []

    def select(self, columns, *a, **k):
        self.columns = columns
        return self

    def eq(self, column, value=None, *a, **k):
        self._eq.append((column, value))
        return self

    def in_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._single = True
        return self

    def update(self, payload, *a, **k):
        """A write, against the same rows a select would have matched.

        Added for the marking routes, which write a mark and read it back: without
        it a fake can only prove what a route *asked* for, never what it left
        behind — and the defect they were written for is exactly a route that
        stores one number and reports another.
        """
        self._write = dict(payload)
        return self

    def _matches(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self._eq if row.get(c) is not None)

    def execute(self):
        data = self.owner.rows.get(self.table)
        if self._write is not None:
            self.owner.writes.append((self.table, dict(self._write),
                                      list(self._eq)))
            for row in (data if isinstance(data, list) else [data]):
                if isinstance(row, dict) and self._matches(row):
                    row.update(self._write)
            return SimpleNamespace(data=data)
        self.owner.reads.append((self.table, self.columns))
        if isinstance(data, list) and self._eq:
            data = [r for r in data if self._matches(r)]
        # `.single()` is the app asking for one row: hand it the row, not the table.
        if self._single and isinstance(data, list):
            data = data[0] if data else None
        return SimpleNamespace(data=_project(data, self.columns))


def _project(data, columns):
    """Only the columns that were asked for, as PostgREST answers.

    Not politeness: a real select returns what it was asked for and nothing else, so
    a fake that hands back the whole row hides the defect this route shipped for one
    version of itself — `answers` was left out of the select, the drawings never
    arrived, and every paper read as one the pupil had left blank.
    """
    if not columns or columns.strip() == "*":
        return data
    wanted = [c.split("(")[0].strip() for c in columns.split(",")]

    def one(row):
        if not isinstance(row, dict):
            return row
        return {k: v for k, v in row.items() if k in wanted}

    return [one(r) for r in data] if isinstance(data, list) else one(data)


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows
        self.reads = []
        self.writes = []

    def table(self, name):
        return _FakeQuery(self, name)

    def wrote(self, table, column=None):
        """Every payload written to `table`, optionally only those naming `column`."""
        return [p for t, p, _eq in self.writes
                if t == table and (column is None or column in p)]


#: Sixteen choice questions, then the five essays the report is about — the shape
#: the real paper has (`Physics Remedial/Enrichment (10A)`, 21 questions, essays
#: 16-20 all `essay_canvas`). The count has to be the real one: the route walks
#: `range(total_questions)`, so a short count would silently describe no question
#: at all.
TYPES = dict({str(i): "mcq" for i in range(16)},
             **{"16": "essay_canvas", "17": "essay_canvas", "18": "essay_canvas",
                "19": "essay_text", "20": "essay_canvas"})
PAGES = dict({str(i): "1" for i in range(16)},
             **{"16": "3", "17": "3", "18": "3", "19": "3", "20": "4"})


def _exam_row(**over):
    row = {
        "id": EXAM,
        "teacher_id": TEACHER_ID,
        "school_id": "school-1",
        "total_questions": 21,
        "question_types": dict(TYPES),
        "question_weights": {str(i): 20.0 for i in range(16, 21)},
        "question_pages": dict(PAGES),
        "pdf_page_urls": list(PAPER),
    }
    row.update(over)
    return row


def _submission_row(**over):
    row = {"id": SUB, "exam_id": EXAM, "student_id": "pupil-1",
           "status": "submitted", "answers": dict(ANSWERS),
           # What `_guard_submission` resolves the owning paper from — the app
           # selects it joined, so the fake has to carry it joined too.
           "exams": {"id": EXAM, "teacher_id": TEACHER_ID, "school_id": "school-1"}}
    row.update(over)
    return row


@pytest.fixture
def env(monkeypatch, app):
    from app.routes import teacher as tmod
    from app.utils import auth as authmod

    rows = {"exams": _exam_row(), "submissions": [_submission_row()]}
    fake = _FakeSupabase(rows)

    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": TEACHER_ID, "email": "t@x", "name": "t", "role": "guru",
        "school_id": "school-1", "status": "active",
    })
    monkeypatch.setattr(tmod, "get_supabase", lambda: fake)

    client = app.test_client()
    client.set_cookie("access_token", "tok")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tok-csrf"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "tok-csrf"
    return client, fake


def _answer(client, submission=SUB, exam=EXAM):
    return client.get(f"/teacher/api/grading-queue/{exam}/answer/{submission}")


class TestTheOnePaperEndpoint:
    def test_a_drawing_arrives_with_the_sheet_it_was_written_on(self, env):
        client, _ = env
        body = _answer(client).get_json()
        page = body["questions"]["16"]["pages"][0]
        assert page["page"] == 2, "the canvas index is the page index, not a number"
        assert page["canvas"] == CANVAS
        assert page["paper"] == PAPER[2], (
            "the drawing is read without the page it was written on: the canvas is "
            "ink on transparency, so the sheet travels with it")

    def test_the_typed_answer_comes_back_whole(self, env):
        client, _ = env
        body = _answer(client).get_json()
        assert body["questions"]["19"]["text"] == ANSWERS["19"]["text"]

    def test_a_note_typed_on_the_sheet_is_an_answer_too(self, env):
        """`canvas` empty and `textBoxes` written: the second way an essay is
        answered on the page, and the difference between a page the pupil answered
        on and a page they merely visited."""
        client, _ = env
        body = _answer(client).get_json()
        pages = body["questions"]["17"]["pages"]
        assert [p["page"] for p in pages] == [3], (
            "the empty page 2 answered nothing and must not be shown as a drawing")
        assert pages[0]["canvas"] == ""
        assert pages[0]["textBoxes"][0]["text"] == "lihat grafik"
        assert pages[0]["paper"] == PAPER[3]

    def test_a_page_with_no_ink_and_no_note_is_not_a_page(self, env):
        client, _ = env
        assert _answer(client).get_json()["questions"]["18"]["pages"] == []

    def test_an_untouched_question_is_an_empty_list_not_a_missing_key(self, env):
        client, _ = env
        body = _answer(client).get_json()
        assert body["questions"]["20"]["pages"] == []
        assert body["questions"]["20"]["text"] == ""

    def test_only_essay_questions_are_described(self, env):
        client, _ = env
        questions = _answer(client).get_json()["questions"]
        assert set(questions) == {"16", "17", "18", "19", "20"}
        assert "0" not in questions, "a choice question has no essay answer"

    def test_the_question_carries_the_sheet_it_was_printed_on(self, env):
        """So the panel opens where the pupil wrote, not on page one of the paper."""
        client, _ = env
        assert _answer(client).get_json()["questions"]["16"]["question_page"] == "3"

    def test_a_paper_of_another_exam_is_refused(self, env):
        """The submission is the caller's to grade and still refused: it belongs to
        another paper, and composing its answers against this exam's sheets would
        put a pupil's handwriting on the wrong page."""
        client, fake = env
        other = {"id": "exam-other", "teacher_id": TEACHER_ID,
                 "school_id": "school-1"}
        fake.rows = {"exams": _exam_row(),
                     "submissions": [_submission_row(exam_id="exam-other",
                                                    exams=other)]}
        resp = _answer(client)
        assert resp.status_code == 404
        assert not any(table == "exams" for table, _ in fake.reads), (
            "the wrong paper's answers were composed against this exam's sheets")

    def test_the_endpoint_asks_for_the_column_the_drawings_live_in(self, env):
        """The defect this file was written after: the guard was asked for the row
        without `answers`, so every question came back with no pages and the panel
        would have shown a blank paper for a pupil who had written on it."""
        client, fake = env
        _answer(client)
        asked = [columns for table, columns in fake.reads if table == "submissions"]
        assert asked, "the endpoint never read the submission"
        assert "answers" in asked[0], (
            "the drawings live in `answers` and the select does not name it")

    def test_the_ownership_guard_runs_before_anything_is_read(self, env, monkeypatch):
        client, fake = env
        from app.routes import teacher as tmod

        refusal = ("refused", 403)
        monkeypatch.setattr(tmod, "_guard_submission",
                            lambda *a, **k: (None, refusal), raising=False)
        assert _answer(client).status_code == 403
        assert fake.reads == [], "a refused caller still read the answers"

    def test_the_browser_asks_the_door_that_exists(self):
        """The two halves of one address, checked as strings: the route the panel
        fetches and the route the blueprint declares."""
        assert ("'/teacher/api/grading-queue/' + this.examId + '/answer/' + sub.id"
                in PAGE)
        assert ('@teacher_bp.route("/api/grading-queue/<exam_id>/answer/<submission_id>")'
                in ROUTES), "the panel fetches a door the app does not open"


class TestTheListStillCarriesNoPictures:
    """Why the drawings are a second request at all."""

    def test_the_list_says_how_many_pages_and_names_no_image(self, env):
        client, _ = env
        body = client.get(f"/teacher/api/grading-queue/{EXAM}").get_json()
        row = body["submissions"][0]
        per_question = {q["index"]: q for q in row["questions"]}
        assert per_question["16"]["canvas_pages"] == 1
        assert per_question["17"]["canvas_pages"] == 1
        assert per_question["18"]["canvas_pages"] == 0
        raw = client.get(f"/teacher/api/grading-queue/{EXAM}").get_data(as_text=True)
        assert "data:image" not in raw, (
            "the queue's list payload carries the drawings, so one page load is "
            "every pupil's handwriting")

    def test_a_page_that_was_only_visited_is_not_counted_as_drawn(self, env):
        """`canvas` is written as an empty string for a page the pupil opened and
        did not draw on, so counting keys would mark every question answered."""
        client, _ = env
        body = client.get(f"/teacher/api/grading-queue/{EXAM}").get_json()
        per_question = {q["index"]: q for q in body["submissions"][0]["questions"]}
        assert per_question["18"]["canvas_pages"] == 0

    def test_the_count_is_the_two_ways_a_page_is_answered(self, env):
        client, _ = env
        body = client.get(f"/teacher/api/grading-queue/{EXAM}").get_json()
        per_question = {q["index"]: q for q in body["submissions"][0]["questions"]}
        assert per_question["17"]["canvas_pages"] == 1, (
            "a note typed onto the sheet is an answer, exactly as ink is")


# ── the panel: both halves, and neither one guessed ──────────────────────────

def _card() -> str:
    """The per-question card, from its header to the bottom action bar."""
    return PAGE.split("<!-- Grading content area -->", 1)[1].split(
        "<!-- Bottom action bar -->", 1)[0]


class TestTheCardShowsBothHalves:
    def test_the_panel_has_a_text_half_and_a_canvas_half(self):
        card = _card()
        assert "data-answer-panels" in card
        assert "data-answer-text" in card, "the typed answer has no panel"
        assert "data-answer-canvas" in card, (
            "the canvas is still missing from the card the report was about")

    def test_the_sheet_goes_under_the_drawing(self):
        card = _card()
        sheet = card.split("data-answer-sheet", 1)[1]
        paper = sheet.index('answerPage(q).paper')
        canvas = sheet.index('answerPage(q).canvas')
        assert paper < canvas, (
            "the drawing is painted under the sheet it was written on")

    def test_the_truncated_preview_is_gone(self):
        """The card read a hundred characters of the answer and said nothing about
        the cut; the whole answer arrives from the endpoint now."""
        assert 'x-text="q.preview"' not in PAGE
        assert "answerText(q)" in PAGE

    def test_the_header_says_straight_away_that_a_question_was_drawn(self):
        assert 'x-text="\'Coretan: \' + q.canvas_pages + \' hal\'"' in PAGE

    def test_an_unanswered_question_says_so_in_words(self):
        card = _card()
        assert "Tidak ada jawaban teks maupun coretan untuk soal ini" in card

    def test_loading_a_failure_and_an_empty_answer_are_three_different_notes(self):
        card = _card()
        assert "Memuat jawaban..." in card
        assert "answersError" in card
        assert "loadAnswers(true)" in card, "a failed fetch cannot be retried"

    def test_the_copy_is_indonesian_because_the_page_pins_it(self):
        """`{% set content_lang = 'id' %}`: a `t()` pair here would be the one string
        that switched, under a document that says Indonesian."""
        assert "{% set content_lang = 'id' %}" in PAGE
        card = _card()
        assert "t('" not in card and "sgT('" not in card

    def test_the_page_nav_is_the_page_nav(self):
        card = _card()
        assert "stepAnswerPage(q, -1)" in card
        assert "stepAnswerPage(q, 1)" in card
        assert "answerPageLabel(q)" in card


# ── the card, rendered ──────────────────────────────────────────────────────

CARD_CONTEXT = {
    "id": EXAM,
    "title": "Physics Remedial/Enrichment (10A)",
    "subject": "Fisika",
    "total_questions": 3,
    "question_types": {"0": "mcq", "1": "essay_canvas", "2": "essay_text"},
    "question_weights": {"1": 50.0, "2": 50.0},
    "answer_key": {"0": "A"},
}


@contextlib.contextmanager
def _signed_in(app, path):
    """A request context with a teacher in it: `base.html` reads `g` for the
    chrome, so a template that extends it cannot be rendered without one."""
    from flask import g

    with app.test_request_context(path):
        g.user_id = TEACHER_ID
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        yield


@pytest.fixture(scope="class")
def rendered(app):
    with _signed_in(app, f"/teacher/grading/{EXAM}"):
        html = app.jinja_env.get_template("teacher/grading_queue.html").render(
            exam=CARD_CONTEXT)
    return html


class TestTheCardRenders:
    def test_the_answer_panel_is_there_once_per_card(self, rendered):
        assert rendered.count("data-answer-panels") == 1, (
            "the card is one x-for template, so it carries one panel")
        assert "data-answer-text" in rendered
        assert "data-answer-canvas" in rendered
        assert "data-answer-sheet" in rendered

    def test_nothing_jinja_left_behind(self, rendered):
        assert "{{" not in rendered and "{%" not in rendered
        assert "Undefined" not in rendered

    def test_the_exam_id_reaches_the_request_the_panel_makes(self, rendered):
        assert "examId: 'exam-1'" in rendered, (
            "the panel builds its URL from this, and a missing id fetches nothing")

    def test_the_rendered_card_is_indonesian_because_the_page_pins_it(self, rendered):
        # The card only: `base.html`'s own chrome follows the toggle and is rendered
        # around it, so the region is bounded rather than read to the end of the page.
        card = rendered.split("<!-- Grading content area -->", 1)[1]\
                      .split("<!-- Bottom action bar -->", 1)[0]
        assert "Jawaban teks" in card and "Coretan siswa" in card
        # A call, not the `t('` inside `alert('` — the same boundary the i18n
        # sweep uses.
        assert not re.search(r"(?<![\w.$])(?:t|sgT)\('", card), (
            "a pair on a page pinned to Indonesian is copy that can never switch")


# ── the panel's arithmetic, executed ────────────────────────────────────────

METHODS = ("loadAnswers", "answerData", "answerText", "answerChars", "answerPages",
           "answerPageIdx", "answerPage", "answerBoxes", "answerPageLabel",
           "stepAnswerPage")


def _method(name: str) -> str:
    match = re.search(rf"\n\s*{re.escape(name)}\(", PAGE)
    assert match, f"{name} is not on the Alpine object any more"
    begin = PAGE.index("{", match.end())
    depth = 0
    for i in range(begin, len(PAGE)):
        if PAGE[i] == "{":
            depth += 1
        elif PAGE[i] == "}":
            depth -= 1
            if depth == 0:
                return "function " + PAGE[match.start():i + 1].strip().rstrip(",")
    raise AssertionError(f"{name} is unterminated")


HARNESS = """
%(methods)s

const QUESTIONS = {
    '16': {index: '16', canvas_pages: 1},
    '17': {index: '17', canvas_pages: 1},
    '18': {index: '18', canvas_pages: 0},
};
const PAPER = %(paper)s;

function makeApp(loaded) {
  return {
    examId: 'exam-1',
    current: {id: 'sub-1'},
    answers: loaded || {},
    answersLoading: false,
    answersError: '',
    qpage: {},
    %(methods_short)s
  };
}

const out = {};
const PAYLOAD = {
  questions: {
    // Drawn on sheets 1 and 3, printed on sheet 4: the sheet the question was
    // printed on carries no drawing, so this one opens on the pupil's first page.
    '16': {index: 16, text: '',
           pages: [{page: 0, canvas: 'C1', paper: PAPER[0], textBoxes: []},
                   {page: 2, canvas: 'C3', paper: PAPER[2], textBoxes: []}],
           question_page: '4'},
    // Written across sheets 1, 3 and 5 and printed on sheet 5: the panel opens on
    // that sheet - index 2 of the drawing, not the first entry.
    '17': {index: 17, text: '',
           pages: [{page: 0, canvas: 'C1', paper: PAPER[0], textBoxes: []},
                   {page: 2, canvas: 'C3', paper: PAPER[2],
                    textBoxes: [{x_pct: 0.1, y_pct: 0.2, text: 'lihat grafik'}]},
                   {page: 4, canvas: 'C5', paper: PAPER[4], textBoxes: []}],
           question_page: '5'},
    '18': {index: 18, text: 'Dua gaya bekerja pada balok.',
           pages: [], question_page: '3'},
    // One drawn page is not a pager.
    '19': {index: 19, text: '', question_page: '3',
           pages: [{page: 2, canvas: 'C3', paper: PAPER[2], textBoxes: []}]},
  },
};

let calls = [];
let respond = () => Promise.resolve({ok: true, json: () => Promise.resolve(PAYLOAD)});
global.fetch = (url) => { calls.push(url); return respond(); };

(async () => {
  const app = makeApp({});
  app.loadAnswers();
  await new Promise(r => setTimeout(r, 30));
  out.fetched_once = calls.length;
  out.url = calls[0];
  out.loaded_keys = Object.keys(app.answers['sub-1'] || {});

  // A second visit asks nothing: the paper is already here.
  app.loadAnswers();
  await new Promise(r => setTimeout(r, 30));
  out.cached = calls.length;

  // ...unless the teacher asks for it again.
  app.loadAnswers(true);
  await new Promise(r => setTimeout(r, 30));
  out.forced = calls.length;

  // A failed fetch says so, and does not look like an unanswered question.
  respond = () => Promise.resolve({ok: false, json: () => Promise.resolve({error: 'Tidak boleh'})});
  const bad = makeApp({});
  bad.loadAnswers();
  await new Promise(r => setTimeout(r, 30));
  out.error = bad.answersError;
  out.error_loaded = Object.keys(bad.answers).length;

  // The panel itself.
  const panel = makeApp({'sub-1': PAYLOAD.questions});
  out.text_whole = panel.answerText(QUESTIONS['18']);
  out.chars = panel.answerChars(QUESTIONS['18']);
  out.pages_empty = panel.answerPages(QUESTIONS['18']).length;

  // A question printed on sheet 5 opens on sheet 5 - the third entry of a drawing
  // that starts on sheet 1.
  const q17 = {index: '17'};
  out.opens_on_printed = panel.answerPageIdx(q17);
  out.opens_page_no = panel.answerPage(q17).page;

  // Drawn twice, printed on a sheet neither drawing is on: first drawn page.
  out.falls_back = panel.answerPageIdx({index: '16'});

  // Stepping, and the step is remembered. Sheet 5 is the last one, so a step
  // forward from there goes nowhere.
  panel.stepAnswerPage(q17, 1);
  out.clamped = panel.answerPageIdx(q17);
  panel.stepAnswerPage(q17, -1);
  out.stepped = panel.answerPageIdx(q17);
  out.step_label = panel.answerPageLabel(q17);
  out.step_boxes = panel.answerBoxes(q17).length;
  panel.stepAnswerPage(q17, -1);
  out.back = panel.answerPageIdx(q17);

  // One page is not a pager.
  const before = panel.answerPageIdx({index: '19'});
  panel.stepAnswerPage({index: '19'}, 1);
  out.single_page_unchanged = panel.answerPageIdx({index: '19'}) === before;

  // A question with no answer at all resolves to nothing, not to an error.
  out.no_answer = panel.answerText({index: '20'});
  out.no_pages = panel.answerPages({index: '20'}).length;
  out.no_label = panel.answerPageLabel({index: '20'});

  console.log(JSON.stringify(out));
})();
"""


def _harness() -> str:
    return HARNESS % {
        "methods": "\n".join(_method(name) for name in METHODS),
        "methods_short": ",\n    ".join(_method(name)[len("function "):]
                                        for name in METHODS),
        "paper": json.dumps(PAPER),
    }


@needs_node
class TestThePanelArithmetic:
    @pytest.fixture(scope="class")
    def result(self):
        done = subprocess.run([NODE, "-e", _harness()], capture_output=True,
                              text=True, encoding="utf-8", timeout=90)
        assert done.returncode == 0, done.stderr[-2000:]
        return json.loads(done.stdout.strip())

    def test_the_paper_is_asked_for_once(self, result):
        assert result["fetched_once"] == 1
        assert result["url"] == "/teacher/api/grading-queue/exam-1/answer/sub-1"
        assert result["loaded_keys"] == ["16", "17", "18", "19"]

    def test_a_paper_already_here_is_not_fetched_again(self, result):
        assert result["cached"] == 1, "moving down the queue and back re-downloads"

    def test_a_failure_can_be_retried(self, result):
        assert result["forced"] == 2

    def test_a_failed_fetch_says_so_instead_of_reading_as_an_empty_answer(self, result):
        assert result["error"] == "Tidak boleh"
        assert result["error_loaded"] == 0

    def test_the_text_shown_is_the_text_stored(self, result):
        assert result["text_whole"] == "Dua gaya bekerja pada balok."
        assert result["chars"] == "28 karakter"
        assert "Dua gaya" not in str(result["pages_empty"])

    def test_the_panel_opens_on_the_sheet_the_question_was_printed_on(self, result):
        """By the *page*, never by position: the drawing starts on sheet 1 and the
        question is printed on sheet 5, so an implementation that handed back the
        first entry would read 0 and page 1."""
        assert result["opens_on_printed"] == 2
        assert result["opens_page_no"] == 4, (
            "the panel is showing a different sheet than the question's own")

    def test_a_question_whose_sheet_carries_no_drawing_opens_on_the_first_one(self, result):
        assert result["falls_back"] == 0

    def test_stepping_moves_one_page_and_shows_what_is_on_it(self, result):
        assert result["stepped"] == 1
        assert result["step_label"] == "Hal 3 (2/3)"
        assert result["step_boxes"] == 1, "the note on the page is not shown"

    def test_stepping_stops_at_the_last_page(self, result):
        assert result["clamped"] == 2, "a step past the last page moved anyway"
        assert result["back"] == 0

    def test_a_single_page_is_not_a_pager(self, result):
        assert result["single_page_unchanged"] is True

    def test_an_unanswered_question_resolves_to_nothing(self, result):
        assert result["no_answer"] == ""
        assert result["no_pages"] == 0
        assert result["no_label"] == ""
