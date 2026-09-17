"""Esquemas de validación para ingresos y gastos conversacionales (HdU13)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MovementType = Literal["income", "expense"]
MovementStatus = Literal["confirmed", "deleted"]
FinancialSessionState = Literal[
    "waiting_missing_data",
    "confirming_creation",
    "choosing_correction",
    "waiting_replacement",
    "confirming_update",
    "confirming_delete",
]

INCOME_CATEGORIES = frozenset(
    {
        "ventas",
        "servicios",
        "aportes_capital",
        "otros_ingresos",
    }
)

EXPENSE_CATEGORIES = frozenset(
    {
        "insumos_mercaderia",
        "transporte",
        "arriendo_servicios",
        "permisos_tramites",
        "marketing",
        "equipamiento",
        "otros_gastos",
    }
)

FINANCIAL_CATEGORIES = INCOME_CATEGORIES | EXPENSE_CATEGORIES
REQUIRED_DRAFT_FIELDS = (
    "movement_type",
    "amount",
    "category",
    "description",
    "occurred_on",
)


def _validate_category_for_type(
    movement_type: MovementType | None,
    category: str | None,
) -> None:
    if movement_type is None or category is None:
        return

    allowed = (
        INCOME_CATEGORIES
        if movement_type == "income"
        else EXPENSE_CATEGORIES
    )
    if category not in allowed:
        raise ValueError(
            f"La categoría '{category}' no corresponde a un movimiento "
            f"de tipo '{movement_type}'"
        )


class FinancialMovementDraft(BaseModel):
    """Movimiento parcial extraído desde un mensaje del usuario."""

    model_config = ConfigDict(extra="forbid")

    movement_type: MovementType | None = None
    amount: int | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    occurred_on: date | None = None
    original_text: str | None = Field(default=None, max_length=2000)

    @field_validator("category", "description", "original_text")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("category")
    @classmethod
    def validate_known_category(cls, value: str | None) -> str | None:
        if value is not None and value not in FINANCIAL_CATEGORIES:
            raise ValueError(f"Categoría financiera desconocida: {value}")
        return value

    @model_validator(mode="after")
    def validate_category_matches_type(self):
        _validate_category_for_type(self.movement_type, self.category)
        return self

    def required_missing_fields(self) -> list[str]:
        """Retorna los campos obligatorios que aún no están disponibles."""
        return [
            field_name
            for field_name in REQUIRED_DRAFT_FIELDS
            if getattr(self, field_name) is None
        ]


class FinancialMovementExtraction(FinancialMovementDraft):
    """Resultado estructurado producido por reglas o por el modelo de IA."""

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_fields: list[str] = Field(default_factory=list)

    @field_validator("missing_fields")
    @classmethod
    def normalize_missing_fields(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            field_name = str(value).strip()
            if field_name and field_name not in normalized:
                normalized.append(field_name)
        return normalized

    @model_validator(mode="after")
    def include_required_missing_fields(self):
        for field_name in self.required_missing_fields():
            if field_name not in self.missing_fields:
                self.missing_fields.append(field_name)
        return self

    def draft_payload(self) -> dict:
        """Entrega solo los campos que se almacenan en el borrador JSONB."""
        return self.model_dump(
            mode="json",
            exclude={"confidence", "missing_fields"},
            exclude_none=True,
        )


class FinancialMovementConfirm(BaseModel):
    """Datos completos requeridos para confirmar o editar un movimiento."""

    model_config = ConfigDict(extra="forbid")

    movement_type: MovementType
    amount: int = Field(gt=0)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    occurred_on: date
    original_text: str | None = Field(default=None, max_length=2000)
    target_movement_id: str | None = None

    @field_validator("category", "description")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("El texto no puede estar vacío")
        return normalized

    @field_validator("original_text")
    @classmethod
    def strip_original_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("category")
    @classmethod
    def validate_known_category(cls, value: str) -> str:
        if value not in FINANCIAL_CATEGORIES:
            raise ValueError(f"Categoría financiera desconocida: {value}")
        return value

    @model_validator(mode="after")
    def validate_category_matches_type(self):
        _validate_category_for_type(self.movement_type, self.category)
        return self


class FinancialMovement(BaseModel):
    """Representación de un movimiento persistido en Supabase."""

    model_config = ConfigDict(extra="ignore")

    id: str
    user_id: str
    movement_type: MovementType
    amount: int = Field(gt=0)
    currency: Literal["CLP"] = "CLP"
    category: str
    description: str
    occurred_on: date
    original_text: str | None = None
    status: MovementStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def validate_persisted_state(self):
        _validate_category_for_type(self.movement_type, self.category)
        if self.status == "confirmed" and self.deleted_at is not None:
            raise ValueError(
                "Un movimiento confirmado no puede tener deleted_at"
            )
        if self.status == "deleted" and self.deleted_at is None:
            raise ValueError(
                "Un movimiento eliminado debe tener deleted_at"
            )
        return self


class FinancialMovementSession(BaseModel):
    """Sesión conversacional persistida mientras se completa un movimiento."""

    model_config = ConfigDict(extra="ignore")

    user_id: str
    state: FinancialSessionState
    draft: dict = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    target_movement_id: str | None = None
    created_at: datetime
    updated_at: datetime


class FinancialCategorySummary(BaseModel):
    category: str
    total: int = Field(ge=0)


class FinancialMonthSummary(BaseModel):
    """Totales calculados por la función SQL del resumen mensual."""

    model_config = ConfigDict(extra="ignore")

    month_start: date
    month_end: date
    income_total: int = Field(ge=0)
    expense_total: int = Field(ge=0)
    net_total: int
    movement_count: int = Field(ge=0)
    income_categories: list[FinancialCategorySummary] = Field(
        default_factory=list
    )
    expense_categories: list[FinancialCategorySummary] = Field(
        default_factory=list
    )

