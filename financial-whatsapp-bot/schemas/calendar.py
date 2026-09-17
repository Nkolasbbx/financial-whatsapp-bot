from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ReminderDays = Literal[0, 1, 3, 7]
CalendarEventSource = Literal["personal", "tributaria", "fondo"]


class CalendarEventCreateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    event_at: datetime
    reminder_days_before: ReminderDays = 0


class CalendarEventUpdateRequest(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=500)
    event_at: datetime | None = None
    reminder_days_before: ReminderDays | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if (
            self.description is None
            and self.event_at is None
            and self.reminder_days_before is None
        ):
            raise ValueError("Debes indicar al menos un campo para actualizar")
        return self


class CalendarEventResponse(BaseModel):
    id: str
    description: str
    event_at: datetime
    reminder_at: datetime
    reminder_days_before: ReminderDays
    status: str
    source: CalendarEventSource = "personal"
    editable: bool = True
    all_day: bool = False
    details: str | None = None
    link: str | None = None
