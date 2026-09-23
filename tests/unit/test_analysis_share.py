"""The link a teacher can send, and the three refusals it has to make.

A share link is the one control in this app that deliberately hands a school's
paper to somebody with no account on the box, so the properties that make it safe
*are* the feature:

* **the token is the permission** — 256 bits from `secrets`, and every failure to
  resolve one is the same 404. Unknown, expired and revoked are distinguishable
  only to a scanner, and telling a stranger which one it was tells them a token
  existed;
* **what a stranger sees is decided in code, not in markup** — the answer key and
  every student name are dropped from the chart payload and from all three
  documents, and the page's key marker is one condition (`option.key and not
  public`) rather than four places inside one chip;
* **a link can be taken back** — revocation is immediate, counted, and applies to
  every live row an exam has, not only the newest.

The service is driven against a fake PostgREST rather than a live database,
because the states worth testing are the ones a database will not be in on
demand: a token that expired an hour ago, a table that was never migrated, an
insert that failed, a `revoked_at` nobody can parse.
"""
from __future__ import annotations

import contextlib
import gc
import io
import json
import re
import weakref
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import analysis_share as share

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = (ROOT / "app" / "routes" / "public.py").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
PAGE = (ROOT / "app" / "templates" / "teacher" / "analysis.html").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase" / "migrations"
             / "031_analysis_share_links.sql").read_text(encoding="utf-8")

EXAM_ID = "exam-1"
TEACHER_ID = "tea-1"
TOKEN = "tok-" + "a" * 40
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


# ── a fake PostgREST, for the two tables this feature touches ────────────────
#
# Faithful in the three ways that matter here: a table nobody migrated *raises*
# (the real failure the feature has to survive), `maybe_single()` answers with a
# response whose `.data` is None — or, in newer PostgREST, with None itself,
# which is the shape this app has a `row_or_none` helper for — and an insert can
# be made to fail, because a share button that 500s on a database hiccup is worse
# than one that says "try again".

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table, op="select", payload=None):
        self.store = store
        self.table = table
        self.op = op
        self.payload = payload
        self.filters = []
        self.nulls = []
        self.single = False
        self.order_by = None
        self.desc = False
        self.limit_n = None
        self.columns = None

    def select(self, columns="*", **_k):
        # The column list is honoured, not ignored. A fake that hands back the
        # whole row makes `select("id,revoked_at")` look equivalent to
        # `select(COLUMNS)` — and the difference is a real defect: a row read
        # without `expires_at` has no expiry, so an expired link reads as active
        # and gets counted as one that was stopped.
        asked = [] if str(columns).strip() == "*" else [
            name.strip() for name in str(columns).split(",")]
        if self.store.legacy and "student_id" in asked:
            # A real 42703, raised the way PostgREST raises it: naming a column the
            # table does not have refuses the *whole* request, so a caller's
            # `except` sees an empty answer rather than a missing field.
            raise RuntimeError('column analysis_share_links.student_id does not exist')
        self.columns = asked or None
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def is_(self, col, val):
        """PostgREST's `.is_`, which is how a scope of "the exam's own links" is
        asked for: `student_id IS NULL`. Only NULL matches, exactly as the database
        does — treating an empty string as NULL here would let a malformed row pass
        as the class link."""
        self.nulls.append((col, str(val).strip().lower()))
        return self

    def order(self, col, desc=False):
        self.order_by, self.desc = col, desc
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _project(self, rows):
        """Only the columns the query asked for, the way PostgREST answers."""
        if not self.columns:
            return rows
        return [{key: row.get(key) for key in self.columns} for row in rows]

    def _matched(self):
        rows = self.store.rows(self.table)
        for col, val in self.filters:
            rows = [row for row in rows if str(row.get(col)) == str(val)]
        for col, val in self.nulls:
            if val == "null":
                rows = [row for row in rows if row.get(col) is None]
            elif val == "not.null":
                rows = [row for row in rows if row.get(col) is not None]
            else:  # pragma: no cover - the app only asks for null / not.null
                rows = []
        if self.order_by:
            rows = sorted(rows, key=lambda row: str(row.get(self.order_by) or ""),
                          reverse=self.desc)
        return rows[: self.limit_n] if self.limit_n is not None else rows

    def execute(self):
        if self.op == "insert":
            return _Resp([self.store.insert_row(self.table, dict(self.payload))])
        if self.op == "update":
            self.store.update_row(self.table, self._matched(), dict(self.payload))
            return _Resp(None)
        rows = self._project(self._matched())
        if self.single:
            return None if self.store.none_response else _Resp(rows[0] if rows else None)
        return _Resp(rows)


class _Table:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def select(self, columns="*", **_k):
        # The column list travels to `_Query`, which is where it is honoured — the
        # question the service asks is always `table(...).select(...)`, so a table
        # that swallowed it would make every projection test blind to the one thing
        # it exists to catch: a query naming a column this schema does not have.
        return _Query(self.store, self.name).select(columns)

    def insert(self, payload):
        return _Query(self.store, self.name, "insert", payload)

    def update(self, payload):
        return _Query(self.store, self.name, "update", payload)


class _Store:
    """One exam's links, plus an exams table far enough to answer with nothing.

    An empty `exams` is a link to a paper somebody deleted, which is a real state
    and the one where counting a read would be a lie.
    """

    def __init__(self, links=(), exams=None, missing=False, fail_insert=False,
                 none_response=False, lost_insert=False,
                 update_faults=0, update_lost=False, legacy=False):
        self.links = [dict(row) for row in links]
        self.exams = list(exams or [])
        self.missing = missing
        #: The table *before* migration 033: no `student_id` column. The migration
        #: is pasted in by hand, so this is the state a release meets on the day it
        #: ships — and the one where a wrong read tells a teacher "nothing is
        #: shared" while the class link still opens the report.
        self.legacy = legacy
        self.fail_insert = fail_insert
        self.none_response = none_response
        self.lost_insert = lost_insert
        #: How many UPDATE calls answer with a dropped connection. `-1` means
        #: every one — which is what the live box did to a revocation.
        self.update_faults = update_faults
        #: True: the write lands and the answer is lost. False: nothing lands.
        self.update_lost = update_lost
        self.tables_touched = []

    def table(self, name):
        if name == "analysis_share_links" and self.missing:
            raise RuntimeError('relation "analysis_share_links" does not exist')
        if name not in ("analysis_share_links", "exams"):
            # Anything else is a query this feature has no business making.
            raise RuntimeError(f'relation "{name}" does not exist')
        self.tables_touched.append(name)
        return _Table(self, name)

    def rows(self, table):
        return self.links if table == "analysis_share_links" else self.exams

    def insert_row(self, table, payload):
        if table == "analysis_share_links" and (self.missing or self.fail_insert):
            raise RuntimeError("insert refused")
        if self.legacy and "student_id" in payload:
            raise RuntimeError('column "student_id" of relation '
                               '"analysis_share_links" does not exist')
        row = dict(payload)
        row.setdefault("id", f"link-{len(self.links) + 1}")
        row.setdefault("created_at", _iso(days=-1))
        row.setdefault("views", 0)
        self.rows(table).append(row)
        if self.lost_insert:
            # Written, then the connection died before the answer came back.
            raise RuntimeError("Server disconnected")
        return row

    def update_row(self, table, rows, payload):
        if self.missing:
            raise RuntimeError('relation "analysis_share_links" does not exist')
        # `update_faults == -1` is a connection that never answers; a positive
        # count is a connection that answers for the next N calls.
        faulting = self.update_faults != 0
        if faulting and self.update_faults > 0:
            self.update_faults -= 1
        if faulting and not self.update_lost:
            raise RuntimeError("Server disconnected")
        for row in rows:
            row.update(payload)
        if faulting and self.update_lost:
            raise RuntimeError("Server disconnected")


