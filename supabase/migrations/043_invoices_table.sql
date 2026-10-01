-- 043_invoices_table.sql
--
-- The table every subscription receipt is written to, created by a migration.
--
-- `_activate_subscription` builds a bill for every activation, but the call sat
-- behind `return code` and never ran — and the table itself was only ever created
-- by `supabase/_COMPLETE_SETUP.sql`, so an environment built from the migrations
-- had no `invoices` at all. Fixing the ordering without this would make every
-- activation raise `relation "invoices" does not exist`. It is added here, before
-- the RLS fix that names it, so both the schema contract and a fresh setup find it.
--
-- Non-destructive and idempotent: nothing is dropped or rewritten, and an
-- existing table only gains the column it is missing. The three demo invoices on
-- the live box are untouched — this statement does not read or write rows.

CREATE TABLE IF NOT EXISTS invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number TEXT NOT NULL UNIQUE,
    school_id UUID NOT NULL,
    transaction_id TEXT,
    order_id TEXT DEFAULT '',
    plan_id INTEGER REFERENCES subscription_plans(id),
    amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'paid',
    payment_method TEXT DEFAULT '',
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    paid_at TIMESTAMPTZ DEFAULT now(),
    due_at TIMESTAMPTZ,
    notes TEXT DEFAULT '',
    activation_code TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS invoice_number_seq START 1;

-- For a box whose table predates the column (003 and later added it by hand).
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS activation_code TEXT DEFAULT '';

ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

-- The same two policies 001 and 20260608 declare, repeated here so a fresh
-- database that reaches this file first still ends with them. `DROP ... IF
-- EXISTS` then `CREATE` keeps it idempotent.
DROP POLICY IF EXISTS "invoices_select_admin" ON invoices;
CREATE POLICY "invoices_select_admin" ON invoices
  FOR SELECT
  USING (
    public._is_role('admin_sekolah') AND school_id = public._user_school_id()
  );

DROP POLICY IF EXISTS "invoices_select_super_admin" ON invoices;
CREATE POLICY "invoices_select_super_admin" ON invoices
  FOR SELECT
  USING (public._is_role('super_admin'));
