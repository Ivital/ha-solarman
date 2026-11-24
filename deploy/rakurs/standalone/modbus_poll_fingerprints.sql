-- Additive prerequisite for future real Modbus ingestion.
-- Existing simulated records remain untouched. DO NOT auto-apply on deploy.
CREATE TABLE IF NOT EXISTS rakurs_modbus_poll_fingerprints (
  poll_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  payload_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_at_utc DATETIME(6) NOT NULL,
  CONSTRAINT fk_rakurs_poll_fingerprint FOREIGN KEY (poll_id)
    REFERENCES rakurs_modbus_poll_runs(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
