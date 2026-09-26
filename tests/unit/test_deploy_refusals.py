"""The refusal that outlives the release which replaced it.

A quarantine answers exactly one question — *what is held now* — and the runner
keeps exactly one record: the next refusal replaces it, and the release that lifts
the quarantine deletes it. Both are right for the tick and both are wrong for the
person reading the page a day later, because the gate's numbers leave the box at
the precise moment a later release has passed and somebody comes to ask why nothing
deployed. The perf card recovers the *held* commit's measurement from the gate's
history; the refusal itself — which commit, which gate, and the lines that gate
refused on — had no survivor at all.

So `deploy/scangrade-deploy.sh` also copies each refusal, byte for byte, into a
bounded directory, and this is the reader and the card for it.

What these tests hold:

* **The history is `none`, `present` or `unreadable`, and absent is not an error.**
  A box that has never had a refusal, or one whose runner predates the history, is
  not a box whose history could not be read — and `unreadable` must never be shown
  as `none`, which is the one wrong answer that looks like the right one.
* **The newest are read first, and only a few are rendered** while the count of all
  found travels with them, because the directory is a file tree an operator can put
  anything into.
* **A record that cannot be parsed is reported, not dropped** — a gap in the history
  is how "nothing was refused" starts reading as the truth.
* **The card renders the gate's own lines verbatim**, because those are the evidence,
  and every reading it can render has a sentence in both languages.
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

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
WHEN = "2026-09-26T05:00:00+00:00"
PERF_REASON = "perf gate (slower than the last release that passed)"
PERF_LINE = ("perf gate: REGRESSED — slowest page p50 812 ms against 480 ms on "
             "366abdf (1.69x, allowed 1.50x) — GET /teacher/results")


def record_text(sha: str, *, when: str = WHEN, reason: str = "theme gate (exit 13)",
                detail: tuple[str, ...] = ()) -> str:
    return "\n".join([sha, when, reason, *detail]) + "\n"


def history_dir(tmp_path: Path, entries: list[tuple[str, str]]) -> Path:
    """A refusal directory as the runner writes it: `refused-<epoch>-<sha7>` names."""
    directory = tmp_path / "refusals"
    directory.mkdir(exist_ok=True)
    for name, text in entries:
        (directory / (status.REFUSAL_PREFIX + name)).write_text(text, encoding="utf-8")
    return directory


# ── the reader ───────────────────────────────────────────────────────────────

class TestTheReader:
    def test_an_absent_history_is_none_rather_than_an_error(self, tmp_path):
        state = status.refusal_history_state(tmp_path / "missing", tmp_path,
                                             now=_now())
        assert state["key"] == status.REFUSALS_NONE
        assert state["records"] == []
        assert state["total"] == 0

    def test_an_empty_directory_is_none_too(self, tmp_path):
        (tmp_path / "refusals").mkdir()
        state = status.refusal_history_state(tmp_path / "refusals", tmp_path,
                                             now=_now())
        assert state["key"] == status.REFUSALS_NONE
        assert state["total"] == 0

    def test_records_are_read_newest_first(self, tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000001-aaaaaaa", record_text(SHA_A, reason="theme gate (exit 13)")),
            ("1700000003-ccccccc", record_text(SHA_C, reason="perf gate (exit 10)")),
            ("1700000002-bbbbbbb", record_text(SHA_B, reason="smoke test (exit 2)")),
        ])
        state = status.refusal_history_state(directory, tmp_path, now=_now())
        assert state["key"] == status.REFUSALS_PRESENT
        assert [r["short"] for r in state["records"]] == ["ccccccc", "bbbbbbb", "aaaaaaa"]
        assert state["total"] == 3

    def test_only_the_newest_are_returned_but_all_found_are_counted(self, tmp_path):
        directory = history_dir(tmp_path, [
            (f"170000000{i}-{'%07x' % i}", record_text(f"{i:040x}"))
            for i in range(1, 6)
        ])
        state = status.refusal_history_state(directory, tmp_path, now=_now(), limit=2)
        assert state["total"] == 5, "the count is what exists, not what is shown"
        assert len(state["records"]) == 2

    def test_the_gate_s_own_lines_are_carried_verbatim(self, tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000001-aaaaaaa",
             record_text(SHA_A, reason=PERF_REASON, detail=(PERF_LINE,))),
        ])
        state = status.refusal_history_state(directory, tmp_path, now=_now())
        record = state["records"][0]
        assert record["gate"] == PERF_REASON
        assert record["reasons"] == [PERF_LINE]

    def test_a_malformed_record_is_reported_and_not_dropped(self, tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000001-junk", "this is not a refusal record\n"),
            ("1700000002-aaaaaaa", record_text(SHA_A)),
        ])
        state = status.refusal_history_state(directory, tmp_path, now=_now())
        assert state["total"] == 2, "a file this reader cannot parse is still a file"
        malformed = [r for r in state["records"] if r["reason_key"] == "malformed"]
        assert malformed and malformed[0]["held"] is False, (
            "a record that is not a refusal was read as one, so its sha-less first "
            "line would be rendered as a commit")
        assert any(r["held"] for r in state["records"]), (
            "one unparseable record hid the well-formed ones")

    def test_only_the_runners_own_names_are_read(self, tmp_path):
        directory = history_dir(tmp_path, [("1700000001-aaaaaaa", record_text(SHA_A))])
        (directory / "notes.txt").write_text("ignored\n", encoding="utf-8")
        (directory / "quarantined.bak").write_text(record_text(SHA_B), encoding="utf-8")
        state = status.refusal_history_state(directory, tmp_path, now=_now())
        assert state["total"] == 1, (
            "a stray file in the directory was counted as a refusal record")

    def test_the_age_travels_with_each_record(self, tmp_path):
        directory = history_dir(tmp_path, [("1700000001-aaaaaaa", record_text(SHA_A))])
        now = _now()
        state = status.refusal_history_state(directory, tmp_path, now=now)
        assert state["records"][0]["age_seconds"] is not None


# ── what the report and the page make of it ──────────────────────────────────

def _now():
    import datetime as dt
    return dt.datetime(2026, 9, 26, 6, 0, tzinfo=dt.timezone.utc)


def render_status(app, report) -> str:
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


class TestThePage:
    def report(self, tmp_path, directory):
        return status.report(repo=str(tmp_path), runner="/nonexistent",
                             snapshot_runner="/nonexistent",
                             pause_file=str(tmp_path / "no-pause"),
                             quarantine_file=str(tmp_path / "no-quarantine"),
                             refusals_dir=str(directory))

    def test_the_report_carries_the_history_and_its_path(self, tmp_path):
        directory = history_dir(tmp_path, [("1700000001-aaaaaaa", record_text(SHA_A))])
        report = self.report(tmp_path, directory)
        assert report["refusals"]["key"] == status.REFUSALS_PRESENT
        assert report["refusals_dir"].endswith("refusals")
        assert report["refusals"]["records"][0]["sha"] == SHA_A

    def test_the_card_lists_each_refusal_with_its_gate_and_lines(self, app, tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000003-ccccccc",
             record_text(SHA_C, reason=PERF_REASON, detail=(PERF_LINE,))),
            ("1700000002-bbbbbbb",
             record_text(SHA_B, reason="theme gate (exit 13)")),
        ])
        html = render_status(app, self.report(tmp_path, directory))
        assert "The Last Few Refusals" in html
        assert "ccccccc" in html and "bbbbbbb" in html, (
            "the history records did not reach the page")
        assert PERF_REASON in html
        assert PERF_LINE in html, (
            "the gate's numbers are the evidence and were not rendered")
        assert "theme gate (exit 13)" in html

    def test_the_newest_refusal_comes_before_the_older_one(self, app, tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000002-bbbbbbb", record_text(SHA_B)),
            ("1700000003-ccccccc", record_text(SHA_C)),
        ])
        html = render_status(app, self.report(tmp_path, directory))
        assert html.index("ccccccc") < html.index("bbbbbbb")

    def test_an_empty_history_says_so_rather_than_showing_a_blank(self, app, tmp_path):
        html = render_status(app, self.report(tmp_path, tmp_path / "missing"))
        assert "No refusal is on record yet" in html
        assert "Belum ada penolakan" in html

    def test_an_unreadable_history_is_not_shown_as_an_empty_one(self, app, tmp_path,
                                                                monkeypatch):
        """`unreadable` and `none` need different remedies and must not look alike."""
        def boom(self):
            raise OSError("permission denied")

        # Scoped so the raised `iterdir` cannot reach the rest of `report()`, which
        # reads the checkout and would fail for a reason this test is not about.
        with monkeypatch.context() as m:
            m.setattr(Path, "iterdir", boom)
            state = status.refusal_history_state(tmp_path / "refusals", tmp_path,
                                                 now=_now())
        assert state["key"] == status.REFUSALS_UNREADABLE

        monkeypatch.setattr(status, "refusal_history_state",
                            lambda *a, **k: state)
        html = render_status(app, self.report(tmp_path, tmp_path / "refusals"))
        assert "cannot read it" in html or "tidak bisa membacanya" in html
        assert "No refusal is on record yet" not in html

    def test_the_card_renders_both_languages(self, app, tmp_path):
        directory = history_dir(tmp_path, [("1700000001-aaaaaaa", record_text(SHA_A))])
        html = render_status(app, self.report(tmp_path, directory))
        assert html.count("t('") >= 8

    def test_the_card_names_the_history_directory(self, app, tmp_path):
        directory = history_dir(tmp_path, [("1700000001-aaaaaaa", record_text(SHA_A))])
        html = render_status(app, self.report(tmp_path, directory))
        assert str(directory) in html


# ── every quarantined commit's own numbers ───────────────────────────────────
#
# The refusal card shows the gate's *last* judgement and the held commit's in a
# block of its own. The older quarantined commits — the ones the lift kept — had
# only the runner's prose, while their measurement sat in the same history file and
# was reachable only by a shell. So each is looked up by its sha, and the four
# answers a single lookup gives are preserved per commit: present, never judged,
# older than the window, unreadable. A commit whose evidence is merely outside the
# read tail must not read as one the gate never judged.


def perf_record(sha: str, *, verdict: str = "regressed", p50: float = 812.0,
                p95: float = 1900.0,
                measured_at: str = "2026-09-25T12:16:00+00:00",
                reasons=None) -> dict:
    """One line of the perf gate's `history.jsonl`, as it writes it."""
    out = {
        "commit": sha, "measured_at": measured_at,
        "latency": {"p50_ms": p50, "p95_ms": p95,
                    "endpoints": ["GET /teacher/results"], "samples": 40},
        "page_cost": {"bytes": 240304.0, "bytes_endpoint": "GET /student/exams",
                      "roundtrips": 12.0,
                      "roundtrips_endpoint": "GET /teacher/dashboard"},
        "error_pct": 0.0, "server_errors_5xx": 0, "verdict": verdict,
    }
    if reasons is not None:
        out["reasons"] = list(reasons)
    return out


