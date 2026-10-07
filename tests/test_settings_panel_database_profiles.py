import tkinter as tk
import unittest
from unittest import mock

from smartscc_tools.services.odoo.profiles import DatabaseProfile
from smartscc_tools.core.global_config import GlobalSettings, InventoryCoaEntry, RepairAccountEntry
from smartscc_tools.shell.settings_panel import (
    GlobalSettingsPanel,
    build_database_profile_preview,
    build_inventory_coa_preview,
    build_repair_account_preview,
)


class _FakeVar:
    def __init__(self, value="") -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _FakeListbox:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.selected: list[int] = []
        self.activated: int | None = None

    def delete(self, _start, _end=None) -> None:
        self.items = []

    def insert(self, _index, value: str) -> None:
        self.items.append(value)

    def bind(self, *_args, **_kwargs) -> None:
        return None

    def selection_clear(self, _start, _end=None) -> None:
        self.selected = []

    def selection_set(self, index: int) -> None:
        self.selected = [index]

    def activate(self, index: int) -> None:
        self.activated = index

    def curselection(self):
        return tuple(self.selected)


class _FakeCombo:
    def __init__(self) -> None:
        self.values = ()

    def configure(self, **kwargs) -> None:
        if "values" in kwargs:
            self.values = tuple(kwargs["values"])


