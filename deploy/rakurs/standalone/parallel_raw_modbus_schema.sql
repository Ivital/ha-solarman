-- RAKURS: additive 2 x Deye SUN-20K-SG05LP3-EU-SM2 schema.
-- Profiles, group membership and every received raw Modbus 16-bit word are
-- independent from decoded field/telemetry samples.
-- NO alterations or deletion of legacy tables; NO claims of connected devices.
-- All times UTC. Raw register addresses are NOT restricted to the 815 P3 catalog.
CREATE TABLE IF NOT EXISTS rakurs_parallel_groups (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  group_key VARCHAR(100) NOT NULL,
  model VARCHAR(150) NOT NULL,
  expected_inverters SMALLINT UNSIGNED NOT NULL DEFAULT 2,
  nominal_ac_w_each INT UNSIGNED NOT NULL DEFAULT 20000,
  battery_mode VARCHAR(24) NOT NULL DEFAULT 'shared',
  battery_nominal_v DECIMAL(6,2) NOT NULL DEFAULT 48.00,
  topology_status VARCHAR(24) NOT NULL DEFAULT 'unverified',
  bms_soc_source VARCHAR(128) NULL,
  created_at_utc DATETIME(6) NOT NULL,
  UNIQUE KEY uq_rakurs_parallel_group_key (group_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_parallel_members (
  group_id BIGINT UNSIGNED NOT NULL,
  device_id BIGINT UNSIGNED NOT NULL,
  slot_no SMALLINT UNSIGNED NOT NULL,
  member_kind VARCHAR(20) NOT NULL,
  parallel_role VARCHAR(20) NOT NULL DEFAULT 'unverified',
  modbus_address SMALLINT UNSIGNED NULL,
  transport_verified TINYINT(1) NOT NULL DEFAULT 0,
  member_verified TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, member_kind, slot_no),
  UNIQUE KEY uq_rakurs_parallel_device (group_id, device_id),
  CONSTRAINT fk_parallel_members_group FOREIGN KEY (group_id)
    REFERENCES rakurs_parallel_groups(id),
  CONSTRAINT fk_parallel_members_device FOREIGN KEY (device_id)
    REFERENCES rakurs_devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_modbus_poll_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  device_id BIGINT UNSIGNED NOT NULL,
  poll_uid VARCHAR(64) NOT NULL,
  observed_at_utc DATETIME(6) NOT NULL,
  finished_at_utc DATETIME(6) NULL,
  transport VARCHAR(32) NOT NULL,
  slave_id SMALLINT UNSIGNED NULL,
  status VARCHAR(24) NOT NULL,
  quality VARCHAR(24) NOT NULL,
  ingest_source VARCHAR(80) NOT NULL,
  request_count INT UNSIGNED NOT NULL DEFAULT 0,
  response_count INT UNSIGNED NOT NULL DEFAULT 0,
  exception_count INT UNSIGNED NOT NULL DEFAULT 0,
  created_at_utc DATETIME(6) NOT NULL,
  UNIQUE KEY uq_rakurs_modbus_poll_uid (poll_uid),
  KEY ix_modbus_poll_device_time (device_id, observed_at_utc),
  CONSTRAINT fk_modbus_poll_device FOREIGN KEY (device_id)
    REFERENCES rakurs_devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_modbus_frames (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  poll_id BIGINT UNSIGNED NOT NULL,
  device_id BIGINT UNSIGNED NOT NULL,
  sequence_no INT UNSIGNED NOT NULL,
  captured_at_utc DATETIME(6) NOT NULL,
  function_code TINYINT UNSIGNED NULL,
  start_address INT UNSIGNED NULL,
  requested_count SMALLINT UNSIGNED NULL,
  status VARCHAR(24) NOT NULL,
  exception_code SMALLINT UNSIGNED NULL,
  error_code VARCHAR(80) NULL,
  request_bytes MEDIUMBLOB NULL,
  response_bytes MEDIUMBLOB NULL,
  UNIQUE KEY uq_modbus_frame_sequence (poll_id, sequence_no),
  KEY ix_modbus_frames_device_time (device_id, captured_at_utc),
  CONSTRAINT fk_modbus_frames_poll FOREIGN KEY (poll_id)
    REFERENCES rakurs_modbus_poll_runs(id),
  CONSTRAINT fk_modbus_frames_device FOREIGN KEY (device_id)
    REFERENCES rakurs_devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_modbus_register_samples (
  poll_id BIGINT UNSIGNED NOT NULL,
  frame_id BIGINT UNSIGNED NOT NULL,
  device_id BIGINT UNSIGNED NOT NULL,
  observed_at_utc DATETIME(6) NOT NULL,
  function_code TINYINT UNSIGNED NOT NULL,
  register_address INT UNSIGNED NOT NULL,
  raw_uint16 SMALLINT UNSIGNED NOT NULL,
  quality VARCHAR(24) NOT NULL,
  ingest_source VARCHAR(80) NOT NULL,
  PRIMARY KEY (poll_id, frame_id, register_address),
  KEY ix_raw_register_device_time
    (device_id, function_code, register_address, observed_at_utc),
  CONSTRAINT fk_raw_register_poll FOREIGN KEY (poll_id)
    REFERENCES rakurs_modbus_poll_runs(id),
  CONSTRAINT fk_raw_register_frame FOREIGN KEY (frame_id)
    REFERENCES rakurs_modbus_frames(id),
  CONSTRAINT fk_raw_register_device FOREIGN KEY (device_id)
    REFERENCES rakurs_devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Function 01/02 bit responses also retain each returned bit, not only the frame.
CREATE TABLE IF NOT EXISTS rakurs_modbus_bit_samples (
  poll_id BIGINT UNSIGNED NOT NULL,
  frame_id BIGINT UNSIGNED NOT NULL,
  device_id BIGINT UNSIGNED NOT NULL,
  observed_at_utc DATETIME(6) NOT NULL,
  function_code TINYINT UNSIGNED NOT NULL,
  bit_address INT UNSIGNED NOT NULL,
  bit_value TINYINT UNSIGNED NOT NULL,
  quality VARCHAR(24) NOT NULL,
  ingest_source VARCHAR(80) NOT NULL,
  PRIMARY KEY (poll_id, frame_id, bit_address),
  KEY ix_modbus_bit_device_time (device_id, function_code, bit_address, observed_at_utc),
  CONSTRAINT fk_modbus_bit_poll FOREIGN KEY (poll_id)
    REFERENCES rakurs_modbus_poll_runs(id),
  CONSTRAINT fk_modbus_bit_frame FOREIGN KEY (frame_id)
    REFERENCES rakurs_modbus_frames(id),
  CONSTRAINT fk_modbus_bit_device FOREIGN KEY (device_id)
    REFERENCES rakurs_devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
