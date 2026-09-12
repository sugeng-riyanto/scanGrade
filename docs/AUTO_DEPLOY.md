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
from the repo copy, installs the units, enables the timer, and does one test run
so you find out immediately whether it works instead of in two minutes.

**The very first time**, the checkout does not contain the installer yet, so run
the copy that was handed over instead — it pulls the same commit before it
installs anything:

```bash
bash /tmp/sgdeploy2/install-auto-deploy.sh
```

Safe to re-run afterwards: it re-reads the automation from the checkout, so it is
also how you upgrade the logic after pulling a newer commit.

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

- the checkout has local changes (it will not clobber hand edits);
- the update is not a fast-forward (history was rewritten);
- the release ships a migration and no snapshot of the data can be taken;
- `requirements.txt` changed and `pip install` failed;
- the code does not compile;
- the app cannot construct with all of its blueprints and routes;
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

### A migration you paste in by hand is invisible to that check

`supabase/migrations/*.sql` is still applied by hand in the Supabase SQL editor,
and pasting SQL changes no file — so nothing detects it. Run this first:

```bash
scangrade-db-snapshot --label before-025   # snapshot now
scangrade-db-snapshot --list               # what is already there
```

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

Migrations are still manual: SQL in `supabase/migrations/` is applied by hand in
the Supabase SQL Editor. Deploying code that expects a column before the column
exists is the failure mode to avoid. Take a snapshot before you paste it.

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
