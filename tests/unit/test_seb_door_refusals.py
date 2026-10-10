"""A pupil turned away at the SEB door is recorded, shown — and never charged.

Why the record did not exist, and why the door is the wrong place for it
-----------------------------------------------------------------------
The exam door already logs one line (``"SEB refused: exam %s without a matching
Config Key or claim"``) and that line is the *only* trace of a pupil who could not
get in: stamped with the exam, so "how many of my pupils were turned away, and when"
cannot be asked of it twice.

The obvious fix — write a row where that line is logged — is wrong, and measurably
so: SEB for macOS/iOS runs on WKWebView, which cannot send the Config Key header at
all, so on an iPad **every honest visit** is redirected to the handshake page and then
admitted. A row written at the door would count that pupil as turned away, on every
gated paper, for ever, and the number would be wrong in the direction that blames the
platform the feature exists to support.

A refusal is *decided* in the handshake, which has exactly three endings: the value
matched (the paper opens, nothing recorded), the value did not match
(``config_key_mismatch``, recorded by the claim route), or there was nothing to
answer with (``no_client`` / ``no_key``, recorded by the report the page itself
sends). One locked-out visit, one row.

Not a penalty
-------------
A pupil refused at the door has sat nothing; most often they opened the link in
Chrome, or their ``.seb`` file predates an edit the teacher made. Charging them is
charging a child for their device or for the teacher's own file. So the record goes
to its own table (migration 064) and the ladder is never touched — and that is
asserted here rather than promised in a docstring, because a reason name quietly
added to ``PENALIZED_VIOLATION_TYPES`` is exactly how this would become a penalty.

Run, not read, where it matters
-------------------------------
The two route behaviours (a mismatched claim, the page's report) drive the real view
bodies through the fake PostgREST store ``tests/unit/test_seb_js_api.py`` already
built, and the two surfaces are **rendered** — a grep for the word "ditolak" passes
on a card whose condition never fires.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import seb_door_log as door

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


PANEL = _read("app", "templates", "teacher", "seb_panel.html")
RESULTS = _read("app", "templates", "teacher", "results.html")
CLAIM = _read("app", "templates", "student", "seb_claim.html")
SEB = _read("app", "routes", "seb.py")
TEACHER = _read("app", "routes", "teacher.py")
MIGRATION = _read("supabase", "migrations", "064_seb_door_refusals.sql")
ANTI = _read("app", "services", "anti_cheat_service.py")

#: Markup with its comments removed. A guard that fires on the prose explaining it is
#: a guard somebody deletes, which this repository has already paid for once.
_COMMENT = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)


def _code(text: str) -> str:
    return _COMMENT.sub("", text)


EXAM_ID = "exam-1"
PUPIL = "stu-1"
OTHER_CLASS = "class-9Z"
SCHOOL = "school-A"
CLASS = "class-7A"
KEY = "a" * 64

EARLY = "2026-09-19T01:02:03+00:00"
LATER = "2026-09-19T03:04:05+00:00"


# ── a postgrest stand-in, small enough to read ──────────────────────────────

class _Query:
    def __init__(self, store, name):
        self._store, self._name = store, name
        self._filters, self._pending = [], None
        self._one, self._order, self._limit = False, None, None

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def neq(self, *a, **k):
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def maybe_single(self):
        self._one = True
        return self

    def insert(self, payload):
        self._pending = dict(payload)
        return self

    def _matches(self):
        out = []
        for row in self._store.rows(self._name):
            hit = True
            for column, want in self._filters:
                if isinstance(want, list):
                    hit = hit and row.get(column) in want
                else:
                    hit = hit and row.get(column) == want
            if hit:
                out.append(row)
        return out

    def execute(self):
        self._store.raise_if_broken(self._name)
        if self._pending is not None:
            row = dict(self._pending)
            row.setdefault("id", f"row-{len(self._store.rows(self._name)) + 1}")
            self._store.tables.setdefault(self._name, []).append(row)
            self._pending = None
            return SimpleNamespace(data=[row], count=1)
        rows = self._matches()
        if self._order:
            column, desc = self._order
            rows = sorted(rows, key=lambda r: str(r.get(column) or ""), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._one:
            return SimpleNamespace(data=rows[0] if rows else None, count=len(rows))
        return SimpleNamespace(data=rows, count=len(rows))


class _Store:
    """Tables by name, plus the tables a test wants to fail on."""

    def __init__(self, tables=None, broken=()):
        self.tables = dict(tables or {})
        self.broken = set(broken)

    def table(self, name):
        return _Query(self, name)

    def rows(self, name):
        return self.tables.setdefault(name, [])

    def raise_if_broken(self, name):
        if name in self.broken or "*" in self.broken:
            raise RuntimeError(f"table {name} is unavailable")


def _exam(**over):
    row = {
        "id": EXAM_ID, "title": "Latihan Ujian", "subject": "Fisika",
        "school_id": SCHOOL, "class_ids": [CLASS], "teacher_id": "guru-1",
        "is_published": True, "status": "active",
        "duration_minutes": 60, "total_questions": 0,
        "question_types": {}, "answer_key": {}, "question_weights": {},
        "start_at": None, "end_at": None, "auto_submit_on_window_end": False,
        "pdf_page_urls": [], "require_seb": True, "seb_config_key": KEY,
    }
    row.update(over)
    return row


def _db(exam=None, **tables):
    base = {
        "exams": [exam if exam is not None else _exam()],
        "profiles": [{"id": PUPIL, "full_name": "Ahmad", "class_id": CLASS,
                      "school_id": SCHOOL, "role": "murid"}],
        "submissions": [],
        "seb_door_refusal": [],
    }
    base.update(tables)
    return _Store(base)


def _refusal(reason=door.REASON_NO_CLIENT, *, student_id=PUPIL, at=EARLY,
             name=None):
    row = {"id": f"r-{reason}-{at}", "school_id": SCHOOL, "exam_id": EXAM_ID,
           "student_id": student_id, "reason": reason, "user_agent": "Chrome/120",
           "created_at": at}
    if name is not None:
        row["profiles"] = {"full_name": name}
    return row


# ── the record ──────────────────────────────────────────────────────────────

class TestTheRecord:
    def test_a_refusal_is_written_with_the_exam_the_pupil_and_the_reason(self):
        db = _db()
        assert door.record(db, _exam(), student_id=PUPIL,
                           reason=door.REASON_NO_CLIENT, user_agent="Chrome") is True

        rows = db.rows("seb_door_refusal")
        assert len(rows) == 1
        row = rows[0]
        assert row["exam_id"] == EXAM_ID
        assert row["school_id"] == SCHOOL
        assert row["student_id"] == PUPIL
        assert row["reason"] == door.REASON_NO_CLIENT
        assert row["created_at"], "a refusal with no moment cannot answer 'when'"

    def test_a_write_that_fails_is_reported_and_never_raised(self, caplog):
        """Called on the path where a pupil is being turned away.

        A recording failure there must not become a second failure — the pupil still
        gets the page that tells them what to open, and the teacher still gets their
        panel. What is lost is one line of evidence, and the loss is *logged*.
        """
        db = _Store({"exams": [_exam()], "profiles": []}, broken={"seb_door_refusal"})
        with caplog.at_level("WARNING"):
            assert door.record(db, _exam(), student_id=PUPIL,
                               reason=door.REASON_NO_KEY) is False
        assert any(rec.name.endswith("seb_door_log") for rec in caplog.records), (
            "the failure was swallowed silently")

    def test_a_refusal_that_cannot_be_attributed_to_an_exam_is_not_written(self):
        """A row the reader cannot find is not a record — it is noise in a table."""
        db = _db()
        assert door.record(db, {}, student_id=PUPIL,
                           reason=door.REASON_NO_CLIENT) is False
        assert db.rows("seb_door_refusal") == []

    def test_the_moment_comes_from_the_server(self):
        """The pupil's clock is the one field a pupil could move."""
        db = _db()
        door.record(db, _exam(), student_id=PUPIL, reason=door.REASON_NO_CLIENT)
        written = db.rows("seb_door_refusal")[0]["created_at"]
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(written)
        assert parsed.tzinfo is not None, "a naive stamp reads as the reader's own day"
        assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 300


