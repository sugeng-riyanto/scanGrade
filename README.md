# ScanGrade

Platform ujian digital untuk sekolah: penyusunan soal, ujian online **offline-first**, koreksi lembar jawaban dari **foto ponsel** (OMR), penilaian esai dengan bantuan AI + override guru, analisis butir soal, laporan resmi per kelas dan per murid, pengawasan ujian, komunikasi guru–murid, serta kepatuhan **UU PDP**.

Dipakai produksi di <https://scangrade.web.id> — VPS 1 vCPU, 3 worker gunicorn+gevent, ~500 murid per sekolah dengan WiFi tidak merata.

## ScanMeet — pembaruan 25 Oktober 2026 / update on 25 October 2026

> **Status / Availability:** ScanMeet Exam code is integrated, but **Live remains OFF in production**. The instructions below describe the planned pilot experience, **not a currently available service**. Do not turn on production feature flags until isolated staging proves media **12/12** and webhook **7/7**, plus release approval.

### Perubahan hari ini / Today's changes

- **PR #9 and #10** integrated the secure LiveKit-based exam supervision and two-browser CDP testing into the mainline.
- **PR #23** pinned and self-hosted LiveKit browser SDK `2.23.0` at `app/static/vendor/livekit/2.23.0/`, loading through cache-busted `asset_v`. The public-CDN script fallback was removed for exam safety.
- **Database migrations 070/071** add exam Live session/audit tables and the per-exam `exams.scan_live_enabled` switch. The infrastructure operator reported both applied with recovery points on 25 October; verify against the migration ledger before future deployment operations.
- **Staging fixture helper:** `deploy/scanmeet_staging_fixture.py` prepares an isolated synthetic school/teacher/two-pupil/outsider/exam fixture, dry-run by default. `--write` changes the staging database. Never use a production Supabase project or expose credentials in logs/artifacts.
- **CI:** Six required workflows passed for commit `fb7d18286e` (Linux and Windows units, browser, region, skipped coverage, landing QC).
- **Production status:** the operator reported `fb7d182` served successfully, health endpoint OK, and both `SCANGRADE_LIVE_ENABLED` and `SCANGRADE_LIVE_EXAM_ENABLED` OFF. This report is not a real-media sign-off.
- **Outstanding:** a separate HTTPS staging host (outside `*.scangrade.web.id`), separate Supabase and LiveKit staging projects, GitHub Environment `scanmeet-staging` secrets, then real camera/microphone isolation **12/12** and webhook **7/7** results. Physical Edge/Android/iPhone tests are not yet verified.

### Cara memakai ScanMeet / How to use ScanMeet

| Guru / Teacher | Murid / Student |
|---|---|
| 1. Buka dashboard guru dan pilih ujian. / Open the teacher dashboard and select an exam. | 1. Masuk dan buka ujian yang diberikan. / Sign in and open your assigned exam. |
| 2. Jika fitur tersedia, aktifkan ScanMeet untuk ujian itu. / If available, enable ScanMeet for that exam. | 2. Izinkan kamera saat browser meminta. / Allow camera access when the browser prompts you. |
| 3. Klik **Mulai pengawasan**. / Choose **Start supervision**. | 3. Kerjakan soal. Mikrofon tetap mati sampai disetujui guru. / Answer questions. Your mic stays off until the teacher approves. |
| 4. Lihat kamera peserta dan setujui permintaan bicara bila perlu. / View student cameras and approve speaking requests as needed. | 4. Gunakan **Angkat tangan** untuk meminta bicara. / Use **Raise hand** to ask to speak. |
| 5. Klik **Akhiri Live** saat selesai. / Choose **End Live** when done. | 5. Bila video putus, tetap kerjakan ujian dan laporkan kepada guru. / If video disconnects, continue the exam and notify the teacher. |

**Exam-mode rules / Aturan mode ujian:** camera required, microphone by teacher approval, hand raise enabled, chat disabled, recording disabled; media failure must not interrupt exam answers, autosave or the timer.

