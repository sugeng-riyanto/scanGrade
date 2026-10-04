-- ──────────────────────────────────────────────────────────────
-- Migration 059: matriks pengawas ujian (periode x ruangan)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Tiga tabel, satu matriks:
--
--   exam_period        satu baris = satu *slot waktu* hari ujian (mis. "Sesi 1",
--                     07:30-09:30). Sekolah menyusunnya sendiri lewat halaman
--                     matriks; tidak ada developer yang perlu menyentuh SQL.
--   exam_room          satu baris = satu ruangan ujian sekolah, dengan kapasitas.
--   invigilation_duty satu baris = satu *sel* matriks: guru G mengawasi ruangan R
--                     pada slot P di tanggal D. Inilah yang dibaca matriks dan
--                     yang diisi baik lewat klik sel maupun lewat unggahan Excel.
--
-- Kenapa `exam_period` terpisah dari `invigilation_schedules`
-- ──────────────────────────────────────────────────────────
-- `invigilation_schedules` (migrasi 041) menyimpan *pelaksanaan ujian* — ujian X
-- di kelas Y pada waktu Z, satu baris per kelas yang mengerjakan. Itu jawaban
-- untuk "kapan kelas ini mengerjakan ujian ini". Matriks pengawas menjawab
-- pertanyaan yang berbeda: "siapa yang berdiri di ruangan ini pada slot ini".
-- Sebuah ruangan berisi banyak kelas dari banyak ujian sekaligus, jadi matriks
-- tidak bisa diturunkan dari pelaksanaan satu kelas — ia butuh slot dan ruangan
-- sebagai sumbunya sendiri. Keduanya hidup berdampingan.
--
-- Kenapa `school_id` ditulis di ketiga tabel
-- ──────────────────────────────────────────
-- Sama seperti 041: setiap tulis difilter oleh kolom itu, dan kolom yang selalu
-- ada di baris yang ditulis tidak bisa lupa disaring. Cakupan lewat
-- period_id/room_id saja akan menuntut setiap rute POST membaca tabel lain lebih
-- dulu, dan rute yang lupa adalah baris yang berpindah NPSN.
--
-- Dua constraint unik yang mencegah bentrok (inti validasi bentrok)
-- ──────────────────────────────────────────────────────────────────
--   invigilation_duty_room_slot_key     satu sel = satu guru. Ruangan yang sama
--                                       pada slot+tanggal yang sama tidak bisa
--                                       punya dua pengawas lewat dua baris ini.
--   invigilation_duty_teacher_slot_key  satu guru tidak bisa berada di dua
--                                       ruangan pada slot+tanggal yang sama.
-- Keduanya ditegakkan **database**, bukan hanya aplikasi: aplikasi mengecek
-- lebih dulu supaya pesannya ramah ("Budi sudah mengawas di Sesi 1"), dan index
-- ini yang menahan dua POST nyaris bersamaan.
--
-- Yang dijawab RLS di sini, dan yang tidak
-- ─────────────────────────────────────
-- Backend memakai service key dan menembus RLS, jadi policy di bawah bukan yang
-- menjaga rute Flask — yang menjaganya saringan `school_id` di
-- `app/services/invigilation_matrix.py`. Policy ini menjawab pertanyaan kedua:
-- kalau kunci publik yang ikut terkirim di setiap halaman dipakai tanpa sesi,
-- apa yang terbaca atau tertulis? Jawabannya: hanya sekolahnya sendiri, dan
-- hanya oleh peran yang memang menyusun jadwal (admin sekolah, wakil kepala
-- sekolah, kepala sekolah membaca).
--
-- Idempoten dan tidak merusak: setiap policy di-drop lebih dulu, setiap tabel dan
-- index memakai IF NOT EXISTS, dan tidak ada satu pun pernyataan yang menghapus
-- tabel, kolom, atau baris.
--
-- Sengaja TANPA BEGIN;/COMMIT; — `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. slot waktu hari ujian ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.exam_period (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    -- Jam dinding hari itu, bukan timestamptz: "Sesi 1" adalah 07:30 setiap hari
    -- ujian, dan menyimpannya sebagai jam-hari-ini menghindari zona waktu yang
    -- berbeda antar tanggal.
    starts_at TIME,
    ends_at TIME,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT exam_period_name_key UNIQUE (school_id, name)
);

CREATE INDEX IF NOT EXISTS idx_exam_period_school
    ON public.exam_period(school_id, sort_order);

-- ── 2. ruangan ujian ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.exam_room (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    capacity INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT exam_room_name_key UNIQUE (school_id, name)
);

CREATE INDEX IF NOT EXISTS idx_exam_room_school
    ON public.exam_room(school_id, name);

-- ── 3. sel matriks: guru x ruangan x slot x tanggal ─────────────────────────

CREATE TABLE IF NOT EXISTS public.invigilation_duty (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    exam_date DATE NOT NULL,
    period_id UUID NOT NULL REFERENCES public.exam_period(id) ON DELETE CASCADE,
    room_id UUID NOT NULL REFERENCES public.exam_room(id) ON DELETE CASCADE,
    teacher_id UUID NOT NULL REFERENCES public.teachers(id) ON DELETE CASCADE,
    -- Dari mana baris ini datang, supaya matriks bisa menandai sel hasil unggahan
    -- dan operator bisa menelusuri asalnya.
    source TEXT NOT NULL DEFAULT 'manual'
        CONSTRAINT invigilation_duty_source_check CHECK (source IN ('manual', 'excel_upload')),
    notes TEXT,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT invigilation_duty_room_slot_key UNIQUE (school_id, exam_date, period_id, room_id),
    CONSTRAINT invigilation_duty_teacher_slot_key UNIQUE (school_id, exam_date, period_id, teacher_id)
);

CREATE INDEX IF NOT EXISTS idx_invigilation_duty_school_date
    ON public.invigilation_duty(school_id, exam_date);
CREATE INDEX IF NOT EXISTS idx_invigilation_duty_teacher
    ON public.invigilation_duty(teacher_id);
CREATE INDEX IF NOT EXISTS idx_invigilation_duty_period
    ON public.invigilation_duty(period_id);

-- ── 4. RLS: hanya sekolahnya sendiri, hanya peran penyusun yang menulis ─────

ALTER TABLE public.exam_period ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exam_room ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invigilation_duty ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "exam_period_read_own_school" ON public.exam_period;
CREATE POLICY "exam_period_read_own_school" ON public.exam_period
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal')
         OR public._is_role('admin_sekolah'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "exam_period_write_schedulers" ON public.exam_period;
CREATE POLICY "exam_period_write_schedulers" ON public.exam_period
    FOR ALL TO authenticated
    USING (
        (public._is_role('admin_sekolah') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    )
    WITH CHECK (
        (public._is_role('admin_sekolah') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "exam_room_read_own_school" ON public.exam_room;
CREATE POLICY "exam_room_read_own_school" ON public.exam_room
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal')
         OR public._is_role('admin_sekolah'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "exam_room_write_schedulers" ON public.exam_room;
CREATE POLICY "exam_room_write_schedulers" ON public.exam_room
    FOR ALL TO authenticated
    USING (
        (public._is_role('admin_sekolah') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    )
    WITH CHECK (
        (public._is_role('admin_sekolah') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "invigilation_duty_read_own_school" ON public.invigilation_duty;
CREATE POLICY "invigilation_duty_read_own_school" ON public.invigilation_duty
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal')
         OR public._is_role('admin_sekolah'))
        AND school_id = public._user_school_id()
    );

-- Seorang guru membaca tugasnya sendiri — halaman "tugas saya" berdiri di atas
-- policy ini — dan tidak seluruh matriks sekolah.
DROP POLICY IF EXISTS "invigilation_duty_read_own_teacher" ON public.invigilation_duty;
CREATE POLICY "invigilation_duty_read_own_teacher" ON public.invigilation_duty
    FOR SELECT TO authenticated
    USING (
        school_id = public._user_school_id()
        AND teacher_id = auth.uid()
    );

DROP POLICY IF EXISTS "invigilation_duty_write_schedulers" ON public.invigilation_duty;
CREATE POLICY "invigilation_duty_write_schedulers" ON public.invigilation_duty
    FOR ALL TO authenticated
    USING (
        (public._is_role('admin_sekolah') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    )
    WITH CHECK (
        (public._is_role('admin_sekolah') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );
