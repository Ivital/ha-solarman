#!/usr/bin/env bash
# Read-only SNI/TLS listener diagnosis. Prints certificate public metadata only.
set -u

echo '=== CERTBOT RAKURS CERTIFICATE (EXPECTED) ==='
sudo openssl x509 -in /etc/letsencrypt/live/rakurs.zakryttya.org.ua/cert.pem \
  -noout -subject -ext subjectAltName -fingerprint -sha256 || true

echo '=== NGINX PORT 443 BINDINGS ==='
sudo ss -lntp '( sport = :443 )' || true

echo '=== RELEVANT NGINX DIRECTIVES AND FILE HEADINGS (NO PRIVATE KEYS) ==='
sudo nginx -T 2>/dev/null | awk '
  /^# configuration file / { file=$0 }
  /^[[:space:]]*listen[[:space:]].*443/ { print file; print NR ":" $0 }
  /^[[:space:]]*server_name[[:space:]]/ { print NR ":" $0 }
  /^[[:space:]]*ssl_certificate[[:space:]]/ { print NR ":" $0 }
' | head -n 110 || true

for target in 127.0.0.1 192.168.5.31; do
  for name in rakurs.zakryttya.org.ua www.rakurs.zakryttya.org.ua; do
    echo "=== SNI CERT: ${target}:443 as ${name} ==="
    if ! timeout 9 openssl s_client -connect "${target}:443" \
        -servername "${name}" -showcerts </dev/null 2>/dev/null |
      openssl x509 -noout -subject -ext subjectAltName -fingerprint -sha256; then
      echo "TLS test failed on ${target} for ${name}"
    fi
  done
done

echo '=== RAKURS SITE FILE STATUS ==='
if sudo test -L /etc/nginx/sites-enabled/rakurs-https.conf; then
  echo 'Rakurs HTTPS site is currently enabled'
else
  echo 'Rakurs HTTPS site is NOT enabled (rollback expected)'
fi

echo '=== RECENT NGINX RELOAD WARNINGS ==='
sudo journalctl -u nginx --since '-30 minutes' --no-pager -p warning 2>/dev/null | tail -n 20 || true

echo '=== SERVICES ==='
systemctl is-active nginx || true
systemctl is-active odoo18 || true
echo '=== READ ONLY TLS DIAG DONE ==='
