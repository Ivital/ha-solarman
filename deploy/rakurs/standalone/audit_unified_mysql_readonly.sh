#!/usr/bin/env bash
# Validate NEW source against live MySQL without deploying or changing services.
set -Eeuo pipefail
umask 077
[[ "${1:---check}" == "--check" ]] || { echo "Usage: $0 --check" >&2; exit 2; }
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
WORK="$(mktemp -d /tmp/rakurs-new-code-check-XXXXXXXX)"
trap 'rm -rf "$WORK"' EXIT
chmod 0755 "$WORK"
install -d -m 0755 "$WORK/standalone" "$WORK/standalone/templates" "$WORK/standalone/static"
for file in __init__.py app.py auth.py parallel_support.py modbus_archive.py modbus_wire.py mysql_transport.py; do
  install -m 0644 "$HERE/$file" "$WORK/standalone/$file"
done
install -m 0644 "$ROOT/signal_catalog.json" "$WORK/signal_catalog.json"
for file in templates/index.html templates/deye_server.html templates/login.html static/app-parallel-twin-20261125.js static/app.js static/app-stable-20261124.js; do
  install -m 0644 "$HERE/$file" "$WORK/standalone/$file"
done
sudo -v
sudo -u rakurs env \
  PYTHONPATH="$WORK" PYTHONDONTWRITEBYTECODE=1 \
  RAKURS_DB_USER=rakurs_web \
  RAKURS_DB_PASSWORD_FILE=/etc/rakurs/mysql_password \
  RAKURS_SESSION_SECRET_FILE=/etc/rakurs/session_secret \
  /var/lib/rakurs/.venv/bin/python - <<'PY'
from importlib import import_module
from urllib.parse import quote
import time

m=import_module("standalone.app")
with m.open_db() as db:
    with db.cursor() as cur:
        cur.execute("SELECT id,session_version FROM rakurs_users "
                    "WHERE is_active=1 ORDER BY id LIMIT 1")
        user=cur.fetchone()
if not user:
    raise SystemExit("NEW_CODE_READONLY_FAIL=NO_ACTIVE_USER")
guest=m.app.test_client()
for path,expected in (("/login",200),("/api/v1/status",401),("/api/v1/parallel",401)):
    r=guest.get(path,base_url="https://localhost")
    assert r.status_code==expected,(path,r.status_code)
c=m.app.test_client()
with c.session_transaction(base_url="https://localhost") as session:
    session.update(uid=user["id"],ver=user["session_version"],at=int(time.time()))
def get(path):
    r=c.get(path,base_url="https://localhost")
    print("ROUTE",path.split("?")[0],"HTTP",r.status_code)
    if r.status_code!=200:
        raise AssertionError(f"NEW_CODE_ROUTE_FAIL={path} HTTP={r.status_code}")
    return r.get_json() if r.is_json else r.get_data(as_text=True)
for path in ("/","/deye","/deye/modbus",
             "/api/v1/auth","/api/v1/catalog","/api/v1/signals",
             "/api/v1/latest?keys=pv_power,load_power,battery_soc",
             "/api/v1/history?key=pv_power&hours=24",
             "/api/v1/events","/api/v1/batches",
             "/api/v1/modbus-fields","/api/v1/parallel/health"):
    get(path)
status=get("/api/v1/status")
devices=get("/api/v1/devices")
assert status["mode"]=="real"
assert status["registered_devices"]==len(devices["devices"]),(
    status["registered_devices"],len(devices["devices"]))
topology=get("/api/v1/parallel")
listed=topology["devices"]
assert topology["registered_inverters"]==len(listed)
for item in listed:
    key=item["device_key"]
    encoded=quote(key,safe="")
    for path in (f"/api/v1/parallel/latest?device={encoded}",
                 f"/api/v1/parallel/history?device={encoded}&key=pv_power&hours=24",
                 f"/api/v1/parallel/raw?device={encoded}&limit=5",
                 f"/api/v1/parallel/modbus-fields?device={encoded}"):
        result=get(path)
        assert result["device_key"]==key
print("MYSQL_DEVICES=",len(devices["devices"]))
print("DEYE_SLOTS=",len(listed))
print("NEW_CODE_MYSQL_READONLY=PASS")
print("MYSQL_WRITES=0 MODBUS_COMMANDS=0 SERVICES_RESTARTED=0")
PY
