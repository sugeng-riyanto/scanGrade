-- ──────────────────────────────────────────────────────────────
-- Migration 041: jadwal pengawasan ujian & permintaan ujian ulang
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Tiga tabel, satu alur:
--
--   invigilation_schedules   satu baris = satu *pelaksanaan*: ujian X di kelas Y
--                            pada waktu Z, di ruang R. Unik per (ujian, kelas),
--                            sebab satu kelas tidak bisa mengerjakan satu ujian
--                            dua kali pada waktu yang sama.
--   invigilator_assignments  satu baris = satu guru yang mengawasi satu
--                            pelaksanaan. Unik per (pelaksanaan, guru), sehingga
--                            menugaskan ulang guru yang sama tidak menggandakan
--                            daftar hadir pengawas.
--   exam_retake_requests     satu baris = satu permintaan murid untuk mengerjakan
--                            ulang satu ujian, diputuskan pengawas kelas itu
--                            (atau wakil kepala sekolah / kepala sekolah).
--
-- Kenapa `school_id` ditulis di ketiga tabel padahal bisa ditempuh lewat `exam_id`
-- ────────────────────────────────────────────────────────────────────────────
-- Karena setiap *tulis* di ketiga tabel difilter oleh kolom itu, dan satu kolom
-- yang selalu ada di baris yang ditulis tidak bisa lupa disaring: rantai
-- `.eq("school_id", …)` hanya bisa ditulis kalau kolomnya ada di baris itu. Kalau
-- cakupan sekolah hanya ditempuh lewat `exam_id`, satu query tulis yang menyebut
-- `id` pelaksanaan tanpa membaca `exams` lebih dulu akan lolos ke sekolah lain —
-- dan itu bentuk kesalahan yang paling mudah terjadi di rute POST.
--
-- Yang dijawab RLS di sini, dan yang tidak
-- ────────────────────────────────────
-- Backend memakai service key dan menembus RLS sepenuhnya, jadi policy di bawah
-- bukan yang menjaga rute Flask — yang menjaganya adalah saringan `school_id` di
-- `app/services/invigilation.py`. Policy ini menjawab pertanyaan kedua, dan satu-
-- satunya yang bisa dijawab database: **kalau kunci publik yang ikut terkirim di
-- setiap halaman dipakai tanpa sesi, apa yang terbaca atau tertulis?**
--
-- Aturannya:
--   * **hanya sekolahnya sendiri.** Setiap predikat membandingkan kolom sekolah
--     baris dengan `public._user_school_id()` milik pemanggil. Tidak ada satu pun
--     policy di sini yang bisa membaca atau menulis lintas NPSN.
--   * **`TO authenticated` ditulis eksplisit.** Policy tanpa klausa `TO` berlaku
--     untuk `PUBLIC`, termasuk `anon` — kunci yang tidak rahasia. Predikatnya
--     memang sudah menuntut sesi, tetapi menyebut pemanggilnya membuat niatnya
--     terbaca, bukan disimpulkan.
--   * **menulis di sini adalah wewenang pengawas, bukan guru biasa.** Kepala
--     sekolah dan wakilnya menyusun jadwal; guru *mengubah* satu hal saja, yaitu
--     keputusan atas permintaan ujian ulang untuk ujian yang benar-benar ia awasi.
--     Itu pun dibatasi lewat `invigilator_assignments`, bukan lewat peran saja:
--     guru yang tidak mengawasi ujian itu tidak punya urusan di barisnya.
--
-- Idempoten dan tidak merusak: setiap policy di-drop lebih dulu (DROP POLICY IF
-- EXISTS), setiap tabel dan index memakai IF NOT EXISTS, dan tidak ada satu
-- pernyataan pun yang menghapus tabel, kolom, atau baris. Dijalankan dua kali
-- hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback), dan kontrol transaksi
-- di dalam berkas membuat dry run menolak berkas ini karena rollback-nya tidak
-- lagi bisa membatalkan apa pun. Semua migrasi lain di folder ini juga begitu.

-- ── 1. pelaksanaan ujian & pengawasnya ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.invigilation_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES public.classes(id) ON DELETE CASCADE,
    scheduled_at TIMESTAMPTZ NOT NULL,
    room TEXT,
    notes TEXT,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT invigilation_schedules_sitting_key UNIQUE (exam_id, class_id)
);

CREATE INDEX IF NOT EXISTS idx_invigilation_schedules_school
    ON public.invigilation_schedules(school_id, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_invigilation_schedules_exam
    ON public.invigilation_schedules(exam_id);

CREATE TABLE IF NOT EXISTS public.invigilator_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    schedule_id UUID NOT NULL REFERENCES public.invigilation_schedules(id) ON DELETE CASCADE,
    teacher_id UUID NOT NULL REFERENCES public.teachers(id) ON DELETE CASCADE,
    is_lead BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT invigilator_assignments_pair_key UNIQUE (schedule_id, teacher_id)
);

