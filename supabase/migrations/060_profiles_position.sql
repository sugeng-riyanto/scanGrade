-- ──────────────────────────────────────────────────────────────
-- Migration 060: jabatan (position) pada profil
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Satu kolom, satu arti: label jabatan yang *diketik sekolah* tentang orangnya —
-- "Kepala Sekolah", "Admin Sekolah", "Tata Usaha". Sebelum ini, jabatan hanya
-- hidup di `school_registration_requests.requester_position` pada saat pendaftaran:
-- begitu sekolah disetujui, jabatan itu tidak pernah ikut ke mana-mana, dan
-- halaman super admin tidak punya tempat untuk menampilkan atau mengubahnya.
--
-- Kenapa di `profiles` dan bukan di `schools`
-- ───────────────────────────────────────────
-- Jabatan adalah milik *orang*, bukan milik sekolah. Satu sekolah bisa punya
-- beberapa akun (kepala sekolah, wakil, admin), masing-masing dengan jabatannya
-- sendiri; kolom di `schools` hanya bisa menyimpan satu. `profiles` sudah menjadi
-- tempat identitas per-akun (nama, telepon, NISN, NUPTK), jadi ini satu kolom lagi
-- di baris yang sama.
--
-- Kenapa TEXT polos, bukan enum
-- ─────────────────────────────
-- Jabatan adalah label bebas: sekolah menamainya sendiri lewat halaman
-- pendaftaran (lihat `app/routes/auth.py`), dan daftar jabatan berubah antar
-- sekolah. Sebuah CHECK constraint akan menolak label yang sah hanya karena
-- belum terdaftar.
--
-- Idempoten dan tidak merusak: satu `ADD COLUMN IF NOT EXISTS`, tanpa DROP, tanpa
-- backfill yang menimpa data yang ada. Profil lama tetap punya jabatan NULL, yang
-- dibaca halaman sebagai "belum diisi".
--
-- Sengaja TANPA BEGIN;/COMMIT; — `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS position TEXT;

COMMENT ON COLUMN public.profiles.position IS
    'Jabatan yang diketik sekolah tentang pemilik akun (mis. "Admin Sekolah"). Label bebas, bukan enum.';