**Learn more / Panduan:** [Landing-page ScanMeet guide](https://scangrade.web.id/#scanmeet-guide) (visible after the documentation change is deployed), [technical configuration](docs/SCANGRADE_LIVE.md), [isolated staging instructions](docs/SCANMEET_BIZNET_STAGING.md), [test acceptance criteria](docs/SCANMEET_ACCEPTANCE_MATRIX.md).

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Cara Kerja Inti](#cara-kerja-inti)
- [Tech Stack](#tech-stack)
- [Peran, Hak Akses & Sesi](#peran-hak-akses--sesi)
- [Struktur Proyek](#struktur-proyek)
- [Menjalankan (lokal)](#menjalankan-lokal)
- [Konvensi & Pagar Kualitas](#konvensi--pagar-kualitas)
- [Environment Variables](#environment-variables)
- [Deployment (produksi)](#deployment-produksi)
- [Testing](#testing)
- [Keamanan & Kepatuhan](#keamanan--kepatuhan)
- [Dokumentasi](#dokumentasi)
- [Lisensi](#lisensi)

## Fitur Utama

| Fitur | Deskripsi |
|-------|-----------|
| **Exam Builder** | Upload PDF soal, **tujuh tipe soal** (pilihan ganda — dengan toggle jawaban ganda, benar/salah, pilihan ganda kompleks / **Tabel Pernyataan AKM**, menjodohkan, drag & drop, mengurutkan, esai canvas), halaman PDF per soal, audio/YouTube, **skema penilaian per tipe** yang dinormalkan tepat ke 100 |
| **Ujian Online** | Mode `classic` dan `rail` (thumbnail halaman), opsi jawaban bisa di kiri/samping kertas, pengaturan tata letak diingat per perangkat, **offline-first** (localStorage → sync saat online), jendela ujian (`end_at`) terpisah dari durasi pengerjaan |
| **Scan OMR** | Lembar jawaban dibaca dari **foto ponsel** (perspektif, miring, blur, bayangan) dengan *matched filter* + verifikasi `grid_match`; menolak lembar yang tidak terbaca daripada menebak; lembar siap cetak 20/40/50/100/160 soal di [`docs/ljk/`](docs/ljk/) |
| **Penilaian Esai** | Coret-coretan di canvas di atas PDF, komentar per soal, **bantuan AI** (kemiripan/grade) yang selalu bisa ditimpa guru, penilaian sebagian (*part credit*) untuk soal menjodohkan/mengurutkan/pilihan ganda kompleks |
| **Anti-Cheat** | Deteksi pindah tab/aplikasi, jendela ditinggalkan, keluar layar penuh, copy/paste, klik kanan, PrintScreen, watermark nama; **masa tenggang 10 detik × 2** sebelum absen dihitung; tangga penalti bertingkat + auto-submit; **Safe Exam Browser (SEB)** opsional untuk ujian berisiko tinggi (Windows/macOS/iPad) |
| **Pengawasan Ujian** | **Jadwal** pengawas per kelas dan **matriks** ruang–jam (guru × ruang × slot), halaman *Tugas Saya* untuk guru, penugasan pengawas lewat `admin_sekolah` dan `vice_principal` |
| **Analisis Butir Soal** | Statistik klasik (tingkat kesukaran, daya beda 27%, point-biserial, Cronbach α, KR-20, SEM) **dan** kalibrasi Rasch (logit, S.E., t-statistik, infit/outfit MNSQ, separation, Wright map) + peta pengecoh; ekspor **CSV/PDF** dan **tautan berbagi** ber-token |
| **Laporan Resmi** | Dokumen per kelas dan **per murid** (PDF/XLSX/CSV dibangun server), **laporan terarsip** per ujian (`/teacher/analysis/<id>/report`), indeks laporan yang bisa dipersempit per sekolah/guru, **tautan berbagi** ber-token untuk orang tua, lampiran PDF tiap murid dalam satu zip |
| **Komunikasi** | Percakapan 1-on-1 ala WhatsApp + pengumuman (broadcast) dengan **RBAC ketat** — tidak ada percakapan/pengumuman antar-murid (diverifikasi server-side) |
| **Papan Tulis** | Whiteboard realtime (Socket.IO) untuk guru dan murid, bisa diekspor ke PDF (dimuat lazily agar impor berat tidak memperlambat startup) |
| **Multi-Sekolah** | Enam peran, isolasi data per sekolah via RLS, tahun ajaran & kenaikan kelas, impor massal murid/guru dari CSV/XLSX, **satu akun banyak sekolah** untuk guru/kepala sekolah/wakil (keanggotaan + sekolah aktif diverifikasi tiap request) |
| **Periode & Penilaian** | Periode asesmen, **KKM & level mata pelajaran**, pembobotan nilai (komponen & bobot), kenaikan kelas, dan tahun ajaran |
| **Langganan** | Paket, faktur, kode aktivasi, dan pembayaran **Midtrans Snap** (vendored, bukan CDN) |
| **Kepatuhan** | Kebijakan privasi 13 pasal + syarat & ketentuan 11 pasal (UU PDP & PSE Kominfo), persetujuan eksplisit, ekspor & penghapusan data, retensi otomatis |
| **Antarmuka** | Bilingual **Indonesia/Inggris** (toggle, preferensi tersimpan), mode terang/gelap, tema `primary`, lantai keterbacaan ≥12px, satu halaman masuk bergaya tab untuk semua peran |

## Cara Kerja Inti

Beberapa mekanika menentukan perilaku aplikasi sehari-hari dan sengaja dipusatkan di satu tempat agar tidak menyimpang:

- **Ujian berjalan di dua jam.** *Jendela* (`start_at`…`end_at`) menentukan kapan murid boleh **memulai**; *durasi* (`duration_minutes`) dihitung dari `started_at` murid itu dan boleh melewati jendela. Toggle `auto_submit_on_window_end` menukar keduanya: sesi berakhir pada `end_at` (atau durasi, mana yang lebih dulu). Satu modul, `app/utils/exam_window.py`, melayani daftar ujian, pintu masuk ujian, `/api/student/sync-draft`, rute submit, dan dua rute scan — jawaban yang terpisah adalah bagaimana halaman berkata "12 menit lagi" sementara server menganggap kertas sudah selesai.
- **Tangga penalti bertingkat.** Tiga jenis pelanggaran yang dihitung — `tab_switch`, `fullscreen_exit`, `focus_lost`. Masa tenggang `10 detik × 2` sebelum absen dibebankan; setelah kesempatan habis, absen dihitung saat terlihat. Penalti naik bertingkat dan berujung auto-submit.
- **Offline-first.** Jawaban disimpan ke localStorage lebih dulu dan disinkronkan ke server saat online; sinkronisasi menghitung deadline dari `started_at` yang **tersimpan di server**, bukan klaim klien.
- **OMR tanpa OCR.** Kertas jawaban dibaca via *matched filter* dan diverifikasi `grid_match`; lembar yang tidak yakin ditolak, bukan ditebak.
- **Penilaian.** Bobot per tipe dinormalkan tepat ke 100 lewat `mark_scheme`; soal menjodohkan/mengurutkan/pilihan ganda kompleks mendapat *part credit*; AI hanya menyarankan dan selalu bisa ditimpa guru.
- **Anggaran round-trip.** Supabase menjawab ~100–165 ms dari kotak ini. `app/utils/req_cache.py` menyediakan `memo()` (per request, di `g`) dan `ttl()` (baris yang identik untuk semua murid satu sekolah) sehingga 500 murid tidak berarti 500 query untuk data yang sama.

## Tech Stack

| Layer | Teknologi |
|-------|-----------|
| Backend | Flask 3.1.1 (app factory + blueprint), Python **3.12+** (lantai di `pyproject.toml`, `.python-version`, dan ditegakkan `python_requires.py`) |
| Server | gunicorn 23 (`gevent`, `127.0.0.1:8000`) di balik nginx |
| Database | Supabase PostgreSQL + Row Level Security |
| Auth & Storage | Supabase Auth (email/password) + Supabase Storage |
| Frontend | Tailwind CSS (**dibangun lokal**, bukan CDN) + Alpine.js v3; HTMX pada sebagian aksi |
| Realtime | Flask-SocketIO (papan tulis, dimuat lazily) |
| Queue | Celery + Redis (tugas OMR asinkron) |
| OMR | OpenCV (`opencv-python-headless`) — *matched filter*, tanpa OCR |
| PDF | ReportLab (laporan, lembar jawaban, ekspor) + xhtml2pdf (kartu hasil) + PyMuPDF & pdf2image/Poppler untuk parsing soal |
| Excel | openpyxl |
| Charts | Chart.js 4 |
| AI (opsional) | OpenAI dan Google Gemini — kemiripan esai, saran nilai, parsing soal PDF; embedding lewat API (bukan `sentence-transformers`) |
| Pembayaran | Midtrans Snap (vendored di `app/static/vendor/midtrans/`) |
| SEB | Rute `seb` + layanan `seb_service`/`seb_config_key`/`seb_crypto` untuk Config Key Safe Exam Browser |
| Observabilitas | Sentry, `/health`, `/metrics/processes`, header `X-Supabase-Roundtrips` |

## Peran, Hak Akses & Sesi

| Peran | Ringkas | Idle / absolut |
|-------|---------|----------------|
| `super_admin` | Seluruh platform: sekolah, langganan, feature flag, pengaturan privasi/DPO, status deploy | 15 mnt / 4 jam |
| `admin_sekolah` | Sekolahnya: guru, murid, kelas, promosi kelas, pengumuman, persetujuan hapus akun | 30 mnt / 8 jam |
| `principal` (Kepala Sekolah) | Membaca sekolahnya secara menyeluruh (laporan, analisis, pengawasan) — tanpa wewenang tulis data sekolah | 45 mnt / 10 jam |
| `vice_principal` (Wakil Kepala) | Sama seperti `principal`; mengelola jadwal & matriks pengawasan pada preview-nya sendiri | 45 mnt / 10 jam |
| `guru` | Ujian, bank soal, penilaian, analisis, laporan, pengumuman ke muridnya; bisa menjadi anggota beberapa sekolah | 60 mnt / 12 jam |
| `murid` | Mengerjakan ujian, melihat hasil, mengirim pesan ke guru/admin sekolah | 120 mnt / 24 jam |

**Satu halaman masuk untuk semua peran.** `/auth/sign-in` adalah satu-satunya tempat kata sandi diperiksa, dengan tab (admin/staff/guru/murid) sebagai petunjuk — bukan gerbang, dan tidak pernah dikirim ke server sebagai klaim. Dua pintu lama `/auth/login` dan `/auth/login-user` tetap menjawab sebagai **alias** (GET dialihkan ke halaman baru, POST tetap menandatangani sesi) karena kartu cetak, bookmark, dan tautan tutorial publik masih menamainya.

Matriks lengkap ada di [`docs/RBAC.md`](docs/RBAC.md) dan [`AGENTS.md`](AGENTS.md).

## Struktur Proyek

```
app/
├── __init__.py                 # App factory, dua klien Supabase, Jinja globals, scheduler, landing/demo
├── config.py                   # Dev/Prod/Testing, MAX_CONTENT_LENGTH=50MB
├── celery_app.py               # Celery untuk tugas OMR
├── whiteboard_init.py          # Socket.IO + blueprint papan tulis (dimuat lazily)
├── routes/                     # 21 modul rute: auth, exam, teacher, student, admin, admin_sekolah,
│                               #   principal, super_admin, api, publish, webhook, seb, media,
│                               #   membership_routes, guide, public, tools, students, whiteboard_*
├── services/                   # 93 modul: omr_service, omr_layout, question_types, mark_scheme,
│                               #   item_analysis, analysis_report/scope/share/frameworks, exam_report,
│                               #   learner_report, report_card_service, answer_sheet_generator,
│                               #   export_service, anti_cheat_service, seb_service, invigilation*,
│                               #   school_membership, enrollment, academic_year, assessment_periods,
│                               #   grade_weighting, subject_kkm/levels, pdf_service/parser,
│                               #   ai_service/ai_grading/ai_embedding, dst.
├── utils/                      # 32 modul: auth, exam_window, exam_access, csrf, rate_limiter, cache,
│                               #   req_cache, query_meter, supabase_retry, asset_version, dst.
├── models/                     # Helper query Supabase
├── static/                     # css (compiled tailwind + theme), js, vendor (alpine, midtrans, inter, socket.io)
└── templates/                  # 146 template: auth, student, teacher, admin, admin_sekolah, principal,
                                #   super_admin, compliance, print, guide, tutorial, shared, tools, errors

supabase/
├── schema.sql                  # DDL idempoten (tabel, RLS, trigger)
├── seed.sql / seed_demo.sql    # Data contoh
├── migrations/                 # Migrasi bernomor (hingga 064) + _COMPLETE_SETUP.sql
└── policies/                   # Definisi policy RLS

deploy/                         # Deploy otomatis + gerbang kualitas + alat operasi (63 berkas)
docs/                           # 55 berkas (32 di root + features/, ljk/, measurements/, user-guides/, dst.)
tests/                          # 324 berkas tes, ~7.300 fungsi tes (unit, integration, e2e)
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

## Konvensi & Pagar Kualitas

Beberapa aturan ditegakkan otomatis karena kegagalannya tidak terlihat (halaman tetap menjawab 200):

- **Nama utilitas Tailwind harus literal.** Tailwind membaca template sebagai teks, jadi `from-{{ warna }}-600` mencocokkan apa pun, tidak menghasilkan apa pun, dan elemen diam-diam mewarisi gaya leluhurnya. Setiap nama harus ditulis utuh (`{% set %}` map, string kelas penuh per cabang).
- **`theme.css` tetap berkas terpisah**, tidak dikembalikan ke `base.html` (blok inline tidak bisa di-cache dan ride di setiap halaman) dan di-`<link>` **setelah** `tailwind.css` (urutan salah menghilangkan remap dark mode tanpa error).
- **Lantai keterbacaan ≥12px** dipetakan sekali di `theme.css`, bukan di 500+ call site.
- **Setiap tabel ≥4 kolom** berada dalam `overflow-x-auto` **dan** membawa `min-w-[...]`.
- **Copy bilingual dibind sebagai pasangan** `t('Indonesia','English')`; pesan yang dibuat script memakai `sgT()`. Halaman yang sengaja berbahasa Indonesia menyatakan `{% set content_lang = 'id' %}`.
- **Cakupan terjemahan adalah angka yang tidak boleh turun.** Hari ini **142 template, 4.379 unit bilingual, 1.376 sisa — 76,1%**, dengan lantai dicatat di `deploy/i18n_baseline.json`.
- **Setiap berkas di `/static/` harus dipakai aplikasi dan ada di commit** — direktori itu dilayani seluruhnya.

Semua ini (dan lain-lain) dijalankan `deploy/theme_gate.sh`, yang dipakai baik oleh pre-commit hook maupun auto-deploy.

## Environment Variables

| Variable | Wajib | Deskripsi |
|----------|-------|-----------|
| `SUPABASE_URL` | Ya | URL project Supabase |
| `SUPABASE_ANON_KEY` | Ya | Kunci publik (auth & operasi pengguna) |
| `SUPABASE_SERVICE_KEY` | Ya | Service role key (backend saja, menembus RLS) |
| `FLASK_SECRET_KEY` | Ya | Acak, min. 32 karakter |
| `DIRECT_URL` | Untuk migrasi & gerbang deploy | URL pooler mode-session; dipakai `deploy/apply_migration.py` dan gerbang schema auto-deploy |
| `FLASK_ENV` | Tidak | `production` di `.env`; paksa `development` untuk HTTP lokal |
| `APP_URL` / `APP_VERSION` | Tidak | URL publik, dipakai gerbang & email |
| `REDIS_URL` | Tidak | `redis://localhost:6379/0`; limiter jatuh ke `memory://` bila kosong |
| `MIDTRANS_SERVER_KEY` / `MIDTRANS_CLIENT_KEY` / `MIDTRANS_IS_PRODUCTION` | Tidak | Pembayaran langganan |
| `SMTP_EMAIL` / `SMTP_PASSWORD` (atau `APP_PASSWORD_GMAIL`) | Tidak | Email (reset kata sandi, aktivasi, notifikasi) |
| `RESEND_API_KEY` | Tidak | Layanan email alternatif |
| `FONNTE_API_KEY` / `WA_API_URL` / `WA_API_KEY` | Tidak | Gateway WhatsApp |
| `SENTRY_DSN` / `SENTRY_ENVIRONMENT` | Tidak | Error tracking |
| `AI_DEFAULT_PROVIDER` / `AI_DEFAULT_KEY` | Tidak | Nilai awal AI per guru (bisa diatur per-guru di UI) |

## Deployment (produksi)

Produksi memakai **deploy otomatis berbasis pull**: VPS memeriksa `origin/main` tiap dua menit, mengambil commit baru, menjalankannya lewat serangkaian gerbang, lalu me-reload gunicorn (SIGHUP) secara mulus. Rilis yang ditolak di-rollback dan **dikarantina** agar tidak dicoba berulang.

```
nginx → gunicorn (gevent, 127.0.0.1:8000) → Flask
systemd: scangrade.service · scangrade-deploy.timer · celery.service
```

Gerbang pada setiap rilis (dijalankan `deploy/theme_gate.sh` dan runner `deploy/scangrade-deploy.sh`):

| Gerbang | Yang dijaga |
|---------|-------------|
| Runner identity (Gate 0) | Launcher yang berjalan masih menunjuk checkout (bukan salinan beku); salinan yang *drifted* ditolak exit 14 |
| Theme | Keterbacaan kontras di kedua mode, kelas Tailwind yang belum dikompilasi, kesegaran `tailwind.css` |
| Stylesheet | `theme.css` tidak kembali ke `base.html`, urutan `<link>` benar |
| i18n | Cakupan terjemahan tidak boleh turun dari lantai yang tercatat |
| Static tree | Setiap berkas di `/static/` dipakai aplikasi dan ada di commit |
| Secrets | Tidak ada service-role key atau Flask secret yang ter-commit |
| Schema contract | Nama kolom/tabel/policy yang dipakai kode ada di schema, tidak terbuka ke `anon`, dan **sudah diterapkan** di DB live |
| Smoke | Login lewat halaman masuk tunggal, puluhan halaman menjawab, RBAC menolak lintas peran, halaman ujian masih membawa panel anti-cheat |
| SEB door | Pintu Safe Exam Browser menerima/menolak sesuai kontrak |
| Touch | Chrome headless (CDP) menyentuh halaman kunci; render ujian diuji (`exam_render_gate.py`) |
| Claims | Angka kapasitas/fasilitas di landing page masih sesuai kotak ini |
| Perf | Rilis tidak lebih lambat/lebih berat dari rilis terakhir yang lulus |
| Commit gates | `served_commit` / `process_commit` / `worker_commit` memastikan yang benar-benar melayani adalah commit yang diverifikasi |

Alat operasi yang penting:

```bash
# migrasi: uji di transaksi yang di-rollback, lalu commit (butuh DIRECT_URL)
python deploy/apply_migration.py supabase/migrations/0xx_x.sql           # dry run
python deploy/apply_migration.py supabase/migrations/0xx_x.sql --commit  # + recovery point
python deploy/apply_migration.py --verify                                # apa yang benar-benar ada di schema

bash deploy/theme_gate.sh                     # gerbang tema + i18n + kontrak schema + CSS + secrets
bash deploy/arm-auto-deploy.sh --check        # apa yang sudah "armed" (read-only)
bash deploy/unstick-deploy.sh                 # pulihkan kotak yang fetch tapi tidak merge
```

Baca [`docs/AUTO_DEPLOY.md`](docs/AUTO_DEPLOY.md) sebelum menyentuh produksi — dokumen itu menjelaskan karantina, snapshot data, armament, kanal `deploy/control/plan`, dan cara rollback.

## Testing

```bash
.venv/Scripts/python.exe -m pytest tests -q   # offline, tanpa jaringan & tanpa Supabase nyata
bash deploy/theme_gate.sh                     # statis, ~beberapa detik
```

Suite berjalan penuh offline (`TestingConfig` menyediakan kredensial dummy), jadi tidak ada project Supabase yang tersentuh. Saat ini **324 berkas tes, ~7.300 fungsi**. Sebagian besar modul punya *guard* tes pasangan; banyak area memakai harness mutasi di `.freebuff/mutate_*.py` (tidak ikut ke git).

## Keamanan & Kepatuhan

- **Isolasi tenant di RLS + Python.** Klien backend memakai service key, jadi batas sekolah ditegakkan di kode (`resolve_for_request`, `school_membership`) dan diverifikasi ulang tiap request, bukan dipercaya karena dipilih sekali.
- **Cookie sesi** `HttpOnly` + `Secure` (di produksi), CSRF token pada form, CSP `frame-ancestors 'self'`.
- **Rate limiting** per-akun untuk endpoint sensitif (login, reset), dengan per-IP sebagai backstop.
- **UU PDP**: kebijakan privasi 13 pasal, syarat & ketentuan 11 pasal, persetujuan eksplisit, hak ekspor & penghapusan data, retensi otomatis, dan peran DPO.
- **Tidak ada rahasia di repo** — gerbang `theme_gate.sh` menolak service-role key atau Flask secret yang ter-commit; rotasi ada di [`docs/SECRET_ROTATION.md`](docs/SECRET_ROTATION.md).

## Dokumentasi

| Dokumen | Isi |
|---------|-----|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Arsitektur aplikasi dan aliran data |
| [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) | Daftar endpoint |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Skema & relasi |
| [`docs/RBAC.md`](docs/RBAC.md) | Matriks hak akses per peran |
| [`docs/AUTHENTICATION.md`](docs/AUTHENTICATION.md) | Alur auth, sesi, timeout, dan reset kata sandi |
| [`docs/SECURITY.md`](docs/SECURITY.md) · [`docs/SECURITY_RLS_MATRIX.md`](docs/SECURITY_RLS_MATRIX.md) · [`docs/SECRET_ROTATION.md`](docs/SECRET_ROTATION.md) | Keamanan, policy RLS per tabel, rotasi kunci |
| [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) | UU PDP & PSE Kominfo |
| [`docs/AUTO_DEPLOY.md`](docs/AUTO_DEPLOY.md) | Deploy otomatis, gerbang, karantina, snapshot |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) · [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) · [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md) · [`docs/MONITORING.md`](docs/MONITORING.md) | Pemasangan & operasi di VPS |
| [`docs/EXAM_TIMING.md`](docs/EXAM_TIMING.md) · [`docs/GRADE_WEIGHTING.md`](docs/GRADE_WEIGHTING.md) · [`docs/ESSAY_GRADING_UX.md`](docs/ESSAY_GRADING_UX.md) | Jendela ujian, pembobotan nilai, alur penilaian esai |
| [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) · [`docs/measurements/`](docs/measurements/) | Pengukuran & pagar performa |
| [`docs/ljk/`](docs/ljk/) | Format lembar jawaban siap cetak + panduan mencetak |
| [`docs/features/`](docs/features/) | Catatan per fitur (SEB, anti-cheat, OMR, pengawasan, pejabat sekolah, dsb.) |
| [`docs/user-guides/`](docs/user-guides/) | Panduan admin, guru, dan murid |
| [`docs/PRICING.md`](docs/PRICING.md) · [`docs/ROADMAP.md`](docs/ROADMAP.md) · [`docs/PRD.md`](docs/PRD.md) | Harga, rencana, dan dokumen produk |
| [`AGENTS.md`](AGENTS.md) | Konteks lengkap proyek (keputusan, jebakan, batasan) |

## Lisensi

MIT
