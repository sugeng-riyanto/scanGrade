"""The pupil's essay, whole, from the editor to the teacher's screen.

The report that started this work was not "the toolbar is ugly": it was a
suspicion that **answers were not reaching the grading page**. Two defects were
real, and both are proven here rather than argued.

1. **A fresh essay's first paragraphs were never captured.** The editor rendered
   its first paragraph from a Jinja fallback, and `onParagraphInput` returned
   early while `paragraphs[i]` did not exist — which it never did until the pupil
   pressed Enter or "+ Paragraph". So a pupil who typed one long answer and
   submitted had `answersText[i] === ''`: the sync loop only writes when something
   is dirty, the submit serialised an empty text, and the teacher was handed a
   blank essay. The page showed the words until it was reloaded.

2. **The teacher was shown a prefix of what was stored.** `grade_detail.html`
   printed `student_text[:300]` and `api_grading_queue` cut each answer at 100
   characters. The database held the whole answer the entire time; the marking
   surfaces showed its opening sentence and an ellipsis.

The rule this file holds: one reader of the stored shape
(`app/services/essay_text.essay_answer_text`), and no surface on the way to a
marker truncates it. The editor's own behaviour — the list toggle, the caret, the
font that must not touch the content — is executed in a real DOM below (jsdom),
because a unit test of a JS function in isolation passes happily while the button
is wired to nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import g

from app.services.essay_text import essay_answer_text

ROOT = Path(__file__).resolve().parents[2]
TAKE = ROOT / "app" / "templates" / "student" / "take_exam.html"
GRADE = ROOT / "app" / "templates" / "teacher" / "grade_detail.html"
QUEUE = ROOT / "app" / "templates" / "teacher" / "grading_queue.html"
TEACHER_PY = ROOT / "app" / "routes" / "teacher.py"
MODULE = ROOT / "app" / "static" / "js" / "essay-editor.js"

NODE = shutil.which("node")

#: A two-paragraph answer with a bulleted list, long enough that the old slices
#: (100 and 300 characters) would cut it mid-sentence. The last sentence is the
#: assertion: if the teacher's page carries it, nothing on the way truncated.
PARA_ONE = (
    "Fotosintesis adalah proses tumbuhan mengubah energi cahaya menjadi energi "
    "kimia yang disimpan sebagai glukosa. Proses ini berlangsung di kloroplas, "
    "tepatnya pada membran tilakoid, dan melibatkan dua tahap yang saling "
    "bergantung: reaksi terang dan siklus Calvin."
)
PARA_TWO = (
    "Faktor yang memengaruhi laju fotosintesis antara lain intensitas cahaya, "
    "kadar karbon dioksida, suhu, dan ketersediaan air. Pada intensitas rendah, "
    "laju naik seiring cahaya; setelah titik jenuh, kenaikan itu berhenti karena "
    "enzim bekerja pada batas kemampuannya."
)
BULLETS = "Kesimpulan yang harus muncul di halaman guru:\nklorofil menyerap cahaya merah dan biru.\noksigen dilepaskan dari pemecahan air."
WHOLE = f"{PARA_ONE}\n\n{PARA_TWO}\n\n{BULLETS}"

ANSWER = {
    "type": "essay_text",
    "text": WHOLE,
    "paragraphs": [
        {"id": "p1", "text": PARA_ONE, "html": f"<div>{PARA_ONE}</div>", "order": 0},
        {"id": "p2", "text": PARA_TWO, "html": f"<div>{PARA_TWO}</div>", "order": 1},
        {
            "id": "p3",
            "text": BULLETS,
            "html": "<ul><li>Kesimpulan yang harus muncul di halaman guru:</li><li>klorofil menyerap cahaya merah dan biru.</li><li>oksigen dilepaskan dari pemecahan air.</li></ul>",
            "order": 2,
        },
    ],
}


# ══ 1. the reader: every shape the page has ever stored ══════════════════════


class TestTheStoredShape:
    def test_the_joined_text_wins(self):
        assert essay_answer_text(ANSWER) == WHOLE

    def test_paragraphs_are_the_fallback_when_text_is_missing(self):
        answer = {"paragraphs": ANSWER["paragraphs"]}
        assert essay_answer_text(answer) == WHOLE

    def test_text_typed_into_boxes_on_the_paper_is_read_in_page_order(self):
        answer = {"pages": {
            "1": {"textBoxes": [{"text": "halaman dua"}]},
            "0": {"textBoxes": [{"text": "halaman satu"}]},
            "2": {"canvas": "data:image/png;base64,AAAA"},
        }}
        assert essay_answer_text(answer) == "halaman satu\nhalaman dua"

    def test_a_drawing_is_not_text(self):
        assert essay_answer_text({"pages": {"0": {"canvas": "data:image/png;base64,AAAA"}}}) == ""

    def test_an_older_bare_string_is_read(self):
        assert essay_answer_text("jawaban lama") == "jawaban lama"

    def test_the_ai_reads_the_same_text_as_the_marker(self):
        """The AI used its own `.text`/`.answer` reader — one text per reader is
        one paper per marker. It now delegates to this one."""
        from app.services import ai_grading_suggestion as ai
        assert ai.answer_text_of(ANSWER) == WHOLE
        assert ai.answer_text_of({"paragraphs": ANSWER["paragraphs"]}) == WHOLE
        assert ai.answer_text_of({"pages": {"0": {"canvas": "data:image/png;base64,A"}}}) == ""


# ══ 2. the marking page renders it in full ══════════════════════════════════

EXAM_VIEW = {
    "id": "exam-1", "title": "Biologi", "subject": "Biologi", "total_questions": 2,
    "question_types": {"0": "mcq", "1": "essay_text"},
    "question_weights": {"0": 40, "1": 60},
    "answer_key": {"0": "A"}, "question_pages": {}, "pdf_page_urls": [],
    "question_texts": {"1": "Jelaskan fotosintesis."},
}
SUBMISSION_VIEW = {
    "id": "sub-1", "student_id": "stu-1", "status": "graded", "score": 60,
    "final_score": 60, "penalty": 0, "submitted_late": False,
    "answers": {"1": ANSWER},
    "teacher_feedback": {"scores": {}, "comments": {}, "overlay_pages": {}},
}


class TestTheMarkingPage:
    def _render(self, app, answer=ANSWER):
        import contextlib
        from flask import g as flask_g
        with app.test_request_context("/teacher/grade/sub-1"):
            flask_g.user_id = "tea-1"
            flask_g.user_name = "Guru Uji"
            flask_g.user_email = "guru@example.test"
            flask_g.user_role = "guru"
            flask_g.tz_offset = 7
            flask_g.show = {}
            submission = dict(SUBMISSION_VIEW, answers={"1": answer})
            return app.jinja_env.get_template("teacher/grade_detail.html").render(
                submission=submission, exam=EXAM_VIEW, exam_id="exam-1",
                student={"full_name": "Ani"}, ai_suggestions={})

    def test_the_whole_answer_reaches_the_teacher(self, app):
        html = self._render(app)
        assert "klorofil menyerap cahaya merah dan biru." in html
        assert "oksigen dilepaskan dari pemecahan air." in html
        assert WHOLE.splitlines()[-1] in html

    def test_the_page_does_not_cut_a_long_answer(self, app):
        long_tail = "PENUTUP-YANG-HANYA-MUNCUL-JIKA-TIDAK-DIPOTONG"
        answer = {"text": ("kalimat panjang. " * 60) + long_tail}
        html = self._render(app, answer)
        assert long_tail in html

    def test_the_template_has_no_slice_on_the_answer(self):
        src = GRADE.read_text(encoding="utf-8")
        assert "student_text[:300]" not in src
        assert "essay_answer_text(ans_raw)" in src


# ══ 3. the marking queue: the same answer, whole ═════════════════════════════


class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    """Rows in, rows out — enough of PostgREST for the queue's two reads."""

    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters = []
        self.single_row = False
        self.mode = "select"

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append((column, set(str(v) for v in values)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self.single_row = True
        return self

    def single(self):
        self.single_row = True
        return self

    def execute(self):
        rows = [dict(r) for r in self.store.rows.get(self.table, [])]
        for column, value in self.filters:
            if isinstance(value, set):
                rows = [r for r in rows if str(r.get(column)) in value]
            else:
                rows = [r for r in rows if str(r.get(column)) == str(value)]
        if self.single_row:
            return _Resp(rows[0] if rows else None)
        return _Resp(rows)


class FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Query(self, name)


def _queue(monkeypatch, app, answer=ANSWER):
    from app.routes import teacher as teacher_routes
    store = FakeSupabase({
        "exams": [{
            "id": "exam-1", "teacher_id": "tea-1", "school_id": "sch-1",
            "question_types": {"0": "mcq", "1": "essay_text"},
            "question_weights": {"0": 40, "1": 60}, "question_scoring": {},
            "answer_key": {"0": "A"}, "total_questions": 2,
        }],
        "submissions": [{
            "id": "sub-1", "exam_id": "exam-1", "student_id": "stu-1",
            "answers": {"1": answer}, "teacher_feedback": {"scores": {}},
            "score": 60, "final_score": 60, "status": "submitted",
            "violations": [], "penalty": 0,
            "submitted_at": "2026-10-01T08:00:00+00:00",
            "profiles": {"full_name": "Ani"},
        }],
        "ai_grading_suggestion": [],
    })
    monkeypatch.setattr(teacher_routes, "get_supabase", lambda: store)
    raw = teacher_routes.api_grading_queue
    while hasattr(raw, "__wrapped__"):
        raw = raw.__wrapped__
    with app.test_request_context("/teacher/api/grading-queue/exam-1"):
        g.user_id = "tea-1"
        g.user_role = "guru"
        g.user_school_id = "sch-1"
        response = raw("exam-1")
    return response.get_json()


class TestTheGradingQueue:
    def test_each_question_carries_the_answer_whole(self, monkeypatch, app):
        payload = _queue(monkeypatch, app)
        row = payload["submissions"][0]
        question = row["questions"][0]
        assert question["preview"] == WHOLE
        assert "klorofil menyerap cahaya merah dan biru." in question["preview"]

    def test_the_sidebar_summary_is_still_a_summary(self, monkeypatch, app):
        payload = _queue(monkeypatch, app)
        row = payload["submissions"][0]
        assert row["preview"] == WHOLE[:150]
        assert len(row["preview"]) < len(WHOLE)

    def test_an_answer_stored_only_as_paragraphs_is_shown(self, monkeypatch, app):
        payload = _queue(monkeypatch, app, answer={"paragraphs": ANSWER["paragraphs"]})
        assert payload["submissions"][0]["questions"][0]["preview"] == WHOLE

    def test_the_route_no_longer_slices_the_answer(self):
        src = TEACHER_PY.read_text(encoding="utf-8")
        assert "text[:100]" not in src
        assert "essay_answer_text(ans)" in src


# ══ 4. the editor, executed in a real DOM (jsdom) ═══════════════════════════

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body><div id="outside">elsewhere</div></body></html>',
                      { pretendToBeVisual: true });
const win = dom.window;
const doc = win.document;
const src = fs.readFileSync(process.env.SG_ESSAY_EDITOR, 'utf8');
new Function('window', src)(win);
const E = win.SGEssayEditor;

const results = [];
function record(name, got, want) { results.push([name, got, want]); }

function editorWith(html) {
    const el = doc.createElement('div');
    el.setAttribute('contenteditable', 'true');
    el.innerHTML = html;
    doc.body.appendChild(el);
    return el;
}

function caretAtEnd(el) {
    const range = doc.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const sel = win.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
}

function caretIn(node, offset) {
    const range = doc.createRange();
    range.setStart(node, offset);
    range.collapse(true);
    const sel = win.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
}

function caretOutside() {
    const out = doc.getElementById('outside');
    const range = doc.createRange();
    range.selectNodeContents(out);
    const sel = win.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
}

/* ── 1. a bullet list is built, and the same button takes it apart ───────── */
{
    const ed = editorWith('<div>alpha</div><div>beta</div>');
    caretAtEnd(ed);
    E.toggleList(ed, 'ul');
    record('bullet markup', ed.innerHTML, '<ul><li>alpha</li><li>beta</li></ul>');
    record('bullet state', E.listKind(ed), 'ul');
    record('bullet text', E.textOf(ed).replace(/\s+/g, ' ').trim(), 'alpha beta');
    E.toggleList(ed, 'ul');
    record('bullet toggled off', ed.innerHTML, '<div>alpha</div><div>beta</div>');
    record('state cleared', E.listKind(ed), null);
    record('text survived the toggle', E.textOf(ed).replace(/\s+/g, ' ').trim(), 'alpha beta');
}

/* ── 2. numbered list, on lines split by <br> (the Firefox shape) ────────── */
{
    const ed = editorWith('satu<br>dua');
    caretAtEnd(ed);
    E.toggleList(ed, 'ol');
    record('numbered markup', ed.innerHTML, '<ol><li>satu</li><li>dua</li></ol>');
    record('numbered state', E.listKind(ed), 'ol');
}

/* ── 3. switching the kind keeps every item ─────────────────────────────── */
{
    const ed = editorWith('<div>one</div><div>two</div>');
    caretAtEnd(ed);
    E.toggleList(ed, 'ul');
    E.toggleList(ed, 'ol');
    record('kind switched', ed.innerHTML, '<ol><li>one</li><li>two</li></ol>');
    record('items kept', ed.querySelectorAll('li').length, 2);
}

/* ── 4. an empty paragraph becomes a list you can type in ───────────────── */
{
    const ed = editorWith('<p><br></p>');
    caretAtEnd(ed);
    E.toggleList(ed, 'ul');
    record('empty becomes one item', ed.querySelectorAll('li').length, 1);
    record('caret is in the item', ed.contains(win.getSelection().anchorNode), true);
    record('state is bullet', E.listKind(ed), 'ul');
}

/* ── 5. the button does not need the caret at the moment it is pressed ──── */
{
    /* This is the defect: `mousedown` on a toolbar button moved focus out of the
       editable, so `execCommand` had no selection to work on. The page keeps the
       caret with `@mousedown.prevent`; this proves the module also survives a
       selection that has already left, from the range it remembered. */
    const ed = editorWith('<div>satu</div><div>dua</div>');
    caretIn(ed.firstChild.firstChild, 2);
    E.remember(ed);
    caretOutside();
    E.toggleList(ed, 'ul');
    record('list applied after focus left', ed.querySelectorAll('li').length, 2);
    record('caret returned into the editor', ed.contains(win.getSelection().anchorNode), true);
}

/* ── 6. Enter is only "new paragraph" outside a list ────────────────────── */
{
    const ed = editorWith('<ul><li>satu</li></ul>');
    caretIn(ed.querySelector('li').firstChild, 2);
    record('caret in a list item', E.inListItem(ed), true);
    const plain = editorWith('<div>biasa</div>');
    caretIn(plain.firstChild.firstChild, 2);
    record('caret outside a list', E.inListItem(plain), false);
}

/* ── 7. paste is text, never markup ─────────────────────────────────────── */
{
    const ed = editorWith('<p><br></p>');
    caretAtEnd(ed);
    let announced = 0;
    ed.addEventListener('input', () => { announced += 1; });
    ed.addEventListener('paste', (e) => { E.pastePlain(e); });
    const ev = new win.Event('paste', { bubbles: true, cancelable: true });
    ev.clipboardData = { getData: () => '<b>tebal</b> & <script>alert(1)</script>' };
    ed.dispatchEvent(ev);
    record('markup was not parsed', ed.querySelector('b') === null, true);
    record('script was not created', ed.querySelector('script') === null, true);
    record('text is literal', E.textOf(ed).indexOf('<b>tebal</b>') !== -1, true);
    record('the page was told to save', announced, 1);
}

/* ── 8. the font is a style, never part of the answer ───────────────────── */
{
    const html = fs.readFileSync(process.env.SG_TAKE_EXAM, 'utf8');
    function extract(name) {
        const start = html.indexOf('        ' + name + '(');
        if (start === -1) throw new Error('missing ' + name);
        let depth = 0;
        const open = html.indexOf('{', start);
        for (let i = open; i < html.length; i++) {
            if (html[i] === '{') depth += 1;
            else if (html[i] === '}') {
                depth -= 1;
                if (depth === 0) return html.slice(start, i + 1);
            }
        }
        throw new Error('unterminated ' + name);
    }
    const src = [
        extract('essayFont'), extract('essaySize'), extract('essayStyle'),
        extract('getAnswersLight'),
    ].join(',\n');
    const sgKind = (type) => (type === 'essay' || type === 'essay_text' || type === 'essay_canvas')
        ? 'essay' : 'choice';
    const ctx = {
        questions: [{ type: 'essay_text' }],
        answersText: { 0: 'alpha\n\nbeta' },
        paragraphs: { 0: [
            { id: 'p1', text: 'alpha', html: '<div>alpha</div>', order: 0 },
            { id: 'p2', text: 'beta', html: '<div>beta</div>', order: 1 },
        ] },
        textFontFamily: {}, textFontSize: {},
        _rememberedFont: '', _rememberedSize: '16',
        textBoxes: {}, totalPages: 1, hasDrawn: {}, _answerTimestamps: {},
    };
    /* The methods are object-literal shorthand in the page, so they are put back
       into one object and called with the page's state as `this` — the same
       receiver they have inside the Alpine component. */
    const api = new Function('sgKind', 'ctx',
        'with (ctx) { const api = {' + src + '}; return api; }')(sgKind, ctx);
    /* `this` inside the component is the component, so the three methods are
       attached to the state object they read — the same receiver. */
    ctx.essayFont = api.essayFont;
    ctx.essaySize = api.essaySize;
    ctx.essayStyle = api.essayStyle;
    ctx.getAnswersLight = api.getAnswersLight;

    const before = JSON.stringify(api.getAnswersLight.call(ctx));
    ctx.textFontFamily[0] = "'Times New Roman', Georgia, serif";
    ctx.textFontSize[0] = 20;
    const after = JSON.stringify(api.getAnswersLight.call(ctx));
    record('font leaves the stored answer untouched', after, before);
    record('the font reaches the screen', api.essayStyle.call(ctx, 0).indexOf('Times New Roman') !== -1, true);
    record('the size reaches the screen', api.essayStyle.call(ctx, 0).indexOf('20px') !== -1, true);
    record('the answer still reads the same', JSON.parse(after)['0'].text, 'alpha\n\nbeta');
}

/* ── 9. typed → serialised → JSON (what localStorage holds) → read back ─── */
{
    const typed = [
        'Fotosintesis mengubah energi cahaya menjadi glukosa.',
        'Faktor yang memengaruhi:',
        'intensitas cahaya',
        'kadar karbon dioksida',
        'suhu',
        'Air juga dibutuhkan sebagai sumber elektron.',
    ].join('\n');
    const html = fs.readFileSync(process.env.SG_TAKE_EXAM, 'utf8');
    /* The paragraph the editor would hold for that answer: a line, a bulleted
       list, and a closing line — the shapes `SGEssayEditor` produces. */
    const paragraph = {
        id: 'p1', order: 0,
        text: typed,
        html: '<div>Fotosintesis mengubah energi cahaya menjadi glukosa.</div>'
            + '<div>Faktor yang memengaruhi:</div>'
            + '<ul><li>intensitas cahaya</li><li>kadar karbon dioksida</li><li>suhu</li></ul>'
            + '<div>Air juga dibutuhkan sebagai sumber elektron.</div>',
    };
    function extract(name) {
        const start = html.indexOf('        ' + name + '(');
        let depth = 0;
        const open = html.indexOf('{', start);
        for (let i = open; i < html.length; i++) {
            if (html[i] === '{') depth += 1;
            else if (html[i] === '}') {
                depth -= 1;
                if (depth === 0) return html.slice(start, i + 1);
            }
        }
        throw new Error('unterminated ' + name);
    }
    const api = new Function('sgKind', 'ctx',
        'with (ctx) { const api = {' + extract('getAnswersLight') + '}; return api; }')(
        (type) => (String(type).indexOf('essay') === 0 ? 'essay' : 'choice'),
        {});
    const ctx = {
        questions: [{ type: 'essay_text' }], answersText: { 0: typed },
        paragraphs: { 0: [paragraph] }, textBoxes: {}, totalPages: 1,
        hasDrawn: {}, _answerTimestamps: {},
    };
    const payload = api.getAnswersLight.call(ctx);
    /* A drop in the connection is the same thing to this data as a page reload:
       the payload goes through JSON into localStorage and comes back. */
    const survived = JSON.parse(JSON.stringify(payload));
    record('round trip text', survived['0'].text, typed);
    record('round trip paragraphs', survived['0'].paragraphs.length, 1);
    record('round trip keeps the markup for redisplay',
           survived['0'].paragraphs[0].html.indexOf('<ul><li>intensitas cahaya</li>') !== -1, true);
    record('round trip is the whole answer', survived['0'].text.length, typed.length);
}

process.stdout.write(JSON.stringify(results));
"""


def _run_harness():
    env = dict(os.environ)
    env["SG_ESSAY_EDITOR"] = str(MODULE)
    env["SG_TAKE_EXAM"] = str(TAKE)
    proc = subprocess.run([NODE, "-e", HARNESS], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="needs node to execute the editor")
needs_jsdom = pytest.mark.skipif(
    NODE is None or subprocess.run(
        [NODE, "-e", "require.resolve('jsdom')"], cwd=str(ROOT),
        capture_output=True).returncode != 0,
    reason="needs the jsdom dev dependency (`npm install --no-save jsdom`)",
)


@needs_node
@needs_jsdom
class TestTheEditorInARealDom:
    """Every case below runs in jsdom against the module the page loads."""

    @pytest.fixture(scope="class")
    def results(self):
        return {name: (got, want) for name, got, want in _run_harness()}

    @pytest.mark.parametrize("name", [
        "bullet markup", "bullet state", "bullet text", "bullet toggled off",
        "state cleared", "text survived the toggle",
        "numbered markup", "numbered state",
        "kind switched", "items kept",
        "empty becomes one item", "caret is in the item", "state is bullet",
        "list applied after focus left", "caret returned into the editor",
        "caret in a list item", "caret outside a list",
        "markup was not parsed", "script was not created", "text is literal",
        "the page was told to save",
        "font leaves the stored answer untouched", "the font reaches the screen",
        "the size reaches the screen", "the answer still reads the same",
        "round trip text", "round trip paragraphs", "round trip keeps the markup for redisplay",
        "round trip is the whole answer",
    ])
    def test_case(self, results, name):
        got, want = results[name]
        assert got == want, f"{name}: {got!r} != {want!r}"


# ══ 5. the wiring the DOM cannot see ════════════════════════════════════════


class TestTheWiring:
    def test_the_editor_module_is_loaded_before_the_component(self):
        src = TAKE.read_text(encoding="utf-8")
        loaded = src.index('<script src="/static/js/essay-editor.js"></script>')
        component = src.index("        execCmd(cmd) {")
        assert loaded < component, "the module must be loaded before it is read"
        assert '<script src="/static/js/essay-editor.js"></script>' in src

    def test_the_list_buttons_keep_the_caret(self):
        """`@click` alone lets the button take focus first; `mousedown.prevent` is
        what keeps the range in the paragraph while the button is pressed."""
        src = TAKE.read_text(encoding="utf-8")
        assert src.count('@mousedown.prevent="applyList(i, \'ul\')"') == 2
        assert src.count('@mousedown.prevent="applyList(i, \'ol\')"') == 2
        assert 'execCmd(\'insertUnorderedList\')' not in src
        assert 'execCmd(\'insertOrderedList\')' not in src

    def test_the_surface_is_not_rebound_to_the_value_being_typed(self):
        """An `x-html` on the live paragraph replaced the nodes on every keystroke
        and dropped the caret; the content is written once from `initialHtml`."""
        src = TAKE.read_text(encoding="utf-8")
        assert 'x-html="para.html' not in src
        assert src.count("x-init=\"$el.innerHTML = para.initialHtml") == 2

    def test_a_fresh_essay_has_a_paragraph_before_the_first_key(self):
        src = TAKE.read_text(encoding="utf-8")
        assert "seedParagraphs()" in src
        assert "                this.seedParagraphs();" in src
        assert "if (!this.paragraphs[qIdx] || !this.paragraphs[qIdx].length)" in src

    def test_the_lists_have_markers_again(self):
        """Tailwind's preflight is why the list was invisible in the DOM."""
        css = (ROOT / "app" / "static" / "css" / "theme.css").read_text(encoding="utf-8")
        assert ".sg-essay-para ul { list-style: disc outside; }" in css
        assert ".sg-essay-para ol { list-style: decimal outside; }" in css


# ══ 6. the page renders, with the controls on it ════════════════════════════

#: The context the exam page's own tests render it with (its route sends exactly
#: these), reused here so this is the real page rather than the source read as
#: text: a Jinja error or an Alpine attribute that never reaches the HTML would be
#: invisible to a grep.
def _render_exam(app, **over):
    from datetime import datetime, timezone
    ctx = {
        "exam": {"id": "e1", "title": "T", "total_questions": 1,
                 "duration_minutes": 30, "question_types": {}, "pdf_page_urls": []},
        "anti_cheat_config": "{\"anti_cheat_enabled\": true}",
        "exam_started_at": datetime(2026, 9, 28, 7, 0, tzinfo=timezone.utc).isoformat(),
        "recovery_code": "", "question_options": {},
        "deadline": datetime(2026, 9, 28, 7, 30, tzinfo=timezone.utc).isoformat(),
        "deadline_reason": "duration", "seconds_left": 1800, "window_end": None,
        "away_grace_seconds": 15, "away_grace_chances": 2,
        "student_name": "Ahmad", "student_class_label": "7A",
    }
    ctx.update(over)
    with app.test_request_context("/student/exams/e1"):
        g.user_id, g.user_name, g.user_role = "stu-1", "Murid Uji", "murid"
        g.tz_offset, g.show = 7, {}
        return app.jinja_env.get_template("student/take_exam.html").render(**ctx)


class TestTheRenderedPage:
    @pytest.fixture(scope="class")
    def page(self, app):
        return _render_exam(app)

    def test_it_renders(self, page):
        assert "sg-essay-sheet" in page
        assert "sg-essay-para" in page

    def test_the_list_buttons_are_there_and_wired(self, page):
        assert page.count("@mousedown.prevent=\"applyList(i, 'ul')\"") == 2
        assert page.count("@mousedown.prevent=\"applyList(i, 'ol')\"") == 2
        assert page.count("aria-pressed=\"listState[i] ===") == 4

    def test_the_font_controls_are_there(self, page):
        assert page.count("setEssayFont(i, $event.target.value)") == 2
        assert page.count("setEssaySize(i, $event.target.value)") == 2
        assert ">Times New Roman" not in page, "the option value is a CSS stack, not prose"
        assert "Times New Roman', Georgia, serif\"" in page

    def test_the_save_state_is_beside_the_editor(self, page):
        assert page.count("Tersimpan otomatis di perangkat ini") == 2
        assert page.count("x-text=\"syncLabel\"") >= 3

    def test_the_editor_module_is_served(self, page):
        assert '<script src="/static/js/essay-editor.js"></script>' in page

    def test_the_stage_still_asks_for_the_essay_exception(self, page):
        assert "!isEssayCurrent ? 'exam-stage--left'" in page


# ══ 7. the whole chain, in one test: typed → serialised → teacher's page ════

@needs_node
@needs_jsdom
def test_what_the_pupil_typed_is_what_the_teacher_reads(app):
    """The end-to-end proof, in the order the answer travels.

    The page's own serialiser (run in node, over the state the editor produces for
    a bulleted answer) hands back the payload that a draft, a `localStorage`
    round trip and the submit route all carry verbatim. That payload is then given
    to the marking page exactly as the route hands it to Jinja, and every line the
    pupil typed — including the bullet lines, which are one `text` string with
    newlines — must be on the page, character for character.
    """
    results = {name: got for name, got, _ in _run_harness()}
    typed = results["round trip text"]
    assert isinstance(typed, str) and len(typed) > 100

    html = TestTheMarkingPage()._render(
        app, {"type": "essay_text", "text": typed})
    assert typed in html, "the answer the pupil typed is not on the marking page"
    for line in typed.splitlines():
        assert line in html, f"a line the pupil typed is missing: {line!r}"
    # And the same text is what the AI is asked to grade.
    from app.services import ai_grading_suggestion as ai
    assert ai.answer_text_of({"type": "essay_text", "text": typed}) == typed
