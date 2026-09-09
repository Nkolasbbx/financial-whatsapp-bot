# Despliegue en Railway — guía completa (dashboard y CLI)

## Arquitectura actual

- **Proyecto Railway:** `stellar-recreation`, ambiente `production`.
- **Repo conectado:** `Nkolasbbx/financial-whatsapp-bot` (GitHub), Root Directory `/financial-whatsapp-bot` en los dos servicios.
- **Servicios:**
  - `financial-whatsapp-bot` — server web (`uvicorn main:app`). Dominio público: `https://financial-whatsapp-bot-production.up.railway.app`.
  - `incredible-adventure` — worker de colas (`arq worker.WorkerSettings`). Sin dominio público (no expone HTTP).
- **Base de datos:** plugin `Redis` de Railway (no Upstash), con volumen `redis-volume-LX-G`.
- **El worker no tiene variables propias**: todas sus env vars son *referencias* a las de `financial-whatsapp-bot` (`${{financial-whatsapp-bot.VAR}}`), para no duplicar secretos. Si agregás una variable nueva al server que el worker también necesite, hay que agregarla también como referencia en el worker (por las dos vías, ver más abajo).

La fuente de verdad de la infraestructura es `.railway/railway.ts` (Infrastructure as Code). Los archivos viejos `railway.web.json` / `railway.worker.json` (Config as Code) quedaron deprecados por Railway — dejan de leerse el **2026-12-01** — y ya no controlan nada porque `.railway/railway.ts` fue aplicado. Se pueden borrar cuando quieras (no antes se resolvió, ver sección "Pendientes" al final).

---

## Cómo cambiar o agregar una variable de entorno (guía rápida)

Esto es lo que vas a hacer más seguido. Railway redeploya el servicio solo en cuanto guardás una variable — no hace falta pushear código ni tocar nada más para que tome efecto.

