-- Migration 035: what one sitting actually did, and what a class normally does
--
-- Jalankan di Supabase SQL Editor (atau lewat deploy/apply_migration.py).
--
-- Tiga tabel, dan masing-masing menjawab satu pertanyaan yang hari ini tidak bisa
-- dijawab dari data yang tersimpan:
--
--   * `attempt_session_events` — satu baris per kejadian selama ujian, dengan
--     nomor urut, dua jam (jam klien dan jam server), durasi, konteks perangkat
--     dan nomor soal. Hari ini yang tersimpan hanya `violation_logs`: satu baris
--     per ketidakhadiran yang **dihitung**, distempel jam server, tanpa urutan,
--     tanpa durasi kecuali kesempatan kedua sempat mengukurnya, dan tanpa satu
--     pun tanda tangan perangkat. Itu cukup untuk menaikkan tangga penalti dan
--     tidak cukup untuk menjawab "apa yang terjadi pada murid ini".
--
--   * `attempt_summary` — satu baris per attempt, dihitung saat ujian selesai.
--     Satu baris per attempt ditegakkan `UNIQUE` pada `attempt_id`, bukan oleh
--     konvensi: submit ulang tidak boleh menumpuk ringkasan.
--
--   * `exam_class_baseline` — sebaran tiap metrik untuk satu (ujian, kelas):
--     median, IQR, MAD dan persentil 10/25/50/75/90. Inilah yang membuat sebuah
--     angka bisa dibaca: "keluar 3 kali" bukan temuan sampai kelasnya menunjukkan
--     apa yang normal.
--
-- ## Aturan NULL, dan kenapa ia ada di sini
--
-- Tiga kolom `attempt_summary` sengaja boleh NULL, dan NULL berarti **tidak ada
-- yang mengukur**, bukan nol:
--
--   `sync_gap_count`, `offline_ms`, `answer_change_count`, `per_question`.
--
-- Sebuah nol adalah temuan ("murid ini tidak pernah kehilangan sinkron"); sebuah
-- pengukuran yang tidak ada adalah ketiadaan temuan. Menuliskan keduanya sebagai
-- `0` membuat guru membaca yang kedua sebagai yang pertama, dan itu satu-satunya
-- cara dashboard ini bisa berbohong tanpa satu pun angka salah. Karena itu
-- `sources` ikut disimpan: ia mencatat tabel mana yang benar-benar menyumbang
-- baris, sehingga nol bisa dibedakan dari ketiadaan sumber tanpa menebak.
--
-- Konsekuensinya hari ini: `violation_logs` sudah mengisi metrik kepergian, jadi
-- metrik itu nyata (termasuk nolnya), sementara metrik yang hanya bisa datang
-- dari `attempt_session_events` bernilai NULL sampai fase ingest mengirim
-- kejadian. Kolomnya sengaja ada sekarang supaya fase itu tidak butuh migrasi.
--
-- `per_question` NULL juga berarti "belum ada kejadian yang menyebut nomor
-- soal" — bukan `{}` (yang berarti "ditanya, dan tidak ada").
--
-- Idempoten dan tidak merusak: `IF NOT EXISTS` di setiap CREATE, `DROP POLICY IF
-- EXISTS` sebelum tiap `CREATE POLICY`, dan tidak ada satu pun pernyataan yang
-- menghapus tabel, kolom, atau baris. Kode di
-- `app/services/attempt_summary.py` benar dengan atau tanpa tabel ini (setiap
-- penulisan ringkasan adalah best-effort dan kegagalannya tidak pernah menggagal
-- submit murid), jadi migrasi ini aman diterapkan kapan saja.

