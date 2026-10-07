import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from smartscc_tools.features.item_journal.entrypoints.gui import PersistentState
from smartscc_tools.services.odoo.profiles import FOLLOW_GLOBAL_PROFILE_ID
from smartscc_tools.core.global_config import GlobalPersistentState, GlobalSettings
from smartscc_tools.core.module_base import ModuleContext
from smartscc_tools.modules.item_journal_module import ItemJournalModule, ItemJournalModuleStateStore


class ItemJournalAdapterTest(unittest.TestCase):
    def test_state_store_migrates_legacy_state_to_global_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            legacy_path = tmp_path / "legacy.json"
            global_path = tmp_path / "global_state.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "workbook_path": r"C:\data\journal.xlsx",
                        "output_dir": str(tmp_path),
                        "db_override": "legacy-db",
                        "grouping_mode": "custom",
                        "custom_limit": 175,
                    }
                ),
                encoding="utf-8",
            )
            global_settings = GlobalSettings()
            store = ItemJournalModuleStateStore(
                global_settings=global_settings,
                global_state_store=GlobalPersistentState(path=global_path),
                legacy_state_store=PersistentState(path=legacy_path),
            )

            state = store.load()
            saved_global = json.loads(global_path.read_text(encoding="utf-8"))

        self.assertEqual(state.database_profile_id, "db_legacy_db")
        self.assertIn("item_journal", saved_global["module_settings"])
        self.assertEqual(
            saved_global["module_settings"]["item_journal"]["workbook_path"],
            r"C:\data\journal.xlsx",
        )

    def test_state_store_prefers_global_module_settings_and_global_defaults(self) -> None:
        global_settings = GlobalSettings(
            db_override="global-db",
            default_output_dir=r"C:\shared\output",
            module_settings={"item_journal": {"workbook_path": "demo.xlsx", "output_dir": "", "db_override": ""}},
        )
        store = ItemJournalModuleStateStore(global_settings=global_settings)

        state = store.load()

        self.assertEqual(state.workbook_path, "demo.xlsx")
        self.assertEqual(state.database_profile_id, FOLLOW_GLOBAL_PROFILE_ID)
        self.assertEqual(state.output_dir, r"C:\shared\output")

    def test_module_create_ui_uses_item_journal_panel(self) -> None:
        module = ItemJournalModule()
        context = ModuleContext(
            global_settings=GlobalSettings(),
            auth_info=None,
            logger=logging.getLogger("test.item_journal_adapter"),
            technical_logger=logging.getLogger("test.item_journal_adapter.tech"),
            root=object(),
        )
        parent = object()

        panel_cls = mock.Mock()
        persistent_state_cls = mock.Mock(return_value=mock.Mock())
        fake_gui_module = SimpleNamespace(ItemJournalPanel=panel_cls, PersistentState=persistent_state_cls)
        with mock.patch("smartscc_tools.modules.item_journal_module._load_item_journal_gui_module", return_value=fake_gui_module):
            result = module.create_ui(parent, context)

        self.assertIs(result, parent)
        panel_cls.assert_called_once()

    def test_module_display_name_uses_internal_transfer_label(self) -> None:
        self.assertEqual(ItemJournalModule().display_name, "Internal Transfer - Odoo")


if __name__ == "__main__":
    unittest.main()
