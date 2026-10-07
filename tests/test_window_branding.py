import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import smartscc_tools.branding as branding


class _FakeWindow:
    def __init__(self) -> None:
        self.iconphoto_calls: list[tuple[object, ...]] = []
        self.iconbitmap_calls: list[dict[str, object]] = []
        self.update_idletasks_calls = 0

    def iconphoto(self, *args) -> None:
        self.iconphoto_calls.append(args)

    def iconbitmap(self, **kwargs) -> None:
        self.iconbitmap_calls.append(kwargs)

    def update_idletasks(self) -> None:
        self.update_idletasks_calls += 1

    def winfo_id(self) -> int:
        return 1234


class WindowBrandingTest(unittest.TestCase):
    def test_apply_window_branding_sets_icons_and_windows_identity(self) -> None:
        fake_window = _FakeWindow()
        fake_icon = object()
        native_result = True

        with mock.patch.object(branding.tk, "PhotoImage", return_value=fake_icon), mock.patch.object(
            branding,
            "set_windows_app_user_model_id",
            return_value=True,
        ) as set_app_id:
            with mock.patch.object(branding, "apply_windows_native_window_icon", return_value=native_result) as native_icon:
                result = branding.apply_window_branding(fake_window)

        self.assertEqual(fake_window.iconphoto_calls, [(True, fake_icon)])
        self.assertEqual(
            fake_window.iconbitmap_calls,
            [{"default": str(branding.MAIN_LOGO_ICO)}],
        )
        self.assertIs(getattr(fake_window, "_smartscc_app_icon"), fake_icon)
        set_app_id.assert_called_once_with()
        native_icon.assert_called_once_with(fake_window)
        self.assertTrue(result.app_user_model_id_applied)
        self.assertTrue(result.photo_icon_applied)
        self.assertTrue(result.bitmap_icon_applied)
        self.assertEqual(result.native_icon_applied, native_result)
        self.assertTrue(branding.is_native_window_branding_ready(result))

    def test_apply_windows_native_window_icon_invokes_win32_icon_messages(self) -> None:
        fake_window = _FakeWindow()
        existing_icon_path = Path(__file__)
        user32 = SimpleNamespace(
            LoadImageW=mock.Mock(side_effect=[101, 202]),
            SendMessageW=mock.Mock(return_value=0),
        )

        with mock.patch.object(branding.sys, "platform", "win32"), mock.patch.object(
            branding.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
            create=True,
        ):
            result = branding.apply_windows_native_window_icon(fake_window, icon_path=existing_icon_path)

        self.assertTrue(result)
        self.assertEqual(fake_window.update_idletasks_calls, 1)
        self.assertEqual(user32.LoadImageW.call_count, 2)
        self.assertEqual(
            user32.SendMessageW.call_args_list,
            [
                mock.call(1234, branding.WM_SETICON, branding.ICON_BIG, 101),
                mock.call(1234, branding.WM_SETICON, branding.ICON_SMALL, 202),
            ],
        )

    def test_is_native_window_branding_ready_handles_missing_or_false_results(self) -> None:
        self.assertFalse(branding.is_native_window_branding_ready(None))
        self.assertFalse(
            branding.is_native_window_branding_ready(
                branding.WindowBrandingResult(
                    app_user_model_id_applied=True,
                    photo_icon_applied=True,
                    bitmap_icon_applied=True,
                    native_icon_applied=False,
                )
            )
        )

    def test_set_windows_app_user_model_id_invokes_shell32_on_windows(self) -> None:
        shell32 = SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=mock.Mock(return_value=0))

        with mock.patch.object(branding.sys, "platform", "win32"), mock.patch.object(
            branding.ctypes,
            "windll",
            SimpleNamespace(shell32=shell32),
            create=True,
        ):
            result = branding.set_windows_app_user_model_id("smartscc.tools.master")

        self.assertTrue(result)
        shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with("smartscc.tools.master")

    def test_set_windows_app_user_model_id_returns_false_outside_windows(self) -> None:
        with mock.patch.object(branding.sys, "platform", "linux"):
            self.assertFalse(branding.set_windows_app_user_model_id())


if __name__ == "__main__":
    unittest.main()
