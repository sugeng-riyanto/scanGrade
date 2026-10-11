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

`--check` exits 0 only when every gate has what it needs — the launcher, the three
confs, the snapshot launcher, the roster, and the `DIRECT_URL` the schema gate reads
the catalogue with — and names every missing piece otherwise, so a missing launcher,
conf, credential or roster is never reported as armed. It reads the installed file the way `/super-admin/deploy-status` does:
a launcher that execs the checkout is the arrangement, `@REPO@` still in the file
means it was installed but never rendered, and anything else is a **copy** — whose
missing gate blocks are printed by name, so "no theme gate, no claims gate, no
performance gate, no quarantine, no schema gate" is a sentence an operator reads
rather than a conclusion they have to reach. A password is never requested for a roster that
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

## The gate on the live schema

Every gate above reads the code or the box. None of them can see the failure this
one exists for: a release whose **database is behind its code**. A migration is
written, merged and deployed but never applied — or applied to the wrong project —
and the app now names a table or a column production does not have. The box is
green, the release deploys, and the first teacher to open the page that reads it
gets a 500. Two migrations in this repository were found in exactly that state
(`--verify` reported them `OUT`), which is why it is now a gate rather than a check
somebody remembers to run.

Before the app is reloaded — the same place the readability gate runs, where rolling
back still costs nothing — the runner runs `apply_migration.py --verify`:

```bash
python deploy/apply_migration.py --verify --repo /opt/scangrade
```

read-only, and reads its exit code:

| Result | Outcome |
|---|---|
| every declared object is present | deploy |
| exit 6 — a file declares objects the schema does not have | roll back and **quarantine** (exit 18) |
| exit 1 — the box could not measure (no `DIRECT_URL`, or a database it cannot reach) | deploy, logged loudly |

A gap is a property of the commit, so it quarantines: the next tick must not re-pull
it. The refusal quotes the objects themselves — `MISSING  column
profiles.preferences` — so the deploy-status page names *what* is missing rather
than only the gate.

Exit 1 is deliberately **not** a rollback. An unreachable database is a property of
the box, not of the release, and a transient disconnect must not take a good release
down. The one box problem that must not be waved through — no `DIRECT_URL` at all —
is caught earlier and harder: `--check` reports it as part of what arms the box, so
the preflight refuses the whole run (exit 15) before any release is fetched. It is
the only gate that needs a database credential.

Because a release that ships a migration is refused until the migration is applied,
the order is: apply it, then release the quarantined commit —

```bash
python deploy/apply_migration.py supabase/migrations/038_x.sql --commit
# then release that exact commit from /super-admin/deploy-status (or push a fix)
```

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

#### The clock is normalized the same way

Response time was the one axis still compared absolutely, so a school that had simply
grown read as a release that had slowed down: four times the rows makes the same page
slower without the release changing a byte, and a 2.33x p50 was a rollback for the
roster. Latency now carries the same floor the cost axes do — the baseline records the
*smallest* signed-in page's p50 and p95 alongside the slow page's own, and the row count
of the page the p50 came from — and is compared against `floor + (baseline − floor) ×
row-growth`. Holding the floor constant is the entire point: scaling a page's *whole*
latency by the growth would make the release with the heaviest layout the most forgiving
exactly when a fixed slowdown is easiest to hide. As with the other two axes, the data's
share is *added* as the absolute amount it is, growth is growth only, and a baseline
written before this existed (no floor, no row count) falls back to the absolute rule —
it may refuse a release the data would have excused, and it cannot let a slower one
through. When the clock *is* refused with a growth in play the reason says so (`the data
accounts for N ms of the rise`), and a `perf gate: note` says the slow page read more
rows, on a pass as well as a refusal. Guards:
`.freebuff/mutate_latency_attribution.py`.

#### A stale baseline does not get to quarantine an innocent release

The baseline is rewritten on every release that **passes**, so it ages exactly when
releases stop passing — which is the state this gate leaves a box in when it keeps
refusing. An old enough baseline can stop describing the box without any release doing
anything: the host gets busier, a neighbour appears, a kernel or an nginx version moves.
The comparison still runs and still diverges, but it weighs today's box against a
description of a box that no longer exists, and the release it refuses is charged for
drift no release caused.

Two facts together license the gate to act on that, and **both** are required. The first
is age: `PERF_BASELINE_MAX_AGE` (default 14 days) says how long a baseline is trusted.
The second is scope: the gate diffs the baseline's commit against the release
(`git diff --name-only`), and when no changed path lies in the measured surface —
`app/`, `deploy/`, the harness, the runtime, a migration — the pages that diverged are
the *same bytes* the baseline measured, so the difference cannot be the release. With
both, the gate re-baselines on the box as it is now and passes, recording verdict
`stale_baseline`, instead of quarantining a commit that provably changed nothing. With
either alone it compares as it always did: a fresh baseline means the divergence is new
information, and an old baseline with a release that did touch a measured page leaves the
release the prime suspect. The window is in days and `0` turns the exemption off. The
measured surface is deliberately wide — a shared layout or a service reaches every page —
so the exemption may only fire when it is *certain* the measured pages are unchanged, and
a release git cannot diff (an unnamed commit, a commit no longer in the object database)
takes the conservative branch and is compared as before. Guards:
`.freebuff/mutate_stale_baseline.py`.

| Result | Outcome |
|---|---|
| no baseline yet | **deploy**, and this release becomes the baseline |
| no worse, on any of the three, within slack | deploy, and the baseline moves up to this release |
| slower or more expensive (confirmed twice) | roll back **if `PERF_ENFORCE=true`** |
| divergent once, clean on the confirmation run | deploy — contention, not a regression |
| could not measure (exit 2) — a busy box, or a probe that did not complete | **warn only**, and the baseline is left alone |
| not armed (exit 4) — no roster, no harness, no base URL, or a baseline from a different reference load | roll back: this release was never compared with the last one that passed |
| the baseline knows a page's cost but this run reports none | **warn only** — a harness that stopped reporting must not retire the payload and query axes in silence |
| a stale baseline (older than `PERF_BASELINE_MAX_AGE`) and a release that changed nothing the gate measures, confirmed twice | deploy, and the box as it is now becomes the baseline (verdict `stale_baseline`) — the divergence is the box, not a release that touched nothing measured |

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
(`PERF_BYTES_SLACK`, `PERF_ROUNDTRIPS_SLACK`), the baseline's age window
(`PERF_BASELINE_MAX_AGE`, in days) and `PERF_ENFORCE`. Unlike the claims gate it
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
fresh file outside the checkout, and the quarantine record quotes the verdict line
and a **per-role summary** from that copy — one line per account that failed, with
how many checks failed for it and which, so "why did nothing deploy" and *which
account could not be served* are both answered without a shell.

Grouping by role is deliberate rather than cosmetic: a flat list of `FAIL …` lines
makes the reader re-group by each line's prefix, and the record's few lines fill with
whatever the stream printed first. `deploy/smoke_test.py` records the role beside
each failure (`Result.fail(msg, role=…)`), and `grouped_failures()` prints one line
per role in a fixed order — `ROLES`, not first-seen — so two runs with the same
failures file the same record. A role the message itself leads with is still grouped
right if a call site ever misses the argument. The exit status is read from
the pipe's first command rather than the pipeline, because a `tee` that succeeded
must not report a smoke test that failed as a pass.

The journal's stream and the recorded copy are **one document by construction, and
that is pinned**: the smoke test's merged stdout+stderr goes through exactly one
`tee`, its file is the only thing the record reads, and nothing consumes stdout after
it. A guard runs the runner's own pipe (its text, with the producer swapped for a
stub) and asserts the stream equals the copy byte-for-byte — so a filter added after
the tee, a redirect instead of it, a dropped `2>&1`, or a record read from anywhere
else fails a test rather than letting the journal show one thing and a refusal quote
another.

