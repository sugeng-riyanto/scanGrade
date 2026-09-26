"""The gate wrote down what it measured, and then nobody could read it.

`/super-admin/deploy-status` names the perf gate when it refuses a release — "the
performance gate" — and quotes the runner's own reason, "slower than the last
release that passed". That is a *which*, not a *what*: the page tells an operator
that something got slower without saying which page, by how much, or against
which numbers.

The numbers were never missing. `deploy/perf_gate.py` appends one JSON line per
judgement to `/var/lib/scangrade-deploy/perf/history.jsonl` — the commit under
judgement, the verdict (`baseline`, `pass`, `unconfirmed`, `regressed`), every
measured number, and, on a regression, the `reasons` list the gate itself printed:
"slowest page p50 812 ms against 480 ms on 366abdf (1.69x, allowed 1.50x) — worst
endpoint(s): GET /teacher/results". And the baseline it compared against is a file
right beside it.

Nothing read either file. So the one answer an operator needed — is this a real
regression or two noisy probes? — was reachable only by SSHing in and running
`journalctl -u scangrade-deploy` (or by reading the history by hand), which is the
exact trip this page exists to make unnecessary.

What these tests hold:

* **The last judgement is the one shown**, and the file is read from the end: it
  grows by a line per deploy for the life of the box, and a page render must not
  depend on how long that history has been accumulating.
* **A record that cannot be read is reported**, never treated as absent. "The gate
  has refused nothing" and "the gate recorded something and this page cannot see
  it" are different states with different remedies, and the same rule the quarantine
  and last-stop records already follow.
* **A verdict this page has no word for is `unknown`**, not the nearest one. A newer
  gate that adds a verdict must not have its refusal rendered as a pass.
* **Every reading the page can render has a sentence in both languages**, because
  the copy lives in the template where the language toggle reaches it.

The JSON shapes below are the gate's own, taken from `baseline_record()` and the
`append_evidence` calls in `deploy/perf_gate.py`.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import deploy_status_service as status  # noqa: E402

TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"

#: The two real commits this box's refusal is about: the release under judgement,
#: and the one still serving that it was measured against. Real shas, so the subject
#: lookup exercised here is the one the page uses rather than a stub.
JUDGED = "88829c924eb356c785658ff667ce902defaed3c1"
BASELINE_SHA = "366abdf0d8b7980e4d283f44ce954001ff466cd9"


# ── the gate's own record ────────────────────────────────────────────────────

def record(*, commit: str = JUDGED, verdict: str = "regressed",
           p50_ms: float = 812.0, p95_ms: float = 1900.0,
           endpoints=("GET /teacher/results",), samples: int = 40,
           reasons=None, measured_at: str = "2026-09-25T12:16:00+00:00") -> dict:
    """One line of `history.jsonl`, as `perf_gate.py` writes it."""
    out = {
        "commit": commit,
        "measured_at": measured_at,
        "shape": {"sessions": 20, "teachers": 2, "duration": 20.0,
                  "base": "https://scangrade.web.id"},
        "latency": {"p50_ms": p50_ms, "p95_ms": p95_ms,
                    "endpoints": list(endpoints), "samples": samples},
        "page_cost": {"bytes": 240304.0, "bytes_endpoint": "GET /student/exams",
                      "roundtrips": 12.0,
                      "roundtrips_endpoint": "GET /teacher/dashboard"},
        "error_pct": 0.0,
        "server_errors_5xx": 0,
        "requests_total": 812,
        "throughput_rps": 40.6,
        "verdict": verdict,
    }
    if reasons is None and verdict == "regressed":
        reasons = [f"slowest page p50 {p50_ms:.0f} ms against 480 ms on {BASELINE_SHA[:7]} "
                   f"({p50_ms / 480.0:.2f}x, allowed 1.50x) — worst endpoint(s): "
                   + ", ".join(out["latency"]["endpoints"][:4])]
    if reasons is not None:
        out["reasons"] = list(reasons)
    return out


def history(tmp_path: Path, *records: dict) -> Path:
    """A `history.jsonl` holding these judgements in order."""
    path = tmp_path / "perf-history.jsonl"
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records),
                    encoding="utf-8")
    return path


def baseline_file(tmp_path: Path, *, commit: str = BASELINE_SHA,
                  p50_ms: float = 480.0, p95_ms: float = 900.0) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({
        "commit": commit,
        "measured_at": "2026-09-20T03:00:00+00:00",
        "shape": {"sessions": 20, "teachers": 2, "duration": 20.0,
                  "base": "https://scangrade.web.id"},
        "latency": {"p50_ms": p50_ms, "p95_ms": p95_ms,
                    "endpoints": ["GET /teacher/results"], "samples": 40},
        "page_cost": {"bytes": 230000.0, "bytes_endpoint": "GET /student/exams",
                      "roundtrips": 11.0,
                      "roundtrips_endpoint": "GET /teacher/dashboard"},
        "error_pct": 0.0,
    }), encoding="utf-8")
    return path


def reading(tmp_path: Path, *records: dict, **kwargs) -> dict:
    """`perf_state()` over a written history, with a baseline beside it."""
    return status.perf_state(history(tmp_path, *records),
                             baseline_path=kwargs.pop("baseline", None)
                             or baseline_file(tmp_path),
                             repo=kwargs.pop("repo", None) or tmp_path, **kwargs)


def quarantine_file(tmp_path: Path, sha: str, *, gate: str = "perf gate (slower than "
                    "the last release that passed)") -> Path:
    """The runner's quarantine record: sha, when, which gate, then its own output."""
    path = tmp_path / "quarantined"
    path.write_text("\n".join([sha, "2026-09-25T19:16:26+07:00", gate +
                               "perf gate: REGRESSED — both probes diverged from 366abdf",
                               "    - slowest page p50 812 ms against 480 ms on 366abdf "
                               "(1.69x, allowed 1.50x)"]) + "\n", encoding="utf-8")
    return path


