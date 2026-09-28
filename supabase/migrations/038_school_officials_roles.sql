-- ──────────────────────────────────────────────────────────────
-- Migration 038: dua peran pengawas sekolah — principal & vice_principal
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Hierarki hari ini berhenti di `admin_sekolah`: satu akun memegang akun, data
-- dan operasional teknis sekolah. Tidak ada peran untuk kepala sekolah dan wakil
-- kepala sekolah, yang tugasnya justru *membaca* sekolahnya — bukan mengelolanya.
-- Menaruh mereka di `admin_sekolah` berarti memberi wewenang tulis kepada pembaca
-- laporan, dan itu bukan keputusan yang pantas diambil demi kepraktisan.
--
-- Migrasi ini hanya membuka *nama* perannya. Yang dikunci di sini adalah
-- satu-satunya hal yang tidak bisa diubah aplikasi: `profiles_role_check`, yang
-- sejak migrasi 007 berbunyi
--
--     CHECK (role IN ('super_admin', 'admin_sekolah', 'guru', 'murid'))
--
-- dan akan menolak setiap baris `principal` dengan 23514 (check_violation) —
-- sehingga akunnya tercipta di `auth.users`, gagal di `profiles`, dan menjadi
-- akun setengah jadi yang bisa login tetapi tidak punya peran.
--
-- Sengaja TIDAK ada di sini, dan itu disadari: policy RLS berlingkup sekolah
-- untuk kedua peran baru. Backend hari ini memakai service-role key, jadi RLS
-- adalah lapisan pertahanan kedua, bukan yang menegakkan akses (lihat
-- docs/SECURITY_RLS_MATRIX.md). Yang menegakkan akses untuk kedua peran ini
-- adalah dekorator route (`role_required`) plus `require_school_access`, dengan
-- test RBAC lintas sekolah. Policy-nya menyusul sebagai migrasi tersendiri agar
-- satu migrasi tidak sekaligus mengubah constraint dan sekumpulan policy.
--
-- Idempoten dan tidak merusak: `DROP CONSTRAINT IF EXISTS` diikuti `ADD
-- CONSTRAINT`, tanpa satu pun pernyataan yang menghapus tabel, kolom, atau baris.
-- Dijalankan dua kali hasilnya sama, dan daftar di bawah adalah daftar lama
-- ditambah dua nama — tidak ada peran yang dicabut, jadi akun yang sudah ada
-- tetap sah.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback), dan kontrol transaksi
-- di dalam berkas membuat dry run menolak berkas ini karena rollback-nya tidak
-- lagi bisa membatalkan apa pun. Semua migrasi lain di folder ini juga begitu.

ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_role_check;

-- Daftar lengkapnya, bukan tambahan: sebuah CHECK tidak bisa diperluas sebagian,
-- dan menuliskan hanya nama baru akan mencabut empat peran yang sudah ada.
ALTER TABLE profiles ADD CONSTRAINT profiles_role_check
    CHECK (role IN ('super_admin', 'admin_sekolah',
                    'principal', 'vice_principal',
                    'guru', 'murid'));

COMMENT ON CONSTRAINT profiles_role_check ON profiles IS
    'Peran yang sah. principal/vice_principal (migrasi 038) adalah peran baca-saja berlingkup sekolah: laporan dan pengawasan, tanpa wewenang mengubah data.';
