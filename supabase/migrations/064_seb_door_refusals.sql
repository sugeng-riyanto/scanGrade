-- ──────────────────────────────────────────────────────────────
-- Migration 064: catatan penolakan di pintu Safe Exam Browser
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor. Dry run wajib: berkas ini mengubah skema.
--
-- Apa yang ditambahkan, dan apa yang SENGAJA tidak
-- ────────────────────────────────────────────────
-- Satu tabel:
--
--   seb_door_refusal   satu baris per penolakan di pintu SEB: ujian mana, murid
--                      mana, apa yang membuat pintu menolaknya, dan kapan.
--
-- YANG PALING PENTING: ini BUKAN pelanggaran dan BUKAN penalti.
-- ────────────────────────────────────────────────────────────
-- Murid yang ditolak masuk belum mengerjakan apa pun. Yang paling sering terjadi
-- justru bukan kesalahannya sama sekali: ia membuka tautan ujian dari Chrome di
-- HP-nya alih-alih dari berkas .seb, atau berkas .seb yang ia pegang sudah lama
-- (guru mengubah pengaturan ujian setelah berkas itu dibagikan). Menghukumnya
-- karena itu berarti menghukum murid karena perangkatnya dan karena berkas yang
-- dibagikan gurunya — persis kesalahan yang sudah dijelaskan panjang di migrasi
-- 062 untuk `environment_signal`.
--
-- Karena itu, sama seperti `environment_signal`:
--   * tidak ada trigger, view, atau kolom di sini yang memanggil tangga penalti
--     (`penalty_per_violation`), penguncian (`lock_pending_resume`), atau
--     pengurangan skor apa pun;
--   * tidak ada satu pun baris di sini yang ditulis ke `violation_logs`;
--   * nama alasan di sini TIDAK ADA yang menjadi anggota
--     `app/services/anti_cheat_service.PENALIZED_VIOLATION_TYPES` — dan itu
--     ditegakkan uji, bukan hanya dijanjikan komentar ini;
--   * satu-satunya pembaca yang dirancang adalah panel SEB milik guru pengampu
--     dan lencana di halaman hasil ujian, keduanya lewat
--     `app/services/seb_door_log.py`.
--
-- ANGKA YANG DIMINTA, DAN MENGAPA DUA ANGKA
-- ─────────────────────────────────────────
-- Pertanyaan guru adalah "berapa murid yang tidak bisa masuk, dan kapan". Satu
-- murid yang mencoba lima kali adalah SATU murid dan LIMA percobaan, dan kolom
-- `student_id` ada di sini supaya keduanya bisa dijawab dari satu pembacaan:
-- jumlah murid yang berbeda, dan jumlah percobaan. Menyimpan hanya jumlah total
-- akan membuat "berapa murid" mustahil dijawab; menyimpan hanya murid pertama
-- akan menyembunyikan kelas yang mencoba berulang kali karena satu berkas basi.
--
-- MENGAPA ALASANNYA TEKS BEBAS, BUKAN CHECK CONSTRAINT
-- ────────────────────────────────────────────────────
-- Alasan baru harus bisa dikumpulkan klien SEBELUM bisa dinilai — pilihan yang
-- sama yang dibuat `environment_signal.signal_type` di migrasi 062. Yang memegang
-- daftar tertutup adalah `seb_door_log.REASON_LABELS`: satu tabel (Indonesia,
-- Inggris) yang membuat alasan tanpa kalimat menjadi kesalahan uji, bukan kalimat
-- kosong di layar guru.
--
-- CATATAN PRIVASI
-- ───────────────
-- Tabel ini SENGAJA tidak menyimpan alamat IP. `seb_access_log` (062) menyimpannya
-- karena yang dicatatnya adalah staf yang membuka rahasia ujian; yang dicatat di
-- sini adalah murid yang gagal masuk, dan untuk itu guru hanya butuh apa dan
-- kapan. Alamat rumah jaringan seorang anak bukan hal yang perlu dikumpulkan demi
-- satu lencana.
--
-- Idempoten dan tidak merusak: setiap tabel/index memakai IF NOT EXISTS, setiap
-- policy di-drop lebih dulu (DROP POLICY IF EXISTS), dan tidak ada satu pernyataan
-- pun yang menghapus tabel, kolom, atau baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback), sama seperti semua
-- migrasi lain di folder ini.

