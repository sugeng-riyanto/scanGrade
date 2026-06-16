"""Run OMR test: generate -> upload -> result. Usage: python test_omr_run.py"""
import os
import requests

s = requests.Session()
s.post("https://scangrade.web.id/auth/login",
       json={"email": "superadmin@scan-grade.app", "password": "superadmin123"})

os.system("python test_scripts/generate_test_data.py --count 10 "
          "--ground-truth --output /tmp/ljk_test 2>/dev/null")

files = []
for f in sorted(os.listdir("/tmp/ljk_test/")):
    if f.endswith(".jpg"):
        files.append(("images", open(f"/tmp/ljk_test/{f}", "rb")))

r = s.post("https://scangrade.web.id/super-admin/api/omr-test/batch",
           files=files).json()

print(f"\n=== OMR TEST ===")
print(f"File: {r['total']}")
print(f"Error: {r['errors']}")
print(f"NISN: {r['nisn_accuracy']}%")
print(f"Keyakinan: {r['avg_confidence']*100:.0f}%")
print(f"Skor: {r['avg_score']}%")
