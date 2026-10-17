"""The student exam page, rendered with every question kind on one paper.

The reported fault was "the teacher set True/False, complex multiple choice,
matching, drag & drop, and a multi-option multiple choice, and none of them
appear for the pupil". This suite is the server half of that question, and it is
deliberately a *render* rather than a grep: it hands the page one paper carrying
every kind plus the public material a matching / drag / complex question needs,
and asserts the page that comes out actually carries

  * the pupil-facing type map (`question_types`) — so `sgKind()` has something to
    classify,
  * one control branch per kind,
  * the public options (`lefts`/`rights`/`chips`/`statements`/`categories`) that
    `public_options()` releases and never the pairing itself, and
  * the *stored* duration, so a custom 90 minutes is what the page is told.

If these hold, the remaining surface is JavaScript at runtime, and a browser — not
a template render — is what proves that half. That boundary is the point: this
file fails loudly the day a kind stops reaching the page, and says nothing about
a kind whose Alpine branch throws after it arrives.
"""
from __future__ import annotations

import contextlib

from flask import g


@contextlib.contextmanager
def _signed_in(app):
    with app.test_request_context("/student/exams/e1"):
        g.user_id, g.user_name, g.user_role = "stu-1", "Murid Uji", "murid"
        g.user_email, g.tz_offset, g.show = "s@test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        yield


def _render(app, qtypes, qopts, extra=None):
    ctx = {
        "exam": {"id": "e1", "title": "T", "total_questions": len(qtypes),
                 "duration_minutes": 90, "question_types": qtypes,
                 "question_canvas": {}, "question_audio": {}, "question_pages": {},
                 "pdf_page_urls": []},
        "anti_cheat_config": "{\"anti_cheat_enabled\": true}",
        "exam_started_at": None, "recovery_code": "", "question_options": qopts,
        "deadline": None, "deadline_reason": "duration", "seconds_left": 5400,
        "window_end": None, "away_grace_seconds": 15, "away_grace_chances": 2,
        "student_name": "Ahmad", "student_class_label": "7A",
    }
    ctx.update(extra or {})
    with _signed_in(app):
        return app.jinja_env.get_template("student/take_exam.html").render(**ctx)


def test_every_kind_block_is_rendered_and_types_are_wired(app):
    qtypes = {"0": "mcq", "1": "true_false", "2": "complex_multiple_choice",
              "3": "match", "4": "drag_drop", "5": "ordering", "6": "essay_canvas"}
    qopts = {
        "2": {"statements": ["a", "b"], "categories": ["Benar", "Salah"]},
        "3": {"lefts": ["x"], "rights": ["y"]},
        "4": {"chips": ["p", "q"]},
        "5": {"chips": ["p", "q"]},
    }
    html = _render(app, qtypes, qopts)

    # 1. the type map reaches the page, question by question
    assert '"true_false"' in html and '"complex_multiple_choice"' in html, \
        "the question types never reached the page"
    # 2. the stored duration reaches the page (not a 60-minute default)
    assert "durationMinutes: 90" in html, "the stored duration never reached the page"
    # 3. one control branch per kind
    for marker in ("truefalse", "pgk", "match", "dragdrop", "ordering"):
        assert f"qKind(i) === '{marker}'" in html, f"missing branch: {marker}"
    # 4. the public material, and never the pairing
    assert '"statements"' in html and '"lefts"' in html and '"chips"' in html, \
        "public options for match/drag/pgk never reached the page"


def test_the_builder_offers_every_type_the_pupil_page_can_render(app):
    """The picker a teacher sees and the kinds the pupil page draws must agree."""
    from app.services.question_types import PICKER_TYPES, question_kind

    with app.test_request_context("/teacher/exams/new"):
        g.user_id, g.user_name, g.user_role = "t-1", "Guru Uji", "guru"
        g.tz_offset, g.show = 7, {}
        g.user_school_id = "sch-1"
        html = app.jinja_env.get_template("teacher/exam_form.html").render(
            exam=None, subjects=[], classes=[])

    for value in PICKER_TYPES:
        assert f'"{value}"' in html, f"the builder no longer offers {value}"
    kinds = {question_kind(t) for t in PICKER_TYPES}
    assert {"choice", "truefalse", "pgk", "match", "dragdrop", "ordering"} <= kinds, \
        "a picker type has no kind for the pupil page to render"


# ── and the half none of the rules above can see: the answer key ─────────────
#
# The render at the top of this file proves the *public* material reaches the paper.
# It is silent about the other half of the same question — the one the route names as
# the reason `public_options()` exists: "the pairing itself stays behind". Every
# assertion above is about what the page carries, and a page that also carried the
# pairing, or the MCQ letters, satisfies all of them.
#
# Two readings, because the paper has two doors. `public_options()` is the one place
# that decides what a pupil may receive, so it is asked directly; the *route* is the
# other, and the fixture above cannot reach it, because it builds the context itself.

