"""SanMar SFTP client (paramiko).

Downloads the daily product data files from the SanMar SFTP share. SanMar uses
SFTP (SSH) on port 2200 — *not* FTPS — and the login is the customer number +
the FTP password from the one-time Bitwarden link (distinct from the
sanmar.com web-services credentials).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import paramiko
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import SftpConfig

log = logging.getLogger(__name__)

# Legacy SSH host-key algorithms SanMar's server still negotiates. paramiko 4.0+
# dropped these from its advertised defaults (and 5.0 removed them outright),
# which makes key exchange fail with "no acceptable host key". We pin paramiko
# to the 3.x line (see pyproject) and additionally offer any of these the
# installed paramiko still supports.
_LEGACY_HOST_KEY_ALGOS = ("ssh-rsa", "ssh-dss")


class SanMarSftp:
    """Thin wrapper around a paramiko SFTP session for SanMar downloads."""

    def __init__(self, config: SftpConfig) -> None:
        self._config = config

    @contextmanager
    def _session(self) -> Iterator[paramiko.SFTPClient]:
        transport = paramiko.Transport((self._config.host, self._config.port))
        self._enable_legacy_host_keys(transport)
        try:
            host_key = self._expected_host_key()
            transport.connect(
                username=self._config.username,
                password=self._config.password,
                hostkey=host_key,
            )
            sftp = paramiko.SFTPClient.from_transport(transport)
            if sftp is None:  # pragma: no cover - defensive
                raise RuntimeError("Failed to open SFTP channel")
            try:
                yield sftp
            finally:
                sftp.close()
        finally:
            transport.close()

    @staticmethod
    def _enable_legacy_host_keys(transport: paramiko.Transport) -> None:
        """Advertise legacy host-key algorithms SanMar's SFTP server requires.

        Appends ``ssh-rsa`` / ``ssh-dss`` (whichever the installed paramiko
        still supports) to the set the client offers during key exchange, so
        connecting to SanMar's older SSH server doesn't fail with "no
        acceptable host key". A no-op when the server uses a modern host key.
        """
        try:
            supported = set(getattr(type(transport), "_key_info", {}))
            offered = list(transport._preferred_keys)
            for algo in _LEGACY_HOST_KEY_ALGOS:
                if algo in supported and algo not in offered:
                    offered.append(algo)
            transport._preferred_keys = tuple(offered)
        except Exception:  # pragma: no cover - defensive against paramiko internals
            log.warning("Could not widen host-key algorithms; using paramiko defaults")

    def _expected_host_key(self) -> paramiko.PKey | None:
        """Return a pinned host key if configured, else None (trust on first use).

        Pinning ``SANMAR_SFTP_HOST_KEY`` (base64 of the server key) is strongly
        recommended for production to prevent MITM.
        """
        raw = self._config.host_key.strip()
        if not raw:
            log.warning(
                "SANMAR_SFTP_HOST_KEY not set — accepting server host key without "
                "verification. Pin the key for production."
            )
            return None
        import base64

        key_bytes = base64.b64decode(raw)
        # Default SanMar key type is ssh-rsa; paramiko reads the algorithm from
        # the blob when constructed via from_type_string is unavailable, so try
        # RSA then Ed25519.
        for key_cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                return key_cls(data=key_bytes)
            except paramiko.SSHException:
                continue
        raise ValueError("SANMAR_SFTP_HOST_KEY could not be parsed as a known key type")

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        reraise=True,
    )
    def download(self, filename: str, dest_dir: str | Path | None = None) -> Path:
        """Download a single file from the configured remote dir; return its path."""
        dest_dir = Path(dest_dir or self._config.download_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        remote_path = f"{self._config.remote_dir.rstrip('/')}/{filename}"
        local_path = dest_dir / filename
        log.info("Downloading %s -> %s", remote_path, local_path)
        with self._session() as sftp:
            sftp.get(remote_path, str(local_path))
        size = local_path.stat().st_size
        log.info("Downloaded %s (%s bytes)", filename, f"{size:,}")
        return local_path

    def download_many(
        self, filenames: list[str], dest_dir: str | Path | None = None
    ) -> dict[str, Path]:
        """Download several files; return {filename: local_path}."""
        return {name: self.download(name, dest_dir) for name in filenames}

    def list_dir(self, remote_dir: str | None = None) -> list[str]:
        """List filenames in a remote directory (for diagnostics)."""
        target = remote_dir or self._config.remote_dir
        with self._session() as sftp:
            return sftp.listdir(target)
