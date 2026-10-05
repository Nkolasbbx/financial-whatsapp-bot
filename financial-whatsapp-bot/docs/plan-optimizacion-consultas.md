# Plan de optimización de consultas y evaluación

Estado: propuesta, sin cambios en la lógica ni en la base de datos.
Fecha: 27 de septiembre de 2026.

## Objetivo

Reducir los accesos repetidos a Supabase y el tiempo de espera, conservando los mensajes, movimientos, permisos y estados de conversación. Medir por acción completa del usuario, además de por endpoint, para que mover trabajo al worker no parezca una reducción artificial.

Hay tres medidas distintas: solicitudes del backend a Supabase, sentencias SQL ejecutadas y tiempo total de la operación. Una RPC puede reducir viajes de red sin reducir las sentencias o el trabajo de PostgreSQL. Ejecutar dos consultas en paralelo tampoco reduce su cantidad.

## Diagnóstico del código actual

Los siguientes conteos proceden de inspección estática del camino exitoso. No son mediciones de producción; excluyen reintentos y deben verificarse con instrumentación.

| Recorrido | Accesos observados en código | Oportunidad |
| --- | --- | --- |
| Worker de respuesta IA, sin RAG ni resumen adicional | 10: perfil (1), historial (2), guardar pregunta (2), guardar respuesta (2), contar mensajes (2), guardar perfil (1) | Reutilizar user_id, evitar guardar un perfil sin cambios y sustituir el conteo completo |
| GET del dashboard financiero | 3: perfil, resumen mensual y lista de movimientos | Unificar resumen y lista, manteniendo la verificación de identidad |
| Apertura completa de Finanzas | 4: perfil al servir HTML y los 3 accesos de la API | Revisar después si la sesión puede aportar un user_id validado |
| Registrar movimiento web y refrescar resumen | 5: perfil e inserción, más los 3 accesos del dashboard | Reutilizar contexto y mejorar la consulta del dashboard |
| Seguimiento de N documentos pendientes | N consultas por ronda, aproximadamente cada 3 segundos más la duración de las solicitudes | Consultar los estados en un lote |

Además, `services/message_router.py` consulta sesiones de finanzas y calendario antes de decidir si un mensaje corresponde a ellas. La detección de fondos también puede consultar una sesión, y su manejador volver a leerla.

Referencias de código: `db/users.py`, `core/ia.py`, `services/message_router.py`, `core/fund_flow.py`, `routers/portal_finances.py`, `routers/portal.py`, `static/admin_documentos.js` y `routers/admin.py`.

## Etapa 1. Medir antes de optimizar

Instrumentar los accesos reales de los dos clientes de Supabase y del pool PostgreSQL usado por RAG/documentos. Registrar lectura, escritura y RPC, duración, resultado y reintentos. Medir Redis aparte.

Asignar un identificador de operación que viaje desde el webhook hasta el job del worker. Los procesos tienen memoria separada: ese identificador debe ir en los argumentos del job. Usar un contexto local a cada solicitud/tarea, no un contador global compartido. Identificar también la generación de resúmenes que deriva de una conversación.

Guardar registros estructurados con escenario, operación de base de datos, duración y resultado. No registrar teléfonos, textos, tokens ni parámetros sensibles. Los identificadores de operación sirven para correlacionar registros, no como etiquetas de métricas de alta cardinalidad.

Entregable: reporte inicial por escenario, con lecturas, escrituras, RPC, latencias y errores.

## Etapa 2. Eliminar repeticiones dentro de cada operación

1. Pasar el `user_id` ya obtenido a lectura de historial, guardado de mensajes, búsqueda del último mensaje y conteo. Mantener adaptadores por teléfono donde hagan falta. Se eliminan cuatro búsquedas de ID en el recorrido habitual de IA.
2. Quitar el `save_user` final de la respuesta IA cuando el perfil no cambió. Cuando exista una modificación, actualizar solo esos campos. Esto también evita sobrescribir cambios concurrentes con una copia antigua del perfil.
3. Sustituir el conteo completo del historial por un contador/checkpoint persistido y actualizado atómicamente junto con la inserción. El trabajo de resumen debe ser idempotente, recuperable y registrar hasta qué mensaje resumió. Como mejora intermedia, pedir solo el conteo, sin descargar los IDs.
4. Pasar las sesiones ya consultadas a los manejadores; evitar volver a buscar el mismo estado en la misma operación.

No conservar un perfil potencialmente obsoleto desde el webhook hasta un worker que podría ejecutar mucho después: reutilizar identidad y leer estado vigente cuando sea necesario. Tampoco agrupar pregunta y respuesta retrasando el guardado de la pregunta hasta que termine la IA; ese comportamiento ya provoca pérdidas cuando falla la generación.

