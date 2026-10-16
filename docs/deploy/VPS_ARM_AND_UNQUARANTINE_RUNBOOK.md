# Runbook — arming the VPS, and releasing a held commit

Two jobs, both at a root console: **make the box able to check a release** (§2), and
**get a release a gate has held unstuck** (§3). Everything here is a command, what it
prints, and what to check before the next step. The *why* is in
[docs/AUTO_DEPLOY.md](../AUTO_DEPLOY.md) — this page does not restate it.

Names used throughout: service `scangrade`, checkout `/opt/scangrade`, its owner is
the service user (`stat -c '%U' /opt/scangrade` → `scangrade`), and every path below
is a literal in [deploy/scangrade-deploy.sh](../../deploy/scangrade-deploy.sh) or
[deploy/install-auto-deploy.sh](../../deploy/install-auto-deploy.sh).

Run as root. Where a step must run **as the service user**, it says so.

## 0. Read before you touch

```bash
sudo bash /opt/scangrade/deploy/arm-auto-deploy.sh --check
echo "check exit: $?"          # 0 armed · 1 not armed · 2 refused (no root / no terminal) · 3 installer or roster unusable
```

| the line it prints | what it means |
|---|---|
| `started as : …` | which user took this reading — the report changes with the caller, on purpose |
| `installer : … bytes` | the installer exists in the checkout (missing = nothing can be armed) |
| `runner : MISSING — /usr/local/bin/scangrade-deploy` | never installed: nothing is deploying at all, and only the installer creates it |
| `runner : renders from the checkout — …` | `/usr/local/bin/scangrade-deploy` execs `deploy/scangrade-deploy.sh`: **the arrangement** |
| `runner : installed but never rendered (@REPO@ is still in it)` | installed and unusable — no release has been deployed by it |
| `runner : a COPY of the deploy script, …` | a snapshot; the line under it names the gate blocks it lacks (`no theme_gate, no claims_gate, …`) — every release it shipped went unchecked |
| `in the repo: deploy/scangrade-deploy.sh has … gate block(s)` | `theme_gate claims_gate perf_gate quarantine schema_gate seb_door_gate` — what the commit on the box can check |
| `snapshot : present/missing` | `/usr/local/bin/scangrade-db-snapshot`; missing = a migration release has no recovery point |
| `recover : present/missing` | `/usr/local/bin/sgfix`; reported but **not** part of armed — it is the way back, not a gate |
| `schema : present — the schema gate can hold a release against the catalogue` | `DIRECT_URL=` in `/opt/scangrade/.env`; missing disarms the box (the value is never printed) |
| `smoke : present — enforcement true/false` (same for `claims`, `perf`) | `/etc/scangrade-smoke.conf`, `/etc/scangrade-claims.conf`, `/etc/scangrade-perf.conf`; `false` means that gate reports and does not roll back |
| `seb : present — one throwaway paper as the smoke pupil, …; enforcement true` | the SEB door gate + a `SMOKE_MURID` in the smoke conf. `MISSING — no SMOKE_MURID` means the door gate reports "could not measure" on every release |
| `browser : present (/usr/bin/chromium…)` | the touch, render **and SEB client-half** measurements need one; missing disarms the box |
| `roster : 500 murid / 30 guru (/opt/scangrade/.freebuff/lt_roster.json)` | the claims and performance gates draw sessions from it; missing disarms the box |

**Check after step 0** — the second caller, which is the one the app and the runner's
preflight use:

```bash
runuser -u scangrade -- bash /opt/scangrade/deploy/arm-auto-deploy.sh --check
echo "check exit as the service user: $?"
```

Both callers must reach the **same verdict** (`ARMED` / `NOT ARMED`). The reports
differ legitimately — the three confs are `0600 root:root`, so the service user reads
them as `present (… cannot read it)`, which is *present*, not absent. What must not
differ is the verdict: if the two disagree, the runner refuses the whole run with
**exit 15** and writes both readings to `/var/lib/scangrade-deploy/unarmed` (§4).
A reading that could not be taken at all (no `runuser`, a user that cannot execute the
checker) is not a disagreement — the run is judged as root and says so in one line.

Also read, if present:

