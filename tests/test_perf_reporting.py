import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path

from smartscc_tools.features.item_journal.observability.perf import PerfReporter, PerfThresholds, RpcTelemetry, classify_cause


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class PerfReportingTest(unittest.TestCase):
    def test_classify_timeout(self) -> None:
        code = classify_cause(
            message="stock.picking.create timeout.",
            stage="BATCH_CREATE",
            severity="error",
            event_type="rpc",
            duration_ms=240000,
        )
        self.assertEqual(code, "transport_timeout")

    def test_classify_stj_missing(self) -> None:
        code = classify_cause(
            message="STJ wajib terisi namun kosong pada 75 baris.",
            stage="STJ_REQUIRED_FAIL",
            severity="error",
            event_type="event",
            duration_ms=10,
        )
        self.assertEqual(code, "stj_missing")

    def test_slow_detector_warn_and_critical(self) -> None:
        thresholds = PerfThresholds.from_profile("aggressive")
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "perf.jsonl"
            reporter = PerfReporter(run_id="r1", profile="aggressive", events_file=str(path))
            reporter.thresholds.warn_stall_sec = thresholds.warn_stall_sec
            reporter.thresholds.critical_stall_sec = thresholds.critical_stall_sec
            reporter.slow_detector.last_progress_ts = datetime.now(timezone.utc) - timedelta(seconds=thresholds.warn_stall_sec + 1)
            alert_warn = reporter.check_stall()
            self.assertIsNotNone(alert_warn)
            self.assertEqual(alert_warn["severity"], "warn")
            reporter.slow_detector.last_progress_ts = datetime.now(timezone.utc) - timedelta(seconds=thresholds.critical_stall_sec + 1)
            reporter.slow_detector.last_emitted_level = ""
            alert_critical = reporter.check_stall()
            self.assertIsNotNone(alert_critical)
            self.assertEqual(alert_critical["severity"], "critical")
            reporter.close()

    def test_mandatory_keys_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            reporter = PerfReporter(run_id="r2", profile="aggressive", events_file=str(path))
            telemetry = RpcTelemetry(
                started_at_utc="2026-03-05T00:00:00Z",
                ended_at_utc="2026-03-05T00:00:02Z",
                duration_ms=2000,
                status="ok",
                attempt=1,
                http_status=200,
                error_class="",
                error_message="",
                model="stock.picking",
                method="create",
                stage="BATCH_CREATE",
                excel_row=2,
            )
            reporter.handle_rpc_telemetry(telemetry)
            reporter.close()
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 1)
            payload = json.loads(lines[0])
            for key in [
                "schema_version",
                "run_id",
                "event_id",
                "timestamp_utc",
                "event_type",
                "stage",
                "severity",
                "cause_code",
                "rpc_model",
                "rpc_method",
                "duration_ms",
                "batch_seq",
                "row_number",
            ]:
                self.assertIn(key, payload)

    def test_perf_alert_goes_to_technical_logger_only(self) -> None:
        user_logger = logging.getLogger(f"test-perf-user-{id(self)}")
        user_logger.setLevel(logging.DEBUG)
        user_logger.handlers.clear()
        user_logger.propagate = False
        user_handler = _CaptureHandler()
        user_logger.addHandler(user_handler)

        technical_logger = logging.getLogger(f"test-perf-tech-{id(self)}")
        technical_logger.setLevel(logging.DEBUG)
        technical_logger.handlers.clear()
        technical_logger.propagate = False
        technical_handler = _CaptureHandler()
        technical_logger.addHandler(technical_handler)

        reporter = PerfReporter(
            run_id="r3",
            profile="aggressive",
            logger=user_logger,
            technical_logger=technical_logger,
            persist_files=False,
        )
        reporter.emit(
            event_type="rpc",
            stage="VALIDATE",
            severity="critical",
            cause_code="odoo_server_slow",
            duration_ms=9000,
            message="slow validate",
        )
        reporter.close()

        user_messages = [record.getMessage() for record in user_handler.records]
        tech_messages = [record.getMessage() for record in technical_handler.records]
        self.assertFalse(any("PERF_ALERT" in message for message in user_messages))
        self.assertTrue(any("PERF_ALERT" in message for message in tech_messages))


if __name__ == "__main__":
    unittest.main()
