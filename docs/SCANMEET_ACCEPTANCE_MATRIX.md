# ScanMeet acceptance matrix — release isolation and ownership

This is a test handoff, **not** evidence that staging passed. Keep general meetings,
background effects, and recording out of the Exam production-release path until
their own acceptance gates pass.

## Release tracks

| Track | Target | Gate | Owner |
| --- | --- | --- | --- |
| Exam | PR #9 integration | 7/7 latest-commit CI, staging media 12/12, webhook 7/7, continuity and authorization | Exam engineer + QA |
| Meeting | separate feature branch | role/school membership, room lifecycle, invitation expiry/revocation, capacity | Meeting engineer |
| Background | separate feature branch | image validation, UI preview/fallback, device budget, exam default OFF | UI/media engineer |
| Recorder | separate feature branch | receive-only grants, local-only recording, visible notices and consent | Privacy/media engineer |

## Exam per-exam ON/OFF acceptance (server is authoritative)

- [ ] Global flags OFF deny join, session creation, and issue no token.
- [ ] Exam setting OFF denies student joining even when global flags ON.
- [ ] Teacher authorized for school/exam can enable one exam only; a different
      exam or school remains unchanged.
- [ ] Student / cross-school teacher / link-holder cannot toggle; expect denial.
- [ ] Two concurrent starts cannot produce two active sessions.
- [ ] Teacher ends/disables an active room; future token issuance is refused;
      media is disconnected and clean-up exceptions are reported.
- [ ] Turn ON again creates a fresh room, never revives revoked token grants.
- [ ] Attempt answers, timer and autosave remain unaffected during enable,
      disable, reconnect, LiveKit unavailable and browser permission denial.
- [ ] Browser UI clearly labels OFF/ON after server confirmation, not optimistic
      local state. CSRF and school-scope checks apply to write routes.

## General meeting access matrix

| Principal | Teacher-only | Selected classes | All classes | Student-group |
| --- | --- | --- | --- | --- |
| Same-school organizer | Yes | Yes | Yes | By approved school policy |
| Same-school selected teacher | Invite only | Invite only | Invite only | Invite only |
| Student in targeted class | No | Invite/eligible | Eligible | Approved member only |
| Same-school student outside target | No | No | Depends on school-defined scope | No |
| Different school | No | No | No | No |
| Possesses shared link only | No authority | No authority | No authority | No authority |

Test token issuance, joins, room roster, moderation and revocation on the
**server**; links are identifiers, not permission grants.

## Virtual background acceptance

- [ ] Default Indonesia nature pack displays properly on narrow and wide screens.
- [ ] Licenses / origin of assets recorded; no unlicensed scraped photos.
- [ ] Upload validates file size, actual MIME/dimensions, strips metadata,
      isolates tenant assets and blocks malformed image content.
- [ ] Preview, change, remove, blur and reset have visible states.
- [ ] Segmentation without physical green screen is distinguished from chroma key.
- [ ] Unsupported or low-powered devices fall back to original camera.
- [ ] Exam policy defaults to **no background replacement** unless separately
      explicitly permitted; camera supervisory coverage is not weakened.

## Local recording / third-viewer acceptance

- [ ] Recorder token is receive-only: publish camera/audio/screen denied.
- [ ] Recording authorization requires admin-in-school, authorized owner or
      delegated operator; URL possession by itself is insufficient.
- [ ] Visible recording indication and required notices/consent shown to every
      participant; unauthorized attempt denied and auditable.
- [ ] In-app recording media is saved **locally only** using permitted browser
      APIs (or locally by OBS); server and storage receive no media blobs.
- [ ] Test playable audio+video file, unsupported codecs, disk-full handling,
      pause/stop, permission revocation, meeting end and recorder disconnect.
- [ ] Browser clean feed for OBS avoids long-lived token in URLs.
- [ ] Clearly document external OBS limitations: remote web app cannot delete
      files or forcibly stop independent screen capture outside its process.
