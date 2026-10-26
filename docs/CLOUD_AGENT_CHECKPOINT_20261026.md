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
| PR #39 | `df0c0bce71d618cd782f0110d6ad159443b30126` — ready for review, 6/6 workflows green |
| PR #40 | `ec517080da13f4cd21f8149ecdecbf23a4d5e5ea` — ready for review, 5/5 green |
| PR #41 | `f2a1050e8367160138e04c66253258f6e2e68de9` — draft |
| PR #42 | `2ff0b2508b77c49c4e6af5e292d5edb5e91117ba` — draft, the integration of #39 + #40 + #41 |

`main` has not moved. Everything from #39 upward was opened **after** the handoff was
written, so its "#37 and #38 are the open work" reading is out of date: the ScanMeet
security set is now five branches, and #42 is the only one carrying all three fixes
together.

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

## PR #39 — a real bug on `main`, reviewed and verified

PR #39 is not a tidy-up. On `main` the Super Admin NPSN lookup gate reads
`re.fullmatch(r"\\d{8}", npsn)`: the doubled backslash makes the regex *literal
backslash followed by eight d's*, so it matches **nothing**. Measured by
extracting the literal from the route's AST the way Python reads it:

```text
main's pattern literal: '\\d{8}'   ->   re.fullmatch(..., '12345678') is None
                                        re.fullmatch(..., '00000001') is None
```

So `/super-admin/scanmeet-schools`, merged in PR #36, could never resolve a
school. Its blast radius is small rather than none: the route is `@_sa_required`
(Super Admin only) and both ScanMeet switches are OFF, so no student ever saw it.
PR #39 changes the pattern to `[0-9]{8}` — correctly ASCII-only, which also
rejects full-width digits that `\d` would have accepted.

The guard is worth keeping because of how it reads the pattern: it walks the
route function's AST for the `re.fullmatch` call instead of restating a constant,
so a test cannot agree with itself while production carries the wrong regex. I
checked that claim rather than assuming it — run against `main`'s file the tests
**fail** (3 failures), and against PR #39's file they pass (10), with the wider
file at 27 passed.

A repo-wide AST sweep for this defect class (a string containing `\\` followed by
a regex class letter) returns **exactly this one instance**. The other two hits are
Windows paths inside fixture source in `tests/unit/test_shell_path_portability.py`,
where the escaping is correct because the string is Python source being written to
a file. So PR #39 fixes the only occurrence, not the first of many.

Not a blocker, recorded so it is not rediscovered: `generate_code_by_npsn()` also
accepts an NPSN from a form and checks only that it is non-empty. The lookup is a
parameterised Supabase call, so this is a wasted round trip and a worse error
message, not an injection.

## PR #40's own open question, answered behaviourally

PR #40 asks a reviewer to "validate HTTP 202 vs 200 contract on admin-disable with an
already-connected browser, including LiveKit delete failure and cleanup ledger insert
failure". Its test file is *structural* — it reads the source for `202` and
`cleanup_pending` — which cannot distinguish a pending deletion from an unrecorded one
and would keep passing if the branch were rearranged to return 200.

tests/unit/test_scanmeet_revocation_http_contract.py (local commit `c9c86ecd`, branch
`test/scanmeet-revocation-http-contract-20261026`) calls the view and asserts the
response: 202 with `cleanup_pending` on a failed room deletion, 202 with
`cleanup_unrecorded` when the queue write also fails (and when it returns nothing),
200 without a `warning` on a clean revocation, and 202 when the session list cannot be
read. **5 passed.** Run against PR #39's route the two failure cases read
`assert 200 == 202` — the defect is real and the test names it.

The browser half is **not** done and is not claimed: a 202 warns that media deletion is
unconfirmed; it is not evidence that a pupil's camera stopped. That needs two real
browsers against a real SFU.

## PR #41 read, not run

The new `live_expiry_cleanup` module in `app/services/` is the destructive one, so the question is what it
does when it cannot tell. It is fail-closed in the right places: an unreadable or
ambiguous entitlement read raises and skips that school, a DB error during the sweep
returns a report with no destructive work, and it rechecks `_confirmed_expired`
immediately before each row update. It paginates the entitlement query (a fixed
first-page limit would starve every later school forever) and drains sessions in
batches with an explicit no-progress guard.

Two things to keep in view. The recheck is not atomic — a renewal can land between the
check and the update, which the code says out loud and leaves as a staging gate. And
`report["ended"]` counts rows it marked `ended` even when `end_room` then failed, so an
operator reading `ended: 5, cleanup_pending: [s1]` may believe five rooms are dark when
four are. That is a reporting clarity problem, not a safety one, but it is the kind of
number someone acts on.

A hypothesis I checked and dropped: `sweep_expired` compares `valid_until` to an
`+00:00` ISO stamp, which would be wrong for a text column. `075` declares
`valid_until timestamptz`, so the comparison is a real timestamp comparison.

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

0. Merge PR #39 before anyone works on ScanMeet licences: the page it repairs
   cannot find a school today. It enables nothing on its own — the switches stay
   OFF and the route stays Super Admin only. If #40 merges first, merge #42's
   combined tree or re-run the suite afterwards: #39 and #40 both touch
   `super_admin.py`, and two green branches are not a green merge.
0b. PR #37's readiness probe is **still broken on GitHub**. `SAVEPOINT
   scanmeet_readiness` against a `connect_readonly()` session (autocommit) raises
   `25P01`, so `--live` reports a traceback instead of a verdict. Only the local
   branch `fix/scanmeet-readiness-live-transaction-20261026` fixes it; no remote
   branch carries the file at all except #37's.
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
