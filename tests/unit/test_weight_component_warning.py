"""A paper filed under a component the subject does not weight is not left silent.

The final mark is the weighted sum of exactly the components a subject weights, so
a paper whose component is missing from that policy contributes **nothing** — it is
counted in ``untagged`` and shows up nowhere else. The realistic path into that
state is not the picker (it only ever offers weighted components): it is the
*drift* after the fact — the school admin drops a component from a subject's
weights, and every paper already filed under it silently stops counting.

Nothing warned anyone. This file guards the two signals that now do:

* :func:`grade_weighting.paper_weight_gap` — the read that names the gap, and is
  deliberately quiet in the three cases that are *not* a gap (an uncategorised
  paper, a subject with no weight policy at all, and an id the school does not own);
* the save door and the builder — a warning flash at save time, and a durable
  banner on the paper so the loss is not silent after the flash is gone.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest
from flask import get_flashed_messages

from app.services import grade_weighting as gw

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"

SCHOOL, SUBJECT, YEAR = "sch-1", "sub-1", "year-1"
TUGAS, PROYEK = "comp-tugas", "comp-proyek"


# ── the read ─────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    """A fake that really filters, so a missing scope is a real miss."""

    def __init__(self, store, table):
        self.store, self.table, self.filters = store, table, []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, str(val)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        def match(row):
            return all(str(row.get(col)) == val for col, val in self.filters)
        return _Resp([dict(r) for r in self.store.get(self.table, []) if match(r)])


class _Sb(dict):
    def table(self, name):
        return _Q(self, name)


def _sb(*, configs=(), tugas_default=0, proyek_default=0, proyek_active=True):
    """A school with two components; ``configs`` is ``[(component_id, percent)]``."""
    return _Sb(
        grade_component_type=[
            {"id": TUGAS, "school_id": SCHOOL, "name": "Tugas Kelas",
             "is_active": True, "default_weight": tugas_default, "sort_order": 1},
            {"id": PROYEK, "school_id": SCHOOL, "name": "Proyek",
             "is_active": proyek_active, "default_weight": proyek_default,
             "sort_order": 2},
        ],
        grade_weight_config=[
            {"school_id": SCHOOL, "subject_id": SUBJECT, "school_year_id": YEAR,
             "component_id": cid, "weight_percent": pct, "is_active": True}
            for cid, pct in configs
        ],
        subjects=[{"id": SUBJECT, "school_id": SCHOOL}],
    )


class TestTheGapRead:
    def test_a_weighted_component_is_not_a_gap(self):
        sb = _sb(configs=[(TUGAS, 100)])
        assert gw.paper_weight_gap(sb, SCHOOL, SUBJECT, TUGAS, YEAR) is None

    def test_a_component_the_subject_does_not_weight_is_named(self):
        """The load-bearing case: filed under Proyek, but only Tugas counts."""
        sb = _sb(configs=[(TUGAS, 100)])
        gap = gw.paper_weight_gap(sb, SCHOOL, SUBJECT, PROYEK, YEAR)
        assert gap is not None, (
            "a paper filed under an unweighted component read as fine — it is "
            "silently out of the final mark")
        assert gap["component_id"] == PROYEK
        assert gap["name"] == "Proyek"
        assert gap["weighted"] == ["Tugas Kelas"]

    def test_a_subject_with_no_policy_is_not_a_gap(self):
        """Nothing is weighted, so the simple mean counts every paper — no loss."""
        sb = _sb(configs=[])
        assert gw.paper_weight_gap(sb, SCHOOL, SUBJECT, PROYEK, YEAR) is None, (
            "a school that never configured weights was warned about a paper the "
            "simple mean already counts")

    def test_the_school_default_policy_counts(self):
        """No per-subject config, but the default distribution applies."""
        sb = _sb(tugas_default=100, configs=[])
        assert gw.paper_weight_gap(sb, SCHOOL, SUBJECT, TUGAS, YEAR) is None
        gap = gw.paper_weight_gap(sb, SCHOOL, SUBJECT, PROYEK, YEAR)
        assert gap and gap["weighted"] == ["Tugas Kelas"], (
            "the school's default weights were not read as the subject's policy")

    def test_an_uncategorised_paper_is_not_a_gap(self):
        """``None`` is a separate, deliberate state — the roster already reports it."""
        sb = _sb(configs=[(TUGAS, 100)])
        assert gw.paper_weight_gap(sb, SCHOOL, SUBJECT, None, YEAR) is None

    def test_a_component_the_school_does_not_own_is_not_a_gap(self):
        sb = _sb(configs=[(TUGAS, 100)])
        assert gw.paper_weight_gap(sb, SCHOOL, SUBJECT, "other-school-comp", YEAR) is None

    def test_a_deactivated_component_is_still_named(self):
        """The commonest drift: the admin deactivates the component."""
        sb = _sb(configs=[(TUGAS, 100)], proyek_active=False)
        gap = gw.paper_weight_gap(sb, SCHOOL, SUBJECT, PROYEK, YEAR)
        assert gap and gap["name"] == "Proyek", (
            "a component switched off was not named, so the paper is silent again")


class TestTheSaveTimeWarningStaysNarrow:
    """The three quiet cases, proved through the door that reads the gap."""

    def test_only_the_unweighted_case_flashes(self):
        assert gw.paper_weight_gap(
            _sb(configs=[(TUGAS, 100)]), SCHOOL, SUBJECT, TUGAS, YEAR) is None
        assert gw.paper_weight_gap(
            _sb(configs=[]), SCHOOL, SUBJECT, PROYEK, YEAR) is None
        assert gw.paper_weight_gap(
            _sb(configs=[(TUGAS, 100)]), SCHOOL, SUBJECT, None, YEAR) is None
        assert gw.paper_weight_gap(
            _sb(configs=[(TUGAS, 100)]), SCHOOL, SUBJECT, PROYEK, YEAR) is not None


# ── the flash at save time ───────────────────────────────────────────────────

class TestTheWarningAtSaveTime:
    def test_the_helper_flashes_a_named_warning(self, app):
        from app.routes import teacher as t
        with app.test_request_context("/teacher/exams/new"):
            t._flash_weight_gap({"component_id": PROYEK, "name": "Proyek",
                                 "weighted": ["Tugas Kelas"]})
            messages = get_flashed_messages(with_categories=True)
        assert messages, "the gap produced no warning at all"
        category, text = messages[0]
        assert category == "warning", f"the warning is filed as {category!r}"
        assert "Proyek" in text, (
            "the warning does not name the component the paper is filed under")
        assert "Tugas Kelas" in text, (
            "the warning does not name a component that would count, so the "
            "teacher is told there is a problem but not how to fix it")

    def test_the_helper_is_silent_when_there_is_no_gap(self, app):
        from app.routes import teacher as t
        with app.test_request_context("/teacher/exams/new"):
            t._flash_weight_gap(None)
            assert get_flashed_messages(with_categories=True) == []

    def test_both_save_doors_read_the_gap(self):
        src = TEACHER.read_text(encoding="utf-8")
        for name in ("exam_form", "exam_detail"):
            body = _body(name)
            assert "paper_weight_gap(" in body, (
                f"{name} never reads the paper's component against the subject's "
                "weights, so the save is silent")
            assert "_flash_weight_gap(" in body, (
                f"{name} reads the gap but never warns the teacher")

    def test_the_builder_is_handed_the_gap_for_a_stored_paper(self):
        body = _body("exam_detail")
        assert "component_gap=" in body, (
            "the edit page is not handed the gap, so opening a drifted paper shows "
            "nothing")


# ── the durable banner on the paper ──────────────────────────────────────────

@contextlib.contextmanager
def _signed_in(app, path):
    from flask import g
    with app.test_request_context(path):
        g.user_id, g.user_name, g.user_role = "u-1", "Guru Uji", "guru"
        g.user_email, g.tz_offset, g.show = "g@example.test", 7, {}
        g.user_school_id, g.user_class_id = SCHOOL, None
        yield


def _exam(**over):
    row = {
        "id": "e1", "title": "Latihan", "subject": "Fisika", "subject_id": SUBJECT,
        "grade_component_type_id": PROYEK,
        "duration_minutes": 45, "total_questions": 10, "question_types": {},
        "end_at": None, "auto_submit_on_window_end": False, "sg_in_progress": False,
        "answer_key": {}, "class_ids": [], "description": "", "max_attempts": 1,
        "pdf_url": None, "question_audio": {}, "question_cognitive": {},
        "question_pages": {}, "question_weights": {}, "start_at": None,
    }
    row.update(over)
    return row


def _render_form(app, *, component_gap=None):
    with _signed_in(app, "/teacher/exams/e1"):
        return app.jinja_env.get_template("teacher/exam_form.html").render(
            exam=_exam(), subjects=[], classes=[], grade_components={},
            builder_defaults={"subject_id": None, "class_ids": [],
                              "duration_minutes": 60},
            component_gap=component_gap)


GAP = {"component_id": PROYEK, "name": "Proyek", "weighted": ["Tugas Kelas"]}


class TestTheBannerOnThePaper:
    def test_a_drifted_paper_shows_the_gap(self, app):
        html = _render_form(app, component_gap=GAP)
        assert "data-component-gap" in html, (
            "the paper shows no durable warning, so once the save flash is gone the "
            "loss is silent again")
        # Bilingual, like the rest of the builder.
        assert "Proyek" in html and "Tugas Kelas" in html
        assert re.search(r"Tidak dihitung|not counted|NOT counted", html), (
            "the banner does not say the paper misses the final mark")

    def test_a_healthy_paper_shows_no_banner(self, app):
        html = _render_form(app, component_gap=None)
        assert "data-component-gap" not in html, (
            "a paper that counts under its subject is warned about anyway")

    def test_the_banner_states_both_languages(self, app):
        html = _render_form(app, component_gap=GAP)
        block = html.split("data-component-gap", 1)[1].split("</div>", 1)[0]
        assert "t(" in block, (
            "the banner carries no bilingual pair, so it lands in the coverage "
            "metric as untranslated copy")


def _body(name: str) -> str:
    src = TEACHER.read_text(encoding="utf-8")
    start = src.index(f"def {name}(")
    match = re.search(r"\r?\ndef ", src[start:])
    return src[start:start + match.start()] if match else src[start:]