# ── 1b. the refused release's numbers outlive the release after it ───────────

class TestTheRefusedReleaseIsKept:
    """The perf card shows the gate's *last* judgement, and that is what loses it.

    A refused commit is quarantined and stops being judged, so the next line in the
    history belongs to a later release that passed — and the numbers behind the
    refusal (`slowest page p50 812 ms against 480 ms`) leave the page exactly when
    somebody finally comes looking, which is after the box has moved on. The
    quarantine file still quotes the gate's reason line, so what was being lost is
    the measurement that line was made from.

    So the held commit is looked up in the same history **by its sha**, and shown
    beside the latest judgement. Three things that lookup must not do:

    * match on the sha appearing as *text* — the window starts mid-record whenever a
      record is bigger than the tail, and a fragment containing the sha is not a
      judgement of it;
    * pretend it searched the whole file when it read a bounded window;
    * report "this gate never judged it" when the truth is "its judgement is older
      than the window" — the difference between a gate that did not run and a page
      that did not read far enough.
    """

    HELD = "88829c924eb356c785658ff667ce902defaed3c1"
    LATER = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

    def test_the_held_numbers_survive_a_later_release_that_passed(self, tmp_path):
        state = reading(tmp_path,
                        record(commit=self.HELD, verdict="regressed", p50_ms=812.0),
                        record(commit=self.LATER, verdict="pass", p50_ms=300.0,
                               measured_at="2026-09-26T04:00:00+00:00"),
                        held_commit=self.HELD)

        assert state["verdict_key"] == "pass", "the latest judgement is the later one"
        held = state["held"]
        assert held["key"] == status.PERF_HELD_PRESENT
        assert held["verdict_key"] == "regressed"
        assert held["commit"] == self.HELD
        assert held["short"] == self.HELD[:7]
        assert held["p50_ms"] == 812.0 and held["p95_ms"] == 1900.0
        assert held["reasons"], "the ratio that made the refusal actionable was lost"
        assert "480 ms" in held["reasons"][0], (
            "the gate's own sentence is the evidence; it has to survive the lookup")

    def test_the_held_judgement_reads_as_the_latest_one_when_it_is(self, tmp_path):
        """The common case while a commit is stuck: no second block on the page."""
        state = reading(tmp_path, record(commit=self.HELD, verdict="regressed"),
                        held_commit=self.HELD)

        assert state["held"]["key"] == status.PERF_HELD_PRESENT
        assert state["held"]["same_as_latest"] is True, (
            "the page would render the same judgement twice")

    def test_a_held_commit_this_gate_never_judged_says_so(self, tmp_path):
        """A commit can be refused by the theme gate and have no line here at all."""
        state = reading(tmp_path, record(commit=self.LATER, verdict="pass"),
                        held_commit=self.HELD)

        assert state["held"]["key"] == status.PERF_HELD_NO_JUDGEMENT
        assert state["held"]["commit"] == self.HELD

    def test_a_held_commit_older_than_the_window_is_not_read_as_never_judged(self, tmp_path):
        """The history is read as a bounded tail, so "not found" has two meanings.

        The distinction matters to the reader: one says this gate did not judge the
        commit, the other says the page did not read far enough — and only the second
        is fixed by looking at the file.
        """
        filler = [record(commit="%040x" % n, verdict="pass", p50_ms=200.0)
                  for n in range(200)]
        state = reading(tmp_path, record(commit=self.HELD, verdict="regressed"),
                        *filler, held_commit=self.HELD)

        assert state["held"]["key"] == status.PERF_HELD_OUT_OF_WINDOW
        assert state["held"]["commit"] == self.HELD

    def test_a_truncated_record_is_not_matched_on_the_sha_inside_it(self, tmp_path):
        """The match is a parsed `commit`, never a substring of the window.

        A record bigger than the tail is read as a fragment, and a fragment fails to
        parse — which is the only thing standing between this lookup and "the held
        commit was judged, verdict: none of your business".
        """
        big = record(commit=self.HELD, verdict="regressed",
                     reasons=["z" * (status.PERF_TAIL_BYTES + 1000)])
        state = reading(tmp_path, big, record(commit=self.LATER, verdict="pass"),
                        held_commit=self.HELD)

        assert state["held"]["key"] != status.PERF_HELD_PRESENT, (
            "the sha was matched as text inside a record the page cannot read")
        assert state["held"]["skipped"] == 1, (
            "a line that could not be read while looking was silently dropped, so "
            "'not found' reads as 'never judged'")

    def test_nothing_held_means_nothing_to_look_up(self, tmp_path):
        """Backwards compatible: a caller that knows of no held commit gets no card."""
        state = reading(tmp_path, record(verdict="pass"))

        assert state["held"]["key"] == status.PERF_HELD_NONE
        assert state["verdict_key"] == "pass", "the latest judgement still arrives"


