# Automated deployment (pull-based)

Pushing to `main` reaches production without anyone opening a console.

The VPS polls `origin/main` every two minutes. When it finds a new commit it
pulls, verifies the code, and reloads the app gracefully. If the new release does
not come up, it puts the previous commit back and restarts that.

Nothing on GitHub needs a credential, and the server needs no inbound SSH: the
box pulls, so the trust boundary stays where it was.

## Install (once, as root on the VPS)

```bash
bash /opt/scangrade/deploy/install-auto-deploy.sh
```

It fast-forwards the checkout first, then installs `/usr/local/bin/scangrade-deploy`
and `/usr/local/bin/scangrade-db-snapshot` as **launchers** (see below), installs
the units, enables the timer, and does one test run so you find out immediately
whether it works instead of in two minutes.

**The very first time**, the checkout does not contain the installer yet, so run
the copy that was handed over instead — it pulls the same commit before it
installs anything:

```bash
bash /tmp/sgdeploy2/install-auto-deploy.sh
```

Safe to re-run afterwards, but you should not need to: the runner it installs is
not a copy of anything, so a fix to the deploy logic reaches the box the same way
the rest of the code does.

## The runner is never a copy

The installed `/usr/local/bin/scangrade-deploy` is `deploy/entrypoint.sh`, rendered
with this checkout's path. It execs `deploy/scangrade-deploy.sh` **from the
checkout**, choosing its target by the name it was installed under. So what root
runs is always the commit the checkout is on: pushing a fix to the deploy script
puts it in effect on the next tick, with nobody opening a console.

It used to be installed as a copy instead, and a copy is a snapshot:

* every later fix to the gates — a corrected probe, a new rollback path — stayed
  on GitHub while the timer kept deploying with the logic of whatever commit was
  current the day it was installed, and nothing compared the two, so the drift
  was invisible;
* the only way to deliver a script fix was to re-run the installer as root, which
  is the manual step automatic deployment exists to remove;
* `/usr/local/bin/scangrade-db-snapshot` was quietly broken from that path: the
  wrapper derives the checkout from its own location, so the copy looked for
  `/usr/local/.venv/bin/python` and refused to take the snapshot at exactly the
  moment one was wanted.

**A stale copy now refuses to run.** Gate 0 of the deploy compares the file it is
running with `deploy/scangrade-deploy.sh` in the checkout *before* it fetches or
merges anything, and stops with exit 14 if they differ:

```
REFUSING: this is an installed COPY of the runner, not the checkout's
    running : /usr/local/bin/scangrade-deploy
    checkout: /opt/scangrade/deploy/scangrade-deploy.sh
    fix once, as root:  bash /opt/scangrade/deploy/install-auto-deploy.sh
```

A copy that still matches the checkout byte-for-byte is the same code and runs
normally, so a box installed before this change keeps deploying until its runner
is genuinely out of date. When you see exit 14 (or the unit in
`systemctl --failed`), re-run the installer once and it is gone for good.

**A copy installed before this change cannot refuse anything**, because the check
arrived with this commit — that box has no launcher and no Gate 0, so it goes on
deploying with the logic of the day it was installed, silently, until the
installer is re-run. If your VPS was installed earlier, do that once:

```bash
bash /opt/scangrade/deploy/install-auto-deploy.sh
```

## Operating it

| I want to… | Command |
|---|---|
| see the last smoke test | `journalctl -u scangrade-deploy.service -n 60 --no-pager` |
| watch deploys as they happen | `journalctl -u scangrade-deploy.service -f` |
| see when the next check is | `systemctl list-timers scangrade-deploy.timer` |
| deploy right now, without waiting | `systemctl start scangrade-deploy.service` |
| **freeze deploys** (e.g. exam week) | `touch /etc/scangrade-deploy.pause` |
| resume | `rm /etc/scangrade-deploy.pause` |
| stop deploying automatically | `systemctl disable --now scangrade-deploy.timer` |
| see the last release that stuck | `cat /var/lib/scangrade-deploy/last-deploy` |

The freeze file is the one to reach for during exams: the timer keeps ticking,
but the script exits immediately, so nothing restarts while students are working.

