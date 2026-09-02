import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db.alertas import get_users_for_fund_alerts


class FundAlertDatabaseTests(unittest.TestCase):
    def test_only_users_with_pending_roadmap_are_returned(self):
        client = MagicMock()
        query = client.table.return_value
        selected = query.select.return_value
        not_formalized = selected.eq.return_value
        onboarding_done = not_formalized.eq.return_value
        roadmap_not_completed = onboarding_done.is_.return_value
        roadmap_not_completed.execute.return_value = SimpleNamespace(
            data=[
                {
                    "id": "pending-user",
                    "roadmap": [
                        {"title": "Primer hito", "done": True},
                        {"title": "Segundo hito", "done": False},
                    ],
                    "roadmap_completed_at": None,
                },
                {
                    "id": "completed-user",
                    "roadmap": [{"title": "Único hito", "done": True}],
                    "roadmap_completed_at": None,
                },
                {
                    "id": "empty-roadmap-user",
                    "roadmap": [],
                    "roadmap_completed_at": None,
                },
                {
                    "id": "invalid-roadmap-user",
                    "roadmap": None,
                    "roadmap_completed_at": None,
                },
            ]
        )
        fake_dependencies = SimpleNamespace(supabase_admin=client)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = get_users_for_fund_alerts()

        self.assertEqual([user["id"] for user in result], ["pending-user"])
        client.table.assert_called_once_with("users")
        onboarding_done.is_.assert_called_once_with(
            "roadmap_completed_at",
            "null",
        )


if __name__ == "__main__":
    unittest.main()
