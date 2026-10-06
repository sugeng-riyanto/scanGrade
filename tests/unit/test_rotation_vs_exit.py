"""Turning a tablet is not leaving the exam — and leaving is still leaving.

Reported from a tablet lab: a pupil who rotated the screen was walked down the
same ladder as a pupil who left the exam. The mechanism is not subtle once it is
seen. Several tablet browsers drop the fullscreen state as a *side effect* of
rotating, and nothing in the detection knew an orientation change had just
happened — so `checkFullscreen()` saw "not fullscreen" and charged
`fullscreen_exit` for a turn. A second detector could bill the same turn again.

The repair has one job that is really two, and this file exists because they pull
in opposite directions:

* **a rotation must not cost anything**, and must not cost it twice; and
* **a pupil who genuinely leaves must still be charged**, including one who
  leaves *during* a rotation, because a grace that also covers the act is not a
  grace — it is the bypass the ladder exists to prevent.

So four properties are asserted here rather than described, and the first two are
the ones a later edit is most likely to break:

1. **The overlay still goes up inside the window.** The grace forgives a penalty,
   never a question: the paper is blocked the instant the absence is seen, exactly
   as it is outside the window. If the rotation branch ever moved above
   `fullscreenBlocked = true`, a pupil could rock the tablet to keep a windowed,
   readable paper for the length of a sitting.
2. **The window is bounded, and its end decides.** A fullscreen loss that outlives
   the transition is charged on every platform that keeps fullscreen across a
   rotation. Only the platform whose fullscreen a turn *drops* — and which cannot
   re-enter without a fresh gesture — is exempt, and then the row is recorded
   rather than charged, never dropped.
3. **The rotation row is not on the ladder.** It goes to the same endpoint but
   through none of `handleViolation`: no local count, no banner, no auto-submit.
   The server decides what counts, and `orientation_shift` is outside
   `PENALIZED_VIOLATION_TYPES`.
4. **The numbers come from the service.** The window is `ROTATION_GRACE_SECONDS`
   handed over by the route, not a literal in the page — the same rule the away
   grace follows, and for the same reason.

The client methods are sliced out of the template and *run* under node against a
fake clock and a fake browser, so what is measured is the shipped code rather than
a description of it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.anti_cheat_service import (
    ORIENTATION_SHIFT, PENALIZED_VIOLATION_TYPES, ROTATION_GRACE_SECONDS, _as_event,
)
from app.services.attempt_events import CLIENT_EVENT_KINDS

ROOT = Path(__file__).resolve().parents[2]
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
SERVICE = ROOT / "app" / "services" / "anti_cheat_service.py"
STUDENT_ROUTE = ROOT / "app" / "routes" / "student.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the client")


def source(path):
    return Path(path).read_text(encoding="utf-8")


def slice_between(text, start, end):
    """The template's own code between two anchors — never a rewrite of it.

    A missing anchor raises ValueError, so an edit that moves or renames the block
    fails this suite loudly instead of leaving a harness that runs nothing.
    """
    i = text.index(start)
    j = text.index(end, i)
    assert j > i
    return text[i:j]


# ── the vocabulary, in one place ─────────────────────────────────────────────

class TestTheVocabularyLivesInTheService:
    def test_the_window_is_the_service_number_and_not_a_literal(self):
        """The page counts with the constant the route hands over. A literal here
        is how the page and the service come to disagree about the same window."""
        page = source(EXAM_PAGE)
        assert "rotationGraceMs: {{ rotation_grace_seconds }} * 1000," in page
        assert not re.search(r"rotationGraceMs:\s*\d", page), (
            "the rotation window is typed into the exam page instead of coming "
            "from the service")

    def test_the_route_hands_it_over(self):
        route = source(STUDENT_ROUTE)
        assert "ROTATION_GRACE_SECONDS" in route
        assert "rotation_grace_seconds=ROTATION_GRACE_SECONDS" in route

    def test_a_rotation_is_recorded_and_never_charged(self):
        kind = ORIENTATION_SHIFT
        assert kind not in PENALIZED_VIOLATION_TYPES, (
            "a rotation is on the ladder, so it can take a pupil's points")
        event = _as_event({
            "user_id": "s", "violation_type": kind,
            "created_at": "2026-10-06T02:00:00Z",
            "metadata": {"trigger": "rotation_while_out_of_fullscreen"},
        })
        assert event["charged"] is False
        # And it is still shown: an event the report silently drops is how a log
        # stops being evidence. The label is a bilingual pair, not the raw key.
        assert event["kind"] == kind
        label = event["label"]
        assert isinstance(label, tuple) and len(label) == 2
        assert all(part and part != kind for part in label), label

    def test_the_page_sends_the_kind_the_service_names(self):
        """Two files spell this string; the pin is what keeps them one string."""
        page = source(EXAM_PAGE)
        assert f"'{ORIENTATION_SHIFT}'" in page, (
            "the page records a kind the service does not define")

    def test_the_timeline_accepts_it(self):
        assert ORIENTATION_SHIFT in CLIENT_EVENT_KINDS, (
            "the paper's own timeline would reject the rotation it reported")

    def test_the_window_is_short_enough_not_to_be_a_free_window(self):
        """It covers a device's rotation animation and the browser's own
        `fullscreenchange`; anything longer is a window a pupil could keep a
        windowed paper readable through by rocking the tablet."""
        assert 0 < ROTATION_GRACE_SECONDS <= 2.0, ROTATION_GRACE_SECONDS


# ── the shape of the detection ───────────────────────────────────────────────

class TestTheDetectionTellsTheTwoApart:
    def test_both_orientation_signals_are_watched(self):
        body = slice_between(source(EXAM_PAGE), "watchOrientation() {",
                             "async resumeFullscreen() {")
        assert "addEventListener('orientationchange'" in body, (
            "a tablet's rotation fires this one and nothing else")
        assert "matchMedia('(orientation: portrait)')" in body, (
            "a desktop window dragged across the portrait line fires only the "
            "media query, so the media query has to be watched too")

    def test_the_watch_is_installed_when_the_ladder_is_armed(self):
        body = slice_between(source(EXAM_PAGE), "setupAntiCheat() {",
                             "        },\n        // What a reload has to measure from")
        assert "this.watchOrientation();" in body

    def test_the_overlay_still_comes_first(self):
        """The property that keeps the grace from being a free window: the paper
        is blocked before anything is forgiven."""
        check = slice_between(source(EXAM_PAGE), "checkFullscreen() {",
                              "watchOrientation() {")
        blocked = check.index("this.fullscreenBlocked = true;")
        forgiven = check.index("if (this._isRotationWindow()) {")
        assert blocked < forgiven, (
            "a fullscreen absence during a rotation is forgiven before the paper "
            "is blocked — that is a window to read the exam through")
        assert "startAwayGrace" not in check, (
            "the fullscreen path started a countdown; this absence is billed by "
            "the ladder, not by the second chance")

    def test_leaving_fullscreen_outside_the_window_is_still_charged(self):
        check = slice_between(source(EXAM_PAGE), "checkFullscreen() {",
                              "watchOrientation() {")
        assert "this.handleViolation('fullscreen_exit');" in check

    def test_the_rotation_is_recorded_once_per_absence(self):
        check = slice_between(source(EXAM_PAGE), "checkFullscreen() {",
                              "watchOrientation() {")
        assert "if (!this._rotationForgiven) {" in check, (
            "the rotation row is written on every sample of the window, so a "
            "single turn fills the teacher's log")
        assert "recordNotCharged('orientation_shift'" in check

    def test_the_end_of_the_window_decides_on_the_platform(self):
        body = slice_between(source(EXAM_PAGE), "_endRotationWindow() {",
                             "recordNotCharged(kind, trigger) {")
        assert "if (this._fsElement()) return;" in body, (
            "a fullscreen that came back by itself would still be charged")
        assert "this._platformLosesFullscreenOnRotation()" in body
        assert "this.handleViolation('fullscreen_exit', 'rotation');" in body, (
            "an absence that outlived the transition is not charged at all")
        # The exemption is the platform's, and it is the strict default: a browser
        # that hides its identity must not win the exemption by hiding it.
        assert "catch (e) { return false; }" in body

    def test_the_rotation_row_never_touches_the_ladder(self):
        """No local count, no banner, no auto-submit — those *are* the ladder, and
        the server is what decides whether a kind counts."""
        body = slice_between(source(EXAM_PAGE), "recordNotCharged(kind, trigger) {",
                             "_applyViolationBanner(n, kind = 'tab_switch') {")
        for forbidden in ("violationCount++", "_applyViolationBanner(",
                          "_maybeAutoSubmit(", "this.violationCount ="):
            assert forbidden not in body, (
                f"recordNotCharged reaches into the ladder through {forbidden}")

    def test_the_re_enter_attempt_asks_for_fullscreen_without_a_gesture(self):
        body = slice_between(source(EXAM_PAGE), "_tryAutoRefullscreen() {",
                             "async resumeFullscreen() {")
        assert "this.requestExamFullscreen()" in body
        assert "if (!this._fsSupported() || this._fsElement()) return;" in body


# ── the debounce cannot be shielded by an uncharged row ──────────────────────

class TestTheDebounceOnlyCountsChargedRows:
    def test_the_window_filters_to_the_penalized_kinds(self):
        """The debounce exists so one act is not billed twice. An informational
        row is not an act, and counting it would drop the next real exit."""
        body = slice_between(source(SERVICE), "def validate_violation_log(",
                             "    if recent.data:")
        assert '.in_("violation_type", list(PENALIZED_VIOLATION_TYPES))' in body


# ── the client, executed ─────────────────────────────────────────────────────

HARNESS = r"""
const fs = require('fs');
const M = eval('({' + fs.readFileSync(process.argv[2], 'utf8') + '})');

