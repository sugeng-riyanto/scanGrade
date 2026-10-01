"""A school's tier comes from what it bought, not from a missing plan id.

Measured on production (`school_subscriptions`, 14 rows): **every** row has
``plan_id IS NULL`` and ``tier = 'trial'`` — including two rows that are
``status='active'`` with a ``subscription_end`` a year out, activated with a real
``SG-…`` code. ``get_tier_for_school`` reads ``plan_id``, maps ``None`` to
``"trial"``, and every paying school is therefore capped at the free trial's
**5 exams/year**. Cash activation and redemption write no plan at all, so they
land in the same hole; even the bought-plan path is resolved by a hard-coded
id→tier table whose ``1`` maps to ``"trial"`` (the live plan ``1 Bulan``,
Rp59.000).

So this file holds three properties:

* **a paid subscription is never ``trial``** — an active row with no plan still
  resolves to the lowest paid tier, because a recorded cash payment is not a free
  trial;
* **what was bought is read from the plan** (its duration band), so a catalogue
  whose ids move is still read correctly, and the read path also uses the span the
  subscription runs for when the plan is gone;
* **the write paths record it** — ``_activate_subscription`` (Midtrans success and
  redemption) and the cash activation both persist ``tier``/``plan_id``, so the
  next reader does not have to infer anything.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import subscription_service as sub


# ── a filtering stand-in for the Supabase client ─────────────────────────────

class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self.rows = [r for r in self.rows if r.get(col) in vals]
        return self

    def gte(self, col, val):
        return self

    def is_(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) is None]
        return self

    def order(self, col, desc=False):
        self.rows.sort(key=lambda r: r.get(col) or "", reverse=bool(desc))
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows, count=len(self.rows))


class _Sb:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))


def _active_school(tables=None, **sub_row):
    row = {"id": 1, "school_id": "S1", "status": "active", "plan_id": None,
           "tier": "trial", "subscription_start": None, "subscription_end": None,
           "trial_end": None}
    row.update(sub_row)
    base = {"school_subscriptions": [row], "subscription_plans": [],
            "exams": [], "students": []}
    base.update(tables or {})
    return _Sb(base), row


# ── the duration bands, one rule for plan length and subscription span ───────

class TestTheDurationBands:
    @pytest.mark.parametrize("days,tier", [
        (30, "basic"), (90, "basic"), (180, "basic"),
        (365, "pro"), (730, "pro"), (1095, "pro"),
        (1096, "enterprise"), (1825, "enterprise"),
        (0, "enterprise"),          # "Selamanya" — no end date at all
        (None, "basic"),            # a paid activation with nothing recorded
    ])
    def test_a_duration_names_a_tier(self, days, tier):
        assert sub.tier_for_duration_days(days) == tier

    def test_the_live_catalogue_reads_correctly(self):
        """The seven plans the box actually sells."""
        days = {1: 30, 2: 90, 3: 120, 4: 180, 5: 365, 6: 730, 7: 1095}
        got = {pid: sub.tier_for_duration_days(d) for pid, d in days.items()}
        assert got == {1: "basic", 2: "basic", 3: "basic", 4: "basic",
                       5: "pro", 6: "pro", 7: "pro"}

    def test_the_hardcoded_id_map_is_gone(self):
        """Phase 1: the plan is read *by relation*, never from a private table.

        `_SEEDED_PLAN_DAYS` mapped an id to a number nothing in the database
        agreed with, so editing a plan's duration — or adding one — silently
        mispriced every school on it. The relation to `subscription_plans` is the
        only source now.
        """
        assert not hasattr(sub, "_plan_id_to_tier"), (
            "the hardcoded id→tier map is back; a plan edited on /plans would "
            "no longer be read from the table")
        assert not hasattr(sub, "_SEEDED_PLAN_DAYS"), (
            "a private copy of the catalogue's durations is back")

    def test_every_seeded_plan_resolves_through_the_relation_to_a_paid_tier(self):
        """The live catalogue (012_subscription_system.sql), read by id.

        The defect in one line: id 1 is the paid `1 Bulan` and the old hardcoded
        map called it `"trial"`. Read through the relation, no seeded plan can
        land on the free tier.
        """
        catalogue = {1: 30, 2: 90, 3: 120, 4: 180, 5: 365, 6: 730, 7: 1095,
                     8: 1825, 9: 2555, 10: 0}
        for pid, days in catalogue.items():
            tier = sub.resolve_tier({"status": "active", "plan_id": pid},
                                    {"id": pid, "duration_days": days})
            assert tier in sub.PAID_TIERS, (
                f"seeded plan {pid} ({days}d) resolved to {tier!r}")
            assert tier == sub.tier_for_duration_days(days)


# ── the resolver: literal first, then inference, never trial for a paid row ──

class TestTheResolver:
    def test_a_recorded_paid_tier_wins(self):
        assert sub.resolve_tier({"status": "active", "tier": "pro"}) == "pro"

    def test_a_plan_is_read_by_its_duration(self):
        plan = {"id": 5, "duration_days": 365}
        assert sub.resolve_tier({"status": "active", "plan_id": 5}, plan) == "pro"

    def test_a_bare_plan_id_is_not_fabricated_from_a_hardcoded_map(self):
        """No plan row and no loader means the id is not trusted.

        Plan 10 is `Selamanya` (enterprise) in the catalogue, so if the resolver
        still carried the old id map this would answer `enterprise`. With the map
        gone it falls past the (absent) plan to the span (absent) and the active
        row's lowest-paid default.
        """
        assert sub.resolve_tier({"status": "active", "plan_id": 10}) == "basic"

    def test_a_plan_id_is_resolved_through_the_plans_relation(self):
        """The relation is how a bare plan id becomes a tier."""
        def loader(pid):
            return {"id": pid, "duration_days": 365} if pid == 5 else None

        assert sub.resolve_tier({"status": "active", "plan_id": 5},
                                load_plan=loader) == "pro"

        def forever(pid):
            return {"id": pid, "duration_days": 0}

        assert sub.resolve_tier({"status": "active", "plan_id": 10},
                                load_plan=forever) == "enterprise"

    def test_the_subscription_span_is_the_last_resort(self):
        row = {"status": "active", "plan_id": None,
               "subscription_start": "2026-06-01T00:00:00+00:00",
               "subscription_end": "2027-06-01T00:00:00+00:00"}
        assert sub.resolve_tier(row) == "pro", (
            "a 365-day activation whose plan was never stored must read as the "
            "year it paid for, not as the trial it did not")

    def test_an_active_row_with_nothing_recorded_is_paid_not_trial(self):
        assert sub.resolve_tier({"status": "active", "plan_id": None}) == "basic", (
            "an active subscription with no plan is still a paid activation; "
            "resolving it to `trial` is the 5-exam cap this file exists to remove")

    def test_a_trial_row_stays_a_trial(self):
        assert sub.resolve_tier({"status": "trial", "plan_id": None}) == "trial"
        assert sub.resolve_tier({"status": "trial_expired"}) == "trial"

    def test_no_subscription_is_trial(self):
        assert sub.resolve_tier(None) == "trial"


# ── the read path the gate uses ──────────────────────────────────────────────

class TestTheSchoolTierIsReadFromWhatItBought:
    def _tier(self, monkeypatch, tables=None, **sub_row):
        sb, _ = _active_school(tables, **sub_row)
        monkeypatch.setattr(sub, "get_supabase", lambda: sb)
        return sub.get_tier_for_school("S1")

    def test_an_active_school_is_not_capped_as_trial(self, monkeypatch):
        assert self._tier(monkeypatch) == "basic", (
            "a school with an active subscription and no stored plan resolved "
            "back to `trial`")

    def test_a_year_is_resolved_from_its_plan(self, monkeypatch):
        tables = {"subscription_plans": [{"id": 5, "duration_days": 365}]}
        assert self._tier(monkeypatch, tables, plan_id=5) == "pro"

    def test_a_lost_plan_is_resolved_from_the_span(self, monkeypatch):
        assert self._tier(
            monkeypatch, None,
            subscription_start="2026-06-01T00:00:00+00:00",
            subscription_end="2027-06-01T00:00:00+00:00") == "pro"

    def test_an_expired_subscription_still_grants_nothing(self, monkeypatch):
        assert self._tier(monkeypatch, None, status="expired") is None
        assert self._tier(
            monkeypatch, None,
            subscription_end="2020-01-01T00:00:00+00:00") is None

    def test_a_real_trial_is_still_a_trial(self, monkeypatch):
        assert self._tier(monkeypatch, None, status="trial", trial_end=None) == "trial"

    def test_five_exams_do_not_stop_a_paying_school(self, monkeypatch):
        """The reported symptom, through the gate that produces it."""
        exams = [{"id": i, "school_id": "S1"} for i in range(5)]
        sb, _ = _active_school({"exams": exams})
        monkeypatch.setattr(sub, "get_supabase", lambda: sb)
        allowed, message = sub.check_feature_limit("S1", "create_exam")
        assert allowed, (
            f"a paying school was refused its sixth exam: {message!r}")

    def test_a_trial_school_is_still_capped(self, monkeypatch):
        exams = [{"id": i, "school_id": "S1"} for i in range(5)]
        sb, _ = _active_school({"exams": exams}, status="trial")
        monkeypatch.setattr(sub, "get_supabase", lambda: sb)
        allowed, _message = sub.check_feature_limit("S1", "create_exam")
        assert not allowed, "the free trial's 5-exam quota stopped applying"


# ── the write paths record what was bought ───────────────────────────────────

class TestTheActivationRecordsWhatWasBought:
    def test_the_midtrans_activation_stores_the_tier(self, monkeypatch):
        from app.services import midtrans_service as mid
        inserted = {}

        class Sb:
            def table(self, name):
                return self

            def update(self, patch):
                return self

            def insert(self, row):
                if "tier" in row or "plan_id" in row:
                    inserted.update(row)
                return self

            def eq(self, *a, **k):
                return self

            def select(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def execute(self):
                return SimpleNamespace(data=[], count=0)

        sb = Sb()
        monkeypatch.setattr(mid, "_load_plan", lambda pid: {"id": pid, "name": "1 Tahun", "duration_days": 365})
        # The email path is not what this test is about; let it fail quietly.
        monkeypatch.setattr(mid, "current_app", SimpleNamespace(
            extensions={"supabase": sb, "supabase_auth": None},
            logger=SimpleNamespace(info=lambda *a, **k: None,
                                   warning=lambda *a, **k: None,
                                   error=lambda *a, **k: None)))
        mid._activate_subscription("S1", 5, "ORD-1", sb)
        assert inserted.get("plan_id") == 5
        assert inserted.get("tier") == "pro", (
            "the activation wrote no tier, so the box has to infer it (or, as it "
            "did, default the school to trial)")

    def test_a_manual_activation_without_a_plan_is_paid(self):
        """A cash activation buys *something*; the tier must not be the free one."""
        assert sub.tier_for_manual_activation() in sub.PAID_TIERS


# ── the wiring, so a future edit cannot quietly drop it ──────────────────────

class TestTheCashPathCarriesIt:
    def test_cash_activation_records_a_plan_and_tier(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[2] / "app" / "routes"
               / "super_admin.py").read_text(encoding="utf-8")
        body = src[src.index("def activate_cash("):]
        body = body[:body.index("\n@super_bp.route")]
        assert '"tier"' in body, (
            "cash activation stores no tier, so a cash-paying school resolves to "
            "the free trial again")
        assert '"plan_id"' in body, (
            "cash activation stores no plan, so the box cannot tell what the "
            "school bought")
