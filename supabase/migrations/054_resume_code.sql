-- ──────────────────────────────────────────────────────────────
-- Migration 054: kode resume otomatis untuk attempt yang terkunci
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Masalah yang dijawab
-- ────────────────────
-- `exams` sudah punya tangga pelanggaran sejak migrasi 011
-- (`anti_cheat_enabled`, `penalty_per_violation`, `max_violations`,
-- `auto_submit_on_max`), dan `calculate_graduated_penalty` sudah menutup ujian di
-- ambang itu. Yang belum ada adalah cara **membuka kembali** attempt yang tertutup
-- karena pelanggaran: hari ini ambang itu berarti "selesai", dan seorang murid
-- yang keluar layar penuh karena notifikasi kehilangan sisa ujiannya tanpa jalan
-- pulih selain guru mencatat manual.
--
-- Migrasi ini menambah satu jalan pulih yang terkendali:
--
--   * `submissions.resume_limit`     batas pemakaian, DISALIN saat attempt dibuat
--                                    (snapshot) supaya kebijakan yang berubah di
--                                    tengah ujian tidak mengubah jatah attempt yang
--                                    sudah berjalan;
--   * `submissions.resume_count_used` berapa kali sudah dipakai;
--   * `submissions.locked_at`        kapan dikunci (untuk audit dan durasi terkunci);
--   * `submissions.last_resumed_at`  kapan terakhir resume sah.
--
-- TIDAK ADA kode baru di sini, dan itu disengaja
-- ─────────────────────────────────────────────
-- Versi pertama fitur ini menambah `submissions.resume_code`, sebuah kode KEDUA yang
-- tampil di layar ujian yang sama dengan kode recovery yang sudah ada sejak lama
-- (`exam_access_codes`, diterbitkan `app/utils/exam_recovery.py`, tampil di topbar
-- ujian sejak menit pertama). Dua kode di satu layar adalah pertanyaan yang murid
-- tidak bisa jawab ("yang mana yang saya ketik?"), dan dua sistem yang sama-sama
-- berarti "jalan kembali ke kertas ini" adalah persis bentuk sistem paralel yang
-- berulang kali ditutup di repositori ini. Jadi kuncinya MEMINJAM kode yang sudah
-- ada; yang benar-benar baru hanyalah gerbang kuncinya: status
-- `locked_pending_resume`, jatah pemakaian, dan aturan deadline.
--
-- PRINSIP WAKTU — INI YANG PALING PENTING DI BERKAS INI
-- ───────────────────────────────────────────────────
-- **Tidak ada satu pun kolom di sini yang bisa mengubah, membekukan, atau
-- memperpanjang waktu ujian.** Deadline attempt tetap dihitung dari
-- `started_at` + `exams.duration_minutes` (+ `end_at` bila ujian diset berhenti di
-- ujung jendela) lewat satu fungsi `app/utils/exam_window.deadline()`, dan migrasi
-- ini tidak menyentuh satu pun dari kolom itu. Sengaja TIDAK ADA kolom bernama
-- `extra_time`, `frozen_until`, `paused_at`, atau sejenisnya, supaya tidak ada
-- pembaca berikutnya yang menjadikannya sumber waktu kedua: jam tetap berjalan
-- selama attempt terkunci, dan murid yang menunggu tiga menit kehilangan tiga
-- menit itu — persis seperti menatap dinding selama tiga menit.
--
-- Status `locked_pending_resume` ditambahkan ke kosakata `submissions.status`
-- dengan **mempertahankan kelima nilai lama**: baris yang sudah ada tidak boleh
-- menjadi tidak valid hanya karena migrasi ini dijalankan.
--
-- Idempoten dan tidak merusak: setiap kolom memakai `IF NOT EXISTS`, setiap index
-- `IF NOT EXISTS`, constraint status di-drop lalu dibuat ulang (pola yang sama
-- dengan migrasi 003), dan tidak ada satu pernyataan pun yang menghapus tabel,
-- kolom, atau baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback), sama seperti semua
-- migrasi lain di folder ini.

