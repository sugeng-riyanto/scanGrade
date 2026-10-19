"""The box report is the half of a diagnosis that gets pasted, so it must not lie by omission.

The three readings it collects have each been the thing that was missing from a
conversation about this box: the journal that nobody opened, the arm check that was
reported as done while the box ran a copy, and the served commit that was four
commits behind while every page answered. So the properties tested here are the
ones that make pasting it safe:

* a `/health` sample is reported **per worker**, because the value is frozen at
  import and one request names whichever worker answered;
* an input that could not be read is a line under `not measured` -- never an empty
  section that reads as "nothing wrong";
* "not there" and "there but unreadable" stay different answers, for the release
  record and for the checkout;
* the block is ASCII and every line is clipped, because it is pasted into a chat
  and a curly quote from a commit subject has already broken a report here once;
* the exit code says which of the three states the run was in (complete, gaps
  named, could not measure) rather than borrowing a gate's.

Everything external is injected: a command runner and an HTTP opener. That is not
only for speed -- the fact being tested is how the tool *reads* git's refusal, and
a real checkout cannot be made to belong to another user from a test.
"""
from __future__ import annotations

import collections
import datetime as _dt
import importlib.util
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("box_report", ROOT / "deploy" / "box_report.py")
box = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(box)

NOW = _dt.datetime(2026, 10, 19, 9, 12, 4, tzinfo=_dt.timezone.utc)


def health_body(commit="17665b2", loaded_at="2026-10-17T13:07:14Z", workers="3",
                reason=None, readers=None):
    body = {
        "status": "ok", "workers": workers, "uptime_ms": 100_000_000,
        "commit": {"available": commit is not None, "commit": commit,
                   "full_commit": (commit or "") and (commit * 6)[:40],
                   "reason_key": reason, "loaded_at": loaded_at},
    }
    if readers is not None:
        body["commit_readers"] = readers
    return json.dumps(body)


def mixed_readers(seen=3, blind=1, commits=None, **overrides):
    """What `/health` publishes when the box's workers do not agree."""
    reading = {"key": "mixed", "seen": seen, "blind": blind,
               "commits": {"17665b2": seen - blind} if commits is None else commits,
               "reasons": {"unreadable": blind} if blind else {},
               "skipped": 0, "expected": seen, "unregistered": 0, "detail": None}
    reading.update(overrides)
    return reading


def opener_for(bodies):
    """An opener that answers with the given bodies, then repeats the last one."""
    queue = list(bodies)

    def open_(url, timeout=5):
        body = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(body, Exception):
            raise body
        if isinstance(body, tuple):
            return body
        return 200, body

    return open_


def git_args(argv):
    return argv[argv.index("-C") + 2:]


def fake_run(rules, seen=None):
    """`rules` is a list of (predicate, (rc, stdout, stderr)); first match wins."""
    def run(argv, env=None, timeout=10):
        if seen is not None:
            seen.append((list(argv), dict(env or {})))
        for predicate, result in rules:
            if predicate(list(argv)):
                return result
        return 0, "", ""
    return run


def checkout_run(head="a" * 40, origin="bbbbbbb", behind="49", ahead="0",
                 refusal=True, seen=None):
    def is_git(argv):
        return "-C" in argv

    def exempt(argv):
        return "safe.directory" in " ".join(argv)

    def args(argv):
        return " ".join(git_args(argv))

    rules = [
        (lambda a: is_git(a) and not exempt(a) and args(a) == "rev-parse HEAD",
         (128, "", "fatal: detected dubious ownership in repository at '/opt/x'")
         if refusal else (0, head, "")),
        (lambda a: is_git(a) and args(a) == "rev-parse HEAD", (0, head, "")),
        (lambda a: is_git(a) and args(a) == "log -1 --format=%s", (0, "the subject", "")),
        (lambda a: is_git(a) and args(a) == "rev-parse --abbrev-ref HEAD", (0, "main", "")),
        (lambda a: is_git(a) and args(a) == "rev-parse --short origin/main",
         (0, origin, "")),
        (lambda a: is_git(a) and args(a) == "rev-list --count HEAD..origin/main",
         (0, behind, "")),
        (lambda a: is_git(a) and args(a) == "rev-list --count origin/main..HEAD",
         (0, ahead, "")),
    ]
    return fake_run(rules, seen=seen)


