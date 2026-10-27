"""The two-browser media driver: what it claims, and whether it can see it.

`deploy/scanmeet_media_gate.py` exists because three of the readings the staging
sign-off asks a person to take cannot be taken by looking at a page: a frame
counter that is actually moving, a microphone the **SFU** refuses rather than a
button the page hides, and media one pupil's browser did *not* receive. This file
is its guard, in two halves.

**The claims** (no browser): each judgement is a pure function over a reading, so
every finding is reached by handing it the reading that should produce it. The
one that matters most is the isolation rule: it is deliberately judged by
participant identity rather than by how many `<video>` elements are on the page,
and the test that fails a leak *with the same tile count* a clean page has is what
keeps that honest.

**The instrument** (a browser, skipped by name when there is none): the driver is
a CDP client and a set of readings, and both can be wrong in ways no judgement
test would catch — a press that lands nowhere, a hidden control reported as
visible, two browsers that are secretly one page. A harness page with a real
`canvas.captureStream()` video and a `confirm()` is served on loopback and driven
through the driver's own `Browser`, so the plumbing is measured rather than
assumed.

What this file cannot do is open a LiveKit room: that needs a project, and it is
the driver's own job on the staging host.
"""

import importlib.util
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "scanmeet_media_gate.py"
SCRIPT = ROOT / "app" / "static" / "js" / "scan-live-exam.js"
TEMPLATE = ROOT / "app" / "templates" / "live" / "exam.html"