def _iso(**delta):
    """A timestamp relative to the clock the code under test actually reads.

    A hard-coded date is a test that starts failing in a month for a reason that
    has nothing to do with the change that broke it.
    """
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _row(token=TOKEN, **over):
    row = {"id": "link-1", "exam_id": EXAM_ID, "token": token,
           "created_by": TEACHER_ID, "created_at": _iso(days=-1),
           "expires_at": _iso(days=30), "revoked_at": None,
           "views": 0, "last_viewed_at": None}
    row.update(over)
    return row


# ── the token is the permission ──────────────────────────────────────────────

class TestTheTokenIsThePermission:
    def test_it_is_two_hundred_and_fifty_six_bits(self):
        """32 bytes. A link to a school's paper is not a place to save a byte."""
        assert share.TOKEN_BYTES == 32
        token = share.new_token()
        assert len(token) >= 43, token
        assert re.fullmatch(r"[A-Za-z0-9_-]+", token), \
            "a token with a character a URL has to escape is a token that mangles"

    def test_two_links_never_share_a_token(self):
        assert len({share.new_token() for _ in range(500)}) == 500

    def test_the_path_it_builds_is_the_route_that_serves_it(self, app):
        """The address a teacher copies and the address that resolves it are one
        string, written once — a hand-built URL in the template is how the two
        drift, and the drift is invisible until somebody's link 404s."""
        assert share.path("abc") == "/r/abc"
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert "/r/<token>" in rules, "the path this module builds is not a route"
        assert "/r/<token>/download.<ext>" in rules


# ── the three refusals are one answer ────────────────────────────────────────

class TestTheThreeRefusals:
    def test_an_unknown_token_resolves_to_nothing(self):
        assert share.resolve(_Store(), "nobody-knows-this") is None

    def test_an_expired_token_resolves_to_nothing(self):
        store = _Store([_row(expires_at="2026-03-09T00:00:00+00:00")])
        assert share.resolve(store, TOKEN) is None

    def test_a_revoked_token_resolves_to_nothing(self):
        store = _Store([_row(revoked_at="2026-03-05T00:00:00+00:00")])
        assert share.resolve(store, TOKEN) is None

    def test_a_revocation_nobody_can_parse_still_revokes(self):
        """The column is only ever written to stop a link, so a value this code
        cannot read is still a value somebody wrote to stop it. Reading it as
        "no revocation" hands the report back to whoever kept the URL."""
        assert share.resolve(_Store([_row(revoked_at="not-a-date")]), TOKEN) is None

    def test_an_expiry_nobody_can_parse_still_expires(self):
        assert share.resolve(_Store([_row(expires_at="whenever")]), TOKEN) is None

    def test_an_empty_token_never_reaches_the_database(self):
        """A scanner that sends nothing should not cost a query per attempt."""
        for empty in (None, "", "   "):
            store = _Store([_row()])
            assert share.resolve(store, empty) is None
            assert store.tables_touched == [], \
                "an empty token was sent to the database anyway"

    def test_a_token_with_whitespace_is_the_same_token(self):
        """The URL travels through a template, a chat client and a mail client;
        a trailing newline is one of them adding something."""
        store = _Store([_row()])
        assert share.resolve(store, f"  {TOKEN}\n")["token"] == TOKEN

    def test_a_response_with_no_row_at_all_is_not_a_crash(self):
        """PostgREST answers `maybe_single()` with None rather than an empty
        response in some versions, and this app has a helper file for exactly
        that shape — a resolver that assumes `.data` 500s on the live box."""
        store = _Store([_row()], none_response=True)
        assert share.resolve(store, TOKEN) is None

    def test_an_unmigrated_table_refuses_rather_than_breaks_the_page(self):
        """The migration is applied by hand. Until it is, the report it sits on
        keeps working — with no link offered, not with a 500."""
        store = _Store([_row()], missing=True)
        assert share.live(store, EXAM_ID) is None
        assert share.resolve(store, TOKEN) is None
        assert share.create(store, EXAM_ID, TEACHER_ID) is None
        assert share.revoke(store, EXAM_ID) == 0
        assert share.card(share.live(store, EXAM_ID)) is None

    def test_the_states_are_read_from_the_columns(self):
        assert share.state(_row(), now=NOW) == share.ACTIVE
        assert share.state(_row(revoked_at="2026-03-05T00:00:00Z"),
                           now=NOW) == share.REVOKED
        assert share.state(_row(expires_at="2026-03-09T00:00:00Z"),
                           now=NOW) == share.EXPIRED
        assert share.state(None, now=NOW) == share.REVOKED

    def test_a_timestamp_without_a_zone_is_read_as_utc_not_as_a_crash(self):
        """Comparing a naive datetime with an aware one raises, so a row that
        arrived without a zone would have been a 500 rather than a dead link."""
        assert share.state(_row(expires_at="2026-03-09T00:00:00"),
                           now=NOW) == share.EXPIRED
        assert share.state({"expires_at": NOW - timedelta(days=1)},
                           now=NOW) == share.EXPIRED
        assert share.state({"expires_at": NOW + timedelta(days=1)},
                           now=NOW) == share.ACTIVE

    def test_a_row_with_no_expiry_is_alive(self):
        assert share.state(_row(expires_at=None), now=NOW) == share.ACTIVE


# ── making a link ────────────────────────────────────────────────────────────

