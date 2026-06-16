"""Run OMR test: generate → upload → result. Usage: python test_omr_run.py"""
import os,requests; s=requests.Session()
s.post('https://scangrade.web.id/auth/login',json={'email':'superadmin@scan-grade.app','password':'superadmin123'})
os.system('python test_scripts/generate_test_data.py --count 10 --ground-truth --output /tmp/ljk_test 2>/dev/null')
files=[('images',open(f'/tmp/ljk_test/{f}','rb')) for f in sorted(os.listdir('/tmp/ljk_test/')) if f.endswith('.jpg')]
r=s.post('https://scangrade.web.id/super-admin/api/omr-test/batch',files=files).json()
print(f"\n=== OMR TEST ===\nFile: {r['total']}\nError: {r['errors']}\nNISN: {r['nisn_accuracy']}%\nKeyakinan: {r['avg_confidence']*100:.0f}%\nSkor: {r['avg_score']}%")