def gate():
    """Import `deploy/scanmeet_media_gate.py` — `deploy/` is not a package."""
    if not GATE_PATH.exists():
        pytest.fail("deploy/scanmeet_media_gate.py does not exist")
    spec = importlib.util.spec_from_file_location("sg_media_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = gate()

#: The browser the driver would use, through the tree's one locator — evaluated at
#: import so the plumbing test skips by name on a machine without one, exactly as
#: the other browser gates do.
BROWSER = GATE.touch_gate.locate_browser()


@pytest.fixture(autouse=True)
def _not_a_runner(monkeypatch):
    """No test may inherit GitHub's own variables and silently expect a commit.

    `expected_commit()` reads `GITHUB_ACTIONS` and `GITHUB_SHA`, which are set on
    the very runner this suite runs on in CI — so a test that did not clear them
    would pass here and refuse there. Test-local, because it is about the suite
    being hermetic rather than about what the driver does.
    """
    for name in ("GITHUB_ACTIONS", "GITHUB_SHA", "SCANMEET_EXPECT_COMMIT"):
        monkeypatch.delenv(name, raising=False)


def token_with(sources, *, data=False):
    """A join token shaped like the one the deployment issues, signed with junk.

    The driver reads grants without verifying, on purpose: the token came from the
    deployment over TLS a moment ago, and what is under test is what it says.
    """
    import jwt

    return jwt.encode({"video": {"canPublishSources": sources,
                                 "canPublishData": data}},
                      "not-the-secret", algorithm="HS256")


# --------------------------------------------------------------------------- #
# What it is driving: the page's own ids and the page's own labels
# --------------------------------------------------------------------------- #
def test_the_snapshot_reads_ids_the_page_actually_has():
    """Every id the reading queries exists in the template or its script.

    A renamed element would not raise: the reading would come back with an empty
    string, and "the queue shows nothing" is exactly what a *working* room prints
    before a pupil raises their hand. So the names are held to the page here.
    """
    source = TEMPLATE.read_text(encoding="utf-8") + SCRIPT.read_text(encoding="utf-8")
    expression = GATE.snapshot_expression()
    ids = set(re.findall(r"getElementById\('([a-z-]+)'\)", expression))
    ids |= set(re.findall(r"byId\('([a-z-]+)'\)", expression))
    ids |= set(re.findall(r"querySelector(?:All)?\('#([a-z-]+)", expression))
    assert {"scan-live", "live-grid", "live-queue", "live-events",
            "live-mic", "live-hand", "live-status", "live-warning"} <= ids
    for element_id in ids:
        assert f'id="{element_id}"' in source or f'"# {element_id}"' in source or \
            f"#{element_id}" in source, (
            f"the reading asks for `{element_id}`, which neither the template nor "
            f"the page's script provides")


def test_the_queue_labels_are_the_ones_the_page_renders():
    """A press is addressed by what the teacher sees, so a reword is a red test."""
    script = SCRIPT.read_text(encoding="utf-8")
    for action, label in (("approve", GATE.LABEL_APPROVE),
                          ("close", GATE.LABEL_CLOSE)):
        assert f'["{action}", "{label}"]' in script, (
            f"`{label}` is no longer the label the page gives `{action}`; the driver "
            f"would press nothing and report a room that never answered")


def test_every_row_it_prints_is_in_the_order():
    """A row missing from the order would be silently absent from a run."""
    names = set(re.findall(r'(?:row|Row)\(\s*"([a-z][a-z-]+)"', GATE_PATH.read_text(
        encoding="utf-8")))
    # The two dynamic spellings the media rows carry.
    # The two rows the media half spells through a conditional still carry a
    # hyphen; requiring one keeps `"microphone"` (a source, not a row) out.
    names |= set(re.findall(r'\b"((?:pupil|teacher|mic|audio|hand|outsider|room|'
                            r'sdk|session)[a-z]*-[a-z-]+)"', GATE_PATH.read_text(
                                encoding="utf-8")))
    ordered = set(GATE.ROW_ORDER)
    assert names <= ordered, f"rows printed but not ordered: {sorted(names - ordered)}"
    assert len(GATE.ROW_ORDER) == len(set(GATE.ROW_ORDER))
    for name in ("pupil-camera", "mic-approved", "pupil-isolation", "room-ended"):
        assert name in ordered


def test_an_unmeasured_run_names_every_row_it_never_took():
    """A run that stopped early must not read as a short list of passes."""
    driver = GATE.Driver("https://staging.example.edu", "exam-1",
                         {"teacher": "t@example.edu:pw", "student": "s@example.edu:pw"})
    skipped = driver._unmeasured("the SDK did not load")
    # Every row after the two opening ones. Written as the tail of the order so a
    # row added to `ROW_ORDER` cannot be left out of an early stop's list.
    assert [item.name for item in skipped] == list(GATE.ROW_ORDER[2:])
    assert all(item.state == "skipped" for item in skipped)


def test_a_missing_account_is_exit_two_not_a_pass():
    assert GATE.main([]) == GATE.EXIT_CANNOT_MEASURE
    assert GATE.main(["--url", "https://staging.example.edu"]) == GATE.EXIT_CANNOT_MEASURE


def test_a_printed_line_cannot_fail_on_an_unhelpful_console():
    """A row's detail carries strings this file did not write.

    `touch_gate.sign_in` explains a failure with an em dash, and the page's own
    text is Indonesian: an unencodable character once crashed a guard on a cp1252
    console, so every printed line is folded first.
    """
    folded = GATE.ascii_safe("gagal \u2014 coba \u2192 lagi\u2026 \u00a72")
    assert folded == "gagal - coba -> lagi... S2"
    assert all(ord(character) < 128 for character in folded)


# --------------------------------------------------------------------------- #
# The claims: each finding reached by the reading that should produce it
# --------------------------------------------------------------------------- #
def video(identity, *, w=640, h=480, frames=10, paused=False, ready=4):
    return {"identity": identity, "w": w, "h": h, "ready": ready,
            "frames": frames, "muted": False, "paused": paused}


def test_a_camera_that_is_decoding_passes():
    first = GATE.Reading(videos=[video("p_a")])
    later = GATE.Reading(videos=[video("p_a", frames=48)])
    assert GATE.camera_finding(first, later, "p_a", "the pupil's page") == ""


def test_a_camera_whose_frames_do_not_advance_is_a_finding():
    """The defect a person's screenshot cannot see: a sized, empty tile."""
    first = GATE.Reading(videos=[video("p_a")])
    frozen = GATE.Reading(videos=[video("p_a")])
    problem = GATE.camera_finding(first, frozen, "p_a", "the pupil's page")
    assert "did not advance" in problem and "published but not arriving" in problem


def test_a_tile_with_no_frames_and_a_missing_tile_are_different_findings():
    empty = GATE.Reading(videos=[video("p_a", w=0, h=0)])
    assert "never received a frame" in GATE.camera_finding(
        empty, empty, "p_a", "the pupil's page")
    nothing = GATE.Reading(videos=[])
    assert "no camera tile" in GATE.camera_finding(
        nothing, nothing, "p_a", "the pupil's page")


def test_isolation_is_judged_by_identity_not_by_how_many_tiles():
    """The reading this driver exists for, and the wrong rule for it.

    A pupil legitimately holds two videos — their own and the supervisor's — so
    "exactly one video" is wrong twice: it fails a clean room with a teacher
    camera, and it passes a leak as long as the pupil's own tile is missing.
    """
    clean = GATE.Reading(videos=[video("p_self"), video("p_teacher")])
    assert GATE.isolation_finding(clean, own_identity="p_self",
                                  supervisor="p_teacher",
                                  other_pupil="p_other") == ""

    # Two tiles, exactly as above — and one of them is another pupil's camera.
    leaked = GATE.Reading(videos=[video("p_self"), video("p_other")])
    problem = GATE.isolation_finding(leaked, own_identity="p_self",
                                     supervisor="p_teacher",
                                     other_pupil="p_other")
    assert "another pupil's camera" in problem


def test_a_foreign_camera_from_nobody_named_still_fails():
    stranger = GATE.Reading(videos=[video("p_self"), video("p_someone")])
    problem = GATE.isolation_finding(stranger, own_identity="p_self",
                                     supervisor="p_teacher", other_pupil="p_other")
    assert "may not see" in problem


def test_a_page_with_no_video_says_so_instead_of_passing():
    empty = GATE.Reading(videos=[])
    assert "cannot" in GATE.isolation_finding(empty, own_identity="p_self",
                                               supervisor="p_teacher",
                                               other_pupil="p_other")
    others_only = GATE.Reading(videos=[video("p_other")])
    assert "own camera is not" in GATE.isolation_finding(
        others_only, own_identity="p_self", supervisor="p_teacher",
        other_pupil="p_other")


def test_the_launcher_passes_what_a_media_page_needs():
    """The flags, because each absent one is a reading that lies.

    No autoplay policy and every remote camera reads as dead; no fake UI and a
    headless browser never publishes at all. The `/dev/shm` escape is asserted
    where it now lives — the shared launcher — rather than in a second copy here.
    """
    flags = GATE.media_flags()
    for flag in ("--autoplay-policy=no-user-gesture-required",
                 "--use-fake-ui-for-media-stream",
                 "--use-fake-device-for-media-stream"):
        assert flag in flags, f"{flag} is missing from the launch"
    assert "--use-file-for-fake-video-capture=/clip.y4m" in GATE.media_flags(
        video_file="/clip.y4m")
    real = GATE.media_flags(fake=False)
    assert "--use-fake-device-for-media-stream" not in real
    assert "--use-fake-ui-for-media-stream" in real

    # One launch path: the flags are extras for it, and the container's 64 MB
    # `/dev/shm` is handled there for every gate that starts a browser.
    argv, env = GATE.touch_gate.browser_launch(
        "/usr/bin/google-chrome", 9222, "/tmp/profile")
    assert "--disable-dev-shm-usage" in argv
    assert argv[-1] == "about:blank", (
        "the shared launcher must still end on the blank page: a flag appended "
        "after it would be read as a URL"
    )
    assert env["HOME"] == "/tmp/profile"


def test_the_browser_starts_through_the_shared_retried_launcher(monkeypatch):
    """One launch path, and the media flags arrive as its `extra_args`.

    The driver used to own its own `Popen` and its own 25-line wait for a debug
    target, so a launch that lost a race cost a whole run and said nothing about
    why (the same defect `touch_gate.start_browser` was written for).
    """
    import asyncio

    seen = []

    def fake_start(browser, **kwargs):
        seen.append((browser, kwargs))
        return None, None, None, (
            "the browser never offered a debug target (chrome said: boom) — after 2 launches")

    monkeypatch.setattr(GATE.touch_gate, "start_browser", fake_start)
    browser = GATE.Browser("teacher", "http://127.0.0.1:9", {}, binary="/bin/chrome")
    with pytest.raises(GATE.CannotMeasure) as caught:
        asyncio.run(browser.open())
    assert "boom" in str(caught.value), (
        "the reason Chrome gave is the only thing that can tell a race from a "
        "broken box, and the driver must not drop it"
    )
    assert seen == [(  
        "/bin/chrome",
        {"extra_args": tuple(GATE.media_flags())},
    )], "the driver must start its browser through `touch_gate.start_browser`"


def test_the_grant_is_what_the_sfu_enforces():
    assert GATE.publish_sources(token_with(["camera"])) == ["camera"]
    assert GATE.publish_sources(token_with(["camera", "microphone"])) == [
        "camera", "microphone"]
    assert GATE.may_publish_data(token_with(["camera"], data=True)) is True
    assert GATE.may_publish_data(token_with(["camera"])) is False


def test_a_finding_outranks_a_gap():
    assert GATE.verdict([GATE.Row("a", "pass"), GATE.Row("b", "pass")]) == 0
    assert GATE.verdict([GATE.Row("a", "pass"), GATE.Row("b", "skipped", "x")]) == 2
    assert GATE.verdict([GATE.Row("a", "skipped", "x"),
                         GATE.Row("b", "fail", "y")]) == 1


def test_the_queue_status_is_parsed_out_of_what_the_teacher_sees():
    reading = GATE.reading_of({
        "path": "/live/exam/x", "status": "ok", "livekit": True,
        "videos": [], "audio": [], "events": [],
        "mic_visible": False, "mic_label": "", "hand_enabled": True,
        "queue": [{"text": "Budi — pending", "buttons": ["Izinkan"],
                   "status": ""},
                  {"text": "Sari — approved", "buttons": ["Akhiri bicara"],
                   "status": ""}],
    })
    assert reading.queue_states() == ["pending", "approved"]
    assert GATE.reading_of({"error": "no live page on this URL"}).error
    assert GATE.reading_of("nonsense", "teacher").error.startswith("teacher answered")


# --------------------------------------------------------------------------- #
# The instrument: can it see a room, or only describe one?
# --------------------------------------------------------------------------- #
#: A stand-in for the exam page, behaving the way `app/static/js/scan-live-exam.js`
#: behaves at the four moments the driver reads it: a camera drawn from a canvas
#: (so decoded frames are real and countable), a hand raise that lands as a
#: `pending` row, an approval that offers the microphone, a press that publishes
#: one, a close that takes it away, and an `Akhiri Live` that asks before it ends.
HARNESS = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<title>harness</title></head><body>
<div id="scan-live">
  <p id="live-status">Memeriksa sesi Live...</p>
  <p id="live-warning" hidden>Pengawasan terputus.</p>
  <div id="live-grid"><div data-identity="p_self"><video id="cam" muted
       playsinline autoplay></video></div></div>
  <button id="live-hand" type="button" disabled>Angkat tangan</button>
  <button id="live-mic" type="button" hidden>Aktifkan mikrofon</button>
  <button id="live-end" type="button">Akhiri Live</button>
  <div id="live-queue"></div>
  <div id="live-events"></div>
</div>
<script>
window.LivekitClient = {};
const canvas = document.createElement('canvas');
canvas.width = 160; canvas.height = 120;
const ctx = canvas.getContext('2d');
let n = 0;
(function draw() { n += 1;
  ctx.fillStyle = 'hsl(' + (n % 360) + ',70%,50%)';
  ctx.fillRect(0, 0, 160, 120);
  requestAnimationFrame(draw);
})();
const cam = document.getElementById('cam');
cam.srcObject = canvas.captureStream(24);
cam.play().catch(() => {});
navigator.mediaDevices.getUserMedia({audio: true})
  .then(s => { window.__mic = s; })
  .catch(() => {});
setTimeout(() => { document.getElementById('live-hand').disabled = false; }, 300);
const root = document.getElementById('scan-live');
function audio() { return [...root.querySelectorAll('audio')]; }
document.getElementById('live-hand').onclick = () => {
  const row = document.createElement('div');
  row.textContent = 'Budi \u2014 pending';
  const accept = document.createElement('button');
  accept.type = 'button'; accept.textContent = 'Izinkan';
  accept.onclick = () => {
    row.firstChild.textContent = 'Budi \u2014 approved';
    accept.remove();
    document.getElementById('live-mic').hidden = false;
    const close = document.createElement('button');
    close.type = 'button'; close.textContent = 'Akhiri bicara';
    close.onclick = () => {
      row.firstChild.textContent = 'Budi \u2014 closed';
      close.remove();
      audio().forEach(a => { a.srcObject = null; a.remove(); });
      document.getElementById('live-mic').hidden = true;
    };
    row.append(close);
  };
  row.append(accept);
  document.getElementById('live-queue').append(row);
};
document.getElementById('live-mic').onclick = () => {
  const a = document.createElement('audio');
  a.srcObject = window.__mic;
  root.append(a);
};
document.getElementById('live-end').onclick = () => {
  if (confirm('Akhiri Live?')) {
    document.getElementById('live-status').textContent =
      'Live selesai. Ujian tetap berjalan.';
  }
};
</script></body></html>
"""


#: What the harness answers on `/health`: the shape the app publishes, so the
#: driver's own reader is measured against a real HTTP answer rather than a stub.
HEALTH = json.dumps({
    "status": "ok",
    "commit": {"available": True, "commit": "77abe6f",
               "full_commit": "77abe6fac0ac227eda219bfdf8596a59d86deaf9",
               "reason_key": None},
})


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the handler's own name
        if self.path == GATE.served_commit_gate.DEFAULT_HEALTH_PATH:
            body = HEALTH.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = HARNESS.encode("utf-8")
        self.send_response(200 if self.path in ("/", "/index.html") else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D401 - silence the test server
        return


@pytest.fixture(scope="module")
def harness():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


needs_browser = pytest.mark.skipif(
    BROWSER is None,
    reason=("no Chrome/Chromium on this machine, so the driver's own plumbing cannot be "
            "driven — set SG_CHROME to a binary"))


async def _drive(base_url: str):
    """The driver's own `Browser`, through the driver's own readings."""
    import asyncio

    first = GATE.Browser("student", base_url, {}, binary=BROWSER)
    second = GATE.Browser("student2", base_url, {}, binary=BROWSER)
    await first.open()
    await second.open()
    try:
        await first.goto("/")
        await second.goto("/")
        assert await first.wait(GATE.HAND_READY, 30), "the harness never enabled the hand"
        assert await first.wait("document.getElementById('cam').videoWidth > 0", 30), \
            "the harness camera never produced a frame"

        one = await first.read()
        await asyncio.sleep(1.5)
        two = await first.read()
        assert one.livekit and two.livekit
        assert GATE.camera_finding(one, two, "p_self", "the page") == "", (
            "the driver cannot see a decoding camera on a page that is decoding one")

        # A hidden control is not pressable — the driver must not press the point
        # where the control would be, and report success for a click on nothing.
        assert await first.press("document.getElementById('live-mic')") is False

        assert await first.press("document.getElementById('live-hand')")
        assert await first.wait(
            "[...document.querySelectorAll('#live-queue > div')].length > 0", 10)
        queued = await first.read()
        assert queued.queue_states() == [GATE.PENDING]
        # Two browsers, two pages: the second one saw nothing of the first.
        assert (await second.read()).queue == []

        assert await first.press_label(GATE.LABEL_APPROVE)
        assert await first.wait(GATE.MIC_VISIBLE, 10), \
            "the approval did not offer the microphone"
        assert await first.press("document.getElementById('live-mic')")
        assert await first.wait(GATE.AUDIO_LIVE, 10), \
            "the driver cannot see a published microphone when there is one"
        assert (await first.read()).audio_live() >= 1

        assert await first.press_label(GATE.LABEL_CLOSE)
        assert await first.wait(GATE.AUDIO_SILENT, 10), \
            "the close left a live audio track the driver cannot tell from a revoked one"
        assert await first.wait(GATE.MIC_GONE, 10)

        # Tapped rather than clicked: the handler opens `confirm()`, and a press
        # that waited for its reply would wait for a page waiting for the dialog.
        assert await first.tap("document.querySelector('#live-end')")
        assert await first.accept_dialog(10), "the confirm() never arrived"
        assert await first.wait(
            "/Live selesai/.test(document.getElementById('live-status').textContent)",
            10), "the page never acted on the accepted dialog"
    finally:
        await first.close()
        await second.close()


@needs_browser
def test_the_driver_can_see_a_room_when_there_is_one(harness):
    import asyncio

    asyncio.run(_drive(harness))


# ── the sign-off sheet, which is the artefact a pilot files ──────────


def _twelve(state="pass", detail="ok"):
    """Every row, all in one state. Named for history; the count is the driver's."""
    return [GATE.Row(name, state, detail) for name in GATE.ROW_ORDER]


def test_the_sheet_carries_the_url_the_exam_and_the_verdict():
    sheet = GATE.signoff_sheet(
        "https://staging.example.edu/", "9b17e852-565d-48de-a88c-8786d77f8014",
        _twelve(), GATE.EXIT_OK, browser="/usr/bin/google-chrome",
        devices="synthetic devices", run_at="2026-10-24T15:00:00+00:00")
    assert "https://staging.example.edu" in sheet.splitlines()[0]
    assert "/" * 2 in sheet.strip().splitlines()[0][:2] or sheet.startswith("## ")
    assert "9b17e852-565d-48de-a88c-8786d77f8014" in sheet
    whole = len(GATE.ROW_ORDER)
    assert f"`SIGNED OFF` (exit 0) - {whole}/{whole}" in sheet
    assert "2026-10-24T15:00:00+00:00" in sheet
    assert "/usr/bin/google-chrome" in sheet and "synthetic devices" in sheet
    # The twelve rows, in the order the driver takes them, once each.
    for index, name in enumerate(GATE.ROW_ORDER, start=1):
        assert f"| {index} | `{name}` |" in sheet
    assert sheet.count("| `") == len(GATE.ROW_ORDER)


def test_a_sheet_that_measured_nothing_says_so_in_its_verdict_line():
    """The whole point of the sheet: a gap cannot read as a complete table."""
    rows = _twelve()
    rows[8] = GATE.Row("audio-reaches-teacher", "skipped",
                       "the teacher is not in the room")
    sheet = GATE.signoff_sheet("https://staging.example.edu", "exam-1", rows,
                               GATE.EXIT_CANNOT_MEASURE)
    whole = len(GATE.ROW_ORDER)
    assert f"`CANNOT MEASURE` (exit 2) - {whole - 1}/{whole}" in sheet
    assert "not** a sign-off" in sheet
    assert "the teacher is not in the room" in sheet
    assert "**SKIP**" in sheet


def test_a_finding_is_labelled_and_counted():
    rows = _twelve()
    rows[2] = GATE.Row("pupil-camera", "fail", "the camera produced no frames")
    sheet = GATE.signoff_sheet("https://staging.example.edu", "exam-1", rows,
                               GATE.EXIT_FINDING)
    whole = len(GATE.ROW_ORDER)
    assert f"`NOT SIGNED OFF` (exit 1) - {whole - 1}/{whole}" in sheet
    assert "**FAIL**" in sheet
    assert "1 row(s) disagreed and 0 could not be measured" in sheet
    assert "not ready for pupils" in sheet


def test_a_table_cell_cannot_break_the_table():
    rows = _twelve()
    rows[0] = GATE.Row("sdk-loaded", "pass", "a | b\nsecond line")
    sheet = GATE.signoff_sheet("https://staging.example.edu", "exam-1", rows,
                               GATE.EXIT_OK)
    cell = [line for line in sheet.splitlines() if line.startswith("| 1 |")][0]
    assert r"a \| b second line" in cell, (
        "a pipe in a detail is GFM's cell escape or it is a fifth column")
    assert len(cell.splitlines()) == 1
    # Every table line has the same number of *columns* as the header. Counted
    # without the escapes, since an escaped pipe is still a pipe character.
    widths = {line.count("|") - line.count(r"\|")
              for line in sheet.splitlines() if line.startswith("|")}
    assert widths == {5}, f"a row broke the table: {widths}"


def test_the_sheet_is_ascii_because_it_gets_pasted_into_a_console():
    sheet = GATE.signoff_sheet("https://staging.example.edu", "exam-1",
                               _twelve(detail="*Akhiri Live* — done → yes"),
                               GATE.EXIT_OK)
    assert all(ord(char) < 128 for char in sheet), (
        "a sheet with a curly quote in it is a sheet that renders as ? on the "
        "console that printed the gate's own lines")


def test_the_signoff_flag_prints_the_sheet_and_keeps_the_exit_code(monkeypatch,
                                                                  capsys):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        rows = _twelve()
        rows[3] = GATE.Row("teacher-receives-camera", "skipped", "no tile arrived")
        return GATE.verdict(rows), rows, {"rows": []}

    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(GATE, "run", fake_run)
    _serving(monkeypatch, _reading())
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--signoff"])
    out = capsys.readouterr().out
    assert code == GATE.EXIT_CANNOT_MEASURE, (
        "printing a sheet must not change what the run is worth")
    assert out.startswith("## ScanMeet media sign-off")
    assert "CANNOT MEASURE" in out and "no tile arrived" in out
    assert calls and calls[0]["accounts"]["student"] == "murid@x:pw"


def test_json_and_signoff_together_are_refused_before_a_browser_is_driven(
        monkeypatch, capsys):
    """The first version let `--json` win and dropped the sheet in silence.

    A run that asked for a sign-off printed a JSON blob and exited 0, so nothing
    said the sheet was never produced. It is refused up front instead — and the
    refusal lands before the run, because driving three real browsers to then
    print "pick one flag" is five minutes spent to learn nothing.
    """
    drove = []

    def fake_run(**kwargs):
        drove.append(kwargs)
        return GATE.EXIT_OK, _twelve(), {}

    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(GATE, "run", fake_run)
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--signoff", "--json"])
    out = capsys.readouterr().out
    assert code == GATE.EXIT_CANNOT_MEASURE
    assert drove == [], "a flag conflict must be refused before the room is opened"
    assert "--json-out" in out, (
        "the refusal has to name the flag that does let a caller keep both")


def test_json_out_keeps_the_reading_beside_the_sheet(monkeypatch, capsys,
                                                     tmp_path):
    payload = {"rows": [{"name": "sdk-loaded", "state": "pass", "detail": "ok"}]}
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: (GATE.EXIT_OK, _twelve(), payload))
    _serving(monkeypatch, _reading())
    out_file = tmp_path / "reading.json"
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--signoff", "--json-out", str(out_file)])
    printed = capsys.readouterr().out
    assert code == GATE.EXIT_OK
    assert printed.startswith("## ScanMeet media sign-off")
    assert json.loads(out_file.read_text(encoding="utf-8"))["rows"][0]["name"] == "sdk-loaded", (
        "the sheet goes to stdout and the reading it was built from goes to the file")


