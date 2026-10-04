-- ──────────────────────────────────────────────────────────────
-- Migration 058: bobot default per komponen (school-wide weight defaults)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Masalah yang dijawab
-- ────────────────────
-- Migrasi 057 memberi bobot **per mapel per tahun**: admin mengisi setiap mapel
-- satu per satu. Sekolah yang memakai satu kebijakan seragam (Tugas 30%, UTS 30%,
-- UAS 40%) harus mengulang kebijakan itu di dua puluh mapel, dan setiap mapel
-- yang belum disentuh tetap jatuh ke rata-rata sederhana padahal sekolah sudah
-- menyatakan bobotnya.
--
-- Yang ditambahkan di sini: **satu distribusi default milik sekolah**, disimpan
-- pada komponen itu sendiri (`default_weight`). Setiap mapel **mengikuti** default
-- ini sampai admin memberi distribusi khusus untuk mapel itu; distribusi khusus
-- tetap menang. Tidak ada tabel baru dan tidak ada baris yang dihapus — kolom
-- tambahan pada `grade_component_type` (057) cukup, karena default adalah sifat
-- komponen, bukan sifat (mapel, tahun).
--
-- Total 100%
-- ----------
-- Seperti 057, aturan `SUM = 100` untuk distribusi yang **diaktifkan** ditegakkan
-- di aplikasi saat disimpan, bukan oleh CHECK (CHECK tidak bisa menjumlahkan
-- baris). Yang bisa ditegakkan database adalah rentang satu baris (0-100).
--
-- Idempoten dan tidak merusak: satu `ADD COLUMN IF NOT EXISTS`, satu constraint
-- yang di-drop lebih dulu, tanpa BEGIN/COMMIT (transaksinya milik
-- `deploy/apply_migration.py`). Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`.

ALTER TABLE public.grade_component_type
    ADD COLUMN IF NOT EXISTS default_weight INTEGER;

ALTER TABLE public.grade_component_type
    DROP CONSTRAINT IF EXISTS grade_component_default_weight_range;
ALTER TABLE public.grade_component_type
    ADD CONSTRAINT grade_component_default_weight_range
        CHECK (default_weight IS NULL OR default_weight BETWEEN 0 AND 100);
