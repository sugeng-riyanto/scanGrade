"""An in-memory stand-in for the four tables the ScanMeet room acts on.

The microphone-approval state machine lives entirely in `app/routes/live.py`, and
what it relies on from the database is narrow enough to model honestly: reads
with `eq`/`in_`/`order`/`limit`, conditional updates (the `eq("status", …)` is a
compare-and-swap), and one unique partial index —

    CREATE UNIQUE INDEX scan_live_one_open_request
      ON scan_live_hand_raises(session_id, student_id)
      WHERE status IN ('pending','approving','approved','closing')

— which is why a second raise from a pupil who already has an open one is a
database error rather than a second row. `FakeQuery.insert` raises the Postgres
duplicate-key error for exactly that, so a test cannot pass by ignoring a
constraint that exists in migration 070.

Two hooks make races reproducible without threads:

* `db.race` writes a status into every row immediately before an update is
  matched, i.e. "another writer decided this between the read and the write".
* `db.race_after = n` lets the first `n` updates through untouched, so a race
  can be aimed at the *second* update of a multi-statement transition.

`stage()` swaps the route module's database, exam, sitting check, LiveKit
boundary and feature switch for these fakes — nothing here opens a socket, reads
a real credential or imports the SDK.
"""
from __future__ import annotations

from flask import Flask, g

from app.routes import live
from app.services.live_policy import OPEN_GRANTS

#: The app's own list, not a copy: a status the route forgets here and the index
#: still counts is exactly the 503 this fake exists to reproduce.
OPEN = OPEN_GRANTS

SESSION = {
    "id": "room-id", "status": "active", "exam_id": "exam-1",
    "created_by": "teacher-a", "school_id": "school-1", "room_name": "sg_room",
}
EXAM = {"id": "exam-1", "teacher_id": "teacher-a", "school_id": "school-1"}
RAISE = {
    "id": "raise-1", "session_id": "room-id", "school_id": "school-1",
    "student_id": "student-x", "status": "pending", "created_at": "t0",
}


class FakeQuery:
    """The supabase-py chain shape the routes actually use."""

    def __init__(self, owner, name):
        self.owner = owner
        self.name = name
        self.op = "select"
        self.columns = "*"
        self.payload = None
        self.filters = []
        self.order_by = None
        self.desc = False
        self.limit_n = None

    # ── chain ────────────────────────────────────────────────────────────────
    def select(self, *columns):
        # PostgREST returns only the columns asked for. Projected here too, so a
        # route that reads a field it never selected fails in this suite rather
        # than against the live database.
        self.op = "select"
        self.columns = columns[0] if columns else "*"
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = dict(payload)
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def in_(self, key, values):
        self.filters.append((key, list(values)))
        return self

    def order(self, key, desc=False):
        self.order_by, self.desc = key, desc
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    # ── execution ────────────────────────────────────────────────────────────
    def _matches(self, row):
        for key, value in self.filters:
            if isinstance(value, list):
                if row.get(key) not in value:
                    return False
            elif str(row.get(key)) != str(value):
                return False
        return True

    def execute(self):
        rows = self.owner.rows.setdefault(self.name, [])
        if self.op == "insert":
            self.owner.check_unique(self.name, self.payload)
            row = dict(self.payload)
            row.setdefault("id", f"{self.name}-{len(rows) + 1}")
            rows.append(row)
            return _Result([dict(row)])
        if self.op == "update":
            self.owner.updates += 1
            if self.owner.race and self.owner.updates > (self.owner.race_after or 0):
                # Another writer got there first, before this one's WHERE ran.
                for row in rows:
                    row["status"] = self.owner.race
                self.owner.race = None
            matched = [r for r in rows if self._matches(r)]
            if self.owner.fail_update:
                raise RuntimeError("database unreachable")
            for row in matched:
                row.update(self.payload)
            return _Result([dict(r) for r in matched])
        matched = [r for r in rows if self._matches(r)]
        if self.order_by:
            matched.sort(key=lambda r: r.get(self.order_by) or "", reverse=self.desc)
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        wanted = None
        if self.columns and self.columns.strip() != "*":
            wanted = [c.strip() for c in self.columns.split(",") if c.strip()]
        out = []
        for row in matched:
            out.append({k: row.get(k) for k in wanted} if wanted else dict(row))
        return _Result(out)


class _Result:
    def __init__(self, data):
        self.data = data


class FakeDB:
    def __init__(self, rows=None):
        self.rows = {name: [dict(r) for r in items]
                     for name, items in (rows or {}).items()}
        self.race = None
        self.race_after = None
        self.updates = 0
        self.fail_update = False
        self.fail_insert = False

    def table(self, name):
        return FakeQuery(self, name)

    def check_unique(self, name, payload):
        if self.fail_insert:
            raise RuntimeError("database unreachable")
        if name == "scan_live_events":
            # `event_key` is UNIQUE in migration 070; the webhook leans on that
            # for idempotent redelivery rather than reading before writing.
            for row in self.rows.get(name, []):
                if str(row.get("event_key")) == str(payload.get("event_key")):
                    raise RuntimeError(
                        'duplicate key value violates unique constraint '
                        '"scan_live_events_event_key_key" (23505)')
            return
        if name != "scan_live_hand_raises":
            return
        for row in self.rows.get(name, []):
            same_pupil = (str(row.get("session_id")) == str(payload.get("session_id"))
                          and str(row.get("student_id")) == str(payload.get("student_id")))
            if same_pupil and row.get("status") in OPEN:
                # 23505 is what supabase-py surfaces for the partial index.
                raise RuntimeError(
                    'duplicate key value violates unique constraint '
                    '"scan_live_one_open_request" (23505)')

    # ── assertions helpers ───────────────────────────────────────────────────
    def raises(self):
        return self.rows.get("scan_live_hand_raises", [])

    def statuses(self):
        return [r["status"] for r in self.raises()]


def stage(monkeypatch, rows=None, *, mic_errors=(), live_on=True,
          sitting_allowed=True):
    """Wire the route module to fakes; return ``(app, db, mic_calls)``.

    ``mic_errors`` is consumed one item per LiveKit call — an exception there is
    raised instead of granting/denying, which is what a LiveKit outage looks
    like to the route.
    """
    db = FakeDB(rows)
    calls = []
    pending = list(mic_errors)

    def fake_mic(config, session_id, room, student_id, enabled):
        calls.append((session_id, room, student_id, enabled))
        if pending:
            outcome = pending.pop(0)
            if isinstance(outcome, Exception):
                raise outcome

    monkeypatch.setattr(live, "get_supabase", lambda: db)
    monkeypatch.setattr(live, "_exam", lambda *_: EXAM)
    monkeypatch.setattr(live, "exam_sitting_allowed",
                        lambda *_: (sitting_allowed, "" if sitting_allowed else "no"))
    monkeypatch.setattr(live.livekit_gateway, "set_student_mic", fake_mic)
    monkeypatch.setattr(live.live_policy, "feature_enabled", lambda *_: live_on)
    return Flask(__name__), db, calls


def door(app, endpoint, *args, role="guru", user="teacher-a", school="school-1"):
    """Call an undecorated view in a request context, as a real response."""
    view = getattr(live, endpoint).__wrapped__
    with app.test_request_context():
        g.user_school_id = school
        g.user_role = role
        g.user_id = user
        return app.make_response(view(*args))
