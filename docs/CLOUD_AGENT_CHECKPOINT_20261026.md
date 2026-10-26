# Cloud continuity checkpoint — 2026-10-26

One page for whoever picks this up next: what a Cloud agent re-measured, what it
changed, what it could not do, and the next action that is safe without a human
at a console.

This file is a **record of a run**, not a licence. Nothing here approves a
migration or a release.

## The commit everything below was measured on

| what | value |
| --- | --- |
| `main` | `16c08c2c1e0ccc9f7c010cc5bace950110749f90` |
| tree of `main` | `34871054a9522ecbfad6d44823b2f1fa58dd7493` |
| re-read from | `git ls-remote` **and** a full (non-shallow) clone, 2026-10-25 |
| PR #37 (draft) | `bde0385deb157bb8da7df167ecf131e05ed3c61e` |
| PR #38 (draft) | `2857be540306cf5d0d2ce5657af4cf187015d7a1` |

The Cloud workspace that this chat started from was **not** this commit. It was
missing 70 tracked files — every `supabase/migrations/07*` from 070 onward, the
whole ScanMeet/live surface, and the constitution document — and 124 more
differed. It was replaced with the real `main` tree before any test ran, and the
import was kept as the branch `backup/cloud-import-1bf9333` (local only).

**Any evidence produced from that stale tree would have been about code that is
not on `main`.** That, rather than any single test, is the reason to re-verify the
workspace before trusting a Cloud agent's numbers.

## Test evidence (Linux VM, Python 3.12.3)

- `python -m pytest tests/unit` on `main`: **11 541 passed, 51 skipped, 0 failed**
  in 579.78 s. The skips are properties of this box, not of the code: no Chrome
  (`SG_CHROME` unset) and no interpreter below the project floor.
- `bash deploy/theme_gate.sh` on the fix branch below: **exit 0**, including
  `doc links: 2162 reference(s) checked, 490 exempt, 0 unresolved`,
  `i18n coverage: OK`, and `merge runbook: 62 labelled row(s) agree`.
- GitHub Actions, read from the run list: `unit suite`, `region guard`,
  `skipped-path coverage`, `landing quality control` and `browser gate` are green
  on both draft PRs. `main`'s own unit-suite run was **cancelled** (superseded),
  not failed — so `main`'s green reading rests on those branch runs.

## What this pass changed

`fix/scanmeet-readiness-live-transaction-20261026` (one commit, based on PR #37's
head): the `--live` half of PR #37's readiness probe could not report a verdict.

It opened its session through `connect_readonly()` in `deploy/apply_migration.py`,
which sets `autocommit=True`. PostgreSQL refuses a savepoint outside a transaction
block (`25P01`), so the probe's **first** statement raised, its own cleanup raised
in `finally`, and the operator got a traceback where `HOLD:` or `PASS:` belonged.

Reproduced against a real PostgreSQL 16 before and after:

| session shape | before | after |
| --- | --- | --- |
| read-only + autocommit, tables absent | raised `NoActiveSqlTransaction` out of the function | 8 findings, no exception |
| read-only + autocommit, 073/074/075 postconditions present | unreachable (raised first) | no findings → `PASS` |

The unit stub is why this reached review instead of a test: it returned quietly
for `SAVEPOINT` statements, which no server does. It now raises `25P01` the way a
real session does, and a second test reads the function's own string literals so
the scaffolding cannot return.

## Deployment state — reported, not re-measured here

This agent has no route to the VPS or the production database, so the following is
the operator's last report and is **not** verified by anything in this file:

- production serves healthy commit `4e455df`;
- `16c08c2` is quarantined (schema gate refusal) and will not self-retry — see
  `docs/AUTO_DEPLOY.md` for how a release request is consumed;
- migrations 073, 074 and 075 are absent from production;
- both ScanMeet switches are OFF. They default to OFF in
  `app/services/live_policy.py` (both must be true), and that is not evidence
  about the environment's variables.
- no successful isolated restore has been demonstrated.

## Blockers

1. **No GitHub credentials in this Cloud workspace.** `git push` fails with
   `could not read Username` and `gh auth status` reports no login. Every commit
   this agent made is local. A durable checkpoint in GitHub is therefore a commit
   waiting to be pushed, not a pushed commit — that is a permission gap, not a
   finding about the repository. Read access works because the repository is
   public.
2. **No production credentials, by design.** Migration execution, backup proof
   and the one-shot release request are operator actions. This agent can prepare
   and verify; it must not be given a database URL to "just run it".
3. **The quarantine is a person's decision.** A release request through the super
   admin deploy status page is the only sanctioned path, and only after the
   schema gates pass against the verified target.

## Next safe action

1. Push `fix/scanmeet-readiness-live-transaction-20261026` onto PR #37's branch
   (or open it against `main`), so the readiness tool PR #37 describes can be run
   at all. Running it from the old production checkout proves nothing.
2. Prove a restorable backup — an archive that exists is not a restore. Until
   that passes, migrations stay unapplied.
3. Dry-run 073, then 074, then 075 with the repository's own migration tool
   against the verified production target; the runner already refuses a file that
   contains its own `COMMIT` and applies each file in one transaction.
4. Only after that: one release request, then `/health` SHA, migration presence,
   unchanged exam counts and both switches still OFF.
