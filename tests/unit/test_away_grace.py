"""The second chance: an absence is a countdown before it is a penalty.

Reported from the classroom: a phone call or a notification used to walk a student
down the penalty ladder while they watched it happen, and the last rung of that
ladder submits the paper for them. Nobody chose to cheat — the exam did the
walking, silently, and the student only found out on the results page.

So an absence is no longer charged when it is seen. The paper blurs at once, the
panel counts down, and coming back inside the countdown costs nothing at all.

Four things here are load-bearing, and each is a guard rather than a promise:

1. **The blur comes first, and always.** The protection never waits for the
   countdown, so the grace can only ever forgive a penalty — it can never expose a
   question. If the blur moved behind the charge, the feature would be a free
   window to read the paper from.
2. **One absence, one charge.** Three detectors can see the same act (the
   visibility handler, the window-blur handler, the fullscreen sampler). A
   countdown already running *is* that absence, and an absence already charged
   cannot be charged again.
3. **The chances are counted.** `graceUsed` is spent on each forgiven return, and
   once they are gone the absence is charged the moment it is seen — exactly the
   behaviour before this feature. An unbounded grace would be a repeatable free
   window: look question 7 up for nine seconds, return, look up question 8.
4. **The numbers are not typed into the page.** The countdown, the terms the
   student agrees to, the guide and the tutorial all use the two constants in
   `app/services/anti_cheat_service.py`, because a page that promises a different
   number from the one it counts is how this class of defect reads.

The countdown is JavaScript inside the exam template, so it is *run* here rather
than described: the real methods are sliced out of the template and executed under
node against a fake clock and a fake browser, the way a student's browser would.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.anti_cheat_service import AWAY_GRACE_CHANCES, AWAY_GRACE_SECONDS

ROOT = Path(__file__).resolve().parents[2]
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
GUIDE_PAGE = ROOT / "app" / "templates" / "guide" / "skor.html"
TUTORIAL_PAGE = ROOT / "app" / "templates" / "tutorial_murid.html"
GRADE_PAGE = ROOT / "app" / "templates" / "teacher" / "grade_detail.html"
SERVICE = ROOT / "app" / "services" / "anti_cheat_service.py"
STUDENT_ROUTE = ROOT / "app" / "routes" / "student.py"
GUIDE_ROUTE = ROOT / "app" / "routes" / "guide.py"
APP_INIT = ROOT / "app" / "__init__.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the countdown")


def source(path):
    return Path(path).read_text(encoding="utf-8")


def _penalty_rows():
    from app.routes.guide import penalty_schedule
    return penalty_schedule()


def slice_between(text, start, end):
    """The template's own code between two anchors — never a rewrite of it.

    A missing anchor raises ValueError, so an edit that moves or renames the block
    fails this suite loudly instead of leaving a harness that runs nothing and
    passes.
    """
    i = text.index(start)
    j = text.index(end, i)
    assert j > i
    return text[i:j]


# ── the two numbers, in one place ────────────────────────────────────────────

class TestTheNumbersLiveInOnePlace:
    def test_the_service_owns_the_countdown_and_the_budget(self):
        assert AWAY_GRACE_SECONDS == 10
        assert AWAY_GRACE_CHANCES == 2

    def test_the_countdown_is_not_typed_into_the_exam_page(self):
        """The page counts with the service's constant. A literal here means the
        guide, the tutorial and the countdown can disagree."""
        page = source(EXAM_PAGE)
        assert "graceSeconds: {{ away_grace_seconds }}" in page
        assert "graceChances: {{ away_grace_chances }}" in page
        assert not re.search(r"graceSeconds:\s*\d", page), (
            "the countdown is typed into the exam page instead of coming from the service")
        assert not re.search(r"graceChances:\s*\d", page)

    def test_the_exam_route_hands_over_the_constants(self):
        route = source(STUDENT_ROUTE)
        assert "AWAY_GRACE_SECONDS" in route and "AWAY_GRACE_CHANCES" in route
        assert "away_grace_seconds=AWAY_GRACE_SECONDS" in route
        assert "away_grace_chances=AWAY_GRACE_CHANCES" in route
        assert "{{ away_grace_seconds }}" in source(EXAM_PAGE)

    def test_the_guide_and_the_tutorial_promise_the_same_numbers(self):
        for page, route in ((GUIDE_PAGE, GUIDE_ROUTE), (TUTORIAL_PAGE, APP_INIT)):
            assert "away_grace_seconds" in source(page), f"{page} never names the countdown"
            assert "away_grace_chances" in source(page), f"{page} never names the budget"
            assert "away_grace_seconds=AWAY_GRACE_SECONDS" in source(route), (
                f"{route} does not pass the constant the page prints")
            assert "away_grace_chances=AWAY_GRACE_CHANCES" in source(route)

    def test_the_guide_states_it_in_both_tabs(self):
        """The guide is read from two sides. A teacher who cannot see the countdown
        reads every short absence in the log as a deliberate one."""
        page = source(GUIDE_PAGE)
        teacher_tab, student_tab = page.split("x-show=\"tab==='student'\"", 1)
        for name, tab in (("teacher", teacher_tab), ("student", student_tab)):
            assert "data-second-chance" in tab, f"the {name} tab never states the second chance"
            assert "{{ away_grace_seconds" in tab, f"the {name} tab names no countdown"
            assert "{{ away_grace_chances" in tab, f"the {name} tab names no budget"

    def test_the_tutorial_states_it_in_the_students_own_language(self):
        """The Indonesian half is the one a student reads by default, so the
        numbers have to be in it — not only in the English half."""
        page = source(TUTORIAL_PAGE)
        assert "{{ away_grace_seconds|default(10) }} detik untuk kembali tanpa dicatat" in page
        assert "{{ away_grace_chances|default(2) }} kali per ujian" in page

    def test_the_terms_screen_states_the_rule_the_page_enforces(self):
        """The student agrees to this list. A rule discovered mid-exam is not a
        rule the student accepted."""
        page = source(EXAM_PAGE)
        agreement = slice_between(page, 'x-text="t(\'Ketentuan Ujian\'', 'x-text="t(\'Pelanggaran ke-1\'')
        assert "{{ away_grace_seconds }}" in agreement
        assert "{{ away_grace_chances }}" in agreement
        assert "tanpa dicatat" in agreement, "the terms never say a return in time is free"


# ── the order of the two acts ────────────────────────────────────────────────

class TestThePaperBlursBeforeAnythingIsCharged:
    def test_the_away_handlers_start_a_countdown_and_never_charge(self):
        page = source(EXAM_PAGE)
        handlers = slice_between(page, "document.addEventListener('visibilitychange'",
                                 "window.addEventListener('focus'")
        assert "startAwayGrace('tab_switch', 'visibilitychange', gone)" in handlers
        assert "startAwayGrace('focus_lost', 'window_blur', gone)" in handlers
        # `gone` is read when the event fires, before the debounce, so the
        # recorded duration is the whole absence rather than the tail of it.
        assert handlers.count("const gone = Date.now();") == 2
        assert "handleViolation(" not in handlers, (
            "an away handler charges on sight again — the countdown is unreachable")

    def test_the_countdown_raises_the_blur_before_any_early_return(self):
        """Everything that can skip the charge comes after the blur, so a chance
        already spent (or a fourth detector) can never leave the paper readable."""
        body = slice_between(source(EXAM_PAGE), "startAwayGrace(kind, trigger, since) {",
                             "_graceTick() {")
        blur = body.index("this.awayBlurred = true;")
        for guard in ("if (this.graceLeft > 0) return;", "if (this.awayCharged) return;"):
            assert body.index(guard) > blur, f"{guard} runs before the paper is blurred"
        assert body.index("this._chargeAway(kind, trigger);") > blur

    def test_leaving_fullscreen_is_still_charged_on_sight(self):
        """The boundary, and it is deliberate: that overlay is not silent — it
        blocks the paper and names the one press that fixes it — so it is not the
        accident the countdown exists for. It is also unreachable from a phone
        call, which arrives as a hidden document and is owned by the visibility
        handler (see `_hiddenAbsenceCharged`)."""
        check = slice_between(source(EXAM_PAGE), "checkFullscreen() {",
                              "async resumeFullscreen() {")
        assert "this.handleViolation('fullscreen_exit');" in check
        assert "startAwayGrace" not in check

    def test_a_submitted_paper_keeps_no_countdown_running(self):
        body = slice_between(source(EXAM_PAGE), "_executeSubmit() {", "this.saveToLocal();")
        assert "this._clearGraceTimer();" in body
        assert "this.graceLeft = 0;" in body

    def test_the_panel_only_counts_down_while_a_chance_is_being_spent(self):
        page = source(EXAM_PAGE)
        assert 'x-show="graceLeft > 0"' in page
        assert 'x-show="awayCharged"' in page, (
            "the panel cannot say an absence was recorded")
        assert "'Penalty-free returns left') + ': ' + _graceChancesLeft()" in page, (
            "the panel never says how many chances are left — the student cannot tell "
            "a first chance from a last one")


# ── the countdown, executed ──────────────────────────────────────────────────

HARNESS = r"""
const fs = require('fs');
const M = eval('({' + fs.readFileSync(process.argv[2], 'utf8') + '})');
const H = eval('({' + fs.readFileSync(process.argv[3], 'utf8') + '})');