When the run dies **before it prints a single check line**, there is no verdict and
no `FAIL` to quote — and a crash is as much a finding as a failed check. So the same
copy is read for a traceback, and the record quotes its **last** six lines: the first
six would file the `Traceback …` header and the early frames and cut off the
exception, which is the one line that says why the process died. The fallback is the
only thing standing between a crash and a record that names the gate and nothing
else, so it lives in one function both refusal arms call (`smoke_detail`) rather than
a copy per arm. stderr is folded into the tee on purpose — the traceback is on
stderr, and this is where it is read back.

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
SMOKE_PRINCIPAL="principal_smp@scan-grade.app:demo123"
SMOKE_VICE_PRINCIPAL="vice_principal_smp@scan-grade.app:demo123"
SMOKE_GURU="guru_mtk_smp@scan-grade.app:demo123"
SMOKE_MURID="siswa2_smp@scan-grade.app:demo123"
```

Each value is `email:password` (split on the *first* colon). `SMOKE_BASE_URL`
must be `https://`: production sets `SESSION_COOKIE_SECURE`, so over plain HTTP
the session cookie is dropped and every login would look broken.

Six roles, and the gate checks all six. A `SMOKE_<ROLE>` that is **absent** is
allowed (the box keeps deploying and the run says which role it did not check),
but one that is **present and wrong** is a failure, and a malformed one is a
failure too — it would otherwise drop the role from the run without saying so.

Check a change without deploying anything:

```bash
set -a; . /etc/scangrade-smoke.conf; set +a
/opt/scangrade/.venv/bin/python /opt/scangrade/deploy/smoke_test.py
```

### What it will and will not roll back

`SMOKE_ENFORCE=true` arms the rollback, and the installer only sets it after
proving that **every one of the six roles** signs in — a config with a stale
password must never be able to reject a good release, and arming refuses a config
that is missing a role. Once armed:

| Result | Outcome |
|---|---|
| a page returns `5xx` after a successful login | roll back |
| a role can open another role's area | roll back |
| **no** role can sign in | roll back — one changed password cannot explain six |
| any configured role cannot sign in | roll back — the role would otherwise go unchecked |
| a `SMOKE_<ROLE>` is present but malformed | roll back — the role would be dropped silently |
| a role has no `SMOKE_<ROLE>` at all | keep the release, and say which role was not checked |
| nothing was testable (exit 2) | roll back — no role could sign in against this release |
| no `/etc/scangrade-smoke.conf`, or a conf that does not parse | roll back — the release was never signed in against |

The line that matters is the one between a **failed** run and an **unarmed** one: a
stale credential in the conf is evidence about the box, so with `SMOKE_ENFORCE` not
`true` a failed run keeps the release, while a conf that is missing or unreadable is
a gate that did not run — and that rolls back. The armament preflight refuses the run
before it starts when the conf is absent, so the last row is the mid-release race.

## The finger floor of the exam builder, on a real browser

Every gate above reads the code or the box; none of them *lays a page out*. The
exam builder is the densest form in the app and its controls are a teacher's
thumb. The floor is a stylesheet rule — the `@media (pointer: coarse)` block in
`app/static/css/theme.css` that gives every control inside `.sg-exam-builder` a
44px minimum in both axes — and it was written after a measurement: **42 controls
under 40px** at every tablet width the page was opened at, **0** after the rule.

That measurement was made once, by hand, in headless Chrome. A rule about
laid-out geometry cannot be kept by a grep, so `deploy/touch_gate.py` measures it
again on every release. It signs in as the teacher the smoke test already uses,
opens `/teacher/exams/new`, emulates a finger (`pointer: coarse`, the media query
the rule is asked as), and at each documented tablet width — portrait and
landscape — measures every control the rule names. It runs right after the smoke
gate, because it reads that gate's own base URL and credentials; there is no
second conf.

It measures the way the rule can be enforced: a control whose computed `display`
is `inline` is skipped, because `min-height`/`min-width` do not apply to a
non-replaced inline box — the rule cannot raise one, so flagging it would be a
finding no release could fix. Hidden controls are skipped for the same reason.

| Result | Outcome |
|---|---|
| every control at least 44px, at every width (exit 0) | log one line |
| a control under the floor (exit 1), `TOUCH_ENFORCE=true` | roll back, quoting the controls |
| a control under the floor (exit 1), not armed | keep the release, but say so |
| no browser, the account refused, the page did not render (exit 2) | keep the release, and say loudly it was **not measured** |

Exit 2 is deliberately not a rollback: a box that cannot start the browser (none
installed, the account refused, the page did not load) must not refuse every good
release over a tool it does not have. It is said in the journal as a skip, never
passed off as a pass. The finding arm is armed with `TOUCH_ENFORCE=true` in
`/etc/scangrade-smoke.conf`, so a browser that produces a false positive cannot
take the site down on its own.

To make it *measure* on a box, give it a browser — `apt-get install -y chromium`
or point `SG_CHROME` at any Chrome/Chromium binary — and arm it (`TOUCH_ENFORCE=true`,
and for the render gate below, `RENDER_ENFORCE=true`). The browser is now part of
the armament preflight (see "An unarmed box deploys nothing"), so a box without one
does not deploy at all rather than shipping every release unmeasured.

## The pupil's exam page, laid out on every release

`deploy/exam_render_gate.py` is the second browser gate, and it exists because a
blank exam page is invisible to every other one.

On 2026-10-03 `/student/exams/<id>` shipped **blank**: `take_exam.html` loaded its
helper scripts (`exam-media.js`, `tools.js`) with `defer`, and `base.html` also
loads Alpine with `defer` — a deferred script runs after `DOMContentLoaded`, so
Alpine started on the next microtask *before* the next deferred script executed.
`x-data="examApp(...)"` read `sgExamMedia` while building, threw
`ReferenceError: sgExamMedia is not defined`, and Alpine abandoned the whole exam
subtree. The route answered **200** with the full paper, the fixture was present,
and the smoke test read the right panels. **Every gate passed.** Only a browser saw
it.

So this gate signs in as the demo pupil the smoke test already uses, opens the
demo exam from the pupil's own list (the same fixture the smoke test reads, so no
second conf), and asks the **rendered DOM**: did any question control render
(`button.q-btn`), any answer block (`.exam-answers > div`), and did the browser
report a page error while loading? Any of those is a finding.

| Result | Outcome |
|---|---|
| the exam rendered, with questions and no page error (exit 0) | log one line |
| blank, or a page error (exit 1), `RENDER_ENFORCE=true` | roll back, quoting the measurement |
| blank, or a page error (exit 1), not armed | keep the release, but say so |
| no browser, no sittable demo exam, the page did not load (exit 2) | keep the release, and say loudly it was **not measured** |

A redirect *away* from the exam (a dead session, a refused sitting) is exit 2, not
a finding: that is a page the gate did not get to look at, not a rendering
regression. The finding arm is `RENDER_ENFORCE=true` in
`/etc/scangrade-smoke.conf`.

Both browser gates start Chrome through one shared helper,
`touch_gate.browser_launch`. It exists because two box facts are invisible in a
developer checkout and each one left the gates answering "could not measure" while
looking armed:

* **Chrome needs a writable `HOME`.** It writes crash reports, mimeapps and its
  singleton lock under `$HOME` even when `--user-data-dir` is given. The gates run
  as the checkout's owner via `runuser`, and a service account's home need not
  exist — on the VPS `scangrade`'s does not — so Chrome died before offering a
  debug target. The profile the gate already creates is handed over as `HOME` too.
* **The sandbox is unavailable to root.** A browser started as root refuses to run
  it (`Running as root without --no-sandbox is not supported`) and never reaches
  its debug port. `--no-sandbox` is added only when the gate is root, so an
  ordinary deploy user keeps the sandbox.

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
a log line, and the `DIRECT_URL` the schema gate reads the live catalogue with.
`arm-auto-deploy.sh` itself is the definition of "armed" for all five gates; the
deploy prints its report verbatim rather than keeping a second copy of the judgement.

Two consequences worth stating, because both bit a real box:

* **The wrapper's "After" report runs the checkout's checker, not its own copy.**
  The installer pulls `origin/main`, and "armed" can have *grown* in that pull. A box
  whose arming ran the four-gate rules it started with printed `ARMED — … all four
  gates` while the very next deploy tick, running the pulled rules, printed
  `NOT ARMED` — two verdicts about one box. So after the installer the wrapper execs
  `bash "$REPO/deploy/arm-auto-deploy.sh" --check` from disk, and the in-process
  `report_state` is only the fallback for a checkout too old to carry the checker.
* **`schema : MISSING` is a refusal, not a warning.** The `DIRECT_URL` in
  `/opt/scangrade/.env` (or `DATABASE_URL`) is the only credential the schema gate
  needs, and presence is the whole question — a `.env` still carrying the dashboard
  placeholder `[YOUR-PASSWORD]` counts as missing. Put the session-mode pooler URL
  there, from Supabase's *Project Settings → Database*, and re-run `--check`.

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

### The gate's own files, over HTTP

The page renders the gate's *last* judgement and a **bounded tail** of its history.
The tail is the same problem one layer down: the judgement that refused a held commit
can be **older** than the window the page reads — that is exactly the commit somebody
is looking for — and the numbers to check are all in the file. Past the window the
only way in was a shell on the box.

So the files travel. `/super-admin/deploy-status` now offers two downloads:

* `/super-admin/deploy-status/perf/evidence` — the judgement history
  (`/var/lib/scangrade-deploy/perf/history.jsonl`);
* `/super-admin/deploy-status/perf/baseline` — what the gate compared against
  (`/var/lib/scangrade-deploy/perf/baseline.json`).

Both are super-admin only, `GET`-only, and read-only. The path is resolved through
the *same* environment variables the page reads (`SCANGRADE_PERF_HISTORY_FILE`,
`SCANGRADE_PERF_BASELINE_FILE`), so the download is the file that was judged rather
than a second guess at where it lives. The body is the file **byte for byte** — the
gate's own line is the evidence, and a re-serialised copy would be a different
document.

Two rules the route follows everywhere on the page, applied to the files:

* **A part that cannot be read is reported, never served empty.** `absent` ("the gate
  judged nothing") and `unreadable` ("this page cannot read the file") are different
  states with different remedies, each answered as itself with a `404` and a reason
  key — an empty `200` is a download an operator would read as an empty history.
* **The download is bounded, and the cut is honest.** The history grows for the life
  of the box, so past 4 MiB the *newest* bytes are served, cut back to a line boundary
  (half a JSON line is not evidence), and the answer says so in `X-Perf-Truncated`
  with the file's true size in `X-Perf-File-Bytes`. `Cache-Control: no-store`, because
  a cached answer from before the refusal that made somebody open it is the wrong
  answer.

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

One of the theme gate's refusals is a **database** state rather than a theme: it runs
`schema_contract.py --require-applied`, which fails when a migration this repository
carries declares a table or a column the live schema does not have. That is the same
question the schema gate below asks through `apply_migration.py --verify`, asked
through `SUPABASE_URL` and the service key instead of `DIRECT_URL` — so a box that
cannot open a Postgres session is still held against the live schema. A gap refuses
the release through the theme gate's exit 1 (so the quarantine reason reads
`theme gate (exit 1)`), and a box with no credentials to ask with is said loudly and
**not** refused: `--require-applied` exits 2 there, and the gate deliberately does not
turn that into its own exit 2, which this deploy reads as a release not to ship.

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

### A refusal that has repeated is not repeated

The box-local-edit heal (`box-edits-logic`) is what ended the dirty-checkout deadlock:
a path the release also writes is set aside — a diff against `HEAD` for a tracked path,
the whole file for one `HEAD` has never seen — and the merge proceeds, on the *first*
tick. Nothing about it waits for a human.

What it cannot do is complete when the place it writes to is the broken thing: the
state directory cannot be created, the filesystem holding it is full, `git diff` cannot
write. Then the same refusal is made again on the next tick, and the one after that,
about the same paths — a refusal that has become a loop while still reading as
patience. A box can sit in that state for days, with every gate it never reached
looking innocent.

So the runner keeps count (`refusal-streak-logic`):

```
/var/lib/scangrade-deploy/refusal-streak         # how many ticks running
/var/lib/scangrade-deploy/refusal-streak.paths   # what the refusal was about
```

Two files, because they answer different questions. The paths are what make two
refusals *the same* — the same sentence about different files is a different problem,
so a different path set starts again at one, and a `\r` in the file (it is a file a
person can edit) is normalised away rather than read as a different refusal. The count
is what the threshold reads: past `REFUSAL_STREAK_MAX` — three, because the timer ticks
every two minutes and a stuck box is stuck for hours, so one would degrade on a first
attempt and a larger number is patience pretending to be policy — the heal stops
re-attempting the shape that failed.

**What changes** is two things, and each answers a different failure:

* the **directory** moves to `REFUSAL_FALLBACK_DIR`
  (`/run/scangrade-set-aside`) — tmpfs, and therefore a different filesystem from
  `/var/lib/scangrade-deploy`, which is the point: the failure being answered *is* a
  `/var` that is full or read-only;
* the **shape** keeps the files themselves rather than their diff (`cp`, not
  `git diff` — a whole file is a superset of its diff, so nothing is lost, only the
  size), which takes the git that failed out of the pipe.

The choice is made **before** anything is attempted, never in the middle: once a path
has been moved out of the tree, an attempt somewhere else has already lost its own
starting state and would try to move a file that is no longer there.

Every run that degrades says so, loudly, and the record names where the evidence went.
The count is cleared the moment the tree no longer holds the edit the refusal was about,
and after any release that lands; a stale count would make the next, unrelated stall a
loop on its first tick. Recording it is bookkeeping and can never fail a run — a box
whose state directory is the broken thing still refuses for the real reason.

**The honest limit.** This is about a runner from this commit onwards. A runner that
predates it — one that refuses on *any* local change, before it fetches — cannot be
reached by any push, which is why the box stranded in that state needs its one console
visit (see `sgfix` below). What is guaranteed from here is narrower and worth stating:
a runner that *can* fetch will not make the same refusal forever.

### An edit the release does not write, and nobody is coming back for

The overlap rule answers "would this merge clobber it". It cannot answer the other
question a dirty checkout raises — is anybody still working on it — and that gap is not
theoretical: a box carrying one hand edit to a file no release writes is refused by
nothing, clobbered by nothing and set aside by nothing, so it stays dirty on every tick
for as long as the box lives, and the change exists nowhere outside the tree. That is
what this box looked like while it sat eleven commits behind.

So the heal has a second reason to preserve a path (`BOX_EDITS_STALE_SECONDS`, a day):
a path the release does **not** write, whose own edit is older than that, is treated as
**stale** — nobody is coming back for it — and is set aside exactly the way an
overlapping path is. Its diff against `HEAD` (or its bytes, for a path `HEAD` has never
seen) goes to `/var/lib/scangrade-deploy/set-aside/`, the path is restored, and the
record names both the preserved copy and the fact that age is why it moved.

Four properties, and each is what keeps this from being a way to lose work:

* **A day, not an hour.** The rule stops a box carrying an *abandoned* edit; anything
  shorter mistakes somebody's morning for an abandonment. It is one constant in the
  script, with the reasoning beside it.
* **Only the path's own mtime can say it.** The refusal memory is cleared on every tick
  the heal lets through, so it says nothing about age; and a *deletion* leaves no file
  to date at all. A path that cannot be dated is therefore **not** stale — the same rule
  as everywhere else here: a reading that failed is not a reason to act.
* **The record says which reason it was.** `set-aside <path>` names every preserved
  copy; `stale <path>` names the subset that age moved, so nobody is sent looking for a
  release that never wrote the file.
* **The note does not repeat.** A dirty set that has not changed since the newest record
  names it is not written down again. Otherwise a box with one local edit writes a
  record every two minutes, and inside a day the newest few the page reads are all the
  same note — pushing the record of anything that *was* preserved off the card, which is
  the opposite of what this directory is for.

