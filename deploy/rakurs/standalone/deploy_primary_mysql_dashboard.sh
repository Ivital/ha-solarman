#!/usr/bin/env bash
# Restore data visibility in the EXISTING Rakurs home, navigation and API.
# No MySQL writes, no standalone demo website and no Nginx configuration edits.
set -Eeuo pipefail
umask 077

MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: deploy_primary_mysql_dashboard.sh [--check|--apply]' >&2
  exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
TARGET=/opt/rakurs/standalone
DOMAIN=rakurs.zakryttya.org.ua
ASSET_PATH='/static/app-clarity-20261124.js'
FILES=(app.py static/app.js templates/index.html)
cd "$ROOT"

for file in "${FILES[@]}"; do
  test -s "$HERE/$file" || {
    echo "STOP: missing source $file" >&2
    exit 2
  }
done
python3 -m py_compile "$HERE/app.py"
grep -Fq 'initial_cards=cards' "$HERE/app.py"
grep -Fq "g.use_simulated_samples" "$HERE/app.py"
grep -Fq 'state.status.mode === "simulation"' "$HERE/static/app.js"
grep -Fq "$ASSET_PATH" "$HERE/templates/index.html"
if command -v node >/dev/null; then
  node --check "$HERE/static/app.js"
fi
echo 'PRIMARY_DASHBOARD_SOURCE_PREFLIGHT=PASS'
if [[ "$MODE" != "--check" ]] && grep -Fq 'app-clarity-20261124.js' "$HERE/templates/index.html"; then
  echo 'STOP: legacy installer would overwrite Clarity UI. Use deploy_clarity_ui.sh --apply instead.' >&2
  exit 2
fi
if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS MYSQL_CHANGED=NO'
  exit 0
fi

sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected Git branch' >&2
  exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: standalone service inactive' >&2
  exit 2
}
for file in "${FILES[@]}"; do
  sudo test -s "$TARGET/$file" || {
    echo "STOP: installed $file missing" >&2
    exit 2
  }
done

BACKUP="$(sudo mktemp -d /var/backups/rakurs-primary-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
for file in "${FILES[@]}"; do
  sudo install -d -m 0700 "$BACKUP/$(dirname "$file")"
  sudo cp -p "$TARGET/$file" "$BACKUP/$file"
done
echo "PRIMARY_DASHBOARD_BACKUP=$BACKUP"

RESTORE=1
rollback() {
  rc=$?
  trap - ERR
  if (( RESTORE )); then
    echo 'DEPLOY ERROR: restoring previous Rakurs primary dashboard' >&2
    for file in "${FILES[@]}"; do
      sudo cp -p "$BACKUP/$file" "$TARGET/$file" || true
    done
    sudo systemctl restart rakurs-standalone.service || true
  fi
  echo "PRIMARY_DASHBOARD_BACKUP=$BACKUP" >&2
  exit "$rc"
}
trap rollback ERR

echo '=== UPDATE THE EXISTING SITE ==='
for file in "${FILES[@]}"; do
  sudo install -o root -g rakurs -m 0640 "$HERE/$file" "$TARGET/$file"
done
sudo systemctl restart rakurs-standalone.service

echo '=== CHECK GUNICORN AND BROWSER ASSETS ==='
READY=NO
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/login || true)"
  JS="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' "http://127.0.0.1:8765$ASSET_PATH" || true)"
  if [[ "$LOGIN" == 200 && "$JS" == 200 ]] &&
    systemctl is-active --quiet rakurs-standalone.service; then
    READY=YES
    break
  fi
  sleep 1
done
if [[ "$READY" != YES ]]; then
  echo "STOP: Gunicorn not ready: login=$LOGIN js=$JS" >&2
  sudo journalctl -u rakurs-standalone.service -n 70 --no-pager >&2 || true
  false
fi
echo "GUNICORN_READY=PASS ATTEMPT=$attempt"

