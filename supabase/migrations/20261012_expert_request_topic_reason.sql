-- HdU16 (iteración): el emprendedor indica el tema y un motivo opcional al
-- pedir un experto, para que el admin pueda priorizar y filtrar.
--
-- 'draft' es el estado mientras el bot le pregunta el tema/motivo: el admin
-- nunca lo ve, y pasa a 'pending' cuando el emprendedor termina de responder.
--
-- Compatible con el código desplegado antes de este cambio: las columnas
-- nuevas aceptan NULL y el código viejo no usa el estado 'draft'. Correr este
-- SQL ANTES de desplegar el código nuevo.
alter table public.expert_requests
    add column if not exists topic text,
    add column if not exists reason text;

alter table public.expert_requests
    drop constraint if exists expert_requests_status_check;
alter table public.expert_requests
    add constraint expert_requests_status_check
    check (status in ('draft', 'pending', 'in_progress', 'resolved'));

-- Una sola solicitud abierta por emprendedor (el borrador también cuenta).
drop index if exists public.expert_requests_one_open_per_user;
create unique index expert_requests_one_open_per_user
    on public.expert_requests (user_id)
    where status in ('draft', 'pending', 'in_progress');