let now = 1000000;
Date.now = () => now;

// The page's own timers are recorded, not scheduled: the tick is called by hand
// so a ten-second countdown is not ten seconds of test.
const ticks = [];
globalThis.setInterval = (fn) => { ticks.push(fn); return ticks.length; };
globalThis.clearInterval = () => {};
globalThis.document = { hidden: false, hasFocus: () => true };
globalThis.window = globalThis;

const posts = [];
globalThis.fetch = (url, opts) => {
  posts.push({ url: url, body: JSON.parse(opts.body) });
  return Promise.resolve({ ok: false });
};

const AWAY = () => { document.hidden = false; document.hasFocus = () => false; };
const BACK = () => { document.hidden = false; document.hasFocus = () => true; };
const settle = () => new Promise((r) => setTimeout(r, 0));

function fresh() {
  return Object.assign({
    submitted: false,
    antiCheat: { enabled: true, penalty_per_violation: 5, max_violations: 5,
                 auto_submit_on_max: true, fullscreen_required: false },
    _isFocusTab: true,
    awayBlurred: false,
    graceSeconds: 10, graceChances: 2, graceUsed: 0, graceLeft: 0,
    graceKind: null, graceTrigger: null, _graceTimer: null, _graceDeadline: 0,
    awaySince: 0, awayCharged: false,
    violationCount: 0, totalPenalty: 0, showViolationBanner: false,
    violationMessage: '', violationType: 'warning', _bannerCount: 0, _bannerKind: 'tab_switch',
    _applyViolationBanner: function () {}, _maybeAutoSubmit: function () {},
    _csrfToken: function () { return 'csrf'; }, resumeFullscreen: async function () {},
  }, M, H);
}

