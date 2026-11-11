"""Reemplaza las fuentes internas de `documents` por un nombre citable.

Por defecto solo simula e imprime los cambios; escribe únicamente con --apply.
Actualiza `metadata`, sin regenerar embeddings.

    python scripts/normalizar_fuentes_documentos.py [--valor ingesta_panel]
    python scripts/normalizar_fuentes_documentos.py --apply [--valor ingesta_panel]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dependencies
from core.ingestion import fuente_automatica

PREFIJO_INTERNO = "Panel admin — "

SQL_GRUPOS = """
    SELECT metadata->>'file_name', metadata->>'comuna', metadata->>'source', count(*)
    FROM documents
    WHERE metadata->>'source' LIKE %s OR metadata->>'source' = ANY(%s)
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 3
"""

# La fecha de carga se guardaba como source_date: se vacía para que la cita no
# la presente como revisión. Una fecha distinta sí fue declarada y se conserva.
SQL_ACTUALIZAR = """
    UPDATE documents
    SET metadata = jsonb_set(metadata, '{source}', to_jsonb(%s::text))
        || CASE WHEN metadata->>'source_date' = left(metadata->>'uploaded_at', 10)
                THEN '{"source_date": ""}'::jsonb
                ELSE '{}'::jsonb
           END
    WHERE metadata->>'file_name' = %s
      AND metadata->>'comuna' IS NOT DISTINCT FROM %s
      AND metadata->>'source' = %s
"""


def planificar_cambios(grupos: list[tuple]) -> tuple[list[dict], list[tuple]]:
    """Separa los grupos corregibles de los que no tienen file_name."""
    cambios, omitidos = [], []
    for file_name, comuna, fuente, filas in grupos:
        if not file_name:
            omitidos.append((comuna, fuente, filas))
            continue
        cambios.append({
            "file_name": file_name,
            "comuna": comuna,
            "fuente": fuente,
            "fuente_nueva": fuente_automatica(file_name, comuna),
            "filas": filas,
        })
    return cambios, omitidos


def normalizar(conn, valores_internos: list[str], aplicar: bool) -> tuple[list[dict], list[tuple]]:
    """Corre todo en una transacción; sin `aplicar` no ejecuta ningún UPDATE."""
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_GRUPOS, (f"{PREFIJO_INTERNO}%", valores_internos))
            cambios, omitidos = planificar_cambios(cur.fetchall())
            if aplicar:
                for c in cambios:
                    cur.execute(SQL_ACTUALIZAR, (c["fuente_nueva"], c["file_name"], c["comuna"], c["fuente"]))
                    if cur.rowcount != c["filas"]:
                        raise RuntimeError(
                            f"{c['file_name']}: se esperaban {c['filas']} filas y se actualizaron {cur.rowcount}."
                        )
        if aplicar:
            conn.commit()
        else:
            conn.rollback()
        return cambios, omitidos
    except Exception:
        conn.rollback()
        raise


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="escribe los cambios (sin esto solo simula)")
    parser.add_argument("--valor", action="append", default=[], help="otro valor interno exacto de source a reemplazar")
    args = parser.parse_args()

    await dependencies.init_dependencies()
    try:
        conn = dependencies.get_pg_conn()
        try:
            cambios, omitidos = await asyncio.to_thread(normalizar, conn, args.valor, args.apply)
        finally:
            dependencies.db_pool.putconn(conn)
    finally:
        await dependencies.shutdown_dependencies()

    for c in cambios:
        print(f"{c['file_name']} | {c['comuna']} | {c['fuente']} -> {c['fuente_nueva']} | {c['filas']} filas")
    for comuna, fuente, filas in omitidos:
        print(f"OMITIDO sin file_name | {comuna} | {fuente} | {filas} filas")

    total = sum(c["filas"] for c in cambios)
    print(f"\n{'APLICADO' if args.apply else 'SIMULACIÓN'}: {len(cambios)} documento(s), {total} fila(s).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