```bash
sudo cat /var/lib/scangrade-deploy/situation       # 5 lines: disposition · source · gate · action · when
sudo cat /var/lib/scangrade-deploy/last-stop       # 4 lines: step · when · exit code · commit under judgement
```

## 1. Arm it

**One command, from an ordinary login** — elevates once, keeps the whole installer log,
and prints what the box is running *before* and *after*:

```bash
bash /opt/scangrade/deploy/arm-auto-deploy.sh
```

It reads the roster from `/tmp/lt_roster.json` **before** asking for a password (a
roster it cannot parse exits `3` with nothing installed), places it at
`/opt/scangrade/.freebuff/lt_roster.json` (mode `0600`, owner = the service user,
because the gates run as that user), then runs the installer and keeps every line in
`/tmp/installer.log`.

**Already root, direct:**

```bash
bash /opt/scangrade/deploy/install-auto-deploy.sh
```

It fast-forwards the checkout, renders `/usr/local/bin/scangrade-deploy` and
`/usr/local/bin/scangrade-db-snapshot` from `deploy/entrypoint.sh`, installs
`/etc/systemd/system/scangrade-deploy.{service,timer}`, enables the timer, and does one
test run. It is also what arms the gates, each only after one real measurement here
passes: `SMOKE_ENFORCE` after every configured role signs in, `TOUCH_ENFORCE` and
`RENDER_ENFORCE` after the browser readings, `SEB_ENFORCE` after the SEB door gate
exits 0, `CLAIMS_ENFORCE` after the claims gate exits 0. `PERF_ENFORCE` is true from
the start.

**What it printed at the end — three sentences, and only one is armed:**

| it says | meaning |
|---|---|
| `ARMED — the runner renders from the checkout and every gate above has what it needs.` | done; the exit code is the installer's, `0` |
| `INSTALLER OK, BUT THE BOX IS NOT FULLY ARMED — read the lines above.` | the installer worked; a gate still says "cannot measure" — its reason is in `/tmp/installer.log` |
| `THE INSTALLER FAILED (exit N) — nothing was half-installed.` | the installer's own codes: **4** dirty checkout · **5** not a fast-forward · **6** a file missing from the release · **7** the theme gate (unreadable templates, disarmed, or the service/app did not come back) · **8** a launcher still carrying `@REPO@` |

**Check after step 1:**

```bash
sudo bash /opt/scangrade/deploy/arm-auto-deploy.sh --check; echo "exit: $?"        # expect 0 + ARMED
runuser -u scangrade -- bash /opt/scangrade/deploy/arm-auto-deploy.sh --check      # same verdict
head -3 /usr/local/bin/scangrade-deploy                                           # must NOT contain: REPO="@REPO@"
sudo grep -E '^(SMOKE|TOUCH|RENDER|SEB|CLAIMS|PERF)_ENFORCE' /etc/scangrade-smoke.conf /etc/scangrade-claims.conf /etc/scangrade-perf.conf
systemctl list-timers scangrade-deploy.timer                                       # next check due within 2 minutes
sudo systemctl start scangrade-deploy.service                                      # one tick now, instead of waiting
journalctl -u scangrade-deploy -n 40 --no-pager
```

