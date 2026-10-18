"""One mark, whichever screen a teacher marks from.

Three screens mark one paper: `/teacher/grade/<submission>` (the paper's own page),
`/teacher/grading/<exam>` (the queue) and `/teacher/grade/<exam>/student/<id>` (the
per-pupil essay list). They disagreed, and the report was specific — the paper's page
said "Skor #17 (15.2 poin)" while the queue said "Bobot: 0" beside a box marked
`/ 100`, and the total each of them showed was its own arithmetic:

* **the queue was told no weight.** The list endpoint built the weight map itself and
  never put a `weight` on a card's question, so `q.weight || 0` printed zero for every
  question of a paper whose essays are worth 15.2 each. It also divided 100 across the
  *essays* when a paper stored no weights, dropping the objectives from the total.
* **both screens computed a final in JavaScript** out of the essay marks alone. A
  paper's final also carries its objective questions and its penalty, so a paper at
  60% on its essays showed 60 where the results page showed 84 — and the number was
  *posted* as `final_score`, so the route stored one arithmetic and replied with
  another.
* **one of the screens was not running at all.** `grade_detail.html` carried a JS
  string with a literal line break in it, which is a SyntaxError, and a SyntaxError in
  an inline script takes every control on the page with it.

So the contract held here is the one the rest of the app already keeps: `exam_scoring`
is the arithmetic, the browser asks for a number rather than deriving one, and the
value a route returns is the value it stored.
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import g

from app.services import exam_scoring
from tests.unit.test_grading_queue_canvas import (
    CARD_CONTEXT, NODE, _FakeSupabase, _signed_in, needs_node,
)

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "app" / "templates" / "teacher" / "grading_queue.html"
DETAIL = ROOT / "app" / "templates" / "teacher" / "grade_detail.html"
TEACHER = ROOT / "app" / "routes" / "teacher.py"

QUEUE_SRC = QUEUE.read_text(encoding="utf-8")
DETAIL_SRC = DETAIL.read_text(encoding="utf-8")
ROUTES_SRC = TEACHER.read_text(encoding="utf-8")

EXAM = "exam-1"
TEACHER_ID = "teacher-1"
SUB = "sub-1"
ESSAYS = [16, 17, 18, 19, 20]

#: The paper behind the report, to the point: 16 choice questions worth 1.5 each
#: (24 points) and five `essay_canvas` essays worth 15.2 each (76). The numbers
#: matter — 5 × 15.2 is what a card must say, and 24 + 76 is the total a queue that
#: summed the essays alone could never reach.
TYPES = dict({str(i): "mcq" for i in range(16)},
             **{str(i): "essay_canvas" for i in ESSAYS})
KEY = {str(i): "A" for i in range(16)}
WEIGHTS = dict({str(i): 1.5 for i in range(16)},
               **{str(i): 15.2 for i in ESSAYS})
#: Every objective question answered correctly, every essay blank until a teacher
#: marks it — so a paper's mark is 24 + whatever the essays pay, and a number that
#: ignored the objectives is visible as 0 rather than as a small difference.
ANSWERS = {str(i): "A" for i in range(16)}


def _exam_row(**over):
    row = {"id": EXAM, "teacher_id": TEACHER_ID, "school_id": "school-1",
           "title": "Fisika Remedial", "subject": "Fisika", "total_questions": 21,
           "question_types": dict(TYPES), "question_weights": dict(WEIGHTS),
           "answer_key": dict(KEY)}
    row.update(over)
    return row


def _submission_row(**over):
    row = {"id": SUB, "exam_id": EXAM, "student_id": "pupil-1",
           "status": "submitted", "answers": dict(ANSWERS), "penalty": 0,
           "teacher_feedback": {}, "score": 24, "final_score": None,
           "profiles": {"full_name": "Ani"},
           # What `_guard_submission` resolves the owning paper from.
           "exams": {"id": EXAM, "teacher_id": TEACHER_ID, "school_id": "school-1"}}
    row.update(over)
    return row


@pytest.fixture
def fake():
    return _FakeSupabase({"exams": _exam_row(), "submissions": [_submission_row()]})


def _bare(view):
    """The view's own body, with the guards peeled off.

    Login, school access and the year lock are tested by their own suites; what is
    under test here is what a marking route writes and what it reports back.
    """
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def _call(app, monkeypatch, fake, name, path, method="POST", body=None, **kwargs):
    from app.routes import teacher as tmod

    monkeypatch.setattr(tmod, "get_supabase", lambda: fake)
    with app.test_request_context(path, method=method, json=body):
        g.user_id = TEACHER_ID
        g.user_role = "guru"
        g.user_school_id = "school-1"
        return _bare(getattr(tmod, name))(**kwargs)


def _override(app, monkeypatch, fake, final=None, scores=None):
    body = {"teacher_feedback": {"scores": scores or {}}}
    if final is not None:
        body["final_score"] = final
    return _call(app, monkeypatch, fake, "override_score",
                 f"/teacher/grade/{SUB}/override", body=body, submission_id=SUB)


ALL_BLANK = {str(i): 0 for i in ESSAYS}
HALF = {str(i): 50 for i in ESSAYS}


def _stored(fake):
    return fake.rows["submissions"][0].get("final_score")


# ── what the queue is told ─────────────────────────────────────────────────


class TestTheQueueKnowsWhatAQuestionIsWorth:
    def test_the_card_carries_the_points_the_mark_is_computed_with(self, app, monkeypatch, fake):
        body = _call(app, monkeypatch, fake, "api_grading_queue",
                     f"/teacher/api/grading-queue/{EXAM}", method="GET",
                     exam_id=EXAM).get_json()
        weights = {int(q["index"]): q["weight"]
                   for q in body["submissions"][0]["questions"]}
        assert weights == {i: 15.2 for i in ESSAYS}, (
            "the queue answered no weight at all, so every card printed \"Bobot: 0\"")

    def test_the_points_are_the_ones_the_total_is_made_of(self, app, monkeypatch, fake):
        """24 objective + 76 essay = 100, not 100 divided among the essays."""
        body = _call(app, monkeypatch, fake, "api_grading_queue",
                     f"/teacher/api/grading-queue/{EXAM}", method="GET",
                     exam_id=EXAM).get_json()
        essay_points = sum(q["weight"] for q in body["submissions"][0]["questions"])
        assert essay_points == pytest.approx(76.0)

    def test_a_stored_zero_is_a_mark_not_a_missing_one(self, app, monkeypatch, fake):
        fake.rows["submissions"][0]["final_score"] = 0
        body = _call(app, monkeypatch, fake, "api_grading_queue",
                     f"/teacher/api/grading-queue/{EXAM}", method="GET",
                     exam_id=EXAM).get_json()
        assert body["submissions"][0]["score"] == 0, (
            "`or` let a paper that scored nothing fall through to its objective score")


# ── what the route stores, and what it says it stored ──────────────────────


class TestTheFinalIsTheServers:
    def test_a_final_sent_by_the_browser_is_not_the_mark_that_is_stored(self, app, monkeypatch, fake):
        """The queue posted its own sum; the row has to carry the real one."""
        body = _override(app, monkeypatch, fake, final=60, scores=ALL_BLANK).get_json()
        assert _stored(fake) == pytest.approx(24.0), (
            "essays at 0 on a paper whose objectives are all correct is 24, not 60")
        assert body["final_score"] == pytest.approx(24.0), (
            "the reply echoed the posted number rather than the stored one")

    def test_a_marked_essay_pays_its_own_share(self, app, monkeypatch, fake):
        _override(app, monkeypatch, fake, final=0, scores=HALF)
        assert _stored(fake) == pytest.approx(24.0 + 5 * 7.6)

    def test_the_reply_is_the_number_in_the_row(self, app, monkeypatch, fake):
        body = _override(app, monkeypatch, fake, final=99, scores=HALF).get_json()
        assert body["final_score"] == _stored(fake)

    def test_the_penalty_is_taken_off(self, app, monkeypatch, fake):
        fake.rows["submissions"][0]["penalty"] = 10
        _override(app, monkeypatch, fake, scores=ALL_BLANK)
        assert _stored(fake) == pytest.approx(14.0)

    def test_the_preview_answers_the_number_the_save_stores(self, app, monkeypatch, fake):
        preview = _call(app, monkeypatch, fake, "preview_score",
                        f"/teacher/grade/{SUB}/preview",
                        body={"teacher_feedback": {"scores": HALF}},
                        submission_id=SUB).get_json()
        _override(app, monkeypatch, fake, scores=HALF)
        assert preview["final_score"] == pytest.approx(_stored(fake))

    def test_the_preview_writes_nothing(self, app, monkeypatch, fake):
        _call(app, monkeypatch, fake, "preview_score",
              f"/teacher/grade/{SUB}/preview",
              body={"teacher_feedback": {"scores": HALF}}, submission_id=SUB)
        assert fake.writes == [], "a preview that writes is a save with no audit trail"

    def test_the_preview_reports_the_objective_score_too(self, app, monkeypatch, fake):
        body = _call(app, monkeypatch, fake, "preview_score",
                     f"/teacher/grade/{SUB}/preview",
                     body={"teacher_feedback": {"scores": HALF}},
                     submission_id=SUB).get_json()
        assert body["score"] == pytest.approx(100.0), (
            "all 16 choice questions are right, so the objective score is 100%")

    def test_the_arithmetic_is_the_paper_s_own(self):
        """The numbers above are `exam_scoring`'s, not a fixture's."""
        parts = exam_scoring.exam_parts(_exam_row())
        assert sum(parts.weights.values()) == pytest.approx(100.0)
        assert parts.weights["16"] == pytest.approx(15.2)


# ── what the pages post ───────────────────────────────────────────────────


def _method(src: str, name: str) -> str:
    """A method body `name(...) { ... }` out of an Alpine object literal."""
    match = re.search(rf"\n\s+{re.escape(name)}\(", src)
    assert match, f"{name} is not defined"
    i = src.index("{", match.end())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[match.start():j + 1]
    raise AssertionError(f"unterminated method {name}")


def _function(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unterminated function {name}")


class TestThePagesSendMarksAndReadTheMark:
    def test_the_queue_posts_no_final(self):
        body = _method(QUEUE_SRC, "saveGrade")
        payload = body.split("JSON.stringify(", 1)[1]
        assert "final_score" not in payload.split(")", 1)[0], (
            "the queue sent a final built from the essay marks alone")
        assert "teacher_feedback" in payload

    def test_the_queue_shows_the_final_the_server_returned(self):
        body = _method(QUEUE_SRC, "saveGrade")
        assert "d.final_score" in body, (
            "the header kept showing the page's own sum after a save")

    def test_the_queue_no_longer_computes_a_total(self):
        assert "computeFinalScore" not in QUEUE_SRC, (
            "a second implementation of the scoring rule survived on the page")

    def test_the_queue_converts_the_mark_to_points(self):
        assert 'data-points-gain' in QUEUE_SRC
        assert "pointsGain(q)" in QUEUE_SRC
        assert "+ ' poin'" in QUEUE_SRC, "the card printed a bare percentage of nothing"

    def test_the_rendered_card_shows_points_and_pays_them(self, app):
        with _signed_in(app, f"/teacher/grading/{EXAM}"):
            html = app.jinja_env.get_template("teacher/grading_queue.html").render(
                exam=CARD_CONTEXT)
        assert "data-points-gain" in html, "the conversion never reached the card"
        assert "'Bobot: '" in html and "+ ' poin'" in html

    def test_the_papers_page_posts_no_final(self):
        body = _method(DETAIL_SRC, "saveAll")
        assert "final_score" not in body.split("JSON.stringify(", 1)[1].split(")", 1)[0]

    def test_the_papers_page_asks_the_server_for_the_running_total(self):
        body = _function(DETAIL_SRC, "calcFinal")
        assert "/preview" in body, "the page still derived the final in the browser"
        assert "answerKey" not in body and "question_weights" not in body, (
            "the page kept its own copy of the scoring rule")
        assert "_scoresSignature" in body, (
            "the page polls this every two seconds; an unchanged mark must not ask")


# ── the page's own arithmetic, run ────────────────────────────────────────


POINTS_HARNESS = """
const app = {
  qscores: {16: '50', 17: '0', 18: '', 19: '100', 20: '100'},
  %(points_gain)s,
};
const out = {};
out.half     = app.pointsGain({index: 16, weight: 15.2});
out.zero     = app.pointsGain({index: 17, weight: 15.2});
out.empty    = app.pointsGain({index: 18, weight: 15.2});
out.full     = app.pointsGain({index: 19, weight: 15.2});
out.noweight = app.pointsGain({index: 20, weight: 0});
console.log(JSON.stringify(out));
"""


@needs_node
class TestTheCardsPointsArithmetic:
    @pytest.fixture(scope="class")
    def result(self):
        script = POINTS_HARNESS % {"points_gain": _method(QUEUE_SRC, "pointsGain")}
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        assert out.returncode == 0, out.stderr[-2000:]
        return json.loads(out.stdout)

    def test_half_of_fifteen_point_two_is_seven_point_six(self, result):
        assert result["half"] == pytest.approx(7.6)

    def test_zero_pays_nothing(self, result):
        assert result["zero"] == 0

    def test_an_empty_box_claims_nothing(self, result):
        assert result["empty"] is None

    def test_a_full_mark_pays_the_whole_question(self, result):
        assert result["full"] == pytest.approx(15.2)

    def test_a_question_worth_nothing_pays_nothing(self, result):
        assert result["noweight"] is None


# ── the running total, executed ──────────────────────────────────────────

FINAL_HARNESS = """
const TOTAL_Q = 2;
const QUESTION_INSTANCES = {0: {scoreVal: '60'}, 1: {scoreVal: ''}};
const posts = [];
let dom = {'final-score-hidden': {value: ''}, 'final-display': {textContent: ''},
           'mcq-display': {textContent: ''}};
