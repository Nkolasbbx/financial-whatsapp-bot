"""Contratos del diagnóstico HDU017. Compatibles con las tablas ya migradas.

Estos modelos validan almacenamiento, no calculan impuestos ni capacidad.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FormalizationDraftPatch(_Model):
    """Solo entradas editables. Los snapshots y resultados no vienen del usuario.

    Las claves omitidas se conservan; answers/rules_config, si se proporcionan,
    reemplazan el objeto completo. Una fecha individual se valida contra el
    periodo persistido al guardar.
    """

    workflow_status: Literal["draft", "ready", "cancelled", "archived"] = "draft"
    conversation_status: Literal["active", "paused", "closed"] = "paused"
    current_step: str | None = Field(default=None, min_length=1, max_length=80)
    pending_field: str | None = Field(default=None, min_length=1, max_length=120)
    period_start: date | None = None
    period_end: date | None = None
    projection_start: date | None = None
    activity_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    comuna_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    location_type: Literal["vivienda", "local", "otra"] | None = None
    answers: dict[str, Any] = Field(default_factory=dict)
    rules_version: str | None = Field(default=None, min_length=1, max_length=100)
    rules_config: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list, max_length=100)
    technical_status: Literal["not_reviewed", "pending_confirmation", "reviewed"] = "not_reviewed"

    @field_validator("missing_fields")
    @classmethod
    def clean_missing_fields(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Los campos faltantes no pueden estar vacíos")
        return list(dict.fromkeys(value.strip() for value in values))


class FormalizationItemInput(_Model):
    """Upsert completo por item_key; los IDs y el snapshot de catálogo son del backend."""

    item_key: str = Field(min_length=1, max_length=120)
    catalog_version_id: str | None = None
    item_kind: Literal[
        "current_operating", "future_operating", "initial_payment", "financing",
        "owner_withdrawal", "other_cashflow",
    ]
    economic_nature: Literal[
        "revenue", "expense", "investment", "guarantee", "capital_contribution",
        "loan_principal", "interest", "tax", "owner_withdrawal", "other",
    ]
    cash_direction: Literal["inflow", "outflow"]
    label: str = Field(min_length=1, max_length=200)
    applicability: Literal["applies", "not_applicable", "to_confirm"] = "to_confirm"
    applicability_reason: str | None = Field(default=None, max_length=2000)
    amount_original: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False,
    )
    unit: Literal["CLP", "UF", "UTM"] | None = None
    amount_clp_min: StrictInt | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    amount_clp_max: StrictInt | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    amount_paid_clp: StrictInt | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    amount_state: Literal["pending", "estimated", "quoted", "confirmed"] = "pending"
    source_type: Literal["catalog", "financial_movement", "user", "quote", "calculation"]
    frequency: Literal[
        "once", "monthly", "annual", "per_request", "variable", "to_confirm",
    ] = "to_confirm"
    due_date: date | None = None
    schedule: list[dict[str, Any]] = Field(default_factory=list, max_length=366)
    replaces_item_key: str | None = Field(default=None, min_length=1, max_length=120)
    included_in_baseline: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("catalog_version_id")
    @classmethod
    def normalize_catalog_id(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None

    @model_validator(mode="after")
    def validate_amounts(self):
        low, high = self.amount_clp_min, self.amount_clp_max
        if (low is None) != (high is None):
            raise ValueError("Indica ambos extremos del rango o deja ambos pendientes")
        if low is not None and high < low:
            raise ValueError("El máximo no puede ser menor que el mínimo")
        if self.amount_original is not None and self.unit is None:
            raise ValueError("El importe original requiere unidad")
        if self.amount_state != "pending" and low is None:
            raise ValueError("Un importe estimado/cotizado/confirmado requiere monto")
        if high is not None and self.amount_paid_clp is not None and self.amount_paid_clp > high:
            raise ValueError("El abono no puede superar el importe máximo")
        if self.replaces_item_key == self.item_key:
            raise ValueError("Una partida no puede sustituirse a sí misma")
        if self.source_type == "catalog" and not self.catalog_version_id:
            raise ValueError("Una partida de catálogo requiere su versión")
        return self


class FormalizationAssessmentInput(_Model):
    """Resultado producido por el motor determinista, nunca por un mensaje libre."""

    assessment_status: Literal["partial", "complete", "not_evaluable"]
    capacity: Literal["alta", "media", "baja"] | None = None
    result: dict[str, Any]
    missing_fields: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_capacity(self):
        if self.assessment_status == "complete":
            if self.capacity is None or self.missing_fields:
                raise ValueError("Un diagnóstico completo requiere capacidad y ningún faltante")
        elif self.capacity is not None:
            raise ValueError("No se puede clasificar una evaluación parcial/no evaluable")
        if not self.result:
            raise ValueError("El resultado debe explicar la evaluación")
        if any(not value.strip() for value in self.missing_fields):
            raise ValueError("Los campos faltantes no pueden estar vacíos")
        self.missing_fields = list(dict.fromkeys(value.strip() for value in self.missing_fields))
        return self
