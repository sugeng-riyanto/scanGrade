-- ──────────────────────────────────────────────────────────────
-- Migration 062: Safe Exam Browser (SEB) + sinyal lingkungan (lemah)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor. Dry run wajib: berkas ini mengubah skema.
--
-- Apa yang ditambahkan di sini, dan apa yang SENGAJA tidak
-- ────────────────────────────────────────────────────────
-- Tiga tabel dan satu kolom:
--
--   exams.require_seb        sakelar per ujian: kertas ini hanya boleh dibuka
--                            dari SEB. DEFAULT false — tidak ada ujian yang
--                            sudah berjalan berubah perilakunya hanya karena
--                            migrasi ini dijalankan.
--   environment_signal       sinyal *lemah* (indikasi VM/remote desktop) per
--                            attempt. Ini BUKAN bukti pelanggaran: tidak ada
--                            satu pun kolom di sini yang dibaca oleh tangga
--                            penalti, penguncian, atau skor. Lihat catatan
--                            "probabilistik" di bawah.
--   exam_seb_credential      satu baris per ujian: HASH password keluar/admin
--                            (yang masuk ke berkas .seb) DAN salinan plain text
--                            yang terenkripsi (untuk dibaca staf berwenang).
--   seb_access_log           siapa membuka password / mengunduh berkas .seb,
--                            kapan, dan atas dasar peran apa.
--
-- TIDAK ADA tabel kode kedua untuk murid, dan itu disengaja: murid tidak pernah
-- menerima password apa pun dalam bentuk apa pun. Berkas .seb yang ia unduh
-- HANYA memuat hash (`hashedQuitPassword`), bukan plain text — jadi mengunduh
-- berkas itu tidak memberinya kunci keluar, persis seperti yang dijanjikan UI.
--
-- KENAPA HASH **DAN** TERENKRIPSI (dua kolom untuk satu password)
-- ──────────────────────────────────────────────────────────────
-- Dua-duanya dibutuhkan, dan keduanya berbeda tujuan:
--
--   * `*_password_hash` adalah SHA-256 Base16 — bentuk yang dibaca SEB dari
--     berkas konfigurasi (`hashedQuitPassword`, `hashedAdminPassword`). Ini yang
--     membuat tombol keluar terkunci di sisi klien, dan TIDAK BISA dibalik.
--   * `*_password_enc` adalah salinan yang bisa dibalik (AEAD), supaya guru
--     pemilik ujian / admin sekolah / pengawas yang sedang bertugas bisa
--     MEMBACAKAN password itu kepada murid yang butuh keluar lebih awal. Tanpa
--     kolom ini, password yang di-generate otomatis akan hilang selamanya dan
--     satu-satunya jalan keluar adalah mengedit ulang berkas .seb.
--
-- Karena bisa dibalik, kolom terenkripsi itu diperlakukan sebagai rahasia
-- setingkat kunci: ia TIDAK PERNAH ditulis ke berkas .seb yang diunduh siapa
-- pun, dan hanya dibaca lewat satu fungsi dekripsi di server (Fase 7). Kuncinya
-- diturunkan dari kredensial aplikasi (lihat app/services/seb_service.py), bukan
-- dari berkas .env terpisah yang harus diurus sekolah.
--
-- KENAPA `environment_signal` TIDAK BOLEH PUNITIF — INI YANG PALING PENTING
-- ──────────────────────────────────────────────────────────────────────────
-- Ciri-ciri lingkungan virtual (string renderer WebGL, presisi timer, rasio
-- piksel) adalah sinyal PROBABILISTIK. Sekolah ini melayani murid dengan
-- perangkat murah dan lama yang punya ciri serupa, dan laboratorium komputer
-- yang sah pun sering berjalan di atas VM. Menjadikannya penalti otomatis akan
-- menghukum murid karena perangkatnya, bukan karena perbuatannya.
--
-- Karena itu:
--   * tidak ada trigger, view, atau kolom di sini yang memanggil tangga
--     penalti (`penalty_per_violation`), penguncian (`lock_pending_resume`),
--     atau pengurangan skor apa pun;
--   * satu-satunya pembaca yang dirancang adalah dashboard tinjauan manual
--     (Fase 8), yang selalu menampilkan penjelasan wajar alternatif;
--   * ambang skor kompositnya tinggal di server (Fase 3) dan TIDAK dipublikasi
--     ke halaman publik mana pun.
-- Satu-satunya mekanisme keras di seluruh fitur ini adalah SEB, dan itu karena
-- validasinya kriptografis (kunci konfigurasi), bukan karena tebakan.
--
-- Idempoten dan tidak merusak: setiap tabel/kolom/index memakai IF NOT EXISTS,
-- setiap policy di-drop lebih dulu (DROP POLICY IF EXISTS), dan tidak ada satu
-- pernyataan pun yang menghapus tabel, kolom, atau baris. Dijalankan dua kali
-- hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback), sama seperti semua
-- migrasi lain di folder ini.

-- ── 1. sakelar per ujian ────────────────────────────────────────────────────