def test_a_reading_that_could_not_be_written_is_not_a_pass(monkeypatch, capsys,
                                                           tmp_path):
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: (GATE.EXIT_OK, _twelve(), {"rows": []}))
    _serving(monkeypatch, _reading())
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--signoff", "--json-out", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == GATE.EXIT_CANNOT_MEASURE, (
        "twelve rows nobody can file is not a sign-off, even when all twelve passed")
    assert "could not write" in out


# ── the one host this driver must never reach by accident ────────────


def test_the_live_site_is_refused_before_a_room_is_opened(monkeypatch, capsys):
    """A stale `$SCANMEET_STAGING_URL` is the whole failure mode this guards.

    The driver opens a Live room and presses a teacher's controls; pointed at the
    live site it would do that to a real exam. Refused up front, with the override
    named, so the deliberate pilot is a decision and not a copy-paste.
    """
    drove = []
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: drove.append(kwargs) or (0, _twelve(), {}))
    code = GATE.main(["--url", "https://scan-grade.app", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw"])
    out = capsys.readouterr().out
    assert code == GATE.EXIT_CANNOT_MEASURE
    assert drove == [], "the live site must be refused before a browser is driven"
    assert "--allow-production" in out


def test_allow_production_lets_a_deliberate_pilot_through(monkeypatch, capsys):
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: (GATE.EXIT_OK, _twelve(), {"rows": []}))
    _serving(monkeypatch, _reading())
    code = GATE.main(["--url", "https://scan-grade.app", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--signoff", "--allow-production"])
    out = capsys.readouterr().out
    assert code == GATE.EXIT_OK
    assert out.startswith("## ScanMeet media sign-off")


def test_the_live_hosts_are_the_ones_the_staging_gate_refuses():
    """One fact, two files: held equal instead of trusted to stay equal."""
    from deploy import scanmeet_staging_gate as staging

    assert staging.PRODUCTION_SITE in GATE.PRODUCTION_HOSTS
    assert "www." + staging.PRODUCTION_SITE in GATE.PRODUCTION_HOSTS
    assert GATE.production_target("https://SCAN-GRADE.APP/live/exam/x") is True
    assert GATE.production_target("https://staging.scan-grade.app") is False, (
        "a subdomain is a different host, and refusing it here would refuse the "
        "staging box someone sensibly named")


def test_the_deployed_site_is_the_one_that_is_refused():
    """A list of hosts that does not include the live one protects nothing.

    Measured on the repository: `README.md` deploys to `scangrade.web.id`, and
    `lmsshb.scangrade.web.id` answers on the same box (both names hold a
    certificate there). The guard held `scan-grade.app` — the demo *email*
    domain in `manage.py` — so `--url https://scangrade.web.id` drove production.
    """
    for live in ("https://scangrade.web.id", "https://www.scangrade.web.id",
                 "https://lmsshb.scangrade.web.id",
                 "https://staging.scangrade.web.id"):
        assert GATE.production_target(live) is True, live
    assert GATE.production_target("https://staging.example.edu") is False


# ── the token's life, and waiting it out ─────────────────────────────
#
# The expiry row is the only one whose measurement is a clock: a token the app
# handed to a page has to be refused by the SFU once its five minutes pass, and
# no amount of driver logic can stand in for the wait. These hold the three
# things that could quietly turn it into a row that never measured anything: a
# fabricated wait, a budget too short for the wait it asks for, and a row taken
# against a room that has already been ended.

import jwt as _jwt  # noqa: E402  (used only by the token-shape tests below)


def _token(exp_offset: float = 300.0, **claims) -> str:
    import time as _time

    now = _time.time()
    payload = {"sub": "p_abc", "iat": now, "exp": now + exp_offset}
    payload.update(claims)
    return _jwt.encode(payload, "secret", algorithm="HS256")


def test_a_token_s_life_is_read_from_its_own_claims():
    assert GATE.token_life(_token(300)) == pytest.approx(300, abs=1)
    assert GATE.token_life(_token(60)) == pytest.approx(60, abs=1)


def test_a_token_that_says_nbf_instead_of_iat_is_still_readable():
    """LiveKit's SDK stamps `nbf` and no `iat`.

    The first run of this row reported the issued token as carrying "no expiry
    pair" for exactly this reason, as a SKIP. The reader falls back rather than
    asking the deployment to change what it mints.
    """
    import time as _time

    now = _time.time()
    stamped = _jwt.encode({"sub": "p_abc", "nbf": now, "exp": now + 300},
                          "secret", algorithm="HS256")
    assert GATE.token_life(stamped) == pytest.approx(300, abs=1)


def test_a_token_without_an_expiry_pair_is_a_gap_not_a_guess():
    """Neither `iat` nor `nbf` means the wait cannot be reasoned about."""
    assert GATE.token_life(_token(300, iat=None)) is None
    assert GATE.token_life(_token(300, exp=None)) is None
    assert GATE.token_deadline(_token(300, exp=None)) is None


def test_the_wait_is_measured_from_the_token_not_from_a_constant():
    """The row sleeps until the token's own `exp` plus the margin."""
    source = open(GATE.__file__, encoding="utf-8").read()
    assert "target = deadline + EXPIRY_MARGIN" in source, (
        "waiting a fixed number of seconds would pass on a token minted a "
        "minute before the row started")
    assert "time.monotonic()" in source, (
        "the elapsed time must be counted on a clock an NTP step cannot move")


class _Clock:
    """Wall clock and monotonic clock that only move when the driver sleeps."""

    def __init__(self, wall=1_000_000.0):
        self.wall, self.mono, self.slept = wall, 0.0, []

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.wall += seconds
        self.mono += seconds


class _Page:
    """The pupil's page, reduced to the one command a wait needs."""

    name = "student"

    def __init__(self, failure=None):
        self.failure, self.pings = failure, 0

    async def ping(self):
        self.pings += 1
        if self.failure:
            raise self.failure


def _drive_wait(monkeypatch, page, seconds, extra=()):
    clock = _Clock()
    monkeypatch.setattr(GATE.time, "time", clock.time)
    monkeypatch.setattr(GATE.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(GATE.asyncio, "sleep", clock.sleep)
    driver = GATE.Driver("https://staging.example.edu", "exam-1", {})
    deadline = clock.wall + seconds
    asyncio_run(driver._wait_past_expiry([page, *extra], deadline))
    return clock, deadline


def asyncio_run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_the_wait_answers_the_browser_so_the_socket_survives(monkeypatch):
    """Chrome closes an idle DevTools socket after ~60s; a 310s wait must talk.

    The first run of this row lost the socket to `keepalive ping timeout` and
    reported two rows as SKIP — a measurement that did not happen, for a reason
    that had nothing to do with the token.
    """
    page = _Page()
    clock, deadline = _drive_wait(monkeypatch, page, 305)
    total = 305 + GATE.EXPIRY_MARGIN
    assert page.pings >= int(total // GATE.EXPIRY_PING_SECONDS) - 1, (
        f"only {page.pings} ping(s) in a {total:g}s wait")
    assert max(clock.slept) <= GATE.EXPIRY_PING_SECONDS, (
        "a single long sleep is the idle socket nobody answers")
    assert clock.wall >= deadline + GATE.EXPIRY_MARGIN, (
        "the attempt must happen after `exp`, not at it")


def test_a_dead_devtools_socket_is_could_not_measure_not_a_pass(monkeypatch):
    """A wait whose connection died measured nothing, and says so."""
    di = type("Closed", (Exception,), {})
    page = _Page(failure=di("sent 1011 keepalive ping timeout"))
    with pytest.raises(GATE.CannotMeasure) as caught:
        _drive_wait(monkeypatch, page, 305)
    assert "closed while waiting" in str(caught.value)
    assert "not measured" in str(caught.value)


def test_the_socket_tolerates_a_pong_that_arrives_late():
    """The library's 20 s pong deadline closed a live socket on the runner.

    The hosted run lost the second pupil 46 s into the wait with `1011 keepalive
    ping timeout` — three Chrome instances doing software video on two cores, not
    a browser that had gone (a process that has gone gives 1006). The keepalive
    stays; its patience is what changed: a peer that never answers is still
    closed, two minutes later.
    """
    options = GATE.DEVTOOLS_SOCKET
    assert options["ping_timeout"] >= 120, (
        "a 20 s pong deadline is what refused the hosted reading")
    assert 0 < options["ping_interval"] < options["ping_timeout"], (
        "the interval must be the shorter of the two, as the library reads them")
    source = open(GATE.__file__, encoding="utf-8").read()
    opened = source[source.index("async def open("):source.index("async def _grant_media")]
    assert "websockets.connect(target, **DEVTOOLS_SOCKET)" in opened, (
        "the socket is opened with one set of options, not a second copy")


def test_the_wait_s_liveness_call_is_bounded_and_names_the_browser(monkeypatch):
    """`send` awaits a reply with no deadline, so the wait must bring its own.

    And when the call does fail, the message has to separate a starved renderer
    from a browser that had gone: only one of those is a finding about the
    deployment, and the exception alone reads the same for both.
    """
    assert GATE.PING_ANSWER_SECONDS > GATE.DEVTOOLS_SOCKET["ping_timeout"]
    source = open(GATE.__file__, encoding="utf-8").read()
    wait = source[source.index("async def _wait_past_expiry"):
                  source.index("async def _expiry_row")]
    assert "asyncio.wait_for(page.ping(), PING_ANSWER_SECONDS)" in wait, (
        "an unbounded liveness call ends the run on its whole budget")

    closed = type("Closed", (Exception,), {})
    with pytest.raises(GATE.CannotMeasure) as caught:
        _drive_wait(monkeypatch, _Page(failure=closed("1011 keepalive")), 305)
    assert "had exited" in str(caught.value), (
        "a page with no process to ask is named as gone")

    class _Proc:
        def poll(self):
            return None

    busy = _Page(failure=closed("1011 keepalive"))
    busy.proc = _Proc()
    with pytest.raises(GATE.CannotMeasure) as caught:
        _drive_wait(monkeypatch, busy, 305)
    assert "still running" in str(caught.value), (
        "a starved renderer and a crashed one are not the same finding")


def test_the_expiry_row_never_turns_a_failed_wait_into_a_row(monkeypatch):
    """The row propagates the gap; `execute()` turns it into exit 2."""
    driver = GATE.Driver("https://staging.example.edu", "exam-1", {})
    driver.browsers["student"] = _Page()
    # The control page too: the row checks for it before it waits, and a row that
    # stopped there would be testing the order rather than the failure.
    control = _Page()
    control.name = "student2"
    driver.browsers["student2"] = control
    driver._token = lambda role: {"token": _token(300), "url": "wss://sfu.example"}

    async def boom(*_a, **_k):
        raise GATE.CannotMeasure("the DevTools connection closed while waiting")

    monkeypatch.setattr(driver, "_wait_past_expiry", boom)
    with pytest.raises(GATE.CannotMeasure):
        asyncio_run(driver._expiry_row())


def test_an_ended_session_issues_no_token_which_is_the_app_half(monkeypatch):
    """The app's own refusal is a separate reading from the SFU's.

    `room-ended` already takes it — after *Akhiri Live* a new `POST /token` must
    answer 403 — and the expiry row must not stand in for it: a token that dies of
    old age says nothing about a session that was ended early.
    """
    source = open(GATE.__file__, encoding="utf-8").read()
    ended = source[source.index("async def _end_row"):source.index("async def execute")]
    assert "a new join token was still issued" in ended, (
        "the ended session is judged by the next token, not by the page's banner")
    assert "code != 403" in ended, "the ended session must be judged by the next token"
    assert "token-expiry" not in ended, (
        "the two readings must not be merged into one row")


def test_the_margin_clears_the_sfu_tolerance_it_was_measured_against():
    """A margin inside the server's leeway asks a question the server is
    still entitled to answer "yes" to.

    Forged tokens put the boundary between 40 s and 60 s past `exp`: accepted
    at -40 s, refused at -60 s. A margin of 10 s reported a "FAIL" on a token
    the SFU was allowed to accept, which is a defect in the row.
    """
    assert GATE.EXPIRY_MARGIN > 60.0, (
        "<= 60 s sits inside the measured tolerance window (40-60 s)")
    assert GATE.EXPIRY_MARGIN <= 120.0, (
        "a margin far beyond the tolerance just makes the run slower")


def test_the_run_budget_carries_the_wait_the_row_asks_for():
    """A budget sized without the TTL turns a measured refusal into a timeout."""
    assert GATE.TOTAL_TIMEOUT >= 300.0 + GATE.TOKEN_TTL_SECONDS, (
        "the expiry row waits a real token out; the budget has to include it")


def test_the_expiry_row_is_taken_while_the_room_is_still_open():
    """After `room-ended` the SFU would refuse the token for the wrong reason."""
    text = open(GATE.__file__, encoding="utf-8").read()
    expiry = text.index("await self._expiry_row()")
    end = text.index("await self._end_row(teacher)")
    assert expiry < end
    assert GATE.ROW_ORDER.index("token-expiry") < GATE.ROW_ORDER.index("room-ended")


def test_the_attempt_uses_the_sdk_shape_the_page_itself_uses():
    """`new lib.Room(...)` then `room.connect(url, token)` — the page's own calls.

    The first version called `LivekitClient.connect`, which does not exist on this
    vendored UMD build; the `TypeError` was then read as a refusal and the row
    reported PASS on a call that never reached the SFU.
    """
    source = GATE.CONNECT_WITH
    page = (ROOT / "app" / "static" / "js" / "scan-live-exam.js").read_text(
        encoding="utf-8")
    assert "new lib.Room(" in page and "room.connect(grant.url, grant.token)" in page
    assert "new lib.Room(" in source and "room.connect(" in source, (
        "the driver must speak the API the page speaks")
    assert "typeof lib.Room !== 'function'" in source, (
        "a page without the SDK has to be a gap the row names, not an error it scores")
    assert "catch" in source and "why" in source, (
        "a rejected promise would reach the driver as an error, not as a finding")
    rendered = source % ('"wss://sfu.example"', '"tok"')
    assert '"wss://sfu.example"' in rendered and "'tok'" not in rendered


def test_a_rejection_only_counts_once_a_fresh_token_has_connected():
    """The control is the difference between evidence and an accident.

    Without it, a dead network, a closed SFU or a wrong SDK call all look exactly
    like an expired token being refused — which is how the row passed on a
    `TypeError` before this.
    """
    body = open(GATE.__file__, encoding="utf-8").read()
    row_body = body[body.index("async def _expiry_row"):body.index("async def _end_row")]
    control = row_body.index("control = await control_page.evaluate")
    attempt = row_body.index("attempt = await pupil.evaluate")
    assert control < attempt, "the fresh token must be tried first, or the attempt means nothing"
    assert 'self._token("student2")' in row_body
    # A control that fails is a gap, and the row says so instead of scoring it.
    failed_control = row_body[row_body.index("if not control.get(\"connected\")"):]
    assert '"skipped"' in failed_control.split("self.step")[0]
    assert "proves nothing" in failed_control


def test_the_expiry_row_is_counted_and_named_like_the_others():
    assert len(GATE.ROW_ORDER) == 13, (
        "a new row is a deliberate change to the sheet, not a side effect")
    assert "token-expiry" in GATE.ROW_ORDER


def test_the_payload_carries_the_sfu_host_and_never_the_token():
    assert GATE.url_host("wss://sfu.example/rtc?access_token=abc") == "wss://sfu.example"
    assert GATE.url_host("wss://user:pw@sfu.example") == "wss://sfu.example"
    assert GATE.url_host("") == ""


# ── the artefact the runbook promises ────────────────────────────────
#
# The runbook tells a reader that `--signoff` prints "the Markdown sheet this
# section ends with", and a sheet nobody rendered is a paragraph again: the row
# names, the table's shape and the closing sentence are all things the driver can
# change without the page noticing. These read the block out of the page and hold
# it to the tool.

RUNBOOK = ROOT / "docs" / "SCANMEET_BIZNET_STAGING.md"


def _runbook_sheet() -> str:
    """The fenced ``markdown`` block whose heading is the sign-off sheet."""
    text = RUNBOOK.read_text(encoding="utf-8")
    match = re.search(r"```markdown\n(## ScanMeet media sign-off.*?)\n```", text, re.S)
    assert match, ("docs/SCANMEET_BIZNET_STAGING.md no longer carries the example "
                   "sign-off sheet §2 promises")
    return match.group(1)


def _sheet_rows(sheet: str) -> list[tuple[str, str, str]]:
    """`(number, row name, state)` for each table line, in the page's order."""
    rows = []
    for line in sheet.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0].isdigit():
            rows.append((cells[0], cells[1].strip("`"), cells[2]))
    return rows


def test_the_runbook_shows_the_sheet_the_driver_prints():
    """A documented artefact has to be the one the tool produces."""
    sheet = _runbook_sheet()
    assert sheet.splitlines()[0] == (
        "## ScanMeet media sign-off - https://staging.your-school-domain.com")
    whole = len(GATE.ROW_ORDER)
    assert f"- **Verdict:** `SIGNED OFF` (exit 0) - {whole}/{whole} row(s) passed" in sheet
    assert "| # | Row | State | What this run observed |" in sheet

    rows = _sheet_rows(sheet)
    assert [name for _, name, _ in rows] == list(GATE.ROW_ORDER), (
        "the page's rows and the driver's rows disagree")
    assert [number for number, _, _ in rows] == [str(i) for i in range(1, whole + 1)]
    assert {state for _, _, state in rows} == {"PASS"}, (
        "the example is the sheet of a complete run, so no row may be SKIP or FAIL")
    assert all(detail for detail in (
        line.split("|")[4].strip() for line in sheet.splitlines()
        if line.startswith("| ") and line.split("|")[1].strip().isdigit())), (
        "a row with an empty evidence cell reads as a row nobody took")

    # The closing sentence is the renderer's own, not a paraphrase of it.
    rendered = GATE.signoff_sheet(
        "https://staging.your-school-domain.com", "exam",
        [GATE.Row(name, "pass", "detail") for name in GATE.ROW_ORDER], GATE.EXIT_OK)
    assert sheet.splitlines()[-1] == rendered.splitlines()[-1]


def test_the_sheet_says_out_loud_when_it_is_not_a_signoff():
    """The page promises an unfinished run heads its table with CANNOT MEASURE."""
    rendered = GATE.signoff_sheet(
        "https://staging.your-school-domain.com", "exam",
        [GATE.Row(name, "skipped", "the browser never joined") for name in GATE.ROW_ORDER],
        GATE.EXIT_CANNOT_MEASURE)
    assert "`CANNOT MEASURE`" in rendered
    assert "**not** a sign-off" in rendered
    assert "SIGNED OFF" not in rendered


# ── waiting for a control a page reveals on its own ──────────────────────────

def test_the_start_control_is_waited_for_before_it_is_pressed():
    """`#live-start` starts `hidden`; a press before the reveal reads as none."""
    source = GATE.START_READY
    assert "live-start" in source
    assert "hidden" in source and "getBoundingClientRect" in source
    # The driver must use it, and use it *before* the press: a wait that lives
    # beside the click but after it would still press into an empty box.
    text = open(GATE.__file__, encoding="utf-8").read()
    wait_at = text.index("teacher.wait(START_READY")
    press_at = text.index('teacher.click("#live-start")')
    assert wait_at < press_at


def test_the_teacher_grid_is_waited_for_by_identity():
    """A tile arrives on TrackSubscribed, after the pupil's first frame."""
    expression = GATE.tiles_expression(["p_aaa", "p_bbb"])
    assert "p_aaa" in expression and "p_bbb" in expression
    assert "data-identity" in expression
    assert "every" in expression, "one pupil's tile is not the other's"
    assert GATE.tiles_expression([]) .count("[]") >= 1


def test_the_teacher_grid_wait_is_for_a_decoded_frame_not_an_element():
    """An appended `<video>` with videoWidth 0 is an element, not a camera."""
    expression = GATE.tiles_expression(["p_aaa"])
    assert "videoWidth > 0" in expression, (
        "waiting for the element alone read as 'no frame of any size' on the next "
        "run — the element is appended before its first frame arrives")


# ── which commit answered, and whether the sheet is about it ──────────
#
# The thirteen rows are about the code that served the pages, and nothing in them
# says which code that was. A host dozens of commits behind serves the same
# thirteen pages and passes the same thirteen rows. Measured on this repository:
# `.github/workflows/scanmeet-staging-media.yml` ran on `0967b040` while the
# staging host answered `/health` with `77abe6f`, and the sheet it produced said
# `SIGNED OFF` — a sign-off for a release that host was not running.


def _reading(commit="77abe6f", *, state=None, why="the app named it"):
    """One `/health` answer, in the reader's own shape."""
    return GATE.served_commit_gate.Reading(
        state or GATE.served_commit_gate.READING_OK, commit, why)


def _serving(monkeypatch, reading):
    """Put a known answer in front of the driver.

    The suite is offline on purpose: without this a `main()` test resolves a real
    hostname, and a test that reaches the network is a test that fails on a box
    with no DNS. The reader itself is measured against the harness instead.
    """
    monkeypatch.setattr(GATE, "served_commit", lambda *args, **kwargs: reading)
    return reading


def test_the_reading_is_the_deploy_gate_s_own_reader(harness):
    """One reader of `/health`, measured against a real HTTP answer.

    The path, the four states and the parse all belong to
    `deploy/served_commit_gate.py` — the module the production deploy gates on
    after every reload. A second parse in the media driver would be a second
    opinion about what the app is serving, and two opinions is how one of them
    goes stale.
    """
    reading = GATE.served_commit(harness)
    assert reading.state == GATE.served_commit_gate.READING_OK
    assert reading.commit == "77abe6fac0ac227eda219bfdf8596a59d86deaf9"
    assert GATE.served_commit_gate.DEFAULT_HEALTH_PATH == "/health"


def test_a_host_that_cannot_be_asked_is_not_a_pass(harness):
    """A page that is not a JSON object is a reading nobody took."""
    reading = GATE.served_commit(harness + "/nowhere")
    assert reading.state == GATE.served_commit_gate.READING_UNREADABLE
    assert reading.commit is None


def test_a_host_on_another_commit_is_refused_before_a_room_is_opened(
        monkeypatch, capsys):
    """A green sheet for code the host is not running is worse than no sheet."""
    drove = []
    _serving(monkeypatch, _reading("77abe6f"))
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: drove.append(kwargs) or (0, _twelve(), {}))
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--expect-commit",
                      "0967b04022f67040116fe2437fde321b9ef1834d"])
    out = capsys.readouterr().out
    assert code == GATE.EXIT_CANNOT_MEASURE
    assert drove == [], (
        "the rows would have been about the code that answered, not the commit "
        "this run is evidence for")
    assert "0967b040" in out and "77abe6f" in out, (
        "the refusal has to name both commits, or nobody can act on it")
    assert "--expect-commit" in out


