#!/usr/bin/env bash
# Fully isolated MySQL8 integration test of the guarded history import.
# No production addresses, credentials, user data or networking to the plant.
set -Eeuo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DBNAME="rakurs"
PASS="rakurs-only-ci-archive-20261125"
NAME="rakurs-history-ci-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

docker run -d --name "$NAME" --network none \
  -e MYSQL_ROOT_PASSWORD="$PASS" -e MYSQL_DATABASE="$DBNAME" \
  mysql:8.0 >/dev/null
# No docker network means no MySQL port even on the runner.
for attempt in $(seq 1 90); do
  # mysqladmin ping returns success even for Access denied while the image
  # is still creating the real root account. Require authenticated SQL.
  if docker exec "$NAME" mysql -uroot "-p$PASS" "$DBNAME" \
      --execute='SELECT 1' >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker exec "$NAME" mysql -uroot "-p$PASS" "$DBNAME" \
  --execute='SELECT 1' >/dev/null
query() { docker exec -i "$NAME" mysql -uroot "-p$PASS" "$DBNAME" "$@"; }

query < "$ROOT/deploy/rakurs/archive_mysql_schema.sql"
query < "$ROOT/deploy/rakurs/standalone/auth_schema.sql"
query < "$ROOT/deploy/rakurs/standalone/parallel_raw_modbus_schema.sql"
query < "$ROOT/deploy/rakurs/standalone/modbus_full_schema.sql"
query < "$ROOT/deploy/rakurs/standalone/polling_config_schema.sql"

query <<'SQL'
INSERT INTO rakurs_devices
(id,device_key,kind,model,created_at_utc)
VALUES (2,'sim_deye20k_parallel_1','simulation','SUN-20K-SG05LP3-EU-SM2',UTC_TIMESTAMP(6)),
       (3,'sim_deye20k_parallel_2','simulation','SUN-20K-SG05LP3-EU-SM2',UTC_TIMESTAMP(6));
INSERT INTO rakurs_parallel_groups
(id,group_key,model,expected_inverters,battery_mode,topology_status,created_at_utc)
VALUES (1,'deye_sun20k_parallel_shared_battery',
'SUN-20K-SG05LP3-EU-SM2',2,'shared','unverified',UTC_TIMESTAMP(6));
INSERT INTO rakurs_parallel_members
(group_id,device_id,slot_no,member_kind,parallel_role,
transport_verified,member_verified)
VALUES (1,2,1,'simulation','unverified',0,0),
       (1,3,2,'simulation','unverified',0,0);
INSERT INTO rakurs_device_settings
(device_id,display_name,enabled,updated_at_utc)
VALUES (2,'Deye №1',0,UTC_TIMESTAMP(6)),
       (3,'Deye №2',0,UTC_TIMESTAMP(6));

INSERT INTO rakurs_signals
(signal_key,label_uk,unit,verified,verification_note)
VALUES ('pv_power','Сумарна потужність PV','кВт',0,'Synthetic sample');
INSERT INTO rakurs_signals
(signal_key,label_uk,unit,verified,verification_note)
WITH RECURSIVE seq(n) AS (
  SELECT 2 UNION ALL SELECT n+1 FROM seq WHERE n<316
)
SELECT CONCAT('signal_',n), CONCAT('Параметр ',n),'кВт',0,'Synthetic sample'
FROM seq;

INSERT INTO rakurs_modbus_fields
(field_key,profile_name,group_name,field_name,platform,unit,
registers_json,register_sets_json,created_at_utc)
WITH RECURSIVE seq(n) AS (
  SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<624
)
SELECT CONCAT('field_',n),'Deye P3','Info',
       CONCAT('Field ',n),'sensor','W','[]','[]',UTC_TIMESTAMP(6)
FROM seq;

INSERT INTO rakurs_samples
(device_id,signal_id,observed_at_utc,received_at_utc,value_decimal,
quality,ingest_source)
SELECT d.id,s.id,'2026-11-25 00:16:18.000000',
'2026-11-25 00:16:18.000000',
IF(s.signal_key='pv_power',5.1,2.3),
'simulated','rakurs_parallel_sim_seed_v1'
FROM rakurs_devices AS d CROSS JOIN rakurs_signals AS s;
INSERT INTO rakurs_modbus_field_samples
(device_id,field_id,observed_at_utc,received_at_utc,value_decimal,
value_text,quality,ingest_source)
SELECT d.id,f.id,'2026-11-25 00:16:18.000000',
'2026-11-25 00:16:18.000000',1.5,
IF(f.field_key='field_1','SIM-PROFILE-01-UNIT1',NULL),
'simulated','rakurs_parallel_sim_seed_v1'
FROM rakurs_devices AS d CROSS JOIN rakurs_modbus_fields AS f;

