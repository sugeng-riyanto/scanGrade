-- ============================================================
-- Migration 024: pengumuman.school_id INT -> UUID, legacy rows remapped
-- ============================================================
-- pengumuman was created with `school_id INT REFERENCES school_settings(id)`,
-- while every other school reference in the schema is a UUID pointing at
-- schools(id). The mismatch is not merely cosmetic: it made the INT column
-- reject a real school UUID, and the Flask layer "fixed" that by retrying with
-- `school_id = 1`. Every school's broadcast therefore landed on the same value,
-- so the read path's `.eq("school_id", 1)` returned announcements from *all*
-- schools. Measured on this database before this migration: one filter value,
-- two announcements, two different schools.
--
-- ---------------------------------------------------------------------------
-- Why the original wording of this migration could not run
-- ---------------------------------------------------------------------------
-- It did `USING school_id::text::uuid`, which needs every existing value to be a
-- valid UUID text. This database had a row holding `1`, and '1' is not a UUID,
-- so the ALTER aborted with:
--
--     invalid input syntax for type uuid: "1"
--
-- ---------------------------------------------------------------------------
-- And `1` is not a school at all
-- ---------------------------------------------------------------------------
-- It is the value the retry wrote. The row's real school is recoverable from its
-- sender's profile, or failing that from its class. That is where this migration
-- takes it from -- and if neither is available it RAISES rather than guess,
-- because filing an announcement under the wrong school is a cross-tenant leak,
-- which is the very thing being fixed here.
--
-- Safe to re-run: it detects the column already being uuid and returns.
-- ============================================================

DO $$
DECLARE
    col_type text;
    unmappable integer;
BEGIN
    SELECT c.data_type
      INTO col_type
      FROM information_schema.columns c
     WHERE c.table_schema = 'public'
       AND c.table_name   = 'pengumuman'
       AND c.column_name  = 'school_id';

    IF col_type IS NULL THEN
        RAISE EXCEPTION 'public.pengumuman.school_id does not exist';
    END IF;

    IF col_type = 'uuid' THEN
        -- Clear a staging column left by an interrupted earlier attempt.
        ALTER TABLE public.pengumuman DROP COLUMN IF EXISTS school_id_new;
        RAISE NOTICE 'pengumuman.school_id is already uuid; nothing to do';
        RETURN;
    END IF;

    -- The mapping is staged in a real column because Postgres refuses a subquery
    -- inside a transform expression:
    --     ALTER COLUMN school_id TYPE uuid USING (COALESCE((SELECT ...), ...))
    -- fails with `cannot use subquery in transform expression`. A plain column
    -- reference is allowed, so the lookup happens in the UPDATE below and the
    -- retype just reads the staged value.
    ALTER TABLE public.pengumuman DROP COLUMN IF EXISTS school_id_new;
    ALTER TABLE public.pengumuman ADD COLUMN school_id_new uuid;

    UPDATE public.pengumuman p
       SET school_id_new = COALESCE(
             (SELECT pr.school_id FROM public.profiles pr
               WHERE pr.id = p.sender_id AND pr.school_id IS NOT NULL),
             (SELECT cl.school_id FROM public.classes cl
               WHERE cl.id = p.class_id AND cl.school_id IS NOT NULL))
     WHERE p.school_id IS NOT NULL;

    -- A row that cannot be traced to a school must stop the migration, not be
    -- quietly parked somewhere.
    SELECT count(*)
      INTO unmappable
      FROM public.pengumuman p
     WHERE p.school_id IS NOT NULL
       AND p.school_id_new IS NULL;

    IF unmappable > 0 THEN
        RAISE EXCEPTION
            'cannot derive a school for % pengumuman row(s); set pengumuman.school_id to a real schools(id) by hand, then re-run',
            unmappable;
    END IF;

    ALTER TABLE public.pengumuman DROP CONSTRAINT IF EXISTS pengumuman_school_id_fkey;
    ALTER TABLE public.pengumuman ALTER COLUMN school_id DROP DEFAULT;

    -- Retype from the staged column. Genuinely NULL rows stay NULL: a
    -- school-less announcement is readable per the app's own logic, whereas
    -- inventing a school for it would hand it to the wrong tenant.
    ALTER TABLE public.pengumuman
        ALTER COLUMN school_id TYPE uuid USING (school_id_new);

    ALTER TABLE public.pengumuman DROP COLUMN school_id_new;

    ALTER TABLE public.pengumuman
        ADD CONSTRAINT pengumuman_school_id_fkey
        FOREIGN KEY (school_id) REFERENCES public.schools(id) ON DELETE CASCADE;
END $$;

-- The old index went away with the type change; put it back.
CREATE INDEX IF NOT EXISTS idx_pengumuman_school_id ON pengumuman(school_id);

-- Also fix pengumuman_read indexes for performance
CREATE INDEX IF NOT EXISTS idx_pengumuman_read_pengumuman_id ON pengumuman_read(pengumuman_id);
CREATE INDEX IF NOT EXISTS idx_pengumuman_read_reader_id ON pengumuman_read(reader_id);
