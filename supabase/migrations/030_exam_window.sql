-- The assignment window, and which of the two clocks ends a sitting.
--
-- `start_at` already existed: the earliest a student may begin. These two columns
-- add the other end of the window and how it interacts with the duration:
--
--   * `end_at`               the last instant a student may *begin*. After it the
--                            exam leaves the student's list; whoever already began
--                            keeps working, because the window governs beginning.
--   * `auto_submit_on_window_end`
--                            when true, the sitting ends at `end_at` whatever time
--                            the student began — the deadline becomes the earlier
--                            of `end_at` and `started_at + duration`, so a late
--                            starter does not get the whole duration. When false
--                            (the default, and what every existing row means) the
--                            duration is counted from the student's own start and
--                            may run past `end_at`, which is what the teacher form
--                            has always said on the Duration field.
--
-- Both nullable/defaulted, so every exam that exists keeps behaving exactly as it
-- did before this migration: no window end, no switch, duration as the only clock.

ALTER TABLE exams ADD COLUMN IF NOT EXISTS end_at TIMESTAMPTZ;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS auto_submit_on_window_end BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN exams.end_at IS
  'Assignment window end: the last instant a student may BEGIN this exam';
COMMENT ON COLUMN exams.auto_submit_on_window_end IS
  'True: the sitting ends at end_at regardless of when the student began, i.e. the earlier of end_at and started_at + duration_minutes';

-- A submission that arrived after its sitting's deadline, beyond the grace.
--
-- Recorded rather than refused: a phone that loses its network in the last minute
-- must not lose the answers. But "the paper was timed" and "the paper came in
-- twenty minutes after the deadline" are different facts about a result, and only
-- one of them was observable before this column existed.
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS submitted_late BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN submissions.submitted_late IS
  'The answers arrived after the sitting deadline plus the grace (app/utils/exam_window.py)';

-- Partial index: the flag is read by the teacher's result list, and false is the
-- overwhelming majority of rows.
CREATE INDEX IF NOT EXISTS idx_submissions_late ON submissions(exam_id)
  WHERE submitted_late = TRUE;
