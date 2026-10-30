# LMS ScanGrade — Roadmap 3 Bulan (tanpa mengganggu modul ujian stabil)

> Prinsip: ujian (`exams`, `submissions`, `exam_window`, `anti-cheat`, OMR) FROZEN.
> Semua modul LMS adalah tabel + route + template BARU yang reuse
> `schools, subjects, classes, school_years, teacher_assignments, assessment_periods,
> grade_weighting, subject_kkm, enrollment`.

## Ringkasan Eksekutif

| Bulan | Tema | Output demo-able |
|-------|------|------------------|
| 1 | Konten + Drive + Tahun Ajaran | Guru upload materi via Drive embed, murid baca per kelas/mapel, clone tahun ajaran |
| 2 | Quiz Formatif + Gradebook + Rapor | Quiz retry + bank soal, nilai akhir per mapel, cetak rapor |
| 3 | Game + ScanMeet Class + Polish | XP/streak/badge, quiz live, absensi otomatis, hardening offline |

Setiap milestone di bawah punya file spec sendiri di folder ini + acceptance criteria
untuk agentic AI. Kerjakan berurutan — M1 adalah fondasi semua yang lain.

## M1 (Minggu 1-4): Konten & Drive & Tahun Ajaran

**Spec:** `LMS_CONTENT_SPEC.md`, `LMS_DRIVE_SPEC.md`

- [ ] Migrasi `080_lms_content.sql`: `content_folders`, `content_items`,
      `content_views`, `content_versions` (semua bawa `school_id`)
- [ ] CRUD guru `/teacher/contents` — filter Tahun Ajaran x Mapel x Kelas,
      cek tulis via `teacher_assignments` (bukan via client)
- [ ] Render murid `/student/learn` — daftar modul + progress bar dari `content_views`
- [ ] Drive embed: simpan `drive_file_id` saja, render `.../preview`,
      auto-share reader ke kelas saat publish, revoke saat unpublish
- [ ] Clone tahun ajaran: copy 1 folder beserta items ke `school_year_id` baru
- [ ] Wizard admin: naik kelas massal + arsip read-only tahun lama (ikut `year_lock`)
- [ ] Absensi harian v1: `/teacher/attendance` (Hadir/Izin/Sakit/Alpa), bisa offline-sync

**Gate:** round-trip budget tetap (dashboard murid ≤6 query), Tailwind literal,
i18n pairs tidak turun, `year_lock` menolak tulis ke tahun tutup.

## M2 (Minggu 5-8): Quiz, Bank Soal, Gradebook, Rapor

**Spec:** `LMS_QUIZ_SPEC.md`

- [ ] Bedakan `Quiz (formatif)` vs `Exam (sumatif)` di level policy:
      Quiz = retry 3x + nilai tertinggi + pembahasan langsung + tanpa penalti tab;
      Exam = aturan sekarang, tidak berubah
- [ ] Bank soal: tag `KD/CP, Bloom C1-C6, kesulitan Rasch` reuse `item_analysis`;
      generator "10 soal campuran mudah-sedang-sukar"
- [ ] Pisahkan `question_bank` dari `exams` — satu soal bisa dipakai Quiz + Exam
- [ ] Gradebook per mapel: `Tugas 30% + Quiz 20% + UTS 20% + UAS 30%`
      (baca `grade_weighting`, guru tidak bisa ubah bobot)
- [ ] Remedial terkunci: nilai < KKM buka misi, nilai maks setelah remedial = KKM
- [ ] Cetak rapor + leger + transkrip (reuse `report_card_service`)

**Gate:** `answer-key-review` tetap jalan, tidak ada perubahan skema `exams/submissions`,
semua bobot dibaca dari konfigurasi sekolah.

## M3 (Minggu 9-12): Game, ScanMeet Class, Polish

**Spec:** `LMS_GAMIFICATION_SPEC.md`, ScanMeet class policy (sudah ada `CLASS_POLICY`)

- [ ] XP + Level per mapel, streak harian, badge (`student_badges`, `student_xp_events`)
- [ ] Liga kelas mingguan (top 5 + peringkatmu, reset Senin)
- [ ] Boss Battle akhir bab + Misi Remedial Game + Kartu Tokoh Indonesia
- [ ] Quiz Live ala Kahoot di atas ScanMeet `class` mode (murid jawab dari HP,
      papan skor via Socket.IO yang sudah ada)
- [ ] ScanMeet `class`: absensi otomatis dari `scan_live_events`,
      recording lokal `MediaRecorder` + tombol upload ke Drive masing-masing user
- [ ] Offline: materi ditandai "tersedia offline" via service worker;
      quiz ringan bisa offline-sync seperti ujian
- [ ] Kepatuhan: consent orang tua, moderasi link, retensi UU PDP
      (chat/rekaman auto-hapus, nilai arsip 5-10 th), tombol Unduh/Hapus dataku
- [ ] Monitoring: murid tidak login 7 hari, kelas tanpa materi, export 1-klik

**Gate:** recording TIDAK PERNAH di mode `exam`, server tidak menyimpan blob video,
leaderboard tidak bocor lintas sekolah.

## Aturan main agentic AI (wajib dibaca tiap task)

1. Jangan ubah file ujian stabil kecuali diminta eksplisit di spec.
2. Setiap tulis ke DB wajib `school_id` + cek `teacher_assignments` / `require_school_access`.
3. Tailwind: nama utility literal, `npm run css:build` setelah ubah template.
4. Bilingual: setiap string baru pakai `t('id','en')`, jangan hardcode.
5. Setiap migrasi: file `supabase/migrations/0xx_*.sql` + instruksi apply manual di SQL Editor.
6. Setiap selesai: tulis test di `tests/unit/test_lms_*.py` + jalankan gate terkait.

Lihat `AGENTIC_AI_TASKS.md` untuk daftar task siap-assign per agent.