function log(body, i) {
  const entry = body.logs[i];
  return { kind: entry.violation_type, trigger: entry.metadata.trigger,
           away: entry.metadata.away_seconds };
}

(async () => {
  const out = {};
  out.extracted = {
    gcd: typeof M.startAwayGrace, tick: typeof M._graceTick, end: typeof M.endAway,
    charge: typeof M._chargeAway, left: typeof M._graceChancesLeft,
    awayOver: typeof M._awayIsOver, back: typeof M.returnToExam,
    violation: typeof H.handleViolation,
  };

  // 1. an absence starts a countdown — the paper blurs, nothing is charged.
  const a = fresh();
  AWAY();
  a.startAwayGrace('focus_lost', 'window_blur');
  out.detected = { charged: posts.length, blurred: a.awayBlurred, left: a.graceLeft,
                   chances: a._graceChancesLeft(), armed: a._graceTimer !== null,
                   stopText: a.graceKind };

  // 2. coming back inside it costs nothing, and spends one chance.
  now += 4000;
  a._graceTick();
  out.midway = { left: a.graceLeft, charged: posts.length };
  BACK();
  a.endAway();
  out.returned = { charged: posts.length, left: a.graceLeft, used: a.graceUsed,
                   chances: a._graceChancesLeft(), blurred: a.awayBlurred };
  await a.returnToExam();
  out.clicked = { blurred: a.awayBlurred, charged: posts.length, used: a.graceUsed };

  // 3. an absence that runs out is charged once, with how long the student was gone.
  //    The duration is measured from the event, so the 1.5 s debounce the page
  //    waits through counts as part of the absence.
  posts.length = 0;
  const b = fresh();
  AWAY();
  now += 1000;
  b.startAwayGrace('tab_switch', 'visibilitychange', now);
  await settle();
  out.beforeExpiry = { charged: posts.length };
  now += 10000;
  b._graceTick();
  await settle();
  out.expired = { charged: posts.length, left: b.graceLeft, used: b.graceUsed };
  await settle();
  b._graceTick();          // a second tick must not bill the same absence again
  await settle();
  out.expiredTwice = { charged: posts.length };
  if (posts.length) { out.expiredRow = log(posts[0].body, 0); }
  BACK();
  b.endAway();
  out.expiredThenBack = { used: b.graceUsed, charged: posts.length };

  // 4. the countdown is bounded: after both chances the next absence is charged
  //    the moment it is seen, as it was before this feature existed.
  posts.length = 0;
  const c = fresh();
  AWAY();
  now += 1000;
  c.startAwayGrace('focus_lost', 'window_blur');
  now += 2000; BACK(); c.endAway();
  AWAY();
  c.startAwayGrace('focus_lost', 'window_blur');
  now += 2000; BACK(); c.endAway();
  out.budgetSpent = { used: c.graceUsed, chances: c._graceChancesLeft(), charged: posts.length };
  AWAY();
  c.startAwayGrace('focus_lost', 'window_blur');
  await settle();
  out.thirdAbsence = { charged: posts.length, left: c.graceLeft, blurred: c.awayBlurred };
  if (posts.length) { out.thirdRow = log(posts[0].body, 0); }
  // And the next absence is billed too: coming back from a charged absence has
  // to clear the flag, or every absence after the first charge would be free.
  BACK();
  c.endAway();
  AWAY();
  now += 1000;
  c.startAwayGrace('focus_lost', 'window_blur', now);
  await settle();
  out.fourthAbsence = { charged: posts.length };

  // 5. one absence, one charge — a second detector seeing the same act must not
  //    open a second countdown or bill a second time.
  posts.length = 0;
  const d = fresh();
  AWAY();
  now += 1000;
  d.startAwayGrace('tab_switch', 'visibilitychange');
  d.startAwayGrace('focus_lost', 'window_blur');
  out.twoDetectors = { kind: d.graceKind, trigger: d.graceTrigger, left: d.graceLeft };
  now += 10000;
  d._graceTick();
  await settle();
  out.twoDetectorsCharged = { charged: posts.length, left: d.graceLeft };
  if (posts.length) { out.twoDetectorsRow = log(posts[0].body, 0); }

  // 6. a stray focus event (nothing pending) must not spend a chance.
  const e = fresh();
  BACK();
  e.endAway();
  out.stray = { used: e.graceUsed, chances: e._graceChancesLeft() };

  // 7. a paper already handed in is never charged by a countdown still running.
  posts.length = 0;
  const f = fresh();
  AWAY();
  now += 1000;
  f.startAwayGrace('focus_lost', 'window_blur');
  f.submitted = true;
  now += 10000;
  f._graceTick();
  await settle();
  out.afterSubmit = { charged: posts.length };

  // 8. a hidden document is the same mechanism, so the phone-call path is the
  //    grace too — and it is never billed twice for a returned-in-time absence.
  posts.length = 0;
  const g = fresh();
  document.hidden = true;
  now += 1000;
  g.startAwayGrace('tab_switch', 'visibilitychange');
  out.hidden = { blurred: g.awayBlurred, left: g.graceLeft, charged: posts.length };
  now += 3000;
  document.hidden = false; BACK();
  g.endAway();
  await settle();
  out.hiddenReturned = { charged: posts.length, used: g.graceUsed };

  // 9. the duration covers the whole absence — the 1.5 s debounce plus the 10 s
  //    countdown — because the page records when the student left, not when it
  //    stopped waiting to be sure.
  posts.length = 0;
  const h = fresh();
  AWAY();
  const leftAt = now;
  now += 1500;
  h.startAwayGrace('focus_lost', 'window_blur', leftAt);
  now += 10000;
  h._graceTick();
  await settle();
  out.debounce = { away: posts.length ? log(posts[0].body, 0).away : null };

  // 10. the student is back when the countdown ends, but the browser swallowed
  //     the focus event. The return is what the chance is for, so being back is
  //     re-read at the deadline rather than assumed from a listener firing.
  posts.length = 0;
  const k = fresh();
  AWAY();
  now += 1000;
  k.startAwayGrace('focus_lost', 'window_blur', now);
  now += 3000;
  BACK();
  now += 10000;
  k._graceTick();
  await settle();
  out.backAtDeadline = { charged: posts.length, used: k.graceUsed };

  console.log(JSON.stringify(out));
})();
"""


class TestTheCountdownItself:
    """The real methods, sliced out of the template and executed."""

    @classmethod
    def run(cls, tmp_path):
        page = source(EXAM_PAGE)
        methods = slice_between(page, "        _graceChancesLeft() {",
                                "        setupAntiCheat() {")
        back = slice_between(page, "        async returnToExam() {",
                             "        _graceChancesLeft() {")
        handle = slice_between(
            page, "        handleViolation(vtype = 'tab_switch'",
            "        _applyViolationBanner(n, kind = 'tab_switch') {")
        (tmp_path / "methods.js").write_text(back + methods, encoding="utf-8")
        (tmp_path / "handle.js").write_text(handle, encoding="utf-8")
        (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
        done = subprocess.run(
            [NODE, str(tmp_path / "harness.js"),
             str(tmp_path / "methods.js"), str(tmp_path / "handle.js")],
            capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip())

    @needs_node
    def test_the_countdown_and_the_charge_are_the_real_ones(self, tmp_path):
        out = self.run(tmp_path)["extracted"]
        assert out == {"gcd": "function", "tick": "function", "end": "function",
                       "charge": "function", "left": "function", "awayOver": "function",
                       "back": "function", "violation": "function"}, out

    @needs_node
    def test_an_absence_blurs_the_paper_and_charges_nothing(self, tmp_path):
        out = self.run(tmp_path)["detected"]
        assert out["charged"] == 0, "the absence was charged before the countdown ran"
        assert out["blurred"] is True, "the paper stayed readable during the countdown"
        assert out["left"] == 10
        assert out["chances"] == 2
        assert out["armed"] is True, "no countdown was started at all"

    @needs_node
    def test_coming_back_inside_the_countdown_costs_nothing_at_all(self, tmp_path):
        out = self.run(tmp_path)
        assert out["midway"] == {"left": 6, "charged": 0}
        assert out["returned"]["charged"] == 0, "a return in time still cost a point"
        assert out["returned"]["used"] == 1
        assert out["returned"]["chances"] == 1
        assert out["returned"]["blurred"] is True, (
            "the paper unblurred on a focus event, without the student saying they are back")
        assert out["clicked"] == {"blurred": False, "charged": 0, "used": 1}

    @needs_node
    def test_an_absence_that_runs_out_is_charged_once_with_its_duration(self, tmp_path):
        out = self.run(tmp_path)
        assert out["beforeExpiry"] == {"charged": 0}
        assert out["expired"] == {"charged": 1, "left": 0, "used": 0}
        assert out["expiredTwice"] == {"charged": 1}, (
            "the same absence was billed again on the next tick")
        assert out["expiredRow"] == {"kind": "tab_switch", "trigger": "visibilitychange",
                                     "away": 10}, out["expiredRow"]

    @needs_node
    def test_the_chances_are_bounded_and_then_the_ladder_is_the_old_one(self, tmp_path):
        out = self.run(tmp_path)
        assert out["budgetSpent"] == {"used": 2, "chances": 0, "charged": 0}
        assert out["thirdAbsence"]["charged"] == 1, (
            "the third absence was forgiven too — the grace is unbounded")
        assert out["thirdAbsence"]["left"] == 0
        assert out["thirdAbsence"]["blurred"] is True, (
            "out of chances, the paper was left readable")
        assert out["thirdRow"]["away"] == 0, "charged on sight, so it has no duration"

    @needs_node
    def test_two_detectors_seeing_one_absence_bill_it_once(self, tmp_path):
        out = self.run(tmp_path)
        assert out["twoDetectors"]["kind"] == "tab_switch", (
            "the second detector took ownership of the same absence")
        assert out["twoDetectorsCharged"] == {"charged": 1, "left": 0}
        assert out["twoDetectorsRow"]["kind"] == "tab_switch"

    @needs_node
    def test_a_stray_focus_event_does_not_spend_a_chance(self, tmp_path):
        assert self.run(tmp_path)["stray"] == {"used": 0, "chances": 2}

    @needs_node
    def test_a_submitted_paper_is_never_charged_by_a_running_countdown(self, tmp_path):
        assert self.run(tmp_path)["afterSubmit"] == {"charged": 0}

    @needs_node
    def test_a_charge_cannot_be_undone_and_is_not_refunded(self, tmp_path):
        """Coming back after the countdown ends does not hand the chance back:
        the absence was real, and the ladder it climbed is the server's."""
        assert self.run(tmp_path)["expiredThenBack"] == {"used": 0, "charged": 1}

    @needs_node
    def test_every_absence_after_the_charge_is_charged_too(self, tmp_path):
        assert self.run(tmp_path)["fourthAbsence"] == {"charged": 2}, (
            "a charged absence left the paper marked as already billed")

    @needs_node
    def test_being_back_at_the_deadline_is_not_a_violation(self, tmp_path):
        """A browser that never fired the focus event must not cost the student
        the points the countdown promised they could keep."""
        assert self.run(tmp_path)["backAtDeadline"] == {"charged": 0, "used": 1}

    @needs_node
    def test_the_recorded_duration_is_the_whole_absence(self, tmp_path):
        """11.5 s: the 1.5 s debounce plus the 10 s countdown. Measuring from the
        debounce instead would record 10 s for every absence, which is the number
        the page already knew — the teacher's copy would say nothing."""
        assert self.run(tmp_path)["debounce"] == {"away": 12}

    @needs_node
    def test_the_phone_call_path_is_the_same_countdown(self, tmp_path):
        out = self.run(tmp_path)
        assert out["hidden"] == {"blurred": True, "left": 10, "charged": 0}
        assert out["hiddenReturned"] == {"charged": 0, "used": 1}


