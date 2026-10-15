# Prompt: dashboard "Log Sesi & Tren Anti-Cheat" (guru) — ScanGrade

Copy the block below into a fresh Freebuff session on this repository. It is
self-contained, and the recon section at the end is the result of a real FASE 0 run:
it exists so the executing agent does not rebuild what already ships.

---

## PERAN

Kamu senior full-stack developer (Flask 3.1, Supabase, Alpine.js 3, Tailwind lokal,
Chart.js 4 lokal) **dan** data analyst untuk ScanGrade. Bangun dashboard GURU
"Log Sesi & Tren Anti-Cheat": log sesi + statistik selama asesmen berlangsung, plus
analisis tren yang komprehensif.

## FILOSOFI PRODUK (paling penting)

Ini BUKAN alat penghakiman. Ia deskriptif dan memberi konteks akal sehat supaya guru
memutuskan sendiri. Konsekuensinya, dan ini acceptance criteria bukan gaya bahasa:

- Tidak ada label "curang/menyontek/pelaku", tidak ada "skor kecurigaan", tidak ada
  peringkat murid dari yang paling mencurigakan. Urutan default daftar murid =
  abjad/nomor absen.
- Bahasa netral-deskriptif: "pola yang layak diperhatikan", "berbeda dari kebiasaan
  kelas" — bukan vonis.
- Setiap pengamatan otomatis WAJIB membawa lima hal: bukti (angka + pembanding),
  penjelasan wajar alternatif, tingkat keyakinan, langkah tindak lanjut manusiawi,
  dan nama metrik/baseline yang dipakai.
- Korelasi bukan sebab-akibat; peringatkan di tempat yang relevan.
- READ-ONLY: tidak ada penalti, auto-submit, penguncian, atau tindakan otomatis dari
  halaman ini.
- Metric-only: jangan simpan konten layar, jawaban per-detik mentah, atau IP lengkap.

## ATURAN KERJA (wajib)

1. Baca `AGENTS.md` dan patuhi semua konvensinya. Ikuti guardian test yang sudah ada;
   jangan melemahkan satu assertion pun agar hijau — perbaiki penyebabnya.
2. Alur: **ANALISIS → BERHENTI minta konfirmasi → implementasi per fase. Satu commit
   per fase.** Jangan gabungkan fase.
3. Migrasi SQL idempoten + non-destruktif lewat `deploy/apply_migration.py`
   (`--dry-run` dulu, konfirmasi user sebelum `--commit`). Jangan sentuh produksi.
   Semua test offline (TestingConfig), tanpa data murid nyata.
4. Beban server: VPS 1 vCPU, 3 worker gevent, ~500 murid serentak. Hindari N+1; pakai
   agregasi SQL, `app/utils/cache` + `app/utils/req_cache`, paginasi, dan hormati
   `app/utils/query_meter` serta `deploy/perf_gate.py` (slack 1.25× + 1 grace query,
   baseline maksimum 14 hari; rebaseline lewat `deploy/perf_rebaseline.intent`).
   Rilis tidak boleh gagal di gerbang Perf.
5. Frontend: Tailwind **lokal** (`npm run css:build` setelah ubah template), Alpine.js,
   Chart.js 4 dari `/static/vendor/`, bukan CDN. Semua string bilingual (`t('id','en')`
   sebagai pair) dan lolos `deploy/theme_gate.sh` (tema dua mode, kontras, i18n,
   schema contract, tabel ≥4 kolom wajib `overflow-x-auto` + `min-w-[...]`, font ≥12px).
   Responsif laptop guru + tablet (769–1023px punya aturan sendiri).
6. **Test dulu (harus gagal sebelum implementasi), lalu implementasi.** Buktikan guard
   baru menggigit dengan mutation harness (`.freebuff/mutate_*.py`, byte-for-byte
   restore); laporkan berapa mutasi tertangkap.
7. Tanpa perlu konfirmasi untuk `git push` / deploy ke VPS **selama semua gate hijau**;
   kalau ada gate merah, laporkan dan jangan paksa.

