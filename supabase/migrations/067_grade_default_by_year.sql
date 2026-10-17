-- ──────────────────────────────────────────────────────────────
-- Migration 067: bobot default per tahun ajaran
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Masalah yang dijawab
-- ────────────────────
-- Migrasi 058 memberi sekolah **satu** distribusi default, disimpan pada
-- `grade_component_type.default_weight` — sifat komponen, bukan sifat tahun. Itu
-- cukup selama kebijakannya tidak pernah berubah, dan salah begitu berubah: nilai
-- akhir dihitung ulang setiap kali dibaca (`grade_weighting.compute` murni atas
-- peta bobot), jadi mengganti default untuk tahun ajaran baru **diam-diam**
-- mengubah nilai mapel-mapel yang mengikuti default di tahun-tahun lama — nilai
-- yang sudah dibagikan ke murid dan dilaporkan ke orang tua.
--
-- Yang ditambahkan di sini: **satu default per tahun ajaran**, sehingga tahun
-- baru bisa punya kebijakannya sendiri tanpa menyentuh tahun yang sudah berjalan.
--
-- Yang dibaca aplikasi, berurutan (satu tempat: `grade_weighting.default_config`)
-- ----------------------------------------------------------------------------
--   1. default milik tahun itu sendiri (tabel ini);
--   2. default tahun terdekat **sebelumnya** yang punya baris — kebijakan yang
--      sedang berlaku ketika tahun itu dinilai;
--   3. `grade_component_type.default_weight` (058) — default sekolah dari sebelum
--      bobot default punya tahun, yang tetap berlaku untuk tahun-tahun yang lebih
--      tua daripada default bertahun yang pertama;
--   4. tidak ada apa-apa: rata-rata sederhana.
--
-- Langkah 2 dan 3 ada supaya "nilai lama tetap memakai bobot saat dinilai" tidak
-- hanya berlaku untuk tahun yang sudah punya baris sendiri: tahun baru mewarisi
-- kebijakan yang berlaku saat ia dibuka, dan tahun lama tidak ikut bergerak ketika
-- tahun yang lebih baru diubah.
--
-- Total 100%
-- ----------
-- Seperti 057, aturan `SUM = 100` untuk distribusi yang dipakai ditegakkan di
-- aplikasi saat disimpan, bukan oleh CHECK (CHECK tidak bisa menjumlahkan baris).
-- Yang ditegakkan database adalah rentang satu baris (0-100) dan keunikan satu
-- komponen per (sekolah, tahun).
--
-- Tidak ada data yang dihapus atau diubah: `default_weight` (058) tetap di tempatnya
-- sebagai lantai untuk tahun-tahun yang lebih tua, dan tidak ada baris yang
-- di-backfill — sekolah yang belum pernah menyimpan default bertahun berperilaku
-- persis seperti sebelum migrasi ini.
--
-- Idempoten dan tidak merusak: setiap tabel/index memakai IF NOT EXISTS, setiap
-- policy di-drop lebih dulu. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya.

CREATE TABLE IF NOT EXISTS public.grade_default_weight (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    school_year_id UUID NOT NULL REFERENCES public.school_years(id) ON DELETE CASCADE,
    component_id UUID NOT NULL REFERENCES public.grade_component_type(id) ON DELETE CASCADE,
    weight_percent INTEGER NOT NULL DEFAULT 0
        CONSTRAINT grade_default_weight_range_check CHECK (weight_percent BETWEEN 0 AND 100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Satu bobot per (tahun, komponen): menyimpan ulang berarti mengubah baris yang
-- sama, bukan menumpuk baris kedua yang membuat "bobot mana yang berlaku"
-- bergantung urutan baca.
CREATE UNIQUE INDEX IF NOT EXISTS idx_grade_default_unique
    ON public.grade_default_weight (school_year_id, component_id);
CREATE INDEX IF NOT EXISTS idx_grade_default_school_year
    ON public.grade_default_weight (school_id, school_year_id, is_active);

-- ── RLS: baca staf sekolah, tulis admin sekolah (sama dengan 057) ───────────

ALTER TABLE public.grade_default_weight ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "grade_default_school_read" ON public.grade_default_weight;
CREATE POLICY "grade_default_school_read" ON public.grade_default_weight
    FOR SELECT TO authenticated
    USING (school_id = public._user_school_id());

DROP POLICY IF EXISTS "grade_default_admin_write" ON public.grade_default_weight;
CREATE POLICY "grade_default_admin_write" ON public.grade_default_weight
    FOR ALL TO authenticated
    USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id())
    WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
