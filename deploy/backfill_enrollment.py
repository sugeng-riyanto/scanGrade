#!/usr/bin/env python3
"""Reconstruct per-year membership from the data that is already on file.

THIS IS THE RISKIEST DATA MIGRATION IN THE PROJECT: it touches rows that stand
behind marks already awarded to real pupils. It is therefore **dry-run by
default** and prints, per school, exactly what it would write before it writes
anything. `--apply` is required to change a single row.

What it can prove, and what it refuses to guess:

* **A class's year.** A class whose `school_year_id` is already set is left
  alone. A class with none is dated from the `created_at` of the exams attached
  to it: if every one of its papers falls inside exactly one `school_years`
  window, that is the class's year. If the papers span two windows, or the class
  has no paper to date it, it is reported as *ambiguous* and left NULL — a class
  put in the wrong year would move a whole cohort's history, which is the one
  mistake this script must not make.
* **A pupil's membership today.** One `student_enrollment` row per pupil, naming
  the class they sit in now and that class's year, status `aktif`. It does NOT
  invent prior years: no row anywhere records where a pupil was before, so
  fabricating one would be a lie that later reads as history.
* **A paper's year.** `exams.school_year_id` is derived from the classes the
  paper is attached to, but only when they all agree; a paper spanning years is
  reported and left NULL.

Usage:
    python deploy/backfill_enrollment.py                 # dry run, prints a plan
    python deploy/backfill_enrollment.py --apply         # writes, after a backup
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except Exception:  # pragma: no cover - dotenv is optional on the box
    pass


def _client():
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("no SUPABASE_URL / SUPABASE_SERVICE_KEY in the environment — nothing to reach")
        sys.exit(2)
    return create_client(url, key)


def _rows(sb, table, select="*"):
    """Rows for `table`, or for the columns that exist yet.

    Migration 046 adds `exams.school_year_id` and `classes.school_year_id` (the
    latter already exists). A dry run is most useful *before* the migration is
    applied, so a column the box does not have yet degrades to `*` rather than
    aborting the whole report.
    """
    try:
        return sb.table(table).select(select).execute().data or []
    except Exception as e:
        if "42703" not in str(e):
            raise
        return sb.table(table).select("*").execute().data or []


def _class_ids(exam) -> list:
    """`class_ids` is jsonb and arrives either as a list or as a JSON string."""
    raw = (exam or {}).get("class_ids") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = []
    return [str(c) for c in raw if c]


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _window_contains(year, dt) -> bool:
    start, end = _parse_dt(year.get("start_date")), _parse_dt(year.get("end_date"))
    if not (start and end and dt):
        return False
    # The stored end date is a calendar day; a paper written that evening is
    # still inside the year, so the end is inclusive to its last second.
    return start <= dt <= end.replace(hour=23, minute=59, second=59)


def merge_pupils(students, profiles) -> list:
    """One row per pupil, with the class both pointers agree on, or the one that exists.

    A pupil's class lives on TWO tables — `students.class_id` and
    `profiles.class_id` — and on the live project they disagree: 598 of 806
    `students` rows carry a class while only 305 of 821 `profiles(murid)` rows
    do, and 15 pupils exist in `profiles` with no `students` row at all. Promotion
    writes both, so the divergence comes from imports that wrote one. Whichever
    pointer holds the class is used; a pupil whose two pointers name *different*
    classes is reported, not silently resolved to one of them.
    """
    by_id = {}
    conflicts = []
    for s in students:
        by_id[str(s["id"])] = {"id": s["id"], "class_id": s.get("class_id")}
    for p in profiles:
        pid = str(p["id"])
        pid_class = p.get("class_id")
        if pid not in by_id:
            by_id[pid] = {"id": p["id"], "class_id": pid_class}
            continue
        known = by_id[pid]["class_id"]
        if known and pid_class and str(known) != str(pid_class):
            conflicts.append({"student_id": p["id"],
                              "students_class": known, "profiles_class": pid_class})
        if not known:
            by_id[pid]["class_id"] = pid_class
    return list(by_id.values()), conflicts


def plan_school(school_id, years, classes, exams, students,
                assume_active_year=False) -> dict:
    """What this script would write for one school, without writing it.

    `assume_active_year` is OFF by default and must be asked for. When it is on,
    a class with no year and no paper to date it takes the school's *active*
    year — the year in progress is the only one a class in current use can
    belong to. Even then it is named in the report as an assumption, not folded
    into the proven count, so an operator can see exactly which rows came from
    it. The default is the conservative one: flag and stop.
    """
    exams_by_class = defaultdict(list)
    for e in exams:
        for cid in _class_ids(e):
            exams_by_class[cid].append(e)

    active_year = next((y for y in years if y.get("is_active")), None)
    class_year, ambiguous, assumed = {}, [], []
    for c in classes:
        cid = str(c["id"])
        if c.get("school_year_id"):
            class_year[cid] = c["school_year_id"]
            continue
        windows = set()
        for e in exams_by_class.get(cid, []):
            dt = _parse_dt(e.get("created_at"))
            for y in years:
                if _window_contains(y, dt):
                    windows.add(y["id"])
        if len(windows) == 1:
            class_year[cid] = windows.pop()
            continue
        if not windows and assume_active_year and active_year:
            class_year[cid] = active_year["id"]
            assumed.append({"name": c.get("name"), "year": active_year.get("name")})
            continue
        class_year[cid] = None
        ambiguous.append({
            "name": c.get("name"),
            "reason": ("papers span %d years" % len(windows)) if windows
                      else "no paper to date it and no year set",
        })

    class_ids_here = {str(c["id"]) for c in classes}
    enrollments, unresolved = [], []
    for s in students:
        cid = str(s.get("class_id") or "")
        if not cid:
            unresolved.append({"student_id": s["id"], "reason": "no class on file"})
            continue
        if cid not in class_ids_here:
            # A class id that is not on this school's roster: the pupil's pointer
            # is stale or belongs elsewhere. Never enrolled into a guessed year.
            unresolved.append({"student_id": s["id"],
                               "reason": "class is not in this school's roster"})
            continue
        year_id = class_year.get(cid)
        if not year_id:
            unresolved.append({"student_id": s["id"], "reason": "class has no year"})
            continue
        enrollments.append({"student_id": s["id"], "class_id": cid, "school_year_id": year_id})

    exam_years, undated = {}, []
    for e in exams:
        ys = {class_year.get(cid) for cid in _class_ids(e)}
        ys.discard(None)
        if len(ys) == 1:
            exam_years[e["id"]] = ys.pop()
        else:
            undated.append(e["id"])

    to_date = {cid: y for cid, y in class_year.items()
               if y and not any(str(c["id"]) == cid and c.get("school_year_id") for c in classes)}
    return {
        "school_id": school_id,
        "years": len(years),
        "classes": len(classes),
        "class_year": class_year,
        "classes_to_date": to_date,
        "ambiguous": ambiguous,
        "assumed": assumed,
        "pupils": len(students),
        "pupils_with_class": sum(1 for s in students if s.get("class_id")),
        "enrollments": enrollments,
        "unresolved": unresolved,
        "exam_years": exam_years,
        "undated_exams": undated,
    }


def apply_plan(sb, plan) -> dict:
    """Write one school's plan. Idempotent: enrollment conflicts update in place."""
    written = {"classes": 0, "enrollments": 0, "exams": 0}
    for cid, year_id in plan["classes_to_date"].items():
        sb.table("classes").update({"school_year_id": year_id}).eq("id", cid).execute()
        written["classes"] += 1
    for row in plan["enrollments"]:
        sb.table("student_enrollment").upsert(
            {"school_id": plan["school_id"], **row, "status": "aktif"},
            on_conflict="student_id,school_year_id").execute()
        written["enrollments"] += 1
    for exam_id, year_id in plan["exam_years"].items():
        sb.table("exams").update({"school_year_id": year_id}).eq("id", exam_id).execute()
        written["exams"] += 1
    return written


