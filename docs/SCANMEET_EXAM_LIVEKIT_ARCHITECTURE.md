# ScanMeet Exam Monitoring V1 — LiveKit architecture and implementation plan

**Status:** technical specification / staging-first proposal. **This document authorizes no production enablement, schema migration, or VPS change.**

**Chosen policy (fixed for Exam Mode):** mode=exam; camera=required; microphone=ask; chat=off; raise_hand=on; recording=off; screen_share=off; layout=grid. The examination/answering service is always authoritative. A media or camera failure must never submit, reset, invalidate, or shorten an examination.

## 1. Existing ScanGrade implementation — build on it

The repository already contains ScanMeet Exam Mode on main. This is **not** a greenfield deployment of the livekit-examples/meet Next.js application.

| Existing component | Location | Current purpose |
| --- | --- | --- |
| Exam policy & global kill switches | app/services/live_policy.py | Both SCANGRADE_LIVE_ENABLED and SCANGRADE_LIVE_EXAM_ENABLED required; locked EXAM_POLICY |
| Access checks and signed join tokens | app/routes/live.py and app/services/livekit_gateway.py | Existing ScanGrade login, school/exam authorization, room-scoped LiveKit tokens |
| Teacher/learner standalone page | app/templates/live/exam.html and app/static/js/scan-live-exam.js | Session start/stop, cameras, hand raises and mic approval |
| Exam and microphone state | migrations 070 + 071 | Sessions, hand raises, audit events, per-exam ON/OFF default false |
| Durable cleanup and stale room repair | migrations 073 + 074 and live_cleanup.py/live_reconcile.py | Retry deletion and reconcile missing SFU rooms |
| School entitlement | migration 075 and live_school_entitlement.py | Per-school add-on authorization; default disabled |
| Browser SDK | app/static/vendor/livekit/2.23.0 | Pinned, same-origin distribution |
| Existing gates | deploy/scanmeet_media_gate.py, scanmeet_webhook_probe.py, scanmeet_staging_gate.py | Media, signatures, and staging isolation checks |

The available repository documentation does **not** establish that a current staging run or production rollout has passed. Never conflate merged code, offline guards, provisioned host, or a historical test run with real LiveKit media evidence. See docs/SCANGRADE_LIVE.md and docs/SCANMEET_PILOT_RELEASE_GATE.md.

## 2. Architecture and separation of duties

~~~mermaid
flowchart TD
    T[Teacher dashboard / Exam Monitoring] -->|session + controls, cookie/CSRF| A[ScanGrade Flask API]
    S[Student exam page / optional media widget] -->|signed-in requests| A
    A -->|school/class/exam access| D[(Supabase ScanGrade tables)]
    A -->|short-lived signed room token| T
    A -->|short-lived signed room token| S
    T <-->|teacher receives student media| L[LiveKit Cloud or isolated SFU]
    S -->|camera and approved mic only| L
    L -->|signed webhook| W[Webhook verification / audit]
    W --> D
    A -->|stop / mute / revoke| L
    R[Independent cleanup + reconcile worker] -->|delete/retry/list rooms| L
    R --> D
    S -->|autosave and submissions: never via LiveKit| E[Existing exam service]
    E --> D
~~~

- **Flask remains the security authority.** The browser never receives LIVEKIT_API_SECRET or any Supabase service-role key. Client tokens contain opaque pseudonymous identities and only exact-room grants.
- **Supabase remains the source of truth** for teacher/school/assigned-pupil access, room status, approval state, and cleanup debt. The SFU is not the exam database.
- **Exam answers/timer/autosave keep their current code paths**, transactions and availability. Do not add foreign keys, failure cascades, or mandatory API calls from submissions to ScanMeet.
- **Separate media infrastructure.** Prefer an isolated LiveKit Cloud project or separate SFU from the examination VPS; do not run a multi-camera SFU on the resource-constrained production application box.
- **One creating supervisor per room for V1.** Supporting additional observers changes the allowed receiver set and requires new design/review.

## 3. Authorization and media policy