The page reads all of it: the set-aside card lists the stale paths under their own
sentence, in both languages, next to the ones a release wrote.

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

### A root-owned file in `.git` is healed before the fetch, not blamed on GitHub

The lock heal answers a *lock*; this answers the other file in `.git` that stops a
release without saying so: one **root owns** and the checkout's owner cannot write.
It arrives the same ways a root-run `git` arrives — a human at a provider console, an
older runner, a command typed as root — and it fails the same way the lock does, at
the *fetch*: git answers `insufficient permission for adding an object` or `Unable to
create .../.git/index.lock`, the runner prints git's own words under **"git fetch
failed (network or credentials)"**, and the box sits there, every two minutes, with
the journal naming a network for a file that is only mis-owned.

`perms-heal-logic` runs inside `branch_refs_read`, ahead of the one fetch in the
script — the plan reader, the adoption and the release all share it, so every fetch
path is covered by one call:

* **`.git` only.** That is all the fetch writes. A file outside it that the release
  also wires is the box-local-edit heal's business, and sweeping the whole checkout
  here would fight the hand edit that heal exists to preserve.
* **"not owned by the owner", not "owned by root".** Any other account fails the same
  write for the same reason, and the test is one uid comparison either way. Root is
  the name the journal prints because it is the one that arrives.
* **`find ... ! -uid`, never `-user`.** The sweep must not depend on a passwd lookup,
  which is exactly what is missing on a minimal box.
* **`chown -R` the whole git directory.** Owner, group and mode are one decision about
  a tree git rewrites constantly; a partial sweep leaves a directory that can be
  written but not traversed.

It **fails open**, and that is not the same as swallowing the failure: a repair that
cannot be made is logged with the paths it found, the fetch runs, and git's own error
then sits beneath a line naming the ownership that could not be fixed. It takes no exit
code and writes no preflight record — a box refusing a release over a file a later tick
could have healed is the deadlock all of this exists to remove. `tests/unit/
test_perms_heal.py` holds the block, the call site and the behaviour.

### The dot at the start of ` M`: a CR in the blob, which no checkout can clear

The dirty guard reports the checkout's own words, so a box can look like it is
refusing a hand edit when the edit is not there:

```
checkout has local changes - NOT deploying:
    M app/routes/admin_sekolah.py
```

That leading space is `git`'s code for *the worktree differs from the index*, and on
2026-09-30 a box sat on it for hours, refusing **every two minutes**, 12 commits
behind, with the recovery lever reporting `app/routes/admin_sekolah.py is set aside`
and the file dirty again on the next tick. It was not an edit. Commit `0afc68e` — the
commit that box was on — had committed that one file with **108 carriage returns**
among its 84,502 bytes (the same path has 0 at `ec6fc5c` and on `main`).

The repo carries `*.py text eol=lf`, and the two sides of that comparison are not
treated alike:

* the **worktree** is read through the clean filter, which normalises CRLF → LF
  before comparing — so a CRLF worktree is *clean* (this repo's own Windows checkout
  has ~100 of them and `git status` is empty);
* the **blob** is compared as stored. A CR in it can therefore never be matched, and
  nothing on the box moves it: `git checkout -f`, `git checkout HEAD -- <path>`,
  `git stash` all leave the same ` M`, and `git merge --ff-only` answers `Your local
  changes to the following files would be overwritten by merge`.

