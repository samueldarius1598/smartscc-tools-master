import logging
import unittest

from smartscc_tools.features.item_journal.messaging import BusinessNarrator, JournalNarrationContext


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class BusinessNarratorTest(unittest.TestCase):
    def _build_narrator(self) -> tuple[BusinessNarrator, _CaptureHandler]:
        logger = logging.getLogger(f"test-business-narrator-{id(self)}")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.propagate = False
        handler = _CaptureHandler()
        logger.addHandler(handler)
        return BusinessNarrator(user_logger=logger, technical_logger=logger), handler

    def test_narrate_ok_journal(self) -> None:
        narrator, handler = self._build_narrator()
        message = narrator.narrate_journal_outcome(
            JournalNarrationContext(
                journal_status="ok",
                journal_refs=["STJ/123"],
            )
        )
        self.assertIn("STJ/123", message)
        self.assertTrue(handler.records)
        self.assertEqual(handler.records[-1].levelno, logging.INFO)

    def test_narrate_not_required_is_info(self) -> None:
        narrator, handler = self._build_narrator()
        message = narrator.narrate_journal_outcome(
            JournalNarrationContext(
                journal_status="not_expected",
                journal_refs=[],
            )
        )
        self.assertIn("Tidak ada Jurnal", message)
        self.assertTrue(handler.records)
        self.assertEqual(handler.records[-1].levelno, logging.INFO)

    def test_narrate_timeout_is_warning(self) -> None:
        narrator, handler = self._build_narrator()
        message = narrator.narrate_journal_outcome(
            JournalNarrationContext(
                journal_status="missing",
                journal_refs=[],
                has_timeout_warning=True,
            )
        )
        self.assertIn("Silakan cek manual di Odoo", message)
        self.assertTrue(handler.records)
        self.assertEqual(handler.records[-1].levelno, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
