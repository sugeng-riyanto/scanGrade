# Supervisor assignments and handover: implementation and release evidence

31 October 2026. Stacked on the exam-entry delivery (PR #65). This document
records implemented behavior; it is not production or real-device signoff.

## Behavior

- `SCANGRADE_LIVE_SUPERVISION_ENABLED=false` by default. Existing exam-owner and
  school-admin permissions continue without querying the assignment table.
- A same-school admin can assign a teaching staff account for up to seven days,
  extend it, or revoke it. Each write requires the last database version and a
  reason; grant/revoke records are audited. Delegation grants media supervision,
  never exam editing, grading or access to another school's pupils.
- Assigned staff find their current exams at `/live/supervision` in their account.
  The admin's exam-room page provides assignment controls and a sign-in-required
  link. Sharing a link does not grant access. This implementation does not send
  WhatsApp messages or invitations.
- Transfer requires an authorized admin and a current session version. It keeps
  the same session ID, concurrency reservation, participant cap and original
  start time. The deadline can only shorten, including the assignment deadline;
  it cannot extend, and the package's session counter does not increment.
- A private database operation records a transfer and a cleanup job atomically.
  The session stays active but cannot issue tokens while `transfer_state=closing`.
  Old microphone approvals close. The existing cleanup worker deletes the old
  room before committing a fresh room name, supervisor and media version.
- Failed provider deletion or failed database finalization stays in the durable
  cleanup queue, with the existing retry/lease/abandonment policy. There is no
  in-memory handover task. An abandoned cleanup needs operator repair; it is not
  shown as a successful transfer.
- The finalizer rechecks expiry, exam ON/OFF, school entitlement and the target's
  current assignment. OFF, revocation or a changed session cancels the transfer.
  It never resurrects an ended session. Revoking the current delegate queues room
  deletion, then ends that session.
- Status exposes an explicit field whitelist. Old-owner end requests and delayed
  maintenance observations cannot act on a different room generation.
- Browser media stops during transfer. A joined pupil may reconnect to the new
  generation with fresh authorization; the microphone remains OFF. A supervisor
  can explicitly join after takeover. Camera capture is separated from publish:
  after a native permission prompt resolves, another status check must authorize
  that exact session/version before the captured track is published.

## Database and deployment

Migration `supabase/migrations/079_scanmeet_supervisor_handover.sql` is additive
and follows 076/077/078. Its new tables have RLS and no browser grants; management
functions use invoker rights, fixed search paths and service-role-only EXECUTE.
No production database migration or deployment was performed by this change.

The isolated Supabase transaction attempt did not execute: its tool confirmation
expired. This is not evidence of a staging migration pass. The local SQL runner
instead uses pinned PGlite PostgreSQL with minimal core-school/profile/exam
fixtures, real ScanMeet migrations, repeat application of 079, SQL assertions
and rollback. It proves SQL execution and sequential state/constraint behavior;
it does not reproduce Supabase Auth, PostgREST schema refresh or simultaneous
connections. The test-only dependency is not required on the production VPS.

Run local SQL checks with `node deploy/scanmeet_supervision_sql_check.cjs` after
`npm ci`; the unit suite also runs this check. The transaction assertions live in
`tests/scanmeet_supervision_sql_checks.sql`. They cover unauthorized/null/other-
school actors, assignment versions, room rotation, quota and deadline preservation,
reserved concurrency, claim ownership, idempotency, revocation, denied starts,
OFF cancellation, audit and browser privilege restrictions.

Before enabling the flag, apply/rehearse the migration on isolated staging,
ensure all serving workers use the same tested SHA, then prove actual two-host
handover, interrupted cleanup/restart, old-token isolation, native permission
prompts during an armed exam and physical tablet/iOS behavior. The existing
webhook/media gate alone does not cover this new handover behavior.

## Token and rollback boundary

Room rotation is **not** a claim that all old JWTs have been revoked. Provider
revocation varies by deployment; an old token may rejoin the retired room. New
media uses a different opaque room name and supervisor permissions. Late client
work is guarded by generation checks and authorization after connect and after
capture, but real SFU/old-client/old-token isolation still needs staging evidence.
Primary provider reference: https://docs.livekit.io/frontends/reference/tokens-grants/.

Mixed old/new workers or cached old clients are not accepted pilot evidence.
Before reverting the code or disabling supervision support, switch pilot exams
OFF, drain cleanup including pending transfers, confirm no room carries pupil
media, and only then revert the application. Keep the additive schema and audit
records; do not drop the cleanup/handover tables as an emergency rollback.
Standalone meetings, attendance exports, camera effects, local recording and
OBS-like production scenes remain separate deliveries.
