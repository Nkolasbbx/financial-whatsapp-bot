import unittest
from datetime import datetime, timezone

from core.calendar_service import (
    calculate_reminder_at,
    get_calendar_timezone,
    normalize_local_event_at,
    prepare_event_schedule,
)


class CalendarServiceTests(unittest.TestCase):
    def test_local_datetime_round_trips_in_configured_timezone(self):
        local_value = datetime(2099, 9, 20, 15, 30)

        utc_value = normalize_local_event_at(
            local_value,
            now=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

        restored = utc_value.astimezone(get_calendar_timezone())
        self.assertEqual(restored.replace(tzinfo=None), local_value)

    def test_past_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "futuro"):
            normalize_local_event_at(
                datetime(2020, 1, 1, 9, 0),
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

    def test_three_day_reminder_preserves_local_calendar_days(self):
        event_at, reminder_at = prepare_event_schedule(
            datetime(2099, 10, 20, 12, 0),
            3,
            now=datetime(2099, 10, 1, tzinfo=timezone.utc),
        )

        timezone_chile = get_calendar_timezone()
        event_local = event_at.astimezone(timezone_chile)
        reminder_local = reminder_at.astimezone(timezone_chile)
        self.assertEqual((event_local.date() - reminder_local.date()).days, 3)
        self.assertEqual(event_local.hour, reminder_local.hour)

    def test_expired_anticipation_is_scheduled_for_now(self):
        now = datetime(2099, 10, 19, 12, 0, tzinfo=timezone.utc)
        event_at = datetime(2099, 10, 20, 12, 0, tzinfo=timezone.utc)

        reminder_at = calculate_reminder_at(
            event_at,
            3,
            now=now,
        )

        self.assertEqual(reminder_at, now)

    def test_invalid_reminder_days_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "anticipación"):
            calculate_reminder_at(
                datetime(2099, 10, 20, 12, 0, tzinfo=timezone.utc),
                2,
                now=datetime(2099, 10, 1, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
