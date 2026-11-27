#!/usr/bin/env bash
# Read-only Rakurs LAN MySQL preflight. No passwords stored, no database writes.
# Plain TCP MySQL is explicitly chosen ONLY for trusted LAN 192.168.5.30.
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
    print("Check MySQL bind-address, port and local firewall.")
    raise SystemExit(2)
PY

if [[ "$MODE" == "--network" ]]; then
  echo "NETWORK ONLY PASS; database authentication not tested."
  exit 0
fi

DB_USER="${RAKURS_DB_USER:-}"
if [[ -z "$DB_USER" ]]; then
  echo "STOP: set RAKURS_DB_USER (the MySQL account allowed to access 'rakurs')." >&2
  exit 2
fi
if command -v mariadb >/dev/null 2>&1; then
  CLIENT="$(command -v mariadb)"
elif command -v mysql >/dev/null 2>&1; then
  CLIENT="$(command -v mysql)"
else
  echo "STOP: no MySQL/MariaDB client available" >&2
  exit 2
fi

HELP="$("$CLIENT" --no-defaults --help 2>&1)"
if grep -q -- '--ssl-mode' <<< "$HELP"; then
  TRANSPORT_ARGS=(--ssl-mode=DISABLED)
elif grep -q -- '--ssl' <<< "$HELP"; then
  TRANSPORT_ARGS=(--skip-ssl)
else
  echo "STOP: client cannot explicitly disable TLS; no login attempted" >&2
  exit 2
fi
[[ -r /dev/tty ]] || { echo "STOP: interactive terminal required" >&2; exit 2; }

echo "=== DATABASE LOGIN (READ ONLY; NO TLS ON TRUSTED LAN) ==="
echo "Note: network traffic and authentication exchange are not TLS encrypted."
echo "Enter the MySQL password in the hidden client prompt; do not post it to chat."
RESULT="$("$CLIENT" --no-defaults --protocol=TCP \
  "--host=$DB_HOST" "--port=$DB_PORT" "--database=$DB_NAME" \
  "--user=$DB_USER" --connect-timeout=7 --batch --skip-column-names \
  "${TRANSPORT_ARGS[@]}" --password \
  --execute="SELECT CONCAT('DATABASE=', DATABASE(), ' VERSION=', VERSION(), ' ACCOUNT=', CURRENT_USER());
SHOW SESSION STATUS LIKE 'Ssl_cipher';
SELECT CONCAT('TABLES=', COUNT(*)) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE();")"
printf '%s\n' "$RESULT"
grep -Fq 'DATABASE=rakurs ' <<< "$RESULT" || { echo "STOP: unexpected database" >&2; exit 2; }
grep -Eq '^Ssl_cipher[[:space:]]*$' <<< "$RESULT" || {
  echo "STOP: expected unencrypted LAN connection, but TLS status differs" >&2
  exit 2
}
grep -Eq '^TABLES=[0-9]+$' <<< "$RESULT" || {
  echo "STOP: cannot verify number of tables" >&2
  exit 2
}
echo "RAKURS MYSQL READ-ONLY LOGIN PASS (NO TLS, NO WRITES)"
