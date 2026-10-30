# Agentic AI Tasks — Siap Assign per Agent (ScanGrade LMS)

> Cara pakai: copy 1 blok TASK ke agent. Setiap task independen,
> punya file target + acceptance + gate. Urutan: A -> B -> C.

## Aturan global semua agent

- Stack: Flask 3.1.1, Python 3.12+, Supabase (service key di server), Tailwind lokal, Alpine v3.
- Jangan ubah `app/routes/exam.py`, `submission_service.py`, `take_exam.html`
  kecuali task menyebutkannya.
- Setiap query bawa `school_id`. Setiap tulis cek `teacher_assignments` / `require_school_access`.
- Tailwind literal. `npm run css:build` setelah ubah template.
- Bilingual `t('id','en')`. Tabel ≥4 kolom: `overflow-x-auto + min-w-[...]`.
- Tulis test `tests/unit/test_lms_*.py`. Buktikan 403 lintas-sekolah SEBELUM select murid.

---

## TASK A1 — Migrasi konten (fondasi)

**File:** `supabase/migrations/080_lms_content.sql`
**Ref:** `LMS_CONTENT_SPEC.md §2`

Buat 4 tabel `content_folders, content_items, content_views, content_versions`
+ index + trigger `updated_at`. Idempoten (IF NOT EXISTS).
JANGAN sertakan RLS rumit — enforce di Python seperti modul lain.

Accept: apply manual di SQL Editor sukses 2x tanpa error, rollback aman
(DROP ... IF EXISTS), kolom sesuai spec.

## TASK A2 — Otorisasi konten

**File baru:** `app/services/content_access.py`
**Ref:** `LMS_CONTENT_SPEC.md §3`

Fungsi `can_write(db, user_id, role, school_id, class_id, subject_id)` +
`can_read(...)`. Baca `teacher_assignments` status active tahun aktif.
Tutup: dropdown kosong = tolak.

Accept: guru tanpa assignment 403 + 0 row ditulis; principal read-only;
test `test_lms_content.py` hijau.

## TASK A3 — CRUD guru + halaman murid

**Files:** `app/routes/contents.py`, `app/templates/teacher/contents*.html`,
`app/templates/student/learn.html`
**Ref:** `LMS_CONTENT_SPEC.md §5`

CRUD folder/item + filter TA x Mapel x Kelas + progress bar murid.
Preview reuse pola `exam_preview` (render tanpa tulis views).

Accept: round-trip ≤6 query, Tailwind gate lolos, i18n tidak turun.

## TASK A4 — Drive embed + permission

**File:** `app/services/drive_service.py` (`drive_embed_url`, `ensure_reader`, `revoke_reader`)
**Ref:** `LMS_DRIVE_SPEC.md`

Mock Drive API di test (jangan panggil Google asli).
CSP `frame-src` hanya drive/docs.

Accept: unpublish -> probe murid 404; tidak ada token di log/template.

## TASK A5 — Clone tahun + naik kelas

**Files:** `app/routes/admin_sekolah.py` (+2 endpoint), wizard template
**Ref:** `LMS_CONTENT_SPEC.md §6`

Clone folder+items ke tahun baru (`is_published=false`), promote massal +
tulis `student_enrollment`. Tahun tutup ditolak `year_lock`.

Accept: arsip tidak berubah 1 byte, clone bisa dipublish independen.

## TASK B1 — Quiz formatif

**Files:** `exams.kind`, `app/routes/quiz.py`, `app/templates/student/quiz.html`
**Ref:** `LMS_QUIZ_SPEC.md`

Retry 3x nilai tertinggi, pembahasan setelah jawab, tanpa anti-cheat ladder.
Sync terpisah dari exam.

Accept: retry ke-4 ditolak; key tidak bocor di JSON sebelum jawab; suite exam hijau.

## TASK B2 — Bank soal + generator

**Files:** `question_bank`, `POST /teacher/quiz/generate`
**Ref:** `LMS_QUIZ_SPEC.md §2`

Tag Bloom + kesulitan Rasch, generate strata 3-4-3, "tarik dari ujian saya".

Accept: tidak pernah ambil soal sekolah lain; generator deterministik di test (seed).

## TASK B3 — Gradebook + rapor

**Files:** `app/routes/gradebook.py`, reuse `grade_weighting, report_card_service`
**Ref:** roadmap M2

Tabel nilai akhir per mapel + cetak rapor/leger/transkrip. Guru tidak bisa ubah bobot.
Remedial maks = KKM.

Accept: contoh hitung manual cocok dengan output; remedial >KKM ditolak.

## TASK C1 — XP/streak/badge

**Files:** `student_xp_events, student_streaks, student_badges`
**Ref:** `LMS_GAMIFICATION_SPEC.md §1-2`

Hitung server-side + idempotency key. Leaderboard per (class, subject) mingguan.

Accept: double-request tidak double-XP; lintas sekolah tidak bocor.

## TASK C2 — Quiz live + boss battle

**Files:** reuse `whiteboard_socket`, ScanMeet `CLASS_POLICY`
**Ref:** `LMS_GAMIFICATION_SPEC.md §3`

Room per quiz, skor realtime, boss battle 10 menit ambang 80%.

Accept: murid luar kelas 403; reconnect tidak menghilangkan skor.

## TASK C3 — ScanMeet class attendance + local recording

**Files:** `meeting_attendance` worker dari `scan_live_events`,
tombol `MediaRecorder` hanya di mode class + banner consent
**Ref:** `SCANMEET` docs + `LMS_DRIVE_SPEC.md §4`

Accept: mode exam tidak pernah tampilkan tombol rekam; server tidak simpan blob;
upload Drive masuk akun user masing-masing.

## TASK C4 — Hardening (terakhir)

Absensi harian offline-sync, service worker materi offline, consent orang tua,
retensi UU PDP, monitoring churn (tidak login 7 hari), export 1-klik.

Accept: `theme_gate.sh` hijau penuh + demo end-to-end guru->murid->rapor direkam.