ALTER TABLE exams ADD COLUMN IF NOT EXISTS require_seb BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN exams.require_seb IS
    'Bila true, kertas ini hanya boleh dibuka dari Safe Exam Browser. DEFAULT false, dan itu bukan kehati-hatian berlebihan: SEB TIDAK ADA untuk Android dan ChromeOS, jadi menyalakan ini pada ujian yang dikerjakan dari HP akan memblokir total mayoritas murid. Hanya untuk ujian di laptop/lab/iPad.';

-- ── 2. sinyal lingkungan (probabilistik, non-punitif) ───────────────────────

CREATE TABLE IF NOT EXISTS public.environment_signal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Ditulis di setiap tabel yang tulisannya disaring: satu kolom yang selalu
    -- ada di barisnya tidak bisa lupa disaring (lihat catatan yang sama di
    -- migrasi 041).
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    -- Attempt *adalah* baris `submissions` (satu baris per murid × ujian),
    -- jadi tidak ada tabel attempt kedua yang harus dijaga tetap sinkron.
    submission_id UUID REFERENCES public.submissions(id) ON DELETE CASCADE,
    student_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    -- Jenis sinyal, mis. 'webgl_renderer', 'timer_precision', 'pixel_ratio',
    -- 'hardware_vs_performance'. Teks bebas bernama-pasti, bukan enum: sinyal
    -- baru harus bisa dikumpulkan klien lebih dulu, lalu dinilai di server.
    signal_type TEXT NOT NULL,
    -- Nilai mentah apa adanya dari klien. JSONB supaya satu sinyal boleh
    -- membawa beberapa angka tanpa menambah kolom per penemuan baru.
    raw_value JSONB,
    -- Sumbangan sinyal ini ke skor komposit. Bagian dari skor itu sendiri:
    -- ambangnya tinggal di server dan tidak dipublikasi.
    weight NUMERIC(6, 3),
    -- Versi pengumpul, supaya perubahan bobot di masa depan bisa dibedakan
    -- dari data lama saat meninjau.
    collector_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.environment_signal IS
    'Sinyal LEMAH indikasi lingkungan virtual (VM/remote desktop) per attempt. Probabilistik dan NON-PUNITIF: tidak ada pembaca di sini yang memicu penalti, penguncian, atau pengurangan skor. Hanya konteks tinjauan manual.';

CREATE INDEX IF NOT EXISTS idx_environment_signal_submission
    ON public.environment_signal(submission_id);
CREATE INDEX IF NOT EXISTS idx_environment_signal_exam_type
    ON public.environment_signal(exam_id, signal_type);
CREATE INDEX IF NOT EXISTS idx_environment_signal_school_created
    ON public.environment_signal(school_id, created_at);

-- Satu baris per (attempt, jenis sinyal): attempt yang dikirim ulang (mis. halaman
-- dimuat lagi) memperbarui barisnya, bukan menumpuk riwayat palsu yang membuat
-- sebuah perangkat tampak lebih mencurigakan hanya karena di-refresh.
CREATE UNIQUE INDEX IF NOT EXISTS idx_environment_signal_once
    ON public.environment_signal(submission_id, signal_type);

