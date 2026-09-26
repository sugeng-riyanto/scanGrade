"""The five synthetic classes, held to the properties they exist to provide.

The seed is the foundation of two different things — the tests of every later
anti-cheat phase, and the data a dashboard is demonstrated on — so what it has to
get right is not "it writes rows". It is that each scenario *contains the thing its
name claims*:

* the ordinary class is ordinary, and carries one sitting older than the event
  pipeline so the NULL rule has a subject;
* the network incident is a **shared** disturbance, uniform across the class, which
  is why no per-student ranking can find it;
* the handset scenario is a per-student pattern with **no** shared minute, and the
  first two absences are forgiven while the rest are charged with no length at all;
* the tail is genuinely far out and does not move the class median;
* the small class is small enough that a baseline has to say so.

Every assertion here is checked against data measured by the app's own arithmetic
(`attempt_summary.summarize` / `baseline_rows`), never against a fixture of summary
numbers — a fixture like that would encode the answer the dashboard is supposed to
compute.

Two guards are about the module rather than the data: it may not re-implement the
summary or the percentile convention, and it may not be reachable from a request.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = ROOT / "deploy" / "anticheat_seed.py"


def _load_seed():
    """Import `deploy/anticheat_seed.py` without making `deploy/` a package."""
    spec = importlib.util.spec_from_file_location("anticheat_seed", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["anticheat_seed"] = module
    spec.loader.exec_module(module)
    return module


seed = _load_seed()

from app.services import anti_cheat_service as anti      # noqa: E402
from app.services import attempt_summary as A            # noqa: E402


def one(name: str):
    return seed.plan(name)


def metrics(name: str) -> dict:
    return {row["metric"]: row for row in seed.baseline_for(one(name))}


def summaries(name: str) -> list[dict]:
    return seed.summaries_for(one(name))


def by_student(name: str) -> dict:
    """The class's summaries by the key the plan knows them by."""
    return {s["student_id"].removeprefix("seed:"): s for s in summaries(name)}


def sessions(name: str):
    return one(name).sessions


#: The summary columns that are measurements rather than identity: the four id
#: columns are the database's own values, not something the plan can predict.
MEASURED_COLUMNS = tuple(c for c in A.SUMMARY_COLUMNS
                         if c not in ("attempt_id", "exam_id", "student_id", "school_id"))


def seq_gaps(session) -> int:
    """How many sequence numbers this sitting is missing."""
    numbers = sorted(int(row["seq"]) for row in session.events)
    return sum(1 for before, after in zip(numbers, numbers[1:]) if after != before + 1)


# ── 1. the five scenarios, and only the five ─────────────────────────────────

class TestTheScenariosAreTheOnesTheDashboardNeeds:
    def test_every_documented_scenario_exists(self):
        assert seed.SCENARIO_NAMES == (
            "normal", "network_incident", "phone_blur", "tail", "small_class")

    def test_the_sizes_are_the_documented_ones(self):
        sizes = {s.name: s.students for s in seed.SCENARIOS}
        assert sizes == {"normal": 30, "network_incident": 28, "phone_blur": 26,
                         "tail": 24, "small_class": 5}
        for scenario in seed.SCENARIOS:
            assert one(scenario.name).size == scenario.students, (
                f"{scenario.name} planned {one(scenario.name).size} sittings but "
                f"documents {scenario.students}")

    def test_the_small_class_is_below_the_apps_minimum(self):
        """Not a taste: below `MIN_BASELINE_N` the baseline has to say so, and a
        scenario one student too big would never exercise that branch."""
        assert one("small_class").size < A.MIN_BASELINE_N

    def test_every_scenario_explains_itself(self):
        for scenario in seed.SCENARIOS:
            assert scenario.why.strip(), f"{scenario.name} has no reason recorded"
            assert scenario.label and scenario.title

    def test_an_unknown_scenario_is_refused_loudly(self):
        with pytest.raises(ValueError) as caught:
            one("cheating_class")
        assert "unknown scenario" in str(caught.value)

    def test_only_the_incident_scenario_declares_a_window(self):
        """The window is data, not prose: it is what the "shared disturbance" rule
        is a statement about, and a scenario that has none must not claim one."""
        for plan_ in seed.plans():
            expected = plan_.scenario.name == "network_incident"
            assert (plan_.incident_window is not None) is expected


