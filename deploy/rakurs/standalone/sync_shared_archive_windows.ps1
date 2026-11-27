#Requires -RunAsAdministrator
param([switch]$Apply)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$SqlFile=Join-Path $PSScriptRoot 'sync_shared_archive.sql'
$Bin=Join-Path $env:ProgramFiles 'MySQL\MySQL Server 8.0\bin'
$MySql=Join-Path $Bin 'mysql.exe'
$Dump=Join-Path $Bin 'mysqldump.exe'
foreach($File in @($SqlFile,$MySql,$Dump)){if(-not(Test-Path -LiteralPath $File -PathType Leaf)){throw "Missing $File"}}
$Base=@('--protocol=TCP','--host=127.0.0.1','--port=3306','--user=Ivital','--password','--default-character-set=utf8mb4')
$Audit=@'
SELECT pm.slot_no,d.device_key,pm.member_kind,pm.member_verified,pm.transport_verified
FROM rakurs_parallel_members pm JOIN rakurs_devices d ON d.id=pm.device_id
JOIN rakurs_parallel_groups pg ON pg.id=pm.group_id
WHERE pg.group_key='deye_sun20k_parallel_shared_battery';
SELECT s.signal_key,COUNT(*) AS matched_numeric_pairs,
SUM(a.value_decimal<>b.value_decimal) AS differing_pairs
FROM rakurs_samples a JOIN rakurs_samples b ON b.device_id=(SELECT id FROM rakurs_devices WHERE device_key='deye_inverter_2')
AND a.signal_id=b.signal_id AND a.observed_at_utc=b.observed_at_utc
JOIN rakurs_signals s ON s.id=a.signal_id
WHERE a.device_id=(SELECT id FROM rakurs_devices WHERE device_key='deye_inverter_1')
AND s.signal_key IN ('load_power','battery_soc')
AND a.value_decimal IS NOT NULL AND b.value_decimal IS NOT NULL GROUP BY s.signal_key;
'@
& $MySql @Base '--database=rakurs' "--execute=$Audit"
if($LASTEXITCODE -ne 0){throw 'Inventory failed'}
if(-not $Apply){Write-Host 'SHARED_ARCHIVE_CHECK_ONLY=PASS';exit 0}
$Dir=Join-Path $env:ProgramData 'RAKURS\backups'
New-Item -ItemType Directory -Path $Dir -Force | Out-Null
& icacls.exe $Dir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Cannot secure backup directory'}
$Backup=Join-Path $Dir ('rakurs-before-shared-sync-'+(Get-Date -Format 'yyyyMMdd-HHmmssfff')+'.sql')
& $Dump @Base '--single-transaction' '--quick' '--skip-lock-tables' '--no-tablespaces' '--set-gtid-purged=OFF' '--column-statistics=0' '--hex-blob' '--routines' '--events' '--triggers' '--comments' "--result-file=$Backup" 'rakurs'
if($LASTEXITCODE -ne 0 -or -not(Test-Path -LiteralPath $Backup)){throw 'Backup failed'}
$Stream=[System.IO.File]::OpenText($Backup)
$Tables=0;$Completed=$false
try{while(($Line=$Stream.ReadLine()) -ne $null){if($Line -match '^CREATE TABLE '){$Tables++};if($Line -like '*Dump completed on*'){$Completed=$true}}}finally{$Stream.Dispose()}
if($Tables -lt 16 -or -not $Completed){throw 'Backup completeness failed'}
Write-Host ('BACKUP_FILE='+$Backup)
Write-Host ('BACKUP_SHA256='+(Get-FileHash -LiteralPath $Backup -Algorithm SHA256).Hash)
$Sql=[System.IO.File]::ReadAllText($SqlFile)
if(-not $Sql.Contains('SHARED_ARCHIVE_SYNC=PASS') -or -not $Sql.Contains('COMMIT;')){throw 'Invalid migration'}
# --execute uses batch mode and exits on SQL error. Do not use --force.
& $MySql @Base '--database=rakurs' '--batch' "--execute=$Sql"
if($LASTEXITCODE -ne 0){throw 'Shared archive transaction failed; inspect report'}
& $MySql @Base '--database=rakurs' "--execute=$Audit"
if($LASTEXITCODE -ne 0){throw 'Post-transaction audit failed'}
Write-Host 'RAKURS_SHARED_SYNC_COMPLETE=PASS'
