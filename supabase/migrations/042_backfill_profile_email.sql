-- ──────────────────────────────────────────────────────────────
-- Migration 042: mengisi cermin email untuk akun yang sudah ada
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- **Yang diukur, bukan yang diasumsikan.** Migrasi 040 menambahkan `profiles.email`
-- sebagai cermin turunan dari alamat di Supabase Auth, dan setiap penulisan email
-- sejak itu menulis keduanya. Tetapi cermin tidak bisa mengisi dirinya sendiri untuk
-- akun yang sudah ada: diperiksa terhadap project hidup, **0 dari 811 baris** punya
-- `email` terisi — murid, guru, admin sekolah, kepala sekolah, wakil kepala sekolah,
-- dan super admin, semuanya kosong. Jadi kolom yang ada supaya sebuah sekolah bisa
-- membangun lembar emailnya dalam satu query itu, hari ini, tidak menjawab apa pun.
--
-- Fungsi ini hanya mengisi yang **kosong**. Baris yang sudah punya nilai tidak
-- disentuh, sehingga menjalankannya dua kali hasilnya sama (pernyataan pertama
-- menjadi no-op) dan sebuah alamat yang sudah benar tidak bisa ditimpa oleh versi
-- yang lebih tua. Alamatnya diambil dari `auth.users` — tabel yang sama di database
-- yang sama — lalu di-lowercase agar cocok dengan cara `account_emails.py` dan
-- `_get_email_map` membandingkannya.
--
-- Alamat kosong di Auth dibiarkan kosong di cermin: menyalinnya sebagai string kosong
-- akan membuat kolomnya tampak terisi padahal tidak ada yang bisa dipakai login.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).
--
-- Tidak ada kolom, tabel, index, atau policy baru di sini, dan tidak ada satu pun
-- pernyataan yang menghapus apa pun — hanya `UPDATE` ke nilai yang belum ada.
-- Idempoten dan tidak merusak.

UPDATE public.profiles AS p
   SET email = lower(u.email)
  FROM auth.users AS u
 WHERE u.id = p.id
   AND p.email IS NULL
   AND u.email IS NOT NULL
   AND btrim(u.email) <> '';