# ── 2. determinism ───────────────────────────────────────────────────────────

class TestTheDataIsReproducible:
    def test_the_same_seed_is_the_same_class(self):
        assert seed.plans() == seed.plans(), (
            "two runs of the same seed produced different data — a test asserting a "
            "distribution would be flaky by construction")

    def test_a_different_day_moves_the_sittings(self):
        later = seed.plans(day=seed.DEFAULT_DAY.replace(day=22))
        assert later != seed.plans()
        assert later[0].sessions[0].started_at.date() == seed.DEFAULT_DAY.replace(day=22)

    def test_a_different_seed_is_a_different_class(self):
        assert seed.plan("normal", seed=1) != seed.plan("normal", seed=2)

    def test_the_generator_is_not_seeded_from_hash(self):
        """`hash()` is salted per process, so a generator built on it reproduces on
        the machine that wrote it and nowhere else. Two things had to be corrected
        here: asserting only that `hashlib` is imported proved nothing, and a plain
        search for `hash(` reads the **prose** — this module's docstring explains the
        rule by naming the call it forbids. So the check is on the parsed tree."""
        source = SEED_PATH.read_text(encoding="utf-8")
        assert "hashlib.sha256" in source
        calls = [node for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "hash"]
        assert not calls, (
            "the seed calls the built-in `hash()`, which is salted per process: the "
            "data would reproduce on the machine that wrote it and nowhere else")
        assert not re.search(r"random\.Random\(\s*hash\(", source), (
            "the per-student generator is seeded from `hash()`")


# ── 3. synthetic means synthetic ─────────────────────────────────────────────

class TestNoRealStudentLooksLikeThis:
    def test_every_address_is_unmailable(self):
        for plan_ in seed.plans():
            for session in plan_.sessions:
                assert session.email.endswith(".invalid"), session.email
                assert session.email.endswith(seed.EMAIL_DOMAIN)

    def test_every_name_carries_the_marker(self):
        for plan_ in seed.plans():
            for session in plan_.sessions:
                assert seed.SEED_MARK in session.display_name
                assert seed.NAME_PREFIX in session.display_name

    def test_the_accounts_are_unique(self):
        emails = [s.email for plan_ in seed.plans() for s in plan_.sessions]
        assert len(emails) == len(set(emails))

    def test_the_marker_is_what_identifies_a_row(self):
        assert seed.is_seed_name(seed.plan("normal").class_name)
        assert seed.is_seed_name(seed.plan("normal").exam_title)
        assert not seed.is_seed_name("Ulangan Harian 7A")          # a teacher's paper
        assert not seed.is_seed_name(None)


# ── 3b. an event belongs to the sitting it describes ─────────────────────────
#
# The defect this exists for was invisible for exactly one reason: every other
# assertion here asks *what* the events say and never *when* they happen. Measured
# before this guard: the network incident's shared window sat at 09:40–09:44 while
# the class handed in at 08:28–08:34 — the flagship "shared disturbance" scenario
# described an outage about seventy minutes after everybody had gone home, and the
# suite was green because a minute the class shares is a minute the class shares,
# whether or not the exam is still running.

class TestAnEventBelongsToTheSittingItDescribes:
    def test_no_event_happens_after_the_paper_is_handed_in(self):
        for plan_ in seed.plans():
            for session in plan_.sessions:
                for row in session.events:
                    when = datetime.fromisoformat(row["server_at"])
                    assert when <= session.submitted_at, (
                        f"{session.student_key}: {row['kind']} at {when:%H:%M} is after "
                        f"the {session.submitted_at:%H:%M} it was handed in")

    def test_no_event_predates_the_sitting(self):
        for plan_ in seed.plans():
            for session in plan_.sessions:
                for row in session.events:
                    when = datetime.fromisoformat(row["server_at"])
                    assert when >= session.started_at, (
                        f"{session.student_key}: {row['kind']} at {when:%H:%M} is "
                        f"before the {session.started_at:%H:%M} it began")

    def test_no_charge_is_recorded_after_the_paper_is_handed_in(self):
        for plan_ in seed.plans():
            for session in plan_.sessions:
                for row in session.violations:
                    when = datetime.fromisoformat(row["created_at"])
                    assert when <= session.submitted_at, (
                        f"{session.student_key}: a {row['violation_type']} charged at "
                        f"{when:%H:%M} is after the paper was handed in")

    def test_the_shared_window_is_inside_the_sitting(self):
        """The incident has to fall inside the exam, for every student in it: an
        outage the class shares is only a disturbance if the class is still sitting."""
        for plan_ in seed.plans():
            if not plan_.incident_window:
                continue
            start, end = plan_.incident_window
            for session in plan_.sessions:
                assert start >= session.started_at, (
                    f"the incident begins before {session.student_key} started")
                assert end <= session.submitted_at, (
                    f"the incident ends after {session.student_key} handed in")


