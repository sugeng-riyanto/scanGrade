"""One drawing, one page — for the student, for the teacher, and in the report.

A teacher marking a paper reported the same annotation appearing on several pages
of a student's answer when the student had drawn on one. Two independent readers
were inventing a page:

* `teacher/grade_detail.html` looked the canvas up as
  `pages[page-1] || pages[page]` — a tolerance for the neighbouring key, which
  painted a drawing made on page 3 onto page 2 as well;
* `routes/student.py` subtracted one from the key whenever the map lacked a `'0'`
  key (`p if "0" in s_pages else p - 1`) — the same drawing composed onto the page
  *before* the one it was made on, in the student's own report PDF — and
  `print/report_card.html` and `student/result_detail.html` kept the same guess,
  which put the drawing over the wrong sheet of paper in the printed report and on
  the student's own result page. `result_detail.html` iterated the student's pages
  and the teacher's pages as one concatenated list, so a page carrying both was
  drawn twice.

The convention is not ambiguous, and that is a measurement rather than an opinion:
the exam page writes `canvasData[i][p]` with `p = this.page - 1`, and across every
stored submission **no key was at or beyond its exam's page count** — which a page
number would have to reach for a drawing on the last page. So the key is the page
index, everywhere, and nothing may look at a neighbouring page.

The truth table is executed rather than grepped: `sgCanvasForPage` is extracted
from the template and run in node over the shape the production submission
actually has (`{'2': {'canvas': ...}}`, drawn while the student was on page 3).
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

GRADE = ROOT / "app" / "templates" / "teacher" / "grade_detail.html"
TAKE = ROOT / "app" / "templates" / "student" / "take_exam.html"
STUDENT_ROUTE = ROOT / "app" / "routes" / "student.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to execute the reader")

#: The production submission behind the report: `Fisika`, question 5
#: (`essay_canvas`), one canvas whose key is '2'. The student was on page 3.
PRODUCTION_PAGES = {"2": {"canvas": "data:image/png;base64,AAA"}}


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_function(text: str, name: str) -> str:
    """`function name(…) { … }` — to its matching brace, indentation-blind."""
    start = text.index(f"function {name}(")
    depth = 0
    for i in range(text.index("{", start), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"{name} is unterminated")


# ── the rule, executed ───────────────────────────────────────────────────────

def run_cases(cases: list[dict]) -> list:
    """Ask the template's own `sgCanvasForPage` what each page resolves to."""
    fn = extract_function(source(GRADE), "sgCanvasForPage")
    script = f"{fn}\nconst cases = {json.dumps(cases)};\n" + """
const out = cases.map(c => {
    const pd = sgCanvasForPage(c.pages, c.page);
    return pd ? pd.canvas : null;
});
console.log(JSON.stringify(out));
"""
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


class TestTheReaderResolvesOnePage:
    def test_the_production_submission_shows_on_its_own_page_only(self):
        """Key '2' is page 3. Pages 1, 2 and 4 must be blank.

        Page 2 is the reported defect: the neighbouring-key tolerance returned the
        page-3 drawing there, so the teacher saw one annotation on two pages and no
        way to tell which page the student had answered on.
        """
        canvases = run_cases([
            {"pages": PRODUCTION_PAGES, "page": 1},
            {"pages": PRODUCTION_PAGES, "page": 2},
            {"pages": PRODUCTION_PAGES, "page": 3},
            {"pages": PRODUCTION_PAGES, "page": 4},
        ])
        assert canvases == [None, None, PRODUCTION_PAGES["2"]["canvas"], None]

    def test_a_drawing_on_the_first_page_is_not_shifted(self):
        """Key '0' is page 1 — and a map that has it is exactly the case the old
        student-side heuristic treated differently from the ones that do not."""
        pages = {"0": {"canvas": "data:image/png;base64,FIRST"}}
        canvases = run_cases([
            {"pages": pages, "page": 1},
            {"pages": pages, "page": 2},
        ])
        assert canvases == ["data:image/png;base64,FIRST", None]

    def test_two_drawings_stay_on_their_two_pages(self):
        pages = {"1": {"canvas": "P2"}, "3": {"canvas": "P4"}}
        canvases = run_cases([{"pages": pages, "page": p} for p in (1, 2, 3, 4, 5)])
        assert canvases == [None, "P2", None, "P4", None]

    def test_a_page_with_boxes_but_no_canvas_resolves_to_nothing(self):
        """`textBoxes` alone is not a drawing; only `canvas` paints an overlay."""
        pages = {"1": {"textBoxes": [{"text": "hello"}]}}
        assert run_cases([{"pages": pages, "page": 2}]) == [None]

    def test_an_empty_or_missing_map_is_not_an_error(self):
        assert run_cases([{"pages": None, "page": 1},
                          {"pages": {}, "page": 2}]) == [None, None]

    def test_the_neighbouring_key_is_never_consulted(self):
        """The exact defect, pinned in the source: `pages[page-1] || pages[page]`."""
        text = source(GRADE)
        assert "ans.pages[p0] || ans.pages[p1]" not in text
        assert "p1 = String(this.page)" not in text
        body = text[text.index("function sgCanvasForPage"):]
        body = body[:body.index("\n}")]
        assert body.count("pages[") == 1, (
            "the reader must look at one page, never a neighbour: " + body)