`SEB_ENFORCE` is the one to read carefully: `false` means the door gate is **not**
armed, and the installer's own log line says which it was — `the SEB door could not be
measured (exit 2)` (usually no demo paper: write one with `manage.py demo-exam`, then
re-run the installer) or `the SEB door did NOT hold (exit N)` (a gated paper opens in a
plain browser, an honest client is refused, or another key's header is admitted — fix
the door, then re-run). Neither is a licence to set it by hand: a gate armed against a
broken measurement refuses good releases.

If `install-auto-deploy.sh` is not in the checkout yet (a box that has never deployed
this code), run the handed-over copy once: `bash /tmp/sgdeploy2/install-auto-deploy.sh`.

## 2. Un-quarantine a held release

**Read the hold first.** These are the box's own words; nothing here is a guess:

```bash
sudo cat /var/lib/scangrade-deploy/quarantined
# line 1  the full sha refused      -> the commit under judgement
# line 2  when it was refused (ISO)
# line 3  which gate refused it     e.g. seb door gate (the SEB toggle is not enforced on the served release)
# line 4+ up to 6 lines of that gate's own finding, cut at 400 chars each
sudo ls -t /var/lib/scangrade-deploy/refusals | head        # the same record, kept past later refusals
sudo cat /var/lib/scangrade-deploy/last-deploy              # the release actually serving: sha · when · recovery point
journalctl -u scangrade-deploy -n 120 --no-pager | grep -E "QUARANTINED|did not pass verification|ROLLED BACK"
```

Same reading without a shell: `/super-admin/deploy-status` → the held-release card
(commit, gate, the gate's numbers, and the release button).

**Then pick the lift, in this order:**

**(a) Push a fix.** A new commit on the branch lifts the quarantine by itself — nothing
to run. Check the next tick:

```bash
journalctl -u scangrade-deploy -n 20 --no-pager   # expect: quarantine lifted: main now points at <sha>, not the refused <sha>
```

**(b) Release this exact commit, once** — when the commit is right and the box was
wrong (or the missing migration is now applied):

```bash
sudo touch /etc/scangrade-deploy.release
```

The same act from the page writes `/var/lib/scangrade-deploy/requests/release`. It is
**one-shot on purpose**: the run removes the file, so if the same gate refuses the same
commit again it quarantines again — a release is never a standing permission.

**(c) Schema gate (exit 18): apply the migration first, then (b).**

```bash
sudo /opt/scangrade/.venv/bin/python /opt/scangrade/deploy/apply_migration.py \
    /opt/scangrade/supabase/migrations/NNN_x.sql --commit --repo /opt/scangrade
sudo /opt/scangrade/.venv/bin/python /opt/scangrade/deploy/apply_migration.py --verify --repo /opt/scangrade
#   expect  "NN in, 0 partial, 0 out, …", exit 0.  Exit 6 = a declared object exists nowhere:
#   apply the migration that declares it before asking for the release.
sudo touch /etc/scangrade-deploy.release
```

**(d) Performance gate — read which of its four answers it gave, because only one is
`--rebaseline`.** The runner's exit is **10** (it rolled back and quarantined); the
`4` in the journal is the *gate's own* code, and the gate's own line names the cause:

| what the journal says | lift |
|---|---|
| `perf gate: NOT ARMED — harness <path> is missing` | the checkout, not the box: the harness is a committed file |
| `perf gate: NOT ARMED — <roster> holds N murid / M guru, but … are needed` | the roster: place it and re-run the installer (§1) — one account per session is required |
| `perf gate: NOT ARMED — no --base URL` | `/etc/scangrade-perf.conf`: it must be `https` in production, because `SESSION_COOKIE_SECURE` means a plain-HTTP login cannot keep its cookie |
| `perf gate: NOT ARMED — <mismatch>` (the baseline describes a differently-shaped run) | **`--rebaseline`** — the box's measurement conditions changed, so the old baseline is not the thing to compare against |
| `perf gate: REGRESSED — …` (and `PERF_ENFORCE=true`) | a faster release, or a deliberate `--rebaseline` after reading the ratios |

A perf refusal is the one hold whose own record carries the **ratios** it refused on
(each `    - ` line against the baseline commit) — read them before re-baselining, or
the regression becomes the new yardstick.

To ask for a re-baseline, from `/super-admin/deploy-status`, or the request file it
writes:

```bash
sudo ls -l /var/lib/scangrade-deploy/requests/rebaseline   # written by the page; removed by the run
sudo journalctl -u scangrade-deploy -n 20 --no-pager       # expect: the perf gate will re-measure the box (--rebaseline)
```

`perf gate: CANNOT MEASURE` (gate exit 2) is a different answer again: the box was too
busy or the harness stopped, the release is **not compared**, and nothing rolls back.

**Check after (b), (c) or (d):**

```bash
sudo cat /var/lib/scangrade-deploy/quarantined      # expect: No such file or directory
sudo cat /var/lib/scangrade-deploy/last-deploy      # expect line 1 == git -C /opt/scangrade rev-parse --short HEAD
git -C /opt/scangrade rev-parse --short HEAD
journalctl -u scangrade-deploy -n 40 --no-pager     # expect: DEPLOY OK: <before> -> <after>
systemctl is-active scangrade scangrade-deploy.timer
```

**Merged is not serving.** Ask the app which commit is answering, through its own
loopback address (nginx is not the process whose reload is in question):

```bash
sudo /opt/scangrade/.venv/bin/python /opt/scangrade/deploy/served_commit_gate.py \
    --base http://127.0.0.1:8000 \
    --commit "$(git -C /opt/scangrade rev-parse HEAD)" --repo /opt/scangrade
