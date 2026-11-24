# Rakurs — HTTPS publication of Home Assistant via existing Debian Nginx

**Names:** `rakurs.zakryttya.org.ua` (canonical), `www.rakurs.zakryttya.org.ua` (HTTPS 301 redirect). Neither domain has been verified from outside as of 24.11.2026. No server-side DNS or firewall change has been performed.

## Gate: do not expose an uninitialized Home Assistant

First create the HA administrator over the private SSH tunnel. Public access **must not** go live until an admin login and MFA (recommended) are set up. Keep container published on `127.0.0.1:8123`; do not change its `ports` or use host network mode.

## Read-only preflight

```bash
cd ~/deye-monitoring-src
git pull --ff-only
bash deploy/rakurs/preflight.sh
```

Inspect the output before *any* Nginx or DNS change. Validate that A/AAAA for both names point to the intended publicly reachable ingress (which may be an upstream proxy or the MikroTik public WAN), and that external TCP 80/443 are routed to this Nginx. Debian is on `192.168.5.31`, a **private** address. Do **not** overwrite existing MikroTik port forwards for Odoo/Ergodata or other sites; if a different server owns ports 80/443, configure name-based reverse proxy there instead. A public IP obtained via `api.ipify.org` is **egress only** and does not prove inbound access. Remove unintended AAAA entries rather than publishing nonworking IPv6.

## After public DNS and ingress routing are confirmed

The following assumes the existing Debian Nginx on 192.168.5.31 owns inbound HTTP/HTTPS for these names. If it does not, STOP and implement this on the true ingress reverse proxy.

First back up and check for existing site conflicts:

```bash
sudo nginx -T 2>/dev/null | grep -E 'server_name[^;]*rakurs[.]zakryttya[.]org[.]ua' || true
sudo mkdir -p /var/www/letsencrypt/.well-known/acme-challenge
sudo install -m 0644 deploy/rakurs/nginx-http.conf.template /etc/nginx/sites-available/rakurs-http.conf
sudo ln -s /etc/nginx/sites-available/rakurs-http.conf /etc/nginx/sites-enabled/rakurs-http.conf
sudo nginx -t && sudo systemctl reload nginx
```

Do not run these blindly if the file/symlink already exists or the name conflicts. Confirm the ACME challenge path through the public Internet (mobile data is preferable to an internal NAT loopback).

Issue one certificate for BOTH names using **webroot**, leaving the existing Nginx running:

```bash
sudo certbot certonly --webroot -w /var/www/letsencrypt \
  --cert-name rakurs.zakryttya.org.ua \
  -d rakurs.zakryttya.org.ua -d www.rakurs.zakryttya.org.ua
```

Only after both-name certificate issuance, enable HTTPS:

```bash
sudo install -m 0644 deploy/rakurs/nginx-https.conf.template /etc/nginx/sites-available/rakurs-https.conf
sudo ln -s /etc/nginx/sites-available/rakurs-https.conf /etc/nginx/sites-enabled/rakurs-https.conf
sudo nginx -t && sudo systemctl reload nginx
```

If either `nginx -t` fails, **do not reload**; remove only the newly created rakurs symlink and correct that site's configuration. Never remove or rewrite existing Odoo/Ergodata Nginx sites.

## Home Assistant behind reverse proxy (2026+)

In Home Assistant UI: **Settings → System → Network → HTTP server**: enable **Trust X-Forwarded-For** and configure **Trusted proxies** with the *actual source IP observed inside Home Assistant* from the host proxy's Docker connection; the Docker bridge gateway printed by preflight is a candidate, not confirmed. Use a precise address, not an overly broad Docker subnet. Saving these settings causes an HA restart/verification. Complete this over the SSH tunnel first so lockouts do not affect public access. Set the external URL to `https://rakurs.zakryttya.org.ua` in the HA Network settings.

`proxy_set_header X-Forwarded-For $remote_addr` deliberately replaces untrusted client-supplied headers; WebSocket Upgrade is forwarded. Do not enable public access until HA trust settings and login have been verified.

## Verify

```bash
sudo nginx -t
curl -Ik https://rakurs.zakryttya.org.ua/
curl -Ik https://www.rakurs.zakryttya.org.ua/
sudo certbot certificates
sudo certbot renew --dry-run
systemctl is-active odoo18 nginx
```

From an *external* network verify the certificate covers both domains; `www` redirects to canonical HTTPS; login and WebSocket function; Odoo and Ergodata remain available. Do not use `-k` for final production verification (only temporary debugging if required).

## Rollback

If a newly enabled Rakurs site breaks Nginx validation, remove only its new `sites-enabled/rakurs-*.conf` symlinks, validate and reload Nginx. Keep the certificate and Home Assistant data. No other site files should be changed. After ingress failure, the prior `ssh -L` access to Home Assistant remains available.
