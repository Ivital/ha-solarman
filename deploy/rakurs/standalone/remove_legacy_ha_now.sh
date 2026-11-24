#!/usr/bin/env bash
# Retire ONLY the previously deployed Rakurs Home Assistant, immediately.
# First put Rakurs HTTPS in maintenance mode to prevent a broken 502 proxy.
# NEVER remove Docker engine, Odoo, Ergodata, ITM, MySQL, or unrelated files.
set -Eeuo pipefail
umask 077

MODE="${1:---audit}"
[[ "$MODE" == "--audit" || "$MODE" == "--remove-now" ]] || {
  echo 'Usage: bash remove_legacy_ha_now.sh [--audit|--remove-now]' >&2
  exit 2
}

ROOT='/home/ivital/deye-monitoring-src'
DATA="$ROOT/deploy/data"
CONTAINER='deye-homeassistant'
VHOST='/etc/nginx/sites-available/rakurs-https.conf'
ENABLED='/etc/nginx/sites-enabled/rakurs-https.conf'
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAINTENANCE="$HERE/nginx-maintenance.conf"
DOMAIN='rakurs.zakryttya.org.ua'

test -d "$ROOT/.git" || { echo 'STOP: unexpected source directory'; exit 2; }
test -f "$MAINTENANCE" || { echo 'STOP: maintenance template missing'; exit 2; }
test -d "$DATA" || { echo 'STOP: HA config mount absent; inspect manually'; exit 2; }
[[ ! -L "$DATA" && "$(realpath "$DATA")" == "$DATA" ]] || {
  echo 'STOP: HA config directory is not a canonical real directory' >&2
  exit 2
}
command -v docker >/dev/null && command -v nginx >/dev/null || {
  echo 'STOP: expected Docker/Nginx unavailable'; exit 2
}
sudo -v
sudo docker container inspect "$CONTAINER" >/dev/null || {
  echo 'STOP: named HA container is missing; no changes made' >&2
  exit 2
}
PROJECT="$(sudo docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")"
SERVICE="$(sudo docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER")"
IMAGE="$(sudo docker inspect --format '{{.Config.Image}}' "$CONTAINER")"
IMAGE_ID="$(sudo docker inspect --format '{{.Image}}' "$CONTAINER")"
CONFIG_MOUNT="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
[[ "$PROJECT" == 'deye-monitoring' && "$SERVICE" == 'homeassistant' ]] || {
  echo 'STOP: Compose labels not recognized; no changes made' >&2; exit 2
}
case "$IMAGE" in
  ghcr.io/home-assistant/home-assistant:*) ;;
  *) echo "STOP: unexpected Docker image: $IMAGE" >&2; exit 2 ;;
esac
[[ "$CONFIG_MOUNT" == "$DATA" ]] || {
  echo "STOP: HA bind source differs from $DATA: $CONFIG_MOUNT" >&2; exit 2
}
[[ -f "$VHOST" && ! -L "$VHOST" && -L "$ENABLED" ]] || {
  echo 'STOP: expected Rakurs-only HTTPS vhost is not installed as expected' >&2; exit 2
}
[[ "$(readlink -f "$ENABLED")" == "$VHOST" ]] || {
  echo 'STOP: enabled Rakurs HTTPS site points elsewhere' >&2; exit 2
}
sudo grep -Fq 'server_name rakurs.zakryttya.org.ua;' "$VHOST" || {
  echo 'STOP: Rakurs server name missing from vhost' >&2; exit 2
}
sudo grep -Fq 'proxy_pass http://127.0.0.1:8123;' "$VHOST" || {
  echo 'STOP: vhost no longer points to HA; do not overwrite it' >&2; exit 2
}
sudo nginx -t >/dev/null

