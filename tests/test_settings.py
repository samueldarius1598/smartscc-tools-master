import unittest

from smartscc_tools.features.item_journal.config import RuntimeSettings


class RuntimeSettingsTest(unittest.TestCase):
    def test_safe_preset_defaults(self) -> None:
        settings = RuntimeSettings.from_preset("safe")
        self.assertEqual(settings.batch_limit, 1000)
        self.assertEqual(settings.retry_max, 2)
        self.assertTrue(settings.auto_validate)
        self.assertTrue(settings.stj_required)
        self.assertEqual(settings.stj_poll_retry_count, 8)
        self.assertEqual(settings.stj_poll_fast_attempts, 3)
        self.assertEqual(settings.stj_poll_max_delay_ms, 10000)
        self.assertEqual(settings.stj_recovery_timeout_ms, 600000)
        self.assertEqual(settings.stj_recovery_poll_delay_ms, 2000)
        self.assertEqual(settings.stj_recovery_fast_attempts, 3)
        self.assertEqual(settings.stj_recovery_poll_max_delay_ms, 10000)
        self.assertTrue(settings.stj_include_origin_scope)
        self.assertEqual(settings.stj_remap_mode, "conservative")
        self.assertAlmostEqual(settings.stj_remap_qty_tolerance, 1e-6)
        self.assertEqual(settings.stj_row_split_policy, "fail")
        self.assertEqual(settings.stj_result_picking_mode, "actual")
        self.assertEqual(settings.create_timeout_extra_retry_max, 1)
        self.assertEqual(settings.create_origin_mode, "technical")

    def test_safe_fast_preset_changes(self) -> None:
        settings = RuntimeSettings.from_preset("safe-fast")
        self.assertEqual(settings.batch_limit, 150)
        self.assertEqual(settings.retry_max, 1)
        self.assertEqual(settings.transport_max_retries, 2)
        self.assertFalse(settings.debug_row_json)

    def test_fast_preset_changes(self) -> None:
        settings = RuntimeSettings.from_preset("fast")
        self.assertEqual(settings.batch_limit, 1500)
        self.assertEqual(settings.prefetch_chunk_size, 400)
        self.assertFalse(settings.debug_row_json)


if __name__ == "__main__":
    unittest.main()
