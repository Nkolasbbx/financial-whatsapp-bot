"""
FinancIAl — scripts/evaluar_rag.py

Evaluación offline de la recuperación RAG: corre las preguntas de
scripts/rag_eval/preguntas.json por el mismo camino que get_ai_response
(texto de búsqueda → requiere_rag → obtener_contexto_rag) contra la tabla
`documents` real, y mide si llegan los documentos esperados.

No envía nada a WhatsApp ni llama al LLM de chat. Sí usa la API de
embeddings de Hugging Face (HF_TOKEN) y la base del RAG (DB_DSN).

Sirve para comparar antes/después sobre los MISMOS datos: corre igual con el
código anterior a la HdU (detecta qué parámetros acepta obtener_contexto_rag).

Uso:
    python scripts/evaluar_rag.py
    python scripts/evaluar_rag.py --etiqueta antes --casos pat-01 seg-01
"""
import argparse
import asyncio
import inspect
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2.pool  # noqa: E402

import core.ia as ia  # noqa: E402
import dependencies  # noqa: E402
from config import DB_DSN  # noqa: E402

PREGUNTAS = Path(__file__).resolve().parent / "rag_eval" / "preguntas.json"
SALIDA = ROOT / "tmp" / "rag_eval"
COMUNAS_SOPORTADAS = {"recoleta", "el bosque"}
GENERAL = "general"
RE_DOCUMENTO = re.compile(r"\[Documento Oficial: (.+?)\] \| \[Ámbito: (.+?)\]")


def ambitos_permitidos(caso: dict) -> set[str]:
    """Comunas de las que puede venir un documento sin que sea una fuga.
    Por defecto, la del perfil más lo nacional; el caso puede fijarlas."""
    if "ambitos" in caso:
        return {a.lower() for a in caso["ambitos"]}
    comuna = (caso.get("comuna") or "").lower()
    return {comuna, GENERAL} if comuna in COMUNAS_SOPORTADAS else {GENERAL}


def documentos_entregados(contexto: str) -> list[tuple[str, str]]:
    docs = []
    for file_name, ambito in RE_DOCUMENTO.findall(contexto):
        ambito = GENERAL if ambito.lower().startswith("general") else ambito.lower()
        docs.append((file_name, ambito))
    return docs


def _ms(inicio: float) -> float:
    return round((time.perf_counter() - inicio) * 1000, 1)


async def evaluar_caso(caso: dict) -> dict:
    pregunta = caso["pregunta"]
    historial = [{"role": "user", "content": caso["previa"]}] if caso.get("previa") else []
    texto = ia._texto_busqueda_rag(pregunta, historial) if hasattr(ia, "_texto_busqueda_rag") else pregunta

    usa_rag, vector = await ia.requiere_rag(texto)
    contexto, ms_embedding, ms_busqueda = "", None, None
    if usa_rag:
        if vector is None:
            inicio = time.perf_counter()
            vector = await ia.obtener_embedding_remoto(texto)
            ms_embedding = _ms(inicio)

        parametros = inspect.signature(ia.obtener_contexto_rag).parameters
        kwargs = {"query_vector": vector}
        if "rubro_usuario" in parametros:
            kwargs["rubro_usuario"] = caso.get("rubro", "")
        if "mensaje_actual" in parametros:
            kwargs["mensaje_actual"] = ia._redactar_datos_personales(pregunta)

        inicio = time.perf_counter()
        resultado = await ia.obtener_contexto_rag(texto, (caso.get("comuna") or "").lower(), **kwargs)
        ms_busqueda = _ms(inicio)
        contexto = resultado["contexto"]

    docs = documentos_entregados(contexto)
    archivos = [file_name for file_name, _ in docs]
    esperados = caso.get("esperados", [])
    rango = next((i for i, f in enumerate(archivos, start=1) if f in esperados), None)
    permitidos = ambitos_permitidos(caso)

    return {
        "id": caso["id"],
        "tipo": caso.get("tipo", ""),
        "texto_busqueda": texto,
        "usa_rag": usa_rag,
        "documentos": docs,
        "rango": rango,
        "acierto": (rango is not None) if esperados else not docs,
        "fugas": [d for d in docs if d[1] not in permitidos],
        "primero_indebido": bool(archivos) and archivos[0] in caso.get("no_primero", []),
        "sin_documentos": bool(esperados) and not docs,
        "bloques_repetidos": len(_bloques(contexto)) - len(set(_bloques(contexto))),
        "caracteres_contexto": len(contexto),
        "ms_embedding": ms_embedding,
        "ms_busqueda": ms_busqueda,
    }


def _bloques(contexto: str) -> list[str]:
    """Texto de cada bloque entregado (sin su encabezado), para contar
    bloques idénticos repetidos en el mismo contexto."""
    partes = re.split(r"\n\[Documento Oficial: .+?\n\[Fuente: .+?\n", contexto)
    return [p.strip() for p in partes[1:]]


def _percentil(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    if len(valores) == 1:
        return valores[0]
    return round(statistics.quantiles(valores, n=100, method="inclusive")[int(p) - 1], 1)


def resumen(resultados: list[dict]) -> dict:
    con_esperados = [r for r in resultados if r["tipo"] != "ninguno"]
    rangos = [r["rango"] for r in con_esperados]
    embeddings = [r["ms_embedding"] for r in resultados if r["ms_embedding"] is not None]
    busquedas = [r["ms_busqueda"] for r in resultados if r["ms_busqueda"] is not None]
    usados = [r for r in resultados if r["usa_rag"]]
    return {
        "casos": len(resultados),
        "hit_at_3": round(sum(1 for x in rangos if x and x <= 3) / max(len(rangos), 1), 3),
        "mrr": round(sum(1 / x for x in rangos if x) / max(len(rangos), 1), 3),
        "aciertos_totales": sum(r["acierto"] for r in resultados),
        "fugas_de_comuna": sum(len(r["fugas"]) for r in resultados),
        "primero_de_otro_rubro": sum(r["primero_indebido"] for r in resultados),
        "sin_documentos_con_esperados": sum(r["sin_documentos"] for r in resultados),
        "bloques_repetidos": sum(r["bloques_repetidos"] for r in resultados),
        "caracteres_contexto_promedio": round(
            sum(r["caracteres_contexto"] for r in usados) / max(len(usados), 1)
        ),
        "ms_embedding_p50": _percentil(embeddings, 50),
        "ms_embedding_p95": _percentil(embeddings, 95),
        "ms_busqueda_p50": _percentil(busquedas, 50),
        "ms_busqueda_p95": _percentil(busquedas, 95),
    }


def imprimir(resultados: list[dict], totales: dict) -> None:
    print(f"\n{'caso':<11} {'ok':<3} {'rango':<6} {'chars':>6}  documentos")
    for r in resultados:
        marca = "sí" if r["acierto"] else "NO"
        if r["fugas"] or r["primero_indebido"]:
            marca = "!!"
        docs = ", ".join(f"{f} ({a})" for f, a in r["documentos"]) or "—"
        print(f"{r['id']:<11} {marca:<3} {str(r['rango'] or '—'):<6} {r['caracteres_contexto']:>6}  {docs}")
    print("\nResumen")
    for clave, valor in totales.items():
        print(f"  {clave:<30} {valor}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--etiqueta", default="", help="Se agrega al nombre del archivo de resultados (ej. antes, despues).")
    parser.add_argument("--casos", nargs="*", help="IDs de casos a correr (por defecto, todos).")
    args = parser.parse_args()

    if not DB_DSN:
        sys.exit("Falta DB_DSN en el entorno (.env): es la base del RAG.")

    casos = json.loads(PREGUNTAS.read_text(encoding="utf-8"))["casos"]
    if args.casos:
        casos = [c for c in casos if c["id"] in set(args.casos)]

    # Solo el pool del RAG: no se inicializan WhatsApp, Redis ni Supabase.
    dependencies.db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=2, dsn=DB_DSN)
    try:
        resultados = [await evaluar_caso(caso) for caso in casos]
    finally:
        dependencies.db_pool.closeall()

    totales = resumen(resultados)
    imprimir(resultados, totales)

    SALIDA.mkdir(parents=True, exist_ok=True)
    sufijo = f"_{args.etiqueta}" if args.etiqueta else ""
    destino = SALIDA / f"resultado_{datetime.now():%Y%m%d_%H%M%S}{sufijo}.json"
    destino.write_text(
        json.dumps({"resumen": totales, "casos": resultados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nResultados guardados en {destino}")


if __name__ == "__main__":
    asyncio.run(main())
