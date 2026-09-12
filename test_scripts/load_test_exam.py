#!/usr/bin/env python3
"""
ScanGrade Load Test — Simulates 300 concurrent students taking an exam.

Architecture:
  1. Create N temporary student accounts (or reuse existing)
  2. All students log in simultaneously
  3. Students start exam, answer MCQ + essay questions
  4. Students sync drafts periodically (every 20-30s)
  5. Students submit exam
  6. Measure: response time, error rate, throughput

Usage:
  python test_scripts/load_test_exam.py --url http://103.93.133.193:8000 --students 300
  python test_scripts/load_test_exam.py --url http://localhost:5000 --students 50 --exam <exam_id>
  python test_scripts/load_test_exam.py --url http://localhost:5000 --students 300 --phase submit-only

Prerequisites:
  - LOAD_TEST=true must be set (bypasses CSRF + rate limits)
  - A published exam with 5 MCQ + 2 essay questions
  - Admin account for creating student accounts
"""

import os
import sys
import json
import time
import random
import string
import argparse
import threading
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Dict, Optional

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)


# ── Configuration ───────────────────────────────────────────────

MCQ_ANSWERS = ["A", "B", "C", "D"]
ESSAY_ANSWERS = [
    "Persamaan linear dua variabel adalah persamaan yang mengandung dua variabel dengan pangkat satu. Contoh: 2x + 3y = 7. Untuk menyelesaikannya bisa menggunakan metode substitusi atau eliminasi.",
    "Eliminasi: 2x + y = 5 dan x - y = 1. Jika ditambah: 3x = 6, x = 2. Kemudian y = 1. Jawaban: x=2, y=1.",
    "Substitusi: dari x + y = 5 dapat x = 5 - y. Substitusi ke 2x - y = 1: 2(5-y) - y = 1, 10 - 3y = 1, y = 3, x = 2.",
    "Grafik: plot kedua garis pada kartesius. Titik potong adalah solusi sistem persamaan linear.",
    "Metode Cramer: determinan D = |2 1; 1 -1| = -3. Dx = |5 1; 1 -1| = -6. Dy = |2 5; 1 1| = 3. x = Dx/D = 2, y = Dy/D = -1.",
]

SYNC_INTERVAL_MIN = 15  # seconds between sync-draft calls
SYNC_INTERVAL_MAX = 30


# ── Data Classes ────────────────────────────────────────────────

@dataclass
class StudentMetrics:
    """Metrics for a single student."""
    student_id: str = ""
    login_time: float = 0
    start_exam_time: float = 0
    sync_times: List[float] = field(default_factory=list)
    submit_time: float = 0
    submit_score: float = 0
    errors: List[str] = field(default_factory=list)
    http_status_codes: List[int] = field(default_factory=list)


@dataclass
class TestResult:
    """Aggregated test results."""
    total_students: int = 0
    successful_logins: int = 0
    successful_submits: int = 0
    failed_submits: int = 0
    login_times: List[float] = field(default_factory=list)
    submit_times: List[float] = field(default_factory=list)
    sync_times: List[float] = field(default_factory=list)
    all_status_codes: List[int] = field(default_factory=list)
    all_errors: List[str] = field(default_factory=list)
    start_time: float = 0
    end_time: float = 0


# ── HTTP Session Factory ────────────────────────────────────────

def create_session(base_url: str) -> requests.Session:
    """Create a requests session with connection pooling."""
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=10,
        pool_maxsize=10,
        max_retries=0,  # Don't retry on load test
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# ── Student Worker ──────────────────────────────────────────────

