-- HdU16: contacto con un experto. El emprendedor pide hablar con un experto
-- de su comuna; el admin de esa comuna toma la solicitud y conversa desde el
-- panel. Los mensajes salen y entran por el número del bot, así que ni el
-- teléfono del emprendedor ni el del admin se muestran al otro.
--
-- Las tablas solo se acceden con la service role (backend): RLS activado y
-- sin políticas, para que el cliente anon no pueda leerlas.
create table if not exists public.expert_requests (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users (id) on delete cascade,
    comuna text not null,
    status text not null default 'pending',
    created_at timestamptz not null default now(),
    taken_at timestamptz,
    taken_by text,
    resolved_at timestamptz,
    closed_by text,
    -- Último mensaje ENTRANTE del emprendedor: WhatsApp solo permite texto
    -- libre dentro de las 24 h siguientes a este instante.
    user_last_message_at timestamptz not null default now(),
    constraint expert_requests_status_check
        check (status in ('pending', 'in_progress', 'resolved'))
);

-- Una sola solicitud abierta por emprendedor.
create unique index if not exists expert_requests_one_open_per_user
    on public.expert_requests (user_id)
    where status in ('pending', 'in_progress');

create index if not exists expert_requests_comuna_idx
    on public.expert_requests (comuna, status, created_at desc);

create table if not exists public.expert_messages (
    id bigserial primary key,
    request_id uuid not null references public.expert_requests (id) on delete cascade,
    sender text not null,
    content text not null,
    created_at timestamptz not null default now(),
    constraint expert_messages_sender_check check (sender in ('expert', 'user'))
);

create index if not exists expert_messages_request_idx
    on public.expert_messages (request_id, created_at);

alter table public.expert_requests enable row level security;
alter table public.expert_messages enable row level security;
