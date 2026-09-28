"""The clock kept running while the page did not — and the page now says so.

A sitting's countdown is the *exam's* clock, not the page's: `started_at` is
stamped once by the server and `seconds_left` is computed from it, so closing the
tab, reloading, or taking a call does not pause anything. A student who comes back
therefore meets a number that is simply *lower*, with nothing on screen to explain
it — the same shape of defect as the paper that invented a countdown out of
`duration_minutes` (`5006f5d`), one step further along.

So the page measures the gap it can see and says it once, in the language being
read. Three things decide whether it may:

1. **A clock has to have run.** A paper with no deadline ("Tak terbatas") has
   nothing that kept moving, so saying it does would be the same lie in the other
   direction. `timeLeft === null` is that paper.
2. **The paper has to be running.** Before "Saya Setuju", or once submitted, there
   is no sitting to explain.
3. **The gap has to be measured, in the two shapes it comes in.** The absence this
   document *witnessed* — a tab switch, a minimised window — is exact, because the
   event is the instant. The one it could not witness — a reload, where the memory
   goes with the document — is read from the device, and that is why the last
   attended instant is persisted rather than kept in a variable.

The notice is a card in the paper, not an overlay, so it can neither be covered by
nor cover the blockers; and it carries no verdict vocabulary, because it explains a
number rather than accusing anyone.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the page's own measurement")

NOW = 1_800_000_000_000          # a fixed clock, so every gap below is arithmetic

#: The methods the notice is made of. Read out of the page rather than re-implemented,
#: because "does the page say how long you were gone?" is not a question a regex can
#: answer about the page's own arithmetic.
AWAY_METHODS = ["_awayStampKey", "_noteAwayGap", "_markSeen", "_persistSeen",
                "_markAway", "_noteReturn", "_startAwayWatch", "dismissAwayNotice"]

#: Words this notice must never use. It reports a gap; a page that tells a pupil
#: they were "absent" or "violating" has turned an explanation into a charge, and
#: the anti-cheat ladder already has its own, deliberate vocabulary for that.
VERDICT_WORDS = ("pelanggaran", "penalti", "menyontek", "curang", "violation",
                 "penalty", "cheat")


# ── reading the page's own code ─────────────────────────────────────────────

def _method(html: str, name: str) -> str:
    """One method of the page's Alpine component, braces balanced."""
    match = re.search(r"\n\s{8}" + re.escape(name) + r"\((?:[^)]*)\)\s*\{", html)
    assert match, f"the page no longer defines {name}()"
    start = html.index("{", match.start())
    depth, j = 0, start
    while j < len(html):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[match.start():j + 1].strip()
        j += 1
    raise AssertionError(f"unbalanced braces reading {name}()")


def _code(source: str, name: str) -> str:
    """One method with its comments removed.

    A guard that reads names rather than calls is satisfied by a comment that
    mentions the name — `"_startAwayWatch(" in init` passed while the call itself
    had been deleted, because the comment above it said "see _startAwayWatch()".
    The same shape as the gate whose own failure text satisfied the check for the
    tool it named, so the wiring below reads code, not prose.
    """
    body = _method(source, name)
    return re.sub(r"//[^\n]*", "", body)


def _constants(html: str) -> list[str]:
    out = []
    for name in ("SG_AWAY_NOTICE_SECONDS", "SG_AWAY_STAMP_MS"):
        match = re.search(r"const " + name + r" = ([0-9]+);", html)
        assert match, f"the page no longer declares {name}"
        out.append(f"const {name} = {match.group(1)};")
    return out


def _render_page(app):
    """The exam page with only the context its own copy needs, as its route sends."""
    from flask import g

    ctx = {
        "exam": {"id": "e1", "title": "T", "total_questions": 1,
                 "duration_minutes": 30, "question_types": {}, "pdf_page_urls": []},
        "anti_cheat_config": "{\"anti_cheat_enabled\": true}",
        "exam_started_at": "2026-09-28T07:00:00+00:00", "recovery_code": "",
        "question_options": {},
        "deadline": "2026-09-28T07:30:00+00:00",
        "deadline_reason": "duration", "seconds_left": 1800, "window_end": None,
        "away_grace_seconds": 15, "away_grace_chances": 2,
        "student_name": "Ahmad", "student_class_label": "7A",
    }
    with app.test_request_context("/student/exams/e1"):
        g.user_id, g.user_name, g.user_role = "stu-1", "Murid Uji", "murid"
        g.tz_offset, g.show = 7, {}
        return app.jinja_env.get_template("student/take_exam.html").render(**ctx)


