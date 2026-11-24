#!/usr/bin/env bash
# Activate only Rakurs HTTPS after verified certificate and HA administrator.
# Never touch other Nginx sites, HA data, Docker Compose, or the MikroTik.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

site=/etc/nginx/sites-available/rakurs-https.conf
link=/etc/nginx/sites-enabled/rakurs-https.conf

echo '=== GUARDS: CERTIFICATE, RUNNING HA, EXISTING HTTP VHOST ==='
sudo test -s /etc/letsencrypt/live/rakurs.zakryttya.org.ua/fullchain.pem
sudo test -s /etc/letsencrypt/live/rakurs.zakryttya.org.ua/privkey.pem
test "$(sudo docker inspect --format '{{.State.Status}}' deye-homeassistant)" = running
sudo test -L /etc/nginx/sites-enabled/rakurs-http.conf
sudo nginx -t

if sudo test -e "$site" || sudo test -L "$link"; then
  echo 'STOP: Rakurs HTTPS site exists; do not overwrite it' >&2
  exit 2
fi

echo '=== GUARD: OWNER ACCOUNT MUST ALREADY EXIST ==='
sudo docker exec deye-homeassistant python3 -c '
import json, sys
from pathlib import Path
p=Path("/config/.storage/auth")
try:
    users=json.loads(p.read_text(encoding="utf-8"))["data"]["users"]
except (OSError, ValueError, KeyError, TypeError):
    print("STOP: Cannot verify HA owner account; finish HA onboarding privately", file=sys.stderr)
    sys.exit(2)
owners=[x for x in users if x.get("is_owner") and x.get("is_active") and not x.get("is_system")]
if not owners:
    print("STOP: HA owner not found; create administrator over SSH tunnel first", file=sys.stderr)
    sys.exit(2)
print("HA owner exists: YES (no identities displayed)")
'

# Settings > System > Network: Trust X-Forwarded-For and actual Nginx
# source address (often Docker gateway 172.18.0.1) must be configured
# BEFORE publishing to avoid HTTP 400.
echo '=== INSTALL ONLY RAKURS HTTPS SITE ==='
sudo install -m 0644 deploy/rakurs/nginx-https.conf.template "$site"
sudo ln -s "$site" "$link"

if ! sudo nginx -t; then
  echo 'STOP: Nginx syntax failed; rolling back Rakurs HTTPS site' >&2
  sudo rm -f -- "$link" "$site"
  exit 1
fi

if ! sudo systemctl reload nginx; then
  echo 'STOP: reload failed; rolling back Rakurs HTTPS site' >&2
  sudo rm -f -- "$link" "$site"
  sudo nginx -t && sudo systemctl reload nginx
  exit 1
fi

echo '=== WAIT FOR NEW NGINX TLS WORKERS AND VERIFY BOTH HTTPS NAMES ==='
# Reload is graceful: the first connection may still reach a worker using
# the previous certificate. Do NOT disable TLS verification or use -k.
code=000
www_code=000
for attempt in $(seq 1 20); do
  code="$(curl --noproxy '*' --silent --max-time 5 \
    --resolve rakurs.zakryttya.org.ua:443:127.0.0.1 \
    -o /dev/null -w '%{http_code}' https://rakurs.zakryttya.org.ua/)" || code=000

  www_code="$(curl --noproxy '*' --silent --max-time 5 \
    --resolve www.rakurs.zakryttya.org.ua:443:127.0.0.1 \
    -o /dev/null -w '%{http_code}' https://www.rakurs.zakryttya.org.ua/)" || www_code=000

  echo "Attempt $attempt: canonical HTTP=$code, www HTTP=$www_code"
  if [[ "$code" =~ ^(200|301|302|303|307|308)$ && "$www_code" == 301 ]]; then
    break
  fi
  sleep 1
done

if [[ ! "$code" =~ ^(200|301|302|303|307|308)$ || "$www_code" != 301 ]]; then
  echo 'STOP: HTTPS failed certificate, proxy, or redirect check; rolling back ONLY Rakurs HTTPS' >&2
  echo 'Inspect SNI, address-specific listen directives, and Nginx reload logs using deploy/rakurs/diagnose_tls.sh' >&2
  sudo rm -f -- "$link" "$site"
  sudo nginx -t && sudo systemctl reload nginx
  exit 1
fi

echo '=== PRODUCTION SERVICES ==='
systemctl is-active nginx
systemctl is-active odoo18
echo 'HTTPS RAKURS LOCAL VALIDATION PASS; verify both public addresses externally'
