import unittest

from smartscc_tools.features.item_journal.messaging import build_slow_feedback, stage_to_business_message


class UiMessagesTest(unittest.TestCase):
    def test_build_slow_feedback_thresholds(self) -> None:
        self.assertIsNone(build_slow_feedback(0))
        self.assertIsNone(build_slow_feedback(9))

        info = build_slow_feedback(10)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.level, "info")
        self.assertIn("menyinkronkan data besar", info.message.lower())

        warn = build_slow_feedback(30)
        self.assertIsNotNone(warn)
        assert warn is not None
        self.assertEqual(warn.level, "warn")
        self.assertIn("koneksi odoo lambat", warn.message.lower())

    def test_transfer_messages(self) -> None:
        start_msg = stage_to_business_message(
            "TRANSFER_START",
            {"company_name": "CECILIA", "item_count": 50},
        )
        self.assertIn("CECILIA", start_msg)
        self.assertIn("(50 item)", start_msg)

        done_msg = stage_to_business_message(
            "TRANSFER_DONE",
            {"transfer_ref": "WH/INT/001"},
        )
        self.assertIn("WH/INT/001", done_msg)
        self.assertIn("berhasil", done_msg.lower())

    def test_sync_wait_message_uses_payload(self) -> None:
        custom = "Sedang sinkronisasi..."
        message = stage_to_business_message(
            "SYNC_WAIT",
            {
                "message": custom,
                "company_name": "CECILIA",
                "src": "WH/Stock",
                "dest": "WH/Output",
                "item_count": 12,
                "preview_top": ["A x1", "B x2", "C x3", "D x4", "E x5"],
                "preview_bottom": ["H x8", "I x9", "J x10", "K x11", "L x12"],
            },
        )
        self.assertIn(custom, message)
        self.assertIn("Company=CECILIA", message)
        self.assertIn("WH/Stock -> WH/Output", message)
        self.assertIn("Top 5:", message)
        self.assertIn("Bottom 5:", message)

    def test_sync_wait_message_uses_full_preview_when_item_count_small(self) -> None:
        message = stage_to_business_message(
            "SYNC_WAIT",
            {
                "message": "Sedang sinkronisasi...",
                "company_name": "CECILIA",
                "src": "WH/Stock",
                "dest": "WH/Output",
                "item_count": 3,
                "preview_top": ["A x1", "B x2", "C x3"],
                "preview_bottom": [],
            },
        )
        self.assertIn("Item: A x1; B x2; C x3", message)


if __name__ == "__main__":
    unittest.main()
