-- ──────────────────────────────────────────────────────────────
-- Migration 055: tandai setiap ujian dengan periode penilaiannya
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Masalah yang dijawab
-- ────────────────────
-- Migrasi 050 membuat `assessment_periods`: satu baris per periode (UTS, UAS,
-- Try Out, Asesmen) dengan rentang tanggal dan satu yang **sedang berjalan**.
-- `exams.exam_type` sudah memakai kosakata yang sama sejak 007. Tetapi tidak ada
-- kolom yang *menghubungkan* keduanya: periode sebuah kertas hanya tersirat dari
-- tanggalnya, dan hilang begitu tanggalnya diubah. Akibatnya hasil tidak bisa
-- dikelompokkan per periode sepanjang tahun — pertanyaan "bagaimana kelas ini di
-- UTS vs UAS" tidak punya cara menjawabnya dari data.
--
-- Kolom ini adalah gabungan itu, dan ia sebuah FK supaya tanda itu tidak bisa
-- menunjuk periode yang tidak ada. `ON DELETE SET NULL` — bukan CASCADE: menghapus
-- satu periode tidak boleh menghapus ujian di bawahnya, ia hanya kehilangan
-- tanda periode dan kembali ke kelompok "tanpa periode".
--
-- Tag otomatis ada di dua pintu tulis (`POST /teacher/exams/new` dan
-- `/teacher/exams/<id>`): periode yang jendelanya ada di dalamnya, dan periode
-- berjalan bila jendelanya kosong. Backfill di bawah mengisi kertas yang sudah
-- ada, dan **hanya yang masih NULL** — tanda yang sudah diisi aturan atau manusia
-- tidak pernah ditimpa.
--
-- Idempoten dan tidak merusak: `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT
-- EXISTS`, backfill hanya pada NULL, dan tidak ada satu pernyataan pun yang
-- menghapus tabel, kolom, atau baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. kolom tanda periode ──────────────────────────────────────────────────

ALTER TABLE exams
    ADD COLUMN IF NOT EXISTS assessment_period_id UUID
        REFERENCES public.assessment_periods(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_exams_assessment_period
    ON exams (assessment_period_id);

-- ── 2. backfill kertas yang sudah ada ───────────────────────────────────────
--
-- Periode dipilih dari tanggal kertas: `start_at` kalau ada, jika tidak
-- `created_at`. Tanggal itu dicocokkan ke rentang periode **sekolah yang sama**,
-- dan bila beberapa periode bertumpang tindih yang terbaru menang supaya hasilnya
-- deterministik. Hanya baris NULL yang disentuh, jadi menjalankan ulang migrasi
-- tidak mengubah apa pun.

UPDATE exams e
SET assessment_period_id = matched.period_id
FROM (
    SELECT exam.id AS exam_id,
           (
               SELECT p.id
               FROM public.assessment_periods p
               WHERE p.school_id = exam.school_id
                 AND COALESCE(exam.start_at, exam.created_at)::date
                     BETWEEN p.start_date AND p.end_date
               ORDER BY p.start_date DESC
               LIMIT 1
           ) AS period_id
    FROM exams exam
    WHERE exam.assessment_period_id IS NULL
) AS matched
WHERE e.id = matched.exam_id
  AND matched.period_id IS NOT NULL;