PAIRING = {"pairs": [["Alfa", "Satu"], ["Beta", "Dua"]]}
SENTINEL = "Q7-KUNCI-JAWABAN-SENTINEL"

#: Answer-shaped context names a route could plausibly hand a template. The page is
#: given every one of them at once: a template prints what it names, so the reading is
#: that none of them is named — and the *strip* below is what makes that true for the
#: one the route actually reads.
ANSWER_KEYS = ("answer_key", "answer_keys", "question_answer_keys", "correct_answers",
               "key")


def _paper_render_sites():
    """`{file name: [the enclosing function body around each render]}`.

    The *function*, not a fixed window around the call: in `student.py` the strip is a
    hundred lines above the render, in the same function — a window is a number that
    silently stops covering the thing it was written to cover.
    """
    import re
    from pathlib import Path
    sites: dict[str, list[str]] = {}
    for path in (Path(__file__).resolve().parents[2] / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        bounds = [m.start() for m in re.finditer(r"\ndef ", text)] + [len(text)]
        for match in re.finditer(r"render_template\(\s*[\"']student/take_exam\.html[\"']", text):
            starts = [b for b in bounds if b < match.start()]
            end = next(b for b in bounds if b > match.start())
            sites.setdefault(path.name, []).append(text[starts[-1]:end])
    return sites


def _take_exam_body() -> str:
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "app" / "routes" / "student.py"
           ).read_text(encoding="utf-8")
    assert "def take_exam(" in src, "the pupil's paper route was renamed"
    return src.split("def take_exam(", 1)[1].split("\ndef ", 1)[0]


class TestTheAnswerNeverReachesThePaper:
    def test_public_options_hands_out_flat_strings_and_never_the_pairing(self):
        """The one function that decides it, driven by the shapes a key really has."""
        from app.services.question_types import public_options

        for qtype, key in (("match", PAIRING),
                           ("drag_drop", {"chips": ["p", "q"]}),
                           ("complex_multiple_choice",
                            {"statements": ["a", "b"], "categories": ["Benar", "Salah"],
                             "answers": {"a": "Benar"}})):
            public = public_options(qtype, key)
            if public is None:
                continue
            assert all(isinstance(v, list) and all(isinstance(x, str) for x in v)
                       for v in public.values()), (
                f"{qtype} hands the pupil a structure a pairing can ride in: {public!r}")
            for name in ("pairs", "matches", "key", "answer", "answers", "solution"):
                assert name not in public, (
                    f"{qtype} publishes {name!r} to the pupil: {public!r}")

    def test_the_route_strips_the_key_and_hands_the_page_the_stripped_row(self):
        """The door the fixture cannot reach: nothing in this file calls the route."""
        import re
        body = _take_exam_body()
        assert re.search(r"safe_exam\s*=\s*\{[^}]*k\s*!=\s*['\"]answer_key['\"]", body, re.S), (
            "the pupil's route no longer strips `answer_key` from the exam row, so the paper"
            " is handed the key the teacher wrote")
        assert re.search(r"render_template\(\s*['\"]student/take_exam\.html['\"]"
                         r"[^)]*exam=safe_exam", body, re.S), (
            "the paper is no longer rendered from the stripped row")
        assert "exam=exam," not in body, (
            "the paper is rendered from the unstripped exam row as well — the strip is then"
            " decoration")

    def test_every_render_site_of_the_paper_strips_the_key(self):
        """There are two doors to this page, and a third would be a third strip to forget.

        The pupil's sitting and the teacher's preview both render it; both must hand it a
        stripped row and build `question_options` through `public_options`. A new render
        site is a finding here on purpose — it is the one place the strip has to be
        remembered twice.
        """
        sites = _paper_render_sites()
        assert set(sites) == {"student.py", "teacher.py"}, (
            f"the paper is rendered from somewhere new: {sorted(sites)} — a new door needs "
            "the same strip, and this is where it is written down")
        for name, windows in sites.items():
            for window in windows:
                assert 'k != "answer_key"' in window, (
                    f"{name} renders the paper without stripping `answer_key`")
                assert "exam=safe_exam" in window, (
                    f"{name} renders the paper from the unstripped exam row")
                assert "exam=exam," not in window, (
                    f"{name} renders the paper from the unstripped row as well")
                assert "public_options(" in window, (
                    f"{name} builds the paper's options without the one function that "
                    "decides what a pupil may receive")

    def test_the_page_shows_no_answer_shaped_context_it_does_not_name(self, app):
        """The weaker half, and stated as such: Jinja prints what a template names.

        Handing the page every answer-shaped key at once is what makes this a reading
        rather than a hope — a template that grew a `{{ exam | tojson }}` or a debug dump
        would carry the sentinel out, and this is the rule that would notice.
        """
        html = _render(app, {"0": "mcq"}, {}, extra={k: {"0": SENTINEL} for k in ANSWER_KEYS})
        assert SENTINEL not in html, (
            "the paper printed a value out of an answer-shaped key it does not name")
