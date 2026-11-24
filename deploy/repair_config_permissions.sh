#!/usr/bin/env bash
# Repair ONLY the top-level Home Assistant bind-mounted /config directory.
# The s6/Home Assistant container runs as uid 0 with cap_drop: ALL.
# A host directory owned by an unprivileged user, mode 0755, is not writable.
set -Eeuo pipefail

mode="${1:---check}"
if [[ "${mode}" != "--check" && "${mode}" != "--apply" ]]; then
    echo "Usage: bash deploy/repair_config_permissions.sh [--check|--apply]" >&2
    exit 2
fi

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
data="${repo}/deploy/data"

if [[ ! -d "${data}" || -L "${data}" ]]; then
    echo "STOP: deploy/data must be an existing real directory, not a symlink" >&2
    exit 2
fi

# Verify the actual mount before any privileged modification.
mount_src="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' deye-homeassistant)"
if [[ "${mount_src}" != "${data}" ]]; then
    echo "STOP: container /config mount is not the expected deploy/data" >&2
    echo "Observed: ${mount_src}" >&2
    echo "Expected: ${data}" >&2
    exit 2
fi

state="$(sudo docker inspect --format '{{.State.Status}}' deye-homeassistant)"
echo "Mount verified: ${data} -> /config"
echo "Container state: ${state}"
echo "Current host directory: $(stat -c 'uid=%u gid=%g mode=%a' -- "${data}")"

if [[ "${mode}" == "--check" ]]; then
    echo "DRY RUN: no filesystem changes; use --apply to repair directory ownership"
    exit 0
fi

if [[ "${state}" != "exited" && "${state}" != "created" ]]; then
    echo "STOP: container must be exited or created before changing permissions" >&2
    exit 2
fi

# Do NOT change anything recursively: this leaves all existing HA data alone.
sudo chown 0:0 -- "${data}"
sudo chmod 0700 -- "${data}"

if [[ "$(stat -c '%u:%g:%a' -- "${data}")" != "0:0:700" ]]; then
    echo "STOP: directory permissions verification failed" >&2
    exit 2
fi

echo "PASS: only deploy/data directory now belongs to root:root (0700)."
echo "No recursive changes, no service configuration changes."
