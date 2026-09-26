# Capacity measurements

These files are the evidence behind the capacity table on the landing page
(`app/templates/landing.html`). That table is checked twice: `deploy/claims_gate.py`
re-measures its lowest rung on **every deploy**, and
`tests/unit/test_landing_claims.py` recomputes every row from the files here and
fails if the page and the numbers have drifted apart.

Publishing a number is therefore not possible without a measurement behind it.

## What was measured

Production, `https://scangrade.web.id`, on **14 Sep 2026** (WIB evening), again on
**25 Sep 2026** (11:42–11:51 UTC) and once more on **26 Sep 2026** (04:09 WIB,
25 Sep 21:09 UTC): **1 vCPU, 957 MB**, 3 gevent workers, bottleneck CPU spent
rendering pages. Every one of those runs is in this directory, and each moved the
row it belongs to — why, and by how much, is in [Re-measured on
25 Sep](#re-measured-on-25-sep) and [Re-checked on 26 Sep](#re-checked-on-26-sep).

Method: `loadtest_concurrent.py` in endurance mode — one account per session, each
session signs in for real, its identity is confirmed through `/auth/me`, then it
keeps loading an exam-shaped mix of pages for the duration of the run. **Reads
only**: the one write is `POST /student/settings/pdp-update`, which is idempotent.
No submissions are created or deleted, so real exam data is untouched.

```bash
python loadtest_concurrent.py 50  1 --base https://scangrade.web.id --duration 60 --json docs/measurements/rung-050.json
python loadtest_concurrent.py 100 1 --base https://scangrade.web.id --duration 60 --json docs/measurements/rung-100.json
python loadtest_concurrent.py 150 1 --base https://scangrade.web.id --duration 60 --json docs/measurements/rung-150.json
```

## The second harness (Locust)

`locustfile.py` measures the same rung a second, independent way. It only became
usable at all in Sep 2026: it used to post its login without a CSRF token (so
every session was rejected 403), judge success by HTTP status (a *rejected* login
also answers 200, so it counted logged-out visitors as students), and draw its
accounts from `siswa1..siswa1000_smp`, of which six exist. Until those were fixed,
no figure could honestly be attributed to it — which is why the landing page names
the harness instead.

```bash
locust -f locustfile.py --host=https://scangrade.web.id \
       --users 55 --spawn-rate 5 --run-time 90s --headless --teachers 5 \
       --report docs/measurements/locust-050.json
```

`--users 55 --teachers 5` is 50 murid + 5 guru, one account each, taken from
`.freebuff/lt_roster.json`. The harness **refuses to start** when `--users`
exceeds the roster: reusing a login turns the app's per-identity rate limit
(120 req/min) into 429s that look like a server failure — an earlier run reported
a fabricated 49% error rate exactly that way. Every session also confirms through
`/auth/me` that it owns the account it signed in as.

The 90-second run recorded here: **55 sessions, 55/55 logged in, 55/55 identities
verified, 1,723 requests, 0 failures, 0 x 429, 0 x 5xx.** Its artifact is
`locust-050.json` (the machine-readable summary the tests read) and
`locust-050-console.txt` is the unedited console output of that run, kept as raw
evidence rather than trimmed — it repeats its tables every few seconds while the run
is live. The suffix is `.txt`, not `.log`, on purpose: `.gitignore` excludes
`*.log`, so as a `.log` this file existed only on the machine that measured it and
no fresh clone could back the row it belongs to.

## Three measurements, one published bound

Three artifacts now back the 50-student row, and they do not agree to the
millisecond — they were taken at different hours on a shared box:

| Artifact | Measured | Sessions | Student pages | p50 | worst p95 | errors |
|---|---|---|---|---|---|---|
| `rung-050.json` (harness, 60 s) | 25 Sep 2026 | 50 murid + 1 guru | 5 | 510–2186 ms | 2.8 s | 0% |
| `rescan-050.json` (harness, 60 s) | 26 Sep 2026 | 50 murid + 1 guru | 5 | 579–1866 ms | **4.8 s** | 0% |
| `locust-050.json` (Locust, 90 s) | 14 Sep 2026 | 50 murid + 5 guru | 4 | 94–620 ms | 3.7 s | 0% |
| **published** | | | | **0.1–2.2 s** | **4.8 s** | **0%** |

The published bound is the **worst** figure any measurement produced, and the two
halves of the row do not come from the same run: the p50 range is 0.1–2.2 s
because the 14 Sep Locust run's low end is 94 ms and the 25 Sep harness run's high
end is 2186 ms, while the p95 is the 26 Sep re-scan's 4.8 s. Publishing the
*better* number is not available to a page that is held to its own advertisement:
the deploy gate refuses a release when the box measures more than 2x what the page
says, and this box has really been observed at p50 **1464 ms** and at p95 3.7 s on
the 50-student rung — a published bound of 620 ms turns a busy afternoon into a
failed release.

The same rule is why the re-scan did not *delete* the 25 Sep artifact. Replacing
it would have been the tidier file, and it would also have narrowed the p50 bound
from 2186 ms to 1866 ms — quietly tightening the gate against a box that has been
observed slower than that, using a run that happened to be taken at 04:09 WIB. A
re-measurement may add an observation; it may not withdraw one.

The Locust mix covers four of the five student pages; `GET /student/exams/<id>`
could not be exercised because the load-test accounts currently have no exams
assigned, so the run found no exam links to follow. The row keeps the endpoint set
of the run that could reach all five.

## The published rows

Per-row figures are the **student page endpoints** (`/student/dashboard`,
`/student/exams`, `/student/results`, `/student/settings`,
`/student/exams/<id>`), which is what "murid serentak" means: p50 is the range
across those pages, p95 is the worst of them.

| Concurrent students | p50 | worst p95 | errors | artifact |
|---|---|---|---|---|
| 50 | 0.1–2.2 s | 4.8 s | 0% | `rung-050.json` (25 Sep) + `rescan-050.json` (26 Sep) + `locust-050.json` (14 Sep) |
| 100 | 4.0–7.6 s | 8.5 s | 0% | `rung-100.json` (25 Sep) |
| 150 | 6.6–9.8 s | 11.4 s | 0% | `rung-150.json` (25 Sep) |
| 500* | 5.6–7.0 s | 10.8 s | 2.3% | `rung-500-endurance.txt` (14 Sep) |

`500*` came from a different run: 500 sessions kept busy for **10 minutes**
(682 s, 13,573 requests), because "500 students at once" is an endurance claim and
a 60-second probe cannot speak to it. Only 174 of the 500 got past login — the rest
were refused `429` by the per-IP login burst limit at nginx, not by the app — so
the row carries the 2.3% that refusal rate implies, and the row is starred on the
page for exactly that reason.

## The same files, on a page

`https://scangrade.web.id/capacity` renders this directory through
`app/services/capacity_service.py`: one row per rung, one card per run, each with
its harness, its date and a download link to the raw file, plus what the two
deploy gates measured on the box itself. It is the same evidence the landing
page's table is held to, in full.

**Adding a measurement is the whole update.** Drop a file in here and commit it:
the page grows a row, and nothing in a template has to be edited. There is one
thing to get right — a JSON artifact may carry `measured_at`, which is what the
page prints as the run's date; without it the date comes from the file's git
commit, and if neither exists the page says *date not recorded* rather than
borrowing the file's mtime (which a fresh clone would set to today).

A file that is not a measurement of a rung — this README, a console transcript,
the box's own loopback samples — is listed on the page as a supporting file, so
the set a reader can check is the set that is there. `docs/measurements/` is
served only through `/capacity/evidence/<name>`, as `text/plain`, from the list
the report built; `tests/unit/test_capacity_page.py` holds the page to these files
a second time, independently of the service, and refuses a page that invents a
figure, a date or a recommendation.

## What the numbers do not say

* **Reads only.** A real exam also submits canvas + text answers, which is heavier
  than opening a page. The comfortable limit below is set from read traffic and is
  deliberately conservative because of it.
* **One client, one source IP.** These runs came from a single public network, so
  the login burst hits nginx's per-IP limit; a school with its own NAT would look
  the same. Per-account limits exist for that reason (`docs/AUTO_DEPLOY.md`).
* **Not the ceiling.** The harness paces each session with 1–3 second waits, so
  these runs measure latency under load, never the box's maximum request rate.
  The ~20 req/s ceiling on the page comes from a separate measurement with
  `vmstat` under a 500-session load (user time 90–93%, idle 0%).
* **A snapshot, not a promise.** This is one sitting on one box. The per-rung
  files can be regenerated with the commands above, and the landing page must be
  updated in the same commit if the numbers move — which is what happened on
  25 Sep 2026, when a re-run of all three rungs did not reproduce 14 Sep's figures
  and the published rows were moved to the new ones in the same commit.

## The comfortable limit

The page recommends **~50 concurrent students per exam session** on this
configuration. That is the largest measured rung holding **0% errors and a worst
page p95 of 4.8 s**; at 100 the p95 rises to 8.5 s and at 150 to 11.4 s, where the
box is still alive but no longer comfortable. The recommendation does not exceed
what was measured — `tests/unit/test_landing_claims.py` enforces that.

## Re-measured on 25 Sep

This section exists because a release was refused. `deploy/claims_gate.py`
re-measures the 50-student rung on every deploy and holds the box to 2x of what the
landing page advertises; it measured the *worst signed-in page* twice and the box no
longer matched the published 962 ms, so the release was quarantined. That is the gate
doing its job, and its remedy is the page's own terms: **re-measure, then publish
what was measured.** All three rungs were re-run with the same harness, the same
roster and the same 60-second shape the 14 Sep figures came from.

Same shape, and the same health: one account per session, identity confirmed through
`/auth/me`, **51/51, 101/101 and 151/151 sessions signed in, 0% errors, 0 x 429,
0 x 5xx**.

| Rung | 14 Sep (published then) | 25 Sep (published now) |
|---|---|---|
| 50 (worst page p50) | 962 ms | **2186 ms** (`/student/settings`) |
| 100 (worst page p50) | 2.2 s | **7.6 s** (`/student/settings`) |
| 150 (worst page p50) | 4.4 s | **9.8 s** (`/student/results`) |

What the re-measurement does and does not say:

* **The steady state still holds the old claim.** In every run the *second half* of
the window answers in **508–646 ms**, and the crowded pages are consistently the
fast ones at 60 s (`/student/dashboard` 510 ms, `/student/exams` 903 ms). What
moved is the **cold opening** — 50 simultaneous logins plus first page render —
which the harness measures because it starts every session at once.
* **The worst p50 comes from the two thinnest samples.** `/student/results` (n=131)
and `/student/settings` (n=50) are visited once per session, so their first — cold —
visit is most of their sample; the pages with hundreds of samples never cross the
limit at 50 students.
* **The box was quiet while measuring.** Loopback `/health` answered p50 **193 ms**
and the harness recorded 0 transport errors, so this is page rendering (the known
bottleneck on 1 vCPU) rather than a busy neighbour or a network fault.
* **No release is implicated.** Production still serves a release older than these
runs — the same code that produced the 14 Sep numbers — so what moved is the box's
own data and traffic, not a commit. The deploy gate's own notes ask for exactly that
check before a published number is moved, and this is it.
* **It is not an artefact of the window.** The same probe shape was run for 30 s and
90 s on the same box: at 30 s the worst page p50 is **3019 ms** and at 90 s it is
**3777 ms**, so no window length makes the 14 Sep row true again. 60 s is published
because that is the shape the other rows use.

## Re-checked on 26 Sep

The p95 the page advertised was not the harness's figure at all: it was the 14 Sep
Locust run's 3.7 s, while the harness's own 25 Sep run had measured 2.8 s. That gap
is what a third artifact settles — the same harness, at the same rung, run by hand
against production instead of from a deploy:

```bash
python loadtest_concurrent.py 50 1 --base https://scangrade.web.id --duration 60 \\
       --json docs/measurements/rescan-050.json
```

It measured a worst page p95 of **4.8 s** (`/student/exams`) — above the 3.7 s the
page was publishing. So the row moved, 3.7 s → **4.8 s**: the worst figure any of
the three artifacts recorded.

What the run says, and what it does not:

* **Healthy, and the same shape as the published rows.** 51/51 sessions signed in,
  identity confirmed through `/auth/me` for all 51, 1,372 requests, **0% errors,
  0 x 429, 0 x 5xx, 0 transport errors**, 93.9 s wall, 14.6 req/s.
* **The p50 bound deliberately did not move.** This run's worst page p50 was
  1866 ms — *better* than the 25 Sep artifact's 2186 ms. Publishing the newer, lower
  number would have narrowed the advertised range from 2186 ms to 1866 ms, and the
  gate holds the box to 2x of whatever the page says: the box has already been seen
  at 3019 ms (30 s window) and 3777 ms (90 s). A re-measurement may add an
  observation; it may not withdraw one, so the 25 Sep artifact stays and the union
  is unchanged.
* **The steady state is still the fast half.** First half p50 1260 ms, second half
  856 ms: what varies between runs is the cold opening, not the box under load.
* **Only the 50-student rung was re-run.** It is the rung the deploy gate compares
  against, and the row that was wrong. The 100, 150 and 500 rows are untouched and
  still rest on the 25 Sep and 14 Sep runs.
* **Its date reads as 25 Sep, and it is the 26 Sep run.** `measured_at` is UTC:
  `2026-09-25T21:09:12+00:00` is 04:09 on 26 Sep in WIB, which is why
  `/capacity/evidence/rescan-050.json` prints 2026-09-25.

The deploy gate was then run by hand against production, on the republished page:

```
claims gate: OK — 51 sessions, worst of 9 page endpoints (n=716): p50 2671 ms
             (page <= 2200 ms), p95 3527 ms (page <= 4800 ms), errors 0.00%
claims gate: the published numbers still describe this deployment
```

That verdict is worth reading for what it does *not* say. This probe is 30 seconds
where the published rows are 60; it loads exactly the rung the page recommends; and
it measures read traffic only. It confirms the row, not the curve and not the
endurance claim.

## Supporting VPS samples

`rung-500-vps-samples.txt` is the box measuring **itself** over loopback every few
seconds during the 500-session run: `/health` goes from 0.006 s idle to ~5 s under
load, with `load` climbing to ~3.1 on 1 vCPU. It is the reason the page can name
CPU rendering as the bottleneck and rule out the network.