# ── the reader ──────────────────────────────────────────────────────────────

class TestTheReader:
    def test_it_reads_newest_first(self):
        db = _db(seb_door_refusal=[_refusal(at=EARLY), _refusal(at=LATER)])
        rows = door.refusals_for_exam(db, EXAM_ID)

        assert [row["at"] for row in rows] == [LATER, EARLY]

    def test_a_read_that_fails_is_an_empty_list(self, caplog):
        """A panel that 500s because its evidence is missing is worse than one that
        says it has none — and this is called with no request context from the
        exports, so the failure has to be logged without `current_app`."""
        db = _db(seb_door_refusal=[_refusal()])
        db.broken.add("seb_door_refusal")
        with caplog.at_level("WARNING"):
            assert door.refusals_for_exam(db, EXAM_ID) == []

    def test_the_row_carries_the_pupil_and_the_reason_pair(self):
        db = _db(seb_door_refusal=[_refusal(name="Ahmad")])
        row = door.refusals_for_exam(db, EXAM_ID)[0]

        assert row["name"] == "Ahmad"
        assert row["label"] == door.REASON_LABELS[door.REASON_NO_CLIENT]
        assert row["reason"] == door.REASON_NO_CLIENT

    def test_a_row_whose_profile_is_gone_still_reads(self):
        db = _db(seb_door_refusal=[_refusal(student_id=None)])
        row = door.refusals_for_exam(db, EXAM_ID)[0]

        assert row["name"] is None and row["student_id"] is None

    def test_an_unknown_reason_is_shown_rather_than_dropped(self):
        """Recording an event the report silently drops is how a log stops being
        evidence — so a reason this release does not know keeps its own word."""
        db = _db(seb_door_refusal=[_refusal(reason="something_new")])
        row = door.refusals_for_exam(db, EXAM_ID)[0]

        assert row["label"] == ("something_new", "something_new")
        assert door.refusal_summary([row])["refused_attempts"] == 1


