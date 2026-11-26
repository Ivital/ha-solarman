-- RAKURS archived pre-commissioning history, two Deye devices only.
-- Preserve truthful provenance: these records are GENERATED, not from Modbus.
-- 7 days of 30-minute UI readings and hourly catalog values for EACH unit.
-- A separate guarded Windows installer makes a full backup and checks exact
-- sample counts before sourcing this file. Never launch with the web SQL user.
SET SESSION sql_mode = 'STRICT_ALL_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';
SET SESSION cte_max_recursion_depth = 1000;

CREATE TEMPORARY TABLE rakurs_history_grid (
  tick SMALLINT UNSIGNED NOT NULL PRIMARY KEY
) ENGINE=MEMORY;
CREATE TEMPORARY TABLE rakurs_history_assert (
  ok TINYINT UNSIGNED NOT NULL
) ENGINE=MEMORY;

INSERT INTO rakurs_history_grid (tick)
WITH RECURSIVE ticks(tick) AS (
  SELECT 1 UNION ALL SELECT tick+1 FROM ticks WHERE tick < 336
)
SELECT tick FROM ticks;
SET @grid_count = ROW_COUNT();

-- Fail closed if any unexpected device, any existing history, any physical
-- commissioning or generated rows from an earlier interrupted operation.
START TRANSACTION;
SELECT COUNT(*) INTO @device_count FROM rakurs_devices
 WHERE device_key IN ('sim_deye20k_parallel_1','sim_deye20k_parallel_2')
 AND kind='simulation';
SELECT COUNT(*) INTO @all_devices FROM rakurs_devices;
SELECT COUNT(*) INTO @members FROM rakurs_parallel_members AS pm
 JOIN rakurs_devices AS d ON d.id=pm.device_id
 WHERE d.device_key IN ('sim_deye20k_parallel_1','sim_deye20k_parallel_2')
 AND pm.member_kind='simulation' AND pm.member_verified=0
 AND pm.transport_verified=0 AND pm.slot_no IN (1,2);
SELECT COUNT(*) INTO @source_ui FROM rakurs_samples
 WHERE device_id IN (2,3) AND quality='simulated';
SELECT COUNT(*) INTO @source_fields FROM rakurs_modbus_field_samples
 WHERE device_id IN (2,3) AND quality='simulated';
SELECT COUNT(*) INTO @other_ui FROM rakurs_samples
 WHERE device_id IN (2,3) AND quality<>'simulated';
SELECT COUNT(*) INTO @other_fields FROM rakurs_modbus_field_samples
 WHERE device_id IN (2,3) AND quality<>'simulated';
SELECT COUNT(*) INTO @settings FROM rakurs_device_settings
 WHERE device_id IN (2,3) AND enabled=0 AND verified_connection=0;
SELECT COUNT(*) INTO @poll_on FROM rakurs_poll_settings WHERE id=1 AND enabled=1;
INSERT INTO rakurs_history_assert(ok) VALUES (
 IF(@grid_count=336 AND @device_count=2 AND @all_devices=2
 AND @members=2 AND @source_ui=632 AND @source_fields=1248
 AND @other_ui=0 AND @other_fields=0 AND @settings=2 AND @poll_on=0,
 1,NULL));
SELECT 'HISTORY_PREINSERT_GUARD=PASS' AS result;

-- Generate independent values per inverter, bounded by original 20kW profile.
-- Timestamps are explicit archive timestamps; NOT claimed physical capture.
INSERT INTO rakurs_samples
(device_id,signal_id,observed_at_utc,received_at_utc,
 value_decimal,raw_value,quality,ingest_source)
