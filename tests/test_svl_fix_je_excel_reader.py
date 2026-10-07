import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from smartscc_tools.features.svl_fix_je.excel_reader import read_excel


def _build_workbook(path: Path, *, svl_id="5139103", je_date=None, sheet_name="SVL_Fix") -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(
        [
            "No",
            "SVL ID",
            "SVL Reference",
            "default_code",
            "Qty",
            "UoM",
            "Unit Cost",
            "Total Value",
            "COA Credit",
            "COA Debit",
            "Journal Code",
            "Tanggal JE",
            "Keterangan",
        ]
    )
    sheet.append(
        [
            1,
            svl_id,
            "WCGT/IN/00892",
            "F-FHVF-0167",
            20,
            "Units",
            25199.41,
            503988.20,
            "1.1.03.01",
            "5.1.01.01",
            "STJ",
            je_date if je_date is not None else datetime(2026, 1, 7),
            "Fix orphan SVL",
        ]
    )
    workbook.save(path)
    workbook.close()


class SvlFixJeExcelReaderTest(unittest.TestCase):
    def test_read_excel_parses_valid_row_and_normalizes_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "svl_fix.xlsx"
            _build_workbook(path)

            rows = read_excel(str(path))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].svl_id, 5139103)
        self.assertEqual(rows[0].je_date, "2026-01-07")
        self.assertEqual(rows[0].journal_code, "STJ")

    def test_read_excel_raises_for_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_excel(r"C:\missing\svl_fix.xlsx")

    def test_read_excel_raises_for_missing_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "svl_fix.xlsx"
            _build_workbook(path, sheet_name="WrongSheet")

            with self.assertRaises(ValueError) as ctx:
                read_excel(str(path))

        self.assertIn("Sheet 'SVL_Fix' tidak ditemukan", str(ctx.exception))

    def test_read_excel_raises_for_invalid_svl_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "svl_fix.xlsx"
            _build_workbook(path, svl_id="ABC-INVALID")

            with self.assertRaises(ValueError):
                read_excel(str(path))

    def test_read_excel_uses_string_date_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "svl_fix.xlsx"
            _build_workbook(path, je_date="2026-02-08")

            rows = read_excel(str(path))

        self.assertEqual(rows[0].je_date, "2026-02-08")