# exit 0 "served commit: OK — …"            the release is answering
# exit 3 "served commit: REFUSED — …"       a different commit is answering
# exit 2 "served commit: CANNOT MEASURE — …" the app could not be asked (not a pass)
```

**If it rolled back instead** (exit 10): `last-deploy` holds the *previous* sha, and the
journal says `ROLLED BACK to <sha> — that release is serving.` The refused commit stays
quarantined, so the box stops re-pulling it.

**If `ROLLBACK ALSO UNHEALTHY` (exit 11):** the previous release is not serving either.
Do not push anything; run the lever:

```bash
sudo /usr/local/bin/sgfix --dry-run    # why the box is stuck, and what would be done; changes nothing
sudo /usr/local/bin/sgfix              # apply the migrations the schema is missing, arm the box,
                                      # run one release, set aside a box-local edit if that blocked the merge
```

Every action it takes is recorded in `/var/lib/scangrade-deploy/recover/<stamp>.txt`.
On a box with no lever installed, read it out of the commit the box has already fetched
— the one-line recipe is in [docs/AUTO_DEPLOY.md](../AUTO_DEPLOY.md), *One word when a
box is stuck: `sgfix`*. A hand `git reset --hard` is not a release: it moves the
checkout and lifts nothing.

## 3. The two refusals, told apart in one look

Both leave the site serving and both read like "nothing deployed" from outside. They
are different facts about different things, and the box keeps a separate record for
each:

```bash
sudo cat /var/lib/scangrade-deploy/quarantined           2>/dev/null || echo "no release is held"
sudo cat /var/lib/scangrade-deploy/refused-before-merge  2>/dev/null || echo "no pre-merge refusal recorded"
```

|  | **the release was refused** | **the run refused before the release moved** |
|---|---|---|
| record | `/var/lib/scangrade-deploy/quarantined`, and the same bytes under `refusals/` | `/var/lib/scangrade-deploy/refused-before-merge`, plus `refused-before-merge.diff` when the refusal is *about* the checkout's contents |
| what happened | the commit was merged and judged; a gate failed; the checkout was reset back to the release that was serving | nothing was merged and nothing was reloaded; the previous release simply keeps serving |
| quarantine | yes — a quarantine is a fact about a *commit* | no, and it never will be: the next tick just retries |
| record lines | sha · when · gate · up to 6 lines of the gate's finding | step · when · exit code · commit (empty when the refusal happened before one existed) · the command's own output |
| `situation` source | `quarantine` | `preflight` |
| cleared by | the gate passing, or the commit being released once | a release merging (the record is removed the moment one does) |
| fixed by | **the commit**: push a fix, or release it once after applying the migration | **the box or its operator**: arm it, heal the lock, provide the roster/`DIRECT_URL` |

**The exit code says which half.** The runner's own codes (what
`systemctl status scangrade-deploy` reports):

| code | refusal | half | quarantine |
|---|---|---|---|
| 2 | not root (`must run as root (it restarts scangrade)`) | — | no |
| 3 | `$REPO` is not a git checkout, cannot be entered, or has no `.venv` | pre-merge | no |
| 4 | the box-local edit could not be set aside (`dirty_checkout`) | pre-merge | no |
| 5 | `git fetch` failed (network or credentials) | pre-merge | no |
| 6 | the merge could not happen (`git merge --ff-only` — a stale `.git/index.lock` lands here) | pre-merge | no |
| 12 | a migration release with no recovery point | pre-merge | no |
| 14 | the installed runner is a **copy** that has drifted from the checkout | pre-merge | no |
| 15 | the box is not armed — or the two callers disagree (§4) — or the launcher refused a checkout with no Gate 0 | pre-merge | no |
| 17 | a stale git index lock root would have to remove while it may be held | pre-merge | no |
| 7 / 8 | `pip install` failed · `python compileall` failed | after the merge | this commit |
| 9 | the app did not construct | after the merge | this commit |
| 13 | the theme gate refused (including a `schema_contract --require-applied` gap) | after the merge | this commit |
| 16 | the release removed Gate 0 from the runner | after the merge | this commit |
| 18 | the schema gate: the release names objects the live database does not have | after the merge | this commit |
| 10 | a post-reload gate refused → **rolled back**, the previous release is serving | after the merge | this commit |
| 11 | the rollback itself did not come back healthy → `sgfix` | after the merge | this commit |

The installer's own codes are a different set — `arm-auto-deploy.sh` passes them
through, so the same digit means something else depending on which program printed it:

| code | what the installer refused |
|---|---|
| 4 | the checkout has local changes |
| 5 | the checkout is not a fast-forward |
| 6 | a file the release has to carry is not in it |
| 7 | the theme gate refused, or the service did not come back |
| 8 | a launcher still carries `@REPO@`, so it was never rendered |

**The gates have a third set, and none of it is a runner exit code.** They are the
number each gate hands the runner, which then decides what to do with it. `claims_gate.py`
owns the vocabulary — `perf_gate.py` imports it — and both use the same four: **0** OK ·
**1** diverged (`perf gate: REGRESSED`, a rollback only when `PERF_ENFORCE=true`) ·
**2** the box could not be measured (never a rollback, in either gate) · **4** not armed
(`perf gate: NOT ARMED` — always a rollback, because no comparison happened).
`seb_door_gate.py` speaks two of its own (**2** could not measure · **3** not enforced,
§5). One refusal is therefore always two numbers — the gate's, which the journal quotes
in the gate's own line, and the runner's, which is what `systemctl status
scangrade-deploy` reports.

**Two records that answer for the *step*, whatever stopped it:**

```bash
sudo cat /var/lib/scangrade-deploy/last-stop    # step · when · exit code · commit, written by the EXIT trap;
                                                # cleared only by a run that finishes (step `done`)