| Capability | Supervising teacher | Assigned pupil | Other pupil/other school |
| --- | --- | --- | --- |
| Join this exam room | Yes, own exam | Yes, assigned active sitting | Denied |
| Publish camera | Optional for teacher | Allowed; UI requests camera on join | Denied |
| Publish microphone | Allowed | DENIED until explicit teacher approval; revoked on close | Denied |
| Receive pupil tracks | Yes, own room only | **No** | No |
| Chat / data channel | Disabled in V1 | Disabled | Denied |
| Screen sharing | Not in Exam V1 | Not in Exam V1 | Denied |
| Recording / egress permission | Denied | Denied | Denied |
| Start/stop, approve/close mic | Via authorized ScanGrade API | No; may raise hand | Denied |

### Priority P0: server-enforced pupil isolation

**Existing gap:** app/services/livekit_gateway.py currently mints student grants with can_subscribe=True, and its microphone update also sets can_subscribe=True. The pupil browser then uses setTrackSubscriptionPermissions to restrict receivers of its published camera. Publisher-side controls and hidden DOM elements **are not an adequate adversarial security boundary**: a modified student client may broaden its publication permissions. The clean-browser media gate alone does not prove privacy against that client.

**For Exam V1, change BOTH token minting and UpdateParticipant microphone changes so pupils receive can_subscribe=False on the SFU; teachers keep can_subscribe=True.** Retain track subscription restrictions only as defense in depth, not as the isolation guarantee. Add assertions decoding signed grants and tests where a malicious student attempts to auto-subscribe to another pupil's track. The SFU must deny delivery; test received track bytes, not only hidden UI tiles. Do not relax this to pass a grid screenshot test.

**Trade-off:** with pupil can_subscribe=False, a pupil cannot hear teacher media in that same room. V1 Exam Monitoring is an uplink-only proctoring channel, not a two-way classroom conference. A subsequent two-way voice feature needs a separately reviewed teacher broadcast/private-room architecture; it must not grant general access to every pupil camera.

### Other non-negotiable grants

- Student token: room_join=true for one opaque room name; can_publish=true with allowed source camera only until authorized mic approval; can_publish_data=false; can_subscribe=false; room_admin=false; room_create=false; room_record=false; no metadata updates.
- Teacher token: room_join and can_subscribe true; publish only chosen teacher sources; room_admin=false and room_record=false. Teacher powers are mediated by authenticated backend API, not distributed server credentials.
- Mint only after school membership, active exam status, assigned pupil/ownership, active session, school entitlement, deployment flags and per-exam switch are all checked server-side.
- Use existing short token TTL (currently five minutes). TTL is a connection control, NOT an instant termination control for users already connected. Always combine session revocation with deleting/removing the running media room and verifying the result.
- Microphone policy is a compare-and-swap state machine: pending → approving → approved → closing → closed; rejection/retry paths fail closed. Ensure crashes and repeated requests cannot leave a pupil microphone unintentionally publishing.
- Disable recording in user policy, deny roomRecord and egress credentials, expose no record endpoint and verify no recording service is enabled for this exam path. A web app cannot prevent independent OS-level screen recording; do not promise otherwise.
- No chat: no UI, no app chat route, and can_publish_data=false. Hand raises use CSRF-protected authenticated HTTP routes, not untrusted client metadata.

## 4. Interface integration — no change to core exam flow

**Teacher dashboard**: add an optional Exam Monitoring panel reachable from the existing exam detail/preview page only when global flags, school entitlement and exam switch permit it. Panel includes START, STOP, room health, hand-raise queue, microphone approval/revocation, and connection alerts. Responsive grid: selectable 9 or 16 thumbnails per page with focus view; subscribe selectively at low resolution for visible thumbnails and request higher resolution only for focus. Never label disconnect or camera denial automatically as cheating.

