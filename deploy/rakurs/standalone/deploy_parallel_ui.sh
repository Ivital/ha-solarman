#!/usr/bin/env bash
# Install read-only two-inverter UI/API. Physical wiring stays untouched.
# No MySQL DDL/DML, Modbus/serial activity, gateway changes, Nginx edits or HA.
set -Eeuo pipefail
umask 077

MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo "Usage: deploy_parallel_ui.sh [--check|--apply]" >&2
  exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
TARGET=/opt/rakurs/standalone
DOMAIN=rakurs.zakryttya.org.ua
FILES=(
  app.py auth.py config_api.py poll_worker.py parallel_support.py modbus_archive.py modbus_wire.py
  mysql_transport.py templates/index.html templates/login.html templates/deye_server.html
  static/app.js static/app-stable-20261124.js
  static/app-parallel-twin-20261125.js static/parallel-twin-20261125.css
)
cd "$ROOT"
for path in "${FILES[@]}"; do
  test -s "$HERE/$path" || { echo "STOP: missing $path" >&2; exit 2; }
done
grep -Fq 'src="/static/app-parallel-twin-20261125.js?v=shared-rs485-20261125-1"' "$HERE/templates/index.html"
grep -Fq 'href="/static/parallel-twin-20261125.css?v=shared-rs485-20261125-1"' "$HERE/templates/index.html"
grep -Fq '@app.get("/api/v1/parallel")' "$HERE/app.py"
grep -Fq 'from .parallel_support import' "$HERE/app.py"
grep -Fq 'case "parallel": await renderParallel(cycle)' "$HERE/static/app-parallel-twin-20261125.js"
grep -Fq 'restoreServerReadings();' "$HERE/static/app-parallel-twin-20261125.js"
grep -Fq 'Дані MySQL' "$HERE/templates/index.html"
grep -Fq 'ПОКАЗНИКИ З MYSQL' "$HERE/templates/deye_server.html"
! grep -Fq 'name="demo"' "$HERE/templates/login.html"
grep -Fq 'aggregate_verified": False' "$HERE/parallel_support.py"
if command -v node >/dev/null 2>&1; then
  node --check "$HERE/static/app-parallel-twin-20261125.js"
  node --check "$HERE/static/app.js"
  node --check "$HERE/static/app-stable-20261124.js"
  node tests/test_rakurs_clarity_ui.js
fi
bash -n "$HERE/audit_parallel_host_readonly.sh"
python3 -m py_compile "$HERE/app.py" "$HERE/parallel_support.py" \
  "$HERE/modbus_archive.py" "$HERE/modbus_wire.py" "$HERE/mysql_transport.py" \
  "$HERE/config_api.py" "$HERE/poll_worker.py"
echo 'PARALLEL_UI_PREFLIGHT=PASS'
echo 'INVERTER_WRITES=0 MODBUS_COMMANDS=0 MYSQL_WRITES=0'
if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS'
  exit 0
fi

sudo -v
echo '=== READ-ONLY NEW-CODE MYSQL ACCEPTANCE ==='
bash "$HERE/audit_unified_mysql_readonly.sh" --check
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected git branch' >&2; exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: Rakurs service not active' >&2; exit 2
}
for path in app.py templates/index.html; do
  sudo test -s "$TARGET/$path" || {
    echo "STOP: currently installed $path missing" >&2; exit 2
  }
done

BACKUP="$(sudo mktemp -d /var/backups/rakurs-parallel-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
for path in "${FILES[@]}"; do
  if sudo test -e "$TARGET/$path"; then
    sudo install -d -m 0700 "$BACKUP/$(dirname "$path")"
    sudo cp -p "$TARGET/$path" "$BACKUP/$path"
  fi
done
echo "PARALLEL_UI_BACKUP=$BACKUP"
rollback() {
  local rc=$?
  trap - ERR
  echo 'PARALLEL DEPLOY FAILED: restoring previous files' >&2
  for path in "${FILES[@]}"; do
    if sudo test -e "$BACKUP/$path"; then
      sudo cp -p "$BACKUP/$path" "$TARGET/$path" || true
    else
      sudo rm -f "$TARGET/$path" || true
    fi
  done
  sudo systemctl restart rakurs-standalone.service || true
  echo "PARALLEL_UI_BACKUP=$BACKUP" >&2
  exit "$rc"
}
trap rollback ERR

echo '=== DEPLOY READ-ONLY PARALLEL API AND UI ==='
for path in "${FILES[@]}"; do
  sudo install -o root -g rakurs -m 0640 "$HERE/$path" "$TARGET/$path"
done
sudo systemctl restart rakurs-standalone.service
READY=NO
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 2 --max-time 3 http://127.0.0.1:8765/login || true)"
  SCRIPT="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 2 --max-time 3 \
    http://127.0.0.1:8765/static/app-parallel-twin-20261125.js || true)"
  if [[ "$LOGIN" == 200 && "$SCRIPT" == 200 ]] &&
     systemctl is-active --quiet rakurs-standalone.service; then
    READY=YES
    break
  fi
  sleep 1
