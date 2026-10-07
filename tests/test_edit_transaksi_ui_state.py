import json
import tempfile
import unittest
from pathlib import Path

from smartscc_tools.features.edit_transaksi.config import EditTransaksiSettings
from smartscc_tools.services.odoo.profiles import FOLLOW_GLOBAL_PROFILE_ID
from smartscc_tools.core.global_config import GlobalPersistentState, GlobalSettings
from smartscc_tools.modules.edit_transaksi_module import _EditTransaksiPanel, _EditTransaksiStateStore
from smartscc_tools.widgets.datetime_picker import DateTimePickerField


class _FakeStateWidget:
    def __init__(self) -> None:
        self._state: set[str] = set()

    def state(self, statespec=None):
        if statespec is None:
            return tuple(sorted(self._state))
        for token in statespec:
            clean = str(token)
            if clean.startswith("!"):
                self._state.discard(clean[1:])
            else:
                self._state.add(clean)
        return tuple(sorted(self._state))


class _FakePopup:
    def __init__(self, *, mapped: bool = True, pointer_xy: tuple[int, int] = (0, 0)) -> None:
        self._mapped = mapped
        self._pointer_xy = pointer_xy
        self.withdraw_calls = 0

    def winfo_exists(self) -> bool:
        return True

    def winfo_ismapped(self) -> bool:
        return self._mapped

    def withdraw(self) -> None:
        self.withdraw_calls += 1
        self._mapped = False

    def winfo_pointerxy(self) -> tuple[int, int]:
        return self._pointer_xy

    def winfo_rootx(self) -> int:
        return 10

    def winfo_rooty(self) -> int:
        return 10

    def winfo_width(self) -> int:
        return 200

    def winfo_height(self) -> int:
        return 160


class _FakeCalendarWidget:
    def __init__(self, popup: _FakePopup) -> None:
        self._popup = popup
        self.focus_force_calls = 0
        self.after_idle_calls: list[object] = []
        self.after_cancel_calls: list[str] = []
        self.bind_calls: list[tuple[str, object]] = []

    def bind(self, event_name: str, callback) -> None:
        self.bind_calls.append((event_name, callback))

    def after_idle(self, callback) -> str:
        self.after_idle_calls.append(callback)
        return "after#1"

    def after_cancel(self, after_id: str) -> None:
        self.after_cancel_calls.append(after_id)

    def focus_force(self) -> None:
        self.focus_force_calls += 1

    def winfo_toplevel(self) -> _FakePopup:
        return self._popup


class _FakeTopLevelWidget:
    def __init__(self, popup: object) -> None:
        self._popup = popup

    def winfo_toplevel(self) -> object:
        return self._popup


class _FakeDateWidget(_FakeStateWidget):
    def __init__(self, popup: _FakePopup, calendar: _FakeCalendarWidget) -> None:
        super().__init__()
        self._top_cal = popup
        self._calendar = calendar


class _FakeTextWidget:
    def __init__(self) -> None:
        self.configure_calls: list[dict[str, object]] = []
        self.insert_calls: list[tuple[str, str]] = []
        self.see_calls: list[str] = []

    def configure(self, **kwargs) -> None:
        self.configure_calls.append(kwargs)

    def insert(self, index: str, text: str) -> None:
        self.insert_calls.append((index, text))

    def see(self, index: str) -> None:
        self.see_calls.append(index)


