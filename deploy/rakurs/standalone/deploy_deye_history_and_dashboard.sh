#!/usr/bin/env bash
# One-time 72-hour Deye history + charts on the EXISTING Rakurs website.
# Preserves original DB rows, marks every new reading simulated, no Modbus I/O.
set -Eeuo pipefail
umask 077

MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: deploy_deye_history_and_dashboard.sh --check|--apply' >&2
  exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
cd "$ROOT"
TARGET=/opt/rakurs/standalone
PYTHON=/var/lib/rakurs/.venv/bin/python
HISTORY="$HERE/append_deye_history_72h.py"
DOMAIN=rakurs.zakryttya.org.ua
VERSION=rakurs-history-72h-v1
FILES=(app.py static/app.js static/style.css templates/index.html)

for item in "${FILES[@]}"; do
  test -s "$HERE/$item" || { echo "STOP: missing $item" >&2; exit 2; }
done
test -s "$HISTORY" || { echo 'STOP: history script missing' >&2; exit 2; }
python3 -m py_compile "$HERE/app.py" "$HISTORY"
python3 "$HISTORY"
grep -Fq "app-stable-20261124.js" "$HERE/templates/index.html"
grep -Fq "style.css?v=$VERSION" "$HERE/templates/index.html"
grep -Fq 'Динаміка за 24 години' "$HERE/static/app.js"
grep -Fq 'SELECT MAX(ms.observed_at_utc)' "$HERE/app.py"
if command -v node >/dev/null; then
  node --check "$HERE/static/app.js"
fi
echo 'RAKURS_HISTORY_PREFLIGHT=PASS'
if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS MYSQL_CHANGED=NO'
  exit 0
fi

sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected checkout branch' >&2; exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: Rakurs service is inactive' >&2; exit 2
}
for item in "${FILES[@]}"; do
  sudo test -s "$TARGET/$item" || {
    echo "STOP: installed $item missing" >&2; exit 2
  }
done

BACKUP="$(sudo mktemp -d /var/backups/rakurs-history-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
for item in "${FILES[@]}"; do
  sudo install -d -m 0700 "$BACKUP/$(dirname "$item")"
  sudo cp -p "$TARGET/$item" "$BACKUP/$item"
done
echo "HISTORY_WEB_BACKUP=$BACKUP"

RESTORE=1
rollback() {
  code=$?
  trap - ERR
  if (( RESTORE )); then
    echo 'DEPLOY FAILED BEFORE DB COMMIT: restoring previous Rakurs web' >&2
    for item in "${FILES[@]}"; do
      sudo cp -p "$BACKUP/$item" "$TARGET/$item" || true
    done
    sudo systemctl restart rakurs-standalone.service || true
  fi
  echo "HISTORY_WEB_BACKUP=$BACKUP" >&2
  exit "$code"
}
trap rollback ERR

echo '=== UPDATE MAIN RAKURS HOMEPAGE AND HISTORY CHARTS ==='
for item in "${FILES[@]}"; do
  sudo install -o root -g rakurs -m 0640 "$HERE/$item" "$TARGET/$item"
done
sudo systemctl restart rakurs-standalone.service

READY=NO
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 2 --max-time 3 http://127.0.0.1:8765/login || true)"
  JS="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 2 --max-time 3 \
    "http://127.0.0.1:8765/static/app.js?v=$VERSION" || true)"
  if [[ "$LOGIN" == 200 && "$JS" == 200 ]] &&
      systemctl is-active --quiet rakurs-standalone.service; then
    READY=YES
    break
  fi
  sleep 1
done
[[ "$READY" == YES ]] || {
  echo "STOP: Gunicorn unavailable LOGIN=$LOGIN JS=$JS" >&2
  sudo journalctl -u rakurs-standalone.service -n 50 --no-pager >&2 || true
  false
}
echo "GUNICORN_READY=PASS ATTEMPT=$attempt"