## The readability gate

Before the app is reloaded, every release is checked for templates that would be
unreadable in either theme (`deploy/theme_gate.sh`, which runs
`tests/unit/test_dark_theme_contrast.py`). The templates are written in light
mode — `text-slate-600` on `bg-white` is the default shape of a card here — and
`base.html` remaps those utilities onto the theme tokens, so a component that is
readable in light mode stays readable in dark mode. Three things break that
silently, and none of them errors:

* a utility used in a template is left out of the remap, so it keeps its light
  colour on a dark page (a badge ends up light-on-light);
* a new template renders its own `<html>` instead of extending `base.html`, so it
  inherits none of the remap;
* a **tint and the text painted on it** are a sub-AA pair in light mode, which is
  what ships: `bg-amber-100 text-amber-600` — a warning badge — is 2.86:1, and no
  remap is involved, because in light mode the compiled palette *is* the theme.
  `base.html` therefore carries an explicit correction per pair.

That last one is checked against `app/static/css/tailwind.css`, so the gate also
notices a class a template uses that the last `npm run css:build` did not emit —
the page would render it as no colour at all.

No other gate can see either one. The app still constructs, every page still
answers `200`, and a screenshot taken in light mode looks correct. So it is its
own gate, and it runs **before** the reload — while rolling back still costs
nothing.

The same check is available as a pre-commit hook, which fails the commit rather
than the release:

```bash
bash deploy/install-git-hooks.sh      # once per clone; hooks are not cloned
```

It runs only when a template or the stylesheet is staged, and
`git commit --no-verify` skips it.

| Result | Outcome |
|---|---|
| every template and utility is readable | deploy |
| a template, utility or tint pair is unreadable in either theme | roll back (exit 13) |
| the gate could not run at all (exit 2) | **warn only**, and say so in the journal |

The last row is deliberate: a missing interpreter or a `pytest` that never got
installed is a fault in the gate, not in the release, and a broken checker must
never be able to take the site down. It is logged loudly instead, and
`install-auto-deploy.sh` proves the gate runs at install time so that warning has
no reason to appear.

## The gate on the published numbers

The landing page publishes a capacity table — concurrent students against p50,
p95 and error rate — measured against this deployment. Nothing kept it true. The
numbers were written by hand and the page carried on advertising them while the
machine underneath changed, which is how 46,698 and 74,923 requests over a
ten-minute peak stayed on the page with nothing in the repository able to
produce either figure.

`deploy/claims_gate.py` closes that: on every release, after the reload, it
re-measures **the rung the page itself advertises** and compares. It reads the
claim out of `app/templates/landing.html` rather than from a constant in the
gate, so editing the page into a bigger promise is what has to be defended.

It runs after the reload because the thing being measured is the code that is
now serving; a probe before the reload would measure the release being replaced.
That is also why a confirmed divergence goes through the shared rollback path —
resetting the checkout without reloading would leave the rejected release
running and fail the same way on every later tick.

What it measures is narrow, deliberately:

* **the lowest advertised rung only.** Loading 500 sessions at deploy time would
  cost the students the deploy is for. If the box cannot hold the smallest
  promise on the page, the rest of the table is not worth measuring.
* **the worst page endpoint**, not a median over everything. A 50-way login burst
  is measured too, but it is a different claim, and letting it into the median
  turns this into a login test the smoke test already runs.
* **a distribution, not endurance.** The published run lasted minutes; the probe
  lasts `CLAIMS_DURATION` seconds, so it cannot see degradation over time.

It says all three of those in its own output, so a passing run cannot be read as
"the whole page is verified".

| Result | Outcome |
|---|---|
| measured, and the page still describes this box | deploy |
| measured, and it does not (confirmed twice) | roll back **if armed** |
| diverged once, clean on the confirmation run | deploy — contention, not a stale claim |
| could not measure (exit 2) | **warn only**, and say so in the journal |

The two-strike rule and the `2x` latency slack exist because a measurement on a
shared box is not a fact about the code alone. They are also bounded: the slack
is tighter than a gap that has actually been observed here (a probe measured a
worst-page p50 of 1464 ms against a published 620 ms, and the first version of
this gate used `3x` and passed it).