let now = 1000000;
Date.now = () => now;

// Timers are captured, not scheduled: the window is closed by hand so a
// second-and-a-half window is not a second and a half of test.
const timers = [];
globalThis.setTimeout = (fn) => { timers.push(fn); return timers.length; };
globalThis.clearTimeout = () => {};
const runTimers = () => { const t = timers.splice(0); t.forEach((fn) => fn()); };

globalThis.document = { hidden: false, hasFocus: () => true };
globalThis.window = globalThis;

const rows = [];
globalThis.fetch = (url, opts) => {
  rows.push(JSON.parse(opts.body).logs[0]);
  return Promise.resolve({ ok: true, json: async () => ({ violations: [] }) });
};

const events = [];
const charges = [];
// Closes of the magnifier. `closePageZoom` is defined outside this slice, so it is
// stood in for below; counting it is what lets a guard assert the blocker never
// leaves the paper readable above it.
const zoomCloses = [];

function setNavigator(ua, platform, touch) {
  Object.defineProperty(globalThis, 'navigator', {
    value: { userAgent: ua, platform: platform, maxTouchPoints: touch },
    configurable: true, writable: true,
  });
}
const DESKTOP = () => setNavigator('Mozilla/5.0 (X11; Linux x86_64)', 'Linux x86_64', 0);
const IPAD = () => setNavigator(
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15',
  'MacIntel', 5);