class TestMakingALink:
    def test_it_records_the_exam_the_author_and_a_date(self):
        created = share.create(_Store(), EXAM_ID, TEACHER_ID)
        assert created["exam_id"] == EXAM_ID
        assert created["created_by"] == TEACHER_ID
        assert created["token"] and created["token"] != TOKEN
        expires = share.moment(created["expires_at"])
        assert share.DEFAULT_DAYS - 1 <= (expires - datetime.now(timezone.utc)).days \
            <= share.DEFAULT_DAYS

    def test_pressing_the_button_twice_returns_the_same_link(self):
        """Two links to one report is two things to remember to revoke, and the
        second one is the one nobody remembers."""
        store = _Store()
        first = share.create(store, EXAM_ID, TEACHER_ID)
        second = share.create(store, EXAM_ID, TEACHER_ID)
        assert first["token"] == second["token"]
        assert len(store.links) == 1

    def test_a_link_that_never_expires_says_so_with_a_null(self):
        created = share.create(_Store(), EXAM_ID, TEACHER_ID, days=None)
        assert created["expires_at"] is None
        assert share.state(created) == share.ACTIVE

    def test_an_expired_link_is_replaced_rather_than_reused(self):
        """Sharing the exam again a term later must hand the teacher a *new*
        address: the old one is sitting in somebody's inbox."""
        store = _Store([_row(token="old", expires_at="2026-03-09T00:00:00+00:00")])
        created = share.create(store, EXAM_ID, TEACHER_ID)
        assert created["token"] != "old"
        assert share.resolve(store, "old") is None

    def test_a_refused_insert_is_reported_rather_than_raised(self):
        assert share.create(_Store(fail_insert=True), EXAM_ID, TEACHER_ID) is None

    def test_a_write_whose_answer_was_lost_still_hands_back_the_link(self):
        """The failure that happened live: the row was written and the *response*
        was dropped (`RemoteProtocolError: Server disconnected`). Reporting
        failure then tells a teacher "try again" while a live link already
        exists — and the retry they make is a second link nobody remembers.
        Retrying blind would be worse, so the recovery is a read."""
        store = _Store(lost_insert=True)
        created = share.create(store, EXAM_ID, TEACHER_ID)
        assert created is not None, "a written link was reported as a failure"
        assert len(store.links) == 1, "the recovery minted a second link"
        assert share.resolve(store, created["token"]) is not None

    def test_the_newest_live_link_is_the_one_the_page_shows(self):
        store = _Store([
            _row(id="old", token="old", created_at="2026-01-01T00:00:00+00:00"),
            _row(id="revoked", token="revoked",
                 created_at="2026-02-01T00:00:00+00:00",
                 revoked_at="2026-02-02T00:00:00+00:00"),
            _row(id="live", token="live", created_at="2026-03-01T00:00:00+00:00"),
        ])
        assert share.live(store, EXAM_ID)["token"] == "live"


# ── taking it back ───────────────────────────────────────────────────────────

class TestTakingItBack:
    def test_stopping_the_share_stops_every_link_the_exam_has(self):
        """An older row somebody still holds must not survive the click."""
        store = _Store([
            _row(id="a", token="a", created_at="2026-02-01T00:00:00+00:00"),
            _row(id="b", token="b", created_at="2026-03-01T00:00:00+00:00"),
        ])
        assert share.revoke(store, EXAM_ID) == 2
        assert share.resolve(store, "a") is None
        assert share.resolve(store, "b") is None
        assert share.live(store, EXAM_ID) is None

    def test_it_does_not_count_or_restamp_links_that_were_already_dead(self):
        store = _Store([
            _row(id="revoked", token="revoked",
                 revoked_at="2026-02-02T00:00:00+00:00"),
            _row(id="expired", token="expired",
                 expires_at="2026-02-03T00:00:00+00:00"),
            _row(id="live", token="live"),
        ])
        assert share.revoke(store, EXAM_ID) == 1
        stamped = {row["id"]: row["revoked_at"] for row in store.links}
        assert stamped["revoked"] == "2026-02-02T00:00:00+00:00", \
            "a revocation date was overwritten with a later one"
        assert stamped["expired"] is None, "an expired link was stamped as revoked"
        assert stamped["live"] is not None

    def test_a_dropped_answer_does_not_leave_the_link_live(self):
        """Measured on the live connection: the UPDATE's answer was dropped, so
        `revoke()` returned 0, the page said "Tidak ada tautan aktif untuk
        dihentikan", and the link went on serving the report. Setting a
        revocation is idempotent, so the stop is written again — and if the
        answer is still lost, the row is *read*, which is the only way to tell
        whether it landed."""
        store = _Store([_row()], update_faults=1, update_lost=False)
        assert share.revoke(store, EXAM_ID) == 1, \
            "one dropped answer was reported as nothing to stop"
        assert share.resolve(store, TOKEN) is None

    def test_a_write_that_landed_but_was_never_acknowledged_is_not_retried_blindly(
            self, ):
        """The other shape of the same failure: the row was written and the
        answer never arrived. The read says so, so the caller is told one link
        was stopped and the link is gone."""
        store = _Store([_row()], update_faults=-1, update_lost=True)
        assert share.revoke(store, EXAM_ID) == 1
        assert share.resolve(store, TOKEN) is None

    def test_a_stop_that_never_lands_is_reported_as_not_stopped(self):
        """The number has to stay honest: a link that could not be stopped is
        not counted as one that was, so the route can say "masih aktif" instead
        of telling the teacher sharing is off."""
        store = _Store([_row()], update_faults=-1, update_lost=False)
        assert share.revoke(store, EXAM_ID) == 0
        assert share.resolve(store, TOKEN) is not None, \
            "the fixture stopped the link after all, so this proves nothing"
        assert share.live(store, EXAM_ID) is not None, \
            "the route's error branch keys off this read"

    def test_an_exam_with_nothing_shared_stops_nothing(self):
        assert share.revoke(_Store(), EXAM_ID) == 0

    def test_stopping_is_scoped_to_one_exam(self):
        store = _Store([
            _row(id="mine", token="mine", exam_id=EXAM_ID),
            _row(id="theirs", token="theirs", exam_id="exam-2"),
        ])
        assert share.revoke(store, EXAM_ID) == 1
        assert share.resolve(store, "theirs") is not None


# ── the scope of a link ──────────────────────────────────────────────────────
#
# One table, one mechanism, and a nullable `student_id` for what the row is
# *about* (migration 033): NULL is the exam's report, a profile id is that one
# learner's. The failure this column exists to prevent is a mix-up — a learner's
# URL handed back as the class link, or a "stop sharing" click under a child's
# name that kills the class report four families are reading.