class SettingsPanelDatabaseProfilesTest(unittest.TestCase):
    def test_preview_formats_alias_value_and_note(self) -> None:
        self.assertEqual(
            build_database_profile_preview("Live ERP", "hwgroup_erp", "Database Live"),
            "Live ERP - hwgroup_erp [Database Live]",
        )

    def test_inventory_coa_preview_formats_code_and_label(self) -> None:
        self.assertEqual(build_inventory_coa_preview("1105001", "Persediaan Alkohol"), "1105001 - Persediaan Alkohol")

    def test_repair_account_preview_formats_code_and_label(self) -> None:
        self.assertEqual(build_repair_account_preview("1108099", "Correction"), "1108099 - Correction")

    def test_save_database_profile_edit_adds_profile_and_refreshes_ui(self) -> None:
        panel = GlobalSettingsPanel.__new__(GlobalSettingsPanel)
        panel._database_profiles = []
        panel._selected_database_profile_id = ""
        panel._settings = GlobalSettings()
        panel.profile_value_var = _FakeVar("hwgroup_erp")
        panel.profile_alias_var = _FakeVar("Live ERP")
        panel.profile_note_var = _FakeVar("Database Live")
        panel.profile_preview_var = _FakeVar("")
        panel.database_profile_listbox = _FakeListbox()
        panel.default_database_profile_combo = _FakeCombo()
        panel.default_database_profile_var = _FakeVar("")
        panel._global_default_label_by_id = {}
        panel._global_default_id_by_label = {}

        panel._save_database_profile_edit()

        self.assertEqual(len(panel._database_profiles), 1)
        self.assertEqual(panel._database_profiles[0].database_value, "hwgroup_erp")
        self.assertIn("Live ERP - hwgroup_erp [Database Live]", panel.database_profile_listbox.items)
        self.assertTrue(panel.default_database_profile_combo.values)

    def test_delete_selected_database_profile_clears_matching_global_default(self) -> None:
        panel = GlobalSettingsPanel.__new__(GlobalSettingsPanel)
        panel._database_profiles = [
            DatabaseProfile(
                profile_id="db_live",
                database_value="hwgroup_erp",
                alias="Live ERP",
                note="Database Live",
            )
        ]
        panel._selected_database_profile_id = "db_live"
        panel._settings = GlobalSettings(default_database_profile_id="db_live")
        panel.profile_value_var = _FakeVar("hwgroup_erp")
        panel.profile_alias_var = _FakeVar("Live ERP")
        panel.profile_note_var = _FakeVar("Database Live")
        panel.profile_preview_var = _FakeVar("")
        panel.database_profile_listbox = _FakeListbox()
        panel.default_database_profile_combo = _FakeCombo()
        panel.default_database_profile_var = _FakeVar("")
        panel._global_default_label_by_id = {}
        panel._global_default_id_by_label = {}

        with mock.patch("smartscc_tools.shell.settings_panel.messagebox.showwarning") as warning_mock:
            panel._delete_selected_database_profile()

        warning_mock.assert_not_called()
        self.assertEqual(panel._database_profiles, [])
        self.assertEqual(panel._settings.default_database_profile_id, "")

    def test_init_builds_real_default_database_combo_widget(self) -> None:
        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

        try:
            panel = GlobalSettingsPanel(
                root,
                GlobalSettings(auto_check_updates=False),
                mock.Mock(),
                root=root,
            )
            root.update_idletasks()

            self.assertIsNotNone(panel.default_database_profile_combo)
            self.assertTrue(panel.default_database_profile_combo.winfo_exists())
            self.assertTrue(panel.default_database_profile_combo.cget("values"))
            self.assertTrue(hasattr(panel, "_scrollable"))
            self.assertTrue(panel.inventory_coa_listbox.winfo_exists())
            self.assertEqual(panel.inventory_coa_listbox.size(), 8)
            self.assertTrue(panel.repair_account_listbox.winfo_exists())
            self.assertEqual(panel.repair_account_listbox.size(), 1)
        finally:
            root.destroy()

    def test_save_inventory_coa_entry_adds_entry_and_refreshes_ui(self) -> None:
        panel = GlobalSettingsPanel.__new__(GlobalSettingsPanel)
        panel._inventory_coa_entries = []
        panel._selected_inventory_coa_entry_id = ""
        panel.inventory_coa_code_var = _FakeVar("1105001")
        panel.inventory_coa_label_var = _FakeVar("Persediaan Alkohol")
        panel.inventory_coa_preview_var = _FakeVar("")
        panel.inventory_coa_listbox = _FakeListbox()

        panel._save_inventory_coa_entry()

        self.assertEqual(len(panel._inventory_coa_entries), 1)
        self.assertEqual(panel._inventory_coa_entries[0].coa_code, "1105001")
        self.assertIn("1105001 - Persediaan Alkohol", panel.inventory_coa_listbox.items)

    def test_delete_inventory_coa_entry_removes_selected_row(self) -> None:
        panel = GlobalSettingsPanel.__new__(GlobalSettingsPanel)
        panel._inventory_coa_entries = [
            InventoryCoaEntry(entry_id="coa_1", coa_code="1105001", label="Persediaan Alkohol")
        ]
        panel._selected_inventory_coa_entry_id = "coa_1"
        panel.inventory_coa_code_var = _FakeVar("1105001")
        panel.inventory_coa_label_var = _FakeVar("Persediaan Alkohol")
        panel.inventory_coa_preview_var = _FakeVar("")
        panel.inventory_coa_listbox = _FakeListbox()

        with mock.patch("smartscc_tools.shell.settings_panel.messagebox.showwarning") as warning_mock:
            panel._delete_selected_inventory_coa_entry()

        warning_mock.assert_not_called()
        self.assertEqual(panel._inventory_coa_entries, [])

    def test_save_repair_account_entry_adds_entry_and_refreshes_ui(self) -> None:
        panel = GlobalSettingsPanel.__new__(GlobalSettingsPanel)
        panel._repair_account_entries = []
        panel._selected_repair_account_entry_id = ""
        panel.repair_account_code_var = _FakeVar("1108099")
        panel.repair_account_label_var = _FakeVar("Correction")
        panel.repair_account_preview_var = _FakeVar("")
        panel.repair_account_listbox = _FakeListbox()

        panel._save_repair_account_entry()

        self.assertEqual(len(panel._repair_account_entries), 1)
        self.assertEqual(panel._repair_account_entries[0].coa_code, "1108099")
        self.assertIn("1108099 - Correction", panel.repair_account_listbox.items)

    def test_delete_repair_account_entry_removes_selected_row(self) -> None:
        panel = GlobalSettingsPanel.__new__(GlobalSettingsPanel)
        panel._repair_account_entries = [
            RepairAccountEntry(entry_id="repair_1", coa_code="1108099", label="Correction")
        ]
        panel._selected_repair_account_entry_id = "repair_1"
        panel.repair_account_code_var = _FakeVar("1108099")
        panel.repair_account_label_var = _FakeVar("Correction")
        panel.repair_account_preview_var = _FakeVar("")
        panel.repair_account_listbox = _FakeListbox()

        with mock.patch("smartscc_tools.shell.settings_panel.messagebox.showwarning") as warning_mock:
            panel._delete_selected_repair_account_entry()

        warning_mock.assert_not_called()
        self.assertEqual(panel._repair_account_entries, [])


if __name__ == "__main__":
    unittest.main()
