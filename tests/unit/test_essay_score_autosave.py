"""A mark is typed once, saved once, and explained once.

Three things the marking screens now do, and each was missing in a way that cost a
teacher time on every paper:

* the score box on `/teacher/grade/<id>` is a **percentage** and the label named the
  question's points — "Skor #17 (15.2 poin):" beside a field that took 0–100, so a
  teacher typing the 12 they meant scored 12% of 15.2. Both now say what they are,
  and the conversion is shown while typing;
* a clicked preset saved but left the cursor on the same card, so marking a class
  was click → hunt → click. A preset, Enter, or the 1–9 keys now save **and** move
  to the next paper;
* the comment was blank until the teacher composed it. It is filled from the mark,
  and the fill never overwrites a comment a teacher wrote.

The comment text and the conversion are JavaScript, so they are **run** rather than
described: the methods are lifted out of the shipped templates and executed under
node against a small fake, the same way `test_away_grace.py` runs the exam page's
clock.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

QUEUE = Path("app/templates/teacher/grade_question.html")
DETAIL = Path("app/templates/teacher/grade_detail.html")

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not installed")


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(src: str, name: str) -> str:
    """The whole `function name(...) { ... }` by brace matching.

    Regex alone cannot: the bodies carry object literals and `if` blocks, so a
    non-greedy match stops at the first inner brace.
    """
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


def _method(src: str, name: str) -> str:
    """A method body `name(...) { ... }` from an Alpine object literal."""
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
                return src[match.start():j + 1].strip().rstrip(",")
    raise AssertionError(f"unterminated method {name}")


def _getter(src: str, name: str) -> str:
    match = re.search(rf"\n\s+get {re.escape(name)}\(\)", src)
    assert match, f"getter {name} is not defined"
    i = src.index("{", match.end())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[match.start():j + 1].strip().rstrip(",")
    raise AssertionError(f"unterminated getter {name}")


def _run_js(script: str) -> dict:
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


# ── the queue page: comment from the mark ───────────────────────────────────

QUEUE_HARNESS = """
%(comment_fn)s
const app = {
  currentQ: 0,
  qPoints: %(points)s,
  %(max_getter)s,
  %(points_of)s,
  %(sync)s,
};
const out = {};
function fresh(score, feedback, isAuto) {
  return { scoreVal: score, feedbackVal: feedback, _commentAuto: isAuto };
}
let s = fresh('80', '', true);
app.syncCommentFromScore(s);
out.filled_from_mark = s.feedbackVal;
out.marked_generated = s._commentAuto;

s = fresh('0', '', true);
app.syncCommentFromScore(s);
out.zero = s.feedbackVal;

s = fresh('100', '', true);
app.syncCommentFromScore(s);
out.full = s.feedbackVal;

// A comment the teacher wrote is left alone, even when the mark moves.
s = fresh('40', 'Sudah bagus, kurang contoh.', false);
app.syncCommentFromScore(s);
out.kept = s.feedbackVal;

// A generated comment may be replaced by the next mark.
s = fresh('40', 'generated', true);
app.syncCommentFromScore(s);
out.regenerated = s.feedbackVal;

out.points_50_of_152 = app.pointsOf('50');
out.hint = app.pointsOf('50');
console.log(JSON.stringify(out));
"""


def _queue_harness(points='{"0": 15.2}') -> str:
    src = _src(QUEUE)
    return QUEUE_HARNESS % {
        "comment_fn": _function(src, "autoCommentFor"),
        "points": points,
        "max_getter": _getter(src, "maxPoints"),
        "points_of": _method(src, "pointsOf"),
        "sync": _method(src, "syncCommentFromScore"),
    }


@requires_node
class TestTheCommentFollowsTheMark:
    @pytest.fixture(scope="class")
    def result(self):
        return _run_js(_queue_harness())

    def test_a_high_mark_reads_as_good_and_names_the_points(self, result):
        text = result["filled_from_mark"]
        assert text.startswith("12.16 dari 15.2 poin"), text
        assert "baik" in text

    def test_a_zero_says_there_is_nothing_to_mark(self, result):
        assert "tidak ada jawaban" in result["zero"]

    def test_a_full_mark_reads_as_excellent(self, result):
        assert "sangat baik" in result["full"]

    def test_a_comment_the_teacher_wrote_is_never_overwritten(self, result):
        assert result["kept"] == "Sudah bagus, kurang contoh."

    def test_a_generated_comment_is_replaced_by_the_next_mark(self, result):
        assert result["regenerated"] != "generated"

    def test_fifty_percent_of_fifteen_point_two_is_seven_point_six(self, result):
        assert result["points_50_of_152"] == pytest.approx(7.6)


# ── the per-paper page: same rule, percentage and points both visible ──────

DETAIL_HARNESS = """
%(comment_fn)s
// The page's own map, named as the template names it.
const Q_POINTS = {"0": 15.2};
function makeApp(score, comment, auto) {
  return {
    qIdx: 0,
    scoreVal: score,
    commentVal: comment,
    commentAuto: auto,
    %(max_getter)s,
    %(earned_getter)s,
    %(hint_getter)s,
    %(sync)s,
  };
}
const out = {};
let app = makeApp('50', '', false);
app.syncCommentFromScore();
out.comment = app.commentVal;
out.auto = app.commentAuto;
out.hint = app.pointsHint;
out.earned = app.earnedPoints;

