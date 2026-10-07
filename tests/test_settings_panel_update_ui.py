import unittest

from smartscc_tools.shell.settings_panel import (
    UpdatePanelViewState,
    build_update_panel_view_state,
    format_download_progress,
)
from smartscc_tools.update_service import UpdateDownloadProgress


class SettingsPanelUpdateUiTest(unittest.TestCase):
    def test_checking_state_disables_actions(self) -> None:
        state = build_update_panel_view_state("checking", latest_version="1.1.0")

        self.assertIsInstance(state, UpdatePanelViewState)
        self.assertFalse(state.check_enabled)
        self.assertFalse(state.download_enabled)
        self.assertFalse(state.install_enabled)
        self.assertIn("cek update", state.status_text)

    def test_ready_state_enables_install(self) -> None:
        state = build_update_panel_view_state(
            "ready",
            latest_version="1.1.0",
            has_manifest=True,
            has_download=True,
            has_release_notes=True,
        )

        self.assertTrue(state.check_enabled)
        self.assertTrue(state.download_enabled)
        self.assertTrue(state.install_enabled)
        self.assertTrue(state.release_notes_enabled)

    def test_failed_state_keeps_manual_recovery_actions(self) -> None:
        state = build_update_panel_view_state(
            "failed",
            latest_version="1.1.0",
            has_manifest=True,
            error_text="Manifest timeout",
        )

        self.assertTrue(state.check_enabled)
        self.assertTrue(state.download_enabled)
        self.assertFalse(state.install_enabled)
        self.assertEqual(state.status_text, "Manifest timeout")

    def test_progress_formatter_includes_percent_and_sizes(self) -> None:
        text = format_download_progress(UpdateDownloadProgress(bytes_received=512, total_bytes=1024))

        self.assertIn("50%", text)
        self.assertIn("512 B", text)
        self.assertIn("1.0 KB", text)


if __name__ == "__main__":
    unittest.main()
