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
  `supabase/migrations/070_scan_live_exam.sql` **only to staging** after validating
  referenced `schools`, `exams`, `profiles` table schema.
- A LiveKit Cloud project (server URL, API key and secret); the video SFU is
  hosted by LiveKit Cloud, not on the 1 GB VPS.

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

Set the following in that file (values are **examples**, not usable secrets).
Use the actual *staging* Supabase URL and correct LiveKit credentials.

```dotenv
SCANMEET_STAGING=true
SCANMEET_STAGING_URL=https://staging.your-school-domain.com
SCANGRADE_LIVE_ENABLED=true
SCANGRADE_LIVE_EXAM_ENABLED=true
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=replace-me
LIVEKIT_API_SECRET=replace-me
SUPABASE_URL=https://independent-staging.supabase.co
SUPABASE_SERVICE_KEY=replace-me
SUPABASE_ANON_KEY=replace-me
FLASK_SECRET_KEY=replace-me
```

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

Use two different browsers (or one normal plus one private window) signed in as
the teacher and as an assigned pupil, and a third as a pupil of **another
school**. Cameras and microphones on, no fake devices.

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

### 3. What to send back

The probe's seven lines, the two console readings from each browser, the
`scan_live_sessions` row for the room, and the count of `scan_live_events` for
it. No screenshots of faces, no keys, no JWTs: the participant identities in
the audit table are pseudonyms by design.