Meta inicial para el bloque del worker descrito: pasar de 10 a aproximadamente 4–5 accesos. Si cambia el punto donde se guardan mensajes, comparar también el recorrido completo webhook + worker para mantener una medición honesta.

## Etapa 3. Agrupar lecturas y simplificar el enrutamiento

- **Finanzas:** una RPC que devuelva resumen y página de movimientos del mes. Calcular los totales sobre todos los movimientos del período, aunque la lista tenga límite. Mantener filtros por usuario, estado y fechas. Meta: 3 a 2 viajes a Supabase para la API, conservando la lectura del perfil.
- **Documentos:** un endpoint que consulte varios estados, filtrados por la comuna autenticada. Meta: N consultas a 1 por ronda. Detener el seguimiento cuando termine y reducir la frecuencia mientras la pestaña esté oculta.
- **Sesiones:** obtener las sesiones necesarias juntas, o evolucionar hacia un estado conversacional único que identifique el módulo activo. Probar expresamente respuestas ambiguas como “sí”, “no” y números. No omitir la consulta del estado solo porque el mensaje no mencione el módulo.
- **Recordatorios:** revisar las secuencias de leer usuario y luego actualizarlo. Reutilizar datos cuando sigan vigentes o ejecutar la decisión y el cambio en una operación atómica.

Agrupar solicitudes no garantiza consultas SQL más baratas: revisar el plan de ejecución y los tiempos del servidor después de cada cambio.

## Etapa 4. Consultas e índices

Revisar los índices reales antes de proponer nuevos; las migraciones financieras ya contienen índices por usuario, fecha y estado. Evaluar el historial por `(user_id, created_at)` y, si corresponde, una variante para el último mensaje de usuario. No añadir índices duplicados.

Usar planes de ejecución sobre datos representativos y observar filas procesadas, buffers, tiempo y posibles bloqueos. Paginar historiales/listados, seleccionar solo columnas utilizadas y evitar devolver datos completos para obtener un conteo.

Esta etapa reduce coste por consulta; no necesariamente la cantidad de consultas. Considerar también el coste adicional de mantener índices al escribir.

## Etapa 5. Caché selectiva, solo si las mediciones lo justifican

Empezar por catálogos de fondos y contenido relativamente estable. Para información por usuario, usar claves separadas por usuario/período y una política explícita de invalidación.

Si se cachea un resumen financiero, invalidarlo después de crear, corregir o eliminar un movimiento desde web y WhatsApp. Una edición de fecha debe invalidar el mes anterior y el nuevo. Evitar que una lectura concurrente vuelva a publicar un valor anterior a la escritura; usar versiones de clave u otra estrategia verificable. La respuesta posterior a guardar debe reflejar el cambio inmediatamente.

Redis ya está disponible: posteriormente puede alojar el límite de mensajes mediante una operación atómica, preservando sus ventanas, excepciones y notificaciones. Medir tanto la carga retirada de PostgreSQL como la añadida a Redis.

Mantener Supabase como fuente persistente. Probar vencimiento y caída de caché; no basar autorizaciones o confirmaciones de guardado en datos desactualizados.

## Métricas

| Métrica | Cómo obtenerla | Interpretación |
| --- | --- | --- |
| Accesos a Supabase por acción | Contadores en el backend, incluyendo worker y reintentos | Principal medida de consultas eliminadas |
| Lecturas, escrituras y RPC por separado | Etiquetas de operación de baja cardinalidad | Evita ocultar que aumentaron las escrituras |
| Tiempo acumulado de accesos a BD | Temporizadores de cada acceso | Incluye red; si hay paralelismo no equivale al tiempo total de la acción |
| Latencia p50/p95 por escenario | Backend y prueba de carga | p95: el 95 % de las operaciones termina dentro de ese tiempo |
| Espera de cola, IA y envío | Temporizadores independientes | Separa la base de datos de factores externos |
| Llamadas y tiempo SQL | Diferencias de `pg_stat_statements` antes/después | Mide trabajo del servidor, no latencia de red |
| Acciones correctas por segundo | Prueba de carga con comprobaciones | Mide capacidad útil, no solo respuestas HTTP |
| Errores, duplicados, pérdidas y datos desactualizados | Respuestas y comparación con datos esperados | La optimización no debe degradar la corrección |
| Aciertos/fallos de caché y accesos a Redis | Instrumentación de caché | Evalúa si la caché realmente evita trabajo |

Para inspeccionar las consultas más costosas, si la extensión está habilitada y accesible:

```sql
SELECT queryid, calls, total_exec_time, mean_exec_time, rows, query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
```

