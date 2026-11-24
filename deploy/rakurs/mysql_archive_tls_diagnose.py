#!/usr/bin/env python3
"""Read-only inspection of untrusted MySQL TLS server certificate metadata.

Uses OpenSSL STARTTLS mysql, sends NO password or SQL, changes no files.
DO NOT import a captured peer certificate as a CA unless separately
verified against the hosting provider's authenticated support channel.
"""
from __future__ import annotations
import re
import subprocess

HOST = "192.168.5.30"
PORT = 3306


def main() -> int:
    print("RAKURS REMOTE MYSQL TLS PEER INSPECTION (NOT YET TRUSTED)")
    print("Target:", HOST, PORT)
    cmd = [
        "openssl", "s_client", "-starttls", "mysql",
        "-connect", "%s:%d" % (HOST, PORT),
        "-servername", HOST, "-verify_hostname", HOST, "-showcerts",
    ]
    try:
        result = subprocess.run(
            cmd, input=b"", capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("TLS_INSPECTION_ERROR:", type(exc).__name__)
        return 2

    output = (result.stdout + b"\n" + result.stderr).decode(
        "utf-8", errors="replace"
    )
    certs = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        output, flags=re.DOTALL,
    )
    print("Peer-supplied certificate count:", len(certs))
    for index, pem in enumerate(certs[:8], start=1):
        print("=== UNTRUSTED CERTIFICATE %d ===" % index)
        try:
            data = subprocess.run(
                ["openssl", "x509", "-noout", "-subject", "-issuer",
                 "-dates", "-fingerprint", "-sha256", "-ext",
                 "subjectAltName"],
                input=pem.encode("ascii"), capture_output=True,
                timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            print("Cannot parse certificate metadata")
            continue
        if data.returncode:
            print("Certificate metadata parser returned an error")
            continue
        for line in data.stdout.decode("utf-8", errors="replace").splitlines():
            if line.strip().startswith((
                "subject=", "issuer=", "notBefore=", "notAfter=",
                "sha256 Fingerprint=", "SHA256 Fingerprint=",
                "X509v3 Subject Alternative Name", "DNS:", "IP Address:"
            )):
                print(line.strip()[:500])

    print("=== OPENSSL VERIFICATION RESULT ===")
    verify_lines = [
        line.strip() for line in output.splitlines()
        if ("Verify return code:" in line or "Verification error:" in line
            or "verify error:num=" in line)
    ]
    for line in verify_lines[-10:]:
        print(line[:500])
    if not verify_lines:
        print("No verification result returned (inspect TLS mode with provider).")
    print("Only untrusted certificate metadata shown. No login/SQL performed.")
    print("Never trust or install these certificates without provider confirmation.")
    return 0 if certs else 2


if __name__ == "__main__":
    raise SystemExit(main())
