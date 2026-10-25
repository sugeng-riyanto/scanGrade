-- ScanMeet paid add-on, tenant-scoped and disabled by default.
-- Separate from subscription billing: no automatic invoice or charge.
CREATE TABLE IF NOT EXISTS public.scanmeet_school_entitlements (
  school_id uuid PRIMARY KEY REFERENCES public.schools(id) ON DELETE CASCADE,
  enabled boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'disabled'
    CHECK (status IN ('disabled','trial','paid','expired')),
  valid_until timestamptz,
  approved_by uuid,
  approved_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (NOT enabled OR (status IN ('trial','paid') AND valid_until IS NOT NULL))
);
ALTER TABLE public.scanmeet_school_entitlements ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.scanmeet_school_entitlements FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.scanmeet_school_entitlements TO service_role;
CREATE INDEX IF NOT EXISTS scanmeet_school_entitlements_valid_until_idx
  ON public.scanmeet_school_entitlements(valid_until) WHERE enabled = true;
COMMENT ON TABLE public.scanmeet_school_entitlements IS
  'ScanMeet per-school entitlement, managed by Super Admin via audited server API. Absence means OFF. Payment is verified externally; no automatic charges.';
