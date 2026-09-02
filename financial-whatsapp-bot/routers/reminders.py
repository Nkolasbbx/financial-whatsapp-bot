import secrets

from fastapi import APIRouter, Header, HTTPException

from config import CRON_SECRET
from services.reminders import send_due_reminders
from services.alertas_tributarias import send_tax_alerts

router = APIRouter(prefix="/internal/reminders")


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
    """Ejecuta recordatorios y alertas tributarias."""
    validate_cron_authorization(authorization)
    reminders_result = await send_due_reminders()
    alerts_result = await send_tax_alerts()
    return {**reminders_result, **alerts_result}