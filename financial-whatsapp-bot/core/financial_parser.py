"""Extracción estructurada de ingresos y gastos escritos en lenguaje natural."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import IA_API_KEY, OLLAMA_MODEL, OLLAMA_URL, REMINDER_TIMEZONE
from schemas.financial_movements import (
    EXPENSE_CATEGORIES,
    FINANCIAL_CATEGORIES,
    INCOME_CATEGORIES,
    FinancialMovementExtraction,
)


_INCOME_PATTERNS = (
    r"\bvendi\b",
    r"\bcobre\b",
    r"\brecibi\b",
    r"\bme pagaron\b",
    r"\bfacture\b",
    r"\btuve (?:un )?ingreso\b",
    r"\bme entraron\b",
    r"\bingrese\b",
)

_EXPENSE_PATTERNS = (
    r"\bcompre\b",
    r"\bpague\b",
    r"\bgaste\b",
    r"\binverti\b",
    r"\babone\b",
    r"\btuve (?:un )?gasto\b",
    r"\bcancele\b",
)

_QUESTION_STARTERS = (
    "como ",
    "que ",
    "cuanto ",
    "cuantos ",
    "cuando ",
    "donde ",
    "puedo ",
    "debo ",
    "tengo que ",
)

_GENERIC_DESCRIPTION_WORDS = {
    "dinero",
    "plata",
    "peso",
    "pesos",
    "ingreso",
    "gasto",
    "venta",
    "compra",
}

_CATEGORY_KEYWORDS = {
    "ventas": (
        "vendi", "venta", "producto", "empanada", "comida", "pedido",
        "mercaderia", "ropa", "artesania",
    ),
    "servicios": (
        "servicio", "asesoria", "reparacion", "trabajo", "atencion",
        "consulta", "instalacion",
    ),
    "aportes_capital": (
        "aporte", "capital", "socio", "ahorro personal",
    ),
    "insumos_mercaderia": (
        "insumo", "harina", "aceite", "ingrediente", "mercaderia",
        "material", "materia prima", "envase", "embalaje", "stock",
    ),
    "transporte": (
        "transporte", "bencina", "combustible", "pasaje", "flete",
        "delivery", "estacionamiento", "peaje",
    ),
    "arriendo_servicios": (
        "arriendo", "agua", "luz", "electricidad", "gas", "internet",
        "telefono", "cuenta",
    ),
    "permisos_tramites": (
        "permiso", "patente", "tramite", "municipalidad", "seremi",
        "notaria", "certificado",
    ),
    "marketing": (
        "publicidad", "marketing", "anuncio", "instagram", "volante",
        "promocion",
    ),
    "equipamiento": (
        "maquina", "equipo", "horno", "refrigerador", "computador",
        "herramienta", "mueble",
    ),
}


def normalize_financial_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    without_accents = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())


def _local_today(now: datetime | None = None) -> date:
    try:
        local_tz = ZoneInfo(REMINDER_TIMEZONE)
    except Exception:
        local_tz = ZoneInfo("UTC")

    current = now or datetime.now(local_tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=local_tz)
    return current.astimezone(local_tz).date()


def detect_movement_type(message: str) -> str | None:
    normalized = normalize_financial_text(message)
    income = any(re.search(pattern, normalized) for pattern in _INCOME_PATTERNS)
    expense = any(re.search(pattern, normalized) for pattern in _EXPENSE_PATTERNS)
    if income == expense:
        return None
    return "income" if income else "expense"


def looks_like_financial_movement(message: str) -> bool:
    """Detección conservadora para no convertir preguntas en movimientos."""
    raw = (message or "").strip()
    normalized = normalize_financial_text(raw)
    if not normalized:
        return False
    if "?" in raw or normalized.startswith(_QUESTION_STARTERS):
        return False
    return detect_movement_type(raw) is not None


def _parse_number_token(token: str) -> float | None:
    compact = token.strip().replace(" ", "")
    if not compact:
        return None

    if "," in compact:
        compact = compact.replace(".", "").replace(",", ".")
    elif compact.count(".") > 1:
        compact = compact.replace(".", "")
    elif "." in compact:
        left, right = compact.split(".", 1)
        if len(right) == 3:
            compact = left + right

    try:
        return float(compact)
    except ValueError:
        return None


def parse_chilean_amount(message: str) -> int | None:
    """Extrae un monto CLP sin confundir cantidades pequeñas con dinero."""
    normalized = normalize_financial_text(message)
    patterns = (
        (r"\$\s*([0-9][0-9. ]*(?:,[0-9]+)?)", 1),
        (r"\b([0-9]+(?:[.,][0-9]+)?)\s*lucas?\b", 1000),
        (r"\b([0-9]+(?:[.,][0-9]+)?)\s*mil(?:\s+pesos?)?\b", 1000),
        (r"\b([0-9][0-9.]*)\s*(?:pesos?|clp)\b", 1),
    )
    for pattern, multiplier in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        parsed = _parse_number_token(match.group(1))
        if parsed is not None and parsed > 0:
            return int(round(parsed * multiplier))

    # Sin símbolo ni unidad solo se considera dinero una cifra >= 1.000.
    for match in re.finditer(r"(?<![/\d])([0-9]{4,}|[0-9]{1,3}(?:\.[0-9]{3})+)(?![/\d])", normalized):
        parsed = _parse_number_token(match.group(1))
        if parsed is not None and parsed >= 1000:
            return int(parsed)
    return None


def _has_money_signal(message: str) -> bool:
    normalized = normalize_financial_text(message)
    return parse_chilean_amount(message) is not None or bool(
        re.search(r"\b(?:pesos?|clp|lucas?|mil pesos?)\b", normalized)
        or "$" in message
    )


def _extract_occurred_on(message: str, now: datetime | None = None) -> date:
    normalized = normalize_financial_text(message)
    today = _local_today(now)
    if re.search(r"\bayer\b", normalized):
        return today - timedelta(days=1)

    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", normalized)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            pass
    return today


def _remove_money_expressions(value: str) -> str:
    patterns = (
        r"\$\s*[0-9][0-9. ]*(?:,[0-9]+)?",
        r"\b[0-9]+(?:[.,][0-9]+)?\s*lucas?\b",
        r"\b[0-9]+(?:[.,][0-9]+)?\s*mil(?:\s+pesos?)?\b",
        r"\b[0-9][0-9.]*\s*(?:pesos?|clp)\b",
        r"\b[0-9]{4,}\b",
    )
    cleaned = value
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned)
    return cleaned


def _extract_description(message: str, movement_type: str | None) -> str | None:
    normalized = normalize_financial_text(message)
    cleaned = _remove_money_expressions(normalized)
    cleaned = re.sub(r"\b(?:hoy|ayer)\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", " ", cleaned)
    for pattern in (*_INCOME_PATTERNS, *_EXPENSE_PATTERNS):
        cleaned = re.sub(pattern, " ", cleaned)
    cleaned = re.sub(
        r"\b(?:en|por|de|del|la|el|un|una|unos|unas|concepto|total)\b",
        " ",
        cleaned,
    )
    cleaned = " ".join(cleaned.split()).strip(" .,-")

    meaningful = [
        token
        for token in cleaned.split()
        if token not in _GENERIC_DESCRIPTION_WORDS
    ]
    if not meaningful:
        return None

    detail = " ".join(cleaned.split())
    if movement_type == "income" and detect_movement_type(message) == "income":
        return f"Venta o ingreso por {detail}"
    if movement_type == "expense":
        return f"Gasto en {detail}"
    return detail.capitalize()


def classify_category(
    message: str,
    movement_type: str | None,
    description: str | None = None,
) -> str | None:
    if movement_type is None:
        return None
    normalized = normalize_financial_text(f"{message} {description or ''}")
    allowed = INCOME_CATEGORIES if movement_type == "income" else EXPENSE_CATEGORIES

    for category, keywords in _CATEGORY_KEYWORDS.items():
        if category not in allowed:
            continue
        if any(keyword in normalized for keyword in keywords):
            return category
    return "otros_ingresos" if movement_type == "income" else "otros_gastos"


def extract_with_rules(
    message: str,
    existing_draft: dict | None = None,
    now: datetime | None = None,
) -> FinancialMovementExtraction:
    """Extrae datos deterministas y conserva valores del borrador anterior."""
    existing = dict(existing_draft or {})
    detected_type = detect_movement_type(message)
    movement_type = existing.get("movement_type") or detected_type
    amount = existing.get("amount") or parse_chilean_amount(message)
    previous_description = existing.get("description")
    extracted_description = _extract_description(message, movement_type)
    description = previous_description or extracted_description
    occurred_on = existing.get("occurred_on") or _extract_occurred_on(message, now)
    previous_category = existing.get("category")
    generic_category = (
        "otros_ingresos" if movement_type == "income" else "otros_gastos"
    )

    # Una categoría solo es confiable cuando existe un concepto. Además, si el
    # primer mensaje quedó en una categoría genérica y el usuario ahora aporta
    # el concepto faltante, se vuelve a clasificar usando ese nuevo dato.
    if description is None:
        category = None
    elif (
        previous_category
        and not (
            previous_category == generic_category
            and previous_description is None
            and extracted_description is not None
        )
    ):
        category = previous_category
    else:
        category = classify_category(message, movement_type, description)

    previous_text = (existing.get("original_text") or "").strip()
    current_text = (message or "").strip()
    original_text = previous_text
    if current_text and current_text not in previous_text:
        original_text = (
            f"{previous_text} | complemento: {current_text}"
            if previous_text
            else current_text
        )

    present = sum(
        value is not None
        for value in (movement_type, amount, category, description, occurred_on)
    )
    confidence = present / 5
    if detected_type is not None:
        confidence = min(1.0, confidence + 0.1)

    return FinancialMovementExtraction(
        movement_type=movement_type,
        amount=amount,
        category=category,
        description=description,
        occurred_on=occurred_on,
        original_text=original_text or None,
        confidence=confidence,
    )


def _extract_json_object(value: str) -> dict | None:
    if not value:
        return None
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(value[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_llm_type(value: object) -> str | None:
    normalized = normalize_financial_text(str(value or ""))
    return {
        "income": "income",
        "ingreso": "income",
        "expense": "expense",
        "gasto": "expense",
    }.get(normalized)


def _normalize_llm_category(value: object, movement_type: str | None) -> str | None:
    normalized = normalize_financial_text(str(value or "")).replace(" ", "_")
    aliases = {
        "venta": "ventas",
        "capital": "aportes_capital",
        "insumos": "insumos_mercaderia",
        "mercaderia": "insumos_mercaderia",
        "arriendo": "arriendo_servicios",
        "servicios_basicos": "arriendo_servicios",
        "permisos": "permisos_tramites",
        "tramites": "permisos_tramites",
        "otro_ingreso": "otros_ingresos",
        "otro_gasto": "otros_gastos",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in FINANCIAL_CATEGORIES:
        return normalized
    if movement_type == "income":
        return "otros_ingresos"
    if movement_type == "expense":
        return "otros_gastos"
    return None


def _extract_with_llm(
    message: str,
    rule_result: FinancialMovementExtraction,
) -> dict | None:
    # Import diferido: core.ia reutiliza split_message desde message_router y
    # cargarlo al importar este módulo crearía una dependencia circular.
    from core.ia import llamar_llm

    prompt = f"""Extrae un movimiento financiero chileno desde el mensaje.