## YANG SUDAH ADA (jangan dibangun ulang) — hasil recon nyata

**Lapisan data (FASE 1 sebagian besar sudah ada):**

- `attempt_session_events` (migrasi 035): `attempt_id` (→`submissions.id`), `seq`
  (`UNIQUE (attempt_id, seq)`), `kind`, `occurred_at` (jam klien) + `server_at` (jam
  server), `duration_ms`, `offline`, `device_class`, `question_index`, `meta` JSONB.
- `attempt_summary` (035): satu baris per attempt (`UNIQUE attempt_id`) —
  `events_seen`, `truncated`, `counts_by_kind`, `away_count/away_total_ms/away_max_ms/
  away_short_count/away_long_count/away_unknown_count`, `sync_gap_count`, `offline_ms`,
  `answer_change_count`, `per_question`, `sitting_ms`, `effective_active_ms`,
  `clock_drift_max_ms`, `clock_suspect`, `sources TEXT[]`, `method_version`.
- `exam_class_baseline` (035): per (exam, class, metric) — `n`, `median`, `p10..p90`,
  `iqr`, `mad`, `min`, `max`, `small_sample`, `method_version`.
- `submissions.last_ping_at`, `ping_count` (056) — heartbeat sebagai sinyal observasi,
  bukan pengunci. `environment_signal` (062) — sinyal VM lemah, non-punitif.
- **Aturan NULL yang wajib dipertahankan:** kolom yang belum terukur bernilai NULL,
  bukan 0; `sources` mencatat tabel mana yang menyumbang, sehingga nol (temuan) bisa
  dibedakan dari ketiadaan sumber.
- Ingest klien sudah ada: `app/routes/api.py` → `attempt_events.record_batch`
  (`CLIENT_EVENT_SEQ_BASE = 1_000_000`, idempoten lewat `ignore_duplicates`).
  Halaman ujian sudah mengirim 9 dari 10 kind; **`control_unavailable` tidak pernah
  dikirim** (lihat celah C2).
- Ringkasan per attempt ditulis saat submit: `submission_service` →
  `attempt_summary.record_for_attempt`. Baseline dihitung on demand:
  `attempt_summary.baseline_for(supabase, exam_id, class_id)` dengan cek kesegaran +
  `method_version`.

**Permukaan guru yang sudah ada:**

- `/teacher/exams/<id>/sessions` → `teacher/session_review.html` + endpoint
  `/teacher/api/exams/<id>/sessions-data` (`session_review.review_for_exam` +
  `attempt_timeline.for_exam`). Sudah memuat: `room` (daftar murid, urutan netral),
  `facts_for`, `observations(...)` (judul/evidence/comparison/explanation/keyakinan/
  follow-up), `shared_window` (indikator gangguan bersama), timeline per murid.
- `/teacher/analytics` → `teacher/analytics.html` (454 baris): **performa/nilai**
  (distribusi skor, rata-rata per ujian, CSV/PDF/print) — bukan metrik anti-cheat.
- `attempt_timeline.for_exam` juga dipakai di `admin_sekolah.py` dan `principal.py`.
- `teacher/submission_violations.html`, `teacher/analysis*.html`, `teacher/reports.html`.

**Yang BELUM ada (inti pekerjaan):** lihat celah C1–C8 di bawah.

## CELAH YANG HARUS DITUTUP

- **C1 — Viewer sesi per attempt belum lengkap.** Yang ada: daftar timeline mentah di
  dalam kartu murid. Yang diminta: garis waktu horizontal sepanjang durasi ujian
  (blok kepergian, fullscreen, offline, sinkron, submit), lapis kedua nomor soal aktif
  + momen perubahan jawaban, filter per tipe/rentang waktu, penanda sumber jam
  (klien vs server), dan tombol **Cetak/Ekspor ringkasan sesi (PDF) yang netral**.
