"""Fixture visual sin red ni credenciales: HTML real y diagnóstico ficticio."""
import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / '.venv' / 'Lib' / 'site-packages'))
deps = types.ModuleType('dependencies')
deps.supabase = deps.supabase_admin = deps.db_pool = None
sys.modules['dependencies'] = deps

from tests.test_formalization_short_flow import ShortFlowTests
from tests.test_formalization_estimates import real_catalog
from services.portal_diagnostic import view
from routers import portal


async def render():
    case = ShortFlowTests(); case.setUp()
    try:
        try:
            case.catalog['document'] = real_catalog()
        except Exception:
            pass  # Synthetic catalogue remains portable when ignored seed is absent.
        case.begin(); case.finish()
        request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(redis=object())))
        with patch.object(portal, 'get_session_phone', AsyncMock(return_value='fake-phone')), patch.object(portal, 'get_user', return_value={'id': 'fake-user'}), patch.object(portal, 'get_or_create_csrf_token', AsyncMock(return_value='test-csrf')):
            page = await portal.panel(request, tab='diagnostico', financial_session='fake-session')
        print(json.dumps({'html': page.body.decode(), 'data': view(case.repo.get())}, ensure_ascii=True))
    finally:
        case.doCleanups()


if __name__ == '__main__':
    asyncio.run(render())
