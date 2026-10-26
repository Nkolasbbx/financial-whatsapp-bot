import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from core import community_events
from core.community_events import (
    NOTICE_PLACE_FALLBACK,
    build_notice_template_parameters,
    validate_community_event,
)
from db import community_events as db_community

NOW = datetime(2030, 3, 10, 12, 0, tzinfo=timezone.utc)


def _valid(**overrides):
    data = {
        "title": "Taller de marketing digital",
        "category": "taller",
        "description": "Aprende a vender por redes sociales.",
        "event_at": "2030-03-20T10:00",
        "notice_at": "2030-03-19T10:00",
        "location": "Centro de Emprendimiento",
        "link": "https://recoleta.cl/taller",
    }
    data.update(overrides)
    return data


class ValidationTests(unittest.TestCase):
    def test_valid_event_is_normalized_to_utc(self):
        cleaned, errors = validate_community_event(_valid(), now=NOW)

        self.assertEqual(errors, [])
        # Marzo en Chile es horario de verano (UTC-3).
        self.assertEqual(cleaned["event_at"], datetime(2030, 3, 20, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(cleaned["notice_at"], datetime(2030, 3, 19, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(cleaned["title"], "Taller de marketing digital")

    def test_every_missing_required_field_is_reported_at_once(self):
        _, errors = validate_community_event(
            {"title": "", "category": "", "description": "  ", "event_at": "", "notice_at": None},
            now=NOW,
        )

        self.assertIn("El título es obligatorio.", errors)
        self.assertIn("Elige una categoría.", errors)
        self.assertIn("La descripción es obligatoria.", errors)
        self.assertIn("Indica la fecha y hora de la actividad.", errors)
        self.assertIn("Indica la fecha y hora del aviso.", errors)

    def test_optional_fields_can_be_empty(self):
        cleaned, errors = validate_community_event(_valid(location="", link=""), now=NOW)

        self.assertEqual(errors, [])
        self.assertIsNone(cleaned["location"])
        self.assertIsNone(cleaned["link"])

    def test_invalid_category(self):
        _, errors = validate_community_event(_valid(category="fiesta"), now=NOW)
        self.assertEqual(errors, ["La categoría elegida no es válida."])

    def test_short_title(self):
        _, errors = validate_community_event(_valid(title="Ok"), now=NOW)
        self.assertEqual(errors, ["El título debe tener al menos 3 caracteres."])

    def test_past_activity_and_notice(self):
        _, errors = validate_community_event(
            _valid(event_at="2030-03-01T10:00", notice_at="2030-02-28T10:00"),
            now=NOW,
        )
        self.assertIn("La actividad debe ser en una fecha futura.", errors)
        self.assertIn("El aviso debe programarse en una fecha futura.", errors)

    def test_notice_after_activity(self):
        _, errors = validate_community_event(_valid(notice_at="2030-03-21T10:00"), now=NOW)
        self.assertEqual(errors, ["El aviso no puede ser posterior a la actividad."])

    def test_notice_at_same_time_as_activity_is_allowed(self):
        _, errors = validate_community_event(_valid(notice_at="2030-03-20T10:00"), now=NOW)
        self.assertEqual(errors, [])

    def test_link_must_be_http(self):
        _, errors = validate_community_event(_valid(link="recoleta.cl/taller"), now=NOW)
        self.assertEqual(errors, ["El enlace debe comenzar con http:// o https://."])

    def test_invalid_date_format(self):
        _, errors = validate_community_event(_valid(event_at="mañana"), now=NOW)
        self.assertIn("La fecha de la actividad no es válida.", errors)

    def test_nonexistent_hour_due_to_daylight_saving(self):
        # El 8 de septiembre de 2030 Chile adelanta los relojes a medianoche.
        _, errors = validate_community_event(
            _valid(event_at="2030-09-10T10:00", notice_at="2030-09-08T00:30"),
            now=NOW,
        )
        self.assertEqual(
            errors,
            ["La hora del aviso no existe por el cambio de horario en Chile."],
        )


class TemplateParametersTests(unittest.TestCase):
    def test_parameters_follow_template_order(self):
        event = {
            "title": "Feria de\nemprendedores",
            "category": "feria",
            "event_at": "2030-03-20T13:00:00+00:00",
            "location": "Plaza   de Recoleta",
        }

        parameters = build_notice_template_parameters(event, "InnovaRecoleta")

        self.assertEqual(parameters[0], "InnovaRecoleta")
        # Meta rechaza saltos de línea y espacios repetidos en los parámetros.
        self.assertEqual(parameters[1], "Feria de emprendedores")
        self.assertEqual(parameters[2], "Feria")
        self.assertEqual(parameters[3], "20 de marzo de 2030 a las 10:00")
        self.assertEqual(parameters[4], "Plaza de Recoleta")

    def test_place_falls_back_to_link_and_then_to_default_text(self):
        event = {"title": "Charla", "category": "charla", "event_at": "2030-03-20T13:00:00+00:00"}

        self.assertEqual(build_notice_template_parameters(event, "X")[4], NOTICE_PLACE_FALLBACK)
        event["link"] = "https://meet.example.com/abc"
        self.assertEqual(
            build_notice_template_parameters(event, "X")[4],
            "https://meet.example.com/abc",
        )
        self.assertTrue(all(build_notice_template_parameters(event, "")))


class _FakeQuery:
    """Imita la cadena de postgrest y registra los filtros aplicados."""

    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = []
        client.queries.append(self)

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.filters.append((name, args))
            return self
        return method

    @property
    def not_(self):
        self.filters.append(("not", ()))
        return self

    def execute(self):
        return SimpleNamespace(data=self.client.data[self.table], count=None)


class _FakeClient:
    def __init__(self, data):
        self.data = data
        self.queries = []

    def table(self, name):
        return _FakeQuery(self, name)


class PendingRecipientsTests(unittest.TestCase):
    def test_filters_by_comuna_consent_and_previous_deliveries(self):
        client = _FakeClient({
            "community_event_deliveries": [
                {"user_id": "sent-user", "delivery_status": "sent", "attempt_count": 1},
                {"user_id": "retry-user", "delivery_status": "failed", "attempt_count": 1},
                {"user_id": "exhausted-user", "delivery_status": "failed", "attempt_count": 3},
            ],
            "users": [
                {"id": "sent-user", "phone": "+56911111111"},
                {"id": "retry-user", "phone": "+56922222222"},
                {"id": "exhausted-user", "phone": "+56933333333"},
                {"id": "new-user", "phone": "+56944444444"},
            ],
        })

        with patch.object(db_community, "_admin_client", return_value=client):
            recipients = db_community.get_pending_recipients(
                {"id": "event-1", "comuna": "Recoleta"},
                limit=10,
            )

        self.assertEqual(
            [recipient["user_id"] for recipient in recipients],
            ["retry-user", "new-user"],
        )
        users_query = next(query for query in client.queries if query.table == "users")
        self.assertIn(("eq", ("comuna", "Recoleta")), users_query.filters)
        self.assertIn(("eq", ("reminders_enabled", True)), users_query.filters)
        self.assertIn(("eq", ("bloqueado", False)), users_query.filters)
        self.assertIn(("eq", ("onboarding_step", "done")), users_query.filters)

    def test_respects_the_limit(self):
        client = _FakeClient({
            "community_event_deliveries": [],
            "users": [{"id": f"user-{i}", "phone": f"+569000000{i:02d}"} for i in range(5)],
        })

        with patch.object(db_community, "_admin_client", return_value=client):
            recipients = db_community.get_pending_recipients(
                {"id": "event-1", "comuna": "Recoleta"},
                limit=2,
            )

        self.assertEqual(len(recipients), 2)


class CategoriesTests(unittest.TestCase):
    def test_categories_match_the_database_check(self):
        self.assertEqual(
            [slug for slug, _ in community_events.CATEGORIES],
            ["capacitacion", "taller", "feria", "fondos", "charla", "networking", "otra"],
        )


if __name__ == "__main__":
    unittest.main()