# ── the two numbers ─────────────────────────────────────────────────────────

class TestTheSummary:
    def test_distinct_pupils_and_attempts_are_both_counted(self):
        """The question has two halves: five tries by one pupil is a mistyped link;
        one try each by five pupils is a file that reached nobody."""
        rows = [dict(door._as_row(_refusal(at=EARLY)), ),
                dict(door._as_row(_refusal(at=LATER))),
                dict(door._as_row(_refusal(student_id="stu-2", at=LATER)))]
        # newest first, as the reader returns them
        rows.sort(key=lambda r: r["at"], reverse=True)

        tally = door.refusal_summary(rows)

        assert tally["refused_pupils"] == 2
        assert tally["refused_attempts"] == 3
        assert tally["refused_last_at"] == LATER

    def test_nothing_to_show_makes_no_claim(self):
        assert door.refusal_summary([]) == {
            "refused_pupils": 0, "refused_attempts": 0, "refused_last_at": None}

    def test_a_row_whose_profile_is_gone_is_an_attempt_not_a_pupil(self):
        rows = [dict(door._as_row(_refusal(student_id=None)))]

        tally = door.refusal_summary(rows)

        assert tally["refused_attempts"] == 1
        assert tally["refused_pupils"] == 0, (
            "a deleted profile was counted as a pupil the school can name")

    def test_the_tally_is_taken_from_the_rows_the_list_shows(self):
        """One read, so the number and the table cannot disagree."""
        db = _db(seb_door_refusal=[_refusal(), _refusal(at=LATER)])
        rows = door.refusals_for_exam(db, EXAM_ID)
        assert door.refusal_summary(rows)["refused_attempts"] == len(rows) == 2


# ── the labels ──────────────────────────────────────────────────────────────

