# HDU001 — Perfil personal y onboarding del negocio

## Preparación

La migración `supabase/migrations/20261014_onboarding_personal_profile.sql`
añade los cinco campos del perfil. Es la misma migración aditiva entregada para
SQL Editor: si ya se ejecutó, no hace falta repetirla. No cambia datos existentes.
No se agregaron dependencias ni variables de entorno.

Reiniciar el backend local o desplegar esta versión para usar el nuevo flujo.
El webhook de Meta y los servicios existentes se mantienen configurados como antes.

## Comportamiento

- Usuarios nuevos: nombre → RUT personal → correo → rubro
  → comuna → estado SII → confirmación y menú.
- El celular se obtiene del remitente de WhatsApp. No se pregunta ni se cambia.
- Los campos que ya contienen datos válidos se omiten.
- La dirección del negocio ya no se pregunta. Se conserva la columna en Supabase
  y cualquier valor guardado anteriormente; no se necesita otra migración.
- Si una sesión estaba esperando la dirección, el siguiente mensaje permite
  continuar con el dato pendiente o terminar el registro, sin guardar ese mensaje
  como dirección ni interpretarlo como respuesta a otra pregunta.
- El RUT se normaliza sin puntos y con guion/dígito verificador; se comprueba
  el dígito, no la identidad. El correo se valida por formato, sin enviar correos.
- Una respuesta inválida conserva la pregunta y los datos anteriores.
- **Volver** permite corregir un dato sin borrar las respuestas restantes.
- `profile_completion_step` guarda el campo personal pendiente o `done`.
  `onboarding_step` conserva los pasos de negocio `1`, `2`, `3`, `3_explicado`, `done`.
- Un usuario antiguo con onboarding terminado completa los datos faltantes en
  su próxima interacción con el bot. El mensaje que abre este registro inicia
  la pregunta; no se ejecuta luego automáticamente como un comando pendiente.
- Quien ya estaba respondiendo rubro/comuna/SII continúa esa secuencia y luego
  completa los datos personales. No se interpreta su respuesta como otro dato.
- Completar datos personales conserva `id`, `phone`, `auth_user_id`, `created_at`,
  historial, resúmenes, roadmap y configuración de recordatorios. El roadmap
  solo se genera al finalizar un onboarding inicial que todavía no tiene uno.
- Una conversación activa con un experto mantiene prioridad. Pausar recordatorios
  y reiniciar siguen disponibles durante el registro.
- Si falla Supabase no se confirma ni se avanza de paso. Reenviar la respuesta
  permite reintentar. Solo el modo sin Supabase configurado usa almacenamiento
  en memoria; un error de conexión no activa ese modo.
- **Reiniciar** mantiene su comportamiento de limpiar el perfil y ahora también
  limpia los datos personales y el paso pendiente. No usarlo para probar la
  conservación del historial: esa prueba se hace completando un perfil antiguo.

## Prueba manual en WhatsApp

1. Con un usuario de prueba nuevo, enviar `hola`: debe pedir nombre, sin llamar
   a la IA. Responder nombre y comprobar que pasa a RUT.
2. Enviar un RUT con dígito incorrecto: debe repetir la pregunta. Luego enviar
   el RUT correcto y comprobar que queda normalizado en Supabase.
3. Enviar un correo incompleto, corregirlo y avanzar. Probar **Volver** para
   corregir el RUT; el nombre y los otros datos deben mantenerse.
4. Después del correo debe pedir rubro directamente. Responder rubro, comuna y estado SII con botones.
   Al terminar debe mostrar el menú y guardar ambos pasos en `done`.
5. Desde rubro, escribir `volver`: debe permitir corregir el correo, sin preguntar dirección.
6. Con un perfil antiguo que tenga hitos completados y datos personales NULL,
   enviar `hola` y completar solo los nuevos datos. Comparar su roadmap,
   fecha de creación, resumen y recordatorios antes/después: deben conservarse.
7. Al terminar, probar **Mi ruta**, **Mi calendario**, **Postular a fondos**,
   **Ingresos y gastos**, una consulta de IA y el acceso al panel.

La suite automatizada también verifica fallos de lectura/escritura, cambios
parciales, continuidad entre mensajes y prioridad de la conversación humana.

## Archivos

- `core/profile_validation.py`: validaciones deterministas.
- `core/onboarding.py`: preguntas, progreso y cierre, calculados antes de guardar.
- `db/users.py`: creación segura, lectura estricta y actualización parcial.
- `services/message_router.py`: entrada al flujo y prioridad de los controles.
