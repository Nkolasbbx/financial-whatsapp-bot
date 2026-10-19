from pathlib import Path
import hashlib
import json

WORKSPACE = Path(__file__).resolve().parents[1]
STAGE = WORKSPACE / 'tmp' / 'ingesta_pos_preparada'
DATE = '2026-10-15'
FILES = {}
ENTRIES = []


def document(path, title, scope, urls, body, dates=None):
    metadata = [
        '---', f'title: {json.dumps(title, ensure_ascii=False)}',
        'rag_ingest: true', 'document_type: factual_source_summary',
        f'jurisdiction: {scope}', f'reviewed_at: "{DATE}"',
        'review_scope: "Comprobación de las afirmaciones incluidas; no auditoría integral de cambios posteriores"',
        'source_urls:', *[f'  - {json.dumps(u)}' for u in urls],
        f'source_publication_date: {json.dumps(dates) if dates else "null"}', '---', '',
    ]
    FILES[path] = '\n'.join(metadata) + body.strip() + '\n'
    ENTRIES.append({'path': path, 'jurisdiction': scope, 'source_urls': urls})


document('nacional/01_inicio_sii_y_registro_sociedades.md',
         'Inicio de actividades y Registro de Empresas y Sociedades', 'CL', [
             'https://www.sii.cl/destacados/minegociocumple/index.html',
             'https://www.registrodeempresasysociedades.cl/PreguntasFrecuentes.aspx',
             'https://www.registrodeempresasysociedades.cl/AyudaTarifasNotariasles.aspx'], '''
# Inicio de actividades y Registro de Empresas y Sociedades

Ámbito: nacional; aplicable también a Recoleta y El Bosque.

## Inicio de actividades ante el SII

El SII informa que el inicio de actividades puede realizarse directamente, sin
contratar contador y sin cobro por ese trámite. Esto no establece gratuidad de
permisos municipales, sanitarios o servicios de terceros.

Fuente: [SII, Mi Negocio Cumple](https://www.sii.cl/destacados/minegociocumple/index.html),
apartado sobre la necesidad de contador. Fecha editorial no informada.

## Certificados del Registro de Empresas y Sociedades

Los certificados emitidos por el RES son gratuitos. El Estatuto Actualizado
acredita la constitución y puede complementarse con los certificados de
Anotaciones y de Vigencia. La personería se acredita mediante el Estatuto
Actualizado que identifica la administración.

Fuente: [RES, preguntas frecuentes](https://www.registrodeempresasysociedades.cl/PreguntasFrecuentes.aspx),
sección 5.1. Fecha editorial no informada.

## Firma notarial de actuaciones RES

| Concepto de suscripción | Tarifa publicada |
|---|---|
| Firma electrónica del formulario, por acto ante un mismo notario, cualquiera sea el número de comparecientes | 0,26 UF |
| Revisión e incorporación de documentos necesarios para la firma de cada compareciente, cuando proceda | 0,18 UF |
| Incorporación de otro documento, cuando proceda | 0,07 UF |

Estas tarifas corresponden a actuaciones RES; los cargos documentales dependen
del supuesto descrito y no constituyen un paquete obligatorio para toda empresa.
La UF conserva su unidad original: no se fija aquí una conversión permanente a pesos.

Fuente: [RES, tarifas notariales](https://www.registrodeempresasysociedades.cl/AyudaTarifasNotariasles.aspx),
apartado Suscripción. Fecha editorial no informada.
''')

