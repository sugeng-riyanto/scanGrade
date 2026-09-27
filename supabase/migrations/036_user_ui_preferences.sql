-- Migration 036: the choices that should follow a student to the next device
--
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Tema, bahasa dan volume peringatan disimpan di `localStorage`, yang sifatnya
-- per-peramban: murid yang sama di laptop perpustakaan, lalu komputer lab, lalu
-- ponselnya sendiri bertemu pengaturan bawaan lagi setiap kali. Kolom ini
-- menyimpannya di profil — satu-satunya catatan yang dibagi semua perangkat —
-- sehingga pilihan yang dibuat saat satu ujian tidak hilang di ujian berikutnya.
--
-- Satu objek JSON, bukan satu kolom per pilihan, supaya preferensi berikutnya
-- tidak butuh migrasi lagi:
--
--     {"theme": "dark", "lang": "en", "alert_volume": 0.5, "alert_muted": false}
--
-- Nilainya divalidasi di sisi aplikasi (`app/services/user_preferences.py`),
-- bukan di sini: sebuah CHECK pada JSON akan menolak baris yang ditulis versi
-- kode yang lebih tua, dan preferensi yang tidak dikenal lebih baik diabaikan
-- daripada menggagalkan render halaman.
--
-- Idempoten dan tidak merusak: satu `ADD COLUMN IF NOT EXISTS`, tanpa satu pun
-- pernyataan yang menghapus tabel, kolom, atau baris. Kode benar dengan atau
-- tanpa kolom ini — sesi membaca `preferences` bila ada, dan jatuh kembali ke
-- kolom lama bila belum (lihat `_fetch_session`), jadi migrasi ini aman
-- diterapkan sebelum atau sesudah rilis.

ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS preferences JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN profiles.preferences IS
    'UI preferences that follow the user across devices: theme, lang, alert_volume, alert_muted. Written only through app/services/user_preferences.py.';
