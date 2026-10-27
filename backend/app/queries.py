"""Every SQL statement the API runs, in one place.

The database does the real work (fraud rules, locking, audit, retries). Reads go through the
dashboard views defined in database/simple.sql, writes go through its stored procedures.
Parameters are written as :name and bound by SQLAlchemy.
"""

# ---------------------------------------------------------------- overview
KPIS = "SELECT * FROM v_dashboard_kpis"
HEALTH = "SELECT * FROM v_system_health"
HOURLY = "SELECT * FROM v_hourly_volume LIMIT 24"
ALERTS = "SELECT * FROM v_fraud_alert_feed LIMIT :n"
RISKY = "SELECT * FROM v_suspicious_accounts WHERE risk_score > 0 LIMIT :n"

# ---------------------------------------------------------------- transactions and accounts
TX_ALL = "SELECT * FROM v_transaction_feed LIMIT :n"
TX_BY_STATUS = "SELECT * FROM v_transaction_feed WHERE status = :status LIMIT :n"
ACCOUNTS = "SELECT * FROM v_account_overview ORDER BY full_name, type_name"
ACCOUNT_RISK = ("SELECT account_id, risk_score, blocked_or_held, flagged, failed_logins_24h "
                "FROM v_suspicious_accounts")

# ---------------------------------------------------------------- fraud
RULE_STATS = "SELECT * FROM v_fraud_rule_stats"
QUARANTINE = "SELECT * FROM v_quarantine_queue"
LOGIN_SECURITY = "SELECT * FROM v_login_security LIMIT 50"
CALL_REVIEW = ("CALL sp_review_quarantined_transaction("
               "CAST(:tx AS uuid), CAST(:approve AS boolean), CAST(:analyst AS uuid), CAST(:notes AS text))")

BLOCKED_IPS = """
SELECT ip_block_id, ip_address, reason, source, blocked_at, expires_at,
       (is_active AND (expires_at IS NULL OR expires_at > NOW())) AS in_force
  FROM blocklisted_ips
 ORDER BY blocked_at DESC"""

BLOCKED_ACCOUNTS = """
SELECT b.block_id, b.account_id, b.external_account_number, u.full_name, b.reason,
       b.blocked_at, b.expires_at,
       (b.is_active AND (b.expires_at IS NULL OR b.expires_at > NOW())) AS in_force
  FROM blocklisted_accounts b
  LEFT JOIN accounts a ON a.account_id = b.account_id
  LEFT JOIN users u    ON u.user_id = a.user_id
 ORDER BY b.blocked_at DESC"""

# ---------------------------------------------------------------- operations
GATEWAYS = "SELECT * FROM v_gateway_health"
QUEUES = "SELECT * FROM v_queue_health ORDER BY queue, status"
AUDIT = "SELECT * FROM v_audit_trail LIMIT :n"
LEDGER = "SELECT * FROM v_ledger_mismatches"
DEAD_LETTERS = """
SELECT dlq_id, task_type, error_message, retry_count, max_retries, status, failed_at
  FROM dead_letter_queue
 ORDER BY failed_at DESC
 LIMIT :n"""
CALL_GATEWAY_RESULT = ("CALL sp_record_gateway_result("
                       "CAST(:gateway AS int), CAST(:ok AS boolean), CAST(:latency AS int))")
CALL_HOUSEKEEPING = "CALL sp_housekeeping()"

# Notification outbox: claim a batch with a lease, then report each result with sp_mark_notification.
CLAIM_NOTIFICATIONS = """
UPDATE system_notifications
   SET delivery_status = 'PROCESSING',
       locked_by       = :worker,
       locked_until    = NOW() + INTERVAL '2 minutes'
 WHERE notification_id IN (
         SELECT notification_id FROM system_notifications
          WHERE delivery_status = 'PENDING' AND next_attempt_at <= NOW()
          ORDER BY created_at
          LIMIT :n
            FOR UPDATE SKIP LOCKED)
RETURNING notification_id, channel, message"""
CALL_MARK_NOTIFICATION = ("CALL sp_mark_notification("
                          "CAST(:id AS uuid), CAST(:sent AS boolean), CAST(:error AS text))")