class TestTheLabels:
    @pytest.mark.parametrize("reason", list(door.REASONS))
    def test_every_reason_has_a_pair(self, reason):
        pair = door.REASON_LABELS.get(reason)
        assert pair, f"{reason} has no sentence — the panel would print its code"
        assert len(pair) == 2 and all(pair), pair

    def test_the_two_halves_are_different_words(self):
        for reason, (id_text, en_text) in door.REASON_LABELS.items():
            assert id_text != en_text, f"{reason} has the same string in both halves"

    def test_no_label_can_close_the_alpine_string_it_is_bound_in(self):
        """The pair is interpolated into `t('{{ id }}','{{ en }}')` in the markup —
        the pattern `principal/progress.html` uses for a pair the service owns. An
        apostrophe in either half would end that string early and break the panel for
        every reader, so the guard is on the label table, where it can still be
        fixed."""
        for reason, pair in door.REASON_LABELS.items():
            for half in pair:
                assert "'" not in half and '"' not in half, (
                    f"{reason}: a quote in {half!r} closes the Alpine string literal")

    def test_the_table_and_the_reason_list_are_the_same_set(self):
        """A reason with no sentence and a sentence with no reason are both ways this
        panel lies."""
        assert set(door.REASON_LABELS) >= set(door.REASONS)
        for reason in door.REASON_LABELS:
            assert reason and isinstance(reason, str)


# ── the handshake, driven for real ──────────────────────────────────────────

def _call(app, monkeypatch, db, view, *, method="POST", payload=None, uid=PUPIL,
          path=None, exam_id=EXAM_ID):
    """Run a real view body; return `(response, status)`.

    `.__wrapped__` skips `@login_required` (this harness sets `g` itself and the
    point is the route's own decisions), exactly as `test_seb_js_api.py` does it.
    """
    from flask import g
    from werkzeug.exceptions import HTTPException

    from app.routes import seb as sebmod

    monkeypatch.setattr(sebmod, "get_supabase", lambda: db)
    app.extensions["supabase"] = db
    kwargs = {"json": payload} if payload is not None else {}
    url = path or f"/student/exams/{exam_id}/seb-claim"
    with app.test_request_context(url, method=method, **kwargs):
        g.user_id, g.user_role = uid, "murid"
        g.user_name, g.user_email = "Ahmad", "a@sekolah.test"
        g.user_school_id, g.user_class_id, g.user_status = SCHOOL, CLASS, "active"
        g.tz_offset, g.show = 7, {}
        try:
            out = getattr(sebmod, view).__wrapped__(exam_id)
        except HTTPException as exc:
            return exc, exc.code
    if isinstance(out, tuple):
        return out[0], out[1]
    if hasattr(out, "status_code"):
        return out, out.status_code
    return out, 200


def _claim_value(app, *, key=KEY):
    from app.services import seb_config_key as ck
    from app.services import seb_service
    with app.test_request_context(f"/student/exams/{EXAM_ID}/seb-claim"):
        url = seb_service.claim_url(EXAM_ID)
    return ck.request_hash(url, key)


