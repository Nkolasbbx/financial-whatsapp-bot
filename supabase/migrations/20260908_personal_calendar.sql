-- HdU08: calendario personalizado de compromisos del negocio.
-- El backend accede mediante SUPABASE_SERVICE_ROLE_KEY; no se exponen estas
-- tablas directamente a clientes anon/authenticated.

create extension if not exists pgcrypto;

create table if not exists public.calendar_events (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    description text not null,
    event_at timestamptz not null,
    reminder_at timestamptz not null,
    status text not null default 'active',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    cancelled_at timestamptz,
    constraint calendar_events_description_length_check
        check (char_length(btrim(description)) between 1 and 500),
    constraint calendar_events_status_check
        check (status in ('active', 'completed', 'cancelled')),
    constraint calendar_events_reminder_before_event_check
        check (reminder_at <= event_at)
);

create index if not exists calendar_events_user_status_event_idx
    on public.calendar_events (user_id, status, event_at);

create index if not exists calendar_events_due_idx
    on public.calendar_events (reminder_at)
    where status = 'active';

create table if not exists public.calendar_sessions (
    user_id uuid primary key references public.users(id) on delete cascade,
    state text not null,
    event_id uuid references public.calendar_events(id) on delete cascade,
    draft_event_at timestamptz,
    draft_description text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint calendar_sessions_state_check check (
        state in (
            'waiting_date',
            'waiting_description',
            'confirming_creation',
            'waiting_new_date',
            'confirming_update',
            'confirming_delete'
        )
    ),
    constraint calendar_sessions_description_length_check
        check (
            draft_description is null
            or char_length(btrim(draft_description)) between 1 and 500
        )
);

create table if not exists public.calendar_deliveries (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references public.calendar_events(id) on delete cascade,
    scheduled_for timestamptz not null,
    delivery_status text not null default 'pending',
    provider_message_id text unique,
    failure_reason text,
    attempt_count integer not null default 1,
    created_at timestamptz not null default now(),
    sent_at timestamptz,
    delivered_at timestamptz,
    read_at timestamptz,
    constraint calendar_deliveries_status_check check (
        delivery_status in (
            'pending', 'sent', 'delivered', 'read', 'failed', 'cancelled'
        )
    ),
    constraint calendar_deliveries_attempt_count_check
        check (attempt_count >= 1),
    constraint calendar_deliveries_event_schedule_unique
        unique (event_id, scheduled_for)
);

create index if not exists calendar_deliveries_event_idx
    on public.calendar_deliveries (event_id, scheduled_for desc);

create or replace function public.set_calendar_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists set_calendar_events_updated_at on public.calendar_events;
create trigger set_calendar_events_updated_at
before update on public.calendar_events
for each row execute function public.set_calendar_updated_at();

drop trigger if exists set_calendar_sessions_updated_at on public.calendar_sessions;
create trigger set_calendar_sessions_updated_at
before update on public.calendar_sessions
for each row execute function public.set_calendar_updated_at();

alter table public.calendar_events enable row level security;
alter table public.calendar_sessions enable row level security;
alter table public.calendar_deliveries enable row level security;

revoke all on public.calendar_events from anon, authenticated;
revoke all on public.calendar_sessions from anon, authenticated;
revoke all on public.calendar_deliveries from anon, authenticated;

grant all on public.calendar_events to service_role;
grant all on public.calendar_sessions to service_role;
grant all on public.calendar_deliveries to service_role;
