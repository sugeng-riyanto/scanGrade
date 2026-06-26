-- ============================================================
-- Migration 023: Specific Recipients for Pengumuman (Broadcast)
-- ============================================================
-- Adds column to store specific recipient IDs for targeted broadcasts.
-- When NULL/empty: all users with target_role can see it.
-- When populated: only those user IDs can see it.
-- ============================================================

ALTER TABLE pengumuman ADD COLUMN IF NOT EXISTS specific_recipients UUID[] DEFAULT NULL;
