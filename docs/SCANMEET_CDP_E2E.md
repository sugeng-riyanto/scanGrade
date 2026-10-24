# ScanMeet: real WebRTC E2E through Chrome DevTools Protocol

This is an **opt-in staging acceptance test**, not a mock of LiveKit. It runs
**three independent Chrome processes** (teacher + TWO pupils) for the two-browser
role flow, because a single pupil cannot prove pupil-to-pupil isolation.

The automated browsers use Chrome's **synthetic camera and microphone device** to
send real RTP over the actual LiveKit SFU. It validates remote decoded video
frames and audio packets but **does not test physical webcams/mics, bandwidth
capacity, malicious client tampering, real signed LiveKit-origin webhooks, or
exam autosave**. Keep the manual acceptance sheet in
`docs/SCANMEET_BIZNET_STAGING.md` for those remaining tests.

## Run on the QA workstation, not a 1 GB production VPS

Requirements:
- Isolated ScanMeet staging deployment with TLS, separate staging Supabase,
  migration `070_scan_live_exam.sql`, working LiveKit Cloud and both staging-only
  feature flags turned ON.
- A **dedicated staging exam** with one creator teacher and TWO assigned pupils.
  No other active Live room for this exam; the driver refuses to take over.
- Chrome/Chromium installed on the machine running the driver (`SG_CHROME`
  can point to the binary). `requests` and `websockets` already come with
  ScanGrade's requirements. To run only this tool in a light venv:
  `python -m pip install requests==2.32.3 websockets==15.0.1`.
- Private login credentials for three **distinct STAGING** accounts.
  Do not paste them into command-line flags, commits, CI logs or screenshots.

Load a private credential file outside the repository using the shell facility
for private secrets. Example on Linux, with a non-committed file readable only
by the operator (`chmod 600`):

```bash
umask 077
# Create ~/.config/scanmeet/cdp-login.env with the keys below and real values.
# Store it OUTSIDE the repository. Never paste real credentials in chats.
set -a
. ~/.config/scanmeet/cdp-login.env
set +a

# staging URL must exactly match the SCANMEET_STAGING_URL in the env file
python -m deploy.scanmeet_cdp_e2e --confirm-staging \
  --json-out scanmeet-e2e.json
```

Keys to store privately:

```dotenv
SCANMEET_STAGING_URL=https://staging.your-school-domain.com
SCANMEET_EXAM_ID=staging-exam-uuid
SCANMEET_TEACHER_LOGIN=teacher@example.edu:staging-password
SCANMEET_PUPIL_A_LOGIN=pupil-a@example.edu:staging-password
SCANMEET_PUPIL_B_LOGIN=pupil-b@example.edu:staging-password
```

**No production domain is accepted.** The driver also requires the explicit
`--confirm-staging` option because it **creates a LiveKit session**. It ends the
room automatically where possible; a failed cleanup is reported for manual
inspection. Do not run during a real examination or with production accounts.

## Assertions performed automatically

1. HTTPS staging URL is exact and not production; three distinct logins
2. Teacher and pupils actually authenticate and see `/live/exam/<exam-id>`
3. No existing active room is taken over
4. Teacher opens a new staging LiveKit session and both pupils connect
5. Teacher's **WebRTC inbound video RTP** shows at least TWO pupil streams
6. Browser's decoded remote video frame counts **increase** across 3 s
7. Each pupil renders exactly ONE camera and their local video frames advance
8. Each pupil's `RTCPeerConnection.getStats()` reports **zero inbound video RTP**
   (catches received-but-hidden video missed by counting DOM tiles alone)
9. Both pupils have no outgoing audio sender and no visible mic control until
   authorized
10. Pupil A raises a hand, teacher approves, pupil publishes microphone
11. Teacher's received audio packet count **increases** on the actual SFU path
12. Teacher revokes; student mic button disappears AND no active outgoing audio
    sender remains after the revocation grace period

Failure exit codes: `0` **PASS**, `1` **FINDING** (behavior violated policy),
`2` **UNMEASURED** (cannot actually verify, e.g. staging not configured).
Do not count exit `2` as an E2E success. The result JSON intentionally contains
no credentials, media, cookies, JWTs, browser traces or identifying pupil data.

**Known possible defects intentionally surfaced:** the present pilot may show a
teacher's camera to pupils (extra pupil tile/inbound RTP) and may leave the
student microphone publishing after teacher revocation. Both must be corrected
and rerun rather than loosening the acceptance criteria.

## GitHub Actions

The `ScanMeet CDP E2E` workflow runs **offline contract unit tests** automatically
on relevant PRs without staging credentials. Its **real staging media job** is
*manual only*, gated by GitHub environment `scanmeet-staging` and a confirmation
input `STAGING_ONLY`. Configure repository environment variables
`SCANMEET_STAGING_URL`, `SCANMEET_EXAM_ID` and environment **secrets**
`SCANMEET_TEACHER_LOGIN`, `SCANMEET_PUPIL_A_LOGIN`, `SCANMEET_PUPIL_B_LOGIN`.
Use protected environment approvals. Depending on GitHub configuration, the
workflow must first exist on the default branch for the Dispatch button to
appear; the CLI above remains usable without a workflow.

Do not attach face screenshots or real audio recordings as CI artifacts. The
workflow uploads only a sanitized JSON verdict.