class TestTheHandshakeRecordsIt:
    def test_a_key_that_does_not_match_is_recorded(self, app, monkeypatch):
        db = _db()
        (resp, status) = _call(app, monkeypatch, db, "js_claim",
                               payload={"config_key": "0" * 64})

        assert status == 403
        rows = db.rows("seb_door_refusal")
        assert len(rows) == 1, "the refusal the server decides left no record"
        assert rows[0]["reason"] == door.REASON_KEY_MISMATCH
        assert rows[0]["student_id"] == PUPIL

    def test_a_matching_key_records_nothing(self, app, monkeypatch):
        db = _db()
        (resp, status) = _call(app, monkeypatch, db, "js_claim",
                               payload={"config_key": _claim_value(app)})

        assert status == 200
        assert db.rows("seb_door_refusal") == [], (
            "an admitted pupil was recorded as turned away")

    def test_the_page_reports_a_client_it_could_not_ask(self, app, monkeypatch):
        """The commonest refusal in this school: the link opened in Chrome.

        That client never posts a claim, so the server never sees the attempt unless
        the page says so — which is the whole reason the endpoint exists.
        """
        db = _db()
        (resp, status) = _call(
            app, monkeypatch, db, "refusal_report",
            payload={"reason": door.REASON_NO_CLIENT},
            path=f"/student/exams/{EXAM_ID}/seb-claim/refused")

        assert status == 200
        rows = db.rows("seb_door_refusal")
        assert len(rows) == 1
        assert rows[0]["reason"] == door.REASON_NO_CLIENT

    def test_the_key_that_was_not_ready_is_its_own_reason(self, app, monkeypatch):
        db = _db()
        _call(app, monkeypatch, db, "refusal_report",
              payload={"reason": door.REASON_NO_KEY},
              path=f"/student/exams/{EXAM_ID}/seb-claim/refused")

        assert db.rows("seb_door_refusal")[0]["reason"] == door.REASON_NO_KEY

    def test_an_unknown_reason_is_refused_and_not_recorded(self, app, monkeypatch):
        """The page sends one of two words; a probe sends whatever it likes, and a
        row a teacher cannot read is worse than a 400 to a script."""
        db = _db()
        (resp, status) = _call(app, monkeypatch, db, "refusal_report",
                               payload={"reason": "made_up"},
                               path=f"/student/exams/{EXAM_ID}/seb-claim/refused")

        assert status == 400
        assert db.rows("seb_door_refusal") == []

    def test_nobody_can_report_a_paper_that_is_not_gated(self, app, monkeypatch):
        db = _db(_exam(require_seb=False))
        (resp, status) = _call(app, monkeypatch, db, "refusal_report",
                               payload={"reason": door.REASON_NO_CLIENT},
                               path=f"/student/exams/{EXAM_ID}/seb-claim/refused")

        assert status == 404, "a report doubles as a way to ask whether a paper is gated"
        assert db.rows("seb_door_refusal") == []

    def test_a_pupil_not_on_the_paper_cannot_file_a_refusal(self, app, monkeypatch):
        db = _db(_exam(class_ids=[OTHER_CLASS]))
        (resp, status) = _call(app, monkeypatch, db, "refusal_report",
                               payload={"reason": door.REASON_NO_CLIENT},
                               path=f"/student/exams/{EXAM_ID}/seb-claim/refused")

        assert status == 403
        assert db.rows("seb_door_refusal") == []

    def test_the_failure_of_the_write_is_reported_but_not_believed_by_the_page(self):
        """`recorded`, not `ok` — the pupil's sentence does not depend on our disk,
        and telling the page the write failed would invite it to retry a thing a
        pupil cannot fix."""
        assert 'return jsonify({"ok": True, "recorded": recorded})' in SEB

    def test_the_page_is_the_only_witness_and_says_so(self):
        """Source rule: the two halves of the door the page can see are reported, and
        the half it cannot (a mismatched key) is *not* reported a second time — the
        claim route already recorded it, and two rows for one visit is a count that
        overstates how many pupils were turned away.

        "Once" is the assertion, not "at least once", and that distinction is the whole
        guard: the page is the only witness to these two reasons, so a reason it reports
        twice is one visit recorded as two, and the panel would tell a teacher that two
        pupils could not get in when one could not. Counting the *call sites* is what
        makes that visible — a second `report('no_key')` in `ask()` is the same defect
        as a `report(` added to the mismatch branch of `send()`, and a check for the
        call's mere presence sees neither.
        """
        code = CLAIM
        assert "refusalUrl" in code, "the page has no endpoint to report to"
        for reason in ("no_client", "no_key"):
            seen = code.count(f"report('{reason}')")
            assert seen == 1, (
                f"{reason} is reported {seen} times, so one locked-out visit leaves "
                f"{seen} rows in the teacher's count — the page is the only witness, "
                f"and it must say a thing once")
        # The mismatch path: `send()`'s else-branch must not call `report(`.
        # Anchored on the *definition* (`send(value) {`) because the first mention
        # of the name is the call inside `ask()`, which legitimately reports.
        send = code[code.index("send(value) {"):]
        assert "report(" not in send[:send.index("\n            },")], (
            "the mismatch path reports a second time — the claim route already did")

    def test_the_new_endpoint_exists_and_is_a_write(self, app):
        rules = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}
        path = "/student/exams/<exam_id>/seb-claim/refused"
        assert path in rules, "the page posts to an endpoint that does not exist"
        assert "GET" not in rules[path], "a refusal can be filed by a link or a prefetch"


