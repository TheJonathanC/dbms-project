-- =====================================================================
--  Banking & Relational Fraud Detection System
--  MCA 4141 - DBMS Lab | PostgreSQL 16
--
--  Run this one file top to bottom.
--    Part 1  Tables            21 tables in 5 modules
--    Part 2  Triggers          10 short trigger functions
--    Part 3  Procedures        1 fraud function + 6 procedures
--    Part 4  Dashboard views   14 views
--    Part 5  Seed data         ~10 rows per table (activity tables are
--                              filled by calling the real procedures)
--    Part 6  Demo queries
--
--  WARNING: drops and recreates every project table and routine.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid(), crypt()

DROP TABLE IF EXISTS
    system_notifications, dead_letter_queue, audit_logs,
    fraud_alerts, blocklisted_accounts, blocklisted_ips, fraud_rules,
    transactions, payment_gateways, transaction_types,
    upi_mappings, cards, beneficiaries, accounts, account_types, branches,
    device_fingerprints, login_history, kyc_documents, user_credentials, users
CASCADE;
DROP SEQUENCE IF EXISTS seq_fraud_config_version;

-- remove routines left over from older versions of this script
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig, p.prokind
               FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
                AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
    LOOP
        EXECUTE format('DROP %s IF EXISTS %s CASCADE',
                       CASE r.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END, r.sig);
    END LOOP;
END $$;


-- =====================================================================
-- PART 1 : TABLES
-- =====================================================================

-- ---------------------------------------------------------------------
-- MODULE 1 : IDENTITY & ACCESS
-- ---------------------------------------------------------------------

