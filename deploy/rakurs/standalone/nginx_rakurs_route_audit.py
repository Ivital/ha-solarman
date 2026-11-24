#!/usr/bin/env python3
"""Read-only Rakurs HTTPS routing audit. Never print full Nginx config or secrets.

Diagnoses why Nginx accepted the Rakurs maintenance config but local HTTPS
still returned HTTP 200. No reloads, writes, MySQL or Docker changes.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

DOMAIN = "rakurs.zakryttya.org.ua"
VHOST = Path("/etc/nginx/sites-available/rakurs-https.conf")
ENABLED = Path("/etc/nginx/sites-enabled/rakurs-https.conf")
TARGETS = ("127.0.0.1", "192.168.5.31")


def execute(argv, timeout=20):
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def server_blocks(config_dump):
    """Extract narrowly scoped, nonsecret server routing metadata."""
    origin = "UNKNOWN"
    depth = 0
    raw = []
    block_file = ""
    blocks = []

    def append_block(content, filename):
        def get(pattern):
            return re.findall(pattern, content, flags=re.MULTILINE)
        listens = get(r"^[ \t]*listen[ \t]+([^;\r\n]+);")
        names = get(r"^[ \t]*server_name[ \t]+([^;\r\n]+);")
        proxies = get(r"^[ \t]*proxy_pass[ \t]+([^;\r\n]+);")
        returns = get(r"^[ \t]*return[ \t]+([0-9]{3})\b")
        marker = "RAKURS_MAINTENANCE_UPGRADING" in content
        if not any(re.search(r"(?<!\d)443(?!\d)", x) for x in listens):
            return
        # Explicitly permitted metadata only. Other config lines stay private.
        blocks.append({
            "file": filename, "listen": listens, "server_name": names,
            "proxy_pass": proxies, "return_codes": returns,
            "maintenance_marker": marker,
        })

    for line in config_dump.splitlines():
        if depth == 0:
            match = re.match(r"^# configuration file ([^:]+):\s*$", line)
            if match:
                origin = match.group(1)
                continue
            if re.match(r"^[ \t]*server[ \t]*\{", line):
                block_file = origin
                raw = [line]
                depth = line.count("{") - line.count("}")
                if depth <= 0:
                    append_block("\n".join(raw), block_file)
                    raw = []
                    depth = 0
                continue
        else:
            raw.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                append_block("\n".join(raw), block_file)
                raw = []
                depth = 0
    return blocks


def print_probe(ip):
    args = [
        "curl", "--noproxy", "*", "--silent", "--show-error",
        "--connect-timeout", "4", "--max-time", "9",
        "--resolve", f"{DOMAIN}:443:{ip}",
        "--dump-header", "-", "--output", "/dev/null",
        "--write-out", "\nPROBE_RESULT=%{http_code} REMOTE_IP=%{remote_ip}\n",
        f"https://{DOMAIN}/",
    ]
    outcome = execute(args, timeout=12)
    if not outcome:
        print(f"HTTPS_PROBE_{ip}=FAILED_TO_RUN")
        return
    result = re.search(r"PROBE_RESULT=([0-9]{3}) REMOTE_IP=(\S+)", outcome.stdout)
    status = result.group(1) if result else "UNKNOWN"
    remote = result.group(2) if result else "UNKNOWN"
    maintenance = bool(re.search(
        r"(?im)^x-rakurs-maintenance:[ \t]*1[ \t]*\r?$",
        outcome.stdout,
    ))
    print(
        f"HTTPS_PROBE_{ip} STATUS={status} REMOTE={remote} "
        f"MAINTENANCE_HEADER={'YES' if maintenance else 'NO'} "
        f"CURL_EXIT={outcome.returncode}"
    )
    if outcome.returncode:
        print(f"HTTPS_PROBE_{ip}_ERROR={type(outcome).__name__}:"
              " HTTPS request failed; credentials/body not displayed")


def main():
    print("=== RAKURS NGINX ROUTING AUDIT (READ ONLY) ===")
    print("DOMAIN=" + DOMAIN)
    print("CONFIG_FILE=" + str(VHOST))
    print("CONFIG_PRESENT=" + str(VHOST.is_file()))
    print("ENABLED_SYMLINK=" + str(ENABLED.is_symlink()))
    print("ENABLED_TARGET=" + (os.path.realpath(ENABLED)
                               if ENABLED.is_symlink() else "NONE"))
    print("MAINTENANCE_TEMPLATE_MARKER=RAKURS_MAINTENANCE_UPGRADING")
    active = execute(["sudo", "nginx", "-T"], timeout=22)
    if not active or active.returncode != 0:
        print("NGINX_EFFECTIVE_CONFIG=UNAVAILABLE; run sudo nginx -t")
    else:
        blocks = server_blocks(active.stdout)
        print("NGINX_EFFECTIVE_CONFIG=READ")
        print("HTTPS_SERVER_BLOCKS=" + str(len(blocks)))
        for index, block in enumerate(blocks[:40], 1):
            print(f"BLOCK_{index}_FILE={block['file']}")
            print(f"BLOCK_{index}_LISTEN={','.join(block['listen']) or 'NONE'}")
            print(f"BLOCK_{index}_NAMES={','.join(block['server_name']) or 'NONE'}")
            print(f"BLOCK_{index}_PROXY={','.join(block['proxy_pass']) or 'NONE'}")
            print(f"BLOCK_{index}_RETURN={','.join(block['return_codes']) or 'NONE'}")
            print(f"BLOCK_{index}_MAINTENANCE={'YES' if block['maintenance_marker'] else 'NO'}")
        if len(blocks) > 40:
            print("HTTPS_SERVER_BLOCKS_TRUNCATED=YES")

    sockets = execute(["sudo", "ss", "-ltnp"], timeout=10)
    if sockets and sockets.returncode == 0:
        print("=== PORT 443 LISTENERS ===")
        filtered = [line.strip() for line in sockets.stdout.splitlines()
                    if re.search(r":443(?:\s|$)", line)]
        for line in filtered[:12]:
            print("LISTENER=" + line[:240])
        if not filtered:
            print("LISTENER_443=NONE")
    else:
        print("LISTENER_443=NOT_AVAILABLE")
    print("=== VERIFIABLE HTTPS PROBES (NO LOGIN) ===")
    for addr in TARGETS:
        print_probe(addr)
    print("AUDIT_FINISHED=YES; FILES_CHANGED=NO; SERVICES_RELOADED=NO")


if __name__ == "__main__":
    main()