- **C2 — `control_unavailable` tidak pernah dilaporkan klien**, sehingga pengamatan
  "kontrol tidak tersedia di perangkat ini" (mis. Fullscreen API di iOS) tidak punya
  data. Kirim dari halaman ujian, sertakan `meta`.
- **C3 — Statistik level ujian/kelas belum ada** sebagai permukaan: distribusi (box
  plot/histogram) `away_total_ms`, `away_count`, `sync_gap_count`,
  `effective_active_ms`; heatmap murid × waktu (baris **tidak** diurutkan menurut
  kecurigaan); agregat waktu per soal (paling lama dibuka, paling sering ditinggalkan
  saat dibuka, paling sering diubah) yang disandingkan dengan tingkat kesukaran dari
  analisis butir; proporsi jawaban berubah dalam N detik setelah kembali vs baseline
  kelas; segmentasi perangkat (laptop vs HP/tablet, per browser) dengan baseline
  masing-masing.
- **C4 — Panel live saat ujian berlangsung** (polling ≥15 dtk, hemat query, atau SSE):
  murid aktif/selesai, distribusi event per tipe, laju event per bucket 5 menit
  dinormalkan per murid aktif, indikator gangguan bersama. Tanpa menambah beban berarti
  saat 500 murid serentak.
- **C5 — Tren longitudinal belum ada sama sekali:** per kelas lintas ujian (garis
  median + pita IQR + anotasi kebijakan/insiden), per murid (jalur persentil, sembunyikan
  bila ujian < 3), per mata pelajaran/tingkat/jam pelaksanaan, Spearman metrik × skor
  dengan CI + peringatan korelasi (tidak ditampilkan bila n < ambang), dampak kebijakan
  sebelum/sesudah tanpa klaim kausal.
- **C6 — Transparansi murid belum ada:** murid bisa melihat log sesinya sendiri (tanpa
  pembanding murid lain) dan mengajukan catatan/penjelasan yang tampil ke guru.
- **C7 — Retensi & audit (UU PDP):** `data_retention_service.RETENTION` belum mencakup
  `attempt_session_events`, `attempt_summary`, `exam_class_baseline`,
  `environment_signal`, `seb_door_refusal`; pembersihan akun (`forget_member`) hanya
  menyentuh `submissions`, `notifications`, `violation_logs`. Tambahkan retensi
  berjenjang (log mentah lebih pendek, agregat lebih lama) + **audit siapa membuka log
  sesi murid tertentu** (belum ada `log_activity` di rute itu).
- **C8 — Dokumentasi + seed + guard bahasa:** `docs/ANTICHEAT_ANALYTICS.md` belum ada
  (metrik, rumus, ambang, keterbatasan, contoh penjelasan wajar), seed sintetis
  terpisah, dan guard test bahasa vonis.

## RENCANA PER FASE (satu commit per fase)

**FASE 1 — Data & agregasi (delta saja).** Audit `attempt_summary`/`exam_class_baseline`
terhadap kebutuhan C3–C5; tambah kolom/metrik yang kurang lewat migrasi idempoten baru.
**Penomoran:** yang terpakai di checkout ini sampai `066_membership_closure_canonical.sql`,
dan `065_traffic_rollup.sql` sudah dipakai sesi lain yang belum menyatu ke sini — jadi
mulai dari `067_`, dan periksa ulang `git log origin/main -- supabase/migrations` sebelum
membuat berkas. Plus job agregasi untuk ujian yang sedang
berjalan (Celery sudah ada di `app/celery_app.py`, tapi jangan menambah beban: hitung
per bucket, bukan per event). Test: kebenaran agregasi, data kosong, n kecil
(`MIN_BASELINE_N = 8`), reproduktibilitas `method_version`.

**FASE 2 — Viewer log sesi per murid (C1, C2).** Garis waktu + filter + sumber jam +
kartu ringkas dengan pembanding kelas ("median kelas X, IQR a–b") + cetak PDF netral.
Test: rendering nyata lewat `base.html`, penanda sumber jam, ekor distribusi tanpa label
individu.

