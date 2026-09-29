-- ──────────────────────────────────────────────────────────────
-- Migration 040: aktivasi login pertama, dan email akun
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Dua fakta yang dibutuhkan sekolah dan tidak bisa didapat dari mana pun:
--
-- **1. Password yang diterbitkan sekolah adalah password sekali pakai, tetapi
-- tidak ada satu pun kolom yang mengatakannya.** Setiap akun guru, murid, kepala
-- sekolah, dan wakil kepala sekolah dibuat dengan `_gen_password()`; password itu
-- hanya bisa keluar lewat berkas kartu login (`app/services/login_cards.py`), dan
-- tidak ada halaman yang bisa membacanya kembali — jadi seluruh sistem sudah
-- memperlakukan password itu sebagai sekali pakai tanpa pernah menegakkannya. Yang
-- hilang cuma akibatnya: murid yang memakai password dari kartu itu selamanya, dan
-- sekolah tidak punya cara menutup celah itu. `must_change_password` adalah saklar
-- yang membuat rilis pertama menuntut password sendiri sebelum halaman lain terbuka.
--
-- **2. Email akun tidak ada di `profiles`.** Emailnya hidup di Supabase Auth, dan
-- membacanya dari sana berarti `auth.admin.list_users()` — satu panggilan yang
-- **dipaginasi 50 per halaman**. Sekolah berisi 723 murid, jadi satu lembar kartu
-- login atau satu daftar email berarti belasan panggilan; persis bentuk yang pernah
-- membuat halaman manajemen user super admin hanya menampilkan 50 dari 806 akun.
-- `profiles.email` adalah salinan turunan, bukan sumber kebenaran: login tetap
-- lewat Auth, dan kolom ini hanya menjawab "email apa yang dipasang pada akun ini"
-- dalam satu query berlingkup sekolah. Setiap penulisan email menulis keduanya.
--
-- Keputusan yang diambil sadar dan bisa dibaca dari angkanya: **baris yang sudah
-- ada dibiarkan `FALSE`.** Memaksa 806 akun yang sudah berjalan mengganti password
-- pada login berikutnya adalah keputusan sekolah, bukan keputusan migrasi — dan
-- yang paling parah, satu kelas yang sedang ujian akan tertahan di halaman ganti
-- password. Yang menyalakan saklar ini adalah jalur yang *menerbitkan* password:
-- pembuatan akun baru dan penerbitan kartu login (sejak rilis ini), plus tombol
-- reset per akun.
--
-- Idempoten dan tidak merusak: setiap kolom ditambahkan dengan `IF NOT EXISTS`,
-- tidak ada satu pernyataan pun yang menghapus kolom, tabel, atau baris. Dijalankan
-- dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).
--
-- Tidak ada policy baru di sini. RLS berlaku per baris, bukan per kolom, jadi
-- `profiles` sudah dijaga `profiles_select_admin_own_school` dan
-- `profiles_select_school_official` (migrasi 001 dan 039); kolom baru mewarisi
-- keduanya. Yang penting justru sebaliknya: kolom ini **tidak** boleh memperluas
-- apa pun yang bisa dibaca tanpa sesi, dan karena ia hidup di tabel yang sudah
-- punya policy berlingkup sekolah, tidak ada yang berubah bagi `anon`.

-- ── 1. password yang harus diganti sebelum halaman lain terbuka ─────────────
ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;

-- Kapan terakhir password diganti oleh pemiliknya sendiri. Diisi hanya oleh
-- /auth/change-password, dan `NULL` berarti "belum pernah" — bukan "tidak tahu".
ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

-- ── 2. email akun, sebagai salinan turunan ──────────────────────────────────
ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS email TEXT;

-- ── 3. indeks untuk pertanyaan yang menumpuk tanpa ini ──────────────────────
-- Halaman admin sekolah menghitung "berapa akun belum aktivasi" untuk satu
-- sekolah. Indeks parsialnya kecil — barisnya hanya yang masih menunggu — dan
-- ia menghilang sendiri saat sekolahnya selesai mengganti password.
CREATE INDEX IF NOT EXISTS idx_profiles_must_change
    ON profiles (school_id)
    WHERE must_change_password;