# ── /health, per worker ──────────────────────────────────────────────────────

class TestTheServedCommitIsSampledPerWorker:
    def test_a_blind_worker_is_counted_rather_than_absorbed(self):
        opening = opener_for([
            health_body("17665b2"), health_body("17665b2"),
            health_body(None, reason="unreadable"),
        ])
        snap = box.read_health("http://127.0.0.1:8000", samples=3, opener=opening)
        assert snap["available"] is True
        assert snap["answered"] == 3 and snap["blind"] == 1
        assert dict(snap["served"]) == {"17665b2": 2}
        assert dict(snap["reasons"]) == {"unreadable": 1}
        assert snap["workers"] == "3"

    def test_two_commits_in_one_second_are_reported_as_two(self):
        """The reload landed in some workers: a single sample would name one of
        them and call the box healthy."""
        opening = opener_for([health_body("17665b2"), health_body("8abe6fc")])
        snap = box.read_health("http://127.0.0.1:8000", samples=4, opener=opening)
        assert set(snap["served"]) == {"17665b2", "8abe6fc"}
        rendered = box.render(_report(health=snap))
        assert "WARNING: the workers report different commits" in rendered

    def test_an_app_that_does_not_answer_is_a_reason_not_a_crash(self):
        opening = opener_for([ConnectionError("refused")])
        snap = box.read_health("http://127.0.0.1:8000", samples=3, opener=opening)
        assert snap["available"] is False
        assert snap["answered"] == 0
        assert "ConnectionError x3" in snap["detail"]
        assert "http://127.0.0.1:8000/health" in snap["detail"]

    def test_a_body_that_is_not_json_is_not_a_reading(self):
        opening = opener_for([(200, "<html>502</html>")])
        snap = box.read_health("http://127.0.0.1:8000", samples=2, opener=opening)
        assert snap["available"] is False
        assert snap["errors"]["not_json"] == 2

    def test_the_apps_own_count_of_every_worker_is_kept(self):
        """The block is the app's count of the box, not a sample of it, so one
        answer is enough — and it is kept beside the tallies rather than inside
        them, because the two count different things."""
        opening = opener_for([
            health_body(),
            health_body(commit="17665b2", readers=mixed_readers()),
        ])
        snap = box.read_health("http://127.0.0.1:8000", samples=2, opener=opening)
        assert snap["readers"]["key"] == "mixed"
        assert snap["readers"]["blind"] == 1
        assert snap["blind"] == 0, "samples and workers were averaged together"

    def test_an_app_from_before_the_block_reads_as_no_block(self):
        opening = opener_for([health_body()])
        snap = box.read_health("http://127.0.0.1:8000", samples=2, opener=opening)
        assert snap["readers"] is None

    def test_the_loaded_at_stamps_are_kept_apart(self):
        opening = opener_for([
            health_body(loaded_at="2026-10-17T13:07:14Z"),
            health_body(loaded_at="2026-10-17T13:07:15Z"),
        ])
        snap = box.read_health("http://127.0.0.1:8000", samples=2, opener=opening)
        assert set(snap["loaded_at"]) == {"2026-10-17T13:07:14Z",
                                          "2026-10-17T13:07:15Z"}


# ── the checkout ─────────────────────────────────────────────────────────────