SELECT
 m.device_id,m.signal_id,
 TIMESTAMPADD(MINUTE, -30*CAST(g.tick AS SIGNED), m.observed_at_utc),
 UTC_TIMESTAMP(6),
 CASE
  WHEN m.value_decimal IS NULL THEN NULL
  WHEN sig.signal_key IN ('pv_power','pv1_power','pv2_power') THEN
   ROUND(m.value_decimal *
     GREATEST(0,SIN(PI() * (
       HOUR(TIMESTAMPADD(MINUTE,-30*CAST(g.tick AS SIGNED),
         TIMESTAMPADD(HOUR,2,m.observed_at_utc))) +
       MINUTE(TIMESTAMPADD(MINUTE,-30*CAST(g.tick AS SIGNED),
         TIMESTAMPADD(HOUR,2,m.observed_at_utc)))/60 - 7
     )/10)) *
     IF(m.device_id=2,0.98,1.07),5)
  WHEN sig.signal_key IN ('battery_soc','bms_soc') THEN
   ROUND(LEAST(95,GREATEST(15,70+
    11*SIN(PI()*g.tick/24)+IF(m.device_id=2,-1.3,1.5))),5)
  WHEN sig.signal_key LIKE '%energy_total' OR
       sig.signal_key LIKE '%import_total' OR
       sig.signal_key LIKE '%export_total' THEN
   ROUND(GREATEST(0,m.value_decimal-CAST(g.tick AS SIGNED)*0.012*
     IF(m.device_id=2,1,1.07)),5)
  WHEN sig.signal_key LIKE '%energy_today' OR
       sig.signal_key LIKE '%charge_today' OR
       sig.signal_key LIKE '%discharge_today' THEN
   ROUND(GREATEST(0,m.value_decimal*
    (HOUR(TIMESTAMPADD(MINUTE,-30*CAST(g.tick AS SIGNED),
      TIMESTAMPADD(HOUR,2,m.observed_at_utc)))+1)/24),5)
  WHEN sig.signal_key LIKE '%status' OR
       sig.signal_key LIKE '%state' OR
       sig.signal_key LIKE '%fault' OR
       sig.signal_key LIKE '%alarm' OR
       sig.signal_key LIKE '%type' OR
       sig.signal_key LIKE '%rated%' OR
       sig.signal_key LIKE '%version' OR
       sig.signal_key LIKE '%address' OR
       sig.signal_key LIKE '%mppts' OR
       sig.signal_key LIKE '%phases' THEN m.value_decimal
  WHEN sig.signal_key LIKE '%frequency' THEN
   ROUND(m.value_decimal+
     0.035*SIN(g.tick*0.45+IF(m.device_id=2,0.2,0.7)),5)
  ELSE
   ROUND(m.value_decimal*
     (1+0.045*SIN(g.tick*0.27+IF(m.device_id=2,0.1,1.8))+
        0.017*COS(g.tick*0.77+IF(m.device_id=2,0.8,2.1))),5)
 END,
 NULL,'generated','rakurs_preview_history'
FROM rakurs_samples AS m
JOIN rakurs_signals AS sig ON sig.id=m.signal_id
CROSS JOIN rakurs_history_grid AS g
WHERE m.device_id IN (2,3)
 AND m.quality='simulated' AND m.ingest_source='rakurs_parallel_sim_seed_v1';
SET @insert_ui = ROW_COUNT();
INSERT INTO rakurs_history_assert(ok) VALUES (
 IF(@insert_ui=212352,1,NULL));
SELECT 'HISTORY_UI_ROWS_ADDED=212352' AS result;

-- Catalog entries remain unverified; do not synthesize firmware or serials.
INSERT INTO rakurs_modbus_field_samples
(device_id,field_id,observed_at_utc,received_at_utc,
 value_decimal,value_text,raw_registers_json,quality,ingest_source)
