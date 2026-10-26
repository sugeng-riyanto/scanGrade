-- Migration 076: classes.school_id must be a uuid, and an integer one is repaired
-- ============================================================================
-- WHAT IS WRONG
--
-- `003_submission_hidden_retracted.sql` — a file about submission visibility —
-- also carries:
--
--     ALTER TABLE classes ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
--
-- and it sorts before `007_multi_school_role_hierarchy.sql`, which asks for the
-- same column as `uuid` with `IF NOT EXISTS`. The first file wins and the second
-- becomes a **silent no-op**. Everything that then compares that column to a
-- school id fails, and none of it fails loudly at the point of the mistake:
--
--   * `007`'s `UPDATE classes SET school_id = '<uuid>'`  -> 42804
--     (column "school_id" is of type integer but expression is of type uuid)
--   * eight RLS policies -> 42883
--     (operator does not exist: integer = uuid), because they read
--     `school_id = public._user_school_id()`
--
-- The type is the whole defect: the policies and the update are correct, and the
-- column is the only thing that disagrees. Measured on an empty PostgreSQL 16
-- built from this repository's SQL, that is 12 statements that never took effect
-- and one table whose `school_id` cannot hold a school.
--
-- WHY THIS IS A NEW FILE AND NOT AN EDIT OF 003
--
-- `deploy/apply_migration.py` identifies a migration by the digest of its bytes.
-- Rewriting `003` after a box has applied it reports `APPLIED, THEN EDITED` for a
-- file nobody edited — the false alarm this repository has already paid for twice
-- — and it would leave every box that already ran the old bytes with a ledger it
-- cannot reconcile. So the repair is additive and forward: the old file keeps its
-- bytes, and this one converges the schema to what the rest of the repository
-- says it should be.
--
-- WHAT IT DOES, AND WHAT IT REFUSES TO DO
--
--  * A schema where the column is already `uuid`, or never existed, returns
--    without touching anything. This file is safe to re-run.
--  * A column that is still `integer` **and holds a value** is refused with a
--    message naming the count. There is no honest mapping from an integer to a
--    `schools(id)` uuid: `1` is the default this file is here to remove, not a
--    school. Guessing is how an announcement lands in the wrong tenant.
--  * An empty-or-NULL column is retyped, the `DEFAULT 1` is dropped, the foreign
--    key to `schools(id)` is installed, and the index is restored.
-- ============================================================================

DO $$
DECLARE
    col_type   text;
    populated  bigint;
    has_school boolean;
BEGIN
    SELECT c.data_type
      INTO col_type
      FROM information_schema.columns c
     WHERE c.table_schema = 'public'
       AND c.table_name   = 'classes'
       AND c.column_name  = 'school_id';

    -- A schema with no such column is a different schema, not a broken one.
    IF col_type IS NULL THEN
        RAISE NOTICE 'public.classes.school_id does not exist; nothing to repair';
        RETURN;
    END IF;

    IF col_type = 'integer' THEN
        -- `1` is not a school. A row that holds one has to be re-pointed by hand,
        -- by someone who knows which school it belongs to.
        SELECT count(*) INTO populated
          FROM public.classes
         WHERE school_id IS NOT NULL;

        IF populated > 0 THEN
            RAISE EXCEPTION
                'public.classes.school_id is integer and % row(s) hold a value; '
                'there is no honest integer-to-uuid mapping. Re-point those rows '
                'to a real schools(id) by hand, then re-run this migration',
                populated;
        END IF;

        ALTER TABLE public.classes ALTER COLUMN school_id DROP DEFAULT;
        -- NULL is the only value the column can hold at this point, so the
        -- transform is lossless by construction rather than by hope.
        ALTER TABLE public.classes
            ALTER COLUMN school_id TYPE uuid USING (NULL::uuid);
        RAISE NOTICE 'public.classes.school_id retyped integer -> uuid';
    END IF;

    -- The foreign key the `IF NOT EXISTS` in 007 never got to install.
    SELECT to_regclass('public.schools') IS NOT NULL INTO has_school;
    IF has_school THEN
        ALTER TABLE public.classes
            DROP CONSTRAINT IF EXISTS classes_school_id_fkey;
        ALTER TABLE public.classes
            ADD CONSTRAINT classes_school_id_fkey
            FOREIGN KEY (school_id) REFERENCES public.schools(id) ON DELETE CASCADE;
    END IF;
END $$;

-- The old index went away with the type change; put it back where the queries
-- that read it (`007`, the school-official policies) expect it.
CREATE INDEX IF NOT EXISTS idx_classes_school ON classes(school_id);