class TestTheCheckoutSaysWhyItCannotBeRead:
    def test_an_ownership_refusal_is_read_with_the_exemption_this_process_passes(
            self, tmp_path):
        repo = tmp_path / "checkout"
        (repo / ".git").mkdir(parents=True)
        seen = []
        snap = box.read_checkout(repo, run=checkout_run(seen=seen), git="/usr/bin/git")
        assert snap["git_state"] == "readable"
        assert snap["exemption"] == "required"
        assert snap["head"] == "a" * 40
        assert snap["behind"] == 49 and snap["ahead"] == 0
        exempt = [argv for argv, _env in seen if "safe.directory" in " ".join(argv)]
        assert exempt, "the probe that produced the reading carried no exemption"
        assert all(argv[1] == "--no-optional-locks" for argv in exempt)
        assert "safe.directory=%s" % os.path.realpath(str(repo)) in exempt[0]

    def test_a_checkout_that_refuses_even_exempted_is_a_broken_tree(self, tmp_path):
        repo = tmp_path / "checkout"
        (repo / ".git").mkdir(parents=True)
        run = fake_run([
            (lambda a: "-C" in a, (128, "", "fatal: detected dubious ownership in "
                                            "repository at '/x'\nnot a git repository")),
        ])
        snap = box.read_checkout(repo, run=run, git="/usr/bin/git")
        assert snap["git_state"] == "unreadable"
        assert snap["exemption"] == "insufficient"
        assert "not a git repository" in snap["detail"]

    def test_a_directory_that_is_not_a_checkout_is_its_own_answer(self, tmp_path):
        snap = box.read_checkout(tmp_path, run=checkout_run(), git="/usr/bin/git")
        assert snap["git_state"] == "not_a_checkout"
        assert snap["exemption"] == "unknown"

    def test_no_git_is_not_an_empty_checkout(self, tmp_path):
        (tmp_path / ".git").mkdir()
        snap = box.read_checkout(tmp_path, run=checkout_run(), git="")
        assert snap["git_state"] == "no_git"
        assert snap["head"] is None

    def test_an_origin_that_is_not_there_is_not_zero_behind(self, tmp_path):
        repo = tmp_path / "checkout"
        (repo / ".git").mkdir(parents=True)
        run = fake_run([
            (lambda a: "-C" in a and git_args(a) == ["rev-parse", "HEAD"],
             (0, "a" * 40, "")),
            (lambda a: "-C" in a and git_args(a) == ["rev-parse", "--abbrev-ref", "HEAD"],
             (0, "main", "")),
            (lambda a: "-C" in a and git_args(a)[:2] == ["rev-parse", "--short"],
             (128, "", "unknown revision")),
        ])
        snap = box.read_checkout(repo, run=run, git="/usr/bin/git")
        assert snap["origin"] is None
        assert snap["behind"] is None, "unknown is not zero"


# ── the release record ───────────────────────────────────────────────────────

class TestTheLastReleaseRecord:
    def test_the_three_lines_the_runner_writes(self, tmp_path):
        path = tmp_path / "last-deploy"
        path.write_text("a" * 40 + "\n2026-10-17T13:06:52+00:00\n/snap/db.tar.gz\n",
                        encoding="utf-8")
        snap = box.read_last_release(path)
        assert snap["state"] == "recorded"
        assert snap["commit"] == "a" * 40
        assert snap["at"] == "2026-10-17T13:06:52+00:00"
        assert snap["restore"] == "/snap/db.tar.gz"

    def test_absent_is_its_own_state(self, tmp_path):
        assert box.read_last_release(tmp_path / "last-deploy")["state"] == "absent"

    def test_an_empty_record_is_not_a_record(self, tmp_path):
        path = tmp_path / "last-deploy"
        path.write_text("\n\n", encoding="utf-8")
        assert box.read_last_release(path)["state"] == "empty"

    def test_a_first_line_that_is_not_a_commit_is_malformed(self, tmp_path):
        path = tmp_path / "last-deploy"
        path.write_text("no write finished\n2026-10-17T13:06:52+00:00\n", encoding="utf-8")
        snap = box.read_last_release(path)
        assert snap["state"] == "malformed"
        assert snap["commit"] is None

    def test_unreadable_is_not_absence(self, tmp_path):
        """A directory where the record should be: present, unreadable, and the
        report must not call that 'no release is on record'."""
        snap = box.read_last_release(tmp_path)
        assert snap["state"] == "unreadable"
        assert snap["detail"]


# ── the journal ──────────────────────────────────────────────────────────────

