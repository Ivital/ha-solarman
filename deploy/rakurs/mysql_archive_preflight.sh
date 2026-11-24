#!/usr/bin/env bash
# RAKURS MySQL archive: no secret persistence, no SQL writes.
set -Eeuo pipefail
DB_HOST='kshz1.mysql.tools'
DB_PORT=3306
DB_NAME='kshz1_rakurs'
DB_USER='kshz1_rakurs'
MODE="${1:---network}"
if [[ "$MODE" != "--network" && "$MODE" != "--login" ]]; then
  echo 'Usage: mysql_archive_preflight.sh [--network|--login]' >&2
  exit 2
fi
echo '=== MYSQL ARCHIVE NETWORK CHECK: NO PASSWORD ==='
python3 - "$DB_HOST" "$DB_PORT" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=7):
        print("MySQL TCP:", host, "port", port, "PASS")
except OSError as exc:
    print("MySQL TCP: FAIL:", type(exc).__name__, str(exc))
    print("Check Remote MySQL allowlist and custom port at hosting provider.")
    raise SystemExit(2)
PY
if [[ "$MODE" == "--network" ]]; then
  echo 'TCP check complete; authentication and TLS identity not tested.'
  exit 0
fi
if command -v mysql >/dev/null 2>&1; then
  CLIENT="$(command -v mysql)"
elif command -v mariadb >/dev/null 2>&1; then
  CLIENT="$(command -v mariadb)"
else
  echo 'STOP: no MySQL/MariaDB command-line client; no changes made' >&2
  exit 2
fi
CA_FILE="${RAKURS_MYSQL_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
test -s "$CA_FILE" || { echo 'STOP: CA bundle missing' >&2; exit 2; }
CLIENT_HELP="$("$CLIENT" --no-defaults --help 2>&1)"
if grep -q -- '--ssl-mode' <<< "$CLIENT_HELP"; then
  TLS_ARGS=(--ssl-mode=VERIFY_IDENTITY "--ssl-ca=$CA_FILE")
elif grep -q -- '--ssl-verify-server-cert' <<< "$CLIENT_HELP"; then
  TLS_ARGS=(--ssl "--ssl-ca=$CA_FILE" --ssl-verify-server-cert)
else
  echo 'STOP: client cannot verify certificate and host identity' >&2
  exit 2
fi
if [[ ! -r /dev/tty ]]; then
  echo 'STOP: secure interactive terminal unavailable' >&2
  exit 2
fi
echo '=== VERIFIED TLS + READ ONLY SQL ==='
echo 'Enter the NEW rotated database password at the hidden client prompt.'
echo 'Do not enter it in a command argument, log, or chat.'
RESULT="$("$CLIENT" --no-defaults --protocol=TCP "--host=$DB_HOST" "--port=$DB_PORT" \
  "--database=$DB_NAME" "--user=$DB_USER" --connect-timeout=7 \
  --default-character-set=utf8mb4 --batch --skip-column-names \
  "${TLS_ARGS[@]}" --password \
  --execute="SELECT CONCAT('DATABASE=', DATABASE(), ' VERSION=', VERSION(), ' ACCOUNT=', CURRENT_USER());
SHOW SESSION STATUS LIKE 'Ssl_cipher';
SELECT CONCAT('TABLES=', COUNT(*)) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE();")"
printf '%s\n' "$RESULT"
grep -Fq 'DATABASE=kshz1_rakurs ' <<< "$RESULT" || { echo 'STOP: wrong database' >&2; exit 2; }
grep -Eq '^Ssl_cipher[[:space:]]+[^[:space:]]+' <<< "$RESULT" || { echo 'STOP: encrypted cipher not confirmed' >&2; exit 2; }
echo 'READ-ONLY VERIFIED TLS DATABASE CHECK: PASS'
