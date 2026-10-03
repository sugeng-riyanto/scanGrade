# Exam timing and sitting continuity

Six principles, and where each one lives in the code. They exist because a sitting is
bounded by *two* clocks and observed from a device whose clock nobody controls, and
because every screen that answered those questions on its own was one edit away from
disagreeing with the screen next to it.

## 1. The server owns the clock

`app/utils/exam_window.py` is the only arithmetic that answers "when does this end".
It reads the sitting's stored `started_at` plus the exam's own `duration_minutes`
(and `end_at` when the exam is set to stop at the window's end), and nothing else.
Every caller — the exam page, the sync API, the deadline sweep, the lock gate, and
`app/services/attempt_status.py` — *asks* it rather than recomputing.

The client never decides anything about time. `GET /student/attempt-status/<exam_id>`
returns `server_now`, an absolute `deadline`, and `seconds_left` computed at that
instant; the page adopts an offset once on load (`_resyncClock`) and re-syncs on every
heartbeat. A wrong phone clock therefore counts toward the wrong number, not the
device's own.

## 2. Heartbeat / liveness

`POST /student/heartbeat/<exam_id>` records `submissions.last_ping_at` and
`ping_count` in one lightweight write, and returns the same status payload so the ping
doubles as the clock re-sync. The page pings on `SG_HEARTBEAT_MS` (25s).

> **The heartbeat is an observation signal, not a lock mechanism.** Nothing in
> `attempt_status.heartbeat` writes `status`. A pupil whose signal dropped, or whose
> ping never arrived, is *not* ejected — only fullscreen/tab-switch can lock a sitting
> (`locked_pending_resume`, migrations 011 + 054). Do not make a missing heartbeat a
> new reason to lock; that would punish the connection, not the behaviour.

Liveness is stored on the row rather than as one event per ping on purpose: a row per
25 seconds per pupil would grow the event table with noise, and the box runs 1 vCPU.

## 3. Idempotent resume

`GET /student/attempt-status/<exam_id>` writes nothing: it creates no attempt, resets
no answers and moves no clock, and returns the same state for the same condition
however many times it is called. That is what makes it safe both as the status door and
as the resume door. `?answers=1` adds the pupil's own saved answers, for a device whose
local draft is gone.

The anti-cheat resume path (`app/services/resume_code.py`, with the pupil's recovery
code) is a **separate** door: a lock is cleared by a code, not by re-reading a status.
The two never stand in for one another.

## 4. Granular audit trail

`app/services/attempt_status.py::record_event` writes one row per *transition* into
`attempt_session_events` (migration 035), numbered `max(seq) + 1` per attempt:

`attempt_started`, `connection_gap`, `connection_resumed`, `attempt_locked`,
`attempt_resumed`, `attempt_finalized`.

A gap between pings greater than `GAP_SECONDS` (90s) is recorded as a
`connection_gap` (with its length) plus a `connection_resumed`, once per return — never
a single overwritten "last seen" column, so a pupil's "my connection dropped three
times" is answerable from the record. `timeline()` reads them in sequence for a
dashboard. Writes are best-effort: the audit trail must never cost a pupil their paper.

## 5. Graceful degradation

Every unexpected condition answers with a state, not a raw error. The status payload
carries a `status` from a closed set — `active`, `locked_pending_resume`, `finished`,
`expired`, `missing` — and a `message_key` per status. Callers render the key in the
pupil's language; a status read that fails answers `missing` with a 200 rather than a
500, so a pupil gets a sentence, not a broken page.

## 6. One source of truth for status

`app/services/attempt_status.get_attempt_status(supabase, exam_id, student_id)` is the
one function that turns a `submissions` row plus the clock into that closed-set status
with its deadline and seconds-left. The pupil page uses it, the heartbeat returns it,
and any dashboard that needs "is this paper still live" should call it rather than
re-deriving the vocabulary. The status decisions live in `_status_of` and nowhere else.

## What is *not* here

Wiring the timeline into the teacher/invigilator/admin dashboards is a read of
`attempt_status.timeline()` on an existing page; the recording half is done and the
reader exists. Structured per-criterion rubrics are a separate feature.
