-- ============================================================
-- Migration 024: Fix pengumuman.school_id type (INT → UUID)
-- ============================================================
-- The pengumuman table was created with school_id INT referencing
-- school_settings(id), but profiles use UUID school_id referencing
-- schools(id). This mismatch causes "invalid input syntax for type
-- integer" when creating/listing pengumuman.
-- ============================================================

-- Drop existing FK constraint
ALTER TABLE pengumuman DROP CONSTRAINT IF EXISTS pengumuman_school_id_fkey;

-- Change column type to UUID; drop default first if present
ALTER TABLE pengumuman ALTER COLUMN school_id DROP DEFAULT;
ALTER TABLE pengumuman ALTER COLUMN school_id TYPE UUID USING school_id::text::uuid;

-- Re-add FK to schools table
ALTER TABLE pengumuman ADD CONSTRAINT pengumuman_school_id_fkey
    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE;

-- Also fix pengumuman_read indexes for performance
CREATE INDEX IF NOT EXISTS idx_pengumuman_read_pengumuman_id ON pengumuman_read(pengumuman_id);
CREATE INDEX IF NOT EXISTS idx_pengumuman_read_reader_id ON pengumuman_read(reader_id);
