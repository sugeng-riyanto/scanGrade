# ScanMeet: Freebuff ↔ ChatGPT engineering handoff

This document defines an asynchronous collaboration protocol through GitHub.
It is not a claim that agents communicate directly, staging is deployed, or
production is approved.

## One source of truth

- Target integration: `integration/livekit-secure-main-20261024` (PR #9).
- Feature implementation owner: Freebuff (code and staging VPS/DNS/.env).
- Independent review, regression tests and CI triage: ChatGPT (GitHub).
- Production release authority: repository owner. Never merge to `main`
  or deploy production without owner authorization.
- Staging credentials are kept outside GitHub and never pasted in comments.
- See [PR #19 — ScanMeet acceptance matrix](https://github.com/sugeng-riyanto/scanGrade/pull/19) for acceptance criteria; its document is merged separately.

## PR handoff template

Paste into a PR description or comment when implementation is ready:

```markdown
### HANDOFF: IMPLEMENTED → REVIEW REQUESTED
- Owner:
- PR / branch / head SHA:
- Scope (exact endpoints/routes/files):
- Behavior and threat model:
- DB migration and rollback:
- Flags and default-OFF confirmation:
- Local commands and results (pass/fail/skip):
- Staging URL: [sanitized host only]
- Required secrets: [names only, never values]
- Known risks / unresolved issues:
- Specific reviewer questions:
```

ChatGPT reviews code and sends:

```markdown
### REVIEW: CHANGES REQUESTED | APPROVED FOR STAGING
- Reviewed head SHA:
- Findings: [severity, affected file/line, reproducer]
- Authorization and cross-school tests:
- Exam continuity (timer/autosave/submission):
- Privacy and recording restrictions:
- CI status for this exact SHA:
- Suggested minimal patch / PR:
- Blockers remaining:
```

Freebuff replies after fixes with:

```markdown
### FIXED: READY FOR RE-REVIEW
- New SHA and linked issue/PR:
- Each finding → remediation/test:
- Targeted tests:
- Full CI state:
- Staging evidence and redacted artifact references:
```

ChatGPT independently verifies the new SHA and gives the exact status:
`CI_PASS`, `STAGING_PASS`, `BLOCKED` or `CHANGES_REQUESTED`.
Only **owner** may authorize merge to main.

## Parallel ownership boundaries

| Workstream | Freebuff | ChatGPT | Release boundary |
| --- | --- | --- | --- |
| Exam mode | Server-side toggle, UI, staging accounts | Auth/security regression, CI/CDP review | PR #9 and staging 12/12 |
| General meetings | Room/membership/UI implementation | Cross-school matrix/tests | Separate feature PR |
| Background studio | Effects, licensed assets, upload | Media permission, performance, payload tests | Separate feature PR |
| Local recorder | Receive-only viewer, OBS/browser output | Role/privacy/storage checks | Separate feature PR |

Do not modify the same file concurrently unless the PR handoff declares
ownership. Coordinate through PR comments and reference exact commit SHAs.

## Immediate queue (re-check GitHub before acting)

1. PR #9: integration CI checks; real staging media 12/12 and webhook 7/7.
2. PR #10: conflict resolved in `08af51e`, verify latest CI and retain CDP
   runner/workflow as additive changes only.
3. PR #11: conflict resolved in `5330899`, check overlap with PR #10 and
   avoid duplicate drivers without documented rationale.
4. PR #19: independent acceptance matrix; merge after CI and review.
5. Teacher ON/OFF persistent per-exam control: ensure server authority,
   immediate denial of join on OFF, room cleanup, no exam state changes.
6. General meeting, virtual background and local recording: different PRs.

## Staging guardrail

Do not use `scangrade.web.id` or any of its subdomains as a staging URL
unless explicit allowlisting is implemented with robust isolation and reviewed:
the current isolation gate denies the production hostname suffix. Prefer an
independent staging domain/host. Use separate LiveKit and database projects.
A 1 GB production VPS is not the test browser runner.

7/7 green CI on an integration SHA **does not equal** 12/12 media E2E.
Record media pass/fail/skipped for each row, webhook 7/7, browser/CDP version,
timestamp, deployed SHA, and sanitized staging host.

## Release criteria

- All required CI workflows PASS on the same exact head SHA.
- No unresolved high/critical security findings.
- Exam continuity and isolation verified.
- Real staging evidence recorded (not inferred from local mocks).
- Rollback instructions confirmed.
- Owner approves main merge and deployment in writing.
