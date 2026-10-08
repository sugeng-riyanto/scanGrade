-- ──────────────────────────────────────────────────────────────
-- Migration 063: permintaan guru untuk bergabung ke NPSN lain
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor. Dry run wajib: berkas ini mengubah skema.
--
-- Dibangun DI ATAS `teacher_school_membership` (migrasi 044), TIDAK menggantinya.
-- 044 sudah memisahkan identitas (satu baris `profiles`) dari keanggotaan (banyak
-- baris, satu per sekolah), dan sudah punya kosakata statusnya sendiri. Berkas ini
-- menambah tiga hal:
--
--   school_membership_request   permintaan guru untuk bergabung ke NPSN lain,
--                               beserta keputusan sekolah tujuan.
--   membership_consent_log      bukti eksplisit persetujuan syarat & ketentuan
--                               privasi lintas-sekolah, DENGAN VERSI dokumen.
--   kolom penutupan di          status 'closed' + siapa/kapan menutup dan
--   teacher_school_membership   siapa/kapan membuka kembali.
--
-- INISIATIF DARI GURU, BUKAN ADMIN — dan skemanya yang menegakkannya
-- ─────────────────────────────────────────────────────────────────
-- Alur "undang guru terdaftar" yang sudah ada (044) dimulai admin sekolah dan
-- menghasilkan baris `teacher_school_membership` berstatus 'invited'. Alur ini
-- kebalikannya: guru yang mencari sekolah tujuan dan mengajukan diri, jadi
-- `school_membership_request` adalah SATU-SATUNYA jalan masuk bagi guru dari
-- sekolah lain. Tidak ada satu pernyataan pun di sini yang membuat admin sekolah
-- bisa menambahkan guru sekolah lain secara sepihak: yang bisa dilakukan admin
-- adalah MEMUTUSKAN permintaan yang sudah ada (`decided_by`), dan hanya di
-- sekolah tujuan permintaan itu.
--
-- KEANGGOTAAN LAIN GURU TIDAK DISIMPAN DI SINI — dan itu disengaja
-- ──────────────────────────────────────────────────────────────
-- Tidak ada kolom "sekolah asal". Itu bukan kelalaian: prinsip desainnya adalah
-- sekolah tujuan HANYA melihat identitas dasar guru (nama, email, status akun),
-- dan keanggotaan guru di NPSN lain adalah informasi milik guru, bukan konsumsi
-- sekolah lain. Dengan tidak menyimpannya, kebocoran itu tidak mungkin terjadi
-- lewat query yang lupa menyaring — bukan sekadar tidak ditampilkan.
--
-- PENUTUPAN TOTAL (OFFBOARDING) MEMAKAI STATUS, BUKAN PENGHAPUSAN
-- ───────────────────────────────────────────────────────────────
-- Status 'closed' ditambahkan ke kosakata 044 TANPA membuang nilai lama
-- ('active', 'invited', 'inactive'): baris yang sudah ada tidak boleh menjadi
-- tidak valid hanya karena migrasi ini dijalankan.
--
-- Konsekuensi penutupan datang dari kode yang SUDAH ADA, dan itu disengaja:
-- `school_membership.memberships_for` dan `is_active_member` menyaring
-- `status = 'active'`, jadi setiap status selain 'active' otomatis berarti "tidak
-- punya akses" — termasuk sekolah yang ditutup. Tidak ada jalan baca kedua yang
-- harus diingat untuk ikut ditutup.
--
-- ASIMETRI YANG SENGAJA, DAN INI BUKAN BUG
-- ────────────────────────────────────────
-- Persetujuan (Fase 6) boleh dilakukan admin_sekolah, principal, ATAU
-- vice_principal di sekolah tujuan — satu persetujuan cukup.
-- Pembukaan kembali keanggotaan yang ditutup (Fase 9) HANYA admin_sekolah.
-- Kolom `reopened_by` di bawah tidak membatasi siapa yang boleh menulisnya; yang
-- membatasinya adalah rute (app/routes/... Fase 9) dan test adversarialnya.
-- Asimetri itu keputusan produk yang eksplisit: mencabut akses seseorang adalah
-- tindakan operasional sekolah, membuka kembali akses yang sudah ditutup adalah
-- tindakan administratif yang sengaja dipersempit ke satu peran.
--
-- Idempoten dan tidak merusak: setiap tabel/kolom/index memakai IF NOT EXISTS,
-- constraint status di-drop lalu dibuat ulang (pola migrasi 003/054), setiap
-- policy di-drop lebih dulu, dan tidak ada satu pernyataan pun yang menghapus
-- tabel, kolom, atau baris.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. permintaan bergabung ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.school_membership_request (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Guru yang mengajukan. Selalu dari sisi `profiles`: identitasnya satu.
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    -- Sekolah TUJUAN. `school_id` ditulis apa adanya (bukan lewat rantai) supaya
    -- setiap tulis dan setiap baca bisa menyaringnya langsung — satu kolom yang
    -- selalu ada di barisnya tidak bisa lupa disaring.
    target_school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CONSTRAINT school_membership_request_status_check
        CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')),
    -- Siapa yang memutuskan, dan DALAM PERAN APA. Peran disalin terpisah dari
    -- `profiles.role`: yang menyetujui bisa principal, dan peran itulah yang
    -- menjelaskan atas dasar kewenangan apa keputusan itu diambil. Peran
    -- seseorang bisa berubah; audit yang menampilkan peran hari ini untuk
    -- keputusan bulan lalu bukan audit.
    decided_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    decided_role TEXT
        CONSTRAINT school_membership_request_decided_role_check
        CHECK (decided_role IS NULL
               OR decided_role IN ('admin_sekolah', 'principal', 'vice_principal')),
    -- Opsional dan boleh kosong: sekolah tujuan tidak wajib menjelaskan
    -- penolakan. UI menyampaikan "tidak disertakan" kalau kosong, bukan mengarang.
    decision_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    -- Batas waktu permintaan menggantung (default 30 hari, dikonfigurasi). Diisi
    -- saat pengajuan supaya kebijakan yang berubah tidak memindahkan batas
    -- permintaan yang sudah berjalan.
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.school_membership_request IS
    'Permintaan guru untuk bergabung ke NPSN lain. Dimulai GURU, diputuskan sekolah TUJUAN (admin_sekolah/principal/vice_principal — satu cukup). Sengaja TIDAK menyimpan sekolah asal guru: keanggotaannya di NPSN lain adalah informasi milik guru, bukan konsumsi sekolah tujuan.';
COMMENT ON COLUMN public.school_membership_request.expires_at IS
    'Batas waktu permintaan menggantung, ditulis saat pengajuan (bukan dibaca ulang dari konfigurasi) supaya perubahan kebijakan tidak memindahkan batas permintaan yang sudah berjalan.';

-- Satu permintaan MENGGANTUNG per (guru, sekolah tujuan). Index parsial, bukan
-- unique biasa: riwayat tetap utuh — guru yang ditolak hari ini boleh mengajukan
-- lagi nanti, dan barisnya yang lama tetap bisa dibaca.
CREATE UNIQUE INDEX IF NOT EXISTS idx_school_membership_request_pending_once
    ON public.school_membership_request(teacher_id, target_school_id)
    WHERE status = 'pending';

-- Dua pertanyaan yang benar-benar ditanyakan aplikasi: "antrean sekolah ini" dan
-- "riwayat saya". Keduanya index, bukan scan.
CREATE INDEX IF NOT EXISTS idx_school_membership_request_school_queue
    ON public.school_membership_request(target_school_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_school_membership_request_teacher
    ON public.school_membership_request(teacher_id, created_at DESC);
-- Sapuan kedaluwarsa membaca yang masih menggantung dan sudah lewat batas.
CREATE INDEX IF NOT EXISTS idx_school_membership_request_expiry
    ON public.school_membership_request(status, expires_at)
    WHERE status = 'pending';

-- ── 2. bukti persetujuan syarat & ketentuan (berversi) ──────────────────────

CREATE TABLE IF NOT EXISTS public.membership_consent_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL
        REFERENCES public.school_membership_request(id) ON DELETE CASCADE,
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    -- Nomor versi dokumen yang disetujui. WAJIB, bukan default: bukti persetujuan
    -- tanpa versi tidak bisa menjawab "persisnya apa yang dia setujui".
    document_version TEXT NOT NULL,
    -- SHA-256 dari teks dokumen yang benar-benar ditampilkan. Versi menjawab
    -- "dokumen yang mana"; hash menjawab "teksnya yang mana" — dua-duanya
    -- dibutuhkan, sebab dokumen bisa diedit tanpa nomor versinya dinaikkan, dan
    -- itu justru kasus yang paling perlu bisa dibuktikan.
    document_sha256 TEXT,
    agreed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.membership_consent_log IS
    'Bukti eksplisit persetujuan syarat & ketentuan privasi lintas-sekolah, per permintaan, DENGAN nomor versi dan hash teks dokumen. Persetujuan versi lama TIDAK PERNAH dianggap sebagai persetujuan versi baru.';

CREATE INDEX IF NOT EXISTS idx_membership_consent_request
    ON public.membership_consent_log(request_id);
CREATE INDEX IF NOT EXISTS idx_membership_consent_teacher_version
    ON public.membership_consent_log(teacher_id, document_version);

-- Satu permintaan menyetujui tepat satu versi satu kali. Rekap ulang tidak boleh
-- menumpuk baris kedua untuk permintaan yang sama — buktinya harus tunggal.
CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_consent_once
    ON public.membership_consent_log(request_id);

-- ── 3. penutupan keanggotaan (offboarding total) ────────────────────────────

-- Kosakata status bertambah, tanpa kehilangan yang lama.
ALTER TABLE public.teacher_school_membership
    DROP CONSTRAINT IF EXISTS teacher_school_membership_status_check;
ALTER TABLE public.teacher_school_membership
    ADD CONSTRAINT teacher_school_membership_status_check
    CHECK (status IN ('active', 'invited', 'inactive', 'closed'));

ALTER TABLE public.teacher_school_membership
    ADD COLUMN IF NOT EXISTS closed_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL;
ALTER TABLE public.teacher_school_membership
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
ALTER TABLE public.teacher_school_membership
    ADD COLUMN IF NOT EXISTS reopened_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL;
ALTER TABLE public.teacher_school_membership
    ADD COLUMN IF NOT EXISTS reopened_at TIMESTAMPTZ;

COMMENT ON COLUMN public.teacher_school_membership.closed_by IS
    'Siapa yang menutup keanggotaan ini. Hanya admin_sekolah sekolah itu yang boleh menulisnya (ditegakkan di rute + test adversarial), dan penutupan berarti akses TOTAL tertutup ke sekolah itu — bukan read-only.';
COMMENT ON COLUMN public.teacher_school_membership.reopened_by IS
    'Siapa yang membuka kembali keanggotaan ini. HANYA admin_sekolah sekolah itu — principal dan vice_principal sengaja TIDAK berwenang membuka kembali, sekalipun mereka berwenang menyetujui permintaan baru. Asimetri ini keputusan produk yang eksplisit, bukan bug.';

-- "keanggotaan yang ditutup di sekolah ini" — pertanyaan halaman Fase 8-9.
CREATE INDEX IF NOT EXISTS idx_teacher_school_membership_closed
    ON public.teacher_school_membership(school_id, closed_at DESC)
    WHERE status = 'closed';

-- ── 4. RLS: hanya sekolahnya sendiri ────────────────────────────────────────
--
-- Backend memakai service key dan menembus RLS sepenuhnya; policy di sini
-- menjawab pertanyaan kedua — kalau kunci publik yang ikut terkirim di setiap
-- halaman dipakai tanpa sesi, apa yang terbaca? Aturannya: hanya sekolahnya
-- sendiri, `TO authenticated` ditulis eksplisit supaya pemanggilnya terbaca.

ALTER TABLE public.school_membership_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.membership_consent_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "school_membership_request_read_own_school" ON public.school_membership_request;
CREATE POLICY "school_membership_request_read_own_school" ON public.school_membership_request
    FOR SELECT TO authenticated
    USING (
        -- Sekolah tujuan melihat antreannya; guru melihat riwayatnya sendiri.
        (
            (public._is_role('admin_sekolah') OR public._is_role('principal')
             OR public._is_role('vice_principal'))
            AND target_school_id = public._user_school_id()
        )
        OR teacher_id = auth.uid()
    );

DROP POLICY IF EXISTS "membership_consent_log_read_own" ON public.membership_consent_log;
CREATE POLICY "membership_consent_log_read_own" ON public.membership_consent_log
    FOR SELECT TO authenticated
    USING (teacher_id = auth.uid());

-- Sengaja TIDAK ada policy INSERT/UPDATE/DELETE untuk `anon`/`authenticated` di
-- tabel permintaan: keputusan persetujuan/penolakan ditulis server memakai
-- service key SETELAH rutenya memeriksa peran dan sekolah tujuan. Kalau keputusan
-- boleh ditulis lewat kunci publik, satu klien yang mengarang POST bisa menyetujui
-- dirinya sendiri — dan itu justru inti dari fitur ini.

-- Catatan privasi yang ditegakkan (bukan sekadar didokumentasikan): tidak ada
-- policy di sini yang memberi sekolah tujuan jalan membaca `teacher_school_membership`
-- guru itu. Kolom sekolah asal memang tidak ada di tabel ini, jadi sekolah tujuan
-- tidak bisa mengetahui di NPSN mana saja guru itu terdaftar — bahkan lewat
-- kunci publik yang bocor.
