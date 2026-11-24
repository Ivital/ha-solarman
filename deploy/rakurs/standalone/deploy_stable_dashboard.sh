#!/usr/bin/env bash
# Fix the existing Rakurs dashboard: keep SSR MySQL values until browser API succeeds.
# No database writes, schema changes, simulation inserts, Nginx or HA changes.
set -Eeuo pipefail
umask 077

MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: deploy_stable_dashboard.sh [--check|--apply]' >&2
  exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
TARGET=/opt/rakurs/standalone
DOMAIN=rakurs.zakryttya.org.ua
ASSET=/static/app-stable-20261124.js
FILES=(static/app.js static/app-stable-20261124.js templates/index.html)
cd "$ROOT"

for file in "${FILES[@]}"; do
  test -s "$HERE/$file" || {
    echo "STOP: source $file not found" >&2; exit 2
  }
done
cmp -s "$HERE/static/app.js" "$HERE/static/app-stable-20261124.js" || {
  echo 'STOP: cached and stable JS diverged' >&2; exit 2
}
grep -Fq 'src="/static/app-stable-20261124.js"' "$HERE/templates/index.html"
grep -Fq 'data-initial-mode=' "$HERE/templates/index.html"
grep -Fq 'restoreServerReadings();' "$HERE/static/app.js"
grep -Fq 'client API incomplete' "$HERE/static/app.js"
if command -v node >/dev/null; then
  node --check "$HERE/static/app.js"
  node --check "$HERE/static/app-stable-20261124.js"
fi
echo 'STABLE_DASHBOARD_PREFLIGHT=PASS'

if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS MYSQL_WRITES=0'
  exit 0
fi

sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected branch' >&2; exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: Rakurs service not active' >&2; exit 2
}
for file in static/app.js templates/index.html; do
  sudo test -s "$TARGET/$file" || {
    echo "STOP: installed $file absent" >&2; exit 2
  }
done

BACKUP="$(sudo mktemp -d /var/backups/rakurs-ssr-guard-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
for file in static/app.js templates/index.html; do
  sudo install -d -m 0700 "$BACKUP/$(dirname "$file")"
  sudo cp -p "$TARGET/$file" "$BACKUP/$file"
done
STABLE_EXISTED=NO
if sudo test -e "$TARGET$ASSET"; then
  STABLE_EXISTED=YES
  sudo cp -p "$TARGET$ASSET" "$BACKUP/$(basename "$ASSET")"
fi
echo "RAKURS_STABLE_BACKUP=$BACKUP"
RESTORE=1
rollback() {
  rc=$?
  trap - ERR
  if (( RESTORE )); then
    echo 'STABLE DEPLOY FAILURE: restoring Rakurs assets' >&2
    for file in static/app.js templates/index.html; do
      sudo cp -p "$BACKUP/$file" "$TARGET/$file" || true
    done
    if [[ "$STABLE_EXISTED" == YES ]]; then
      sudo cp -p "$BACKUP/$(basename "$ASSET")" "$TARGET$ASSET" || true
    else
      sudo rm -f "$TARGET$ASSET"
    fi
    sudo systemctl restart rakurs-standalone.service || true
  fi
  echo "RAKURS_STABLE_BACKUP=$BACKUP" >&2
  exit "$rc"
}
trap rollback ERR

echo '=== APPLY GUARDED RAKURS FRONTEND ==='
for file in "${FILES[@]}"; do
  sudo install -o root -g rakurs -m 0640 "$HERE/$file" "$TARGET/$file"
done
sudo systemctl restart rakurs-standalone.service

READY=NO
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' -s --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/login || true)"
  JS="$(curl --noproxy '*' -s --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' "http://127.0.0.1:8765$ASSET" || true)"
  if [[ "$LOGIN" == 200 && "$JS" == 200 ]] &&
      systemctl is-active --quiet rakurs-standalone.service; then
    READY=YES
    break
  fi
  sleep 1
