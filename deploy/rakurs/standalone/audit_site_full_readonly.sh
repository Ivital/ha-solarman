#!/usr/bin/env bash
# Read-only assessment of deployed RAKURS and pinned GitHub sources.
set -Eeuo pipefail
umask 077
ROOT=/home/ivital/deye-monitoring-src
TARGET=/opt/rakurs/standalone
PREFIX=deploy/rakurs/standalone
BASE="${RAKURS_AUDIT_REF:-$(git -C /home/ivital/deye-monitoring-src rev-parse origin/feature/deye-trb145-monitoring)}"
BRANCH=feature/deye-trb145-monitoring
DOMAIN=rakurs.zakryttya.org.ua
VENV=/home/ivital/.venvs/rakurs-tests
cd "$ROOT"
sudo -v
git cat-file -e "$BASE^{commit}"
BEFORE="$(git status --porcelain=v1)"
WORK="$(mktemp -d /tmp/rakurs-site-audit-XXXXXXXX)"
trap 'rm -rf "$WORK"' EXIT

echo '=== A. ACTIVE RELEASE MANIFEST: SHA-256 ==='
MATCH=0; DIFFERENT=0; MISSING=0
while IFS= read -r relative; do
  [[ -z "$relative" || "$relative" == \#* ]] && continue
  case "$relative" in
    /*|*'..'*|*'\\'*) echo "INVALID_MANIFEST_PATH=$relative"; exit 2 ;;
  esac
  expected="$(git show "$BASE:$PREFIX/$relative" | sha256sum | cut -d' ' -f1)"
  if ! sudo test -f "$TARGET/$relative"; then
    echo "MISSING=$relative"; MISSING=$((MISSING + 1))
    continue
  fi
  actual="$(sudo sha256sum "$TARGET/$relative" | cut -d' ' -f1)"
  if [[ "$actual" == "$expected" ]]; then
    MATCH=$((MATCH + 1))
  else
    echo "DIFFERENT=$relative"; DIFFERENT=$((DIFFERENT + 1))
  fi
done < <(git show "$BASE:$PREFIX/release_manifest.txt")
echo "MANIFEST_MATCH=$MATCH DIFFERENT=$DIFFERENT MISSING=$MISSING"

echo '=== B. INSTALLED PYTHON/JS SYNTAX ==='
sudo python3 - <<'PY'
import ast
from pathlib import Path
root=Path('/opt/rakurs/standalone')
files=sorted(root.glob('*.py'))
for p in files:
    ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
print('INSTALLED_PYTHON_SYNTAX=PASS FILES=',len(files))
PY
sudo node --check "$TARGET/static/app-parallel-twin-20261125.js"
echo 'ACTIVE_JS_SYNTAX=PASS'

echo '=== C. NGINX AND SYSTEMD ==='
systemctl is-active rakurs-standalone.service nginx.service
systemctl show rakurs-standalone.service -p ActiveState -p MainPID -p User -p WorkingDirectory -p FragmentPath
sudo nginx -t
for url in /login /api/v1/status /api/v1/parallel; do
  status="$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 10 "http://127.0.0.1:8765$url" || true)"
  echo "ANONYMOUS_HTTP $url $status"
done

echo '=== D. PUBLIC HTTPS ACTIVE ASSET CONTENT ==='
for relative in static/style.css static/clarity-ui-20261124.css static/events-ui-20261124.css static/parallel-twin-20261125.css static/app-parallel-twin-20261125.js; do
  expected="$(git show "$BASE:$PREFIX/$relative" | sha256sum | cut -d' ' -f1)"
  if curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 "https://$DOMAIN/$relative" -o "$WORK/asset"; then
    actual="$(sha256sum "$WORK/asset" | cut -d' ' -f1)"
    [[ "$actual" == "$expected" ]] && echo "PUBLIC_ASSET_MATCH=$relative" || echo "PUBLIC_ASSET_DIFFERENT=$relative"
  else
    echo "PUBLIC_ASSET_UNAVAILABLE=$relative"
  fi
done

echo '=== E. ACTUAL FLASK API: IN-PROCESS READ-ONLY REQUESTS ==='
sudo -u rakurs env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/rakurs \
  RAKURS_DB_USER=rakurs_web \
  RAKURS_DB_PASSWORD_FILE=/etc/rakurs/mysql_password \
  RAKURS_SESSION_SECRET_FILE=/etc/rakurs/session_secret \
  /var/lib/rakurs/.venv/bin/python - <<'PY'
import time
from standalone import app as m
with m.open_db() as db:
    with db.cursor() as c:
        c.execute('SELECT id,session_version FROM rakurs_users WHERE is_active=1 ORDER BY id LIMIT 1')
        account=c.fetchone()
if not account:
    raise SystemExit('STOP: no active account for read-only API audit')
guest=m.app.test_client()
for path,expected in [('/login',200),('/api/v1/status',401),('/api/v1/parallel',401)]:
    response=guest.get(path,base_url='https://localhost')
    print('GUEST_API',path,'HTTP',response.status_code,'EXPECTED',expected)
    response.close()
client=m.app.test_client()
with client.session_transaction(base_url='https://localhost') as session:
    session['uid']=account['id']
    session['ver']=account['session_version']
    session['at']=int(time.time())
paths=[
 '/', '/deye', '/deye/modbus',
 '/api/v1/auth','/api/v1/catalog',
 '/api/v1/status?demo=1','/api/v1/status?real=1',
 '/api/v1/signals','/api/v1/latest?keys=pv_power,battery_soc',
 '/api/v1/history?key=pv_power&hours=24',
 '/api/v1/events','/api/v1/devices',
 '/api/v1/batches','/api/v1/parallel','/api/v1/parallel/health',
 '/api/v1/modbus-fields'
]
failures=0
for path in paths:
    start=time.monotonic()
    try:
        response=client.get(path,base_url='https://localhost')
        elapsed=round((time.monotonic()-start)*1000)
        print('API_CHECK',path.split('?')[0], 'HTTP',response.status_code,'MS',elapsed)
        if response.status_code!=200: failures+=1
        response.close()
    except Exception as exc:
        failures+=1
        print('API_ERROR',path.split('?')[0],type(exc).__name__)
def read(path):
    r=client.get(path,base_url='https://localhost')
    try:return r.get_json() if r.status_code==200 else {}
    finally:r.close()
status=read('/api/v1/status')
devices=read('/api/v1/devices')
sc=status.get('registered_devices')
dc=len(devices.get('devices',[]))
print('MYSQL_DEVICE_CONSISTENCY','STATUS_COUNT',sc,'LIST_COUNT',dc,
      'RESULT','MATCH' if sc==dc else 'MISMATCH')
assert sc==dc and status.get('mode')=='real'
print('FLASK_API_HTTP_FAILURES=',failures)
print('AUTH_API_USED_LOCAL_EPHEMERAL_SESSION_ONLY=YES')
print('MYSQL_WRITES=0 MODBUS_COMMANDS=0')
PY

echo '=== F. RELEASE CODE: FULL SOURCE TESTS IN SEPARATE CLONE ==='
git fetch --no-tags origin "refs/heads/$BRANCH"
PATCH_SHA="$(git rev-parse FETCH_HEAD)"
git clone --shared --no-checkout -q "$ROOT" "$WORK/source"
git -C "$WORK/source" checkout -q --detach "$PATCH_SHA"
cd "$WORK/source"
test -x "$VENV/bin/python"
"$VENV/bin/python" -m unittest discover -s tests -p 'test_*.py' -q
node --check deploy/rakurs/standalone/static/app-parallel-twin-20261125.js
node tests/test_rakurs_clarity_ui.js
node tests/test_rakurs_ssr_guard.js
bash deploy/rakurs/standalone/deploy_parallel_ui.sh --check

echo '=== G. ORIGINAL CHECKOUT MUST REMAIN INTACT ==='
cd "$ROOT"
[[ "$(git status --porcelain=v1)" == "$BEFORE" ]] || { echo 'ORIGINAL_WORKTREE_CHANGED=YES'; exit 1; }
echo 'ORIGINAL_WORKTREE_UNCHANGED=YES'
echo "PR_HEAD=$PATCH_SHA"
echo 'SITE_AUDIT_READONLY=COMPLETE'
echo 'DEPLOY=NO MYSQL_WRITES=0 MODBUS_COMMANDS=0'
