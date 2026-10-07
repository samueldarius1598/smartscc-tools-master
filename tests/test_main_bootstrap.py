from unittest import TestCase
from unittest.mock import patch

import main


class MainBootstrapTest(TestCase):
    def test_main_without_args_launches_tools_master_gui(self) -> None:
        with patch("smartscc_tools.entrypoints.gui.launch_tools_master_gui", return_value=11) as launch_gui:
            result = main.main([])

        self.assertEqual(result, 11)
        launch_gui.assert_called_once_with()

    def test_main_gui_alias_launches_tools_master_gui(self) -> None:
        with patch("smartscc_tools.entrypoints.gui.launch_tools_master_gui", return_value=13) as launch_gui:
            result = main.main(["gui"])

        self.assertEqual(result, 13)
        launch_gui.assert_called_once_with()

    def test_main_gui_inspector_dispatches_dashboard_gui_cli(self) -> None:
        with patch(
            "smartscc_tools.features.svl_fix_je.dashboard_gui_cli.main",
            return_value=17,
        ) as gui_cli_main:
            result = main.main(["svl-dashboard-gui-inspect", "--company-id", "755"])

        self.assertEqual(result, 17)
        gui_cli_main.assert_called_once_with(["--company-id", "755"])

    def test_main_with_other_args_dispatches_cli(self) -> None:
        with patch("smartscc_tools.features.item_journal.entrypoints.cli.main", return_value=7) as cli_main:
            result = main.main(["upload", "--workbook", "demo.xlsm"])

        self.assertEqual(result, 7)
        cli_main.assert_called_once_with(["upload", "--workbook", "demo.xlsm"])