# ── 4. the ordinary class is ordinary ────────────────────────────────────────

class TestTheOrdinaryClassIsTheReference:
    def test_nothing_modern_is_charged(self):
        """The one sitting older than the event pipeline carries a charged absence —
        that is how its absences were recorded at all — so the claim is about the
        sittings the pipeline covers."""
        for session in sessions("normal"):
            if session.event_source:
                assert session.violations == (), session.note

    def test_every_absence_is_inside_the_grace(self):
        """An absence over the grace would be charged, and a reference class with a
        charge in it is not a reference."""
        for summary in summaries("normal"):
            assert summary["away_long_count"] == 0
            assert summary["away_max_ms"] < seed.GRACE_SECONDS * 1000
            if summary["away_count"]:
                assert summary["away_short_count"] == summary["away_count"]

    def test_the_students_are_working_almost_all_of_the_sitting(self):
        for summary in summaries("normal"):
            assert summary["effective_active_ms"] is not None
            ratio = summary["effective_active_ms"] / summary["sitting_ms"]
            assert ratio > 0.99, f"an ordinary sitting lost {1 - ratio:.1%} of its time"

    def test_no_clock_is_suspect(self):
        assert not any(s["clock_suspect"] for s in summaries("normal"))

    def test_the_class_has_no_shared_moment(self):
        share, _ = seed.busiest_minute(one("normal"))
        assert share < 0.35, (
            "the reference class shares a minute with a third of itself — every "
            "later comparison against it would be excused by a disturbance")

    def test_an_absence_is_already_unusual_here(self):
        """So that "left twice" means something later: the class's median is zero and
        one absence is at the 90th percentile."""
        row = metrics("normal")["away_count"]
        assert row["p50"] == 0
        assert row["p90"] <= 1


# ── 5. the incident is shared, and uniform ───────────────────────────────────

class TestTheIncidentIsSharedRatherThanSuspicious:
    def test_every_student_has_an_event_inside_the_window(self):
        assert seed.incident_share(one("network_incident")) >= 0.8, (
            "the shared disturbance is not shared: the context rule would never fire")

    def test_the_whole_class_shares_one_minute(self):
        share, _ = seed.busiest_minute(one("network_incident"))
        assert share >= 0.5, (
            "no minute of the incident has half the class in it, so a rule looking "
            "for a common moment would not find this")

    def test_every_student_lost_the_connection(self):
        for summary in summaries("network_incident"):
            assert summary["offline_ms"] and summary["offline_ms"] > 0
            assert summary["sync_gap_count"] and summary["sync_gap_count"] > 0

    def test_nobody_stands_out_of_the_class(self):
        """The point of the scenario: the absent time is uniform, so ranking the
        class by it cannot name a culprit — any dashboard that does is naming the
        incident, not a student."""
        row = metrics("network_incident")["offline_ms"]
        assert row["p90"] / row["p50"] < 2.0, (
            f"the incident's absences are not uniform (p50 {row['p50']}, "
            f"p90 {row['p90']}) — a per-student ranking could pass as a finding")
        counted = metrics("network_incident")["away_count"]
        assert counted["p90"] - counted["p50"] <= 1

    def test_nothing_is_charged(self):
        """An outage is not misconduct, and the seed must not make it look like
        one: no violation rows anywhere in this class."""
        assert not any(s.violations for s in sessions("network_incident"))


# ── 6. the handsets are a pattern, not an incident ───────────────────────────