INSERT INTO rakurs_modbus_poll_runs
(device_id,poll_uid,observed_at_utc,finished_at_utc,transport,slave_id,
status,quality,ingest_source,request_count,response_count,created_at_utc)
VALUES (2,'simdeye20k_parallel_1_v1','2026-11-25 00:16:18',
'2026-11-25 00:16:18','simulation',NULL,'simulated','simulated',
'rakurs_parallel_sim_seed_v1',1,1,UTC_TIMESTAMP(6)),
(3,'simdeye20k_parallel_2_v1','2026-11-25 00:16:18',
'2026-11-25 00:16:18','simulation',NULL,'simulated','simulated',
'rakurs_parallel_sim_seed_v1',1,1,UTC_TIMESTAMP(6));
INSERT INTO rakurs_ingest_batches
(device_id,started_at_utc,finished_at_utc,received_count,accepted_count,status)
VALUES (2,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6),940,940,'simulated'),
(3,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6),940,940,'simulated');
SQL

docker cp "$ROOT/deploy/rakurs/standalone/populate_two_deye_history.sql" \
  "$NAME:/tmp/populate_two_deye_history.sql"

docker exec "$NAME" mysql -uroot "-p$PASS" "$DBNAME" \
  --execute='source /tmp/populate_two_deye_history.sql'

actual="$(docker exec "$NAME" mysql -uroot "-p$PASS" "$DBNAME" -N -B \
  --execute="SELECT CONCAT(
  (SELECT COUNT(*) FROM rakurs_devices),':',
  (SELECT COUNT(*) FROM rakurs_samples),':',
  (SELECT COUNT(*) FROM rakurs_modbus_field_samples),':',
  (SELECT COUNT(*) FROM rakurs_parallel_members WHERE member_kind='archive'),':',
  (SELECT COUNT(*) FROM rakurs_samples WHERE quality='generated'),':',
  (SELECT COUNT(*) FROM rakurs_poll_settings WHERE enabled=1),':',
  (SELECT COUNT(*) FROM rakurs_modbus_field_samples WHERE value_text LIKE 'SIM-PROFILE%'),':',
  (SELECT IF(ABS(
    (SELECT m.value_decimal FROM rakurs_samples AS m JOIN rakurs_signals AS sig
      ON sig.id=m.signal_id WHERE m.device_id=2 AND sig.signal_key='pv_power'
      ORDER BY m.observed_at_utc DESC LIMIT 1)-
    (SELECT m.value_decimal FROM rakurs_samples AS m JOIN rakurs_signals AS sig
      ON sig.id=m.signal_id WHERE m.device_id=3 AND sig.signal_key='pv_power'
      ORDER BY m.observed_at_utc DESC LIMIT 1))>0.1,1,0)))")"
test "$actual" = '2:212984:210912:2:212984:0:0:1' || {
  echo "INTEGRATION_MYSQL_POSTVERIFY_FAIL=$actual" >&2
  exit 2
}
# Refuse a repeat, never accidentally double-insert history.
if docker exec "$NAME" mysql -uroot "-p$PASS" "$DBNAME" \
    --execute='source /tmp/populate_two_deye_history.sql' >/dev/null 2>&1; then
  echo 'INTEGRATION_IDEMPOTENCE_GUARD=FAIL: repeated source unexpectedly succeeded' >&2
  exit 2
fi
echo 'INTEGRATION_MYSQL_POSTVERIFY=PASS'
echo 'INTEGRATION_IDEMPOTENCE_GUARD=PASS'
echo 'HARDWARE_MODBUS_COMMANDS=0'


# Execute the actual application analytics SELECTs against this isolated database.
# Test counters are inserted only after the history import and exact-count checks.
query <<'SQL'
INSERT INTO rakurs_signals(signal_key,label_uk,unit,verified,verification_note)
VALUES ('pv_energy_total','PV counter','кВт·год',0,'CI fixture'),
       ('grid_import_total','Import counter','кВт·год',0,'CI fixture');