# Fail closed if ANY other container uses this config tree or its ancestor.
while IFS= read -r other; do
  [[ "$other" == "$CONTAINER" ]] && continue
  while IFS= read -r mount; do
    [[ -n "$mount" ]] || continue
    case "$mount" in
      "$DATA"|"$DATA/"*|"$ROOT"|"$ROOT/"|"$ROOT/deploy")
        echo "STOP: config tree is also used by container: $other" >&2; exit 2 ;;
    esac
  done < <(sudo docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$other")
done < <(sudo docker ps -a --format '{{.Names}}')

echo '=== LEGACY HA REMOVAL PRECHECK ==='
echo "CONTAINER=$CONTAINER"
echo "COMPOSE_PROJECT=$PROJECT SERVICE=$SERVICE"
echo "CONFIG_MOUNT=$DATA"
echo "IMAGE=$IMAGE"
echo "HTTPS_VHOST=$VHOST"
echo 'PRECHECK=PASS; other services not targeted'
if [[ "$MODE" == '--audit' ]]; then
  echo 'AUDIT_ONLY=PASS; NO FILES OR SERVICES CHANGED'
  exit 0
fi

read -r -p 'Type exactly REMOVE-OLD-HA to show maintenance and retire HA: ' \
  CONFIRM < /dev/tty
[[ "$CONFIRM" == 'REMOVE-OLD-HA' ]] || {
  echo 'ABORTED; no changes made'; exit 2
}

# Owned backup in /var/backups, preserving previous public Nginx site.
BACKUP_DIR="$(sudo mktemp -d /var/backups/rakurs-ha-retire-XXXXXXXX)"
sudo chmod 0700 "$BACKUP_DIR"
sudo cp -p "$VHOST" "$BACKUP_DIR/rakurs-https.conf"
sudo docker inspect "$CONTAINER" | sudo tee "$BACKUP_DIR/container-inspect.json" >/dev/null
SWITCHED=0
REMOVED=0
HA_STOPPED=0
STEP="prepare rollback"
on_error() {
  rc=$?
  trap - ERR
  echo "FAILED_STEP=$STEP EXIT_CODE=$rc" >&2
  if (( REMOVED == 0 )); then
    echo 'Failure before deletion: restoring prior HA site and container.' >&2
    if (( HA_STOPPED == 1 )); then sudo docker start "$CONTAINER" || true; fi
    if (( SWITCHED == 1 )); then
      sudo cp -p "$BACKUP_DIR/rakurs-https.conf" "$VHOST"
      sudo nginx -t && sudo systemctl reload nginx || true
    fi
  else
    echo 'Container already removed. Maintenance stays until standalone is ready.' >&2
  fi
  echo "Saved backup directory: $BACKUP_DIR" >&2
  exit "$rc"
}
trap on_error ERR

# Switch only the Rakurs HTTPS site, leave certificate and other sites alone.
STEP='install maintenance config and reload Nginx'
SWITCHED=1
sudo install -m 0644 "$MAINTENANCE" "$VHOST"
sudo nginx -t
sudo systemctl reload nginx
STEP='verify local HTTPS maintenance response, bypass any shell HTTPS proxy'
BODY="$(mktemp)"
HEADERS="$(mktemp)"
trap 'rm -f -- "${BODY:-}" "${HEADERS:-}"' EXIT
HTTP="$(curl --noproxy '*' --silent --show-error --max-time 12 \
  --connect-timeout 4 --resolve "$DOMAIN:443:127.0.0.1" \
  --dump-header "$HEADERS" --output "$BODY" \
  --write-out '%{http_code}' "https://$DOMAIN/")"
if [[ "$HTTP" != '503' ]] || ! grep -Fq 'RAKURS_MAINTENANCE_UPGRADING' "$BODY"; then
  echo "STOP: localhost HTTPS maintenance not confirmed (HTTP=$HTTP)" >&2
  echo 'Response status / content-type (no response body disclosed):' >&2
  grep -iE '^(HTTP/|content-type:|x-rakurs-maintenance:)' "$HEADERS" >&2 || true
  echo 'Nginx site will be rolled back; HA remains untouched.' >&2
  false
fi
rm -f -- "$BODY" "$HEADERS"
unset BODY HEADERS
trap - EXIT
echo 'RAKURS_MAINTENANCE_HTTPS=PASS'

# Stop for a consistent copy of the prior HA SQLite/WAL/config.
STEP="stop old HA container"
sudo docker stop --time 40 "$CONTAINER"
HA_STOPPED=1
STEP="create and verify HA config backup"
sudo tar --one-file-system -C "$ROOT/deploy" \
  -czf "$BACKUP_DIR/ha-config.tar.gz" data
sudo tar -tzf "$BACKUP_DIR/ha-config.tar.gz" >/dev/null
sudo sha256sum "$BACKUP_DIR/ha-config.tar.gz" | sudo tee "$BACKUP_DIR/backup.sha256" >/dev/null
echo "HA_CONFIG_BACKUP_VERIFIED=$BACKUP_DIR/ha-config.tar.gz"

STEP="remove verified old HA container"
sudo docker rm "$CONTAINER"
REMOVED=1
STEP="remove isolated old HA config after verified backup"
sudo rm -rf -- "$DATA"
echo 'HOME_ASSISTANT_CONTAINER_AND_CONFIG_REMOVED=PASS'

# Remove old HA image only when Docker says unused: never force.
if sudo docker image rm "$IMAGE_ID" >/dev/null 2>&1; then
  echo 'HOME_ASSISTANT_IMAGE_REMOVED=PASS'
else
  echo 'HOME_ASSISTANT_IMAGE_LEFT_IN_PLACE=still_needed_or_protected'
fi

# The dedicated compose network may be removed ONLY if empty.
NETWORK='deye-monitoring_monitoring'
if sudo docker network inspect "$NETWORK" >/dev/null 2>&1; then
  NETWORK_PROJECT="$(sudo docker network inspect --format '{{index .Labels "com.docker.compose.project"}}' "$NETWORK")"
  NETWORK_MEMBERS="$(sudo docker network inspect --format '{{len .Containers}}' "$NETWORK")"
  if [[ "$NETWORK_PROJECT" == 'deye-monitoring' && "$NETWORK_MEMBERS" == 0 ]]; then
    sudo docker network rm "$NETWORK" >/dev/null || true
    echo 'EMPTY_OLD_HA_NETWORK_REMOVAL_ATTEMPTED=PASS'
  fi
fi
trap - ERR
echo 'REMOVAL=COMPLETE; RAKURS HTTPS IS TEMPORARILY IN MAINTENANCE MODE'
echo "RECOVERY_BACKUP=$BACKUP_DIR"
echo 'No Docker engine, Nginx service, MySQL, Odoo, Ergodata, or ITM deleted.'