const FRESH = {
  submitted: false, _antiCheatArmed: true,
  antiCheat: { enabled: true, fullscreen_required: true, penalty_per_violation: 5,
               max_violations: 5, auto_submit_on_max: true },
  _fsWatch: null, _fsWasAbsent: false, _sawFullscreen: true, _fsArmedAt: 0,
  _fsGraceMs: 4000, fullscreenBlocked: false, fullscreenRetryFailed: false,
  _hiddenAbsenceCharged: false, _isFocusTab: true,
  rotationGraceMs: 1500, _rotationGraceUntil: 0, _rotationTimer: null,
  _rotationForgiven: false, _rotationWatch: null,
  violationCount: 0, totalPenalty: 0,
  _inFullscreen: false, _refullscreen: 0,
  _fsElement: function () { return this._inFullscreen ? {} : null; },
  _fsSupported: function () { return true; },
  requestExamFullscreen: function () { this._refullscreen++; return Promise.resolve(false); },
  _csrfToken: function () { return 'csrf'; },
  _ev: function (kind) { events.push(kind); },
  handleViolation: function (vtype, trigger) { charges.push({ vtype: vtype, trigger: trigger }); },
  _applyViolationBanner: function () {}, _maybeAutoSubmit: function () {},
  closePageZoom: function () { zoomCloses.push(true); },
};

// The real methods last, so the shipped code wins over the stand-ins above; the
// stand-ins only cover what the slice does not contain (the browser, the clock,
// the wire).
function fresh(over) { return Object.assign({}, FRESH, M, over || {}); }

let zoomClosedWhenBlocked = 0, zoomClosedOnPlainExit = 0;

