# ingesta_pos: selección documental para el RAG

Preparación: 15-10-2026, America/Santiago. No se ejecutó una ingesta.

## Contenido

- `nacional/`: siete Markdown de información nacional reutilizable en ambas comunas.
- `el_bosque/`: requisitos municipales publicados y calendario.
- `recoleta/`: alcance nacional aplicable, identificado expresamente como tal.
- `fuentes/`: PDF MINSAL original trasladado como respaldo, sin modificar bytes.
- `_control/`: trazabilidad, exclusiones y manifiesto; documentación administrativa.

Los nueve Markdown de contenido contienen hechos seleccionados y sus límites,
no cuestionarios, rutas de producto, umbrales de capacidad ni presupuestos ficticios.
Son síntesis redactadas para este corpus; no son documentos emitidos por la autoridad.

## Selección para cargar

La lista exacta es `_control/manifiesto.json`, campo `documents`.
El campo `rag_ingest: true` identifica los nueve Markdown de contenido.
No cargar recursivamente toda esta carpeta: README, `_control/` y `fuentes/`
no pertenecen a esa lista. El PDF respalda el resumen; cargar ambos sin una
decisión de deduplicación produciría contenido repetido.

Una fuente consultada respalda las afirmaciones descritas, no certifica aprobación
individual ni ausencia de modificaciones futuras. La fecha `reviewed_at` es de
consulta; `source_publication_date: null` indica fecha editorial no identificada.
Los importes mantienen unidades y periodos; no se fijan valores futuros de UF/UTM.

## Recoleta

No se autenticó la copia municipal de derechos 2026 ni se obtuvo un checklist local
actual completo. Por eso no se incorporan esas tarifas ni requisitos antiguos como
hechos vigentes. El detalle está en `_control/revision_y_exclusiones.md`.

## Traslado y fuentes originales

Solo se traslada el PDF MINSAL que tiene procedencia oficial comprobada.
Los Markdown originales se conservan porque mezclan investigación y diseño.
Los otros tres PDF permanecen en `fuentes_locales/` por sus limitaciones.
Se actualizan los dos enlaces existentes al PDF trasladado.
