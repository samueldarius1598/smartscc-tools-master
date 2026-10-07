import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from smartscc_tools.core.auth import AutoAuthService


class AuthStubTest(unittest.TestCase):
    def test_authenticate_returns_placeholder_without_fetching_config(self) -> None:
        service = AutoAuthService()

        with mock.patch("smartscc_tools.core.auth.fetch_odoo_config") as fetch_config:
            auth_info = service.authenticate()

        self.assertEqual(auth_info.user_email, "user@smartscc.com")
        self.assertEqual(auth_info.display_name, "user")
        fetch_config.assert_not_called()

    def test_background_hydration_updates_user_and_invokes_callback(self) -> None:
        service = AutoAuthService()
        callback_event = threading.Event()
        callback_payload: list[str] = []

        def on_update(info) -> None:
            callback_payload.append(info.user_email)
            callback_event.set()

        with mock.patch("smartscc_tools.core.auth.build_runtime_settings", return_value=(object(), object())), mock.patch(
            "smartscc_tools.core.auth.fetch_odoo_config",
            return_value=SimpleNamespace(user_email="planner@smartscc.com"),
        ):
            service.authenticate()
            service.start_background_hydration(on_update=on_update)
            self.assertTrue(callback_event.wait(timeout=2))

        current = service.get_current_user()
        assert current is not None
        self.assertEqual(current.user_email, "planner@smartscc.com")
        self.assertEqual(callback_payload, ["planner@smartscc.com"])

    def test_background_hydration_failure_keeps_placeholder_user(self) -> None:
        service = AutoAuthService()
        service.authenticate()

        with mock.patch("smartscc_tools.core.auth.build_runtime_settings", return_value=(object(), object())), mock.patch(
            "smartscc_tools.core.auth.fetch_odoo_config",
            side_effect=RuntimeError("network down"),
        ):
            service.start_background_hydration()
            thread = service._hydration_thread  # noqa: SLF001
            assert thread is not None
            thread.join(timeout=2)

        current = service.get_current_user()
        assert current is not None
        self.assertEqual(current.user_email, "user@smartscc.com")


if __name__ == "__main__":
    unittest.main()