document('nacional/02_microempresa_familiar.md', 'Microempresa Familiar: condiciones nacionales', 'CL', [
    'https://www.sii.cl/portales/emprendedor/4puntos_claves.html',
    'https://www.bcn.cl/leychile/navegar?idNorma=18967',
    'https://www.sii.cl/preguntas_frecuentes/rut_inicio_actividades/001_105_2094.htm'], '''
# Microempresa Familiar: condiciones nacionales

Ámbito: nacional, incluidas Recoleta y El Bosque. No acredita un negocio particular.

## Condiciones

El SII publica las siguientes condiciones para la MEF:

- La actividad se realiza en la casa habitación familiar.
- Quien la desarrolla debe ocupar legítimamente la vivienda: propia, arrendada o cedida.
- Pueden trabajar hasta cinco personas ajenas a la familia; el límite se refiere a ellas.
- Los activos productivos no pueden superar 1.000 UF; se excluye el valor del inmueble.

Para iniciar actividades bajo esta modalidad, el portal indica gestionar primero
la clasificación municipal y después el inicio ante el SII.

Fuente: [Portal Emprendedor SII](https://www.sii.cl/portales/emprendedor/4puntos_claves.html),
sección Persona Natural/MEF. Fecha editorial no informada.

El artículo 26 de Rentas Municipales excluye actividades peligrosas,
contaminantes o molestas. En viviendas de condominios exige autorización del
Comité de Administración. Las actividades alimentarias siguen sujetas al
Reglamento Sanitario de los Alimentos.

Fuente: [Decreto 2.385, artículo 26](https://www.bcn.cl/leychile/navegar?idNorma=18967).

## Contribuyente con inicio de actividades vigente

Acogerse a MEF en la municipalidad no exige efectuar otro trámite de inicio de
actividades ante el SII: mantiene su calidad de contribuyente y régimen tributario.
La respuesta no elimina obligaciones independientes por otros cambios del negocio.

Fuente: [SII, pregunta 001.105.2094](https://www.sii.cl/preguntas_frecuentes/rut_inicio_actividades/001_105_2094.htm),
actualizada el 09-06-2025.
''')

document('nacional/03_patente_municipal_marco_general.md', 'Patente municipal: monto anual y pago', 'CL', [
    'https://www.bcn.cl/leychile/navegar?idNorma=18967'], '''
# Patente municipal: monto anual y pago

Ámbito nacional; comprende Recoleta y El Bosque. Fuente: Decreto 2.385,
artículos 24 y 29, texto consultado en BCN el 15-10-2026.

## Monto general

El artículo 24 establece un valor por doce meses de entre 2,5 y 5 por mil del
capital propio, con mínimo de 1 UTM y máximo de 8.000 UTM. La tasa concreta
se determina municipalmente dentro del marco legal.

El mismo artículo distingue capital inicial en actividades nuevas y capital
de balance en existentes. Contiene reglas particulares, incluido capital propio
tributario simplificado; no corresponde sustituir esa base por ventas mensuales.

## Periodo y cuotas

El artículo 29 contempla doce meses desde el 1 de julio hasta el 30 de junio
siguiente. Permite pago al contado o en dos cuotas iguales durante julio y enero.
La división en cuotas no convierte el importe anual en dos cobros anuales completos.

Fuente: [BCN, Decreto 2.385](https://www.bcn.cl/leychile/navegar?idNorma=18967).

Esta ficha no determina una tasa local ni incluye derechos de aseo o publicidad.
''')

document('nacional/04_autorizacion_sanitaria_alimentos.md', 'Autorización sanitaria de establecimientos de alimentos', 'CL', [
    'https://www.bcn.cl/leychile/navegar?idNorma=71271'], '''
# Autorización sanitaria de establecimientos de alimentos

Ámbito nacional; aplicable también a establecimientos en Recoleta y El Bosque.
Fuente: Reglamento Sanitario de los Alimentos, DS 977, artículos 6 a 9.

## Autorización y antecedentes

La instalación, modificación estructural y funcionamiento de establecimientos
alimentarios requieren autorización sanitaria según el artículo 6. El artículo 7
regula los antecedentes de la solicitud en función del establecimiento.

## Duración

El artículo 8 establece tres años desde el otorgamiento y prórrogas automáticas
por periodos iguales y sucesivos. Existen excepciones: comunicación de no
continuar y decisión fundada de la autoridad, entre otras situaciones previstas
por el propio artículo. La duración no acredita un cobro de renovación trienal.

## Plazo de resolución

El artículo 9 exige inspección previa y contempla treinta días hábiles desde que
se completan los antecedentes requeridos para resolver la solicitud. El plazo
no se cuenta necesariamente desde la primera consulta del interesado.

Fuente: [BCN, Reglamento Sanitario de los Alimentos](https://www.bcn.cl/leychile/navegar?idNorma=71271).
Los aranceles se documentan por separado; esta ficha no certifica instalaciones.
''')

