# Medir antes de optimizar

Esta herramienta registra el comportamiento actual. No optimiza consultas,
no añade endpoints, no necesita migraciones y no modifica el RAG. No envía
mensajes de prueba automáticamente: las interacciones reales pueden guardar
datos y enviar mensajes como siempre. Usar perfiles y registros de prueba.

## Activar en local

Desde la carpeta de la aplicación (la que contiene `main.py`), agregar al `.env`:

```dotenv
PERFORMANCE_METRICS_ENABLED=true
PERFORMANCE_RUN_LABEL=baseline_01
PERFORMANCE_OUTPUT_DIR=local_testing/performance/baseline_01
```

Reiniciar tanto el servidor web como el worker. Las variables de entorno de la
consola tienen prioridad sobre `.env`; comprobar que no tengan valores viejos.
El envoltorio de conteo de Supabase se instala al iniciar cada proceso.

En dos terminales con el entorno virtual del proyecto activado:

```powershell
python -m uvicorn main:app --port 8000 --log-level info
```

```powershell
python -m arq worker.WorkerSettings
```

Redis y la conexión habitual entre WhatsApp y el webhook deben estar operativos.
Para medir el recorrido de WhatsApp, Meta debe apuntar a esta instancia (por el
túnel local que utilices). Un bot que sigue apuntando a Railway no mide el código
local. No cambiar la configuración de producción solo para hacer estas pruebas.
No medir una corrida con recarga automática o reinicios entre cada mensaje.

En Railway, configurar las variables en web y worker y volver a desplegar ambos.
Los archivos locales son efímeros y cada servicio tiene su propio filesystem:
guardar los logs de AMBOS servicios antes de reiniciar. No crear un endpoint
público para descargarlos. El script también lee exportaciones de logs que
contengan líneas `FINANCIAL_PERF {...}`; si una exportación usa otro formato,
extraer primero el texto de esas líneas.

## Qué se guarda

- Una traza aleatoria, etiqueta de ejecución, tipo de entrada y funcionalidad.
- Tiempo local de procesamiento y duración hasta el último envío aceptado por Meta.
- Tiempo de espera por el turno del mismo teléfono, del enrutamiento y del envío.
- Cantidad de `execute()` a Supabase, duración total y por tabla o función RPC.
- En interpretación financiera: preparación, extracción y espera aproximada en Redis.
- Errores observados, intentos del worker y mensajes sin envío o sin completar.

Los nuevos registros no contienen teléfono, texto, montos, ID de usuario, ID de
mensaje de WhatsApp, parámetros de consulta, URLs, tokens ni detalle de las
excepciones. Las etiquetas deben ser genéricas, sin datos personales o secretos.
Los logs anteriores del proyecto conservan su comportamiento; esta herramienta
no los anonimiza. No compartir una exportación completa sin revisarla.

Hay salida en consola y, si se configuró `PERFORMANCE_OUTPUT_DIR`, un archivo
`metrics-<proceso>.jsonl` por proceso. La carpeta `local_testing/` ya está ignorada
por Git. Si se elige otra carpeta, verificar que sus archivos no se publiquen.
Los archivos son acumulativos y no se borran automáticamente.

## Casos sugeridos y condiciones

Mantener un protocolo escrito por ejecución:

| Caso | Acción | Preparación |
|---|---|---|
| Menú | `menú` | Perfil completo, sin conversaciones o formularios activos. |
| Onboarding | Responder una pregunta definida | Mismo paso inicial y datos equivalentes por repetición. |
| Roadmap | `mi roadmap` | Mismo avance; no mezclar con marcar un hito. |
| Avance | Marcar el paso listo | Restaurar el mismo estado de prueba antes de repetir. |
| Fondos | Contestar el mismo requisito | Mismo requisito activo y mismas convocatorias. |
| Calendario | `ver mi calendario` | Misma cantidad de eventos; comprobar el comando disponible. |
| Finanzas | `Gasté 15000 en harina` | Sin borrador previo; no confirmar el guardado si no es necesario. |
| Resumen | `resumen del mes` | Mismo periodo y mismos movimientos. |
| Diagnóstico | Contestar el mismo paso | Mismo estado del borrador y mismo catálogo. |
| Asesor IA | La misma pregunta escrita (ej. `¿qué patente necesito en Recoleta?`) | Mismo historial y mismo resumen; separar preguntas con y sin RAG en etiquetas distintas. |

La agrupación automática es por funcionalidad y tipo de entrada, no por contenido.
Si se comparan acciones distintas dentro de un módulo (consultar roadmap frente
a completar un hito), usar ejecuciones/etiquetas separadas. No mezclar ambas
acciones en una misma mediana y atribuir el resultado a una sola.

1. Anotar commit (`git rev-parse --short HEAD`), fecha, ambiente, región, modelo,
   cantidad de workers, concurrencia, datos y resultado funcional esperado.