sudo cat /var/lib/scangrade-deploy/situation    # disposition · source · gate · action · when
# disposition `self`  = the next tick clears it (a fetch, a lock, a merge, a snapshot, a box edit)
# disposition `human` = nothing clears it but you (not root, no checkout, no venv, unreadable checkout)
# action              = wait | release | rebaseline | console  — and only an action the page can
#                       actually carry out is offered as a button
```

## 4. The one refusal with a record and no quarantine (exit 15, armament)

`/var/lib/scangrade-deploy/unarmed` holds **both** readings — one per caller — plus the
line saying they disagree. It is deleted the moment the box is armed again, because
there is no commit to quarantine:

```bash
sudo cat /var/lib/scangrade-deploy/unarmed
# as root (uid 0): ARMED — every gate has what it needs.
# as scangrade: ARMED — every gate has what it needs.
# --- as root ---            <the checker's full report>
# --- as scangrade ---       <and the same, as the service user saw it>
```

Reproduce both readings, then arm:

```bash
sudo bash /opt/scangrade/deploy/arm-auto-deploy.sh --check
runuser -u scangrade -- bash /opt/scangrade/deploy/arm-auto-deploy.sh --check
bash /opt/scangrade/deploy/arm-auto-deploy.sh
```

Exit 15 is also the **launcher's** code when the checkout no longer carries Gate 0
(`$TARGET does not carry Gate 0 — refusing to run it.`). The journal text tells them
apart: that one is printed by `/usr/local/bin/scangrade-deploy` before it execs
anything; the unarmed one is printed by the runner as `UNARMED — REFUSING TO DEPLOY`.

## 5. The same two answers inside one gate (SEB door, Gate 4c)

The door gate's verdict is read from its exit code, and its two failure modes are not
the same fact:

| gate exit | meaning | what the runner does |
|---|---|---|
| 0 | both halves measured and holding | logs `seb door: OK` |
| 2 | **could not measure** — no credentials, no fixture to clone, the app unreachable, a release that predates the door, no browser, or a database older than the refusal record | keeps the release and says loudly it was **not measured** — never a rollback |
| 3 | **not enforced** — a plain browser opened a gated paper, an honest client was refused, another key's header was admitted, the handshake road is missing, or the refusal panel's retry is dead / filed nothing / navigates away | rolls back **only if `SEB_ENFORCE=true`**; otherwise it prints the finding and keeps the release, with `to enforce it: SEB_ENFORCE="true" in /etc/scangrade-smoke.conf` |

Exit 2 is not a pass: a box in that state ships every release with the claim "SEB is
enforced" measured nowhere. `arm-auto-deploy.sh --check` is what makes that visible —
its `seb` line is the only place in the app that reports it.

## 6. Prove the armed gate bites (laptop, not the box)

```bash
python deploy/drill_seb_armed_rollback.py
```

It runs the **shipped** Gate 4c block and the **shipped** refusal path from
`deploy/scangrade-deploy.sh` against a scratch two-commit box with a stand-in
`deploy/seb_door_gate.py`, and expects: armed + a finding → `exit 10` with the checkout
back on the serving commit and the quarantine record written; unarmed + a finding →
release kept; armed + exit 2 → release kept; and, with the block's own `HEALTHY=0`
deleted, the same finding keeps the release — so the rollback is the shipped block
deciding, not an accident.

## 7. Every path this runbook touches

| path | mode / owner | what it is |
|---|---|---|
| `/opt/scangrade` | service user | the checkout; what the launchers exec |
| `/opt/scangrade/.env` | service user | box config; `DIRECT_URL` arms the schema gate |
| `/opt/scangrade/.freebuff/lt_roster.json` | `0600`, service user | the load-test roster (claims + perf gates) |
| `/opt/scangrade/deploy/*.sh`, `deploy/*.py` | committed | the gates themselves — what runs is this, not a copy |
| `/usr/local/bin/scangrade-deploy` | root | launcher: renders from the checkout |
| `/usr/local/bin/scangrade-db-snapshot` | root | launcher: the recovery point for a migration release |
| `/usr/local/bin/sgfix` | root | the lever ([deploy/scangrade-recover.sh](../../deploy/scangrade-recover.sh)) |
| `/etc/scangrade-smoke.conf` | `0600 root:root` | `SMOKE_*`, `TOUCH_ENFORCE`, `RENDER_ENFORCE`, `SEB_ENFORCE`, `SG_CHROME` |
| `/etc/scangrade-claims.conf` · `/etc/scangrade-perf.conf` | `0600 root:root` | `CLAIMS_ENFORCE` · `PERF_ENFORCE` |
| `/etc/scangrade-deploy.pause` | root | freeze deploys (exam week); the timer keeps ticking, nothing restarts |
| `/etc/scangrade-deploy.release` | root | release the held commit **once**; the run removes it |
| `/etc/systemd/system/scangrade-deploy.{service,timer}` | root | the two-minute tick |
| `/var/lib/scangrade-deploy/` | `root:scangrade` `0750` | the state directory (records below are `0644`, so the app can read them) |
| `…/quarantined` | `0644` | the commit a gate refused: sha · when · gate · finding |
| `…/refusals/` | — | the same records, kept past later refusals (`ls -t`) |
| `…/unarmed` | `0644` | the armament refusal, both callers, deleted once armed |
| `…/refused-before-merge` (+ `.diff`) | `0644` | the pre-merge refusal: step · when · exit · commit · output |
| `…/last-deploy` | — | the release serving: sha · when · recovery point |
| `…/last-stop` | — | the step the last failed run stopped at: step · when · exit · commit |
| `…/situation` | `0644` | disposition · source · gate · action · when |
| `…/requests/release`, `…/requests/rebaseline` | — | what the deploy-status page's buttons write |
| `…/control/`, `…/lever/`, `…/recover/` | — | branch-issued commands, the recovery lever, the `sgfix` records |
| `/tmp/installer.log` | — | every line of the last arm run |

Read [docs/deploy/VPS_DEPLOY_CHECKLIST.md](VPS_DEPLOY_CHECKLIST.md) for the order the
whole provisioning runs in; this page is only the two console jobs.