const document = {getElementById: (id) => dom[id] || null};
global.fetch = (url, opts) => {
  posts.push({url: url, body: JSON.parse(opts.body)});
  return Promise.resolve({ok: true,
    json: () => Promise.resolve({final_score: 62.5, score: 68.75})});
};
// A tick or two, so the fetch chain settles without a real timer.
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
clearTimeout = () => {};
function collectFeedback() {
  const scores = {};
  for (let q = 0; q < TOTAL_Q; q++) {
    const inst = QUESTION_INSTANCES[q];
    if (inst.scoreVal !== '') scores[q] = parseFloat(inst.scoreVal);
  }
  return {scores: scores, comments: {}};
}
%(signature)s
var _finalSig = null, _finalTimer = null;
%(calc)s

(async () => {
  const out = {};
  calcFinal();
  await settle();
  out.requests = posts.length;
  out.url = posts[0] && posts[0].url;
  out.body = posts[0] && posts[0].body;
  out.final = dom['final-display'].textContent;
  out.hidden = dom['final-score-hidden'].value;
  out.mcq = dom['mcq-display'].textContent;

  // The page polls this every two seconds: an unchanged mark must not ask again.
  calcFinal();
  await settle();
  out.after_poll = posts.length;

  // A mark that moved does.
  QUESTION_INSTANCES[1].scoreVal = '80';
  calcFinal();
  await settle();
  out.after_change = posts.length;
  out.changed_scores = posts[posts.length - 1].body.teacher_feedback.scores;
  console.log(JSON.stringify(out));
})();
"""


@needs_node
class TestTheRunningTotalIsAsked:
    @pytest.fixture(scope="class")
    def result(self):
        calc = _function(DETAIL_SRC, "calcFinal").replace(
            "{{ submission.id }}", "sub-1")
        script = FINAL_HARNESS % {
            "signature": _function(DETAIL_SRC, "_scoresSignature"),
            "calc": calc.replace("setTimeout(", "setTimeout0("),
        }
        script = "const setTimeout0 = (fn) => fn();\n" + script
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        assert out.returncode == 0, out.stderr[-2000:]
        return json.loads(out.stdout)

    def test_it_asks_the_preview_for_this_paper(self, result):
        assert result["url"] == "/teacher/grade/sub-1/preview"

    def test_it_sends_the_marks_and_no_final(self, result):
        assert result["body"] == {"teacher_feedback": {"scores": {"0": 60}}}

    def test_the_display_shows_what_the_server_answered(self, result):
        assert result["final"] == 62.5
        assert result["hidden"] == 62.5
        assert result["mcq"] == 68.75

    def test_an_unchanged_mark_asks_nothing(self, result):
        assert result["after_poll"] == 1, "fifteen requests a paper while nothing changed"

    def test_a_changed_mark_asks_once_more(self, result):
        assert result["after_change"] == 2
        assert result["changed_scores"] == {"0": 60, "1": 80}


# ── the script has to parse before any of it can run ─────────────────────


def _inline_scripts(src: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)


def _blank_jinja(script: str) -> str:
    """Jinja out of the way, so node can read the JavaScript around it."""
    script = re.sub(r"\{\{.*?\}\}", "0", script, flags=re.S)
    return re.sub(r"\{%.*?%\}", "", script, flags=re.S)


@needs_node
class TestTheMarkingPagesScriptsParse:
    """A SyntaxError in an inline script is not a broken control, it is all of them.

    `grade_detail.html` had one — a string literal split across lines — and the page
    it belongs to looked fine: the server-rendered marks were there, and every
    button, box and canvas tool silently did nothing.
    """

    @pytest.mark.parametrize("template", [QUEUE, DETAIL],
                             ids=["grading_queue", "grade_detail"])
    def test_every_inline_script_parses(self, template, tmp_path):
        scripts = _inline_scripts(template.read_text(encoding="utf-8"))
        assert scripts, "no inline script found — the selector changed"
        for i, script in enumerate(scripts):
            path = tmp_path / f"{template.stem}_{i}.js"
            path.write_text(_blank_jinja(script), encoding="utf-8")
            out = subprocess.run([NODE, "--check", str(path)],
                                 capture_output=True, text=True,
                                 encoding="utf-8", timeout=60)
            assert out.returncode == 0, (
                f"{template.name} script {i} does not parse: {out.stderr[-1200:]}")