**Student exam**: put a compact nonblocking camera-status widget alongside the paper. Camera required means prompt for browser permission and report denied/lost camera to the supervisor; **it cannot override operating-system/browser consent and must not block autosave, exam clock, submission, or SEB exit rules**. Students see their own preview and status, not a remote class grid. On narrow screens, the paper gets priority and the widget collapses or floats without narrowing the writing area.

**Network/device errors**: LiveKit outages, permission denial, token expiry, page reload, mute faults and device removal render an advisory and queue a connection event; never invoke grade, submit, autosave, timer, SEB unlock, or disciplinary actions from these callbacks. Teacher gets connected / reconnecting / camera unavailable / stopped; do not infer malicious conduct.

**Teacher customization boundary:** in Exam V1, the seven policy values above are fixed by school-approved Exam Mode. Teacher may choose pagination/focus density and turn an individual exam's ScanMeet switch on/off. Future Live Class and Hybrid policies belong in distinct endpoints and room types and must not reuse Exam Mode grants by accident.

## 5. Existing API contracts (retain; version only when necessary)

- GET /api/live/exams/:exam_id — authorized status, per-exam switch and locked policy.
- POST /api/live/exams/:exam_id/enabled — teacher/school-admin authorized boolean switch; OFF disables new tokens then ends/cleans room.
- POST /api/live/exams/:exam_id/sessions — authorized teacher starts or returns one active room.
- POST /api/live/sessions/:id/token — identity-bound short-lived LiveKit credential, no-store.
- POST /api/live/sessions/:id/hand-raises — assigned pupil requests microphone.
- GET /api/live/sessions/:id/hand-raises — teacher/moderation state.
- POST /api/live/sessions/:id/hand-raises/:request_id/{approve,reject,close,reconcile} — teacher-controlled CAS moderation.
- POST /api/live/sessions/:id/end — stop and durable cleanup.
- /webhook/livekit — verify LiveKit signature and replay policy before audit insertion.

Preserve the existing session table and additive migrations 070/071/073/074/075. Do not add a policy migration merely to restate constant Exam Mode values. If future customization requires persistent class-mode settings, introduce a separate reviewed schema, not an unchecked JSON policy override in a student request.

## 6. Release plan and work packages

**Work package 0 — exact-SHA baseline and ownership**
1. Pin the main commit SHA and inventory live flags, migration ledger, entitlement state, browser SDK version, CI results, and existing URLs.
2. Keep production SCANGRADE_LIVE_ENABLED=false and SCANGRADE_LIVE_EXAM_ENABLED=false until release approval. Confirm from actual deployment state; source-code defaults do not prove deployed flags.
3. Decide staging operator, reviewer, incident contact and approved school pilot. No real student exam data in staging.

**Work package 1 — P0 isolation and lifecycle**
1. Change student grants and microphone updates to can_subscribe=false; retain the teacher as the only V1 receiver.
2. Add fail-closed join/microphone/reconnect tests, hostile-client attempts to subscribe, wrong-school and unassigned-user denial, and two simultaneous teachers/rooms tests.
3. Prove STOP writes non-active session before cleanup, denies fresh tokens, and disconnects *already joined* participants. If delete_room fails, a durable worker retries independently of both feature flags; expose outstanding age and an explicit NOT COMPLETE state until the room is gone.
4. Exercise stale room detection, abandoned cleanup and recovery after worker interruption. Treat an inability to verify media deletion as a release blocker.

**Work package 2 — responsive embedding**
1. Keep existing standalone /live/exam/:id as a safe staging pilot; do not rewrite exam handling.
2. Add teacher grid entry and student nonblocking media widget behind feature flags only after P0 review.
3. Test desktop, tablet, Android/iOS where supported and Safe Exam Browser compatibility separately. No UI asserts compliance if browser permissions prohibit capture.
4. Add conservative selective subscriptions, adaptiveStream and dynacast; measure real bandwidth and FPS rather than assuming an affordable participant limit.

