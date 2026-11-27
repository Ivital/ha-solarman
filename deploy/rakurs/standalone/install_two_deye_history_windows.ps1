#Requires -RunAsAdministrator
<#
RAKURS: seven-day independent archived Deye history, without hardware access.
Default is READ ONLY. -Apply makes a verified full SQL backup, then runs one
guarded transaction. Pinned Git SHA required. Windows PowerShell 4 compatible.
Provenance remains 'generated'; never assert these are measured from inverters.
No DELETE/TRUNCATE, no Modbus requests, no inverter control.
#>
param([switch]$Apply,[string]$GitRef='')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest

$Bin=Join-Path $env:ProgramFiles 'MySQL\MySQL Server 8.0\bin'
$MySql=Join-Path $Bin 'mysql.exe'
$Dump=Join-Path $Bin 'mysqldump.exe'
foreach($exe in @($MySql,$Dump)){
  if(-not(Test-Path -LiteralPath $exe -PathType Leaf)){
    throw "Missing MySQL client: $exe"
  }
}
$Base=@('--protocol=TCP','--host=127.0.0.1','--port=3306',
 '--user=Ivital','--password','--database=rakurs',
 '--default-character-set=utf8mb4')
$Inventory=@'
SELECT id,device_key,kind,model FROM rakurs_devices ORDER BY id;
SELECT device_id,COUNT(*) AS signals,MIN(observed_at_utc) AS oldest,
MAX(observed_at_utc) AS latest FROM rakurs_samples
GROUP BY device_id ORDER BY device_id;
SELECT device_id,COUNT(*) AS field_samples,
MIN(observed_at_utc) AS oldest,MAX(observed_at_utc) AS latest
FROM rakurs_modbus_field_samples GROUP BY device_id ORDER BY device_id;
SELECT id,enabled,interval_seconds FROM rakurs_poll_settings;
'@
Write-Host '=== MYSQL PREVIEW DATA INVENTORY / NO WRITES ==='
& $MySql @Base "--execute=$Inventory"
if($LASTEXITCODE -ne 0){throw 'Database inventory failed'}
if(-not $Apply){
  Write-Host 'HISTORY_PREVIEW_CHECK_ONLY=PASS'
  Write-Host 'MYSQL_WRITES=0 MODBUS_COMMANDS=0'
  exit 0
}
if($GitRef -notmatch '^[0-9a-f]{40}$'){
  throw 'Pinned full Git commit SHA required'
}
$Guard=@'
SELECT IF(
 (SELECT COUNT(*) FROM rakurs_devices)=2
 AND (SELECT COUNT(*) FROM rakurs_devices WHERE id IN(2,3)
      AND kind='simulation' AND device_key IN
      ('sim_deye20k_parallel_1','sim_deye20k_parallel_2'))=2
 AND (SELECT COUNT(*) FROM rakurs_samples
      WHERE device_id IN(2,3) AND quality='simulated')=632
 AND (SELECT COUNT(*) FROM rakurs_modbus_field_samples
      WHERE device_id IN(2,3) AND quality='simulated')=1248
 AND (SELECT COUNT(*) FROM rakurs_device_settings
      WHERE device_id IN(2,3) AND enabled=0 AND verified_connection=0)=2
 AND (SELECT COUNT(*) FROM rakurs_poll_settings
      WHERE id=1 AND enabled=1)=0,'READY','STOP') AS result;
'@
Write-Host '=== STRICT EXACT-BASELINE CHECK ==='
$Result=@(& $MySql @Base '--batch' '--skip-column-names' "--execute=$Guard")
if($LASTEXITCODE -ne 0){throw 'Baseline audit failed'}
if(($Result -join '').Trim() -ne 'READY'){
  throw 'Data not in verified original two-Deye state; refusing to append'
}
Write-Host 'TWO_DEYE_PREVIEW_BASELINE=PASS'

