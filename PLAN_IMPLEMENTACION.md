# 🏗️ Plan de Implementación - Criterios 3, 4, 5

## Resumen de Estado

### ✅ Implementados (Criterios 1-2)
- `mi roadmap` - Visualiza estado con barras de progreso
- `listo` - Marca hitos como completados y actualiza BD

### ❌ Por Implementar (Criterios 3-5)
1. **Criterio 3**: Ayuda contextualizada (inyectar contexto del hito en IA)
2. **Criterio 4**: Finalización 100% (mejorar experiencia cuando roadmap termina)
3. **Criterio 5**: Manejo de insatisfacción (detectar y ofrecer alternativas)

---

## 🏗️ Arquitectura de Solución

### **Criterio 3: Ayuda Contextualizada**

```
Usuario: "❓ Ayuda" (HITO_AYUDA_ID)
    ↓
route_message() detecta HITO_AYUDA_ID
    ↓
get_pending_milestone(user) → obtiene hito pendiente
    ↓
extract_hito_context(user) → {"nombre", "desc", "rubro", "comuna"}
    ↓
Retorna "__AI_QUERY_WITH_HITO_CONTEXT__"
    ↓
webhook.py detecta ese patrón
    ↓
process_ai_and_send(phone, message, hito_context=...)
    ↓
get_ai_response(user, message, hito_context=...)
    ↓
Inyecta en SYSTEM_PROMPT:
    [CONTEXTO DEL HITO ACTUAL]
    Nombre: {hito.title}
    Descripción: {hito.desc}
    Rubro: {user.rubro}
    Comuna: {user.comuna}
    
    Tu objetivo es guiar al usuario paso-a-paso para completar este hito.
    ↓
Envía respuesta con botones de satisfacción
```

### **Criterio 4: Finalización 100%**

```
mark_hito_done(user) → último hito completado
    ↓
Detecta: no hay más hitos pendientes
    ↓
Genera mensaje épico de felicitación
    ↓
Incluye resumen: tiempo, rubro, municipio
    ↓
Ofrece siguientes acciones (fondos, optimización)
    ↓
Guarda evento: roadmap_completed en BD
```

### **Criterio 5: Manejo de Insatisfacción**

```
Patrón de insatisfacción detectado:
  - "eso no me sirvió"
  - "sigo sin entender"
  - "no entiendo"
  - "aún tengo dudas"
  - "no funcionó"
    ↓
route_message() ofrece opciones interactivas:
    
  ┌─────────────────────────┐
  │ No quedó claro, ¿verdad?│
  ├─────────────────────────┤
  │ 🔄 Reformular respuesta │
  │ 👨‍💼 Hablar con asesor    │
  │ 📋 Continuar roadmap    │
  └─────────────────────────┘
    ↓
  - "🔄 Reformular": Repite la pregunta al LLM con otro enfoque
  - "👨‍💼 Hablar": Ofrece contacto de soporte (email/teléfono)
  - "📋 Continuar": Vuelve al roadmap
```

---

## 📝 Cambios por Archivo

### **1. `core/roadmaps.py`** (Nuevo)
```python
def extract_hito_context(user: dict) -> dict | None:
    """Extrae contexto del hito pendiente para inyectar en IA."""
    hito = get_pending_milestone(user)
    if not hito:
        return None
    return {
        "title": hito.get("title"),
        "description": hito.get("desc"),
        "rubro": user.get("rubro", "No definido"),
        "comuna": user.get("comuna", "No definida"),
    }
```

### **2. `services/message_router.py`** (Modificar)

#### Nueva función: `detect_unsatisfaction()`
```python
UNSATISFIED_PATTERNS = [
    "no me sirvió", "no sirvió", "eso no me sirvió",
    "sigo sin entender", "no entiendo", "aún tengo dudas",
    "no funcionó", "no funcionó así", "no es lo que",
    "eso no fue", "me sirve", "me ayuda", "confundido",
]

def detect_unsatisfaction(message: str) -> bool:
    """Detecta si el usuario está insatisfecho con la respuesta."""
    return any(pattern in message.lower() for pattern in UNSATISFIED_PATTERNS)
```

#### Nueva función: `handle_unsatisfaction()`
```python
def handle_unsatisfaction(user: dict) -> dict:
    """Ofrece opciones cuando el usuario no está satisfecho."""
    return {
        "type": "buttons",
        "body": (
            "Entiendo que no quedó claro 😊 ¿Qué prefieres?\n\n"
            "_Puedo intentar de otra forma, conectarte con soporte, "
            "o continuamos con el roadmap._"
        ),
        "options": [
            ("unsatisfied_reformulate", "🔄 Reformular"),
            ("unsatisfied_support", "👨‍💼 Soporte humano"),
            ("unsatisfied_roadmap", "📋 Continuar roadmap"),
        ],
    }
```

#### Modificar: `route_message()`
- Detectar `HITO_AYUDA_ID` y retornar `"__AI_QUERY_WITH_CONTEXT__"`
- Manejar respuestas de insatisfacción (`unsatisfied_*`)

### **3. `core/ia.py`** (Modificar)

