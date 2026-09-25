# Capacity measurements

These files are the evidence behind the capacity table on the landing page
(`app/templates/landing.html`). That table is checked twice: `deploy/claims_gate.py`
re-measures its lowest rung on **every deploy**, and
`tests/unit/test_landing_claims.py` recomputes every row from the files here and
fails if the page and the numbers have drifted apart.

Publishing a number is therefore not possible without a measurement behind it.

## What was measured

Production, `https://scangrade.web.id`, on **14 Sep 2026** (WIB evening) and again
on **25 Sep 2026** (11:42–11:51 UTC): **1 vCPU, 957 MB**, 3 gevent workers,
bottleneck CPU spent rendering pages. The 25 Sep run is what the landing page
currently publishes, and it is **slower** than the first one — why, and by how
much, is the section [Re-measured on 25 Sep](#re-measured-on-25-sep).

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

## Two measurements, one published bound

Two artifacts now back the 50-student row, and they do not agree to the
millisecond — they were taken minutes apart on a shared box:

| Artifact | Measured | Sessions | Student pages | p50 | worst p95 | errors |
|---|---|---|---|---|---|---|
| `rung-050.json` (harness, 60 s) | 25 Sep 2026 | 50 murid + 1 guru | 5 | 510–2186 ms | 2.8 s | 0% |
| `locust-050.json` (Locust, 90 s) | 14 Sep 2026 | 50 murid + 5 guru | 4 | 94–620 ms | 3.7 s | 0% |
| **published** | | | | **0.1–2.2 s** | **3.7 s** | **0%** |

The published bound is the **worst** figure either measurement produced, which is
why the row now reads 0.1–2.2 s: the low end is the 14 Sep Locust run's 94 ms and
the high end is the 25 Sep run's 2186 ms. Publishing the *better* number is not
available to a page that is held to its own advertisement: the deploy gate refuses
a release when the box measures more than 2x what the page says, and this box has
really been observed at p50 **1464 ms** on the 50-student rung — a published bound
of 620 ms turns a busy afternoon into a failed release.

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
| 50 | 0.1–2.2 s | 3.7 s | 0% | `rung-050.json` (25 Sep) + `locust-050.json` (14 Sep) |
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
page p95 of 3.7 s**; at 100 the p95 rises to 8.5 s and at 150 to 11.4 s, where the
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

## Supporting VPS samples

`rung-500-vps-samples.txt` is the box measuring **itself** over loopback every few
seconds during the 500-session run: `/health` goes from 0.006 s idle to ~5 s under
load, with `load` climbing to ~3.1 on 1 vCPU. It is the reason the page can name
CPU rendering as the bottleneck and rule out the network.
