-- Migration 009: Super Admin Approval System
-- Adds approval workflow fields to school_registration_requests
-- Adds registration_codes table for activation codes

ALTER TABLE school_registration_requests
ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS review_notes TEXT,
ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ DEFAULT NOW();

-- Ensure the registration_codes table has all required fields
ALTER TABLE registration_codes
ADD COLUMN IF NOT EXISTS duration_label TEXT DEFAULT '1-month',
ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ DEFAULT NOW();