document('nacional/05_aranceles_sanitarios_2026.md', 'Aranceles sanitarios 2026: alimentos MEF e industrias', 'CL', [
    'https://www.diariooficial.interior.gob.cl/publicaciones/2026/04/09/44420/01/2788376.pdf',
    'https://www.bcn.cl/leychile/navegar?idNorma=275933'], '''
# Aranceles sanitarios 2026: alimentos MEF e industrias

Ámbito nacional, incluidas Recoleta y El Bosque. Los importes siguientes son
aranceles publicados; su aplicación depende de la prestación y del establecimiento.

## Resolución exenta MINSAL 331/2026

Dictada el 26-02-2026 y publicada el 09-04-2026, Diario Oficial, CVE 2788376.
Actualiza aranceles de las resoluciones 96/1996 y 473/2008. Su numeral 3 mantiene
vigente lo no modificado de esas resoluciones y sus modificaciones posteriores.

### Control de alimentos: rama MEF

Página 9, sección 1.4, numeral 1.4.1:

- Valor base para locales MEF: $34.900 CLP.
- Si el capital declarado es igual o inferior a 60 UF, se paga solo ese valor base.
- Por encima de ese umbral se contempla adicional de 0,5 % sobre el capital
  inicial declarado ante el SII, actualizado; se señala arancel máximo de $986.100 CLP.
- La actualización del capital considera variaciones del IPC.

El umbral de 60 UF es arancelario; no reemplaza el límite de activos productivos
para ser MEF. La rama no constituye tarifa general de cualquier negocio alimentario.

### Industrias y talleres

Página 13, sección 1.6, numeral 1.6.1: informe sanitario de instalación,
ampliación o traslado de industrias y talleres. Publica base de $124.300 CLP,
adicional de 0,5 % del capital inicial declarado ante SII actualizado, y máximo
de $986.100 CLP.

Página 13, sección 2.1, numeral 2.1.4: certificado de calificación técnica de
industrias, $107.700 CLP. Es una prestación distinta del informe sanitario.

Fuente: [Diario Oficial, Resolución 331/2026](https://www.diariooficial.interior.gob.cl/publicaciones/2026/04/09/44420/01/2788376.pdf).
Respaldo local: [PDF original](../fuentes/minsal_aranceles_res_331_2026.pdf).

## Beneficio sujeto a acreditación

La Resolución 473, normas generales, literal e), contempla pagar el 20 % del
arancel vigente cuando el titular acredita alguna de las calidades allí indicadas,
incluida certificación municipal MEF. Es un pago del 20 %, no un descuento del 20 %.
No basta declarar que el negocio es pequeño o familiar.

El literal g) regula el cobro cuando hay varias autorizaciones. Los beneficios
temporales del literal h) están delimitados a periodos de 2025. No se establece
aquí acumulación automática de beneficios ni un precio final individual.

Fuente: [BCN, Resolución 473](https://www.bcn.cl/leychile/navegar?idNorma=275933),
normas generales e), g) y h).
''', '2026-04-09')

