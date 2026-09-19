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

### If that attempt does not take — the one-command way

Run from a shell that is not root, `install-auto-deploy.sh` prints a banner and
exits 2 **before touching anything**. In a screenful of output that reads as done,
and the box then keeps serving a **copy** of the deploy script installed on an
earlier day — it deploys every release while running no gate at all, and it has no
`/etc/scangrade-claims.conf`, no `/etc/scangrade-perf.conf` and no load-test
roster. Nothing on the box goes red: the timer keeps working, which is exactly why
this state can survive a report that the installer ran.

`deploy/arm-auto-deploy.sh` exists to make that impossible to mistake. Run it as
yourself; it elevates once, keeps the installer's whole output in
`/tmp/installer.log`, and states what the box is running *before* and *after*:

```bash
bash /opt/scangrade/deploy/arm-auto-deploy.sh --check   # what is armed; changes nothing
bash /opt/scangrade/deploy/arm-auto-deploy.sh           # arm it — asks for your password once
```

`--check` exits 0 only when all four gates have what they need, and names every
missing piece otherwise, so a missing launcher, conf or roster is never reported
as armed. It reads the installed file the way `/super-admin/deploy-status` does:
a launcher that execs the checkout is the arrangement, `@REPO@` still in the file
means it was installed but never rendered, and anything else is a **copy** — whose
missing gate blocks are printed by name, so "no theme gate, no claims gate, no
performance gate, no quarantine" is a sentence an operator reads rather than a
conclusion they have to reach. A password is never requested for a roster that
cannot be parsed, and nothing is written at all in `--check`.

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

### A successful release re-renders the installed launcher

`/usr/local/bin/scangrade-deploy` and `/usr/local/bin/scangrade-db-snapshot` are
rendered from `deploy/entrypoint.sh`, and until now nothing re-rendered them: a
launcher left behind by a fix to the template stayed behind until somebody ran the
installer as root — the manual step this automation exists to remove. The last step
of a release that passed every gate now rewrites both from the checkout it just
verified, so an installed launcher cannot lag its template.

What that heals, on the next successful release, with no root step:

* a **launcher rendered from an older `deploy/entrypoint.sh`.** It still runs the
  checkout, so nothing is broken — but whatever changed in the template is not in
  effect, and the deploy-status page calls this `launcher_stale`;
* a **missing** launcher or snapshot command;
* a **copy that still matches the checkout byte-for-byte** — Gate 0 lets it through
  precisely because it is the same code, and it becomes a launcher here.

What it does **not** heal, and why: a **copy that has drifted** from the checkout —
still the likeliest state of an old box, and the one `exit 14` names. That copy
refuses before any gate can pass, and the refresh is the last step of a release that
passes, so no release ever reaches it. Nothing inside a file that is not the
checkout's can apply the checkout's logic; that is the one step a file cannot take
for itself, and it is why the installer still exists. The same holds for a copy that
predates Gate 0: it carries no check, so it keeps deploying with the logic of the
day it was installed until the installer runs once.

Three properties, because the installed path is the only thing the timer runs:

* **it never installs a launcher it has not parsed.** The render is checked for the
  `@REPO@` placeholder and with `bash -n` *before* the move, and every failure
  leaves the installed file exactly as it was. A launcher that cannot run is worse
  than a stale one: then nothing can deploy at all.
* **the install is atomic.** The render is staged in the target's own directory, so
  `mv` is a rename on one filesystem and no tick can observe a half-written file.
* **it is skipped when the bytes already match.** Gate 0 compares *content*, so a
  timestamp that moved while the content did not would be a signal that lies.

Two properties of the unit are load-bearing here: it runs as root and is
deliberately *not* hardened (`ProtectSystem`/`NoNewPrivileges` would break a script
that has to write the checkout and restart another unit), which is the only reason
a refresh can write `/usr/local/bin` at all; and `PrivateTmp=true` is a second
reason the render is staged beside the target instead of in `/tmp`.

It cannot take a release down with it: the app is verified and serving by the time
it runs, so a failure is logged and nothing else happens — rolling a working site
back to fix a bookkeeping step would trade the wrong thing. It also does not run on
the rollback path, where the checkout is about to move back and the launcher would
point at logic that no longer runs. The trace is one line per launcher it actually
replaced — two on the first release after this lands, none on every release after
that:

