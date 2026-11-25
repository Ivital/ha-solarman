-- RAKURS configuration plane. Additive only; NEVER changes telemetry/history.
-- Install only after a full verified MySQL backup. All configuration is
-- disabled by default. No Modbus/RS485 commands are emitted by this migration.
CREATE TABLE IF NOT EXISTS rakurs_poll_settings (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  enabled TINYINT(1) NOT NULL DEFAULT 0,
  interval_seconds SMALLINT UNSIGNED NOT NULL DEFAULT 60,
  updated_by BIGINT UNSIGNED NULL,
  updated_at_utc DATETIME(6) NULL,
  CONSTRAINT ck_rakurs_poll_id CHECK (id=1),
  CONSTRAINT ck_rakurs_poll_interval CHECK (interval_seconds BETWEEN 15 AND 3600),
  CONSTRAINT fk_rakurs_poll_updater FOREIGN KEY (updated_by) REFERENCES rakurs_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_device_settings (
  device_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  display_name VARCHAR(96) NOT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 0,
  transport ENUM('unconfigured','modbus_tcp','rtu_tcp') NOT NULL DEFAULT 'unconfigured',
  gateway_ip VARCHAR(45) NULL,
  gateway_port SMALLINT UNSIGNED NULL,
  slave_id TINYINT UNSIGNED NULL,
  function_code TINYINT UNSIGNED NOT NULL DEFAULT 3,
  register_address SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  register_count TINYINT UNSIGNED NOT NULL DEFAULT 1,
  timeout_ms SMALLINT UNSIGNED NOT NULL DEFAULT 1500,
  verified_connection TINYINT(1) NOT NULL DEFAULT 0,
  updated_at_utc DATETIME(6) NOT NULL,
  CONSTRAINT fk_rakurs_cfg_device FOREIGN KEY (device_id) REFERENCES rakurs_devices(id),
  CONSTRAINT ck_rakurs_cfg_fc CHECK (function_code IN (3,4)),
  CONSTRAINT ck_rakurs_cfg_words CHECK (register_count BETWEEN 1 AND 8),
  CONSTRAINT ck_rakurs_cfg_timeout CHECK (timeout_ms BETWEEN 200 AND 5000),
  CONSTRAINT ck_rakurs_cfg_slave CHECK (slave_id IS NULL OR slave_id BETWEEN 1 AND 247)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rakurs_poll_runtime (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  worker_heartbeat_utc DATETIME(6) NULL,
  last_attempt_utc DATETIME(6) NULL,
  last_success_utc DATETIME(6) NULL,
  last_device_id BIGINT UNSIGNED NULL,
  state VARCHAR(32) NOT NULL DEFAULT 'not_configured',
  last_error VARCHAR(96) NULL,
  CONSTRAINT ck_rakurs_runtime_id CHECK (id=1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO rakurs_poll_settings
(id,enabled,interval_seconds,updated_at_utc)
VALUES (1,0,60,UTC_TIMESTAMP(6));
INSERT IGNORE INTO rakurs_poll_runtime (id,state) VALUES (1,'not_configured');

-- Initial profiles use existing PKs and preserve prior data / origin labels.
INSERT IGNORE INTO rakurs_device_settings
(device_id,display_name,enabled,transport,updated_at_utc)
SELECT d.id, CONCAT('Deye №',pm.slot_no),0,'unconfigured',UTC_TIMESTAMP(6)
FROM rakurs_devices AS d
JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id
JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id
WHERE pg.group_key='deye_sun20k_parallel_shared_battery'
  AND pm.slot_no IN (1,2);