-- ── 1. satu baris per kejadian ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS attempt_session_events (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- `attempt_id` menunjuk `submissions.id`: satu baris per (murid, ujian) sudah
    -- ditegakkan `submissions_student_exam_unique`, jadi attempt dan submission
    -- adalah hal yang sama dan tidak perlu tabel attempt kedua.
    attempt_id     UUID NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    -- Jam klien dan jam server disimpan berdampingan. Yang pertama bisa digeser
    -- murid, yang kedua tidak; selisihnya adalah satu-satunya bukti jam yang
    -- dipindahkan, dan `attempt_summary.clock_drift_max_ms` yang menyimpannya.
    occurred_at    TIMESTAMPTZ,
    server_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms    INTEGER,
    offline        BOOLEAN NOT NULL DEFAULT FALSE,
    device_class   TEXT,
    question_index INTEGER,
    meta           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Nomor urut yang unik per attempt, bukan per kiriman. Inilah yang membuat
    -- nomor urut berarti sesuatu: kejadian yang sama dikirim dua kali (sync yang
    -- diulang setelah koneksi pulih, satu tab kedua) ditolak **database**, bukan
    -- dipercayakan pada klien untuk tidak mengirimnya. Tanpa ini, celah urutan dan
    -- penyuntingan nomor tidak bisa dibedakan dari kiriman ganda yang normal.
    UNIQUE (attempt_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_attempt_events_attempt_kind
    ON attempt_session_events(attempt_id, kind);

COMMENT ON TABLE attempt_session_events IS
    'One row per event during a sitting, numbered per attempt. `UNIQUE (attempt_id, seq)` refuses a replayed event; both clocks are kept so a moved device clock is visible as the difference. Read by app/services/attempt_summary.py.';

COMMENT ON COLUMN attempt_session_events.seq IS
    'Client-assigned sequence number, unique within the attempt. A gap is information (an event was lost or edited), which is why the number is stored rather than only used for ordering.';

COMMENT ON COLUMN attempt_session_events.occurred_at IS
    'The client''s reading of when this happened. Kept beside server_at, never instead of it, because this end is the one a student can move.';

-- ── 2. satu baris per attempt ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS attempt_summary (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id          UUID NOT NULL UNIQUE REFERENCES submissions(id) ON DELETE CASCADE,
    exam_id             UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    student_id          UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    school_id           UUID REFERENCES schools(id) ON DELETE CASCADE,

    events_seen         INTEGER NOT NULL DEFAULT 0,
    truncated           BOOLEAN NOT NULL DEFAULT FALSE,
    counts_by_kind      JSONB NOT NULL DEFAULT '{}'::jsonb,

    away_count          INTEGER NOT NULL DEFAULT 0,
    away_total_ms       BIGINT NOT NULL DEFAULT 0,
    away_max_ms         BIGINT NOT NULL DEFAULT 0,
    away_short_count    INTEGER NOT NULL DEFAULT 0,
    away_long_count     INTEGER NOT NULL DEFAULT 0,
    away_unknown_count  INTEGER NOT NULL DEFAULT 0,

    -- NULL = tidak ada sumber yang mengukurnya. Lihat catatan aturan NULL di atas.
    sync_gap_count      INTEGER,
    offline_ms          BIGINT,
    answer_change_count INTEGER,
    per_question        JSONB,

    sitting_ms          BIGINT,
    effective_active_ms BIGINT,

    clock_drift_max_ms  BIGINT NOT NULL DEFAULT 0,
    clock_suspect       BOOLEAN NOT NULL DEFAULT FALSE,

    first_event_at      TIMESTAMPTZ,
    last_event_at       TIMESTAMPTZ,
    sources             TEXT[] NOT NULL DEFAULT '{}',
    method_version      TEXT NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attempt_summary_exam ON attempt_summary(exam_id);
CREATE INDEX IF NOT EXISTS idx_attempt_summary_student ON attempt_summary(student_id);

ALTER TABLE attempt_summary ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE attempt_summary IS
    'What one sitting cost, one row per attempt (UNIQUE on attempt_id). Written when a sitting ends, best-effort, so a failure here never costs a student their submission.';

COMMENT ON COLUMN attempt_summary.sync_gap_count IS
    'NULL when nothing measured synchronisation. Once attempt_session_events carries sync_gap rows, this is a real count and its zero is a finding.';

COMMENT ON COLUMN attempt_summary.offline_ms IS
    'NULL when nothing measured connectivity. A zero here would be this table claiming the link never dropped, which is not something violation_logs can say.';

COMMENT ON COLUMN attempt_summary.answer_change_count IS
    'NULL until an event source reports answer edits. The stored submission holds only the final answers, so today there is no history to count.';

COMMENT ON COLUMN attempt_summary.per_question IS
    'Per-question dwell in ms, keyed by question number. NULL means no event named a question; {} would mean "asked, and there was nothing".';

COMMENT ON COLUMN attempt_summary.effective_active_ms IS
    'Time on the paper: sitting minus measured absences. NULL whenever any absence has no recorded length, because sitting-minus-known is larger than the truth and would be read as an effort measurement.';

COMMENT ON COLUMN attempt_summary.sources IS
    'The tables that actually contributed rows to this summary. Recorded so a null can be told apart from a zero without guessing, and so an older summary can be recognised as computed from fewer sources.';

COMMENT ON COLUMN attempt_summary.truncated IS
    'True when the attempt had more events than the summary reads. A silently truncated summary is indistinguishable from a complete one.';

-- ── 3. sebaran kelas sebagai pembanding ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS exam_class_baseline (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id        UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    class_id       UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    metric         TEXT NOT NULL,
    n              INTEGER NOT NULL,

    median         DOUBLE PRECISION,
    p10            DOUBLE PRECISION,
    p25            DOUBLE PRECISION,
    p50            DOUBLE PRECISION,
    p75            DOUBLE PRECISION,
    p90            DOUBLE PRECISION,
    iqr            DOUBLE PRECISION,
    mad            DOUBLE PRECISION,
    metric_min     DOUBLE PRECISION,
    metric_max     DOUBLE PRECISION,

    small_sample   BOOLEAN NOT NULL DEFAULT FALSE,
    method_version TEXT NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Satu baris per metrik. Tanpa ini, setiap perhitungan ulang menumpuk baris
    -- dan pembacaan menjadi "yang mana yang terakhir" — pertanyaan yang tidak
    -- pernah dimaksudkan siapa pun.
    UNIQUE (exam_id, class_id, metric)
);

ALTER TABLE exam_class_baseline ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE exam_class_baseline IS
    'The distribution of each attempt metric for one (exam, class), so a single number can be read against what is normal for that room. Written when a reader asks for it and the stored row is stale or from another method version.';

COMMENT ON COLUMN exam_class_baseline.mad IS
    'Median absolute deviation: a spread that does not move when one student has a very bad sitting, which is the point of using it beside the IQR.';

COMMENT ON COLUMN exam_class_baseline.small_sample IS
    'True below app/services/attempt_summary.py MIN_BASELINE_N. A class of five still gets a baseline; the reader is told it is five.';

COMMENT ON COLUMN exam_class_baseline.method_version IS
    'Which rule produced these numbers. A percentile is only comparable to a percentile from the same rule, so a row from an older version is recomputed rather than served.';

-- ── 4. siapa yang boleh membaca ──────────────────────────────────────────────
--
-- Aplikasi mencapai ketiganya dengan service key, yang menembus RLS. Tidak ada
-- peran lain yang boleh: baris di sini adalah perilaku murid yang bisa dikenali,
-- dan `attempt_session_events` memuat nomor soal serta waktu keluar — peta ujian
-- yang tidak perlu dipegang klien anonim mana pun. Cakupan per sekolah ditegakkan
-- di rute (guru: kelasnya; admin sekolah: sekolahnya; murid: dirinya), sebab
-- service key melewati RLS dan kebijakan di sini hanya bisa menutup pintu, bukan
-- memilih baris.

ALTER TABLE attempt_session_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS attempt_session_events_service_only ON attempt_session_events;
CREATE POLICY attempt_session_events_service_only ON attempt_session_events
  FOR ALL USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS attempt_summary_service_only ON attempt_summary;
CREATE POLICY attempt_summary_service_only ON attempt_summary
  FOR ALL USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS exam_class_baseline_service_only ON exam_class_baseline;
CREATE POLICY exam_class_baseline_service_only ON exam_class_baseline
  FOR ALL USING (auth.role() = 'service_role');
