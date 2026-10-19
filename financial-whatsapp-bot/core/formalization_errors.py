"""Referencias públicas seguras para errores HDU017.

Nunca utiliza str(error), argumentos, consultas ni detalles del driver. Los
códigos y textos son cerrados; SQLSTATE solo se muestra con formato válido.
No requiere importar psycopg2 ni conectarse a la base para clasificar errores.
"""

from __future__ import annotations

import re

from db.formalization_diagnostics import (
    FormalizationConflictError,
    FormalizationConnectionError,
    FormalizationNotFoundError,
)


_MESSAGES = {
    "DIAG_CONNECTION": "La conexión con el Supabase del diagnóstico no está disponible.",
    "DIAG_SCHEMA": "Una consulta no es compatible con la estructura de la base de datos.",
    "DIAG_PERMISSION": "La base de datos no autorizó la operación requerida.",
    "DIAG_CONSTRAINT": "Una restricción o validación de la base de datos rechazó la operación.",
    "DIAG_TIMEOUT": "La operación excedió el tiempo disponible o quedó esperando un bloqueo.",
    "DIAG_CONFLICT": "La evaluación cambió mientras se procesaba la solicitud. Debes revisarla nuevamente.",
    "DIAG_VALIDATION": "No guardé esa respuesta. Revisa el formato indicado y vuelve a intentarlo.",
    "DIAG_INTERNAL": "Ocurrió un error interno al procesar el diagnóstico.",
}
_STAGES = {
    "load": "lectura del diagnóstico", "create": "inicio del diagnóstico",
    "resume": "reanudación", "flow": "flujo del diagnóstico",
    "calculate": "cálculo", "confirm": "confirmación",
}


def _chain(error: Exception):
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        yield error
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)


def _sqlstate(error: Exception) -> str | None:
    value = (getattr(error, "pgcode", None) or getattr(error, "sqlstate", None)
             or getattr(error, "code", None))
    return value if isinstance(value, str) and re.fullmatch(r"[0-9A-Z]{5}", value) else None


def safe_sqlstate(error: Exception) -> str | None:
    """Un SQLSTATE no contiene consultas, valores ni identidad del usuario."""
    return next((state for current in _chain(error) if (state := _sqlstate(current))), None)


def formalization_error_code(error: Exception) -> str:
    chain = list(_chain(error))
    for current in chain:
        if isinstance(current, FormalizationConflictError):
            return "DIAG_CONFLICT"
        if isinstance(current, FormalizationConnectionError):
            return "DIAG_CONNECTION"
        # PostgREST conserva SQLSTATE en .code y usa códigos propios para la API.
        api_code = getattr(current, "code", None)
        if api_code in {"PGRST000", "PGRST001", "PGRST002"}:
            return "DIAG_CONNECTION"
        if api_code == "PGRST003":
            return "DIAG_TIMEOUT"
        if api_code in {"PGRST200", "PGRST201", "PGRST202", "PGRST203", "PGRST204", "PGRST205"}:
            return "DIAG_SCHEMA"
        if api_code in {"PGRST301", "PGRST302", "PGRST303"}:
            return "DIAG_PERMISSION"
        state = _sqlstate(current)
        if state:
            if state.startswith(("08", "28")) or state in {"3D000", "53300", "57P01", "57P02", "57P03"}:
                return "DIAG_CONNECTION"
            if state in {"42501", "25006"}:
                return "DIAG_PERMISSION"
            if state.startswith("42") or state == "3F000":
                return "DIAG_SCHEMA"
            if state.startswith("23") or state == "P0001":
                return "DIAG_CONSTRAINT"
            if state in {"57014", "55P03"}:
                return "DIAG_TIMEOUT"
            if state in {"40001", "40P01"}:
                return "DIAG_CONFLICT"
            if state.startswith("22"):
                return "DIAG_VALIDATION"
        if isinstance(current, TimeoutError):
            return "DIAG_TIMEOUT"
        if isinstance(current, ConnectionError):
            return "DIAG_CONNECTION"
        for cls in type(current).__mro__:
            if cls.__module__.startswith("httpx"):
                if cls.__name__ == "TimeoutException":
                    return "DIAG_TIMEOUT"
                if cls.__name__ in {"NetworkError", "ConnectError"}:
                    return "DIAG_CONNECTION"
        # Broken/closed connections may not carry SQLSTATE. Avoid importing the
        # driver just for isinstance; inspect only fixed driver class names.
        if any(cls.__module__.startswith("psycopg2") and cls.__name__ in {
            "OperationalError", "InterfaceError", "PoolError",
        } for cls in type(current).__mro__):
            return "DIAG_CONNECTION"
    if any(isinstance(current, (ValueError, FormalizationNotFoundError)) for current in chain):
        return "DIAG_VALIDATION"
    return "DIAG_INTERNAL"


def formalization_error_message(error: Exception, stage: str = "flow") -> str:
    code = formalization_error_code(error)
    state = safe_sqlstate(error)
    missing_rpc = any(getattr(current, "code", None) == "PGRST202" for current in _chain(error))
    prefix = "" if code == "DIAG_VALIDATION" else "No pude consultar o guardar el diagnóstico.\n"
    return (prefix + _MESSAGES[code] + f"\n\nCódigo: {code}"
            + f"\nEtapa: {_STAGES.get(stage, _STAGES['flow'])}"
            + ("\nFaltan las funciones del diagnóstico o debe actualizarse la caché de Supabase." if missing_rpc else "")
            + (f"\nSQLSTATE: {state}" if state else ""))
