#!/usr/bin/env bash
# Only switch existing Rakurs HTTPS vhost once standalone auth works.
# All other Nginx/Odoo/Ergodata sites and TLS certs remain unchanged.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo 'Usage: bash switch_rakurs_nginx.sh [--check|--apply]' >&2
  exit 2
}
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$DIR/nginx-standalone.conf"
DEST='/etc/nginx/sites-available/rakurs-https.conf'
SITE='rakurs.zakryttya.org.ua'

if [[ ! -f "$TEMPLATE" ]]; then echo 'STOP: template absent' >&2; exit 2; fi
if ! systemctl is-active --quiet rakurs-standalone.service; then
  echo 'STOP: Rakurs standalone service is not active' >&2; exit 2
fi
STATUS="$(curl --silent --show-error --max-time 8 --output /dev/null \
  --write-out '%{http_code}' http://127.0.0.1:8765/login)"
[[ "$STATUS" == '200' ]] || {
  echo "STOP: standalone /login HTTP=$STATUS (expected 200)" >&2; exit 2
}
if [[ "$MODE" == '--check' ]]; then
  echo "STANDALONE_LOGIN_HTTP=$STATUS; NGINX_CHANGE=NONE"
  echo "Next: manual review current vhost $DEST before --apply"
  exit 0
fi

sudo -v
[[ -f "$DEST" && ! -L "$DEST" ]] || {
  echo "STOP: expected existing Rakurs site not regular file: $DEST" >&2; exit 2
}
sudo grep -q 'server_name rakurs.zakryttya.org.ua' "$DEST" || {
  echo 'STOP: current file is not Rakurs HTTPS vhost' >&2; exit 2
}
if ! sudo grep -Fq 'proxy_pass http://127.0.0.1:8123;' "$DEST"; then
  sudo grep -Fq 'RAKURS_MAINTENANCE_UPGRADING' "$DEST" || {
    echo 'STOP: vhost is neither old HA nor known Rakurs maintenance' >&2; exit 2
  }
fi
read -r -p "Type SWITCH-RAKURS to replace ONLY Rakurs HTTPS proxy: " CONFIRM </dev/tty
[[ "$CONFIRM" == 'SWITCH-RAKURS' ]] || { echo 'ABORTED'; exit 2; }

BACKUP_DIR="/var/backups/rakurs-nginx-switch-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -m 0700 "$BACKUP_DIR"
sudo cp -p "$DEST" "$BACKUP_DIR/rakurs-https.conf"
rollback() {
  echo 'SWITCH FAILED: restoring prior Rakurs HTTPS site' >&2
  sudo cp -p "$BACKUP_DIR/rakurs-https.conf" "$DEST"
  sudo nginx -t && sudo systemctl reload nginx || true
}
trap rollback ERR
sudo install -m 0644 "$TEMPLATE" "$DEST"
sudo nginx -t
sudo systemctl reload nginx
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
CODE="$(curl --silent --show-error --max-time 12 --resolve "$SITE:443:127.0.0.1" \
  --output "$TMP" --write-out '%{http_code}' "https://$SITE/login")"
[[ "$CODE" == 200 ]] && grep -Fq 'Вхід — RAKURS' "$TMP" || {
  echo "STOP: HTTPS login check failed (HTTP=$CODE)" >&2; false
}
trap - ERR
echo "RAKURS_HTTPS_STANDALONE=PASS; PRIOR_PROXY_BACKUP=$BACKUP_DIR"
echo 'Do not delete the old HA container until independent login and API tests pass.'