# ── the small index beside the history ───────────────────────────────────────

class TestTheIndexFindsWhatTheWindowCannot:
    """The bounded tail is what made a lookup stop answering; the index removes it.

    A judgement past the read window used to report `out_of_window` — honest, and
    still a reader opening the file by hand. The gate now keeps a small index beside
    the history (one short line per judgement: a commit and the byte offset of its
    record), and the reader seeks straight to it, so the answer stops depending on
    how long the history has been accumulating. The tail scan stays the fallback, so
    a box whose gate predates the index keeps today's behaviour.
    """

    HELD = "8" * 40
    LATER = "7" * 40

    def _history(self, tmp_path, *, index=True):
        path = tmp_path / "history.jsonl"
        first = json.dumps(record(commit=self.HELD, verdict="regressed"), sort_keys=True)
        padded = record(commit=self.LATER, verdict="pass")
        padded["pad"] = "x" * (status.PERF_TAIL_BYTES + 4096)
        # Bytes, not text: the offset is a byte offset into the file the gate writes,
        # and text mode would rewrite `\n` as `\r\n` on Windows and shift every one.
        path.write_bytes((first + "\n" + json.dumps(padded, sort_keys=True)
                          + "\n").encode("utf-8"))
        if index:
            status._perf_index_path(path).write_bytes(
                (json.dumps({"commit": self.HELD, "offset": 0}) + "\n").encode("utf-8"))
        return path

    def test_a_held_judgement_beyond_the_window_is_found_through_the_index(self, tmp_path):
        state = status.perf_state(self._history(tmp_path), repo=tmp_path,
                                  held_commit=self.HELD)
        assert state["held"]["key"] == status.PERF_HELD_PRESENT, (
            "the index points straight at the record; the window no longer decides")
        assert state["held"]["p50_ms"] == 812.0
        assert state["held"]["verdict_key"] == "regressed"

    def test_without_the_index_the_same_lookup_is_still_out_of_window(self, tmp_path):
        state = status.perf_state(self._history(tmp_path, index=False), repo=tmp_path,
                                  held_commit=self.HELD)
        assert state["held"]["key"] == status.PERF_HELD_OUT_OF_WINDOW, (
            "the tail scan remains the fallback for a gate that predates the index")

    def test_a_stale_offset_is_not_taken_as_this_commit_s_judgement(self, tmp_path):
        path = self._history(tmp_path, index=False)
        # The index points at the *other* commit's line, which is what a rotated or
        # rewritten history looks like from here.
        offset = len(json.dumps(record(commit=self.HELD, verdict="regressed"),
                                sort_keys=True).encode("utf-8")) + 1
        status._perf_index_path(path).write_text(
            json.dumps({"commit": self.HELD, "offset": offset}) + "\n",
            encoding="utf-8")
        state = status.perf_state(path, repo=tmp_path, held_commit=self.HELD)
        assert state["held"]["key"] == status.PERF_HELD_OUT_OF_WINDOW, (
            "an offset that lands on another commit's record is not this commit's "
            "judgement")

    def test_the_page_and_the_gate_derive_the_index_at_the_same_path(self):
        """Two derivations, one file: the gate writes it and this page reads it.

        The path is not configured, so the only thing keeping the writer and the
        reader together is that both use the same rule — which is worth pinning,
        because a drift here would have the page read an index nothing writes.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("perf_gate_index_guard",
                                                      ROOT / "deploy" / "perf_gate.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        for path in (Path("/var/lib/scangrade-deploy/perf/history.jsonl"),
                     Path("C:/tmp/x.jsonl")):
            assert module.index_path(path) == status._perf_index_path(path), (
                f"the gate writes {module.index_path(path)} but the page reads "
                f"{status._perf_index_path(path)}")

    def test_the_index_finds_each_commit_for_the_refusal_history_too(self, tmp_path):
        path = tmp_path / "history.jsonl"
        first = json.dumps(record(commit=self.HELD, verdict="regressed"), sort_keys=True)
        second = json.dumps(record(commit=self.LATER, verdict="pass", p50_ms=640.0),
                            sort_keys=True)
        padded = record(commit="9" * 40, verdict="pass")
        padded["pad"] = "x" * (status.PERF_TAIL_BYTES + 4096)
        path.write_bytes((first + "\n" + second + "\n"
                          + json.dumps(padded, sort_keys=True) + "\n").encode("utf-8"))
        status._perf_index_path(path).write_bytes(
            (json.dumps({"commit": self.HELD, "offset": 0}) + "\n"
             + json.dumps({"commit": self.LATER,
                           "offset": len(first.encode("utf-8")) + 1}) + "\n"
             ).encode("utf-8"))
        found = status.perf_judgements(path, [self.HELD, self.LATER])
        assert found[self.HELD]["key"] == status.PERF_HELD_PRESENT
        assert found[self.HELD]["p50_ms"] == 812.0
        assert found[self.LATER]["key"] == status.PERF_HELD_PRESENT
        assert found[self.LATER]["p50_ms"] == 640.0


# ── what the gate recorded ───────────────────────────────────────────────────

class TestWhatTheGateRecorded:
    def test_a_box_that_has_never_judged_a_release_says_so(self, tmp_path):
        state = status.perf_state(tmp_path / "absent.jsonl", repo=tmp_path)
        assert state["key"] == status.PERF_NONE
        assert state["present"] is False
        assert state["verdict_key"] is None
        assert state["reasons"] == []
        assert state["path"].endswith("absent.jsonl"), (
            "a reader who has to set this path elsewhere needs to be told which one "
            "this page looked at")

    def test_a_record_that_cannot_be_read_is_not_reported_as_absent(self, tmp_path):
        state = status.perf_state(tmp_path, repo=tmp_path)
        assert state["key"] == status.PERF_UNREADABLE
        assert state["present"] is False
        assert state["reason"] and state["reason"] != "absent", (
            "\"nothing was recorded\" and \"the record cannot be read\" have different "
            "remedies, and the page must not pick one")

    def test_a_record_the_page_cannot_parse_is_not_a_pass(self, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text("this is not json\n", encoding="utf-8")
        state = status.perf_state(path, repo=tmp_path)
        assert state["key"] == status.PERF_MALFORMED
        assert state["verdict_key"] is None, "a refusal was rendered as a verdict"
        assert "this is not json" in (state["reason"] or ""), (
            "the line is the diagnosis; a reader with no shell needs to see it")

    def test_the_last_judgement_is_the_one_shown(self, tmp_path):
        older = record(verdict="pass", reasons=None, measured_at="2026-09-24T10:00:00+00:00")
        newer = record(verdict="regressed")
        state = reading(tmp_path, older, newer)
        assert state["key"] == status.PERF_PRESENT
        assert state["verdict"] == "regressed"
        assert state["verdict_key"] == "regressed"
        assert state["measured_at"] == newer["measured_at"]

    def test_a_verdict_this_page_has_no_word_for_is_unknown(self, tmp_path):
        state = reading(tmp_path, record(verdict="hyperslow"))
        assert state["verdict"] == "hyperslow", "the gate's own word is shown as it is"
        assert state["verdict_key"] == status.PERF_VERDICT_UNKNOWN, (
            "an unknown verdict must not borrow the nearest sentence — a newer gate's "
            "refusal would render as a pass")

    def test_the_measured_numbers_are_all_there(self, tmp_path):
        state = reading(tmp_path, record())
        assert state["p50_ms"] == 812.0
        assert state["p95_ms"] == 1900.0
        assert state["endpoints"] == ["GET /teacher/results"]
        assert state["samples"] == 40
        assert state["error_pct"] == 0.0
        assert state["bytes"] == 240304.0
        assert state["bytes_endpoint"] == "GET /student/exams"
        assert state["roundtrips"] == 12.0
        assert state["roundtrips_endpoint"] == "GET /teacher/dashboard"

    def test_the_commit_under_judgement_is_named_with_its_subject(self, tmp_path):
        state = reading(tmp_path, record(), repo=ROOT)
        assert state["commit"] == JUDGED
        assert state["short"] == "88829c9"
        assert state["subject"], (
            "the sha is what a reader copies; the subject is what they recognise")

    def test_a_commit_that_is_not_a_sha_is_dropped_rather_than_shown(self, tmp_path):
        state = reading(tmp_path, record(commit="not-a-sha"))
        assert state["commit"] is None and state["short"] is None
        assert state["verdict_key"] == "regressed", (
            "an unreadable commit must not cost the reading that is still good")

    def test_the_reasons_are_the_gate_s_own_lines(self, tmp_path):
        reasons = [
            "slowest page p50 812 ms against 480 ms on 366abdf (1.69x, allowed 1.50x) "
            "— worst endpoint(s): GET /teacher/results",
            "heaviest page sends 240.3 KB against 224.6 KB on 366abdf (1.07x, allowed "
            "1.05x) — GET /student/exams",
        ]
        state = reading(tmp_path, record(reasons=reasons))
        assert state["reasons"] == reasons, (
            "these lines are the whole answer to \"what got slower\"; paraphrasing "
            "them would drop the numbers")
        assert state["reasons_total"] == 2

    def test_a_wall_of_reasons_is_bounded_but_still_counted(self, tmp_path):
        many = [f"reason {i}" for i in range(40)]
        state = reading(tmp_path, record(reasons=many))
        assert len(state["reasons"]) == status.PERF_REASONS_SHOWN
        assert state["reasons_total"] == 40, (
            "a truncated list that does not say it was truncated reads as the whole "
            "finding")

    def test_the_baseline_it_was_measured_against_is_named(self, tmp_path):
        state = reading(tmp_path, record())
        assert state["baseline"]["present"] is True
        assert state["baseline"]["commit"] == BASELINE_SHA
        assert state["baseline"]["short"] == "366abdf"
        assert state["baseline"]["p50_ms"] == 480.0
        assert state["baseline"]["p95_ms"] == 900.0

    def test_a_missing_baseline_is_not_invented(self, tmp_path):
        state = status.perf_state(history(tmp_path, record()), repo=tmp_path,
                                  baseline_path=tmp_path / "absent.json")
        assert state["baseline"]["present"] is False
        assert state["baseline"]["p50_ms"] is None

    def test_a_baseline_that_cannot_be_parsed_is_not_invented(self, tmp_path):
        bad = tmp_path / "baseline.json"
        bad.write_text("{not json", encoding="utf-8")
        state = status.perf_state(history(tmp_path, record()), repo=tmp_path,
                                  baseline_path=bad)
        assert state["baseline"]["present"] is False
        assert state["baseline"]["key"] == status.PERF_MALFORMED

    def test_the_age_of_the_reading_is_stated(self, tmp_path):
        one = record(measured_at="2026-09-25T12:16:00+00:00")
        state = reading(tmp_path, one,
                        now=status._dt.datetime(2026, 9, 25, 13, 0, 0,
                                                tzinfo=status._dt.timezone.utc))
        assert state["age_seconds"] == 2640

    def test_a_record_with_no_latency_at_all_is_not_a_zero(self, tmp_path):
        """Two ways to have no latency, because they exercise different lines.

        The first record has no `latency` block at all, so the reader never looks
        inside one. The second has the block with nothing in it, which is the case
        the arithmetic runs on — and a test with only the first would pass while the
        page reported an unmeasured release as 0 ms, which is the most flattering
        possible reading of a release nobody measured.
        """
        for thin in ({"commit": JUDGED, "measured_at": "2026-09-25T12:16:00+00:00",
                      "verdict": "pass"},
                     {"commit": JUDGED, "measured_at": "2026-09-25T12:16:00+00:00",
                      "verdict": "pass",
                      "latency": {"p50_ms": None, "p95_ms": None,
                                  "endpoints": [], "samples": None}}):
            state = reading(tmp_path, thin)
            assert state["p50_ms"] is None, (
                "a release that reported nothing must not read as a release that was "
                f"fast: {thin}")
            assert state["p95_ms"] is None, thin


# ── the file is append-only and grows for the life of the box ────────────────

class TestTheFileGrows:
    def test_a_long_history_still_yields_the_last_judgement(self, tmp_path):
        lines = [json.dumps(record(verdict="pass", reasons=None,
                                   measured_at=f"2026-09-{d:02d}T00:00:00+00:00"))
                 for d in range(1, 29)]
        lines.append(json.dumps(record(verdict="regressed")))
        path = tmp_path / "history.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        state = status.perf_state(path, repo=tmp_path,
                                  baseline_path=baseline_file(tmp_path))
        assert state["verdict_key"] == "regressed"
        assert state["measured_at"] == "2026-09-25T12:16:00+00:00"

    def test_a_history_far_larger_than_the_read_window_still_finds_the_last_line(
            self, tmp_path):
        """The window is what keeps a page render from reading a growing file.

        The line is padded, not the count raised, because the failure mode is a
        single judgement line larger than the tail this reads — a release whose
        reasons list is enormous — and that must not read as a *previous* release's
        pass, which would tell the operator the refused release passed.
        """
        path = tmp_path / "history.jsonl"
        padded = record()
        padded["pad"] = "x" * (status.PERF_TAIL_BYTES + 4096)
        path.write_text(json.dumps(record(verdict="pass", reasons=None)) + "\n"
                        + json.dumps(padded, sort_keys=True) + "\n", encoding="utf-8")
        state = status.perf_state(path, repo=tmp_path,
                                  baseline_path=baseline_file(tmp_path))
        assert state["key"] in (status.PERF_PRESENT, status.PERF_MALFORMED)
        assert state["verdict_key"] != "pass", (
            "falling back to an older line would report a release that passed as the "
            "one that was refused")


# ── what the report and the page make of it ──────────────────────────────────

def render_status(app, report) -> str:
    """The template with a given report, for the tests that read its copy."""
    from flask import g, render_template
    with app.test_request_context("/super-admin/deploy-status"):
        g.user_id = "a-super-admin"
        g.user_role = "super_admin"
        g.user_name = "Tester"
        g.user_email = "t@t"
        g.tz_offset = 7
        return render_template("super_admin/deploy_status.html", status=report,
                               alerts={"interval_seconds": 21600}, testalert=None,
                               released=None)


def template_verdict_keys() -> set[str]:
    """The verdicts the page has a sentence for in either language."""
    return set(re.findall(r"p\.verdict_key == '([a-z0-9_]+)'",
                          TEMPLATE.read_text(encoding="utf-8")))


class TestWhatItAddsUpTo:
    def test_the_report_carries_the_reading_and_its_path(self, tmp_path):
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               perf_history_file=str(tmp_path / "absent.jsonl"))
        assert report["perf"]["key"] == status.PERF_NONE
        assert report["perf_file"].endswith("absent.jsonl")

    def test_the_report_reads_the_perf_history_it_is_given(self, tmp_path):
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               perf_history_file=str(history(tmp_path, record())),
                               perf_baseline_file=str(baseline_file(tmp_path)))
        assert report["perf"]["verdict_key"] == "regressed"
        assert report["perf"]["reasons"], "the reasons did not reach the page's data"


class TestThePageKeepsTheRefusal:
    """The card has to carry two judgements at once, and say which is which."""

    HELD = TestTheRefusedReleaseIsKept.HELD
    LATER = TestTheRefusedReleaseIsKept.LATER

    def report(self, tmp_path, *records):
        return status.report(repo=str(tmp_path), runner="/nonexistent",
                             snapshot_runner="/nonexistent",
                             pause_file=str(tmp_path / "no-pause"),
                             quarantine_file=str(quarantine_file(tmp_path, self.HELD)),
                             perf_history_file=str(history(tmp_path, *records)),
                             perf_baseline_file=str(baseline_file(tmp_path)))

    def test_the_refused_numbers_are_still_on_the_page_after_a_later_pass(
            self, app, tmp_path):
        html = render_status(app, self.report(
            tmp_path,
            record(commit=self.HELD, verdict="regressed", p50_ms=812.0),
            record(commit=self.LATER, verdict="pass", p50_ms=300.0,
                   measured_at="2026-09-26T04:00:00+00:00")))

        assert "The Refused Release’s Own Numbers" in html
        assert "812" in html, "the refused release's measurement is still missing"
        assert self.HELD[:7] in html
        assert "300" in html, "the later release is still the headline"

    def test_it_is_not_rendered_twice_while_the_refusal_is_the_latest(self, app, tmp_path):
        html = render_status(app, self.report(
            tmp_path, record(commit=self.HELD, verdict="regressed")))

        assert "The Refused Release’s Own Numbers" not in html, (
            "the same judgement was rendered twice, as the latest and as the held")

    def test_a_held_commit_this_gate_never_judged_names_the_other_card(self, app, tmp_path):
        html = render_status(app, self.report(
            tmp_path, record(commit=self.LATER, verdict="pass")))

        assert "no judgement from the performance gate" in html, (
            "a commit another gate refused was shown as one this gate never saw fit "
            "to measure, with no way to tell that from a missing lookup")

    def test_a_judgement_older_than_the_window_says_so_rather_than_never(self, app, tmp_path):
        filler = [record(commit="%040x" % n, verdict="pass", p50_ms=200.0)
                  for n in range(200)]
        html = render_status(app, self.report(
            tmp_path, record(commit=self.HELD, verdict="regressed"), *filler))

        assert "older than the history window" in html, (
            "'the gate never judged it' and 'this page did not read far enough' send "
            "an operator to two different places")


class TestThePageNamesTheMeasurement:
    def test_every_verdict_has_a_sentence_in_both_languages(self):
        assert template_verdict_keys() == status.PERF_VERDICTS, (
            f"missing from the page: {sorted(status.PERF_VERDICTS - template_verdict_keys())}; "
            f"invented by the page: {sorted(template_verdict_keys() - status.PERF_VERDICTS)}")

    def test_the_reasons_and_the_numbers_reach_the_page(self, app, tmp_path):
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               perf_history_file=str(history(tmp_path, record(
                                   reasons=["slowest page p50 812 ms against 480 ms on "
                                            "366abdf (1.69x, allowed 1.50x) — worst "
                                            "endpoint(s): GET /teacher/results"]))),
                               perf_baseline_file=str(baseline_file(tmp_path)))
        html = render_status(app, report)
        assert "slowest page p50 812 ms against 480 ms on 366abdf (1.69x, allowed " \
               "1.50x) — worst endpoint(s): GET /teacher/results" in html, (
            "the gate's own line is the evidence; a test that only greps the heading "
            "would pass on a page that throws the reading away")
        assert "88829c9" in html
        assert "812" in html and "1900" in html, "the measured numbers are not shown"
        assert "366abdf" in html, "the baseline it was compared against is not named"

    @pytest.mark.parametrize("verdict, sentence", [
        ("regressed", "the release before it is what is still serving"),
        ("pass", "is not worse than the release it was compared against"),
        ("baseline", "the first release measured on this box"),
        ("unconfirmed", "the second did not confirm it"),
    ])
    def test_each_verdict_renders_the_sentence_for_that_verdict(
            self, app, tmp_path, verdict, sentence):
        """A guard on the *branch*, not on the word.

        Grepping that the template compares `p.verdict_key` to each verdict proves
        the page has not lost a branch; it cannot notice a branch that kept its
        condition and lost its sentence — a refusal then renders in the words of a
        pass, which is worse than rendering nothing.
        """
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               perf_history_file=str(history(tmp_path, record(
                                   verdict=verdict, reasons=None))),
                               perf_baseline_file=str(baseline_file(tmp_path)))
        html = render_status(app, report)
        assert sentence in html, f"{verdict!r} rendered without its own sentence"

    def test_a_box_with_no_judgement_yet_says_so_rather_than_showing_a_blank(
            self, app, tmp_path):
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               perf_history_file=str(tmp_path / "absent.jsonl"))
        html = render_status(app, report)
        assert "Performance Gate" in html, "the card did not render"
        assert "no judgement" in html.lower(), (
            "the cold state has to be a sentence, not an empty box")

    def test_an_unreadable_history_is_not_rendered_as_a_clean_bill(self, app, tmp_path):
        """The card's *own* sentence, not any sentence on the page.

        "cannot" and "could not" appear in three other cards — that is how the first
        version of this test passed while the perf card rendered an unreadable record
        as a box that had simply never judged anything, which is the reading that
        gets an operator to stop looking.
        """
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               perf_history_file=str(tmp_path))
        html = render_status(app, report)
        assert "this page cannot say whether the last release passed" in html, (
            "an unreadable record must be reported, not shown as nothing to report")
        assert "No judgement has been recorded at this path yet" not in html, (
            "the cold-state sentence is the wrong story about a record it could not read")


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
