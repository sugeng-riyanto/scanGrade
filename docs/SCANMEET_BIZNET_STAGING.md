# ScanMeet — safe Biznet Gio staging (no production modification)

**Repository branch:** `integration/livekit-secure-main-20261024`, PR #9.
The VPS auto-deploy service tracks **main only** in `/opt/scangrade`; this
staging procedure uses a **separate checkout** and does not alter its branch,
service, systemd units, .env, nginx or exam database.

## Prerequisites

- A non-root SSH account on Biznet Gio, enough free RAM/disk for the extra
  Python app. A 1 GB VPS already running production can be too constrained
  for a second full app; use a temporary isolated VPS or schedule a maintenance
  test if needed. Never run a second worker during an active exam.
- A separate HTTPS staging domain, with DNS/TLS configured on the hosting side.
- A separate staging Supabase project. Apply
  `supabase/migrations/070_scan_live_exam.sql` **and**
  `supabase/migrations/071_scan_live_exam_toggle.sql` **only to staging** after
  validating referenced `schools`, `exams`, `profiles` table schema. Without 071
  every paper reads as ScanMeet OFF — the feature's default, not a fault — and
  the per-exam switch answers 409 with "Could not save the ScanMeet switch".
- A LiveKit Cloud project (server URL, API key and secret); the video SFU is
  hosted by LiveKit Cloud, not on the 1 GB VPS.
- Four synthetic identities in the staging database — teacher, two pupils in one
  school, one pupil of another school — and one staged exam. They are passed to
  the media gate on the command line as `email:password`, never stored in the
  env file.

## Values the owner must supply

This is the whole list. The template `deploy/livekit-staging.env.example`
carries every key and no value; the gate refuses it unchanged, so a copied
template cannot read as a configured pilot. Fill it at
`~/.config/scanmeet/staging.env` on the staging host (outside the checkout,
mode `0600`), then run the gate — it names every value still missing, and never
prints one.

<!-- owner-values:start -->

| Key | Where the value comes from | How to prove it is not production's | What a wrong value does |
|---|---|---|---|
| `SCANMEET_STAGING` | the literal `true` | it is the declaration itself | any other value: "SCANMEET_STAGING=true is required" |
| `SCANMEET_STAGING_URL` | the staging host's own DNS/TLS entry | it must not be `scangrade.web.id`, its `www.`/`lmsshb.` names, or any subdomain of that domain | gate refuses; the media driver also refuses the live site unless `--allow-production` |
| `SCANGRADE_LIVE_ENABLED` | you set it on the staging host; the template ships `false` | it is `true` only there — the template's `false` is what CI checks, so a copy that lands in a production `.env` cannot switch Live on | pilot refused: "both Live feature flags" |
| `SCANGRADE_LIVE_EXAM_ENABLED` | you set it on the staging host; the template ships `false` | as above | as above; also the reason a webhook probe answers 404 |
| `LIVEKIT_URL` | LiveKit Cloud → the staging project → Settings → URL (`wss://<project>.livekit.cloud`) | the project name differs from production's LiveKit project | `--probe-livekit` fails; browsers cannot signal |
| `LIVEKIT_API_KEY` | same project → Keys | belongs to the staging project | RoomService probe 401s and microphone approval cannot be applied |
| `LIVEKIT_API_SECRET` | same key, shown once at creation | same project | 401 on the probe and on every `UpdateParticipant` |
| `SUPABASE_URL` | Supabase → the staging project → Settings → API → Project URL | the ref must differ from `deploy/supabase_region.py`'s `REF`, and the value must not still say `your-project` | gate refuses "isolated staging Supabase project" or "still a placeholder" |
| `SUPABASE_SERVICE_KEY` | same page → `service_role` | that staging project only | 401s on every query; RLS bypasses are per project |
| `SUPABASE_ANON_KEY` | same page → `anon` | same project | login and CSRF flows fail for every account |
| `FLASK_SECRET_KEY` | generate it **on the staging host**: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` | never reuse production's | every staging session (and its CSRF token) is invalidated on restart |

<!-- owner-values:end -->

Two values are **not** in the env file, because they are chosen when the run
starts rather than configured once:

| Argument | Where it comes from |
|---|---|
| `--exam <uuid>` | a staged exam in the staging database, with at least two assigned pupils in one class |
| `--teacher`, `--student`, `--student2`, `--outsider` | the four synthetic identities, as `email:password`; `--outsider` must belong to a **different** school |

The media gate itself never provisions any of them — it refuses with exit 2 and
names what it could not measure, which is why "exit 2" is a blocker and not a
pass.

## Commands (SSH on staging server, under an ordinary user)

First obtain the repository on a new, separate path:

```bash
git clone --single-branch --branch integration/livekit-secure-main-20261024 \
  https://github.com/sugeng-riyanto/scanGrade.git ~/scanmeet-staging
