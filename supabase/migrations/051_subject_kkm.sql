-- ──────────────────────────────────────────────────────────────
-- Migration 051: KKM per mapel, per tahun ajaran, opsional per tingkat
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Masalah yang dijawab
-- ────────────────────
-- Sampai sekarang KKM hidup di **`exams.passing_score`** — satu integer per
-- *ujian* dengan default 70, dibaca di ~27 tempat untuk memutuskan
-- lulus/tidak lulus. Itu bukan tempatnya: sekolah tahu "Geografi tuntas di 70
-- untuk kelas 7 dan 75 untuk kelas 9", dan dengan KKM per ujian satu mapel bisa
-- lulus dengan angka berbeda di dua ruang yang sama.
--
-- Tabel ini menyimpannya sebagai fakta milik sekolah: satu baris per (mapel,
-- tahun ajaran), dengan **`grade_level` opsional**. `NULL` berarti angka umum
-- mapel itu; baris dengan tingkat berarti override khusus tingkat itu. Bentuk
-- inilah jawaban atas pertanyaan Fase 0 (satu angka per mapel vs berbeda per
-- tingkat): sekolah yang hanya ingin satu angka per mapel **tidak pernah**
-- menulis override, jadi model sederhana adalah himpunan bagiannya, bukan skema
-- kedua.
--
-- Kenapa tahun ajaran ada di kunci
-- ────────────────────────────────
-- Kurikulum berubah, dan nilai yang sudah dilaporkan tahun lalu diputuskan oleh
-- angka yang berlaku **saat itu**. Membaca "KKM yang sekarang" untuk rapor tahun
-- lalu berarti mengubah nilai yang sudah ditunjukkan ke orang tua. Karena itu
-- tahunnya adalah bagian dari pertanyaan, bukan konteks.
--
-- Yang **tidak** diubah migrasi ini
-- ─────────────────────────────────
-- `exams.passing_score` tetap ada dan tetap dibaca laporan. Sebuah ujian
-- menyimpan salinan KKM saat ia dibuat, jadi nilai historis tidak pernah bergerak
-- walau KKM mapel diubah kemudian. Mengganti pembaca laporan agar menempuh
-- tabel ini adalah langkah berikutnya, bukan penghapusan kolom lama.
--
-- Idempoten dan tidak merusak: setiap tabel/index memakai IF NOT EXISTS, setiap
-- policy di-drop lebih dulu, dan tidak ada satu pernyataan pun yang menghapus
-- tabel, kolom, atau baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

CREATE TABLE IF NOT EXISTS public.subject_kkm (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    subject_id UUID NOT NULL REFERENCES public.subjects(id) ON DELETE CASCADE,
    school_year_id UUID REFERENCES public.school_years(id) ON DELETE CASCADE,
    -- NULL = angka umum mapel; diisi = override untuk tingkat itu saja.
    grade_level TEXT,
    kkm INTEGER NOT NULL DEFAULT 70
        CONSTRAINT subject_kkm_range_check CHECK (kkm BETWEEN 0 AND 100),
    updated_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subject_kkm_school
    ON public.subject_kkm(school_id, school_year_id);
CREATE INDEX IF NOT EXISTS idx_subject_kkm_subject
    ON public.subject_kkm(subject_id, school_year_id);

-- Postgres menganggap NULL berbeda satu sama lain, jadi indeks unik biasa
-- **tidak** menahan dua angka umum untuk satu mapel — dan setelah itu "angka
-- umumnya yang mana" bergantung pada urutan baris. Dua indeks parsial membuat
-- kedua kunci itu nyata: satu untuk angka umum, satu untuk override per tingkat.
CREATE UNIQUE INDEX IF NOT EXISTS idx_subject_kkm_general
    ON public.subject_kkm (subject_id, school_year_id)
    WHERE grade_level IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_subject_kkm_per_grade
    ON public.subject_kkm (subject_id, school_year_id, grade_level)
    WHERE grade_level IS NOT NULL;

-- ── RLS: satu sekolah, baca stafnya, tulis admin sekolah ────────────────────

ALTER TABLE public.subject_kkm ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "subject_kkm_school_read" ON public.subject_kkm;
CREATE POLICY "subject_kkm_school_read" ON public.subject_kkm
    FOR SELECT TO authenticated
    USING (school_id = public._user_school_id());

DROP POLICY IF EXISTS "subject_kkm_admin_write" ON public.subject_kkm;
CREATE POLICY "subject_kkm_admin_write" ON public.subject_kkm
    FOR ALL TO authenticated
    USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id())
    WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