It declines to measure, rather than guessing, when the box is already busy — the
same 1 vCPU serves real students, and loading it during a live exam would both
disturb the exam and produce a number that means nothing. It judges the *best* of
several `/health` samples, so a box that has just been reloaded is not mistaken
for a busy one.

### Arming it

`/etc/scangrade-claims.conf` (root-only) holds the base URL, the roster path, the
probe size and `CLAIMS_ENFORCE`. The installer proves the plumbing with
`--check`, runs one real probe, and arms the gate **only** if the page matches
what it measured — the same discipline as `SMOKE_ENFORCE`, and for the same
reason: a gate armed against a page that does not match would reject every
release.

```bash
# what it needs: one account per session
cd /opt/scangrade && .venv/bin/python provision_loadtest.py 60 2
bash /opt/scangrade/deploy/install-auto-deploy.sh     # re-run to arm

# measure by hand, without deploying anything
.venv/bin/python deploy/claims_gate.py --check
.venv/bin/python deploy/claims_gate.py --base https://scangrade.web.id

# every run, kept
cat /var/lib/scangrade-deploy/claims/history.jsonl
```

The roster is required and lives outside git (`.freebuff/lt_roster.json`), so a
rollback cannot remove it. No roster means `cannot measure`: the gate says so on
every release rather than passing silently.

## The smoke test that gates a release

After the app is reloaded, the deploy signs in as each of the four roles and
opens the pages that matter (`deploy/smoke_test.py`). A port answering `200` only
says gunicorn is up; it says nothing about whether login still works, whether a
page `500`s for one role, or whether an RBAC guard was loosened — and "deployed
but teachers cannot open anything" is exactly what a reachability probe waves
through.

It checks three things:

| | What it proves |
|---|---|
| four logins | session cookies, both login routes, both auth stores |
| ~32 pages | every role's landing page, admin console and work queues render |
| 6 refusals | admin/guru/murid cannot open another role's area |

### Credentials

They are deployment-specific and are passwords, so they live in
`/etc/scangrade-smoke.conf` (mode 0600, created by the installer) and never in
the repo:

```sh
SMOKE_BASE_URL="https://scangrade.web.id"
SMOKE_ENFORCE="true"
SMOKE_SUPER_ADMIN="superadmin@scan-grade.app:superadmin123"
SMOKE_ADMIN_SEKOLAH="admin_smp@scan-grade.app:demo123"
SMOKE_GURU="guru_mtk_smp@scan-grade.app:demo123"
SMOKE_MURID="siswa2_smp@scan-grade.app:demo123"
```

Each value is `email:password` (split on the *first* colon). `SMOKE_BASE_URL`
must be `https://`: production sets `SESSION_COOKIE_SECURE`, so over plain HTTP
the session cookie is dropped and every login would look broken.

Check a change without deploying anything:

```bash
set -a; . /etc/scangrade-smoke.conf; set +a
/opt/scangrade/.venv/bin/python /opt/scangrade/deploy/smoke_test.py
```

### What it will and will not roll back

`SMOKE_ENFORCE=true` arms the rollback, and the installer only sets it after
proving that **every** configured account signs in — a config with a stale
password must never be able to reject a good release. Once armed:

| Result | Outcome |
|---|---|
| a page returns `5xx` after a successful login | roll back |
| a role can open another role's area | roll back |
| **no** role can sign in | roll back — one changed password cannot explain four |
| one role cannot sign in | warn only; the others still gate the release |
| `SMOKE_BASE_URL` unreachable | skip, exit 2 — a DNS or nginx problem is not fixed by rolling back code |

The line between the last three is the whole point: the dangerous case still
rolls back, and a stale credential cannot.

## What it refuses to do

The script takes **no arguments**. It is the only thing root runs unattended, so
it is not a general-purpose command runner — the branch, the repo and the paths
are fixed in the script itself.

It also stops instead of guessing when:

- it is running from an installed copy that differs from the checkout (exit 14)
  — see [The runner is never a copy](#the-runner-is-never-a-copy);
- the checkout has local changes (it will not clobber hand edits);
- the update is not a fast-forward (history was rewritten);
- the release ships a migration and no snapshot of the data can be taken;
- `requirements.txt` changed and `pip install` failed;
- the code does not compile;
- the app cannot construct with all of its blueprints and routes;
- a template would be unreadable in either theme;
- the app does not answer `200` on `127.0.0.1:8000` afterwards.

Each of those is a distinct non-zero exit, visible in the journal. On the last
two it rolls the checkout back first, so the site keeps serving the release that
was working.

## Why a reload and not a restart

`scangrade.service` carries `ExecReload=/bin/kill -s HUP $MAINPID`. SIGHUP makes
gunicorn retire its workers gracefully: in-flight requests get up to
`graceful_timeout` (30 s) to finish. A hard `restart` would cut off whatever a
student was in the middle of, which is the opposite of what an automatic deploy
should risk.

If `ExecReload` is ever removed from the unit, `systemctl reload` fails and the
script falls back to `restart` — still correct, just blunt. `tests/unit/test_auto_deploy.py`
pins that line so the fallback cannot become the silent default.

## Rolling back with the data, not just the code

Git can put the code back. It cannot put the data back. So a release that changes
`supabase/migrations/` is **not allowed to start** without a snapshot taken while
the old schema is still the one being served:

```
release changes supabase/migrations — taking a data snapshot first
snapshot: /var/backups/scangrade/scangrade-db-20260912T134316Z-<commit>.tar.gz
```

If that snapshot cannot be taken, the release is not deployed (exit 12). It runs
before the merge, so a refusal leaves nothing half-applied — the checkout is
simply still on the commit that was working.

On success the archive is named in the journal, and if the release later fails
verification the rollback message prints the command that puts the data back, at
the moment someone is already reading the log.

### Applying a migration

Pasting SQL into the Supabase SQL editor is how this was done until
`024_fix_pengumuman_school_id_type.sql` was wrong twice in a row: an `integer`
holding `1` cannot be cast to `uuid`, and Postgres refuses a subquery inside
`ALTER COLUMN ... TYPE ... USING`. Both were found mid-edit, once after the
foreign key had already been dropped.

`deploy/apply_migration.py` runs the file for real and rolls it back first, so the
mistake happens where it costs nothing:

```bash
python deploy/apply_migration.py supabase/migrations/025_x.sql            # trial, then roll back
python deploy/apply_migration.py supabase/migrations/025_x.sql --commit   # trial, then apply
python deploy/apply_migration.py --status                                 # what has a record
python deploy/apply_migration.py --verify                                 # what is really in the schema
```

The trial is not optional — `--commit` runs it first in the same invocation. It
prints the schema delta the migration would make, runs the file a second time to
show whether applying it twice is safe, and *verifies* the rollback on a fresh
connection: a difference afterwards is an error, not a warning.

It needs `DIRECT_URL` (the session-mode pooler), because only a real transaction
can be rolled back, and it refuses to run unless the project in `DIRECT_URL`
matches the one in `SUPABASE_URL`. `--commit` takes its own snapshot first, and
records what it applied in `/var/lib/scangrade-migrations`.

`--verify` needs none of that. It reads, opening the session read-only, and
reports for every file in `supabase/migrations/` which of the objects the file
declares are actually in the database. Absence has four meanings and only one of
them is a problem:

| verdict | meaning |
|---|---|
| `IN` | every declared object is present |
| `superseded` | another file drops that name, so the object was replaced |
| `PARTIAL` / `OUT` | some or all declared objects are missing — the file did not take effect |
| `no objects` | nothing checkable: a data-only file, or a placeholder |

It exists because `--status` cannot answer the question people actually ask. On
a checkout where migrations were applied by hand, every file reads `no record`,
which means "unknown" and not "not applied". It needs no privilege either —
reading a ledger directory that does not exist is not a privileged operation.

Objects created by dynamic SQL inside a `DO $$ ... $$` block cannot be read out
of a file at all, so they are counted and reported as unreadable rather than
assumed present. A verifier that is confidently wrong is worse than none.

This is what showed that `20260608_fix_rls_policies.sql` never took effect and
`20260608_usage_tracking.sql` was never applied: the first declares policies on
`activation_codes`, a table no migration creates, so the SQL editor rolled the
whole file back — taking its own `ADD COLUMN` statements with it.

### A migration pasted in by hand is invisible to that check

If you still paste SQL by hand, pasting changes no file — so nothing detects it.
Run this first:

```bash
scangrade-db-snapshot --label before-025   # snapshot now
scangrade-db-snapshot --list               # what is already there
```

That command is `deploy/scangrade-db-snapshot.sh` in the repo, and it runs from
the checkout as well as from `/usr/local/bin`:

```bash
sudo bash /opt/scangrade/deploy/scangrade-db-snapshot.sh --label before-025
```

It is root-only either way: the archives hold personal data, and `--restore`
overwrites live data.

### Putting the data back

```bash
scangrade-db-snapshot --restore /var/backups/scangrade/<archive>
scangrade-db-snapshot --restore <archive> --dry-run    # report only
```

It reads every writable table in `public` through the PostgREST API with the
service key the app already has. No database password, no Supabase personal
access token — nothing to go and find on the day it is needed. Rows are upserted
by primary key, so running it twice is the same as running it once.

Before it writes anything it takes a snapshot of the **current** state, and
refuses to continue if it cannot: the state being overwritten is the only copy of
it.

```
   capturing the current state first, so this restore is itself undoable...
   current state saved as scangrade-db-...-pre-restore.tar.gz
```

### What it restores, and what it cannot

| | |
|---|---|
| archives | `/var/backups/scangrade`, mode `0600` inside a `0700` directory, newest 5 kept |
| order | parents before children, derived from the foreign keys in the PostgREST spec |
| a cycle | `profiles.class_id` → `classes` and `classes.teacher_id` → `profiles` cannot both be satisfied, since Postgres checks each statement as it runs. Those four back-edges go in as NULL and are re-linked afterwards |
| identity keys | `notifications` and `notification_recipients` have `GENERATED ALWAYS AS IDENTITY` primary keys, which the API will not accept. Rows that still exist are **reverted in place**; a row that is gone cannot be recreated through the API and is reported as such |
| auth users | **not** restored — GoTrue never exposes password hashes. `auth_users.ndjson` holds the ids and emails, for recreating an account and resetting its password |
| the schema | **not** restored. This writes rows into tables that already exist; a migration that drops a column is not undone by it |
| `updated_at` | re-stamped by the database's own triggers, so restored rows carry a newer timestamp. Every other value comes back identical |

If anything did not come back, the restore exits **3** and prints `INCOMPLETE`
with the tables and the reason. A restore that reports success while rows are
missing is worse than one that fails.

The archives hold names, phone numbers and exam answers, so they stay root-only
and are rotated. An unsecured copy of that data is a breach in its own right —
prune sooner (lower `--keep`) if your retention policy is shorter than five
releases.

## What a deploy still does not do

**The Tailwind stylesheet is a committed build artifact.** The VPS never runs
`npm`. If a change touches templates, run `npm run css:build` locally and commit
`app/static/css/tailwind.css` with it, or the new classes will be missing in
production.

Migrations are still applied deliberately rather than by the deploy: nothing in
`supabase/migrations/` runs itself, so deploying code that expects a column
before the column exists remains the failure mode to avoid. Use
`apply_migration.py` (above) — it trials the file, rolls it back, takes a
recovery point, and only then applies it.

`--status` reports what has a record, but a migration applied before that tool
existed has none, and it says so rather than guessing. `--verify` answers from
the schema instead: it names the objects a file declares that are not there, and
distinguishes "this file never took effect" from "a later file replaced this".

## Rolling back by hand

```bash
cd /opt/scangrade
runuser -u scangrade -- git reset --hard <commit>
systemctl reload scangrade

# and, if that release changed the schema:
scangrade-db-snapshot --restore /var/backups/scangrade/<archive>
```

Then fix `origin/main` — otherwise the next tick will try the same bad release
again. Freeze first if you need time: `touch /etc/scangrade-deploy.pause`.
