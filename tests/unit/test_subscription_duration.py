"""How long a subscription runs, and the column that says so.

Phase 0 raised two questions about the plan catalogue: does the code read the same
column the schema declares (`duration_days`, not a `duration_months` that never
existed), and do all the places that compute an expiry agree.

They did not. The online activation defaulted to a year for an activation that
named no plan; the cash path left `subscription_end` **NULL** whenever it could
not read a plan, which made the subscription open-ended — `get_tier_for_school`
only expires a row whose end has passed, so a school activated by cash kept every
entitlement for ever. One rule now covers both
(`subscription_service.subscription_end`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import subscription_service as sub

ROOT = Path(__file__).resolve().parents[2]


class TestTheExpiryRule:
    NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_a_plan_runs_for_its_own_length(self):
        for days in (30, 90, 120, 180, 365, 730, 1095, 1825):
            assert sub.subscription_end(self.NOW, days) == self.NOW + timedelta(days=days)

    def test_a_forever_plan_has_no_end(self):
        assert sub.subscription_end(self.NOW, 0) is None, (
            "`Selamanya` (duration 0) must have no end, not a zero-length one")

    def test_an_unknown_length_takes_the_documented_default(self):
        assert sub.subscription_end(self.NOW, None) == self.NOW + timedelta(
            days=sub.CASH_DEFAULT_DAYS), (
            "a cash activation with no readable plan must run the documented year, "
            "not for ever")
        assert sub.CASH_DEFAULT_DAYS == 365

    def test_a_nonsense_length_takes_the_default(self):
        assert sub.subscription_end(self.NOW, "n/a") == self.NOW + timedelta(
            days=sub.CASH_DEFAULT_DAYS)


class TestTheColumnNameMatchesTheSchema:
    def test_the_schema_declares_duration_days(self):
        schema = (ROOT / "supabase" / "migrations"
                  / "012_subscription_system.sql").read_text(encoding="utf-8")
        assert "duration_days INTEGER" in schema

    def test_no_module_reads_a_duration_months_that_does_not_exist(self):
        offenders = []
        for base in ("app", "deploy", "supabase"):
            for path in (ROOT / base).rglob("*"):
                if path.suffix not in (".py", ".sql", ".html"):
                    continue
                if "__pycache__" in str(path):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                if "duration_months" in text:
                    offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, (
            "these files name a `duration_months` column the schema does not "
            f"have: {offenders}")


class TestBothWritePathsUseTheOneRule:
    def test_the_cash_activation_uses_the_shared_rule(self):
        src = (ROOT / "app" / "routes" / "super_admin.py").read_text(encoding="utf-8")
        body = src[src.index("def activate_cash("):]
        body = body[:body.index("\n@super_bp.route")]
        assert "subscription_end(" in body, (
            "the cash activation computes its own expiry again, which is how it "
            "ended up NULL and never expiring")

    def test_the_online_activation_uses_the_shared_rule(self):
        src = (ROOT / "app" / "services" / "midtrans_service.py").read_text(encoding="utf-8")
        body = src[src.index("def _activate_subscription("):]
        body = body[:body.index("def _generate_invoice(")]
        assert "subscription_end(" in body
