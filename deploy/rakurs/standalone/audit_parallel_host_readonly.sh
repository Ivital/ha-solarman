#!/usr/bin/env bash
# Passive host inventory only; never open a serial interface or send network packets.
set -Eeuo pipefail
[[ "${1:---check}" == "--check" ]] || {
  echo 'Usage: audit_parallel_host_readonly.sh [--check]' >&2; exit 2
}
echo 'DEYE_MODEL=SUN-20K-SG05LP3-EU-SM2'
echo 'EXPECTED_PHYSICAL_INVERTERS=2'
echo 'SHARED_BATTERY=YES'
echo 'PHYSICAL_TOPOLOGY=NOT_VERIFIED'
echo 'TRB145_PORT_MAPPING=UNKNOWN'
echo 'MASTER_SLAVE_MAPPING=UNKNOWN'
echo '=== Hostname (not physical cable mapping) ==='
hostname -s
echo '=== Serial devices visible on this host; names only ==='
if [[ -d /dev/serial/by-id ]]; then
  find /dev/serial/by-id -mindepth 1 -maxdepth 1 -type l -printf '%f\n' | sort
else
  echo 'No /dev/serial/by-id directory (TCP gateway may not appear here)'
fi
echo '=== Existing system service state (no start, stop, or restart) ==='
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-active rakurs-standalone.service || true
else
  echo 'systemctl unavailable'
fi
echo '=== Local link interface names (not addresses or network scan) ==='
if command -v ip >/dev/null 2>&1; then
  ip -o link show | awk -F': ' '{print $2}' | sed 's/@.*//'
else
  echo 'ip tool unavailable'
fi
echo 'AUDIT_MODE=PASSIVE_ONLY'
echo 'NETWORK_SCAN=NO SERIAL_OPEN=NO MODBUS_COMMANDS=0 MYSQL_WRITES=0'
echo 'NEXT=VISUAL_INSPECTION_WITHOUT_OPENING_ENERGIZED_COMPARTMENTS'
