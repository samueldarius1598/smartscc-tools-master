import unittest

from smartscc_tools.widgets.datetime_picker import DateTimePickerField


class DateTimePickerFieldTest(unittest.TestCase):
    def test_parse_value_with_datetime(self) -> None:
        parsed = DateTimePickerField.parse_value("2026-03-14 09:08:07")
        self.assertEqual(parsed, ("2026-03-14", "09", "08", "07"))

    def test_parse_value_with_date_only_defaults_midnight(self) -> None:
        parsed = DateTimePickerField.parse_value("2026-03-14")
        self.assertEqual(parsed, ("2026-03-14", "00", "00", "00"))

    def test_compose_value_for_datetime(self) -> None:
        value = DateTimePickerField.compose_value(
            date_text="2026-03-14",
            hour_text="09",
            minute_text="08",
            second_text="07",
            date_only=False,
        )
        self.assertEqual(value, "2026-03-14 09:08:07")

    def test_compose_value_for_date_only(self) -> None:
        value = DateTimePickerField.compose_value(
            date_text="2026-03-14",
            hour_text="09",
            minute_text="08",
            second_text="07",
            date_only=True,
        )
        self.assertEqual(value, "2026-03-14")


if __name__ == "__main__":
    unittest.main()
