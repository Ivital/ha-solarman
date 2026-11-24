#!/usr/bin/env bash
# Read-only comparison of Rakurs assets by local, LAN, and public HTTPS routes.
# Does not read credentials, alter MySQL, restart services or edit Nginx.
set -Eeuo pipefail
umask 077
DOMAIN=rakurs.zakryttya.org.ua
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
JS="$HERE/static/app.js"
TARGET=/opt/rakurs/standalone
VERSION=rakurs-history-72h-v1
echo '=== RAKURS BROWSER DELIVERY AUDIT ==='
echo "EXPECTED_VERSION=$VERSION"
echo "GITHUB_WORKTREE_SHA256=$(sha256sum "$JS" | cut -d ' ' -f 1)"
echo "CURRENT_COMMIT=$(git -C "$HERE" rev-parse --short HEAD)"
echo '=== INSTALLED FILES ==='
for file in static/app.js templates/index.html static/style.css; do
  if sudo test -s "$TARGET/$file"; then
    checksum="$(sudo sha256sum "$TARGET/$file" | cut -d ' ' -f 1)"
    echo "INSTALLED_FILE=$file SHA256=$checksum"
  else
    echo "INSTALLED_FILE=$file MISSING"
  fi
done
echo '=== DNS LOOKUPS ==='
getent ahostsv4 "$DOMAIN" | awk '$2=="STREAM" {print "DNS_A="$1}' | sort -u || true
getent ahostsv6 "$DOMAIN" | awk '$2=="STREAM" {print "DNS_AAAA="$1}' | sort -u || true

probe() {
  label="$1"
  shift
  tmp="$(mktemp)"
  meta="$(mktemp)"
  if curl --noproxy '*' --silent --show-error --connect-timeout 4 --max-time 15 \
    -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' \
    -D "$meta" -o "$tmp" \
    -w 'HTTP=%{http_code} REMOTE_IP=%{remote_ip}' \
    "$@" "https://$DOMAIN/static/app.js?v=$VERSION&probe=20261124-routing" > "$meta.txt"; then
    echo "PROBE=$label $(cat "$meta.txt")"
    echo "PROBE=$label SHA256=$(sha256sum "$tmp" | cut -d ' ' -f 1)"
    if cmp -s "$JS" "$tmp"; then
      echo "PROBE=$label MATCH_GITHUB=YES"
    else
      echo "PROBE=$label MATCH_GITHUB=NO"
    fi
    if grep -Fq 'Динаміка за 24 години' "$tmp"; then
      echo "PROBE=$label HAS_CHARTS=YES"
    else
      echo "PROBE=$label HAS_CHARTS=NO"
    fi
    if grep -Fq '["modbus", "Modbus", "▤"]' "$tmp"; then
      echo "PROBE=$label HAS_MODBUS_MENU=YES"
    else
      echo "PROBE=$label HAS_MODBUS_MENU=NO"
    fi
  else
    echo "PROBE=$label UNREACHABLE"
  fi
  rm -f "$tmp" "$meta" "$meta.txt"
}
echo '=== HTTPS VIA LOCAL NGINX ==='
probe LOOPBACK --resolve "$DOMAIN:443:127.0.0.1"
echo '=== HTTPS VIA LAN INTERFACE ==='
probe LAN --resolve "$DOMAIN:443:192.168.5.31"
echo '=== HTTPS VIA ACTUAL DNS ==='
probe DNS
echo '=== PUBLIC ROUTES ==='
for route in / /login /deye /service_worker.js /manifest.json; do
  status="$(curl --noproxy '*' --silent --connect-timeout 4 --max-time 10 \
     -o /dev/null -w '%{http_code} %{remote_ip}' \
     "https://$DOMAIN$route" || true)"
  echo "ROUTE=$route RESULT=$status"
done
echo 'AUDIT=COMPLETE MYSQL_CHANGED=NO SERVICE_RESTARTED=NO'
