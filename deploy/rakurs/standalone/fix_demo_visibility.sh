#!/usr/bin/env bash
# Show the existing one-shot Deye data in the standalone Rakurs UI.
# No schema updates, no insertions, no other services changed.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: fix_demo_visibility.sh [--check|--apply]' >&2; exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
cd "$ROOT"
TARGET=/opt/rakurs/standalone

for item in auth.py templates/login.html static/app.js; do
  test -s "$HERE/$item" || { echo "STOP: source $item missing"; exit 2; }
done
python3 -m py_compile "$HERE/auth.py"
if command -v node >/dev/null; then
  node --check "$HERE/static/app.js"
fi
grep -Fq 'window.location.replace(target.pathname + target.search + target.hash)' \
  "$HERE/static/app.js"
grep -Fq 'name="demo" value="1"' "$HERE/templates/login.html"
echo 'RAKURS_DEMO_DISPLAY_SOURCES=PASS'
if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS; DATABASE_AND_SERVER_UNCHANGED'
  exit 0
fi

sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected source branch' >&2; exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: Rakurs standalone not active' >&2; exit 2
}
for item in auth.py templates/login.html static/app.js; do
  sudo test -s "$TARGET/$item" || {
    echo "STOP: installed $item missing" >&2; exit 2
  }
done

BACKUP="$(sudo mktemp -d /var/backups/rakurs-display-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
for item in auth.py templates/login.html static/app.js; do
  sudo install -d -m 0700 "$BACKUP/$(dirname "$item")"
  sudo cp -p "$TARGET/$item" "$BACKUP/$item"
done
echo "RAKURS_DISPLAY_BACKUP=$BACKUP"

rollback() {
  rc=$?
  trap - ERR
  echo 'DISPLAY DEPLOY FAILED: restoring previous Rakurs files' >&2
  for item in auth.py templates/login.html static/app.js; do
    sudo cp -p "$BACKUP/$item" "$TARGET/$item" || true
  done
  sudo systemctl restart rakurs-standalone.service || true
  echo "RESTORE_FILES=$BACKUP" >&2
  exit "$rc"
}
trap rollback ERR

echo '=== INSTALL VIEW AND LOGIN FIX ==='
for item in auth.py templates/login.html static/app.js; do
  sudo install -o root -g rakurs -m 0640 "$HERE/$item" "$TARGET/$item"
done
sudo systemctl restart rakurs-standalone.service

echo '=== WAIT FOR LOCAL RAKURS ==='
READY=NO
for attempt in $(seq 1 25); do
  LOGIN="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/login || true)"
  [[ "$LOGIN" == 200 ]] || { sleep 1; continue; }
  DEMOLOGIN="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
    'http://127.0.0.1:8765/login?demo=1' || true)"
  if grep -Fq 'name="demo" value="1"' <<<"$DEMOLOGIN"; then
    READY=YES
    break
  fi
  sleep 1
done
[[ "$READY" == YES ]] || {
  sudo journalctl -u rakurs-standalone.service -n 50 --no-pager >&2 || true
  echo 'STOP: new login not ready' >&2
  false
}
echo "LOGIN_HTTP=$LOGIN DEMO_LOGIN_FORM=PASS ATTEMPT=$attempt"

echo '=== CHECK ANONYMOUS REDIRECT AND STATIC JAVASCRIPT ==='
HEADER="$(curl --noproxy '*' -sS -D - -o /dev/null \
  'http://127.0.0.1:8765/?demo=1' | tr -d '\r')"
grep -Eq '^HTTP/[0-9.]+ 303 ' <<<"$HEADER"
grep -Eiq '^location: /login\?demo=1$' <<<"$HEADER"
JS_TMP="$(mktemp)"
trap 'rm -f "$JS_TMP"' EXIT
curl --noproxy '*' -fsS --max-time 8 \
  -o "$JS_TMP" http://127.0.0.1:8765/static/app.js
grep -Fq 'window.location.replace(target.pathname + target.search + target.hash)' "$JS_TMP"
echo 'DEMO_REDIRECT_AND_JS=PASS'

echo '=== VERIFY REAL MYSQL DATASET WITHOUT ALTERATION ==='
sudo -u rakurs /var/lib/rakurs/.venv/bin/python - <<'PY'
import pymysql
from pathlib import Path

db = pymysql.connect(
    host="192.168.5.30", port=3306, database="rakurs",
    user="rakurs_web",
    password=Path("/etc/rakurs/mysql_password").read_text().strip(),
    ssl_disabled=True, connect_timeout=5, read_timeout=8)
try:
    with db.cursor() as cur:
        for table, count in (
            ("rakurs_devices", 1),
            ("rakurs_signals", 316),
            ("rakurs_samples", 316),
            ("rakurs_modbus_fields", 624),
            ("rakurs_modbus_field_samples", 624),
            ("rakurs_modbus_register_inventory", 815),
        ):
            cur.execute("SELECT COUNT(*) FROM " + table)
            actual = cur.fetchone()[0]
            print(table + "=" + str(actual))
            if actual != count:
                raise SystemExit("Unexpected MySQL dataset; no writes made")
        cur.execute(
            "SELECT s.value_decimal FROM rakurs_samples s "
            "JOIN rakurs_signals f ON f.id=s.signal_id "
            "WHERE f.signal_key='pv_power' AND s.quality='simulated' "
            "AND s.ingest_source='rakurs_demo_one_shot' "
            "LIMIT 1"
        )
        found = cur.fetchone()
        if found is None:
            raise SystemExit("STOP: synthetic pv_power not available")
        print("SIMULATED_PV_POWER=" + str(found[0]))
finally:
    db.close()
print("MYSQL_READ_ONLY_DEMO_VERIFIED=PASS")
PY
sudo nginx -t
trap - ERR
echo 'RAKURS_DEMO_VISIBLE_CODE=PASS'
echo 'OPEN=https://rakurs.zakryttya.org.ua/?demo=1#/modbus'
echo "BACKUP=$BACKUP"
