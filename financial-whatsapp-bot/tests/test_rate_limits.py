import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db.rate_limits import (
    check_message_rate_limit,
    is_rate_limit_exempt,
)


class RateLimitTests(unittest.TestCase):
    @patch("db.rate_limits.RATE_LIMIT_ENABLED", False)
    def test_disabled_rate_limit_allows_message(self):
        fake_dependencies = SimpleNamespace(supabase_admin=None)
        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = check_message_rate_limit("+56911111111")

        self.assertTrue(result["allowed"])
        self.assertFalse(result["notify_user"])
        self.assertEqual(result["retry_after_seconds"], 0)

    @patch("db.rate_limits.RATE_LIMIT_BLOCK_SECONDS", 60)
    @patch("db.rate_limits.RATE_LIMIT_WINDOW_SECONDS", 60)
    @patch("db.rate_limits.RATE_LIMIT_MAX_MESSAGES", 10)
    @patch("db.rate_limits.RATE_LIMIT_ENABLED", True)
    def test_allowed_decision_uses_configured_rpc_parameters(self):
        supabase_admin = MagicMock()
        supabase_admin.rpc.return_value.execute.return_value = SimpleNamespace(
            data=[{
                "allowed": True,
                "notify_user": False,
                "retry_after_seconds": 0,
            }]
        )

        fake_dependencies = SimpleNamespace(supabase_admin=supabase_admin)
        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = check_message_rate_limit("+56911111111")

        self.assertTrue(result["allowed"])
        supabase_admin.rpc.assert_called_once_with(
            "check_message_rate_limit",
            {
                "p_phone": "+56911111111",
                "p_max_messages": 10,
                "p_window_seconds": 60,
                "p_block_seconds": 60,
            },
        )

    @patch("db.rate_limits.RATE_LIMIT_ENABLED", True)
    def test_first_blocked_message_requests_notification(self):
        supabase_admin = MagicMock()
        supabase_admin.rpc.return_value.execute.return_value = SimpleNamespace(
            data=[{
                "allowed": False,
                "notify_user": True,
                "retry_after_seconds": 60,
            }]
        )

        fake_dependencies = SimpleNamespace(supabase_admin=supabase_admin)
        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = check_message_rate_limit("+56911111111")

        self.assertFalse(result["allowed"])
        self.assertTrue(result["notify_user"])
        self.assertEqual(result["retry_after_seconds"], 60)

    @patch("db.rate_limits.RATE_LIMIT_ENABLED", True)
    def test_repeated_block_does_not_notify_again(self):
        supabase_admin = MagicMock()
        supabase_admin.rpc.return_value.execute.return_value = SimpleNamespace(
            data=[{
                "allowed": False,
                "notify_user": False,
                "retry_after_seconds": 35,
            }]
        )

        fake_dependencies = SimpleNamespace(supabase_admin=supabase_admin)
        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = check_message_rate_limit("+56911111111")

        self.assertFalse(result["allowed"])
        self.assertFalse(result["notify_user"])
        self.assertEqual(result["retry_after_seconds"], 35)

    @patch("db.rate_limits.RATE_LIMIT_ENABLED", True)
    def test_supabase_error_fails_open(self):
        supabase_admin = MagicMock()
        supabase_admin.rpc.side_effect = RuntimeError("Supabase unavailable")

        fake_dependencies = SimpleNamespace(supabase_admin=supabase_admin)
        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = check_message_rate_limit("+56911111111")

        self.assertTrue(result["allowed"])
        self.assertFalse(result["notify_user"])

    def test_opt_out_commands_are_exempt(self):
        self.assertTrue(is_rate_limit_exempt("pausar recordatorios"))
        self.assertTrue(is_rate_limit_exempt("No quiero recordatorios."))
        self.assertTrue(is_rate_limit_exempt("menu_recordatorios_off"))
        self.assertFalse(is_rate_limit_exempt("mi roadmap"))


if __name__ == "__main__":
    unittest.main()