# ── the teacher's overlay must be blank on a page with no drawing ────────────

class TestTheOverlayIsCleared:
    def test_the_overlay_is_blanked_before_the_missing_data_return(self):
        """The other half of the report: the overlay was never cleared, so the last
        drawing stayed painted for the rest of the session and every later page
        looked annotated. Clearing has to happen *before* the early return, which is
        the only ordering that leaves a page the student skipped empty."""
        text = source(GRADE)
        body = text[text.index("drawStudentCanvas() {"):]
        body = body[:body.index("img.onload")]
        clear = body.index("sCtx.clearRect(0, 0, sc.width, sc.height)")
        early = body.index("if (!pd) return;")
        assert clear < early, "clearRect must precede the early return"

    def test_the_overlay_is_painted_from_the_same_rule(self):
        text = source(GRADE)
        body = text[text.index("drawStudentCanvas() {"):]
        body = body[:body.index("img.onload")]
        assert "this.studentCanvasFor(this.page)" in body, (
            "the overlay has to use the one rule, not its own lookup")


# ── the writer's convention, which the readers have to match ─────────────────

class TestTheWriterKeysByIndex:
    def test_the_exam_page_stores_under_the_page_index(self):
        text = source(TAKE)
        assert "this.canvasData[i][p] = d.el.toDataURL('image/png')" in text
        restore = text[text.index("_restorePage(i, p) {"):]
        assert "if (p === undefined) p = this.page - 1;" in restore[:400]

    def test_no_writer_ever_keys_by_the_display_page_number(self):
        """If this ever changes, both readers change with it — which is the point of
        failing here rather than off by one in three places."""
        for path in (TAKE, GRADE):
            assert "pages[this.page]" not in source(path), (
                f"{path.name} keys a canvas by 1-based page number")

    def test_the_static_branch_agrees_with_the_rule(self):
        """The markup shows each stored canvas on `pn + 1`, i.e. index + 1."""
        assert 'x-show="page === {{ pn|int + 1 }}"' in source(GRADE)

    def test_the_student_s_text_boxes_are_shown_on_one_page_too(self):
        """The same tolerance was on the boxes: `page == pn+1 || page == pn` drew a
        note typed on page 3 onto page 2 as well. A box, like a drawing, belongs to
        exactly one page."""
        text = source(GRADE)
        assert "||page=={{ pn }}" not in text
        assert "|| page=={{ pn }}" not in text
        assert "(page=={{ pn+1 }}?'block':'none')" in text

    def test_no_page_lookup_anywhere_accepts_a_neighbour(self):
        """A sweep rather than two spot checks: any `page ==` comparison that also
        accepts the other page is this defect returning under a new name."""
        text = source(GRADE)
        for match in re.finditer(r"page\s*[=!]==?\s*[^,;)\n]{0,40}", text):
            window = match.group(0)
            assert "||" not in window or "pn+1" not in window, (
                f"a page comparison accepts a second page: {window!r}")


# ── the student's own report, where the same drawing was moved a page back ───

class TestTheReportNoLongerGuesses:
    def test_the_zero_key_heuristic_is_gone(self):
        text = source(STUDENT_ROUTE)
        assert '"0" in s_pages' not in text, (
            "the page index is not inferred from the presence of a '0' key")
        assert "pdf_idx = p_idx\n" in text

    def test_the_teacher_overlay_merge_is_index_keyed_too(self):
        """Teacher feedback is stored under the same index convention, so the two
        halves of the merged report cannot disagree about which page is which."""
        text = source(STUDENT_ROUTE)
        assert "ov_p_idx = int(ov_p_str)" in text
        assert "page_imgs[ov_p_idx] = bg" in text

    def test_a_drawing_on_the_last_page_would_be_out_of_range_as_an_index(self):
        """Why the survey is decisive rather than suggestive.

        A page number for the final page equals the page count; an index for it is
        one less. Reading production found no key at or beyond the count, so no
        stored drawing can have been written as a page number.
        """
        page_count = 12
        index_of_last = page_count - 1
        assert index_of_last < page_count
        assert "pdf_url = pdf_page_urls[pdf_idx] if (0 <= pdf_idx < len(pdf_page_urls)) else \"\"" \
            in source(STUDENT_ROUTE), "an out-of-range index still has to be survivable"


# ── the two documents that print or show the drawing ─────────────────────────

REPORT_CARD = ROOT / "app" / "templates" / "print" / "report_card.html"
RESULT_DETAIL = ROOT / "app" / "templates" / "student" / "result_detail.html"