class TestTheHandsetsAreInterruptedNotCheating:
    def test_exactly_the_documented_number_of_handsets(self):
        phones = [s for s in sessions("phone_blur") if s.device_class == "phone"]
        assert len(phones) == seed.HANDSETS == 7

    def test_a_handset_is_interrupted_at_least_three_times(self):
        for session in sessions("phone_blur"):
            if session.device_class == "phone":
                assert len([k for k in (r["kind"] for r in session.events)
                            if k in seed.AWAY_KINDS]) >= 3

    def test_the_first_two_absences_are_forgiven(self):
        """At least the two chances, and never a long one: a handset's absences are
        short by nature, and the class as a whole has no long absence at all."""
        measured = by_student("phone_blur")
        for session in sessions("phone_blur"):
            if session.device_class != "phone":
                continue
            summary = measured[session.student_key]
            assert summary["away_short_count"] >= anti.AWAY_GRACE_CHANCES
            assert summary["away_long_count"] == 0

    def test_the_charged_ones_have_no_length_at_all(self):
        """Charged on sight, so nobody measured them — and the dashboard has to be
        able to show an absence whose length is unknown instead of zero."""
        charged = [v for s in sessions("phone_blur") for v in s.violations]
        assert charged, "no absence was charged, so `away_unknown_count` never fires"
        for violation in charged:
            assert "away_seconds" not in violation["metadata"]
        measured = by_student("phone_blur")
        for session in sessions("phone_blur"):
            if session.device_class == "phone":
                assert measured[session.student_key]["away_unknown_count"] >= 1

    def test_one_charged_absence_is_unknown_in_both_of_its_rows(self):
        """A charged absence reaches the dashboard as **two** rows — the violation
        and the event — and the reading is unknown when *either* says so. That is
        exactly why a mutation that gives only one of them a length is invisible to
        the guard above: the other row still carries the unknown. Both are pinned
        here, so neither can quietly become measured."""
        for session in sessions("phone_blur"):
            if session.device_class != "phone":
                continue
            unmeasured = [row for row in session.events
                          if row["kind"] in seed.AWAY_KINDS and row["duration_ms"] is None]
            assert unmeasured, (
                f"{session.student_key} has no unmeasured absence event: the charged "
                "one grew a length, so a reader can no longer tell the ladder "
                "charged it on sight")

    def test_the_classmates_are_not_interrupted(self):
        for session in sessions("phone_blur"):
            if session.device_class != "phone":
                assert session.violations == ()

    def test_the_handsets_have_no_shared_minute(self):
        """Which is what separates this scenario from the incident: the pattern is
        per student, so it can only be read per student."""
        share, _ = seed.busiest_minute(one("phone_blur"))
        assert share < 0.35

    def test_an_unmeasured_absence_makes_the_working_time_unknowable(self):
        """`sitting minus known` would be larger than the truth, so the app refuses
        to state it — and the seed has to contain that case."""
        interrupted = [s for s in summaries("phone_blur") if s["away_unknown_count"]]
        assert interrupted
        assert all(s["effective_active_ms"] is None for s in interrupted)
        others = [s for s in summaries("phone_blur") if not s["away_unknown_count"]]
        assert all(s["effective_active_ms"] is not None for s in others)


# ── 7. the tail is real, and does not move the median ────────────────────────