# ── what the pages actually render ──────────────────────────────────────────

class TestTheRenderedPromises:
    """Rendered rather than grepped, because a promise is the *output*: a `t()`
    whose Jinja half went missing, or a number that only exists in a comment,
    reads fine in the source and wrong on the screen."""

    def _render(self, app, template, signed_in=True, **ctx):
        """`base.html` picks one of two layouts on `g.user_id` and a page rendered
        into the wrong one throws its whole body away **without an error**: the
        guide's body is in the signed-in layout and the tutorial's in the public
        one, so each was invisible — and the test that "passed" against the empty
        page was passing on nothing. Both are rendered the way their own route
        does."""
        from flask import g
        path = "/guide/skor" if signed_in else "/tutorial/murid"
        with app.test_request_context(path):
            if signed_in:
                g.user_id = "guru-1"
                g.user_name = "Guru Uji"
                g.user_email = "guru@example.test"
                g.user_role = "guru"
                g.tz_offset = 7
            g.show = {}
            return app.jinja_env.get_template(template).render(**ctx)

    def test_the_guide_states_the_countdown_in_both_tabs(self, app):
        html = self._render(app, "guide/skor.html", penalty_schedule=_penalty_rows(),
                            charged_violations=3, away_grace_seconds=AWAY_GRACE_SECONDS,
                            away_grace_chances=AWAY_GRACE_CHANCES)
        blocks = re.findall(r"<p[^>]*data-second-chance[^>]*>(.*?)</p>", html, re.S)
        assert len(blocks) == 2, "the guide renders the second chance in one tab only"
        for block in blocks:
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block))
            assert str(AWAY_GRACE_SECONDS) in text, f"no countdown in: {text[:120]}"
            assert str(AWAY_GRACE_CHANCES) in text, f"no budget in: {text[:120]}"
            assert "t(" not in text, "an Alpine binding leaked into the rendered copy"

    def test_the_tutorial_states_it_with_the_numbers_the_route_passes(self, app):
        html = self._render(app, "tutorial_murid.html", signed_in=False, ac={},
                            away_grace_seconds=AWAY_GRACE_SECONDS,
                            away_grace_chances=AWAY_GRACE_CHANCES)
        assert f"{AWAY_GRACE_SECONDS} detik untuk kembali tanpa dicatat" in html
        assert f"maksimal {AWAY_GRACE_CHANCES} kali per ujian" in html

    def test_the_exam_page_compiles_and_counts_with_the_route_s_numbers(self, app):
        """Compiled with Jinja, so a syntax error in the countdown's markup or in
        the terms list cannot reach a student's phone on the day of an exam; and
        the two numbers are the ones the route hands over."""
        html = self._render_exam(app)
        assert f"graceSeconds: {AWAY_GRACE_SECONDS}," in html
        assert f"graceChances: {AWAY_GRACE_CHANCES}," in html
        assert f"maksimal {AWAY_GRACE_CHANCES} kali per ujian" in html

    def _render_exam(self, app):
        """The exam template with only the context its own copy needs.

        It is not rendered on a bare `render()` in the suite anywhere else — it is
        a 4,400-line page whose whole context comes from one route — so this is
        the compile check the rest of the file does not have, kept to the two
        numbers and the terms sentence it is here for.
        """
        from flask import g
        with app.test_request_context("/student/exams/e1"):
            g.user_id = "stu-1"
            g.user_name = "Murid Uji"
            g.user_role = "murid"
            g.tz_offset = 7
            g.show = {}
            return app.jinja_env.get_template("student/take_exam.html").render(
                exam={"id": "e1", "title": "T", "total_questions": 1,
                      "duration_minutes": 30, "question_types": {}},
                anti_cheat_config="{\"anti_cheat_enabled\": true}",
                exam_started_at=None, recovery_code="", question_options={},
                deadline=None, deadline_reason="", seconds_left=1800, window_end=None,
                away_grace_seconds=AWAY_GRACE_SECONDS, away_grace_chances=AWAY_GRACE_CHANCES)