```
launcher refreshed: the deploy runner at /usr/local/bin/scangrade-deploy now renders from /opt/scangrade
launcher refreshed: the snapshot command at /usr/local/bin/scangrade-db-snapshot now renders from /opt/scangrade
```

`git reset --hard` by hand is not a release and does not refresh anything, so after
one of those the launcher is whatever the last *successful* release installed.

## Operating it

| I want to… | Command |
|---|---|
| see the last smoke test | `journalctl -u scangrade-deploy.service -n 60 --no-pager` |
| watch deploys as they happen | `journalctl -u scangrade-deploy.service -f` |
| see when the next check is | `systemctl list-timers scangrade-deploy.timer` |
| deploy right now, without waiting | `systemctl start scangrade-deploy.service` |
| **freeze deploys** (e.g. exam week) | `touch /etc/scangrade-deploy.pause` |
| resume | `rm /etc/scangrade-deploy.pause` |
| see which commit a gate refused, and why | `cat /var/lib/scangrade-deploy/quarantined` |
| retry a quarantined commit once | `touch /etc/scangrade-deploy.release` |
| stop deploying automatically | `systemctl disable --now scangrade-deploy.timer` |
| see the last release that stuck | `cat /var/lib/scangrade-deploy/last-deploy` |

The freeze file is the one to reach for during exams: the timer keeps ticking,
but the script exits immediately, so nothing restarts while students are working.
It is a *human* decision about a window of time. The quarantine below is the
automatic, per-commit sibling of it.

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

**The conf file reaches the gate through the environment, and that is worth
knowing.** The deploy sources `/etc/scangrade-claims.conf` and passes every
`CLAIMS_*` setting as an environment variable; `claims_gate.py` reads them with
`env_default()`. It did not always: the gate originally read only `argv`, so
`CLAIMS_BASE_URL` arrived, was ignored, and every production run ended at
`no --base URL` — exit 2, "could not measure", on a gate that looked installed
and healthy. That is why `/var/lib/scangrade-deploy/claims` had never been created. `tests/unit/test_perf_gate.py` now fails if the installer writes a
setting its gate never reads.

## The gate on the release before this one

The claims gate compares this box with a number printed on the landing page, at
the rung that page advertises. It cannot see a release that costs 40% of every
page's response time while staying inside the published bound — and five of those
in a row are a box that no longer does what it did, each one passing on its own.

`deploy/perf_gate.py` asks that other question. It runs a **small fixed
reference load** — 20 concurrent students for 20 seconds by default — and
compares the result with **the last release that passed**. Same harness, same
box, same load, so the two numbers cancel and what is left is what the release
changed. It is small on purpose: this is the 1 vCPU that serves students, and the
deploy does not get to load it the way a benchmark would. At 20 sessions the box
is far from saturated, so latency tracks per-request cost rather than queueing,
which is the thing a release can change. The advertised rung stays the claims
gate's job.

Three things are compared, because they fail independently: the response time of
the slowest signed-in page (the symptom a student feels), the **bytes** the
heaviest page sends (what a phone on a school connection pays for), and the
**Supabase round-trips** the busiest render spends — read from the app's own
`X-Supabase-Roundtrips` header, which is the *cause* the other two only reflect. A
page can stay exactly as fast while gaining three queries or a 200 KB script, and
that is the release this refuses. Each axis carries a ratio **and** an absolute
grace (1.25x + 8 KiB of payload, 1.25x + 1 query), so a list that honestly got
longer is not mistaken for a leak — a release has to clear both to be refused. The
round-trip number is what the page costs *when it does its work*, not what a cache
hit spent: a warm entry replays the cost it was built with, because otherwise the
busiest student page reads as free on every request. For the same reason the
program's bytecode is dropped before a mutation run — see
`.freebuff/mutate_page_cost.py`.

| Result | Outcome |
|---|---|
| no baseline yet | **deploy**, and this release becomes the baseline |
| no worse, on any of the three, within slack | deploy, and the baseline moves up to this release |
| slower or more expensive (confirmed twice) | roll back **if `PERF_ENFORCE=true`** |
| divergent once, clean on the confirmation run | deploy — contention, not a regression |
| could not measure (exit 2) | **warn only**, and the baseline is left alone |
| the baseline knows a page's cost but this run reports none | **warn only** — a harness that stopped reporting must not retire the payload and query axes in silence |

The baseline is written **only when a release passes**. If a refused release
became the yardstick, the next release would be measured against it and the
regression would be permanent and invisible. A deliberate slowdown (more work per
page, a feature worth its cost) is recorded with `--rebaseline`, so the trade is
stated rather than assumed.