echo '=== TEST AUTHENTICATED MAIN HOME AND APIs WITH EXISTING MYSQL DATA ==='
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
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, session_version FROM rakurs_users "
            "WHERE is_active=1 ORDER BY id LIMIT 1"
        )
        account = cur.fetchone()
if not account:
    raise SystemExit("STOP: no active Rakurs account")
client = m.app.test_client()
with client.session_transaction(base_url="https://localhost") as sess:
    sess["uid"] = account["id"]
    sess["ver"] = account["session_version"]
    sess["at"] = int(time.time())

def get_json(path):
    response = client.get(path, base_url="https://localhost")
    if response.status_code != 200:
        raise SystemExit(f"STOP: {path} HTTP={response.status_code}")
    return response.get_json()

home = client.get("/", base_url="https://localhost")
assert home.status_code == 200, f"MAIN_HOME_HTTP={home.status_code}"
html = home.get_data(as_text=True)
for marker in (
    "app-stable-20261124.js",
    'Показники Deye з бази даних',
    "5.1", "6.4", "72",
):
    assert marker in html, f"Missing MySQL value/marker on MAIN PAGE: {marker}"
print("MAIN_HOME_MYSQL_HTML=PASS")
print("MAIN_HOME_HTTP=200")

status = get_json("/api/v1/status")
assert status["mode"] == "simulation", status
assert status["simulated_signals"] == 316, status
assert status["verified_signals"] == 0, status
print("MAIN_SITE_STATUS_DISPLAYED=316")
signals = get_json("/api/v1/signals")
assert len(signals["signals"]) == 316
print("MAIN_SITE_SIGNALS=316")
latest = get_json("/api/v1/latest?keys=pv_power,load_power,battery_soc")
values = latest["values"]
expected = {"pv_power": 5.1, "load_power": 6.4, "battery_soc": 72}
for key, number in expected.items():
    actual = values[key]["value_decimal"]
    assert abs(float(actual) - number) < 1e-6, (key, actual)
    assert values[key]["quality"] == "simulated", key
    print(f"MAIN_SITE_{key.upper()}={actual}")
modbus = get_json("/api/v1/modbus-fields")
assert modbus["count"] == 624, modbus["count"]
assert modbus["register_count"] == 815
assert len(modbus["fields"]) == 624
assert any(x["value_decimal"] is not None or x["value_text"] is not None
           for x in modbus["fields"])
print("MAIN_SITE_MODBUS_FIELDS=624")
print("MAIN_SITE_MODBUS_REGISTERS=815")

real_only = get_json("/api/v1/status?real=1")
assert real_only["mode"] == "real"
assert not real_only["has_real_samples"]
print("REAL_ONLY_SWITCH_RETAINS_ACCURACY=PASS")
print("PRIMARY_MYSQL_DASHBOARD_CHECK=PASS")
PY

echo '=== VERIFY EXACT MAIN JAVASCRIPT OVER HTTPS ==='
JS_TMP="$(mktemp)"
trap 'rm -f "$JS_TMP"' EXIT
curl --noproxy '*' --fail --silent --show-error --max-time 15 \
  --resolve "$DOMAIN:443:127.0.0.1" \
  "https://$DOMAIN$ASSET_PATH" -o "$JS_TMP"
EXPECTED="$(sha256sum "$HERE/static/app.js" | awk '{print $1}')"
OBSERVED="$(sha256sum "$JS_TMP" | awk '{print $1}')"
printf 'SOURCE_SHA256=%s\nHTTPS_SHA256=%s\n' "$EXPECTED" "$OBSERVED"
[[ "$EXPECTED" == "$OBSERVED" ]] || {
  echo 'STOP: HTTPS did not serve the new primary app.js' >&2
  false
}
grep -Fq 'state.status.mode === "simulation"' "$JS_TMP"
sudo nginx -t

RESTORE=0
trap - ERR
echo 'RAKURS_PRIMARY_SITE=PASS'
echo 'MYSQL_MUTATIONS=0'
echo 'OPEN=https://rakurs.zakryttya.org.ua/'
echo "PRIMARY_DASHBOARD_BACKUP=$BACKUP"
