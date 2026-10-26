import unittest
from datetime import datetime, timezone

from core.ics import build_community_ics, escape_text, fold_line

NOW = datetime(2030, 3, 10, 12, 0, tzinfo=timezone.utc)


def _event(**overrides):
    event = {
        "id": "11111111-2222-3333-4444-555555555555",
        "title": "Taller de marketing, ventas; y redes",
        "category": "taller",
        "description": "Primera línea\nSegunda línea",
        "location": "Centro de Emprendimiento",
        "link": "https://recoleta.cl/taller?a=1,2",
        "created_by": "InnovaRecoleta",
        "event_at": "2030-03-20T13:00:00+00:00",
        "status": "scheduled",
        "updated_at": "2030-03-01T12:00:00+00:00",
    }
    event.update(overrides)
    return event


class IcsTests(unittest.TestCase):
    def test_escapes_text_values(self):
        self.assertEqual(escape_text("a,b;c\\d\ne"), "a\\,b\\;c\\\\d\\ne")

    def test_folds_long_lines_without_breaking_utf8(self):
        line = "DESCRIPTION:" + "ñ" * 80
        folded = fold_line(line)

        for physical_line in folded.split("\r\n"):
            self.assertLessEqual(len(physical_line.encode("utf-8")), 75)
        self.assertEqual(folded.replace("\r\n ", ""), line)

    def test_builds_calendar_with_utc_dates_and_crlf(self):
        body = build_community_ics([_event()], calendar_name="Convocatorias Recoleta", now=NOW)
        unfolded = body.replace("\r\n ", "")

        self.assertTrue(body.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(body.endswith("END:VCALENDAR\r\n"))
        self.assertIn("UID:11111111-2222-3333-4444-555555555555@financial", unfolded)
        self.assertIn("DTSTART:20300320T130000Z", unfolded)
        self.assertIn("DTEND:20300320T140000Z", unfolded)
        self.assertIn("SUMMARY:Taller de marketing\\, ventas\\; y redes", unfolded)
        self.assertIn("DESCRIPTION:Primera línea\\nSegunda línea", unfolded)
        self.assertIn("STATUS:CONFIRMED", unfolded)
        self.assertIn("URL:https://recoleta.cl/taller?a=1,2", unfolded)
        self.assertIn("CATEGORIES:Taller", unfolded)

    def test_cancelled_events_are_published_as_cancelled(self):
        body = build_community_ics(
            [_event(status="cancelled")],
            calendar_name="Convocatorias Recoleta",
            now=NOW,
        )
        self.assertIn("STATUS:CANCELLED", body)

    def test_sequence_increases_with_updates(self):
        first = build_community_ics([_event()], calendar_name="X", now=NOW)
        second = build_community_ics(
            [_event(updated_at="2030-03-02T12:00:00+00:00")],
            calendar_name="X",
            now=NOW,
        )

        def sequence(body):
            line = next(line for line in body.split("\r\n") if line.startswith("SEQUENCE:"))
            return int(line.split(":", 1)[1])

        self.assertGreater(sequence(second), sequence(first))


if __name__ == "__main__":
    unittest.main()
