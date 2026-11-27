#!/usr/bin/env bash
# One-step UI modernization for the EXISTING Rakurs website.
# Files only; MySQL, Nginx configuration and real devices remain unchanged.
set -Eeuo pipefail
# Historical installer. Keep --check for forensic audits, but never permit an
# old deployment entrypoint to overwrite the current twin-device release.
if [[ "${1:---check}" == "--apply" ]]; then
  echo 'STOP: legacy deploy disabled; use deploy_parallel_ui.sh --apply' >&2
  exit 2
fi

umask 077
MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: deploy_clarity_ui.sh [--check|--apply]' >&2; exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
cd "$ROOT"
TARGET=/opt/rakurs/standalone
DOMAIN=rakurs.zakryttya.org.ua
JS=static/app-clarity-20261124.js
CSS=static/clarity-ui-20261124.css
HTML=templates/index.html
FILES=("$JS" "$CSS" "$HTML")

for item in "${FILES[@]}"; do
  test -s "$HERE/$item" || { echo "STOP: source $item missing" >&2; exit 2; }
done
grep -Eq '/static/(app-(clarity|events)-20261124|app-parallel-twin-20261125).js' "$HERE/$HTML"
grep -Fq '/static/clarity-ui-20261124.css' "$HERE/$HTML"
grep -Fq 'Показники Deye з бази даних' "$HERE/$HTML"
grep -Fq 'restoreServerReadings();' "$HERE/$JS"
grep -Fq 'function energyFlow(values)' "$HERE/$JS"
grep -Fq 'async function renderTrendDeck(' "$HERE/$JS"
grep -Fq 'async function renderModbus(' "$HERE/$JS"
if command -v node >/dev/null; then
  node --check "$HERE/$JS"
  node tests/test_rakurs_clarity_ui.js
fi
echo 'CLARITY_UI_SOURCE_PREFLIGHT=PASS'
if [[ "$MODE" != "--check" ]] && grep -Eq '/static/(app-events-20261124|app-parallel-twin-20261125).js' "$HERE/$HTML"; then
  echo 'STOP: legacy Clarity installer would revert Events UI. Use deploy_events_ui.sh --apply.' >&2
  exit 2
fi
if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS MYSQL_WRITES=0'
  exit 0
fi

sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected branch' >&2; exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: Rakurs standalone service inactive' >&2; exit 2
}
sudo test -s "$TARGET/$HTML" || { echo 'STOP: installed HTML missing' >&2; exit 2; }
BACKUP="$(sudo mktemp -d /var/backups/rakurs-clarity-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
for item in "${FILES[@]}"; do
  if sudo test -e "$TARGET/$item"; then
    sudo install -d -m 0700 "$BACKUP/$(dirname "$item")"
    sudo cp -p "$TARGET/$item" "$BACKUP/$item"
  fi
done
echo "CLARITY_UI_BACKUP=$BACKUP"

rollback() {
  code=$?
  trap - ERR
  echo 'CLARITY UI FAILURE: restoring previous Rakurs files' >&2
  for item in "${FILES[@]}"; do
    if sudo test -e "$BACKUP/$item"; then
      sudo cp -p "$BACKUP/$item" "$TARGET/$item" || true
    else
      sudo rm -f "$TARGET/$item" || true
    fi
  done
  sudo systemctl restart rakurs-standalone.service || true
  echo "ROLLBACK_SOURCE=$BACKUP" >&2
  exit "$code"
}
trap rollback ERR

echo '=== INSTALL MAIN WEBSITE UI ==='
for item in "${FILES[@]}"; do
  sudo install -o root -g rakurs -m 0640 "$HERE/$item" "$TARGET/$item"
done
sudo systemctl restart rakurs-standalone.service
READY=NO
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 2 --max-time 3 http://127.0.0.1:8765/login || true)"
  STATIC="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 2 --max-time 3 "http://127.0.0.1:8765/$JS" || true)"
  if [[ "$LOGIN" == 200 && "$STATIC" == 200 ]] &&
     systemctl is-active --quiet rakurs-standalone.service; then
    READY=YES
    break
  fi
  sleep 1
