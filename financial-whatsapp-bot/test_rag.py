"""
Test rápido del flujo RAG completo: pool de conexiones + embedding + query vectorial.
Uso:
    python test_rag.py "texto de prueba, ej: patente comercial"
Requiere estar parado en la raíz del proyecto (para que los imports relativos funcionen).
"""
 
import asyncio
import sys
 
from psycopg2 import pool
 
from config import SUPABASE_DB_DSN
from core.ia import obtener_embedding_remoto
 
 
async def main():
    query_texto = sys.argv[1] if len(sys.argv) > 1 else "patente comercial"
    comuna_test = "recoleta"
 
    print("=" * 60)
    print("TEST RAG: pool + embedding + query vectorial")
    print("=" * 60)
 
    # 1) Pool de conexiones (igual que en dependencies.py)
    print("\n[1/3] Creando pool de conexiones...")
    try:
        db_pool = pool.SimpleConnectionPool(minconn=1, maxconn=2, dsn=SUPABASE_DB_DSN)
        print("✅ Pool creado correctamente")
    except Exception as e:
        print(f"❌ Fallo al crear el pool: {type(e).__name__}: {e}")
        return
 
    # 2) Embedding remoto (Hugging Face)
    print(f"\n[2/3] Generando embedding para: {query_texto!r}")
    try:
        vector = await obtener_embedding_remoto(query_texto)
        print(f"✅ Embedding generado ({len(vector)} dimensiones)")
    except Exception as e:
        print(f"❌ Fallo al generar embedding: {type(e).__name__}: {e}")
        db_pool.closeall()
        return
 
    # 3) Query vectorial contra la tabla documents (misma lógica que obtener_contexto_rag)
    print(f"\n[3/3] Consultando tabla 'documents' (comuna={comuna_test})...")
 
    def _query():
        conn = db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT content, metadata
                    FROM documents
                    WHERE metadata->>'comuna' ILIKE %s OR metadata->>'comuna' ILIKE '%%general%%'
                    ORDER BY embedding <=> %s::vector
                    LIMIT 4;
                    """,
                    (f"%{comuna_test}%", vector),
                )
                return cur.fetchall()
        finally:
            db_pool.putconn(conn)
 
    try:
        resultados = await asyncio.to_thread(_query)
        if resultados:
            print(f"✅ Query exitosa: {len(resultados)} resultado(s) encontrados\n")
            for i, (content, metadata) in enumerate(resultados, start=1):
                preview = (content or "")[:120].replace("\n", " ")
                print(f"  {i}. [{(metadata or {}).get('file_name', '?')}] {preview}...")
        else:
            print("⚠️  Query exitosa pero sin resultados (revisa si la tabla tiene datos para esa comuna)")
    except Exception as e:
        print(f"❌ Fallo en la query: {type(e).__name__}: {e}")
    finally:
        db_pool.closeall()
 
    print("\n🎉 Test finalizado.")
 
 
if __name__ == "__main__":
    asyncio.run(main())