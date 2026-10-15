"""Validaciones locales del perfil; no verifican identidad ni propiedad del correo."""

import re
import unicodedata


class ProfileValidationError(ValueError):
    """Respuesta que el usuario debe corregir antes de continuar."""


_NON_ANSWERS = {
    "hola", "buenas", "buenos dias", "buenas tardes", "buenas noches",
    "ok", "okay", "gracias", "listo", "si", "no", "no se", "ayuda",
    "volver", "atras", "cancelar", "reiniciar", "menu", "omitir",
    "no tengo", "prefiero no responder",
}
_CONTROL_PREFIXES = (
    "menu_", "profile_", "rubro_", "comuna_", "sii_", "fund_", "finance_",
    "calendar_", "hito_", "experto_", "unsatisfied_",
)


def _clean_text(value: str) -> str:
    if not isinstance(value, str) or any(unicodedata.category(c) == "Cc" for c in value):
        raise ProfileValidationError("Escribe el dato en una sola línea.")
    return " ".join(unicodedata.normalize("NFC", value).strip().split())


def _is_non_answer(text: str) -> bool:
    normalized = "".join(
        c for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    ).strip(" .!¡?¿")
    return normalized in _NON_ANSWERS or normalized.startswith(_CONTROL_PREFIXES)


def validate_name(value: str) -> str:
    text = _clean_text(value)
    if (
        not 2 <= len(text) <= 100
        or sum(c.isalpha() for c in text) < 2
        or any(not (c.isalpha() or c in " '-’") for c in text)
        or _is_non_answer(text)
    ):
        raise ProfileValidationError("Escribe tu nombre, por ejemplo: María González.")
    return text


def validate_rut(value: str) -> str:
    text = _clean_text(value)
    if not re.fullmatch(r"(?:[0-9]{1,8}|[0-9]{1,2}(?:\.[0-9]{3}){2})-?[0-9kK]", text):
        raise ProfileValidationError("Escribe tu RUT con dígito verificador, por ejemplo: 12.345.678-5.")
    compact = text.replace(".", "").replace("-", "").upper()
    number, digit = compact[:-1].lstrip("0"), compact[-1]
    if not number:
        raise ProfileValidationError("Revisa el número y el dígito verificador de tu RUT.")
    total = sum(int(n) * (2 + i % 6) for i, n in enumerate(reversed(number)))
    remainder = 11 - total % 11
    expected = "0" if remainder == 11 else "K" if remainder == 10 else str(remainder)
    if digit != expected:
        raise ProfileValidationError("El dígito verificador no coincide. Revisa tu RUT e inténtalo otra vez.")
    return f"{number}-{digit}"


def validate_email(value: str) -> str:
    text = _clean_text(value)
    error = "Escribe un correo válido, por ejemplo: nombre@ejemplo.cl."
    if len(text) > 254 or text.count("@") != 1:
        raise ProfileValidationError(error)
    local, domain = text.rsplit("@", 1)
    if (
        not 1 <= len(local) <= 64
        or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
        or local.startswith(".") or local.endswith(".") or ".." in local
    ):
        raise ProfileValidationError(error)
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ProfileValidationError(error) from exc
    labels = domain.split(".")
    if len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ) or len(f"{local}@{domain}") > 254:
        raise ProfileValidationError(error)
    return f"{local}@{domain}"


def validate_address(value: str) -> str:
    text = _clean_text(value)
    if not 5 <= len(text) <= 200 or not any(c.isalpha() for c in text) or _is_non_answer(text):
        raise ProfileValidationError(
            "Escribe una dirección de tu negocio, por ejemplo: Av. Recoleta 123. "
            "También puedes tocar Omitir dirección."
        )
    return text


PROFILE_VALIDATORS = {
    "nombre_completo": validate_name,
    "rut": validate_rut,
    "email": validate_email,
    "direccion_negocio": validate_address,
}


def has_valid_profile_value(user: dict, field: str) -> bool:
    try:
        PROFILE_VALIDATORS[field](user.get(field))
        return True
    except ProfileValidationError:
        return False
