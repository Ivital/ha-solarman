#Requires -RunAsAdministrator
<#
  RAKURS: remove ONLY the former single-device Deye from local Windows MySQL.
  Run on the MySQL Windows Server (192.168.5.30), not on ergodata-lnx.
  Default is read-only. -Apply creates full SQL backup, validates it, then
  deletes only exact legacy device key and its child rows in one transaction.
  MySQL credentials are requested interactively by mysql.exe/mysqldump.exe.
#>
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Bin = Join-Path $env:ProgramFiles 'MySQL\MySQL Server 8.0\bin'
$MySql = Join-Path $Bin 'mysql.exe'
$Dump = Join-Path $Bin 'mysqldump.exe'
foreach ($Exe in @($MySql,$Dump)) {
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
        throw "STOP: MySQL client missing: $Exe"
    }
}
$BaseArgs = @('--protocol=TCP','--host=127.0.0.1','--port=3306',
              '--user=Ivital','--password','--database=rakurs',
              '--default-character-set=utf8mb4')
$InspectSql = @'
SELECT id,device_key,kind,model
FROM rakurs_devices
ORDER BY id;
SELECT d.id,d.device_key,d.kind,
  (SELECT COUNT(*) FROM rakurs_samples WHERE device_id=d.id) AS samples,
  (SELECT COUNT(*) FROM rakurs_modbus_field_samples WHERE device_id=d.id) AS fields,
  (SELECT COUNT(*) FROM rakurs_device_events WHERE device_id=d.id) AS events,
  (SELECT COUNT(*) FROM rakurs_ingest_batches WHERE device_id=d.id) AS batches,
  (SELECT COUNT(*) FROM rakurs_parallel_members WHERE device_id=d.id) AS pair_memberships,
  (SELECT COUNT(*) FROM rakurs_modbus_poll_runs WHERE device_id=d.id) AS poll_runs
FROM rakurs_devices d
WHERE d.device_key IN ('simulated_deye_sg05lp3',
  'sim_deye20k_parallel_1','sim_deye20k_parallel_2')
ORDER BY d.device_key;
'@
Write-Host '=== MYSQL DEVICE AND REFERENCES (READ ONLY) ==='
& $MySql @BaseArgs "--execute=$InspectSql"
if ($LASTEXITCODE -ne 0) { throw 'STOP: read-only MySQL inspection failed' }
if (-not $Apply) {
    Write-Host 'OLD_DEYE_RETIRE=CHECK_ONLY'
    Write-Host 'MYSQL_WRITES=0'
    exit 0
}

$BackupDir = Join-Path $env:ProgramData 'RAKURS\backups'
New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
# MySQL dumps can include private account and credential hashes.
& icacls.exe $BackupDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'STOP: unable to protect backup directory ACL' }
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Backup = Join-Path $BackupDir ("rakurs-before-old-deye-" + $Stamp + '.sql')
Write-Host '=== FULL MYSQL BACKUP BEFORE ANY DELETE ==='
$DumpArgs = @('--protocol=TCP','--host=127.0.0.1','--port=3306',
 '--user=Ivital','--password','--default-character-set=utf8mb4',
 '--single-transaction','--quick','--skip-lock-tables','--no-tablespaces',
 '--set-gtid-purged=OFF','--column-statistics=0','--hex-blob',
 '--routines','--events','--triggers','--comments',
 "--result-file=$Backup",'rakurs')
& $Dump @DumpArgs
if ($LASTEXITCODE -ne 0) { throw 'STOP: MySQL full backup failed; database unchanged' }
$Info = Get-Item -LiteralPath $Backup
if ($Info.Length -lt 1024) { throw 'STOP: backup too small; database unchanged' }
# Windows PowerShell 4.0 on Windows Server 2012 R2 does not support ::new().
# File.OpenText returns a StreamReader and handles UTF-8 dump text directly.
$Stream = [System.IO.File]::OpenText($Backup)
$Tables = 0
$Completed = $false
try {
    while (($Line = $Stream.ReadLine()) -ne $null) {
        if ($Line -match '^CREATE TABLE ') { $Tables++ }
        if ($Line -like '*Dump completed on*') { $Completed = $true }
    }
} finally { $Stream.Dispose() }
if ($Tables -lt 16 -or -not $Completed) {
    throw ("STOP: incomplete SQL backup, CREATE TABLE={0}, footer={1}" -f $Tables,$Completed)
}
$Hash = (Get-FileHash -LiteralPath $Backup -Algorithm SHA256).Hash
Write-Host ("BACKUP_FILE={0}" -f $Backup)
Write-Host ("BACKUP_SHA256={0}" -f $Hash)
Write-Host ("BACKUP_TABLES={0}" -f $Tables)

# All guards and deletes run on ONE MySQL connection inside ONE transaction.
# An error in a guard aborts mysql.exe; InnoDB automatically rolls back.
# The failure guard deliberately references a nonexistent table.
$DeleteSql = @'
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION;
SET @old_id := (SELECT id FROM rakurs_devices
 WHERE device_key='simulated_deye_sg05lp3' AND kind='simulation'
 LIMIT 1);
