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

- [ ] HTTPS on isolated staging host (not scangrade.web.id or its subdomains).
- [ ] Staging database and LiveKit project differ from production.
- [ ] Four synthetic identities with 2 pupils in same school + outsider.
- [ ] Chrome/CDP real RTP evidence: 12 named media rows PASS, pupil-to-pupil
      inbound RTP zero; teacher receives correct feeds; mic audio only when
      approved and stops on revoke.
- [ ] LiveKit webhook security probes: 7/7 measured on isolated host.
- [ ] Manual reject chat/screen sharing/recording in Exam.
- [ ] 30-second network drop/rejoin, autosave and timer continuity.
- [ ] Log redaction, no tokens/passwords in artifacts.
- [ ] Signed run output records commit SHA, staging host, timestamp, test
      accounts as pseudonyms and PASS/FAIL/SKIP for each item.
- [ ] Explicit owner approval for merge into main and production rollout.

### CI integration guidance

PR #10 has the separate CDP workflow while the integration branch may not.
Do not count a green **offline** CDP job or skipped protected-environment job
as 12/12 staging PASS. Reconcile PR #10 and PR #11 test overlap before merging,
and require an explicit live staging run with saved non-sensitive evidence.