def student_worker(
    base_url: str,
    student_email: str,
    password: str,
    exam_id: str,
    metrics: StudentMetrics,
    num_mcq: int = 5,
    num_essay: int = 2,
    phase: str = "full",
):
    """Simulate a single student taking an exam end-to-end."""
    session = create_session(base_url)
    uid = student_email.split("@")[0]

    try:
        # ── Phase 1: Login ──
        if phase in ("full", "login-only"):
            t0 = time.time()

            # Get CSRF token
            resp = session.get(f"{base_url}/auth/login-user", timeout=10)
            csrf = ""
            if 'csrf-token" content="' in resp.text:
                csrf = resp.text.split('csrf-token" content="')[1].split('"')[0]

            # Login
            resp = session.post(
                f"{base_url}/auth/login-user",
                data={"email": student_email, "password": password},
                headers={"X-CSRF-Token": csrf},
                allow_redirects=False,
                timeout=10,
            )
            metrics.login_time = time.time() - t0
            metrics.http_status_codes.append(resp.status_code)

            if resp.status_code not in (302, 200):
                metrics.errors.append(f"Login failed: {resp.status_code}")
                return

            # Follow redirect to dashboard
            session.get(f"{base_url}/student/dashboard", timeout=10)

        if phase in ("submit-only", "sync-only"):
            # For submit-only, we need to login first
            if phase == "submit-only":
                t0 = time.time()
                resp = session.get(f"{base_url}/auth/login-user", timeout=10)
                csrf = ""
                if 'csrf-token" content="' in resp.text:
                    csrf = resp.text.split('csrf-token" content="')[1].split('"')[0]
                resp = session.post(
                    f"{base_url}/auth/login-user",
                    data={"email": student_email, "password": password},
                    headers={"X-CSRF-Token": csrf},
                    allow_redirects=False,
                    timeout=10,
                )
                metrics.login_time = time.time() - t0

        if phase == "login-only":
            return

        # ── Phase 2: Start Exam ──
        t0 = time.time()
        resp = session.get(f"{base_url}/student/exams/{exam_id}", timeout=15)
        metrics.start_exam_time = time.time() - t0
        metrics.http_status_codes.append(resp.status_code)

        # Get CSRF token for API calls
        csrf = ""
        if 'csrf-token" content="' in resp.text:
            csrf = resp.text.split('csrf-token" content="')[1].split('"')[0]

        if phase == "sync-only":
            # Just sync for a while
            for _ in range(3):
                time.sleep(random.uniform(SYNC_INTERVAL_MIN, SYNC_INTERVAL_MAX))
                _do_sync(session, base_url, exam_id, csrf, num_mcq, num_essay, metrics)
            return

        # ── Phase 3: Answer Questions + Sync ──
        answers = {}
        # MCQ answers
        for i in range(num_mcq):
            answers[str(i)] = random.choice(MCQ_ANSWERS)

        # Essay answers
        for i in range(num_essay):
            answers[str(num_mcq + i)] = random.choice(ESSAY_ANSWERS)

        # Simulate answering over time (spread out to trigger sync-draft)
        answering_duration = random.uniform(60, 180)  # 1-3 minutes
        sync_count = 0
        start_answering = time.time()

        while time.time() - start_answering < answering_duration:
            # Sync draft periodically
            _do_sync(session, base_url, exam_id, csrf, num_mcq, num_essay, metrics)
            sync_count += 1

            # Gradually reveal answers (simulate real answering)
            progress = min(1.0, (time.time() - start_answering) / answering_duration)
            num_revealed = int(progress * (num_mcq + num_essay))
            for i in range(min(num_revealed, num_mcq)):
                answers[str(i)] = random.choice(MCQ_ANSWERS)
            for i in range(min(num_revealed - num_mcq, num_essay)):
                if num_revealed > num_mcq:
                    answers[str(num_mcq + i)] = random.choice(ESSAY_ANSWERS)

            time.sleep(random.uniform(SYNC_INTERVAL_MIN, SYNC_INTERVAL_MAX))

        # Final sync before submit
        _do_sync(session, base_url, exam_id, csrf, num_mcq, num_essay, metrics)

        # ── Phase 4: Submit ──
        if phase in ("full", "submit-only"):
            t0 = time.time()
            resp = session.post(
                f"{base_url}/student/exams/{exam_id}/submit",
                json={"answers": answers},
                headers={
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrf,
                },
                timeout=30,
            )
            metrics.submit_time = time.time() - t0
            metrics.http_status_codes.append(resp.status_code)

            try:
                data = resp.json()
                metrics.submit_score = data.get("score", 0)
                if resp.status_code != 200 or data.get("error"):
                    metrics.errors.append(f"Submit error: {data.get('error', resp.status_code)}")
            except Exception:
                metrics.errors.append(f"Submit parse error: {resp.text[:100]}")

    except requests.exceptions.Timeout:
        metrics.errors.append("Request timeout")
    except requests.exceptions.ConnectionError:
        metrics.errors.append("Connection error")
    except Exception as e:
        metrics.errors.append(f"Unexpected: {str(e)[:100]}")


def _do_sync(session, base_url, exam_id, csrf, num_mcq, num_essay, metrics):
    """Do a single sync-draft call."""
    t0 = time.time()
    sync_answers = {}
    for i in range(num_mcq):
        sync_answers[str(i)] = random.choice(MCQ_ANSWERS)
    for i in range(num_essay):
        sync_answers[str(num_mcq + i)] = random.choice(ESSAY_ANSWERS)

    try:
        resp = session.post(
            f"{base_url}/api/student/sync-draft",
            json={"exam_id": exam_id, "answers": sync_answers, "light": True},
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
            },
            timeout=10,
        )
        elapsed = time.time() - t0
        metrics.sync_times.append(elapsed)
        metrics.http_status_codes.append(resp.status_code)
    except Exception as e:
        metrics.errors.append(f"Sync error: {str(e)[:50]}")


