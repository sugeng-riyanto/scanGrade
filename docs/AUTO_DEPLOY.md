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

### Gate 0 has three directions, and the quietest one is closed by the app

* **A copy that has drifted** is refused by the running copy itself, exit 14.
* **A checkout that no longer carries Gate 0** — rolled back past the commit that
  added it, or edited out while debugging — is refused by the *launcher* before it
  execs anything, exit 15, because a deploy script with no Gate 0 can never
  refuse a copy of itself again.
* **A release that removes the block** is refused and quarantined by the run that
  reads it (exit 16), because that run is the last one that could have noticed.
* **A copy so old it predates Gate 0 entirely** cannot refuse itself, and nothing
  in it can judge it. The app refuses instead: the copy's own construct gate sets
  `START_BACKGROUND_SCHEDULERS=false`, which marks the construction as the deploy's
  probe, and the probe asks `deploy/arm-auto-deploy.sh --check` whether this box is
  armed. If it is not, it prints `SCANGRADE-UNARMED` and exits non-zero, the
  construct gate treats that as a failed release, and the previous commit keeps
  serving. `gunicorn` builds the same app without the marker, so this can never
  refuse the site — only a release.

The marker is not new: it dates from the script's first commit, so every copy ever
installed carries it. That is what makes the last case reachable at all.

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
| see the last few refusals, kept past the current one | `ls -t /var/lib/scangrade-deploy/refusals` |
| see which step stopped the last run *before* it merged | `cat /var/lib/scangrade-deploy/refused-before-merge` |
| see the step the last run stopped at, whatever stopped it | `cat /var/lib/scangrade-deploy/last-stop` |
| retry a quarantined commit once | `touch /etc/scangrade-deploy.release` |
| stop deploying automatically | `systemctl disable --now scangrade-deploy.timer` |
| see the last release that stuck | `cat /var/lib/scangrade-deploy/last-deploy` |
| **unstick a box that fetches and never merges** | `curl -fsSL https://raw.githubusercontent.com/sugeng-riyanto/scanGrade/main/deploy/unstick-deploy.sh \| sudo bash` |

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
| the gate could not run at all (exit 2) | roll back (exit 13) |
| the release removes the check itself (exit 3) | roll back (exit 13) |

The last two are not a fault in the release, and they still roll it back: the
alternative is shipping a commit nobody read for contrast, which is the same as
having no gate. The armament preflight below refuses the whole run, before anything
is fetched, when the box cannot run this gate at all — so reaching here with exit 2
means the box changed between the two checks, and the rollback is the safe half of
that race.

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
| could not measure (exit 2) — the box was busy, or the probe did not complete | **warn only**, and say so in the journal |
| not armed (exit 4) — no roster, no harness, no base URL, an unreadable claim | roll back: this release was never measured |

The two rows above the last one are different answers, and they used to be one.
An absent measurement on a busy box is not evidence against the release; a box that
cannot check the claim at all means the release went out with the published table
re-checked by nothing. The armament preflight normally refuses such a run before
anything is fetched; the rollback is what happens if the box changes mid-release.

The two-strike rule and the `2x` latency slack exist because a measurement on a
shared box is not a fact about the code alone. They are also bounded: the slack
is tighter than a gap that has actually been observed here (a probe measured a
worst-page p50 of 1464 ms against a published 620 ms, and the first version of
this gate used `3x` and passed it).

It declines to measure, rather than guessing, when the box is already busy — the
same 1 vCPU serves real students, and loading it during a live exam would both
disturb the exam and produce a number that means nothing. It judges the *best* of
several samples of a **rendered page** (the landing page), so a box that has just
been reloaded is not mistaken for a busy one.

It asks a page rather than `/health`, and that is not a detail. `/health` renders
no template and touches no page code, so a box whose three workers are saturated
on exactly the pages these gates measure can still answer it in a millisecond.
Judging *that* said "idle", the gate loaded a box it should have left alone, and
the slow pages it then measured were the students' — read as a divergence and
rolled back. The question the probe asks has to be about the same work the gate
is about.

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
rollback cannot remove it. No roster means **not armed** (exit 4), and that release
is refused rather than deployed unmeasured.

