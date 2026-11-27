#!/usr/bin/env bash
# Audit and guarded retirement of ONLY the previous Rakurs Home Assistant.
# Never remove shared Docker engine, Nginx, Certbot, Odoo or Ergodata.
set -Eeuo pipefail
umask 077
MODE="${1:---audit}"
[[ "$MODE" == '--audit' || "$MODE" == '--remove-ha' ]] || {
  echo 'Usage: bash retire_homeassistant.sh [--audit|--remove-ha]' >&2; exit 2
}
ROOT='/home/ivital/deye-monitoring-src'
DATA="$ROOT/deploy/data"
CONTAINER='deye-homeassistant'
echo '=== LEGACY RAKURS HOME ASSISTANT AUDIT ==='
[[ -d "$ROOT/.git" ]] || { echo 'STOP: unexpected project root'; exit 2; }
[[ ! -L "$DATA" ]] || { echo 'STOP: legacy data is a symlink'; exit 2; }
if [[ -d "$DATA" ]]; then
  [[ "$(realpath "$DATA")" == "$DATA" ]] || { echo 'STOP: data path mismatch'; exit 2; }
  du -sh "$DATA" 2>/dev/null || true
else
  echo "LEGACY_DATA=ABSENT"
fi

if sudo docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  PROJECT="$(sudo docker inspect --format \
    '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")"
  SERVICE="$(sudo docker inspect --format \
    '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER")"
  [[ "$PROJECT" == 'deye-monitoring' && "$SERVICE" == 'homeassistant' ]] || {
    echo "STOP: Docker container labels do not match expected Rakurs HA" >&2; exit 2
  }
  echo "LEGACY_CONTAINER=FOUND; COMPOSE_PROJECT=$PROJECT; SERVICE=$SERVICE"
  sudo docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' \
    "$CONTAINER"
else
  echo "LEGACY_CONTAINER=ABSENT"
fi
echo 'Rakurs Nginx HTTPS site:'
sudo grep -nE 'proxy_pass.*(8123|8765)' \
  /etc/nginx/sites-available/rakurs-https.conf 2>/dev/null || true
systemctl is-active rakurs-standalone.service 2>/dev/null || true

if [[ "$MODE" == '--audit' ]]; then
  echo 'AUDIT_ONLY: NO DELETIONS, NO CONTAINER CHANGES'
  exit 0
fi

# Hard gate: never delete the old backend while public Rakurs still uses it.
systemctl is-active --quiet rakurs-standalone.service || {
  echo 'STOP: standalone service is not active' >&2; exit 2
}
[[ "$(curl --noproxy '*' -sS --max-time 8 -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:8765/login)" == '200' ]] || {
  echo 'STOP: standalone login not responding' >&2; exit 2
}
EFFECTIVE_NGINX="$(sudo nginx -T 2>/dev/null)"
grep -Fq 'proxy_pass http://127.0.0.1:8765;' <<<"$EFFECTIVE_NGINX" || {
  echo 'STOP: new Rakurs HTTPS proxy not active' >&2; exit 2
}
if grep -Fq 'proxy_pass http://127.0.0.1:8123;' <<<"$EFFECTIVE_NGINX"; then
  echo 'STOP: active Nginx still references HA port 8123' >&2; exit 2
fi
CODE="$(curl --noproxy '*' -sS --max-time 12 --resolve \
  rakurs.zakryttya.org.ua:443:127.0.0.1 \
  -o /dev/null -w '%{http_code}' \
  https://rakurs.zakryttya.org.ua/login)"
[[ "$CODE" == '200' ]] || {
  echo "STOP: public domain HTTPS login unavailable (HTTP=$CODE)" >&2; exit 2
}
read -r -p 'Type REMOVE-LEGACY-HA to back up then delete the old HA only: ' \
  CONFIRM </dev/tty
[[ "$CONFIRM" == 'REMOVE-LEGACY-HA' ]] || { echo 'ABORTED'; exit 2; }

# Fail closed unless the verified old container is still present.
sudo docker container inspect "$CONTAINER" >/dev/null || {
  echo 'STOP: legacy container already absent; inspect remaining data separately' >&2
  exit 2
}
MOUNT="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
[[ "$MOUNT" == "$DATA" ]] || {
  echo 'STOP: legacy /config mount does not match expected directory' >&2; exit 2
}
# Verify no other Docker container mounts this data directory before deletion.
while IFS= read -r c; do
  [[ "$c" == "$CONTAINER" ]] && continue
  if sudo docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$c" |
    grep -Fxq "$DATA"; then
    echo "STOP: $DATA is used by other container $c" >&2
    exit 2
  fi
done < <(sudo docker ps -a --format '{{.Names}}')
IMAGE_ID="$(sudo docker inspect --format '{{.Image}}' "$CONTAINER")"
IMAGE_NAME="$(sudo docker inspect --format '{{.Config.Image}}' "$CONTAINER")"
case "$IMAGE_NAME" in
  ghcr.io/home-assistant/home-assistant:*) ;;
  *) echo "STOP: unexpected image for $CONTAINER ($IMAGE_NAME)" >&2; exit 2 ;;
esac
[[ -d "$DATA" ]] || {
  echo 'STOP: old HA data directory missing, no deletion performed' >&2; exit 2
}

# Private, root-owned backup directory: works even for root-owned HA files.
BACKUP_DIR="$(sudo mktemp -d /var/backups/rakurs-legacy-ha-XXXXXXXX)"
sudo chmod 0700 "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/ha-config.tar.gz"
HA_STOPPED=0
HA_REMOVED=0
rollback_on_error() {
  rc=$?
  trap - ERR
  if (( HA_STOPPED == 1 && HA_REMOVED == 0 )); then
    echo 'Retirement failed before removal: restarting legacy HA container.' >&2
    sudo docker start "$CONTAINER" || true
  else
    echo 'Retirement failed: inspect container and backup before further actions.' >&2
  fi
  echo "RECOVERY_BACKUP_DIR=$BACKUP_DIR" >&2
  exit "$rc"
}
trap rollback_on_error ERR

# Stop first for a consistent SQLite/WAL snapshot, then verify the archive
# before the irreversible docker rm or deletion of its configuration.
sudo docker stop --time 40 "$CONTAINER"
HA_STOPPED=1
sudo tar --one-file-system -czf "$BACKUP" -C "$ROOT/deploy" data
sudo tar -tzf "$BACKUP" >/dev/null
sudo sha256sum "$BACKUP" | sudo tee "$BACKUP_DIR/backup.sha256" >/dev/null
echo "LEGACY_BACKUP_VALIDATED=$BACKUP"

sudo docker rm "$CONTAINER"
HA_REMOVED=1
echo 'LEGACY_HOME_ASSISTANT_CONTAINER_REMOVED=PASS'
# Docker refuses removal when an image is still used elsewhere; never force.
if sudo docker image rm "$IMAGE_ID" >/dev/null 2>&1; then
  echo 'UNUSED_HOME_ASSISTANT_IMAGE_REMOVED=PASS'
else
  echo 'HOME_ASSISTANT_IMAGE_RETAINED=still_used_or_protected'
fi
sudo rm -rf -- "$DATA"
echo 'LEGACY_HOME_ASSISTANT_CONFIG_REMOVED=PASS'
trap - ERR
echo 'Docker engine, Nginx and its Rakurs HTTPS certificate retained.'
echo 'The old app backup is kept for rollback; remove it separately after verification.'