class TestTheTailIsFarOutWithoutMovingTheClass:
    def test_one_sitting_is_truncated(self):
        assert sum(1 for s in summaries("tail") if s["truncated"]) == 1

    def test_the_truncated_sitting_really_exceeds_the_cap(self):
        over = [s for s in sessions("tail") if len(s.events) > A.MAX_EVENTS]
        assert len(over) == 1, "the truncated case has no events over the cap"

    def test_the_overflowing_sitting_reports_the_cap_not_the_total(self):
        """What the database will say: the reader stops at `MAX_EVENTS + 1` rows, so
        `events_seen` is the number read. A plan that reported 1200 would disagree
        with the stored row about exactly the sittings that overflow."""
        over = [s for s in summaries("tail") if s["truncated"]]
        assert over[0]["events_seen"] == A.MAX_EVENTS + 1

    def test_one_clock_is_suspect(self):
        assert sum(1 for s in summaries("tail") if s["clock_suspect"]) == 1

    def test_the_suspect_clock_is_visible_in_the_two_readings(self):
        drift = [s for s in summaries("tail") if s["clock_suspect"]]
        assert drift[0]["clock_drift_max_ms"] > A.CLOCK_TOLERANCE_MS

    def test_one_sitting_is_missing_a_sequence_number(self):
        gapped = [s for s in sessions("tail") if seq_gaps(s) == 1]
        assert len(gapped) == 1, (
            "the gapped-sequence case is absent, so nothing exercises the rule that "
            "a lost number is information")

    def test_one_sitting_is_far_above_the_class(self):
        long_one = max(summaries("tail"), key=lambda s: s["away_total_ms"] or 0)
        at_p90 = metrics("tail")["away_total_ms"]["p90"]
        assert (long_one["away_total_ms"] or 0) > at_p90
        assert long_one["away_long_count"] >= 1

    def test_the_median_is_unmoved_by_the_tail(self):
        """The reason a baseline carries MAD beside the IQR: one very bad sitting
        must not decide what "normal" means for the class."""
        row = metrics("tail")["away_total_ms"]
        assert row["p50"] <= 20_000, f"the tail moved the median to {row['p50']}"
        assert row["p90"] < row["metric_max"] / 4


# ── 8. what nobody measured is NULL, not zero ────────────────────────────────

class TestTheUnmeasuredCaseSurvivesTheSeed:
    def test_one_sitting_per_ordinary_class_predates_the_pipeline(self):
        for name in ("normal", "small_class"):
            legacy = [s for s in sessions(name) if not s.event_source]
            assert len(legacy) == 1, f"{name} has no unmeasured sitting"
            assert legacy[0].events == ()

    def test_its_connectivity_is_unknown_while_its_absences_are_real(self):
        for name in ("normal", "small_class"):
            unknown = [s for s in summaries(name) if s["sync_gap_count"] is None]
            assert len(unknown) == 1
            assert unknown[0]["offline_ms"] is None
            assert unknown[0]["answer_change_count"] is None
            assert unknown[0]["away_total_ms"] is not None      # measured by the log

    def test_the_baseline_counts_only_what_was_measured(self):
        """One unmeasured sitting makes the connectivity baseline n-1, and a
        baseline that counted it as zero would understate the whole class."""
        for name in ("normal", "small_class"):
            size = one(name).size
            rows = metrics(name)
            assert rows["away_count"]["n"] == size
            assert rows["sync_gap_count"]["n"] == size - 1
            assert rows["offline_ms"]["n"] == size - 1

    def test_the_small_class_baseline_says_it_is_small(self):
        rows = metrics("small_class")
        assert rows, "a class of five produced no baseline at all"
        for metric, row in rows.items():
            assert row["small_sample"] is True, metric
            assert row["n"] == one("small_class").size - (
                1 if metric in ("sync_gap_count", "offline_ms", "answer_change_count")
                else 0)


# ── 9. the seed and the app share one arithmetic and one vocabulary ──────────

class TestTheSeedUsesTheAppsOwnRules:
    def test_the_grace_is_the_apps_own(self):
        assert seed.GRACE_SECONDS == anti.AWAY_GRACE_SECONDS
        assert seed.GRACE_CHANCES == anti.AWAY_GRACE_CHANCES

    def test_the_event_cap_is_the_apps_own(self):
        assert seed.SUMMARY_MAX_EVENTS == A.MAX_EVENTS

    def test_every_kind_is_one_the_summary_reads(self):
        """A kind the summary does not branch on would be counted in
        `counts_by_kind` and measured nowhere — the seed would look rich and the
        dashboard would read zeros."""
        source = (ROOT / "app" / "services" / "attempt_summary.py").read_text(encoding="utf-8")
        measured = set(A.AWAY_KINDS) | {"sync_gap", "went_offline", "answer_change"}
        assert set(seed.ALL_KINDS) == measured
        for kind in seed.ALL_KINDS:
            assert f'"{kind}"' in source, f"{kind} is emitted but the summary ignores it"

    def test_the_seed_does_not_re_implement_the_summary(self):
        """One arithmetic: the demo data has to be measured by the code production
        measures with, or the two can disagree without anyone noticing."""
        source = SEED_PATH.read_text(encoding="utf-8")
        assert "from app.services import attempt_summary" in source
        assert not re.search(r"def (summarize|percentile|baselines?)\s*\(", source), (
            "the seed has grown its own measurement instead of calling the app's")

    def test_the_app_never_reaches_the_seed(self):
        """It is demo tooling, not a runtime path: a request must not be able to
        write synthetic sittings into a real school."""
        offenders = [str(path.relative_to(ROOT))
                     for path in (ROOT / "app").rglob("*.py")
                     if "anticheat_seed" in path.read_text(encoding="utf-8")]
        assert not offenders, offenders


