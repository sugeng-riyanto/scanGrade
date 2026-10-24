#!/usr/bin/env python3
"""Audit a disposable offline run's JUnit report for real coverage gaps."""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED = (
    "test_a_failed_profile_write_undoes_the_new_account",
    "test_the_command_space_offers_it",
    "test_it_passes_on_the_real_page_with_a_real_roster",
    "test_a_named_harness_runs_green_end_to_end",
    "test_a_real_old_interpreter_can_import_the_module",
    "test_a_real_old_interpreter_may_run_the_deploy_gates_own_targets",
    "test_a_real_old_interpreter_still_refuses_the_whole_suite",
)


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
    args = parser.parse_args(argv)
    okay, report = audit(args.report, minimum=args.minimum)
    print(report)
    return 0 if okay else 1


if __name__ == "__main__":
    sys.exit(main())