class TestTheDeployJournal:
    def test_the_verdict_lines_are_lifted_out_of_the_tail(self):
        body = "\n".join([
            "2026-10-19T08:00:00+0000 host scangrade-deploy[1]: fetch ok",
            "2026-10-19T08:00:02+0000 host scangrade-deploy[1]: theme gate: OK",
            "2026-10-19T08:00:03+0000 host scangrade-deploy[1]: REFUSED - served commit",
            "2026-10-19T08:00:04+0000 host scangrade-deploy[1]: rolled back to 17665b2",
        ])
        run = fake_run([(lambda a: a[0] == "journalctl", (0, body, ""))])
        snap = box.read_journal("scangrade-deploy", since="1 hour ago", lines=20, run=run)
        assert snap["available"] is True and snap["entries"] == 4
        assert any("REFUSED" in line for line in snap["verdicts"])
        assert any("rolled back" in line for line in snap["verdicts"])
        assert len(snap["verdicts"]) == 2, "ordinary lines are not verdicts"

    def test_the_window_is_read_wide_and_printed_narrow(self):
        """The timer ticks every two minutes: a refusal an hour ago is far above a
        40-line tail, and the tail is not what the digest is for."""
        seen = []
        run = fake_run([(lambda a: a[0] == "journalctl", (0, "a line", ""))], seen=seen)
        snap = box.read_journal(lines=40, run=run)
        argv = seen[0][0]
        fetched = int(argv[argv.index("-n") + 1])
        assert fetched >= 2000, argv
        assert snap["tail"] == ["a line"]

    def test_a_journal_that_cannot_be_read_says_so(self):
        run = fake_run([(lambda a: a[0] == "journalctl",
                         (1, "", "No journal files were found."))])
        snap = box.read_journal(run=run)
        assert snap["available"] is False
        assert "No journal files were found." in snap["detail"]

    def test_a_missing_journalctl_names_the_tool(self):
        run = fake_run([(lambda a: a[0] == "journalctl",
                         (255, "", "FileNotFoundError: journalctl"))])
        snap = box.read_journal(run=run)
        assert snap["available"] is False
        assert "journalctl could not be run" in snap["detail"]

    def test_an_empty_window_is_measured_and_empty(self):
        run = fake_run([(lambda a: a[0] == "journalctl",
                         (0, "-- No entries --", ""))])
        snap = box.read_journal(run=run)
        assert snap["available"] is True and snap["entries"] == 0


# ── the arm check ────────────────────────────────────────────────────────────

class TestWhatIsArmed:
    def test_the_checkers_own_verdict_is_kept(self, tmp_path):
        repo = tmp_path / "checkout"
        (repo / "deploy").mkdir(parents=True)
        (repo / "deploy" / "arm-auto-deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        run = fake_run([(lambda a: a[0] == "bash",
                         (1, "   launcher   : copy\nNOT ARMED - see the lines above", ""))])
        snap = box.read_arm(repo, run=run)
        assert snap["available"] is True
        assert snap["verdict"] == "NOT ARMED"
        assert any("launcher" in line for line in snap["lines"])

    def test_an_armed_box_reads_armed(self, tmp_path):
        repo = tmp_path / "checkout"
        (repo / "deploy").mkdir(parents=True)
        (repo / "deploy" / "arm-auto-deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        seen = []
        run = fake_run([(lambda a: a[0] == "bash", (0, "ARMED - every gate has what it needs.", ""))],
                       seen=seen)
        snap = box.read_arm(repo, run=run)
        assert snap["verdict"] == "ARMED"
        assert seen[0][1] == {"SG_REPO": str(repo)}, (
            "the checker must be pointed at the checkout being reported on")

    def test_a_missing_checker_is_named_rather_than_skipped(self, tmp_path):
        snap = box.read_arm(tmp_path, run=fake_run([]))
        assert snap["available"] is False
        assert "arm-auto-deploy.sh" in snap["detail"]

    def test_a_checker_that_never_ran_is_not_an_unarmed_box(self, tmp_path):
        """Exit 255 is `_default_run`'s "this command could not start" -- no bash,
        no permission. Reading it as unarmed sends the operator to arm a box whose
        checker is the thing that is missing."""
        repo = tmp_path / "checkout"
        (repo / "deploy").mkdir(parents=True)
        (repo / "deploy" / "arm-auto-deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        snap = box.read_arm(repo, run=fake_run([(lambda a: a[0] == "bash",
                                                 (255, "", "bash: not found"))]))
        assert snap["available"] is False
        assert "could not run" in snap["detail"]
        assert snap["verdict"] is None


