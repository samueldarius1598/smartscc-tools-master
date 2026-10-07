import unittest
from unittest import mock

from smartscc_tools.entrypoints.gui import launch_tools_master_gui


class GuiEntrypointTest(unittest.TestCase):
    def test_launch_tools_master_gui_bootstraps_process_branding_before_window(self) -> None:
        events: list[str] = []
        app = mock.Mock()
        app.run.return_value = 77

        def mark_branding() -> bool:
            events.append("branding")
            return True

        def make_window() -> mock.Mock:
            events.append("window")
            return app

        with mock.patch("smartscc_tools.entrypoints.gui.set_windows_app_user_model_id", side_effect=mark_branding):
            with mock.patch("smartscc_tools.entrypoints.gui.DashboardWindow", side_effect=make_window):
                result = launch_tools_master_gui()

        self.assertEqual(result, 77)
        self.assertEqual(events, ["branding", "window"])
        app.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