def test_a_host_that_could_not_say_is_not_a_signoff(monkeypatch, capsys):
    """Silence is not agreement: an unreadable `/health` cannot verify anything."""
    drove = []
    _serving(monkeypatch, _reading(
        None, state=GATE.served_commit_gate.READING_UNREADABLE,
        why="the host did not answer /health: ConnectionError"))
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: drove.append(kwargs) or (0, _twelve(), {}))
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--expect-commit", "0967b040"])
    out = capsys.readouterr().out
    assert code == GATE.EXIT_CANNOT_MEASURE
    assert drove == []
    assert "ConnectionError" in out


def test_the_run_says_what_the_host_served(monkeypatch, capsys):
    """Read for every run, not only when a commit was demanded."""
    asked = []

    def reader(url, **kwargs):
        asked.append(url)
        return _reading("77abe6f")

    monkeypatch.setattr(GATE, "served_commit", reader)
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: (GATE.EXIT_OK, _twelve(), {"rows": []}))
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw"])
    out = capsys.readouterr().out
    assert code == GATE.EXIT_OK
    assert asked == ["https://staging.example.edu"], (
        "the reader asks the host this run drives, not some other one")
    assert "77abe6f" in out, "a console run has to say which commit answered it"


def test_the_sheet_names_the_commit_that_served_the_rows(monkeypatch, capsys,
                                                         tmp_path):
    full = "77abe6fac0ac227eda219bfdf8596a59d86deaf9"
    _serving(monkeypatch, _reading(full))
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: (GATE.EXIT_OK, _twelve(), {"rows": []}))
    out_file = tmp_path / "reading.json"
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--signoff", "--json-out", str(out_file)])
    printed = capsys.readouterr().out
    assert code == GATE.EXIT_OK
    assert "Served commit:" in printed and full in printed, (
        "a pasted sheet is filed as evidence about a commit; it has to name it")
    kept = json.loads(out_file.read_text(encoding="utf-8"))["served_commit"]
    assert kept["commit"] == full and kept["expected"] is None