# ── the block ────────────────────────────────────────────────────────────────

def _report(**overrides):
    report = {
        "when": NOW, "host": "srv", "kernel": "Linux 6.8", "repo": "/opt/scangrade",
        "health": {"available": True, "url": "http://127.0.0.1:8000/health",
                   "samples": 3, "answered": 3, "blind": 0,
                   "served": {"17665b2": 3}, "reasons": {},
                   "loaded_at": {"2026-10-17T13:07:14Z": 3}, "errors": {},
                   "workers": "3", "uptime_ms": 100_000_000, "detail": None},
        "checkout": {"path": "/opt/scangrade", "git_state": "readable", "detail": None,
                     "exemption": "not required", "head": "a" * 40,
                     "subject": "the subject", "branch": "main", "origin": "bbbbbbb",
                     "behind": 49, "ahead": 0},
        "placement": {"state": "same", "distance": 0},
        "release": {"path": "/var/lib/scangrade-deploy/last-deploy",
                    "state": "recorded", "commit": "a" * 40,
                    "at": "2026-10-17T13:06:52+00:00", "restore": None,
                    "detail": None},
        "journal": {"available": True, "unit": "scangrade-deploy", "since": "2 hours ago",
                    "entries": 2, "tail": ["line one", "line two"],
                    "verdicts": ["line two"], "detail": None},
        "arm": {"available": True, "script": "/opt/scangrade/deploy/arm-auto-deploy.sh",
                "rc": 0, "verdict": "ARMED", "lines": ["   launcher : execs the checkout"],
                "detail": None},
    }
    report.update(overrides)
    return report


class TestWhereTheServedCommitSits:
    def test_the_same_commit_needs_no_git_call(self):
        seen = []
        placement = box.place_served("17665b2", "/repo", "17665b2" + "d" * 33,
                                     run=fake_run([], seen=seen), git="/usr/bin/git")
        assert placement == {"state": "same", "distance": 0}
        assert seen == [], "a matching commit was checked against the graph anyway"

    def test_a_commit_in_this_history_is_the_reload_that_did_not_take(self):
        seen = []
        run = fake_run([(lambda a: "-C" in a and "rev-list --count" in " ".join(a),
                         (0, "49", ""))], seen=seen)
        placement = box.place_served("17665b2", "/repo", "8abe6fc" + "c" * 33,
                                     run=run, git="/usr/bin/git")
        assert placement == {"state": "behind", "distance": 49}
        assert any("17665b2..HEAD" in " ".join(argv) for argv, _env in seen)

    def test_a_commit_the_checkout_never_had_is_elsewhere(self):
        run = fake_run([(lambda a: "rev-list --count" in " ".join(a),
                         (128, "", "bad revision"))])
        placement = box.place_served("17665b2", "/repo", "8abe6fc" + "c" * 33,
                                     run=run, git="/usr/bin/git")
        assert placement == {"state": "elsewhere", "distance": None}

    def test_no_reading_is_unknown_not_same(self):
        assert box.place_served(None, "/repo", "a" * 40) == {"state": "unknown",
                                                            "distance": None}