cd ~/scanmeet-staging
git status --short
git log -1 --oneline
```

Create a PRIVATE file outside the checkout; do not paste credentials in chat,
GitHub, logs, or shell command history. The template at
`deploy/livekit-staging.env.example` contains no credentials.

```bash
mkdir -p ~/.config/scanmeet
chmod 700 ~/.config/scanmeet
umask 077
nano ~/.config/scanmeet/staging.env
chmod 600 ~/.config/scanmeet/staging.env
```

Fill in every value the table above names. The template is
`deploy/livekit-staging.env.example`; do not retype the list from memory, because
a key that is missing there is a key the gate will refuse and the run will
never take.

Run the **offline safety guard before installing or starting anything**:

```bash
python3 -m deploy.scanmeet_staging_gate --env ~/.config/scanmeet/staging.env
```

It fails if the checkout is `/opt/scangrade`, the env file is in the repo or
world-readable, or staging reuses the production Supabase ref/domain, or either
Live flag is disabled. It never prints credentials.

On a sufficiently provisioned *isolated staging* host, install into a private
virtual environment (not production's `/opt/scangrade/.venv`):

```bash
python3 -m venv .venv-scanmeet
. .venv-scanmeet/bin/activate
python -m pip install -r requirements.txt -r requirements-live.txt
python -m pip check
python -m deploy.livekit_sdk_contract
python -m deploy.scanmeet_staging_gate --env ~/.config/scanmeet/staging.env --probe-livekit
```

A PASS here proves LiveKit credentials reach its **read-only RoomService API**.
It does **not** prove actual media streams, camera permissions, RLS, webhook
signatures, or teacher/student isolation. After applying the staging migration
and configuring a separate staging web process behind TLS, perform a pilot
with a teacher, assigned student and another-school account. Verify pupil
cannot view other students, mic starts disabled, hand-raise approval, no chat,
no recording, room end and signed webhook audit.

**Do not run** `deploy/deploy.sh`, `deploy/scangrade-deploy.sh`, or restart
`scangrade` for this staging test: those scripts target production.

## Pull updated staging code safely

```bash
cd ~/scanmeet-staging
git status --short
git fetch origin integration/livekit-secure-main-20261024
git merge --ff-only origin/integration/livekit-secure-main-20261024
```

Run the checks again after pulling. The real production deploy automatically
follows `main` and will *not* fetch this staging branch for deployment.
The merge into `main` must remain a separate release decision after real
staging E2E evidence and GitHub CI succeed. Production Live flags stay OFF.

## Acceptance sheet — what to run, and what counts as evidence

Everything in this section needs the staging deployment from the steps above.
Run them in this order; each one produces a line that goes into the sign-off.

### 1. The webhook, with real signatures

```bash
# key and secret come from the environment: LIVEKIT_API_KEY, LIVEKIT_API_SECRET
python -m deploy.scanmeet_webhook_probe \
  --url https://staging.your-school-domain.com

# once a room is open, name it and 204 stops being an acceptable answer:
python -m deploy.scanmeet_webhook_probe \
  --url https://staging.your-school-domain.com --room sg_the_open_room
```

The probe signs seven deliveries the way LiveKit signs them (HS256, `sha256`
claim over the exact body) and judges the answers: no signature, tampered body,
wrong secret, expired, oversized, valid, and a replay of the valid one. Expected:

```
  PASS  missing-signature  HTTP 401 (expected [401])
  PASS  tampered-body      HTTP 401 (expected [401])
  PASS  wrong-secret       HTTP 401 (expected [401])
  PASS  expired            HTTP 401 (expected [401])
  PASS  oversized          HTTP 413 (expected [413])
  PASS  valid              HTTP 200 (expected [200])
  PASS  replay             HTTP 200 (expected [200])

PASS: 7/7 webhook probes answered as expected
```

Exit codes: 0 all as expected · 1 one disagreed · 2 could not probe (missing URL
or key). A 404 means the two Live flags are not both on; a 503 means the route
is reachable but unconfigured. The probe proves *signature and size handling*;
it cannot prove LiveKit itself delivered, so after the run open the teacher's
room and confirm the audit panel lists the events your own browser session
produced.

### 2. Two real browsers, real media

Half of this section is driven rather than watched. Three of its readings cannot
be taken by looking at a page — a frame counter that is moving, a microphone the
**SFU** refuses rather than a button the page hides, and media one pupil's
browser did *not* receive — so `deploy/scanmeet_media_gate.py` takes them, in
three headless browsers, and prints one row per claim.

With the env file exported on the staging host:

```bash
set -a; . ~/.config/scanmeet/staging.env; set +a
python -m deploy.scanmeet_media_gate \
  --url "$SCANMEET_STAGING_URL" --exam <exam_id> \
  --teacher guru@school:pw --student murid1@school:pw \
  --student2 murid2@school:pw --outsider murid@other-school:pw
