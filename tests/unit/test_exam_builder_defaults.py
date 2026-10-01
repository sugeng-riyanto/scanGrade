"""The exam builder's defaults, and the promise that disclosure removed nothing.

Approved redesign of /teacher/exams/new: a hurried teacher should not have to walk
past thirteen optional controls, while a teacher who needs control keeps every one
of them. The two halves of that promise are guarded here:

* what may be pre-filled is *derivable* (sole assignment, or this teacher's last
  duration) — never guessed. With more than one choice the field is left empty,
  because a wrong guess is worse than an empty field;
* progressive disclosure hides controls, it does not delete them. Every field the
  page had before must still be in the template, so un-checking a default works.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.routes import teacher as t

TEMPLATE = (Path(__file__).resolve().parents[2] / "app" / "templates"
            / "teacher" / "exam_form.html")

#: Every control the builder had before the redesign. If one disappears, the
#: guard fails: "advanced" may collapse a control, never remove it.
INVENTORY_FIELDS = (
    "title", "subject_id", "class_ids", "description", "duration_minutes",
    "start_at", "end_at", "max_attempts", "auto_submit_on_window_end",
    "total_questions", "question_types", "answer_key", "question_weights",
    "question_pages", "question_audio", "question_cognitive",
    "anti_cheat_enabled", "penalty_per_violation", "max_violations",
    "fullscreen_required", "watermark_name", "block_copy_paste",
    "block_right_click", "auto_submit_on_max", "allow_calculator",
    "block_screenshot", "randomize_questions", "randomize_options", "action",
)


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Sb:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Q(self.rows)


def _subjects(n):
    return [{"id": f"s{i}"} for i in range(n)]


def _classes(n):
    return [{"id": f"c{i}"} for i in range(n)]


class TestDerivableDefaults:
    def test_a_sole_subject_and_class_are_prefilled(self):
        d = t._builder_defaults(_Sb([]), "t1", _subjects(1), _classes(1))
        assert d["subject_id"] == "s0"
        assert d["class_ids"] == ["c0"]

    def test_two_choices_are_not_guessed(self):
        d = t._builder_defaults(_Sb([]), "t1", _subjects(2), _classes(3))
        assert d["subject_id"] is None, "a choice to make must be left to the teacher"
        assert d["class_ids"] == []

    def test_duration_comes_from_this_teachers_last_exam(self):
        d = t._builder_defaults(_Sb([{"duration_minutes": 90}]), "t1",
                                _subjects(2), _classes(2))
        assert d["duration_minutes"] == 90

    def test_no_history_falls_back_to_the_app_default(self):
        d = t._builder_defaults(_Sb([]), "t1", _subjects(2), _classes(2))
        assert d["duration_minutes"] == 60

    def test_a_failed_history_read_still_yields_the_app_default(self):
        class Boom:
            def table(self, name):
                raise RuntimeError("supabase down")

        d = t._builder_defaults(Boom(), "t1", _subjects(1), _classes(1))
        assert d["duration_minutes"] == 60


class TestDisclosureRemovedNothing:
    def test_every_inventory_field_is_still_in_the_template(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        missing = [f for f in INVENTORY_FIELDS if f'name="{f}"' not in src]
        assert not missing, f"the redesign dropped field(s): {missing}"

    def test_the_same_checkbox_can_always_be_un_checked(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        # The anti-cheat switches stay real checkboxes even behind the expander.
        for field in ("fullscreen_required", "block_copy_paste", "randomize_options"):
            assert f'type="checkbox"' in src
            assert field in src


class TestStructureAndIndicators:
    def test_there_is_an_advanced_layer_that_collapses(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "x-show=\"advanced\"" in src, "the advanced layer is missing"
        assert "advanced = !advanced" in src, "nothing toggles the advanced layer"

    def test_the_anti_cheat_switches_are_not_behind_a_toggle(self):
        """The fairness policy is read before publishing, and a control nobody
        opens is a control nobody reads — the switches are on screen, and the
        one-line summary above them says what the paper enforces."""
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "penalty_per_violation" in src and "max_violations" in src
        assert "acDetail" not in src, "an anti-cheat switch is behind an expander again"
        for field in ("fullscreen_required", "allow_calculator", "block_screenshot"):
            assert f'name="{field}"' in src

    def test_the_auto_fill_badge_exists_and_clears_on_touch(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "subjectAuto" in src and "durationAuto" in src and "classAuto" in src
        assert "durationAuto=false" in src, "the badge must clear when the teacher changes it"
        assert "'otomatis'" in src or '"otomatis"' in src

    def test_the_rendered_builder_attribute_is_well_formed(self, app):
        """A double quote inside the `x-data` value ends the HTML attribute, the
        browser prints the rest of the object as text and Alpine never starts —
        every collapsed control stays hidden and the page reads as broken. This
        happened for real (a `// "otomatis"…` comment), so the rendered attribute
        is checked for quotes rather than trusted."""
        import re
        from flask import g

        with app.test_request_context("/teacher/exams/new"):
            g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
            g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
            g.user_school_id, g.user_class_id = "sch-1", "cls-1"
            html = app.jinja_env.get_template("teacher/exam_form.html").render(
                exam=None, subjects=[], classes=[],
                builder_defaults={"subject_id": None, "class_ids": [],
                                  "duration_minutes": 60})
        attr = re.search(r'<div x-data="(.*?)"\s*\n\s*x-init=', html, re.S)
        assert attr, "the builder's x-data attribute no longer parses"
        assert '"' not in attr.group(1), (
            "a double quote inside x-data ends the attribute; the browser then "
            "prints the rest as text and Alpine never starts")