CREATE TABLE users (
    user_id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name         VARCHAR(100) NOT NULL,
    email             VARCHAR(255) NOT NULL UNIQUE,
    phone_number      VARCHAR(15)  NOT NULL UNIQUE,
    onboarding_status VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE',
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_users_email      CHECK (email ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    CONSTRAINT chk_users_phone      CHECK (phone_number ~ '^\+?[0-9]{10,14}$'),
    CONSTRAINT chk_users_onboarding CHECK (onboarding_status IN ('PENDING','ACTIVE','SUSPENDED','CLOSED'))
);

-- 1-to-1 with users, kept separate so password hashes never appear in profile queries
CREATE TABLE user_credentials (
    credential_id UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID         NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash VARCHAR(255) NOT NULL,
    pin_hash      VARCHAR(255) NOT NULL,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kyc_documents (
    kyc_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    pan_number     VARCHAR(20) UNIQUE,
    aadhaar_number VARCHAR(20) UNIQUE,
    is_verified    BOOLEAN     NOT NULL DEFAULT FALSE,
    verified_at    TIMESTAMPTZ,
    CONSTRAINT chk_kyc_pan     CHECK (pan_number     IS NULL OR pan_number     ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'),
    CONSTRAINT chk_kyc_aadhaar CHECK (aadhaar_number IS NULL OR aadhaar_number ~ '^[0-9]{12}$'),
    CONSTRAINT chk_kyc_any_id  CHECK (pan_number IS NOT NULL OR aadhaar_number IS NOT NULL)
);

-- multi-valued fact about a user (4NF): one row per registered device
CREATE TABLE device_fingerprints (
    device_id        UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    fingerprint_hash VARCHAR(255) NOT NULL,
    device_label     VARCHAR(100),
    is_trusted       BOOLEAN      NOT NULL DEFAULT FALSE,
    registered_at    TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at     TIMESTAMPTZ,
    CONSTRAINT uq_user_device UNIQUE (user_id, fingerprint_hash)
);

CREATE TABLE login_history (
    login_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    device_id      UUID        REFERENCES device_fingerprints(device_id) ON DELETE SET NULL,
    ip_address     VARCHAR(45) NOT NULL,
    is_success     BOOLEAN     NOT NULL,
    failure_reason VARCHAR(50),
    attempted_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_login_user_time ON login_history (user_id, attempted_at DESC);
CREATE INDEX idx_login_ip_time   ON login_history (ip_address, attempted_at DESC) WHERE NOT is_success;


-- ---------------------------------------------------------------------
-- MODULE 2 : CORE BANKING LEDGER
-- ---------------------------------------------------------------------

CREATE TABLE branches (
    branch_id   SERIAL       PRIMARY KEY,
    ifsc_code   VARCHAR(20)  NOT NULL UNIQUE,
    branch_name VARCHAR(100) NOT NULL,
    city        VARCHAR(50)  NOT NULL,
    CONSTRAINT chk_branch_ifsc CHECK (ifsc_code ~ '^[A-Z]{4}0[A-Z0-9]{6}$')
);

CREATE TABLE account_types (
    account_type_id SERIAL        PRIMARY KEY,
    type_name       VARCHAR(50)   NOT NULL UNIQUE,
    minimum_balance DECIMAL(15,2) NOT NULL DEFAULT 0.00 CHECK (minimum_balance >= 0)
);

CREATE TABLE accounts (
    account_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID          NOT NULL REFERENCES users(user_id)                 ON DELETE RESTRICT,
    branch_id       INT           NOT NULL REFERENCES branches(branch_id)            ON DELETE RESTRICT,
    account_type_id INT           NOT NULL REFERENCES account_types(account_type_id) ON DELETE RESTRICT,
    balance         DECIMAL(15,2) NOT NULL DEFAULT 0.00 CHECK (balance >= 0),
    daily_limit     DECIMAL(15,2) NOT NULL DEFAULT 100000.00 CHECK (daily_limit > 0),
    account_status  VARCHAR(20)   NOT NULL DEFAULT 'ACTIVE',
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_account_status CHECK (account_status IN ('ACTIVE','FROZEN','FLAGGED'))
);
CREATE INDEX idx_accounts_user   ON accounts (user_id);
CREATE INDEX idx_accounts_status ON accounts (account_status) WHERE account_status <> 'ACTIVE';

CREATE TABLE beneficiaries (
    beneficiary_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID         NOT NULL REFERENCES users(user_id)       ON DELETE CASCADE,
    account_id       UUID         NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
    beneficiary_name VARCHAR(100) NOT NULL,
    added_at         TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_beneficiary UNIQUE (user_id, account_id)
);

CREATE TABLE cards (
    card_id         UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID          NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    card_number     VARCHAR(19)   NOT NULL UNIQUE,          -- stored masked, never the full number
    expiry_date     DATE          NOT NULL,
    daily_pos_limit DECIMAL(15,2) NOT NULL DEFAULT 50000.00 CHECK (daily_pos_limit >= 0),
    card_status     VARCHAR(10)   NOT NULL DEFAULT 'ACTIVE',
    CONSTRAINT chk_card_status CHECK (card_status IN ('ACTIVE','BLOCKED','EXPIRED'))
);

CREATE TABLE upi_mappings (
    mapping_id UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID         NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    vpa        VARCHAR(100) NOT NULL UNIQUE,
    CONSTRAINT chk_vpa CHECK (vpa ~ '^[a-z0-9._-]{2,50}@[a-z]{2,20}$')
);


-- ---------------------------------------------------------------------
-- MODULE 3 : TRANSACTIONS
-- ---------------------------------------------------------------------

CREATE TABLE transaction_types (
    type_id          SERIAL      PRIMARY KEY,
    type_name        VARCHAR(20) NOT NULL UNIQUE,
    requires_gateway BOOLEAN     NOT NULL DEFAULT FALSE    -- NEFT / RTGS go through an external gateway
);

-- also the dependency-health registry (circuit-breaker state lives here)
CREATE TABLE payment_gateways (
    gateway_id           SERIAL       PRIMARY KEY,
    gateway_name         VARCHAR(100) NOT NULL UNIQUE,
    endpoint_url         VARCHAR(255) NOT NULL,
    priority             INT          NOT NULL DEFAULT 1,
    is_active            BOOLEAN      NOT NULL DEFAULT TRUE,
    health_status        VARCHAR(10)  NOT NULL DEFAULT 'HEALTHY',
    consecutive_failures INT          NOT NULL DEFAULT 0,
    last_success_at      TIMESTAMPTZ,
    last_failure_at      TIMESTAMPTZ,
    circuit_open_until   TIMESTAMPTZ,
    avg_latency_ms       INT,
    CONSTRAINT chk_gateway_health CHECK (health_status IN ('HEALTHY','DEGRADED','DOWN'))
);

CREATE TABLE transactions (
    transaction_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key     VARCHAR(64)   NOT NULL UNIQUE,
    request_fingerprint VARCHAR(32)   NOT NULL,     -- md5 of the request: catches a key reused for a different transfer
    sender_account_id   UUID          NOT NULL REFERENCES accounts(account_id)       ON DELETE RESTRICT,
    receiver_account_id UUID          NOT NULL REFERENCES accounts(account_id)       ON DELETE RESTRICT,
    type_id             INT           NOT NULL REFERENCES transaction_types(type_id) ON DELETE RESTRICT,
    gateway_id          INT           REFERENCES payment_gateways(gateway_id),
    amount              DECIMAL(15,2) NOT NULL CHECK (amount > 0),
    status              VARCHAR(20)   NOT NULL DEFAULT 'PENDING',
    status_reason       VARCHAR(100),
    client_ip           VARCHAR(45),
    device_hash         VARCHAR(255),
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at        TIMESTAMPTZ,
    CONSTRAINT chk_tx_status  CHECK (status IN ('PENDING','SUCCESS','REJECTED','QUARANTINED','FAILED')),
    CONSTRAINT chk_tx_parties CHECK (sender_account_id <> receiver_account_id)
);
CREATE INDEX idx_tx_sender_created   ON transactions (sender_account_id,   created_at DESC);
CREATE INDEX idx_tx_receiver_created ON transactions (receiver_account_id, created_at DESC);
CREATE INDEX idx_tx_open_states      ON transactions (status, created_at) WHERE status IN ('PENDING','QUARANTINED');


-- ---------------------------------------------------------------------
-- MODULE 4 : FRAUD DETECTION & SECURITY
-- ---------------------------------------------------------------------

CREATE TABLE fraud_rules (
    rule_id            SERIAL        PRIMARY KEY,
    rule_name          VARCHAR(100)  NOT NULL UNIQUE,
    description        VARCHAR(255),
    evaluation_metric  VARCHAR(50)   NOT NULL,
    threshold_value    DECIMAL(15,2) NOT NULL CHECK (threshold_value >= 0),
    applies_to_type_id INT           REFERENCES transaction_types(type_id),   -- NULL = every payment rail
    lookback_minutes   INT           NOT NULL DEFAULT 1440 CHECK (lookback_minutes > 0),
    window_start       TIME,                                                  -- optional time-of-day window (IST)
    window_end         TIME,
    action             VARCHAR(20)   NOT NULL DEFAULT 'BLOCK',
    is_active          BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_rule_action CHECK (action IN ('FLAG','QUARANTINE','BLOCK')),
    CONSTRAINT chk_rule_metric CHECK (evaluation_metric IN (
        'BLOCKLIST_MATCH','BLOCKLIST_IP','MAX_SINGLE_AMOUNT','NIGHT_HIGH_VALUE',
        'DAILY_LIMIT_EXCEEDED','MAX_TXN_PER_HOUR','NEW_DEVICE_HIGH_VALUE',
        'NEW_BENEFICIARY_HIGH_VALUE','RECENT_FAILED_LOGINS','STRUCTURING','RAPID_PASS_THROUGH'))
);

-- append-only: one row per (transaction, rule that fired)
CREATE TABLE fraud_alerts (
    alert_id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID         NOT NULL REFERENCES transactions(transaction_id) ON DELETE RESTRICT,
    rule_id        INT          NOT NULL REFERENCES fraud_rules(rule_id)         ON DELETE RESTRICT,
    action_taken   VARCHAR(20)  NOT NULL,
    details        TEXT,
    raised_at      TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    review_status  VARCHAR(20)  NOT NULL DEFAULT 'OPEN',
    reviewed_by    UUID         REFERENCES users(user_id),
    reviewed_at    TIMESTAMPTZ,
    review_notes   VARCHAR(255),
    CONSTRAINT uq_alert_tx_rule UNIQUE (transaction_id, rule_id),
    CONSTRAINT chk_alert_action CHECK (action_taken IN ('FLAG','QUARANTINE','BLOCK')),
    CONSTRAINT chk_alert_review CHECK (review_status IN ('OPEN','CONFIRMED','FALSE_POSITIVE'))
);
CREATE INDEX idx_alerts_rule_time ON fraud_alerts (rule_id, raised_at DESC);
CREATE INDEX idx_alerts_open      ON fraud_alerts (raised_at DESC) WHERE review_status = 'OPEN';

CREATE TABLE blocklisted_accounts (
    block_id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id              UUID         REFERENCES accounts(account_id) ON DELETE RESTRICT,
    external_account_number VARCHAR(50),
    reason                  VARCHAR(255) NOT NULL,
    blocked_at              TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at              TIMESTAMPTZ,                    -- NULL = permanent, otherwise TTL
    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT chk_block_target CHECK (account_id IS NOT NULL OR external_account_number IS NOT NULL)
);
CREATE UNIQUE INDEX uq_blocklist_account_active ON blocklisted_accounts (account_id)
    WHERE is_active AND account_id IS NOT NULL;
CREATE INDEX idx_blocklist_external ON blocklisted_accounts (external_account_number)
    WHERE external_account_number IS NOT NULL;

CREATE TABLE blocklisted_ips (
    ip_block_id UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_address  VARCHAR(45)  NOT NULL UNIQUE,
    reason      VARCHAR(255) NOT NULL,
    source      VARCHAR(20)  NOT NULL DEFAULT 'MANUAL',
    blocked_at  TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMPTZ,                                -- TTL for automatic blocks
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT chk_ip_source CHECK (source IN ('MANUAL','AUTO_BRUTE_FORCE','THREAT_FEED'))
);


-- ---------------------------------------------------------------------
-- MODULE 5 : AUDIT, LOGS & SUPPORT
-- ---------------------------------------------------------------------

-- immutable record of every balance change (written only by a trigger)
CREATE TABLE audit_logs (
    log_id         UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_seq      BIGINT        GENERATED ALWAYS AS IDENTITY UNIQUE,   -- strict ordering
    transaction_id UUID          REFERENCES transactions(transaction_id) ON DELETE RESTRICT,  -- NULL for deposits
    account_id     UUID          NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
    old_balance    DECIMAL(15,2) NOT NULL,
    new_balance    DECIMAL(15,2) NOT NULL,
    change_amount  DECIMAL(15,2) GENERATED ALWAYS AS (new_balance - old_balance) STORED,
    changed_by     VARCHAR(63)   NOT NULL DEFAULT current_user,
    recorded_at    TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX idx_audit_account_seq ON audit_logs (account_id, audit_seq);
CREATE INDEX idx_audit_tx          ON audit_logs (transaction_id) WHERE transaction_id IS NOT NULL;

-- work that failed and needs attention / replay
CREATE TABLE dead_letter_queue (
    dlq_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID         REFERENCES transactions(transaction_id) ON DELETE RESTRICT,
    task_type      VARCHAR(100) NOT NULL,
    payload        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    error_message  TEXT,
    retry_count    INT          NOT NULL DEFAULT 0,
    max_retries    INT          NOT NULL DEFAULT 3,
    next_retry_at  TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status         VARCHAR(12)  NOT NULL DEFAULT 'PENDING',
    failed_at      TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at    TIMESTAMPTZ,
    CONSTRAINT chk_dlq_status CHECK (status IN ('PENDING','REPLAYING','RESOLVED','ABANDONED'))
);
CREATE INDEX idx_dlq_due ON dead_letter_queue (next_retry_at) WHERE status = 'PENDING';

-- transactional outbox: written in the same commit as the money movement, sent later by a worker
CREATE TABLE system_notifications (
    notification_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    transaction_id    UUID         REFERENCES transactions(transaction_id) ON DELETE RESTRICT,
    notification_type VARCHAR(20)  NOT NULL,
    channel           VARCHAR(10)  NOT NULL DEFAULT 'SMS',
    message           VARCHAR(500) NOT NULL,
    delivery_status   VARCHAR(12)  NOT NULL DEFAULT 'PENDING',
    retry_count       INT          NOT NULL DEFAULT 0,
    max_retries       INT          NOT NULL DEFAULT 5,
    next_attempt_at   TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_by         VARCHAR(100),
    locked_until      TIMESTAMPTZ,                   -- worker lease
    last_error        TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at           TIMESTAMPTZ,
    CONSTRAINT chk_notif_status  CHECK (delivery_status IN ('PENDING','PROCESSING','SENT','DEAD')),
    CONSTRAINT chk_notif_channel CHECK (channel IN ('SMS','EMAIL','PUSH'))
);
CREATE UNIQUE INDEX uq_notif_dedupe ON system_notifications (transaction_id, user_id, notification_type)
    WHERE transaction_id IS NOT NULL;
CREATE INDEX idx_notif_due  ON system_notifications (next_attempt_at) WHERE delivery_status = 'PENDING';
CREATE INDEX idx_notif_user ON system_notifications (user_id, created_at DESC);


-- =====================================================================
-- PART 2 : TRIGGERS
-- =====================================================================

-- T1. Keep updated_at current
CREATE OR REPLACE FUNCTION fn_set_updated_at() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END $$;

CREATE TRIGGER trg_users_updated_at       BEFORE UPDATE ON users            FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_credentials_updated_at BEFORE UPDATE ON user_credentials FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_accounts_updated_at    BEFORE UPDATE ON accounts         FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_rules_updated_at       BEFORE UPDATE ON fraud_rules      FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();


-- T2. History tables are append-only
CREATE OR REPLACE FUNCTION fn_block_change() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed', TG_OP, TG_TABLE_NAME;
END $$;

CREATE TRIGGER trg_audit_logs_readonly    BEFORE UPDATE OR DELETE ON audit_logs   FOR EACH ROW EXECUTE FUNCTION fn_block_change();
CREATE TRIGGER trg_fraud_alerts_no_delete BEFORE DELETE           ON fraud_alerts FOR EACH ROW EXECUTE FUNCTION fn_block_change();
CREATE TRIGGER trg_transactions_no_delete BEFORE DELETE           ON transactions FOR EACH ROW EXECUTE FUNCTION fn_block_change();


-- T3. Every balance change is written to audit_logs.
--     The transfer procedures put the current transaction id in the
--     setting app.current_txn_id so the audit row can point back to it.
CREATE OR REPLACE FUNCTION fn_audit_balance() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_logs (transaction_id, account_id, old_balance, new_balance)
    VALUES (NULLIF(current_setting('app.current_txn_id', true), '')::uuid,
            NEW.account_id, OLD.balance, NEW.balance);
    RETURN NEW;
END $$;

CREATE TRIGGER trg_audit_balance
AFTER UPDATE OF balance ON accounts
FOR EACH ROW WHEN (OLD.balance IS DISTINCT FROM NEW.balance)
EXECUTE FUNCTION fn_audit_balance();


-- T4. A finished transaction can't change, and amount/parties never change
CREATE OR REPLACE FUNCTION fn_guard_transaction() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status NOT IN ('PENDING', 'QUARANTINED') THEN
        RAISE EXCEPTION 'Transaction is already % and cannot change', OLD.status;
    END IF;
    IF NEW.amount <> OLD.amount
       OR NEW.sender_account_id   <> OLD.sender_account_id
       OR NEW.receiver_account_id <> OLD.receiver_account_id THEN
        RAISE EXCEPTION 'Amount and parties of a transaction cannot be changed';
    END IF;
    IF NEW.status IN ('SUCCESS', 'REJECTED', 'FAILED') THEN
        NEW.completed_at := NOW();
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_guard_transaction
BEFORE UPDATE ON transactions
FOR EACH ROW EXECUTE FUNCTION fn_guard_transaction();


-- T5. Outbox: when a transfer reaches a result, queue the customer messages
CREATE OR REPLACE FUNCTION fn_notify_transaction() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO system_notifications (user_id, transaction_id, notification_type, message)
    SELECT a.user_id, NEW.transaction_id,
           CASE NEW.status WHEN 'SUCCESS' THEN 'DEBIT_ALERT'
                           WHEN 'QUARANTINED' THEN 'TXN_ON_HOLD'
                           ELSE 'TXN_REJECTED' END,
           format('Transfer of INR %s: %s%s', NEW.amount, NEW.status,
                  COALESCE(' (' || NEW.status_reason || ')', ''))
      FROM accounts a WHERE a.account_id = NEW.sender_account_id;

    IF NEW.status = 'SUCCESS' THEN
        INSERT INTO system_notifications (user_id, transaction_id, notification_type, message)
        SELECT a.user_id, NEW.transaction_id, 'CREDIT_ALERT',
               format('INR %s credited to your account', NEW.amount)
          FROM accounts a WHERE a.account_id = NEW.receiver_account_id;
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER trg_notify_transaction
AFTER UPDATE OF status ON transactions
FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status
                   AND NEW.status IN ('SUCCESS', 'QUARANTINED', 'REJECTED'))
EXECUTE FUNCTION fn_notify_transaction();


-- T6. 3 blocked/held transfers from one account within an hour -> account FLAGGED
CREATE OR REPLACE FUNCTION fn_flag_repeat_offender() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_sender UUID;
BEGIN
    SELECT sender_account_id INTO v_sender FROM transactions WHERE transaction_id = NEW.transaction_id;

    IF (SELECT COUNT(DISTINCT fa.transaction_id)
          FROM fraud_alerts fa
          JOIN transactions x ON x.transaction_id = fa.transaction_id
         WHERE x.sender_account_id = v_sender
           AND fa.action_taken IN ('BLOCK', 'QUARANTINE')
           AND fa.raised_at >= NOW() - INTERVAL '1 hour') >= 3 THEN
        UPDATE accounts SET account_status = 'FLAGGED'
         WHERE account_id = v_sender AND account_status = 'ACTIVE';
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER trg_flag_repeat_offender
AFTER INSERT ON fraud_alerts
FOR EACH ROW WHEN (NEW.action_taken IN ('BLOCK', 'QUARANTINE'))
EXECUTE FUNCTION fn_flag_repeat_offender();


-- T7. Tell the owner whenever their account status changes
CREATE OR REPLACE FUNCTION fn_notify_account_status() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO system_notifications (user_id, notification_type, message)
    VALUES (NEW.user_id, 'ACCOUNT_STATUS',
            format('Your account ending %s is now %s', right(NEW.account_id::text, 4), NEW.account_status));
    RETURN NULL;
END $$;

CREATE TRIGGER trg_notify_account_status
AFTER UPDATE OF account_status ON accounts
FOR EACH ROW WHEN (OLD.account_status IS DISTINCT FROM NEW.account_status)
EXECUTE FUNCTION fn_notify_account_status();


-- T8. Blocklisting one of our own accounts freezes it
CREATE OR REPLACE FUNCTION fn_freeze_blocklisted() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE accounts SET account_status = 'FROZEN'
     WHERE account_id = NEW.account_id AND account_status <> 'FROZEN';
    RETURN NULL;
END $$;

CREATE TRIGGER trg_freeze_blocklisted
AFTER INSERT ON blocklisted_accounts
FOR EACH ROW WHEN (NEW.account_id IS NOT NULL AND NEW.is_active)
EXECUTE FUNCTION fn_freeze_blocklisted();


-- T9. Brute force: 5 failed logins from one IP within 10 minutes -> block the IP for 1 hour
CREATE OR REPLACE FUNCTION fn_auto_block_ip() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT COUNT(*) FROM login_history
         WHERE ip_address = NEW.ip_address
           AND NOT is_success
           AND attempted_at >= NEW.attempted_at - INTERVAL '10 minutes') >= 5 THEN
        INSERT INTO blocklisted_ips (ip_address, reason, source, expires_at)
        VALUES (NEW.ip_address, '5+ failed logins within 10 minutes', 'AUTO_BRUTE_FORCE', NOW() + INTERVAL '1 hour')
        ON CONFLICT (ip_address) DO UPDATE
           SET is_active = TRUE, expires_at = EXCLUDED.expires_at
         WHERE blocklisted_ips.source = 'AUTO_BRUTE_FORCE';     -- never shorten a manual / feed block
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER trg_auto_block_ip
AFTER INSERT ON login_history
FOR EACH ROW WHEN (NOT NEW.is_success)
EXECUTE FUNCTION fn_auto_block_ip();


-- T10. KYC verified_at follows is_verified
CREATE OR REPLACE FUNCTION fn_kyc_verified_at() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.verified_at := CASE WHEN NEW.is_verified THEN COALESCE(NEW.verified_at, NOW()) END;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_kyc_verified_at
BEFORE INSERT OR UPDATE OF is_verified ON kyc_documents
FOR EACH ROW EXECUTE FUNCTION fn_kyc_verified_at();


-- =====================================================================
-- PART 3 : FUNCTIONS & PROCEDURES
-- =====================================================================

-- F1. Fraud engine.
--     Checks every active rule against one transaction, writes a
--     fraud_alerts row for each rule that fires, and returns the strongest
--     action (BLOCK > QUARANTINE > FLAG), or NULL if the transfer is clean.
--     Rules are rows in fraud_rules, so thresholds change with an UPDATE.
CREATE OR REPLACE FUNCTION fn_run_fraud_checks(p_tx_id UUID)
RETURNS VARCHAR LANGUAGE plpgsql AS $$
DECLARE
    v_tx    transactions%ROWTYPE;
    r       fraud_rules%ROWTYPE;
    v_user  UUID;                 -- owner of the sending account
    v_since TIMESTAMPTZ;          -- start of the rule's lookback window
    v_hit   BOOLEAN;
    v_worst VARCHAR;
    v_rank  CONSTANT TEXT[] := ARRAY['FLAG', 'QUARANTINE', 'BLOCK'];
BEGIN
    SELECT * INTO v_tx FROM transactions WHERE transaction_id = p_tx_id;
    SELECT user_id INTO v_user FROM accounts WHERE account_id = v_tx.sender_account_id;

    FOR r IN SELECT * FROM fraud_rules
              WHERE is_active
                AND (applies_to_type_id IS NULL OR applies_to_type_id = v_tx.type_id)
              ORDER BY rule_id
    LOOP
        v_since := NOW() - make_interval(mins => r.lookback_minutes);

        v_hit := CASE r.evaluation_metric
            WHEN 'BLOCKLIST_MATCH' THEN EXISTS (
                SELECT 1 FROM blocklisted_accounts b
                 WHERE b.account_id = v_tx.receiver_account_id AND b.is_active
                   AND (b.expires_at IS NULL OR b.expires_at > NOW()))

            WHEN 'BLOCKLIST_IP' THEN EXISTS (
                SELECT 1 FROM blocklisted_ips i
                 WHERE i.ip_address = v_tx.client_ip AND i.is_active
                   AND (i.expires_at IS NULL OR i.expires_at > NOW()))

            WHEN 'MAX_SINGLE_AMOUNT' THEN
                v_tx.amount > r.threshold_value

            WHEN 'NIGHT_HIGH_VALUE' THEN
                v_tx.amount > r.threshold_value
                AND (NOW() AT TIME ZONE 'Asia/Kolkata')::TIME BETWEEN r.window_start AND r.window_end

            WHEN 'DAILY_LIMIT_EXCEEDED' THEN
                v_tx.amount + (SELECT COALESCE(SUM(x.amount), 0) FROM transactions x
                                WHERE x.sender_account_id = v_tx.sender_account_id
                                  AND x.status = 'SUCCESS' AND x.created_at >= v_since)
                > (SELECT a.daily_limit FROM accounts a WHERE a.account_id = v_tx.sender_account_id)

            WHEN 'MAX_TXN_PER_HOUR' THEN
                (SELECT COUNT(*) FROM transactions x
                  WHERE x.sender_account_id = v_tx.sender_account_id
                    AND x.created_at >= v_since) > r.threshold_value

            WHEN 'NEW_DEVICE_HIGH_VALUE' THEN
                v_tx.amount > r.threshold_value AND v_tx.device_hash IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM device_fingerprints d
                                 WHERE d.user_id = v_user
                                   AND d.fingerprint_hash = v_tx.device_hash AND d.is_trusted)

            WHEN 'NEW_BENEFICIARY_HIGH_VALUE' THEN
                v_tx.amount > r.threshold_value
                AND NOT EXISTS (SELECT 1 FROM beneficiaries b
                                 WHERE b.user_id = v_user
                                   AND b.account_id = v_tx.receiver_account_id
                                   AND b.added_at <= v_since)

            WHEN 'RECENT_FAILED_LOGINS' THEN
                (SELECT COUNT(*) FROM login_history lh
                  WHERE lh.user_id = v_user AND NOT lh.is_success
                    AND lh.attempted_at >= v_since) >= r.threshold_value

            -- 3 or more big transfers just under the ceiling = splitting money to dodge it
            WHEN 'STRUCTURING' THEN
                v_tx.amount >= r.threshold_value
                AND (SELECT COUNT(*) FROM transactions x
                      WHERE x.sender_account_id = v_tx.sender_account_id
                        AND x.amount >= r.threshold_value AND x.status <> 'REJECTED'
                        AND x.created_at >= v_since) >= 3

            -- received a large sum and is now sending 90%+ of it straight out = mule
            WHEN 'RAPID_PASS_THROUGH' THEN
                (SELECT COALESCE(SUM(x.amount), 0) FROM transactions x
                  WHERE x.receiver_account_id = v_tx.sender_account_id
                    AND x.status = 'SUCCESS' AND x.created_at >= v_since)
                BETWEEN r.threshold_value AND v_tx.amount / 0.9

            ELSE FALSE
        END;

        IF COALESCE(v_hit, FALSE) THEN
            INSERT INTO fraud_alerts (transaction_id, rule_id, action_taken, details)
            VALUES (p_tx_id, r.rule_id, r.action, r.description);

            IF v_worst IS NULL OR array_position(v_rank, r.action) > array_position(v_rank, v_worst) THEN
                v_worst := r.action;
            END IF;
        END IF;
    END LOOP;

    RETURN v_worst;
END $$;


-- P1. Back-office deposit (opening balances). Audited by T3 with no transaction id.
CREATE OR REPLACE PROCEDURE sp_admin_deposit(p_account_id UUID, p_amount DECIMAL)
LANGUAGE plpgsql AS $$
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'Deposit amount must be positive';
    END IF;
    PERFORM set_config('app.current_txn_id', '', true);
    UPDATE accounts SET balance = balance + p_amount WHERE account_id = p_account_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Account % not found', p_account_id;
    END IF;
END $$;


-- P2. Money transfer
--   1 idempotency     same key -> earlier result, no second payment
--   2 validation
--   3 gateway         NEFT/RTGS need a gateway whose circuit is closed
--   4 record          transaction row as PENDING
--   5 lock            both accounts, always in id order (no deadlocks)
--   6 fraud engine    BLOCK -> REJECTED, QUARANTINE -> held for an analyst
--   7 status checks   frozen/flagged accounts, KYC, minimum balance
--   8 move money      debit + credit + SUCCESS commit together (atomic)
--   Lock timeout -> RETRYABLE (client retries with the same key).
--   Any other error -> everything rolls back, the failure goes to dead_letter_queue.
CREATE OR REPLACE PROCEDURE sp_execute_secure_transfer(
    p_idempotency_key VARCHAR,
    p_sender_id       UUID,
    p_receiver_id     UUID,
    p_amount          DECIMAL,
    p_type_id         INT,
    p_client_ip       VARCHAR DEFAULT NULL,
    p_device_hash     VARCHAR DEFAULT NULL,
    INOUT p_status    TEXT    DEFAULT NULL,
    INOUT p_tx_id     UUID    DEFAULT NULL)
LANGUAGE plpgsql AS $$
DECLARE
    v_fp         VARCHAR := md5(concat_ws('|', p_sender_id, p_receiver_id, round(p_amount, 2), p_type_id));
    v_old        transactions%ROWTYPE;
    v_gateway    INT;
    v_sender     RECORD;
    v_rcv_status VARCHAR;
    v_action     VARCHAR;
    v_reason     TEXT;
BEGIN
    PERFORM set_config('lock_timeout', '3s', true);

    -- 1. idempotency
    SELECT * INTO v_old FROM transactions WHERE idempotency_key = p_idempotency_key;
    IF FOUND THEN
        p_tx_id  := v_old.transaction_id;
        p_status := CASE WHEN v_old.request_fingerprint <> v_fp
                         THEN 'REJECTED: KEY_REUSED_FOR_DIFFERENT_TRANSFER'
                         ELSE 'DUPLICATE: ' || v_old.status END;
        RETURN;
    END IF;

    -- 2. validation
    IF p_amount IS NULL OR p_amount <= 0 OR p_sender_id = p_receiver_id THEN
        p_status := 'REJECTED: INVALID_REQUEST';
        RETURN;
    END IF;

    BEGIN
        -- 3. gateway
        IF (SELECT requires_gateway FROM transaction_types WHERE type_id = p_type_id) THEN
            SELECT gateway_id INTO v_gateway
              FROM payment_gateways
             WHERE is_active AND (circuit_open_until IS NULL OR circuit_open_until < NOW())
             ORDER BY priority LIMIT 1;
            IF v_gateway IS NULL THEN
                p_status := 'DEFERRED: NO_GATEWAY_AVAILABLE';
                RETURN;
            END IF;
        END IF;

        -- 4. record
        INSERT INTO transactions (idempotency_key, request_fingerprint, sender_account_id,
                                  receiver_account_id, type_id, gateway_id, amount, client_ip, device_hash)
        VALUES (p_idempotency_key, v_fp, p_sender_id, p_receiver_id, p_type_id, v_gateway,
                p_amount, p_client_ip, p_device_hash)
        RETURNING transaction_id INTO p_tx_id;

        PERFORM set_config('app.current_txn_id', p_tx_id::text, true);

        -- 5. lock
        PERFORM 1 FROM accounts
         WHERE account_id IN (p_sender_id, p_receiver_id)
         ORDER BY account_id
           FOR UPDATE;

        -- 6. fraud engine
        v_action := fn_run_fraud_checks(p_tx_id);

        IF v_action IN ('BLOCK', 'QUARANTINE') THEN
            SELECT fr.rule_name INTO v_reason
              FROM fraud_alerts fa JOIN fraud_rules fr ON fr.rule_id = fa.rule_id
             WHERE fa.transaction_id = p_tx_id AND fa.action_taken = v_action
             LIMIT 1;

            p_status := CASE v_action WHEN 'BLOCK' THEN 'REJECTED' ELSE 'QUARANTINED' END;
            UPDATE transactions SET status = p_status, status_reason = 'FRAUD: ' || v_reason
             WHERE transaction_id = p_tx_id;
            p_status := p_status || ': FRAUD: ' || v_reason;
            RETURN;
        END IF;

        -- 7. status checks
        SELECT a.balance, a.account_status, t.minimum_balance,
               EXISTS (SELECT 1 FROM kyc_documents k WHERE k.user_id = a.user_id AND k.is_verified) AS kyc_ok
          INTO v_sender
          FROM accounts a JOIN account_types t ON t.account_type_id = a.account_type_id
         WHERE a.account_id = p_sender_id;

        SELECT account_status INTO v_rcv_status FROM accounts WHERE account_id = p_receiver_id;

        v_reason := CASE
            WHEN v_sender.account_status <> 'ACTIVE' THEN 'SENDER_'   || v_sender.account_status
            WHEN v_rcv_status <> 'ACTIVE'            THEN 'RECEIVER_' || v_rcv_status
            WHEN NOT v_sender.kyc_ok                 THEN 'SENDER_KYC_NOT_VERIFIED'
            WHEN v_sender.balance - p_amount < v_sender.minimum_balance THEN 'INSUFFICIENT_FUNDS'
        END;

        IF v_reason IS NOT NULL THEN
            UPDATE transactions SET status = 'REJECTED', status_reason = v_reason
             WHERE transaction_id = p_tx_id;
            p_status := 'REJECTED: ' || v_reason;
            RETURN;
        END IF;

        -- 8. move the money
        UPDATE accounts SET balance = balance - p_amount WHERE account_id = p_sender_id;
        UPDATE accounts SET balance = balance + p_amount WHERE account_id = p_receiver_id;

        UPDATE transactions
           SET status = 'SUCCESS',
               status_reason = CASE WHEN v_action = 'FLAG' THEN 'FLAGGED_FOR_REVIEW' END
         WHERE transaction_id = p_tx_id;

        p_status := 'SUCCESS';

    EXCEPTION
        WHEN lock_not_available OR deadlock_detected THEN
            p_status := 'RETRYABLE: ' || SQLERRM;
            p_tx_id  := NULL;
        WHEN OTHERS THEN
            INSERT INTO dead_letter_queue (task_type, payload, error_message)
            VALUES ('TRANSFER_FAILURE',
                    jsonb_build_object('idempotency_key', p_idempotency_key, 'sender', p_sender_id,
                                       'receiver', p_receiver_id, 'amount', p_amount, 'type_id', p_type_id),
                    SQLERRM);
            p_status := 'FAILED: ' || SQLERRM;
            p_tx_id  := NULL;
    END;
END $$;


-- P3. Analyst decision on a quarantined transfer.
--     Approve -> money moves (balance re-checked under lock). Reject -> closed.
--     Every alert on that transaction gets the analyst's verdict.
CREATE OR REPLACE PROCEDURE sp_review_quarantined_transaction(
    p_tx_id      UUID,
    p_approve    BOOLEAN,
    p_analyst_id UUID,
    p_notes      TEXT DEFAULT NULL,
    INOUT p_status TEXT DEFAULT NULL)
LANGUAGE plpgsql AS $$
DECLARE
    v_tx transactions%ROWTYPE;
    v_ok BOOLEAN := FALSE;
BEGIN
    SELECT * INTO v_tx FROM transactions
     WHERE transaction_id = p_tx_id AND status = 'QUARANTINED'
       FOR UPDATE;
    IF NOT FOUND THEN
        p_status := 'ERROR: NOT_A_QUARANTINED_TRANSACTION';
        RETURN;
    END IF;

    IF p_approve THEN
        PERFORM 1 FROM accounts
         WHERE account_id IN (v_tx.sender_account_id, v_tx.receiver_account_id)
         ORDER BY account_id FOR UPDATE;

        SELECT a.balance - v_tx.amount >= t.minimum_balance INTO v_ok
          FROM accounts a JOIN account_types t ON t.account_type_id = a.account_type_id
         WHERE a.account_id = v_tx.sender_account_id;
    END IF;

    IF v_ok THEN
        PERFORM set_config('app.current_txn_id', p_tx_id::text, true);
        UPDATE accounts SET balance = balance - v_tx.amount WHERE account_id = v_tx.sender_account_id;
        UPDATE accounts SET balance = balance + v_tx.amount WHERE account_id = v_tx.receiver_account_id;
        UPDATE transactions SET status = 'SUCCESS', status_reason = 'RELEASED_BY_ANALYST'
         WHERE transaction_id = p_tx_id;
        p_status := 'SUCCESS: RELEASED_BY_ANALYST';
    ELSE
        p_status := CASE WHEN p_approve THEN 'INSUFFICIENT_FUNDS_AT_RELEASE' ELSE 'REJECTED_BY_ANALYST' END;
        UPDATE transactions SET status = 'REJECTED', status_reason = p_status
         WHERE transaction_id = p_tx_id;
        p_status := 'REJECTED: ' || p_status;
    END IF;

    UPDATE fraud_alerts
       SET review_status = CASE WHEN v_ok THEN 'FALSE_POSITIVE' ELSE 'CONFIRMED' END,
           reviewed_by   = p_analyst_id,
           reviewed_at   = NOW(),
           review_notes  = LEFT(p_notes, 255)
     WHERE transaction_id = p_tx_id;
END $$;


-- P4. Notification worker reports a delivery result.
--     Failure -> retry later with exponential backoff (2, 4, 8, 16 ... min) + random jitter.
--     Out of retries -> DEAD and copied to dead_letter_queue.
CREATE OR REPLACE PROCEDURE sp_mark_notification(p_id UUID, p_sent BOOLEAN, p_error TEXT DEFAULT NULL)
LANGUAGE plpgsql AS $$
DECLARE
    v_status VARCHAR;
BEGIN
    IF p_sent THEN
        UPDATE system_notifications
           SET delivery_status = 'SENT', sent_at = NOW(), last_error = NULL,
               locked_by = NULL, locked_until = NULL
         WHERE notification_id = p_id;
        RETURN;
    END IF;

    UPDATE system_notifications
       SET retry_count     = retry_count + 1,
           last_error      = p_error,
           locked_by       = NULL,
           locked_until    = NULL,
           delivery_status = CASE WHEN retry_count + 1 >= max_retries THEN 'DEAD' ELSE 'PENDING' END,
           next_attempt_at = NOW() + make_interval(mins => power(2, retry_count + 1)::int) * (0.5 + random())
     WHERE notification_id = p_id AND delivery_status NOT IN ('SENT', 'DEAD')
    RETURNING delivery_status INTO v_status;

    IF v_status = 'DEAD' THEN
        INSERT INTO dead_letter_queue (transaction_id, task_type, payload, error_message)
        SELECT transaction_id, 'SEND_' || channel || '_NOTIFICATION',
               jsonb_build_object('notification_id', notification_id, 'user_id', user_id), p_error
          FROM system_notifications WHERE notification_id = p_id;
    END IF;
END $$;


-- P5. Gateway health / circuit breaker.
--     2 failures in a row -> DEGRADED, 5 -> DOWN and the circuit opens for 5 minutes.
--     Any success -> HEALTHY again.
CREATE OR REPLACE PROCEDURE sp_record_gateway_result(p_gateway_id INT, p_success BOOLEAN, p_latency_ms INT DEFAULT NULL)
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE payment_gateways
       SET consecutive_failures = CASE WHEN p_success THEN 0 ELSE consecutive_failures + 1 END,
           health_status = CASE WHEN p_success                     THEN 'HEALTHY'
                                WHEN consecutive_failures + 1 >= 5 THEN 'DOWN'
                                WHEN consecutive_failures + 1 >= 2 THEN 'DEGRADED'
                                ELSE health_status END,
           circuit_open_until = CASE WHEN p_success                     THEN NULL
                                     WHEN consecutive_failures + 1 >= 5 THEN NOW() + INTERVAL '5 minutes'
                                     ELSE circuit_open_until END,
           last_success_at = CASE WHEN p_success THEN NOW() ELSE last_success_at END,
           last_failure_at = CASE WHEN p_success THEN last_failure_at ELSE NOW() END,
           avg_latency_ms  = CASE WHEN p_latency_ms IS NULL THEN avg_latency_ms
                                  ELSE (COALESCE(avg_latency_ms, p_latency_ms) * 4 + p_latency_ms) / 5 END
     WHERE gateway_id = p_gateway_id;
END $$;


-- P6. Housekeeping (run every minute from the Python scheduler or pg_cron)
CREATE OR REPLACE PROCEDURE sp_housekeeping()
LANGUAGE plpgsql AS $$
BEGIN
    -- TTL: expired blocks stop being active
    UPDATE blocklisted_ips      SET is_active = FALSE WHERE is_active AND expires_at <= NOW();
    UPDATE blocklisted_accounts SET is_active = FALSE WHERE is_active AND expires_at <= NOW();

    -- a worker died while holding a notification -> give it back to the queue
    UPDATE system_notifications
       SET delivery_status = 'PENDING', locked_by = NULL, locked_until = NULL
     WHERE delivery_status = 'PROCESSING' AND locked_until < NOW();

    -- a transfer stuck in PENDING never moved money (that commits together with SUCCESS)
    UPDATE transactions SET status = 'FAILED', status_reason = 'TIMEOUT_RECOVERY'
     WHERE status = 'PENDING' AND created_at < NOW() - INTERVAL '5 minutes';
END $$;


-- =====================================================================
-- PART 4 : DASHBOARD VIEWS
--   Overview page    v_dashboard_kpis, v_system_health, v_hourly_volume
--   Accounts page    v_account_overview, v_suspicious_accounts
--   Transactions     v_transaction_feed
--   Fraud page       v_fraud_alert_feed, v_fraud_rule_stats, v_quarantine_queue,
--                    v_login_security
--   Operations page  v_gateway_health, v_queue_health, v_audit_trail,
--                    v_ledger_mismatches
-- =====================================================================

-- Top-of-page number cards
CREATE OR REPLACE VIEW v_dashboard_kpis AS
SELECT
    (SELECT COUNT(*) FROM accounts)                                          AS total_accounts,
    (SELECT COALESCE(SUM(balance), 0) FROM accounts)                         AS total_deposits,
    (SELECT COUNT(*) FROM accounts WHERE account_status <> 'ACTIVE')         AS restricted_accounts,
    (SELECT COUNT(*) FROM transactions
      WHERE created_at >= NOW() - INTERVAL '24 hours')                       AS transactions_24h,
    (SELECT COUNT(*) FROM transactions
      WHERE status = 'SUCCESS' AND created_at >= NOW() - INTERVAL '24 hours') AS successful_24h,
    (SELECT COALESCE(SUM(amount), 0) FROM transactions
      WHERE status = 'SUCCESS' AND created_at >= NOW() - INTERVAL '24 hours') AS amount_moved_24h,
    (SELECT COUNT(*) FROM transactions WHERE status = 'QUARANTINED')         AS awaiting_review,
    (SELECT COUNT(*) FROM fraud_alerts
      WHERE raised_at >= NOW() - INTERVAL '24 hours')                        AS fraud_alerts_24h,
    (SELECT COUNT(*) FROM fraud_alerts WHERE review_status = 'OPEN')         AS open_alerts,
    (SELECT COUNT(*) FROM blocklisted_ips
      WHERE is_active AND (expires_at IS NULL OR expires_at > NOW()))        AS blocked_ips,
    (SELECT COUNT(*) FROM dead_letter_queue WHERE status = 'PENDING')        AS dead_letters_pending;

-- Readiness / graceful-degradation banner
CREATE OR REPLACE VIEW v_system_health AS
SELECT
    CASE WHEN rules = 0 OR stuck > 0 THEN 'NOT_READY'
         WHEN gw_up = 0              THEN 'LIMITED'     -- UPI/IMPS work, NEFT/RTGS deferred
         WHEN gw_up < gw_total OR dlq > 0 THEN 'DEGRADED'
         ELSE 'HEALTHY' END AS system_mode,
    rules  AS active_fraud_rules,
    gw_up  AS gateways_up,
    gw_total AS gateways_total,
    stuck  AS stuck_pending_transactions,
    dlq    AS dead_letters_pending,
    NOW()  AS checked_at
FROM (SELECT
        (SELECT COUNT(*) FROM fraud_rules WHERE is_active) AS rules,
        (SELECT COUNT(*) FROM payment_gateways
          WHERE is_active AND (circuit_open_until IS NULL OR circuit_open_until < NOW())) AS gw_up,
        (SELECT COUNT(*) FROM payment_gateways) AS gw_total,
        (SELECT COUNT(*) FROM transactions
          WHERE status = 'PENDING' AND created_at < NOW() - INTERVAL '5 minutes') AS stuck,
        (SELECT COUNT(*) FROM dead_letter_queue WHERE status = 'PENDING') AS dlq) s;

-- Chart: transactions per hour by outcome
CREATE OR REPLACE VIEW v_hourly_volume AS
SELECT date_trunc('hour', created_at)                           AS hour,
       COUNT(*)                                                 AS total,
       COUNT(*) FILTER (WHERE status = 'SUCCESS')               AS success,
       COUNT(*) FILTER (WHERE status = 'REJECTED')              AS rejected,
       COUNT(*) FILTER (WHERE status = 'QUARANTINED')           AS quarantined,
       COUNT(*) FILTER (WHERE status = 'FAILED')                AS failed,
       COALESCE(SUM(amount) FILTER (WHERE status = 'SUCCESS'), 0) AS amount_moved
  FROM transactions
 GROUP BY 1
 ORDER BY 1 DESC;

-- Accounts table
CREATE OR REPLACE VIEW v_account_overview AS
SELECT a.account_id,
       u.full_name,
       u.email,
       b.branch_name,
       b.city,
       t.type_name,
       a.balance,
       a.daily_limit,
       a.account_status,
       EXISTS (SELECT 1 FROM kyc_documents k WHERE k.user_id = u.user_id AND k.is_verified) AS kyc_verified,
       (SELECT COALESCE(SUM(x.amount), 0) FROM transactions x
         WHERE x.sender_account_id = a.account_id AND x.status = 'SUCCESS'
           AND x.created_at >= NOW() - INTERVAL '24 hours')                                AS sent_last_24h,
       a.created_at
  FROM accounts a
  JOIN users u         ON u.user_id = a.user_id
  JOIN branches b      ON b.branch_id = a.branch_id
  JOIN account_types t ON t.account_type_id = a.account_type_id;

-- Accounts ranked by risk
CREATE OR REPLACE VIEW v_suspicious_accounts AS
SELECT a.account_id,
       u.full_name,
       a.account_status,
       COUNT(DISTINCT fa.transaction_id) FILTER (WHERE fa.action_taken IN ('BLOCK','QUARANTINE')) AS blocked_or_held,
       COUNT(DISTINCT fa.transaction_id) FILTER (WHERE fa.action_taken = 'FLAG')                  AS flagged,
       (SELECT COUNT(*) FROM login_history lh
         WHERE lh.user_id = u.user_id AND NOT lh.is_success
           AND lh.attempted_at >= NOW() - INTERVAL '24 hours')                                    AS failed_logins_24h,
       COUNT(DISTINCT fa.transaction_id) FILTER (WHERE fa.action_taken IN ('BLOCK','QUARANTINE')) * 3
         + COUNT(DISTINCT fa.transaction_id) FILTER (WHERE fa.action_taken = 'FLAG')
         + (SELECT COUNT(*) FROM login_history lh
             WHERE lh.user_id = u.user_id AND NOT lh.is_success
               AND lh.attempted_at >= NOW() - INTERVAL '24 hours')
         + CASE WHEN a.account_status <> 'ACTIVE' THEN 5 ELSE 0 END                               AS risk_score
  FROM accounts a
  JOIN users u ON u.user_id = a.user_id
  LEFT JOIN transactions x ON x.sender_account_id = a.account_id
                          AND x.created_at >= NOW() - INTERVAL '24 hours'
  LEFT JOIN fraud_alerts fa ON fa.transaction_id = x.transaction_id
 GROUP BY a.account_id, u.user_id, u.full_name, a.account_status
 ORDER BY risk_score DESC;

-- Live transaction list
CREATE OR REPLACE VIEW v_transaction_feed AS
SELECT x.transaction_id,
       x.created_at,
       tt.type_name,
       su.full_name AS sender_name,
       ru.full_name AS receiver_name,
       x.amount,
       x.status,
       x.status_reason,
       g.gateway_name,
       x.client_ip,
       x.completed_at
  FROM transactions x
  JOIN transaction_types tt ON tt.type_id = x.type_id
  JOIN accounts sa ON sa.account_id = x.sender_account_id
  JOIN users su    ON su.user_id = sa.user_id
  JOIN accounts ra ON ra.account_id = x.receiver_account_id
  JOIN users ru    ON ru.user_id = ra.user_id
  LEFT JOIN payment_gateways g ON g.gateway_id = x.gateway_id
 ORDER BY x.created_at DESC;

-- Everything the fraud engine caught
CREATE OR REPLACE VIEW v_fraud_alert_feed AS
SELECT fa.alert_id,
       fa.raised_at,
       fr.rule_name,
       fr.evaluation_metric,
       fa.action_taken,
       fa.review_status,
       x.transaction_id,
       x.amount,
       x.status     AS transaction_status,
       su.full_name AS sender_name,
       ru.full_name AS receiver_name,
       an.full_name AS reviewed_by,
       fa.review_notes
  FROM fraud_alerts fa
  JOIN fraud_rules fr  ON fr.rule_id = fa.rule_id
  JOIN transactions x  ON x.transaction_id = fa.transaction_id
  JOIN accounts sa     ON sa.account_id = x.sender_account_id
  JOIN users su        ON su.user_id = sa.user_id
  JOIN accounts ra     ON ra.account_id = x.receiver_account_id
  JOIN users ru        ON ru.user_id = ra.user_id
  LEFT JOIN users an   ON an.user_id = fa.reviewed_by
 ORDER BY fa.raised_at DESC;

-- How useful is each rule (hits, confirmed vs false positives)
CREATE OR REPLACE VIEW v_fraud_rule_stats AS
SELECT fr.rule_id,
       fr.rule_name,
       fr.evaluation_metric,
       fr.action,
       fr.threshold_value,
       fr.is_active,
       COUNT(fa.alert_id)                                                       AS total_hits,
       COUNT(fa.alert_id) FILTER (WHERE fa.raised_at >= NOW() - INTERVAL '24 hours') AS hits_24h,
       COUNT(fa.alert_id) FILTER (WHERE fa.review_status = 'OPEN')              AS open,
       COUNT(fa.alert_id) FILTER (WHERE fa.review_status = 'CONFIRMED')         AS confirmed,
       COUNT(fa.alert_id) FILTER (WHERE fa.review_status = 'FALSE_POSITIVE')    AS false_positives
  FROM fraud_rules fr
  LEFT JOIN fraud_alerts fa ON fa.rule_id = fr.rule_id
 GROUP BY fr.rule_id
 ORDER BY total_hits DESC, fr.rule_id;

-- Analyst work queue: held transfers waiting for a decision
CREATE OR REPLACE VIEW v_quarantine_queue AS
SELECT x.transaction_id,
       x.created_at,
       su.full_name AS sender_name,
       ru.full_name AS receiver_name,
       x.amount,
       x.status_reason,
       string_agg(fr.rule_name, ', ' ORDER BY fr.rule_id)               AS rules_triggered,
       round(EXTRACT(EPOCH FROM NOW() - x.created_at) / 60)            AS waiting_minutes
  FROM transactions x
  JOIN accounts sa      ON sa.account_id = x.sender_account_id
  JOIN users su         ON su.user_id = sa.user_id
  JOIN accounts ra      ON ra.account_id = x.receiver_account_id
  JOIN users ru         ON ru.user_id = ra.user_id
  JOIN fraud_alerts fa  ON fa.transaction_id = x.transaction_id
  JOIN fraud_rules fr   ON fr.rule_id = fa.rule_id
 WHERE x.status = 'QUARANTINED'
 GROUP BY x.transaction_id, su.full_name, ru.full_name
 ORDER BY x.created_at;

-- Failed logins per IP in the last 24h, and whether the IP is blocked
CREATE OR REPLACE VIEW v_login_security AS
SELECT lh.ip_address,
       COUNT(*) FILTER (WHERE NOT lh.is_success)                AS failed_attempts,
       COUNT(*) FILTER (WHERE lh.is_success)                    AS successful_logins,
       COUNT(DISTINCT lh.user_id)                               AS users_targeted,
       MAX(lh.attempted_at)                                     AS last_attempt,
       EXISTS (SELECT 1 FROM blocklisted_ips i
                WHERE i.ip_address = lh.ip_address AND i.is_active
                  AND (i.expires_at IS NULL OR i.expires_at > NOW())) AS is_blocked
  FROM login_history lh
 WHERE lh.attempted_at >= NOW() - INTERVAL '24 hours'
 GROUP BY lh.ip_address
 ORDER BY failed_attempts DESC;

CREATE OR REPLACE VIEW v_gateway_health AS
SELECT gateway_id, gateway_name, priority, is_active, health_status,
       consecutive_failures, avg_latency_ms, last_success_at, last_failure_at,
       COALESCE(circuit_open_until > NOW(), FALSE) AS circuit_open,
       circuit_open_until
  FROM payment_gateways
 ORDER BY priority;

-- Outbox and dead-letter queue depth
CREATE OR REPLACE VIEW v_queue_health AS
SELECT 'NOTIFICATIONS' AS queue, delivery_status AS status, COUNT(*) AS items, MIN(created_at) AS oldest
  FROM system_notifications GROUP BY delivery_status
UNION ALL
SELECT 'DEAD_LETTER', status, COUNT(*), MIN(failed_at)
  FROM dead_letter_queue GROUP BY status;

-- Balance history with names
CREATE OR REPLACE VIEW v_audit_trail AS
SELECT al.audit_seq,
       al.recorded_at,
       u.full_name,
       al.account_id,
       al.old_balance,
       al.new_balance,
       al.change_amount,
       COALESCE(tt.type_name, 'DEPOSIT') AS source,
       al.transaction_id,
       al.changed_by
  FROM audit_logs al
  JOIN accounts a ON a.account_id = al.account_id
  JOIN users u    ON u.user_id = a.user_id
  LEFT JOIN transactions x      ON x.transaction_id = al.transaction_id
  LEFT JOIN transaction_types tt ON tt.type_id = x.type_id
 ORDER BY al.audit_seq DESC;

-- Consistency check: balance must equal the last audit entry. Healthy = 0 rows.
CREATE OR REPLACE VIEW v_ledger_mismatches AS
SELECT a.account_id, a.balance, last.new_balance AS last_audited_balance
  FROM accounts a
  LEFT JOIN LATERAL (SELECT al.new_balance FROM audit_logs al
                      WHERE al.account_id = a.account_id
                      ORDER BY al.audit_seq DESC LIMIT 1) last ON TRUE
 WHERE a.balance <> COALESCE(last.new_balance, 0);


-- =====================================================================
-- PART 5 : SEED DATA
--   Fixed, readable UUIDs:
--     users     a0000000-0000-0000-0000-0000000000NN
--     accounts  b0000000-0000-0000-0000-0000000000NN
--   Demo password for everyone: Passw0rd!   PIN: 4821
-- =====================================================================

-- ---------------- Module 1 ----------------
INSERT INTO users (user_id, full_name, email, phone_number) VALUES
 ('a0000000-0000-0000-0000-000000000001', 'Aarav Sharma',    'aarav.sharma@example.com',    '9876500001'),
 ('a0000000-0000-0000-0000-000000000002', 'Priya Nair',      'priya.nair@example.com',      '9876500002'),
 ('a0000000-0000-0000-0000-000000000003', 'Rohan Mehta',     'rohan.mehta@example.com',     '9876500003'),
 ('a0000000-0000-0000-0000-000000000004', 'Ananya Kulkarni', 'ananya.kulkarni@example.com', '9876500004'),
 ('a0000000-0000-0000-0000-000000000005', 'Vikram Rao',      'vikram.rao@example.com',      '9876500005'),  -- money mule
 ('a0000000-0000-0000-0000-000000000006', 'Neha Iyer',       'neha.iyer@mitbank.example',   '9876500006'),  -- fraud analyst
 ('a0000000-0000-0000-0000-000000000007', 'Karthik Reddy',   'karthik.reddy@example.com',   '9876500007'),
 ('a0000000-0000-0000-0000-000000000008', 'Sneha Patil',     'sneha.patil@example.com',     '9876500008'),
 ('a0000000-0000-0000-0000-000000000009', 'Arjun Menon',     'arjun.menon@example.com',     '9876500009'),
 ('a0000000-0000-0000-0000-000000000010', 'Meera Joshi',     'meera.joshi@example.com',     '9876500010');

INSERT INTO user_credentials (user_id, password_hash, pin_hash)
SELECT user_id, crypt('Passw0rd!', gen_salt('bf')), crypt('4821', gen_salt('bf')) FROM users;

INSERT INTO kyc_documents (user_id, pan_number, aadhaar_number, is_verified) VALUES
 ('a0000000-0000-0000-0000-000000000001', 'ABCPS1234A', '234567890121', TRUE),
 ('a0000000-0000-0000-0000-000000000002', 'BCDPN2345B', '345678901232', TRUE),
 ('a0000000-0000-0000-0000-000000000003', 'CDEPM3456C', '456789012343', TRUE),
 ('a0000000-0000-0000-0000-000000000004', 'DEFPK4567D', '567890123454', TRUE),
 ('a0000000-0000-0000-0000-000000000005', 'EFGPR5678E', '678901234565', TRUE),
 ('a0000000-0000-0000-0000-000000000006', 'FGHPI6789F', '789012345676', TRUE),
 ('a0000000-0000-0000-0000-000000000007', 'GHIPR7890G', '890123456787', TRUE),
 ('a0000000-0000-0000-0000-000000000008', 'HIJPP8901H', '901234567898', TRUE),
 ('a0000000-0000-0000-0000-000000000009', 'IJKPM9012J', '112345678909', TRUE),
 ('a0000000-0000-0000-0000-000000000010', 'JKLPJ0123K', '223456789010', FALSE);  -- KYC still pending

INSERT INTO device_fingerprints (user_id, fingerprint_hash, device_label, is_trusted, registered_at, last_seen_at) VALUES
 ('a0000000-0000-0000-0000-000000000001', 'fp-aarav-iphone',      'Aarav iPhone',        TRUE,  NOW() - INTERVAL '120 days', NOW() - INTERVAL '1 day'),
 ('a0000000-0000-0000-0000-000000000001', 'fp-aarav-new-laptop',  'Unknown laptop',      FALSE, NOW() - INTERVAL '1 hour',   NOW() - INTERVAL '1 hour'),
 ('a0000000-0000-0000-0000-000000000002', 'fp-priya-pixel',       'Priya Pixel',         TRUE,  NOW() - INTERVAL '90 days',  NOW() - INTERVAL '2 hours'),
 ('a0000000-0000-0000-0000-000000000003', 'fp-rohan-galaxy',      'Rohan Galaxy',        TRUE,  NOW() - INTERVAL '60 days',  NOW() - INTERVAL '3 days'),
 ('a0000000-0000-0000-0000-000000000004', 'fp-ananya-oneplus',    'Ananya OnePlus',      TRUE,  NOW() - INTERVAL '45 days',  NOW() - INTERVAL '5 hours'),
 ('a0000000-0000-0000-0000-000000000005', 'fp-vikram-burner',     'Unregistered handset',FALSE, NOW() - INTERVAL '3 days',   NOW() - INTERVAL '1 hour'),
 ('a0000000-0000-0000-0000-000000000007', 'fp-karthik-iphone',    'Karthik iPhone',      TRUE,  NOW() - INTERVAL '200 days', NOW() - INTERVAL '6 hours'),
 ('a0000000-0000-0000-0000-000000000008', 'fp-sneha-macbook',     'Sneha MacBook',       TRUE,  NOW() - INTERVAL '30 days',  NOW() - INTERVAL '1 day'),
 ('a0000000-0000-0000-0000-000000000009', 'fp-arjun-redmi',       'Arjun Redmi',         TRUE,  NOW() - INTERVAL '75 days',  NOW() - INTERVAL '2 days'),
 ('a0000000-0000-0000-0000-000000000010', 'fp-meera-ipad',        'Meera iPad',          TRUE,  NOW() - INTERVAL '10 days',  NOW() - INTERVAL '4 days');

-- 7 normal logins + a brute-force burst on Arjun's profile from one IP
-- (the burst fires T9, which auto-blocks 203.0.113.50 for an hour)
INSERT INTO login_history (user_id, ip_address, is_success, failure_reason, attempted_at) VALUES
 ('a0000000-0000-0000-0000-000000000001', '106.51.20.11',  TRUE,  NULL, NOW() - INTERVAL '1 day'),
 ('a0000000-0000-0000-0000-000000000002', '106.51.20.12',  TRUE,  NULL, NOW() - INTERVAL '20 hours'),
 ('a0000000-0000-0000-0000-000000000003', '106.51.20.13',  TRUE,  NULL, NOW() - INTERVAL '18 hours'),
 ('a0000000-0000-0000-0000-000000000004', '49.207.55.80',  TRUE,  NULL, NOW() - INTERVAL '6 hours'),
 ('a0000000-0000-0000-0000-000000000007', '117.200.10.4',  TRUE,  NULL, NOW() - INTERVAL '5 hours'),
 ('a0000000-0000-0000-0000-000000000008', '157.45.88.21',  TRUE,  NULL, NOW() - INTERVAL '3 hours'),
 ('a0000000-0000-0000-0000-000000000010', '106.51.20.19',  TRUE,  NULL, NOW() - INTERVAL '2 hours'),
 ('a0000000-0000-0000-0000-000000000009', '203.0.113.50',  FALSE, 'INVALID_PASSWORD', NOW() - INTERVAL '9 minutes'),
 ('a0000000-0000-0000-0000-000000000009', '203.0.113.50',  FALSE, 'INVALID_PASSWORD', NOW() - INTERVAL '8 minutes'),
 ('a0000000-0000-0000-0000-000000000009', '203.0.113.50',  FALSE, 'INVALID_PASSWORD', NOW() - INTERVAL '7 minutes'),
 ('a0000000-0000-0000-0000-000000000009', '203.0.113.50',  FALSE, 'INVALID_PASSWORD', NOW() - INTERVAL '6 minutes'),
 ('a0000000-0000-0000-0000-000000000009', '203.0.113.50',  FALSE, 'INVALID_PASSWORD', NOW() - INTERVAL '5 minutes');

-- ---------------- Module 2 ----------------
INSERT INTO branches (ifsc_code, branch_name, city) VALUES       -- branch_id 1..5
 ('MITB0000101', 'Manipal Main Branch', 'Manipal'),
 ('MITB0000102', 'Udupi City',          'Udupi'),
 ('MITB0000201', 'Mangaluru Hampankatta','Mangaluru'),
 ('MITB0000202', 'Hubballi Central',    'Hubballi'),
 ('MITB0000301', 'Bengaluru Koramangala','Bengaluru');

INSERT INTO account_types (type_name, minimum_balance) VALUES     -- 1 Savings, 2 Current, 3 Salary
 ('Savings', 1000.00), ('Current', 5000.00), ('Salary', 0.00);

-- created empty, funded below through the audited deposit procedure
INSERT INTO accounts (account_id, user_id, branch_id, account_type_id, daily_limit) VALUES
 ('b0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001', 1, 1, 100000),  -- Aarav
 ('b0000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000002', 1, 1, 100000),  -- Priya
 ('b0000000-0000-0000-0000-000000000003', 'a0000000-0000-0000-0000-000000000003', 2, 2, 500000),  -- Rohan (current)
 ('b0000000-0000-0000-0000-000000000004', 'a0000000-0000-0000-0000-000000000004', 4, 3, 500000),  -- Ananya (salary)
 ('b0000000-0000-0000-0000-000000000005', 'a0000000-0000-0000-0000-000000000005', 4, 1, 100000),  -- Vikram (mule)
 ('b0000000-0000-0000-0000-000000000006', 'a0000000-0000-0000-0000-000000000007', 3, 1, 100000),  -- Karthik
 ('b0000000-0000-0000-0000-000000000007', 'a0000000-0000-0000-0000-000000000008', 5, 2, 500000),  -- Sneha (current)
 ('b0000000-0000-0000-0000-000000000008', 'a0000000-0000-0000-0000-000000000009', 2, 1, 100000),  -- Arjun
 ('b0000000-0000-0000-0000-000000000009', 'a0000000-0000-0000-0000-000000000010', 5, 3, 100000),  -- Meera (salary)
 ('b0000000-0000-0000-0000-000000000010', 'a0000000-0000-0000-0000-000000000004', 4, 1, 100000);  -- Ananya (savings)

CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000001',  75000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000002', 125000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000003', 400000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000004', 400000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000005',   2000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000006',  60000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000007', 300000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000008',  45000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000009', 150000);
CALL sp_admin_deposit('b0000000-0000-0000-0000-000000000010',  50000);

-- all added 30 days ago, so they count as established payees
INSERT INTO beneficiaries (user_id, account_id, beneficiary_name, added_at) VALUES
 ('a0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000002', 'Priya Nair',      NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000003', 'Rohan Mehta',     NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000001', 'Aarav Sharma',    NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000005', 'Vikram Rao',      NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000004', 'b0000000-0000-0000-0000-000000000001', 'Aarav Sharma',    NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000004', 'b0000000-0000-0000-0000-000000000002', 'Priya Nair',      NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000007', 'b0000000-0000-0000-0000-000000000007', 'Sneha Patil',     NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000008', 'b0000000-0000-0000-0000-000000000006', 'Karthik Reddy',   NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000009', 'b0000000-0000-0000-0000-000000000001', 'Aarav Sharma',    NOW() - INTERVAL '30 days'),
 ('a0000000-0000-0000-0000-000000000010', 'b0000000-0000-0000-0000-000000000003', 'Rohan Mehta',     NOW() - INTERVAL '30 days');

INSERT INTO cards (account_id, card_number, expiry_date, daily_pos_limit, card_status) VALUES
 ('b0000000-0000-0000-0000-000000000001', 'XXXX XXXX XXXX 4821', '2029-08-31',  50000, 'ACTIVE'),
 ('b0000000-0000-0000-0000-000000000002', 'XXXX XXXX XXXX 7710', '2028-11-30',  75000, 'ACTIVE'),
 ('b0000000-0000-0000-0000-000000000003', 'XXXX XXXX XXXX 3305', '2030-02-28', 100000, 'ACTIVE'),
 ('b0000000-0000-0000-0000-000000000004', 'XXXX XXXX XXXX 9162', '2029-05-31',  50000, 'ACTIVE'),
 ('b0000000-0000-0000-0000-000000000005', 'XXXX XXXX XXXX 0013', '2027-01-31',  25000, 'BLOCKED'),
 ('b0000000-0000-0000-0000-000000000006', 'XXXX XXXX XXXX 5540', '2028-07-31',  50000, 'ACTIVE'),
 ('b0000000-0000-0000-0000-000000000007', 'XXXX XXXX XXXX 6678', '2030-10-31', 100000, 'ACTIVE'),
 ('b0000000-0000-0000-0000-000000000008', 'XXXX XXXX XXXX 2219', '2025-12-31',  50000, 'EXPIRED'),
 ('b0000000-0000-0000-0000-000000000009', 'XXXX XXXX XXXX 8834', '2029-03-31',  50000, 'ACTIVE'),
 ('b0000000-0000-0000-0000-000000000010', 'XXXX XXXX XXXX 1197', '2028-09-30',  50000, 'ACTIVE');

INSERT INTO upi_mappings (account_id, vpa) VALUES
 ('b0000000-0000-0000-0000-000000000001', 'aarav@mitbank'),
 ('b0000000-0000-0000-0000-000000000002', 'priya@mitbank'),
 ('b0000000-0000-0000-0000-000000000003', 'rohan.biz@mitbank'),
 ('b0000000-0000-0000-0000-000000000004', 'ananya@mitbank'),
 ('b0000000-0000-0000-0000-000000000005', 'vikram@mitbank'),
 ('b0000000-0000-0000-0000-000000000006', 'karthik@mitbank'),
 ('b0000000-0000-0000-0000-000000000007', 'sneha.store@mitbank'),
 ('b0000000-0000-0000-0000-000000000008', 'arjun@mitbank'),
 ('b0000000-0000-0000-0000-000000000009', 'meera@mitbank'),
 ('b0000000-0000-0000-0000-000000000010', 'ananya.savings@mitbank');

-- ---------------- Module 3 ----------------
INSERT INTO transaction_types (type_name, requires_gateway) VALUES   -- 1 UPI, 2 IMPS, 3 NEFT, 4 RTGS
 ('UPI', FALSE), ('IMPS', FALSE), ('NEFT', TRUE), ('RTGS', TRUE);

INSERT INTO payment_gateways (gateway_name, endpoint_url, priority) VALUES   -- gateway_id 1..3
 ('NPCI Switch Primary', 'https://api.npci-sim.example/v1/primary', 1),
 ('NPCI Switch Backup',  'https://api.npci-sim.example/v1/backup',  2),
 ('Legacy Clearing Hub', 'https://clearing.legacy-sim.example/v2',  3);

-- ---------------- Module 4 ----------------
INSERT INTO fraud_rules (rule_name, description, evaluation_metric, threshold_value,
                         lookback_minutes, window_start, window_end, action) VALUES
 ('Mule Account Blocklist Match',      'Receiver account is on the blocklist',
     'BLOCKLIST_MATCH',                0, 1440, NULL,    NULL,    'BLOCK'),
 ('Blocklisted IP Origin',             'Request came from a blocklisted IP address',
     'BLOCKLIST_IP',                   0, 1440, NULL,    NULL,    'BLOCK'),
 ('Max Single Transfer Ceiling',       'A single transfer above 1 lakh is blocked',
     'MAX_SINGLE_AMOUNT',         100000, 1440, NULL,    NULL,    'BLOCK'),
 ('Daily Cumulative Limit',            'Last 24h outgoing total above the account daily_limit is blocked',
     'DAILY_LIMIT_EXCEEDED',           0, 1440, NULL,    NULL,    'BLOCK'),
 ('Rapid Fire Transactions',           'More than 10 attempts from one account in an hour is held',
     'MAX_TXN_PER_HOUR',              10,   60, NULL,    NULL,    'QUARANTINE'),
 ('Night Time High Value Transfer',    'Above 50,000 between 01:00 and 05:00 IST is flagged',
     'NIGHT_HIGH_VALUE',           50000, 1440, '01:00', '05:00', 'FLAG'),
 ('New Device High Value',             'Above 25,000 from an untrusted device is held',
     'NEW_DEVICE_HIGH_VALUE',      25000, 1440, NULL,    NULL,    'QUARANTINE'),
 ('New Beneficiary High Value',        'Above 20,000 to a payee not saved for 24h is flagged',
     'NEW_BENEFICIARY_HIGH_VALUE', 20000, 1440, NULL,    NULL,    'FLAG'),
 ('Failed Login Burst Before Transfer','3+ failed logins in the last 30 minutes before a transfer is held',
     'RECENT_FAILED_LOGINS',           3,   30, NULL,    NULL,    'QUARANTINE'),
 ('Structuring Below Ceiling',         '3 transfers of 80,000+ within 24h is held',
     'STRUCTURING',                80000, 1440, NULL,    NULL,    'QUARANTINE'),
 ('Rapid Pass-Through (Mule Pattern)', 'Receiving 50,000+ and sending 90%+ of it out within an hour is held',
     'RAPID_PASS_THROUGH',         50000,   60, NULL,    NULL,    'QUARANTINE');

INSERT INTO blocklisted_ips (ip_address, reason, source, expires_at) VALUES
 ('185.220.101.45', 'Known TOR exit node used for credential stuffing', 'THREAT_FEED', NULL),
 ('45.155.205.233', 'Botnet command-and-control server',                'THREAT_FEED', NOW() + INTERVAL '30 days'),
 ('91.240.118.172', 'Credential-stuffing source (shared feed)',          'THREAT_FEED', NOW() + INTERVAL '7 days'),
 ('198.51.100.23',  'Linked to a fraud ring under investigation',       'MANUAL',      NULL),
 ('103.75.190.11',  'Hosted a phishing page copying our login',         'MANUAL',      NULL),
 ('192.0.2.77',     'Old brute-force burst (TTL already over)',         'AUTO_BRUTE_FORCE', NOW() - INTERVAL '1 day');

-- the internal row fires T8, which freezes Vikram's account
INSERT INTO blocklisted_accounts (account_id, external_account_number, reason, expires_at) VALUES
 ('b0000000-0000-0000-0000-000000000005', NULL, 'Internal account identified as a money mule', NULL),
 (NULL, 'EXT-5021998877', 'External account in a layering chain',      NULL),
 (NULL, 'EXT-7788991122', 'Reported by a victim as a scam receiver',   NULL),
 (NULL, 'EXT-3344556677', 'Frozen on court order',                     NOW() + INTERVAL '180 days'),
 (NULL, 'EXT-9900112233', 'Lottery-scam collection account',           NULL);

-- ---------------- Module 3 + 4 + 5 activity (through the real procedures) ----------------
-- Each CALL prints its result. Comments show what each one should return.
CALL sp_execute_secure_transfer('TXN-0001', 'b0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000002',   5000, 1, '106.51.20.11', 'fp-aarav-iphone');  -- SUCCESS
CALL sp_execute_secure_transfer('TXN-0002', 'b0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000003',  15000, 2);   -- SUCCESS
CALL sp_execute_secure_transfer('TXN-0003', 'b0000000-0000-0000-0000-000000000006', 'b0000000-0000-0000-0000-000000000007',   8000, 1);   -- SUCCESS
CALL sp_execute_secure_transfer('TXN-0004', 'b0000000-0000-0000-0000-000000000007', 'b0000000-0000-0000-0000-000000000006',  12000, 2);   -- SUCCESS
CALL sp_execute_secure_transfer('TXN-0005', 'b0000000-0000-0000-0000-000000000004', 'b0000000-0000-0000-0000-000000000001',  45000, 3);   -- SUCCESS (NEFT via gateway)
CALL sp_execute_secure_transfer('TXN-0006', 'b0000000-0000-0000-0000-000000000010', 'b0000000-0000-0000-0000-000000000002',   3000, 1);   -- SUCCESS
CALL sp_execute_secure_transfer('TXN-0007', 'b0000000-0000-0000-0000-000000000007', 'b0000000-0000-0000-0000-000000000009',  25000, 2);   -- SUCCESS, flagged: new payee
CALL sp_execute_secure_transfer('TXN-0008', 'b0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000002',  30000, 1, NULL, 'fp-aarav-new-laptop');  -- QUARANTINED: new device
CALL sp_execute_secure_transfer('TXN-0009', 'b0000000-0000-0000-0000-000000000008', 'b0000000-0000-0000-0000-000000000001',   5000, 1);   -- QUARANTINED: failed logins
CALL sp_execute_secure_transfer('TXN-0010', 'b0000000-0000-0000-0000-000000000009', 'b0000000-0000-0000-0000-000000000003',   1000, 1);   -- REJECTED: KYC not verified
CALL sp_execute_secure_transfer('TXN-0011', 'b0000000-0000-0000-0000-000000000010', 'b0000000-0000-0000-0000-000000000002',  60000, 2);   -- REJECTED: insufficient funds
CALL sp_execute_secure_transfer('TXN-0012', 'b0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000003',   1000, 1, '185.220.101.45');  -- REJECTED: blocklisted IP
CALL sp_execute_secure_transfer('TXN-0013', 'b0000000-0000-0000-0000-000000000005', 'b0000000-0000-0000-0000-000000000003',    500, 1);   -- REJECTED: sender frozen
CALL sp_execute_secure_transfer('TXN-0014', 'b0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000005',   1000, 1);   -- REJECTED: mule receiver
CALL sp_execute_secure_transfer('TXN-0015', 'b0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000001', 150000, 4);   -- REJECTED: above 1 lakh
CALL sp_execute_secure_transfer('TXN-0016', 'b0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000001', 120000, 4);   -- REJECTED: above 1 lakh -> 3rd hit, Rohan FLAGGED (T6)
CALL sp_execute_secure_transfer('TXN-0017', 'b0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000001',    500, 1);   -- REJECTED: sender flagged
CALL sp_execute_secure_transfer('TXN-0018', 'b0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000099',    100, 1);   -- FAILED: unknown account -> dead_letter_queue
CALL sp_execute_secure_transfer('TXN-0001', 'b0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000002',   5000, 1);   -- DUPLICATE: SUCCESS (no double payment)

-- Analyst Neha reviews the two held transfers
DO $$
DECLARE
    v_id  UUID;
    v_res TEXT;
BEGIN
    SELECT transaction_id INTO v_id FROM transactions WHERE idempotency_key = 'TXN-0008';
    CALL sp_review_quarantined_transaction(v_id, TRUE, 'a0000000-0000-0000-0000-000000000006',
                                           'Customer confirmed the new laptop on a call', v_res);
    RAISE NOTICE 'TXN-0008 -> %', v_res;      -- SUCCESS: RELEASED_BY_ANALYST

    SELECT transaction_id INTO v_id FROM transactions WHERE idempotency_key = 'TXN-0009';
    CALL sp_review_quarantined_transaction(v_id, FALSE, 'a0000000-0000-0000-0000-000000000006',
                                           'Account takeover suspected after brute-force attempts', v_res);
    RAISE NOTICE 'TXN-0009 -> %', v_res;      -- REJECTED: REJECTED_BY_ANALYST
END $$;

-- one more held transfer, left open so the analyst queue has work in it
CALL sp_execute_secure_transfer('TXN-0019', 'b0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000003',  40000, 2, '49.36.12.200', 'fp-unknown-tablet');  -- QUARANTINED: new device

-- Gateway health: primary and backup report fine, the legacy hub fails 5 times -> DOWN
CALL sp_record_gateway_result(1, TRUE, 120);
CALL sp_record_gateway_result(2, TRUE, 210);
CALL sp_record_gateway_result(3, FALSE);
CALL sp_record_gateway_result(3, FALSE);
CALL sp_record_gateway_result(3, FALSE);
CALL sp_record_gateway_result(3, FALSE);
CALL sp_record_gateway_result(3, FALSE);

-- Notification worker: deliver the first 15, one SMS keeps failing until it is DEAD
DO $$
DECLARE
    r    RECORD;
    v_id UUID;
BEGIN
    FOR r IN SELECT notification_id FROM system_notifications ORDER BY created_at LIMIT 15 LOOP
        CALL sp_mark_notification(r.notification_id, TRUE);
    END LOOP;

    SELECT notification_id INTO v_id FROM system_notifications
     WHERE delivery_status = 'PENDING' ORDER BY created_at DESC LIMIT 1;
    FOR i IN 1..5 LOOP
        CALL sp_mark_notification(v_id, FALSE, 'SMS provider timeout (HTTP 504)');
    END LOOP;
END $$;

-- A few older dead letters for the operations page
INSERT INTO dead_letter_queue (task_type, payload, error_message, retry_count, status, failed_at, resolved_at) VALUES
 ('GATEWAY_CALLBACK',   '{"gateway_id": 2, "ref": "NEFT-88231"}', 'Callback signature mismatch', 3, 'ABANDONED', NOW() - INTERVAL '3 days', NOW() - INTERVAL '2 days'),
 ('FRAUD_REPORT_EXPORT','{"report": "daily_alerts"}',             'Storage bucket unavailable',  1, 'RESOLVED',  NOW() - INTERVAL '1 day',  NOW() - INTERVAL '20 hours'),
 ('SEND_EMAIL_NOTIFICATION', '{"user_id": "a0000000-0000-0000-0000-000000000004"}', 'Mailbox full', 0, 'PENDING', NOW() - INTERVAL '2 hours', NULL);


-- =====================================================================
-- PART 6 : DEMO QUERIES
-- =====================================================================
-- Dashboard
--   SELECT * FROM v_dashboard_kpis;
--   SELECT * FROM v_system_health;
--   SELECT * FROM v_transaction_feed;
--   SELECT * FROM v_fraud_alert_feed;
--   SELECT * FROM v_fraud_rule_stats;
--   SELECT * FROM v_suspicious_accounts;
--   SELECT * FROM v_login_security;
--   SELECT * FROM v_gateway_health;
--   SELECT * FROM v_queue_health;
--   SELECT * FROM v_audit_trail;
--   SELECT * FROM v_ledger_mismatches;       -- must be empty
--
-- Idempotency: same key, different amount
--   CALL sp_execute_secure_transfer('TXN-0001', 'b0000000-0000-0000-0000-000000000001',
--        'b0000000-0000-0000-0000-000000000002', 9999, 1);      -- REJECTED: KEY_REUSED...
--
-- Structuring: three 85k transfers from Ananya (salary) -> the third is QUARANTINED
--   CALL sp_execute_secure_transfer('S-1', 'b0000000-0000-0000-0000-000000000004', 'b0000000-0000-0000-0000-000000000001', 85000, 2);
--   CALL sp_execute_secure_transfer('S-2', 'b0000000-0000-0000-0000-000000000004', 'b0000000-0000-0000-0000-000000000002', 85000, 2);
--   CALL sp_execute_secure_transfer('S-3', 'b0000000-0000-0000-0000-000000000004', 'b0000000-0000-0000-0000-000000000001', 85000, 2);
--
-- Graceful degradation: take all gateways offline, NEFT is DEFERRED, UPI still works
--   UPDATE payment_gateways SET is_active = FALSE;
--   CALL sp_execute_secure_transfer('G-1', 'b0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000003', 1000, 3);
--   CALL sp_execute_secure_transfer('G-2', 'b0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000001', 1000, 1);
--   UPDATE payment_gateways SET is_active = TRUE;
--
-- Append-only protection
--   DELETE FROM audit_logs;                  -- ERROR
--   DELETE FROM transactions;                -- ERROR
--
-- TTL clean-up
--   CALL sp_housekeeping();
