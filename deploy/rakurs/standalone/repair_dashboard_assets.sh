#!/usr/bin/env bash
# Scoped Rakurs JS cache-busting and end-to-end HTTPS asset integrity check.
# Does not update database, Nginx, other vhosts, Home Assistant, or Docker.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: repair_dashboard_assets.sh --check|--apply' >&2
  exit 2
}

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
cd "$ROOT"
TARGET=/opt/rakurs/standalone
DOMAIN=rakurs.zakryttya.org.ua
ASSET_PATH='/static/app.js?v=rakurs-history-72h-v1'
SCRIPT_MARKER='let DEMO_MODE = new URLSearchParams(window.location.search)'
MODBUS_MARKER='["modbus", "Modbus", "▤"]'
CHECK_JS="$HERE/static/app.js"
CHECK_HTML="$HERE/templates/index.html"

test -s "$CHECK_JS"
test -s "$CHECK_HTML"
grep -Fq "$ASSET_PATH" "$CHECK_HTML"
grep -Fq "$SCRIPT_MARKER" "$CHECK_JS"
grep -Fq "$MODBUS_MARKER" "$CHECK_JS"
if command -v node >/dev/null; then node --check "$CHECK_JS"; fi
echo 'RAKURS_ASSET_SOURCE_PREFLIGHT=PASS'
if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS; NO LIVE CHANGES'
  exit 0
fi

sudo -v
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: service inactive before change' >&2
  exit 2
}
for item in static/app.js templates/index.html; do
  sudo test -s "$TARGET/$item" || {
    echo "STOP: installed $item missing" >&2
    exit 2
  }
done
BACKUP="$(sudo mktemp -d /var/backups/rakurs-assets-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
sudo install -d -m 0700 "$BACKUP/static" "$BACKUP/templates"
for item in static/app.js templates/index.html; do
  sudo cp -p "$TARGET/$item" "$BACKUP/$item"
done
echo "ASSETS_BACKUP=$BACKUP"
rollback() {
  rc=$?
  trap - ERR
  echo 'ASSET DEPLOY FAILURE: restoring previous Rakurs assets' >&2
  for item in static/app.js templates/index.html; do
    sudo cp -p "$BACKUP/$item" "$TARGET/$item" || true
  done
  sudo systemctl restart rakurs-standalone.service || true
  echo "ASSETS_BACKUP=$BACKUP" >&2
  exit "$rc"
}
trap rollback ERR

echo '=== INSTALL VERSIONED ASSETS ==='
sudo install -o root -g rakurs -m 0640 "$CHECK_JS" "$TARGET/static/app.js"
sudo install -o root -g rakurs -m 0640 "$CHECK_HTML" "$TARGET/templates/index.html"
sudo systemctl restart rakurs-standalone.service

READY=NO
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/login || true)"
  [[ "$LOGIN" == 200 ]] || { sleep 1; continue; }
  STATIC="$(curl --noproxy '*' --silent --connect-timeout 2 --max-time 3 \
    -o /dev/null -w '%{http_code}' "http://127.0.0.1:8765$ASSET_PATH" || true)"
  if [[ "$STATIC" == 200 ]]; then
    READY=YES
    echo "GUNICORN_ASSET_READY_ON_ATTEMPT=$attempt"
    break
  fi
  sleep 1
done
[[ "$READY" == YES ]] || {
  echo 'STOP: service or JS is unavailable' >&2
  sudo journalctl -u rakurs-standalone.service -n 60 --no-pager >&2 || true
  false
}

echo '=== VERIFY HTTPS SERVES THE EXACT NEW JAVASCRIPT ==='
FILE="$(mktemp)"
trap 'rm -f "$FILE"' EXIT
curl --noproxy '*' --fail --silent --show-error --max-time 12 \
  --resolve "$DOMAIN:443:127.0.0.1" \
  "https://$DOMAIN$ASSET_PATH" -o "$FILE"
EXPECTED="$(sha256sum "$CHECK_JS" | awk '{print $1}')"
SERVED="$(sha256sum "$FILE" | awk '{print $1}')"
printf 'EXPECTED_SHA256=%s\nHTTPS_SHA256=%s\n' "$EXPECTED" "$SERVED"
[[ "$EXPECTED" == "$SERVED" ]] || {
  echo 'STOP: HTTPS is serving stale/different app.js' >&2
  false
}
grep -Fq "$MODBUS_MARKER" "$FILE"

echo '=== VERIFY RENDERED DASHBOARD HTML AS AUTHENTICATED USER ==='
sudo -u rakurs env \
  PYTHONPATH=/opt/rakurs \
  RAKURS_DB_USER=rakurs_web \
  RAKURS_DB_PASSWORD_FILE=/etc/rakurs/mysql_password \
  RAKURS_SESSION_SECRET_FILE=/etc/rakurs/session_secret \
  /var/lib/rakurs/.venv/bin/python - <<'PY'
import importlib
import time

module = importlib.import_module("standalone.app")
with module.open_db() as db:
    with db.cursor() as cur:
        cur.execute("SELECT id, session_version FROM rakurs_users "
                    "WHERE is_active=1 ORDER BY id LIMIT 1")
        account = cur.fetchone()
if not account:
    raise SystemExit("STOP: no active Rakurs user")
client = module.app.test_client()
with client.session_transaction(base_url="https://localhost") as sess:
    sess["uid"] = account["id"]
    sess["ver"] = account["session_version"]
    sess["at"] = int(time.time())
response = client.get("/?demo=1", base_url="https://localhost")
assert response.status_code == 200, f"HTML_HTTP={response.status_code}"
html = response.get_data(as_text=True)
expected = "/static/app.js?v=rakurs-history-72h-v1"
assert expected in html, "New HTML does not load the versioned JS"
print("AUTHORIZED_HTML_HTTP=200")
print("VERSIONED_SCRIPT_IN_RENDERED_HTML=PASS")
PY
sudo nginx -t
trap - ERR
echo 'RAKURS_ASSET_FIX=PASS'
echo 'OPEN=https://rakurs.zakryttya.org.ua/?demo=1#/modbus'
echo "ASSETS_BACKUP=$BACKUP"
