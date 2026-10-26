import unittest
from unittest.mock import AsyncMock, patch

from services import community_notices

EVENT = {
    "id": "event-1",
    "comuna": "Recoleta",
    "created_by": "InnovaRecoleta",
    "title": "Taller de marketing digital",
    "category": "taller",
    "event_at": "2099-09-20T13:00:00+00:00",
    "notice_at": "2099-09-19T13:00:00+00:00",
    "location": "Centro de Emprendimiento",
}


@patch.object(community_notices, "COMMUNITY_NOTICES_ENABLED", True)
@patch.object(community_notices, "mark_community_event_notified")
@patch.object(community_notices, "mark_community_delivery_failed")
@patch.object(community_notices, "mark_community_delivery_sent")
@patch.object(community_notices, "reserve_community_delivery")
@patch.object(community_notices, "get_pending_recipients")
@patch.object(community_notices, "get_due_community_events")
@patch.object(community_notices, "send_template", new_callable=AsyncMock)
class CommunityNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_template_to_each_recipient_and_closes_the_notice(
        self, send_mock, due_mock, recipients_mock, reserve_mock, sent_mock, failed_mock, notified_mock,
    ):
        due_mock.return_value = [EVENT]
        recipients_mock.side_effect = [
            [
                {"user_id": "user-1", "phone": "+56911111111"},
                {"user_id": "user-2", "phone": "+56922222222"},
            ],
            [],  # después del envío ya no queda nadie pendiente
        ]
        reserve_mock.side_effect = ["delivery-1", "delivery-2"]
        send_mock.side_effect = [
            {"messages": [{"id": "wamid.1"}]},
            {"messages": [{"id": "wamid.2"}]},
        ]

        result = await community_notices.send_due_community_notices()

        self.assertEqual(result["community_sent"], 2)
        self.assertEqual(result["community_completed"], 1)
        sent_mock.assert_any_call("delivery-1", "wamid.1")
        sent_mock.assert_any_call("delivery-2", "wamid.2")
        notified_mock.assert_called_once_with("event-1")
        phone, template, language, parameters = send_mock.await_args_list[0].args
        self.assertEqual(phone, "+56911111111")
        self.assertEqual(template, community_notices.COMMUNITY_TEMPLATE_NAME)
        self.assertEqual(parameters[0], "InnovaRecoleta")
        self.assertEqual(parameters[1], "Taller de marketing digital")

    async def test_duplicate_reservation_is_skipped(
        self, send_mock, due_mock, recipients_mock, reserve_mock, sent_mock, failed_mock, notified_mock,
    ):
        due_mock.return_value = [EVENT]
        recipients_mock.side_effect = [[{"user_id": "user-1", "phone": "+56911111111"}], []]
        reserve_mock.return_value = None

        result = await community_notices.send_due_community_notices()

        self.assertEqual(result["community_skipped"], 1)
        send_mock.assert_not_awaited()

    async def test_meta_failure_is_recorded_without_stopping_the_batch(
        self, send_mock, due_mock, recipients_mock, reserve_mock, sent_mock, failed_mock, notified_mock,
    ):
        due_mock.return_value = [EVENT]
        recipients_mock.side_effect = [
            [
                {"user_id": "user-1", "phone": "+56911111111"},
                {"user_id": "user-2", "phone": "+56922222222"},
            ],
            [{"user_id": "user-1", "phone": "+56911111111"}],  # el fallido se reintentará
        ]
        reserve_mock.side_effect = ["delivery-1", "delivery-2"]
        send_mock.side_effect = [
            RuntimeError("plantilla no aprobada"),
            {"messages": [{"id": "wamid.2"}]},
        ]

        result = await community_notices.send_due_community_notices()

        self.assertEqual(result["community_failed"], 1)
        self.assertEqual(result["community_sent"], 1)
        failed_mock.assert_called_once_with("delivery-1", "plantilla no aprobada")
        # Queda un pendiente con intentos disponibles: el aviso sigue abierto.
        notified_mock.assert_not_called()

    async def test_event_without_pending_recipients_is_marked_notified(
        self, send_mock, due_mock, recipients_mock, reserve_mock, sent_mock, failed_mock, notified_mock,
    ):
        due_mock.return_value = [EVENT]
        recipients_mock.return_value = []

        result = await community_notices.send_due_community_notices()

        self.assertEqual(result["community_completed"], 1)
        notified_mock.assert_called_once_with("event-1")
        send_mock.assert_not_awaited()


class DisabledNoticesTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(community_notices, "COMMUNITY_NOTICES_ENABLED", False)
    async def test_disabled_flag_does_not_query_database(self):
        with patch.object(community_notices, "get_due_community_events") as due_mock:
            result = await community_notices.send_due_community_notices()

        self.assertEqual(result["community_status"], "disabled")
        due_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