# ── 10. the writer is dry by default, idempotent, and scoped ─────────────────

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """A PostgREST-shaped store: it filters, sorts, and *applies* writes."""

    def __init__(self, client, table):
        self.client, self.table = client, table
        self._op, self._payload, self._on_conflict = "select", None, None
        self._filters, self._order, self._limit = [], None, None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._op, self._payload, self._on_conflict = "upsert", payload, on_conflict
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def _matches(self, row):
        return all(row.get(column) == value for column, value in self._filters)

    def execute(self):
        rows = self.client.tables.setdefault(self.table, [])
        self.client.calls.append((self.table, self._op))
        if self._op in ("insert", "upsert"):
            out = []
            keys = [k.strip() for k in (self._on_conflict or "id").split(",")]
            for payload_row in (self._payload if isinstance(self._payload, list)
                                else [self._payload]):
                row = dict(payload_row)
                index = next((i for i, existing in enumerate(rows)
                              if all(existing.get(k) == row.get(k) for k in keys)), None)
                if index is None:
                    # A fresh id per *row*: sharing one counter per payload is how a
                    # fake makes every merged row collide on the same id, which a
                    # real database cannot do.
                    row.setdefault("id", self.client.next_id(self.table))
                    rows.append(row)
                    out.append(row)
                else:
                    # A merge keeps the stored id when the payload carried none.
                    rows[index] = {**rows[index], **row}
                    out.append(rows[index])
                self.client.written.setdefault(self.table, []).append(row)
            return FakeResult(out)
        if self._op == "delete":
            kept = [row for row in rows if not self._matches(row)]
            self.client.deleted[self.table] = self.client.deleted.get(self.table, 0) + (
                len(rows) - len(kept))
            rows[:] = kept
            return FakeResult([])
        found = [dict(row) for row in rows if self._matches(row)]
        if self._order:
            column, desc = self._order
            if all(isinstance(row.get(column), (int, float)) for row in found):
                found.sort(key=lambda row: row.get(column), reverse=bool(desc))
            else:
                found.sort(key=lambda row: str(row.get(column) or ""), reverse=bool(desc))
        if self._limit is not None:
            found = found[:self._limit]
        return FakeResult(found)


class FakeAdmin:
    def __init__(self, client):
        self.client = client

    def create_user(self, payload):
        email = payload["email"]
        if email in self.client.accounts:
            raise RuntimeError("A user with this email address has already been registered")
        uid = f"user-{len(self.client.accounts) + 1}"
        self.client.accounts[email] = uid
        return SimpleNamespace(user=SimpleNamespace(id=uid))

    def list_users(self):
        return [SimpleNamespace(id=uid, email=email)
                for email, uid in self.client.accounts.items()]

    def delete_user(self, uid):
        for email, existing in list(self.client.accounts.items()):
            if existing == uid:
                self.client.accounts.pop(email)
        self.client.deleted_accounts.append(uid)


class FakeClient:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.written, self.deleted, self.calls = {}, {}, []
        self.accounts, self.deleted_accounts = {}, []
        self._ids = 0
        self.auth = SimpleNamespace(admin=FakeAdmin(self))

    def next_id(self, table: str) -> str:
        self._ids += 1
        return f"{table}-{self._ids}"

    def table(self, name):
        return FakeQuery(self, name)


def a_school() -> FakeClient:
    """A school with one teacher, so the writer has somebody to own the exams."""
    return FakeClient(tables={
        "profiles": [{"id": "guru-1", "role": "guru", "school_id": "s-1"}],
        "teacher_assignments": [{"teacher_id": "guru-1", "school_id": "s-1"}],
    })