- [ ] Exam recording remains disabled.

## Stage release evidence — never infer PASS from offline CI

- [x] HTTPS on isolated staging host (not scangrade.web.id or its subdomains).
- [ ] Staging database and LiveKit project differ from production.
- [x] Five synthetic identities: 2 pupils in one school, one pupil of another
      school, a teacher, and one pupil who belongs to **no** school (the case
      that must be refused rather than served).
- [x] Chrome/CDP real RTP evidence: 13 named media rows PASS, pupil-to-pupil
      inbound RTP zero; teacher receives correct feeds; mic audio only when
      approved and stops on revoke.
- [x] LiveKit webhook security probes: 7/7 measured on isolated host.
- [ ] Manual reject chat/screen sharing/recording in Exam.
- [ ] 30-second network drop/rejoin, autosave and timer continuity.
- [ ] Log redaction, no tokens/passwords in artifacts.
- [x] Signed run output records commit SHA, staging host, timestamp, test
      accounts as pseudonyms and PASS/FAIL/SKIP for each item.
- [ ] Explicit owner approval for merge into main and production rollout.

### Measured on the isolated staging host — 25 Oct 2026

Recorded here so the checklist above is a record of measurements rather than of
intentions. Everything below was taken **against the running staging deployment**,
from a **clean checkout of the commit named**, not from a working tree.

