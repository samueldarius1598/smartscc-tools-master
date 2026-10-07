import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import smartscc_tools.features.item_journal.entrypoints.cli as cli_module

from smartscc_tools.features.item_journal.entrypoints.cli import _CliProgressView


class CliProgressViewTest(unittest.TestCase):
    def test_plain_fallback_progress_and_summary(self) -> None:
        with mock.patch.object(cli_module.sys.stdout, "isatty", return_value=False):
            view = _CliProgressView()

        out = io.StringIO()
        with redirect_stdout(out):
            view.on_progress(
                {
                    "stage": "TRANSFER_START",
                    "current": 1,
                    "total": 10,
                    "company_name": "CECILIA",
                    "item_count": 10,
                }
            )
            view.on_progress(
                {
                    "stage": "TRANSFER_DONE",
                    "current": 10,
                    "total": 10,
                    "transfer_ref": "WH/INT/001",
                }
            )
            view.show_summary({"rows_success": 10, "rows_error": 0, "rows_stopped": 0})

        text = out.getvalue()
        self.assertIn("Memproses Transfer Internal", text)
        self.assertIn("Transfer WH/INT/001 berhasil diproses.", text)
        self.assertIn("Ringkasan Upload", text)


if __name__ == "__main__":
    unittest.main()
