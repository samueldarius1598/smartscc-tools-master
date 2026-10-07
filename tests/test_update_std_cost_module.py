import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartscc_tools.core.global_config import GlobalPersistentState, GlobalSettings
from smartscc_tools.core.module_base import ModuleContext
from smartscc_tools.modules.update_std_cost_module import (
    UpdateStdCostModule,
    _UpdateStdCostStateStore,
    build_runtime_connection,
)
from smartscc_tools.features.update_std_cost.config import UpdateStdCostSettings


class UpdateStdCostModuleTest(unittest.TestCase):
    def test_state_store_round_trips_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            global_path = Path(tmp_dir) / "global_state.json"
            settings = GlobalSettings()
            store = _UpdateStdCostStateStore(
                global_settings=settings,
                global_state_store=GlobalPersistentState(path=global_path),
            )

            store.save(
                UpdateStdCostSettings(
                    database_profile_id="db_live",
                    last_workbook_file=r"C:\data\cost.xlsx",
                    mode="variant",
                    execute_dry_run=True,
                    logs_section_open=True,
                )
            )
            saved = json.loads(global_path.read_text(encoding="utf-8"))

        payload = saved["module_settings"]["update_std_cost"]
        self.assertEqual(payload["database_profile_id"], "db_live")
        self.assertEqual(payload["mode"], "variant")
        self.assertTrue(payload["execute_dry_run"])

    def test_module_create_ui_uses_panel(self) -> None:
        module = UpdateStdCostModule()
        context = ModuleContext(
            global_settings=GlobalSettings(),
            auth_info=None,
            logger=logging.getLogger("test.update_std_cost"),
            technical_logger=logging.getLogger("test.update_std_cost.tech"),
            root=object(),
        )
        parent = object()

        with mock.patch("smartscc_tools.modules.update_std_cost_module._UpdateStdCostPanel") as panel_cls:
            result = module.create_ui(parent, context)

        self.assertIs(result, parent)
        panel_cls.assert_called_once()

    def test_module_display_name_uses_requested_label(self) -> None:
        self.assertEqual(UpdateStdCostModule().display_name, "Update Standard Cost Item - Odoo")

    def test_build_runtime_connection_applies_explicit_database_profile_value(self) -> None:
        with mock.patch(
            "smartscc_tools.modules.update_std_cost_module.build_runtime_settings",
            return_value=(object(), object()),
        ) as settings_mock, mock.patch(
            "smartscc_tools.modules.update_std_cost_module.fetch_odoo_config",
            return_value=mock.Mock(base_url="https://demo.local", database="base-db"),
        ):
            settings, config = build_runtime_connection(
                GlobalSettings(),
                logging.getLogger("test.update_std_cost.connection"),
                database_profile_id="override-db",
            )

        self.assertIs(settings, settings_mock.return_value[0])
        self.assertEqual(config.database, "override-db")


if __name__ == "__main__":
    unittest.main()
