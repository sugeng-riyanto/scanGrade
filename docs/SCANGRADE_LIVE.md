# ScanMeet — live exam supervision inside ScanGrade (staging pilot)

Implementation branch: integration/livekit-secure-main-20261024.
User-facing name: **ScanMeet**. Backend module names remain `scan_live_*` for migration compatibility.

## Safety contract

- Disabled by default. Both SCANGRADE_LIVE_ENABLED=true AND
  SCANGRADE_LIVE_EXAM_ENABLED=true are required.
- Never mutate \`submissions\`, \`exam_targets\`, \`exam_access_codes\`, or exam timer.
- Camera required is a **UI policy**, not browser-enforceable. A camera error must
  notify the supervisor, NOT invalidate/submit the paper.
- Student audio is disabled at first. Only a teacher-approved hand raise grants
  microphone publishing. Chat/data publish, recording, and screen share are forbidden.
- This is an exam-only API; custom non-exam class settings need separate endpoints.

## Deploy in staging (NOT on the examination production VPS)

1. Deploy migration \`supabase/migrations/070_scan_live_exam.sql\` to staging,
   verify \`schools\`, \`exams\`, and \`profiles\` references and service-role RLS.
2. Install optional \`pip install -r requirements-live.txt\` on the staging worker.
3. Set LIVEKIT_URL=wss://..., LIVEKIT_API_KEY and LIVEKIT_API_SECRET only on server.
   Configure LiveKit webhook to https://<staging-host>/webhook/livekit.
4. Configure network/TURN and LiveKit egress policy with recording **disabled**.
5. Enable both feature flags for staging only; leave production OFF.
   Open /live/exam/<exam_id> with a logged-in teacher or eligible student.
6. Integration-test with a real teacher, assigned pupil and distinct school.
7. Pilot after the tests below pass, and only then plan UI entry points.

## API (existing ScanGrade login and X-CSRF-Token are mandatory)

- GET  /api/live/exams/<exam_id> (staff/pupil exam access)
- POST /api/live/exams/<exam_id>/sessions (authorized staff only)
- POST /api/live/sessions/<id>/token (staff or authorized pupil; 5-min room token)
- POST /api/live/sessions/<id>/hand-raises (authorized pupil)
- GET  /api/live/sessions/<id>/hand-raises (authorized staff)
- GET  /api/live/sessions/<id>/events (authorized staff; signed webhook audit)
- POST /api/live/sessions/<id>/hand-raises/<request_id>/approve
- POST /api/live/sessions/<id>/hand-raises/<request_id>/reject
- POST /api/live/sessions/<id>/hand-raises/<request_id>/close
- POST /api/live/sessions/<id>/hand-raises/<request_id>/reconcile
  (authorized staff; clears a grant parked in `approving`/`closing`)
- POST /api/live/sessions/<id>/end
- POST /api/live/exams/<exam_id>/enabled  `{"enabled": true|false}` (exam's own
  teacher or the school admin; see the switch below)

## The per-examination switch (migration 071)

`exams.scan_live_enabled` is the teacher's decision about a paper. It is the
value the join routes read, so it — not the existence of a room — is what makes
ScanMeet available for an exam. Migration 070 remains as it was; 071 only adds
the column (default `false`, reversible with `DROP COLUMN IF EXISTS
scan_live_enabled`), and a staging box that has not applied it reads every paper
as OFF rather than answering with a PostgREST error.

| Request | What happens |
|---|---|
| `GET /api/live/exams/<id>` | returns `enabled` beside the session, so the page paints the stored value |
| `POST .../enabled {"enabled": true}` | stores ON. **Never creates a room**: `POST /sessions` stays the explicit start, so two ON requests cannot produce two rooms |
| `POST .../enabled {"enabled": false}` | stores OFF, ends the active session row (`status='ended'`, the gate every join token checks), then asks LiveKit to delete the room |
| LiveKit delete fails | the toggle still succeeds and reports `media_cleanup: pending`; new tokens are already refused |
| Student, or a teacher who does not manage the paper, or another school | 403 |
| Body is not a JSON boolean | 400 |
| Either `SCANGRADE_LIVE_*` flag is off | 404, as everywhere else |

The two environment flags remain deployment-level kill switches: the paper's
switch can only narrow access, never widen it. The page consults the server on
every load — the button text is never the authority, so a second device sees
the stored state instead of overwriting it.

## Microphone-approval state machine

One row per pupil per room, and six statuses. Every transition is a conditional
write (`where status = <what this request saw>`), so two requests cannot both
win and the loser answers 409 rather than reporting a state it did not set:

| from | pupil raises | approve | reject | close | reconcile |
|---|---|---|---|---|---|
| *(none)* | → `pending` | — | — | — | — |
| `pending` | returns the row | → `approved` | → `rejected` | 409 | 409 |
| `approving` | returns the row | 409 | 409 | 409 | → `pending` |
| `approved` | returns the row | 409 | 409 | → `closed` | 409 |
| `closing` | returns the row | 409 | 409 | 409 | → `closed` |
| `rejected` | → new `pending` | 409 | 409 | 409 | 409 |
| `closed` | → new `pending` | 409 | 409 | 409 | 409 |

Three rules hold that table in place, and each has its own tests (the matrix,
retries and races are in `tests/unit/test_live_mic_state_machine.py`):

- **The open set is the index's, not the route's.** Migration 070's partial
  unique index counts `pending`, `approving`, `approved` **and** `closing` as one
  open request. Reading only the first three left a pupil whose row was `closing`
  with a 503 they could not resolve; the list is `live_policy.OPEN_GRANTS` now and
  the fixture raises 23505 like the database does.
- **Failures end with the microphone off.** A LiveKit error between the
  transitional and final write reverts the row and mutes; a bookkeeping write that
  matches nothing (the row moved meanwhile) mutes again and answers 409 rather than
  claiming an approval it no longer owns.
- **A grant is only ever repaired by the supervising teacher**, and repair is
  idempotent by refusal: a repaired row answers 409 the second time.

## Operational caveats / missing work before production

- A standalone staging UI is at /live/exam/<exam_id> but intentionally not linked from active exam pages.
- Pilot includes verified and deduplicated LiveKit webhook audit: /webhook/livekit;
  configure the webhook in LiveKit staging and verify signature/room scope.
  Teacher UI polls /api/live/sessions/<id>/events. Add push notifications, track
  connection reconciliation, and definitive camera-off alerts before production.
- On abnormal worker exit, a grant can be parked in `approving`/`closing`: the
  pupil's `hand_raise` keeps returning that row and `moderate` refuses it with
  409, because `approve`/`close` only reset the state on their own error path.
  `reconcile` repairs it **fail-closed** — it mutes the pupil through LiveKit
  first, then moves `approving` back to `pending` (the teacher decides again) or
  `closing` to `closed` (the teacher already asked for silence), conditional on
  the state it read, so a decision made meanwhile wins. The teacher console
  offers the action on exactly those two states. What is still missing is
  *detection*: nothing polls for a parked row, so a teacher has to notice it in
  the queue before pushing it. Wire that timer before unattended production exam
  monitoring.
- After \`end\`, database rejects new token minting; a room deletion failure
  requires cleanup. Already-issued tokens are short-lived.
- Rate limits, load test, cross-tenant integration tests and secret management are
  required prior to enabling production. Vendor the JS SDK instead of CDN.
- Pilot student publisher sets SFU track subscription permissions BEFORE publishing
  so only the room creator can subscribe to that pupil. Needs independent security
  verification and malicious-client testing before rollout; an altered publisher
  could loosen permissions on its own camera. Additional teacher observers require
  explicit allowance, not UI-only filtering.
- Never run migration, set flags or deploy to production just by merging this branch.

## Rollback

Set either flag OFF first. Existing examinations, autosave, scoring and submissions
do not use this module. Revert the app branch if necessary; additive tables can
remain, avoiding a destructive DB rollback during an examination.


## Separate preflight workstream (staging safety)

Prepare an environment file privately. Do not commit secrets or enable Live on the
production examination host.

    python deploy/livekit_preflight.py --env deploy/livekit-staging.env.example

This must pass with flags OFF. On a staging-only machine, supply a real private
env file with both flags ON, secure wss endpoint, key and secret, then run:

    python deploy/livekit_preflight.py --env /path/to/private/staging.env --pilot

A passing offline preflight is **not** proof that the LiveKit server is reachable
or that SFU subscription restrictions work. Validate those separately with real
teacher/student accounts. Changing deployment feature flags takes effect after
the application configuration is reloaded; it does not immediately disconnect
already-connected room participants.

Pilot v1 has a **single creator invigilator** per room. This limitation ensures
that the receiver identity allowed by a student's track subscription permissions
is exactly the teacher who created the session. Other staff cannot take over
an active room silently. Multi-invigilator support requires a separate reviewed
permission design.


## Read-only LiveKit connectivity smoke test

After deploying an **isolated staging** database and installing
`pip install -r requirements-live.txt`, prepare a private env file with both
ScanGrade Live flags true and the real LiveKit Cloud (or HTTPS-enabled
self-hosted) WSS endpoint / API credentials. Never commit that file.

Run on staging (from repository root):

    python -m deploy.livekit_connectivity --env /path/to/private/staging.env

This runs the existing offline preflight and requests a **read-only** room list
from LiveKit over HTTPS. It does not create any Live rooms or affect exams.
PASS verifies only that credentials reach the server; it does **not** validate
video media delivery, camera permissions, privacy against altered clients,
cross-school access, or signed-webhook behavior. Test teacher and students with
real browser sessions before enabling the pilot. If the command reports FAIL,
check credentials, egress to LiveKit over 443/TCP, DNS, and the endpoint.

The browser SDK uses `wss://` but the administrative Python RoomService
uses `https://`; `live_policy.server_api_url` derives it safely.
