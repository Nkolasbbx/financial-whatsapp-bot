from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class CostAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=120)
    mode: Literal["manual", "estimate", "pending", "excluded"]
    amount: StrictInt | None = Field(default=None, ge=0, le=9_000_000_000_000)
    paid: StrictInt = Field(default=0, ge=0, le=9_000_000_000_000)
    frequency: Literal["once", "monthly", "annual"] | None = None
    due_date: date | None = None
    included: bool = False
    origin: Literal["informed", "quote", "own"] = "informed"

    @model_validator(mode="after")
    def valid_manual(self):
        if self.mode == "manual" and (self.amount is None or self.frequency is None):
            raise ValueError("Indica el monto y la frecuencia")
        if self.mode == "manual" and self.paid > self.amount:
            raise ValueError("Lo pagado no puede superar el total")
        return self


class DiagnosticAdjustments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagnostic_id: UUID
    lock_version: StrictInt = Field(ge=0)
    request_id: UUID
    changes: list[CostAdjustment] = Field(max_length=80)

    @model_validator(mode="after")
    def unique_keys(self):
        keys = [item.key for item in self.changes]
        if len(set(keys)) != len(keys):
            raise ValueError("No repitas un costo")
        return self