-- ── 3. kredensial SEB per ujian ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.exam_seb_credential (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    -- SHA-256 Base16 dari password keluar. Inilah bentuk yang dibaca SEB dari
    -- berkas .seb (`hashedQuitPassword`), dan tidak bisa dibalik.
    quit_password_hash TEXT NOT NULL,
    -- Salinan terenkripsi (AEAD) untuk dibaca staf berwenang. TERPISAH dari
    -- berkas .seb: berkas yang diunduh siapa pun tidak pernah memuat kolom ini.
    quit_password_enc TEXT NOT NULL,
    admin_password_hash TEXT NOT NULL,
    admin_password_enc TEXT NOT NULL,
    -- Config Key = SHA-256 Base16 dari JSON kanonik pengaturan SEB yang server
    -- hasilkan sendiri (lihat app/services/seb_service.py). SEB mengirim
    -- `X-SafeExamBrowser-ConfigKeyHash` = SHA256(URL absolut + Config Key);
    -- itulah "kunci masuk" yang bisa diverifikasi server tanpa menebak.
    config_key TEXT,
    -- Browser Exam Key: dibiarkan NULL dan TIDAK PERNAH di-generate server.
    -- BEK memuat tanda tangan kode aplikasi SEB, jadi hanya klien SEB yang bisa
    -- membuatnya (dinyatakan resmi oleh proyek SEB). Kolom ini menerima nilai
    -- yang di-*daftarkan* sekolah dari SEB Config Tool, dan server hanya
    -- membandingkan. Kunci yang bisa dibuat server tidak membuktikan apa pun.
    browser_exam_key TEXT,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.exam_seb_credential IS
    'Satu baris per ujian: hash password keluar/admin (masuk ke berkas .seb) DAN salinan plain text terenkripsi (untuk staf berwenang). Plain text TIDAK PERNAH ikut ke berkas .seb yang diunduh siapa pun.';
COMMENT ON COLUMN public.exam_seb_credential.browser_exam_key IS
    'BEK yang didaftarkan sekolah dari SEB Config Tool. SERVER TIDAK PERNAH MEMBUATNYA: BEK memuat tanda tangan kode aplikasi SEB, jadi hanya klien SEB yang bisa menghasilkannya — kunci yang bisa dibuat server tidak membuktikan kliennya SEB asli.';

-- Satu ujian, satu baris: menerbitkan ulang password memperbarui barisnya.
CREATE UNIQUE INDEX IF NOT EXISTS idx_exam_seb_credential_exam
    ON public.exam_seb_credential(exam_id);
CREATE INDEX IF NOT EXISTS idx_exam_seb_credential_school
    ON public.exam_seb_credential(school_id);

-- ── 4. audit akses password & unduhan berkas .seb ───────────────────────────

CREATE TABLE IF NOT EXISTS public.seb_access_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    exam_id UUID REFERENCES public.exams(id) ON DELETE CASCADE,
    user_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    -- Peran pada saat akses, DISALIN: peran seseorang bisa berubah, dan audit
    -- yang menampilkan peran hari ini untuk aksi bulan lalu bukan audit.
    role_at_access TEXT NOT NULL,
    access_type TEXT NOT NULL
        CONSTRAINT seb_access_log_access_type_check
        CHECK (access_type IN ('view_password', 'download_file')),
    -- Diisi bila kewenangannya BUKAN milik sendiri: admin sekolah yang membuka
    -- password ujian guru lain tercatat "atas nama guru X", sesuai matriks Fase 7.
    on_behalf_of UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.seb_access_log IS
    'Audit terpusat: siapa membuka password keluar/admin SEB dan siapa mengunduh berkas .seb, kapan, dan atas dasar peran apa. Setiap tampilan password dan setiap unduhan berkas wajib menulis satu baris di sini.';

CREATE INDEX IF NOT EXISTS idx_seb_access_log_exam_created
    ON public.seb_access_log(exam_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_seb_access_log_school_created
    ON public.seb_access_log(school_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_seb_access_log_user
    ON public.seb_access_log(user_id, created_at DESC);

-- ── 5. RLS: hanya sekolahnya sendiri ────────────────────────────────────────
--
-- Backend memakai service key dan menembus RLS sepenuhnya; policy di sini
-- menjawab pertanyaan kedua — kalau kunci publik yang ikut terkirim di setiap
-- halaman dipakai tanpa sesi, apa yang terbaca? Aturannya: hanya sekolahnya
-- sendiri, dan `TO authenticated` ditulis eksplisit supaya pemanggilnya terbaca,
-- bukan disimpulkan (policy tanpa klausa `TO` berlaku untuk PUBLIC, termasuk
-- anon). Yang menjawab "siapa staf berwenang" adalah app/services/seb_access.py,
-- bukan policy ini.

ALTER TABLE public.environment_signal ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exam_seb_credential ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.seb_access_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "environment_signal_read_own_school" ON public.environment_signal;
CREATE POLICY "environment_signal_read_own_school" ON public.environment_signal
    FOR SELECT TO authenticated
    USING (
        (public._is_role('guru') OR public._is_role('admin_sekolah')
         OR public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

-- Tidak ada policy INSERT/UPDATE/DELETE untuk `anon`/`authenticated` di sini:
-- sinyal ditulis server saat attempt dimulai, memakai service key. Murid tidak
-- pernah menulis tabel ini langsung, sehingga klien tidak bisa mengarang sinyal
-- yang memberatkan dirinya sendiri maupun membersihkan jejaknya.

DROP POLICY IF EXISTS "exam_seb_credential_read_own_school" ON public.exam_seb_credential;
CREATE POLICY "exam_seb_credential_read_own_school" ON public.exam_seb_credential
    FOR SELECT TO authenticated
    USING (
        (public._is_role('guru') OR public._is_role('admin_sekolah')
         OR public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

-- Catatan penting soal policy di atas: yang terbaca lewat jalur ini adalah
-- HASH dan CIPHERTEXT. Ciphertext berguna hanya bagi pemegang kunci aplikasi,
-- dan kunci itu tidak pernah dikirim ke browser. Token kunci publik yang dipakai
-- tanpa sesi tidak mendapat apa-apa: predicatenya menuntut sesi DAN sekolah yang
-- sama.

DROP POLICY IF EXISTS "seb_access_log_read_own_school" ON public.seb_access_log;
CREATE POLICY "seb_access_log_read_own_school" ON public.seb_access_log
    FOR SELECT TO authenticated
    USING (
        (public._is_role('admin_sekolah') OR public._is_role('principal')
         OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

-- Guru pemilik ujian tidak diberi policy baca atas log ini lewat kunci publik:
-- log menyebut peran dan alamat IP pembaca lain. Yang boleh membacanya lewat
-- aplikasi adalah kepala sekolah dan admin sekolah (lihat app/services/seb_access.py),
-- dan setiap pembacaan itu sendiri juga tercatat.