class TestTheBlockIsSafeToPaste:
    def test_it_is_ascii_and_every_line_is_clipped(self):
        report = _report()
        report["journal"]["tail"] = ["\u2014 a curly \u2019quote\u2026" + "x" * 400]
        rendered = box.render(report)
        rendered.encode("ascii"), "the block is not ASCII"
        assert "-- a curly 'quote..." in rendered, "the punctuation was not folded"
        assert all(len(line) <= 150 for line in rendered.splitlines())
        assert rendered.startswith("=== ScanGrade box report ===")
        assert rendered.endswith("=== end report ===")

    def test_it_prints_the_three_readings_and_the_record(self):
        rendered = box.render(_report())
        for needle in ("served : 17665b2 x3", "loaded : 2026-10-17T13:07:14Z",
                       "HEAD   : aaaaaaaaa", "behind: 49", "verdict: ARMED",
                       "commit : " + "a" * 40, "entries: 2"):
            assert needle in rendered, needle

    def test_the_apps_own_count_is_printed_when_the_app_publishes_it(self):
        report = _report()
        report["health"]["readers"] = mixed_readers()
        rendered = box.render(report)
        assert ("readers: mixed -- 1 of 3 worker(s) cannot name their own commit "
                "(unreadable x1)") in rendered

    def test_two_commits_in_the_apps_own_count_are_named_too(self):
        report = _report()
        report["health"]["readers"] = mixed_readers(
            blind=0, commits={"17665b2": 2, "8abe6fc": 1})
        rendered = box.render(report)
        assert "the workers named 2 different commits (17665b2 x2, 8abe6fc x1)" in rendered

    def test_a_mixed_box_is_a_gap_and_not_a_clean_exit(self):
        """Exit 0 says the box was read and nothing was wrong. One worker serving a
        different commit — or unable to say what it serves — is not that."""
        report = _report()
        report["health"]["readers"] = mixed_readers()
        rendered = box.render(report)
        assert "* served commit: mixed --" in rendered
        assert box.exit_code_of(report) == 1

    def test_an_app_that_publishes_no_count_is_not_charged_with_one(self):
        """A release from before the block answers `/health` without it, and a
        report that invented a finding there would refuse a box for being old."""
        report = _report()
        report["health"]["readers"] = None
        rendered = box.render(report)
        assert "readers:" not in rendered
        assert box.exit_code_of(report) == 0

    def test_an_unmeasured_count_is_named_rather_than_omitted(self):
        report = _report()
        report["health"]["readers"] = mixed_readers(
            seen=None, blind=None, commits={}, key="unmeasured",
            detail="no live worker has recorded a reading")
        rendered = box.render(report)
        assert "readers: NOT MEASURED (no live worker has recorded a reading)" in rendered
        assert box.exit_code_of(report) == 0, (
            "an unmeasured count is not a finding about the release")

    def test_a_blind_worker_is_a_warning_not_a_silent_count(self):
        report = _report()
        report["health"].update({"blind": 4, "answered": 15, "samples": 15,
                                 "served": {"17665b2": 11},
                                 "reasons": {"unreadable": 4}})
        rendered = box.render(report)
        assert "blind  : unreadable x4" in rendered
        assert "some workers cannot name their own commit" in rendered

    def test_unmeasured_inputs_are_named_rather_than_omitted(self):
        report = _report()
        report["health"] = {**_report()["health"], "available": False,
                            "detail": "no answer from http://127.0.0.1:8000/health "
                                      "(ConnectionError x15)"}
        report["journal"] = {**_report()["journal"], "available": False,
                             "detail": "No journal files were found."}
        rendered = box.render(report)
        assert "-- not measured --" in rendered
        assert "served commit: no answer" in rendered
        assert "deploy journal: No journal files were found." in rendered
        assert "NOT MEASURED -- no answer" in rendered
        assert box.exit_code_of(report) == 1

    def test_a_complete_report_says_the_list_is_empty(self):
        assert "(none -- every input above was read)" in box.render(_report())

    def test_the_served_commit_is_placed_against_head(self):
        report = _report()
        report["checkout"] = {**_report()["checkout"], "head": "17665b2" + "d" * 33}
        report["release"] = {**_report()["release"], "commit": "17665b2" + "d" * 33}
        rendered = box.render(report)
        assert "served : 17665b2 matches HEAD" in rendered
        assert "the process holds the box's code" in rendered

    def test_a_release_that_never_loaded_is_the_reading_this_report_exists_for(self):
        report = _report()
        report["checkout"] = {**_report()["checkout"], "head": "8abe6fc" + "c" * 33}
        report["placement"] = {"state": "behind", "distance": 49}
        rendered = box.render(report)
        assert "served : 17665b2 is 49 commit(s) behind HEAD 8abe6fc" in rendered
        assert "a release that landed and was never loaded" in rendered

    def test_without_a_release_record_a_behind_reading_is_not_called_a_reload(self):
        """The same graph shape is what a report run against another box's checkout
        looks like, and only the runner's own record can tell them apart."""
        report = _report()
        report["checkout"] = {**_report()["checkout"], "head": "8abe6fc" + "c" * 33}
        report["placement"] = {"state": "behind", "distance": 49}
        report["release"] = {**_report()["release"], "state": "absent",
                            "commit": None, "at": None}
        rendered = box.render(report)
        assert "a release that landed and was never loaded" not in rendered
        assert "may be another box's checkout" in rendered

    def test_a_checkout_from_somewhere_else_is_not_called_a_failed_reload(self):
        """The same two shas mean a different thing when the report is run against
        another box's checkout, and calling that a failed reload would send the
        operator after a reload that was never due."""
        report = _report()
        report["placement"] = {"state": "elsewhere", "distance": None}
        rendered = box.render(report)
        assert "not in this checkout's history" in rendered
        assert "never loaded it" not in rendered

    def test_the_majority_is_the_box_and_not_the_last_worker(self):
        report = _report()
        report["health"].update({"served": {"17665b2": 8, "8abe6fc": 1}})
        assert box.majority_commit(report["health"]) == "17665b2"

    def test_a_checkout_that_moved_outside_the_runner_is_noted(self):
        report = _report()
        report["release"]["commit"] = "c" * 40
        rendered = box.render(report)
        assert "the tree moved outside the runner" in rendered