-- ── 1. satu baris per penolakan ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.seb_door_refusal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Ditulis di setiap tabel yang tulisannya disaring: satu kolom yang selalu ada
    -- di barisnya tidak bisa lupa disaring (lihat catatan yang sama di 041/062).
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    -- ON DELETE SET NULL, bukan CASCADE: baris murid yang sudah lulus tetap harus
    -- terbaca sebagai riwayat penolakan ujian itu, walau profilnya kemudian hilang.
    --
    -- Kunci asing ini juga yang membuat PostgREST bisa menyematkan
    -- `profiles!left(full_name)` di sisi baca. Menghapusnya membuat pembaca panel
    -- kehilangan nama murid dengan pesan "could not find a relationship", jadi
    -- keberadaannya diuji, bukan hanya diandalkan.
    student_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    -- Apa yang membuat pintu menolak. Nilai hari ini: 'no_client' (dibuka dari
    -- browser biasa), 'no_key' (SEB terbuka tetapi kunci konfigurasinya belum
    -- siap), 'config_key_mismatch' (kunci tidak cocok — biasanya berkas .seb lama).
    -- Teks bebas, lihat catatan di atas.
    reason TEXT NOT NULL,
    -- Perangkat yang dipakai, dipotong 500 karakter seperti `seb_access_log`.
    -- Inilah yang membedakan "Chrome di Android" dari "Safari di iPad" tanpa
    -- menebak dari alasan itu sendiri.
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.seb_door_refusal IS
    'Satu baris per penolakan di pintu SEB: ujian, murid, alasan, kapan. NON-PUNITIF: tidak ada pembaca di sini yang memicu penalti, penguncian, atau pengurangan skor — murid yang ditolak justru paling sering hanya salah membuka tautan. Dibaca app/services/seb_door_log.py.';

COMMENT ON COLUMN public.seb_door_refusal.reason IS
    'Apa yang membuat pintu menolak: no_client / no_key / config_key_mismatch hari ini. Teks bebas (tanpa CHECK) karena alasan baru harus bisa dikumpulkan klien lebih dulu; daftar tertutupnya ada di seb_door_log.REASON_LABELS.';

COMMENT ON COLUMN public.seb_door_refusal.student_id IS
    'Murid yang ditolak. NULL bila profilnya sudah dihapus — barisnya tetap terbaca sebagai riwayat penolakan ujian itu. Kunci asing ini juga yang membuat PostgREST dapat menyematkan nama profil di sisi baca.';

-- Untuk panel: "berapa murid ditolak, dan kapan terakhir" satu ujian, terbaru dulu.
CREATE INDEX IF NOT EXISTS idx_seb_door_refusal_exam_created
    ON public.seb_door_refusal(exam_id, created_at DESC);
-- Untuk pertanyaan tingkat sekolah ("kelas mana yang paling banyak tertolak"),
-- yang belum punya halaman tetapi sudah punya indeksnya.
CREATE INDEX IF NOT EXISTS idx_seb_door_refusal_school_created
    ON public.seb_door_refusal(school_id, created_at DESC);
-- Untuk "berapa kali murid ini mencoba", yang adalah setengah dari lencana di
-- halaman hasil (murid vs percobaan).
CREATE INDEX IF NOT EXISTS idx_seb_door_refusal_exam_student
    ON public.seb_door_refusal(exam_id, student_id);

-- ── 2. RLS: hanya sekolahnya sendiri ────────────────────────────────────────
--
-- Backend memakai service key dan menembus RLS sepenuhnya; policy di sini menjawab
-- pertanyaan kedua — kalau kunci publik yang ikut terkirim di setiap halaman
-- dipakai tanpa sesi, apa yang terbaca? Aturannya: hanya sekolahnya sendiri, dan
-- `TO authenticated` ditulis eksplisit supaya pemanggilnya terbaca, bukan
-- disimpulkan (policy tanpa klausa `TO` berlaku untuk PUBLIC, termasuk `anon`).
--
-- Guru DIIKUTKAN di sini, berbeda dari `seb_access_log` di 062. Perbedaannya
-- disengaja: log itu memuat alamat IP dan peran pembaca LAIN, jadi guru tidak
-- diberi akses; tabel ini memuat murid-murid yang tidak bisa masuk ujian GURU ITU,
-- dan guru yang tidak boleh membaca "tiga murid saya tidak bisa masuk" adalah guru
-- yang tidak bisa menolong mereka sebelum ujian dimulai.

ALTER TABLE public.seb_door_refusal ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "seb_door_refusal_read_own_school" ON public.seb_door_refusal;
CREATE POLICY "seb_door_refusal_read_own_school" ON public.seb_door_refusal
    FOR SELECT TO authenticated
    USING (
        (public._is_role('guru') OR public._is_role('admin_sekolah')
         OR public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

-- Tidak ada policy INSERT/UPDATE/DELETE untuk `anon`/`authenticated` di sini:
-- penolakan ditulis server, dari rute yang sudah memeriksa bahwa pemanggilnya
-- memang murid di ujian itu. Murid tidak pernah menulis tabel ini langsung,
-- sehingga klien tidak bisa mengarang penolakan yang memberatkan dirinya maupun
-- membersihkan jejak penolakannya.
