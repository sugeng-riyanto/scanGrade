"""Stres test OMR — kirim N scan via Celery queue, ukur throughput + error rate.
Usage:
  python test_scripts/stress_omr.py --dir test_omr/batch1 --exam <exam_id> [--bulk]
  python test_scripts/stress_omr.py --dir test_omr/batch1 --url https://scangrade.web.id
"""
import os
import sys
import json
import time
import argparse
import requests

API_URL = "https://scangrade.web.id"
HEADERS = {}


def login(email, password):
    """Login dan simpan session cookie."""
    s = requests.Session()
    resp = s.post(f"{API_URL}/auth/login", json={"email": email, "password": password})
    if resp.ok:
        HEADERS["Cookie"] = "; ".join(f"{k}={v}" for k, v in s.cookies.get_dict().items())
        print(f"✅ Login ok: {email}")
    else:
        print(f"❌ Login gagal: {resp.text}")
        sys.exit(1)


def test_single(image_path, exam_id, total_q=50):
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"{API_URL}/api/scan/process",
            files={"image": f},
            data={"exam_id": exam_id, "total_questions": total_q},
            headers=HEADERS,
        )
    return resp.json()


def test_bulk(zip_path, exam_id, total_q=50):
    with open(zip_path, "rb") as f:
        resp = requests.post(
            f"{API_URL}/api/scan/bulk",
            files={"archive": f},
            data={"exam_id": exam_id, "total_questions": total_q},
            headers=HEADERS,
        )
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="OMR Stres Test")
    parser.add_argument("--dir", help="Folder berisi file .jpg untuk di-test")
    parser.add_argument("--zip", help="File ZIP untuk bulk test")
    parser.add_argument("--exam", default="", help="Exam ID untuk grading")
    parser.add_argument("--total", type=int, default=50, help="Jumlah soal per LJK")
    parser.add_argument("--bulk", action="store_true", help="Gunakan endpoint bulk")
    parser.add_argument("--url", default=API_URL, help="Base URL")
    parser.add_argument("--email", default="superadmin@scan-grade.app", help="Email login")
    parser.add_argument("--password", default="superadmin123", help="Password login")
    args = parser.parse_args()

    global API_URL
    API_URL = args.url

    # Login
    login(args.email, args.password)

    if args.bulk and args.zip:
        print(f"\n📦 Bulk test: {args.zip}")
        start = time.time()
        result = test_bulk(args.zip, args.exam, args.total)
        elapsed = time.time() - start

        if result.get("error"):
            print(f"❌ Error: {result['error']}")
            return

        print(f"\n=== HASIL BULK TEST ===")
        print(f"Total: {result.get('total', 0)}")
        print(f"Processed: {result.get('processed', 0)}")
        print(f"Failed: {result.get('failed', 0)}")
        print(f"Waktu: {elapsed:.1f}s")
        print(f"Rata-rata: {elapsed/max(result.get('total',1),1):.2f}s/file")

        # Aggregate
        ok = [r for r in result.get("results", []) if not r.get("error")]
        if ok:
            avg_conf = sum(r.get("avg_confidence", 0) for r in ok) / len(ok)
            nisn_ok = sum(1 for r in ok if r.get("nisn", "?").count("?") == 0)
            print(f"Akuras NISN: {nisn_ok}/{len(ok)} ({nisn_ok/len(ok)*100:.1f}%)")
            print(f"Rata-rata keyakinan: {avg_conf*100:.1f}%")

    elif args.dir:
        images = sorted([f for f in os.listdir(args.dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))])
        print(f"\n📸 Single test: {len(images)} files from {args.dir}")

        total = 0
        errors = 0
        start = time.time()

        for fname in images:
            path = os.path.join(args.dir, fname)
            result = test_single(path, args.exam, args.total)
            total += 1
            if result.get("error"):
                errors += 1
            if total % 10 == 0:
                elapsed = time.time() - start
                print(f"  {total}/{len(images)} — {errors} errors, {elapsed:.1f}s")

        elapsed = time.time() - start
        print(f"\n=== HASIL STREST TEST ===")
        print(f"Total: {total}")
        print(f"Errors: {errors} ({errors/max(total,1)*100:.1f}%)")
        print(f"Waktu: {elapsed:.1f}s")
        print(f"Rata-rata: {elapsed/max(total,1):.2f}s/file")
        print(f"Throughput: {total/elapsed:.1f} file/detik")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