(async () => {
  const out = {};
  out.extracted = {
    check: typeof M.checkFullscreen, note: typeof M._noteOrientationShift,
    window: typeof M._isRotationWindow, end: typeof M._endRotationWindow,
    platform: typeof M._platformLosesFullscreenOnRotation,
    record: typeof M.recordNotCharged, auto: typeof M._tryAutoRefullscreen,
  };

  // 1. A rotation alone: the window opens, the turn is recorded for the timeline,
  //    and the page asks for fullscreen back without a gesture.
  DESKTOP();
  let a = fresh();
  rows.length = 0; events.length = 0; charges.length = 0;
  a._noteOrientationShift();
  out.onShift = { window: a._isRotationWindow(), events: events.slice(),
                  refullscreen: a._refullscreen };

  // 2. The browser took fullscreen away. Inside the window this is the
  //    transition's, so nothing is charged — BUT the paper is blocked at once.
  a._inFullscreen = false;
  const zoomBeforeBlock = zoomCloses.length;
  a.checkFullscreen();
  zoomClosedWhenBlocked = zoomCloses.length - zoomBeforeBlock;
  out.duringWindow = { charged: charges.length, blocked: a.fullscreenBlocked,
                       leftFullscreenRows: rows.length,
                       kinds: rows.map((r) => r.violation_type),
                       chargedFlag: rows.length ? rows[0].metadata.charged : null };

  // 3. A second sample inside the same window must not write a second row.
  a.checkFullscreen();
  out.sampledAgain = { rows: rows.length };

  // 4. The window closes with fullscreen back: nothing at all happened. The
  //    browser fires `fullscreenchange` when it regains the state, and that is
  //    what lifts the overlay — `checkFullscreen` owns that flag and only it, so
  //    the harness models the event rather than reaching into the flag.
  a._inFullscreen = true;
  a.checkFullscreen();
  now += a.rotationGraceMs + 200;
  runTimers();
  out.cameBack = { charged: charges.length, blocked: a.fullscreenBlocked };

  // 5. The window closes and fullscreen did not come back, on a platform that
  //    keeps fullscreen across a rotation: the absence outlived its explanation.
  DESKTOP();
  let b = fresh();
  rows.length = 0; charges.length = 0;
  b._noteOrientationShift();
  b._inFullscreen = false;
  b.checkFullscreen();
  now += b.rotationGraceMs + 200;
  runTimers();
  out.stayedOutDesktop = { charged: charges.length, kinds: charges.map((c) => c.vtype),
                           triggers: charges.map((c) => c.trigger) };

  // 6. The same sequence on the platform whose fullscreen a turn drops and which
  //    cannot re-enter without a gesture: recorded, never charged.
  IPAD();
  let c = fresh();
  rows.length = 0; charges.length = 0;
  c._noteOrientationShift();
  c._inFullscreen = false;
  c.checkFullscreen();
  now += c.rotationGraceMs + 200;
  runTimers();
  out.stayedOutIpad = { charged: charges.length, rows: rows.length,
                        blocked: c.fullscreenBlocked };

  // 7. A rotation must never move the pupil's own counter, whatever else it does.
  DESKTOP();
  let d = fresh();
  rows.length = 0; charges.length = 0;
  d._noteOrientationShift();
  d._inFullscreen = false;
  d.checkFullscreen();
  out.pupilCounter = { afterRotation: d.violationCount };

  // 8. A genuine exit that begins outside any window is still billed on sight —
  //    the property the whole ladder rests on.
  let e = fresh();
  charges.length = 0;
  const zoomBeforeExit = zoomCloses.length;
  e._inFullscreen = false;
  e.checkFullscreen();
  zoomClosedOnPlainExit = zoomCloses.length - zoomBeforeExit;
  out.plainExit = { charged: charges.length, kinds: charges.map((c) => c.vtype) };

  // 9. The same absence is not billed twice by the sampler.
  e.checkFullscreen();
  out.plainExitAgain = { charged: charges.length };

  // 10. A rotation while the pupil is *already* out of fullscreen and billed must
  //     not hand the absence back.
  let f = fresh();
  charges.length = 0; rows.length = 0;
  f._inFullscreen = false;
  f.checkFullscreen();
  const billedBefore = f.violationCount;
  f._noteOrientationShift();
  f.checkFullscreen();
  out.rotationAfterCharge = { charged: charges.length, rows: rows.length,
                              counter: f.violationCount, was: billedBefore };

  out.magnifier = { closedWhenBlocked: zoomClosedWhenBlocked,
                    closedOnPlainExit: zoomClosedOnPlainExit };

  console.log(JSON.stringify(out));
})();
"""


class TestTheRotationIsNotAnExit:
    """The real methods, sliced out of the template and executed."""

    @classmethod
    def run(cls, tmp_path):
        page = source(EXAM_PAGE)
        check = slice_between(page, "checkFullscreen() {", "watchOrientation() {")
        rotation = slice_between(page, "watchOrientation() {", "async resumeFullscreen() {")
        record = slice_between(page, "recordNotCharged(kind, trigger) {",
                               "_applyViolationBanner(n, kind = 'tab_switch') {")
        (tmp_path / "methods.js").write_text(check + rotation + record, encoding="utf-8")
        (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
        done = subprocess.run(
            [NODE, str(tmp_path / "harness.js"), str(tmp_path / "methods.js")],
            capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip())

    @needs_node
    def test_the_methods_under_test_are_the_shipped_ones(self, tmp_path):
        assert self.run(tmp_path)["extracted"] == {
            "check": "function", "note": "function", "window": "function",
            "end": "function", "platform": "function", "record": "function",
            "auto": "function"}

    @needs_node
    def test_a_rotation_opens_a_window_records_the_turn_and_asks_for_fullscreen(
            self, tmp_path):
        out = self.run(tmp_path)["onShift"]
        assert out["window"] is True, "the rotation opened no window at all"
        assert out["events"] == [ORIENTATION_SHIFT], (
            "the turn was not written to the paper's own timeline")
        assert out["refullscreen"] == 1, (
            "the page never asked for fullscreen back after a rotation")

    @needs_node
    def test_a_rotation_that_took_fullscreen_away_costs_nothing_but_blocks(
            self, tmp_path):
        out = self.run(tmp_path)["duringWindow"]
        assert out["charged"] == 0, "a rotation was billed as leaving the exam"
        assert out["blocked"] is True, (
            "the paper stayed readable outside fullscreen — the grace exposed a "
            "question, which is the one thing it must never do")
        assert out["leftFullscreenRows"] == 1, (
            "the turn left no trace in the teacher's record")
        assert out["kinds"] == [ORIENTATION_SHIFT]
        assert out["chargedFlag"] is False, (
            "the recorded row claims to be a charged violation")

    @needs_node
    def test_one_turn_writes_one_row_however_often_it_is_sampled(self, tmp_path):
        assert self.run(tmp_path)["sampledAgain"] == {"rows": 1}

    @needs_node
    def test_fullscreen_that_comes_back_on_its_own_is_not_an_absence(self, tmp_path):
        out = self.run(tmp_path)["cameBack"]
        assert out == {"charged": 0, "blocked": False}, out

    @needs_node
    def test_an_absence_that_outlives_the_turn_is_charged_where_it_means_an_exit(
            self, tmp_path):
        """The guard against this fix becoming a bypass: on a platform that holds
        fullscreen across a rotation, a loss that persists is the pupil leaving."""
        out = self.run(tmp_path)["stayedOutDesktop"]
        assert out["charged"] == 1, (
            "a pupil who left fullscreen and stayed out was forgiven")
        assert out["kinds"] == ["fullscreen_exit"]
        assert out["triggers"] == ["rotation"], (
            "the charge does not say it followed a rotation")

    @needs_node
    def test_the_platform_whose_turn_drops_fullscreen_is_recorded_not_charged(
            self, tmp_path):
        out = self.run(tmp_path)["stayedOutIpad"]
        assert out["charged"] == 0, (
            "an iPadOS pupil was billed for their tablet's behaviour")
        assert out["rows"] == 1, "the absent charge left no record either"
        assert out["blocked"] is True, (
            "the iPadOS exemption left the paper readable outside fullscreen")

    @needs_node
    def test_a_rotation_never_moves_the_pupils_own_counter(self, tmp_path):
        assert self.run(tmp_path)["pupilCounter"] == {"afterRotation": 0}

    @needs_node
    def test_a_genuine_exit_is_still_billed_on_sight(self, tmp_path):
        """No window, no forgiveness: the ladder is exactly what it was."""
        out = self.run(tmp_path)
        assert out["plainExit"] == {"charged": 1, "kinds": ["fullscreen_exit"]}
        assert out["plainExitAgain"] == {"charged": 1}, (
            "the sampler billed one absence twice")

    @needs_node
    def test_the_blocker_takes_the_magnifier_down_with_it(self, tmp_path):
        """The magnified page sits on the maximized-viewer layer, above the
        blocker's scrim. Leaving it up while the answers behind it are blocked is
        the one thing the blocker exists to stop — so raising the overlay closes
        the magnifier, for a forgiven rotation and a billed exit alike."""
        out = self.run(tmp_path)["magnifier"]
        assert out["closedWhenBlocked"] == 1, (
            "the overlay went up and the magnifier stayed on the paper")
        assert out["closedOnPlainExit"] == 1, (
            "a billed exit left the magnifier on the paper")

    @needs_node
    def test_a_rotation_cannot_undo_a_charge_that_already_happened(self, tmp_path):
        out = self.run(tmp_path)["rotationAfterCharge"]
        assert out["counter"] == out["was"], "the rotation refunded a charge"
        assert out["charged"] == 1, "the rotation opened a second charge"
