#!/usr/bin/env bash
# Read-only: diagnose Rakurs DNS, occupied Nginx listeners and Docker proxy.
set -u
echo '=== RAKURS DOMAIN DNS (local resolver) ==='
for name in rakurs.zakryttya.org.ua www.rakurs.zakryttya.org.ua; do
  echo "--- ${name} ---"
  getent ahostsv4 "${name}" | awk '!a[$1]++ {print "A/local:", $1}' || true
  getent ahostsv6 "${name}" | awk '!a[$1]++ {print "AAAA/local:", $1}' || true
  if command -v dig >/dev/null 2>&1; then
    dig +time=2 +tries=1 @1.1.1.1 +short A "${name}" 2>/dev/null | sed 's/^/A public: /' || true
    dig +time=2 +tries=1 @1.1.1.1 +short AAAA "${name}" 2>/dev/null | sed 's/^/AAAA public: /' || true
  fi
done
echo '=== PUBLIC EGRESS IPV4 (not necessarily inbound IP) ==='
curl -4 -fsS --connect-timeout 3 --max-time 7 https://api.ipify.org || true
echo
echo '=== TCP LISTENERS 80,443,8123 ==='
sudo ss -lntp '( sport = :80 or sport = :443 or sport = :8123 )' || true
echo '=== NGINX CONFIG VALIDATION ==='
sudo nginx -t || true
echo '=== EXISTING RAKURS NGINX SERVER NAMES ==='
sudo nginx -T 2>/dev/null | grep -E 'server_name[^;]*rakurs[.]zakryttya[.]org[.]ua' || true
echo '=== CERTBOT ==='
command -v certbot || true
echo '=== EXISTING LETSENCRYPT CERT (NO KEY CONTENT) ==='
if sudo test -f /etc/letsencrypt/live/rakurs.zakryttya.org.ua/fullchain.pem; then
  echo 'Certificate path EXISTS'
else
  echo 'Certificate path MISSING'
fi
echo '=== DOCKER HOME ASSISTANT ==='
sudo docker inspect --format 'status={{.State.Status}} ports={{json .HostConfig.PortBindings}}' deye-homeassistant || true
echo '=== DOCKER NETWORK GATEWAY / TRUSTED PROXY CANDIDATE ==='
sudo docker network inspect --format '{{range .IPAM.Config}}subnet={{.Subnet}} gateway={{.Gateway}}{{end}}' deye-monitoring_monitoring || true
echo '=== HOME ASSISTANT HTTP ==='
curl -sS --connect-timeout 2 --max-time 5 -o /dev/null -w 'HTTP=%{http_code}\n' http://127.0.0.1:8123/ || true
echo '=== PRODUCTION SERVICES ==='
systemctl is-active odoo18 || true
systemctl is-active nginx || true
echo '=== COMPLETED: READ ONLY ==='
