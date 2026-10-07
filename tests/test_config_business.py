import unittest

from smartscc_tools.features.item_journal.config import (
    apply_business_overrides,
    build_runtime_settings,
    defaults_for_preset,
)


class ConfigBusinessTest(unittest.TestCase):
    def test_defaults_safe_fast(self) -> None:
        settings = defaults_for_preset("safe-fast")
        self.assertEqual(settings.transaction_volume_per_batch, 150)
        self.assertEqual(settings.max_retry_for_network_errors, 1)
        self.assertEqual(settings.wait_between_network_retries_ms, 500)

    def test_apply_business_overrides(self) -> None:
        base = defaults_for_preset("safe-fast")
        updated = apply_business_overrides(
            base,
            [
                "TRANSACTION_VOLUME_PER_BATCH=220",
                "MAX_RETRY_FOR_NETWORK_ERRORS=3",
                "INCLUDE_RELATED_TRANSFERS_IN_JOURNAL_CHECK=false",
            ],
        )
        self.assertEqual(updated.transaction_volume_per_batch, 220)
        self.assertEqual(updated.max_retry_for_network_errors, 3)
        self.assertFalse(updated.include_related_transfers_in_journal_check)

    def test_apply_business_overrides_rejects_unknown_key(self) -> None:
        base = defaults_for_preset("safe-fast")
        with self.assertRaises(ValueError) as ctx:
            apply_business_overrides(base, ["STJ_POLL_RETRY_COUNT=8"])
        self.assertIn("Konstanta bisnis tidak dikenal", str(ctx.exception))

    def test_build_runtime_settings_maps_values(self) -> None:
        runtime, business = build_runtime_settings(
            preset="safe-fast",
            set_args=[
                "TRANSACTION_VOLUME_PER_BATCH=333",
                "MAX_WAIT_TIME_FOR_JOURNAL_CREATION_MS=120000",
                "MAX_JOURNAL_VERIFICATION_ATTEMPTS=6",
                "WAIT_BETWEEN_JOURNAL_VERIFICATION_MS=900",
                "WAIT_BETWEEN_JOURNAL_RECOVERY_CHECKS_MS=1400",
            ],
        )
        self.assertEqual(business.transaction_volume_per_batch, 333)
        self.assertEqual(runtime.batch_limit, 333)
        self.assertEqual(runtime.stj_recovery_timeout_ms, 120000)
        self.assertEqual(runtime.stj_poll_retry_count, 6)
        self.assertEqual(runtime.stj_poll_retry_delay_ms, 900)
        self.assertEqual(runtime.stj_recovery_poll_delay_ms, 1400)


if __name__ == "__main__":
    unittest.main()