def _run(html: str, body: str) -> object:
    """Run a fragment against the page's real away-notice methods, in node."""
    methods = ",\n".join(_method(html, name) for name in AWAY_METHODS)
    script = "\n".join(_constants(html)) + "\n" + (
        "const M = {\n" + methods + "\n};\n"
        "function store(seed) {\n"
        "  const m = Object.assign({}, seed || {});\n"
        "  return { getItem: k => (k in m ? String(m[k]) : null),\n"
        "           setItem: (k, v) => { m[k] = String(v); },\n"
        "           _raw: m };\n"
        "}\n"
        "function fresh(state, seed) {\n"
        "  globalThis.localStorage = store(seed);\n"
        "  globalThis.document = {hidden: false, addEventListener: () => 0};\n"
        "  globalThis.window = {addEventListener: () => 0};\n"
        "  globalThis.setInterval = () => 0;\n"
        "  const realNow = Date.now;\n"
        "  Date.now = () => NOW;\n"
        "  const self = Object.assign({submitted: false, showExamAgreement: false,\n"
        "    timeLeft: 1800, awayNoticeMinutes: 0, _seenAt: 0, _stampedAt: 0,\n"
        "    _awayBeganAt: 0, stampTimer: null}, state || {});\n"
        "  for (const k of Object.keys(M)) self[k] = M[k];\n"
        "  self._restore = () => { Date.now = realNow; };\n"
        "  return self;\n"
        "}\n"
        "const NOW = " + str(NOW) + ";\n"
    ) + "\n" + body
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


# ── 1. a clock has to have run ──────────────────────────────────────────────

class TestAPaperWithNoClockExplainsNothing:
    """`timeLeft === null` is "Tak terbatas": the paper may have no deadline at
    all, and then nothing ran while the student was gone. A notice there would be
    the invented-countdown defect (5006f5d) wearing the other mask."""

    @needs_node
    def test_no_notice_when_there_is_no_clock(self, app):
        html = _render_page(app)
        got = _run(html, (
            "const out = [null, 1800].map(function (left) {\n"
            "  const s = fresh({timeLeft: left, _seenAt: NOW - 600 * 1000});\n"
            "  s._noteReturn();\n"
            "  const v = s.awayNoticeMinutes; s._restore();\n"
            "  return v;\n"
            "});\n"
            "console.log(JSON.stringify(out));\n"
        ))
        assert got == [0, 10], (
            "an unlimited paper claims a clock kept running, or a timed one is "
            f"silent: {got}")


# ── 2. the paper has to be running ──────────────────────────────────────────

class TestBeforeThePaperRunsThereIsNothingToExplain:
    @needs_node
    def test_no_notice_before_agreement_or_after_submit(self, app):
        html = _render_page(app)
        got = _run(html, (
            "const cases = [{submitted: true}, {showExamAgreement: true}, {}];\n"
            "const out = cases.map(function (c) {\n"
            "  const s = fresh(Object.assign({_seenAt: NOW - 600 * 1000}, c));\n"
            "  s._noteReturn();\n"
            "  const v = s.awayNoticeMinutes; s._restore();\n"
            "  return v;\n"
            "});\n"
            "console.log(JSON.stringify(out));\n"
        ))
        assert got == [0, 0, 10], (
            f"the notice fires where there is no running paper: {got}")


# ── 3. the gap is measured, and the threshold is honest ─────────────────────

class TestTheGapIsMeasuredRatherThanGuessed:
    @needs_node
    def test_a_short_absence_is_not_worth_a_sentence(self, app):
        """Below the threshold the clock barely moved; a notice on every tab
        switch would be noise on the thing it exists to explain."""
        html = _render_page(app)
        got = _run(html, (
            "const gaps = [0, 1, 30, 59, 60, 90, 600, 3660];\n"
            "const out = gaps.map(function (g) {\n"
            "  const s = fresh({_seenAt: NOW - g * 1000});\n"
            "  s._noteReturn();\n"
            "  const v = s.awayNoticeMinutes; s._restore();\n"
            "  return v;\n"
            "});\n"
            "console.log(JSON.stringify(out));\n"
        ))
        assert got[:4] == [0, 0, 0, 0], f"a short absence still announces itself: {got}"
        assert got[4:] == [1, 2, 10, 61], f"the minutes are not the gap: {got}"

    @needs_node
    def test_a_witnessed_departure_is_preferred_to_the_heartbeat(self, app):
        """A tab switch or a minimised window is an event, and the event *is* the
        instant. The stored heartbeat is at most a cadence behind, so reading it
        instead would under-report the very absence the panel just charged for."""
        html = _render_page(app)
        got = _run(html, (
            "const s = fresh({_seenAt: NOW - 10 * 1000, _awayBeganAt: NOW - 300 * 1000});\n"
            "s._noteReturn();\n"
            "const out = {minutes: s.awayNoticeMinutes, began: s._awayBeganAt,\n"
            "             seen: s._seenAt};\n"
            "s._restore();\n"
            "console.log(JSON.stringify(out));\n"
        ))
        assert got["minutes"] == 5, (
            f"the notice used the heartbeat, not the departure: {got}")
        assert got["began"] == 0, "the next absence would continue this one"
        assert got["seen"] == NOW, "the page did not record that it is attended"


