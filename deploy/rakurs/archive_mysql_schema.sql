-- Separate, uninitialized RAKURS MySQL archive. Do NOT run against HA Recorder.
-- Five EMPTY tables. No device input, credentials, sample writes or DROP.
-- DATETIME(6) columns must always be written in UTC.
CREATE TABLE IF NOT EXISTS rakurs_devices (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  device_key VARCHAR(128) NOT NULL,
  kind VARCHAR(40) NOT NULL,
  model VARCHAR(150) NULL,
  serial_number VARCHAR(160) NULL,
  commissioned_at_utc DATETIME(6) NULL,
  created_at_utc DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_rakurs_device_key (device_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_signals (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  signal_key VARCHAR(128) NOT NULL,
  label_uk VARCHAR(255) NOT NULL,
  unit VARCHAR(32) NULL,
  source_register VARCHAR(64) NULL,
  function_code TINYINT UNSIGNED NULL,
  scale_expression VARCHAR(160) NULL,
  state_class VARCHAR(30) NULL,
  verified TINYINT(1) NOT NULL DEFAULT 0,
  verified_at_utc DATETIME(6) NULL,
  verification_note TEXT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_rakurs_signal_key (signal_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_samples (
  device_id BIGINT UNSIGNED NOT NULL,
  signal_id BIGINT UNSIGNED NOT NULL,
  observed_at_utc DATETIME(6) NOT NULL,
  received_at_utc DATETIME(6) NOT NULL,
  value_decimal DECIMAL(24,9) NULL,
  raw_value BIGINT NULL,
  quality VARCHAR(20) NOT NULL,
  ingest_source VARCHAR(80) NOT NULL,
  PRIMARY KEY (device_id, signal_id, observed_at_utc),
  KEY idx_rakurs_samples_signal_time (signal_id, observed_at_utc),
  KEY idx_rakurs_samples_time (observed_at_utc),
  CONSTRAINT fk_rakurs_sample_device FOREIGN KEY (device_id) REFERENCES rakurs_devices(id),
  CONSTRAINT fk_rakurs_sample_signal FOREIGN KEY (signal_id) REFERENCES rakurs_signals(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_device_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  device_id BIGINT UNSIGNED NOT NULL,
  observed_at_utc DATETIME(6) NOT NULL,
  severity VARCHAR(24) NOT NULL,
  event_code VARCHAR(80) NOT NULL,
  message TEXT NOT NULL,
  source VARCHAR(80) NOT NULL,
  PRIMARY KEY (id),
  KEY idx_rakurs_events_time (observed_at_utc),
  CONSTRAINT fk_rakurs_event_device FOREIGN KEY (device_id) REFERENCES rakurs_devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_ingest_batches (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  device_id BIGINT UNSIGNED NOT NULL,
  started_at_utc DATETIME(6) NOT NULL,
  finished_at_utc DATETIME(6) NULL,
  received_count INT UNSIGNED NOT NULL DEFAULT 0,
  accepted_count INT UNSIGNED NOT NULL DEFAULT 0,
  rejected_count INT UNSIGNED NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL,
  error_code VARCHAR(80) NULL,
  PRIMARY KEY (id),
  KEY idx_rakurs_batches_time (started_at_utc),
  CONSTRAINT fk_rakurs_batch_device FOREIGN KEY (device_id) REFERENCES rakurs_devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
