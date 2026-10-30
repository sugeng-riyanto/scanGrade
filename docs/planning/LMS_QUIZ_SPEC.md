# LMS Quiz Spec — Best Version Dunia (Formatif vs Sumatif)

> Agent: quiz formatif BARU, exam sumatif FROZEN. Satu bank soal, dua pintu keluar.

## 1. Policy

| Aspek | Quiz (formatif) | Exam (sumatif, existing) |
|-------|-----------------|--------------------------|
| Retry | 3x, nilai tertinggi | 1x (`max_attempts`) |
| Pembahasan | langsung setelah jawab | setelah publish nilai |
| Anti-cheat | OFF (tanpa penalti tab) | penuh (graduated + auto-submit) |
| Nilai ke rapor | masuk komponen Quiz | masuk komponen UTS/UAS |
| Timer | per-quiz longgar + bisa pause | `exam_window.deadline()` keras |

Implementasi: kolom baru `exams.kind TEXT DEFAULT 'exam'` (`exam|quiz`),
bukan tabel baru. Semua guard exam cek `kind='exam'`.

## 2. Bank soal

- Tabel `question_bank (id, school_id, subject_id, teacher_id, qtype, stem_md,
  options JSONB, key JSONB, kkm_tag, bloom TEXT, difficulty REAL NULL, source_exam_id NULL)`.
- Tagging: `KD/CP`, `bloom C1-C6`, `difficulty` dari `item_analysis` (Rasch logit).
- Generator: `POST /teacher/quiz/generate {subject_id, n=10, mix: 3-4-3}` —
  ambil acak berstrata mudah/sedang/sukar.
- Import: "Tarik dari ujian saya" — copy soal exam ke bank (tidak memindah).

## 3. Flow murid (tiru Canvas + Quizizz + Duolingo)

1. Buka quiz -> lihat info (retry tersisa, deadline homework-mode).
2. Kerjakan — offline-first reuse `sync-draft` (localStorage dulu).
3. Jawab salah -> 1 kalimat pembahasan dari `teacher_feedback` + tombol "Coba lagi".
4. Selesai -> XP dihitung (`skor x bobot kesulitan`), retry tersisa berkurang.
5. Homework-mode: deadline 2 hari, boleh buka materi (link `content_items` terkait).

## 4. Route

- `/teacher/quiz/new`, `/teacher/quiz/<id>/edit` (reuse `exam_form` pola, bukan copy).
- `/student/quiz/<id>` — tanpa anti-cheat ladder, tanpa fullscreen wajib.
- `/api/student/quiz-sync` — terpisah dari exam sync agar budget query tidak campur.

## 5. Acceptance

- [ ] Quiz retry ke-4 ditolak, nilai tertinggi yang disimpan.
- [ ] Pembahasan tidak bocor sebelum jawab (cek response JSON tanpa key).
- [ ] Exam lama tidak berubah perilaku (suite exam existing hijau).
- [ ] Generator tidak pernah mengambil soal sekolah lain.
- [ ] Test: `tests/unit/test_lms_quiz.py`.