# ── 4. the absence a reload comes back from ─────────────────────────────────

class TestTheGapSurvivesTheDocumentThatMeasuredIt:
    @needs_node
    def test_a_reload_is_measured_from_what_the_device_remembers(self, app):
        """A reload takes every variable with it, so a gap that only lived in one
        is a gap no reload can see — and a reload is exactly when the clock comes
        back lower."""
        html = _render_page(app)
        got = _run(html, (
            "const out = [10 * 60, 30, null].map(function (age) {\n"
            "  const seed = {};\n"
            "  if (age !== null) seed['exam_seen_e1'] = NOW - age * 1000;\n"
            "  const s = fresh({}, seed);\n"
            "  s._startAwayWatch();\n"
            "  const v = s.awayNoticeMinutes;\n"
            "  const wrote = globalThis.localStorage._raw['exam_seen_e1'];\n"
            "  s._restore();\n"
            "  return [v, wrote];\n"
            "});\n"
            "console.log(JSON.stringify(out));\n"
        ))
        assert got[0][0] == 10, f"a reload is not measured from the device: {got}"
        assert got[1][0] == 0 and got[2][0] == 0, f"a fresh visit is announced: {got}"
        assert all(row[1] == str(NOW) for row in got), (
            f"the instant of this visit was not written, so the next one cannot "
            f"measure it: {got}")

    @needs_node
    def test_the_stamp_belongs_to_one_exam(self, app):
        """A stamp shared between papers would report the gap since the *other*
        exam was last open."""
        html = _render_page(app)
        got = _run(html, (
            "const s = fresh({});\n"
            "const key = s._awayStampKey(); s._restore();\n"
            "console.log(JSON.stringify(key));\n"
        ))
        assert got == "exam_seen_e1", f"the stamp is not per exam: {got!r}"

    @needs_node
    def test_a_departure_is_written_at_once_and_not_left_to_the_cadence(self, app):
        """The stamp must be current at the moment the student leaves: the reload
        that follows may be a second later, and measuring from a cadence-old
        instant would credit them time they were actually reading."""
        html = _render_page(app)
        got = _run(html, (
            "const s = fresh({});\n"
            "const first = {at: 0, stamp: null};\n"
            "s._markAway();\n"
            "first.at = s._awayBeganAt;\n"
            "first.stamp = globalThis.localStorage._raw['exam_seen_e1'];\n"
            "s._markSeen();\n"
            "s._awayBeganAt = NOW;\n"
            "s._markAway();\n"
            "const second = s._awayBeganAt;\n"
            "s._restore();\n"
            "console.log(JSON.stringify({first: first, second: second}));\n"
        ))
        assert got["first"]["at"] == NOW, "the departure instant was not recorded"
        assert got["first"]["stamp"] == str(NOW), (
            "the departure was not written to the device, so a reload one second "
            "later would measure nothing")
        assert got["second"] == NOW, (
            "a second departure moved the clock, which would make one absence "
            "continue into the next")


# ── 5. dismissal is a state change, not a re-measure ────────────────────────

class TestItCanBePutAway:
    @needs_node
    def test_dismissing_does_not_erase_the_measurement(self, app):
        """The card is dismissed with a click; the gap it reported is the page's
        own reading and dismissing the card is not a reason to forget it."""
        html = _render_page(app)
        got = _run(html, (
            "const s = fresh({_seenAt: NOW - 600 * 1000});\n"
            "s._noteReturn();\n"
            "const shown = s.awayNoticeMinutes;\n"
            "s.dismissAwayNotice();\n"
            "const after = {notice: s.awayNoticeMinutes, seen: s._seenAt};\n"
            "s._restore();\n"
            "console.log(JSON.stringify({shown: shown, after: after}));\n"
        ))
        assert got["shown"] == 10, f"nothing was shown to dismiss: {got}"
        assert got["after"]["notice"] == 0, f"the card could not be dismissed: {got}"
        assert got["after"]["seen"] == NOW, (
            "dismissing re-marked the clock, which would shorten the next gap")


# ── 6. the wiring, read off the page ────────────────────────────────────────