$Dir=Join-Path $env:ProgramData 'RAKURS\backups'
New-Item -ItemType Directory -Path $Dir -Force | Out-Null
& icacls.exe $Dir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not secure backup directory'}
$Stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$Backup=Join-Path $Dir ('rakurs-before-historical-preview-'+$Stamp+'.sql')
$DumpArgs=@('--protocol=TCP','--host=127.0.0.1','--port=3306',
 '--user=Ivital','--password','--default-character-set=utf8mb4',
 '--single-transaction','--quick','--skip-lock-tables','--no-tablespaces',
 '--set-gtid-purged=OFF','--column-statistics=0','--hex-blob',
 '--routines','--events','--triggers','--comments',
 "--result-file=$Backup",'rakurs')
Write-Host '=== FULL MYSQL BACKUP BEFORE ANY CHANGES ==='
& $Dump @DumpArgs
if($LASTEXITCODE -ne 0){throw 'Backup failed; database unchanged'}
if((Get-Item -LiteralPath $Backup).Length -lt 1024){
  throw 'Incomplete backup'
}
$Stream=[System.IO.File]::OpenText($Backup)
$Tables=0
$Completed=$false
try{
  while(($Line=$Stream.ReadLine()) -ne $null){
    if($Line -match '^CREATE TABLE '){$Tables++}
    if($Line -like '*Dump completed on*'){$Completed=$true}
  }
} finally { $Stream.Dispose() }
if($Tables -lt 16 -or -not $Completed){
  throw 'Full backup completeness check failed'
}
$Hash=(Get-FileHash -LiteralPath $Backup -Algorithm SHA256).Hash
Write-Host ('BACKUP_FILE='+$Backup)
Write-Host ('BACKUP_SHA256='+$Hash)
Write-Host ('BACKUP_TABLES='+$Tables)

[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$SqlFile=Join-Path $Dir ('populate-two-deye-'+$GitRef+'.sql')
$Url='https://raw.githubusercontent.com/Ivital/ha-solarman/'+$GitRef+
  '/deploy/rakurs/standalone/populate_two_deye_history.sql'
Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $SqlFile
$Sql=[System.IO.File]::ReadAllText($SqlFile)
if(-not $Sql.Contains('HISTORY_PREINSERT_GUARD=PASS') -or
   -not $Sql.Contains('HISTORY_POSTINSERT_GUARD=PASS') -or
   -not $Sql.Contains('rakurs_preview_history') -or
   -not $Sql.Contains('COMMIT;') -or
   $Sql.Contains('DROP TABLE') -or
   $Sql.Contains('TRUNCATE TABLE') -or
   $Sql.Contains('DELETE FROM') -or
   $Sql.Contains('FOREIGN_KEY_CHECKS=0')){
  throw 'Unrecognized SQL migration; database unchanged'
}
Write-Host ('MIGRATION_SQL_SHA256='+
  (Get-FileHash -LiteralPath $SqlFile -Algorithm SHA256).Hash)
Write-Host '=== APPLY TRANSACTIONAL 7-DAY HISTORY ==='
# SOURCE is processed by the MySQL command-line client; pass a short command
# rather than placing thousands of SQL characters into PowerShell arguments.
$SourcePath=$SqlFile.Replace('\','/')
& $MySql @Base ("--execute=source "+$SourcePath)
if($LASTEXITCODE -ne 0){
  throw 'History migration failed: inspect SQL transaction before retry'
}

$Verify=@'
SELECT id,device_key,kind FROM rakurs_devices ORDER BY id;
SELECT COUNT(*) AS devices FROM rakurs_devices;
SELECT device_id,COUNT(*) AS ui_rows,
MIN(observed_at_utc) AS first_utc,MAX(observed_at_utc) AS last_utc
FROM rakurs_samples GROUP BY device_id ORDER BY device_id;
SELECT device_id,COUNT(*) AS modbus_field_rows FROM
rakurs_modbus_field_samples GROUP BY device_id ORDER BY device_id;
SELECT enabled,interval_seconds FROM rakurs_poll_settings WHERE id=1;
'@
Write-Host '=== VERIFY TWO SEPARATE HISTORIES ==='
& $MySql @Base "--execute=$Verify"
if($LASTEXITCODE -ne 0){throw 'Post-migration verification failed'}
Write-Host 'TWO_DEYE_HISTORICAL_PREVIEW=PASS'
Write-Host 'HARDWARE_MODBUS_READS=0 AUTOMATIC_POLLING=DISABLED'
