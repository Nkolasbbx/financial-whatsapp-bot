import unittest
from unittest.mock import patch

from core.financial_flow import FINANCIAL_CONFIRM_ID, FINANCIAL_PARSE_TASK
from services.message_router import route_message


class FinancialRoutingTests(unittest.TestCase):
    def setUp(self):
        self.user = {
            "id": "user-1",
            "phone": "+56911111111",
            "onboarding_step": "done",
            "reminder_count": 0,
        }

    @patch("services.message_router.cancel_fund_session")
    @patch("services.message_router.clear_calendar_session")
    @patch("services.message_router.get_financial_session", return_value=None)
    @patch("services.message_router.get_user")
    def test_natural_movement_is_prioritized_before_other_flows(
        self,
        get_user_mock,
        _get_session_mock,
        clear_calendar_mock,
        cancel_fund_mock,
    ):
        get_user_mock.return_value = self.user

        result = route_message(
            self.user["phone"],
            "hoy vendí $40.000 en empanadas",
        )

        self.assertEqual(result, FINANCIAL_PARSE_TASK)
        clear_calendar_mock.assert_called_once_with("user-1")
        cancel_fund_mock.assert_called_once_with("user-1")

    @patch("services.message_router.handle_financial_message")
    @patch("services.message_router.get_financial_session")
    @patch("services.message_router.get_user")
    def test_financial_button_uses_existing_session(
        self,
        get_user_mock,
        get_session_mock,
        handle_mock,
    ):
        get_user_mock.return_value = self.user
        session = {"state": "confirming_creation", "draft": {}}
        get_session_mock.return_value = session
        handle_mock.return_value = {"type": "text", "body": "confirmado"}

        result = route_message(self.user["phone"], FINANCIAL_CONFIRM_ID)

        self.assertEqual(result["body"], "confirmado")
        handle_mock.assert_called_once_with(
            self.user,
            FINANCIAL_CONFIRM_ID,
            session,
        )


if __name__ == "__main__":
    unittest.main()