# ── and it is never a penalty ───────────────────────────────────────────────

class TestItIsNotAPenalty:
    def test_no_reason_is_a_penalized_violation(self):
        penalized = {
            token.strip().strip('"') for token in
            re.search(r"PENALIZED_VIOLATION_TYPES = \(([^)]*)\)", ANTI)
            .group(1).replace("\n", " ").split(",") if token.strip()}

        assert penalized, "the ladder's own list could not be read, so this proves nothing"
        assert not (set(door.REASONS) & penalized), (
            "a door refusal is charged by the ladder — the whole point is that it is not")

    def test_the_module_never_writes_the_violation_log(self):
        source = (ROOT / "app" / "services" / "seb_door_log.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        assert "violation_logs" not in code, (
            "the refusal module reached for the penalty log — a refused pupil has sat "
            "nothing and must never be charged")
        assert door.TABLE == "seb_door_refusal", (
            "the record moved into a table the penalty path reads")

    def test_the_routes_do_not_charge_a_refusal(self):
        """The two refusal paths in `seb.py` write the record and nothing else: an
        anti-cheat write there would be a penalty by the back door."""
        body = SEB[SEB.index("def js_claim("):SEB.index("def _seb_response(")]
        assert "violation_logs" not in body, "the claim route writes the penalty log"
        assert "handleViolation" not in body
        assert "anti_cheat" not in body
        assert "seb_door_log.record(" in body, "the refusal is decided and not recorded"

    def test_the_two_surfaces_say_out_loud_that_it_is_not_a_penalty(self):
        panel = _code(PANEL)
        assert "tidak ada penalti" in panel and "no penalty" in panel, (
            "the panel shows a count of pupils without saying it costs them nothing")
        results = RESULTS
        assert "tidak ada penalti" in results and "no penalty" in results, (
            "the results chip reads as a penalty mark")

    def test_the_penalty_column_is_not_where_it_lives(self):
        """The results *header* carries it, not a row: a pupil turned away at the door
        has no submission, so no row of the list belongs to them."""
        assert "stats.refused_pupils" in RESULTS
        # `\b`, because `stats.refused_pupils` contains the letters `s.refused` and a
        # plain substring search would report the header chip as the defect it guards.
        assert not re.search(r"\bs\.refused|\bs\.door", _code(RESULTS)), (
            "the refusal was hung on a submission row, which a refused pupil has not")

    def test_the_reader_feeds_no_penalty_input(self):
        """`attach_refusals` writes three keys and none of them is read by the ladder."""
        stats = {}
        door.attach_refusals(_db(seb_door_refusal=[_refusal()]), EXAM_ID, stats)

        assert set(stats) == {"refused_pupils", "refused_attempts", "refused_last_at"}
        for banned in ("penalty", "violations", "final_score"):
            assert banned not in stats, f"{banned} is a penalty input written by a record"


# ── the panel ───────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _signed_in(app, path):
    """A request context carrying everything base.html reads for a teacher.

    A context manager, not a bare `push()`: an unpopped context leaks onto the rest
    of the pytest session, where "there is no app context" is another suite's premise.
    """
    from flask import g
    with app.test_request_context(path):
        g.user_id = "tea-1"
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        yield


def _render_panel(app, refusals, *, require_seb=True):
    with _signed_in(app, f"/teacher/exams/{EXAM_ID}/seb"):
        return app.jinja_env.get_template("teacher/seb_panel.html").render(
            exam=_exam(require_seb=require_seb),
            verdict={"ok": False, "role": None},
            credential=None, has_credential=False, file_is_current=False,
            can_manage=False, confirm_field="confirm_no_android",
            mobile_risk={"android": 0, "known": 0}, access_log=[],
            refusals=refusals, refusal_tally=door.refusal_summary(refusals))


