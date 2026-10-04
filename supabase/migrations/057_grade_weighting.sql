-- ──────────────────────────────────────────────────────────────
-- Migration 057: komponen nilai berbobot (weighted grade components)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Masalah yang dijawab
-- ────────────────────
-- Sampai sekarang "nilai akhir" seorang murid untuk satu mapel adalah
-- **rata-rata sederhana** dari semua nilainya di mapel itu — setiap ujian
-- berbobot sama. Sekolah yang sesungguhnya memberi bobot (mis. Tugas 30%,
-- UTS 30%, UAS 40%) tidak bisa menyatakannya, sehingga nilai akhir yang
-- ditampilkan bukan nilai yang sekolah maksud.
--
-- Dua tabel, dan pemisahannya disengaja:
--
--   * `grade_component_type` adalah **daftar komponen** milik satu sekolah —
--     "Tugas Kelas", "Proyek", "Ulangan Harian", "UTS", "UAS", atau nama lain
--     sesuai kebijakan sekolah. Ini daftar yang bisa dibaca dan disunting admin.
--   * `grade_weight_config` adalah **bobot** komponen itu untuk satu mapel di
--     satu tahun ajaran (lihat "Granularitas" di bawah).
--
-- Granularitas: PER MAPEL PER TAHUN AJARAN
-- ----------------------------------------
-- Paling fleksibel, dan karena itu default. Sekolah yang ingin satu kebijakan
-- seragam cukup memberi bobot yang sama ke setiap mapel — model yang lebih luas
-- adalah himpunan bagiannya, bukan skema kedua. Tahun ajaran adalah bagian dari
-- kunci karena bobot yang dulu memutuskan nilai yang sudah dilaporkan tidak boleh
-- bergerak setelahnya (selaras prinsip KKM, migrasi 051).
--
-- Total 100%
-- ----------
-- `SUM(bobot_persen) = 100` untuk sebuah (mapel, tahun) ditegakkan **di aplikasi**
-- saat konfigurasi disimpan (dan saat diaktifkan) — bukan oleh CHECK, karena CHECK
-- tidak bisa menjumlahkan baris. Yang bisa ditegakkan database adalah rentang
-- satu baris (0-100) dan keunikan satu komponen per (mapel, tahun).
--
-- Yang **tidak** diubah migrasi ini
-- --------------------------------
-- `exams.exam_type` (007) tetap ada dengan kosakata tetapnya. Kolom baru
-- `grade_component_type_id` adalah penanda yang bisa diatur sekolah dan boleh
-- NULL: ujian lama tanpa penanda tidak rusak, dan sekolah yang belum mengatur
-- bobot apa pun tetap mendapat rata-rata sederhana (fallback di aplikasi).
--
-- Idempoten dan tidak merusak: setiap tabel/index memakai IF NOT EXISTS, setiap
-- policy di-drop lebih dulu, dan tidak ada satu pernyataan pun yang menghapus
-- tabel, kolom, atau baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. komponen nilai yang tersedia untuk sekolah ini ───────────────────────

CREATE TABLE IF NOT EXISTS public.grade_component_type (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Nama komponen unik per sekolah (peka huruf: "UTS" dan "uts" adalah dua hal yang
-- membingungkan, jadi disamakan lewat indeks ekspresi).
CREATE UNIQUE INDEX IF NOT EXISTS idx_grade_component_name
    ON public.grade_component_type (school_id, lower(name));
CREATE INDEX IF NOT EXISTS idx_grade_component_school
    ON public.grade_component_type (school_id, is_active, sort_order);

-- ── 2. bobot per mapel per tahun ajaran ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.grade_weight_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    subject_id UUID NOT NULL REFERENCES public.subjects(id) ON DELETE CASCADE,
    school_year_id UUID NOT NULL REFERENCES public.school_years(id) ON DELETE CASCADE,
    component_id UUID NOT NULL REFERENCES public.grade_component_type(id) ON DELETE CASCADE,
    weight_percent INTEGER NOT NULL DEFAULT 0
        CONSTRAINT grade_weight_range_check CHECK (weight_percent BETWEEN 0 AND 100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Satu bobot per (mapel, tahun, komponen): menyimpan ulang berarti mengubah baris
-- yang sama, bukan menumpuk baris kedua yang membuat "bobot mana yang berlaku"
-- bergantung urutan baca.
CREATE UNIQUE INDEX IF NOT EXISTS idx_grade_weight_unique
    ON public.grade_weight_config (subject_id, school_year_id, component_id);
CREATE INDEX IF NOT EXISTS idx_grade_weight_subject_year
    ON public.grade_weight_config (school_id, subject_id, school_year_id, is_active);

-- ── 3. penanda komponen pada ujian ──────────────────────────────────────────
-- NULLABLE dengan sengaja: ujian yang sudah ada (dan sekolah yang belum mengatur
-- bobot) tetap valid. ON DELETE SET NULL supaya menonaktifkan/menghapus komponen
-- tidak menghapus ujiannya — ujian itu hanya kembali "belum dikategorikan".

ALTER TABLE public.exams
    ADD COLUMN IF NOT EXISTS grade_component_type_id UUID
        REFERENCES public.grade_component_type(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_exams_grade_component
    ON public.exams (grade_component_type_id);

-- ── 4. RLS: baca staf sekolah, tulis admin sekolah ──────────────────────────

ALTER TABLE public.grade_component_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.grade_weight_config ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "grade_component_school_read" ON public.grade_component_type;
CREATE POLICY "grade_component_school_read" ON public.grade_component_type
    FOR SELECT TO authenticated
    USING (school_id = public._user_school_id());

DROP POLICY IF EXISTS "grade_component_admin_write" ON public.grade_component_type;
CREATE POLICY "grade_component_admin_write" ON public.grade_component_type
    FOR ALL TO authenticated
    USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id())
    WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());

DROP POLICY IF EXISTS "grade_weight_school_read" ON public.grade_weight_config;
CREATE POLICY "grade_weight_school_read" ON public.grade_weight_config
    FOR SELECT TO authenticated
    USING (school_id = public._user_school_id());

DROP POLICY IF EXISTS "grade_weight_admin_write" ON public.grade_weight_config;
CREATE POLICY "grade_weight_admin_write" ON public.grade_weight_config
    FOR ALL TO authenticated
    USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id())
    WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