class TestTheScopeOfALink:
    def test_the_two_scopes_are_two_links(self):
        store = _Store([
            _row(id="class", token="class-token"),
            _row(id="one", token="one-token", student_id="stu-1"),
        ])
        assert share.live(store, EXAM_ID)["token"] == "class-token"
        assert share.live(store, EXAM_ID, "stu-1")["token"] == "one-token"
        assert share.live(store, EXAM_ID, "stu-2") is None

    def test_one_learners_rows_are_not_the_exams(self):
        """Every learner has their own link, and asking for one must not answer
        with another's — the page under it carries a child's name."""
        store = _Store([
            _row(id="one", token="one-token", student_id="stu-1"),
            _row(id="two", token="two-token", student_id="stu-2"),
        ])
        assert share.live(store, EXAM_ID, "stu-2")["token"] == "two-token"
        assert share.live(store, EXAM_ID) is None, \
            "a learner's link was handed back as the exam's link"

    def test_pressing_the_button_for_one_learner_does_not_move_the_class_link(self):
        store = _Store([_row(id="class", token="class-token")])
        created = share.create(store, EXAM_ID, TEACHER_ID, student_id="stu-1")
        assert created["student_id"] == "stu-1"
        assert created["token"] != "class-token"
        assert share.live(store, EXAM_ID)["token"] == "class-token"

    def test_one_learners_button_is_idempotent_too(self):
        store = _Store()
        first = share.create(store, EXAM_ID, TEACHER_ID, student_id="stu-1")
        second = share.create(store, EXAM_ID, TEACHER_ID, student_id="stu-1")
        assert first["token"] == second["token"]
        assert len(store.links) == 1

    def test_stopping_one_learner_leaves_the_class_and_the_others_alone(self):
        """The click is under one child's name. The class report and the three
        other families' links are not what that teacher meant."""
        store = _Store([
            _row(id="class", token="class-token"),
            _row(id="one", token="one-token", student_id="stu-1"),
            _row(id="two", token="two-token", student_id="stu-2"),
        ])
        assert share.revoke(store, EXAM_ID, "stu-1") == 1
        assert share.resolve(store, "one-token") is None
        assert share.resolve(store, "two-token") is not None
        assert share.resolve(store, "class-token") is not None, \
            "stopping one learner's link stopped the class report"

    def test_stopping_the_class_report_leaves_every_learner_alone(self):
        store = _Store([
            _row(id="class", token="class-token"),
            _row(id="one", token="one-token", student_id="stu-1"),
        ])
        assert share.revoke(store, EXAM_ID) == 1
        assert share.resolve(store, "class-token") is None
        assert share.resolve(store, "one-token") is not None, \
            "the class link's own revoke killed the individual links"

    def test_the_card_says_which_scope_the_link_opens(self):
        """Two links on one screen, one under a child's name: the token is the
        only other thing telling them apart."""
        assert share.card(_row())["student_id"] is None
        assert share.card(_row(student_id="stu-1"))["student_id"] == "stu-1"

    # ── the day before the migration is pasted in ─────────────────────────

    def test_a_class_link_still_works_before_033_is_applied(self):
        """The migration is applied by hand, and until it is the table has no
        `student_id`. Naming that column in a projection is a 42703, which every
        caller turns into "there is no link" — so the class share would stop
        minting, stop resolving and report "nothing to stop" while a live link
        went on serving the report."""
        store = _Store(legacy=True)
        created = share.create(store, EXAM_ID, TEACHER_ID)
        assert created is not None, "the class link could not be minted"
        assert share.live(store, EXAM_ID)["token"] == created["token"]
        assert share.resolve(store, created["token"]) is not None, \
            "an existing class link stopped resolving"
        assert share.revoke(store, EXAM_ID) == 1
        assert share.resolve(store, created["token"]) is None

    def test_a_learner_link_cannot_be_minted_before_033_is_applied(self):
        """Answers `None` rather than writing a row without the scope: a link that
        opens the *class* report under a child's name is worse than no link."""
        store = _Store(legacy=True)
        assert share.create(store, EXAM_ID, TEACHER_ID, student_id="stu-1") is None
        assert store.links == [], "a scope-less row was written for a learner"
        assert share.live(store, EXAM_ID, "stu-1") is None


# ── counting reads ───────────────────────────────────────────────────────────

class TestCountingReads:
    def test_an_open_is_counted_and_stamped(self):
        store = _Store([_row(views=3)])
        share.register_view(store, store.links[0])
        assert store.links[0]["views"] == 4
        assert share.moment(store.links[0]["last_viewed_at"]) is not None

    def test_a_read_of_a_link_with_no_count_yet_starts_at_one(self):
        store = _Store([_row(views=None)])
        share.register_view(store, store.links[0])
        assert store.links[0]["views"] == 1

    def test_a_failed_count_never_costs_the_reader_the_report(self):
        """Counting is bookkeeping; the report is the point. An update that
        raises must not turn a readable page into a 500."""
        store = _Store([_row()], missing=True)
        try:
            share.register_view(store, store.links[0])
        except Exception as error:  # pragma: no cover - the failure is the point
            pytest.fail(f"a failed count raised: {error!r}")


# ── the card the teacher reads ───────────────────────────────────────────────

class TestTheCardTheTeacherSees:
    def test_the_url_is_the_route_the_module_resolves(self):
        card = share.card(_row(), base_url="https://scangrade.web.id/")
        assert card["url"] == f"https://scangrade.web.id/r/{TOKEN}"
        assert share.path(TOKEN) in card["url"]

    def test_a_relative_url_is_produced_when_there_is_no_root(self):
        assert share.card(_row())["url"] == f"/r/{TOKEN}"

    def test_it_carries_what_the_card_shows(self):
        expiring = _iso(days=21)
        card = share.card(_row(views=7, expires_at=expiring))
        assert card["views"] == 7
        assert card["state"] == share.ACTIVE
        assert card["expires_at"] == share.moment(expiring), \
            "the date the card prints is not the date the resolver reads"
        assert card["created_at"].tzinfo is not None, \
            "a naive timestamp would print the wrong day to a teacher in UTC+7"

    def test_an_exam_with_no_link_has_no_card(self):
        assert share.card(None) is None
        assert share.card({}) is None


# ── the door a stranger comes through ────────────────────────────────────────
#
# The app has two front doors for a report — the sidebar for people with an
# account, and the share route for everybody else — and the share route is the
# only place that serves a school's data with no session at all. So it is tested
# through the real app: the routing, the limiter, and the 404 rather than a
# redirect to a login page the visitor cannot complete.

@pytest.fixture
def anonymous(app, monkeypatch):
    """A client for the share routes, with a fake database and nobody signed in."""
    from app.routes import public as public_module

    def _client(store):
        monkeypatch.setattr(public_module, "get_supabase", lambda: store)
        return app.test_client()

    return _client


