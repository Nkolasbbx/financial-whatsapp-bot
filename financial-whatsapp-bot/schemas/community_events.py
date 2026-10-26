from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class CommunityEventRequest(BaseModel):
    """Datos del formulario de convocatoria del panel admin.

    Los campos son laxos a propósito: la validación y sus mensajes en
    español los genera core/community_events.validate_community_event, que
    informa todos los errores juntos. La comuna no se recibe: se toma
    siempre de la cuenta admin (un `comuna` en el body se ignora).
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    category: str | None = None
    description: str | None = None
    event_at: str | None = None
    notice_at: str | None = None
    location: str | None = None
    link: str | None = None


class DeliverySummary(BaseModel):
    sent: int = 0
    failed: int = 0
    pending: int = 0


NoticeStatus = Literal["programado", "enviando", "enviado", "cancelado"]


class CommunityEventAdminResponse(BaseModel):
    id: str
    title: str
    category: str
    category_label: str
    description: str
    location: str | None = None
    link: str | None = None
    comuna: str
    created_by: str
    event_at: datetime
    notice_at: datetime
    status: Literal["scheduled", "cancelled"]
    notice_status: NoticeStatus
    deliveries: DeliverySummary
    google_sync_status: str
    editable: bool
    cancellable: bool