Tomar instantáneas al comienzo y al final, comparar por queryid/usuario/base de datos y calcular diferencias de llamadas y tiempo total. Las estadísticas son acumuladas: una media histórica no representa una ejecución concreta de la prueba. Si hubo un reinicio de estadísticas, descartar esa comparación. No reiniciar las estadísticas de producción para ejecutar el benchmark. Las sentencias internas de RPC pueden depender de la configuración de seguimiento; interpretar los resultados junto con la instrumentación del backend.

## Protocolo de prueba antes/después

1. Preparar un entorno de pruebas con la misma configuración y volumen representativo. Incluir usuarios con historial pequeño y grande, meses vacíos y con muchos movimientos. Usar usuarios de prueba distintos y mantener fijo el conjunto de datos entre comparaciones.
2. Ejecutar escenarios separados: consulta IA, botón de menú, completar hito, consulta financiera, alta de ingreso/gasto, conversación financiera en varios pasos y seguimiento de 10 documentos. Incluir un escenario específico que dispare resumen de conversación.
3. Simular IA, embeddings y envío a WhatsApp para aislar el coste de base de datos; mantener Supabase real de pruebas. Después ejecutar una muestra pequeña con los servicios reales para comprobar integración. No generar mensajes reales a usuarios durante la carga.
4. Para el webhook, generar IDs únicos, esperar a que los jobs terminen y medir respuesta completa. Un HTTP 200 de recepción no demuestra que el worker acabó. Medir reenvíos del mismo ID por separado para comprobar deduplicación.
5. Ejecutar cargas de 1, 5, 10 y 20 usuarios concurrentes, dentro de la capacidad del entorno. Propuesta inicial: 1 minuto de calentamiento y 5 minutos medidos por nivel, tres repeticiones. Ajustar duración si no hay suficientes muestras para un p95 estable. Usar la misma tasa de llegada y distribución de acciones antes/después; registrar las operaciones que no pudieron iniciarse o completar.
6. Probar caché vacía y caliente por separado. No repetir todas las acciones con un solo teléfono: el bloqueo por usuario serializaría el trabajo y el limitador puede rechazar solicitudes. Mantener esos mecanismos y reportar sus rechazos por separado.
7. Añadir pruebas funcionales de permisos, idempotencia, borradores de WhatsApp, cambios web/WhatsApp simultáneos e invalidación después de escribir. Mantener pruebas de presupuesto máximo de accesos para los escenarios deterministas.
8. Repetir tras cada etapa y guardar el reporte con versión del código, configuración, volumen de datos, concurrencia y resultados. Revertir o ajustar la etapa si reduce solicitudes pero empeora SQL, latencia o corrección.

k6 puede generar carga HTTP, medir latencias y aplicar umbrales automáticos. Para el worker, complementar con la correlación y medición del backend. Los mocks unitarios verifican presupuestos de accesos, pero no prueban rendimiento real de Supabase.

## Criterios iniciales de aceptación

- Cero pérdidas, duplicados, accesos entre usuarios o saldos incorrectos en los casos de prueba.
- Alcanzar los presupuestos de accesos por escenario indicados, o documentar una razón funcional para revisarlos.
- Meta propuesta de reducción del p95 del tramo de backend sin IA: al menos 25 % en escenarios optimizados, por confirmar con la línea base. No prometer igual reducción del tiempo total de respuesta del modelo.
- Menos de 1 % de errores operativos inesperados en la carga acordada, sin empeorar la línea base; cero errores en las pruebas funcionales deterministas. Rechazos esperados de permisos/validación se miden aparte.
- El dato recién guardado aparece inmediatamente en el resumen correspondiente.

Formato del reporte: escenario, versión, accesos/acción, lecturas, escrituras, RPC, p50, p95, errores, acciones correctas/segundo y verificación de integridad.

Ejemplo únicamente aritmético: pasar de 10 a 5 accesos equivale a una reducción del 50 %. La fórmula es `(antes - después) / antes × 100`; aplicar a resultados comparables, no a cifras de escenarios distintos.

## Orden recomendado

Instrumentación → reutilización de identidad y eliminación de escrituras redundantes → consultas agrupadas de Finanzas y documentos → sesiones e índices → caché selectiva. Cada etapa debe entregar su comparación antes/después antes de continuar con la siguiente.

## Fuentes técnicas

- [Supabase: pg_stat_statements](https://supabase.com/docs/guides/database/extensions/pg_stat_statements)
- [Supabase: asesor de índices](https://supabase.com/docs/guides/database/extensions/index_advisor)
- [Grafana k6: métricas](https://grafana.com/docs/k6/latest/using-k6/metrics/)
- [Grafana k6: umbrales de aprobación](https://grafana.com/docs/k6/latest/using-k6/thresholds/)
