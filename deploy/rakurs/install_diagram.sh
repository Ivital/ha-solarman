#!/usr/bin/env bash
# Install a public, static diagram ONLY in the Rakurs HA /config/www/rakurs folder.
set -Eeuo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
data="$repo/deploy/data"
source="$repo/deploy/rakurs/assets/energy-flow.svg"
target="$data/www/rakurs/energy-flow.svg"
test -f "$source" && test -d "$data" && test ! -L "$data"
test "$(git -C "$repo" branch --show-current)" = "feature/deye-trb145-monitoring"
real_mount="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' deye-homeassistant)"
if [[ "$real_mount" != "$data" ]]; then
  echo "STOP: /config is not mapped to expected folder" >&2
  exit 2
fi
test "$(sudo docker inspect --format '{{.State.Status}}' deye-homeassistant)" = running

python3 - "$source" <<'PY'
import sys
from pathlib import Path
from xml.etree import ElementTree
s=Path(sys.argv[1]).read_text(encoding="utf-8")
assert ElementTree.fromstring(s).tag=="{http://www.w3.org/2000/svg}svg"
assert "<script" not in s.lower() and "<foreignobject" not in s.lower()
assert "https://" not in s
print("SVG PARSE / STATIC SAFETY: PASS")
PY

for path in "$data/www" "$data/www/rakurs" "$target"; do
  if sudo test -L "$path"; then echo "STOP: symlink at $path" >&2; exit 2; fi
done
if sudo test -e "$target" && ! sudo test -f "$target"; then
  echo "STOP: target is not regular file" >&2
  exit 2
fi
sudo install -d -m 0755 "$data/www/rakurs"
if sudo test -f "$target" && ! cmp -s "$source" <(sudo cat "$target"); then
  backup="$target.backup-$(date +%Y%m%d-%H%M%S)"
  sudo cp -p "$target" "$backup"
  echo "Previous SVG backed up: $backup"
fi
sudo install -m 0644 "$source" "$target"
a="$(sha256sum "$source" | cut -d' ' -f1)"
b="$(sudo sha256sum "$target" | cut -d' ' -f1)"
test "$a" = "$b"
echo "SVG SHA256 VERIFIED: $b"
code="$(curl -sS --connect-timeout 3 --max-time 8 -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8123/local/rakurs/energy-flow.svg?v=3')" || code=000
echo "HOME ASSISTANT /local SVG: HTTP=$code"
if [[ "$code" != 200 ]]; then
  echo "STOP: do not import new dashboard until SVG is HTTP 200. If /config/www was newly created, restart only Home Assistant in a controlled step." >&2
  exit 3
fi
systemctl is-active nginx
systemctl is-active odoo18
echo "RAKURS GRAPHIC INSTALLED; import dashboard-rakurs.yaml into existing UI panel."
