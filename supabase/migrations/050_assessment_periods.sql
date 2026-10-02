-- ──────────────────────────────────────────────────────────────
-- Migration 050: periode penilaian (UTS, UAS, Try Out, Asesmen Sekolah)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Masalah yang dijawab
-- ────────────────────
-- `exams.exam_type` sudah ada sejak migrasi 007 dengan kosakata yang persis
-- dibutuhkan sekolah — `ulangan, uts, uas, tryout, ljk` — tetapi **tidak ada satu
-- baris kode pun yang membacanya**. Jadi kosakatanya sudah disepakati sejak lama;
-- yang tidak pernah ada adalah cara **menjadwalkan** periode: tidak ada baris yang
-- menamai rentang tanggal, tidak ada cara mengatakan periode mana yang sedang
-- berjalan, dan tidak ada apa pun yang membuat pilihan itu berarti sama di halaman
-- setiap peran.
--
-- Tabel ini menjawabnya dengan satu baris per periode: jenisnya, namanya, tanggal
-- mulai dan selesainya, dan apakah ia periode yang **sedang berjalan**.
--
-- Satu periode berjalan per sekolah, dan itu invarian basis data
-- ─────────────────────────────────────────────────────────────
-- Pertanyaan "ujian ini masuk periode mana" harus punya tepat satu jawaban, sebab
-- jawaban itu yang membuat halaman guru dan daftar murid menunjuk periode yang sama.
-- Karena itu `CREATE UNIQUE INDEX ... WHERE is_active` — bukan aturan yang harus
-- diingat setiap rute tulis. Rute yang lupa membersihkan yang lama akan ditolak
-- database, bukan menghasilkan dua periode berjalan yang diam-diam berbeda antar
-- halaman.
--
-- Yang dijawab RLS di sini, dan yang tidak
-- ────────────────────────────────────
-- Backend memakai service key dan menembus RLS; yang menjaga rute Flask adalah
-- saringan `school_id` di `app/services/assessment_periods.py`. Policy di bawah
-- menjawab pertanyaan yang hanya bisa dijawab database: **kalau kunci publik yang
-- ikut terkirim di setiap halaman dipakai tanpa sesi, periode siapa yang terbaca
-- atau tertulis?** Jawabannya: hanya sekolahnya sendiri, dan menulis hanya wakil
-- kepala sekolah — wewenang yang didelegasikan, bukan wewenang oversight. Kepala
-- sekolah membaca (policy SELECT di bawah) tetapi tidak menulis.
--
-- Idempoten dan tidak merusak: setiap tabel/index memakai IF NOT EXISTS, setiap
-- policy di-drop lebih dulu, dan tidak ada satu pernyataan pun yang menghapus
-- tabel, kolom, atau baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. tabel periode ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.assessment_periods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    -- Kosakata yang sama dengan `exams.exam_type` (007) plus `asesmen` untuk
    -- Asesmen Sekolah; lihat catatan di atas.
    kind TEXT NOT NULL
        CONSTRAINT assessment_periods_kind_check
        CHECK (kind IN ('mid_semester', 'final_semester', 'tryout', 'asesmen')),
    name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Tanggal terbalik adalah periode yang tidak pernah berjalan dan tidak bisa
    -- ditampilkan; menahannya di sini membuat setiap pembaca tidak perlu memeriksa.
    CONSTRAINT assessment_periods_dates_check CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_assessment_periods_school
    ON public.assessment_periods(school_id, start_date DESC);

-- Invarian "tepat satu periode berjalan per sekolah" (lihat catatan di atas).
CREATE UNIQUE INDEX IF NOT EXISTS idx_assessment_periods_one_active
    ON public.assessment_periods (school_id)
    WHERE is_active;

-- ── 1b. kosakata `exams.exam_type` diperluas ke jenis periode yang sama ────
--
-- `exam_type` (007) menerima `ulangan, uts, uas, tryout, ljk`; tabel di atas
-- menerima `mid_semester, final_semester, tryout, asesmen`. Itu dua kata untuk
-- gagasan yang sama, dan membiarkannya berbeda berarti seorang guru tidak bisa
-- menandai ujiannya dengan periode yang baru saja dijadwalkan wakil kepala
-- sekolah — periode yang bisa dijadwalkan tetapi tidak bisa dipakai tidak berarti
-- apa-apa. Perluasan ini **aditif**: nilai lama tetap diterima supaya baris yang
-- sudah ada tidak pernah ditolak.
DO $$ BEGIN
    ALTER TABLE exams DROP CONSTRAINT IF EXISTS exams_exam_type_check;
    ALTER TABLE exams
        ADD CONSTRAINT exams_exam_type_check
        CHECK (exam_type IN ('ulangan', 'uts', 'uas', 'tryout', 'ljk',
                             'mid_semester', 'final_semester', 'asesmen'));
EXCEPTION WHEN undefined_table THEN NULL;
END $$;

-- ── 2. RLS: sekolah sendiri, baca dua pejabat, tulis wakil kepala ───────────

ALTER TABLE public.assessment_periods ENABLE ROW LEVEL SECURITY;

-- Kepala sekolah dan wakilnya membaca seluruh periode sekolahnya, berdampingan
-- dengan admin sekolah — persis seperti `school_official_required` memakai
-- `role_required(*OFFICIAL_ROLES)`. Policy yang menyebut salah satunya saja akan
-- membuat kepala sekolah dan wakilnya berbeda hak di tingkat database padahal di
-- tingkat rute keduanya membaca halaman yang sama.
DROP POLICY IF EXISTS "assessment_periods_select_official" ON public.assessment_periods;
CREATE POLICY "assessment_periods_select_official" ON public.assessment_periods
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal')
         OR public._is_role('admin_sekolah'))
        AND school_id = public._user_school_id()
    );

-- Guru dan murid juga perlu membacanya: halaman guru menandai periode yang
-- berjalan pada ujiannya, dan daftar murid menunjuk periode yang sama. Yang
-- dibaca adalah *kalender sekolah*, bukan data siapa pun — jadi membacanya
-- selebar satu sekolah tidak membocorkan apa-apa, sedangkan tidak membacanya
-- membuat dua peran itu menyebut periode yang berbeda dari pejabatnya.
DROP POLICY IF EXISTS "assessment_periods_select_school_member" ON public.assessment_periods;
CREATE POLICY "assessment_periods_select_school_member" ON public.assessment_periods
    FOR SELECT TO authenticated
    USING (school_id = public._user_school_id());

-- Menyusun kalender penilaian adalah wewenang yang didelegasikan ke wakil kepala
-- sekolah, sama seperti jadwal pengawasan (041). Kepala sekolah membaca lewat
-- policy di atas tetapi tidak menulis.
DROP POLICY IF EXISTS "assessment_periods_write_vice_principal" ON public.assessment_periods;
CREATE POLICY "assessment_periods_write_vice_principal" ON public.assessment_periods
    FOR ALL TO authenticated
    USING (
        public._is_role('vice_principal')
        AND school_id = public._user_school_id()
    )
    WITH CHECK (
        public._is_role('vice_principal')
        AND school_id = public._user_school_id()
    );