class TestThePageAsksForTheNoticeWithoutTouchingTheLadder:
    def test_the_page_starts_watching_itself_when_it_loads(self):
        init = _code(PAGE.read_text(encoding="utf-8"), "init")
        assert "this._startAwayWatch();" in init, (
            "the page never reads the stamp it left on the device, so a reload "
            "comes back to a lower clock with no explanation")

    def test_the_absence_ending_is_what_measures_it(self):
        """Both ways back — the tab becoming visible, and the window becoming the
        one in front — end in the same measurement."""
        watch = _code(PAGE.read_text(encoding="utf-8"), "_startAwayWatch")
        assert "this._noteReturn();" in watch, (
            "coming back to the paper does not measure the absence")
        assert "'visibilitychange'" in watch and "'focus'" in watch, (
            "only one of the two ways back is watched")

    def test_a_witnessed_departure_is_recorded_by_every_way_out(self):
        """A tab switch hides the document; a window behind another window does
        not; a reload takes the document with it. Each is an absence the page can
        date exactly, so each gets a mark."""
        watch = _code(PAGE.read_text(encoding="utf-8"), "_startAwayWatch")
        assert watch.count("this._markAway();") >= 3, (
            "one of the three ways out of the page does not record when it "
            "happened, so a reload measures from a stale instant")
        assert "'blur'" in watch and "'pagehide'" in watch

    def test_explaining_the_clock_is_not_a_penalty_feature(self):
        """The notice is about the exam's clock, not about the anti-cheat: it must
        not be armed, gated or silenced by the ladder, or a paper with the ladder
        off would come back to an unaccountably lower clock — and a student who was
        charged would read the charge into a card that carries none."""
        source = PAGE.read_text(encoding="utf-8")
        assert "antiCheat" not in _code(source, "_startAwayWatch"), (
            "the notice is wired into the anti-cheat ladder")
        assert "this._noteReturn(" not in _code(source, "endAway"), (
            "the notice rides on the away grace, which is a penalty path")


# ── 7. the notice explains, and does not accuse ─────────────────────────────

MARKER = 'x-show="awayNoticeMinutes > 0'


def _banner() -> str:
    """The card, from its gate to the next block comment — the shape the page
    already uses to keep one card readable in a 5000-line template."""
    source = PAGE.read_text(encoding="utf-8")
    start = source.find(MARKER)
    assert start != -1, "the page has no away notice"
    start = source.rfind("<!--", 0, start)
    assert start != -1, "the away notice is not delimited by a comment"
    end = source.find("<!--", source.find(MARKER))
    assert end != -1, "the away notice runs to the end of the page"
    return source[start:end]


def _visible(block: str) -> str:
    """What a student actually reads: the block's own comments are written for
    whoever maintains the page, and holding them to the notice's rules would make
    the rules unstatable."""
    stripped = re.sub(r"<!--.*?-->", "", block, flags=re.S)
    assert stripped.strip(), "the away notice is nothing but comments"
    return stripped


class TestTheNoticeIsReadableAndNeutral:
    def test_it_is_a_card_in_the_paper_and_not_an_overlay(self):
        """An overlay would have to be placed in the layer scale and would be
        covered by (or cover) the blockers. A card in the paper cannot."""
        block = _banner()
        assert "sg-layer-" not in block, "the notice is stacked as an overlay"
        assert "fixed" not in block, "the notice is pinned over the paper"
        assert "awayNoticeMinutes > 0" in block, "the card is not bound to the gap"
        assert "dismissAwayNotice()" in block, "the card cannot be put away"
        assert 'role="status"' in block, (
            "a notice that appears while the student is reading is announced to "
            "a screen reader only if it says it is one")

    def test_it_never_turns_a_gap_into_a_charge(self):
        block = _visible(_banner()).lower()
        for word in VERDICT_WORDS:
            assert word not in block, (
                f"the notice uses {word!r}; it explains a clock, it does not "
                "accuse a pupil")

    def test_every_word_of_it_is_a_bilingual_pair(self):
        """The i18n sweep reads card bodies, but a card written here is easy to
        add to without a pair — so the pairs are asserted, not assumed."""
        block = _visible(_banner())
        assert block.count("t(") >= 3, (
            "the notice is not written with bilingual pairs")
        for value in re.findall(r'x-(?:text|html)="([^"]*)"', block):
            stripped = re.sub(r"t\('[^']*',\s*'[^']*'\)", "", value)
            prose = re.findall(r"'([^']*)'", stripped)
            for literal in prose:
                assert not re.search(r"[A-Za-z]{2,}", literal), (
                    f"prose outside a t() pair: {literal!r} in {value!r}")
