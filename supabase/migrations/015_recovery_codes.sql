-- Migration 015: Exam Recovery Codes
-- Create table to store recovery codes for exam resume feature

CREATE TABLE IF NOT EXISTS exam_recovery_codes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    code VARCHAR(6) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exam_id, student_id)
);

-- Index for fast recovery code lookup
CREATE INDEX IF NOT EXISTS idx_recovery_codes_code ON exam_recovery_codes(code);
CREATE INDEX IF NOT EXISTS idx_recovery_codes_student ON exam_recovery_codes(student_id);

-- RLS: students can only see their own codes
ALTER TABLE exam_recovery_codes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Students can read own recovery codes"
    ON exam_recovery_codes FOR SELECT
    USING (student_id = auth.uid());

CREATE POLICY "Students can insert own recovery codes"
    ON exam_recovery_codes FOR INSERT
    WITH CHECK (student_id = auth.uid());

CREATE POLICY "Students can update own recovery codes"
    ON exam_recovery_codes FOR UPDATE
    USING (student_id = auth.uid());
