#!/usr/bin/env python3
"""Report the violations already recorded on papers whose anti-cheat is switched off.

Why this exists
---------------
`exams.anti_cheat_enabled` is the school's own switch. A paper with it off records
nothing *from the moment it is switched off* — the log, the ladder, the stored count
and the resume lock all now ask `anti_cheat_service.enabled`, so a switched-off paper
cannot collect another row or charge another point.

That is the forward half. This is the backward half: rows written while the paper was
still monitored keep sitting in `violation_logs`, and a teacher's result carries the
penalty they were charged under a policy the school has since changed. Reading those
rows used to mean opening the database.

Read-only by construction. It selects and counts; it never inserts, updates or
deletes, and it does not touch a score or a penalty — the rows are evidence, and an
audit that moved a mark would be the opposite of an audit. What to do about marks
already awarded is a decision for the school, and this is the list it needs to make
it.

Usage
-----
    python deploy/audit_unmonitored_violations.py                    # whole project
    python deploy/audit_unmonitored_violations.py --school-id <uuid> # one school
    python deploy/audit_unmonitored_violations.py --json report.json # machine-readable
    python deploy/audit_unmonitored_violations.py --pupils           # name them

Exit codes: 0 measured (even when the answer is "nothing found"), 2 could not
measure — no credentials, or the read failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _client():
    """A service-key client, from the environment (`.env` on the box)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:                                    # pragma: no cover
        pass
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("no SUPABASE_URL / SUPABASE_SERVICE_KEY in the environment — nothing to reach",
              file=sys.stderr)
        sys.exit(2)
    return create_client(url, key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--school-id", default=None,
                        help="only this school's papers")
    parser.add_argument("--json", default=None,
                        help="also write the full report here")
    parser.add_argument("--pupils", action="store_true",
                        help="name every affected pupil (the report holds them either way)")
    args = parser.parse_args(argv)

    from app.services import anti_cheat_service

    report = anti_cheat_service.unmonitored_report(
        _client(), school_id=args.school_id)

    charged = sum(1 for row in report["rows"] if row.get("charged"))
    holding = [paper for paper in report["papers"] if paper["recorded"]]
    print(f"papers with anti-cheat off                : {len(report['papers'])}")
    print(f"  of those, holding recorded rows         : {len(holding)}")
    print(f"affected pupils (paper × pupil)           : {len(report['pupils'])}")
    print(f"rows recorded                             : {report['total']} "
          f"({charged} charged, {report['total'] - charged} recorded only)")
    print("nothing was changed: no score, no penalty, no row\n")

    for paper in report["papers"]:
        print(f"  {paper['recorded']:>4} row(s)  {paper.get('title') or paper['id']}")
    if args.pupils:
        print()
        for line in report["pupils"]:
            kinds = ", ".join(str(k) for k in line["kinds"])
            print(f"  {line.get('name') or line.get('user_id')} · "
                  f"{line.get('exam_title') or line.get('exam_id')} · "
                  f"{line['recorded']} row(s), {line['charged']} charged ({kinds})")
            print(f"      first {line.get('first_at')}  last {line.get('last_at')}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nfull report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