CREATE INDEX IF NOT EXISTS idx_invigilator_assignments_teacher
    ON public.invigilator_assignments(teacher_id);
CREATE INDEX IF NOT EXISTS idx_invigilator_assignments_schedule
    ON public.invigilator_assignments(schedule_id);

-- ── 2. permintaan ujian ulang ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.exam_retake_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.students(id) ON DELETE CASCADE,
    class_id UUID REFERENCES public.classes(id) ON DELETE SET NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CONSTRAINT exam_retake_requests_status_check
        CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    decided_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    decision_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Satu permintaan terbuka per (ujian, murid). Index parsial, bukan unique biasa:
-- riwayat tetap utuh — seorang murid yang ditolak hari ini boleh meminta lagi
-- setelah kejadiannya, dan barisnya yang lama tetap bisa dibaca.
CREATE UNIQUE INDEX IF NOT EXISTS idx_exam_retake_requests_one_open
    ON public.exam_retake_requests(exam_id, student_id)
    WHERE status IN ('pending', 'approved');

CREATE INDEX IF NOT EXISTS idx_exam_retake_requests_school
    ON public.exam_retake_requests(school_id, status);
CREATE INDEX IF NOT EXISTS idx_exam_retake_requests_student
    ON public.exam_retake_requests(student_id);

-- ── 3. RLS: hanya baca sekolahnya, hanya tulis pengawasnya ──────────────────

ALTER TABLE public.invigilation_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invigilator_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exam_retake_requests ENABLE ROW LEVEL SECURITY;

-- Kepala sekolah dan wakilnya membaca seluruh jadwal sekolahnya. Kedua nama peran
-- ditulis berdampingan di setiap policy, persis seperti `school_official_required`
-- memakai `role_required(*OFFICIAL_ROLES)` — policy yang menyebut salah satunya
-- saja membuat kepala sekolah dan wakilnya berbeda hak di tingkat database,
-- padahal di tingkat rute keduanya satu.
DROP POLICY IF EXISTS "invigilation_schedules_select_official" ON public.invigilation_schedules;
CREATE POLICY "invigilation_schedules_select_official" ON public.invigilation_schedules
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal')
         OR public._is_role('admin_sekolah'))
        AND school_id = public._user_school_id()
    );

-- Guru membaca pelaksanaan yang ia awasi sendiri, dan itu dinyatakan lewat
-- `invigilator_assignments` alih-alih lewat peran: seorang guru berhak tahu jadwal
-- yang menjadi tugasnya, bukan seluruh jadwal sekolah.
DROP POLICY IF EXISTS "invigilation_schedules_select_invigilator" ON public.invigilation_schedules;
CREATE POLICY "invigilation_schedules_select_invigilator" ON public.invigilation_schedules
    FOR SELECT TO authenticated
    USING (
        school_id = public._user_school_id()
        AND EXISTS (
            SELECT 1
            FROM public.invigilator_assignments a
            JOIN public.teachers t ON t.id = a.teacher_id
            WHERE a.schedule_id = public.invigilation_schedules.id
              AND t.id = auth.uid()
        )
    );

-- Hanya wakil kepala sekolah yang menyusun jadwal. Kepala sekolah membaca
-- (policy di atas) tetapi tidak menulis, sesuai matriks di docs/RBAC.md: menyusun
-- jadwal adalah wewenang yang didelegasikan, bukan wewenang oversight.
DROP POLICY IF EXISTS "invigilation_schedules_write_vice_principal" ON public.invigilation_schedules;
CREATE POLICY "invigilation_schedules_write_vice_principal" ON public.invigilation_schedules
    FOR ALL TO authenticated
    USING (
        public._is_role('vice_principal')
        AND school_id = public._user_school_id()
    )
    WITH CHECK (
        public._is_role('vice_principal')
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "invigilator_assignments_select_official" ON public.invigilator_assignments;
CREATE POLICY "invigilator_assignments_select_official" ON public.invigilator_assignments
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal')
         OR public._is_role('admin_sekolah'))
        AND school_id = public._user_school_id()
    );

-- Guru membaca penugasan yang menunjuk dirinya — halaman "tugas saya" berdiri di
-- atas policy ini — dan juga penugasan pada pelaksanaan yang ia awasi, sebab
-- seorang pengawas perlu melihat rekan pengawas di ruang yang sama.
DROP POLICY IF EXISTS "invigilator_assignments_select_own" ON public.invigilator_assignments;
CREATE POLICY "invigilator_assignments_select_own" ON public.invigilator_assignments
    FOR SELECT TO authenticated
    USING (
        school_id = public._user_school_id()
        AND (
            EXISTS (
                SELECT 1 FROM public.teachers t
                WHERE t.id = public.invigilator_assignments.teacher_id
                  AND t.id = auth.uid()
            )
            OR EXISTS (
                SELECT 1
                FROM public.invigilator_assignments mine
                WHERE mine.schedule_id = public.invigilator_assignments.schedule_id
                  AND mine.teacher_id = auth.uid()
            )
        )
    );

