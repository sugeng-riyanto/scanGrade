# ScanMeet handoff — PR #28 hardening (candidate `3d1e780`)

**Verdict: HOLD** for production activation. The code-level P0 findings are fixed
and measured on isolated staging; the media matrix could **not** be measured on
the host available, and one operator action is still outstanding.

| | |
|---|---|
| Branch | `scanmeet/integration-final` (head of it — the commit this page is on) |
| History it replaces | `scanmeet/cleanup-debt-stale-repair` at `9505793`, kept for audit |
| Base | `origin/main` at `4e455df1` |
| Measurements were taken at | `3d1e780` — `app/`, `supabase/`, `tests/` and `deploy/` are byte-identical to this branch's (`git diff --stat 3d1e780 HEAD -- app/ supabase/ tests/ deploy/` is empty; only this page, two other pages and two label lines in the media workflow differ) |
| Built on | `421d0e1` (the SHA PR #28 merged) + `7574fa2` + `3d1e780` |
| Staging host | `https://scanmeet-stg.103.93.133.193.sslip.io` (isolated vhost, own database, own LiveKit project) |
| Production | untouched, ScanMeet **OFF** (measured below) |

### Why this branch exists, rather than a merge of the old one

`main` merged PR #28 at `a462183` and then reverted it at `ee5c914` / `4e455df`
("restore pre-PR28 code pending safety review"). A branch that descends from the
pre-revert history cannot restore that code by merging — the merge has nothing to
add — so the integration branch is built **from `origin/main`** and says what it
is: `cd8cba7` reverts the revert, and `fb707af`, `c4960ce` and the two documentation
commits are the follow-up fixes replayed on top. `git diff` against the measured
branch is empty, which is the check that this is the same code and not a retyping
of it.

The compare view for the review is
`https://github.com/sugeng-riyanto/scanGrade/compare/main...scanmeet/integration-final?expand=1`.
PR #30 (revert-of-revert) must not be merged in parallel — it re-adds `421d0e1`
without any of the fixes below.

## What was fixed, and why each one was a real defect

### 1. Disposal deadlocked behind the feature flags

`run_once()` returned early while both ScanMeet flags were off, and the scheduler
was only started when they were on. So the debt recorded by a failed
`delete_room` could never be paid after a teacher switched the paper off — which
is exactly the state in which a room still carrying pupil video must go. The two
flags now gate **creation, sessions and token issuance** (every route checks that
itself); disposal and reconciliation no longer consult them, and they gate
themselves on LiveKit credentials and the 073/074 tables, reporting `BLOCKED`
with the name of what is missing instead of pretending to have run.

Measured on staging, both flags forced off for the command:

```
feature_enabled(cfg) = False
{"ran": true, "cleanup": {"attempted": 2, "done": 2, "retried": 0,
 "abandoned": 0, "lost": 0}, "reconcile": {"measured": true, ...}}
after : [('sg_rc_debt_a', 'done', 0, 'room was already absent: ServerError(c…'),
         ('sg_rc_debt_b', 'done', 0, 'room was already absent: ServerError(c…')]
```

The counters are that process's own, which is what makes the row attributable:
the staging unit's scheduler was also running, and a previous attempt to measure
this was contaminated by it (`attempted: 0` while the rows were already `done`).

### 2. Cleanup debt died with its parent row

Migration 073 declared `session_id` and `school_id` `ON DELETE CASCADE`, so
deleting a session, a school, or a row under a retention purge erased the only
record that a room still needed deleting. Migration 074 is forward-only (073 is
already applied on staging) and releases the reference with `ON DELETE SET NULL`
plus a trigger that stamps `orphaned_at`; the worker reads `room_name`, never the
parent.

Measured on staging, live:

```
session deleted  → job survives, session_id IS NULL, school_id kept,
                   orphaned_at set, status pending
school deleted   → session cascaded away, job survives with both references NULL,
                   orphaned_at set, status pending
```

Schema as the ledger claims it, read back from `pg_constraint`:

```
scan_live_cleanup_jobs_session_id_fkey → FOREIGN KEY (session_id) … ON DELETE SET NULL
scan_live_cleanup_jobs_school_id_fkey  → FOREIGN KEY (school_id) … ON DELETE SET NULL
rls enabled: True
grants: postgres, service_role — no anon, no authenticated
```

### 3. Claim lease and worker concurrency

`claim_next()` now renews and compares the lease, not just the token, so a worker
that lost its lease cannot mark another worker's job done and cannot touch the
room it no longer owns. Eight consecutive failures end in `abandoned` with the
last error, visible to an operator; a restart resumes from the table.

### 4. Stale-session reconciliation

`unknown → live → missing/stale` is the only path to a park. A session that never
had a room is not stale; a probe that fails is not evidence of absence; and a
room that returns clears the candidate inside a measured grace window, so one
short room list during a LiveKit restart cannot end a lesson. Parking is a
conditional update against `status = 'active'`, so an `ended` session is never
overwritten, and recovery issues a new session rather than reviving the old room.

### 5. The 413 that the proxy turned into a 502

The signed-webhook probe measured 6/7 on staging: `oversized` answered **502**
where the route answers 413. The cap was never broken — the route refuses on
`Content-Length` before reading a byte — but gunicorn resets a connection that
still has unread bytes, and nginx passes that reset on as an upstream failure. A
delivery that was correctly refused looked like a delivery the deployment could
not handle. The refusing branch now drains what the proxy already buffered
(bounded, never parsed) and answers 413.

## Regression tests, by name

Ninety tests across the four suites hold the fixes above, and they run with no
network, no browser and no LiveKit project:

| File | Tests |
|---|---|
| `tests/unit/test_live_cleanup.py` | 36 |
| `tests/unit/test_live_reconcile.py` | 13 |
| `tests/unit/test_live_webhook.py` | 13 |
| `tests/unit/test_live_exam_toggle.py` | 28 |

The ones that would have to be deleted for a regression to pass:

```
test_cleanup_runs_with_the_feature_flags_off
test_maintenance_is_blocked_without_credentials
test_maintenance_is_blocked_without_the_ledger_table
test_schema_ready_reads_the_ledger
test_an_orphaned_job_is_still_deleted
test_two_workers_cannot_claim_the_same_room
test_a_crashed_claim_is_recovered_after_its_lease_without_burning_an_attempt
test_a_worker_that_lost_its_lease_does_not_touch_the_room
test_a_renewal_without_the_token_is_refused
test_a_backed_off_job_is_not_claimed_early
test_the_park_needs_the_room_to_stay_gone_past_the_grace
test_a_concurrent_decision_wins_over_the_repair
test_a_transition_that_lost_its_row_is_reported_not_assumed
the_cap_drains_what_the_proxy_buffered_before_answering
discard_stops_at_the_ceiling_and_at_the_end_of_the_body
```

Run at this branch: `90 passed`. The wider set that touches ScanMeet, the browser
and deploy gates (`-k "live or scanmeet or webhook or gate_armament"`) is
`515 passed`; the media-gate, staging-gate and staging-workflow suites are
`92 passed`.

## Evidence measured on staging at `3d1e780`

| Gate | Result | How it was taken |
|---|---|---|
| Staging isolation + LiveKit RoomService auth | **PASS** (exit 0) | `deploy.scanmeet_staging_gate --probe-livekit` |
| Signed webhook | **7/7 PASS** (exit 0) | `deploy.scanmeet_webhook_probe --url <staging>` |
| RBAC end to end | **15/15 PASS** (exit 0) | `deploy.scanmeet_rbac_gate`, four synthetic identities + the no-school one |
| Debt survives session/school deletion | **PASS** | live delete + read back, above |
| Disposal with both flags off | **PASS** | one maintenance pass, its own counters |
| Media (13 rows, real browsers) | **CANNOT MEASURE** (exit 2), 2/13 rows taken | see below |
| Device matrix (Chrome, Edge, Android, iOS) | **NOT TESTED** | no devices on this host |
| Production flags | **OFF, measured** | `POST https://scangrade.web.id/webhook/livekit` → `404 {"error":"Live disabled"}` |

RBAC rows as the run printed them:

```
teacher-sign-in, student-sign-in, teacher-door-refuses-pupil,
pupil-cannot-open-room (403), teacher-opens-room (201),
outsider-refused-status, noschool-refused-paper (302),
pupil-cannot-end-room (403), outsider-refused-token (403),
noschool-refused-token (403), pupil-gets-token (camera only),
teacher-ends-room (202), rls-profiles-scoped,
rls-cross-school-invisible (1 and 1 rows, 0 in common),
rls-live-tables-invisible (403, 403)
```

## Why the media matrix is `CANNOT MEASURE`

The only host available is the 1-CPU production box that also serves staging. The
run took `sdk-loaded` and `session` (the room opened and the teacher joined), then
the second Chrome launch never exposed a debug target: 21 browser processes
remained after the attempt, load average reached **20.5** with **72 MB** free of
957 MB. The gate refused to file a sign-off, which is the behaviour the review
asked for — it reports `CANNOT MEASURE` with the reason rather than a green tick.

That run also slowed production (`https://scangrade.web.id/` took 51 s during it,
200 again after the browsers were cleared). The lesson is the one the mission
states: the media matrix belongs on a runner, not on this box. The browser
workflow in `.github/workflows/scanmeet-staging-media.yml` is the venue, and its
evidence needs the eight secrets in the `scanmeet-staging` environment.

## Owner actions outstanding

1. **Open the PR** from this branch (compare link above) — posting to GitHub from
   this checkout needs a token with `repo` scope, which is not available here.
2. **Run the media matrix** in `.github/workflows/scanmeet-staging-media.yml`
   against this SHA, on a runner — 13/13 PASS is the gate for pilot. It must not
   be run on the 1-vCPU host that serves production: a previous attempt there left
   21 browser processes, took load to 20.5 and slowed the live site to 51 s.
3. **`apply_migration.py --status` is unreadable** on the box:
   `PermissionError: /var/backups/scangrade/scangrade-db-….tar.gz`. That is a
   root-owned recovery-point directory, so the ledger cannot answer for itself
   without an administrator. Do not loosen the directory's mode to make it pass.
   The minimal verification, run **as the account that owns that directory** (it
   is root's; `scangrade` cannot read it), and without `--commit` — a dry run reads
   the ledger and the schema and changes nothing:

   ```bash
   cd /home/scangrade/scanmeet-staging
   set -a; . /home/scangrade/.config/scanmeet/staging.env; set +a
   .venv-scanmeet/bin/python deploy/apply_migration.py \
     supabase/migrations/073_scan_live_cleanup_reconcile.sql
   .venv-scanmeet/bin/python deploy/apply_migration.py \
     supabase/migrations/074_scan_live_cleanup_debt_survives.sql
   ```

   Without `--commit` each of those is a dry run that only reads the ledger and
the schema, which is what the exit gate asks for.
4. **Decide the device matrix**: Chrome and Edge desktop can be run on a runner;
   Android and iOS need real hardware, and until then they are `NOT TESTED`, not
   `PASS`.

## Rollback

Both ScanMeet migrations are additive: 073 creates the cleanup and reconcile
tables, 074 re-points two foreign keys and adds a trigger. Disabling the feature
does not need either of them dropped — the app keeps serving exams with no
ScanMeet row present. If a schema rollback is ever required, drop
`scan_live_cleanup_jobs`, `scan_live_events`, the reconcile helper, and the
session columns 070 added, in that order, after the rooms have been deleted; the
disposal worker is the only reader and it refuses to run when its table is
missing.
