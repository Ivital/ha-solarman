#!/usr/bin/env bash
# Show real Rakurs ingest activity in Events without inventing hardware alarms.
# Changes HTML/JS/CSS only; no DB writes, schema changes, or device polling.
set -Eeuo pipefail
umask 077

MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: deploy_events_ui.sh [--check|--apply]' >&2; exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
TARGET=/opt/rakurs/standalone
DOMAIN=rakurs.zakryttya.org.ua
FILES=(
  "templates/index.html"
  "static/app-events-20261124.js"
  "static/events-ui-20261124.css"
)
cd "$ROOT"

for item in "${FILES[@]}"; do
  test -s "$HERE/$item" || {
    echo "STOP: missing source $item" >&2; exit 2
  }
done
grep -Fq 'src="/static/app-events-20261124.js"' "$HERE/templates/index.html"
grep -Fq 'href="/static/events-ui-20261124.css"' "$HERE/templates/index.html"
grep -Fq 'function renderEvents(cycle)' "$HERE/static/app-events-20261124.js" ||
  grep -Fq 'async function renderEvents(cycle)' "$HERE/static/app-events-20261124.js"
grep -Fq 'api("/batches")' "$HERE/static/app-events-20261124.js"
grep -Fq 'api("/events")' "$HERE/static/app-events-20261124.js"
grep -Fq 'restoreServerReadings();' "$HERE/static/app-events-20261124.js"
if command -v node >/dev/null; then
  node --check "$HERE/static/app-events-20261124.js"
  node tests/test_rakurs_clarity_ui.js
fi
echo 'RAKURS_EVENTS_PREFLIGHT=PASS'

if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS MYSQL_WRITES=0'
  exit 0
fi

sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected branch' >&2; exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: Rakurs service is inactive' >&2; exit 2
}
sudo test -s "$TARGET/templates/index.html" || {
  echo 'STOP: current HTML missing' >&2; exit 2
}

BACKUP="$(sudo mktemp -d /var/backups/rakurs-events-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
for item in "${FILES[@]}"; do
  if sudo test -e "$TARGET/$item"; then
    sudo install -d -m 0700 "$BACKUP/$(dirname "$item")"
    sudo cp -p "$TARGET/$item" "$BACKUP/$item"
  fi
done
echo "EVENTS_UI_BACKUP=$BACKUP"
rollback() {
  local rc=$?
  trap - ERR
  echo 'EVENTS DEPLOY ERROR: restoring previous Rakurs assets' >&2
  for item in "${FILES[@]}"; do
    if sudo test -e "$BACKUP/$item"; then
      sudo cp -p "$BACKUP/$item" "$TARGET/$item" || true
    else
      sudo rm -f "$TARGET/$item" || true
    fi
  done
  sudo systemctl restart rakurs-standalone.service || true
  echo "EVENTS_UI_BACKUP=$BACKUP" >&2
  exit "$rc"
}
trap rollback ERR

echo '=== INSTALL EVENTS JOURNAL IN THE EXISTING RAKURS WEBSITE ==='
for item in "${FILES[@]}"; do
  sudo install -o root -g rakurs -m 0640 "$HERE/$item" "$TARGET/$item"
done
sudo systemctl restart rakurs-standalone.service

READY=NO
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' -s --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/login || true)"
  SCRIPT="$(curl --noproxy '*' -s --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/static/app-events-20261124.js || true)"
  if [[ "$LOGIN" == 200 && "$SCRIPT" == 200 ]] &&
       systemctl is-active --quiet rakurs-standalone.service; then
    READY=YES; break
  fi
  sleep 1
done
[[ "$READY" == YES ]] || {
  echo "STOP: Gunicorn not ready LOGIN=$LOGIN JS=$SCRIPT" >&2
  sudo journalctl -u rakurs-standalone.service -n 50 --no-pager >&2 || true
  false
}
echo "GUNICORN_READY=PASS ATTEMPT=$attempt"

echo '=== VERIFY AUTHENTICATED MYSQL JOURNAL, BATCHES, MAIN DASHBOARD ==='
sudo -u rakurs env \
  PYTHONPATH=/opt/rakurs \
  RAKURS_DB_USER=rakurs_web \
  RAKURS_DB_PASSWORD_FILE=/etc/rakurs/mysql_password \
  RAKURS_SESSION_SECRET_FILE=/etc/rakurs/session_secret \
  /var/lib/rakurs/.venv/bin/python - <<'PY'
from importlib import import_module
import time

m = import_module("standalone.app")
with m.open_db() as db:
    with db.cursor() as cur:
        cur.execute("SELECT id, session_version FROM rakurs_users "
                    "WHERE is_active=1 ORDER BY id LIMIT 1")
        user = cur.fetchone()
if not user:
    raise SystemExit("STOP: no active Rakurs account")
client = m.app.test_client()
with client.session_transaction(base_url="https://localhost") as sess:
    sess["uid"] = user["id"]
    sess["ver"] = user["session_version"]
    sess["at"] = int(time.time())

home = client.get("/", base_url="https://localhost")
assert home.status_code == 200
html = home.get_data(as_text=True)
for marker in (
    "/static/app-events-20261124.js",
    "/static/events-ui-20261124.css",
    'Показники Deye з бази даних',
    "5.1", "6.4", "72",
):
    assert marker in html, "Home page missing " + marker
print("EVENTS_MAIN_MYSQL_HOME=PASS")
def get(path):
    response = client.get("/api/v1/" + path, base_url="https://localhost")
    assert response.status_code == 200, (path, response.status_code)
    return response.get_json()
events = get("events")
batches = get("batches")
assert isinstance(events["events"], list)
assert isinstance(batches["batches"], list)
print("DEVICE_EVENT_RECORDS=" + str(len(events["events"])))
print("INGEST_BATCH_RECORDS=" + str(len(batches["batches"])))
assert any(b.get("error_code") == "DEYE_HISTORY_72H_V1"
           for b in batches["batches"]), "Missing original 72-hour import batch"
print("HISTORY_IMPORT_BATCH=PASS")
status = get("status")
assert status["mode"] == "simulation", status
assert status["simulated_signals"] == 316, status
print("RAKURS_MYSQL_READ_ONLY=PASS")
PY

echo '=== VERIFY BOTH UNIQUE ASSETS FROM PUBLIC AND LOCAL HTTPS ==='
for item in static/app-events-20261124.js static/events-ui-20261124.css; do
  expected="$(sha256sum "$HERE/$item" | awk '{print $1}')"
  tmp="$(mktemp)"
  for route in LOCAL PUBLIC; do
    if [[ "$route" == LOCAL ]]; then
      curl --noproxy '*' -fsS --connect-timeout 4 --max-time 15 \
        --resolve "$DOMAIN:443:127.0.0.1" \
        "https://$DOMAIN/$item" -o "$tmp"
    else
      curl --noproxy '*' -fsS --connect-timeout 4 --max-time 15 \
        "https://$DOMAIN/$item" -o "$tmp"
    fi
    observed="$(sha256sum "$tmp" | awk '{print $1}')"
    echo "HTTPS_$(basename "$item")_$route=$observed"
    if [[ "$observed" != "$expected" ]]; then
      rm -f "$tmp"
      echo "STOP: HTTPS asset mismatch ($route)" >&2
      false
    fi
  done
  rm -f "$tmp"
done
sudo nginx -t
trap - ERR
echo 'RAKURS_EVENTS_UI=PASS'
echo 'MYSQL_WRITES=0'
echo 'OPEN=https://rakurs.zakryttya.org.ua/#/events'
echo "EVENTS_UI_BACKUP=$BACKUP"
