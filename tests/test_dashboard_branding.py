from types import SimpleNamespace
import unittest
from unittest import mock

from smartscc_tools.branding import WindowBrandingResult
from smartscc_tools.shell.dashboard import DashboardWindow
from smartscc_tools.shell.sidebar import SidebarWidget


class _FakeRoot:
    def __init__(self) -> None:
        self.bind_calls: list[tuple[str, object, str | None]] = []
        self.unbind_calls: list[tuple[str, str | None]] = []
        self.after_idle_calls: list[object] = []

    def bind(self, event_name: str, callback, add: str | None = None) -> str:
        self.bind_calls.append((event_name, callback, add))
        return "map-bind-1"

    def unbind(self, event_name: str, funcid: str | None = None) -> None:
        self.unbind_calls.append((event_name, funcid))

    def after_idle(self, callback) -> str:
        self.after_idle_calls.append(callback)
        return "idle-1"


class DashboardBrandingTest(unittest.TestCase):
    def _make_window(self) -> DashboardWindow:
        window = DashboardWindow.__new__(DashboardWindow)
        window.root = _FakeRoot()
        window._logger = mock.Mock()
        window._window_branding_result = None
        window._window_branding_refresh_binding_id = None
        window._window_branding_refresh_scheduled = False
        return window

    def test_schedule_post_map_branding_refresh_binds_root_map_once(self) -> None:
        window = self._make_window()

        window._schedule_post_map_branding_refresh()

        self.assertEqual(len(window.root.bind_calls), 1)
        event_name, callback, add_mode = window.root.bind_calls[0]
        self.assertEqual(event_name, "<Map>")
        self.assertEqual(add_mode, "+")
        self.assertIs(callback.__self__, window)
        self.assertEqual(callback.__func__, DashboardWindow._on_root_map_for_branding)
        self.assertEqual(window._window_branding_refresh_binding_id, "map-bind-1")

    def test_on_root_map_for_branding_unbinds_and_schedules_one_idle_refresh(self) -> None:
        window = self._make_window()
        window._schedule_post_map_branding_refresh()

        window._on_root_map_for_branding(SimpleNamespace(widget=window.root))
        window._on_root_map_for_branding(SimpleNamespace(widget=window.root))

        self.assertTrue(window._window_branding_refresh_scheduled)
        self.assertEqual(window.root.unbind_calls, [("<Map>", "map-bind-1")])
        self.assertEqual(len(window.root.after_idle_calls), 1)
        scheduled_callback = window.root.after_idle_calls[0]
        self.assertIs(scheduled_callback.__self__, window)
        self.assertEqual(scheduled_callback.__func__, DashboardWindow._refresh_window_branding_after_map)

    def test_on_root_map_for_branding_ignores_non_root_widgets(self) -> None:
        window = self._make_window()
        window._schedule_post_map_branding_refresh()

        window._on_root_map_for_branding(SimpleNamespace(widget=object()))

        self.assertFalse(window._window_branding_refresh_scheduled)
        self.assertEqual(window.root.unbind_calls, [])
        self.assertEqual(window.root.after_idle_calls, [])

    def test_refresh_window_branding_after_map_reapplies_window_branding(self) -> None:
        window = self._make_window()
        expected_result = WindowBrandingResult(
            app_user_model_id_applied=True,
            photo_icon_applied=True,
            bitmap_icon_applied=True,
            native_icon_applied=True,
        )

        with mock.patch("smartscc_tools.shell.dashboard.apply_window_branding", return_value=expected_result) as apply_branding:
            with mock.patch("smartscc_tools.shell.dashboard.is_native_window_branding_ready", return_value=True) as is_ready:
                window._refresh_window_branding_after_map()

        apply_branding.assert_called_once_with(window.root)
        is_ready.assert_called_once_with(expected_result)
        self.assertIs(window._window_branding_result, expected_result)
        window._logger.debug.assert_not_called()

    def test_refresh_window_branding_after_map_logs_when_native_branding_missing(self) -> None:
        window = self._make_window()
        missing_native_result = WindowBrandingResult(
            app_user_model_id_applied=True,
            photo_icon_applied=True,
            bitmap_icon_applied=True,
            native_icon_applied=False,
        )

        with mock.patch("smartscc_tools.shell.dashboard.apply_window_branding", return_value=missing_native_result):
            with mock.patch("smartscc_tools.shell.dashboard.is_native_window_branding_ready", return_value=False):
                window._refresh_window_branding_after_map()

        window._logger.debug.assert_called_once()

    def test_ensure_settings_panel_builds_global_settings_panel_lazily_once(self) -> None:
        window = self._make_window()
        window._settings_panel = None
        window._module_container = object()
        window._settings = object()
        window._state_store = object()
        window._update_status = mock.Mock()
        window._shutdown_for_update = mock.Mock()
        fake_panel = object()
        panel_cls = mock.Mock(return_value=fake_panel)

        with mock.patch("smartscc_tools.shell.dashboard._load_global_settings_panel_class", return_value=panel_cls):
            first = window._ensure_settings_panel()
            second = window._ensure_settings_panel()

        self.assertIs(first, fake_panel)
        self.assertIs(second, fake_panel)
        panel_cls.assert_called_once_with(
            window._module_container,
            window._settings,
            window._state_store,
            root=window.root,
            status_callback=window._update_status,
            shutdown_callback=window._shutdown_for_update,
        )

    def test_on_module_selected_settings_uses_lazy_settings_panel(self) -> None:
        window = self._make_window()
        window._settings_panel = None
        window._module_container = mock.Mock()
        window._settings = object()
        window._state_store = mock.Mock()
        window._update_status = mock.Mock()
        window._shutdown_for_update = mock.Mock()
        fake_panel = object()
        panel_cls = mock.Mock(return_value=fake_panel)

        with mock.patch("smartscc_tools.shell.dashboard._load_global_settings_panel_class", return_value=panel_cls):
            window._on_module_selected(SidebarWidget.SETTINGS_ID)

        window._module_container.show_settings.assert_called_once_with(fake_panel)
        panel_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