A changed reference load, a box that is already busy, or a divergence a second
probe did not confirm are all `could not measure`. None of them is evidence
against the release, none of them rolls anything back, and the gate says so out
loud on every release — a gate that can only say "could not measure" is a gate
that is off.

### Arming it

`/etc/scangrade-perf.conf` (root-only) holds the base URL, the roster path, the
reference load, the baseline path, the payload and query slacks
(`PERF_BYTES_SLACK`, `PERF_ROUNDTRIPS_SLACK`) and `PERF_ENFORCE`. Unlike the claims gate it
is armed from the first release, and that difference is deliberate: with no
baseline the gate writes one and passes, so arming it cannot reject anything.
From the second release on, a confirmed regression rolls the release back.

```bash
# the reference load needs one account per session, like any probe
cd /opt/scangrade && .venv/bin/python provision_loadtest.py 25 3
bash /opt/scangrade/deploy/install-auto-deploy.sh   # writes the conf, checks it

# measure by hand, without deploying anything
.venv/bin/python deploy/perf_gate.py --check
.venv/bin/python deploy/perf_gate.py --base https://scangrade.web.id

# the reference measurement, and every run beside it
cat /var/lib/scangrade-deploy/perf/baseline.json
cat /var/lib/scangrade-deploy/perf/history.jsonl
```

Measured on this box on 15 Sep 2026, three consecutive runs at the reference load
against production: the first wrote a baseline of p50 1026 ms / p95 1547 ms; the
second compared against it and read p50 735 ms (0.72x), so it passed and moved
the baseline; the third was forced through the refusal path with a slack no
measurement can meet, and left the baseline byte-identical — `md5` unchanged —
which is the property the whole design rests on.

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


## A refused release is quarantined, not retried

A rollback moves the **checkout**, not the branch. So without something else, the
next tick fetches the same commit, sees it is not what is deployed, pulls it, and
walks into the same gate — rejected, rolled back, re-pulled, every two minutes,
for as long as the bad commit sits on `main`. The box reloads gunicorn and the
Celery worker on every lap, the journal fills with one repeated failure, and the
only exit is a human pushing a fix. Nothing about that loop is visible on any
page: the previous release serves throughout.

So a release that a **gate** rejects is quarantined by commit:

```
/var/lib/scangrade-deploy/quarantined
```

Three lines: the full sha of the refused commit, when it was refused, and which
gate refused it (for example `perf gate (slower than the last release that
passed)`).

A quarantined commit is not retried. The next tick prints why and exits `0`
without touching the checkout, so a refused release costs one rollback and then
one journal line every two minutes — not a reload loop.

**It comes back by itself.** The moment `origin/main` moves to another commit the
quarantine is lifted, because a new commit is a new release and the refused one
is no longer the question being asked. The normal fix path needs no command.

**Or release it explicitly**, for the case where the gate was right about the box
and wrong about the release — a busy VPS that made the performance gate diverge,
a smoke password rotated without a commit:

```bash
touch /etc/scangrade-deploy.release     # one attempt, then consumed
```

It is deliberately one-shot: the file is deleted whether the retry passes or
fails, so it cannot become a standing override that quietly pins a known-bad
commit in place. If the release is refused again it is quarantined again.

### What is *not* quarantined

Two failures are properties of the **box**, not of the release, and retrying them
is the correct behaviour, so they keep retrying every tick:

* a failed `git fetch` (network or credentials);
* a snapshot that could not be taken for a migration release — nothing has been
  merged at that point, and the script says it will retry;
* a `pip install` that failed, which is usually the network.

Everything else that is a **gate** quarantines: `compileall`, app construction,
the theme gate, and the post-reload verification (smoke test, claims gate,
performance gate, and the app not answering `200`). The quarantine record names
which one, so "why did nothing deploy" is answered by one `cat`.

A **manual** rollback does not write a quarantine — `git reset --hard` by hand
leaves no record — so after one, the next tick will indeed try the same release
again. That is the paragraph at the end of this document.

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

Then fix `origin/main` — a hand rollback writes no quarantine, so the next tick
will try that same release again and refuse it the same way. Freeze first if you
need time: `touch /etc/scangrade-deploy.pause`.

If the refusal came from the deploy itself it is already quarantined, and the
journal says so: `git reset` by hand is then only needed when you want the
checkout somewhere other than the previous release.