```

Expected: twelve `PASS` rows — `sdk-loaded`, `session`, `pupil-camera`,
`teacher-receives-camera`, `pupil-isolation`, `mic-starts-closed`, `hand-raise`,
`mic-approved`, `audio-reaches-teacher`, `mic-closed`, `outsider-refused`,
`room-ended` — and a closing `PASS: 12/12 rows`. The driver reads each page
**and** the join token the server issues, because the token's
`canPublishSources` is what LiveKit enforces while the DOM is only what the page
drew: `mic-starts-closed` is a camera-only grant, `mic-approved` is
camera + microphone, and `mic-closed` is the camera again.

Exit codes: 0 every row passed · 1 a row disagreed (the row naming the defect) ·
2 could not measure — a missing account, Live off, a page that never loaded, or a
run that ended early. **2 is not a pass**: an unfinished run names the rows it
never took instead of printing a shorter list of passes.

Add `--signoff` to print those twelve rows as the Markdown sheet this section
ends with — the URL, the exam id, when it ran, the browser it drove and the
verdict the run earned, ready to paste into the pilot's record. The sheet carries
the verdict it earned rather than the one the reader hopes for: a run that could
not take a row heads its table with `CANNOT MEASURE` and says in words that it is
not a sign-off. Keep the raw reading beside it with
`--signoff --json-out reading.json`; `--json` and `--signoff` are refused
together, because both write stdout and the run that asked for a sheet would
otherwise get a JSON blob and no sheet at all.

The driver refuses the live site — `scangrade.web.id`, its `www.`, the
`lmsshb.` name and every other subdomain of that domain — unless
`--allow-production`, because a stale `$SCANMEET_STAGING_URL` would otherwise open
a real room and press a real teacher's controls on the exams pupils are sitting:
the escape hatch is for a deliberate pilot, never a copy-paste. (`scan-grade.app`
is the demo *email* domain in `manage.py`, not where the app is deployed; the
guard lists it too, so pointing a staging run there is refused rather than
silently measuring nothing.)

What the driver covers, from the table below: 01, 02, 03, 04, 05, 06, 07 and 10,
plus the isolation reading the table asks a person to take from two console
snippets. What stays manual: **08** (a pupil trying chat, screen share or
recording — the grant row already shows those sources are absent, but a person
pressing the control is the pilot's own evidence) and **09** (a ~30 s network
drop and reconnect). Its cameras are Chrome's synthetic device
(`--use-fake-device-for-media-stream`: a real `getUserMedia` track, a real
publish, a real SFU subscription and real decoded frames, with a test pattern
where a face would be); `--no-fake-devices` uses the box's hardware instead, and
the header line always says which.

For the pilot itself — real cameras, real people — use two different browsers
(or one normal plus one private window) signed in as the teacher and as an
assigned pupil, and a third as a pupil of **another school**.

| # | Action | Evidence to record |
|---|---|---|
| 01 | Teacher opens `/live/exam/<exam_id>`, clicks **Mulai pengawasan** | Status line stops at "Memeriksa sesi Live..." and the grid switches on |
| 02 | Assigned pupil opens the same page | Their own tile appears; teacher's grid gains a tile |
| 03 | Other-school pupil opens the page | "Live session not available for this account" (403), never a tile |
| 04 | Pupil's camera on | In the teacher console: `#live-grid video` count matches the room, each with `videoWidth > 0` |
| 05 | Pupil clicks **Angkat tangan** | Request appears in the teacher's queue as `pending` |
| 06 | Teacher clicks **Izinkan** | Pupil's mic button appears; the teacher's audit list gains `track_published` for audio |
| 07 | Teacher clicks **Akhiri bicara** | Audit gains `track_unpublished`; the pupil's mic button disappears |
| 08 | Pupil tries chat / screen share / recording | No control publishes; the server's permission set refuses it (check the console for a refusal, not a hidden button) |
| 09 | Pupil's network drops for ~30 s | Their tile greys out, the exam page keeps its timer and autosave, and the tile returns after reconnect |
| 10 | Teacher clicks **Akhiri Live** | New joins are refused; existing tiles drop |

Console snippet for **both** browsers, to record what is actually flowing:

```js
(() => [...document.querySelectorAll('#live-grid video')].map(v => ({
  w: v.videoWidth, h: v.videoHeight, ready: v.readyState,
  frames: v.getVideoPlaybackQuality().totalVideoFrames,
  ct: +v.currentTime.toFixed(1),
})) )()
```