**The conf file reaches the gate through the environment, and that is worth
knowing.** The deploy sources `/etc/scangrade-claims.conf` and passes every
`CLAIMS_*` setting as an environment variable; `claims_gate.py` reads them with
`env_default()`. It did not always: the gate originally read only `argv`, so
`CLAIMS_BASE_URL` arrived, was ignored, and every production run ended at
`no --base URL` — now exit 4, "not armed", on a gate that looked installed and
healthy (it was exit 2 at the time, which is how a box kept deploying with the
claim unchecked). That is why `/var/lib/scangrade-deploy/claims` had never been created. `tests/unit/test_perf_gate.py` now fails if the installer writes a
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
**Supabase queries** the busiest render issues — read from the app's own
`X-Supabase-Queries` header, which is the *cause* the other two only reflect. A
page can stay exactly as fast while gaining three queries or a 200 KB script, and
that is the release this refuses. Each axis carries a ratio **and** an absolute
grace (1.25x + 8 KiB of payload, 1.25x + 1 query), so a list that honestly got
longer is not mistaken for a leak — a release has to clear both to be refused. The
query number is what the page costs *when it does its work*, not what a cache
hit spent: a warm entry replays the cost it was built with, because otherwise the
busiest student page reads as free on every request. For the same reason the
program's bytecode is dropped before a mutation run — see
`.freebuff/mutate_page_cost.py`.

#### Why there are two more numbers than there used to be

Bytes and queries alone cannot say *who* spent them, and the app now sends four
per render for exactly that reason: `X-Supabase-Queries` (issued once per query),
`X-Supabase-Roundtrips` (one per **attempt**, retries included), `X-Supabase-Rows`
(records read), and the page bytes the harness weighs. Two measurements on
production, same release a day apart, are the whole argument: every endpoint's page
bytes were identical to the byte while `/teacher/dashboard` went from 1 round-trip
to 3. Nothing about that page changed, so a gate scoring attempts as a ratio was one
bad afternoon away from rolling back a release for the transport.

So the round-trip axis is scored on what the render **issued**, and the cost axes are
**normalized against the data**: today's rows against the baseline's rows, holding the
part of a page that does not scale with the roster constant (the baseline records the
smallest signed-in page its run loaded as that floor — a layout does not grow with the
roster). The data's share is *added* to the allowance as the absolute amount it is, never
multiplied into it: scaling a page's allowance by its growth would make the most-grown
page the most forgiving, which is where a fixed addition is easiest to hide. Growth is
growth only — a dataset that shrank does not license refusing a page that did not change.
Both the normalization and the retry split are reported as `perf gate: note — …` lines
next to the verdict, on passes as well as refusals, because they are answers rather than
excuses. A baseline written before any of this carries no row counts, and there the old
absolute rule stands: it may refuse a release the data would have excused, and it cannot
let a heavier one through. The guards are mutation-checked by
`.freebuff/mutate_perf_attribution.py`.

| Result | Outcome |
|---|---|
| no baseline yet | **deploy**, and this release becomes the baseline |
| no worse, on any of the three, within slack | deploy, and the baseline moves up to this release |
| slower or more expensive (confirmed twice) | roll back **if `PERF_ENFORCE=true`** |
| divergent once, clean on the confirmation run | deploy — contention, not a regression |
| could not measure (exit 2) — a busy box, or a probe that did not complete | **warn only**, and the baseline is left alone |
| not armed (exit 4) — no roster, no harness, no base URL, or a baseline from a different reference load | roll back: this release was never compared with the last one that passed |
| the baseline knows a page's cost but this run reports none | **warn only** — a harness that stopped reporting must not retire the payload and query axes in silence |

The baseline is written **only when a release passes**. If a refused release
became the yardstick, the next release would be measured against it and the
regression would be permanent and invisible. A deliberate slowdown (more work per
page, a feature worth its cost) is recorded with `--rebaseline`, so the trade is
stated rather than assumed.

A box that is already busy, or a divergence a second probe did not confirm, is
`could not measure` (exit 2): not evidence against the release, and it rolls
nothing back. A **changed reference load** is the other answer — exit 4, "not
armed" — because the comparison this gate exists to make is not happening at all,
and the message names the remedy (`--rebaseline`). Every one of them is said out
loud on every release; a gate that can only say "could not measure" is a gate that
is off.

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

