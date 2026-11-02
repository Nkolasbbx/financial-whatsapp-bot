"""Caché en memoria para catálogos que solo cambian por migración o por el admin
(HDU optimización, P-09). Nunca guardar aquí datos de un usuario.

Cada proceso (web y worker) tiene su propia caché; CATALOG_CACHE_TTL_SECONDS
acota cuánto tarda en verse un cambio del catálogo. Se entregan copias para
que un llamador no pueda modificar el valor compartido.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

_lock = threading.Lock()
_entries: dict[str, tuple[Any, float, Any]] = {}


def _ttl() -> float:
    from config import CATALOG_CACHE_TTL_SECONDS

    return float(CATALOG_CACHE_TTL_SECONDS)


def cached(key: str, client: Any, loader: Callable[[], Any]) -> Any:
    """Devuelve una copia del valor cacheado o lo carga con loader().

    La entrada pertenece al cliente con el que se leyó: si cambia el cliente
    (por ejemplo, otro proyecto o un cliente de prueba), se vuelve a leer.
    No se cachean None ni errores: el próximo llamado reintenta.
    """
    ttl = _ttl()
    if ttl <= 0:
        return loader()
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry is not None and entry[0] is client and entry[1] > now:
            return copy.deepcopy(entry[2])
    value = loader()
    if value is not None:
        with _lock:
            _entries[key] = (client, now + ttl, copy.deepcopy(value))
    return value


def clear(key: str | None = None) -> None:
    """Invalida una entrada o toda la caché (tests y cambios desde el admin)."""
    with _lock:
        if key is None:
            _entries.clear()
        else:
            _entries.pop(key, None)
