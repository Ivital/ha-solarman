#!/bin/sh
# Certbot deploy hook: reload Nginx only when Rakurs certificate renews.
set -eu
[ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/rakurs.zakryttya.org.ua" ] || exit 0
/usr/sbin/nginx -t
/bin/systemctl reload nginx