document('nacional/06_actividades_economicas_sii.md', 'Actividades SII: alimentos, vestuario y diseño', 'CL', [
    'https://www.sii.cl/ayudas/ayudas_por_servicios/1956-codigos-1959.html'], '''
# Actividades SII: alimentos, vestuario y diseño

Ámbito nacional. Selección de filas del catálogo oficial consultado el 15-10-2026.
Las descripciones se abrevian; no sustituyen la glosa completa del SII.

| Código | Descripción abreviada | Afecto a IVA | Categoría tributaria |
|---|---|---|---|
| 107100 | Panadería y pastelería | SI | 1 |
| 107500 | Comidas envasadas con rotulación e información nutricional | SI | 1 |
| 141001 | Confección de vestuario textil y materiales similares | SI | 1 |
| 477102 | Tienda especializada en prendas y accesorios | SI | 1 |
| 477101 | Tienda especializada en calzado | SI | 1 |
| 741001 | Diseño de vestuario | G | G |

La nota G indica que la afectación o categoría dependen de las características
de la actividad. No equivale a una exención.

El código 477101 corresponde a calzado, no a venta de prendas. El 107500
incluye condiciones de envase, rotulación e información nutricional.

Estos indicadores no identifican por sí solos el régimen Pro Pyme ni los
permisos municipales o sanitarios de un establecimiento.

Fuente: [SII, catálogo de actividades económicas](https://www.sii.cl/ayudas/ayudas_por_servicios/1956-codigos-1959.html).
Fecha editorial no identificada.
''')

document('nacional/07_pro_pyme_tasas_transitorias.md', 'Pro Pyme general: tasas transitorias de IDPC y PPM', 'CL', [
    'https://www.sii.cl/normativa_legislacion/circulares/2025/circu53.pdf'], '''
# Pro Pyme general: tasas transitorias de IDPC y PPM

Ámbito nacional. Fuente: Circular SII 53, de 03-09-2025, secciones II.1 y II.2.
Alcance: contribuyentes del artículo 14 letra D N.º 3 de la Ley sobre Impuesto
a la Renta. No es una tasa universal para todo emprendimiento.

## Impuesto de Primera Categoría

| Año comercial | Tasa transitoria IDPC |
|---|---|
| 2025, 2026 y 2027 | 12,5 % |
| 2028 | 15 % |

La circular vincula el beneficio a los hitos de cotización de la Ley 21.735
y explica su cumplimiento hasta 2028. Distingue el régimen transparente
14 D N.º 8, liberado de IDPC; no le aplica esta rebaja.

## Pagos Provisionales Mensuales

- Año de inicio de actividades: 0,125 %.
- Ingresos brutos del giro del año anterior hasta 50.000 UF: 0,125 %.
- Sobre 50.000 UF: 0,25 %.

Estas tasas abarcan ingresos de agosto de 2025 a diciembre de 2027, declarados
desde septiembre de 2025 hasta enero de 2028. La circular no establece PPM
rebajado para el ejercicio 2028.

Fuente: [SII, Circular 53/2025](https://www.sii.cl/normativa_legislacion/circulares/2025/circu53.pdf),
páginas 1 y 4. Son tasas legales condicionadas, no una liquidación del contribuyente.
''', '2025-09-03')

document('el_bosque/01_patentes_comerciales.md', 'El Bosque: requisitos generales y calendario de patentes', 'CL-RM-EL_BOSQUE', [
    'https://www.municipalidadelbosque.cl/tramites-y-beneficios/patentes-comerciales/'], '''
# El Bosque: requisitos generales y calendario de patentes

Comuna: El Bosque. Información publicada en la página municipal de patentes,
consultada el 15-10-2026; sin fecha editorial visible.

## Antecedentes generales publicados

- Recepción final de Obras Municipales.
- Certificado de número y rol de avalúo del inmueble ante SII.
- Fotocopia por ambos lados de la cédula de identidad.
- Contrato de arriendo o autorización del propietario.
- Inicio de actividades ante SII.
- Informe técnico del Departamento de Medio Ambiente, Aseo y Ornato.

Para sociedades, la página añade copia del RUT, escritura o constitución y
cédula del representante legal. Remite al Departamento de Patentes para los
requisitos específicos según el tipo solicitado; no desarrolla una lista
diferenciada para sociedades constituidas mediante RES.

## Plazos publicados

Con requisitos cumplidos, informa obtención aproximada en 15 a 20 días hábiles.
Publica renovación del 10 al 31 de enero y del 10 al 31 de julio de cada año.
La estimación no expresa el tiempo total de formalización.

La fuente no contiene un tarifario completo ni un checklist particular MEF.
El informe técnico mencionado corresponde al departamento municipal indicado.

Fuente: [Municipalidad de El Bosque, Patentes comerciales](https://www.municipalidadelbosque.cl/tramites-y-beneficios/patentes-comerciales/).
''')

