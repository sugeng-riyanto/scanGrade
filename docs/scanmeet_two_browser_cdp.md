# ScanMeet two-browser staging CDP acceptance

This test is **staging-only**. It does not create accounts, change flags, read production secrets, or run in the production checkout. Run the offline staging gate and the seven-row signed webhook probe first. Use only synthetic teacher/pupil test users and an isolated staging LiveKit room.

## Prerequisites

- Python 3.12+, Chromium and Playwright (separate, optional E2E dependency).
- A real HTTPS staging URL, an isolated staging Supabase project, flags enabled, and the signed webhook probe returning 7/7 PASS.
- Three distinct already-authenticated Playwright `storage_state` JSON files for test teacher, pupil1 and pupil2. Keep outside Git (for example `~/.config/scanmeet/e2e/` with mode 0700 and files 0600). The test never prints their contents.
- Three full URLs pointing to the **same staging host** and the *same active ScanMeet room*, each appropriate to its role.
- Actual page selectors for teacher join, pupil join, pupil mic request and teacher approval, as applicable. Inspect the currently deployed UI and fill those in; **do not guess**.

```bash
cd ~/scanmeet-staging
. .venv-scanmeet/bin/activate
python -m pip install "playwright>=1.55,<2"
python -m playwright install chromium

# First establish gate and webhook preconditions.
python -m deploy.scanmeet_staging_gate --env ~/.config/scanmeet/staging.env
set -a; . ~/.config/scanmeet/staging.env; set +a
python -m deploy.scanmeet_webhook_probe --url "$SCANMEET_STAGING_URL"

python -m deploy.scanmeet_two_browser_cdp \
  --teacher-url 'https://staging.your-domain/path/to/teacher/room' \
  --pupil1-url 'https://staging.your-domain/path/to/pupil1/room' \
  --pupil2-url 'https://staging.your-domain/path/to/pupil2/room' \
  --teacher-state "$HOME/.config/scanmeet/e2e/teacher.json" \
  --pupil1-state "$HOME/.config/scanmeet/e2e/pupil1.json" \
  --pupil2-state "$HOME/.config/scanmeet/e2e/pupil2.json" \
  --teacher-join 'REPLACE_WITH_REAL_TEACHER_JOIN_SELECTOR' \
  --pupil-join 'REPLACE_WITH_REAL_PUPIL_JOIN_SELECTOR' \
  --ask-selector 'REPLACE_WITH_REAL_MIC_REQUEST_SELECTOR' \
  --approve-selector 'REPLACE_WITH_REAL_APPROVE_SELECTOR' \
  --out "$HOME/.config/scanmeet/e2e/result.json"
```

Omit a join selector only when that role joins automatically. **Do not omit mic request / teacher approval selectors:** missing either fails acceptance closed. Dummy selectors fail on timeout.

## What the runner measures

Two independent Chromium browser processes are started. Browser A contains two separate authentication contexts (teacher and pupil 1); browser B contains the second pupil. Both pupils run instrumented getUserMedia capturing live local audio/video track states; Chrome DevTools Protocol `Runtime.evaluate` measures the actual DOM video elements, decoded video dimensions, and `getVideoPlaybackQuality().totalVideoFrames`.

The mandatory acceptance assertions are:

1. Pupil 1: `document.querySelectorAll('#live-grid video').length === 1`, both with another pupil present.
2. Pupil 2: same check.
3. Each pupil's single video increments `totalVideoFrames` across two readings at least **three seconds** apart, and has positive decoded dimensions.
4. Both pupil browsers have a live enabled local camera capture track.
5. Before approval, neither pupil has a live **enabled** captured audio track.
6. Pupil 1 requests mic; teacher explicitly approves; pupil 1 gains a live enabled audio track.
7. Pupil 2 stays muted after pupil 1's approval.

Output `result.json` contains sample values and pass/fail checks; exit 0 is PASS and exit 1 is FAIL. Never publish the full artifact without checking that URLs/room paths contain no tokens or private test data.

**Scope limitation:** A browser getUserMedia capture state and decoded remote frames are strong browser-side evidence, but not a proof of SFU server subscription policy or RTP authorization. An adversarial WebRTC subscriber may require an additional server-side isolation audit. This test also cannot prove correct UI semantics if the deployed selectors refer to wrong controls; inspect them before running. Run the driver against the actual staging deployment and retain both pupils' `count=1` and increasing frame samples before declaring acceptance.

Do not treat a missing staging URL, skipped approval, a login redirect, or a failed navigation as PASS.