app = makeApp('10', 'Tulis sendiri', false);
app.syncCommentFromScore();
out.kept = app.commentVal;

app = makeApp('90', '', false);
app.syncCommentFromScore();
out.ninety = app.commentVal;
console.log(JSON.stringify(out));
"""


def _detail_harness() -> str:
    src = _src(DETAIL)
    return DETAIL_HARNESS % {
        "comment_fn": _function(src, "autoCommentFor"),
        "max_getter": _getter(src, "maxPoints"),
        "earned_getter": _getter(src, "earnedPoints"),
        "hint_getter": _getter(src, "pointsHint"),
        "sync": _method(src, "syncCommentFromScore"),
    }


@requires_node
class TestTheScoreBoxSaysWhatItMeans:
    @pytest.fixture(scope="class")
    def result(self):
        return _run_js(_detail_harness())

    def test_the_hint_converts_the_percentage_to_points(self, result):
        assert result["hint"] == "= 7.6 / 15.2 poin"

    def test_the_comment_is_generated_from_the_mark(self, result):
        assert result["comment"].startswith("7.6 dari 15.2 poin")
        assert result["auto"] is True

    def test_a_typed_comment_survives(self, result):
        assert result["kept"] == "Tulis sendiri"


# ── wiring: what the controls actually call ────────────────────────────────

class TestTheControlsPostAndAdvance:
    def test_a_clicked_preset_saves_and_advances(self):
        src = _src(QUEUE)
        assert '@click="pickScore(s, preset)"' in src
        body = _method(src, "pickScore")
        assert "saveEssayScore" in body or "flushScore" in body
        assert "_nextStudent()" in body, "a clicked mark has to move to the next paper"

    def test_typing_is_debounced_rather_than_one_request_per_keystroke(self):
        src = _src(QUEUE)
        assert '@input="onScoreTyped(s)"' in src
        body = _method(src, "onScoreTyped")
        assert "setTimeout" in body
        assert "clearTimeout" in body
        assert "saveEssayScore" in body        # still saved, just once

    def test_enter_saves_immediately(self):
        src = _src(QUEUE)
        body = _method(src, "flushScore")
        assert "clearTimeout" in body, "a pending timer must not fire after the flush"
        assert "saveEssayScore" in body

    def test_the_keyboard_shortcuts_use_the_same_path(self):
        src = _src(QUEUE)
        body = _method(src, "_onKey")
        assert "pickScore(s" in body, (
            "the 1-9/0 shortcuts still write the score themselves, which skips the "
            "comment and the advance")
        assert "saveEssayScore" not in body

    def test_the_per_paper_page_advances_on_enter(self):
        src = _src(DETAIL)
        assert '@keydown.enter.prevent="saveAndGoNext()"' in src
        body = _method(src, "saveAndGoNext")
        assert "doAutoSave()" in body
        assert "document.getElementById('score-'" in body

    def test_the_per_paper_page_autosaves_on_input(self):
        src = _src(DETAIL)
        assert '@input="onScoreInput"' in src
        assert "scheduleSave()" in _method(src, "onScoreInput")

    def test_the_box_is_visibly_a_percentage(self):
        src = _src(DETAIL)
        block = src.split('<span class="text-xs font-extrabold text-stone-400">%</span>')[0]
        assert "maks {{ qweight }} poin" in block
        assert 'max="100"' in block

    def test_the_queue_box_is_visibly_a_percentage_too(self):
        src = _src(QUEUE)
        assert '<span class="text-[10px] text-surface-400 font-medium">%</span>' in src
        assert 'x-text="pointsHint(s)"' in src
