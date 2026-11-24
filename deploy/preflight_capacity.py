#!/usr/bin/env python3
"""Non-mutating capacity gate for a controlled Deye Home Assistant trial."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys

DEFAULT_MIN_AVAILABLE_MIB = 1536
DEFAULT_MIN_FREE_DISK_GIB = 8


def capacity_allows_probe(mem_available_kib: int, minimum_mib: int) -> bool:
    return mem_available_kib >= minimum_mib * 1024


def read_memavailable_kib() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    raise RuntimeError("MemAvailable is missing from /proc/meminfo")


def main() -> int:
    minimum_mib = DEFAULT_MIN_AVAILABLE_MIB
    available_kib = read_memavailable_kib()
    free_gib = shutil.disk_usage("/").free / (1024**3)
    print(f"MemAvailable: {available_kib / 1024:.0f} MiB")
    print(f"Disk free: {free_gib:.1f} GiB")
    print(f"Minimum MemAvailable for considering a probe: {minimum_mib} MiB")
    print("This checks a snapshot only; it cannot guarantee peak-load safety.")
    memory_ok = capacity_allows_probe(available_kib, minimum_mib)
    disk_ok = free_gib >= DEFAULT_MIN_FREE_DISK_GIB
    if not memory_ok or not disk_ok:
        print("NO-GO: do not start Home Assistant on this shared host yet.", file=sys.stderr)
        return 2
    print("CAPACITY PRECHECK PASS: review Odoo load before any manual start.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
