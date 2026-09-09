import logging
import secrets

from fastapi import APIRouter, Header, HTTPException

from config import CRON_SECRET
from services.calendar_reminders import send_due_calendar_reminders
from services.reminders import send_due_reminders
from services.alertas_tributarias import send_tax_alerts

router = APIRouter(prefix="/internal/reminders")
logger = logging.getLogger("financial")


def validate_cron_authorization(authorization: str | None) -> None:
    """Valida la credencial compartida por Vercel y las pruebas locales."""
    if not CRON_SECRET:
        raise HTTPException(
            status_code=503,
            detail="CRON_SECRET no está configurado",
        )

    expected = f"Bearer {CRON_SECRET}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Credencial de cron inválida")


@router.get("/run", include_in_schema=False)
@router.post("/run", include_in_schema=False)
async def run_reminders(
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    """Ejecuta todos los recordatorios desde un único endpoint protegido."""
    validate_cron_authorization(authorization)
    result = {}
    errors = {}
    services = (
        ("roadmap", send_due_reminders),
        ("alerts", send_tax_alerts),
        ("calendar", send_due_calendar_reminders),
    )
    for name, service in services:
        try:
            result.update(await service())
        except Exception as error:
            logger.exception("Falló el servicio de recordatorios %s", name)
            errors[name] = str(error)

    if errors:
        result["errors"] = errors
        result["status"] = "partial_failure"
    return result
