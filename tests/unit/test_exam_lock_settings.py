"""The lock-on-violation feature is unreachable until the builder can turn it on.

`resume_code.decide` reads two columns — `exams.lock_pending_resume` (opt in to
locking at the violation threshold instead of ending the paper) and
`exams.resume_code_limit` (how many times the pupil may use their code) — and
`submissions.resume_limit` is copied from the second when a sitting opens.
Migration 054 added all of them, and its own closing note said the RBAC parity and
the UI would follow "di fase UI".

They never did. Nothing writes either column: not `POST /teacher/exams/new`, not
`POST /teacher/exams/<id>`, and no control on `exam_form.html` posts them. So the
column keeps its `false` default for every paper a teacher can build, `decide()`
returns `CONTINUE`, and the whole lock/resume path — the pupil's recovery code,
the locked state, the audit — can never run. The feature exists and cannot be
reached, which from a teacher's chair is the same as not existing.

These guards pin the two write doors and the one form, so the control cannot go
missing again. They are source guards on purpose: the failure is a field that is
simply absent, and a request-level test would have to build a whole exam to see it.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEACHER_ROUTES = ROOT / "app" / "routes" / "teacher.py"
EXAM_FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"


def _create_body() -> str:
    src = TEACHER_ROUTES.read_text(encoding="utf-8")
    return src.split("def exam_form(")[1].split("\ndef ")[0]


def _update_body() -> str:
    src = TEACHER_ROUTES.read_text(encoding="utf-8")
    return src.split("def exam_detail(")[1].split("\ndef ")[0]


class TestTheWriteDoorsPersistTheLockSettings:
    def test_the_create_door_reads_the_lock_toggle(self):
        body = _create_body()
        assert 'request.form.get("lock_pending_resume")' in body, (
            "POST /teacher/exams/new never reads the lock toggle, so the column "
            "stays at its false default and the lock path can never run")

    def test_the_create_door_reads_the_resume_limit(self):
        body = _create_body()
        assert 'request.form.get("resume_code_limit"' in body, (
            "POST /teacher/exams/new never reads the resume limit")

    def test_the_update_door_reads_both(self):
        body = _update_body()
        assert 'request.form.get("lock_pending_resume")' in body, (
            "POST /teacher/exams/<id> never reads the lock toggle")
        assert 'request.form.get("resume_code_limit"' in body, (
            "POST /teacher/exams/<id> never reads the resume limit")

    def test_both_doors_put_the_settings_in_the_row_they_write(self):
        for name, body in (("create", _create_body()), ("update", _update_body())):
            assert '"lock_pending_resume": lock_pending_resume' in body, (
                f"the {name} payload does not carry lock_pending_resume")
            assert '"resume_code_limit": resume_code_limit' in body, (
                f"the {name} payload does not carry resume_code_limit")

    def test_the_limit_is_clamped_to_a_sane_range(self):
        """0 means "no automatic unlock, send the pupil to the teacher"; a negative
        or an absurd number must not reach the column."""
        for name, body in (("create", _create_body()), ("update", _update_body())):
            assert re.search(r"max\(\s*0\s*,", body), (
                f"the {name} door does not floor the resume limit at 0")
            assert re.search(r"min\(\s*(\d+)\s*,", body), (
                f"the {name} door does not cap the resume limit")


class TestTheBuilderShowsTheControl:
    def test_the_form_posts_the_lock_toggle(self):
        html = EXAM_FORM.read_text(encoding="utf-8")
        assert re.search(r'name="lock_pending_resume"', html), (
            "the exam builder has no lock-on-violation switch, so no teacher can "
            "opt a paper into locking")

    def test_the_form_posts_the_resume_limit(self):
        html = EXAM_FORM.read_text(encoding="utf-8")
        assert re.search(r'name="resume_code_limit"', html), (
            "the exam builder has no resume-limit field")

    def test_the_seeds_come_from_the_stored_row(self):
        """An exam saved with locking on must render on, not reset to the default —
        the same shadowing bug the shuffle toggles had."""
        html = EXAM_FORM.read_text(encoding="utf-8")
        block = html.split("antiCheatSettings({", 1)[1].split("})", 1)[0]
        assert "lock_pending_resume:" in block, (
            "the anti-cheat card does not seed the lock toggle from the exam row")
        assert "resume_code_limit:" in block, (
            "the anti-cheat card does not seed the resume limit from the exam row")

    def test_the_labels_are_bilingual(self):
        html = EXAM_FORM.read_text(encoding="utf-8")
        html = html.split("name=\"lock_pending_resume\"", 1)[0][-600:]
        assert "t('" in html, (
            "the lock switch's label is not a bilingual t() pair")
        tail = EXAM_FORM.read_text(encoding="utf-8")
        tail = tail.split('name="resume_code_limit"', 1)[0][-600:]
        assert "t('" in tail, (
            "the resume-limit field's label is not a bilingual t() pair")


# ── the other direction: an absent field read as ON, and a second writer ──────
#
# Every rule above asserts the doors *read* the two fields. The reader is where this
# feature can still be wrong: a form value is a *string*, so a door that reads the
# toggle for its truthiness turns a posted `"false"` into "locking is on" — one more
# paper that locks itself, with the control visibly off in the builder. And the write
# is pinned to one file: the two doors below are the only places either column is ever
# set, so a second writer (a bulk edit, a duplicate, an AI path) is a finding here
# rather than a paper that locked itself for a reason nothing on the page explains.

COLUMNS = ("lock_pending_resume", "resume_code_limit")

#: Every file that may *name* either column, and what it does with it. A reader may
#: select the column and the builder may post it; only `teacher.py` puts either in a
#: row it writes. The list is a declaration, not a permission: a new name here is a
#: decision somebody wrote down.
MENTIONS = {
    "app/routes/teacher.py": "the two write doors — the only place either column is set",
    "app/services/resume_code.py": "the decision that reads them (`decide`)",
    "app/services/attempt_status.py": "the status read that selects them",
    "app/routes/api.py": "the sync route's select list",
    "app/routes/seb.py": "the SEB door's select list",
    "app/templates/teacher/exam_form.html": "the only control that posts them",
}


class TestTheToggleIsReadAsAStringAndWrittenOnce:
    def test_a_missing_or_false_toggle_is_off(self):
        """`bool("false")` is True — the whole reason the read is an equality."""
        for name, body in (("create", _create_body()), ("update", _update_body())):
            assert 'request.form.get("lock_pending_resume") == "true"' in body, (
                f"the {name} door no longer compares the toggle with the string it posts, "
                "so a posted 'false' or a missing field can read as on")
            assert 'bool(request.form.get("lock_pending_resume"))' not in body, (
                f"the {name} door reads the toggle for its truthiness, and every string a "
                "form posts — 'false' included — is truthy")

    def test_no_payload_outside_the_two_doors_carries_either_column(self):
        for path in (ROOT / "app").rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if relative == "app/routes/teacher.py":
                continue
            text = path.read_text(encoding="utf-8")
            for column in COLUMNS:
                assert f'"{column}":' not in text, (
                    f"{relative} builds a row carrying {column}: the write belongs in the "
                    "builder's two doors, where the control that sets it lives")

    def test_no_third_file_names_either_column_at_all(self):
        """A new reader is fine to have; a new *writer* is not, and this is where it shows."""
        named = set()
        for path in (ROOT / "app").rglob("*"):
            if path.suffix not in (".py", ".html"):
                continue
            text = path.read_text(encoding="utf-8")
            if any(column in text for column in COLUMNS):
                named.add(path.relative_to(ROOT).as_posix())
        assert named == set(MENTIONS), (
            "the lock columns gained a place that names them: "
            f"{sorted(named ^ set(MENTIONS))} — declare it in MENTIONS, and if it writes "
            "either column, move the write into the exam form's doors")
