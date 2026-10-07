import json
import tempfile
import unittest
from pathlib import Path

from smartscc_tools.services.odoo.profiles import DEFAULT_DUMMY_PROFILE_ID, DEFAULT_LIVE_PROFILE_ID, DatabaseProfile
from smartscc_tools.core.global_config import (
    GlobalPersistentState,
    GlobalSettings,
    InventoryCoaEntry,
    RepairAccountEntry,
)


class GlobalConfigTest(unittest.TestCase):
    def test_load_returns_defaults_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state = GlobalPersistentState(path=Path(tmp_dir) / "missing.json").load()

        self.assertEqual(state.active_module_id, "item_journal")
        self.assertEqual(state.window_geometry, "1280x820")
        self.assertTrue(state.auto_check_updates)
        self.assertTrue(state.auto_download_updates)
        self.assertEqual(state.last_update_check_utc, "")
        self.assertEqual(state.ignored_update_version, "")
        self.assertEqual([profile.profile_id for profile in state.database_profiles], [DEFAULT_LIVE_PROFILE_ID, DEFAULT_DUMMY_PROFILE_ID])
        self.assertEqual(state.default_database_profile_id, "")
        self.assertTrue(state.inventory_coa_defaults_initialized)
        self.assertEqual([entry.coa_code for entry in state.inventory_coa_entries[:2]], ["1105001", "1105002"])
        self.assertEqual(len(state.inventory_coa_entries), 8)
        self.assertTrue(state.repair_account_defaults_initialized)
        self.assertEqual([entry.coa_code for entry in state.repair_account_entries], ["1108099"])
        self.assertEqual(state.module_settings, {})

    def test_load_survives_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "global_state.json"
            path.write_text("{invalid", encoding="utf-8")

            state = GlobalPersistentState(path=path).load()

        self.assertEqual(state.db_override, "")
        self.assertEqual(state.default_database_profile_id, "")
        self.assertEqual(state.active_module_id, "item_journal")

    def test_save_round_trip_preserves_geometry_and_module_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "global_state.json"
            store = GlobalPersistentState(path=path)
            settings = GlobalSettings(
                db_override="demo-db",
                database_profiles=[
                    DatabaseProfile(
                        profile_id="db_demo",
                        database_value="demo-db",
                        alias="Demo ERP",
                        note="Testing",
                    )
                ],
                default_database_profile_id="db_demo",
                default_output_dir=r"C:\output",
                default_input_dir=r"C:\input",
                active_module_id="edit_transaksi",
                window_geometry="1440x900+10+20",
                auto_check_updates=False,
                auto_download_updates=False,
                last_update_check_utc="2026-03-16T12:00:00Z",
                ignored_update_version="1.0.9",
                inventory_coa_entries=[InventoryCoaEntry(entry_id="coa_1", coa_code="1105001", label="Persediaan Alkohol")],
                repair_account_entries=[RepairAccountEntry(entry_id="repair_1", coa_code="1108099", label="Correction")],
                module_settings={"item_journal": {"db_override": "item-db"}},
            )

            store.save(settings)
            saved_payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = store.load()

        self.assertNotIn("db_override", saved_payload)
        self.assertEqual(saved_payload["window_geometry"], "1440x900+10+20")
        self.assertEqual(loaded.window_geometry, "1440x900+10+20")
        self.assertFalse(loaded.auto_check_updates)
        self.assertFalse(loaded.auto_download_updates)
        self.assertEqual(loaded.last_update_check_utc, "2026-03-16T12:00:00Z")
        self.assertEqual(loaded.ignored_update_version, "1.0.9")
        self.assertEqual(loaded.default_database_profile_id, "db_demo")
        self.assertEqual(loaded.database_profiles[0].database_value, "demo-db")
        self.assertTrue(loaded.inventory_coa_defaults_initialized)
        self.assertEqual(loaded.inventory_coa_entries[0].coa_code, "1105001")
        self.assertTrue(loaded.repair_account_defaults_initialized)
        self.assertEqual(loaded.repair_account_entries[0].coa_code, "1108099")
        self.assertEqual(loaded.module_settings["item_journal"]["db_override"], "item-db")
        self.assertEqual(loaded.active_module_id, "edit_transaksi")

    def test_load_migrates_legacy_db_override_into_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "global_state.json"
            path.write_text(
                json.dumps(
                    {
                        "db_override": "legacy-db",
                        "module_settings": {},
                    }
                ),
                encoding="utf-8",
            )

            loaded = GlobalPersistentState(path=path).load()

        self.assertEqual(loaded.default_database_profile_id, "db_legacy_db")
        self.assertTrue(any(profile.database_value == "legacy-db" for profile in loaded.database_profiles))

    def test_load_seeds_inventory_coa_entries_when_field_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "global_state.json"
            path.write_text(
                json.dumps(
                    {
                        "database_profiles": [],
                        "module_settings": {},
                    }
                ),
                encoding="utf-8",
            )

            loaded = GlobalPersistentState(path=path).load()

        self.assertEqual(len(loaded.inventory_coa_entries), 8)
        self.assertTrue(loaded.inventory_coa_defaults_initialized)
        self.assertEqual(loaded.inventory_coa_entries[0].coa_code, "1105001")
        self.assertEqual([entry.coa_code for entry in loaded.repair_account_entries], ["1108099"])

    def test_load_seeds_explicit_empty_inventory_coa_entries_without_init_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "global_state.json"
            path.write_text(
                json.dumps(
                    {
                        "inventory_coa_entries": [],
                        "module_settings": {},
                    }
                ),
                encoding="utf-8",
            )

            loaded = GlobalPersistentState(path=path).load()

        self.assertTrue(loaded.inventory_coa_defaults_initialized)
        self.assertEqual(len(loaded.inventory_coa_entries), 8)
        self.assertTrue(loaded.repair_account_defaults_initialized)
        self.assertEqual([entry.coa_code for entry in loaded.repair_account_entries], ["1108099"])

    def test_load_preserves_explicit_empty_inventory_coa_entries_after_init_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "global_state.json"
            path.write_text(
                json.dumps(
                    {
                        "inventory_coa_defaults_initialized": True,
                        "inventory_coa_entries": [],
                        "module_settings": {},
                    }
                ),
                encoding="utf-8",
            )

            loaded = GlobalPersistentState(path=path).load()

        self.assertTrue(loaded.inventory_coa_defaults_initialized)
        self.assertEqual(loaded.inventory_coa_entries, [])

    def test_load_preserves_explicit_empty_repair_account_entries_after_init_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "global_state.json"
            path.write_text(
                json.dumps(
                    {
                        "repair_account_defaults_initialized": True,
                        "repair_account_entries": [],
                        "module_settings": {},
                    }
                ),
                encoding="utf-8",
            )

            loaded = GlobalPersistentState(path=path).load()

        self.assertTrue(loaded.repair_account_defaults_initialized)
        self.assertEqual(loaded.repair_account_entries, [])


if __name__ == "__main__":
    unittest.main()