class TestTheWriterIsDryByDefault:
    def test_the_default_is_a_plan_not_a_write(self):
        """Omitting the flag has to mean *do not write*. Every other test here passes
        `dry_run=True` explicitly, so a default flipped to `False` — which turns a
        read-only inspection into ~113 accounts and five classes — survived them."""
        client = a_school()
        made = seed.ensure(client, "s-1", say=lambda *a: None)
        assert made["dry_run"] is True
        assert client.written == {}, client.written
        assert [op for _, op in client.calls if op != "select"] == []

    def test_a_dry_run_writes_nothing(self):
        """It may *read* — a dry run has to find the classes and exams it would
        write — but no write of any kind may leave it."""
        client = a_school()
        made = seed.ensure(client, "s-1", dry_run=True, say=lambda *a: None)
        assert client.written == {}, client.written
        assert [op for _, op in client.calls if op != "select"] == []
        assert made["dry_run"] is True
        assert made["sessions"] == sum(s.students for s in seed.SCENARIOS)

    def test_a_dry_run_still_names_what_it_would_write(self):
        said = []
        seed.ensure(a_school(), "s-1", dry_run=True, say=said.append)
        text = "\n".join(said)
        assert seed.SEED_MARK in text
        for scenario in seed.SCENARIOS:
            assert scenario.title in text

    def test_the_write_lands_in_the_expected_tables(self):
        client = a_school()
        made = seed.ensure(client, "s-1", dry_run=False, say=lambda *a: None)
        assert made["sessions"] == sum(s.students for s in seed.SCENARIOS)
        assert made["skipped"] == 0
        for table in ("classes", "exams", "profiles", "submissions",
                      "attempt_session_events", "attempt_summary", "exam_class_baseline"):
            assert client.written.get(table), f"{table} was never written"

    def test_every_summary_is_produced_by_the_apps_own_reader(self):
        """Not by the plan: the writer hands the rows to `record_for_attempt`, so
        the demo data is what production would have stored for the same events."""
        client = a_school()
        seed.ensure(client, "s-1", dry_run=False, say=lambda *a: None)
        rows = client.tables["attempt_summary"]
        assert len(rows) == sum(s.students for s in seed.SCENARIOS)
        assert all(row["method_version"] == A.METHOD_VERSION for row in rows)
        legacy = [row for row in rows if "attempt_session_events" not in (row["sources"] or [])]
        assert legacy, "the unmeasured case did not survive the write"

    def test_the_written_numbers_are_the_plan_own_measurement(self):
        """The demo dashboard reads the rows, the phase tests read the plan; if the
        two disagreed, every later guard would be testing a different dataset than
        the one a reader sees."""
        client = a_school()
        seed.ensure(client, "s-1", dry_run=False, say=lambda *a: None)
        written = {row["student_id"]: row for row in client.tables["attempt_summary"]}
        compared = 0
        for plan_ in seed.plans():
            for session in plan_.sessions:
                if not session.event_source:
                    continue                    # the legacy row is checked separately
                mine = seed.summary_for(session)
                theirs = written[client.accounts[session.email]]
                for column in MEASURED_COLUMNS:
                    assert theirs.get(column) == mine.get(column), (
                        f"{session.student_key}: {column} is "
                        f"{theirs.get(column)!r} in the database and "
                        f"{mine.get(column)!r} in the plan")
                compared += 1
        assert compared == sum(s.students for s in seed.SCENARIOS) - 2

    def test_the_legacy_row_keeps_its_connectivity_unmeasured(self):
        """The one thing `record_for_attempt` cannot produce today, and the reason
        the writer has a second branch: a summary that says "never measured" rather
        than "measured zero"."""
        client = a_school()
        seed.ensure(client, "s-1", dry_run=False, say=lambda *a: None)
        legacy = [row for row in client.tables["attempt_summary"]
                  if "attempt_session_events" not in (row["sources"] or [])]
        assert len(legacy) == 2, "one per ordinary class"
        for row in legacy:
            assert row["sources"] == ["violation_logs"]
            assert row["sync_gap_count"] is None
            assert row["offline_ms"] is None
            assert row["away_total_ms"] is not None

    def test_running_it_twice_does_not_double_the_data(self):
        client = a_school()
        seed.ensure(client, "s-1", dry_run=False, say=lambda *a: None)
        first = {table: len(rows) for table, rows in client.tables.items()}
        seed.ensure(client, "s-1", dry_run=False, say=lambda *a: None)
        for table, count in first.items():
            assert len(client.tables[table]) == count, (
                f"{table} grew on a second run: a seeder that appends is a seeder "
                "whose numbers depend on how many times it ran")

    def test_clear_removes_only_marker_rows(self):
        client = a_school()
        client.tables["exams"] = [
            {"id": "e-seed", "title": seed.plan("normal").exam_title, "school_id": "s-1"},
            {"id": "e-teacher", "title": "Ulangan Harian 7A", "school_id": "s-1"},
        ]
        client.tables["classes"] = [
            {"id": "c-seed", "name": seed.plan("normal").class_name, "school_id": "s-1"},
            {"id": "c-teacher", "name": "Kelas 7A", "school_id": "s-1"},
        ]
        client.accounts[seed.plan("normal").sessions[0].email] = "user-99"

        removed = seed.clear(client, "s-1", say=lambda *a: None)

        assert removed == {"exams": 1, "classes": 1, "accounts": 1}
        assert [row["id"] for row in client.tables["exams"]] == ["e-teacher"]
        assert [row["id"] for row in client.tables["classes"]] == ["c-teacher"]
        assert client.deleted_accounts == ["user-99"]