DROP POLICY IF EXISTS "invigilator_assignments_write_vice_principal" ON public.invigilator_assignments;
CREATE POLICY "invigilator_assignments_write_vice_principal" ON public.invigilator_assignments
    FOR ALL TO authenticated
    USING (
        public._is_role('vice_principal')
        AND school_id = public._user_school_id()
    )
    WITH CHECK (
        public._is_role('vice_principal')
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "exam_retake_requests_select_official" ON public.exam_retake_requests;
CREATE POLICY "exam_retake_requests_select_official" ON public.exam_retake_requests
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal')
         OR public._is_role('admin_sekolah'))
        AND school_id = public._user_school_id()
    );

-- Murid membaca permintaannya sendiri. Penghubungnya adalah **`students.id` itu
-- sendiri**: tabel `students` dan `teachers` memakai `profiles.id` sebagai primary
-- key-nya (`id UUID PRIMARY KEY REFERENCES profiles(id)`), jadi `auth.uid()`
-- membandingkan langsung dengan `student_id`/`teacher_id`. Tidak ada kolom
-- `profile_id` di kedua tabel itu, dan policy yang menyebutnya akan gagal dibuat.
DROP POLICY IF EXISTS "exam_retake_requests_select_own" ON public.exam_retake_requests;
CREATE POLICY "exam_retake_requests_select_own" ON public.exam_retake_requests
    FOR SELECT TO authenticated
    USING (
        school_id = public._user_school_id()
        AND public.exam_retake_requests.student_id = auth.uid()
    );

DROP POLICY IF EXISTS "exam_retake_requests_insert_own" ON public.exam_retake_requests;
CREATE POLICY "exam_retake_requests_insert_own" ON public.exam_retake_requests
    FOR INSERT TO authenticated
    WITH CHECK (
        school_id = public._user_school_id()
        AND status = 'pending'
        AND public.exam_retake_requests.student_id = auth.uid()
    );

-- Guru memutuskan hanya untuk ujian yang ia awasi. Ini policy yang menjaga
-- keputusan dari guru yang tidak ada di ruangan itu; saringan yang sama ditulis
-- sekali lagi di `invigilation.decide_retake` (service key menembus RLS), sebab
-- policy yang tidak pernah dieksekusi tetap tidak menjaga rute Flask.
DROP POLICY IF EXISTS "exam_retake_requests_decide_invigilator" ON public.exam_retake_requests;
CREATE POLICY "exam_retake_requests_decide_invigilator" ON public.exam_retake_requests
    FOR UPDATE TO authenticated
    USING (
        school_id = public._user_school_id()
        AND EXISTS (
            SELECT 1
            FROM public.invigilation_schedules sc
            JOIN public.invigilator_assignments a ON a.schedule_id = sc.id
            JOIN public.teachers t ON t.id = a.teacher_id
            WHERE sc.exam_id = public.exam_retake_requests.exam_id
              AND sc.school_id = public.exam_retake_requests.school_id
              AND a.teacher_id = auth.uid()
        )
    )
    WITH CHECK (
        school_id = public._user_school_id()
        AND status IN ('approved', 'rejected', 'withdrawn')
    );

DROP POLICY IF EXISTS "exam_retake_requests_decide_vice_principal" ON public.exam_retake_requests;
CREATE POLICY "exam_retake_requests_decide_vice_principal" ON public.exam_retake_requests
    FOR UPDATE TO authenticated
    USING (
        public._is_role('vice_principal')
        AND school_id = public._user_school_id()
    )
    WITH CHECK (
        public._is_role('vice_principal')
        AND school_id = public._user_school_id()
        AND status IN ('approved', 'rejected', 'withdrawn')
    );

-- `school_id` tidak boleh berpindah: seorang murid yang membuat permintaan di
-- sekolahnya tidak boleh memindahkan barisnya ke sekolah lain, dan seorang wakil
-- kepala sekolah tidak boleh menyerahkan jadwalnya ke NPSN lain. WITH CHECK di
-- atas sudah menahan baris *baru*, tetapi UPDATE mengevaluasi USING terhadap baris
-- lama dan WITH CHECK terhadap baris hasil — dua-duanya di sini membandingkan
-- dengan sekolah pemanggil, jadi baris hanya bisa berakhir di sekolah yang sama.
