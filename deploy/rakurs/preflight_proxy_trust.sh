#!/usr/bin/env bash
# Safe, read-only preflight: test Home Assistant reverse-proxy trust before Nginx publication.
# This makes one synthetic HTTP request with a documentation-only X-Forwarded-For
# IP, solely to the host-local HA port; it neither accesses the inverter nor
# creates/changing any Home Assistant settings.
set -Eeuo pipefail

state="$(sudo docker inspect --format '{{.State.Status}}' deye-homeassistant)"
if [[ "$state" != running ]]; then
    echo "STOP: Home Assistant container not running ($state)" >&2
    exit 2
fi

echo "=== SYNTHETIC X-FORWARDED-FOR REQUEST TO LOCAL HA ONLY ==="
response="$(curl --noproxy '*' --silent \
    --connect-timeout 3 --max-time 8 \
    -H 'Host: rakurs.zakryttya.org.ua' \
    -H 'X-Forwarded-For: 192.0.2.123' \
    -H 'X-Forwarded-Proto: https' \
    -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8123/)" || response=000

echo "Home Assistant proxy-test HTTP=$response"

if [[ "$response" =~ ^(200|301|302|303|307|308|401)$ ]]; then
    echo "PASS: Home Assistant accepts forwarded headers from host Nginx source"
    exit 0
fi

echo "STOP: Home Assistant does not yet accept proxy traffic (or returned unexpected status)" >&2
echo "=== RECENT HOME ASSISTANT FORWARDED-PROXY ERRORS ===" >&2
sudo docker logs --since 15m deye-homeassistant 2>&1 |
    grep -Ei '(http[.]forwarded|untrusted proxy|reverse proxy.*not.set.up|x.forwarded.for)' |
    tail -n 15 || true
echo "Configure Settings > System > Network > HTTP server: Trust X-Forwarded-For and exact source IP from logs; save and confirm via SSH tunnel." >&2
echo "Do NOT add 0.0.0.0/0, grant Docker extra capabilities, or change Odoo/Ergodata Nginx sites." >&2
exit 2