def test_a_sheet_that_could_not_read_the_commit_says_so(monkeypatch, capsys,
                                                        tmp_path):
    """A blank beside `13/13 passed` reads as a match, so it is never blank."""
    _serving(monkeypatch, _reading(
        None, state=GATE.served_commit_gate.READING_ABSENT,
        why="the answer carried no commit block (a release from before the "
            "reading)"))
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: (GATE.EXIT_OK, _twelve(), {"rows": []}))
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--signoff", "--json-out", str(tmp_path / "r.json")])
    printed = capsys.readouterr().out
    assert code == GATE.EXIT_OK
    assert "could not be read" in printed


def test_either_spelling_of_a_commit_answers_the_same_question():
    """`$GITHUB_SHA` is forty characters and `/health` publishes both forms."""
    full = "77abe6fac0ac227eda219bfdf8596a59d86deaf9"
    assert GATE.commit_matches(_reading(full), "77abe6f")
    assert GATE.commit_matches(_reading("77abe6f"), full)
    assert not GATE.commit_matches(_reading("77abe6f"), "0967b040")
    assert not GATE.commit_matches(_reading(full), ""), (
        "an empty expectation is not a match for everything")
    assert not GATE.commit_matches(_reading("77ab"), "77ab"), (
        "four characters identify nothing, so it is not a match")
    assert not GATE.commit_matches(
        _reading(None, state=GATE.served_commit_gate.READING_UNREADABLE,
                 why="nothing answered"), "77abe6f")


