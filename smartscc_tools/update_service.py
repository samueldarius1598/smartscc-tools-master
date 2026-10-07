"""App update manifest parsing, download, and install helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Callable
from urllib.parse import urlparse

import httpx


PLACEHOLDER_UPDATE_MANIFEST_URL = "https://github.com/YOUR-ORG/YOUR-REPO/releases/latest/download/latest.json"
UPDATE_MANIFEST_URL = os.getenv("SMARTSCC_UPDATE_MANIFEST_URL", PLACEHOLDER_UPDATE_MANIFEST_URL)
DEFAULT_UPDATE_CACHE_DIR = (
    Path(os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or (Path.home() / ".cache"))
    / "smartscc_tools"
    / "updates"
)
AUTO_CHECK_INTERVAL = timedelta(hours=24)
_VERSION_TOKEN_PATTERN = re.compile(r"[0-9]+|[A-Za-z]+")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_PLACEHOLDER_MARKERS = ("YOUR-ORG", "YOUR-REPO")


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    installer_url: str
    sha256: str
    release_notes_url: str
    published_at: str

    @classmethod
    def from_payload(cls, payload: object) -> "UpdateManifest":
        if not isinstance(payload, dict):
            raise ValueError("Manifest update harus berupa object JSON.")

        required_keys = (
            "version",
            "installer_url",
            "sha256",
            "release_notes_url",
            "published_at",
        )
        missing_keys = [key for key in required_keys if key not in payload]
        if missing_keys:
            raise ValueError(f"Manifest update tidak lengkap: {', '.join(missing_keys)}")

        version = str(payload.get("version") or "").strip()
        installer_url = str(payload.get("installer_url") or "").strip()
        sha256 = str(payload.get("sha256") or "").strip().lower()
        release_notes_url = str(payload.get("release_notes_url") or "").strip()
        published_at = str(payload.get("published_at") or "").strip()

        if not version:
            raise ValueError("Manifest update tidak memiliki version.")
        if not installer_url.startswith(("http://", "https://")):
            raise ValueError("Manifest update memiliki installer_url yang tidak valid.")
        if not _SHA256_PATTERN.match(sha256):
            raise ValueError("Manifest update memiliki sha256 yang tidak valid.")
        if release_notes_url and not release_notes_url.startswith(("http://", "https://")):
            raise ValueError("Manifest update memiliki release_notes_url yang tidak valid.")
        if not published_at:
            raise ValueError("Manifest update tidak memiliki published_at.")

        return cls(
            version=version,
            installer_url=installer_url,
            sha256=sha256,
            release_notes_url=release_notes_url,
            published_at=published_at,
        )


@dataclass(frozen=True)
class UpdateDownloadProgress:
    bytes_received: int
    total_bytes: int


@dataclass(frozen=True)
class UpdateCheckResult:
    checked_at_utc: str
    current_version: str
    manifest: UpdateManifest | None
    is_update_available: bool
    is_ignored: bool
    cached_installer_path: Path | None
    message: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc_timestamp(value: str) -> datetime | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    if clean.endswith("Z"):
        clean = f"{clean[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc_timestamp(value: str) -> str:
    parsed = parse_utc_timestamp(value)
    if parsed is None:
        return "Belum pernah"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def should_auto_check(
    last_update_check_utc: str,
    *,
    now: datetime | None = None,
    interval: timedelta = AUTO_CHECK_INTERVAL,
) -> bool:
    parsed = parse_utc_timestamp(last_update_check_utc)
    if parsed is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current.astimezone(timezone.utc) - parsed) >= interval


def version_key(value: str) -> tuple[tuple[int, object], ...]:
    clean = str(value or "").strip().lstrip("vV")
    if not clean:
        return tuple()
    parts = _VERSION_TOKEN_PATTERN.findall(clean)
    normalized: list[tuple[int, object]] = []
    for part in parts:
        if part.isdigit():
            normalized.append((0, int(part)))
        else:
            normalized.append((1, part.lower()))
    return tuple(normalized)


def is_newer_version(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class UpdateService:
    """Fetch update manifests, download installers, and launch upgrades."""

    def __init__(
        self,
        *,
        current_version: str,
        manifest_url: str = UPDATE_MANIFEST_URL,
        cache_dir: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.current_version = str(current_version or "").strip()
        self.manifest_url = str(manifest_url or "").strip()
        self.cache_dir = Path(cache_dir or DEFAULT_UPDATE_CACHE_DIR)
        self._transport = transport
        self._timeout = timeout or httpx.Timeout(60.0, connect=15.0)

    def _ensure_manifest_url_configured(self) -> None:
        if not self.manifest_url:
            raise RuntimeError("Update manifest URL belum dikonfigurasi.")
        if any(marker in self.manifest_url for marker in _PLACEHOLDER_MARKERS):
            raise RuntimeError(
                "Update manifest URL masih placeholder. Ganti SMARTSCC_UPDATE_MANIFEST_URL atau constant updater."
            )

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=True,
            transport=self._transport,
            timeout=self._timeout,
        )

    async def fetch_manifest(self) -> UpdateManifest:
        self._ensure_manifest_url_configured()
        async with self._build_client() as client:
            response = await client.get(self.manifest_url)
            response.raise_for_status()
            payload = response.json()
        return UpdateManifest.from_payload(payload)

    def _cache_path_for_manifest(self, manifest: UpdateManifest) -> Path:
        parsed = urlparse(manifest.installer_url)
        file_name = Path(parsed.path).name or f"SmartsCC-ToolsMaster-Setup-{manifest.version}.exe"
        safe_version = re.sub(r"[^A-Za-z0-9._-]+", "_", manifest.version).strip("._") or "unknown"
        return self.cache_dir / safe_version / file_name

    def prepare_install(self, manifest: UpdateManifest) -> Path:
        installer_path = self._cache_path_for_manifest(manifest)
        if not installer_path.exists():
            raise FileNotFoundError(f"Installer update belum ada di cache: {installer_path}")
        if compute_sha256(installer_path).lower() != manifest.sha256.lower():
            raise ValueError("Installer update di cache gagal verifikasi checksum.")
        return installer_path

    async def check_for_update(self, *, ignored_version: str = "") -> UpdateCheckResult:
        checked_at_utc = utc_now_iso()
        manifest = await self.fetch_manifest()
        is_update_available = is_newer_version(manifest.version, self.current_version)
        is_ignored = bool(ignored_version.strip()) and manifest.version == ignored_version.strip()
        cached_installer_path: Path | None = None
        message = "Aplikasi sudah memakai versi terbaru."

        if is_update_available:
            try:
                cached_installer_path = self.prepare_install(manifest)
            except (FileNotFoundError, ValueError):
                cached_installer_path = None

            if is_ignored:
                message = f"Versi {manifest.version} tersedia tetapi sedang diabaikan."
            elif cached_installer_path is not None:
                message = f"Update {manifest.version} sudah siap diinstall."
            else:
                message = f"Update {manifest.version} tersedia untuk di-download."

        return UpdateCheckResult(
            checked_at_utc=checked_at_utc,
            current_version=self.current_version,
            manifest=manifest,
            is_update_available=is_update_available,
            is_ignored=is_ignored,
            cached_installer_path=cached_installer_path,
            message=message,
        )

    async def download_update(
        self,
        manifest: UpdateManifest,
        *,
        progress_callback: Callable[[UpdateDownloadProgress], None] | None = None,
    ) -> Path:
        try:
            return self.prepare_install(manifest)
        except (FileNotFoundError, ValueError):
            pass

        installer_path = self._cache_path_for_manifest(manifest)
        installer_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = installer_path.with_suffix(f"{installer_path.suffix}.download")
        if temp_path.exists():
            temp_path.unlink()

        async with self._build_client() as client:
            async with client.stream("GET", manifest.installer_url) as response:
                response.raise_for_status()
                total_bytes = int(response.headers.get("Content-Length") or 0)
                bytes_received = 0
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        handle.write(chunk)
                        bytes_received += len(chunk)
                        if progress_callback is not None:
                            progress_callback(
                                UpdateDownloadProgress(
                                    bytes_received=bytes_received,
                                    total_bytes=total_bytes,
                                )
                            )

        digest = compute_sha256(temp_path).lower()
        if digest != manifest.sha256.lower():
            temp_path.unlink(missing_ok=True)
            raise ValueError("Checksum installer update tidak cocok.")

        if installer_path.exists():
            installer_path.unlink()
        temp_path.replace(installer_path)
        return installer_path

    def launch_installer(self, installer_path: Path) -> None:
        subprocess.Popen([str(installer_path)])
