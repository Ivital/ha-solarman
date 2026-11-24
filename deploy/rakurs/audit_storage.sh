#!/usr/bin/env bash
# RAKURS recorder storage read-only audit. Never create SQLite DB, expose secrets,
# stop Docker, change HA config, or alter other services.
set -Eeuo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
data="$repo/deploy/data"
test -d "$data" && test ! -L "$data"

echo "=== VERIFY HOME ASSISTANT CONFIG MOUNT ==="
source="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' deye-homeassistant)"
if [[ "$source" != "$data" ]]; then
  echo "STOP: expected /config bind mount does not match" >&2
  exit 2
fi
echo "Bind mount: $data -> /config"
echo "=== HOST DISK CAPACITY ==="
df -h -- "$data"
echo "=== RECORDER DATABASE METADATA (NO MEASUREMENTS OR SECRETS) ==="
sudo docker exec deye-homeassistant python3 -c '
import os, sqlite3
from pathlib import Path
config=Path("/config/configuration.yaml")
if config.exists():
    lines=config.read_text(encoding="utf-8").splitlines()
    print("Custom recorder YAML section:", any(s.strip()=="recorder:" for s in lines))
    print("Explicit db_url present:", any("db_url:" in s for s in lines))
else:
    print("Configuration YAML absent: inspect HA config separately")
p=Path("/config/home-assistant_v2.db")
if not p.is_file():
    print("Default SQLite DB: NOT FOUND (check custom recorder.db_url)")
    raise SystemExit(0)
print("Default SQLite DB exists: YES")
print("Default SQLite size MiB: %.2f"%(p.stat().st_size/1048576))
# Opens existing DB in strict read-only mode; does not create/modify anything.
db=sqlite3.connect("file:/config/home-assistant_v2.db?mode=ro", uri=True, timeout=3)
try:
    print("Database journal mode:", db.execute("PRAGMA journal_mode").fetchone()[0])
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type=\x27table\x27")}
    for t in ("states", "statistics", "statistics_short_term", "events"):
        print("Table %s: %s"%(t, "present" if t in names else "absent"))
finally:
    db.close()
'
echo "=== SERVICES ==="
systemctl is-active odoo18
systemctl is-active nginx
echo "=== END: READ-ONLY AUDIT ==="
