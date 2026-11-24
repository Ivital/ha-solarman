#!/usr/bin/env bash
# Rakurs standalone migration with individually gated phases.
# NEVER delete or disable old HA until new HTTPS Rakurs login is verified.
set -Eeuo pipefail
umask 077
MODE="${1:---status}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$MODE" in
  --status)
    bash "$DIR/stage_standalone.sh" --check
    bash "$DIR/apply_auth_schema.sh" --check
    bash "$DIR/retire_homeassistant.sh" --audit
    echo 'STATUS_ONLY=PASS; NO CHANGES'
    ;;
  --prepare)
    echo '=== LOCAL SOURCE AND AUTH SCHEMA CHECKS ==='
    bash "$DIR/stage_standalone.sh" --check
    bash "$DIR/apply_auth_schema.sh" --check
    echo '=== ADD TWO MYSQL AUTH TABLES; RETAIN 5 MEASUREMENT TABLES ==='
    bash "$DIR/apply_auth_schema.sh" --apply
    echo '=== STAGE NEW ISOLATED SYSTEMD APP; HA UNCHANGED ==='
    bash "$DIR/stage_standalone.sh" --install
    echo '=== CREATE LEAST-PRIVILEGE WEB DB ACCOUNT ==='
    sudo /var/lib/rakurs/.venv/bin/python \
      "$DIR/provision_mysql_web_user.py"
    echo '=== CREATE FIRST RAKURS WEB ADMIN ==='
    sudo /var/lib/rakurs/.venv/bin/python \
      "$DIR/bootstrap_admin.py"
    echo '=== ENABLE STANDALONE ON LOCALHOST:8765 ==='
    sudo systemctl enable --now rakurs-standalone.service
    READY_CODE="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
      http://127.0.0.1:8765/login)"
    [[ "$READY_CODE" == 200 ]] || {
      echo "STOP: new login not healthy (HTTP=$READY_CODE), old site remains intact" >&2
      exit 2
    }
    UNAUTH_CODE="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
      http://127.0.0.1:8765/api/v1/status)"
    [[ "$UNAUTH_CODE" == 401 ]] || {
      echo "STOP: anonymous API is not protected (HTTP=$UNAUTH_CODE)" >&2
      exit 2
    }
    echo 'RAKURS_STANDALONE_LOGIN_READY=PASS; UNAUTHORIZED_API=401'
    echo 'Current public HTTPS site and HA have NOT been changed.'
    echo 'Run --publish only after checking the new localhost login.'
    ;;
  --publish)
    echo '=== VERIFY ISOLATED NEW APP BEFORE HTTPS SWITCH ==='
    bash "$DIR/switch_rakurs_nginx.sh" --check
    bash "$DIR/switch_rakurs_nginx.sh" --apply
    echo 'NEW_HTTPS_RAKURS=LIVE; OLD_HA_STILL_AVAILABLE_FOR_ROLLBACK'
    echo 'Log in through the real HTTPS site and verify pages before --retire.'
    ;;
  --retire)
    echo '=== INSPECT LEGACY HA AND KEEP OTHER SERVICES SAFE ==='
    bash "$DIR/retire_homeassistant.sh" --audit
    bash "$DIR/retire_homeassistant.sh" --remove-ha
    echo 'LEGACY_HA_RETIREMENT_COMPLETED; KEEP_BACKUP_FOR_ROLLBACK'
    ;;
  *)
    echo 'Usage: migrate_rakurs.sh [--status|--prepare|--publish|--retire]' >&2
    exit 2
    ;;
esac
