#!/usr/bin/env python3
"""Backfill `school_subscriptions.tier` from what each school actually bought.

The runtime resolver (`app/services/subscription_service.py`) was fixed to read
the `tier` column first and then the plan by relation, so a paying school is no
longer capped at the trial's quota. But the *stored* column is still wrong on the
live box: Phase 0 measured **14 rows, every one `plan_id IS NULL` and
`tier = 'trial'`**, including active subscriptions a school paid for. Anything
that trusts the column directly — a raw SQL report, a hand audit — still reads a
paying school as a trial.

This is the data migration that repairs the column. It is deliberately timid:

* **Dry run by default.** With no flags it reads, plans, prints the summary and
  writes nothing. `--apply --yes` is the only way to write.
* **It never guesses.** Only two things are evidence: a stored paid tier, or a
  `plan_id` that resolves through the relation to `subscription_plans`. A row
  whose only sign of life is `status = 'active'` is marked **PERLU KEPUTUSAN
  MANUAL** and left exactly as it is — not defaulted to `trial` (the bug) and not
  to the highest tier (the temptation). The operator decides those, per school.
* **It is non-destructive and idempotent.** It only ever writes `tier`, only to
  rows a plan proves, and a second run proposes nothing for a row it already
  fixed. It deletes and alters nothing.
* **A real trial stays a trial.** `status` of `trial` / `trial_expired` is a fact
  about the row, not an absence of evidence.

Usage
-----
    python deploy/backfill_subscription_tier.py             # dry run

    python deploy/backfill_subscription_tier.py --apply      # refused: needs --yes
    python deploy/backfill_subscription_tier.py --apply --yes

Exit codes
----------
    0  ran (a dry run, or an apply that wrote)
    2  `--apply` without `--yes`, or the database could not be read
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.subscription_service import (  # noqa: E402
    PAID_TIERS, tier_for_duration_days)

TRIAL_STATUSES = ("trial", "trial_expired")

#: The actions a decision can carry.
UPDATE = "update"   # a plan proves the tier; --apply will write it
KEEP = "keep"       # already correct
MANUAL = "manual"   # no trusted source; a human decides, nobody writes
SKIP = "skip"       # nothing to grant (an expired subscription)


def decide(row, plans_by_id):
    """One row's decision: what it is, what it should be, and on what evidence.

    Pure — no I/O — so the rule can be exercised without a database. The proposal
    is present only when a source is trusted; a manual row carries *no*
    `proposed_tier`, so no caller can accidentally write a guess.
    """
    status = (row.get("status") or "").lower()
    stored = row.get("tier")
    plan_id = row.get("plan_id")
    decision = {
        "school_id": row.get("school_id"),
        "status": status,
        "plan_id": plan_id,
        "current_tier": stored,
        "proposed_tier": None,
        "source": None,
        "reason": "",
        "action": KEEP,
    }

    if status == "expired":
        decision.update(action=SKIP,
                        reason="langganan berakhir — tidak ada tier untuk ditulis")
        return decision

    if status in TRIAL_STATUSES:
        if stored in PAID_TIERS:
            decision.update(
                action=MANUAL,
                reason=("status trial tetapi kolom tier menyimpan paket "
                        f"berbayar ({stored}) — bertentangan, perlu keputusan manusia"))
        else:
            decision.update(action=KEEP, proposed_tier="trial", source="trial",
                            reason="trial asli (bukan sekolah berbayar)")
        return decision

    if stored in PAID_TIERS:
        decision.update(action=KEEP, proposed_tier=stored, source="stored",
                        reason="kolom tier sudah menyimpan paket berbayar")
        return decision

    plan = plans_by_id.get(plan_id) if plan_id is not None else None
    if plan and plan.get("duration_days") is not None:
        days = plan.get("duration_days")
        decision.update(action=UPDATE,
                        proposed_tier=tier_for_duration_days(days),
                        source="plan",
                        reason=f"plan_id={plan_id} (durasi {days} hari) via relasi subscription_plans")
        return decision

    decision.update(
        action=MANUAL,
        reason=("tidak ada sumber paket yang tepercaya "
                "(plan_id kosong atau tidak ada di subscription_plans)"))
    return decision


def plan_backfill(subscriptions, plans_by_id):
    """The decisions for a whole table, in input order."""
    return [decide(row, plans_by_id) for row in subscriptions]


def render(decisions):
    """The report an operator reads before deciding whether to `--apply`."""
    updates = [d for d in decisions if d["action"] == UPDATE]
    manual = [d for d in decisions if d["action"] == MANUAL]
    keep = [d for d in decisions if d["action"] == KEEP]
    skip = [d for d in decisions if d["action"] == SKIP]

    lines = ["backfill subscription tier — ringkasan",
             f"  baris dibaca        : {len(decisions)}",
             f"  akan diubah         : {len(updates)}",
             f"  sudah benar         : {len(keep)}",
             f"  PERLU KEPUTUSAN MANUAL: {len(manual)}",
             f"  dilewati (expired)  : {len(skip)}",
             ""]

    if updates:
        lines.append("akan diubah (tier akan ditulis):")
        for d in updates:
            lines.append(f"    - {d['school_id']}: {d['current_tier']!r} -> "
                         f"{d['proposed_tier']!r}  [{d['reason']}]")
        lines.append("")
    if manual:
        lines.append("PERLU KEPUTUSAN MANUAL (tidak akan disentuh — tentukan per sekolah):")
        for d in manual:
            lines.append(f"    - {d['school_id']}: status={d['status']!r} "
                         f"plan_id={d['plan_id']!r} tier={d['current_tier']!r}  "
                         f"({d['reason']})")
        lines.append("")
    return "\n".join(lines)


def _latest_per_school(rows):
    """One row per school: the newest subscription it has.

    The runtime reads the newest row too (`get_tier_for_school` orders by
    `created_at` descending), so the backfill and the app must look at the same
    row or they disagree about the same school.
    """
    seen = {}
    for row in rows:
        school = row.get("school_id")
        if school not in seen:
            seen[school] = row
    return list(seen.values())


def backfill(supabase, apply=False):
    """Read, plan, and (only when `apply`) write the trusted tiers.

    Returns `(decisions, written)`. `written` is 0 on a dry run by construction.
    """
    subs = supabase.table("school_subscriptions") \
        .select("school_id, status, plan_id, tier, created_at") \
        .order("created_at", desc=True).execute().data or []
    plans = supabase.table("subscription_plans") \
        .select("id, duration_days").execute().data or []
    plans_by_id = {p.get("id"): p for p in plans}

    decisions = plan_backfill(_latest_per_school(subs), plans_by_id)
    if not apply:
        return decisions, 0

    written = 0
    for d in decisions:
        if d["action"] != UPDATE:
            continue
        supabase.table("school_subscriptions") \
            .update({"tier": d["proposed_tier"]}) \
            .eq("school_id", d["school_id"]).execute()
        written += 1
    return decisions, written


def main(argv=None, supabase=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the trusted tiers (requires --yes)")
    parser.add_argument("--dry-run", action="store_true",
                        help="read and plan only (the default)")
    parser.add_argument("--yes", action="store_true",
                        help="confirm an --apply against a real database")
    args = parser.parse_args(argv)

    if args.apply and not args.yes:
        print("backfill subscription tier: --apply needs --yes.\n"
              "    Run it without --apply first to read the plan; nothing was written.")
        return 2

    if supabase is None:
        from app.utils.auth import get_supabase
        supabase = get_supabase()

    try:
        decisions, written = backfill(supabase, apply=args.apply)
    except Exception as exc:  # noqa: BLE001 - a read failure is not a write
        print(f"backfill subscription tier: could not read the database — "
              f"{type(exc).__name__}: {exc}")
        return 2

    print(render(decisions))
    if args.apply:
        print(f"applied: {written} baris ditulis.")
    else:
        print("dry run — tidak ada yang ditulis. Tambahkan --apply --yes untuk menulis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
