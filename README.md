# ScanGrade

Sistem koreksi ujian digital — scan lembar jawaban pilihan ganda via kamera, koreksi esai dengan AI similarity scoring + teacher override, anti-cheat deteksi tab switch, analytics dashboard, dan export hasil ke XLSX/PDF.

## Fitur Utama

| Fitur | Deskripsi |
|-------|-----------|
| **Exam Builder** | Buat ujian: upload PDF soal, atur jumlah MCQ, centang tipe esai (canvas/text), tambah audio/YouTube per soal |
| **Scan & OMR** | Scan lembar jawaban via kamera, deteksi jawaban MCQ otomatis |
| **Essay Grading** | Guru mengoreksi esai langsung di canvas overlay di atas PDF soal, scoring + komentar per soal |
| **Anti-Cheat** | Deteksi tab switch (`visibilitychange`), penalty dikalkulasi server-side, rate-limited logging |
| **Offline-First** | Auto-save ke localStorage, sync ke server saat online — aman untuk 500 siswa dengan WiFi tidak stabil |
| **Analytics** | Dashboard interaktif dengan Chart.js: distribusi nilai, soal tersulit, per-band stats |
| **Export** | Download hasil ujian ke XLSX (openpyxl) atau PDF (ReportLab) |
| **Publish** | Publish nilai ke siswa via login atau WhatsApp (Fonnte) |

## Tech Stack

| Layer | Teknologi |
|-------|-----------|
| Backend | Flask 3.1.1 (app factory + blueprints) |
| Database | Supabase PostgreSQL + Row Level Security |
| Auth | Supabase Auth (email/password + JWT) |
| Storage | Supabase Storage (bucket: exam-pdfs, student-answers) |
| Frontend | Tailwind CSS + Alpine.js v3 + HTMX |
| Charts | Chart.js 4.x |
| PDF | PyMuPDF (render) + ReportLab (export) + pdf.js (viewer) |
| Excel | openpyxl |
| OMR | OpenCV + pytesseract |
| Queue | Celery + Redis (notifikasi async) |

## Struktur Proyek

```
app/
├── __init__.py              # Flask app factory, Supabase clients, Jinja filters
├── config.py                # Dev/Prod config, MAX_CONTENT_LENGTH=50MB
├── routes/
│   ├── auth.py              # Login, register, logout
│   ├── exam.py              # CRUD ujian + PDF upload
│   ├── admin.py             # Dashboard admin
│   ├── teacher.py           # Builder, grading, publish
│   ├── student.py           # Take exam, submit, results
│   ├── api.py               # Anti-cheat, sync-draft, scan
│   ├── publish.py           # Publish scores
│   └── webhook.py           # Midtrans/Fonnte callbacks
├── services/
│   ├── pdf_service.py       # PDF → PNG conversion
│   ├── grading_service.py   # Scoring logic
│   ├── omr_service.py       # OMR answer detection
│   ├── anti_cheat_service.py# Violation validation
│   ├── analytics_service.py # Stats aggregation
│   ├── export_service.py    # XLSX/PDF export
│   ├── notification_service.py # WhatsApp/email
│   ├── ljk_generator.py     # Lembar jawaban generator
│   └── auth_service.py      # Auth helpers
├── models/                  # Supabase query helpers
├── utils/
│   └── auth.py              # get_supabase(), get_auth_client(), login_required
└── templates/               # Jinja2 + Tailwind + Alpine.js
    ├── auth/                # Login, register
    ├── student/             # Take exam (offline-first), results
    └── teacher/             # Dashboard, exam form, grade detail, results

supabase/
├── schema.sql               # Idempotent DDL (tables, RLS, triggers)
├── seed.sql                 # Sample data
├── migrations/              # Schema migrations
└── policies/                # RLS policy definitions
```

## Setup

### Prasyarat

- Python 3.10+
- Poppler (untuk pdf2image) — [Windows download](https://github.com/oschwartz10612/poppler-windows/releases)
- Redis (opsional, untuk Celery)

### Instalasi

```bash
# 1. Clone
git clone https://github.com/sugeng-riyanto/scanGrade.git
cd scanGrade

# 2. Virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# 3. Install dependencies
pip install -r requirements.txt

# 4. Konfigurasi
cp .env.example .env
# Edit .env — isi SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY

# 5. Setup database
# Jalankan supabase/schema.sql di Supabase SQL Editor

# 6. Jalankan
flask run --host=0.0.0.0 --port=5000 --reload
# atau
python wsgi.py
```

### Environment Variables

| Variable | Wajib | Deskripsi |
|----------|-------|-----------|
| `SUPABASE_URL` | Ya | Project URL dari Supabase dashboard |
| `SUPABASE_ANON_KEY` | Ya | Anon/public key (untuk frontend & auth) |
| `SUPABASE_SERVICE_KEY` | Ya | Service role key (backend only, bypass RLS) |
| `FLASK_SECRET_KEY` | Ya | Min 32 karakter random |
| `FLASK_ENV` | Tidak | `development` (default) atau `production` |
| `NGROK_URL` | Tidak | Auto-filled oleh start-dev.sh |
| `FONNTE_TOKEN` | Tidak | WhatsApp gateway token |
| `REDIS_URL` | Tidak | `redis://localhost:6379/0` |

## Arsitektur Offline-First

Dirancang untuk 500 siswa concurrent dengan WiFi tidak stabil (3 lantai):

```
Siswa (browser)
  └─ Menjawab → localStorage (instant, 0ms)
       ├─ Light sync: MCQ + teks (~5KB) setiap 20 detik
       └─ Heavy sync: Canvas JPEG (~30-80KB) setiap 60 detik

Server (Flask)
  ├─ Rate limit: 3s (light), 10s (canvas)
  ├─ Per-user lock: mencegah concurrent DB writes
  └─ Dirty hash check: skip jika data tidak berubah
```

- **Offline submit**: Jawaban disimpan di localStorage, auto-submit saat reconnect
- **Canvas compression**: Max 500px, JPEG quality 0.4 — dari ~500KB jadi ~30KB
- **Connection indicator**: Pill di topbar (hijau=sinkron, kuning=menyimpan, abu-abu=offline, merah=error)

## Keamanan

- **Row Level Security (RLS)**: Guru hanya akses exam miliknya, siswa hanya lihat submission sendiri
- **Two Supabase clients**: Service key (backend, bypass RLS) vs anon key (auth operations)
- **Anti-cheat**: `visibilitychange` + debounce 1500ms, penalty selalu dihitung server-side
- **No copy-paste**: Essay textarea dilindungi dari copy/paste/cut/context menu
- **Rate limiting**: Violation log max 1/2 detik, sync draft min 3 detik interval

## Testing

```bash
pytest
```

## Dokumentasi

- [`AGENTS.md`](AGENTS.md) — Konteks lengkap untuk AI assistant
- [`supabase/schema.sql`](supabase/schema.sql) — Database schema (idempotent)
- [`.env.example`](.env.example) — Template environment variables

## License

MIT