done
[[ "$READY" == YES ]] || {
  echo "STOP: frontend not serving LOGIN=$LOGIN SCRIPT=$JS" >&2
  sudo journalctl -u rakurs-standalone.service -n 50 --no-pager >&2 || true
  false
}
echo "GUNICORN_READY=PASS ATTEMPT=$attempt"

echo '=== VERIFY MAIN HOMEPAGE MY SQL READINGS AND HISTORY ==='
sudo -u rakurs env \
  PYTHONPATH=/opt/rakurs \
  RAKURS_DB_USER=rakurs_web \
  RAKURS_DB_PASSWORD_FILE=/etc/rakurs/mysql_password \
  RAKURS_SESSION_SECRET_FILE=/etc/rakurs/session_secret \
  /var/lib/rakurs/.venv/bin/python - <<'PY'
import importlib
import time

m = importlib.import_module("standalone.app")
with m.open_db() as db:
    with db.cursor() as cursor:
        cursor.execute("SELECT id, session_version FROM rakurs_users "
                       "WHERE is_active=1 ORDER BY id LIMIT 1")
        user = cursor.fetchone()
if not user:
    raise SystemExit("STOP: no active Rakurs account")
client = m.app.test_client()
with client.session_transaction(base_url="https://localhost") as session:
    session["uid"] = user["id"]
    session["ver"] = user["session_version"]
    session["at"] = int(time.time())
page = client.get("/", base_url="https://localhost")
assert page.status_code == 200, page.status_code
html = page.get_data(as_text=True)
for marker in (
    'src="/static/app-stable-20261124.js"',
    'data-initial-mode="simulation"',
    "5.1", "6.4", "72",
):
    assert marker in html, "Missing root MySQL reading or updated script: " + marker
print("PRIMARY_HTML_WITH_MYSQL_VALUES=PASS")

status = client.get("/api/v1/status", base_url="https://localhost")
assert status.status_code == 200
assert status.get_json()["mode"] == "simulation"
latest = client.get(
    "/api/v1/latest?keys=pv_power,load_power,battery_soc",
    base_url="https://localhost",
)
assert latest.status_code == 200
values = latest.get_json()["values"]
for name, expected in (
    ("pv_power", 5.1),
    ("load_power", 6.4),
    ("battery_soc", 72.0),
):
    assert abs(float(values[name]["value_decimal"]) - expected) < 0.00001
    assert values[name]["quality"] == "simulated"
print("PRIMARY_LATEST_API=PASS")
history = client.get(
    "/api/v1/history?key=pv_power&hours=24",
    base_url="https://localhost",
)
assert history.status_code == 200
assert len(history.get_json()["points"]) >= 12
print("HISTORY_24H=PASS")
PY

echo '=== VERIFY JAVASCRIPT OVER LOCAL AND PUBLIC HTTPS ==='
EXPECTED="$(sha256sum "$HERE/static/app-stable-20261124.js" | awk '{print $1}')"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
for ROUTE in LOCAL PUBLIC; do
  if [[ "$ROUTE" == LOCAL ]]; then
    curl --noproxy '*' -fsS --connect-timeout 4 --max-time 15 \
      --resolve "$DOMAIN:443:127.0.0.1" \
      "https://$DOMAIN$ASSET" -o "$TMP"
  else
    curl --noproxy '*' -fsS --connect-timeout 4 --max-time 15 \
      "https://$DOMAIN$ASSET" -o "$TMP"
  fi
  ACTUAL="$(sha256sum "$TMP" | awk '{print $1}')"
  echo "SCRIPT_SHA256_$ROUTE=$ACTUAL"
  [[ "$ACTUAL" == "$EXPECTED" ]] || {
    echo "STOP: stale JS over $ROUTE HTTPS" >&2; false
  }
done
sudo nginx -t

RESTORE=0
trap - ERR
echo 'RAKURS_STABLE_MAIN=PASS'
echo 'MYSQL_WRITES=0'
echo 'OPEN=https://rakurs.zakryttya.org.ua/'
echo "RAKURS_STABLE_BACKUP=$BACKUP"