2. Usar primero una ejecución de calentamiento con otra etiqueta. Reiniciar al
   cambiar etiquetas, no entre cada repetición. Mantener idéntica configuración
   para la comparación posterior.
3. Repetir cada caso al menos 20 veces para una primera línea base, una respuesta
   completa antes del siguiente mensaje. No desactivar el limitador: espaciar
   las pruebas para respetarlo; sus bloqueos no representan el caso normal.
4. Confirmar manualmente que cada respuesta y escritura sean correctas. Recibir
   un envío aceptado no prueba que su contenido o el resultado de negocio sea correcto.
5. Si se prueba concurrencia, utilizar otra etiqueta y usuarios de prueba distintos.
   Los mensajes del mismo usuario se serializan deliberadamente. No comparar
   una ejecución secuencial con otra concurrente como si fueran equivalentes.

No hay números de línea base hasta realizar estas pruebas contra el ambiente
real. Los tests automáticos usan servicios simulados y no miden Railway.

## Generar el reporte

Ejecutar desde la misma carpeta de la aplicación:

```powershell
python scripts/performance_report.py local_testing/performance/baseline_01 --run baseline_01 --output local_testing/performance/baseline_01.md
```

El reporte muestra muestras totales/válidas, mediana, P95, consultas, errores,
reintentos y etapas. Para combinar logs descargados de Railway:

```powershell
python scripts/performance_report.py local_testing/web.log local_testing/worker.log --run baseline_01 --output local_testing/performance/baseline_01.md
```

Después de optimizar, cambiar etiqueta y carpeta a `after_01`, reiniciar ambos
procesos y repetir el mismo protocolo. Comparar:

```powershell
python scripts/performance_report.py local_testing/performance/baseline_01 --run baseline_01 --compare local_testing/performance/after_01 --compare-run after_01 --output local_testing/performance/comparacion.md
```

Los reportes no requieren dependencias adicionales y no contactan servicios.

## Interpretación y límites

- El tiempo empieza al entrar al manejador del webhook, no cuando la persona
  pulsa enviar en el teléfono. Termina con la aceptación de Meta, no con su
  entrega/lectura. Para movimientos encolados, el aviso inicial no cuenta como
  respuesta final: se necesita también el registro del worker.
- Las consultas del asesor (`__AI_QUERY__` y variantes) aparecen como caso `ai`.
  La traza del web termina en `queued` y la del worker aporta el tiempo final,
  unidas por la traza igual que en finanzas. Etapas del worker: `ai_history`,
  `ai_rag_decision`, `ai_rag` (contiene `ai_embedding` y `ai_rag_query`),
  `ai_personal_context`, `ai_llm`, `ai_persist` y `ai_send`. Son inclusivas: no
  sumar `ai_rag` con `ai_rag_query`. La búsqueda pgvector usa una conexión
  directa a Postgres y no aparece en el conteo de `execute()` de Supabase.
- El worker registra además el retraso de su event loop (líneas con
  `"kind":"loop_lag"`): una espera de 100 ms que despierta 50 ms tarde o más.
  El reporte lo muestra en su propia sección. Valores altos indican trabajo
  síncrono que bloquea a los demás jobs.
- Se miden webhooks de Meta con un solo mensaje. Los lotes y callbacks solo de
  estados se omiten. Twilio y el panel web no forman parte de esta línea base.
- `no_reply` también puede corresponder a duplicados o relé humano: no siempre
  es un error. Separar estos casos del protocolo de respuestas automáticas.
- El conteo representa llamadas al SDK `execute()`, no el número de sentencias
  SQL dentro de un RPC ni los reintentos HTTP internos. Solo cubre las operaciones
  que utilizan los clientes compartidos instrumentados.
- Las etapas son inclusivas y se solapan. No sumar `routing` más `supabase`, ni
  `financial_processing` más `financial_parse`, como si fueran independientes.
- Los tiempos dentro de cada proceso usan un reloj monotónico. La espera de
  Redis y el total web-worker usan reloj de pared, son aproximados y requieren
  relojes sincronizados. Los desfases negativos se señalan y no se comparan.
- Los errores técnicos detectados y trazas incompletas quedan visibles, fuera
  de las medianas de respuestas limpias. Una respuesta de error manejada por el
  negocio puede parecer técnicamente correcta: verificar el contenido manualmente.
- Se unen web y worker por la traza, y los reintentos no se convierten en usuarios
  adicionales. Si falta el log de uno de los procesos, queda incompleta.
- Registrar añade una pequeña sobrecarga. Mantenerlo activado en AMBAS ejecuciones.
  No inferir mejoras solo de una consulta menos: comprobar los tiempos y errores.

Para detener la medición, poner `PERFORMANCE_METRICS_ENABLED=false` y reiniciar
web y worker. No cambia ni borra información en Supabase.
