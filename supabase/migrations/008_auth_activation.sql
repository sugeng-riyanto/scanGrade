-- Migration 008: Auth Activation & Forgot Password support
-- Adds columns for activation code flow

ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS activation_code VARCHAR(12);
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS is_activated BOOLEAN DEFAULT FALSE;
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_reg_req_activation_code ON school_registration_requests(activation_code);
CREATE INDEX IF NOT EXISTS idx_reg_req_email ON school_registration_requests(requester_email);