class TestTheJsonReading:
    """`--json` exists so two runs can be compared, which is a property of the bytes.

    A diff of two readings is only worth reading when the parts that did not change
    are byte-identical: an unchanged box must produce an unchanged file. That is
    what the sorted tallies and the fixed key order below are for.
    """

    def test_the_object_carries_every_section_and_the_verdict(self):
        payload = box.as_json(_report())
        for key in ("schema", "when", "host", "kernel", "repo", "exit", "gaps",
                    "health", "checkout", "placement", "release", "journal", "arm"):
            assert key in payload, key
        assert payload["schema"] == box.JSON_SCHEMA
        assert payload["exit"] == 0 and payload["gaps"] == []
        json.dumps(payload)  # raises if a Counter or a datetime was left in it

    def test_the_time_is_iso_utc_so_two_files_are_ordered(self):
        assert box.as_json(_report())["when"] == "2026-10-19T09:12:04Z"

    def test_two_runs_that_only_sampled_in_a_different_order_are_identical(self):
        """A Counter's order is the order the samples arrived. Left alone, two
        readings of an unchanged box would differ in every tally's key order and a
        diff would report the whole served-commit section as changed."""
        first = _report()
        first["health"]["served"] = collections.Counter({"17665b2": 3, "8abe6fc": 1})
        second = _report()
        second["health"]["served"] = collections.Counter()
        second["health"]["served"]["8abe6fc"] = 1
        second["health"]["served"]["17665b2"] = 3
        assert json.dumps(box.as_json(first)) == json.dumps(box.as_json(second))
        assert list(box.as_json(first)["health"]["served"]) == ["17665b2", "8abe6fc"]

    def test_what_could_not_be_measured_is_a_list_and_not_an_absence(self):
        report = _report()
        report["health"] = {**_report()["health"], "available": False,
                            "detail": "no answer"}
        report["journal"] = {**_report()["journal"], "available": False,
                             "detail": "denied"}
        payload = box.as_json(report)
        assert payload["exit"] == 1
        assert any(gap.startswith("served commit: no answer") for gap in payload["gaps"])
        assert "deploy journal: denied" in payload["gaps"]

    def test_the_json_keeps_the_characters_the_block_folds(self):
        """The block is ASCII because it is pasted; a machine can read the em dash,
        and a comparison should show words, not the folding."""
        report = _report()
        report["journal"]["tail"] = ["\u2014 a curly \u2019quote"]
        assert box.as_json(report)["journal"]["tail"] == ["\u2014 a curly \u2019quote"]
        json.dumps(box.as_json(report), ensure_ascii=True).encode("ascii")

    def test_json_mode_prints_one_object_and_keeps_the_exit_code(self, tmp_path, capsys):
        repo = tmp_path / "checkout"
        (repo / ".git").mkdir(parents=True)
        (repo / "deploy").mkdir()
        (repo / "deploy" / "arm-auto-deploy.sh").write_text("#!/bin/sh\n",
                                                             encoding="utf-8")
        code = box.main(
            ["--repo", str(repo), "--samples", "2", "--json",
             "--state-dir", str(tmp_path / "state")],
            run=checkout_run(), opener=opener_for([health_body(), health_body()]))
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert code == 0, out
        assert payload["exit"] == 0
        assert payload["health"]["served"] == {"17665b2": 2}
        assert payload["when"].endswith("Z")
        assert "=== ScanGrade box report ===" not in out, (
            "--json printed the block as well, so the file is not one object")

    def test_json_mode_still_says_could_not_measure_with_exit_two(self, tmp_path, capsys):
        code = box.main(
            ["--repo", str(tmp_path / "missing"), "--base", "http://127.0.0.1:9",
             "--samples", "1", "--json", "--state-dir", str(tmp_path / "state")],
            run=fake_run([(lambda a: a[0] == "journalctl", (1, "", "denied")),
                          (lambda a: a[0] == "bash", (2, "", "not a terminal"))]),
            opener=opener_for([ConnectionError("refused")]))
        payload = json.loads(capsys.readouterr().out)
        assert code == 2 and payload["exit"] == 2
        assert payload["gaps"], "a run that measured nothing reported no gap"


