"""The exam builder must not be handed a JSON string.

`question_pages` is written with `json.dumps`, so the column holds a JSON string
inside jsonb and PostgREST returns it as one — while `question_types` beside it is
a real object. The builder does `question_pages[i]` for every question, and
indexing a *string* by `'0'`, `'1'`, … returns a character. The exam-edit form
showed the page fields filled with `{`, `"`, `0`, `"`, `:` — five pieces of
nonsense a teacher could not recognise as wrong — and saving the form wrote them
back over the real page mapping. A range of `"{"` parses to no pages at all, which
stops the student's paper from turning when a question is picked.

The failure is silent in both directions: the form renders, the save succeeds, and
the only visible symptom is a student paging back and forth by hand.
"""
import json
import re
from pathlib import Path

ROUTE = Path(__file__).resolve().parents[2] / "app" / "routes" / "teacher.py"

from app.routes.teacher import _as_dict, _as_list, _normalise_exam_json  # noqa: E402


def test_a_json_string_becomes_the_object_it_holds():
    assert _as_dict('{"0": "1", "1": "2-4"}') == {"0": "1", "1": "2-4"}


def test_a_real_object_is_left_alone():
    value = {"0": "1"}
    assert _as_dict(value) is value
    assert _as_list(["a"]) == ["a"]


def test_nonsense_is_an_empty_container_not_an_exception():
    assert _as_dict("not json") == {}
    assert _as_dict(None) == {}
    assert _as_list("not json") == []
    assert _as_list(None) == []


def test_the_page_column_is_parsed_before_the_builder_reads_it():
    """The exact defect: `pages[i]` on a string is a character."""
    row = {
        "question_types": {"0": "mcq", "1": "mcq"},
        "question_pages": json.dumps({"0": "1", "1": "2-4"}),
        "pdf_page_urls": json.dumps(["/static/uploads/exams/x/page_001.png"]),
    }
    # What the template used to receive (and did) — in the browser this is
    # `question_pages['0']`; in Python a string needs an integer index, and it
    # returns the same characters.
    assert row["question_pages"][0] == "{"
    assert row["question_pages"][1] == '"'

    _normalise_exam_json(row)

    assert row["question_pages"] == {"0": "1", "1": "2-4"}
    assert row["pdf_page_urls"] == ["/static/uploads/exams/x/page_001.png"]
    assert row["question_types"] == {"0": "mcq", "1": "mcq"}


def test_the_edit_route_normalises_what_it_renders():
    source = ROUTE.read_text(encoding="utf-8")
    route = source.index('@teacher_bp.route("/exams/<exam_id>", methods=["GET", "POST", "DELETE"])')
    get_branch = source.index("if request.method == \"GET\":", route)
    get_branch = source[get_branch:get_branch + 900]

    assert "exam_data = _normalise_exam_json(exam_row)" in get_branch, (
        "the exam-edit form is rendered without parsing its jsonb columns, so the "
        "page fields show characters of a JSON string")
    assert 'render_template("teacher/exam_form.html", exam=exam_data' in get_branch, (
        "the render moved out of the branch this test inspects")


def test_every_json_column_the_form_reads_is_normalised():
    """A field the template reads but this list forgets is the same bug again."""
    template = (Path(__file__).resolve().parents[2]
                / "app" / "templates" / "teacher" / "exam_form.html").read_text(encoding="utf-8")
    read = set(re.findall(r"exam\.(question_[a-z_]+|answer_key|pdf_page_urls)\s*(?:\||\})", template))

    normalised = set(re.findall(r'"(\w+)"', ROUTE.read_text(encoding="utf-8")[
        ROUTE.read_text(encoding="utf-8").index("for field in ("):
        ROUTE.read_text(encoding="utf-8").index("if isinstance(exam_data.get(\"pdf_page_urls\")")]))

    missing = {name for name in read if name not in normalised and name != "pdf_page_urls"}
    assert not missing, f"the form reads {sorted(missing)} but the route never parses it"
