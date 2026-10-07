from pathlib import Path
import unittest

from PIL import Image

from smartscc_tools.branding import (
    EDIT_STOCK_MOVEMENT_ICON,
    FIXING_UNLINK_SVL_ICON,
    ITEM_JOURNAL_ICON,
    MAIN_LOGO_ICO,
    MAIN_LOGO_PNG,
    UPDATE_STANDARD_COST_ICON,
)
from smartscc_tools.modules.edit_transaksi_module import EditTransaksiModule
from smartscc_tools.modules.item_journal_module import ItemJournalModule
from smartscc_tools.modules.svl_fix_je_module import SvlFixJeModule
from smartscc_tools.modules.update_std_cost_module import UpdateStdCostModule


class BrandingAssetsTest(unittest.TestCase):
    def test_branding_paths_exist(self) -> None:
        self.assertTrue(MAIN_LOGO_PNG.exists())
        self.assertTrue(EDIT_STOCK_MOVEMENT_ICON.exists())
        self.assertTrue(ITEM_JOURNAL_ICON.exists())
        self.assertTrue(FIXING_UNLINK_SVL_ICON.exists())
        self.assertTrue(UPDATE_STANDARD_COST_ICON.exists())

    def test_module_icons_use_branding_folder(self) -> None:
        self.assertEqual(Path(ItemJournalModule().icon_path), ITEM_JOURNAL_ICON)
        self.assertEqual(Path(EditTransaksiModule().icon_path), EDIT_STOCK_MOVEMENT_ICON)
        self.assertEqual(Path(SvlFixJeModule().icon_path), FIXING_UNLINK_SVL_ICON)
        self.assertEqual(Path(UpdateStdCostModule().icon_path), UPDATE_STANDARD_COST_ICON)

    def test_packaging_files_reference_mainlogo_icon(self) -> None:
        spec_text = Path("build_tools/packaging/windows/tools_master_gui.spec").read_text(encoding="utf-8")
        iss_text = Path("build_tools/packaging/windows/installer.iss").read_text(encoding="utf-8")
        ps1_text = Path("build_tools/packaging/windows/install_app.ps1").read_text(encoding="utf-8")
        self.assertIn("mainlogo.ico", spec_text)
        self.assertIn("mainlogo.ico", iss_text)
        self.assertIn("mainlogo.ico", ps1_text)

    def test_generated_mainlogo_ico_exists(self) -> None:
        self.assertTrue(MAIN_LOGO_ICO.exists())

    def test_generated_mainlogo_ico_contains_small_windows_sizes(self) -> None:
        with Image.open(MAIN_LOGO_ICO) as icon_image:
            icon_sizes = set(icon_image.info.get("sizes", set()))

        self.assertIn((16, 16), icon_sizes)
        self.assertIn((32, 32), icon_sizes)


if __name__ == "__main__":
    unittest.main()
