#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
[[ "$MODE" == --check || "$MODE" == --apply ]]
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for path in rakurs-alert-monitor.service rakurs-alert-monitor.timer alert_monitor.py; do test -s "$HERE/$path"; done
python3 -m py_compile "$HERE/alert_monitor.py"
echo ALERT_MONITOR_PREFLIGHT=PASS
[[ "$MODE" == --apply ]] || exit 0
sudo -v
systemctl is-active --quiet rakurs-standalone.service
sudo cmp "$HERE/alert_monitor.py" /opt/rakurs/standalone/alert_monitor.py
# First run only: refuse to overwrite an unexpected customized systemd unit.
for unit in rakurs-alert-monitor.service rakurs-alert-monitor.timer; do
  target="/etc/systemd/system/$unit"
  if sudo test -e "$target"; then
    sudo cmp "$HERE/$unit" "$target" || { echo "STOP: customized $unit"; exit 2; }
  else
    sudo install -o root -g root -m 0644 "$HERE/$unit" "$target"
  fi
done
sudo systemctl daemon-reload
sudo systemctl start rakurs-alert-monitor.service
sudo test -s /var/lib/rakurs-alerts/state.json
sudo systemctl enable --now rakurs-alert-monitor.timer
systemctl is-active --quiet rakurs-alert-monitor.timer
echo ALERT_MONITOR_TIMER=PASS
echo MYSQL_WRITES=0 MODBUS_COMMANDS=0 EXTERNAL_NOTIFICATIONS=0
