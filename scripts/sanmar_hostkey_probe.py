"""Print SanMar's SFTP server host key so it can be pinned.

A host key is public: the server sends it to every client before any login,
so this needs no username or password and prints nothing secret. The base64
line is what goes in data/sanmar_sftp_host_key.b64 (or SANMAR_SFTP_HOST_KEY).
If a pinned key is already on file it says whether the server still matches.
"""

from __future__ import annotations

import socket

import paramiko

from sanmar_netsuite.config import SftpConfig
from sanmar_netsuite.sanmar.sftp_client import (
    SanMarSftp,
    _pinned_host_key_from_file,
)


def main() -> int:
    cfg = SftpConfig.from_env()
    sock = socket.create_connection((cfg.host, cfg.port), timeout=30)
    transport = paramiko.Transport(sock)
    try:
        SanMarSftp._enable_legacy_host_keys(transport)
        transport.start_client(timeout=30)
        key = transport.get_remote_server_key()
    finally:
        transport.close()
    b64 = key.get_base64()
    print(f"host: {cfg.host}:{cfg.port}")
    print(f"key type: {key.get_name()}")
    print(f"sha256 fingerprint: {key.fingerprint}")
    print(f"HOSTKEY_BASE64={b64}")
    pinned = cfg.host_key.strip() or _pinned_host_key_from_file()
    if pinned:
        print("pinned key on file: " + ("MATCHES the server" if pinned == b64
                                        else "DOES NOT MATCH the server"))
        return 0 if pinned == b64 else 1
    print("pinned key on file: none yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
