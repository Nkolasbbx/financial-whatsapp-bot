# E4 — Investigación de mecanismos de web scraping para fondos y normativas

> Entregable E4 del Spike "Investigación de migración a WhatsApp Business API y preparación de información para RAG" (Backlog Sprint 1). Documento de investigación y diseño — no incluye implementación de código.

## 1. Objetivo

Definir cómo mantener actualizada, sin depender 100% de carga manual, la información que hoy alimenta:

- La tabla `fondos` en Supabase (leída por `core/fondos.py` para el simulador de HdU05) — nombre, monto, fecha de cierre, requisitos.
- La tabla `documents` en Postgres/pgvector (leída por `core/ia.py` para el RAG) — normativas y trámites municipales/SII.

## 2. Resumen ejecutivo

| Fuente | robots.txt (verificado en vivo) | Veredicto |
|---|---|---|
| SERCOTEC (`www.sercotec.cl`) | Permite todo salvo `/wp-admin/`. Publica sitemap (`/wp-sitemap.xml`). | ✅ Scraping-friendly |
| CORFO (`www.corfo.cl`) | **Prohíbe explícitamente `/sites/cpp/convocatorias/`** | ⚠️ Esa ruta específica no debe scrapearse — ver §3.2 |
| SII (`www.sii.cl`) | No publica `robots.txt` (404) | ⚠️ Sin restricción explícita, pero es la autoridad tributaria: tratar con máxima cautela y alcance mínimo |
| Municipalidad de El Bosque (`www.municipalidadelbosque.cl`) | `Allow: /` total, 5 sitemaps publicados | ✅ Muy scraping-friendly |
| Municipalidad de Recoleta | Ver hallazgo colateral en §6 — **el dominio usado hoy en el código no resuelve** | ⚠️ Corregir el dominio antes de scrapear nada |

## 3. Fuentes objetivo, en detalle

### 3.1 SERCOTEC — fondos concursables

- **Qué se necesita**: nombre del programa, monto máximo, fecha de cierre de convocatoria, requisitos, link — mismos campos que ya modela `FONDOS_FALLBACK` en [core/fondos.py](financial-whatsapp-bot/core/fondos.py).
- **robots.txt** (`https://www.sercotec.cl/robots.txt`, verificado):
  ```
  User-agent: *
  Disallow: /wp-admin/
  Allow: /wp-admin/admin-ajax.php
  Sitemap: https://www.sercotec.cl/wp-sitemap.xml
  ```
  Sitio WordPress estándar, sin restricciones sobre el contenido público. El sitemap XML es un buen punto de entrada para descubrir todas las páginas de programas sin tener que rastrear el menú de navegación a mano.
- **Complejidad técnica**: baja-media. Listado de programas parece HTML servido por el propio WordPress (no una SPA); se puede resolver con `requests` + `BeautifulSoup`. Si algún detalle de convocatoria carga por JS, subir a Playwright solo para esa página puntual.

### 3.2 CORFO — fondos concursables

- **robots.txt** (`https://www.corfo.cl/robots.txt`, verificado):
  ```
  User-agent: *
  Disallow: /sites/cpp/convocatorias/
  ```
  Se confirmó además que `https://www.corfo.cl/sites/cpp/convocatorias/` responde `HTTP 302` (redirección activa) — es decir, es una ruta real y viva, no un resto de configuración vieja. **Esta ruta específica no debe scrapearse**, sea cual sea el mecanismo elegido.
- **Alternativas a evaluar antes de descartar CORFO**:
  1. Buscar si CORFO publica los mismos datos en otra ruta no restringida (portales de convocatorias suelen tener también fichas individuales fuera de `/sites/cpp/convocatorias/`).
  2. Revisar si existe una API o dataset abierto en `datos.gob.cl` con programas CORFO vigentes.
  3. Si ninguna alternativa aparece, dejar CORFO fuera del scraping automático y mantenerlo con carga manual — es preferible a scrapear una ruta explícitamente prohibida.

### 3.3 SII — calendario tributario y normativa

- Las fechas del calendario tributario (F29 día 12, F22 30 de abril, patentes) **no requieren scraping**: son plazos fijados por ley y ya están calculados en código en [core/alertas_tributarias.py](financial-whatsapp-bot/core/alertas_tributarias.py). No hay nada que automatizar ahí.
- Lo que sí podría beneficiarse de actualización periódica es contenido normativo de apoyo (instructivos, guías de trámites) para el RAG.
- **robots.txt**: `https://www.sii.cl/robots.txt` devuelve `404` (el sitio no publica uno). Sin restricción técnica explícita, pero por ser la autoridad tributaria conviene: limitarse a páginas de ayuda/instructivos públicos (no intentar nada que roce autenticación ni trámites transaccionales), y frecuencia baja (mensual, no diaria).

### 3.4 Municipalidades (Recoleta y El Bosque) — permisos y patentes

- **El Bosque** (`https://www.municipalidadelbosque.cl/robots.txt`, verificado):
  ```
  User-agent: *
  Allow: /
  Sitemap: https://www.municipalidadelbosque.cl/sitemap-1.xml
  Sitemap: https://www.municipalidadelbosque.cl/sitemap-2.xml
  ... (5 sitemaps en total)
  ```
  Totalmente abierto. Es el mismo dominio que ya usa `Ingest/data/step3_permisos_el_bosque.md` como `source_url`, así que ya está validado como fuente correcta.