class TestTheVisitorsDoor:
    def test_a_link_nobody_ever_made_is_a_404(self, anonymous):
        response = anonymous(_Store()).get("/r/nobody-knows-this")
        assert response.status_code == 404, \
            "an unknown token must not be a login page and must not be a 500"

    def test_a_revoked_link_is_the_same_404(self, anonymous):
        store = _Store([_row(revoked_at="2026-03-05T00:00:00+00:00")])
        assert anonymous(store).get(f"/r/{TOKEN}").status_code == 404

    def test_an_expired_link_is_the_same_404(self, anonymous):
        store = _Store([_row(expires_at="2026-03-09T00:00:00+00:00")])
        assert anonymous(store).get(f"/r/{TOKEN}").status_code == 404

    def test_a_link_to_an_exam_that_is_gone_is_not_counted_as_a_read(self, anonymous):
        """The count is what tells a teacher somebody looked. A link opened after
        the paper was deleted was opened by nobody, and saying otherwise sends
        the teacher looking for a reader who does not exist."""
        store = _Store([_row(views=0)], exams=[])
        assert anonymous(store).get(f"/r/{TOKEN}").status_code == 404
        assert store.links[0]["views"] == 0

    def test_the_download_extension_is_a_whitelist_not_a_format(self, anonymous):
        """A path built from a caller's text is how a download route becomes a
        file-read route, so three names are the three documents.

        The refusal has to happen *before* the token is looked up: an extension
        that falls through to a default document answers 404 here for the wrong
        reason (the token is fine, the exam is missing), which is why this also
        asserts that nothing was queried.
        """
        for hostile in ("exe", "py", "sql", "csv.gz", "pdf.exe"):
            store = _Store([_row()])
            response = anonymous(store).get(f"/r/{TOKEN}/download.{hostile}")
            assert response.status_code == 404, f"{hostile} was not refused"
            assert store.tables_touched == [], \
                f"{hostile} reached the database instead of being refused"

    def test_the_download_route_still_whitelists_its_extension(self):
        body = PUBLIC.split("def shared_analysis_file", 1)[1]
        assert 'if ext not in ("csv", "xlsx", "pdf")' in body, \
            "the download route no longer whitelists its extension"

    def test_the_module_keeps_every_limiter_a_view_may_be_decorated_with(self):
        """The whole class above answered 500 under the full suite, and this is why.

        Measured, not theorised: `ReferenceError: weakly-referenced object no longer
        exists`, raised inside flask-limiter's own wrapper. Its decorator stores only
        a *weak* proxy to its Limiter (`_limits.py: self.limiter = weakref.proxy(...)`),
        and `_rate_limit` applies that decorator at import time — so the limiter has
        to outlive every `create_app()`. Assigning a new one to the module global, as
        the app used to, is what floated the old one and collected the views with it.
        """
        from flask_limiter import Limiter

        from app.utils import rate_limiter as rl

        ambient, kept = rl.limiter, list(rl._limiters)
        try:
            first = Limiter(key_func=lambda: "test")
            dying = weakref.ref(first)
            assert rl.remember_limiter(first) is first, \
                "adopting a limiter has to return it, so the caller can go on"
            assert rl.limiter is first, "the module did not adopt it"

            # A second app, which is what a rebuilt app is: `limiter` moves on, and
            # every view decorated with `first` goes on holding a weak proxy to it.
            second = Limiter(key_func=lambda: "test")
            rl.remember_limiter(second)
            assert rl.limiter is second, "the fixture did not replace the limiter"

            del first
            gc.collect()
            assert dying() is not None, (
                "a limiter a decorated view holds was left collectable once a later "
                "app replaced it: flask-limiter keeps only weakref.proxy, so that "
                "view would answer ReferenceError instead of serving")
        finally:
            rl.limiter = ambient
            rl._limiters[:] = kept

    def test_the_app_adopts_its_limiter_instead_of_just_replacing_it(self):
        """The wiring half, read from the source rather than from a second app.

        Building another app here would start another pair of background schedulers
        for the sake of one assertion, and the question is only whether
        `create_app` goes through the call that keeps the limiter alive.
        """
        source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")

        assert "rl_module.remember_limiter(limiter)" in source, \
            "create_app replaced the limiter instead of adopting it"
        assert "rl_module.limiter = limiter" not in source, \
            "a bare assignment is what left the decorated views pointing at a "\
            "collected object"

    def test_a_stranger_cannot_make_a_link(self, anonymous):
        """Whoever may read the analysis may share it — that is the permission —
        but there has to *be* somebody. This refusal comes from two guards, and
        the source check below is what keeps both of them there: a POST is
        rejected by CSRF before any view runs, so a test that only watched the
        status code would stay green with no auth decorator at all."""
        store = _Store()
        response = anonymous(store).post(f"/teacher/analysis/{EXAM_ID}/share")
        assert response.status_code in (302, 403), response.status_code
        assert store.links == [], "an anonymous POST minted a link"

    def test_the_two_share_buttons_are_guarded_like_the_report_itself(self):
        for name in ("def share_exam_analysis", "def revoke_exam_analysis"):
            head, block = TEACHER.split(name, 1)[0], TEACHER.split(name, 1)[1]
            decorators = head.rsplit("@teacher_bp.route", 1)[1]
            block = block.split("\n@teacher_bp.route", 1)[0]
            assert "@teacher_or_admin_required" in decorators, f"{name} lost its guard"
            assert "_guard_exam(" in block, (
                f"{name} does not check that this teacher may open this exam, so "
                "any signed-in teacher could share somebody else's paper")
            assert 'log_activity("' in block, f"{name} leaves no audit trail"
            assert "/teacher/analysis/" in block, f"{name} does not come back to the page"

    def test_the_shortcut_that_would_skip_the_permission_check_is_not_used(self):
        """`_guard_exam` is the one door; talking to the database directly here
        is how a route ends up checking less than the page it belongs to."""
        block = TEACHER.split("def share_exam_analysis", 1)[1].split(
            "\n@teacher_bp.route", 1)[0]
        assert 'table("exams")' not in block, \
            "the share route queries the exam itself instead of the shared guard"

    def test_the_public_route_is_the_one_route_with_no_guard(self):
        """By design and by necessity — and the reason it is worth asserting is
        that adding a guard here would look like a fix while making every link
        somebody was sent useless."""
        segment = PUBLIC.split('@public_bp.route("/r/<token>")', 1)[1].split(
            "def shared_analysis", 1)[0]
        assert "@_rate_limit" in segment, "a public report is not rate limited"
        for guard in ("@teacher_or_admin_required", "@login_required",
                      "role_required", "session_required"):
            assert guard not in segment, f"the share route grew a {guard} guard"

    def test_the_shared_document_does_not_name_the_teacher(self):
        """A shared report about a paper is checkable against the school. The
        person who shared it is not the point, and their name in an address that
        can be forwarded anywhere is a detail nobody agreed to."""
        assert 'teacher=""' in PUBLIC.split("def shared_analysis_file", 1)[1], \
            "the shared document names a teacher"


# ── what a stranger may see ──────────────────────────────────────────────────

EXAM = {
    "id": EXAM_ID, "title": "Mid Semester 1", "subject": "Fisika",
    "total_questions": 4,
    "question_types": {"0": "mcq", "1": "mcq", "2": "true_false",
                       "3": "essay_canvas"},
    "question_weights": {"0": 25.0, "1": 25.0, "2": 25.0, "3": 25.0},
    "answer_key": {"0": "A", "1": "C", "2": "true"},
}

STUDENT_NAMES = [f"Murid {index:02d}" for index in range(10)]


def _submissions():
    rows = []
    for index, name in enumerate(STUDENT_NAMES):
        strong = index < 5
        rows.append({
            "student_name": name,
            "answers": {"0": "A" if strong else "B", "1": "C" if strong else "A",
                        "2": "true" if strong else "false",
                        "3": {"text": "jawaban"}},
            "teacher_feedback": {"scores": {"3": 80 if strong else 40}},
            "final_score": 88 if strong else 42,
        })
    return rows


@pytest.fixture(scope="module")
def analysis():
    from app.services import item_analysis
    return item_analysis.analyse(EXAM, _submissions())


@pytest.fixture(scope="module")
def teacher_payload(analysis):
    from app.routes.teacher import _chart_payload
    return _chart_payload(analysis, EXAM)


@pytest.fixture(scope="module")
def public_payload(analysis):
    from app.routes.teacher import _chart_payload
    return _chart_payload(analysis, EXAM, public=True)