### Vía dashboard (la más simple, sin instalar nada)
1. Entrá a [railway.app](https://railway.app) → proyecto **stellar-recreation** → ambiente **production**.
2. Click en el servicio que corresponda:
   - **`financial-whatsapp-bot`** si es una variable nueva de la app (Meta, Supabase, IA, etc.) — este es el server, acá van los valores reales.
   - **`incredible-adventure`** (el worker) solo si esa variable también la necesita el worker.
3. Pestaña **Variables** → **+ New Variable**.
   - En `financial-whatsapp-bot`: nombre y valor real (texto plano o secreto).
   - En `incredible-adventure`: **no repitas el valor** — poné como valor `${{financial-whatsapp-bot.NOMBRE_DE_LA_VARIABLE}}` (referencia a la del server). Así si la rotás después, se actualiza sola en el worker.
4. Guardá. Railway dispara el redeploy automáticamente — mirá la pestaña **Deployments** para ver el build en curso, y esperá a que el servicio quede en verde (**Online**) de nuevo.
5. **Paso extra importante:** actualizá `.railway/railway.ts` en el repo con la misma variable (ver más abajo por qué) y commiteá el cambio. Si no lo hacés, el archivo queda desincronizado con lo real, y si en el futuro alguien corre `railway config apply`, va a **borrar** cualquier variable que no esté en ese archivo — ya nos pasó una vez con el worker.
   - Si es una variable nueva en `financial-whatsapp-bot`: agregala al objeto `env` de `financialWhatsappBot` en `.railway/railway.ts`, como `NOMBRE_DE_LA_VARIABLE: preserve()`.
   - Si el worker también la necesita: agregala también al `env` de `incredibleAdventure`, como `NOMBRE_DE_LA_VARIABLE: financialWhatsappBot.env.NOMBRE_DE_LA_VARIABLE`.

### Vía CLI
Necesitás la CLI de Railway. En esta máquina no está instalada globalmente (intentar `npm install -g @railway/cli` pidió permisos de administrador que no tenemos), así que se usa al vuelo con `npx` — tarda un par de segundos de más en cada llamada pero no requiere instalar nada:

```bash
npx -y @railway/cli@latest login    # una sola vez, o si la sesión expiró
npx -y @railway/cli@latest link     # una sola vez por carpeta: elegís stellar-recreation → production
```

Para setear una variable:
```bash
# Variable nueva en el server, valor real:
npx -y @railway/cli@latest variable set NOMBRE=valor --service financial-whatsapp-bot

# Si el worker también la necesita, como referencia (sin repetir el secreto):
npx -y @railway/cli@latest variable set "NOMBRE=\${{financial-whatsapp-bot.NOMBRE}}" --service incredible-adventure
```
Sin `--skip-deploys`, cada `variable set` dispara un redeploy solo. Confirmá con:
```bash
npx -y @railway/cli@latest status
```
que ambos servicios vuelvan a **Online**.

Igual que en el dashboard: **después actualizá `.railway/railway.ts` a mano** con la misma variable (mismo motivo, ver arriba). No hace falta correr `railway config apply` para que la variable funcione — ya está aplicada en cuanto corriste `variable set` — el `.ts` es solo para que quede documentada y no se borre en un `apply` futuro.

> Nota sobre `npm install railway` vs `@railway/cli`: son dos paquetes npm distintos. `railway` (el que ya está en `package.json` de este repo) es el SDK de tipos que usa `.railway/railway.ts` — no trae el comando `railway` de terminal. El comando de terminal lo da `@railway/cli`, que acá se usa vía `npx -y @railway/cli@latest` en vez de instalarlo global.

---

## Cómo deployar un cambio de código (guía rápida)

Railway está conectado directo al repo de GitHub — no hay ningún paso manual de "subir" el código, deployar es simplemente pushear.

### Paso a paso
1. Hacé tu cambio de código como siempre (commit en una rama, PR, lo que uses) y mergealo/pusheálo a `main`.
2. Railway detecta el push en `Nkolasbbx/financial-whatsapp-bot` y dispara un build + deploy **automático en los dos servicios** (`financial-whatsapp-bot` y `incredible-adventure`), porque ambos apuntan al mismo repo y Root Directory — no hay forma de deployar uno sin el otro con un push normal.
3. Mirá que terminen bien:
   - **Dashboard:** cada servicio → pestaña **Deployments** → el build más nuevo debería pasar de "Building" a "Deploying" a **Active** (punto verde).
   - **CLI:**
     ```bash
     npx -y @railway/cli@latest status
     ```
     Confirmá que ambos servicios digan `● Online`.
4. Si algo falla, revisá los logs del deploy que falló:
   - **Dashboard:** click en ese deployment → logs.
   - **CLI:**
     ```bash
     npx -y @railway/cli@latest logs --deployment --service financial-whatsapp-bot
     npx -y @railway/cli@latest logs --deployment --service incredible-adventure
     ```

### Redeployar sin pushear código nuevo
Útil si el build falló por algo transitorio, o si cambiaste una variable y por algún motivo no redeployó solo.
```bash
npx -y @railway/cli@latest redeploy --service financial-whatsapp-bot --yes
npx -y @railway/cli@latest redeploy --service incredible-adventure --yes
```
Dashboard: servicio → **Deployments** → en el último deployment, botón **⋮ → Redeploy**.

### Lo que un push a `main` NO hace
No toca nada de `.railway/railway.ts` ni de la config de infraestructura (start command, restart policy, variables) — eso es independiente, ver la sección de Infrastructure as Code más abajo. Un push solo reconstruye y redeploya el código de la app con la configuración que ya esté puesta en Railway.

---

## Cómo prender y apagar los servicios (ahorrar costos)

Railway cobra por cómputo mientras el contenedor está corriendo. Si en algún momento no necesitás el bot activo (por ejemplo, una pausa larga sin uso), podés escalar los servicios a **0 réplicas** en vez de borrar nada — apaga el cómputo pero deja toda la configuración (variables, start command, etc.) intacta para prenderlo de nuevo cuando quieras.

⚠️ **Mientras estén apagados, el bot no responde nada**: ni el webhook de Meta (server apagado) ni los mensajes en cola (worker apagado). No es un modo "espera", es apagado real.

### Apagar
```bash
npx -y @railway/cli@latest scale us-west=0 --service financial-whatsapp-bot
npx -y @railway/cli@latest scale us-west=0 --service incredible-adventure
```
(`us-west` es la región donde están desplegados hoy — corroborá con `railway service list --json` si en algún momento la migran.)

### Prender de nuevo
```bash
npx -y @railway/cli@latest scale us-west=1 --service financial-whatsapp-bot
npx -y @railway/cli@latest scale us-west=1 --service incredible-adventure
```

### Por dashboard (equivalente)
Servicio → **Settings → Scaling/Regions** → bajar/subir el número de réplicas de esa región (`0` para apagar, `1` para prender).

### Notas
- **Redis no hace falta apagarlo** para ahorrar la mayor parte del costo — el cómputo de los dos servicios (web + worker) es lo que más pesa. Si igual querés apagarlo: `npx -y @railway/cli@latest scale us-west=0 --service Redis`. Los datos del volumen (`redis-volume-LX-G`) no se borran al parar el contenedor — al prenderlo de nuevo la cola de jobs pendiente sigue ahí.
- **`.railway/railway.ts` va a quedar desactualizado** (dice `replicas: { "us-west2": 1 }` para los tres recursos). Si corrés `railway config apply` mientras algo está en 0 réplicas, va a "corregir" eso de vuelta a 1 y prenderlo sin que lo pidas — tenelo presente si tocás infraestructura mientras el bot está apagado a propósito.

---

## Camino A — Todo por el dashboard (sin CLI)

Para el día a día (cambiar una variable, ver logs, redeployar) el dashboard alcanza y es más simple. La CLI/`.railway/railway.ts` importa sobre todo para cambios estructurales (start command, healthcheck, restart policy, agregar un servicio nuevo).

### Deployar un cambio de código
1. Pusheá a la rama conectada (`main`) del repo `Nkolasbbx/financial-whatsapp-bot`. Railway detecta el push y dispara el build/deploy solo (para ambos servicios, si el push tocó código que ambos comparten — Railway no distingue por carpeta acá porque los dos apuntan al mismo Root Directory).
2. Si necesitás forzar un redeploy sin push nuevo: dashboard → servicio → pestaña **Deployments** → en el último deployment, botón **⋮ → Redeploy**.

### Ver logs
Dashboard → servicio → pestaña **Deployments** → click en el deployment activo → logs en vivo (deploy logs / HTTP logs según el service).

### Variables de entorno
Dashboard → servicio → pestaña **Variables**.
- Para el server (`financial-whatsapp-bot`): los valores se cargan directo (texto plano o secreto).
- Para el worker (`incredible-adventure`): en vez de tipear el valor, usá una **referencia** a la variable del server: click en "+ New Variable" → nombre igual al del server → como valor escribí `${{financial-whatsapp-bot.NOMBRE_VAR}}` (reemplazando `NOMBRE_VAR`). Así se mantiene sincronizada si rotás el secreto en el server.
- Ver la lista completa de variables necesarias más abajo.
- Cualquier cambio de variables dispara un redeploy automático del servicio afectado (salvo que uses "skip deploy" por CLI, ver Camino B).

### Start command / healthcheck / restart policy
Dashboard → servicio → **Settings → Deploy**:
- **Start Command:**
  - `financial-whatsapp-bot`: `uvicorn main:app --host 0.0.0.0 --port $PORT`
  - `incredible-adventure`: `arq worker.WorkerSettings`
- **Healthcheck Path** (solo `financial-whatsapp-bot`, el worker no tiene HTTP): `/`, timeout 30s.
- **Restart Policy:** `ON_FAILURE`, Max Retries `10` — en **ambos** servicios. Esto es lo que hace que el worker se reinicie solo si Redis tiene un blip de red en vez de quedar "Crashed" para siempre (ver `redis_settings.py` para el hardening del lado de la app que esto complementa).
  - **Nota:** a la fecha de esta guía, aplicar este campo vía `railway config apply` (Camino B) no lo persiste (bug de Railway, confirmado). Configuralo acá, a mano, hasta que lo arreglen.

### Root Directory (solo la primera vez, al crear un servicio)
Dashboard → servicio → **Settings → Source → Root Directory** = `/financial-whatsapp-bot` (**con la barra inicial**; sin ella Railway analiza la raíz del repo entero y falla con "could not determine how to build the app"). Después de cambiarlo hace falta un redeploy manual para que tome efecto.

### Generar/ver el dominio público (solo para `financial-whatsapp-bot`)
Dashboard → servicio → **Settings → Networking → Public Networking → Generate Domain**. Ese dominio + `/webhook/whatsapp` es la URL que va en la config del webhook de Meta (ver `main.py` / `routers/webhook.py`: el router no tiene prefijo, así que el path completo siempre es `/webhook/whatsapp`).

---

## Camino B — CLI + Infrastructure as Code (`.railway/railway.ts`)

### Requisitos
- **Node.js 22+** — la CLI evalúa `.railway/railway.ts` con soporte nativo de TypeScript de Node, que solo existe desde la v22. Si tenés Node más viejo (`node --version`), los comandos `railway config *` van a fallar con `node: bad option: --experimental-strip-types`. El resto de comandos de la CLI (`login`, `link`, `variable`, `domain`, `logs`, `status`, `redeploy`...) funcionan con cualquier Node.
  - Si no querés instalar Node 22 en el sistema, se puede descargar standalone y anteponerlo al PATH solo para el comando puntual:
    ```bash
    curl -fsSL -o /tmp/node22.tar.xz https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.xz
    tar -xf /tmp/node22.tar.xz -C /tmp
    PATH="/tmp/node-v22.14.0-linux-x64/bin:$PATH" railway config plan
    ```
- El paquete npm `railway` instalado en la raíz del repo (`package.json` + `node_modules/railway`) — es una dependencia real en tiempo de ejecución de `railway config plan/apply` (no solo para autocompletado del editor), por eso este repo Python tiene un `package.json` mínimo. `node_modules/` está en `.gitignore`; si lo borrás, reinstalalo con `npm install`.
- La CLI se puede correr sin instalarla globalmente: `npx -y @railway/cli@latest <comando>`.

### Login y link (una sola vez por máquina)
```bash
npx -y @railway/cli@latest login     # abre el navegador
npx -y @railway/cli@latest link      # elegí: workspace → stellar-recreation → production → un servicio cualquiera (da igual cuál, "link" es solo para tener sesión activa en la carpeta)
npx -y @railway/cli@latest status    # confirma proyecto/ambiente/servicios linkeados
```
El link queda guardado en `~/.railway/config.json` (fuera del repo), no hace falta repetirlo salvo que cambies de máquina.

### Editar infraestructura (start command, healthcheck, restart policy, agregar servicio, etc.)
1. Editá `.railway/railway.ts` a mano.
2. Previsualizá el diff (no aplica nada):
   ```bash
   npx -y @railway/cli@latest config plan
   ```
   Revisá que diga `0 to destroy` antes de aplicar — si aparece algo como "Delete variable ..." o "Delete resource ...", es porque el archivo quedó desactualizado respecto de lo que hay en Railway (alguien cambió algo a mano en el dashboard, o vía CLI, después del último `pull`). Corré `railway config pull --force` primero, revisá el diff resultante contra tus cambios, y recién ahí aplicá.
3. Aplicá:
   ```bash
   npx -y @railway/cli@latest config apply --yes
   ```
4. Si tocaste algo que no sea `restartPolicy*` (esos dos campos no persisten, ver nota arriba), `railway config plan` debería dar `0 to change` después del apply.

### Traer a `.railway/railway.ts` lo que ya existe en Railway
Usalo si sospechás que el archivo del repo quedó desincronizado (por ejemplo, alguien tocó variables desde el dashboard):
```bash
npx -y @railway/cli@latest config pull --force
```
Esto **sobreescribe** `.railway/railway.ts` con el estado real (nombres de servicio, `rootDirectory`, variables como `preserve()`). Ojo: por algún motivo `pull` no trae `build`/`deploy` (start command, healthcheck, restart policy) — hay que volver a agregarlos a mano comparando con la sección de arriba, o con un `git diff` contra la versión anterior del archivo si no cambiaron.

### Variables por CLI
```bash
npx -y @railway/cli@latest variable list --service financial-whatsapp-bot --json   # incluye valores en crudo, no compartir el output
npx -y @railway/cli@latest variable set NOMBRE=valor --service financial-whatsapp-bot
npx -y @railway/cli@latest variable set NOMBRE='${{financial-whatsapp-bot.NOMBRE}}' --service incredible-adventure --skip-deploys
```
`--skip-deploys` evita que cada `set` dispare un redeploy — útil si vas a setear varias variables seguidas; después redeployá una sola vez:
```bash
npx -y @railway/cli@latest redeploy --service incredible-adventure --yes
```
**Importante:** si agregás/cambiás una variable por CLI en el worker, actualizá también el bloque `env` de `incredibleAdventure` en `.railway/railway.ts` con la misma referencia (`financialWhatsappBot.env.NOMBRE`) — si no, el próximo `railway config apply` la va a marcar como "Delete variable" y la borra, aunque exista en Railway. Este repo ya pasó por eso una vez.

### Dominio del webhook
```bash
npx -y @railway/cli@latest domain --service financial-whatsapp-bot   # crea uno si no existe, o lo muestra si ya existe
```
URL completa para Meta: `https://<dominio-generado>/webhook/whatsapp` (GET para la verificación inicial con `META_WEBHOOK_VERIFY_TOKEN`, POST para los mensajes entrantes).

### Logs y estado
```bash
npx -y @railway/cli@latest status                                              # servicios, deployments activos
npx -y @railway/cli@latest logs --deployment --service incredible-adventure    # logs de deploy/runtime del worker
npx -y @railway/cli@latest logs --http --service financial-whatsapp-bot        # logs HTTP del server
npx -y @railway/cli@latest redeploy --service <nombre> --yes                   # redeploy sin rebuild forzado
npx -y @railway/cli@latest restart --service <nombre>                          # restart sin rebuild
```

---

## Variables de entorno (las 32 que usa la app)

Todas viven en `financial-whatsapp-bot`; el worker las referencia todas desde ahí (ver `.railway/railway.ts`). `config.py` tiene el detalle de cada una y sus defaults.

| Variable | Para qué |
|---|---|
| `REDIS_URL` | Conexión a la cola de arq (server y worker). Sin esto cae a `localhost:6379` y el worker no arranca. |
| `DB_DSN` | Postgres/Supabase para búsqueda RAG. |
| `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | Cliente de Supabase (normal y admin). |
| `META_WHATSAPP_TOKEN`, `META_PHONE_NUMBER_ID`, `META_WABA_ID`, `META_APP_SECRET`, `META_GRAPH_API_VERSION`, `META_WEBHOOK_VERIFY_TOKEN` | WhatsApp Cloud API (Meta) — envío y verificación del webhook. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER` | Alternativa a Meta si `WHATSAPP_PROVIDER=twilio`. |
| `WHATSAPP_PROVIDER` | `meta` o `twilio`. |
| `OLLAMA_URL`, `OLLAMA_MODEL` | Modelo de IA para las respuestas (o Groq si la URL contiene `groq.com`). |
| `IA_API_KEY` | Key del proveedor de IA. |
| `RES_URL`, `RES_MODEL`, `RES_KEY` | Modelo de resúmenes (Groq). |
| `HF_TOKEN`, `EMBEDDING_MODEL_NAME` | Modelo de embeddings (Hugging Face) para RAG. |
| `DEBUG` | Habilita modo debug/Twilio local. |
| `CRON_SECRET` | Autoriza el cron de recordatorios (`Authorization: Bearer ...`). |
| `REMINDERS_ENABLED`, `REMINDER_DAYS`, `REMINDER_BATCH_SIZE`, `REMINDER_TEMPLATE_NAME`, `REMINDER_FINAL_TEMPLATE_NAME`, `REMINDER_TEMPLATE_LANGUAGE`, `REMINDER_RECIPIENT_LABEL`, `REMINDER_TIMEZONE` | Sistema de recordatorios automáticos. |

---

## Troubleshooting (cosas que ya pasaron acá)

- **`redis.exceptions.ConnectionError` a `localhost:6379` en el worker** → `REDIS_URL` no está seteada en `incredible-adventure`. Sintomática de que el worker no tiene ninguna variable de entorno configurada — revisar `railway variable list --service incredible-adventure` (o Variables en el dashboard).
- **El botón "Deploy" del dashboard no arranca y no se puede descartar el aviso** → el servicio todavía usaba Config as Code (`railway.web.json`/`railway.worker.json`) y Railway empezó a bloquear deploys de servicios no migrados. Solución: migrar ese servicio a `.railway/railway.ts` (Camino B) o configurar sus settings de deploy directo en el dashboard (Camino A) y limpiar el campo "Config as Code Path" en Settings → General.
- **`railway config migrate` no encuentra nada** → solo busca archivos llamados literalmente `railway.json`/`railway.toml`; no reconoce nombres custom como `railway.web.json`.
- **`restartPolicyType`/`restartPolicyMaxRetries` nunca aparecen en `railway config pull`/`plan`, ni configurados a mano en el dashboard** → confirmado: se configuró ON_FAILURE/10 manualmente en el dashboard en ambos servicios, y aun así `railway config pull --force` no trae ese campo (ni como valor seteado, ni de ninguna forma), y `railway config plan` lo sigue mostrando como pendiente (`null → 10`, `null → "ON_FAILURE"`) para siempre. No es un problema de tu configuración — la herramienta de IaC (`pull`/`plan`/`apply`) directamente no lee ni escribe ese campo todavía, aunque el campo sí existe y funciona en el dashboard y en el schema del SDK (`DeployConfig.restartPolicyType`). **No perseguir esto con más `apply`s** — confiar en lo que muestra el dashboard (Settings → Deploy → Restart Policy) como fuente de verdad para este campo puntual, ignorando el diff de `plan`.
- **`Unknown region 'ams'` al correr `railway scale`** → el código de región interno que usa el grafo/IaC (`us-west2`, `ams`, etc.) no es el mismo que espera `railway scale`/`railway service scale`, que quiere el slug "humano" (`us-west`, `eu-west`, `us-east`, `southeast-asia`). Confirmá la región real con `railway service list --json` (campo `regions[].name`, ej. `us-west2`) y mapeala al slug correspondiente antes de escalar.
- **`node: bad option: --experimental-strip-types`** → Node del sistema es menor a 22; ver sección de requisitos del Camino B.

## Pendientes
- Borrar `railway.web.json` / `railway.worker.json` del repo (ya no hacen nada, pero no molestan hasta 2026-12-01).
- Confirmar en el dashboard que el campo "Config as Code Path" de ambos servicios quedó vacío.
- Revisar cada tanto si Railway ya arregló que `pull`/`plan`/`apply` lean `restartPolicy*` (correr `railway config pull --force` y mirar si el campo aparece en el archivo generado). Mientras tanto, el dashboard es la única fuente de verdad confiable para ese campo puntual.
