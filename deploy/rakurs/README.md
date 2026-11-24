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

## Certificate issuance and actual HTTP validation (24.11.2026)

Local ACME challenge returned HTTP 200 and `rakurs-acme-ok` for BOTH
domain names. Certbot completed successful HTTP-01 issuance; certificate
SANs contain `rakurs.zakryttya.org.ua` and
`www.rakurs.zakryttya.org.ua`. Validity: 24.11.2026–22.02.2027
(exact `notAfter`: 2027-02-22 09:59:07 UTC). Renewal scheduling was
reported by Certbot. **HTTPS Nginx site has not yet been activated**.

After the HA owner exists and reverse proxy trust is set in the UI,
activate HTTPS with the guarded script:

```bash
cd ~/deye-monitoring-src
git pull --ff-only
bash deploy/rakurs/activate_https.sh
```

The script does not install the site if an active HA owner cannot be
verified from `/config/.storage/auth`, or if the existing Rakurs
HTTPS Nginx site already exists. It rolls back just the new Rakurs
HTTPS site on Nginx validation, reload or local HTTPS check failure.
If HA returns 400, configure **Settings → System → Network → HTTP server**
to trust the actual Nginx proxy IP (Docker gateway candidate
`172.18.0.1`) before repeating. Do NOT change `configuration.yaml`
for this setting on HA 2026.8+.

After successfully activating HTTPS, install the renewal reload hook so
that a renewed certificate is actually picked up by running Nginx:

```bash
sudo install -m 0755 deploy/rakurs/renewal-nginx-reload.sh \
  /etc/letsencrypt/renewal-hooks/deploy/rakurs-nginx-reload.sh
sudo certbot renew --dry-run
```

The hook only reloads Nginx after renewal of the Rakurs certificate.
Verify from an external network that both HTTPS names terminate at the
intended Nginx rather than an upstream/misrouted server.

## TLS hostname mismatch during first activation (24.11.2026)

The first `activate_https.sh` run confirmed an existing HA owner and
passed `nginx -t`, but the immediate `curl --resolve ... 127.0.0.1`
checks received the **wrong TLS certificate** (hostname mismatch).
The activation script correctly rolled back **only** the Rakurs
HTTPS server file. The Let's Encrypt certificate remains valid for
both names and need NOT be reissued.

Possible causes: an HTTPS listener bound to a more specific local
address than the new wildcard `listen 443`; delayed Nginx worker reload;
or conflicting SNI/server blocks. Diagnose read-only:

```bash
cd ~/deye-monitoring-src
git pull --ff-only
bash deploy/rakurs/diagnose_tls.sh
```

The script shows the *public* certificate subject/SAN/fingerprint served
on `127.0.0.1` and `192.168.5.31` for both SNI hostnames, Nginx
port bindings, a restricted list of Nginx TLS directives, and reload
warnings. **No private key contents** are printed or modified.

`activate_https.sh` now polls up to 20 times after Nginx reload;
TLS validation remains enabled. If it still fails, it rolls back.
Do not guess `listen` parameters or enable `curl -k` as a fix.

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