#### Modificar: `get_ai_response()`
```python
async def get_ai_response(
    user: dict, 
    message: str, 
    ollama_available: bool,
    hito_context: dict | None = None,  # ← NUEVO
    background_tasks=None
) -> str:
    """
    Si hito_context viene rellenado:
      - Inyecta en system prompt sección [CONTEXTO DEL HITO]
      - Instrucciones específicas para guiar en ese trámite
    """
    # ... código existente ...
    
    hito_info = ""
    if hito_context:
        hito_info = f"""
[CONTEXTO DEL HITO ACTUAL A COMPLETAR]:
- *Nombre del hito*: {hito_context['title']}
- *Descripción*: {hito_context['description']}
- *Rubro del usuario*: {hito_context['rubro']}
- *Comuna*: {hito_context['comuna']}

Tu objetivo es guiar paso-a-paso para completar este hito específico.
Usa ejemplos del rubro, mantén el tono amigable y muy breve.
No hagas preguntas genéricas; sé específico para este trámite.
"""
    
    system_con_rag = (
        f"{system}\n\n"
        f"{hito_info}"  # ← INSERTAR CONTEXTO AQUÍ
        f"[RESUMEN DE INTERACCIONES PREVIAS]:\n"
        # ... resto del código ...
    )
```

### **4. `webhook.py`** (Modificar)

#### Nueva lógica en `whatsapp_webhook()`
```python
if result == "__AI_QUERY__":
    # Caso normal: sin contexto
    background_tasks.add_task(process_ai_and_send, phone, message, ...)

elif result == "__AI_QUERY_WITH_CONTEXT__":
    # Caso nuevo: con contexto del hito
    user = get_user(phone)
    hito_ctx = extract_hito_context(user) if user else None
    background_tasks.add_task(
        process_ai_and_send,
        phone,
        message,
        ...,
        hito_context=hito_ctx
    )
```

### **5. `core/roadmaps.py`** (Mejorar `mark_hito_done()`)

Cuando se complete el último hito (roadmap 100%):
- Mensaje más celebratorio
- Incluir resumen (cuántos hitos, en cuánto tiempo)
- Destacar siguiente acción (fondos)
- Botón de "Compartir logro" (opcional)

---

## 🧪 Testing End-to-End

### Test 1: Criterio 3 (Ayuda contextualizada)
```
1. Usuario completa onboarding (rubro=textil, comuna=Recoleta)
2. Escribe "mi roadmap" → ve hito 1: "Obtener CI vigente"
3. Presiona "❓ Ayuda"
4. Espera respuesta que mencione:
   - "CI vigente" (título del hito)
   - "textil" (rubro)
   - "Recoleta" (comuna)
   - Pasos específicos para renovar CI
5. Valida: IA menciona el hito específico + rubro + comuna
```

### Test 2: Criterio 4 (Finalización 100%)
```
1. Usuario marca todos los hitos como "listo"
2. En el último hito, presiona "listo"
3. Espera respuesta que incluya:
   - Mensaje de felicitación épica
   - "100% de avance"
   - Próximo paso sugerido (fondos)
4. Valida: Mensaje celebratorio + botón de fondos destacado
```

### Test 3: Criterio 5 (Insatisfacción)
```
1. Usuario hace una pregunta sobre un hito
2. IA responde
3. Usuario escribe: "eso no me sirvió"
4. Espera botones de opciones:
   - 🔄 Reformular
   - 👨‍💼 Soporte humano
   - 📋 Continuar roadmap
5. Valida: Cada opción funciona correctamente
```

---

## 📊 Riesgos y Mitigación

| Riesgo | Impacto | Mitigación |
|--------|---------|-----------|
| Inyectar contexto corrompe system prompt | Alto | Escapar caracteres, validar tamaño |
| IA ignora contexto del hito | Alto | Test unitarios, ejemplos en prompt |
| Demora aumenta > 3s | Medio | Caché embeddings, optimizar RAG |
| Botones no se ven bien | Bajo | Revisar límite de caracteres (1024) |

---

## ✅ Checklist de Implementación

### Fase 1: Preparación
- [ ] Crear funciones nuevas en `roadmaps.py` (extract_hito_context)
- [ ] Crear detectores en `message_router.py` (detect_unsatisfaction)

### Fase 2: Lógica de Enrutamiento
- [ ] Modificar `route_message()` para HITO_AYUDA_ID
- [ ] Agregar manejo de respuestas de insatisfacción
- [ ] Crear handlers para cada opción de insatisfacción

### Fase 3: Integración con IA
- [ ] Modificar `get_ai_response()` para aceptar `hito_context`
- [ ] Inyectar contexto en SYSTEM_PROMPT
- [ ] Modificar `process_ai_and_send()` para pasar contexto

### Fase 4: Webhook
- [ ] Detectar `__AI_QUERY_WITH_CONTEXT__` en webhook
- [ ] Pasar contexto a background task

### Fase 5: UX/Mensajes
- [ ] Mejorar mensaje de felicitación en mark_hito_done()
- [ ] Refinar opciones de insatisfacción

### Fase 6: Testing
- [ ] Test manual en WhatsApp (3 escenarios)
- [ ] Test de limites (respuestas largas, emojis)
- [ ] Validar que no rompe criterios 1-2

---

## 📚 Referencias

- **Criterio 3**: Enriquecimiento de prompt con contexto dinámico
- **Criterio 4**: UX conversacional para hitos finales
- **Criterio 5**: Detección de insatisfacción + opciones interactivas