# ── 11. the command a person types reaches the writer — and never writes unasked ─
#
# A writer nothing can invoke is a fixture; a command that writes by default is a
# landmine. The seed is only a *foundation* if both halves hold, so these hold the
# seam between `manage.py` and the module rather than the module's arithmetic.

@pytest.fixture(scope="module")
def manage():
    """`manage.py` builds a Flask app at import time, so import it once."""
    import manage as manage_module
    return manage_module


def _the_demo_school(manage_module) -> FakeClient:
    """Just enough PostgREST for the one school lookup the command performs."""
    school = manage_module.DEMO_SCHOOLS[0]
    return FakeClient(tables={"schools": [{"id": "s-1", "npsn": school["npsn"]}]})


class TestTheCommandReachesTheWriterWithoutWritingByAccident:
    def test_the_command_space_offers_it(self, manage):
        text = (ROOT / "manage.py").read_text(encoding="utf-8")
        assert '"seed-anticheat"' in text, "manage.py does not offer `seed-anticheat`"
        assert "seed-anticheat" in (manage.__doc__ or ""), (
            "the usage block does not tell anyone the command exists")

    def _record(self, manage, monkeypatch):
        """Replace the seeder with a recorder, so what is tested is the wiring."""
        seen = {"ensure": [], "clear": []}
        monkeypatch.setattr(manage, "cheat_seed", SimpleNamespace(
            ensure=lambda *a, **k: seen["ensure"].append((a, k)) or {},
            clear=lambda *a, **k: seen["clear"].append((a, k)) or {},
            DEFAULT_DAY=seed.DEFAULT_DAY))
        monkeypatch.setattr(manage, "get_supabase", lambda: _the_demo_school(manage))
        return seen

    def _run(self, manage, *, write=False, clear=False):
        return manage.cmd_seed_anticheat(SimpleNamespace(
            day=seed.DEFAULT_DAY.isoformat(), write=write, clear=clear))

    def test_the_plain_command_plans_and_does_not_write(self, manage, monkeypatch):
        seen = self._record(manage, monkeypatch)
        assert self._run(manage) == 0
        assert seen["clear"] == []
        assert len(seen["ensure"]) == 1, "the command found no demo school to seed"
        _, kwargs = seen["ensure"][0]
        assert kwargs["dry_run"] is True
        assert kwargs["day"] == seed.DEFAULT_DAY
        assert seen["ensure"][0][0][1] == "s-1", "it must seed the school it looked up"

    def test_only_the_flag_writes(self, manage, monkeypatch):
        seen = self._record(manage, monkeypatch)
        self._run(manage, write=True)
        assert seen["ensure"][0][1]["dry_run"] is False

    def test_clearing_is_its_own_act(self, manage, monkeypatch):
        seen = self._record(manage, monkeypatch)
        manage.cmd_seed_anticheat(SimpleNamespace(
            day=seed.DEFAULT_DAY.isoformat(), write=True, clear=True))
        assert seen["ensure"] == [], "a clear that also seeds is not a clear"
        assert seen["clear"][0][0][1] == "s-1"