SET @all_count := (SELECT COUNT(*) FROM rakurs_devices);
SET @keep_count := (SELECT COUNT(DISTINCT d.device_key)
 FROM rakurs_devices AS d
 JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id
 JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id
 WHERE pg.group_key='deye_sun20k_parallel_shared_battery'
 AND d.device_key IN ('sim_deye20k_parallel_1','sim_deye20k_parallel_2')
 AND pm.slot_no IN (1,2));
SET @slot_count := (SELECT COUNT(DISTINCT pm.slot_no)
 FROM rakurs_devices AS d
 JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id
 JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id
 WHERE pg.group_key='deye_sun20k_parallel_shared_battery'
 AND d.device_key IN ('sim_deye20k_parallel_1','sim_deye20k_parallel_2')
 AND pm.slot_no IN (1,2));
SET @old_memberships := (SELECT COUNT(*) FROM rakurs_parallel_members
 WHERE device_id=@old_id);
SET @unknown_fk := (SELECT COUNT(*)
 FROM information_schema.KEY_COLUMN_USAGE
 WHERE REFERENCED_TABLE_SCHEMA=DATABASE()
 AND REFERENCED_TABLE_NAME IN
 ('rakurs_devices','rakurs_modbus_poll_runs','rakurs_modbus_frames')
 AND TABLE_NAME NOT IN (
 'rakurs_samples','rakurs_modbus_field_samples',
 'rakurs_device_events','rakurs_ingest_batches',
 'rakurs_parallel_members','rakurs_modbus_poll_runs',
 'rakurs_modbus_frames','rakurs_modbus_register_samples',
 'rakurs_modbus_bit_samples','rakurs_modbus_poll_fingerprints'));
SET @valid := (@old_id IS NOT NULL AND @all_count=3
 AND @keep_count=2 AND @slot_count=2
 AND @old_memberships=0 AND @unknown_fk=0);
SET @guard_sql := IF(@valid,
 'SELECT ''RAKURS_PREDELETE_GUARD=PASS'' AS result',
 'SELECT * FROM RAKURS_GUARD_FAILED_STOP_AND_ROLLBACK');
PREPARE guard_stmt FROM @guard_sql;
EXECUTE guard_stmt;
DEALLOCATE PREPARE guard_stmt;
SET @have_fp := (SELECT COUNT(*) FROM information_schema.TABLES
 WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='rakurs_modbus_poll_fingerprints');
SET @fp_sql := IF(@have_fp=1,
 'DELETE FROM rakurs_modbus_poll_fingerprints WHERE poll_id IN (SELECT id FROM rakurs_modbus_poll_runs WHERE device_id=@old_id)',
 'SELECT ''NO_POLL_FINGERPRINT_TABLE'' AS result');
PREPARE fp_stmt FROM @fp_sql;
EXECUTE fp_stmt;
DEALLOCATE PREPARE fp_stmt;
DELETE FROM rakurs_modbus_bit_samples WHERE device_id=@old_id;
DELETE FROM rakurs_modbus_register_samples WHERE device_id=@old_id;
DELETE FROM rakurs_modbus_frames WHERE device_id=@old_id;
DELETE FROM rakurs_modbus_poll_runs WHERE device_id=@old_id;
DELETE FROM rakurs_modbus_field_samples WHERE device_id=@old_id;
DELETE FROM rakurs_samples WHERE device_id=@old_id;
DELETE FROM rakurs_device_events WHERE device_id=@old_id;
DELETE FROM rakurs_ingest_batches WHERE device_id=@old_id;
DELETE FROM rakurs_devices
 WHERE id=@old_id AND device_key='simulated_deye_sg05lp3'
 AND kind='simulation';
SET @deleted_old := ROW_COUNT();
SET @remaining := (SELECT COUNT(*) FROM rakurs_devices);
SET @keep_after := (SELECT COUNT(*) FROM rakurs_devices
 WHERE device_key IN ('sim_deye20k_parallel_1','sim_deye20k_parallel_2'));
SET @post_ok := (@deleted_old=1 AND @remaining=2 AND @keep_after=2);
SET @post_sql := IF(@post_ok,
 'SELECT ''RAKURS_POSTDELETE_GUARD=PASS'' AS result',
 'SELECT * FROM RAKURS_POSTDELETE_GUARD_FAILED_ROLLBACK');
PREPARE post_stmt FROM @post_sql;
EXECUTE post_stmt;
DEALLOCATE PREPARE post_stmt;
COMMIT;
SELECT 'OLD_DEYE_REMOVAL=PASS' AS result, @old_id AS removed_device_id,
 @remaining AS remaining_devices;
'@
Write-Host '=== DELETE EXACTLY ONE LEGACY DEYE (WITH TRANSACTIONAL GUARDS) ==='
& $MySql @BaseArgs "--execute=$DeleteSql"
if ($LASTEXITCODE -ne 0) {
    throw ("STOP: deletion refused or rolled back; verified backup remains at " + $Backup)
}
Write-Host 'OLD_DEYE_REMOVAL=PASS'
Write-Host 'REMAINING_DEVICES=2'
Write-Host ("BACKUP_FILE={0}" -f $Backup)
