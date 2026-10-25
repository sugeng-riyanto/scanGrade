# ScanMeet pilot release gate (25 October 2026)

This is an **operator checklist**, not proof that a gate has passed. Never mark a
runtime check PASS from a unit test or a historical run from another commit.

## Evidence required, pinned to release commit

- [ ] Record candidate Git SHA, deployed staging SHA, staging origin, UTC run time,
      redacted evidence artifact links and operator sign-off.
- [ ] RBAC: real user JWT, unaffiliated user with NULL school, cross-school denial;
      no authenticated pupil can administer a session.
- [ ] Webhook: valid signature and replay acceptance contract, expired/tampered
      signatures denied; confirm no sensitive request bodies in logs.
- [ ] Media: per-pupil track isolation, decoded camera frames for both pupils,
      microphone request/grant/revoke, outsider denied, teacher shutdown.
- [ ] Five-minute token expiry: app refuses issuance for ended sessions; SFU rejects
      expired tokens, measured after expiry (not merely decoded JWT claims).
- [ ] Fault injection: LiveKit delete_room fails; new tokens denied immediately;
      durable cleanup retry eventually deletes live media room, including after
      restart, with attempt limits, telemetry and idempotence.
- [ ] Stale active session: database row says active but SFU room missing; recover
      safely without allowing cross-school sessions or stale tokens.
- [ ] Device matrix: desktop Chrome/Edge, Android Chrome, iOS Safari;
      explicitly label every untested combination NOT TESTED.
- [ ] Network loss/rejoin, browser refresh, camera permission denial; timer,
      submitted answers and autosave are unchanged across failures.
- [ ] Capacity measurements on a separate load runner; no intentional load spike
      on the shared production VPS. Measure CPU, RSS, response latency, egress,
      and error rates against a documented pilot participant limit.
- [ ] CI on exact SHA: Linux unit, Windows unit, browser, landing, region, skipped
      path and ScanMeet isolation checks all green. Unresolved failures BLOCK.
- [ ] Review fresh-staging migration differences and rollback procedure. Production
      migrations are separately approved and applied using the deployment gate.

## Deployment controls

**Before merge:** independent code review of the exact PR diff; check pinned
LiveKit SDK and same-origin assets; no plaintext secrets or staging identities
committed; verify a tested backup/restore procedure. Ensure documented recovery
points for database and application release.

**Before pilot activation:** verify the deployed SHA and staging acceptance
evidence still match. Confirm `SCANGRADE_LIVE_ENABLED=false` and
`SCANGRADE_LIVE_EXAM_ENABLED=false` in production until an explicit operator
approval. Confirm observability for 5xx, LiveKit room cleanup pending age,
room-count mismatches and exam timer/autosave health. Record pilot schools,
participant cap, start/end time and responsible incident contact.

**During pilot:** activate only approved feature flags in the smallest supported
scope; do not infer school isolation from a UI switch. Watch media room count,
pending-cleanup oldest age, live 5xx, timeouts and exam autosave. Stop the pilot if
any school isolation/privacy violation is observed.

**Rollback / kill switch:** disable new Live exam entry and token issuance first;
then terminate existing LiveKit rooms via verified cleanup, because disabling
issuance DOES NOT disconnect already connected media. Roll back application code
through the tested deploy process if necessary. A database downgrade is NOT
assumed safe; use a documented migration-specific recovery plan. Verify existing
exam responses and timer continue operating while media is disabled.

## Decision record

| Field | Value |
| --- | --- |
| Candidate SHA | NOT RECORDED |
| Staging E2E evidence | NOT ATTACHED TO THIS DOCUMENT |
| Device/network evidence | NOT VERIFIED |
| Cleanup failure/retry evidence | NOT VERIFIED |
| Production flags | MUST REMAIN OFF BEFORE APPROVAL |
| Reviewers / operator | NOT SIGNED |
| Decision | NO-GO until all mandatory gates above pass |

Record a dated, auditable GO/NO-GO decision in the release PR. Do not silently
convert NOT TESTED to PASS.