echo '=== VERIFY NEW JS AND CSS REALLY SERVED THROUGH HTTPS ==='
for item in app.js style.css; do
  FILE="$(mktemp)"
  curl --noproxy '*' --fail --silent --show-error --max-time 15 \
    --resolve "$DOMAIN:443:127.0.0.1" \
    "https://$DOMAIN/static/$item?v=$VERSION" -o "$FILE"
  EXPECTED="$(sha256sum "$HERE/static/$item" | awk '{print $1}')"
  ACTUAL="$(sha256sum "$FILE" | awk '{print $1}')"
  rm -f "$FILE"
  printf '%s_SOURCE_SHA256=%s\n%s_HTTPS_SHA256=%s\n' \
    "$item" "$EXPECTED" "$item" "$ACTUAL"
  [[ "$EXPECTED" == "$ACTUAL" ]] || {
    echo "STOP: HTTPS serves stale $item" >&2; false
  }
done

echo '=== APPEND 72H HISTORY, ONE COMMIT, EXPLICIT CONFIRMATION ==='
sudo env PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$HISTORY" --execute </dev/tty

# The history transaction is committed; do not restore an older web build.
RESTORE=0
trap - ERR

echo '=== VERIFY THE EXISTING AUTHENTICATED SITE AND 24H CHART DATA ==='
sudo -u rakurs env \
  PYTHONPATH=/opt/rakurs \
  RAKURS_DB_USER=rakurs_web \
  RAKURS_DB_PASSWORD_FILE=/etc/rakurs/mysql_password \
  RAKURS_SESSION_SECRET_FILE=/etc/rakurs/session_secret \
  "$PYTHON" - <<'PY'
from importlib import import_module
import time

app = import_module("standalone.app")
with app.open_db() as db:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, session_version FROM rakurs_users "
            "WHERE is_active=1 ORDER BY id LIMIT 1"
        )
        user = cur.fetchone()
if not user:
    raise SystemExit("STOP: Rakurs user not found")
client = app.app.test_client()
with client.session_transaction(base_url="https://localhost") as sess:
    sess["uid"] = user["id"]
    sess["ver"] = user["session_version"]
    sess["at"] = int(time.time())


def fetch(path):
    r = client.get(path, base_url="https://localhost")
    if r.status_code != 200:
        raise SystemExit(f"STOP: {path} returned HTTP={r.status_code}")
    return r.get_json()


home = client.get("/", base_url="https://localhost")
assert home.status_code == 200
body = home.get_data(as_text=True)
assert "rakurs-history-72h-v1" in body
assert "5.1" in body
print("RAKURS_PRIMARY_HOME=PASS")

status = fetch("/api/v1/status")
assert status["simulated_signals"] == 316
assert status["mode"] == "simulation"
print("RAKURS_VISIBLE_SIGNALS=316")

for key in ("pv_power", "load_power", "battery_soc"):
    points = fetch("/api/v1/history?key=" + key + "&hours=24")["points"]
    numeric = [float(p["value_decimal"]) for p in points
               if p["value_decimal"] is not None]
    if len(numeric) < 12 or max(numeric) == min(numeric):
        raise SystemExit(
            f"STOP: trend {key} lacks variation or history: {len(numeric)}"
        )
    print(f"TREND_{key.upper()}_POINTS={len(numeric)}")
print("RAKURS_24H_TRENDS=PASS")

modbus = fetch("/api/v1/modbus-fields")
assert modbus["count"] == 624, modbus["count"]
assert modbus["register_count"] == 815
assert len({x["field_key"] for x in modbus["fields"]}) == 624
print("RAKURS_MODBUS_UNIQUE_FIELDS=624")
print("RAKURS_HISTORY_READBACK=PASS")
PY
sudo nginx -t
echo 'RAKURS_HISTORY_AND_MAIN_CHARTS=PASS'
echo 'OPEN=https://rakurs.zakryttya.org.ua/#/overview'
echo 'HISTORY=https://rakurs.zakryttya.org.ua/#/history'
echo "HISTORY_WEB_BACKUP=$BACKUP"