**Work package 3 — isolated staging acceptance**
1. Use an isolated HTTPS origin, isolated staging Supabase, LiveKit project and synthetic school A/school B users; no production credentials. See docs/SCANMEET_BIZNET_STAGING.md and deploy/livekit-staging.env.example.
2. Apply only reviewed migrations to staging and verify RLS/school roles. Run livekit preflight, SDK contract and read-only connectivity before opening media.
3. Execute real two/three-browser media gate: all 13/13 rows PASS, including decoded camera frames, mic denied before approval, approved then revoked, outsider denied, expiry at the SFU, room ended and pupil isolation.
4. Execute signed webhook probe: all 7/7 PASS, plus actual room-originated audit events, invalid signature/oversized/replay checks.
5. Add hostile-publisher/security-negative tests not satisfied by the existing 13 rows. Simulate refused camera access, packet loss, browser reload, LiveKit API failure, delete-room failure, worker kill/restart, and changed enrollment while connected.
6. Verify **no loss or mutation of submitted/unsaved answers**, timer invariance and submission independence in every media failure path.

**Work package 4 — pilot and promotion**
1. CI on the **same SHA**: Linux/Windows unit suites, region guard, landing, browser, ScanMeet isolation and security checks all PASS.
2. Do a small synthetic pilot before testing a school-size group; record server CPU/RSS, uplink/downlink, joined-camera/decoded-frame counts, SFU errors, p95 API latency and autosave health. Set an explicit session cap from measurements, not guesses.
3. Independent code/security review, device matrix and signed GO/NO-GO attached to pilot PR. Existing docs/SCANMEET_PILOT_RELEASE_GATE.md is the acceptance record; it currently does not establish a production GO.
4. Only after staging signoff, enable the feature for a tiny approved school/exam with admin supervision. Leave global flags OFF for everyone else until approved. Verify deployed SHA, live join and media cleanup after every change.

## 7. Stop conditions, telemetry and rollback

- **Immediate NO-GO:** any student receives another student's media; exam data or timer mutates after media failure; a stopped room stays streaming without a working verified cleanup path; cross-school room join; camera/mic grant bypass; unknown live recording.
- Counters: active sessions, authorized teacher count, published camera/mic tracks, failed joins, rejected tokens, school-scope denials, cleanup queue depth/oldest age/abandoned count, missing-room grace candidates, and exam autosave failures. Use pseudonymous identities; avoid storing frames/voice.
- Kill switch: turn OFF new Live entry and token issuance; issue room deletions and **verify existing tracks cease**. Feature flags alone do not disconnect participants.
- Rollback: revert Live code/feature flags using the existing guarded deployment, not a force reset. Keep additive schema and cleanup debt intact until room cleanup is confirmed. Exam service must remain serving the original paper.
- Data privacy: purpose limitation, clear notices for pupils/parents/schools, minimum metadata, retention period decided by school policy; no automatic cheating score from connectivity or video status. No recordings in V1.

## 8. Evidence checklist and definition of done

- [ ] P0 code review: SFU-enforced subscriber denial for pupils, including on microphone approval.
- [ ] P0 malicious-client isolation tests on a real LiveKit SFU PASS.
- [ ] Teacher/assigned pupil allowed; different school, unassigned pupil and second teacher denied.
- [ ] Microphone transitions PASS with retry/race/interrupted worker recovery.
- [ ] Stop proves token refusal AND existing media termination (or a visible, retried failure).
- [ ] Real staging media driver 13/13 PASS and signed webhook 7/7 PASS on the target SHA.
- [ ] Camera-off, browser reload, offline and SEB scenarios leave exam answers/timer untouched.
- [ ] Responsive teacher and student UI tested on defined viewport/device matrix.
- [ ] GitHub required CI green for exact SHA and no unresolved security checks.
- [ ] Staging credentials/URL and production credentials demonstrably distinct.
- [ ] Operator signs GO/NO-GO, approves initial exam/school allowlist and participant cap.
- [ ] Production flags remain OFF until separately approved and deployment state verified.

**Decision:** use the existing Flask + ScanMeet + LiveKit stack; do not deploy a second meeting app, do not host SFU on the examination VPS, and do not enable production monitoring until this checklist is evidenced.
