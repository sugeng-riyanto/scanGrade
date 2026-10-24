#!/usr/bin/env python3
"""Audit a disposable offline run's JUnit report for real coverage gaps."""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# These are the exact 31 formerly skipped cases, recorded from the clean CI
# JUnit/pytest failure list, not a selection of convenient representative cases.
REQUIRED_OFFLINE = (
    "test_a_failed_profile_write_undoes_the_new_account",
    "test_a_failed_role_row_undoes_the_new_account",
    "test_a_failed_teachers_row_undoes_the_new_account",
    "test_an_account_that_already_existed_is_never_deleted",
    "test_a_clean_seed_writes_all_three_rows",
    "test_a_dead_address_reports_instead_of_deleting",
    "test_the_command_space_offers_it",
    "test_the_plain_command_plans_and_does_not_write",
    "test_only_the_flag_writes",
    "test_clearing_is_its_own_act",
    "test_the_list_comes_from_the_seed",
    "test_the_fallback_is_in_step_with_the_seed",
    "test_an_expired_trial_is_repaired_rather_than_left_alone",
    "test_it_asks_the_database_for_the_newest_row",
    "test_the_row_it_writes_never_expires",
    "test_an_active_school_is_left_alone",
    "test_every_status_that_is_not_active_is_repaired[trial]",
    "test_every_status_that_is_not_active_is_repaired[trial_expired]",
    "test_every_status_that_is_not_active_is_repaired[expired]",
    "test_every_status_that_is_not_active_is_repaired[cancelled]",
    "test_every_status_that_is_not_active_is_repaired[suspended]",
    "test_every_status_that_is_not_active_is_repaired[None]",
    "test_a_school_with_no_subscription_at_all_gets_one",
    "test_the_command_covers_every_school_the_demo_page_advertises",
    "test_the_command_reports_what_it_did_for_each_school",
    "test_every_demo_school_offers_both_officials",
    "test_a_process_with_an_unreadable_env_file_still_imports_the_config",
    "test_it_passes_on_the_real_page_with_a_real_roster",
    "test_a_real_old_interpreter_can_import_the_module",
    "test_a_real_old_interpreter_may_run_the_deploy_gates_own_targets",
    "test_a_real_old_interpreter_still_refuses_the_whole_suite",
)
REQUIRED_MUTATION = ("test_a_named_harness_runs_green_end_to_end",)
REQUIRED = REQUIRED_OFFLINE + REQUIRED_MUTATION

def audit(path: Path, *, minimum: int = 11000, required=REQUIRED) -> tuple[bool, str]:
    try:
        cases = list(ET.parse(path).getroot().iter("testcase"))
    except (OSError, ET.ParseError) as exc:
        return False, f"No parseable JUnit coverage report: {type(exc).__name__}: {exc}"
    names = {case.get("name", "") for case in cases}
    skipped = [case.get("name", "<unnamed>") for case in cases
               if case.find("skipped") is not None]
    broken = [case.get("name", "<unnamed>") for case in cases
              if case.find("failure") is not None or case.find("error") is not None]
    missing = sorted(set(required) - names)
    issues = []
    if len(cases) < minimum:
        issues.append(f"only {len(cases)} tests ran, minimum {minimum}")
    if skipped:
        issues.append(f"{len(skipped)} skipped: {', '.join(skipped[:12])}")
    if broken:
        issues.append(f"{len(broken)} failed/error: {', '.join(broken[:12])}")
    if missing:
        issues.append(f"missing formerly-skipped checks: {', '.join(missing)}")
    summary = f"JUnit: {len(cases)} tests, {len(skipped)} skipped, {len(broken)} failed/error"
    return not issues, summary + ("\n" + "\n".join(issues) if issues else "\nAll required skip-paths exercised")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path)
    parser.add_argument("--minimum", type=int, default=11000)
    parser.add_argument("--scope", choices=("full", "offline", "mutation"), default="full",
                        help="which formerly-skipped paths this JUnit file must include")
    args = parser.parse_args(argv)
    required = {"full": REQUIRED, "offline": REQUIRED_OFFLINE,
                "mutation": REQUIRED_MUTATION}[args.scope]
    okay, report = audit(args.report, minimum=args.minimum, required=required)
    print(report)
    return 0 if okay else 1


if __name__ == "__main__":
    sys.exit(main())