# ── Account Creation ────────────────────────────────────────────

def create_test_accounts(base_url: str, admin_email: str, admin_pass: str, count: int) -> List[Dict]:
    """Create test student accounts via admin API."""
    session = create_session(base_url)

    # Login as admin
    resp = session.get(f"{base_url}/auth/login", timeout=10)
    csrf = ""
    if 'csrf-token" content="' in resp.text:
        csrf = resp.text.split('csrf-token" content="')[1].split('"')[0]

    resp = session.post(
        f"{base_url}/auth/login",
        data={"email": admin_email, "password": admin_pass},
        headers={"X-CSRF-Token": csrf},
        allow_redirects=False,
        timeout=10,
    )
    if resp.status_code != 302:
        print(f"Admin login failed: {resp.status_code}")
        return []

    # Create student accounts
    accounts = []
    password = "loadtest123"

    for i in range(count):
        email = f"loadtest_{i:04d}@scan-grade.app"
        name = f"Load Test Student {i:04d}"

        try:
            # Get fresh CSRF
            resp = session.get(f"{base_url}/admin-sekolah/students", timeout=10)
            csrf = ""
            if 'csrf-token" content="' in resp.text:
                csrf = resp.text.split('csrf-token" content="')[1].split('"')[0]

            resp = session.post(
                f"{base_url}/admin-sekolah/students/create",
                data={
                    "name": name,
                    "email": email,
                    "password": password,
                    "nisn": f"{random.randint(1000000000, 9999999999)}",
                },
                headers={"X-CSRF-Token": csrf},
                allow_redirects=False,
                timeout=10,
            )

            if resp.status_code in (302, 200):
                accounts.append({"email": email, "password": password})
                if (i + 1) % 50 == 0:
                    print(f"  Created {i + 1}/{count} accounts")
            else:
                # Account might already exist — still usable
                accounts.append({"email": email, "password": password})

        except Exception as e:
            accounts.append({"email": email, "password": password})
            # Continue — account might exist from previous run

    print(f"  ✅ {len(accounts)} accounts ready")
    return accounts


# ── Results Printer ─────────────────────────────────────────────

def print_results(result: TestResult, phase: str):
    """Print formatted test results."""
    duration = result.end_time - result.start_time

    print("\n" + "=" * 60)
    print("  SCANGRADE LOAD TEST RESULTS")
    print("=" * 60)
    print(f"  Phase:        {phase}")
    print(f"  Students:     {result.total_students}")
    print(f"  Duration:     {duration:.1f}s")
    print(f"  Throughput:   {result.total_students / max(duration, 1):.1f} students/s")
    print()

    # Login metrics
    if result.login_times:
        print("  LOGIN")
        print(f"    Success:    {result.successful_logins}/{result.total_students}")
        print(f"    Avg time:   {statistics.mean(result.login_times) * 1000:.0f}ms")
        print(f"    P50 time:   {statistics.median(result.login_times) * 1000:.0f}ms")
        print(f"    P95 time:   {sorted(result.login_times)[int(len(result.login_times) * 0.95)] * 1000:.0f}ms")
        print(f"    P99 time:   {sorted(result.login_times)[int(len(result.login_times) * 0.99)] * 1000:.0f}ms")
        print(f"    Max time:   {max(result.login_times) * 1000:.0f}ms")
        print()

    # Submit metrics
    if result.submit_times:
        print("  SUBMIT EXAM")
        print(f"    Success:    {result.successful_submits}/{result.total_students}")
        print(f"    Failed:     {result.failed_submits}")
        print(f"    Avg time:   {statistics.mean(result.submit_times) * 1000:.0f}ms")
        print(f"    P50 time:   {statistics.median(result.submit_times) * 1000:.0f}ms")
        print(f"    P95 time:   {sorted(result.submit_times)[int(len(result.submit_times) * 0.95)] * 1000:.0f}ms")
        print(f"    Max time:   {max(result.submit_times) * 1000:.0f}ms")
        print()

    # Sync metrics
    if result.sync_times:
        print("  SYNC-DRAFT")
        print(f"    Total syncs: {len(result.sync_times)}")
        print(f"    Avg time:   {statistics.mean(result.sync_times) * 1000:.0f}ms")
        print(f"    P95 time:   {sorted(result.sync_times)[int(len(result.sync_times) * 0.95)] * 1000:.0f}ms")
        print()

    # HTTP status codes
    if result.all_status_codes:
        status_counts = {}
        for code in result.all_status_codes:
            status_counts[code] = status_counts.get(code, 0) + 1
        print("  HTTP STATUS CODES")
        for code in sorted(status_counts.keys()):
            emoji = "✅" if 200 <= code < 400 else "⚠️" if code == 429 else "❌"
            print(f"    {emoji} {code}: {status_counts[code]}")
        print()

    # Errors
    if result.all_errors:
        print("  ERRORS")
        error_counts = {}
        for err in result.all_errors:
            key = err[:60]
            error_counts[key] = error_counts.get(key, 0) + 1
        for err, count in sorted(error_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"    [{count}x] {err}")
        print()

    print("=" * 60)


# ── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ScanGrade Load Test — Simulate concurrent students taking exams",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with 300 students on production
  python test_scripts/load_test_exam.py --url https://scangrade.web.id --students 300

  # Test locally with 50 students
  python test_scripts/load_test_exam.py --url http://localhost:5000 --students 50

  # Submit-only phase (students already logged in)
  python test_scripts/load_test_exam.py --url http://localhost:5000 --students 300 --phase submit-only

  # Login-only stress test
  python test_scripts/load_test_exam.py --url http://localhost:5000 --students 300 --phase login-only

  # Create test accounts first, then load test
  python test_scripts/load_test_exam.py --url https://scangrade.web.id --create-accounts 300
        """,
    )
    parser.add_argument("--url", default="http://localhost:5000", help="Base URL of the app")
    parser.add_argument("--students", type=int, default=100, help="Number of concurrent students")
    parser.add_argument("--exam", default="", help="Exam ID (auto-detect if empty)")
    parser.add_argument("--phase", choices=["full", "login-only", "submit-only", "sync-only"],
                        default="full", help="Test phase")
    parser.add_argument("--mcq", type=int, default=5, help="Number of MCQ questions")
    parser.add_argument("--essay", type=int, default=2, help="Number of essay questions")
    parser.add_argument("--admin-email", default="admin_smp@scan-grade.app", help="Admin email for account creation")
    parser.add_argument("--admin-pass", default="demo123", help="Admin password")
    parser.add_argument("--student-email", default="", help="Single student email (for quick test)")
    parser.add_argument("--student-pass", default="demo123", help="Student password")
    parser.add_argument("--create-accounts", type=int, default=0, help="Create N test accounts first")
    parser.add_argument("--ramp-up", type=float, default=0, help="Ramp-up time in seconds (0 = all at once)")
    parser.add_argument("--output", default="", help="Save JSON results to file")
    args = parser.parse_args()

    print("=" * 60)
    print("  SCANGRADE LOAD TEST")
    print("=" * 60)
    print(f"  URL:       {args.url}")
    print(f"  Students:  {args.students}")
    print(f"  Phase:     {args.phase}")
    print(f"  Questions: {args.mcq} MCQ + {args.essay} essay")
    print()

    # ── Step 1: Create accounts if requested ──
    accounts = []
    if args.create_accounts > 0:
        print("📦 Creating test accounts...")
        accounts = create_test_accounts(
            args.url, args.admin_email, args.admin_pass, args.create_accounts
        )
        # Save accounts for reuse
        accounts_file = "test_scripts/load_test_accounts.json"
        with open(accounts_file, "w") as f:
            json.dump(accounts, f)
        print(f"  Saved to {accounts_file}")
        print()

    # ── Step 2: Load existing accounts ──
    if not accounts:
        accounts_file = "test_scripts/load_test_accounts.json"
        if os.path.exists(accounts_file):
            with open(accounts_file) as f:
                accounts = json.load(f)
            print(f"  Loaded {len(accounts)} existing accounts from {accounts_file}")

    if args.student_email:
        accounts = [{"email": args.student_email, "password": args.student_pass}] * args.students

    if not accounts:
        print("❌ No accounts available. Use --create-accounts N or --student-email")
        print("   Or create accounts manually via admin dashboard")
        sys.exit(1)

    # ── Step 3: Auto-detect exam ID ──
    exam_id = args.exam
    if not exam_id:
        print("🔍 Auto-detecting exam ID...")
        try:
            # Login as teacher and find exam
            s = create_session(args.url)
            resp = s.get(f"{args.url}/auth/login-user", timeout=10)
            csrf = ""
            if 'csrf-token" content="' in resp.text:
                csrf = resp.text.split('csrf-token" content="')[1].split('"')[0]
            resp = s.post(
                f"{args.url}/auth/login-user",
                data={"email": "guru_mtk_smp@scan-grade.app", "password": "demo123"},
                headers={"X-CSRF-Token": csrf},
                allow_redirects=False,
                timeout=10,
            )
            resp = s.get(f"{args.url}/teacher/exams", timeout=10)
            # Try to find exam ID from page
            if "/teacher/exams/" in resp.text:
                import re
                match = re.search(r'/teacher/exams/([a-f0-9-]{36})', resp.text)
                if match:
                    exam_id = match.group(1)
                    print(f"  Found exam: {exam_id}")
        except Exception as e:
            print(f"  ⚠️  Could not auto-detect exam: {e}")

    if not exam_id:
        print("❌ Exam ID required. Use --exam <uuid>")
        sys.exit(1)

    # ── Step 4: Verify LOAD_TEST mode ──
    print("⚡ Checking LOAD_TEST mode...")
    try:
        resp = requests.get(f"{args.url}/health", timeout=5)
        health = resp.json()
        redis_status = health.get("redis", {})
        print(f"  Redis: {'connected' if redis_status.get('connected') else 'not connected (memory fallback)'}")
        print(f"  Workers: {health.get('workers', '1')}")
    except Exception:
        print("  ⚠️  Could not check health endpoint")

    # ── Step 5: Run load test ──
    print(f"\n🚀 Starting load test with {args.students} students...")
    result = TestResult(total_students=args.students)
    result.start_time = time.time()

    # Trim accounts to match student count
    test_accounts = accounts[:args.students]

    with ThreadPoolExecutor(max_workers=min(args.students, 200)) as executor:
        futures = {}

        for i, account in enumerate(test_accounts):
            metrics = StudentMetrics(student_id=account["email"].split("@")[0])

            # Ramp-up: stagger starts
            if args.ramp_up > 0:
                delay = (i / args.students) * args.ramp_up
                time.sleep(delay / args.students)  # Stagger submission

            future = executor.submit(
                student_worker,
                args.url,
                account["email"],
                account["password"],
                exam_id,
                metrics,
                args.mcq,
                args.essay,
                args.phase,
            )
            futures[future] = metrics

            # Print progress every 50 students
            if (i + 1) % 50 == 0:
                elapsed = time.time() - result.start_time
                print(f"  Dispatched {i + 1}/{args.students} students ({elapsed:.1f}s)")

        # ── Collect results ──
        print("\n  Waiting for all students to complete...")
        for future in as_completed(futures):
            metrics = futures[future]
            try:
                future.result(timeout=300)  # 5 min timeout per student
            except Exception as e:
                metrics.errors.append(f"Worker crashed: {str(e)[:50]}")

            # Aggregate
            if metrics.login_time > 0:
                result.login_times.append(metrics.login_time)
                if not metrics.errors or "Login" not in metrics.errors[0]:
                    result.successful_logins += 1

            if metrics.submit_time > 0:
                result.submit_times.append(metrics.submit_time)
                if not any("Submit error" in e for e in metrics.errors):
                    result.successful_submits += 1
                else:
                    result.failed_submits += 1

            result.sync_times.extend(metrics.sync_times)
            result.all_status_codes.extend(metrics.http_status_codes)
            result.all_errors.extend(metrics.errors)

    result.end_time = time.time()

    # ── Step 6: Print results ──
    print_results(result, args.phase)

    # ── Step 7: Save JSON results ──
    if args.output:
        output_data = {
            "config": {
                "url": args.url,
                "students": args.students,
                "phase": args.phase,
                "exam_id": exam_id,
                "mcq": args.mcq,
                "essay": args.essay,
            },
            "results": {
                "duration_s": result.end_time - result.start_time,
                "successful_logins": result.successful_logins,
                "successful_submits": result.successful_submits,
                "failed_submits": result.failed_submits,
                "login_avg_ms": statistics.mean(result.login_times) * 1000 if result.login_times else 0,
                "submit_avg_ms": statistics.mean(result.submit_times) * 1000 if result.submit_times else 0,
                "sync_avg_ms": statistics.mean(result.sync_times) * 1000 if result.sync_times else 0,
                "total_syncs": len(result.sync_times),
                "total_errors": len(result.all_errors),
                "status_codes": dict(
                    sorted(
                        {c: result.all_status_codes.count(c) for c in set(result.all_status_codes)}.items()
                    )
                ),
            },
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\n📊 Results saved to {args.output}")


if __name__ == "__main__":
    main()
