# ScanGrade

Platform ujian digital untuk sekolah: penyusunan soal, ujian online **offline-first**, koreksi lembar jawaban dari **foto ponsel** (OMR), penilaian esai dengan bantuan AI + override guru, analisis butir soal, laporan resmi per kelas dan per murid, komunikasi guru–murid, serta kepatuhan **UU PDP**.

Dipakai produksi di <https://scangrade.web.id> (VPS 1 vCPU, 3 worker gunicorn+gevent, ~500 murid per sekolah).

## Fitur Utama

| Fitur | Deskripsi |
|-------|-----------|
| **Exam Builder** | Upload PDF soal, enam tipe soal (pilihan ganda, benar/salah, menjodohkan, drag & drop, mengurutkan, esai canvas), halaman PDF per soal, audio/YouTube, **skema penilaian per tipe** yang dinormalkan tepat ke 100 |
| **Ujian Online** | Mode `classic` dan `rail` (thumbnail halaman), opsi jawaban bisa di kiri/samping kertas, pengaturan tata letak diingat per perangkat, **offline-first** (localStorage → sync) |
| **Scan OMR** | Lembar jawaban dibaca dari **foto ponsel** (perspektif, miring, blur, bayangan) dengan *matched filter* + verifikasi `grid_match`; menolak lembar yang tidak terbaca daripada menebak; lembar siap cetak 20/40/50/100/160 soal di [`docs/ljk/`](docs/ljk/) |
| **Penilaian Esai** | Coret-coretan di canvas di atas PDF, komentar per soal, **bantuan AI** (kemiripan/grade) yang selalu bisa ditimpa guru, penilaian sebagian (*part credit*) untuk soal menjodohkan/mengurutkan |
| **Anti-Cheat** | Deteksi pindah tab/aplikasi, jendela ditinggalkan, keluar layar penuh, copy/paste, klik kanan, PrintScreen, watermark nama; **masa tenggang 10 detik × 2** sebelum absen dihitung; tangga penalti bertingkat + auto-submit |
| **Analisis Butir Soal** | Statistik klasik (tingkat kesukaran, daya beda 27%, point-biserial, Cronbach α, KR-20, SEM) **dan** kalibrasi Rasch (logit, S.E., t-statistik, infit/outfit MNSQ, separation, Wright map) + peta pengecoh |
| **Laporan Resmi** | Dokumen per kelas dan **per murid** (PDF/XLSX/CSV dibangun server), indeks laporan yang bisa dipersempit per sekolah/guru, **tautan berbagi** ber-token untuk orang tua, lampiran PDF tiap murid dalam satu zip |
| **Komunikasi** | Percakapan 1-on-1 ala WhatsApp + pengumuman (broadcast) dengan **RBAC ketat** — tidak ada percakapan/pengumuman antar-murid (diverifikasi server-side) |
| **Papan Tulis** | Whiteboard realtime (Socket.IO) untuk guru dan murid, bisa diekspor ke PDF |
| **Multi-Sekolah** | Empat peran, isolasi data per sekolah via RLS, tahun ajaran & kenaikan kelas, impor massal murid/guru dari CSV/XLSX |
| **Langganan** | Paket, faktur, dan pembayaran **Midtrans Snap** (vendored, bukan CDN) |
| **Kepatuhan** | Kebijakan privasi 13 pasal + syarat & ketentuan 11 pasal (UU PDP & PSE Kominfo), persetujuan eksplisit, ekspor & penghapusan data, retensi otomatis |
| **Antarmuka** | Bilingual **Indonesia/Inggris** (toggle, preferensi tersimpan), mode terang/gelap, tema `primary`, lantai keterbacaan ≥12px |

## Tech Stack

| Layer | Teknologi |
|-------|-----------|
| Backend | Flask 3.1.1 (app factory + blueprints), Python 3.12 |
| Server | gunicorn 23 (`gevent`, `127.0.0.1:8000`) di balik nginx |
| Database | Supabase PostgreSQL + Row Level Security |
| Auth & Storage | Supabase Auth (email/password) + Supabase Storage |
| Frontend | Tailwind CSS (**dibangun lokal**, bukan CDN) + Alpine.js v3; HTMX pada sebagian aksi |
| Realtime | Flask-SocketIO (papan tulis) |
| Queue | Celery + Redis (tugas OMR asinkron) |
| OMR | OpenCV (`opencv-python-headless`) — *matched filter*, tanpa OCR |
| PDF | ReportLab (laporan, lembar jawaban, ekspor) + xhtml2pdf (kartu hasil) + PyMuPDF & pdf2image/Poppler untuk parsing soal |
| Excel | openpyxl |
| Charts | Chart.js 4 |
| AI (opsional) | OpenAI dan Google Gemini — kemiripan esai, saran nilai, parsing soal PDF |
| Pembayaran | Midtrans Snap (vendored di `app/static/vendor/midtrans/`) |
| Observabilitas | Sentry, `/health`, `/metrics/processes`, header `X-Supabase-Roundtrips` |