class TestThePayloadAStrangerReceives:
    def test_no_student_name_reaches_the_browser(self, teacher_payload,
                                                 public_payload):
        """The payload is what the page's own script holds, so a name here is a
        name forwarded to a stranger even while nothing draws it."""
        assert teacher_payload["people"], "the teacher's copy is the one with people"
        assert public_payload["people"] == []
        assert all(name not in json.dumps(public_payload) for name in STUDENT_NAMES)

    def test_the_payload_is_the_same_payload_otherwise(self, teacher_payload,
                                                      public_payload):
        """Same calibration, same numbers — a redacted report that disagrees with
        the teacher's is worse than no report."""
        for key in ("summary", "items", "logits", "bins", "separation", "meta"):
            assert key in public_payload, f"a shared payload lost {key}"
        assert public_payload["items"] == teacher_payload["items"]
        assert public_payload["logits"] == teacher_payload["logits"]
        assert public_payload["summary"] == teacher_payload["summary"]

    def test_the_payload_never_carries_the_answer_key(self, public_payload):
        """Read as text rather than key by key: a key added under a new name is
        exactly the mistake this is here to catch."""
        blob = json.dumps(public_payload)
        assert "answer_key" not in blob
        # The framework vocabulary legitimately carries `key` fields — a framework,
        # a band and a level are all named by one — so the check is on the marks a
        # question's answer would be recognised *by*: the option letter a key is,
        # and any per-option flag saying which one is right.
        assert '"correct"' not in blob and '"is_key"' not in blob
        for item in public_payload["items"]:
            assert set(item) & {"key", "answer", "options", "distractors"} == set(), \
                "a shared item carries something the key could be read from"

    def test_the_default_is_the_teachers_own_report(self, teacher_payload):
        """The redaction has to be asked for by name. A default of `public=True`
        would quietly empty the teacher's own page — so the parameter is a
        decision at each call site, and these two tests are its two directions."""
        assert teacher_payload["people"]
        assert PUBLIC.count("public=True") >= 4, \
            "the share route stopped asking for the redacted copy"


