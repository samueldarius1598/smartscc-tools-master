import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smartscc_tools.services.gas.credentials import load_gas_api_key


class GasCredentialsTests(unittest.TestCase):
    def test_private_file_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            key_file = Path(folder) / "smartscc_tools" / "gas_api_key.txt"
            with patch.dict(os.environ, {"APPDATA": folder, "SMARTSCC_GAS_API_KEY": ""}):
                self.assertEqual(load_gas_api_key(), "")
                key_file.parent.mkdir()
                key_file.write_text(" local-demo-key\n", encoding="utf-8")
                self.assertEqual(load_gas_api_key(), "local-demo-key")
                with patch.dict(os.environ, {"SMARTSCC_GAS_API_KEY": " env-demo-key "}):
                    self.assertEqual(load_gas_api_key(), "env-demo-key")

    def test_missing_key_prevents_network_requests(self):
        from smartscc_tools.services.odoo import gateway
        from smartscc_tools.features.item_journal.config import RuntimeSettings
        from smartscc_tools.features.update_std_cost import gas_client

        with patch.object(gateway, "GAS_API_KEY", ""), patch.object(gateway, "_request_with_retry") as request:
            with self.assertRaisesRegex(RuntimeError, "API key GAS"):
                gateway.fetch_odoo_config(RuntimeSettings())
            request.assert_not_called()
        with patch.object(gas_client, "GAS_API_KEY", ""), patch.object(gas_client, "_get_with_retry") as request:
            with self.assertRaisesRegex(RuntimeError, "API key GAS"):
                gas_client.fetch_company_area_map(area_gid=1)
            request.assert_not_called()

    def test_optional_fallback_config_and_missing_fallback_skip_network(self):
        from smartscc_tools.services.gas.credentials import load_gas_setting
        from smartscc_tools.services.odoo import gateway
        from smartscc_tools.features.item_journal.config import RuntimeSettings

        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"APPDATA": folder, "SMARTSCC_GAS_FALLBACK_CONFIG_SSID": ""}):
                self.assertEqual(load_gas_setting("SMARTSCC_GAS_FALLBACK_CONFIG_SSID", "fallback_config_ssid.txt"), "")
                config_file = Path(folder) / "smartscc_tools" / "fallback_config_ssid.txt"
                config_file.parent.mkdir()
                config_file.write_text("demo-sheet-id\n", encoding="utf-8")
                self.assertEqual(load_gas_setting("SMARTSCC_GAS_FALLBACK_CONFIG_SSID", "fallback_config_ssid.txt"), "demo-sheet-id")
                with patch.dict(os.environ, {"SMARTSCC_GAS_FALLBACK_CONFIG_SSID": "env-demo-sheet"}):
                    self.assertEqual(load_gas_setting("SMARTSCC_GAS_FALLBACK_CONFIG_SSID", "fallback_config_ssid.txt"), "env-demo-sheet")
        with patch.object(gateway, "FALLBACK_CONFIG_SSID", ""), patch.object(gateway, "_request_with_retry") as request:
            self.assertEqual(gateway._fetch_fallback_csv(1, None, RuntimeSettings()), [])
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
