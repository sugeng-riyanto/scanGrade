ALTER TABLE public.exams
    ADD COLUMN IF NOT EXISTS total_points numeric(6,1) NOT NULL DEFAULT 100
    CHECK (total_points BETWEEN 0.1 AND 10000);

-- Serialize first submission creation with changes to the exam configuration.
CREATE OR REPLACE FUNCTION public.lock_exam_for_submission()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM id FROM public.exams WHERE id = NEW.exam_id FOR SHARE;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS lock_exam_for_submission ON public.submissions;
CREATE TRIGGER lock_exam_for_submission
    BEFORE INSERT ON public.submissions
    FOR EACH ROW EXECUTE FUNCTION public.lock_exam_for_submission();

CREATE OR REPLACE FUNCTION public.protect_exam_with_submissions()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.start_at IS NOT DISTINCT FROM NEW.start_at
            AND OLD.require_seb IS NOT DISTINCT FROM NEW.require_seb
            AND (OLD.total_points IS NOT DISTINCT FROM NEW.total_points
                 OR current_setting('scangrade.confirmed_point_scale', true) = OLD.id::text) THEN
            RETURN NEW;
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.submissions WHERE exam_id = OLD.id) THEN
        RAISE EXCEPTION 'exam_sitting_started' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS protect_exam_with_submissions ON public.exams;
CREATE TRIGGER protect_exam_with_submissions
    BEFORE UPDATE OF start_at, require_seb, total_points OR DELETE ON public.exams
    FOR EACH ROW EXECUTE FUNCTION public.protect_exam_with_submissions();

CREATE OR REPLACE FUNCTION public.apply_exam_point_scale(
    paper_id uuid, previous_points numeric, new_points numeric
)
RETURNS boolean LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM set_config('scangrade.confirmed_point_scale', paper_id::text, true);
    UPDATE public.exams
        SET total_points = new_points
        WHERE id = paper_id AND total_points = previous_points;
    RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION public.apply_exam_point_scale(uuid, numeric, numeric) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_exam_point_scale(uuid, numeric, numeric) TO service_role;