INSERT INTO rakurs_samples(device_id,signal_id,observed_at_utc,received_at_utc,value_decimal,quality,ingest_source)
SELECT 2,s.id,t.stamp,t.stamp,CASE WHEN s.signal_key='pv_energy_total' THEN t.pv ELSE t.imp END,'generated','ci_energy_counter'
FROM rakurs_signals s CROSS JOIN (
  SELECT '2026-11-25 00:00:00' AS stamp,10.5 AS pv,7 AS imp
  UNION ALL SELECT '2026-11-25 00:10:00',11.7,3
  UNION ALL SELECT '2026-11-25 00:16:18',13.7,4
) t WHERE s.signal_key IN ('pv_energy_total','grid_import_total');
SQL
query <<'SQL'
SET SESSION cte_max_recursion_depth=2000;
INSERT INTO rakurs_signals(signal_key,label_uk,unit,verified,verification_note) VALUES ('ci_dense_signal','Dense chart fixture','кВт',0,'CI fixture');
INSERT INTO rakurs_samples(device_id,signal_id,observed_at_utc,received_at_utc,value_decimal,quality,ingest_source)
WITH RECURSIVE seq(n) AS (SELECT 0 UNION ALL SELECT n+1 FROM seq WHERE n<1199)
SELECT 2,s.id,TIMESTAMPADD(SECOND,n,'2026-11-24 12:30:00.123456'),
'2026-11-24 13:00:00',CASE WHEN n=600 THEN 10000 WHEN n=601 THEN -9000 ELSE MOD(n,9) END,'generated','ci_chart_resolution'
FROM rakurs_signals s CROSS JOIN seq WHERE s.signal_key='ci_dense_signal';
SQL
RAKURS_CI_CONTAINER="$NAME" RAKURS_CI_PASSWORD="$PASS" PYTHONPATH="$ROOT/deploy/rakurs" python3 - <<'PY'
import os
import subprocess
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from standalone import app as module

def literal(value):
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        value = value.isoformat(sep=" ")
    assert isinstance(value, str)
    assert chr(92) not in value
    return "'" + value.replace("'", "''") + "'"

def query(sql, params=()):
    assert sql.lstrip().upper().startswith(("SELECT ", "WITH "))
    statement = sql % tuple(literal(p) for p in params)
    cmd = ["docker", "exec", "-i", os.environ["RAKURS_CI_CONTAINER"], "mysql",
           "-uroot", "-p" + os.environ["RAKURS_CI_PASSWORD"], "rakurs", "--batch", "--raw"]
    result = subprocess.run(cmd, input=statement, text=True, capture_output=True, check=True)
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return []
    names = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        row = dict(zip(names, line.split("\t")))
        for key, value in row.items():
            if value == "NULL":
                row[key] = None
            elif key in {"stamp", "first_at", "last_at", "observed_at_utc", "received_at_utc"}:
                row[key] = datetime.fromisoformat(value)
            elif key in {"samples", "decreases"}:
                row[key] = int(value)
            elif key in {"opening_value", "closing_value", "value_decimal"}:
                row[key] = Decimal(value)
        rows.append(row)
    return rows

def one(sql, params=()):
    result = query(sql, params)
    return result[0] if result else None

