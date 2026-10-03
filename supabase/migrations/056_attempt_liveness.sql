-- ──────────────────────────────────────────────────────────────
-- Migration 056: liveness of one sitting (the heartbeat's own columns)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Masalah yang dijawab
-- ────────────────────
-- Tidak ada satu pun tanda di server tentang apakah seorang murid masih di
-- kertasnya. Yang tersimpan hanya violation_logs (ketidakhadiran yang DIHITUNG)
-- dan attempt_session_events (satu baris per kejadian). Menulis satu baris event
-- tiap ping 25 detik per murid akan membuat tabel event tumbuh oleh kebisingan,
-- bukan oleh kejadian — dan kotak ini 1 vCPU. Jadi ping menulis ke kolom ringan
-- di sini, sementara TRANSISI (celah terdeteksi, tersambung kembali) tetap
-- ditulis sebagai event.
--
-- PRINSIP: heartbeat adalah sinyal OBSERVASI, BUKAN mekanisme pengunci
-- ─────────────────────────────────────────────────────────────────
-- last_ping_at tidak pernah dibaca untuk memutuskan apa pun tentang boleh atau
-- tidaknya murid melanjutkan: satu-satunya pemicu locked_pending_resume tetap
-- fullscreen/tab-switch (migrasi 011 + 054). Kolom ini ada supaya guru bisa
-- membedakan "koneksinya putus sebentar" dari "murid benar-benar meninggalkan
-- ujian lama" — dan supaya transisi itu punya stempel.
--
-- Idempoten dan tidak merusak: IF NOT EXISTS di setiap kolom, tidak ada satu
-- pun pernyataan yang menghapus tabel, kolom, atau baris. Dijalankan dua kali
-- hasilnya sama. Sengaja TANPA BEGIN;/COMMIT; — deploy/apply_migration.py yang
-- memiliki transaksinya.

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS last_ping_at TIMESTAMPTZ;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS ping_count INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN submissions.last_ping_at IS
    'Instan ping heartbeat terakhir diterima. Sinyal observasi untuk guru, BUKAN input keputusan apa pun: penguncian tetap hanya dari pelanggaran fullscreen/tab-switch.';

COMMENT ON COLUMN submissions.ping_count IS
    'Berapa ping diterima untuk attempt ini. Membedakan baru mulai dari sudah lama duduk tanpa membaca tabel event.';

CREATE INDEX IF NOT EXISTS idx_submissions_last_ping
    ON submissions(last_ping_at)
    WHERE status = 'draft';
