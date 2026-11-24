#!/usr/bin/env bash
# Single controlled rollout of the FULL twin Deye simulated archive and web UI.
# 1. Read-only source tests
# 2. MySQL dump (mandatory), additive CREATE and one atomic synthetic transaction
# 3. Audit saved registers
# 4. Deploy web API and unique JS/CSS, with independent rollback
# Does not communicate with inverters, gateways or Modbus.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" || "$MODE" == "--resume" ]] || {
  echo 'Usage: deploy_full_modbus_archive.sh [--check|--apply|--resume]' >&2
  exit 2
}
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/../../.." && pwd)"
cd "$ROOT"
test -s "$HERE/parallel_raw_modbus_schema.sql"
test -s "$HERE/modbus_archive.py"
test -s "$HERE/seed_two_parallel_demo.py"
test -s "$HERE/audit_two_inverters_mysql.py"
test -s "$HERE/static/app-parallel-twin-20261125.js"
test -s "$HERE/static/parallel-twin-20261125.css"
python3 -m py_compile "$HERE/modbus_archive.py" "$HERE/seed_two_parallel_demo.py" "$HERE/app.py"
python3 "$HERE/seed_two_parallel_demo.py"
bash "$HERE/deploy_parallel_ui.sh" --check
echo 'TWO_DEYE_MODBUS_ARCHIVE_SOURCE_CHECK=PASS'
if [[ "$MODE" == "--check" ]]; then
  echo 'DRY_RUN=PASS MYSQL_WRITES=0 MODBUS_READS=0'
  exit 0
fi

sudo -v
[[ "$(git branch --show-current)" == "feature/deye-trb145-monitoring" ]] || {
  echo 'STOP: unexpected git branch' >&2; exit 2
}
sudo test -x /var/lib/rakurs/.venv/bin/python || {
  echo 'STOP: Rakurs Python venv missing' >&2; exit 2
}
if [[ "$MODE" == "--apply" ]]; then
  command -v mysqldump >/dev/null || command -v mariadb-dump >/dev/null || {
    echo 'STOP: MySQL backup client unavailable; no database changes' >&2; exit 2
  }
fi
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: current Rakurs service must be healthy before applying' >&2; exit 2
}

if [[ "$MODE" == "--apply" ]]; then
  echo '=== MANDATORY MYSQL BACKUP AND ATOMIC SYNTHETIC TWIN ARCHIVE ==='
  echo 'Real inverters will NOT be registered. This step writes simulated test data.'
  BACKUP_TRANSPORT="${RAKURS_MYSQL_BACKUP_TRANSPORT:-VERIFY_TLS}"
  [[ "$BACKUP_TRANSPORT" == "VERIFY_TLS" ||
     "$BACKUP_TRANSPORT" == "LAN_PLAINTEXT_ACK" ]] || {
    echo 'STOP: use VERIFY_TLS or explicit LAN_PLAINTEXT_ACK' >&2
    exit 2
  }
  if [[ "$BACKUP_TRANSPORT" == "LAN_PLAINTEXT_ACK" ]]; then
    echo 'WARNING: backup traffic and MySQL credentials will be UNENCRYPTED within the LAN.'
    echo 'This mode must only be used on the trusted 192.168.5.30 segment.'
  else
    echo 'MYSQL_BACKUP_TLS_VERIFICATION=REQUIRED'
  fi
  # sudo normally discards shell environment. Forward ONLY these approved switches.
  sudo env \
    "RAKURS_MYSQL_BACKUP_TRANSPORT=$BACKUP_TRANSPORT" \
    "RAKURS_MYSQL_BACKUP_CA_FILE=${RAKURS_MYSQL_BACKUP_CA_FILE:-}" \
    /var/lib/rakurs/.venv/bin/python "$HERE/seed_two_parallel_demo.py" --execute </dev/tty
  echo 'SYNTHETIC_DATABASE_COMMITTED=PASS'
else
  # Seed already committed on the prior attempt. Never ask for owner password
  # or repeat any DDL/DML; verify both devices before touching the website.
  echo 'RESUME=SKIP_SEED_AND_BACKUP'
  echo 'RESUME_DB_WRITES=0'
fi

echo '=== VERIFY EXACT TWO-INVERTER DB COUNTS (READ-ONLY ACCOUNT) ==='
# The rakurs service account cannot traverse /home/ivital. Supply the
# audited, non-secret Python source over stdin; do NOT chmod private folders.
cat "$HERE/audit_two_inverters_mysql.py" | sudo -u rakurs env \
  RAKURS_DB_USER=rakurs_web \
  RAKURS_DB_PASSWORD_FILE=/etc/rakurs/mysql_password \
  /var/lib/rakurs/.venv/bin/python -

echo 'TWIN_DATABASE_AUDIT_BEFORE_DEPLOY=PASS'
echo '=== DEPLOY NEW WEB API AND UNIQUE VERSIONED ASSETS ==='
bash "$HERE/deploy_parallel_ui.sh" --apply

echo '=== VERIFY AUTHORISED WEB DEMO AND RAW REGISTER ENDPOINTS ==='
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
    with db.cursor() as c:
        c.execute("SELECT id,session_version FROM rakurs_users "
                  "WHERE is_active=1 ORDER BY id LIMIT 1")
        user = c.fetchone()
assert user, "No active Rakurs account"
client = m.app.test_client()
with client.session_transaction(base_url="https://localhost") as session:
    session["uid"] = user["id"]
    session["ver"] = user["session_version"]
    session["at"] = int(time.time())
def get(path):
    res = client.get("/api/v1/" + path, base_url="https://localhost")
    assert res.status_code == 200, (path, res.status_code)
    return res.get_json()
topology = get("parallel")
assert topology["registered_inverters"] == 0
assert not topology["aggregate_verified"]
assert topology["battery_soc_percent"] is None
print("NO_FAKE_PHYSICAL_INVERTERS=PASS")
data = get("parallel/simulation")
assert data["simulation"] is True and data["storage_ready"] is True
assert len(data["devices"]) == 2
for index, device in enumerate(data["devices"], 1):
    assert device["slot_no"] == index
    assert (device["raw_registers"], device["decoded_fields"],
            device["ui_fields"]) == (815,624,316)
    key = device["device_key"]
    vals = get("parallel/simulation/latest?device=" + key)
    assert vals["simulation"] is True
    assert vals["values"]["battery_soc"]["quality"] == "simulated"
    raw = get("parallel/simulation/raw?device=" + key + "&limit=500")
    assert raw["total"] == 815 and len(raw["raw_registers"]) == 500
    rest = get("parallel/simulation/raw?device=" + key +
               "&offset=500&limit=500")
    assert len(rest["raw_registers"]) == 315
    seen = {(r["function_code"],r["register_address"])
            for r in raw["raw_registers"] + rest["raw_registers"]}
    assert len(seen) == 815
    print("DEYE_SIM_" + str(index) + "_FULL_RAW_MODBUS=PASS")
print("TWIN_PARALLEL_WEB_ARCHIVE=PASS")
PY
echo 'FULL_DEYE_MODBUS_DATABASE_AND_WEB=PASS'
if [[ "$MODE" == "--resume" ]]; then
  echo 'TWO_DEYE_RESUME=PASS MYSQL_WRITES=0'
fi
echo 'MODBUS_COMMANDS=0 PHYSICAL_DEVICES_CREATED=0'
echo 'OPEN=https://rakurs.zakryttya.org.ua/#/parallel'
