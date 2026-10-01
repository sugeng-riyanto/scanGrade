-- Migration 047: a school year's life — draft, active, closed
--
-- `school_years` (007) carries only `is_active`, a boolean. That cannot say the
-- two things the year-close wizard needs to say:
--
--   * **draft** — the next year exists (classes can be prepared against it) but
--     nothing is running in it yet, so it must not become the active year just
--     by being created;
--   * **closed** — the year is finished and its papers and marks are history.
--     A closed year is READ-ONLY: it is what stops a late edit from changing
--     marks a pupil has already been shown.
--
-- Additive and non-destructive. `is_active` is kept and stays authoritative for
-- "which year is running now" — every existing reader (`/admin-sekolah/dashboard`,
-- `/export`, the class dropdowns) keeps working untouched. `status` is derived
-- from `is_active` for the rows already on file, so no year is left without one.
--
-- Run through `deploy/apply_migration.py` (dry run first) or in the SQL editor.
-- Safe to run twice.

-- Added NULLABLE first, so the backfill below runs exactly once. A column added
-- with `DEFAULT 'draft'` would mark every existing row `draft`, and a re-run of
-- this file would then flip any *legitimate* draft — a year the wizard had just
-- created — to `closed`, because the UPDATE could no longer tell them apart.
ALTER TABLE school_years
    ADD COLUMN IF NOT EXISTS status TEXT;

-- Derive the life of the rows already on file from the column they already have.
-- A year that is active now is `active`; every other year on file is treated as
-- `closed` rather than `draft`, because a draft is a year nobody has started and
-- these rows are years the school has finished (or was using before `is_active`
-- moved on). Marking them `draft` would make finished history look editable.
UPDATE school_years
   SET status = CASE WHEN is_active THEN 'active' ELSE 'closed' END
 WHERE status IS NULL;

-- Only now does `draft` become the default for rows created later.
ALTER TABLE school_years ALTER COLUMN status SET DEFAULT 'draft';
ALTER TABLE school_years ALTER COLUMN status SET NOT NULL;

DO $$ BEGIN
    ALTER TABLE school_years
        ADD CONSTRAINT school_years_status_check
        CHECK (status IN ('draft', 'active', 'closed'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- "the closed years of this school" is read on every write guard; the active one
-- is already served by 007's partial index.
CREATE INDEX IF NOT EXISTS idx_school_years_status
    ON school_years (school_id, status);