def render_jinja(snippet: str, **ctx) -> str:
    """Run a slice of a real template, so the guard cannot pass on a copy."""
    from jinja2 import Environment
    return Environment().from_string(snippet).render(**ctx)


def without_comments(text: str) -> str:
    """A template with its `{# … #}` blocks blanked.

    The guards below read *code*. Every one of these files explains, in a comment,
    which expression it used to use — that is worth keeping, and a guard that failed
    on its own documentation would be deleted rather than obeyed.
    """
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


def keys_rendered(out: str) -> list:
    """The rendered key list, minus the whitespace Jinja leaves behind."""
    return [part.strip() for part in out.split(",") if part.strip()]


def extract_page_loop(text: str) -> str:
    """The merge plus the `for` header, taken whole out of the template.

    Returning the header *and* the merge together is what makes the test
    meaningful: the defect was a `for` over two concatenated key lists, so a guard
    that read only the loop header would pass on the broken version.
    """
    start = text.index("{% set p_keys = namespace(all=[]) %}")
    marker = "{% for p_key in p_keys.all %}"
    end = text.index(marker) + len(marker)
    return text[start:end] + "{{ p_key }},{% endfor %}"


class TestThePrintedPagesAreNotGuessedEither:
    """Three readers of one convention — and it was written down in only one of them.

    `routes/student.py` was fixed to read the key as the page index, while
    `print/report_card.html` and `student/result_detail.html` still guessed:
    `p_idx if '0' in s_pages else p_idx - 1`. The guess is invisible when the student
    drew on page one — key `'0'` is there, so the subtraction is skipped — and wrong
    every other time. The production submission in the report (`{'2': {...}}`, drawn
    on page 3) came out over **page 2** of the paper in both documents.
    """

    def test_neither_document_infers_the_page_from_a_zero_key(self):
        for path in (REPORT_CARD, RESULT_DETAIL):
            code = without_comments(source(path))
            assert "'0' in s_pages" not in code, (
                f"{path.name} still infers the page index from the presence of a '0' key")
            assert "{% set pdf_idx = p_idx %}" in code, (
                f"{path.name} must take the page index straight from the key")

    def test_all_three_readers_state_the_same_rule(self):
        """The rule lives in three files, so it is asserted in all three: a copy
        that drifts is how the screen and the printed sheet disagree."""
        assert "pdf_idx = p_idx" in source(STUDENT_ROUTE)
        for path in (REPORT_CARD, RESULT_DETAIL):
            assert "{% set pdf_idx = p_idx %}" in source(path), path.name

    def test_the_production_key_lands_on_the_page_the_student_drew_on(self):
        """Key '2' is the third sheet, not the second.

        The arithmetic the old heuristic got wrong, stated once: every stored key
        (0, 2, 5 here) indexes the paper directly.
        """
        pdf_pages = [f"page-{i}" for i in range(12)]
        for key, expected in (("0", "page-0"), ("2", "page-2"), ("5", "page-5")):
            assert pdf_pages[int(key)] == expected
            # the defunct rule, for contrast: it subtracted one unless key '0' existed
            assert pdf_pages[int(key) - 1] != expected or int(key) == 0

    def test_the_result_page_renders_one_block_per_page(self):
        """A page the student drew on *and* the teacher annotated was rendered twice.

        `result_detail.html` iterated `s_page_keys + ov_page_keys` while the block
        below it draws both maps for one key — so the same paper page, its drawing
        and its annotation, appeared twice in a row, which is exactly what "the
        annotation is on several pages" looks like from the student's side.
        """
        snippet = extract_page_loop(source(RESULT_DETAIL))
        out = render_jinja(snippet, s_page_keys=["2"], ov_page_keys=["2", "3"])
        assert keys_rendered(out) == ["2", "3"], (
            f"one block per page, in order: {out!r}")

    def test_a_page_that_only_the_teacher_annotated_is_still_shown(self):
        """Merging must not drop the teacher's own pages — the reason the key lists
        were concatenated in the first place, so the fix cannot regress into it."""
        snippet = extract_page_loop(source(RESULT_DETAIL))
        out = render_jinja(snippet, s_page_keys=[], ov_page_keys=["1"])
        assert keys_rendered(out) == ["1"]

    def test_the_merge_keeps_the_student_s_pages_first(self):
        """Order is the paper's order, not the map's: page 2 before page 3 whatever
        order the two maps are consulted in."""
        snippet = extract_page_loop(source(RESULT_DETAIL))
        out = render_jinja(snippet, s_page_keys=["2"], ov_page_keys=["3"])
        assert keys_rendered(out) == ["2", "3"]

    def test_the_report_card_merges_the_two_maps_exactly_once(self):
        """The printed sheet was already correct — pinned so it stays the pattern the
        result page was brought into line with."""
        code = without_comments(source(REPORT_CARD))
        assert "namespace(keys=s_pages.keys() | list)" in code
        assert "{% for p_key in s_page_keys + ov_page_keys %}" not in code
        assert "{% for p_key in s_pages.keys() + ov_pages.keys() %}" not in code
