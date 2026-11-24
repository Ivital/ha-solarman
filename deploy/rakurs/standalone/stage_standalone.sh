#!/usr/bin/env bash
# Stage independent Rakurs Flask service; never alter nginx/HA/Deye/ITM.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
[[ "$MODE" == '--check' || "$MODE" == '--install' ]] || {
  echo 'Usage: bash stage_standalone.sh [--check|--install]' >&2; exit 2
}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
test -s "$SRC/signal_catalog.json"
test -s "$HERE/release_manifest.txt"
mapfile -t RELEASE_FILES < <(grep -v -E '^[[:space:]]*(#|$)' "$HERE/release_manifest.txt")
test "${#RELEASE_FILES[@]}" -ge 17 || {
  echo "STOP: incomplete release manifest" >&2; exit 2
}
declare -A RELEASE_SEEN
for f in "${RELEASE_FILES[@]}"; do
  [[ "$f" =~ ^[a-zA-Z0-9_./-]+$ && "$f" != /* && "$f" != *../* ]] || {
    echo "STOP: unsafe manifest entry: $f" >&2; exit 2
  }
  [[ ! -v RELEASE_SEEN["$f"] ]] || { echo "STOP: duplicate $f" >&2; exit 2; }
  RELEASE_SEEN["$f"]=1
  test -f "$HERE/$f" && test -s "$HERE/$f" && ! test -L "$HERE/$f" || {
    echo "STOP: required release file missing, empty or symlink: $f" >&2; exit 2
  }
done
python3 -m py_compile "$HERE/app.py" "$HERE/auth.py" "$HERE/bootstrap_admin.py"
echo 'RAKURS_STANDALONE_SOURCES=PASS'
echo 'Target code /opt/rakurs (no HA); code run as system user rakurs.'
if [[ "$MODE" == '--check' ]]; then
  echo 'DRY_RUN=PASS; NO SYSTEM CHANGES'
  exit 0
fi
sudo -v
# Bootstrap is not a rolling update mechanism. Never overwrite a running
# deployment with unbacked-up files: use the managed deploy script instead.
if systemctl is-active --quiet rakurs-standalone.service; then
  echo "STOP: service active; stage_standalone --install is unsafe on live Rakurs" >&2
  echo "Use the versioned release deploy after a verified backup." >&2
  exit 2
fi
if ! getent passwd rakurs >/dev/null; then
  sudo useradd --system --user-group --home-dir /var/lib/rakurs \
    --create-home --shell /usr/sbin/nologin rakurs
fi
sudo install -d -m 0750 -o root -g rakurs /opt/rakurs
sudo install -d -m 0750 -o root -g rakurs \
  /opt/rakurs/standalone \
  /opt/rakurs/standalone/static \
  /opt/rakurs/standalone/templates
sudo install -d -m 0750 -o rakurs -g rakurs /var/lib/rakurs
sudo install -d -m 0750 -o root -g rakurs /etc/rakurs
for f in "${RELEASE_FILES[@]}"; do
  sudo install -m 0640 -o root -g rakurs "$HERE/$f" \
    "/opt/rakurs/standalone/$f"
done
sudo install -m 0640 -o root -g rakurs "$SRC/signal_catalog.json" \
  /opt/rakurs/signal_catalog.json

if [[ ! -e /etc/rakurs/session_secret ]]; then
  sudo python3 - <<'PY'
import os, pwd, secrets
path = "/etc/rakurs/session_secret"
owner = pwd.getpwnam("rakurs")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    os.fchown(fd, owner.pw_uid, owner.pw_gid)
    os.write(fd, secrets.token_hex(32).encode("ascii") + b"\n")
    os.fsync(fd)
finally:
    os.close(fd)
PY
fi

if [[ ! -x /var/lib/rakurs/.venv/bin/python ]]; then
  sudo -u rakurs python3 -m venv /var/lib/rakurs/.venv
fi
sudo -u rakurs /var/lib/rakurs/.venv/bin/python -m pip \
  install --disable-pip-version-check \
  -r /opt/rakurs/standalone/requirements.txt

sudo install -m 0644 "$HERE/rakurs-standalone.service" \
  /etc/systemd/system/rakurs-standalone.service
sudo systemctl daemon-reload
echo 'RAKURS_STANDALONE_STAGE=PASS; SERVICE_NOT_STARTED'
echo 'Next: auth tables, web admin and separate MySQL account/secret; then start service.'
echo 'No nginx, Home Assistant, Odoo, Ergodata or Teltonika changes made.'
