"""The backfill for `school_subscriptions.tier` — dry-run first, never a guess.

Phase 0 measured the production table: **14 rows, all of them `plan_id IS NULL`
and `tier = 'trial'`**, including active subscriptions a school paid for. The
runtime resolver was fixed to read what was bought, but the *stored* column is
still wrong, so a raw SQL report — or anything that trusts the column directly —
still says every paying school is on the trial.

So `deploy/backfill_subscription_tier.py` rewrites the column from what each row
can actually prove, and this file holds the rules that make it safe:

* **It defaults to a dry run.** Nothing is written unless `--apply --yes` is
  given; an accidental invocation prints the plan and stops.
* **It never guesses.** A row whose only evidence is "active" is marked
  *perlu keputusan manual* and left alone — not defaulted to `trial` (the bug)
  nor to the highest tier (the temptation). Only a stored paid tier or a plan
  read by relation to `subscription_plans` is trusted.
* **It is idempotent.** Once a row carries a paid tier, a second run proposes
  nothing for it, so a re-run after a partial failure is safe.
* **A trial row is left as a trial** — that is a fact about the row, not an
  absence of evidence.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "deploy" / "backfill_subscription_tier.py"


def backfill_module():
    if not SCRIPT_PATH.exists():
        pytest.fail("deploy/backfill_subscription_tier.py does not exist")
    spec = importlib.util.spec_from_file_location("sg_tier_backfill", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def row(school="S1", status="active", tier="trial", plan_id=None, **extra):
    base = {"school_id": school, "status": status, "tier": tier,
            "plan_id": plan_id}
    base.update(extra)
    return base


PLANS = {5: {"id": 5, "duration_days": 365}, 1: {"id": 1, "duration_days": 30}}


# ── the decision for one row ─────────────────────────────────────────────────


class TestTheDecision:
    def decide(self, **kwargs):
        plans = kwargs.pop("plans", PLANS)
        return backfill_module().decide(row(**kwargs), plans)

    def test_a_plan_relation_is_trusted_and_banded(self):
        d = self.decide(plan_id=5)
        assert d["action"] == "update"
        assert d["proposed_tier"] == "pro"
        assert d["source"] == "plan"

    def test_a_stored_paid_tier_is_left_alone(self):
        d = self.decide(tier="pro", plan_id=None)
        assert d["action"] == "keep", (
            "a row that already says what it bought must not be rewritten")

    def test_an_active_row_with_no_evidence_is_manual_and_gets_no_tier(self):
        d = self.decide(plan_id=None, tier="trial")
        assert d["action"] == "manual", (
            "with no plan and no stored tier the row must be flagged, not guessed")
        assert d.get("proposed_tier") is None, (
            "a manual row must carry no proposed tier — defaulting to trial is "
            "the bug, defaulting to the highest tier is the temptation")

    def test_a_trial_row_is_a_trial_not_a_manual_row(self):
        d = self.decide(status="trial", tier="trial", plan_id=None)
        assert d["action"] == "keep"
        assert d["proposed_tier"] == "trial" or d["proposed_tier"] is None

    def test_a_trial_expired_row_is_a_trial(self):
        assert self.decide(status="trial_expired", tier="trial")["action"] == "keep"

    def test_a_trial_status_row_with_a_paid_tier_is_contradictory(self):
        d = self.decide(status="trial", tier="pro")
        assert d["action"] == "manual", (
            "a row in trial status that stores a paid tier is a contradiction a "
            "human has to settle, not something the backfill overwrites")

    def test_an_expired_row_is_skipped(self):
        assert self.decide(status="expired")["action"] == "skip"

    def test_a_plan_id_with_no_plan_row_is_still_manual(self):
        """A dangling plan id is not evidence of a tier."""
        d = self.decide(plan_id=999)
        assert d["action"] == "manual"
        assert d.get("proposed_tier") is None


# ── the plan over a whole table ──────────────────────────────────────────────


class TestThePlan:
    def test_it_separates_updates_from_manual_rows(self):
        b = backfill_module()
        decisions = b.plan_backfill(
            [row("A", plan_id=5), row("B", plan_id=None),
             row("C", tier="pro"), row("D", status="expired")],
            PLANS)
        by_school = {d["school_id"]: d["action"] for d in decisions}
        assert by_school == {"A": "update", "B": "manual", "C": "keep", "D": "skip"}

    def test_the_manual_list_is_what_the_report_marks(self):
        b = backfill_module()
        decisions = b.plan_backfill([row("B", plan_id=None)], PLANS)
        report = b.render(decisions)
        assert "perlu keputusan manual" in report.lower(), (
            "the manual rows must be named in the report, in the words the "
            "operator asked for")

    def test_a_second_run_proposes_nothing_for_a_rewritten_row(self):
        b = backfill_module()
        first = b.plan_backfill([row("A", plan_id=5)], PLANS)[0]
        rewritten = row("A", plan_id=5, tier=first["proposed_tier"])
        second = b.plan_backfill([rewritten], PLANS)[0]
        assert second["action"] == "keep", "the backfill is not idempotent"


# ── the dry run is the default, and apply needs --yes ────────────────────────


class _UpdateRecorder:
    def __init__(self, subs, plans):
        self.subs = list(subs)
        self.plans = list(plans)
        self.updates = []

    def table(self, name):
        return _RecorderTable(self, name)


class _RecorderTable:
    def __init__(self, rec, name):
        self.rec, self.name, self._filter, self._patch = rec, name, {}, None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filter[col] = val
        return self

    def in_(self, col, vals):
        self._filter[col] = ("in", vals)
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def execute(self):
        rows = self.rec.subs if self.name == "school_subscriptions" else self.rec.plans
        if self._patch is not None:
            for r in rows:
                if all(r.get(k) == v for k, v in self._filter.items()):
                    r.update(self._patch)
            self.rec.updates.append((self.name, dict(self._filter), dict(self._patch)))
            return SimpleNamespace(data=rows)
        matched = [r for r in rows
                   if all(r.get(k) == v for k, v in self._filter.items())]
        return SimpleNamespace(data=matched)


class TestTheRunWritesNothingByDefault:
    def _client(self):
        return _UpdateRecorder(
            [row("A", plan_id=5), row("B", plan_id=None)],
            [dict(p) for p in PLANS.values()])

    def test_a_dry_run_performs_no_writes(self):
        b = backfill_module()
        client = self._client()
        decisions, written = b.backfill(client, apply=False)
        assert client.updates == [], "a dry run wrote to the database"
        assert written == 0
        assert {d["school_id"]: d["action"] for d in decisions} == {
            "A": "update", "B": "manual"}

    def test_an_apply_writes_only_the_trusted_rows(self):
        b = backfill_module()
        client = self._client()
        _decisions, written = b.backfill(client, apply=True)
        assert written == 1
        assert [u[1].get("school_id") for u in client.updates] == ["A"]
        assert client.subs[0]["tier"] == "pro"
        assert client.subs[1]["tier"] == "trial", (
            "a manual row must not be touched by an apply")

    def test_apply_without_yes_is_refused(self):
        b = backfill_module()
        client = self._client()
        code = b.main(["--apply"], supabase=client)
        assert code != 0
        assert client.updates == [], "an unconfirmed apply wrote anyway"

    def test_the_default_invocation_is_a_dry_run(self):
        b = backfill_module()
        client = self._client()
        code = b.main([], supabase=client)
        assert code == 0
        assert client.updates == []

    def test_apply_yes_writes(self):
        b = backfill_module()
        client = self._client()
        code = b.main(["--apply", "--yes"], supabase=client)
        assert code == 0
        assert len(client.updates) == 1