def test_a_runner_is_evidence_about_the_commit_it_ran_on():
    """A dispatched run is about the commit the caller dispatched it on.

    This is the case the workflow was wrong about: it ran on `0967b040` while the
    host answered `77abe6f`, and nothing asked the question. On a runner the
    question has an answer the caller already gave, so it is asked by default.
    """
    full = "0967b04022f67040116fe2437fde321b9ef1834d"
    wanted, why = GATE.expected_commit(None, {"GITHUB_ACTIONS": "true",
                                             "GITHUB_SHA": full})
    assert wanted == full
    assert "GITHUB_SHA" in why


def test_a_flag_beats_the_environment_and_an_empty_one_records():
    """Empty is not absent: it is a person saying `record, do not judge`."""
    environ = {"SCANMEET_EXPECT_COMMIT": "aaaaaaaa", "GITHUB_ACTIONS": "true",
               "GITHUB_SHA": "bbbbbbbb"}
    assert GATE.expected_commit("cccccccc", environ)[0] == "cccccccc"
    assert GATE.expected_commit("", environ)[0] == ""
    assert GATE.expected_commit(None, environ)[0] == "aaaaaaaa", (
        "the environment decides before the runner's own variables do")


def test_without_a_runner_or_a_value_nothing_is_demanded():
    """A workstation run against a shared box may only record what answered."""
    assert GATE.expected_commit(None, {}) == ("", "")
    assert GATE.expected_commit(None, {"GITHUB_ACTIONS": "true"})[0] == "", (
        "a runner with no sha cannot name a commit, so it demands nothing")