## Peran & Hak Akses

| Peran | Ringkas |
|-------|---------|
| `super_admin` | Seluruh platform: sekolah, langganan, feature flag, pengaturan privasi/DPO, status deploy |
| `admin_sekolah` | Sekolahnya: guru, murid, kelas, promosi kelas, pengumuman, persetujuan hapus akun |
| `guru` | Ujian, bank soal, penilaian, analisis, laporan, pengumuman ke muridnya |
| `murid` | Mengerjakan ujian, melihat hasil, mengirim pesan ke guru/admin sekolah |

Matriks lengkap ada di [`docs/RBAC.md`](docs/RBAC.md) dan [`AGENTS.md`](AGENTS.md).

## Struktur Proyek

```
app/
├── __init__.py                 # App factory, dua klien Supabase, Jinja globals, scheduler
├── config.py                   # Dev/Prod/Testing, MAX_CONTENT_LENGTH=50MB
├── celery_app.py               # Celery untuk tugas OMR
├── whiteboard_init.py          # Socket.IO + blueprint papan tulis (dimuat terpisah)
├── routes/                     # 17 modul rute (18 blueprint terdaftar): auth, exam, teacher,
│                               #   student, admin_sekolah, super_admin, api, publish, webhook,
│                               #   whiteboard_*, guide, public, tools, students
├── services/                   # 44 modul: omr_service, omr_layout, question_types, mark_scheme,
│                               #   item_analysis, analysis_report/scope/share, exam_report,
│                               #   learner_report, answer_sheet_generator, export_service,
│                               #   anti_cheat_service, pdf_service/parser, ai_service, dll.
├── utils/                      # auth, exam_window, exam_access, csrf, rate_limiter, cache,
│                               #   req_cache, query_meter, supabase_retry, asset_version, dll.
├── models/                     # Helper query Supabase
├── static/                     # css (compiled tailwind), js, vendor (alpine, midtrans, inter)
└── templates/                  # 125 template: auth, student, teacher, admin_sekolah,
                                #   super_admin, compliance, print, guide, tutorial, shared

supabase/
├── schema.sql                  # DDL idempoten (tabel, RLS, trigger)
├── seed.sql / seed_demo.sql    # Data contoh
├── migrations/                 # Migrasi bernomor (+ _COMPLETE_SETUP.sql)
└── policies/                   # Definisi policy RLS

deploy/                         # Deploy otomatis + gerbang kualitas + alat operasi
docs/                           # 24 dokumen: arsitektur, API, RBAC, keamanan, deploy, ljk, pengukuran
tests/                          # 111 file, ~2.580 fungsi tes
manage.py · wsgi.py · gunicorn.conf.py · loadtest_concurrent.py · locustfile.py
```

## Menjalankan (lokal)

```bash
git clone https://github.com/sugeng-riyanto/scanGrade.git
cd scanGrade

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/macOS
pip install -r requirements.txt

cp .env.example .env              # isi SUPABASE_URL / ANON_KEY / SERVICE_KEY / FLASK_SECRET_KEY

npm install && npm run css:build  # WAJIB: Tailwind dibangun lokal, bukan CDN

FLASK_ENV=development .venv/Scripts/python.exe -m flask run --host 127.0.0.1 --port 5000 --debug
```

Dua jebakan yang sering memakan waktu:

- **`FLASK_ENV` harus `development` untuk server HTTP lokal.** `.env` berisi `production`, yang mengaktifkan `SESSION_COOKIE_SECURE`; cookie sesi lalu dibuang oleh browser/`requests` dan **setiap login menjawab 403 CSRF** — itu bukan bug CSRF.
- **Tanpa `--debug`, tidak ada yang hot-reload** — bukan hanya Python, **template Jinja juga tidak**. Restart server setelah mengubah route atau template.

Setelah mengubah template, jalankan `npm run css:build` lagi; tanpa itu kelas baru tidak ada di `tailwind.css` yang dikirim (ada gerbangnya di `deploy/css_freshness.py`).

## Environment Variables