def perf_history(tmp_path: Path, *records: dict) -> Path:
    path = tmp_path / "perf-history.jsonl"
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records),
                    encoding="utf-8")
    return path


def quarantine_file(tmp_path: Path, sha: str, *, gate: str = PERF_REASON) -> Path:
    path = tmp_path / "quarantined"
    path.write_text("\n".join([sha, WHEN, gate]) + "\n", encoding="utf-8")
    return path


class TestEveryQuarantinedCommitHasItsNumbers:
    def test_the_lookup_reads_each_commit_from_one_history(self, tmp_path):
        path = perf_history(tmp_path,
                            perf_record(SHA_A, verdict="regressed", p50=812.0),
                            perf_record(SHA_B, verdict="pass", p50=640.0))
        found = status.perf_judgements(path, [SHA_A, SHA_B])
        assert set(found) == {SHA_A, SHA_B}
        assert found[SHA_A]["key"] == status.PERF_HELD_PRESENT
        assert found[SHA_A]["verdict_key"] == "regressed"
        assert found[SHA_A]["p50_ms"] == 812.0
        assert found[SHA_B]["verdict_key"] == "pass"
        assert found[SHA_B]["p50_ms"] == 640.0

    def test_a_commit_older_than_the_window_is_not_called_never_judged(self, tmp_path):
        padded = perf_record(SHA_B)
        padded["pad"] = "x" * (status.PERF_TAIL_BYTES + 4096)
        path = tmp_path / "perf-history.jsonl"
        path.write_text(json.dumps(perf_record(SHA_A)) + "\n"
                        + json.dumps(padded, sort_keys=True) + "\n",
                        encoding="utf-8")
        found = status.perf_judgements(path, [SHA_A])
        assert found[SHA_A]["key"] == status.PERF_HELD_OUT_OF_WINDOW, (
            "a lookup that ran out of window must not read as a gate that never "
            "judged — only the first is fixed by opening the file")

    def test_an_absent_history_is_no_judgement_not_an_error(self, tmp_path):
        found = status.perf_judgements(tmp_path / "missing.jsonl", [SHA_A])
        assert found[SHA_A]["key"] == status.PERF_HELD_NO_JUDGEMENT
        assert found[SHA_A]["p50_ms"] is None

    def test_only_valid_shas_are_looked_up(self, tmp_path):
        found = status.perf_judgements(tmp_path / "missing.jsonl",
                                       [None, "nope", SHA_A])
        assert set(found) == {SHA_A}

    def _report(self, tmp_path, directory, *records, quarantine=None):
        return status.report(
            repo=str(tmp_path), runner="/nonexistent",
            snapshot_runner="/nonexistent",
            pause_file=str(tmp_path / "no-pause"),
            quarantine_file=str(quarantine or tmp_path / "no-quarantine"),
            refusals_dir=str(directory),
            perf_history_file=str(perf_history(tmp_path, *records)),
            perf_baseline_file=str(tmp_path / "no-baseline"))

    def test_the_report_gives_each_refusal_its_own_judgement(self, tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000002-bbbbbbb", record_text(SHA_B, reason=PERF_REASON)),
            ("1700000001-aaaaaaa", record_text(SHA_A, reason=PERF_REASON)),
        ])
        report = self._report(tmp_path, directory,
                              perf_record(SHA_A, p50=812.0),
                              perf_record(SHA_B, verdict="pass", p50=640.0))
        by_sha = {record["sha"]: record for record in report["refusals"]["records"]}
        assert by_sha[SHA_A]["perf"]["key"] == status.PERF_HELD_PRESENT
        assert by_sha[SHA_A]["perf"]["p50_ms"] == 812.0
        assert by_sha[SHA_B]["perf"]["p50_ms"] == 640.0, (
            "the older refusal's measurement did not reach the page's data")

    def test_the_held_refusal_is_marked_current(self, tmp_path):
        directory = history_dir(tmp_path, [("1700000001-aaaaaaa", record_text(SHA_A))])
        report = self._report(tmp_path, directory, perf_record(SHA_A),
                              quarantine=quarantine_file(tmp_path, SHA_A))
        record = report["refusals"]["records"][0]
        assert record["is_current"] is True

    def test_a_refusal_that_is_not_held_is_not_current(self, tmp_path):
        directory = history_dir(tmp_path, [("1700000001-aaaaaaa", record_text(SHA_A))])
        report = self._report(tmp_path, directory, perf_record(SHA_A),
                              quarantine=quarantine_file(tmp_path, SHA_B))
        assert report["refusals"]["records"][0]["is_current"] is False