**FASE 3 — Statistik ujian & kelas + live panel (C3, C4).** Satu halaman ringkasan ujian
(tab: Distribusi, Per soal, Perangkat) + panel live. Target: **≤3 query utama** untuk
halaman ringkasan, cache per (exam, `method_version`), invalidasi saat attempt baru
submit. Test: query count lewat `query_meter`, `X-Supabase-Roundtrips`, dan
`loadtest_concurrent.py` untuk beban.

**FASE 4 — Tren longitudinal (C5).** Halaman "Tren": per kelas, per murid, per mapel/
tingkat/jam, Spearman + CI + peringatan, dampak kebijakan. Test: fungsi statistik
terhadap nilai acuan, penekanan tampilan saat n kecil.

**FASE 5 — Mesin konteks akal sehat (penyempurnaan `session_review.observations`).**
Aturan robust (median/MAD, persentil, batas n minimum), bukan kotak hitam. Wajib:
"gangguan bersama" menurunkan keyakinan pengamatan individu di jendela yang sama.
Larangan: menggabungkan sinyal jadi satu skor; menyarankan sanksi. Test: assert bahwa
setiap pengamatan punya lima komponen dan bahwa gangguan bersama menurunkan keyakinan.

**FASE 6 — UI/UX, ekspor, transparansi (C6).** Empat halaman (Ringkasan ujian, Log sesi
murid, Tren kelas, Konteks & Pengamatan), peringatan tetap terlihat, panel "Cara membaca
dashboard ini" (glosarium, contoh penjelasan wajar, batas teknologi: HP kedua dan orang
lain di ruangan tidak terdeteksi), ekspor CSV/XLSX/PDF dengan metadata metode + versi
baseline, dan tampilan log milik murid + catatannya.

**FASE 7 — Privasi, RBAC, retensi (C7).** Guru hanya kelas/ujian miliknya (pakai
`exam_access.managed_exam`/`_guard_exam` yang sudah ada), admin sekolah untuk sekolahnya,
murid hanya miliknya, super_admin agregat tanpa PII bila mungkin. Setiap route: guard
peran + cek kepemilikan; tambahkan ke smoke RBAC. Retensi berjenjang + audit pembukaan
log.

**FASE 8 — Performa.** Ukur tiap halaman baru dengan `query_meter`, catat ke baseline
perf (`deploy/perf_gate.py`), verifikasi dengan `loadtest_concurrent.py`; jangan menaikkan
beban selama ujian serentak.

**FASE 9 — Test, seed sintetis, dokumentasi (C8).** Seed kelas 30 murid: sesi normal,
lonjakan offline serempak (insiden jaringan), murid HP dengan blur wajar, pola ekor
distribusi, kelas kecil n < 8. **Guard bahasa:** test yang gagal bila template/i18n
dashboard ini memuat kata vonis ("curang", "menyontek", "pelaku", "cheater", "guilty",
"tersangka") atau menyortir default menurut tingkat kecurigaan. Tulis
`docs/ANTICHEAT_ANALYTICS.md` + update `docs/SECURITY.md`, `docs/RBAC.md`, `AGENTS.md`,
dan panduan singkat guru.

## FORMAT LAPORAN SETIAP FASE

Ringkasan perubahan · file diubah · migrasi (jika ada, dengan status dry-run/commit) ·
test ditambahkan dan **apa yang dibuktikan** · hasil pytest + `deploy/theme_gate.sh` +
perf · risiko tersisa · pertanyaan keputusan untuk user. Berhenti setelah FASE 0 dan
setelah setiap fase; tunggu "lanjut".

## PERINTAH PERTAMA

Jalankan **FASE 0 saja**: verifikasi ulang recon di atas terhadap kode saat ini (nomor
migrasi terpakai, kolom yang benar-benar ada, rute/template yang benar-benar ada),
laporkan celah C1–C8 beserta buktinya (file + baris), usulkan delta rencana, lalu
BERHENTI dan minta konfirmasi.
