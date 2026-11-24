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
[[ "$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:8765/login)" == '200' ]] || {
  echo 'STOP: standalone login not responding' >&2; exit 2
}
sudo nginx -T 2>/dev/null | grep -q 'proxy_pass http://127.0.0.1:8765;' || {
  echo 'STOP: new Rakurs HTTPS proxy not active' >&2; exit 2
}
if sudo nginx -T 2>/dev/null | grep -q 'proxy_pass http://127.0.0.1:8123;'; then
  echo 'STOP: active Nginx still references HA port 8123' >&2; exit 2
fi
CODE="$(curl -sS --max-time 12 --resolve \
  rakurs.zakryttya.org.ua:443:127.0.0.1 \
  -o /dev/null -w '%{http_code}' \
  https://rakurs.zakryttya.org.ua/login)"
[[ "$CODE" == '200' ]] || {
  echo "STOP: public domain HTTPS login unavailable (HTTP=$CODE)" >&2; exit 2
}
read -r -p 'Type REMOVE-LEGACY-HA to back up then delete the old HA only: ' \
  CONFIRM </dev/tty
[[ "$CONFIRM" == 'REMOVE-LEGACY-HA' ]] || { echo 'ABORTED'; exit 2; }

if sudo docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  # Verify no other Docker container binds the same HA configuration directory.
  while IFS= read -r c; do
    [[ "$c" == "$CONTAINER" ]] && continue
    if sudo docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$c" |
      grep -Fxq "$DATA"; then
      echo "STOP: $DATA is used by other container $c" >&2; exit 2
    fi
  done < <(sudo docker ps -a --format '{{.Names}}')
  IMAGE_ID="$(sudo docker inspect --format '{{.Image}}' "$CONTAINER")"
  IMAGE_NAME="$(sudo docker inspect --format '{{.Config.Image}}' "$CONTAINER")"
  case "$IMAGE_NAME" in
    ghcr.io/home-assistant/home-assistant:*) ;;
    *) echo "STOP: unexpected image for $CONTAINER ($IMAGE_NAME)" >&2; exit 2 ;;
  esac
  sudo docker stop "$CONTAINER"
fi

BACKUP="$HOME/rakurs-legacy-ha-backup-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
if [[ -d "$DATA" ]]; then
  tar --create --gzip --file="$BACKUP" -C "$ROOT/deploy" data
  chmod 600 "$BACKUP"
  tar -tzf "$BACKUP" >/dev/null
  echo "LEGACY_BACKUP_VALIDATED=$BACKUP"
fi
if sudo docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  sudo docker rm "$CONTAINER"
  echo 'LEGACY_HOME_ASSISTANT_CONTAINER_REMOVED=PASS'
  # Never force image removal: Docker refuses if other containers use it.
  if [[ -n "${IMAGE_ID:-}" ]]; then
    if sudo docker image rm "$IMAGE_ID" >/dev/null 2>&1; then
      echo 'UNUSED_HOME_ASSISTANT_IMAGE_REMOVED=PASS'
    else
      echo 'HOME_ASSISTANT_IMAGE_RETAINED=still_used_or_protected'
    fi
  fi
fi
if [[ -d "$DATA" ]]; then
  # The directory has been confirmed to be the exact isolated bind source.
  sudo rm -rf -- "$DATA"
  echo 'LEGACY_HOME_ASSISTANT_CONFIG_REMOVED=PASS'
fi
echo 'Docker engine, Nginx and its Rakurs HTTPS certificate retained.'
echo 'The old app backup is kept for rollback; remove it separately after verification.'
