-- ============================================================================
-- Corporate Banking Change of Account Mandate — Production PostgreSQL Schema
-- Target Engine: PostgreSQL 15+ / 18.6 (CloudSQL & Local PostgreSQL Failover)
-- ============================================================================

CREATE TABLE IF NOT EXISTS corporate_customers (
    customer_id VARCHAR(32) PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    uen VARCHAR(32) UNIQUE NOT NULL,
    entity_type VARCHAR(64) NOT NULL CHECK (
        entity_type IN (
            'TECH_STARTUP',
            'SME',
            'MNC_SUBSIDIARY',
            'PARTNERSHIP_LLP',
            'REGULATED_IMPORT_EXPORT'
        )
    ),
    industry VARCHAR(128) NOT NULL,
    incorporation_country VARCHAR(8) NOT NULL DEFAULT 'SG',
    kyc_status VARCHAR(64) NOT NULL DEFAULT 'VERIFIED',
    ideal_corp_id VARCHAR(64) NOT NULL,
    ideal_auth_status VARCHAR(64) NOT NULL DEFAULT 'IDEAL_TOKEN_AUTHENTICATED',
    active_mandate_version INT NOT NULL DEFAULT 1,
    mandate_status VARCHAR(64) NOT NULL DEFAULT 'ACTIVE',
    relationship_manager VARCHAR(128) NOT NULL,
    registered_address TEXT NOT NULL,
    governance_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bank_accounts (
    account_id VARCHAR(32) PRIMARY KEY,
    customer_id VARCHAR(32) NOT NULL REFERENCES corporate_customers(customer_id) ON DELETE CASCADE,
    account_number VARCHAR(32) UNIQUE NOT NULL,
    account_name VARCHAR(128) NOT NULL,
    account_type VARCHAR(64) NOT NULL CHECK (
        account_type IN (
            'OPERATING',
            'MULTI_CURRENCY',
            'TRADE_FINANCE_FX',
            'ESCROW_TRUST',
            'TREASURY_SWEEP'
        )
    ),
    currency VARCHAR(8) NOT NULL,
    current_balance NUMERIC(18, 2) NOT NULL,
    available_balance NUMERIC(18, 2) NOT NULL,
    sgd_equivalent_balance NUMERIC(18, 2) NOT NULL,
    is_included_in_mandate_change BOOLEAN NOT NULL DEFAULT TRUE,
    mandate_scope_tag VARCHAR(64) NOT NULL DEFAULT 'STANDARD_CORPORATE_MANDATE',
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bank_accounts_customer ON bank_accounts(customer_id);

CREATE TABLE IF NOT EXISTS signatories (
    signatory_id VARCHAR(32) PRIMARY KEY,
    customer_id VARCHAR(32) NOT NULL REFERENCES corporate_customers(customer_id) ON DELETE CASCADE,
    full_name VARCHAR(128) NOT NULL,
    role_title VARCHAR(128) NOT NULL,
    signing_group VARCHAR(8) NOT NULL CHECK (signing_group IN ('A', 'B', 'C')),
    id_type VARCHAR(32) NOT NULL DEFAULT 'NRIC',
    id_number_masked VARCHAR(32) NOT NULL,
    nationality VARCHAR(8) NOT NULL DEFAULT 'SG',
    email VARCHAR(128) NOT NULL,
    mobile_masked VARCHAR(32) NOT NULL,
    auth_method VARCHAR(64) NOT NULL DEFAULT 'IDEAL_DIGITAL_TOKEN',
    ideal_status VARCHAR(64) NOT NULL DEFAULT 'TOKEN_ACTIVE',
    specimen_signature_status VARCHAR(64) NOT NULL DEFAULT 'VERIFIED_ON_FILE',
    specimen_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    individual_max_limit_sgd NUMERIC(18, 2),
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' CHECK (
        status IN ('ACTIVE', 'PENDING_ADDITION', 'PENDING_REVOCATION', 'REVOKED')
    ),
    effective_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    revocation_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signatories_customer_group ON signatories(customer_id, signing_group, status);

CREATE TABLE IF NOT EXISTS signing_rules (
    rule_id VARCHAR(32) PRIMARY KEY,
    customer_id VARCHAR(32) NOT NULL REFERENCES corporate_customers(customer_id) ON DELETE CASCADE,
    account_scope VARCHAR(64) NOT NULL DEFAULT 'ALL_SELECTED_ACCOUNTS',
    tier_order INT NOT NULL,
    tier_label VARCHAR(128) NOT NULL,
    min_amount_sgd NUMERIC(18, 2) NOT NULL DEFAULT 0.00,
    max_amount_sgd NUMERIC(18, 2),
    currency VARCHAR(8) NOT NULL DEFAULT 'SGD',
    rule_expression VARCHAR(128) NOT NULL,
    human_readable_rule TEXT NOT NULL,
    required_combinations JSONB NOT NULL,
    requires_board_resolution_above BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (customer_id, account_scope, tier_order)
);

CREATE INDEX IF NOT EXISTS idx_signing_rules_customer ON signing_rules(customer_id, tier_order);

CREATE TABLE IF NOT EXISTS board_resolutions (
    resolution_id VARCHAR(32) PRIMARY KEY,
    customer_id VARCHAR(32) NOT NULL REFERENCES corporate_customers(customer_id) ON DELETE CASCADE,
    resolution_ref VARCHAR(64) UNIQUE NOT NULL,
    format_type VARCHAR(64) NOT NULL CHECK (
        format_type IN ('STANDARD_BRC_09', 'CUSTOM_BOARD_MINUTES', 'LLP_PARTNERS_RESOLUTION')
    ),
    document_title VARCHAR(255) NOT NULL,
    meeting_date DATE NOT NULL,
    quorum_confirmed BOOLEAN NOT NULL DEFAULT TRUE,
    clause_checklist JSONB NOT NULL,
    extracted_text_summary TEXT NOT NULL,
    audit_score INT NOT NULL CHECK (audit_score BETWEEN 0 AND 100),
    audit_status VARCHAR(64) NOT NULL CHECK (
        audit_status IN ('COMPLIANT', 'ACTION_REQUIRED_CLAUSE_GAP', 'NON_COMPLIANT', 'PENDING_AUDIT')
    ),
    audit_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    audited_by VARCHAR(64) NOT NULL DEFAULT 'GEMINI_LIVE_COMPLIANCE_COPILOT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_board_resolutions_customer ON board_resolutions(customer_id);

CREATE TABLE IF NOT EXISTS mandate_change_applications (
    application_id VARCHAR(32) PRIMARY KEY,
    customer_id VARCHAR(32) NOT NULL REFERENCES corporate_customers(customer_id) ON DELETE CASCADE,
    resolution_id VARCHAR(32) REFERENCES board_resolutions(resolution_id) ON DELETE SET NULL,
    application_ref VARCHAR(64) UNIQUE NOT NULL,
    current_stage INT NOT NULL DEFAULT 1 CHECK (current_stage BETWEEN 1 AND 5),
    status VARCHAR(64) NOT NULL CHECK (
        status IN (
            'IN_REVIEW_DRAFT',
            'PENDING_BOARD_SIGNATURES',
            'PARTIALLY_SIGNED',
            'SUBMITTED_TO_CORE_BANKING',
            'APPROVED_AND_EFFECTIVE',
            'REJECTED'
        )
    ),
    summary_description TEXT NOT NULL,
    affected_account_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    mandate_diff_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    digisign_signers JSONB NOT NULL DEFAULT '[]'::jsonb,
    submitted_by VARCHAR(128) NOT NULL,
    submitted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_applications_customer ON mandate_change_applications(customer_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mandate_audit_logs (
    log_id BIGSERIAL PRIMARY KEY,
    customer_id VARCHAR(32) NOT NULL REFERENCES corporate_customers(customer_id) ON DELETE CASCADE,
    application_id VARCHAR(32) REFERENCES mandate_change_applications(application_id) ON DELETE SET NULL,
    event_type VARCHAR(64) NOT NULL,
    actor_name VARCHAR(128) NOT NULL,
    actor_channel VARCHAR(64) NOT NULL,
    target_entity VARCHAR(128),
    before_state JSONB,
    after_state JSONB,
    compliance_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_customer ON mandate_audit_logs(customer_id, created_at DESC);

CREATE TABLE IF NOT EXISTS active_workspace_state (
    workspace_id VARCHAR(32) PRIMARY KEY DEFAULT 'DEFAULT_WORKSPACE',
    active_customer_id VARCHAR(32) NOT NULL REFERENCES corporate_customers(customer_id),
    active_stage INT NOT NULL DEFAULT 1 CHECK (active_stage BETWEEN 1 AND 5),
    last_simulated_amount_sgd NUMERIC(18, 2) NOT NULL DEFAULT 150000.00,
    last_simulated_currency VARCHAR(8) NOT NULL DEFAULT 'SGD',
    last_tool_executed VARCHAR(64),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