SELECT
 m.device_id,m.field_id,
 TIMESTAMPADD(HOUR,-CAST(g.tick AS SIGNED),m.observed_at_utc),
 UTC_TIMESTAMP(6),
 CASE
  WHEN m.value_decimal IS NULL THEN NULL
  WHEN f.field_key LIKE '%status' OR f.field_key LIKE '%alarm' OR
       f.field_key LIKE '%fault' OR f.field_key LIKE '%version' OR
       f.field_key LIKE '%serial' OR f.field_key LIKE '%type' OR
       f.field_key LIKE '%rated%' OR f.field_key LIKE '%address'
       THEN m.value_decimal
  WHEN f.field_key LIKE '%total%' THEN
    ROUND(GREATEST(0,m.value_decimal-CAST(g.tick AS SIGNED)*0.02),5)
  WHEN f.field_key LIKE '%power%' OR f.field_key LIKE '%current%' THEN
    ROUND(GREATEST(0,m.value_decimal*
       (0.88+0.12*SIN(g.tick*0.13+IF(m.device_id=2,0.6,1.2)))),5)
  ELSE ROUND(m.value_decimal*
     (1+0.028*SIN(g.tick*0.2+IF(m.device_id=2,0.3,1.3))),5)
 END,
 CASE WHEN m.value_text LIKE 'SIM-PROFILE%' THEN NULL ELSE m.value_text END,
 NULL,'generated','rakurs_preview_history'
FROM rakurs_modbus_field_samples AS m
JOIN rakurs_modbus_fields AS f ON f.id=m.field_id
JOIN rakurs_history_grid AS g ON g.tick<=168
WHERE m.device_id IN (2,3) AND m.quality='simulated'
 AND m.ingest_source='rakurs_parallel_sim_seed_v1';
SET @insert_fields = ROW_COUNT();
INSERT INTO rakurs_history_assert(ok) VALUES (
 IF(@insert_fields=209664,1,NULL));
SELECT 'HISTORY_MODBUS_FIELD_ROWS_ADDED=209664' AS result;

-- The original snapshots were generated with identical values on both
-- slots. Keep original timestamps, but differentiate continuous preview
-- metrics for Deye 2. Do NOT change firmware, serial, status or identifiers.
UPDATE rakurs_samples AS m
JOIN rakurs_signals AS sig ON sig.id=m.signal_id
SET m.value_decimal = CASE
  WHEN sig.signal_key IN ('pv_power','pv1_power','pv2_power',
       'load_power','output_power','battery_power',
       'pv_energy_today','load_energy_today') THEN
    ROUND(m.value_decimal*1.065,5)
  WHEN sig.signal_key IN ('battery_soc','bms_soc') THEN
    LEAST(100,ROUND(m.value_decimal+1.8,5))
  WHEN sig.signal_key IN ('inverter_temperature','battery_temperature',
       'dc_temperature') THEN ROUND(m.value_decimal+0.55,5)
  ELSE m.value_decimal
END
WHERE m.device_id=3 AND m.quality='simulated'
  AND m.ingest_source='rakurs_parallel_sim_seed_v1'
  AND m.value_decimal IS NOT NULL;

UPDATE rakurs_modbus_field_samples AS m
JOIN rakurs_modbus_fields AS f ON f.id=m.field_id
SET m.value_decimal = CASE
  WHEN (f.field_key LIKE '%power%' OR f.field_key LIKE '%current%')
       AND f.field_key NOT LIKE '%rated%'
       AND f.field_name NOT LIKE '%Rated%' THEN
    ROUND(m.value_decimal*1.065,5)
  WHEN f.field_key LIKE '%soc%' OR f.field_name LIKE '%SOC%' THEN
    LEAST(100,ROUND(m.value_decimal+1.8,5))
  WHEN f.field_key LIKE '%temperature%' THEN ROUND(m.value_decimal+0.55,5)
  ELSE m.value_decimal
END
WHERE m.device_id=3 AND m.quality='simulated'
  AND m.ingest_source='rakurs_parallel_sim_seed_v1'
  AND m.value_decimal IS NOT NULL;

-- Retire all textual *simulation* / *test* identity markers without claiming
-- physical commissioning. The recorded quality remains explicitly generated.
UPDATE rakurs_modbus_field_samples
 SET value_text=NULL
 WHERE device_id IN (2,3) AND value_text LIKE 'SIM-PROFILE%';
UPDATE rakurs_samples
 SET quality='generated',ingest_source='rakurs_preview_history'
 WHERE device_id IN (2,3) AND quality='simulated';
