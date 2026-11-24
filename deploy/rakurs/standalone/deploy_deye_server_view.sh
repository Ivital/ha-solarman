#!/usr/bin/env bash
# Deploy HTML pages that render stored Deye data without JavaScript.
# No MySQL writes, no Nginx edit, no other service touched.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: deploy_deye_server_view.sh --check|--apply' >&2
  exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
cd "$ROOT"
TARGET=/opt/rakurs/standalone
FILES=(app.py auth.py templates/login.html templates/deye_server.html static/deye-server.css)
for item in "${FILES[@]}"; do
  [[ -s "$HERE/$item" ]] || { echo "STOP: missing source $item" >&2; exit 2; }
done
python3 -m py_compile "$HERE/app.py" "$HERE/auth.py"
grep -Fq '@app.get("/deye")' "$HERE/app.py"
grep -Fq '@app.get("/deye/modbus")' "$HERE/app.py"
if [[ "$MODE" == "--check" ]]; then
  echo 'DEYE_SERVER_VIEW_PREFLIGHT=PASS'
  echo 'MYSQL_WRITES=NONE'
  exit 0
fi
sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected Git branch' >&2; exit 2
}
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: Rakurs service is not active' >&2; exit 2
}
for item in app.py auth.py templates/login.html; do
  sudo test -s "$TARGET/$item" || {
    echo "STOP: installed $item missing" >&2; exit 2
  }
done
BACKUP="$(sudo mktemp -d /var/backups/rakurs-ssr-XXXXXXXX)"
sudo chmod 0700 "$BACKUP"
for item in app.py auth.py templates/login.html; do
  sudo install -d -m 0700 "$BACKUP/$(dirname "$item")"
  sudo cp -p "$TARGET/$item" "$BACKUP/$item"
done
echo "ROLLBACK_BACKUP=$BACKUP"
RESTORE=1
rollback() {
  code=$?
  trap - ERR
  if (( RESTORE )); then
    echo 'ROLLING BACK PREVIOUS RAKURS APPLICATION FILES' >&2
    for item in app.py auth.py templates/login.html; do
      sudo cp -p "$BACKUP/$item" "$TARGET/$item" || true
    done
    sudo rm -f "$TARGET/templates/deye_server.html" "$TARGET/static/deye-server.css"
    sudo systemctl restart rakurs-standalone.service || true
  fi
  echo "ROLLBACK_BACKUP=$BACKUP" >&2
  exit "$code"
}
trap rollback ERR
for item in "${FILES[@]}"; do
  sudo install -o root -g rakurs -m 0640 "$HERE/$item" "$TARGET/$item"
done
sudo systemctl restart rakurs-standalone.service

READY=NO
for attempt in $(seq 1 30); do
  LOGIN="$(curl --noproxy '*' --silent --max-time 3 -o /dev/null \
    -w '%{http_code}' http://127.0.0.1:8765/login || true)"
  VIEW="$(curl --noproxy '*' --silent --max-time 3 -o /dev/null \
    -w '%{http_code}' http://127.0.0.1:8765/deye || true)"
  STYLE="$(curl --noproxy '*' --silent --max-time 3 -o /dev/null \
    -w '%{http_code}' http://127.0.0.1:8765/static/deye-server.css || true)"
  if [[ "$LOGIN" == 200 && "$VIEW" == 303 && "$STYLE" == 200 ]] &&
    systemctl is-active --quiet rakurs-standalone.service; then
    READY=YES
    break
  fi
  sleep 1
done
if [[ "$READY" != YES ]]; then
  echo "STOP: Gunicorn not ready: LOGIN=$LOGIN VIEW=$VIEW CSS=$STYLE" >&2
  sudo journalctl -u rakurs-standalone.service -n 50 --no-pager >&2 || true
  false
fi
echo "WEB_LOGIN=$LOGIN ANONYMOUS_VIEW=$VIEW STYLESHEET=$STYLE ATTEMPT=$attempt"

echo '=== TEST AUTHENTICATED SERVER RENDERING AND MYSQL ==='
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
        cur.execute("SELECT id, session_version FROM rakurs_users "
                    "WHERE is_active=1 ORDER BY id LIMIT 1")
        account = cur.fetchone()
if not account:
    raise SystemExit("STOP: active Rakurs account not found")
client = m.app.test_client()
with client.session_transaction(base_url="https://localhost") as session:
    session["uid"] = account["id"]
    session["ver"] = account["session_version"]
    session["at"] = int(time.time())
for path, expected_count, expected_text in (
    ("/deye", 316, "5.1"),
    ("/deye/modbus", 624, "0x"),
):
    response = client.get(path, base_url="https://localhost")
    assert response.status_code == 200, (path, response.status_code)
    html = response.get_data(as_text=True)
    assert f'<strong>{expected_count}</strong>' in html, (path, "count missing")
    assert expected_text in html, (path, "sample missing")
    assert "/static/app.js" not in html, (path, "SPA dependency detected")
    print(f"RENDER_{path}=PASS")
    print(f"RENDERED_HTML_BYTES_{path}={len(html.encode())}")
print("AUTHENTICATED_MYSQL_HTML=PASS")
PY

echo '=== CHECK PUBLIC HTTPS ROUTING (ANONYMOUS MUST REDIRECT) ==='
HTTPS="$(curl --noproxy '*' --silent --max-time 10 \
  --resolve 'rakurs.zakryttya.org.ua:443:127.0.0.1' \
  -o /dev/null -w '%{http_code}' \
  'https://rakurs.zakryttya.org.ua/deye' || true)"
[[ "$HTTPS" == 303 ]] || {
  echo "STOP: HTTPS /deye returned $HTTPS, expected redirect 303" >&2
  false
}
echo "HTTPS_ANONYMOUS_DEYE=$HTTPS"
sudo nginx -t
RESTORE=0
trap - ERR
echo 'RAKURS_DEYE_SERVER_RENDER=PASS'
echo 'OPEN=https://rakurs.zakryttya.org.ua/deye'
echo 'OPEN_MODBUS=https://rakurs.zakryttya.org.ua/deye/modbus'
echo "ROLLBACK_BACKUP=$BACKUP"