class TestTheExitCodes:
    def test_complete_is_zero(self):
        assert box.exit_code_of(_report()) == 0

    def test_a_named_gap_is_one(self):
        report = _report()
        report["arm"] = {**_report()["arm"], "available": False, "detail": "not here"}
        assert box.exit_code_of(report) == 1

    def test_a_checkout_that_is_not_there_says_which_fix_it_needs(self):
        report = _report()
        report["checkout"] = {**_report()["checkout"], "git_state": "not_a_checkout",
                              "detail": None}
        rendered = box.render(report)
        assert "checkout: there is no .git at that path" in rendered
        assert "None" not in rendered

    def test_nothing_measured_is_two(self):
        report = _report()
        report["health"] = {**_report()["health"], "available": False, "detail": "no"}
        report["checkout"] = {**_report()["checkout"], "git_state": "not_a_checkout"}
        assert box.exit_code_of(report) == 2

    def test_main_prints_the_block_and_returns_the_code(self, tmp_path, capsys):
        repo = tmp_path / "checkout"
        (repo / ".git").mkdir(parents=True)
        (repo / "deploy").mkdir()
        (repo / "deploy" / "arm-auto-deploy.sh").write_text("#!/bin/sh\n",
                                                             encoding="utf-8")
        code = box.main(
            ["--repo", str(repo), "--base", "http://127.0.0.1:8000",
             "--samples", "2", "--state-dir", str(tmp_path / "state")],
            run=checkout_run(), opener=opener_for([health_body(), health_body()]))
        out = capsys.readouterr().out
        assert code == 0, out
        assert "served : 17665b2 x2" in out
        assert "=== end report ===" in out

    def test_main_with_nothing_to_measure_is_two(self, tmp_path, capsys):
        code = box.main(
            ["--repo", str(tmp_path / "missing"), "--base", "http://127.0.0.1:9",
             "--samples", "1", "--state-dir", str(tmp_path / "state")],
            run=fake_run([(lambda a: a[0] == "journalctl", (1, "", "denied")),
                          (lambda a: a[0] == "bash", (2, "", "not a terminal"))]),
            opener=opener_for([ConnectionError("refused")]))
        assert code == 2
        assert "-- not measured --" in capsys.readouterr().out