def _render_results(app, stats, rows):
    with _signed_in(app, "/teacher/results?exam_id=exam-1"):
        return app.jinja_env.get_template("teacher/results.html").render(
            submissions=rows, stats=stats, exam_id=EXAM_ID, exams=[_exam()],
            exam=_exam(), scan_subs=[], online_subs=rows)


def _stats(**over):
    stats = {"avg": 78.0, "max": 80, "min": 76, "count": 2, "passed": 2,
             "pass_rate": 100, "threshold": 70, "late": 0,
             "refused_pupils": 0, "refused_attempts": 0, "refused_last_at": None}
    stats.update(over)
    return stats


class TestThePanel:
    def test_the_count_and_the_attempts_are_on_the_page(self, app):
        rows = door.refusals_for_exam(
            _db(seb_door_refusal=[_refusal(at=EARLY, name="Ahmad"),
                                 _refusal(at=LATER, name="Budi", student_id="stu-2")]),
            EXAM_ID)
        html = _render_panel(app, rows)

        assert "data-refusal-tally" in html
        assert "'2 ' + t('murid','pupils')" in html, "two pupils were reported as one"
        assert "'&middot; 2 ' + t('percobaan','attempts')" in html

    def test_the_last_moment_is_shown_in_the_school_clock(self, app):
        rows = door.refusals_for_exam(_db(seb_door_refusal=[_refusal(at=LATER)]), EXAM_ID)
        html = _render_panel(app, rows)

        assert "19 Sep 2026" in html, (
            "the panel shows a count without the 'when' the request asked for")

    def test_every_reason_gets_its_own_sentence(self, app):
        rows = door.refusals_for_exam(
            _db(seb_door_refusal=[_refusal(reason=door.REASON_NO_CLIENT),
                                 _refusal(reason=door.REASON_NO_KEY),
                                 _refusal(reason=door.REASON_KEY_MISMATCH)]), EXAM_ID)
        html = _render_panel(app, rows)

        assert len(rows) == len(door.REASONS), "not every reason was on the page to check"
        for reason, (id_text, en_text) in door.REASON_LABELS.items():
            if reason in door.REASONS:
                assert id_text in html and en_text in html, (
                    f"{reason} is listed without its own sentence")

    def test_each_row_binds_its_label_as_a_pair(self, app):
        """The pair has to be *in the markup* for the toggle to reach it."""
        rows = door.refusals_for_exam(
            _db(seb_door_refusal=[_refusal(reason=door.REASON_NO_KEY)]), EXAM_ID)
        html = _render_panel(app, rows)

        assert "t('SEB terbuka, tetapi kunci konfigurasinya belum siap'," in html, (
            "the reason is printed as a frozen string, so English mode shows Indonesian")

    def test_nobody_turned_away_is_stated_and_not_implied(self, app):
        html = _render_panel(app, [])

        assert "data-refusal-none" in html
        assert "Belum ada murid yang ditolak masuk ujian ini." in html
        assert "Nobody has been turned away from this exam yet." in html

    def test_the_card_is_absent_when_the_paper_was_never_gated(self, app):
        """There is nothing the card could say about a paper SEB was never on for."""
        html = _render_panel(app, [], require_seb=False)

        assert "data-seb-refusals" not in html

    def test_the_route_reads_the_log_once_and_tallies_those_very_rows(self):
        panel = SEB[SEB.index("def panel("):SEB.index("def enable(")]
        assert "refusals = seb_door_log.refusals_for_exam(" in panel
        assert "refusal_summary(refusals)" in panel, (
            "the tally is taken from a second read, so it can disagree with the table")

    def test_the_panel_shows_the_count_it_says_it_shows(self, app):
        """The number on the card is the number of *pupils*, not rows."""
        rows = door.refusals_for_exam(
            _db(seb_door_refusal=[_refusal(at=EARLY), _refusal(at=LATER)]), EXAM_ID)
        html = _render_panel(app, rows)

        assert "'1 ' + t('murid','pupils')" in html, (
            "two attempts by one pupil was reported as two pupils")
        assert "'&middot; 2 ' + t('percobaan','attempts')" in html


# ── the results header ──────────────────────────────────────────────────────