Paste it twice, 3 seconds apart. `totalVideoFrames` rising is decoded video; a
frozen or never-arrived track leaves it at 0. **On the pupil's page the array
must have length 1** — their own camera — while another pupil is in the room.
A second entry there is the media-isolation failure this pilot exists to catch,
and it is not visible in the UI.

Paste in the **teacher's** page to see the room's actual topology:

```js
(() => performance.getEntriesByType('resource')
  .filter(e => /livekit|\.cloud/.test(e.name)).length)()
```

Anything above 0 means the browser really reached the LiveKit host; a page that
only ever shows "Memeriksa sesi Live..." never did.

#### The sheet `--signoff` prints

This is the artefact — not a summary of it — for a run that took all twelve rows.
It is the output of `deploy/scanmeet_media_gate.py`'s own sheet renderer, so the
wording below is the wording a pilot's ticket will carry; the four header values
are the run's (URL, exam id, when it ran, which browser), and every cell in the
table comes from a reading the driver took, never from this page.

```markdown
## ScanMeet media sign-off - https://staging.your-school-domain.com

- **Verdict:** `SIGNED OFF` (exit 0) - 12/12 row(s) passed
- **Exam:** `9f4c2a10-8d3e-4b71-9c55-2e7a1b0d6f38`
- **Run at:** 2026-10-24T09:12:44+07:00
- **Browser:** `/usr/bin/google-chrome` (synthetic devices)

| # | Row | State | What this run observed |
| --- | --- | --- | --- |
| 1 | `sdk-loaded` | PASS | the page reached the pinned SDK |
| 2 | `session` | PASS | the room opened (7c1e4a92...) and the teacher joined it |
| 3 | `pupil-camera` | PASS | the pupil's page is decoding its own camera, and the frames were still rising 3s later |
| 4 | `teacher-receives-camera` | PASS | the teacher's grid is decoding 2 pupil camera(s) |
| 5 | `pupil-isolation` | PASS | neither pupil's page holds any camera but their own and the supervisor's, while the other is in the room |
| 6 | `mic-starts-closed` | PASS | a fresh pupil token grants the camera only, and the page offers no microphone control |
| 7 | `hand-raise` | PASS | the request is pending in the API and a row the teacher can see in the queue |
| 8 | `mic-approved` | PASS | approval grants camera + microphone and the pupil's control appears |
| 9 | `audio-reaches-teacher` | PASS | the teacher's page holds a live audio track for the room |
| 10 | `mic-closed` | PASS | the close takes the grant back to the camera, the control away and the teacher's live audio with it |
| 11 | `outsider-refused` | PASS | another school's pupil is refused both the page and the token |
| 12 | `room-ended` | PASS | *Akhiri Live* is confirmed, and the next join token is refused |

Every row passed: cameras decode, the microphone is refused until a teacher approves it and revoked after, no pupil receives another's media, and the ended room admits nobody.
```

The verdict line is the one a reader checks first, and it is earned rather than
assumed: `--signoff` on a run that skipped a row prints `CANNOT MEASURE` with the
same table and a closing sentence saying it is **not** a sign-off. A sheet with a
`**SKIP**` or a `**FAIL**` in it is a finding to send back, not a pilot to file —
`test_the_runbook_shows_the_sheet_the_driver_prints` reads this block and fails
if the twelve row names or the closing sentence stop matching the tool.

#### The per-exam switch — four readings the twelve rows do not take

The driver opens the room the teacher started; it does not turn the paper's
switch off and on. These are the acceptance-matrix rows for it, and each one is
taken from the server's answer rather than from the button's label:

| Reading | How | What counts |
|---|---|---|
| OFF is stored | open `/live/exam/<exam_id>` as the teacher with the switch already off | the page says `NONAKTIF`, **Mulai pengawasan** is not offered, and a second device says the same after reload |
| OFF refuses a room | `POST /api/live/exams/<exam_id>/sessions` (or the page's button if it were offered) | `409 {"error": "ScanMeet is off for this exam"}`; no `scan_live_sessions` row is created |
| ON permits, never starts | press **Aktifkan ScanMeet**, then reload | `AKTIF`, and **no** session row until **Mulai pengawasan** is pressed — two ON requests leave one paper with no room |
| OFF mid-session revokes | with a room open, press **Nonaktifkan ScanMeet** | the session row turns `ended`, the tiles drop, the next join token is refused, and if LiveKit cleanup failed the page says *"Pembersihan media tertunda"* — the exam, its timer and autosave are untouched |

Record the `scan_live_sessions` row's `status` and the HTTP code each time; a
row that is still `active` after OFF is the finding, whatever the page shows.

### 3. What to send back

The probe's seven lines, the two console readings from each browser, the
`scan_live_sessions` row for the room, and the count of `scan_live_events` for
it. No screenshots of faces, no keys, no JWTs: the participant identities in
the audit table are pseudonyms by design.
