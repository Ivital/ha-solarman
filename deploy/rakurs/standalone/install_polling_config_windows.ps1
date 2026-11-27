#Requires -RunAsAdministrator
<#
RAKURS one-time additive installation of configuration tables for two Deye.
Run ONLY on Windows MySQL host 192.168.5.30. No telemetry deletion,
no gateway polling, no change to the two existing inverter records.
Default -- inspection only. -Apply requires a pinned Git commit ref.
Works with Windows PowerShell 4.0 and MySQL 8 clients.
#>
param([switch]$Apply,[string]$GitRef='')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Bin=Join-Path $env:ProgramFiles 'MySQL\MySQL Server 8.0\bin'
$MySql=Join-Path $Bin 'mysql.exe'
$Dump=Join-Path $Bin 'mysqldump.exe'
foreach($exe in @($MySql,$Dump)) {
  if(-not (Test-Path -LiteralPath $exe -PathType Leaf)) {throw "Missing MySQL executable: $exe"}
}
$Base=@('--protocol=TCP','--host=127.0.0.1','--port=3306',
 '--user=Ivital','--password','--database=rakurs','--default-character-set=utf8mb4')
$Check=@'
SELECT id,device_key,kind,model FROM rakurs_devices ORDER BY id;
SELECT COUNT(*) AS config_tables FROM information_schema.TABLES
WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN
('rakurs_poll_settings','rakurs_device_settings','rakurs_poll_runtime');
'@
Write-Host '=== READ-ONLY DEVICE AND SCHEMA CHECK ==='
& $MySql @Base "--execute=$Check"
if($LASTEXITCODE -ne 0){throw 'MySQL preflight failed'}
if(-not $Apply) {Write-Host 'CONFIG_SCHEMA_CHECK_ONLY=PASS';exit 0}
if($GitRef -notmatch '^[0-9a-f]{40}$') {throw 'Pinned full Git commit SHA required'}
$TLS='https://raw.githubusercontent.com/Ivital/ha-solarman/'+$GitRef+'/deploy/rakurs/standalone/polling_config_schema.sql'
$Secure=Join-Path $env:ProgramData 'RAKURS\backups'
New-Item -ItemType Directory -Path $Secure -Force | Out-Null
& icacls.exe $Secure /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Unable to secure backup directory'}
$Stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$Backup=Join-Path $Secure ('rakurs-before-config-'+$Stamp+'.sql')
$DumpArgs=@('--protocol=TCP','--host=127.0.0.1','--port=3306',
 '--user=Ivital','--password','--default-character-set=utf8mb4',
 '--single-transaction','--quick','--skip-lock-tables','--no-tablespaces',
 '--set-gtid-purged=OFF','--column-statistics=0','--hex-blob',
 '--routines','--events','--triggers','--comments',
 "--result-file=$Backup",'rakurs')
Write-Host '=== FULL DATABASE BACKUP ==='
& $Dump @DumpArgs
if($LASTEXITCODE -ne 0){throw 'Backup failed, MySQL unchanged'}
if((Get-Item -LiteralPath $Backup).Length -lt 1024){throw 'Incomplete SQL backup'}
$Stream=[System.IO.File]::OpenText($Backup)
$Tables=0
$Completed=$false
try {
  while(($Line=$Stream.ReadLine()) -ne $null){
    if($Line -match '^CREATE TABLE '){$Tables++}
    if($Line -like '*Dump completed on*'){$Completed=$true}
  }
} finally {$Stream.Dispose()}
if($Tables -lt 16 -or -not $Completed){throw 'Incomplete backup table count/footer'}
Write-Host ('BACKUP_FILE='+$Backup)
Write-Host ('BACKUP_SHA256='+(Get-FileHash -LiteralPath $Backup -Algorithm SHA256).Hash)
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$SqlFile=Join-Path $Secure ('polling-schema-'+$GitRef+'.sql')
Invoke-WebRequest -UseBasicParsing -Uri $TLS -OutFile $SqlFile
$Schema=[System.IO.File]::ReadAllText($SqlFile)
if(-not $Schema.Contains('CREATE TABLE IF NOT EXISTS rakurs_device_settings') -or
   -not $Schema.Contains('DEFAULT 0') -or $Schema.Contains('DROP TABLE')){
  throw 'Pinned schema did not pass static safety checks'
}
Write-Host '=== INSTALL THREE ADDITIVE CONFIG TABLES ==='
& $MySql @Base "--execute=$Schema"
if($LASTEXITCODE -ne 0){throw 'Schema installation failed; inspect before retry'}
# Only configuration-plane DML. No privileges to modify measured values.
$Grants=@'
GRANT SELECT,INSERT,UPDATE ON rakurs.rakurs_device_settings TO 'rakurs_web'@'192.168.5.31';
GRANT SELECT,UPDATE ON rakurs.rakurs_poll_settings TO 'rakurs_web'@'192.168.5.31';
GRANT SELECT ON rakurs.rakurs_poll_runtime TO 'rakurs_web'@'192.168.5.31';
GRANT INSERT (device_key,kind,model,serial_number,created_at_utc)
 ON rakurs.rakurs_devices TO 'rakurs_web'@'192.168.5.31';
GRANT UPDATE (model,serial_number) ON rakurs.rakurs_devices
 TO 'rakurs_web'@'192.168.5.31';
GRANT INSERT ON rakurs.rakurs_parallel_members TO 'rakurs_web'@'192.168.5.31';
'@
Write-Host '=== GRANT ONLY ADMIN CONFIGURATION TABLE RIGHTS ==='
& $MySql @Base "--execute=$Grants"
if($LASTEXITCODE -ne 0){throw 'Schema installed, but config rights not ready'}
$Post=@'
SELECT TABLE_NAME FROM information_schema.TABLES
WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN
('rakurs_poll_settings','rakurs_device_settings','rakurs_poll_runtime')
ORDER BY TABLE_NAME;
SELECT id,enabled,interval_seconds FROM rakurs_poll_settings;
SELECT device_id,display_name,enabled,verified_connection
 FROM rakurs_device_settings ORDER BY device_id;
SELECT COUNT(*) AS devices FROM rakurs_devices;
'@
& $MySql @Base "--execute=$Post"
if($LASTEXITCODE -ne 0){throw 'Post-migration read failed'}
Write-Host 'RAKURS_CONFIG_SCHEMA=PASS'
Write-Host 'POLLING_DEFAULT_DISABLED=YES'
Write-Host 'TELEMETRY_ROWS_PRESERVED=YES'
