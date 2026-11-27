#!/usr/bin/env bash
# Rakurs MySQL 8 LAN archive: optional one-time creation of FIVE EMPTY tables.
# Safety: no DROP/ALTER/DELETE, no changes to HA/ITM, no credentials persisted.
set -Eeuo pipefail
umask 077

HOST='192.168.5.30'
PORT=3306
DATABASE='rakurs'
DB_USER='Ivital'
MODE="${1:---check}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="$DIR/archive_mysql_schema.sql"

if [[ "$MODE" != "--check" && "$MODE" != "--apply" ]]; then
  echo 'Usage: bash mysql_archive_apply_schema.sh [--check|--apply]' >&2
  exit 2
fi

# Fail-closed source validation: only the five fixed CREATE TABLE statements.
python3 - "$SCHEMA" <<'PY'
from pathlib import Path
import re
import sys

sql_path = Path(sys.argv[1])
text = sql_path.read_text(encoding="utf-8")
sql = "\n".join(line for line in text.splitlines()
                if not line.lstrip().startswith("--"))
statements = [stmt.strip() for stmt in sql.split(";") if stmt.strip()]
expected = ("rakurs_devices", "rakurs_signals", "rakurs_samples",
            "rakurs_device_events", "rakurs_ingest_batches")
assert len(statements) == len(expected), "STOP: unexpected DDL statement count"
for name, statement in zip(expected, statements):
    pattern = (r"(?is)\ACREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"
               + name + r"\s*\(.*\)\s*ENGINE=InnoDB\s+"
               r"DEFAULT\s+CHARSET=utf8mb4\s+COLLATE=utf8mb4_unicode_ci\s*\Z")
    assert re.fullmatch(pattern, statement), "STOP: unexpected DDL for " + name
    for forbidden in ("DROP", "ALTER", "TRUNCATE", "DELETE", "INSERT", "UPDATE",
                      "GRANT", "REVOKE", "CREATE USER", "USE "):
        assert not re.search(r"\b" + forbidden + r"\b", statement, flags=re.I), (
            "STOP: forbidden SQL clause in " + name
        )
print("LOCAL_SCHEMA_VALIDATION=PASS; EXPECTED_NEW_TABLES=5")
PY

if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS; NO DATABASE CONNECTION; NO SQL EXECUTED'
  echo "Target: $HOST:$PORT database=$DATABASE (5 empty tables)"
  exit 0
fi

if [[ ! -r /dev/tty ]]; then
  echo 'STOP: interactive terminal required to confirm schema creation' >&2
  exit 2
fi

echo "TARGET: $HOST:$PORT / $DATABASE as $DB_USER"
echo 'Creates only 5 new empty rakurs_* tables. MySQL DDL is not a multi-table transaction.'
echo 'If any statement fails, partial tables may remain; do not rerun blindly.'
read -r -p 'To create the tables, type exactly rakurs: ' CONFIRM < /dev/tty
if [[ "$CONFIRM" != 'rakurs' ]]; then
  echo 'ABORTED: no changes made'
  exit 2
fi
unset CONFIRM

if command -v mariadb >/dev/null 2>&1; then
  CLIENT="$(command -v mariadb)"
elif command -v mysql >/dev/null 2>&1; then
  CLIENT="$(command -v mysql)"
else
  echo 'STOP: MySQL/MariaDB client missing' >&2
  exit 2
fi
HELP="$("$CLIENT" --no-defaults --help 2>&1)"
if grep -q -- '--ssl-mode' <<< "$HELP"; then
  TRANSPORT=(--ssl-mode=DISABLED)
elif grep -q -- '--ssl' <<< "$HELP"; then
  TRANSPORT=(--skip-ssl)
else
  echo 'STOP: client cannot explicitly select LAN plaintext transport' >&2
  exit 2
fi

echo '=== ENTER MYSQL PASSWORD AT HIDDEN PROMPT ==='
{
  # If any tables or views exist, PREPARE an invalid SQL statement: MySQL must
  # abort before the first CREATE. No CREATE ROUTINE privilege is needed.
  cat <<'SQL'
SET @rakurs_existing := (
  SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()
);
SET @rakurs_guard_sql := IF(
  DATABASE() = 'rakurs' AND @rakurs_existing = 0,
  'SELECT ''RAKURS_EMPTY_DATABASE_CONFIRMED''',
  'RAKURS_ABORT_NONEMPTY_OR_WRONG_DATABASE'
);
PREPARE rakurs_guard FROM @rakurs_guard_sql;
EXECUTE rakurs_guard;
DEALLOCATE PREPARE rakurs_guard;
SQL
  cat "$SCHEMA"
  cat <<'SQL'
SELECT CONCAT('RAKURS_TABLES=', COUNT(*))
  FROM information_schema.TABLES
  WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (
    'rakurs_devices','rakurs_signals','rakurs_samples',
    'rakurs_device_events','rakurs_ingest_batches'
  );
SET @rakurs_total := (
  SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()
);
SET @rakurs_verify_sql := IF(
  DATABASE() = 'rakurs' AND @rakurs_total = 5,
  'SELECT ''RAKURS_SCHEMA_VERIFY_PASS''',
  'RAKURS_SCHEMA_VERIFY_FAILED'
);
PREPARE rakurs_verify FROM @rakurs_verify_sql;
EXECUTE rakurs_verify;
DEALLOCATE PREPARE rakurs_verify;
SHOW TABLES;
SQL
} | "$CLIENT" --no-defaults --protocol=TCP "--host=$HOST" "--port=$PORT" \
    "--database=$DATABASE" "--user=$DB_USER" --connect-timeout=7 \
    --batch --skip-column-names --default-character-set=utf8mb4 \
    "${TRANSPORT[@]}" --password

echo 'RAKURS SCHEMA INSTALL COMPLETE (5 EMPTY TABLES, NO SAMPLE DATA)'
