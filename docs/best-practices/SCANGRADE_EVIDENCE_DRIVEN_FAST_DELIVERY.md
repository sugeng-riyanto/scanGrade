# ScanGrade / ScanMeet — Evidence-Driven Engineering & Fast Continuous Delivery

> Operational playbook for coding agents, QA, DevOps and release managers. This document is guidance, **not evidence that any release gate has passed**.

## Mission and operating boundaries

Complete and maintain ScanGrade and ScanMeet with verifiable RBAC, CRUD, API/database/frontend/backend compatibility and secure LiveKit behavior. Reduce hallucinated status reports, repeated work, and staging-to-production delay. Work autonomously on authorized development and staging tasks. **Do not merge main, activate ScanMeet in production, change production data, or deploy production without explicit authorization for that action and verified release gates.** Never interrupt active exams.

## 1. Evidence-first protocol

1. Inspect current branch, clean/dirty worktree, HEAD SHA, open PRs, CI results, deployed staging SHA and latest checkpoint before editing.
2. Treat Git commits, machine-generated test results, migration ledger, deployed artifact digest, server health probes and signed E2E artifacts as evidence. Chat summaries and coding-agent assertions are not evidence.
3. Every acceptance criterion has one of **PASS / FAIL / BLOCKED / UNKNOWN**. PASS requires command, environment, SHA, timestamp and retrievable log/artifact. Stale or mismatched evidence is UNKNOWN.
4. Before claiming success, independently verify observable behavior and negative authorization cases. Never invent counts, logs, URLs, deployment state or GO decisions.
5. Check whether the exact same test already passed on the same SHA, dependencies, config and environment. Reuse valid evidence; rerun when any relevant input changed or release policy requires it.
6. After two distinct unsuccessful fixes, stop repeating the same command; investigate root cause and revise the approach. Never skip or weaken tests to turn red green.
7. Report only **new substantive changes**: changed SHA, CI regression, review/merge, staging E2E, license revocation, migrations, backup/restore/rollback or GO/NO-GO.

## 2. Single checkpoint and traceability

Maintain `docs/agent-progress.md` (create when executing a task) with: task ID, requirement, owner/agent, environment, HEAD SHA, artifact digest, command, PASS/FAIL/BLOCKED/UNKNOWN, log URL/path, blockers and next executable action. Keep secrets out of files and logs. Update checkpoint after meaningful work, not after every message. Do not overwrite evidence from earlier SHAs.

Example:

| Requirement | SHA / environment | Result | Evidence | Next action |
| --- | --- | --- | --- | --- |
| Live camera disconnect on entitlement OFF | exact SHA / staging | UNKNOWN | pending authenticated media trace | run connected-media E2E |

## 3. Full-stack acceptance matrix

- **RBAC:** super admin, school admin, principal, teacher, supervisor and pupil; validate tenant/school boundaries and unauthorized access server-side and via RLS, not merely UI visibility.
- **CRUD:** create/read/update/archive/delete where applicable; validation, ownership, audit trail, concurrency, negative tests and consistent UI states.
- **API contracts:** frontend request payloads, auth, routes, status codes, backend validation, response schemas and DB columns/constraints match; include contract tests and backwards compatibility.
- **Database:** migrations in correct dependency order (including 073–075 and later applicable versions), foreign keys, indexes, RLS, transaction behavior and tenant isolation; use isolated clone before production.
- **Frontend:** responsive desktop/tablet/mobile, role-specific navigation, accessible controls, loading/error states and real backend integration.
- **LiveKit:** authorized room/token issuance, school/pupil isolation, camera required when enabled, mic closed until approval, chat disabled, hand raise enabled, recording disabled, attendance and close-session behavior.
- **Revocation:** connected media disconnects on license OFF, expiry or archival; token refresh cannot restore unauthorized access; cleanup failure recovers after restart; validate with real authenticated sessions, not mocks alone.

## 4. Efficient development and CI

1. Prioritize the smallest change that removes the highest-severity verified blocker. Prefer focused commits and reviewable PRs.
2. Run affected lint/unit/contract/RBAC tests first; run full regression and critical E2E on release candidates.
3. Parallelize independent CI jobs and cache dependencies safely; key caches by lockfile, environment and version.
4. Cancel superseded *non-deployment* checks when safe; serialize deployments and never interrupt migrations.
5. Build an immutable release artifact **once**. Record commit SHA and artifact digest. Promote that same artifact to staging and production with environment-specific secrets/config.
6. Verify the **served SHA/digest** equals the tested candidate; a mismatch is an automatic NO-GO.
7. Store CI and E2E logs, screenshots, traces and structured reports as artifacts with retention and links.

## 5. Staging and database readiness

- Staging must be isolated from production DB, keys, storage, users and LiveKit rooms; never copy real student data into unprotected fixtures.
- Automatically deploy candidate artifact to staging and run health/readiness, auth, RBAC, API/DB contract, UI smoke and authenticated LiveKit media E2E.
- Use **expand–migrate–contract**: backward-compatible schema addition, safe backfill, validation, then later removal of obsolete fields.
- Validate migration dependencies and integrity on a clone; prove backup creation and restore rehearsal, plus rollback or forward-recovery plan.
- Keep deployment and feature activation separate. **Gate A** permits safely deploying disabled code only after compatibility, safety, CI and recovery gates. **Gate B** permits activation only after all connected-media, authorization and entitlement tests pass.

## 6. Release gate and production protection

Before any authorized production action verify:

- Correct candidate SHA/digest and all mandatory CI checks PASS.
- Staging serves the exact candidate artifact and critical authenticated E2E PASS.
- Live camera connected-session revocation on OFF/expiry, restart cleanup and isolation PASS.
- Migration readiness, RLS, backup, restore and recovery evidence PASS.
- No active exams will be disrupted; production health baseline recorded.
- Canary/pilot cohort, monitoring thresholds, rollback owner and rollback commands documented.

If a critical item is missing, stale, failed or SHA-mismatched, decide **NO-GO**. A successful CI run is not proof of LiveKit media safety. Application rollback does not automatically reverse database changes. After explicit authorization and all gates PASS, deploy same artifact, smoke-test, pilot, monitor, and expand only when healthy.

## 7. Performance targets (not guarantees)

| Stage | Optimization target |
| --- | --- |
| Parallel CI | 2–5 min |
| Automated staging deployment | 1–3 min |
| Critical smoke and E2E | 3–8 min |
| Evidence release gate | 1–2 min |
| Production deploy and health | 2–5 min |

Total nominal target: **9–23 minutes**, excluding necessary canary observation, complex migrations and real-media test setup. On constrained production VPS, run builds/tests in GitHub Actions or isolated runners, not on the live exam host.

## 8. Agent execution contract

1. Discover existing implementation, tests, blockers and release evidence.
2. Build a gap matrix by RBAC/CRUD/API/database/frontend/backend/LiveKit.
3. Fix verified gaps in a dedicated branch; never silently alter main.
4. Run targeted tests, then integration and release gates; preserve artifacts.
5. Update checkpoint and open a reviewable PR with precise evidence.
6. Never claim a production deployment or activation that has not been performed and verified.

**Progress report format:**

- **SHA / artifact digest:** exact identifiers
- **Newly verified:** only new PASS items with evidence links
- **FAIL / BLOCKED / UNKNOWN:** exact reason and log
- **Next action:** highest-priority executable step
- **Release decision:** GO or NO-GO, justified by gates

**Non-negotiable:** protect exam integrity and production stability; no fabricated PASS, no blind reruns, no duplicate reports and no unsafe shortcuts.
