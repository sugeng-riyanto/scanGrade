-- Migration 034: one row in trial_settings
--
-- `trial_settings` is a singleton by intent and by nothing else. The table has no
-- uniqueness, so a second row is insertable, and both readers asked for
-- `.limit(1)` with no ordering. Two rows therefore mean the settings page edits
-- one value while a grant site applies the other: a box that is honest and wrong
-- at the same time, and impossible to see from either screen.
--
-- The seed in `012_subscription_system.sql` ends with `ON CONFLICT DO NOTHING`
-- and no conflict target, which on a table with no unique index inserts on every
-- run rather than once — so re-running the setup is one of the ways a second row
-- got there in the first place.
--
-- The application does not depend on this constraint: every read takes the newest
-- row by id, so the page and the grant sites agree with or without it. That is
-- what makes the migration safe to apply whenever, and safe to have not applied
-- yet — the constraint removes the possibility instead of being the detection.

-- Keep the row a human wrote last, drop the rest. Measured on production before
-- this migration: fifteen rows, of which fourteen were June seeds and two were a
-- `30` an operator set on 22 September — carrying the *lowest* ids. "Newest row"
-- would have kept a seed (14) and silently dropped the setting that was actually
-- made, so the survivor is chosen by `updated_at`, with `id` breaking ties.
-- NULLS LAST for the same reason the reader uses it: the column has a default, so
-- a NULL means "written without one", not "written most recently".
DELETE FROM trial_settings t
WHERE t.id <> (
    SELECT id FROM trial_settings
    ORDER BY updated_at DESC NULLS LAST, id DESC
    LIMIT 1
);

-- At most one row, enforced by the database rather than by convention. A unique
-- index on the constant `true` is how Postgres is asked for "one row, whatever it
-- holds" — a UNIQUE on a column would constrain the *value* instead of the count.
CREATE UNIQUE INDEX IF NOT EXISTS trial_settings_singleton
    ON trial_settings ((true));

COMMENT ON TABLE trial_settings IS
    'How many days a new school''s free trial lasts. At most one row, enforced by the trial_settings_singleton index; read through app/services/trial_settings.py.';