# ---------------------------------------------------------------- transfers
RAIL_ID = "SELECT type_id FROM transaction_types WHERE type_name = :rail"

TRANSFER_OPTIONS_ACCOUNTS = """
SELECT a.account_id, a.user_id, u.full_name, t.type_name, a.balance, a.account_status
  FROM accounts a
  JOIN users u         ON u.user_id = a.user_id
  JOIN account_types t ON t.account_type_id = a.account_type_id
 ORDER BY u.full_name, t.type_name"""
TRANSFER_OPTIONS_DEVICES = """
SELECT user_id, fingerprint_hash, device_label, is_trusted
  FROM device_fingerprints
 ORDER BY user_id, registered_at"""
TRANSFER_OPTIONS_RAILS = "SELECT type_name, requires_gateway FROM transaction_types ORDER BY type_id"

ACCOUNT_EXISTS = "SELECT 1 FROM accounts WHERE account_id = :acc"
PIN_CHECK = """
SELECT a.user_id, COALESCE(c.pin_hash = crypt(:pin, c.pin_hash), FALSE) AS pin_ok
  FROM accounts a
  LEFT JOIN user_credentials c ON c.user_id = a.user_id
 WHERE a.account_id = :acc"""
RECORD_FAILED_LOGIN = """
INSERT INTO login_history (user_id, ip_address, is_success, failure_reason)
VALUES (CAST(:uid AS uuid), CAST(:ip AS varchar), FALSE, CAST(:reason AS varchar))"""
FAILED_LOGIN_BURST = """
INSERT INTO login_history (user_id, ip_address, is_success, failure_reason)
SELECT CAST(:uid AS uuid), CAST(:ip AS varchar), FALSE, 'INVALID_PASSWORD'
  FROM generate_series(1, CAST(:n AS int))"""
ANY_USER_WITH_ACCOUNT = "SELECT user_id FROM accounts ORDER BY created_at LIMIT 1"
IP_IS_BLOCKED = """
SELECT EXISTS (SELECT 1 FROM blocklisted_ips
                WHERE ip_address = :ip AND is_active
                  AND (expires_at IS NULL OR expires_at > NOW())) AS blocked"""

# The stored procedure does the real work: idempotency key, locking, fraud rules, audit.
# The two INOUT parameters (status, transaction id) come back as the one result row.
CALL_TRANSFER = """
CALL sp_execute_secure_transfer(
    CAST(:key AS varchar), CAST(:sender AS uuid), CAST(:receiver AS uuid),
    CAST(:amount AS numeric), CAST(:type_id AS int),
    CAST(:ip AS varchar), CAST(:device AS varchar))"""

# ---------------------------------------------------------------- analytics inputs
ANALYSIS_TRANSACTIONS = """
SELECT sender_account_id::text AS sender, receiver_account_id::text AS receiver,
       amount::float8 AS amount, status
  FROM transactions
 WHERE created_at >= NOW() - make_interval(days => CAST(:days AS int))"""
ANALYSIS_ALERTS = """
SELECT x.sender_account_id::text AS sender, fa.transaction_id::text AS transaction_id, fa.action_taken
  FROM fraud_alerts fa
  JOIN transactions x ON x.transaction_id = fa.transaction_id
 WHERE fa.raised_at >= NOW() - make_interval(days => CAST(:days AS int))"""
ANALYSIS_BLOCKED = ("SELECT account_id::text AS account_id FROM blocklisted_accounts "
                    "WHERE is_active AND account_id IS NOT NULL")
ANALYSIS_NAMES = ("SELECT a.account_id::text AS account_id, u.full_name || ' (' || t.type_name || ')' AS name "
                  "FROM accounts a JOIN users u ON u.user_id = a.user_id "
                  "JOIN account_types t ON t.account_type_id = a.account_type_id")
ANALYSIS_CEILING = ("SELECT MIN(threshold_value)::float8 AS ceiling FROM fraud_rules "
                    "WHERE evaluation_metric = 'MAX_SINGLE_AMOUNT' AND is_active")
