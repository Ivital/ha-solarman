#!/usr/bin/env bash
# Read-only Rakurs LAN MySQL preflight. No secret persistence or database writes.
set -Eeuo pipefail

DB_HOST="192.168.5.30"
DB_PORT="${RAKURS_DB_PORT:-3306}"
DB_NAME="rakurs"
MODE="${1:---network}"
if [[ "$MODE" != "--network" && "$MODE" != "--login" ]]; then
  echo "Usage: bash mysql_archive_preflight.sh [--network|--login]" >&2
  exit 2
fi
echo "=== RAKURS LAN MYSQL: $DB_HOST:$DB_PORT / $DB_NAME ==="
python3 - "$DB_HOST" "$DB_PORT" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=7):
        print("MYSQL TCP PASS")
except OSError as exc:
    print("MYSQL TCP FAIL:", type(exc).__name__, str(exc))
    print("Check MySQL bind-address, port 3306 and firewall on 192.168.5.30.")
    raise SystemExit(2)
PY
if [[ "$MODE" == "--network" ]]; then
  echo "NETWORK ONLY PASS; user, password, TLS and database permissions not tested."
  exit 0
fi

DB_USER="${RAKURS_DB_USER:-}"
if [[ -z "$DB_USER" ]]; then
  echo "STOP: set RAKURS_DB_USER to the MySQL user allowed to access 'rakurs'." >&2
  echo "Password must be entered only at the hidden prompt; never pass it in env." >&2
  exit 2
fi
if command -v mariadb >/dev/null 2>&1; then
  CLIENT="$(command -v mariadb)"
elif command -v mysql >/dev/null 2>&1; then
  CLIENT="$(command -v mysql)"
else
  echo "STOP: MySQL/MariaDB client not installed" >&2
  exit 2
fi
CA_FILE="${RAKURS_MYSQL_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
test -s "$CA_FILE" || { echo "STOP: CA bundle not available" >&2; exit 2; }
HELP="$("$CLIENT" --no-defaults --help 2>&1)"
if grep -q -- '--ssl-mode' <<< "$HELP"; then
  TLS=(--ssl-mode=VERIFY_IDENTITY "--ssl-ca=$CA_FILE")
elif grep -q -- '--ssl-verify-server-cert' <<< "$HELP"; then
  TLS=(--ssl "--ssl-ca=$CA_FILE" --ssl-verify-server-cert)
else
  echo "STOP: client cannot verify TLS identity" >&2
  exit 2
fi
test -r /dev/tty || { echo "STOP: no terminal for interactive password" >&2; exit 2; }
echo "=== DATABASE LOGIN (READ ONLY, VERIFIED TLS) ==="
echo "Enter database password in hidden client prompt (not in command/history)."
RESULT="$("$CLIENT" --no-defaults --protocol=TCP "--host=$DB_HOST" \
    "--port=$DB_PORT" "--database=$DB_NAME" "--user=$DB_USER" \
    --connect-timeout=7 --batch --skip-column-names \
    "${TLS[@]}" --password \
    --execute="SELECT CONCAT('DATABASE=', DATABASE(), ' ACCOUNT=', CURRENT_USER());
SHOW SESSION STATUS LIKE 'Ssl_cipher';
SELECT CONCAT('TABLES=', COUNT(*)) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE();")"
printf '%s\n' "$RESULT"
grep -Fq 'DATABASE=rakurs ' <<< "$RESULT" || { echo "STOP: unexpected database" >&2; exit 2; }
grep -Eq '^Ssl_cipher[[:space:]]+[^[:space:]]+' <<< "$RESULT" || { echo "STOP: TLS cipher not confirmed" >&2; exit 2; }
echo "RAKURS MYSQL VERIFIED LOGIN PASS (NO WRITES)"