class TestTheDocumentsAStrangerDownloads:
    def test_the_csv_is_the_teachers_file_minus_the_people(self, analysis):
        """Compared section by section, because "the students section is gone"
        and "everything else survived" are two claims and the defect this guards
        against is the redaction taking a section of *statistics* with it — a
        shared report that quietly lost its split table is a report somebody
        quotes as complete."""
        from app.services import analysis_report

        def sections(text):
            """The CSV's blocks, keyed by their title line.

            Blank lines separate them and a block is [title, header, rows…],
            which is how the file is read by eye as well as by a spreadsheet.
            """
            blocks, current = {}, None
            for line in text.splitlines():
                if not line.strip():
                    current = None
                    continue
                if current is None:
                    current = line
                    blocks[current] = []
                else:
                    blocks[current].append(line)
            return blocks

        teacher = sections(analysis_report.analysis_csv(analysis, EXAM, lang="en"))
        public = sections(analysis_report.analysis_csv(
            analysis, EXAM, lang="en", public=True))

        # The file says what it is. Read before the comparison below, which wants
        # the two files to be otherwise identical line for line.
        notice = [line for rows in public.values() for line in rows
                  if line.startswith("Shared copy")]
        assert notice, "a downloaded file does not say it is the redacted copy"
        for title in public:
            public[title] = [line for line in public[title]
                             if not line.startswith("Shared copy")]

        people = [title for title, rows in teacher.items()
                  if any(name in ",".join(rows) for name in STUDENT_NAMES)]
        assert people, "the fixture has no section of student names"
        statistics = set(teacher) - set(people)
        assert statistics == set(public), (
            "the shared CSV is not the teacher's file with the people section"
            f" removed: it lost {sorted(statistics - set(public))} and gained"
            f" {sorted(set(public) - statistics)}"
        )
        for title in statistics:
            if title == "Option distribution":
                # The other intended difference, and the one that needs its own
                # test: the Key column is the answer key in spreadsheet form.
                continue
            assert teacher[title] == public[title], \
                f"the shared CSV changed the {title!r} section"

    def test_the_csv_does_not_hand_over_the_answer_key(self, analysis):
        """The `key` column is the answer key in spreadsheet form — the one
        column that turns a shared report into a shared marking scheme. Read from
        the rows under the header rather than by searching for a word, so a
        column that changes its label is still checked."""
        from app.services import analysis_report

        def option_rows(public):
            lines = analysis_report.analysis_csv(
                analysis, EXAM, lang="en", public=public).splitlines()
            start = lines.index("No,Option,Chosen,Key") + 1
            rows = []
            for line in lines[start:]:
                if not line[:1].isdigit():
                    break
                rows.append(line)
            return rows

        teacher_rows, public_rows = option_rows(False), option_rows(True)
        assert public_rows, "no option rows were read, so this proves nothing"
        assert any(row.endswith(",yes") for row in teacher_rows), \
            "the fixture keys nothing, so the redaction is untested"
        assert all(row.endswith(",") for row in public_rows), \
            "a shared CSV names the option the question is keyed to"

    def test_the_workbook_keeps_its_charts_and_drops_the_people_sheet(self, analysis):
        from app.services import analysis_report

        def read(blob):
            """Sheet names from the manifest, every cell from the sheets.

            Cells are read as the file's own XML rather than through `openpyxl`,
            because the claim is about the *file* a school downloads: a reader
            that parses the workbook would still hand back the sheet's data if the
            bytes had been written somewhere no version of Excel opens.
            """
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                manifest = archive.read("xl/workbook.xml").decode("utf-8", "ignore")
                sheets = re.findall(r'<sheet[^>]*\bname="([^"]+)"', manifest)
                cells = "".join(
                    archive.read(name).decode("utf-8", "ignore")
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/") and name.endswith(".xml"))
            return sheets, cells

        people_sheet = analysis_report._sheet("people", "en")
        teacher_sheets, teacher_shared = read(
            analysis_report.analysis_xlsx(analysis, EXAM, lang="en"))
        public_sheets, public_shared = read(
            analysis_report.analysis_xlsx(analysis, EXAM, lang="en", public=True))
        assert people_sheet in teacher_sheets, \
            "the fixture produces no people sheet, so this proves nothing"
        assert people_sheet not in public_sheets, \
            "the shared workbook still has a sheet of student names"
        assert all(name not in public_shared for name in STUDENT_NAMES)
        assert len(public_sheets) == len(teacher_sheets) - 1

    def test_the_pdf_keeps_its_charts_and_drops_the_people_table(self, analysis):
        """Read with a PDF reader rather than grepped, because the people table
        is a flowable: "the code does not append it" and "it is not on the page"
        are two claims and only the second one is the feature."""
        import fitz

        from app.services import analysis_report

        teacher = analysis_report.analysis_pdf(analysis, EXAM, school="SMA Negeri 1")
        public = analysis_report.analysis_pdf(analysis, EXAM, school="SMA Negeri 1",
                                              public=True)

        def read(blob):
            with fitz.open(stream=blob, filetype="pdf") as document:
                text = " ".join(page.get_text() for page in document)
                drawings = sum(len(page.get_drawings()) for page in document)
            return text, drawings

        teacher_text, _ = read(teacher)
        public_text, drawings = read(public)
        assert any(name in teacher_text for name in STUDENT_NAMES), \
            "the fixture produces no people table, so this proves nothing"
        assert not any(name in public_text for name in STUDENT_NAMES)
        assert "SMA Negeri 1" in public_text, \
            "a shared report without the school is a report nobody can check"
        assert drawings > 20, f"the shared PDF has {drawings} drawings — no charts"


class TestThePageAStrangerOpens:
    """The rendered page, not the template text: the key marker, the banner and
    the absent share control are facts about the HTML, and only the HTML has
    them."""

    @contextlib.contextmanager
    def _as_teacher(self, app, path=f"/teacher/analysis/{EXAM_ID}"):
        from flask import g
        with app.test_request_context(path):
            g.user_id = TEACHER_ID
            g.user_name = "Guru Uji"
            g.user_email = "guru@example.test"
            g.user_role = "guru"
            g.tz_offset = 7
            g.show = {}
            yield

    def _render(self, app, chart, signed_in=False, **extra):
        from app.services import analysis_frameworks as af

        render = app.jinja_env.get_template("teacher/analysis.html").render
        # The route always hands the page the resolved framework — it is what
        # decides which panels render — so the fixture does too.
        extra.setdefault("framework", af.resolve(None))
        if signed_in:
            with self._as_teacher(app):
                return render(exam=EXAM, chart=chart,
                              download_base=f"/teacher/analysis/{EXAM_ID}", **extra)
        with app.test_request_context("/r/" + TOKEN):
            return render(exam=EXAM, chart=chart,
                          download_base=f"/r/{TOKEN}", **extra)

    def test_it_says_what_it_is_and_what_it_withholds(self, app, analysis,
                                                      public_payload):
        html = self._render(app, public_payload, analysis=analysis)
        assert "A shared report" in html
        assert "The answer key and student names are not included" in html
        assert "{" + "{" not in html and "{%" not in html

    def test_no_key_marker_is_rendered_in_the_option_panel(
            self, app, analysis, public_payload, teacher_payload):
        """The green chip and the key icon *are* the answer key, drawn as a
        colour. The page decides it once — `option.key and not public` — so this
        asserts the rendered fact in both directions: a test that only looked at
        the shared copy would stay green after the marker was deleted altogether,
        which is the failure mode that matters here."""
        shared = self._render(app, public_payload, analysis=analysis)
        teacher = self._render(app, teacher_payload, analysis=analysis,
                              signed_in=True)
        assert "fa-key text-[9px]" in teacher, \
            "the teacher's page lost its key marker, so this test is blind"
        assert "fa-key text-[9px]" not in shared, \
            "the shared page marks the option the paper is keyed to"

    def test_a_signed_in_readers_cookie_does_not_upgrade_the_copy(
            self, app, analysis, public_payload):
        """The leak this page had, and the reason the flag is the route's: which
        *chrome* wraps a report depends on the session, and which *copy* it is
        must not. Reading the same question meant a teacher who was logged in
        and clicked somebody's share link rendered the teacher's copy of a
        shared report — green key chips and all — because they had a cookie, on
        an exam that need not be theirs."""
        html = self._render(app, public_payload, analysis=analysis,
                            signed_in=True, public_view=True)
        assert "fa-key text-[9px]" not in html, \
            "a session turned a shared report into the teacher's copy"
        assert "A shared report" in html, "the reader is not told what this is"
        assert "data-share-card" not in html
        assert "Student ability" not in html

    def test_the_teacher_without_the_flag_still_gets_the_teachers_copy(
            self, app, analysis, teacher_payload):
        """The other direction, so the flag cannot simply be ignored: the
        teacher's own route asks for their copy and gets it."""
        html = self._render(app, teacher_payload, analysis=analysis, signed_in=True)
        assert "fa-key text-[9px]" in html
        assert "A shared report" not in html

    def test_the_route_tells_the_template_which_copy_to_render(self):
        """The flag is passed by the public route and read by the block it
        renders — a source check, because the failure mode is a flag nobody
        wired, which renders perfectly and leaks."""
        assert "public_view=True" in PUBLIC.split("def shared_analysis(", 1)[1]
        assert "body(public_view|default(" in PAGE, \
            "the rendered copy is decided by the session again"

    def test_the_share_control_is_not_on_a_shared_page(self, app, analysis,
                                                       public_payload):
        """A stranger cannot revoke the link they were handed, and a button they
        cannot use is a button that invites a support ticket.

        Asserted against the markup, not the page's text: the page's own script
        mentions `[data-share-url]` in `copyShareLink()`, and a reader that greps
        the script would call the control present on a page that has none."""
        html = self._render(app, public_payload, analysis=analysis)
        for pattern in (r"data-share-card", r"<input[^>]*data-share-url",
                        r'<form[^>]*action="[^"]*/share'):
            assert re.search(pattern, html) is None, \
                f"the shared page offers {pattern}"

    def test_the_teacher_is_offered_the_control_and_the_url(self, app, analysis,
                                                            teacher_payload):
        """The control has to be there before anybody can send a link, and the
        URL is the server's own — the page copies the field rather than
        rebuilding the address in JavaScript, where a second copy could drift."""
        link = share.card(_row(), base_url="https://scangrade.web.id/")
        html = self._render(app, teacher_payload, analysis=analysis,
                            signed_in=True, share=link)
        assert "data-share-card" in html
        assert f'value="https://scangrade.web.id/r/{TOKEN}"' in html
        assert "copyShareLink()" in html
        assert "/teacher/analysis/exam-1/share/revoke" in html

    def test_an_unshared_exam_is_offered_the_button_and_no_url(self, app, analysis,
                                                              teacher_payload):
        html = self._render(app, teacher_payload, analysis=analysis,
                            signed_in=True, share=None)
        assert "Create a public link" in html
        assert "Not shared yet" in html
        assert re.search(r"<input[^>]*data-share-url", html) is None

    def test_the_form_actions_are_routes_that_exist(self, app):
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert "/teacher/analysis/<exam_id>/share" in rules
        assert "/teacher/analysis/<exam_id>/share/revoke" in rules


# ── the database half of the contract ────────────────────────────────────────

class TestTheTableTheLinksLiveIn:
    def test_the_migration_matches_the_columns_the_service_reads(self):
        assert "CREATE TABLE IF NOT EXISTS analysis_share_links (" in MIGRATION
        for column in ("exam_id", "token", "created_by", "created_at",
                       "expires_at", "revoked_at", "views", "last_viewed_at"):
            assert column in share.COLUMNS, f"{column} is in the table but not read"
            assert re.search(rf"^\s*{column}\s", MIGRATION, re.M), \
                f"{column} is read but not in the table"

    def test_the_token_is_unique_in_the_database_too(self):
        """The service never checks for a collision — one in 2^256 is not worth a
        round-trip — so the uniqueness the code relies on is the schema's."""
        assert "token           TEXT NOT NULL UNIQUE" in MIGRATION

    def test_only_the_service_key_may_read_it(self):
        """This row *is* the access, so a policy open to a session or to `anon`
        hands out every shared report on the box."""
        assert "ENABLE ROW LEVEL SECURITY" in MIGRATION
        assert "auth.role() = 'service_role'" in MIGRATION
        assert "FOR ALL" in MIGRATION, "a policy that covers only SELECT leaks the rest"
        assert "TO public" not in MIGRATION and "TO anon" not in MIGRATION

    def test_deleting_an_exam_takes_its_links_with_it(self):
        """Otherwise the row that grants access outlives the report it points at
        and the resolver finds a live link to nothing."""
        assert "REFERENCES exams(id) ON DELETE CASCADE" in MIGRATION

    def test_the_row_is_reachable_by_the_token_the_resolver_uses(self):
        assert "idx_analysis_share_token" in MIGRATION
        assert "idx_analysis_share_exam" in MIGRATION

    def test_it_can_be_run_twice(self):
        """Migrations here are applied by hand, and a half-applied one gets
        pasted again."""
        assert "IF NOT EXISTS" in MIGRATION
        assert "DROP POLICY IF EXISTS" in MIGRATION


class TestTheCopyOnThePage:
    def test_every_share_sentence_is_a_pair(self):
        """The EN/ID button translates what is in the catalogue and only what is
        in it, so a sentence written straight into the markup ignores the toggle.
        Read from the rendered page's own region rather than the whole file: the
        share card is the part that did not exist before."""
        block = PAGE.split("data-share-card", 1)[1].split(
            "{% if not analysis.items", 1)[0]
        pairs = re.findall(r"t\(\s*'([^']*)'\s*,\s*'([^']*)'", block)
        assert len(pairs) >= 5, "the share card stopped using the catalogue"
        assert all(indonesian.strip() and english.strip()
                   for indonesian, english in pairs)
        for phrase in ("Bagikan laporan ini", "Salin tautan", "Create a public link",
                       "Hentikan berbagi", "Belum dibagikan"):
            assert phrase in block, f"the share card lost {phrase!r}"

    def test_the_page_says_the_link_is_redacted_before_it_is_sent(self):
        """A teacher about to paste a link into a parents' group is told what the
        link shows, at the moment they are looking at the button."""
        block = PAGE.split("data-share-card", 1)[1]
        assert "no answer key, no student names" in block


# ── the filed report page, and the handler every card depends on ─────────────

REPORT_PAGE = (ROOT / "app" / "templates" / "teacher" / "analysis_report.html"
               ).read_text(encoding="utf-8")
STUDENT_PAGE = (ROOT / "app" / "templates" / "teacher" / "analysis_student.html"
                ).read_text(encoding="utf-8")
BASE_PAGE = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
PUBLIC_PY = (ROOT / "app" / "routes" / "public.py").read_text(encoding="utf-8")
TEACHER_PY = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")


@contextlib.contextmanager
def as_teacher(app, path):
    from flask import g
    with app.test_request_context(path):
        g.user_id = TEACHER_ID
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        yield


class TestTheFiledReportOffersTheLink:
    """The report page is the document a teacher hands over, and it had no way to
    share itself — the control was one page up, on the item analysis. "There is no
    shareable link here" was the honest reading of the page, and it was reported as
    exactly that."""

    def _render(self, app, analysis, share=None):
        from app.services import exam_report

        with as_teacher(app, f"/teacher/analysis/{EXAM_ID}/report"):
            return app.jinja_env.get_template(
                "teacher/analysis_report.html").render(
                    exam=EXAM, analysis=analysis,
                    report=exam_report.report(analysis, EXAM),
                    share=share, download_base=f"/teacher/analysis/{EXAM_ID}",
                    lang="id")

    def test_a_shared_exam_shows_the_url_and_the_copy_button(self, app, analysis):
        link = share.card(_row(), base_url="https://scangrade.web.id/")
        html = self._render(app, analysis, share=link)

        assert "data-share-card" in html
        assert f'value="https://scangrade.web.id/r/{TOKEN}"' in html
        assert "copyShareLink()" in html
        assert f"/teacher/analysis/{EXAM_ID}/share/revoke" in html

    def test_an_unshared_exam_is_offered_the_button_and_no_url(self, app, analysis):
        html = self._render(app, analysis, share=None)

        assert "Create a public link" in html
        assert "Not shared yet" in html
        assert re.search(r"<input[^>]*data-share-url", html) is None

    def test_the_card_is_not_printed_into_the_filed_copy(self, app, analysis):
        """This page is the printable document. A live token printed into a copy a
        school files is the link handed out without anybody deciding to."""
        block = REPORT_PAGE.split("data-share-card", 1)[0].rsplit("<div", 1)[1]
        assert "no-print" in block, "the share card would print with the report"

    def test_the_report_route_hands_the_page_its_link(self):
        """A card with no `share` renders the "not shared yet" state for an exam
        that *is* shared — the control would be lying, and quietly: the URL is the
        one part of the card that cannot be guessed."""
        call = TEACHER_PY.split('"teacher/analysis_report.html"', 1)[1][:600]
        assert "share=_share_card(" in call

    def test_the_report_page_is_teacher_only(self):
        """Which is what lets the card sit there unguarded. The page a share link
        opens is `analysis.html`; if that ever changed, this card would be rendered
        for a stranger."""
        assert "analysis_report.html" not in PUBLIC_PY
        assert "analysis_report.html" in TEACHER_PY


class TestTheCopyHandlerIsReachable:
    """A handler Alpine cannot resolve is a click that does nothing, and a click
    that does nothing looks exactly like a click that worked. The learner report's
    Copy button was dead for exactly that reason: it called a method only the
    analysis page defined."""

    def _templates(self):
        return sorted((ROOT / "app" / "templates").rglob("*.html"))

    def test_one_definition_serves_every_card(self):
        definers = [path.name for path in self._templates()
                    if "copyShareLink(" in path.read_text(encoding="utf-8")
                    and "async copyShareLink" in path.read_text(encoding="utf-8")]
        assert definers == ["base.html"], (
            f"copyShareLink is defined in {definers}; one definition is what keeps "
            "a page from drawing the button without the method behind it")

    def test_every_page_that_draws_the_button_inherits_it(self):
        for path in self._templates():
            text = path.read_text(encoding="utf-8")
            if "copyShareLink()" not in text:
                continue
            if "async copyShareLink" in text:
                continue            # the definition itself, not a call to it
            assert '{% extends "base.html" %}' in text, (
                f"{path.name} draws the copy button but does not extend base.html, "
                "so the method it calls is not in scope")

    def test_no_page_names_a_note_that_does_not_exist(self):
        """`linkNote` was referenced by two pages that never declared it, next to a
        button that called a method they did not have either."""
        offenders = [path.name for path in self._templates()
                     if "linkNote" in path.read_text(encoding="utf-8")]
        assert offenders == [], (
            f"{offenders} read a note that no scope declares; the note is "
            "`shareNote`, in base.html, beside the method that writes it")

    def test_the_base_scope_declares_both_halves(self):
        assert "shareNote:" in BASE_PAGE
        assert "async copyShareLink()" in BASE_PAGE
        assert "[data-share-url]" in BASE_PAGE, (
            "the button copies the server's field rather than rebuilding the URL")

    def test_the_two_cards_that_share_one_handler_both_show_its_note(self):
        for name, text in (("analysis_student.html", STUDENT_PAGE),
                           ("analysis_report.html", REPORT_PAGE)):
            block = text.split("data-share-card", 1)[1]
            assert 'x-text="shareNote"' in block, (
                f"{name} draws the button but shows no note when it is pressed")
