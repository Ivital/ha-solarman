"""Verified MySQL TLS transport; deliberately no silent SSL downgrade.

Existing web deployments without a CA can continue in their PRE-EXISTING
LAN plaintext mode temporarily, but no real capture ingest may be enabled
until a trusted CA has been deployed and verified against the server identity.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


def mysql_tls_options(*, require_verified: bool = False) -> dict:
    location = os.environ.get("RAKURS_MYSQL_CA_FILE", "").strip()
    if not location:
        if require_verified:
            raise RuntimeError(
                "Verified MySQL CA required for real Modbus ingestion; "
                "refusing unencrypted credentials or telemetry"
            )
        return {"ssl_disabled": True}
    path = Path(location)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise ValueError("CA must be a regular non-writable file")
        if not path.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----"):
            raise ValueError("CA file is not a PEM certificate")
    except (OSError, ValueError) as exc:
        raise RuntimeError("Unsafe or missing MySQL trust anchor") from exc
    return {
        "ssl_ca": str(path),
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    }


def mysql_transport_state() -> str:
    return ("TLS_VERIFY_IDENTITY" if os.environ.get("RAKURS_MYSQL_CA_FILE")
            else "LAN_PLAINTEXT_LEGACY_REQUIRES_MIGRATION")
