-- Independent Rakurs web authentication tables in EXISTING MySQL 'rakurs'.
-- Password hashes only; no plain passwords, no HA data, no sample users.
CREATE TABLE rakurs_users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('admin','viewer') NOT NULL DEFAULT 'viewer',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  session_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
  created_at_utc DATETIME(6) NOT NULL,
  last_login_at_utc DATETIME(6) NULL,
  UNIQUE KEY uq_rakurs_user (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE rakurs_login_attempts (
  attempt_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  failure_count INT UNSIGNED NOT NULL DEFAULT 0,
  last_failed_at_utc DATETIME(6) NOT NULL,
  locked_until_utc DATETIME(6) NULL,
  KEY idx_rakurs_attempts_expiry (locked_until_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