def _group(rows):
    out = defaultdict(list)
    for r in rows:
        out[r.get("school_id")].append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill student_enrollment (dry run by default).")
    ap.add_argument("--apply", action="store_true",
                    help="write the plan (default is a dry run that writes nothing)")
    ap.add_argument("--assume-active-year", action="store_true",
                    help="give a class with no year and no paper to date it the "
                         "school's ACTIVE year (named in the report as an assumption)")
    args = ap.parse_args()

    sb = _client()
    years = _group(_rows(sb, "school_years"))
    classes = _group(_rows(sb, "classes", "id,school_id,name,school_year_id,academic_year"))
    exams = _group(_rows(sb, "exams", "id,school_id,class_ids,created_at,school_year_id"))
    students = _group(_rows(sb, "students", "id,school_id,class_id,status"))
    pupils = _group([r for r in _rows(sb, "profiles", "id,school_id,class_id,role")
                     if r.get("role") == "murid"])
    school_ids = sorted(set(years) | set(classes))

    print("=" * 72)
    print("BACKFILL student_enrollment — %s" % ("APPLY" if args.apply else "DRY RUN"))
    print("=" * 72)

    total = Counter()
    for sid in school_ids:
        merged, conflicts = merge_pupils(students.get(sid, []), pupils.get(sid, []))
        plan = plan_school(sid, years.get(sid, []), classes.get(sid, []),
                           exams.get(sid, []), merged,
                           assume_active_year=args.assume_active_year)
        plan["pointer_conflicts"] = conflicts
        print("\n-- school %s ----------------------------------------------" % str(sid)[:8])
        print("   years=%d classes=%d to-date=%d  pupils=%d(with class %d)  enrollments=%d  exams dated=%d"
              % (plan["years"], plan["classes"], len(plan["classes_to_date"]),
                 plan["pupils"], plan["pupils_with_class"],
                 len(plan["enrollments"]), len(plan["exam_years"])))
        for c in plan["pointer_conflicts"][:5]:
            print("   CONFLICT pupil %s  students=%s profiles=%s"
                  % (str(c["student_id"])[:8], str(c["students_class"])[:8],
                     str(c["profiles_class"])[:8]))
        for a in plan["ambiguous"][:8]:
            print("   AMBIGUOUS class %-14s %s" % (a["name"], a["reason"]))
        for a in plan["assumed"][:8]:
            print("   ASSUMED   class %-14s -> active year %s" % (a["name"], a["year"]))
        for u in plan["unresolved"][:8]:
            print("   UNRESOLVED pupil %s  %s" % (str(u["student_id"])[:8], u["reason"]))
        if plan["undated_exams"]:
            print("   UNDATED exams (span years): %d" % len(plan["undated_exams"]))
        total["classes"] += len(plan["classes_to_date"])
        total["enrollments"] += len(plan["enrollments"])
        total["exams"] += len(plan["exam_years"])
        total["ambiguous"] += len(plan["ambiguous"])
        total["unresolved"] += len(plan["unresolved"])

        if args.apply:
            written = apply_plan(sb, plan)
            print("   WRITTEN classes=%d enrollments=%d exams=%d"
                  % (written["classes"], written["enrollments"], written["exams"]))

    print("\n" + "=" * 72)
    print("TOTALS  classes=%d  enrollments=%d  exams=%d  ambiguous=%d  unresolved=%d"
          % (total["classes"], total["enrollments"], total["exams"],
             total["ambiguous"], total["unresolved"]))
    if not args.apply:
        print("Nothing was written. Re-run with --apply AFTER a verified snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