# No se presenta como normativa comunal una regla obtenida de una fuente nacional.
document('recoleta/01_alcance_normativa_nacional.md', 'Recoleta: alcance del marco nacional disponible', 'CL-RM-RECOLETA', [
    'https://www.sii.cl/portales/emprendedor/4puntos_claves.html'], '''
# Recoleta: alcance del marco nacional disponible

Esta ficha identifica información nacional aplicable en Recoleta; no contiene
un tarifario ni un procedimiento especial confirmado de la municipalidad.

El SII sitúa la clasificación MEF en la municipalidad correspondiente a la comuna.
Las condiciones nacionales y el tratamiento del inicio vigente se encuentran en
[Microempresa Familiar](../nacional/02_microempresa_familiar.md).

Los aranceles MINSAL tienen ámbito nacional, incluidas instalaciones de Recoleta:
[aranceles sanitarios 2026](../nacional/05_aranceles_sanitarios_2026.md).
No constituyen derechos cobrados por la municipalidad.

Fuente de la gestión municipal MEF: [Portal Emprendedor SII](https://www.sii.cl/portales/emprendedor/4puntos_claves.html),
sección Persona Natural/MEF.
''')

FILES['README.md'] = '''# ingesta_pos: selección documental para el RAG

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
'''

FILES['_control/revision_y_exclusiones.md'] = '''# Revisión y exclusiones: NO INGESTAR

Documento administrativo, 15-10-2026. No forma parte del corpus informativo.

## Contenido excluido

| Material original | Tratamiento |
|---|---|
| 01_fuentes_y_vigencia.md | Registro de localización; los hechos seleccionados se contrastaron con las fuentes primarias. No se ingesta el registro completo. |
| 02_catalogo_costos.md | Se rescatan gratuidades y aranceles respaldados; se omiten IDs C01-C24, fórmulas del producto, cotizaciones y pendientes. |
| 03, 04, 07 | Rutas, metodología, plantillas y cálculos propios: excluidos. |
| 05, 06, 10 | Auditoría, pendientes y validación interna: excluidos. |
| 08, 09 | Se recupera respaldo sanitario/MEF en fichas nacionales; se excluyen escenarios, importes de prueba y orden de tramitación construido para los pilotos. |
| 11, 12, 13 | Umbrales de producto, esquema de datos e implementación: excluidos. |
| 14 | Marco legal nacional seleccionado; sin traducción al motor ni importes de aseo no corroborados. |
| 15 | Códigos e indicadores verificados; sin cuestionario, campos de datos ni selección automática de actividad. |
| 16 | Tasas de Circular 53/2025 seleccionadas; sin proceso del proyecto ni extensión a regímenes distintos. |
| fichas/ completo | Plantillas, guiones, escenarios y decisiones propias: excluidos. |
| README e inventarios originales | Administración: excluidos. |

## Recoleta: pendientes fuera del corpus

### Ordenanza de derechos 2026

Permanece en `../../fuentes_locales/recoleta_ordenanza_derechos_2026.pdf`.
En la portada revisada los campos de número y fecha están vacíos. La afirmación
anterior del inventario sobre número 85 y fecha 28-10-2025 no quedó respaldada
por esa portada. El texto de página 2 sí declara aplicación desde 01-01-2026.
Código impreso de validación: fed60975b0a4fd3; no se validó la firma.

Las referencias de 0,12 UTM por certificado y 3 UTM por cambio de destino quedan
fuera hasta autenticar copia, vigencia y supuesto. No se reconstruye una tasa
local de patente a partir de la referencia histórica de 2022.

### Aseo

El dato de 1,27 UTM no se incorpora: el dossier lo atribuye a un antecedente del
usuario no corroborado. Una noticia sobre propuesta no prueba aprobación.

### Requisitos sin fecha

Permanece `../../fuentes_locales/recoleta_patentes_comerciales_sin_fecha.pdf`.
La página 4 contiene la referencia `codigo potestades 2013-02-26`, que no se
presenta como fecha editorial demostrada. Se excluyen sus listas societarias
tradicionales como checklist actual universal.

## Guía de Economía 2021

Permanece `../../fuentes_locales/guia_formalizacion_economia_2021.pdf`.
Su origen institucional se corroboró en RES, pero se evita incorporar
procedimientos históricos al corpus de respuestas actuales. Los conceptos
rescatados se respaldan con páginas oficiales consultadas para esta preparación.

## Límites de la revisión

Lectura selectiva de normas, páginas y tablas pertinentes; no auditoría integral
de toda la legislación. Se comprueba contenido publicado, no exigibilidad para
un negocio particular. Ningún permiso, tarifa municipal pendiente, presupuesto
ficticio ni nivel de capacidad se convierte en hecho mediante esta selección.
'''

