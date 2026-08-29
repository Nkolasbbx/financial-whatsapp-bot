# Despliegue en Railway: config-as-code para server + worker

## Por qué dos archivos

Railway asocia **un archivo de configuración a un servicio**. Este proyecto necesita dos servicios corriendo desde el mismo repo, con comandos de arranque distintos: el server web (uvicorn) y el worker de colas (arq). Por eso hay dos archivos, no uno:

- `railway.web.json` → servicio del server.
- `railway.worker.json` → servicio del worker.

Ambos viven dentro de `financial-whatsapp-bot/`, porque el repo (`financial-pvm`) es un **monorepo** (también contiene `Ingest/`, `supabase/`, etc.) — cada servicio de Railway necesita que se le indique explícitamente dónde vive el código de este proyecto.

## Qué configura cada campo

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "RAILPACK" },
  "deploy": {
    "startCommand": "uvicorn main:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/",
    "healthcheckTimeout": 30,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

- `builder: RAILPACK`: el detector automático de Railway para proyectos Python con `requirements.txt` — no hace falta un `Dockerfile`.
- `startCommand`: el mismo comando de `Makefile` (`run-server` / `run-worker`), pero explícito para Railway. El server usa `$PORT` (variable que Railway inyecta) en vez del `8000` fijo del Makefile local.
- `healthcheckPath: "/"` (solo en el web): usa el endpoint ya existente en `main.py` (`@app.get("/")`) para que Railway sepa cuándo el deploy está sano antes de enrutarle tráfico. El worker no tiene HTTP, así que no lleva healthcheck.
- `restartPolicyType: "ON_FAILURE"` + `restartPolicyMaxRetries: 10`: **esta es la respuesta directa al miedo original** de que el worker se caiga por un error de conexión y no vuelva a prender. Si el proceso muere (por ejemplo, un `TimeoutError` de Redis que el hardening de `redis_settings.py` no haya logrado absorber), Railway lo reinicia automáticamente, hasta 10 veces, antes de marcarlo como "Crashed" y requerir intervención manual.

## Qué NO configura este archivo (y dónde va en su lugar)

Confirmado contra la documentación de Railway: **el `Root Directory` de cada servicio y las variables de entorno no son parte del config-as-code** — se configuran aparte, en el dashboard (o por CLI), por diseño de Railway.

Esto significa que, aunque los archivos ya están en el repo, todavía hay pasos manuales por servicio, una sola vez, al crear cada uno:

1. `Settings → Source → Root Directory` = `financial-whatsapp-bot`.
2. `Settings → Config as Code Path` = `/financial-whatsapp-bot/railway.web.json` (o `railway.worker.json` en el otro servicio).
3. `Variables`: copiar manualmente lo que hoy vive en `.env` local (que está en `.gitignore` — Railway nunca lo lee). Para `REDIS_URL` específicamente, no se pega el DSN a mano: se referencia el servicio de Redis de Railway (ver `docs/redis-resilience-and-cost.md` para por qué conviene el Redis de Railway en vez de Upstash):
   ```
   REDIS_URL=${{Redis.REDIS_URL}}
   ```
   (mismo valor en ambos servicios — web y worker apuntan al mismo Redis).

## Por qué mejora las cosas

- El "cómo se arranca cada servicio" queda versionado en el repo (config-as-code) en vez de vivir solo como clics en la UI de alguien — reproducible y auditable en el historial de git.
- El `restartPolicyType: ON_FAILURE` es la red de seguridad de infraestructura que complementa (no reemplaza) el hardening de `redis_settings.py`: los blips de red normales ya no tumban el proceso; si algo más serio sí lo tumba, Railway lo reintenta solo.
- Separar los dos servicios explícitamente (en vez de intentar correr uvicorn y arq en el mismo proceso/contenedor) mantiene la arquitectura que ya tiene el proyecto — web y worker siguen siendo procesos independientes, ahora también en producción.
