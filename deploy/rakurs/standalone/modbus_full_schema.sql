-- Full read-capable P3 profile catalog. Simulation only, no Modbus writes.
-- Non-destructive additions to existing rakurs schema.
CREATE TABLE IF NOT EXISTS rakurs_modbus_fields (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  field_key VARCHAR(64) NOT NULL,
  profile_name VARCHAR(96) NOT NULL,
  group_name VARCHAR(128) NOT NULL,
  field_name VARCHAR(255) NOT NULL,
  platform VARCHAR(32) NOT NULL,
  unit VARCHAR(48) NULL,
  rule_code VARCHAR(32) NULL,
  scale_definition VARCHAR(128) NULL,
  class_name VARCHAR(48) NULL,
  state_class VARCHAR(48) NULL,
  registers_json JSON NOT NULL,
  register_sets_json JSON NOT NULL,
  derived TINYINT(1) NOT NULL DEFAULT 0,
  verified TINYINT(1) NOT NULL DEFAULT 0,
  created_at_utc DATETIME(6) NOT NULL,
  UNIQUE KEY uq_rakurs_modbus_field_key (field_key),
  KEY ix_rakurs_modbus_group (group_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_modbus_field_samples (
  device_id BIGINT UNSIGNED NOT NULL,
  field_id BIGINT UNSIGNED NOT NULL,
  observed_at_utc DATETIME(6) NOT NULL,
  received_at_utc DATETIME(6) NOT NULL,
  value_decimal DECIMAL(24,9) NULL,
  value_text VARCHAR(512) NULL,
  raw_registers_json JSON NULL,
  quality VARCHAR(20) NOT NULL,
  ingest_source VARCHAR(80) NOT NULL,
  PRIMARY KEY (device_id, field_id, observed_at_utc),
  KEY ix_rakurs_modbus_samples_field_time (field_id, observed_at_utc),
  CONSTRAINT fk_rakurs_modbus_sample_device
    FOREIGN KEY (device_id) REFERENCES rakurs_devices(id),
  CONSTRAINT fk_rakurs_modbus_sample_field
    FOREIGN KEY (field_id) REFERENCES rakurs_modbus_fields(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_modbus_register_inventory (
  address SMALLINT UNSIGNED NOT NULL PRIMARY KEY,
  function_code TINYINT UNSIGNED NOT NULL DEFAULT 3,
  profile_name VARCHAR(96) NOT NULL,
  verified TINYINT(1) NOT NULL DEFAULT 0,
  created_at_utc DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