# ── the teacher's side of the record ─────────────────────────────────────────

class TestTheTeacherCanSeeTheCountdownRan:
    def test_the_service_surfaces_how_long_the_student_was_gone(self):
        from app.services.anti_cheat_service import _as_event
        event = _as_event({
            "user_id": "s", "violation_type": "focus_lost", "created_at": "2026-09-13T06:23:48Z",
            "metadata": {"violation_count": 3, "trigger": "window_blur", "away_seconds": 47},
        })
        assert event["away_seconds"] == 47
        assert event["charged"] is True

    def test_a_row_without_a_duration_says_nothing_about_one(self):
        """`None` is a fact: this absence was recorded the moment it was seen. The
        report must not print a zero it cannot know."""
        from app.services.anti_cheat_service import _as_event
        event = _as_event({
            "user_id": "s", "violation_type": "fullscreen_exit",
            "created_at": "2026-09-13T06:23:48Z", "metadata": {"trigger": "fullscreenchange"},
        })
        assert event["away_seconds"] is None
        page = source(GRADE_PAGE)
        assert "{% if ev.away_seconds %}" in page, (
            "the marking page decides to print a duration on something other than a duration")

    def test_the_metadata_the_page_sends_carries_the_duration(self):
        page = source(EXAM_PAGE)
        assert "away_seconds: awaySeconds" in page
        assert "handleViolation(vtype = 'tab_switch', trigger = null, awaySeconds = null)" in page
