import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from routers import portal_roadmap


async def _run_immediately(function, *args, **kwargs):
    return function(*args, **kwargs)


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=object())),
    )


def _user(done_flags):
    return {
        "id": "session-user",
        "phone": "56911111111",
        "inicio_sii": "no",
        "roadmap": [
            {"id": index, "title": f"Hito {index}", "desc": "", "done": done}
            for index, done in enumerate(done_flags, start=1)
        ],
    }


class PortalRoadmapTests(unittest.IsolatedAsyncioTestCase):
    def _patches(self, user, save_mock):
        return (
            patch.object(
                portal_roadmap,
                "_authenticated_user",
                new=AsyncMock(return_value=user),
            ),
            patch.object(portal_roadmap, "_require_csrf", new=AsyncMock()),
            patch.object(portal_roadmap, "save_user", new=save_mock),
            patch.object(
                portal_roadmap,
                "run_in_threadpool",
                new=_run_immediately,
            ),
        )

    async def test_missing_session_is_rejected(self):
        with patch.object(
            portal_roadmap,
            "get_session_phone",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as context:
                await portal_roadmap._authenticated_user(_request(), None)

        self.assertEqual(context.exception.status_code, 401)

    async def test_invalid_csrf_is_rejected(self):
        with patch.object(
            portal_roadmap,
            "validate_csrf_token",
            new=AsyncMock(return_value=False),
        ):
            with self.assertRaises(HTTPException) as context:
                await portal_roadmap._require_csrf(_request(), "session", "bad")

        self.assertEqual(context.exception.status_code, 403)

    async def test_marks_pending_milestone_as_done(self):
        user = _user([True, False, False])
        save_mock = Mock()
        a, b, c, d = self._patches(user, save_mock)
        with a, b, c, d:
            result = await portal_roadmap.complete_milestone(
                2,
                _request(),
                financial_session="session-id",
                csrf_token="csrf",
            )

        self.assertTrue(user["roadmap"][1]["done"])
        self.assertEqual(save_mock.call_args.args[0], "56911111111")
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["percentage"], 67)
        self.assertEqual(result["title"], "Hito 2")

    async def test_rejects_milestone_that_is_not_the_pending_one(self):
        user = _user([True, False, False])
        original = copy.deepcopy(user)
        save_mock = Mock()
        a, b, c, d = self._patches(user, save_mock)
        with a, b, c, d:
            with self.assertRaises(HTTPException) as context:
                await portal_roadmap.complete_milestone(
                    3,
                    _request(),
                    financial_session="session-id",
                    csrf_token="csrf",
                )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(user, original)
        save_mock.assert_not_called()

    async def test_last_milestone_marks_user_as_formalized(self):
        user = _user([True, True, False])
        save_mock = Mock()
        a, b, c, d = self._patches(user, save_mock)
        with a, b, c, d:
            result = await portal_roadmap.complete_milestone(
                3,
                _request(),
                financial_session="session-id",
                csrf_token="csrf",
            )

        self.assertTrue(result["formalized"])
        self.assertEqual(user["inicio_sii"], "si")
        self.assertEqual(user["roadmap"], [])

    async def test_undo_reverts_last_completed_milestone(self):
        user = _user([True, True, False])
        save_mock = Mock()
        a, b, c, d = self._patches(user, save_mock)
        with a, b, c, d:
            result = await portal_roadmap.undo_last_milestone(
                _request(),
                financial_session="session-id",
                csrf_token="csrf",
            )

        self.assertFalse(user["roadmap"][1]["done"])
        self.assertEqual(result["title"], "Hito 2")
        self.assertEqual(result["completed"], 1)
        save_mock.assert_called_once()

    async def test_undo_without_completed_milestones_is_rejected(self):
        user = _user([False, False])
        save_mock = Mock()
        a, b, c, d = self._patches(user, save_mock)
        with a, b, c, d:
            with self.assertRaises(HTTPException) as context:
                await portal_roadmap.undo_last_milestone(
                    _request(),
                    financial_session="session-id",
                    csrf_token="csrf",
                )

        self.assertEqual(context.exception.status_code, 409)
        save_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
