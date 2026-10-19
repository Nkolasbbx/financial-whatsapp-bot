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
from services import portal_diagnostic as service
from schemas.portal_diagnostic import DiagnosticAdjustments
from datetime import date
from uuid import uuid4


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
        initial = view(case.repo.get())
        changes = [dict(key=item['key'], mode='manual', amount=1000, paid=0,
                        frequency=item['frequency'] if item['frequency'] in {'once', 'monthly', 'annual'} else 'once',
                        due_date='2026-11-15') for item in initial['items'] if item['mode'] == 'pending']
        payload = DiagnosticAdjustments(diagnostic_id=initial['diagnostic_id'], lock_version=initial['lock_version'],
                                        request_id=uuid4(), changes=changes, coverage_confirmed=True)
        with patch.object(service, 'get_formalization_context', side_effect=case.repo.get), patch.object(service, 'save_formalization_draft', side_effect=case.repo.save), patch.object(service, 'confirm_formalization_revision', side_effect=case.repo.confirm), patch.object(service, 'today', return_value=date(2026, 10, 18)):
            completed = service.update(case.user['id'], payload)
        print(json.dumps({'html': page.body.decode(), 'data': initial, 'completed': completed}, ensure_ascii=True))
    finally:
        case.doCleanups()


if __name__ == '__main__':
    asyncio.run(render())