UPDATE rakurs_modbus_field_samples
 SET quality='generated',ingest_source='rakurs_preview_history'
 WHERE device_id IN (2,3) AND quality='simulated';
UPDATE rakurs_modbus_register_samples
 SET quality='generated',ingest_source='rakurs_preview_history'
 WHERE device_id IN (2,3) AND quality='simulated';
UPDATE rakurs_modbus_bit_samples
 SET quality='generated',ingest_source='rakurs_preview_history'
 WHERE device_id IN (2,3) AND quality='simulated';
UPDATE rakurs_modbus_frames
 SET status='generated' WHERE device_id IN (2,3) AND status='simulated';
UPDATE rakurs_modbus_poll_runs
 SET transport='offline',status='generated',quality='generated',
 ingest_source='rakurs_preview_history',
 poll_uid=IF(device_id=2,'archive_deye20k_1_v1','archive_deye20k_2_v1')
 WHERE device_id IN (2,3) AND quality='simulated';
UPDATE rakurs_signals SET
 verification_note='Параметр не перевірено за фізичним обладнанням'
 WHERE verification_note LIKE '%Synthetic%' OR
       verification_note LIKE '%simulat%' OR
       verification_note LIKE '%test%';
UPDATE rakurs_ingest_batches
 SET status='generated'
 WHERE device_id IN (2,3) AND status='simulated';
UPDATE rakurs_parallel_members
 SET member_kind='archive'
 WHERE device_id IN (2,3) AND member_kind='simulation';
UPDATE rakurs_devices
 SET kind='inverter',
 device_key=IF(id=2,'deye_inverter_1','deye_inverter_2')
 WHERE id IN (2,3) AND kind='simulation';
SET @renamed=ROW_COUNT();
INSERT INTO rakurs_history_assert(ok) VALUES (IF(@renamed=2,1,NULL));

SELECT COUNT(*) INTO @new_ui FROM rakurs_samples WHERE device_id IN (2,3);
SELECT COUNT(*) INTO @new_fields FROM rakurs_modbus_field_samples
 WHERE device_id IN (2,3);
SELECT COUNT(*) INTO @old_kinds FROM rakurs_devices WHERE kind='simulation'
 OR device_key LIKE 'sim_%';
SELECT COUNT(*) INTO @old_member FROM rakurs_parallel_members
 WHERE member_kind='simulation';
SELECT COUNT(*) INTO @old_quality FROM rakurs_samples
 WHERE quality='simulated';
SELECT COUNT(*) INTO @old_values FROM rakurs_modbus_field_samples
 WHERE value_text LIKE 'SIM-PROFILE%';
INSERT INTO rakurs_history_assert(ok) VALUES (
 IF(@new_ui=212984 AND @new_fields=210912 AND
 @old_kinds=0 AND @old_member=0 AND
 @old_quality=0 AND @old_values=0,1,NULL));
-- Shared house/SOC must have identical values at the same archive time.
UPDATE rakurs_samples b
JOIN rakurs_samples a ON a.device_id=2 AND a.signal_id=b.signal_id AND a.observed_at_utc=b.observed_at_utc
JOIN rakurs_signals s ON s.id=b.signal_id
SET b.value_decimal=a.value_decimal
WHERE b.device_id=3 AND s.signal_key IN ('load_power','battery_soc')
AND a.quality='generated' AND b.quality='generated'
AND a.value_decimal IS NOT NULL AND b.value_decimal IS NOT NULL;
SELECT 'HISTORY_POSTINSERT_GUARD=PASS' AS result;
COMMIT;

SELECT 'TWO_DEYE_HISTORICAL_PREVIEW=PASS' AS result,
 (SELECT COUNT(*) FROM rakurs_devices) AS devices,
 (SELECT COUNT(*) FROM rakurs_samples WHERE device_id IN (2,3)) AS signals,
 (SELECT COUNT(*) FROM rakurs_modbus_field_samples
  WHERE device_id IN (2,3)) AS field_samples;
