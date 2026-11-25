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
  if docker exec "$NAME" mysqladmin ping -uroot "-p$PASS" --silent >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker exec "$NAME" mysqladmin ping -uroot "-p$PASS" --silent >/dev/null
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
  (SELECT COUNT(*) FROM rakurs_modbus_field_samples WHERE value_text LIKE 'SIM-PROFILE%'))")"
test "$actual" = '2:212984:210912:2:212984:0:0' || {
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
