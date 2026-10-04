"""Tick a pupil into a track, un-tick them out of it — and never \"assign\".

Requested, looking at *Kelas & Level — Civics*: *\"Add check list and un checklist
students. There is dropdown basics, intermediate and advances. Please add checklist
and un checklist (not assign) the class.\"*

Two changes, and the last clause is the one worth guarding.

**The pupils.** A track was one `<select>` per pupil: 40 pupils is 40 dropdowns, and
picking the three advanced ones means reading every name and opening a select each
time. It is now three level groups per class, each listing the pupils with a
checkbox — so the admin opens *Lanjutan* and ticks the three, which is the shape the
work actually has. A pupil is on exactly one track, so ticking them in a group moves
them there, and un-ticking a pupil who is *on* a non-default track returns them to the
default one. The default is the server's (`default_level`, migration 049's basic) and
is never spelled out in the browser: the vocabulary lives in
`app/services/subject_levels.LEVELS`, and a second copy here is how the page and the
CHECK constraint would drift.

**The class tick-list.** It stays a tick-list, and it now writes on the tick rather
than behind a *Simpan Kelas* button — the same immediacy the rest of this page has.
What it must never become is an *assignment*: `class_subjects` says which classes
*offer* a subject, and `teacher_assignments` says *who teaches* it. Those are
different sentences, and the bug this repository keeps closing is a page that
conflates them — a tick that quietly wrote an assignment row would put a teacher on a
pair nobody chose. The last guard below pins that the write touches no other table.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "subjects.html"
SERVICE = ROOT / "app" / "services" / "subject_levels.py"


def _html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _script() -> str:
    """The Alpine factory only — so a `data-` attribute cannot satisfy a guard."""
    html = _html()
    return html.split("<script>", 1)[1].split("</script>", 1)[0]


def _markup() -> str:
    html = _html()
    return html.split("<script>", 1)[0]


# ── the write is a tick, never an assignment ───────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = []
        self.op = "select"
        self.payload = None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def eq(self, col, val):
        self.filters.append((col, str(val)))
        return self

    def in_(self, col, values):
        self.filters.append((col, {str(v) for v in values}))
        return self

    def upsert(self, payload, on_conflict=None):
        self.op = "upsert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _match(self, row):
        for col, val in self.filters:
            got = str(row.get(col))
            if isinstance(val, set):
                if got not in val:
                    return False
            elif got != val:
                return False
        return True

    def execute(self):
        self.db.log.append((self.op, self.table, self.payload))
        rows = [dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)]
        return _Res(rows)


class _DB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.log = []

    def table(self, name):
        return _Q(self, name)


def test_the_class_tick_writes_only_the_offering_table():
    """`class_subjects` says what is offered; it must never say who teaches it."""
    from app.services import subject_levels as sl

    db = _DB(
        subjects=[{"id": "s1", "school_id": "sch"}],
        classes=[{"id": "c1", "school_id": "sch"}, {"id": "c2", "school_id": "sch"}],
        class_subjects=[],
    )
    ok, _res = sl.save_mapping(db, "sch", "s1", ["c1", "c2"], created_by="admin")
    assert ok
    written = {table for op, table, _p in db.log if op in ("upsert", "update", "insert")}
    assert written == {"class_subjects"}, (
        "the class tick wrote to another table, so ticking a class has begun to "
        f"mean an assignment: {sorted(written)}")

    # The pupils' levels share this rule: they belong to `student_subject_levels`.
    db2 = _DB(
        subjects=[{"id": "s1", "school_id": "sch"}],
        classes=[{"id": "c1", "school_id": "sch"}],
        profiles=[{"id": "p1", "role": "murid", "school_id": "sch", "class_id": "c1"}],
        student_subject_levels=[],
    )
    ok2, _r2 = sl.save_levels(db2, "sch", "s1", "c1", {"p1": "advanced"}, set_by="admin")
    assert ok2
    written2 = {table for op, table, _p in db2.log if op in ("upsert", "update", "insert")}
    assert written2 == {"student_subject_levels"}, sorted(written2)


def test_re_saving_the_same_selection_writes_nothing():
    """Auto-save means one save per tick, so a tick must cost one row at most.

    The modal posts its whole selection every time (that is what keeps two tabs
    honest), so `save_mapping` has to write only what actually changed — otherwise
    a tick on a school with 22 classes rewrites all 22 rows.
    """
    from app.services import subject_levels as sl

    db = _DB(
        subjects=[{"id": "s1", "school_id": "sch"}],
        classes=[{"id": "c1", "school_id": "sch"}],
        class_subjects=[{"id": "cs1", "class_id": "c1", "subject_id": "s1",
                         "school_id": "sch", "is_active": True}],
    )
    ok, res = sl.save_mapping(db, "sch", "s1", ["c1"])
    assert ok
    assert res["added"] == 0 and res["removed"] == 0
    writes = [(op, p) for op, t, p in db.log
              if op in ("upsert", "update", "insert")]
    assert not writes, (
        "saving an unchanged selection rewrote rows that were already right")


def test_ticking_one_more_class_writes_only_that_class():
    from app.services import subject_levels as sl

    db = _DB(
        subjects=[{"id": "s1", "school_id": "sch"}],
        classes=[{"id": "c1", "school_id": "sch"}, {"id": "c2", "school_id": "sch"}],
        class_subjects=[{"id": "cs1", "class_id": "c1", "subject_id": "s1",
                         "school_id": "sch", "is_active": True}],
    )
    ok, res = sl.save_mapping(db, "sch", "s1", ["c1", "c2"])
    assert ok and res["added"] == 1
    upserts = [p for op, t, p in db.log if op == "upsert"]
    assert len(upserts) == 1, "more than one upsert for one new tick"
    rows = upserts[0] if isinstance(upserts[0], list) else [upserts[0]]
    assert {str(r["class_id"]) for r in rows} == {"c2"}, (
        "the tick rewrote a class that was already on")


# ── the pupil control: three groups, one checkbox each ─────────────────────

class TestThePupilsAreGroupedByLevel:
    def test_the_per_pupil_dropdown_is_gone(self):
        markup = _markup()
        assert not re.search(r'setLevel\(cid', markup), (
            "the per-pupil `<select>` is still there, so a class of 40 is still 40 "
            "dropdowns to read")
        script = _script()
        assert "setLevel(" not in script, (
            "the old per-pupil setter is still reachable from the page")

    def test_each_level_is_its_own_group_with_a_count(self):
        markup = _markup()
        assert "levelGroups(" in markup, "the pupils are not grouped by level"
        assert "count" in markup or "length" in markup, (
            "the group header does not say how many pupils are in it")

    def test_a_pupil_has_a_checkbox_in_every_group(self):
        """They must be tickable into a group they are not currently in."""
        markup = _markup()
        assert re.search(r'type="checkbox"[^>]*toggleLevel\(', markup, re.S) or \
            re.search(r'toggleLevel\([^)]*\)"[^>]*type="checkbox"', markup, re.S), (
            "the level rows have no checkbox wired to the toggle handler")
        assert "@change=\"toggleLevel(" in markup, (
            "the checkbox does not report which side it moved to")

    def test_the_toggle_knows_the_pupil_the_class_and_the_level(self):
        found = re.search(r"toggleLevel\(([^)]*)\)", _markup())
        assert found, "no toggle handler at all"
        args = found.group(1)
        for token in ("cid", "student_id", "level"):
            assert token in args, f"the toggle handler does not receive `{token}`"

    def test_the_groups_come_from_the_servers_vocabulary(self):
        """`basic/intermediate/advanced` is `subject_levels.LEVELS`; a second copy
        in the browser is how the page and 049's CHECK drift apart.

        The three *names* are still display copy (they are labels), but the *list*
        and the default must be the server's answer, read on open.
        """
        script = _script()
        assert "d.levels" in script, "the server's level list is dropped on arrival"
        assert "d.default_level" in script, "the server's default level is dropped"
        assert re.search(r"this\.levels\s*\|\|\s*\[\]", script), (
            "the groups are not built from the list the server sent")
        assert "['basic','intermediate','advanced']" not in script and \
            '["basic","intermediate","advanced"]' not in script, (
            "the vocabulary is hard-coded in the browser again")
        markup = _markup()
        assert "defaultTrack()" in markup, (
            "the group header does not read the default from the server's answer")


class TestTickingAPupilWritesThatPupil:
    def test_the_toggle_posts_a_one_pupil_map(self):
        script = _script()
        body = script.split("toggleLevel(", 1)[1]
        assert re.search(r"levels:\s*\{", body), (
            "the toggle does not send a levels map, so nothing is written")
        assert "class_id" in body, "the toggle does not name the class"

    def test_u_ticking_returns_the_pupil_to_the_servers_default(self):
        script = _script()
        body = script.split("toggleLevel(", 1)[1].split("\n        },", 1)[0]
        assert "defaultTrack()" in body, (
            "un-ticking a pupil does not use the server's default track")
        for literal in ("'basic'", '"basic"', "'intermediate'", '"intermediate"'):
            assert literal not in body, (
                f"un-ticking writes `{literal}`, a level the browser chose rather "
                f"than the one the server declared")

    def test_it_writes_only_the_pupil_that_changed(self):
        """One checkbox is one write, not the whole 40-pupil roster."""
        script = _script()
        body = script.split("toggleLevel(", 1)[1].split("\n        },", 1)[0]
        assert "rosters[cid]" not in body or "forEach" not in body, (
            "a single tick re-posts the entire class roster")

    def test_the_server_refusal_reaches_the_reader(self):
        script = _script()
        body = script.split("toggleLevel(", 1)[1].split("\n        },", 1)[0]
        assert "levelMsg" in body, (
            "a refused or failed level write leaves no message on screen")


# ── the class tick-list saves on the tick ─────────────────────────────────

class TestTheClassTickSavesItself:
    def test_the_save_button_is_gone(self):
        markup = _markup()
        assert "Simpan Kelas" not in markup and "Save classes" not in markup.lower(), (
            "the explicit save button is still there, so a tick can still be lost")

    def test_ticking_calls_the_save(self):
        script = _script()
        body = script.split("toggleClass(", 1)[1].split("\n        },", 1)[0]
        assert "saveMapping()" in body, (
            "ticking a class no longer saves it, so the tick would be lost on close")

    def test_the_class_toggle_still_posts_the_whole_selection(self):
        """The modal owns the selection; a delta would let two tabs disagree."""
        script = _script()
        assert "saveMapping() {" in script, "the save function is gone"
        body = script.split("saveMapping() {", 1)[1].split("\n        },", 1)[0]
        assert "mappedIds" in body, "the save no longer posts the whole selection"

    def test_a_failed_class_save_is_still_reported(self):
        script = _script()
        body = script.split("saveMapping() {", 1)[1].split("\n        },", 1)[0]
        assert "mappingMsg" in body


# ── the copy, in both languages ───────────────────────────────────────────

class TestTheNewCopyIsBilingual:
    def test_every_new_string_has_a_pair(self):
        markup = _markup()
        block = markup.split("Level murid per kelas", 1)[1] if \
            "Level murid per kelas" in markup else markup
        assert "t('" in block, "the level groups carry no i18n helper"

    def test_the_default_is_explained(self):
        """\"Un-ticking puts this pupil back to Dasar\" has to be said, or the
        behaviour looks like a lost edit."""
        markup = _markup()
        assert re.search(r"bawaan|default|Dasar", markup), (
            "the page never says that un-ticking returns a pupil to the default track")