class TestThePageShowsEveryRefusalsNumbers:
    def report(self, tmp_path, directory, *, quarantine=None, history_path=None):
        return status.report(
            repo=str(tmp_path), runner="/nonexistent",
            snapshot_runner="/nonexistent",
            pause_file=str(tmp_path / "no-pause"),
            quarantine_file=str(quarantine or tmp_path / "no-quarantine"),
            refusals_dir=str(directory),
            perf_history_file=str(history_path or tmp_path / "no-perf.jsonl"),
            perf_baseline_file=str(tmp_path / "no-baseline"))

    def _two_refusals(self, tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000002-bbbbbbb", record_text(SHA_B, reason=PERF_REASON)),
            ("1700000001-aaaaaaa", record_text(SHA_A, reason=PERF_REASON)),
        ])
        history = perf_history(tmp_path,
                               perf_record(SHA_A, p50=812.0),
                               perf_record(SHA_B, verdict="pass", p50=640.0))
        return directory, history

    def test_an_older_refusals_numbers_are_on_the_page(self, app, tmp_path):
        directory, history = self._two_refusals(tmp_path)
        html = render_status(app, self.report(tmp_path, directory,
                                              history_path=history))
        assert "The performance gate’s own numbers for this commit" in html, (
            "the history card does not name the measurement it is showing")
        assert "812" in html, "the older refusal's measurement is missing"
        assert "640" in html

    def test_the_held_commit_is_not_printed_twice(self, app, tmp_path):
        directory, history = self._two_refusals(tmp_path)
        html = render_status(app, self.report(
            tmp_path, directory, history_path=history,
            quarantine=quarantine_file(tmp_path, SHA_A)))
        assert html.count("812") == 1, (
            "the held commit's numbers appear in both its own block and the history "
            "card — the page prints the same measurement twice")
        assert "640" in html

    def test_a_refusal_with_no_judgement_shows_no_numbers(self, app, tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000001-aaaaaaa", record_text(SHA_A, reason="theme gate (exit 13)"))])
        html = render_status(app, self.report(tmp_path, directory,
                                              history_path=tmp_path / "none.jsonl"))
        assert "The performance gate’s own numbers for this commit" not in html

    def test_a_perf_refusal_whose_judgement_is_out_of_window_says_so(self, app,
                                                                   tmp_path):
        directory = history_dir(tmp_path, [
            ("1700000001-aaaaaaa", record_text(SHA_A, reason=PERF_REASON))])
        padded = perf_record(SHA_B)
        padded["pad"] = "x" * (status.PERF_TAIL_BYTES + 4096)
        path = tmp_path / "perf-history.jsonl"
        path.write_text(json.dumps(padded, sort_keys=True) + "\n", encoding="utf-8")
        html = render_status(app, self.report(tmp_path, directory, history_path=path))
        assert "older than the history window" in html, (
            "a perf refusal whose numbers ran out of the read tail says nothing, so "
            "it reads as a gate that never judged it")


def test_the_page_branches_on_the_readings_that_are_not_the_ordinary_one():
    """`none` is the else; `present` and `unreadable` each need their own branch."""
    template = TEMPLATE.read_text(encoding="utf-8")
    for key in ("present", status.REFUSALS_UNREADABLE):
        assert re.search(rf"R\.key == '{key}'", template), (
            f"the template has no branch for the {key!r} reading, so that state would "
            "render as whatever the fallback happens to be")