def test_an_empty_expectation_records_the_host_without_refusing(
        monkeypatch, capsys, tmp_path):
    """The escape hatch is deliberate, and it still prints what served."""
    _serving(monkeypatch, _reading("77abe6f"))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "0967b04022f67040116fe2437fde321b9ef1834d")
    monkeypatch.setattr(GATE.touch_gate, "locate_browser", lambda: "/bin/chrome")
    monkeypatch.setattr(
        GATE, "run", lambda **kwargs: (GATE.EXIT_OK, _twelve(), {"rows": []}))
    code = GATE.main(["--url", "https://staging.example.edu", "--exam", "exam-1",
                      "--teacher", "guru@x:pw", "--student", "murid@x:pw",
                      "--expect-commit", "", "--signoff", "--json-out",
                      str(tmp_path / "r.json")])
    printed = capsys.readouterr().out
    assert code == GATE.EXIT_OK, "an explicit empty value must not refuse"
    assert "77abe6f" in printed


def test_a_printed_host_carries_no_credentials():
    """The report is pasted into tickets; a URL may carry a password."""
    assert GATE.host_of("https://guru:pw@staging.example.edu/live?e=1#x") == \
        "https://staging.example.edu/live"
    assert GATE.host_of("http://127.0.0.1:5055/") == "http://127.0.0.1:5055"
