-- ScanGrade Subscription & Payment System
-- Adds tables for Midtrans integration, subscription plans, school subscriptions,
-- payment transactions, activation codes, and trial settings.

-- 1. Midtrans Settings (single row, super admin only)
CREATE TABLE IF NOT EXISTS midtrans_settings (
    id SERIAL PRIMARY KEY,
    merchant_id TEXT NOT NULL DEFAULT '',
    client_key TEXT NOT NULL DEFAULT '',
    server_key TEXT NOT NULL DEFAULT '',
    is_production BOOLEAN DEFAULT false,
    updated_by UUID,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Subscription Plans (configurable by super admin)
CREATE TABLE IF NOT EXISTS subscription_plans (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    duration_label TEXT NOT NULL DEFAULT '',
    duration_days INTEGER NOT NULL DEFAULT 0,
    price DECIMAL(12,2) NOT NULL DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. School Subscriptions (one active + history per school)
CREATE TABLE IF NOT EXISTS school_subscriptions (
    id SERIAL PRIMARY KEY,
    school_id UUID NOT NULL,
    plan_id INTEGER REFERENCES subscription_plans(id),
    status TEXT NOT NULL DEFAULT 'trial',
    trial_days INTEGER NOT NULL DEFAULT 14,
    trial_start TIMESTAMPTZ DEFAULT now(),
    trial_end TIMESTAMPTZ,
    subscription_start TIMESTAMPTZ,
    subscription_end TIMESTAMPTZ,
    activation_code TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 4. Payment Transactions
CREATE TABLE IF NOT EXISTS payment_transactions (
    id SERIAL PRIMARY KEY,
    school_id UUID NOT NULL,
    plan_id INTEGER REFERENCES subscription_plans(id),
    order_id TEXT UNIQUE NOT NULL,
    gross_amount DECIMAL(12,2),
    status TEXT DEFAULT 'pending',
    snap_token TEXT,
    snap_redirect_url TEXT,
    payment_type TEXT,
    transaction_time TIMESTAMPTZ,
    settlement_time TIMESTAMPTZ,
    activation_code TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 5. Trial Settings (single row, set by super admin)
CREATE TABLE IF NOT EXISTS trial_settings (
    id SERIAL PRIMARY KEY,
    trial_days INTEGER NOT NULL DEFAULT 14,
    updated_by UUID,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Seed default subscription plans (optimized pricing for Indonesian market)
INSERT INTO subscription_plans (name, duration_label, duration_days, price, sort_order) VALUES
    ('1 Bulan', '1 Bulan', 30, 59000, 1),
    ('3 Bulan', '3 Bulan', 90, 149000, 2),
    ('4 Bulan', '4 Bulan', 120, 179000, 3),
    ('6 Bulan', '6 Bulan', 180, 249000, 4),
    ('1 Tahun', '1 Tahun', 365, 399000, 5),
    ('2 Tahun', '2 Tahun', 730, 699000, 6),
    ('3 Tahun', '3 Tahun', 1095, 949000, 7),
    ('5 Tahun', '5 Tahun', 1825, 1399000, 8),
    ('7 Tahun', '7 Tahun', 2555, 1799000, 9),
    ('Selamanya', 'Selamanya', 0, 2499000, 10)
ON CONFLICT DO NOTHING;

-- Seed default trial settings
INSERT INTO trial_settings (trial_days) VALUES (14)
ON CONFLICT DO NOTHING;

-- Add pricing_config column to school_settings
ALTER TABLE school_settings ADD COLUMN IF NOT EXISTS pricing_config JSONB DEFAULT '{"model": "flat", "tiers": []}';

-- Add payment_fee_config column to school_settings
ALTER TABLE school_settings ADD COLUMN IF NOT EXISTS payment_fee_config JSONB DEFAULT '{"fee_percent": 0, "fee_flat": 4000, "fee_note": "Biaya admin Rp 4.000 (transfer bank)"}';

-- Add payment_details column to payment_transactions (stores VA numbers, etc.)
ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS payment_details JSONB DEFAULT '{}';
