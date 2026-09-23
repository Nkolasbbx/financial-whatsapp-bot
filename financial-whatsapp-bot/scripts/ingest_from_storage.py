import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arq import create_pool

import dependencies
from config import ARQ_QUEUE_NAME
from redis_settings import get_redis_settings
from services.ingestion_jobs import enqueue_document_ingestion, validate_upload

CONTENT_TYPES = {".pdf": "application/pdf", ".md": "text/markdown"}


def parse_item(item):
    path, _, rubros = item.partition(":")
    return path, [r.strip() for r in rubros.split(",") if r.strip()] or ["general"]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--comuna", required=True)
    parser.add_argument("--uploaded-by", default="script")
    parser.add_argument("--vigencia-desde", default=None)
    parser.add_argument("--vigencia-hasta", default=None)
    parser.add_argument("archivos", nargs="+")
    args = parser.parse_args()

    await dependencies.init_dependencies()
    redis = await create_pool(get_redis_settings(), default_queue_name=ARQ_QUEUE_NAME)
    storage = dependencies.supabase_admin.storage.from_(args.bucket)
    errores = 0

    try:
        for item in args.archivos:
            path, rubros = parse_item(item)
            file_name = Path(path).name
            content_type = CONTENT_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")
            try:
                raw_bytes = storage.download(path)  # descarga desde Supabase Storage
                error = validate_upload(file_name, content_type, len(raw_bytes))
                if error:
                    raise ValueError(error)
                await enqueue_document_ingestion(
                    redis=redis,
                    raw_bytes=raw_bytes,
                    file_name=file_name,
                    content_type=content_type,
                    comuna=args.comuna,
                    uploaded_by=args.uploaded_by,
                    rubros=rubros,
                    vigencia_desde=args.vigencia_desde,
                    vigencia_hasta=args.vigencia_hasta,
                )
                print(f"OK   {file_name} (rubros={rubros})")
            except Exception as exc:
                errores += 1
                print(f"ERR  {path}: {exc}")
    finally:
        await redis.close()
        await dependencies.shutdown_dependencies()

    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