**That last line is why no release could rescue this box**: `local_edits_heal` (and
`sgfix`'s set-aside) preserved an edit with `git checkout HEAD -- <path>` — the one
operation that cannot clear this class — so the healer ran, reported success, and the
tree was dirty again. **Both healers now close this class themselves**, after the
ordinary restore and only when it is still not enough: HEAD's own bytes are written
into the worktree (staged where root may write, then copied as the checkout's owner),
and if the filters still make the path differ, one `"<path>" -text` line is added to
that checkout's own `info/attributes` — which wins over `.gitattributes`, is not
committed and changes no other clone. A path that is still different after both is a
refusal, never a merge over a tree that could not be cleaned. The record names
`verbatim <path>` and `attribute <path>` for each, so a local override an operator
cannot see is never the next thing that strands a box.

Such a blob is born from a commit built *around* the filters, which is what a
scripted commit does: `git hash-object -w --no-filters <path>` followed by
`git update-index --cacheinfo 100644,<sha>,<path>`. Neither normalises — where
`git add` would have. The remedy for one is one line, measured (the blob's CR count
goes 3 → 0):

```
git add --renormalize <path>
```

For a box already sitting on such a commit, two lines at the console make the tree
match whatever the blob holds — the filter is neutralised **locally** (`.git/info/attributes` wins over
`.gitattributes` and changes nothing in the repository), then
the blob's own bytes are written verbatim:

```
printf 'app/routes/admin_sekolah.py -text\n' >> /opt/scangrade/.git/info/attributes
cd /opt/scangrade && runuser -u scangrade -- sh -c 'git cat-file blob HEAD:app/routes/admin_sekolah.py > app/routes/admin_sekolah.py'
```

**This is now what both healers do by themselves**, so these two lines are only for a
box whose runner and lever predate the heal — every later box is healed by `sgfix`, or
by the release the runner fetches once its own heal has cleaned the tree. The next
tick then fetches, merges and runs every gate normally. After that release
the blob is CR-free and the attribute is no longer needed (`rm -f
/opt/scangrade/.git/info/attributes` is safe). The shape is deliberate — it makes
the commit the box is *on* reproducible, so if a gate refuses and the runner runs
`git reset --hard $BEFORE`, the worktree is still byte-identical to the blob and the
box cannot strand itself again. `git fetch origin main` + `git reset --hard
origin/main` also works and is shorter, but it skips the gates, the migration step
and the reload, leaving the box on new code that nothing has checked.

`tests/unit/test_committed_line_endings.py` is the guard: it scans the **index and
`HEAD` blobs** (not the worktree, which legitimately carries CRs here) with `git
grep -l -I --cached -e $'\r'`, refuses to read a `git` that could not be asked as
"clean", and names `0afc68e` as the commit that caused this. A scripted commit that
builds blobs with `hash-object --no-filters` is the thing it exists to catch.

### And the app refuses to *serve* such a checkout

Everything above is about a release landing. The gap that was left is the other
half: a box can be **serving** code whose own commit it cannot reproduce — the CR
blob is in `HEAD`, the runner refuses, and the site answers `200` for hours while
nothing says why. So the app asks at construction:

* **the rule is the page's own.** `dirty_kinds_state` already answers *which kind*
  of dirty the checkout is holding, so the startup check calls it rather than
  writing a second classifier — two implementations of one rule eventually disagree
  about the box, on the page that exists to explain it.
* **only measured evidence refuses.** A hand edit (the runner sets it aside as a
  patch), an untracked file (no blob to blame), a path whose attribute *asks* for
  CRLF, a directory that is not a checkout and a box with no `git` all pass. This is
  deliberately more forgiving than the armament check, and the reason is that it
  runs where the students are: a server is not taken down because `git` was busy.
* **the journal names it.** The app prints `SCANGRADE-UNREPRODUCIBLE`, the runner
  greps for it at the construct gate, and the release is quarantined under
  `checkout not reproducible (the app refused to serve it)` — which the
  deploy-status page has its own bilingual sentence for. Without the marker the
  refusal would read as "app did not construct" and send the next reader hunting for
  a Python fault that is not there.

One honest consequence: on a box with no `DIRECT_URL` the schema gate is already
blind, and this check is another thing a *serving* process can refuse for. It refuses
only on a measured blob, so it cannot fire on a healthy box — but a box in the strand
now says so instead of serving quietly, which is the whole point.

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

### Whether it clears itself, or needs you

The records above answer *what* refused a release. They do not answer the question
an operator actually opens the page with — **do I have to do anything?** — and the two
answers look identical from the outside: a fetch that cannot reach GitHub and a gate
that rejected the code both leave the previous release serving and nothing new
deploying, while the shape of the right response is opposite. The exit code cannot
tell them apart either, because `4` is a box edit the runner heals *and* a checkout it
cannot read, and those are opposite answers.

So the runner, which alone knows the step and the code together, decides and writes it
beside the records it already keeps:

```
/var/lib/scangrade-deploy/situation
```

Five positional lines: the disposition (`self` or `human`), what refused
(`preflight`, `quarantine`, `dependencies`, `unarmed`, `unhealthy`, `runner_copy`),
the gate when one is named, the one action that ends it
(`wait` | `release` | `rebaseline` | `console`), and when.

Two lists decide the pre-merge half, and they are lists rather than a range because
the disposition is read from the *step* that refused, never from the code:

* `SITUATION_SELF_GATES` — the next tick clears it: `fetch_failed`,
  `snapshot_refused`, `lock_refused`, `merge_refused`, `dirty_checkout`;
* `SITUATION_HUMAN_GATES` — static, so no tick ends it: `not_root`, `no_checkout`,
  `no_virtualenv`, `checkout_unreadable`.

A post-merge refusal is classified by its code: a failed dependency install retries
itself (exit 7 — it deliberately does not quarantine); a quarantined commit needs a
decision, and the *perf* gate gets `rebaseline` because retrying it against a yardstick
that no longer describes the box only re-refuses it; `runner not armed`, a drifted copy
and an unhealthy rollback are `console`, because no release request ends them.

The file is written by the `EXIT` trap (where the step and code are both in scope) and
cleared by a run that finishes and at the one `preflight_forget` call site — so its
presence means the last tick stopped, the same contract `last-stop` has.

`/super-admin/deploy-status` renders one card from it. A `self` departure says plainly
that the runner will retry and offers **no button** — waiting is not an action — while a
`human` one carries **exactly one** button, and the quarantine card's own request forms
are suppressed while that card is up so there is one place to press. The button is
offered only while a commit is actually held (`situation_state(..., held=...)` is handed
that answer by the quarantine, rather than deriving its own), and a refusal nothing here
can end says so instead of offering a button that cannot help.

`SITUATION_*` — the disposition and action vocabulary — lives in
`app/services/deploy_status_service.py`, and `tests/unit/test_deploy_situation.py` holds
the two lists against every `PREFLIGHT_GATE=` in the runner and against the page's own
`PREFLIGHT_GATES`, so a refusal added later cannot arrive as neither answer.

## One word when a box is stuck: `sgfix`

Everything above is the runner healing itself, and the heal has one property that can
make it undeliverable: **it travels in a release.** Several of the runner's refusals
are about the box's *arrangement* rather than about a release — a drifted copy of the
runner, an unarmed one, a missing virtualenv, a checkout git cannot read — and each of
them used to exit before the runner had fetched anything. So the box could not fetch
the fix for the thing that stopped it fetching, and it sat exactly where it was; the
only way out used to be a console session on a noVNC window, typing
`git -C /opt/scangrade …` by hand into a screen with no clipboard.

### The branch is read before the box refuses

Every arrangement refusal now reads the branch **first** (`branch-first-logic`). A
`git fetch` writes refs and `FETCH_HEAD` and touches neither the working tree nor the
index, so it succeeds on a checkout that is dirty, rolled back, held by a quarantine or
about to be refused — and whatever `origin/main` then names can be read without
merging anything. A box that cannot land a release therefore still obtains the current
lever, which is what ends the circle.

The read is deliberately weightless: it writes no record and takes no code of its own
(the refusal that follows *is* the reason the box is not deploying), it moves no
revision and reloads nothing, and a fetch that cannot reach GitHub logs its reason and
returns rather than changing the refusal that follows. It does not run where it cannot:
not before the root check (the fetch drops to the checkout's owner with `runuser`, which
needs root) and not behind the pause file (a freeze somebody asked for).

So the checkout carries a lever, installed by the same installer as
`scangrade-deploy`:

```bash
sgfix                 # recover this box, in one word
sgfix --dry-run       # say why it is stuck and what would be done; change nothing
```

It is the **only** installed name that is short on purpose: it is typed by hand,
where the length of the command is part of whether the job gets done. It takes no
argument that aims it (only `--dry-run` and `--help`) — the same rule the deploy
runner holds — because this is a thing root runs, not a thing anyone steers.

### Where the lever comes from, and why it is not the checkout

A lever installed only by a release that passed is a lever the box that needs it
cannot receive: the release is what is being refused. So the runner reads it out of
the **fetched commit** instead, on every tick, before any gate has spoken
(`fetch-lever-logic`):

* `deploy/scangrade-recover.sh` and `deploy/entrypoint.sh` are read out of
  `origin/main` with `git show`, which is a read of the object store and touches
  nothing in the tree;
* they are written under `/var/lib/scangrade-deploy/lever/deploy/` — the *template*
  and the *lever* — and `/usr/local/bin/sgfix` is rendered from that tree rather than
  from the checkout, because the checkout is the one thing a stuck box cannot update;
* a commit from before the lever existed carries neither file, and that is every
  box's first tick after this landed: nothing is installed and nothing is said;
* a lever that does not parse is refused, both blobs are read before either lands, and
  the install is a rename inside the target's own directory — so a lever that is
  running keeps the inode it started with, and a tick that changed nothing raises no
  mtime.

The fetch is the one step that succeeds on a dirty, rolled-back, `refused-before-merge`
or quarantined checkout — it writes refs and never the tree — which is why this is the
step the lever hangs off. The block moves nothing: no merge, no reset, no reload. It is
safe to run on every tick precisely because of that.

### The pipeline can reach the box: a plan, and the branch's own runner

`sgfix` still needs somebody at a console, and on the VPS that console is a noVNC
window where a long command has to be typed by hand — which is not a channel. So two
things the box obeys are read out of the commit it has already fetched
(`control-plan-logic`), and neither needs a release, a reload, an inbound connection or
a key:

**`deploy/control/plan`** — an order, obeyed **before** the release decision and
**before** the pause check. That ordering is the point: a box whose release is refused,
quarantined or frozen is exactly the box a push cannot reach, because the thing that
would apply the push is the thing that is refusing. Reading it before the pause check
is what makes `resume` mean anything — the pause check exits, so a command read after
it could never arrive — and reading it on *every* tick, rather than only on the
arrangement refusals the lever already answers, is what lets a box that is merely
behind still be told what to do.

| command | what the box does |
|---|---|
| `recover` | runs its own lever (`sgfix`'s code, installed from the same fetched commit) and ends that tick — the lever runs a release, so carrying on would deploy twice for one order |
| `release` | writes the same `requests/release` the status page writes, so the quarantine on the held commit is lifted |
| `rebaseline` | writes `requests/rebaseline`, so the perf gate re-measures the box instead of comparing against a stale baseline |
| `pause` / `resume` | creates and removes `/etc/scangrade-deploy.pause` — the exam-week freeze, set and lifted from the pipeline |
| `none` | the no-op, so the channel has a state meaning "nothing is being asked" rather than an absent file |

Publish one — one word, no arguments to remember:

```bash
bash deploy/scangrade-plan.sh recover "the schema gate is holding 2162e18"
```

It writes the plan, commits it and pushes, refusing a command the runner does not know
(so a typo stops here rather than at the far end of a push), refusing to publish from a
branch the box does not read, and never forcing. The box reads it on its next tick, at
most one timer interval away.

A plan is **content-addressed**: the runner keys its record on the plan's own hash and
obeys it once, so a `recover` does not re-run every two minutes. `issued:` is what makes
a re-issued order a *new* plan, which is why the publisher stamps a fresh one every run.
An unknown command is refused **by name and recorded** — never ignored, because a plan
the reader silently skips is a box nobody can command *and* nobody can tell is
uncommanded. The record of what was obeyed lands in
`/var/lib/scangrade-deploy/control/<plan-hash>.applied`, newest few kept.

**The branch's runner is adopted before anything is judged.** A box can only be
commanded by the runner it is running, so a runner older than the plan's vocabulary is
a box the pipeline cannot reach — the same deadlock one level up. Before the pause check
the runner therefore compares itself with `origin/main:deploy/scangrade-deploy.sh` and,
when they differ, re-executes the branch's copy. On a box that has caught up the bytes
are identical, so this costs one `cmp` and does nothing; on a stuck box it means every
merged change reaches it on the next tick, with no release and no console.

Three details decide whether that handover works or quietly fails:

* **one level only.** `SCANGRADE_RUNNER_ADOPTED` is exported before the handover and the
  adopted runner returns immediately when it is set, so a branch whose runner kept
  differing cannot re-exec itself forever;
* **the lock is released first.** `exec 9>&-` — `flock` is per *open file description*,
  so a re-exec that kept fd 9 would hand the adopted runner a lock this process already
  holds, and it would answer "another deploy is already running" and give up. An
  adoption that always fails, looking like one that worked;
* **Gate 0 is not weakened.** The refusal of an installed copy is unchanged; what it now
  also accepts is a file whose bytes hash to the blob that was adopted — which is the
  same condition it always held, "your bytes are the branch's", and not a permission to
  run whatever the runner last wrote.

A branch whose runner does not parse leaves the working one running (this runs on a box
already in trouble), and a tick pays **one** `git fetch`: the plan read, the adoption and
`branch_refs_read` all share it, and the release's own fetch consults the same flag.

What this does *not* claim: a box whose runner predates this block cannot read a plan or
adopt one, so it still needs the one console line — `sgfix`, or the `git show`
one-liner in `deploy/scangrade-recover.sh`'s header. That is the last console session
the arrangement should ever need, because after it the adoption keeps the box current.

The visible consequence: `/usr/local/bin/sgfix` execs
`/var/lib/scangrade-deploy/lever/deploy/scangrade-recover.sh`, and the checkout's copy
of that file is only the fallback on a box that has not ticked yet.

### What it does, in order

1. says why the box is stuck, read from the runner's **own records** (the quarantine,
   `refused-before-merge`, `unarmed`, `last-stop`) and the tail of the journal —
   never a second opinion about the box;
2. applies the migrations `apply_migration.py --verify` reports as missing, each one
   after its own rolled-back trial run. Before the release and not after: the schema
   gate refuses a release whose database is behind its code, so applying them later
   would only earn that refusal;
3. runs one release;
4. answers the refusals it can, and runs the release once more after each: a
   **box-local edit** is set aside — a patch against `HEAD` for a tracked path, the
   whole file for one `HEAD` has never seen — the **schema** quarantine step 2 just
   answered is lifted for one attempt, and an **unarmed box** is armed by running the
   script the runner's own refusal names (`deploy/arm-auto-deploy.sh`). The **perf**
   gate is answered instead by writing the same re-measurement request the status
   page's button writes, so the release is judged against a baseline re-taken from
   the box as it is now;
5. whenever the checkout *still* did not move, names what refused it — read from the
   runner's own quarantine, `refused-before-merge` or `unarmed` record, *after* the
   attempt, so a retry something stops is reported as that thing rather than as
   "nothing happened";
6. verifies: the checkout moved, the app answers, and everything it did is in the
   record.

The naming in step 5 is not decoration. Measured on this box (2026-09-30): the lever
set aside `app/routes/admin_sekolah.py`, ran the release a second time, and printed
only `the checkout did not move` — while the runner had already written
`perf gate (p50 1359 ms vs 374 ms)` into its own quarantine file. `NOT MOVED` reads
as "nothing is happening"; the gate's name is what sends an operator to the re-baseline
button. A refusal record from the *first* attempt is never quoted as a second's: the
reader only trusts a `refused-before-merge` whose bytes differ from the ones already
there when the retry began.

### The unarmed shape, and why no release can clear it

An unarmed box is the one arrangement refusal the pipeline cannot fix by shipping
code: the runner writes `/var/lib/scangrade-deploy/unarmed` and exits `15` at its
armament preflight, **before it fetches anything**, because a box that cannot measure
a release must not deploy one. So the lever answers it the same way the runner's own
refusal tells an operator to: it runs `deploy/arm-auto-deploy.sh` (the installer the
arm script wraps), keeps every line in the record, and runs one release once more.

It is not a bypass. The arm script ends by re-running the same `--check` the runner
runs, and the runner re-checks the armament on the next tick — so a gate still
missing what it needs leaves the box unarmed, `unarmed` is written again, and the
release is refused again. What the lever removes is the *console session*, not the
check: `arm-auto-deploy.sh` needs root, and `sgfix` is already root, which is exactly
the hop a noVNC session was doing by hand. This shape ranks above the box-local edit
in the lever's own precedence, because the runner's armament preflight runs before its
dirty check — on a box that is both unarmed and dirty, the armament is what refused
the run.

### What it refuses, deliberately

* it never resets a branch and never pushes — the checkout only ever moves by the
  runner's own `git merge --ff-only`;
* it never discards an edit without writing it down first. The record is written
  **before** the tree is touched, an entry that cannot be recorded is left exactly
  where it was, and the run says so. A recovery tool that loses a box's only copy of
  a fix is worse than the console session it replaces;
* it answers refusals only by doing the thing the gate named, never by clearing it:
  the schema gate by applying the migrations it wants (step 2) and retrying; the perf
  gate by asking the runner to re-measure the box, exactly as the status page's
  re-baseline button does; the armament by running the arm script and letting the
  runner's next `--check` decide. None is a bypass — every gate runs again and can
  still refuse. A theme, smoke or claims refusal is a statement about the release with
  no action this lever can take, so it is named and the run stops;
* **Gate 0 does not apply to it.** That gate exists to keep a drifted *runner* from
  deploying from somewhere other than the checkout; the lever runs no gates at all —
  it starts the unit, which is where the gates live — so requiring the block in it
  would refuse on a box that is working correctly.

Each run writes `/var/lib/scangrade-deploy/recover/<stamp>.txt` — the reason, what it
set aside and where the patch is, which migrations it applied, what each release
attempt did — and keeps the newest five, with each set-aside tree going when its
record goes. A recovery that cannot be read back afterwards is indistinguishable
from a box somebody broke by hand.

### A box that cannot fetch at all

There is exactly one shape this does not reach, and it is the one the boxes were in
the day the lever was written: a runner from **before** the fetch-lever code, whose
dirty check refuses *before* its own fetch. That runner never learns what
`origin/main` holds, so it can never run the block that would hand it the lever — no
push can reach it, and the fix for that refusal is carried by the release the refusal
is holding.

On a box in that state the way out is one line. It reads the lever out of the commit the
box has **already fetched**; a fetched ref **older than the lever** — a box that stalled
before `deploy/scangrade-recover.sh` existed — is the other state, and the same line
answers it by fetching `origin` itself and reading again. Both are typed, not reasoned
about: the line is the one thing to remember.

```bash
sudo bash -c 'runuser -u scangrade -- git -C /opt/scangrade show origin/main:deploy/scangrade-recover.sh > /tmp/sgfix.sh || { runuser -u scangrade -- git -C /opt/scangrade fetch -q origin && runuser -u scangrade -- git -C /opt/scangrade show origin/main:deploy/scangrade-recover.sh > /tmp/sgfix.sh; } && bash /tmp/sgfix.sh'
```

Four things make that the right shape here rather than a trick:

* **`git show` is a read of the object store**, so the common case works on the
  checkouts this section is about — dirty, rolled back, refused or quarantined — where
  a fetch, an install or a merge does not;
* **the fetch is a fallback, and only that.** It happens when, and only when, the read
  came up empty. A box that cannot reach GitHub is exactly the box this line is for, so
  fetching unconditionally would hang the one case that needed nothing fetched;
* **every git call is made as the deploy's own user** (`runuser -u scangrade`),
  including the fetch — that one *writes* refs, into a checkout it does not own, which
  is the disagreement the note at the end of this section is about;
* **it writes the lever to a file and runs only what it read.** A file is what an older
  lever needs: a lever old enough to still carry the `sudo -E bash "$0"` hop cannot
  re-run itself out of a **pipe** (a piped script has no `$0` — it is the shell's own
  path), so a one-line pipe can die with a message about the wrong thing. And the `&&`
  before `bash` is load-bearing: `git show … > /tmp/sgfix.sh` truncates that file before
  git runs, so running it regardless would execute an empty script, exit `0`, and report
  a recovery that changed nothing at all.

What it runs is the real lever, not a simplified recovery: a box-local edit is set
aside **with a patch and a record** rather than stashed, the migrations the live schema
is missing are applied, one release runs, and the run verifies itself and writes down
everything it did.

On a console with no clipboard, and when the fetched ref already has the lever, the same
recovery is two shorter lines:

```bash
git -C /opt/scangrade show origin/main:deploy/scangrade-recover.sh > /tmp/f.sh
bash /tmp/f.sh
```

The lever drops to the checkout's owner itself for every git read and write, so nothing
here has to be typed as `scangrade` — and the edit it moves is preserved as a patch under
`/var/lib/scangrade-deploy/recover/<stamp>/`, named in the record beside it.

Run any of these without root and the lever refuses, printing the one line above — a
file has a `$0`, so the hop it prints is one that works. (The hop used to be keyed on
`$0` itself, which is the shell's own path for a piped script and `bash`'s meaning in the
current directory for a bad one; `BASH_SOURCE` is what it is keyed on now.)

The plain console steps below are left for one residual case, and it is narrower than it
looks: a box whose fetched ref predates the lever **and** that cannot reach GitHub at
all. There the line has nothing to read and nothing to fetch, and no release could land
either, so the checkout has to be cleared by hand:

```bash
sudo -i
cd /opt/scangrade
git status --porcelain        # what is in the way; read `git diff` before discarding it
git stash                     # or: git checkout -- <path>, once you have read it
systemctl start scangrade-deploy.service
journalctl -u scangrade-deploy.service -n 60 --no-pager
```

One complication from that first run, and it is about *who* git is: the deploy runs as
`runuser -u scangrade`, so a box-local edit stashed by `root` can leave the deploy
disagreeing about one path (line endings are the usual reason). Ask git as the deploy
user rather than as root — `runuser -u scangrade -- git -C /opt/scangrade status
--porcelain` — and read `--numstat`: a whole-file `N/N` on one path is a line-ending
rewrite, a small one is a hotfix nobody committed.

Once a release lands on a box carrying the fetch-lever code, the lever is refreshed
out of `origin/main` on every tick from then on, and the console is not needed for
this class of stall again. `--check` reports the lever separately from the gates it is
armed for: a missing lever is worth saying out loud and is not a reason to hold a
release, since folding it in would refuse every release on every box installed before
the lever existed — including the one that installs it.

## A release that landed is not a release that is being served

Every other figure on `/super-admin/deploy-status` is read out of a file on the box,
and all of them can be perfectly happy while the process answering the page is still
running older code. The merge succeeded, the `systemctl reload` failed or has not
happened yet, and from the outside the site looks exactly like a box with nothing
waiting. That was the shape production was in for a day and a half: the checkout had
fetched, the journal said a release was refused, the site answered, and nothing on the
page could tell "the code being served is the code the box holds" from "the code being
served is four commits older".

So the app reads its **own** commit — `app/utils/build_info.py` — and the page places
it against the checkout's `HEAD`:

| placement | what it means |
|---|---|
| `current` | the last release is loaded in this process |
| `behind` | the checkout moved on and this process never came up on it — a release that landed and was not loaded. The only one of the five that changes the headline verdict |
| `ahead` | the checkout moved *back* under a running process |
| `unknown` | the commit is not in this checkout's history at all — a reset, or a process started from elsewhere |
| `unreadable` | the process could not read its own commit, so nothing is known |

Three decisions make the reading worth trusting, and each is a test:

* **it reads where this code lives, never `SCANGRADE_REPO`.** That variable names the
  checkout the deploy will act on, and the two agree until a release has moved the
  checkout past the running process — the one moment the distinction *is* the answer.
  A process that read its own commit from there would report the checkout's `HEAD`
  under the name of the running code;
* **it is resolved when the module is imported** and handed out by identity. Re-reading
  it per request would follow the checkout as it moves, which the checkout reading
  already does; this one has to be fixed until the process is replaced, because that is
  the fact the page needs. It is resolved at *import* rather than at first use for a
  reason that is easy to miss and fatal to the check below: a worker that was never
  asked before a release merged would take its reading afterwards, and would then report
  the newly merged commit while running the code it loaded yesterday — and it would go on
  reporting it, because the memo fills once. At import, python has just loaded these
  files, so the commit the checkout held then is the commit whose content is in memory;
* **`unknown` is never a number.** A commit this repository does not have is reported
  as a reason, not as "zero commits away" — those two are opposite answers, and the
  second one reads as reassurance.

The reading sits above `dirty` and `behind` in the verdict order, and below every
stop. Those two describe the *arrangement* — what the next tick would do — and this one
describes what a student is being served right now; a `refused` record or a heal the
runner performed is a stop, and a stop stays the headline.

### And the deploy asks it too, before it trusts any gate

Everything *below* the reload claims to measure "this release": the smoke test signs
in as each role, the claims gate re-reads the published table, the perf gate compares
a reference load with the last release that passed. All three rest on the premise that
the code answering is the code that was just merged — and a `systemctl reload` that
quietly did nothing breaks it without breaking any of them.

So the app publishes the commit it is serving on `/health` (`app/__init__.py`,
`served_commit`, fenced with `served-commit:start`), and
`deploy/served_commit_gate.py` compares it with the commit the runner just merged. A
mismatch refuses the release and quarantines it, exactly like a failing gate.

Three things about it are decisions rather than plumbing:

* **One probe is not a verdict.** gunicorn's reload is graceful, so a worker that is
  finishing an in-flight request can answer the first ask with the old commit. The gate
  asks five times, two seconds apart, and refuses only when *no* probe reported the
  merged commit — the perf gate's rule about a divergence no second probe confirmed.
* **"Could not measure" is not "bad release".** An app that cannot be asked is a box
  problem, and the health probe already owns reachability. Only a *reading* that
  contradicts the merge refuses. The gate's refusal exit code is `3`, deliberately not
  `1`, because python exits `1` on an uncaught exception and a check that crashed must
  never be read as a check that refused.
* **Silence is evidence only when this release ships the reading.** A release that
  ships it can always name its commit, so an app answering without one is not this
  release — the reload did not take. A release that *predates* the reading cannot be
  asked, and refusing it would quarantine the very commit that introduces the check.
  The gate is told which file to ask (`--reporter`) and grep it for the marker, so the
  two halves cannot drift apart: `tests/unit/test_served_commit_gate.py` reads the
  marker out of the gate and requires it in the app.

The marker is the one thing here with a source-level coupling, and it is deliberate:
`REPORTER_MARKER` in the gate and the fence in `app/__init__.py` are the same string,
held together by that test. If they ever diverge the gate degrades to "cannot measure"
on every tick — loudly, in the journal, never as a silent pass.

### The other process in the checkout: the Celery worker

Gunicorn is not the only process holding this release. The Celery worker imports its
task modules once, at start-up, and keeps them in memory for the life of the process,
so reloading the app leaves it answering with the previous release. That is a
**half-deployed release**, and it has already bitten this box: `page_index` was added
to the OMR task's signature and to its caller in one commit, `systemctl reload`
touched gunicorn alone, and every scan then failed with

```
process_omr_scan() got an unexpected keyword argument 'page_index'
```

— the caller new, the worker old, and the box reporting a successful release.

So the worker answers the same question, on the broker it already uses.
`app/celery_app.py` registers a `served_commit` control command (fenced with
`worker-commit:start`) that returns `build_info.snapshot()` — the commit whose code
the worker loaded, resolved at import — and `deploy/worker_commit_gate.py` broadcasts
it and compares the replies with the merged commit. It runs between the served-commit
check and the smoke gate, and a mismatch refuses and quarantines the release exactly
like a failing gate.

Three differences from the app gate, each of them a decision:

* **Silence is read twice, and a dead worker refuses.** The app always has an HTTP
  surface, so a release shipping the reading can always name its commit when asked.
  The worker has no such floor: a worker that is *down* and one *built before the
  reading* both answer nothing to `served_commit`. They are told apart by a second
  question — Celery's built-in `ping`, which every worker answers whatever code it
  loaded, because it is not this reading. **Nobody answering either question is a
  worker that is not running at all**: a release with no worker cannot process a
  single scan, so that is exit `4` and a refusal, recorded as `worker down (…)` — a
  distinct gate key from `worker_commit`, because the remedy is "bring the worker
  back", not "find the commit it is on". An answered `ping` with an unanswered
  `served_commit` is a worker built before the reading, which says nothing about *this*
  release and stays exit `2` (never a rollback), and a worker that **answers and
  names a different commit** is exit `3` as before.
* **It is asked, not the CLI.** `celery inspect <command>` cannot see a custom
  command: its argument parser collects the command names when Celery is imported,
  before an application has registered anything. The gate therefore imports the
  worker's own app and uses `control.broadcast`, which reaches the registered command.
* **A box with no worker unit is not a failure.** `scangrade-celery` missing means
  async OMR simply queues; the block logs that and moves on.
* **It runs as the app's account, not the web server's.** The two processes hand each
  other *paths inside this checkout* — the scan route writes the upload under
  `app/static/uploads/scans/` and enqueues the worker with that path, not with the
  bytes — so ownership and mode decide, per file, whether the work can be done at all,
  and two accounts are two answers to that one question: a `0750` directory, or any
  umask stricter than the writer's, leaves the file the app has just written
  unreadable to a worker running as `www-data`, and the task fails on a file that is
  plainly there. `deploy/celery.service` therefore names the same `User=`/`Group=` as
  `deploy/scangrade.service` — the pair `install-auto-deploy.sh` already reads as the
  identity the box runs as, and from which it builds the deploy state directory's
  ownership. The installer installs the worker's unit as well (as
  `scangrade-celery.service`, the name the roster and the runner use), because the
  runner restarts the units the roster names and installs none; and because a process
  keeps the account it started as, a unit that run wrote is followed by a restart of
  the unit — the tick after an arm deploys nothing, so no later release would do it.

### Every long-lived process, and the roster that closes the set

The two gates above are hand-wired: each names one process, in its own fence, with
its own command line. That is an arrangement that was true when it was written, and
nothing keeps it true. A third long-lived unit on the box — a scheduler, a helper
with no HTTP surface — imports the release once at start-up and holds it across
every reload, and no gate asks it, because no gate knows it exists. That is the
`page_index` half-deploy one process further out.

`deploy/long_lived.py` is the **roster**, and it is deliberately closed. It names the
units that run this checkout and how each is asked:

* `http` — the app publishes the commit it serves on `/health`; the gate reuses
  `served_commit_gate.py`'s reading;
* `celery` — the worker answers a control command; the gate reuses
  `worker_commit_gate.py`;
* `attestation` — a generic helper writes `app/utils/process_attest.py`'s file at
  start-up (the commit whose code it loaded, its pid, and when), and the gate ties
  that file to the pid `systemctl show -p MainPID` is running now — an old file from a
  previous run does not count.

`deploy/process_commit_gate.py` then reads the box's own unit files
(`/etc/systemd/system/*.service`) and **discovers** any unit that runs this checkout
but the roster does not carry (`WorkingDirectory=` or an `Exec*=` under the checkout).
An uncovered unit is a **refusal** — `process commit (…)`, exit `3` — so a helper
cannot be added to the box without the deploy noticing it. That is the difference
from the two gates above: they check that a *known* process is current, and this one
checks that the set of processes is *known*.

The runner gained a `process-commit-gate` block after the worker block
(`RUN_STEP="processes"`), and its restart loop now reads the roster
(`deploy/long_lived.py --units`) so a new unit is restarted by being in the roster
rather than by a second edit that drifts. The generic helper is not probed for
liveness the way the worker is, so a process that cannot be asked is exit `2` — never
a rollback — and only a readable disagreement, or an uncovered unit, refuses.

**A refusal names the process, how far off it is, and the remedy.** The refusal used
to stop at two shas — "the worker reports `bbbbbbb`" — which told an operator that
something diverged but not what to do about it. The next move is one of exactly two,
and they are different: the process missed the reload (**restart the unit**), or the
box is on history the merged commit never had (**re-baseline the box**).
`deploy/commit_divergence.py` reads the commit graph and says which one it is:
*behind* (the unit's commit is an ancestor of the merged one — restart it), *ahead* or
*diverged* (the box holds newer or other code — re-baseline it), or *unknown* when git
cannot answer, in which case the sentence says so rather than guessing. The served,
worker and process gates each append it to their refusal (the runner passes `--repo`
and `--unit`), so the quarantine record and the card carry e.g. "running `62fa16f`,
3 commit(s) behind `a8889f8` — … restart `scangrade-celery`" instead of two hashes.

## The way in: the box installs its own key

Every section above assumes something can reach the box. A runner that refuses
*before* it fetches breaks that assumption completely: no push, no release request and
no page can move it, because the thing that would apply any of them is the thing that
is refusing. Measured on the box this was written for — 19 commits behind with
`M app/routes/admin_sekolah.py` — every stored password and all three local ssh keys
were refused, and the provider's console sat behind a captcha. The only channel left
was a human at a terminal that cannot paste.

So the box installs its own way in, out of the repository it already trusts. Every
`deploy/authorized-keys/*.pub` is appended by the runner to the deploy account's
`authorized_keys`, once, on the first tick after the release that carries it. The
private half stays on the operator's machine; a public half in a git history is not a
secret, and it is the half that opens nothing.

Four properties, each of them a way this could be an install in name only:

* **idempotent by key material, not by line.** A second tick appends nothing, and the
  same key under a different trailing comment is the same key. A runner that appended
  every two minutes would grow that file forever and turn one inspection into a scroll;
* **appended, never rewritten.** A key somebody else put there is not this runner's to
  remove — and removing one of these is an edit on the box, not a change here:

  ```bash
  sed -i '/ scangrade-deploy$/d' /root/.ssh/authorized_keys
  ```

* **plain public keys only.** A line carrying `authorized_keys` options is refused and
  named in the journal, because a file sshd parses is not a place for this script to
  author options into — options are how a copied file becomes a command on every login;
* **nothing is printed from the key itself.** Only the file it landed in, so a file
  that should never have been called `.pub` is not echoed into the journal by the
  install that refuses it.

It runs **after the root check** — writing root's key is what it is for — and **before
the pause check**, deliberately: a box frozen for exam week is exactly a box nobody is
watching, and a runner far enough behind to be refusing before its own fetch is exactly
a box somebody would otherwise have to reach by hand. An absent
`deploy/authorized-keys/` is a no-op, which is what lets this land on a box whose
release predates it.

The path is root's own home from `getent passwd root`, and `SCANGRADE_AUTHORIZED_KEYS`
overrides it for an `sshd_config` that names a different file. Guards:
`tests/unit/test_deploy_key.py` (11 tests — the install, the second tick, other keys,
the comment, the options line, the no-op, the order, and the shape of the directory
itself, including that no private half is committed beside the public one),
mutation-checked **10/10** (`.freebuff/mutate_deploy_key.py`).

### What it does not do

There is still exactly one box this cannot be installed on by itself: one whose runner
predates the block, because that runner is the thing that would install it. For that
box, the way in arrives with the recovery — one line typed at the console, and every
recovery after it is an `ssh`:

```bash
ssh -i ~/.ssh/scangrade_deploy root@<box> 'systemctl start scangrade-deploy'
```

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

`--verify` is also run by the deploy runner itself, as the schema gate (see above):
a release whose declared objects are not in the database is refused and quarantined,
so a migration written but never applied can no longer reach production behind a
green box.

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
