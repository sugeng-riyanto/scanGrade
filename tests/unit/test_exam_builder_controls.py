"""The exam builder's controls: duration, target classes, defaults, one button, and
a schedule that survives publishing.

Five changes were asked for on `/teacher/exams/new`, and each was a real fault:

1. **Duration** was a fixed `<select>` of half-step values, so a teacher could not
   enter "any number of minutes"; it is now a number input, step 5, default 60.
2. **Target Classes** listed every class the teacher was assigned to *any* subject,
   so a class the admin assigned for Physics showed up while building a Maths paper
   — and the write guard then refused the save. The list must follow the admin's
   assignment for the **selected subject**.
3. **Block screenshot** and **Lock at threshold** defaulted to *unchecked*, though
   they are the safe setting — a teacher had to know to turn them on.
4. Two submit buttons (Save / Publish & send) were asked to become one.
5. **Starts (scheduled)** was silently discarded: publishing wrote
   ``start_at = None``, so a paper scheduled for tomorrow opened the moment it was
   published. The window must be kept, or the "start schedule / window end" the
   form promises never applies.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"


def _source() -> str:
    return TEACHER.read_text(encoding="utf-8")


def _form() -> str:
    return FORM.read_text(encoding="utf-8")


def _body(name: str) -> str:
    src = _source()
    start = src.index(f"def {name}(")
    match = re.search(r"\r?\ndef ", src[start:])
    return src[start:start + match.start()] if match else src[start:]


@contextlib.contextmanager
def _signed_in(app, path, role="guru"):
    from flask import g
    with app.test_request_context(path):
        g.user_id, g.user_name, g.user_role = "u-1", "Guru Uji", role
        g.user_email, g.tz_offset, g.show = "g@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", None
        yield


def _exam(**over):
    row = {
        "id": "e1", "title": "Latihan", "subject": "Fisika", "subject_id": "sub1",
        "duration_minutes": 45, "total_questions": 10, "question_types": {},
        "end_at": None, "auto_submit_on_window_end": False, "sg_in_progress": False,
        "answer_key": {}, "class_ids": [], "description": "", "max_attempts": 1,
        "pdf_url": None, "question_audio": {}, "question_cognitive": {},
        "question_pages": {}, "question_weights": {}, "start_at": None,
    }
    row.update(over)
    return row


def _render_form(app, exam, *, subjects=None, classes=None, classes_by_subject=None,
                 builder_defaults=None):
    ctx = {
        "exam": exam, "subjects": subjects or [], "classes": classes or [],
        "grade_components": {},
        "builder_defaults": builder_defaults or {"subject_id": None, "class_ids": [],
                                                 "duration_minutes": 60},
    }
    if classes_by_subject is not None:
        ctx["classes_by_subject"] = classes_by_subject
    with _signed_in(app, "/teacher/exams/new"):
        return app.jinja_env.get_template("teacher/exam_form.html").render(**ctx)


# ── 1. the duration field ───────────────────────────────────────────────────

DURATION_INPUT = re.compile(
    r'<input[^>]*name="duration_minutes"[^>]*>', re.S)


def _duration_input(html: str) -> str:
    found = DURATION_INPUT.search(html)
    assert found, "the builder has no duration input any more"
    return found.group(0)


def _duration_value(html: str):
    tag = _duration_input(html)
    m = re.search(r'value="([^"]*)"', tag)
    return m.group(1) if m else ""


class TestTheDurationField:
    def test_it_is_a_number_input_stepping_five_minutes(self, app):
        tag = _duration_input(_render_form(app, None))
        assert 'type="number"' in tag, (
            "the duration is still a fixed <select>, so a teacher cannot type a "
            "number of minutes")
        assert 'step="5"' in tag, (
            "the duration does not increment in five-minute steps")

    def test_a_new_paper_opens_on_sixty_minutes(self, app):
        html = _render_form(app, None)
        assert _duration_value(html) == "60", (
            "a new paper does not open on the default hour")

    def test_a_stored_duration_is_shown(self, app):
        assert _duration_value(_render_form(app, _exam(duration_minutes=90))) == "90"

    def test_an_unlimited_paper_shows_zero_not_sixty(self, app):
        """`0` is the Unlimited branch; the form must not quietly offer 60."""
        assert _duration_value(_render_form(app, _exam(duration_minutes=0))) == "0"
        assert _duration_value(_render_form(app, _exam(duration_minutes=None))) == "0"

    def test_the_builder_defaults_to_sixty(self):
        from app.routes import teacher as t

        class _Sb:
            def table(self, *a, **k):
                raise RuntimeError("no read should be needed for the default")

        d = t._builder_defaults(_Sb(), "t1", [{"id": "s1"}], [{"id": "c1"}])
        assert d["duration_minutes"] == 60, (
            "the duration default is not the requested 60 minutes")


# ── 2. target classes follow the subject's assignment ───────────────────────

class TestTargetClassesFollowTheAssignment:
    def test_the_route_builds_a_subject_to_classes_map(self):
        for name in ("exam_form", "exam_detail"):
            body = _body(name)
            assert "classes_by_subject" in body, (
                f"{name} does not compute which classes belong to which subject, so "
                "Target Classes can only be the teacher's whole assignment")

    def test_the_map_is_built_from_the_active_pairs(self):
        body = _body("_builder_scope")
        assert "teacher_assignments_for(" in body, (
            "the builder reads raw assignment rows rather than the active-pair "
            "reader, so a removed pair is offered back")

    def test_the_page_filters_the_class_grid_by_the_selected_subject(self):
        html = _form()
        assert "targetClasses(" in html, (
            "the class grid has no component that follows the chosen subject")
        assert re.search(r"hasClass\(\s*'", html), (
            "no class is filtered by the subject it is assigned to")

    def test_a_class_not_assigned_for_a_subject_is_hidden(self, app):
        html = _render_form(
            app, None,
            subjects=[{"id": "sub1", "name": "Fisika"}],
            classes=[{"id": "c1", "name": "8A"}, {"id": "c2", "name": "8B"}],
            classes_by_subject={"sub1": ["c1"]})
        # The mapping reaches the script as JSON, and c1 is the only class in it.
        assert '"sub1"' in html and '"c1"' in html, (
            "the subject→classes map never reaches the page")
        assert 'hasClass(' in html, "the grid does not consult the map"

    def test_an_admin_still_sees_every_class(self):
        """An admin runs the school; the classes are not scoped for them."""
        html = _form()
        assert "scoped" in html and "hasClass(" in html, (
            "the filter has no unscoped branch, so an admin would lose classes")
        srcline = _body("_builder_scope")
        assert "return subjects, classes, None" in srcline, (
            "the admin path is not marked unscoped, so the page cannot tell")


# ── 3. the two safe defaults are checked ────────────────────────────────────

def _anti_cheat_seeds(html: str) -> str:
    block = html.split("antiCheatSettings({", 1)[1].split("})", 1)[0]
    return block


class TestTheSafeDefaultsAreChecked:
    def test_block_screenshot_is_on_for_a_new_paper(self, app):
        seeds = _anti_cheat_seeds(_render_form(app, None))
        assert re.search(r"block_screenshot:\s*true", seeds), (
            "Block screenshot still defaults to off on a new paper")

    def test_lock_at_threshold_is_on_for_a_new_paper(self, app):
        seeds = _anti_cheat_seeds(_render_form(app, None))
        assert re.search(r"lock_pending_resume:\s*true", seeds), (
            "Lock at threshold still defaults to off on a new paper")

    def test_a_stored_off_still_renders_off(self, app):
        """The default is for a *new* paper; an existing row must win."""
        seeds = _anti_cheat_seeds(
            _render_form(app, _exam(block_screenshot=False, lock_pending_resume=False)))
        assert re.search(r"block_screenshot:\s*false", seeds)
        assert re.search(r"lock_pending_resume:\s*false", seeds)


# ── 4. one submit button ────────────────────────────────────────────────────

class TestOneSubmitButton:
    def test_the_form_has_a_single_action_button(self, app):
        html = _render_form(app, None)
        assert html.count('name="action"') == 1, (
            "the form still has both a Save and a Publish button")
        assert 'value="publish"' in html
        assert 'value="save_active"' not in html, (
            "the Save button is still there")

    def test_the_button_says_it_publishes_and_saves(self, app):
        html = _render_form(app, None)
        assert re.search(r"Publish.*Kirim|Kirim.*Kelas", html), (
            "the single button no longer names publishing")


# ── 5. publishing keeps the schedule ────────────────────────────────────────

class TestPublishingKeepsTheSchedule:
    def test_publish_no_longer_blanks_the_start(self):
        for name in ("exam_form", "exam_detail"):
            body = _body(name)
            assert not re.search(r'if action == "publish":\s*\n\s*start_at = None', body), (
                f"{name} still discards the scheduled start when publishing, so a "
                "paper scheduled for later opens immediately")

    def test_the_start_is_always_converted_from_the_form(self):
        for name in ("exam_form", "exam_detail"):
            body = _body(name)
            assert 'to_utc_iso(request.form.get("start_at"' in body, (
                f"{name} no longer reads the scheduled start from the form")
