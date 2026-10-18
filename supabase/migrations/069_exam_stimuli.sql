-- ──────────────────────────────────────────────────────────────
-- Migrasi 069: stimulus soal (satu konteks, banyak sub-soal)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor. Dry run wajib: berkas ini mengubah skema.
--
-- MENGAPA SATU KOLOM JSONB, BUKAN TABEL `exam_stimulus` + `exam_question`
-- ────────────────────────────────────────────────────────────────────────
-- Sebuah soal di ScanGrade bukan baris: `exams.question_types`, `exams.answer_key`,
-- `exams.question_weights` dan `exams.question_pages` semuanya peta JSON yang
-- dikunci nomor soal. Tidak ada tabel `exam_question`, jadi menambah satu tabel
-- untuk stimulus berarti menciptakan struktur kedua yang harus disinkronkan dengan
-- peta itu pada setiap simpan — dua sumber kebenaran untuk satu daftar soal, dan
-- itulah kelas cacat yang modul `question_types.py` ada untuk mengakhirinya.
--
-- Jadi stimulus mengikuti bentuk yang sudah ada: satu kolom JSONB di `exams`,
-- berisi
--
--   {"s1": {"title": "Bacaan: Siklus Air", "content": "…", "questions": [3, 4, 5]}}
--
-- `questions` adalah daftar nomor soal yang berada di bawah stimulus itu. Soal di
-- luar daftar mana pun adalah soal biasa, dan stimulus yang tidak punya soal tidak
-- disimpan (sebuah konteks tanpa pertanyaan bukan soal).
--
-- YANG TIDAK DIUBAH: `question_types`, `answer_key`, `question_weights`. Setiap
-- sub-soal tetap satu soal biasa dengan tipe dan poinnya sendiri — stimulus tidak
-- punya poin, tidak punya tipe, dan tidak masuk "Mark Scheme by Question Type".
-- Itu yang membuat penilaiannya tidak butuh rumus baru: sub-soal dinilai persis
-- seperti soal lain, lalu dijumlahkan oleh `hitung_nilai_akhir()` yang sudah ada.
--
-- IDEMPOTEN: aman dijalankan dua kali.

ALTER TABLE public.exams
    ADD COLUMN IF NOT EXISTS question_stimuli JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.exams.question_stimuli IS
    'Stimulus soal: {id: {title, content, questions:[index]}}. Wadah tampilan saja — tiap sub-soal tetap dinilai menurut tipenya sendiri.';

-- Kode yang membaca kolom ini menangani kolom yang belum ada (lihat daftar
-- `data.pop(...)` di rute simpan), jadi sebuah kotak yang belum menjalankan
-- migrasi ini tetap bisa menyimpan ujian — hanya tanpa stimulus. Urutan deploy
-- yang benar tetap: migrasi dulu, rilis kemudian.