class TestTheResultsHeader:
    def test_the_count_is_shown_when_pupils_were_turned_away(self, app):
        html = _render_results(app, _stats(refused_pupils=3), [])

        assert "ditolak masuk" in html, "the results header does not surface the refusals"
        assert "'3 ' + t('ditolak masuk','turned away')" in html

    def test_nothing_is_claimed_when_nobody_was(self, app):
        html = _render_results(app, _stats(), [])

        assert "ditolak masuk" not in html, (
            "an exam nobody was refused from claims a refusal count")

    def test_the_chip_says_which_language_it_follows(self, app):
        """The results page declares `content_lang = 'id'`, so copy that *does* follow
        the toggle has to carry `:lang="lang"` on its own element or a screen reader
        announces English words as Indonesian."""
        html = _render_results(app, _stats(refused_pupils=1), [])
        titled_at = html.index(':title="t(\'Murid yang ditolak')
        tag = html[html.rindex("<span", 0, titled_at):html.index(">", html.rindex(
            "<span", 0, titled_at))]
        assert ':lang="lang"' in tag, "the chip follows the toggle without saying so"

    def test_the_route_attaches_the_tally_in_the_results_pass(self):
        body = TEACHER[TEACHER.index("def _exam_results("):]
        body = body[:body.index("\ndef _attach_leaving(")]
        assert "seb_door_log.attach_refusals(supabase, exam_id, stats)" in body, (
            "the results page never counts the refusals, so its chip can only show zero")


# ── the schema the reader depends on ────────────────────────────────────────

class TestTheSchema:
    def test_every_column_the_reader_selects_is_one_the_migration_creates(self):
        """A column left out of a select arrives *absent*, never as an error — and a
        column named that does not exist refuses the whole read. Both are silent in
        the direction that matters, so the select and the file are compared."""
        for column in ("id", "school_id", "exam_id", "student_id", "reason",
                       "user_agent", "created_at"):
            assert re.search(rf"^\s*{column}\s+[A-Z]", MIGRATION, re.M), (
                f"the reader selects {column}, which migration 064 does not create")
        assert set(door.ROW_COLUMNS.replace(" ", "").split(",")) == {
            "id", "school_id", "exam_id", "student_id", "reason", "user_agent",
            "created_at"}

    def test_the_embed_is_declared_by_a_foreign_key(self):
        """`profiles!left(full_name)` works only because `student_id` declares the
        relationship; without it PostgREST refuses the embed and the panel loses the
        names with a message nobody sees in a test that uses a fake client."""
        assert re.search(r"student_id UUID REFERENCES public\.profiles\(id\)", MIGRATION), (
            "the reader embeds profiles, but the migration declares no relationship")

    def test_the_table_is_not_wired_to_the_penalty_path(self):
        """The *statements*, not the comments — the file's own prose names the
        penalty path to explain why it is absent, and a guard that fires on that
        sentence is a guard that gets deleted."""
        ddl = "\n".join(line for line in MIGRATION.splitlines()
                        if not line.strip().startswith("--")).lower()
        assert "update_updated_at" not in ddl, "a trigger is not needed here"
        for banned in ("penalty", "violation", "lock_pending_resume", "score",
                       "trigger"):
            assert banned not in ddl, f"{banned} appears in the table's own statements"

    def test_the_migration_is_idempotent(self):
        for match in re.finditer(r"CREATE TABLE(?! IF NOT EXISTS)", MIGRATION):
            raise AssertionError("a CREATE TABLE without IF NOT EXISTS is not re-runnable")
        for match in re.finditer(r"CREATE INDEX(?! IF NOT EXISTS)", MIGRATION):
            raise AssertionError("a CREATE INDEX without IF NOT EXISTS is not re-runnable")
        assert MIGRATION.count("DROP POLICY IF EXISTS") >= MIGRATION.count("CREATE POLICY")

    def test_the_school_scoped_read_policy_names_its_caller(self):
        """A policy with no `TO` clause applies to PUBLIC, including `anon`."""
        policy = MIGRATION[MIGRATION.index("CREATE POLICY"):]
        assert "TO authenticated" in policy
        assert "school_id = public._user_school_id()" in policy
