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
