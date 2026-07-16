-- Migration 013: Fix missing columns identified by code audit
-- Run this AFTER migrations 001-012 in Supabase SQL Editor

-- 1. Add school_id to profiles (queried by auth, student, teacher, admin routes)
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES auth.users(id) ON DELETE SET NULL;

-- 2. Add started_at to submissions (for timer persistence across refresh)
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ DEFAULT NOW();

-- 3. Add student_id index on submissions (for faster lookups)
CREATE INDEX IF NOT EXISTS idx_submissions_student_exam ON submissions(student_id, exam_id);

-- 4. Create schools table if not exists (queried by many routes)
CREATE TABLE IF NOT EXISTS schools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    npsn TEXT,
    address TEXT,
    phone TEXT,
    email TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Add school_id FK to classes
ALTER TABLE classes ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE CASCADE;

-- 6. Add unique constraint to prevent double submission
-- (Remove if constraint name already exists from earlier migration)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'submissions_student_exam_unique' AND table_name = 'submissions'
    ) THEN
        ALTER TABLE submissions ADD CONSTRAINT submissions_student_exam_unique
            UNIQUE (student_id, exam_id);
    END IF;
END $$;

-- 7. Enable RLS on schools
ALTER TABLE schools ENABLE ROW LEVEL SECURITY;

-- 8. RLS policies for schools
DROP POLICY IF EXISTS "schools_select_own" ON schools;
CREATE POLICY "schools_select_own" ON schools FOR SELECT USING (true);
DROP POLICY IF EXISTS "schools_insert_service" ON schools;
CREATE POLICY "schools_insert_service" ON schools FOR INSERT WITH CHECK (true);
DROP POLICY IF EXISTS "schools_update_service" ON schools;
CREATE POLICY "schools_update_service" ON schools FOR UPDATE USING (true);

-- Done! Verify with:
-- SELECT column_name FROM information_schema.columns WHERE table_name='profiles';
-- SELECT column_name FROM information_schema.columns WHERE table_name='submissions';