FILES['_control/trazabilidad.md'] = '''# Trazabilidad: NO INGESTAR

Fecha de contraste: 15-10-2026, America/Santiago.

| Markdown de contenido | Documentación de origen | Fuente primaria contrastada |
|---|---|---|
| nacional/01 | 01, 02 | SII Mi Negocio Cumple; FAQ RES sección 5.1; tarifas notariales RES, Suscripción |
| nacional/02 | 08, 01 | SII cuatro puntos, MEF; artículo 26 Rentas Municipales; FAQ SII 001.105.2094 |
| nacional/03 | 14, 02 | Decreto 2.385, artículos 24 y 29, BCN |
| nacional/04 | 08, 01 | DS 977, artículos 6-9, BCN |
| nacional/05 | 08, 09, 02 | Resolución 331/2026, páginas 9, 13 y 18; Resolución 473, normas generales e), g), h) |
| nacional/06 | 15 | Catálogo de actividades SII: códigos seleccionados y nota G |
| nacional/07 | 16 | Circular SII 53/2025, páginas 1 y 4 |
| el_bosque/01 | 14, 01 | Página municipal de Patentes Comerciales, apartados de requisitos, obtención y renovación |
| recoleta/01 | 08, 14 | Alcance nacional MEF, SII; remisión al resumen MINSAL nacional, sin atribución de reglas comunales |

Las URL completas figuran en cada documento y en el manifiesto. Las páginas
web sin fecha editorial identificada llevan `source_publication_date: null`.
La fecha original de un decreto no se presenta como fecha de su última versión.

## Respaldo trasladado

Origen: `../../fuentes_locales/minsal_aranceles_res_331_2026.pdf`.
Destino: `../fuentes/minsal_aranceles_res_331_2026.pdf`.
SHA-256: `56b89573b8803d0f44b141d7f903a457fca56e364f4174e02de3e62705911974`.
Procedencia: Diario Oficial, publicación 09-04-2026, CVE 2788376.
La huella comprueba identidad de bytes; no equivale a validación criptográfica de firma.

No se trasladan Markdown de investigación completos porque contienen diseño
propio. No se consulta ni modifica Supabase ni el índice de producción.
'''

for relative, content in FILES.items():
    p = STAGE / relative
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding='utf-8')

manifest = {
    'prepared_at': DATE,
    'ingestion_executed': False,
    'documents': [dict(e, sha256=hashlib.sha256((STAGE/e['path']).read_bytes()).hexdigest()) for e in ENTRIES],
    'exclude_from_ingestion': ['README.md', '_control/**', 'fuentes/**'],
    'source_pdf_to_move': {
        'from': 'fuentes_locales/minsal_aranceles_res_331_2026.pdf',
        'to': 'ingesta_pos/fuentes/minsal_aranceles_res_331_2026.pdf',
        'sha256': '56b89573b8803d0f44b141d7f903a457fca56e364f4174e02de3e62705911974',
    },
}
(STAGE/'_control/manifiesto.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(f'Preparados {len(ENTRIES)} Markdown de contenido, 3 Markdown de control y 1 manifiesto en {STAGE}')
