# Panduan Uji OMR ScanGrade

## 1. Stres Test OMR — 500+ Scan Real

**Tujuan:** Mengukur throughput OMR processing dengan file gambar asli (bukan request kosong).

### Persiapan
```bash
# Buat dataset dari LJK asli (print + scan)
mkdir -p /opt/scangrade/test_omr/batch1
# Isi folder dengan foto LJK hasil print, scan dari berbagai HP
# Beri nama: ljk_001.jpg, ljk_002.jpg, ... ljk_100.jpg

# Buat ZIP untuk bulk test
zip -j /opt/scangrade/test_omr/batch1.zip /opt/scangrade/test_omr/batch1/*.jpg
```

### Test Script
Buat file `test_scripts/stress_omr.py`:

```python
"""Stres test OMR — 500+ scan images via Celery queue."""
import os
import time
import requests

API_URL = "https://scangrade.web.id"
TOKEN = "<ambil dari cookie>"
EXAM_ID = "<exam_id yang punya answer key>"

HEADERS = {"Cookie": f"session={TOKEN}"}

def test_single(image_path):
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"{API_URL}/api/scan/process",
            files={"image": f},
            data={"exam_id": EXAM_ID, "total_questions": 50},
            headers=HEADERS,
        )
    return resp.json()

def test_bulk(zip_path):
    with open(zip_path, "rb") as f:
        resp = requests.post(
            f"{API_URL}/api/scan/bulk",
            files={"archive": f},
            data={"exam_id": EXAM_ID, "total_questions": 50},
            headers=HEADERS,
        )
    return resp.json()

# Test timing
start = time.time()
total = 0
errors = 0

# Loop semua file
for fname in sorted(os.listdir("test_omr/batch1/")):
    if not fname.endswith(".jpg"):
        continue
    result = test_single(os.path.join("test_omr/batch1/", fname))
    total += 1
    if result.get("error"):
        errors += 1
    if total % 50 == 0:
        elapsed = time.time() - start
        print(f"{total} files: {errors} errors, {elapsed:.1f}s elapsed")

print(f"\n=== HASIL ===")
print(f"Total: {total}")
print(f"Error: {errors} ({errors/total*100:.1f}%)")
print(f"Waktu: {time.time()-start:.1f}s")
print(f"Rata-rata: {(time.time()-start)/total:.2f}s/file")
```

### Jalankan
```bash
pip install requests
python test_scripts/stress_omr.py
```

### Yang Diukur
| Metrik | Target |
|--------|--------|
| Error rate | < 5% |
| Rata-rata waktu per scan | < 5 detik (async) |
| Celery queue depth | Tidak menumpuk > 100 |
| Memory usage | < 800MB |

### Monitor Selama Test
```bash
# Terminal 1: Celery log
journalctl -u scangrade-celery -f

# Terminal 2: Resource
htop

# Terminal 3: Redis queue length
redis-cli LLEN celery
```

---

## 2. Detection Rate — Validasi Akurasi

**Tujuan:** Mengukur persentase bubble yang terbaca benar dari LJK asli.

### Metode: Ground Truth Comparison

1. **Siapkan 50-100 LJK** (print dari generator)
2. **Isi manual** dengan pensil 2B — catat jawaban asli di kertas terpisah (ground truth)
3. **Scan dengan 3-5 HP berbeda:**
   - Kamera belakang HP murah (Redmi, Oppo entry)
   - Kamera HP mid-range (Samsung A系列, iPhone SE)
   - Kamera HP flagship
   - Kondisi: siang (cahaya cukup), malam (lampu ruangan)
4. **Upload dan catat hasil deteksi**
5. **Bandingkan dengan ground truth**

### Spreadsheet Tracking
Buat Google Sheets dengan kolom:

| LJK_ID | Jawaban Asli | A_Detected | B_Detected | Correct? | Conf_Avg | NISN_GT | NISN_Detected | HP_Model | Kondisi |
|--------|-------------|------------|------------|----------|----------|---------|---------------|----------|---------|
| 001 | A,B,C,D,E | A,B,C,D,E | A,B,C,D,E | 5/5 ✓ | 0.92 | 12345678 | 12345678 | Redmi 9C | Siang |
| 002 | A,B,C,D,E | A,B,C,-,-  | A,B,C,D,E | 3/5 ⚠ | 0.45 | 87654321 | 87654321 | Redmi 9C | Malam |

### Hitung Metrik
```python
# Hitung detection rate
total_soal = 50 * 100  # 50 soal x 100 LJK
benar = 4500  # contoh
akurasi = benar / total_soal * 100
print(f"Akurasi bubble: {akurasi:.1f}%")

# Hitung NISN accuracy
nisn_benar = 85  # 85 dari 100
print(f"Akurasi NISN: {nisn_benar}%")

# Hitung per HP
# iPhone: 98%, Samsung: 95%, Redmi: 88%
```

### Target
| Metrik | Minimum | Target |
|--------|---------|--------|
| Akurasi bubble (total) | 90% | 97% |
| Akurasi bubble (cahaya cukup) | 95% | 99% |
| Akurasi NISN | 85% | 95% |
| False positive (bubble kosong terdeteksi) | < 2% | < 0.5% |
| False negative (bubble terisi tidak terbaca) | < 5% | < 2% |

### Jika Akurasi Rendah — Cek Penyebab
```bash
# Lihat debug image untuk LJK yang gagal
# Buka: https://scangrade.web.id/teacher/scan
# Upload ulang LJK yang error, screenshot debug image
# Analisa: apakah registration mark terdeteksi? Grid posisi meleset?
```

---

## 3. Kalibrasi Geometry — Uji Multi-HP

**Tujuan:** Memastikan grid posisi bubble benar di berbagai resolusi kamera.

### Persiapan
Print 1 LJK referensi — isi penuh semua bubble kolom pertama (soal 1-25, opsi A).

### Uji 5 HP Berbeda
| HP | Resolusi | Hasil (soal 1 terdeteksi?) | Catatan |
|----|----------|---------------------------|---------|
| iPhone 15 | 48MP | ? | |
| Samsung A54 | 50MP | ? | |
| Redmi 12C | 8MP | ? | |
| Xiaomi Note 12 | 108MP | ? | |
| Oppo A78 | 50MP | ? | |

### Auto-Calibration Script (Jika Perlu)
Jika posisi meleset, jalankan ini untuk mencari offset optimal:

```python
# test_scripts/calibrate_geometry.py
"""Cari offset X/Y optimal untuk grid OMR."""
import cv2
import numpy as np
from app.services.omr_service import *

image_path = "test_omr/calibration/reference.jpg"
img = cv2.imread(image_path)

# Coba berbagai offset
for dx in range(-10, 11, 2):
    for dy in range(-10, 11, 2):
        # Apply offset
        test_scan(img, offset_x=dx, offset_y=dy)
        # Hitung berapa bubble terbaca
        score = evaluate_detection(result)
        print(f"Offset({dx},{dy}): score={score}")
```

### Kalibrasi Otomatis
Save hasil terbaik sebagai JSON, lalu update `omr_service.py`:

```json
{
  "redmi_9c": {"dx": 0, "dy": 2},
  "iphone_15": {"dx": -1, "dy": 0},
  "default": {"dx": 0, "dy": 0}
}
```

---

## 4. Kesimpulan — Checklist Go-Live

- [ ] **Stres test**: 500 scan < 5% error, memory < 800MB
- [ ] **Akurasi bubble**: > 95% (minimal), > 97% (target)
- [ ] **Akurasi NISN**: > 90%
- [ ] **3 HP berbeda**: hasil konsisten
- [ ] **Cahaya rendah**: masih > 85%
- [ ] **Celery queue**: tidak overflow
- [ ] **Cleanup**: file temp terhapus otomatis

Jika semua checklist terpenuhi, OMR siap produksi untuk 1-2 sekolah.