- **Recoleta**: ver hallazgo colateral en §6 antes de scrapear nada — el dominio que hoy usa el proyecto para esta comuna no resuelve.
- **Complejidad técnica**: alta en general para municipios. Sus sitios cambian de estructura con más frecuencia que un ministerio o servicio nacional, y a veces el contenido relevante (requisitos de patente) vive en un PDF descargable en vez de HTML — como ya ocurre con los PDFs que hoy están cargados a mano en `Ingest/docs/`.

## 4. Herramientas evaluadas

| Herramienta | Cuándo usarla | Contras |
|---|---|---|
| `requests` + `BeautifulSoup` | HTML estático (SERCOTEC, El Bosque, Recoleta — todos parecen WordPress) | No ejecuta JS; si el contenido se carga por fetch/AJAX del lado del cliente, no lo ve. |
| Playwright (headless) | Páginas con contenido dinámico (posible en CORFO, o fichas de detalle específicas) | Más pesado en cómputo y tiempo de ejecución; requiere navegador embebido en el contenedor del worker. |
| `pdfplumber` / `PyPDF2` | Documentos oficiales en PDF (como los ya bajados a mano en `Ingest/docs/`) | Extracción de texto desde PDF es frágil ante tablas o layouts complejos; puede requerir limpieza manual posterior igual que hoy. |
| APIs/datasets abiertos (`datos.gob.cl`) | Alternativa a evaluar para CORFO antes de descartarlo (ver §3.2) | Cobertura y actualización desconocidas sin revisarlo primero — pendiente de investigar puntualmente. |

No se evaluó Selenium por ser funcionalmente redundante con Playwright, que además ya es más rápido de configurar en headless dentro de un contenedor.

## 5. Arquitectura de integración propuesta (sin código)

La idea es no inventar un mecanismo nuevo, sino extender los patrones que el proyecto ya usa:

1. **Extracción → normalización**: un extractor por fuente (sercotec, el_bosque, recoleta, ...) que devuelve registros ya en el shape que espera cada tabla destino:
   - Fondos → mismas columnas que lee `core/fondos.py:_get_fondos_from_supabase()` (`nombre`, `emoji`, `link`, `monto_max`, `fecha_cierre`, `activo`, `requisitos`).
   - Normativas → mismo pipeline que ya usa `Ingest/ingest_supabase.py`: limpiar texto, trocear, generar embedding con `intfloat/multilingual-e5-base` (prefijo `passage:`), insertar en `documents` con su `metadata` (`comuna`, `source`, `source_url`, `source_date`).
2. **Idempotencia**: antes de regenerar embeddings o reinsertar una fila, comparar por hash de contenido o por URL — para no volver a pagar el costo de embeddings (Hugging Face) por contenido que no cambió, siguiendo el mismo criterio de cuidado de costos que ya aplicaron para Redis/Upstash (`docs/redis-resilience-and-cost.md` en el repo de la app).
3. **Disparo periódico**: el proyecto ya corre **arq** para la cola de IA (`worker.py`). arq soporta `cron_jobs` nativos en `WorkerSettings` — es el punto de enganche más natural para un job periódico de scraping, sin necesitar un disparador externo nuevo. Alternativa más simple si se prefiere no tocar el worker: reusar el patrón de endpoint protegido por `CRON_SECRET` que ya existe en `routers/reminders.py`.
4. **Monitoreo**: seguir el mismo patrón de contadores (`sent`/`failed`/`skipped`) que usan `services/reminders.py` y `services/alertas_tributarias.py`, para poder ver de un vistazo si una fuente dejó de responder o cambió de estructura (scraper roto silenciosamente es el riesgo más común de este tipo de sistemas).

## 6. Hallazgo colateral (no relacionado al scraping en sí, pero se detectó durante esta investigación)

Al verificar el dominio municipal de Recoleta que usa el código hoy (`https://www.municipalidadderecoleta.cl/`, referenciado en `core/alertas_tributarias.py` como link de la alerta de patente municipal), **el dominio no resuelve DNS** (`curl: Could not resolve host`). El dominio real y funcionando es `https://www.recoleta.cl/` (HTTP 200, robots.txt estándar de WordPress). Esto significa que el link que hoy se le envía al usuario formalizado en Recoleta en la alerta de patente municipal está roto. No se modificó código para esto — queda reportado para que se decida si se corrige junto con el resto de HdU07.

## 7. Frecuencia de actualización propuesta

| Contenido | Frecuencia sugerida | Motivo |
|---|---|---|
| Fondos (SERCOTEC) | Diaria | Las fechas de cierre importan para las alertas de HdU07/CA2 — un desfase de varios días podría hacer perder una convocatoria. |
| Normativas/permisos municipales | Semanal o quincenal | Cambian con poca frecuencia; no justifica correrlo a diario. |
| Normativa SII de apoyo | Mensual | Plazos legales fijos; solo cambia el instructivo/guía de apoyo, no el calendario. |

## 8. Qué falta para pasar de esta investigación a implementación

- Decidir la alternativa para CORFO (§3.2) antes de excluirlo definitivamente.
- Confirmar si existe alguna limitación de Términos de Servicio más allá de `robots.txt` (ninguno de los sitios revisados publica un ToS que prohíba explícitamente el acceso automatizado a contenido público, pero no se hizo una revisión legal formal).
- Elegir el punto de enganche definitivo (arq `cron_jobs` vs. endpoint + cron externo) según preferencia del equipo.
- Diseñar el shape exacto de metadata para asociar cada documento scrapeado a su fuente/fecha, reusando el que ya define `Ingest/ingest_supabase.py`.