done
[[ "$READY" == YES ]] || {
  echo "STOP: Gunicorn unavailable LOGIN=$LOGIN JS=$SCRIPT" >&2
  sudo journalctl -u rakurs-standalone.service -n 50 --no-pager >&2 || true
  false
}
echo "GUNICORN_READY=PASS ATTEMPT=$attempt"

echo '=== READ-ONLY AUTHENTICATED MYSQL CHECK ==='
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
        account = cur.fetchone()
if not account:
    raise SystemExit("STOP: missing authorized account")
c = m.app.test_client()
with c.session_transaction(base_url="https://localhost") as s:
    s["uid"] = account["id"]
    s["ver"] = account["session_version"]
    s["at"] = int(time.time())
home = c.get("/", base_url="https://localhost")
assert home.status_code == 200
html = home.get_data(as_text=True)
for fragment in ("/static/app-parallel-twin-20261125.js",
                 "/static/parallel-twin-20261125.css",
                 'Показники Deye з бази даних'): 
    assert fragment in html, "Missing HTML/DB content: " + fragment
print("PARALLEL_EXISTING_HOME=PASS")
deye_page = c.get("/deye", base_url="https://localhost")
assert deye_page.status_code == 200
assert "ПОКАЗНИКИ З MYSQL" in deye_page.get_data(as_text=True)
modbus_page = c.get("/deye/modbus", base_url="https://localhost")
assert modbus_page.status_code == 200
print("DEYE_ARCHIVE_HTML=PASS")
res = c.get("/api/v1/parallel", base_url="https://localhost")
assert res.status_code == 200, res.status_code
profile = res.get_json()
assert profile["expected_inverters"] == 2
assert profile["model"] == "SUN-20K-SG05LP3-EU-SM2"
assert profile["shared_battery"] is True
assert profile["master_slave_verified"] is False
assert profile["rs485_topology_verified"] is False
assert profile["aggregate_verified"] is False
assert profile["battery_soc_percent"] is None
assert profile["aggregate_values"] == {}
assert len(profile["devices"]) == profile["registered_inverters"]
assert [int(d["slot_no"]) for d in profile["devices"]]==[1,2]
print("PARALLEL_PUBLIC_SLOT_LABELS=PASS")
assert profile["registered_inverters"] == len(profile["devices"])
print("PARALLEL_PROFILE_READ_ONLY=PASS")
print("MYSQL_DEYE_DEVICE_SLOTS=" + str(profile["registered_inverters"]))
rejected = c.get("/api/v1/parallel/latest?device=simulated_deye_sg05lp3",
                 base_url="https://localhost")
assert rejected.status_code == 404
print("UNREGISTERED_DEVICE_NOT_IN_PARALLEL_GROUP=PASS")
events = c.get("/api/v1/events", base_url="https://localhost")
assert events.status_code == 200
print("EXISTING_EVENTS=PASS")
status = c.get("/api/v1/status", base_url="https://localhost")
assert status.status_code == 200
state = status.get_json()
assert state["mode"] == "real"
devices_response = c.get("/api/v1/devices", base_url="https://localhost")
assert devices_response.status_code == 200
assert state["registered_devices"] == len(devices_response.get_json()["devices"])
for item in profile["devices"]:
    device = item["device_key"]
    latest = c.get("/api/v1/parallel/latest?device=" + device,
                   base_url="https://localhost")
    assert latest.status_code == 200
    assert latest.get_json()["device_key"] == device
print("ALL_MYSQL_DEVICES_VISIBLE=PASS")
print("EMPTY_MYSQL_COMPATIBLE=YES")
print("MYSQL_READ_ONLY=PASS")
PY

echo '=== VERIFY NEW ASSETS LOCALLY AND THROUGH PUBLIC HTTPS ==='
for path in static/app-parallel-twin-20261125.js static/parallel-twin-20261125.css \
            static/app.js static/app-stable-20261124.js; do
  expected="$(sha256sum "$HERE/$path" | awk '{print $1}')"
  tmp="$(mktemp)"
  for route in LOCAL PUBLIC; do
    if [[ "$route" == LOCAL ]]; then
      curl --noproxy '*' -fsS --connect-timeout 4 --max-time 15 \
        --resolve "$DOMAIN:443:127.0.0.1" \
        "https://$DOMAIN/$path" -o "$tmp"
    else
      curl --noproxy '*' -fsS --connect-timeout 4 --max-time 15 \
        "https://$DOMAIN/$path" -o "$tmp"
    fi
    observed="$(sha256sum "$tmp" | awk '{print $1}')"
    echo "ASSET_$(basename "$path")_$route=$observed"
    if [[ "$observed" != "$expected" ]]; then
      rm -f "$tmp"
      echo "STOP: HTTPS asset mismatch $path ($route)" >&2
      false
    fi
  done
  rm -f "$tmp"
done
sudo nginx -t
trap - ERR
echo 'RAKURS_PARALLEL_UI=PASS'
echo 'PHYSICAL_TOPOLOGY=UNVERIFIED'
echo 'MODBUS_COMMANDS=0 MYSQL_WRITES=0'
echo 'OPEN=https://rakurs.zakryttya.org.ua/#/parallel'
echo "PARALLEL_UI_BACKUP=$BACKUP"
