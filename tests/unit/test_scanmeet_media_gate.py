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
    assert [item.name for item in skipped] == [
        "pupil-camera", "teacher-receives-camera", "pupil-isolation",
        "mic-starts-closed", "hand-raise", "mic-approved",
        "audio-reaches-teacher", "mic-closed", "outsider-refused", "room-ended"]
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


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the handler's own name
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