| Variable | Wajib | Deskripsi |
|----------|-------|-----------|
| `SUPABASE_URL` | Ya | URL project Supabase |
| `SUPABASE_ANON_KEY` | Ya | Kunci publik (auth & operasi pengguna) |
| `SUPABASE_SERVICE_KEY` | Ya | Service role key (backend saja, menembus RLS) |
| `FLASK_SECRET_KEY` | Ya | Acak, min. 32 karakter |
| `DIRECT_URL` | Untuk migrasi | URL pooler mode-session; hanya dipakai `deploy/apply_migration.py` |
| `FLASK_ENV` | Tidak | `production` di `.env`; paksa `development` untuk HTTP lokal |
| `APP_URL` | Tidak | URL publik, dipakai gerbang & email |
| `REDIS_URL` | Tidak | `redis://localhost:6379/0`; limiter jatuh ke `memory://` bila kosong |
| `MIDTRANS_SERVER_KEY` / `MIDTRANS_CLIENT_KEY` | Tidak | Pembayaran langganan |
| `SMTP_*` | Tidak | Email (reset kata sandi, notifikasi) |
| `FONNTE_API_KEY` | Tidak | Gateway WhatsApp |

## Deployment (produksi)

Produksi memakai **deploy otomatis berbasis pull**: VPS memeriksa `origin/main` tiap dua menit, mengambil commit baru, menjalankannya lewat serangkaian gerbang, lalu me-reload gunicorn (SIGHUP) secara mulus. Rilis yang ditolak di-rollback dan **dikarantina** agar tidak dicoba berulang.

```
nginx → gunicorn (gevent, 127.0.0.1:8000) → Flask
systemd: scangrade.service · scangrade-deploy.timer · celery.service
```

Gerbang pada setiap rilis (semuanya di `deploy/`, dijalankan `deploy/theme_gate.sh` dan runner):

| Gerbang | Yang dijaga |
|---------|-------------|
| Theme | Keterbacaan kontras di kedua mode, kelas Tailwind yang belum dikompilasi |
| i18n | Cakupan terjemahan tidak boleh turun dari lantai yang tercatat |
| Schema contract | Nama kolom/policy/role yang dipakai kode ada di schema & tidak terbuka ke `anon` |
| Smoke | Empat peran bisa login, ~32 halaman menjawab, RBAC menolak lintas peran, halaman ujian masih membawa panel anti-cheat |
| Claims | Angka kapasitas di landing page masih sesuai kotak ini |
| Perf | Rilis tidak lebih lambat/lebih berat dari rilis terakhir yang lulus |

Alat operasi yang penting:

```bash
# migrasi: uji di transaksi yang di-rollback, lalu commit (butuh DIRECT_URL)
python deploy/apply_migration.py supabase/migrations/0xx_x.sql           # dry run
python deploy/apply_migration.py supabase/migrations/0xx_x.sql --commit  # + recovery point
python deploy/apply_migration.py --verify                                # apa yang benar-benar ada di schema

bash deploy/theme_gate.sh                     # gerbang tema + i18n + kontrak schema + CSS
bash deploy/arm-auto-deploy.sh --check        # apa yang sudah "armed" (read-only)
bash deploy/unstick-deploy.sh                 # pulihkan kotak yang fetch tapi tidak merge
```

Baca [`docs/AUTO_DEPLOY.md`](docs/AUTO_DEPLOY.md) sebelum menyentuh produksi — dokumen itu menjelaskan karantina, snapshot data, dan cara rollback.

## Testing

```bash
.venv/Scripts/python.exe -m pytest tests -q   # offline, tanpa jaringan & tanpa Supabase nyata
bash deploy/theme_gate.sh                     # ~1 detik, statis
```

Suite berjalan penuh offline (`TestingConfig` menyediakan kredensial dummy), jadi tidak ada project Supabase yang tersentuh. Sebagian besar modul punya *guard* tes pasangan; banyak area memakai harness mutasi di `.freebuff/mutate_*.py` (tidak ikut ke git).

## Dokumentasi

| Dokumen | Isi |
|---------|-----|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Arsitektur aplikasi dan aliran data |
| [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) | Daftar endpoint |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Skema & relasi |
| [`docs/RBAC.md`](docs/RBAC.md) | Matriks hak akses per peran |
| [`docs/SECURITY.md`](docs/SECURITY.md) · [`docs/SECURITY_RLS_MATRIX.md`](docs/SECURITY_RLS_MATRIX.md) | Keamanan & policy RLS per tabel |
| [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) | UU PDP & PSE Kominfo |
| [`docs/AUTO_DEPLOY.md`](docs/AUTO_DEPLOY.md) | Deploy otomatis, gerbang, karantina, snapshot |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) · [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) | Pemasangan di VPS |
| [`docs/ljk/`](docs/ljk/) | Format lembar jawaban siap cetak + panduan mencetak |
| [`docs/features/`](docs/features/) | Catatan per fitur |
| [`AGENTS.md`](AGENTS.md) | Konteks lengkap proyek (keputusan, jebakan, batasan) |

## Lisensi

MIT