-- ── 1. kosakata status bertambah, tanpa kehilangan yang lama ────────────────

ALTER TABLE submissions DROP CONSTRAINT IF EXISTS submissions_status_check;
ALTER TABLE submissions ADD CONSTRAINT submissions_status_check
    CHECK (status IN ('draft', 'submitted', 'graded', 'published', 'retracted',
                      'locked_pending_resume'));

-- ── 2. kolom kode resume pada attempt ──────────────────────────────────────
--
-- Attempt *adalah* baris `submissions` (lihat catatan di migrasi 035: satu baris
-- per (murid, ujian) sudah ditegakkan `submissions_student_exam_unique`, jadi
-- tidak perlu tabel attempt kedua). Karena itu kodenya menempel di sini, bukan di
-- tabel paralel yang harus dijaga agar tidak berbeda pendapat dengan baris ini.

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS resume_limit INTEGER;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS resume_count_used INTEGER NOT NULL DEFAULT 0;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS last_resumed_at TIMESTAMPTZ;

COMMENT ON COLUMN submissions.resume_limit IS
    'Batas pemakaian kode untuk attempt ini, DISALIN dari exams.resume_code_limit saat attempt dibuat. Snapshot, bukan bacaan ulang: kebijakan yang berubah di tengah ujian tidak boleh mengubah jatah attempt yang sudah berjalan.';
COMMENT ON COLUMN submissions.resume_count_used IS
    'Berapa kali kode ini sudah dipakai untuk membuka kunci. Setiap pemakaian juga tercatat di event log anti-cheat dan audit log.';
COMMENT ON COLUMN submissions.locked_at IS
    'Kapan server mengunci attempt ini. Dipakai untuk audit dan untuk menghitung berapa lama murid menunggu, BUKAN sebagai sumber waktu ujian: deadline tetap dihitung dari started_at + duration_minutes.';
COMMENT ON COLUMN submissions.last_resumed_at IS
    'Kapan resume sah terakhir terjadi. Murni catatan; tidak ada aritmetika waktu yang membacanya.';

-- Tidak ada index kode di sini: kode yang dipakai untuk membuka kunci adalah baris
-- `exam_access_codes` milik murid itu, dan indexnya sudah ada di sana
-- (`idx_access_codes_code`). Satu kode, satu tempat mencarinya.

-- Yang butuh index adalah "attempt mana yang sedang terkunci", sebab itulah pertanyaan
-- yang ditanyakan halaman guru/pengawas dan sapuan deadline.
CREATE INDEX IF NOT EXISTS idx_submissions_locked
    ON submissions(status)
    WHERE status = 'locked_pending_resume';

-- ── 3. kebijakan di sisi ujian ─────────────────────────────────────────────
--
-- `lock_pending_resume` DEFAULT FALSE, dan itu keputusan yang disengaja: di
-- ambang `max_violations`, perilaku yang berlaku hari ini adalah penutupan ujian
-- (`auto_submit_on_max`). Mengubahnya menjadi penguncian untuk SETIAP ujian yang
-- sudah berjalan berarti diam-diam mengubah apa yang terjadi pada murid di tengah
-- ujian. Jadi fitur ini opt-in per ujian; sekolah yang ingin memakainya menyalakan
-- sakelarnya, dan paritas RBAC-nya menyusul di fase UI.

ALTER TABLE exams ADD COLUMN IF NOT EXISTS lock_pending_resume BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS resume_code_limit INTEGER NOT NULL DEFAULT 2;

COMMENT ON COLUMN exams.lock_pending_resume IS
    'Bila true, menembus max_violations MENGUNCI attempt (locked_pending_resume) alih-alih menutupnya, dan murid dapat membukanya dengan kode resume selama deadline belum lewat. Default false supaya tidak ada ujian yang sudah berjalan berubah perilakunya.';
COMMENT ON COLUMN exams.resume_code_limit IS
    'Berapa kali kode boleh dipakai untuk membuka kunci satu attempt. Disalin ke submissions.resume_limit saat attempt dibuat. 0 berarti tidak ada buka-kunci otomatis (murid langsung ke jalur manual guru).';