class _FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class EditTransaksiUiStateTest(unittest.TestCase):
    @staticmethod
    def _build_picker_with_popup(
        popup: _FakePopup | None = None,
    ) -> tuple[DateTimePickerField, _FakeDateWidget, _FakeCalendarWidget, _FakePopup]:
        actual_popup = popup or _FakePopup()
        calendar = _FakeCalendarWidget(actual_popup)
        date_widget = _FakeDateWidget(actual_popup, calendar)
        picker = DateTimePickerField.__new__(DateTimePickerField)
        picker._date_widget = date_widget
        picker._calendar_focus_after_id = None
        picker._enabled = True
        return picker, date_widget, calendar, actual_popup

    def test_apply_ttk_readonly_state_transitions_disable_enable_cleanly(self) -> None:
        widget = _FakeStateWidget()

        DateTimePickerField._apply_ttk_readonly_state(widget, enabled=False)
        self.assertIn("disabled", widget.state())
        self.assertNotIn("readonly", widget.state())

        DateTimePickerField._apply_ttk_readonly_state(widget, enabled=True)
        self.assertIn("readonly", widget.state())
        self.assertNotIn("disabled", widget.state())

        DateTimePickerField._apply_ttk_readonly_state(widget, enabled=True)
        self.assertIn("readonly", widget.state())
        self.assertNotIn("disabled", widget.state())

    def test_state_store_load_ignores_legacy_last_picking_reference(self) -> None:
        global_settings = GlobalSettings(
            module_settings={
                "edit_transaksi": {
                    "last_picking_reference": "CTR6/OUT/00003",
                    "http_timeout_read": 45,
                    "max_retry": 5,
                }
            }
        )
        store = _EditTransaksiStateStore(global_settings=global_settings)

        state = store.load()

        self.assertIsInstance(state, EditTransaksiSettings)
        self.assertEqual(state.http_timeout_read, 45)
        self.assertEqual(state.max_retry, 5)
        self.assertEqual(state.database_profile_id, FOLLOW_GLOBAL_PROFILE_ID)
        self.assertFalse(state.logs_section_open)
        self.assertFalse(hasattr(state, "last_picking_reference"))

    def test_state_store_save_strips_legacy_last_picking_reference_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            global_path = Path(tmp_dir) / "global_state.json"
            global_settings = GlobalSettings(
                module_settings={"edit_transaksi": {"last_picking_reference": "CTR6/OUT/00003"}}
            )
            store = _EditTransaksiStateStore(
                global_settings=global_settings,
                global_state_store=GlobalPersistentState(path=global_path),
            )

            store.save(EditTransaksiSettings(http_timeout_read=33, max_retry=4))
            saved = json.loads(global_path.read_text(encoding="utf-8"))

        payload = saved["module_settings"]["edit_transaksi"]
        self.assertNotIn("last_picking_reference", payload)
        self.assertEqual(payload["http_timeout_read"], 33)
        self.assertEqual(payload["max_retry"], 4)
        self.assertEqual(payload["database_profile_id"], FOLLOW_GLOBAL_PROFILE_ID)
        self.assertFalse(payload["logs_section_open"])

    def test_state_store_round_trips_logs_section_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            global_path = Path(tmp_dir) / "global_state.json"
            global_settings = GlobalSettings(module_settings={})
            store = _EditTransaksiStateStore(
                global_settings=global_settings,
                global_state_store=GlobalPersistentState(path=global_path),
            )

            store.save(EditTransaksiSettings(http_timeout_read=33, max_retry=4, logs_section_open=True))
            saved = json.loads(global_path.read_text(encoding="utf-8"))

        payload = saved["module_settings"]["edit_transaksi"]
        self.assertTrue(payload["logs_section_open"])

    def test_append_log_updates_latest_preview_to_single_line(self) -> None:
        panel = _EditTransaksiPanel.__new__(_EditTransaksiPanel)
        panel.log_text = _FakeTextWidget()
        panel.latest_log_line_var = _FakeVar("Belum ada log.")

        _EditTransaksiPanel._append_log(
            panel,
            "ERROR: perubahan gagal disimpan karena lock date aktif\nbaris kedua tetap diringkas",
        )

        self.assertEqual(
            panel.latest_log_line_var.get(),
            "ERROR: perubahan gagal disimpan karena lock date aktif baris kedua tetap diringkas",
        )
        self.assertEqual(
            panel.log_text.insert_calls,
            [("end", "ERROR: perubahan gagal disimpan karena lock date aktif\nbaris kedua tetap diringkas\n")],
        )

    def test_finalize_calendar_focus_out_keeps_popup_open_for_focus_inside_popup(self) -> None:
        picker, _date_widget, calendar, popup = self._build_picker_with_popup()
        picker.focus_get = lambda: _FakeTopLevelWidget(popup)

        picker._finalize_calendar_focus_out()

        self.assertEqual(popup.withdraw_calls, 0)
        self.assertEqual(calendar.focus_force_calls, 1)

    def test_finalize_calendar_focus_out_closes_popup_when_focus_leaves_popup(self) -> None:
        picker, date_widget, _calendar, popup = self._build_picker_with_popup()
        outside_popup = object()
        picker.focus_get = lambda: _FakeTopLevelWidget(outside_popup)
        date_widget.state(("pressed",))

        picker._finalize_calendar_focus_out()

        self.assertEqual(popup.withdraw_calls, 1)
        self.assertNotIn("pressed", date_widget.state())

    def test_finalize_calendar_focus_out_refocuses_when_pointer_still_inside_popup(self) -> None:
        picker, _date_widget, calendar, popup = self._build_picker_with_popup(
            _FakePopup(pointer_xy=(40, 40))
        )
        picker.focus_get = lambda: None

        picker._finalize_calendar_focus_out()

        self.assertEqual(popup.withdraw_calls, 0)
        self.assertEqual(calendar.focus_force_calls, 1)


if __name__ == "__main__":
    unittest.main()