- **Commit:** `17d857b8e1f84cf6cf87af4129cbdffc9dee5755`
  (`staging/scanmeet-fixture-provisioner`, the head PR #25 tracks).
- **Host:** `https://scanmeet-stg.103.93.133.193.sslip.io` — an sslip.io name for
  the box's own IP, so it is neither `scangrade.web.id` nor one of its subdomains
  and passes `deploy/scanmeet_staging_gate.py`'s host refusal.
- **Exam:** `be8a29e4-970f-4987-9e98-a6dc552050b5` (synthetic, staging only).
- **Identities:** four synthetic accounts — teacher, two pupils in school A, one
  pupil in school B. Addresses are not reproduced here.
- **Run at:** `2026-10-25T07:25:11Z`.

Method — the committed runner, so a second machine repeats this without editing a
file. Values live in one private env file outside the repository; nothing is
exported to a shell history and nothing is printed:

```bash
python -m deploy.scanmeet_runner --env "$HOME/scanmeet-staging.env" --all --check
python -m deploy.scanmeet_runner --env "$HOME/scanmeet-staging.env" --all --signoff
python -m deploy.scanmeet_rbac_gate --env "$HOME/scanmeet-staging.env" \
    --url "$SCANMEET_STAGING_URL" --exam "$SCANMEET_EXAM_ID" \
    --teacher "$SCANMEET_TEACHER" --student "$SCANMEET_STUDENT" \
    --student2 "$SCANMEET_STUDENT2" --outsider "$SCANMEET_OUTSIDER" \
    --noschool "$SCANMEET_NOSCHOOL"
```

| Gate | Result | Exit | What it read |
| --- | --- | --- | --- |
| `scanmeet_webhook_probe` | **7/7 PASS** | 0 | signature accepted, forgery/missing/stale/duplicate/wrong-room/unknown-event refused |
| `scanmeet_media_gate` | **12/12 SIGNED OFF** (the twelve rows that existed then) | 0 | `sdk-loaded`, `session`, `pupil-camera`, `teacher-receives-camera`, `pupil-isolation`, `mic-starts-closed`, `hand-raise`, `mic-approved`, `audio-reaches-teacher`, `mic-closed`, `outsider-refused`, `room-ended` |
| `scanmeet_media_gate` — `token-expiry` (added later) | **FAIL** | 1 | the SFU **admitted** a token 80 s past its own `exp`, while a fresh token connected from the same room in the same moment. The app's half passed: `exp - nbf` is exactly 300 s, and an ended session issues no new token (`room-ended` PASS). Whether LiveKit applies a grace window wider than the 10 s margin, or does not enforce `exp` at all, is not yet measured — see the note below. |
| `scanmeet_rbac_gate` | **15/15 PASS** | 0 | both sign-ins, the teacher door, the pupil's refused `POST /session`, the owner's room, the outsider's refused status and token, **the no-school pupil's refused paper (302 to the exam list) and refused token (403)**, the token's publish sources (`['camera']` before approval), the owner's `POST /end`, and RLS read as a **user JWT** (`profiles` scoped to 1 school; two schools share 0 rows; `live_sessions`/`live_hand_raises` answer 403 to a browser key) |

Media row 4 was measured after a repair found by the first run of this sequence:
the driver waited for a `<video>` element and read `videoWidth` before the first
frame was decoded, so it reported "no frame" about the instant of the snapshot. It
now waits for a decoded frame per pupil identity. Three consecutive runs after the
change each reported 12/12.

The `noschool` identity is seeded by `deploy/scanmeet_staging_fixture.py` with
`school_id` NULL and no class, and the fixture finds it on a re-run by asking for
the null (`is_(...)`) rather than `eq(..., None)` — a filter PostgREST reads as the
string `None`, which would have created a second copy on every run and looked
idempotent while doing it.

`token-expiry` is the one row whose measurement is a clock: it waits out a token
minted with a 300-second life (`exp - nbf`, read from the token the app issued,
which must be the policy's five minutes) and then offers that same token from the
page that holds it, which must be refused. It is taken **before** `room-ended`,
because after the room is gone the SFU would refuse for the wrong reason, and the
run's budget carries the wait (`300 + TTL + 60`) so the row cannot turn into "the
run exceeded 300s".

Two repairs came out of taking it for real, and both were failures of the row
rather than of the product: the five-minute wait lost Chrome's DevTools socket
(`1011 keepalive ping timeout`, so the row and `room-ended` both skipped), and the
first "refusal" it reported was `TypeError: window.LivekitClient.connect is not a
function` — a broken call scored as evidence. The wait now answers every browser
it left open, and a rejection only counts after the **same room has accepted a
freshly minted token from a second pupil in the same moment**. A CDP socket that
dies is could-not-measure, never a pass.

What it measured on staging, with the control in place: `connected: true` for a
token whose `exp` had passed **80 seconds earlier**, and `connected: true` for the
fresh control token in the same run — so the room was reachable and the stale
token was accepted. That is reported as FAIL. The next reading is a token minted
with `exp` far in the past (ten minutes, using the staging project's own API
secret) to separate "LiveKit has a grace window wider than the margin" from
"LiveKit does not enforce `exp` at all"; until that is measured, no claim is made
either way.

Still **open** in the checklist above, and deliberately not claimed as PASS:

- the 30-second network drop/rejoin, autosave and timer continuity — needs a run
  with the network cut under a live attempt;
- the manual reject of chat / screen sharing / recording — a UI judgement, not a
  gate;
- any browser or device other than desktop Chrome driven by CDP. Edge, Android and
  iPhone are **NOT TESTED**; the media gate's synthetic-device emulation is not a
  device test and is reported as `(synthetic devices)` on its own sign-off sheet;
- a load run with participants joining a room — not attempted, because the staging
  host is the same 1 vCPU box that serves production exams;
- owner approval for merge and rollout.

One reliability finding, recorded rather than repaired: a run that inherits an
`active` session row whose LiveKit room has already been deleted (possible when a
previous run stopped before ending its session) opens no room, and the gate reports
it as **could not measure** rather than clearing the stale row. It is a property of
the driver's start, not of the product, and it has not reproduced on a run that
began from a quiet state.

### CI integration guidance

PR #10 has the separate CDP workflow while the integration branch may not.
Do not count a green **offline** CDP job or skipped protected-environment job
as 12/12 staging PASS. Reconcile PR #10 and PR #11 test overlap before merging,
and require an explicit live staging run with saved non-sensitive evidence.
