import importlib.util
from pathlib import Path
import sys
import unittest


TOOL_DIR = Path(__file__).resolve().parents[1] / "tools" / "odoo_inspector"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))
SPEC = importlib.util.spec_from_file_location("uom_inline_check", TOOL_DIR / "uom_inline_check.py")
uom_inline_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(uom_inline_check)


class UomInlineCheckTest(unittest.TestCase):
    def test_same_parent_path_root_converts_by_factor(self) -> None:
        source = {"id": 2513, "name": "ZAK @25 KG", "factor": 25000, "parent_path": "14/2513/"}
        target = {"id": 2335, "name": "JAR @1 KG", "factor": 1000, "parent_path": "14/2335/"}

        result = uom_inline_check.conversion_result(source, target, uom_inline_check.Decimal("1"))

        self.assertTrue(result["inline"])
        self.assertEqual(result["converted_quantity"], "25")

    def test_different_parent_path_root_blocks_conversion(self) -> None:
        source = {"id": 2704, "name": "CASE @24 BTL @330 ML", "factor": 7920, "parent_path": "11/2238/2704/"}
        target = {"id": 2205, "name": "BTL @1 (EA)", "factor": 1, "parent_path": "2205/"}

        result = uom_inline_check.conversion_result(source, target, uom_inline_check.Decimal("1"))

        self.assertFalse(result["inline"])
        self.assertIsNone(result["converted_quantity"])
        self.assertIn("Different parent_path roots", result["reason"])

    def test_units_and_pcs_are_business_equivalent_override(self) -> None:
        source = {"id": 1, "name": "Units", "factor": 1, "parent_path": "1/"}
        target = {"id": 51, "name": "PCS (P)", "factor": 1, "parent_path": "51/"}

        result = uom_inline_check.conversion_result(source, target, uom_inline_check.Decimal("8"))

        self.assertTrue(result["inline"])
        self.assertEqual(result["converted_quantity"], "8")
        self.assertEqual(result["override"], "units_pcs_p_1_to_1")

    def test_parent_path_ids_ignores_invalid_segments(self) -> None:
        row = {"parent_path": "14/x/2335/"}

        self.assertEqual(uom_inline_check.parent_path_ids(row), [14, 2335])


if __name__ == "__main__":
    unittest.main()
