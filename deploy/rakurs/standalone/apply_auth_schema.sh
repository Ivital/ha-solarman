#!/usr/bin/env bash
# One-time RAKURS standalone authentication schema installer.
# Creates precisely 2 new tables in already initialized LAN MySQL 'rakurs'.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="$DIR/auth_schema.sql"
if [[ "$MODE" != "--check" && "$MODE" != "--apply" ]]; then
  echo 'Usage: bash apply_auth_schema.sh [--check|--apply]' >&2
  exit 2
fi

python3 - "$FILE" <<'PY'
import re
import sys
from pathlib import Path

script = Path(sys.argv[1]).read_text(encoding="utf-8")
clean = "\n".join(line for line in script.splitlines()
                  if not line.lstrip().startswith("--"))
statements = [s.strip() for s in clean.split(";") if s.strip()]
assert len(statements) == 2, "Unexpected auth statement count"
for name, statement in zip(("rakurs_users", "rakurs_login_attempts"), statements):
    pattern = (r"(?is)\ACREATE\s+TABLE\s+" + name +
               r"\s*\(.*\)\s*ENGINE=InnoDB\s+"
               r"DEFAULT\s+CHARSET=utf8mb4\s+COLLATE=utf8mb4_unicode_ci\s*\Z")
    assert re.fullmatch(pattern, statement), "Unsafe auth schema: " + name
    for forbidden in ("DROP", "ALTER", "TRUNCATE", "DELETE", "INSERT",
                      "UPDATE", "GRANT", "REVOKE", "CREATE USER", "USE "):
        assert not re.search(r"\b" + forbidden + r"\b", statement, flags=re.I), (
            "Forbidden SQL in auth schema: " + name)
print("RAKURS_AUTH_SCHEMA_CHECK_PASS; NEW_TABLES=2")
PY

if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS; NO MYSQL CONNECTION'
  exit 0
fi

if [[ ! -r /dev/tty ]]; then
  echo 'STOP: interactive terminal unavailable' >&2
  exit 2
fi
echo 'TARGET: 192.168.5.30:3306, database=rakurs, user=Ivital'
echo 'Existing 5 monitoring tables are retained. Adds only users + login attempts.'
read -r -p 'Type exactly auth-rakurs to proceed: ' CONFIRM </dev/tty
[[ "$CONFIRM" == "auth-rakurs" ]] || { echo 'ABORTED: no changes'; exit 2; }

if command -v mariadb >/dev/null 2>&1; then
  CLIENT="$(command -v mariadb)"
elif command -v mysql >/dev/null 2>&1; then
  CLIENT="$(command -v mysql)"
else
  echo 'STOP: no MySQL/MariaDB command-line client' >&2
  exit 2
fi
HELP="$("$CLIENT" --no-defaults --help 2>&1)"
if grep -q -- '--ssl-mode' <<< "$HELP"; then
  TRANSPORT=(--ssl-mode=DISABLED)
elif grep -q -- '--ssl' <<< "$HELP"; then
  TRANSPORT=(--skip-ssl)
else
  echo 'STOP: client does not provide explicit LAN plaintext option' >&2
  exit 2
fi

{
  cat <<'SQL'
SET @rk_total := (
  SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()
);
SET @rk_base := (
  SELECT COUNT(*) FROM information_schema.tables
  WHERE table_schema=DATABASE() AND table_name IN
    ('rakurs_devices','rakurs_signals','rakurs_samples',
     'rakurs_device_events','rakurs_ingest_batches')
);
SET @rk_guard := IF(
  DATABASE()='rakurs' AND @rk_total=5 AND @rk_base=5,
  'SELECT ''RAKURS_EXISTING_FIVE_TABLES_PASS''',
  'RAKURS_ABORT_SCHEMA_NOT_INITIAL_FIVE_TABLES'
);
PREPARE rk_guard FROM @rk_guard;
EXECUTE rk_guard;
DEALLOCATE PREPARE rk_guard;
SQL
  cat "$FILE"
  cat <<'SQL'
SET @rk_total_after := (
  SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()
);
SET @rk_auth := (
  SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()
  AND table_name IN ('rakurs_users','rakurs_login_attempts')
);
SET @rk_verify := IF(
  DATABASE()='rakurs' AND @rk_total_after=7 AND @rk_auth=2,
  'SELECT ''RAKURS_AUTH_SCHEMA_PASS''',
  'RAKURS_AUTH_SCHEMA_VERIFY_FAILED'
);
PREPARE rk_verify FROM @rk_verify;
EXECUTE rk_verify;
DEALLOCATE PREPARE rk_verify;
SHOW TABLES;
SQL
} | "$CLIENT" --no-defaults --protocol=TCP \
      --host=192.168.5.30 --port=3306 --database=rakurs --user=Ivital \
      --batch --skip-column-names --connect-timeout=7 \
      "${TRANSPORT[@]}" --password

echo 'AUTH TABLES INSTALLED; no default user or password created.'