Its output is streamed to the journal as it runs, so an operator watching the
deploy sees each role being opened rather than a silence that ends in a verdict.
A refusal keeps that stream *and* is recorded: the runner tees the transcript to a
fresh file outside the checkout, and the quarantine record quotes the failing
checks (`FAIL …`) and the verdict line from that copy, so "why did nothing
deploy" names the check that failed without a shell. The exit status is read from
the pipe's first command rather than the pipeline, because a `tee` that succeeded
must not report a smoke test that failed as a pass.

It checks four things:

| | What it proves |
|---|---|
| four logins | session cookies, both login routes, both auth stores |
| ~32 pages | every role's landing page, admin console and work queues render |
| 6 refusals | admin/guru/murid cannot open another role's area |
| one exam page | the student's exam screen still carries both anti-cheat panels, armed |

The last one is the only check that asserts anything about what a page *says*, and
the only one that opens a page needing a row to exist. Both are deliberate: the
fullscreen blocker and the away blur live on that one page, a release that drops
either leaves every other check green, and the page cannot be opened without an
exam. So the murid check finds the demo exam on the student's own list, opens it,
and reads out of the served document that

* it is the sitting page for the exam it asked for;
* the exam arms anti-cheat *and* requires fullscreen — with either off, neither
  panel would ever be revealed however intact the markup is;
* both panels are present, each with its own sentence;
* the countdown carries the server's own `AWAY_GRACE_SECONDS`, not a hard-coded
  number;
* the page watches `fullscreenchange` and `visibilitychange`, which are what set
  the two flags.

A missing panel fails the release; a missing *fixture* only warns, with the command
that puts it back — the demo schools are data, and a box whose demo data was cleared
must not roll a healthy release back (see the section below). Opening an exam is the
one place this smoke test changes state, because in this app opening an exam opens
the sitting that belongs to it: one demo account, on the demo paper, reused by every
later run.

### The exam it opens

`deploy/demo_exam_fixture.py` holds the paper, and the deploy runner refreshes it
**before** the smoke test runs, on every release (`python manage.py demo-exam`, as
the service user, with the schedulers switched off so constructing the app cannot
start the retention purge). It is written to be sittable rather than merely to
exist: assigned to every class in each demo school, no window at all (so it cannot
close on a date), anti-cheat and fullscreen on, and every standing attempt on it
voided — `retracted`, the app's own word for an attempt that does not stand — so a
demo visitor who submitted it does not hide it from the check.

If the smoke test reports *no sittable demo exam*, the fixture is gone or has been
changed back. Put it back with:

```bash
cd /opt/scangrade && sudo -u scangrade .venv/bin/python manage.py demo-exam
```

It is idempotent: run it as often as you like. It touches the three seeded demo
schools (NPSN `99887711`, `99887722`, `99887733`) and nothing else, and a box with
no demo data is not an error — the check simply has nothing to open.

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
| nothing was testable (exit 2) | roll back — no role could sign in against this release |
| no `/etc/scangrade-smoke.conf`, or a conf that does not parse | roll back — the release was never signed in against |

The line that matters is the one between a **failed** run and an **unarmed** one: a
stale credential in the conf is evidence about the box, so with `SMOKE_ENFORCE` not
`true` a failed run keeps the release, while a conf that is missing or unreadable is
a gate that did not run — and that rolls back. The armament preflight refuses the run
before it starts when the conf is absent, so the last row is the mid-release race.

## An unarmed box deploys nothing

Every gate below the preflight can be *skipped*, and each skip used to be a
sentence in the journal and nothing else: the readability gate without `pytest`
logged "this release is NOT contrast-checked", a missing smoke conf logged its skip,
and the claims and performance gates said "could not measure" on a box with no
roster. Each carried on and kept the release — so a box in that state deployed every
commit while checking almost none of them, with the site green and "the gates ran"
quietly false.

So the armament is judged **once, before anything is fetched**, by the same checker
a human runs:

```bash
bash /opt/scangrade/deploy/arm-auto-deploy.sh --check   # read-only; what the deploy asks
```