Devuelve SOLO JSON válido, sin Markdown, con estas claves:
movement_type: "income", "expense" o null
amount: entero CLP o null
category: una de ventas, servicios, aportes_capital, otros_ingresos,
insumos_mercaderia, transporte, arriendo_servicios, permisos_tramites,
marketing, equipamiento, otros_gastos, o null
description: descripción breve o null
occurred_on: fecha YYYY-MM-DD o null
confidence: número entre 0 y 1

Reglas obligatorias:
- No confundas una cantidad de productos con dinero.
- "vendí 10 empanadas" tiene amount null.
- No inventes montos, fechas ni conceptos.
- Si no se indica fecha, usa {rule_result.occurred_on.isoformat() if rule_result.occurred_on else _local_today().isoformat()}.

Mensaje: {json.dumps(message, ensure_ascii=False)}
"""
    response = llamar_llm(
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un extractor de datos. Responde exclusivamente con "
                    "un objeto JSON y nunca agregues explicaciones."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=250,
        temperature=0.0,
        ollama_url=OLLAMA_URL,
        ollama_model=OLLAMA_MODEL,
        ia_api_key=IA_API_KEY,
    )
    return _extract_json_object(response)


def extract_financial_movement(
    message: str,
    existing_draft: dict | None = None,
    now: datetime | None = None,
) -> FinancialMovementExtraction:
    """Combina reglas confiables con un fallback estructurado de IA."""
    rule_result = extract_with_rules(message, existing_draft, now)

    # Si reglas simples obtuvieron los datos relevantes, evitamos costo y
    # latencia del modelo. La fecha y categoría ya tienen fallback seguro.
    if rule_result.movement_type and rule_result.description:
        return rule_result

    llm_data = _extract_with_llm(message, rule_result)
    if not llm_data:
        return rule_result

    llm_type = _normalize_llm_type(llm_data.get("movement_type"))
    movement_type = rule_result.movement_type or llm_type
    llm_amount = llm_data.get("amount")
    if not _has_money_signal(message):
        llm_amount = None
    try:
        llm_amount = int(llm_amount) if llm_amount is not None else None
    except (TypeError, ValueError):
        llm_amount = None

    # El modelo no puede inventar el concepto faltante. La descripción solo se
    # acepta cuando las palabras que la sustentan estaban realmente presentes
    # en el mensaje del usuario. Sin concepto tampoco se confirma categoría.
    description = rule_result.description
    category = rule_result.category
    if description and category is None:
        category = classify_category(message, movement_type, description)
        if category is None:
            category = _normalize_llm_category(
                llm_data.get("category"),
                movement_type,
            )

    occurred_on = rule_result.occurred_on
    llm_date = llm_data.get("occurred_on")
    if occurred_on is None and llm_date:
        try:
            occurred_on = date.fromisoformat(str(llm_date))
        except ValueError:
            occurred_on = None

    try:
        llm_confidence = float(llm_data.get("confidence") or 0)
    except (TypeError, ValueError):
        llm_confidence = 0

    return FinancialMovementExtraction(
        movement_type=movement_type,
        amount=rule_result.amount or llm_amount,
        category=category,
        description=description,
        occurred_on=occurred_on,
        original_text=rule_result.original_text,
        confidence=max(rule_result.confidence, min(1.0, max(0.0, llm_confidence))),
    )