done
[[ "$READY" == YES ]] || {
  echo "STOP: service unavailable LOGIN=$LOGIN STATIC=$STATIC" >&2
  sudo journalctl -u rakurs-standalone.service -n 60 --no-pager >&2 || true
  false
}
echo "GUNICORN_READY=PASS ATTEMPT=$attempt"

echo '=== CHECK AUTHENTICATED MAIN PAGE AND MYSQL (READ ONLY) ==='
sudo -u rakurs env \
  PYTHONPATH=/opt/rakurs \
  RAKURS_DB_USER=rakurs_web \
  RAKURS_DB_PASSWORD_FILE=/etc/rakurs/mysql_password \
  RAKURS_SESSION_SECRET_FILE=/etc/rakurs/session_secret \
  /var/lib/rakurs/.venv/bin/python - <<'PY'
from importlib import import_module
import time

app = import_module("standalone.app")
with app.open_db() as db:
    with db.cursor() as cur:
        cur.execute("SELECT id, session_version FROM rakurs_users "
                    "WHERE is_active=1 ORDER BY id LIMIT 1")
        user = cur.fetchone()
if not user:
    raise SystemExit("STOP: no active Rakurs user")
client = app.app.test_client()
with client.session_transaction(base_url="https://localhost") as sess:
    sess["uid"] = user["id"]
    sess["ver"] = user["session_version"]
    sess["at"] = int(time.time())
r = client.get("/", base_url="https://localhost")
assert r.status_code == 200, f"HOME_HTTP={r.status_code}"
html = r.get_data(as_text=True)
for fragment in (
    "/static/app-clarity-20261124.js",
    "/static/clarity-ui-20261124.css",
    'data-initial-mode="simulation"',
    "5.1", "6.4", "72",
    "Показники Deye з бази даних",
):
    assert fragment in html, f"Missing required MySQL reading or asset: {fragment}"
print("CLARITY_HOME_MYSQL_SSR=PASS")
latest = client.get("/api/v1/latest?keys=pv_power,load_power,battery_soc",
                    base_url="https://localhost")
assert latest.status_code == 200
values = latest.get_json()["values"]
for key, number in (("pv_power", 5.1), ("load_power", 6.4), ("battery_soc", 72.0)):
    assert abs(float(values[key]["value_decimal"]) - number) < 0.00001
print("CLARITY_API_READINGS=PASS")
history = client.get("/api/v1/history?key=pv_power&hours=72",
                     base_url="https://localhost")
assert history.status_code == 200
assert len(history.get_json()["points"]) >= 12
print("CLARITY_HISTORY=PASS")
PY

echo '=== VERIFY JS/CSS IDENTICAL THROUGH HTTPS ==='
for item in "$JS" "$CSS"; do
  EXPECTED="$(sha256sum "$HERE/$item" | awk '{print $1}')"
  TMP="$(mktemp)"
  for ROUTE in LOCAL PUBLIC; do
    if [[ "$ROUTE" == LOCAL ]]; then
      curl --noproxy '*' -fsS --connect-timeout 4 --max-time 15 \
        --resolve "$DOMAIN:443:127.0.0.1" \
        "https://$DOMAIN/$item" -o "$TMP"
    else
      curl --noproxy '*' -fsS --connect-timeout 4 --max-time 15 \
        "https://$DOMAIN/$item" -o "$TMP"
    fi
    ACTUAL="$(sha256sum "$TMP" | awk '{print $1}')"
    echo "ASSET_$(basename "$item")_$ROUTE=$ACTUAL"
    [[ "$ACTUAL" == "$EXPECTED" ]] || {
      rm -f "$TMP"
      echo "STOP: mismatched HTTPS asset $item ($ROUTE)" >&2
      false
    }
  done
  rm -f "$TMP"
done
sudo nginx -t
trap - ERR
echo 'RAKURS_CLARITY_UI=PASS'
echo 'MYSQL_WRITES=0'
echo 'OPEN=https://rakurs.zakryttya.org.ua/'
echo "CLARITY_UI_BACKUP=$BACKUP"
