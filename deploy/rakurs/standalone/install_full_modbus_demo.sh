#!/usr/bin/env bash
# Deploy complete Deye P3 demo to existing standalone Rakurs without touching Nginx.
# Only two web files change; simulator inserts a single synthetic DB batch.
set -Eeuo pipefail
# Historical installer. Keep --check for forensic audits, but never permit an
# old deployment entrypoint to overwrite the current twin-device release.
if [[ "${1:---check}" == "--apply" ]]; then
  echo 'STOP: legacy deploy disabled; use deploy_parallel_ui.sh --apply' >&2
  exit 2
fi

umask 077

MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--execute" ]] || {
  echo 'Usage: install_full_modbus_demo.sh [--check|--execute]' >&2; exit 2
}
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
HERE="$ROOT/deploy/rakurs/standalone"
PY="$HERE/simulate_deye_once.py"
TARGET="/opt/rakurs/standalone"
PYTHON="/var/lib/rakurs/.venv/bin/python"

test -s "$HERE/app.py"
test -s "$HERE/static/app.js"
test -s "$PY"
test -s "$HERE/modbus_full_schema.sql"
test -s "$ROOT/deploy/rakurs/modbus_full_catalog.json"
test -s "$ROOT/deploy/rakurs/signal_catalog.json"
python3 -m py_compile "$HERE/app.py" "$PY"
python3 "$PY"
if command -v node >/dev/null; then
  node --check "$HERE/static/app.js"
fi
echo "FULL_MODBUS_DEMO_FILES=PASS"
if [[ "$MODE" == "--check" ]]; then
  echo "CHECK_ONLY=PASS; NO SYSTEM OR DATABASE CHANGES"
  exit 0
fi

sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: wrong repository branch' >&2; exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: standalone service inactive before deployment' >&2
  exit 2
}

# The previous interrupted attempt should have restored the old app. Do not
# depend on this: check and save the actual current working installed files.
# /opt/rakurs is 0750 root:rakurs; the deploying user cannot stat
# its files directly. Test as root and print an explicit diagnostic.
sudo test -s "$TARGET/app.py" || {
  echo 'STOP: installed Rakurs app.py missing or empty' >&2
  exit 2
}
sudo test -s "$TARGET/static/app.js" || {
  echo 'STOP: installed Rakurs app.js missing or empty' >&2
  exit 2
}
BACKUP="$(sudo mktemp -d /var/backups/rakurs-demo-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
sudo cp -p "$TARGET/app.py" "$BACKUP/app.py"
sudo cp -p "$TARGET/static/app.js" "$BACKUP/app.js"
echo "WEB_BACKUP=$BACKUP"

RESTORE=1
restore_on_error() {
  rc=$?
  trap - ERR
  if (( RESTORE )); then
    echo 'DEPLOYMENT FAILED: restoring previous Rakurs web files' >&2
    sudo cp -p "$BACKUP/app.py" "$TARGET/app.py" || true
    sudo cp -p "$BACKUP/app.js" "$TARGET/static/app.js" || true
    sudo systemctl restart rakurs-standalone.service || true
    for i in $(seq 1 15); do
      code="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
        -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/login || true)"
      if [[ "$code" == 200 ]]; then
        echo 'PREVIOUS_RAKURS_RESTORED=PASS'
        break
      fi
      sleep 1
    done
  fi
  echo "WEB_BACKUP=$BACKUP" >&2
  exit "$rc"
}
trap restore_on_error ERR

echo "=== DEPLOY TWO WEB FILES ==="
sudo install -o root -g rakurs -m 0640 "$HERE/app.py" "$TARGET/app.py"
sudo install -o root -g rakurs -m 0640 "$HERE/static/app.js" "$TARGET/static/app.js"
sudo systemctl restart rakurs-standalone.service

echo "=== WAIT FOR GUNICORN TO BECOME READY ==="
READY=NO
LOGIN=000
API=000
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/login || true)"
  if [[ "$LOGIN" == 200 ]] && systemctl is-active --quiet rakurs-standalone.service; then
    API="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
      -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/v1/modbus-fields || true)"
    if [[ "$API" == 401 ]]; then
      READY=YES
      echo "RAKURS_READY_ON_ATTEMPT=$attempt"
      break
    fi
  fi
  sleep 1
done
if [[ "$READY" != YES ]]; then
  echo "STOP: web service did not become ready; LOGIN=$LOGIN MODBUS_API=$API" >&2
  sudo journalctl -u rakurs-standalone.service -n 70 --no-pager -o cat >&2 || true
  false
fi
echo "LOGIN_HTTP=$LOGIN ANONYMOUS_MODBUS_API_HTTP=$API"

echo "=== ONE SYNTHETIC MYSQL READ CYCLE ==="
# Interactive confirmation, password hidden; create only when all monitoring
# tables are empty. Seed script checks all target counts before DB COMMIT.
sudo "$PYTHON" "$PY" --execute </dev/tty

# At this point DB was committed: do not roll back the web to an old schema
# version on a later verification failure.
RESTORE=0
trap - ERR

echo "=== VERIFY COMMITTED MYSQL COUNTS ==="
sudo -u rakurs "$PYTHON" - <<'PY'
from pathlib import Path
import pymysql

p = Path("/etc/rakurs/mysql_password")
db = pymysql.connect(
    host="192.168.5.30", port=3306, database="rakurs",
    user="rakurs_web", password=p.read_text().strip(),
    ssl_disabled=True, connect_timeout=5, read_timeout=10)
try:
    with db.cursor() as cur:
        expected = {
            "rakurs_devices": 1,
            "rakurs_signals": 316,
            "rakurs_samples": 316,
            "rakurs_ingest_batches": 1,
            "rakurs_modbus_fields": 624,
            "rakurs_modbus_field_samples": 624,
            "rakurs_modbus_register_inventory": 815,
        }
        for table, count in expected.items():
            cur.execute("SELECT COUNT(*) FROM " + table)
            actual = cur.fetchone()[0]
            print(f"{table.upper()}={actual}")
            if actual != count:
                raise SystemExit(f"VERIFY FAILED: {table}, expected {count}")
        cur.execute("SELECT COUNT(*) FROM rakurs_samples WHERE quality='simulated'")
        if cur.fetchone()[0] != 316:
            raise SystemExit("VERIFY FAILED: quality labels")
        cur.execute("SELECT COUNT(*) FROM rakurs_modbus_fields WHERE verified=1")
        if cur.fetchone()[0] != 0:
            raise SystemExit("VERIFY FAILED: profile fields must remain unverified")
finally:
    db.close()
print("RAKURS_FULL_DEYE_DATABASE=PASS")
PY

systemctl is-active --quiet rakurs-standalone.service
sudo nginx -t
echo 'FULL_MODBUS_DEMO_DEPLOYED=PASS'
echo 'SITE=https://rakurs.zakryttya.org.ua/?demo=1#/modbus'
echo "WEB_BACKUP=$BACKUP"
