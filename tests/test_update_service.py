import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx

from smartscc_tools.update_service import (
    UpdateManifest,
    UpdateService,
    is_newer_version,
    should_auto_check,
)


class UpdateServiceTest(unittest.TestCase):
    def test_version_compare_and_auto_check_helpers(self) -> None:
        self.assertTrue(is_newer_version("1.2.0", "1.1.9"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))
        self.assertFalse(should_auto_check("2999-01-01T00:00:00Z"))

    def test_check_for_update_returns_no_update_when_versions_match(self) -> None:
        binary = b"setup-100"
        digest = hashlib.sha256(binary).hexdigest()
        transport = httpx.MockTransport(
            self._build_handler(
                manifest_payload={
                    "version": "1.0.0",
                    "installer_url": "https://example.com/download/setup-1.0.0.exe",
                    "sha256": digest,
                    "release_notes_url": "https://example.com/release-notes/1.0.0",
                    "published_at": "2026-03-16T12:00:00Z",
                },
                binary_payload=binary,
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            service = UpdateService(
                current_version="1.0.0",
                manifest_url="https://example.com/latest.json",
                cache_dir=Path(tmp_dir),
                transport=transport,
            )

            result = asyncio.run(service.check_for_update())

        self.assertFalse(result.is_update_available)
        self.assertEqual(result.message, "Aplikasi sudah memakai versi terbaru.")

    def test_check_for_update_marks_newer_version_available(self) -> None:
        binary = b"setup-101"
        digest = hashlib.sha256(binary).hexdigest()
        transport = httpx.MockTransport(
            self._build_handler(
                manifest_payload={
                    "version": "1.0.1",
                    "installer_url": "https://example.com/download/setup-1.0.1.exe",
                    "sha256": digest,
                    "release_notes_url": "https://example.com/release-notes/1.0.1",
                    "published_at": "2026-03-16T12:00:00Z",
                },
                binary_payload=binary,
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            service = UpdateService(
                current_version="1.0.0",
                manifest_url="https://example.com/latest.json",
                cache_dir=Path(tmp_dir),
                transport=transport,
            )

            result = asyncio.run(service.check_for_update())

        self.assertTrue(result.is_update_available)
        self.assertIsNotNone(result.manifest)
        self.assertIn("tersedia", result.message)
        self.assertIsNone(result.cached_installer_path)

    def test_fetch_manifest_rejects_invalid_payload(self) -> None:
        transport = httpx.MockTransport(
            self._build_handler(
                manifest_payload={
                    "version": "1.0.1",
                    "sha256": "0" * 64,
                    "release_notes_url": "https://example.com/release-notes/1.0.1",
                    "published_at": "2026-03-16T12:00:00Z",
                },
                binary_payload=b"",
            )
        )

        service = UpdateService(
            current_version="1.0.0",
            manifest_url="https://example.com/latest.json",
            transport=transport,
        )

        with self.assertRaises(ValueError):
            asyncio.run(service.fetch_manifest())

    def test_download_update_rejects_checksum_mismatch(self) -> None:
        binary = b"setup-102"
        transport = httpx.MockTransport(
            self._build_handler(
                manifest_payload={
                    "version": "1.0.2",
                    "installer_url": "https://example.com/download/setup-1.0.2.exe",
                    "sha256": "0" * 64,
                    "release_notes_url": "https://example.com/release-notes/1.0.2",
                    "published_at": "2026-03-16T12:00:00Z",
                },
                binary_payload=binary,
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            service = UpdateService(
                current_version="1.0.0",
                manifest_url="https://example.com/latest.json",
                cache_dir=Path(tmp_dir),
                transport=transport,
            )
            manifest = UpdateManifest(
                version="1.0.2",
                installer_url="https://example.com/download/setup-1.0.2.exe",
                sha256="0" * 64,
                release_notes_url="https://example.com/release-notes/1.0.2",
                published_at="2026-03-16T12:00:00Z",
            )

            with self.assertRaises(ValueError):
                asyncio.run(service.download_update(manifest))

    def test_download_update_saves_and_reuses_cached_installer(self) -> None:
        binary = b"setup-103"
        digest = hashlib.sha256(binary).hexdigest()
        transport = httpx.MockTransport(
            self._build_handler(
                manifest_payload={
                    "version": "1.0.3",
                    "installer_url": "https://example.com/download/setup-1.0.3.exe",
                    "sha256": digest,
                    "release_notes_url": "https://example.com/release-notes/1.0.3",
                    "published_at": "2026-03-16T12:00:00Z",
                },
                binary_payload=binary,
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            service = UpdateService(
                current_version="1.0.0",
                manifest_url="https://example.com/latest.json",
                cache_dir=Path(tmp_dir),
                transport=transport,
            )
            manifest = asyncio.run(service.fetch_manifest())

            progress_events: list[tuple[int, int]] = []
            installer_path = asyncio.run(
                service.download_update(
                    manifest,
                    progress_callback=lambda progress: progress_events.append(
                        (progress.bytes_received, progress.total_bytes)
                    ),
                )
            )
            prepared_path = service.prepare_install(manifest)
            self.assertTrue(installer_path.exists())
            self.assertEqual(prepared_path, installer_path)
            self.assertEqual(installer_path.read_bytes(), binary)
            self.assertTrue(progress_events)

    @staticmethod
    def _build_handler(*, manifest_payload: dict[str, object], binary_payload: bytes):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/latest.json"):
                return httpx.Response(200, json=manifest_payload)
            if request.url.path.endswith(".exe"):
                return httpx.Response(
                    200,
                    content=binary_payload,
                    headers={"Content-Length": str(len(binary_payload))},
                )
            return httpx.Response(404)

        return handler


if __name__ == "__main__":
    unittest.main()
