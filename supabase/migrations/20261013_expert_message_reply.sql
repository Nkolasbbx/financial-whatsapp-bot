-- HdU16 (iteración): el experto puede responder citando un mensaje puntual
-- del emprendedor, como el "Responder" de WhatsApp.
--
-- wa_message_id: wamid del mensaje recibido, necesario para que WhatsApp lo
--   muestre citado al enviar la respuesta.
-- reply_to_id: a qué mensaje de la conversación responde el experto (para
--   mostrar la cita también en el panel).
--
-- Compatible con el código anterior: ambas columnas aceptan NULL. Correr este
-- SQL ANTES de desplegar el código nuevo.
alter table public.expert_messages
    add column if not exists wa_message_id text,
    add column if not exists reply_to_id bigint
        references public.expert_messages (id) on delete set null;
