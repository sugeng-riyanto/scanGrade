# ScanGrade Live — Exam Mode pilot (isolated integration)

Implementation branch: feature/scangrade-livekit-exam-20261024.

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
- POST /api/live/sessions/<id>/hand-raises/<request_id>/approve
- POST /api/live/sessions/<id>/hand-raises/<request_id>/reject
- POST /api/live/sessions/<id>/hand-raises/<request_id>/close
- POST /api/live/sessions/<id>/end

## Operational caveats / missing work before production

- A standalone staging UI is at /live/exam/<exam_id> but intentionally not linked from active exam pages.
- Pilot UI uses polling and camera checks. Add push notifications, verified/deduplicated
  webhooks, and backend camera-off alerts before production.
- On abnormal worker exit, transitional \`approving\`/\`closing\` states need
  reconciliation; do not enable unattended production exam monitoring before it.
- After \`end\`, database rejects new token minting; a room deletion failure
  requires cleanup. Already-issued tokens are short-lived.
- Rate limits, load test, cross-tenant integration tests and secret management are
  required prior to enabling production. Vendor the JS SDK instead of CDN.
- Student can_subscribe=true currently permits requesting other student tracks;
  UI hiding does NOT enforce privacy. Enforce subscription permissions before release.
- Never run migration, set flags or deploy to production just by merging this branch.

## Rollback

Set either flag OFF first. Existing examinations, autosave, scoring and submissions
do not use this module. Revert the app branch if necessary; additive tables can
remain, avoiding a destructive DB rollback during an examination.