If it says the box is not armed, the run is refused with exit 15: nothing is pulled,
nothing is reloaded, and no release is staged. It is a refusal to *deploy*, not a
rollback — no release is under judgement, the box is — and it is **not** quarantined,
because a quarantine is a record about a commit.

That check now also counts the smoke test's conf, because `SMOKE_ENFORCE` and
`/etc/scangrade-smoke.conf` are what make "every role still works" a gate rather than
a log line. `arm-auto-deploy.sh` itself is the definition of "armed" for all four
gates; the deploy prints its report verbatim rather than keeping a second copy of
the judgement.

### Seeing it without a shell

The refusal writes the checker's report to
`/var/lib/scangrade-deploy/unarmed` and deletes it the moment the box is armed again.
`/super-admin/deploy-status` reads it, and that matters: a box that refuses every
release has **no other symptom**. Nothing is fetched, no commit moves, nothing is
quarantined, and the site keeps serving — from outside it is indistinguishable from
"no new commits". The page says which of three states it is in: a refusal (with the
checker's own report and how long ago), a record it cannot read, or nothing refused.

The one directory both sides need is `/var/lib/scangrade-deploy`; the installer
creates it `root:"$SERVICE_GROUP"` mode 0750 and the record itself `0644`, so the app
can read a refusal and cannot write one away.

## The alert when the runner goes stale

`/super-admin/deploy-status` only answers when somebody opens it. That is the same
failure one level up, and it is measured rather than imagined: production ran for a
week with the runner **45 commits behind**, deploying every push with the deploy
logic of the day it was installed, and the only trace was a page nobody had a reason
to open.

So the reading is made on a timer and emailed. **The app sends it, not the runner**
— deliberately, because the runner is the thing being reported and an old copy would
compose its own report with the gates of the day it was installed: the alert would be
missing exactly when it matters. `gunicorn` is the process guaranteed to be the new
commit, since the release that pulled it is also the one that reloaded it.

### What is worth an email

Four readings, in the order they bite:

| reading | what it means |
|---|---|
| **the copy predates Gate 0** | nothing can refuse it — every release ships with the gates of the day it was installed, silently |
| **the copy has drifted** | Gate 0 refuses it with exit 14, so *nothing* deploys while the site looks healthy |
| **the runner is N commits behind** | a launcher rendered from an older `entrypoint.sh`, or a copy from an older commit |
| **the checkout is N commits behind `origin/main`** | the pipeline has stopped: a failed fetch, a quarantine nobody released, or `PAUSE` |

"More than a few commits" is **5** (`DEPLOY_ALERT_MIN_COMMITS`). Small on purpose:
the timer runs every six hours and a healthy box is never more than a commit or two
behind for more than a moment, so a threshold high enough to be quiet is high enough
to miss the thing the alert exists for.

It **never mails a reading it could not make**. An absent launcher, an unreadable
file, a directory that is not a git tree: the page says "cannot measure" and nobody
is woken, because an alert that cannot be acted on is how a channel gets muted.

### One mail per problem, not one per tick

The record (`deploy_alerts.json`, in whichever directory `state_dir()` chose — see
below) keeps the identity of what was sent:
**the kind, the count, and the revision**. So 5 commits behind that becomes 40 sends
again, a runner that is fixed and drifts again sends again, and the same reading
stays quiet for a week (`RENOTIFY_AFTER_SECONDS`) before one reminder. A record that
cannot be parsed counts as *nothing sent*, because the wrong answer there is "never
again".

Three `gunicorn` workers start together and tick together, so the tick is claimed
with an `O_EXCL` file (`deploy_alerts.lock`, beside the record) — one winner computes
and sends, the others return. The claim is released even when sending raises, and one
abandoned by a killed worker is stolen after 15 minutes rather than blocking alerts
forever. A **failed send is not recorded**, so the next tick tries again instead of
one SMTP wobble silencing the problem for a week.

### Who gets it

`system_settings` key **`deploy_alert_recipients`** (comma, semicolon or newline
separated) if it is set; otherwise every active `super_admin`'s email; otherwise the
SMTP account itself, which still reaches a person and is flagged on the page as the
fallback it is. The card on
[`/super-admin/deploy-status`](https://scangrade.web.id/super-admin/deploy-status)
shows the addresses, which rule chose them, the cadence, the threshold, and the last
alert with its date — so "alerts are armed" is something an operator can check
instead of assume.

There is a **Send a test email** button (guarded POST, audited). It records nothing,
which is the point: it cannot silence or delay a real alert for the same staleness.
The failure it prevents is a mail path that has never once been exercised being
needed at the moment the box is broken.

### The timer

Started from `create_app` beside the cleanup and retention loops, behind the same

```ini
START_BACKGROUND_SCHEDULERS
```

switch, so a deploy probe constructs the app without starting a third thread. The
first pass waits a full `DEPLOY_ALERT_INTERVAL_SECONDS` (default **6 h**): a deploy
restarts the app, and an alert about a runner that was already stale belongs on the
page, not in an inbox the moment the service comes back up.

No gate needs arming for this — the recipients come from the database or the SMTP
settings the app already has.

### Where the record lives, and why that had to be arranged

The record is the *only* thing that stops a stale runner being mailed every tick, so
where it can be written is a correctness question. It cannot live in the checkout:
the deploy pulls into `/opt/scangrade` **as root**, so the service user cannot write a
byte inside it — and the service reads a *missing* record as "nothing was sent", so
production as first written would have mailed every six hours, forever, about the same
runner. The installer therefore creates one more directory the app owns:

```
/var/lib/scangrade-deploy/alerts     $SERVICE_USER:$SERVICE_GROUP 0750
```

The service picks its directory in this order — `SCANGRADE_ALERT_STATE_DIR` (used
alone: falling through would silently ignore what an operator asked for), then that
directory when the installer has created it, then Flask's instance folder, which is
what a dev checkout gets. It **creates only the last one**: anything under
`/var/lib/scangrade-deploy` is root's and the installer's, and that is what keeps the
quarantine record unwritable by the process it constrains. The directory that
answered is printed on the card, and a box where none of the three works says so on
both the card and in the journal rather than looking armed.

Guard: the same suite, plus `tests/unit/test_auto_deploy.py` comparing the installer's
path with `DEPLOY_STATE_DIR` in the service, because two copies of one path that move
separately leave an alert that stops recording on a box that looks installed —
mutation-checked, **9/9 injected defects caught**
(`.freebuff/mutate_alert_state_dir.py`).

Guard: `tests/unit/test_deploy_alerts.py` (70 tests) — the policy against the real
report shape, the four kinds, the identity and the reminder, the claim across
processes, the fallbacks, the state-directory choice, the card and both routes —
mutation-checked, **23/23 injected defects caught**
(`.freebuff/mutate_deploy_alerts.py`), plus the state-directory harness above.


## What it refuses to do

The script takes **no arguments**. It is the only thing root runs unattended, so
it is not a general-purpose command runner — the branch, the repo and the paths
are fixed in the script itself.

It also stops instead of guessing when:

- it is running from an installed copy that differs from the checkout (exit 14)
  — see [The runner is never a copy](#the-runner-is-never-a-copy);
- the checkout has lost Gate 0, so it could never refuse a copy again (exit 15) —
  the launcher refuses to exec it at all;
- this box is not armed to check a release (exit 15, and the report is kept in
  `/var/lib/scangrade-deploy/unarmed`) — see
  [An unarmed box deploys nothing](#an-unarmed-box-deploys-nothing);
- the release itself removes Gate 0 (exit 16, and it is quarantined);
- the checkout has local changes (exit 4 — it will not clobber hand edits);
- `git status` cannot read the checkout at all (exit 4): *"we could not tell"* is
  not *"it is clean"*, and an empty answer used to mean both;
- the fetch cannot reach GitHub (exit 5, network or credentials);
- the update cannot be merged into the checkout (exit 6 — history rewritten, an
  untracked file in the way);
- the checkout's index is locked and the runner will not force it (exit 17 —
  something named `git` is running, or the process table could not be read);
- the release ships a migration and no snapshot of the data can be taken (exit 12);
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

### The refused release's numbers outlive the release after it

The performance gate appends one line per judgement to
`/var/lib/scangrade-deploy/perf/history.jsonl`, and the status page shows the
**last** line — which is exactly what a quarantined commit loses. Once it is held
it stops being judged, so the next line belongs to a later release that passed,
and the measurement the refusal was made from leaves the page just as somebody
comes looking. The quarantine file still quotes the gate's sentence, but the
numbers behind it (`slowest page p50 812 ms against 480 ms (1.69x, allowed
1.50x)`) were reaching nobody without a shell.

So the held commit's own judgement is looked up in the same file, **by the sha
parsed out of each record** rather than matched as text, and shown beside the
latest judgement on the deploy-status page (`The Refused Release’s Own Numbers`).
Two distinctions it keeps, because a missing lookup is a wrong answer that looks
like a right one:

* **"not judged" and "older than the window" are different.** The history is read
  as a bounded tail, so a lookup that ran out of window reports
  `older than the history window` rather than "this gate never judged it" — only
  the first is fixed by looking at the file.
* **A fragment is not a judgement.** The window starts mid-record whenever one is
  larger than the tail, and a line the page cannot read is counted as *skipped*,
  not dropped — a dropped line is how "not found" starts reading as "never
  judged".

When the held commit is still the latest judgement the second block is not
rendered at all, so the same numbers never appear twice.

#### The index beside the history

The history is append-only and grows by a line per deploy for the life of the box,
and the page deliberately reads only a bounded tail of it (`PERF_TAIL_BYTES`, 64
KiB). That bound is what the `older than the history window` answer above is made
from — correct, and still a reader opening the file by hand. So the gate also keeps
a small index beside it:

```
/var/lib/scangrade-deploy/perf/history.jsonl.index
```

one short line per judgement — the commit and the byte offset of its record. The
page reads the index **whole** (it is far smaller than the history and stays cheap
far longer) and seeks straight to a record the tail no longer covers, so a held
commit's numbers are found however long the history has been accumulating. The path
is the history's own name plus `.index`, **derived in both places** rather than
configured, so the gate that writes it and the page that reads it cannot be pointed
at different files (`perf_gate.index_path()` and
`deploy_status_service._perf_index_path()`).

Three things it does *not* do:

* **It never overrides the tail.** If the tail already holds a commit, its judgement
  is the newer one (a later append sits nearer the end), so the tail wins and the
  index only fills what the window lost.
* **It is not evidence.** The history remains the authority; an index that is
  absent, unreadable or corrupt just means the tail scan answers as it always did —
  which is what a box whose gate predates the index gets.
* **A stale offset is not a judgement.** A rotated or rewritten history can leave an
  offset pointing at another commit's line, so the reader checks that the record it
  landed on names the commit it asked for; a mismatch is discarded, never shown.

### The whole refusal, kept past the lift

The perf card above recovers the *held* commit's measurement from the gate's own
history. The rest of the refusal — which commit, which gate, and the lines that gate
printed — still had no survivor: the next refusal overwrote the quarantine file, and
the release that lifted it deleted the file outright. So the runner also copies each
refusal, **byte for byte**, into

```
/var/lib/scangrade-deploy/refusals/refused-<epoch>-<short sha>
```

a directory rather than one framed file, because each record has to be the
quarantine file's exact bytes and a single file would need a delimiter the gate's own
output could collide with. The name sorts by time, so the newest are found without
statting anything. Nothing here decides whether to retry: a history write that fails
is ignored, and the copy is made **before** the gate's detail is cleared from the
runner's variables, so the record kept is the one the box acted on.

The last **five** are kept (`REFUSALS_KEEP`); older ones are pruned. A list that
never shrinks is a directory that grows forever on the box the deploy writes to.

The status page reads them into a card (`The Last Few Refusals`), newest first, with
each refusal's gate sentence and the gate's own lines verbatim. Two readings carry
the weight, because a missing record is a wrong answer that looks like a right one:

* **`none` and `unreadable` are different.** No refusals yet (or a runner from
  before the history) is the ordinary answer and is said out loud; a directory that
  exists and cannot be read is its own sentence, never rendered as the empty one.
* **A file that is not a record is reported, not dropped.** A record whose first
  line is not a sha is shown as *not recognised* rather than silently skipped — a gap
  in the history is how "nothing was refused" starts reading as the truth.

Everything the page lists is the runner's own words: the detail lines are the
evidence, and the card renders them under the runner's three-line header exactly as
the quarantine card does.

**Each of those refusals also carries its own measurement.** The block above shows
the *held* commit's numbers, and the perf card shows the *latest* judgement, but the
older quarantined commits had only the runner's prose while their measurement sat in
the same `history.jsonl` — reachable only by a shell. So every commit in the refusal
history is looked up by its sha (`perf_judgements()`), from **one** read of the tail,
and the card prints each one's verdict and its p50/p95 beside the gate's lines. The
four answers a single lookup gives are kept per commit — `present`, no judgement,
older than the window, unreadable — because a commit whose evidence is merely outside
the read tail must not read as one the gate never judged. The held commit's inline
copy is suppressed: its numbers are already on the page in its own block (or in the
latest judgement, when it is that too), and the same measurement must not print
twice.

#### A row can release the commit it names

The one-click release above the list is a form posting to
`/super-admin/deploy-status/release`. Each row has the same button, and it posts a
hidden `sha` field naming *that row's* commit. The field is not decoration: the
runner deploys `origin/$BRANCH` and honours a quarantine only for the commit it
currently **holds**, so a bare request from an older row would clear the quarantine
for the wrong commit — silently, from a row that names a different one. So the
service refuses the request when the sha it is given is not the held commit
(`not_held`), and writes nothing; the page turns that into a sentence rather than a
release. Case is normalised, because a sha is hex and the same commit in different
case is the same commit.

Only the row whose commit is *under judgement* offers a working button. An older row
is disabled with the reason beside it — the branch has moved past that commit, so
the runner cannot check it out again, and a control that promised otherwise would be
a lie. Rows from a history whose quarantine has since been lifted (nothing is held)
are disabled for the other reason: there is nothing to release.

The button is deliberately still a `POST` form rather than a fetch, so the CSRF
injection in `base.html` reaches it, and the route is `POST`-only and super-admin
gated like the one above.

### What is *not* quarantined

Two failures are properties of the **box**, not of the release, and retrying them
is the correct behaviour, so they keep retrying every tick:

* a failed `git fetch` (network or credentials);
* a snapshot that could not be taken for a migration release — nothing has been
  merged at that point, and the script says it will retry;
* a `pip install` that failed, which is usually the network.

Everything else that is a **gate** quarantines: the check that Gate 0 survives the
release, `compileall`, app construction (including the app refusing to be deployed
by an unarmed runner), the theme gate, and the post-reload verification (smoke test,
claims gate, performance gate, and the app not answering `200`). The quarantine
record names which one, so "why did nothing deploy" is answered by one `cat`.

One refusal deliberately writes **no** quarantine: the armament preflight, which
refuses the *run* rather than a commit. Its record is
`/var/lib/scangrade-deploy/unarmed`, which the status page reads, and it is deleted
as soon as the box is armed again — a quarantine is a fact about a commit, and there
is no commit here.

### The other half: refusals *before* the release moves

The list above is about a release that got as far as being merged and judged. The
other half of "why is nothing deploying?" had no record at all — the run refusing
*before* it touched the checkout: a dirty tree, an unreadable one, a fetch with no
network, a migration release with no recovery point, a merge that cannot happen.

That gap is not theoretical. A box fetched `origin/main` every two minutes for three
hours and merged none of it, while its own status page reported `0 uncommitted
files`, `No Held Release`, a launcher that passes Gate 0, and `Running` for the
schedule — all true, and all describing a box that was deploying nothing. The cause
was a stale `.git/index.lock`, which makes `git merge` fail while `git status`
succeeds; the journal said `not a fast-forward (history rewritten?)` every tick,
which names a cause that was not there.

So a refusal that happens before the merge writes its own record:

```
/var/lib/scangrade-deploy/refused-before-merge
```

Five parts: the step it refused at (a key the status page has a sentence for),
when, the exit code `systemctl status` will also report, the commit under judgement
(empty when the run refused before there was one), and then the **command's own
output** — git's error, or the snapshot's. It is written by every pre-merge exit and
removed the moment a release merges, so it always describes the last attempt rather
than a state that has since been fixed. A later refusal overwrites it; nothing
stacks.

It is not a quarantine and it does not try to be: a fetch that could not reach
GitHub is the world's fault, not the commit's, and the next tick simply tries again.
Nothing in the record decides whether to retry — the exit codes already do.

### The one refusal that heals itself instead of waiting for you

That stale `.git/index.lock` is the reason the runner now looks for it **twice** in
a run, at both ends of everything that can touch the index (`lock-heal-logic` in the
script): once before the first read — the dirty guard below reads the index through
the same lock, so a lock is what makes it report an unreadable checkout — and once
immediately before the merge, because on a release that ships a migration the first
heal is minutes old by then (a fetch and a data snapshot run in between) and a lock
appearing in that window would land on the merge and be read as a merge failure.
The second call costs one `stat` when there is no lock, and `lock_heal` caches the
path it asked git for, so the two calls cannot disagree about where the lock is.

A lock file is evidence that a git *was* running, not that one is, and three answers
are acted on rather than guessed at:

* **nothing holds it** — the file is removed, the journal says so, and the release
  moves on the same tick. No shell, no root step, nothing to remember.
* **a git process holds it** — it is left alone, because clearing it would corrupt
  whatever that git is in the middle of, and the run stops with **exit 17** and a
  `lock_refused` record whose detail names the holder (`1234 git`), so the status
  page says a locked checkout instead of showing a box that looks merely idle. The
  next tick tries again; nothing in this needs an operator.
* **the process table cannot be read** — also exit 17, also `lock_refused`. "We
  could not tell" is not "nobody holds it", and removing the file on that basis is
  the one way this could destroy work.

The lock is found by asking git where its index lives (`rev-parse
--absolute-git-dir`), so a linked worktree — where `.git` is a *file* — is handled
too. The holder scan reads `/proc/<pid>/comm`, one read per process: no `pgrep`
(procps) and no `lsof` (a package), neither of which is a given on a minimal box,
and a heal that stops working because a tool is missing is the same silent stall in
a new costume. It is deliberately repo-blind — a git running anywhere counts — and
being wrong that way costs one delayed tick, where the opposite corrupts somebody's
in-flight `git add`.

If a merge is refused anyway, the journal names *who* owns the lock — git's own
stderr says a lock file could not be created, and it cannot say whether anybody is
holding it. Both answers are printed (`1234 git`, or that a lock is there although
no git process holds it), so "history rewritten?" is not a reading this box can give
you again.

A **manual** rollback does not write a quarantine — `git reset --hard` by hand
leaves no record — so after one, the next tick will indeed try the same release
again. That is the paragraph at the end of this document.

### The step it stopped at, whatever stopped it

Everything above is written by a branch somebody wrote for a refusal they thought
of. The hole that leaves is the run that stops where there is no branch: a step
added later, a command that returns non-zero with no `if` around it, a `pipefail` in
a helper nobody looked at twice. Those end a run with one line in the middle of a
1 300-line journal, and `exit 7` is not a step.

So the runner names the phase it is in and its `EXIT` trap writes any non-zero exit
down itself:

```
/var/lib/scangrade-deploy/last-stop
```

Four positional lines: the step, when it stopped, the exit code, and the commit
under judgement (empty when the run stopped before there was one). The trap being
the writer is the point — a new failure path cannot forget to record itself — and it
is installed **before the first exit in the script**, which a test asserts, because
an exit above it would run unrecorded.

Only a run that reaches the end deletes the record. A tick that exits 0 because
`origin/main` has not moved, or because another run holds the lock, is not a
recovery; clearing the record there would erase the evidence every other minute. So
a record on disk means the box is *still* stopping on that step, and the status page
says exactly that — including when the record exists but cannot be read, which is
never rendered as a clean state.

`/super-admin/deploy-status` renders both halves of it: the step and the code, each
with a plain sentence (a step or a code from a newer runner is shown as itself,
never guessed at), and a one-line table of every exit code this runner can end with.
The vocabulary is the runner's own — `EXIT_CODES` and `RUN_STEPS` in
`app/services/deploy_status_service.py` — and the tests assert it in both
directions: every `RUN_STEP=` assignment and every `exit N` in the script is in
those tables, and every row in those tables has a sentence in the page.

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