with patch.object(module, "selected_overview_device_id", return_value=2), patch.object(module, "one", side_effect=one), patch.object(module, "rows", side_effect=query):
    with module.app.test_request_context("/api/v1/energy?period=day"):
        data = module.energy().get_json()
        items = {r["key"]: r for r in data["counters"]}
        assert Decimal(items["pv_energy_total"]["value"]) == Decimal("3.2"), items
        assert items["grid_import_total"]["value"] is None, items
        assert items["grid_import_total"]["decreases"] == 1
        assert data["start"] == "2026-11-24T22:00:00Z", data
    with module.app.test_request_context("/api/v1/latest?keys=pv_energy_total,grid_import_total"):
        result = module.latest().get_json()
        assert Decimal(result["values"]["pv_energy_total"]["value_decimal"]) == Decimal("13.7")
        assert Decimal(result["values"]["grid_import_total"]["value_decimal"]) == Decimal("4")
    with module.app.test_request_context("/api/v1/history?key=pv_energy_total&hours=24&anchor=latest"):
        data = module.history().get_json()
        assert len(data["points"]) == 3, data
    from datetime import timedelta
    start=datetime(2026,11,24,12,30,0,123456)
    end=start+timedelta(seconds=1199)
    overview=module.chart_points(2,"ci_dense_signal",start,end)
    assert overview["resolution"]=="extrema" and overview["sample_count"]==1200, overview["sample_count"]
    points=overview["points"]
    assert len(points)<=960
    assert max(Decimal(r["value_decimal"]) for r in points)==Decimal("10000")
    assert min(Decimal(r["value_decimal"]) for r in points)==Decimal("-9000")
    assert points[0]["observed_at_utc"]==module.serialize(start)
    assert points[-1]["observed_at_utc"]==module.serialize(end)
    from urllib.parse import urlencode
    params={"key":"ci_dense_signal","start":module.serialize(start),"end":module.serialize(end)}
    with module.app.test_request_context("/api/v1/history/range?"+urlencode(params)):
        first=module.history_range().get_json()
    assert len(first["points"])==1000 and first["truncated"]
    params["before"]=first["next_before"]
    with module.app.test_request_context("/api/v1/history/range?"+urlencode(params)):
        second=module.history_range().get_json()
    assert len(second["points"])==200 and not second["truncated"]
    assert len({r["observed_at_utc"] for r in first["points"]+second["points"]})==1200
print("INTEGRATION_MYSQL_ADAPTIVE_HISTORY_AND_RAW_CURSOR=PASS")
print("INTEGRATION_MYSQL_ANALYTICS_COUNTERS_BATCH_LATEST_AND_ARCHIVE=PASS")
PY


# Verify the exact shared-signal migration, including refusal of measured rows,
# repeatability and preservation of the other signals, in isolated MySQL only.
query <<'SQL'
INSERT INTO rakurs_signals(signal_key,label_uk,unit,verified,verification_note)
VALUES ('load_power','Load','кВт',0,'CI fixture'),('battery_soc','SOC','%',0,'CI fixture');
INSERT INTO rakurs_samples(device_id,signal_id,observed_at_utc,received_at_utc,value_decimal,quality,ingest_source)
SELECT d.id,s.id,'2026-11-25 00:16:18.000786','2026-11-25 00:16:18',
 CASE WHEN s.signal_key='load_power' THEN IF(d.id=2,6.4,6.816) ELSE IF(d.id=2,72,73.8) END,
 'generated','rakurs_preview_history'
FROM rakurs_devices d CROSS JOIN rakurs_signals s WHERE s.signal_key IN ('load_power','battery_soc');
SQL
# The CI history fixture has precisely two archive members, polling disabled.
for attempt in 1 2; do
  output="$(query < "$ROOT/deploy/rakurs/standalone/sync_shared_archive.sql")"
  echo "$output"
  grep -Fq 'SHARED_ARCHIVE_SYNC=PASS' <<<"$output"
done
actual="$(query -N -B --execute="SELECT CONCAT(s.signal_key,':',a.value_decimal,':',b.value_decimal) FROM rakurs_samples a JOIN rakurs_samples b ON b.device_id=3 AND a.signal_id=b.signal_id AND a.observed_at_utc=b.observed_at_utc JOIN rakurs_signals s ON s.id=a.signal_id WHERE a.device_id=2 AND s.signal_key IN ('load_power','battery_soc') ORDER BY s.signal_key")"
test "$actual" = $'battery_soc:72.000000000:72.000000000\nload_power:6.400000000:6.400000000'
# Refuse if raw/physical data enters the target, even when a difference exists.
query --execute="UPDATE rakurs_samples m JOIN rakurs_signals s ON s.id=m.signal_id SET m.quality='measured',m.value_decimal=75 WHERE m.device_id=3 AND s.signal_key='battery_soc'"
if query < "$ROOT/deploy/rakurs/standalone/sync_shared_archive.sql" >/dev/null 2>&1; then
  echo 'SHARED_ARCHIVE_PHYSICAL_GUARD=FAIL';exit 2
fi
test "$(query -N -B --execute="SELECT m.value_decimal FROM rakurs_samples m JOIN rakurs_signals s ON s.id=m.signal_id WHERE m.device_id=3 AND s.signal_key='battery_soc'")" = '75.000000000'
echo 'SHARED_ARCHIVE_MYSQL_TRANSACTION_AND_GUARDS=PASS'
