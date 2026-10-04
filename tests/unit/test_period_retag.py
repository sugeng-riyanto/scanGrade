"""A period's tag is *derived*, so moving the calendar must re-derive it.

`exams.assessment_period_id` is the period a paper's window sits in (the tag added
with migration 055). The write doors recompute it when the *paper* is saved — but
nothing recomputed it when the **calendar** moved, so:

* editing a period's dates left every paper filed under it stale (its window is no
  longer in the period it names), and could leave a paper that a period now covers
  filed somewhere else;
* deleting a period left its papers dangling — the FK's `ON DELETE SET NULL` clears
  the column rather than deleting the papers, but nothing re-homed them to whatever
  period now covers their window.

This is the missing sweep. It shares one rule with the write doors (`_tag_for`) so
the two can never disagree, runs scoped to the caller's school on every read and
write, and writes only rows whose computed tag differs — a calendar edit that
changes nothing writes nothing, and running it twice is the same as running it once.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "assessment_periods.py"


# ── a fake that really filters, mutates and deletes ──────────────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters = []
        self.mode, self.payload = "select", None

    def select(self, *a, **k):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("!=" + column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("IN " + column, set(map(str, values))))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _matches(self, row):
        for key, value in self.filters:
            if key.startswith("!="):
                if str(row.get(key[2:])) == str(value):
                    return False
            elif key.startswith("IN "):
                if str(row.get(key[3:])) not in value:
                    return False
            elif str(row.get(key)) != str(value):
                return False
        return True

    def execute(self):
        self.store.calls.append((self.table, tuple(self.filters), self.payload,
                                 self.mode))
        if self.table in self.store.fail:
            raise RuntimeError("boom")
        if self.table in self.store.fail_writes and self.mode in ("update", "delete"):
            raise RuntimeError("write boom")
        rows = self.store.rows.setdefault(self.table, [])
        if self.mode == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"p{len(rows) + 1}")
            rows.append(row)
            return _Resp([row])
        if self.mode == "update":
            hit = [r for r in rows if self._matches(r)]
            for row in hit:
                row.update(self.payload)
            return _Resp(hit)
        if self.mode == "delete":
            hit = [r for r in rows if self._matches(r)]
            self.store.rows[self.table] = [r for r in rows if r not in hit]
            return _Resp(hit)
        return _Resp([r for r in rows if self._matches(r)])


class FakeSupabase:
    def __init__(self, rows=None, fail=(), fail_writes=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.fail = set(fail)
        self.fail_writes = set(fail_writes)
        self.calls = []

    def table(self, name):
        return _Query(self, name)

    def writes(self, table="exams"):
        return [c for c in self.calls if c[0] == table and c[3] in ("update", "delete")]

    def exam(self, exam_id):
        for row in self.rows.get("exams", []):
            if row.get("id") == exam_id:
                return row
        raise AssertionError(f"no exam {exam_id}")


def _period(**over):
    row = {"id": "p1", "school_id": "s1", "kind": "mid_semester", "name": "UTS",
           "start_date": "2026-09-01", "end_date": "2026-09-10", "is_active": True}
    row.update(over)
    return row


def _exam(**over):
    row = {"id": "e1", "school_id": "s1", "start_at": "2026-09-05T02:00:00",
           "end_at": "2026-09-05T03:00:00", "assessment_period_id": "p1"}
    row.update(over)
    return row


# ── the rule, shared with the write doors ────────────────────────────────────

class TestOneRuleForTheTag:
    def test_the_later_of_two_overlapping_periods_wins(self):
        """A paper inside two overlapping periods belongs to the later one, and
        that must not depend on the order a query happened to return them in."""
        from app.services import assessment_periods as ap

        early = _period(id="p-early", start_date="2026-09-01", end_date="2026-09-30")
        late = _period(id="p-late", start_date="2026-09-20", end_date="2026-09-25",
                       is_active=False)
        # Deliberately the wrong order: the newest must still win.
        tag = ap._tag_for(ap._as_date("2026-09-22"), [early, late])
        assert tag and tag["id"] == "p-late"

    def test_a_date_in_no_period_falls_back_to_the_running_one(self):
        from app.services import assessment_periods as ap

        p1 = _period(id="p1", start_date="2026-09-01", end_date="2026-09-10")
        p2 = _period(id="p2", start_date="2026-09-20", end_date="2026-09-30",
                     is_active=False)
        tag = ap._tag_for(ap._as_date("2026-09-15"), [p2, p1])
        assert tag and tag["id"] == "p1"

    def test_a_windowless_paper_takes_the_running_period(self):
        from app.services import assessment_periods as ap

        p1 = _period(id="p1", is_active=False)
        p2 = _period(id="p2", start_date="2026-12-01", end_date="2026-12-10",
                     is_active=True)
        tag = ap._tag_for(None, [p1, p2])
        assert tag and tag["id"] == "p2"

    def test_the_write_door_and_the_sweep_share_the_rule(self):
        """A second copy of the rule is how the two drift apart; the sweep must
        call the same helper the write door does."""
        src = SERVICE.read_text(encoding="utf-8")
        assert "def _tag_for(" in src
        assert src.count("_tag_for(") >= 2, (
            "period_for_exam and retag_school_exams must both use _tag_for")


# ── the sweep ────────────────────────────────────────────────────────────────

class TestRetaggingASchool:
    def test_a_stale_tag_is_corrected_to_the_period_that_now_covers_it(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [
                _period(id="p2", start_date="2026-09-20", end_date="2026-09-30"),
                _period(id="p1", start_date="2026-09-01", end_date="2026-09-10",
                        is_active=False)],
            "exams": [_exam(id="e1", start_at="2026-09-25T02:00:00",
                            end_at="2026-09-25T03:00:00", assessment_period_id="p1")]})

        out = ap.retag_school_exams(sb, "s1")

        assert out["retagged"] == 1
        assert sb.exam("e1")["assessment_period_id"] == "p2"

    def test_a_dangling_tag_is_corrected_rather_than_left_pointing_at_nothing(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [_period(id="p1")],
            "exams": [_exam(id="e1", assessment_period_id="ghost-period")]})

        ap.retag_school_exams(sb, "s1")

        assert sb.exam("e1")["assessment_period_id"] == "p1"

    def test_a_tag_with_no_period_left_is_cleared(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [],
            "exams": [_exam(id="e1", assessment_period_id="ghost")]})

        ap.retag_school_exams(sb, "s1")

        assert sb.exam("e1")["assessment_period_id"] is None

    def test_a_correct_tag_is_never_rewritten(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [_period(id="p1")],
            "exams": [_exam(id="e1", assessment_period_id="p1")]})

        out = ap.retag_school_exams(sb, "s1")

        assert out["retagged"] == 0
        assert sb.writes() == [], "a correct tag was rewritten"

    def test_only_the_callers_school_is_swept(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [_period(id="p1", school_id="s1")],
            "exams": [
                _exam(id="e1", school_id="s1", assessment_period_id="ghost"),
                _exam(id="e2", school_id="s2", assessment_period_id="ghost")]})

        ap.retag_school_exams(sb, "s1")

        assert sb.exam("e1")["assessment_period_id"] == "p1"
        assert sb.exam("e2")["assessment_period_id"] == "ghost", (
            "another school's paper was re-homed by this school's edit")
        for _table, filters, _payload, _mode in sb.writes():
            assert ("school_id", "s1") in filters, (
                "a re-tag write was not scoped to the caller's school")

    def test_a_read_that_fails_sweeps_nothing_and_does_not_raise(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [_period()]}, fail=("exams",))

        out = ap.retag_school_exams(sb, "s1")

        assert out["ok"] is False and out["retagged"] == 0
        assert sb.rows.get("exams", []) == []

    def test_a_write_that_fails_does_not_raise(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(
            rows={"assessment_periods": [_period(id="p1")],
                  "exams": [_exam(id="e1", assessment_period_id="ghost")]},
            fail_writes=("exams",))

        out = ap.retag_school_exams(sb, "s1")

        assert out["ok"] is False and out["reason"] == "write_failed"

    def test_the_sweep_is_idempotent(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [_period(id="p1")],
            "exams": [_exam(id="e1", assessment_period_id="ghost")]})

        first = ap.retag_school_exams(sb, "s1")
        before = len(sb.writes())
        second = ap.retag_school_exams(sb, "s1")

        assert first["retagged"] == 1 and second["retagged"] == 0
        assert len(sb.writes()) == before, "the second sweep wrote again"

    def test_after_a_sweep_every_tag_matches_the_write_doors_rule(self):
        """The invariant the sweep exists for: no paper disagrees with what the
        write door would set for it now."""
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [
                _period(id="p2", start_date="2026-09-20", end_date="2026-09-30"),
                _period(id="p1", start_date="2026-09-01", end_date="2026-09-10",
                        is_active=False)],
            "exams": [
                _exam(id="e1", start_at="2026-09-05T02:00:00",
                      end_at="2026-09-05T03:00:00", assessment_period_id="p2"),
                _exam(id="e2", start_at="2026-09-25T02:00:00",
                      end_at="2026-09-25T03:00:00", assessment_period_id="p1")]})

        ap.retag_school_exams(sb, "s1")

        for exam in sb.rows["exams"]:
            door = ap.period_for_exam(sb, "s1", exam["start_at"], exam["end_at"])
            want = door["id"] if door else None
            assert exam["assessment_period_id"] == want, exam["id"]


# ── the doors that change the calendar call the sweep ────────────────────────

class TestTheCalendarDoorsReDeriveTheTags:
    def test_editing_a_periods_dates_re_derives_the_tags(self):
        """`p2` grows to cover a paper that had fallen back to the running one."""
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [
                _period(id="p1", start_date="2026-09-01", end_date="2026-09-10"),
                _period(id="p2", start_date="2026-09-15", end_date="2026-09-25",
                        is_active=False)],
            "exams": [_exam(id="e1", start_at="2026-09-12T02:00:00",
                            end_at="2026-09-12T03:00:00",
                            assessment_period_id="p1")]})

        out = ap.save_period(sb, "s1", period_id="p2", kind="final_semester",
                             name="UAS", start_date="2026-09-10",
                             end_date="2026-09-25", is_active=False)

        assert out["ok"] is True
        assert sb.exam("e1")["assessment_period_id"] == "p2", (
            "the paper kept a tag the edited calendar no longer gives it")

    def test_a_period_that_loses_dates_releases_its_paper_to_the_running_one(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [
                _period(id="p2", start_date="2026-09-21", end_date="2026-09-30"),
                _period(id="p1", start_date="2026-09-01", end_date="2026-09-20",
                        is_active=False)],
            "exams": [_exam(id="e1", start_at="2026-09-18T02:00:00",
                            end_at="2026-09-18T03:00:00",
                            assessment_period_id="p1")]})

        ap.save_period(sb, "s1", period_id="p1", kind="mid_semester", name="UTS",
                       start_date="2026-09-01", end_date="2026-09-10",
                       is_active=False)

        assert sb.exam("e1")["assessment_period_id"] == "p2"

    def test_creating_a_period_re_derives_the_tags(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [
                _period(id="p1", start_date="2026-09-01", end_date="2026-09-10")],
            "exams": [_exam(id="e1", start_at="2026-09-18T02:00:00",
                            end_at="2026-09-18T03:00:00",
                            assessment_period_id="p1")]})

        ap.save_period(sb, "s1", period_id=None, kind="final_semester", name="UAS",
                       start_date="2026-09-15", end_date="2026-09-25",
                       is_active=False)

        new_period = sb.rows["assessment_periods"][-1]
        assert sb.exam("e1")["assessment_period_id"] == new_period["id"], (
            "a newly scheduled period did not claim the paper it covers")

    def test_moving_the_running_period_re_derives_the_windowless_papers(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [
                _period(id="p1", start_date="2026-09-01", end_date="2026-09-10"),
                _period(id="p2", start_date="2026-09-15", end_date="2026-09-25",
                        is_active=False)],
            "exams": [_exam(id="e1", start_at=None, end_at=None,
                            assessment_period_id="p1")]})

        ap.save_period(sb, "s1", period_id="p2", kind="final_semester", name="UAS",
                       start_date="2026-09-15", end_date="2026-09-25", is_active=True)

        assert sb.exam("e1")["assessment_period_id"] == "p2"

    def test_deleting_a_period_re_homes_its_papers(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [
                _period(id="p2", start_date="2026-09-01", end_date="2026-09-30"),
                _period(id="p1", start_date="2026-09-20", end_date="2026-09-25",
                        is_active=False)],
            "exams": [
                _exam(id="e1", start_at="2026-09-22T02:00:00",
                      end_at="2026-09-22T03:00:00", assessment_period_id="p1"),
                _exam(id="e2", start_at="2026-09-05T02:00:00",
                      end_at="2026-09-05T03:00:00", assessment_period_id="p2")]})

        out = ap.delete_period(sb, "s1", "p1")

        assert out["ok"] is True
        assert sb.exam("e1")["assessment_period_id"] == "p2", (
            "a paper filed under the deleted period was left dangling")
        assert sb.exam("e2")["assessment_period_id"] == "p2"

    def test_deleting_the_last_period_leaves_papers_untagged_not_dangling(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "assessment_periods": [_period(id="p1")],
            "exams": [_exam(id="e1", assessment_period_id="p1")]})

        ap.delete_period(sb, "s1", "p1")

        assert sb.exam("e1")["assessment_period_id"] is None

    def test_a_failed_sweep_does_not_turn_a_saved_period_into_a_failure(self):
        """The period *was* written; a sweep that could not read the exams must
        not report the calendar edit as refused."""
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [_period()]}, fail=("exams",))

        out = ap.save_period(sb, "s1", period_id="p1", kind="mid_semester",
                             name="UTS", start_date="2026-09-01",
                             end_date="2026-09-05", is_active=True)

        assert out["ok"] is True
        assert out["retag"]["ok"] is False, (
            "the sweep result should be reported, not hidden")
        assert sb.rows["assessment_periods"][0]["end_date"] == "2026-09-05"

    def test_both_calendar_writes_call_the_sweep(self):
        src = SERVICE.read_text(encoding="utf-8")
        save = src.split("def save_period(")[1].split("\ndef ")[0]
        delete = src.split("def delete_period(")[1].split("\ndef ")[0]
        assert "retag_school_exams(" in save, "editing a period never re-derives tags"
        assert "retag_school_exams(" in delete, "deleting a period never re-homes papers"
