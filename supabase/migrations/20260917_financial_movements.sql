-- HdU13: registro conversacional de ingresos y gastos.
-- El backend usa SUPABASE_SERVICE_ROLE_KEY; no se exponen estas tablas a
-- clientes anon/authenticated.

create extension if not exists pgcrypto;

create table if not exists public.financial_movements (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    movement_type text not null,
    amount bigint not null,
    currency text not null default 'CLP',
    category text not null,
    description text not null,
    occurred_on date not null,
    original_text text,
    status text not null default 'confirmed',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    constraint financial_movements_type_check
        check (movement_type in ('income', 'expense')),
    constraint financial_movements_amount_check check (amount > 0),
    constraint financial_movements_currency_check check (currency = 'CLP'),
    constraint financial_movements_category_check check (
        (
            movement_type = 'income'
            and category in (
                'ventas', 'servicios', 'aportes_capital', 'otros_ingresos'
            )
        )
        or
        (
            movement_type = 'expense'
            and category in (
                'insumos_mercaderia', 'transporte', 'arriendo_servicios',
                'permisos_tramites', 'marketing', 'equipamiento',
                'otros_gastos'
            )
        )
    ),
    constraint financial_movements_description_length_check
        check (char_length(btrim(description)) between 1 and 500),
    constraint financial_movements_original_text_length_check
        check (original_text is null or char_length(original_text) <= 2000),
    constraint financial_movements_status_check
        check (status in ('confirmed', 'deleted')),
    constraint financial_movements_deleted_state_check check (
        (status = 'confirmed' and deleted_at is null)
        or (status = 'deleted' and deleted_at is not null)
    )
);

create table if not exists public.financial_movement_sessions (
    user_id uuid primary key references public.users(id) on delete cascade,
    state text not null,
    draft jsonb not null default '{}'::jsonb,
    missing_fields text[] not null default '{}'::text[],
    target_movement_id uuid
        references public.financial_movements(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint financial_movement_sessions_state_check check (
        state in (
            'waiting_missing_data', 'confirming_creation',
            'choosing_correction', 'waiting_replacement',
            'confirming_update', 'confirming_delete'
        )
    ),
    constraint financial_movement_sessions_draft_check
        check (jsonb_typeof(draft) = 'object')
);

create index if not exists financial_movements_user_date_idx
    on public.financial_movements (user_id, occurred_on desc)
    where status = 'confirmed';

create index if not exists financial_movements_user_created_idx
    on public.financial_movements (user_id, created_at desc)
    where status = 'confirmed';

create index if not exists financial_movements_summary_idx
    on public.financial_movements (
        user_id, movement_type, category, occurred_on
    )
    where status = 'confirmed';

create index if not exists financial_movement_sessions_updated_idx
    on public.financial_movement_sessions (updated_at);

create or replace function public.set_financial_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists set_financial_movements_updated_at
    on public.financial_movements;
create trigger set_financial_movements_updated_at
before update on public.financial_movements
for each row execute function public.set_financial_updated_at();

drop trigger if exists set_financial_sessions_updated_at
    on public.financial_movement_sessions;
create trigger set_financial_sessions_updated_at
before update on public.financial_movement_sessions
for each row execute function public.set_financial_updated_at();

create or replace function public.confirm_financial_movement(
    p_user_id uuid,
    p_movement_type text,
    p_amount bigint,
    p_category text,
    p_description text,
    p_occurred_on date,
    p_original_text text default null,
    p_target_movement_id uuid default null
)
returns public.financial_movements
language plpgsql
set search_path = public
as $$
declare
    saved_movement public.financial_movements%rowtype;
begin
    if p_user_id is null then
        raise exception 'El usuario es obligatorio';
    end if;
    if p_movement_type not in ('income', 'expense') then
        raise exception 'Tipo de movimiento inválido';
    end if;
    if p_amount is null or p_amount <= 0 then
        raise exception 'El monto debe ser mayor que cero';
    end if;
    if p_description is null or btrim(p_description) = '' then
        raise exception 'La descripción es obligatoria';
    end if;
    if p_occurred_on is null then
        raise exception 'La fecha es obligatoria';
    end if;

    if p_target_movement_id is null then
        insert into public.financial_movements (
            user_id, movement_type, amount, currency, category, description,
            occurred_on, original_text, status
        ) values (
            p_user_id, p_movement_type, p_amount, 'CLP', p_category,
            btrim(p_description), p_occurred_on,
            nullif(btrim(p_original_text), ''), 'confirmed'
        ) returning * into saved_movement;
    else
        update public.financial_movements
        set movement_type = p_movement_type,
            amount = p_amount,
            category = p_category,
            description = btrim(p_description),
            occurred_on = p_occurred_on,
            original_text = coalesce(
                nullif(btrim(p_original_text), ''), original_text
            )
        where id = p_target_movement_id
          and user_id = p_user_id
          and status = 'confirmed'
        returning * into saved_movement;

        if not found then
            raise exception 'No se encontró el movimiento que se quiere modificar';
        end if;
    end if;

    delete from public.financial_movement_sessions
    where user_id = p_user_id;
    return saved_movement;
end;
$$;

create or replace function public.soft_delete_financial_movement(
    p_user_id uuid,
    p_movement_id uuid
)
returns public.financial_movements
language plpgsql
set search_path = public
as $$
declare
    deleted_movement public.financial_movements%rowtype;
begin
    update public.financial_movements
    set status = 'deleted', deleted_at = now()
    where id = p_movement_id
      and user_id = p_user_id
      and status = 'confirmed'
    returning * into deleted_movement;

    if not found then
        raise exception 'No se encontró el movimiento que se quiere eliminar';
    end if;

    delete from public.financial_movement_sessions
    where user_id = p_user_id;
    return deleted_movement;
end;
$$;

create or replace function public.get_financial_month_summary(
    p_user_id uuid,
    p_month_start date,
    p_month_end date
)
returns jsonb
language sql
stable
set search_path = public
as $$
    with filtered_movements as (
        select movement_type, category, amount
        from public.financial_movements
        where user_id = p_user_id
          and status = 'confirmed'
          and occurred_on >= p_month_start
          and occurred_on < p_month_end
    ),
    totals as (
        select
            coalesce(sum(amount) filter (where movement_type = 'income'), 0)
                as income_total,
            coalesce(sum(amount) filter (where movement_type = 'expense'), 0)
                as expense_total,
            count(*) as movement_count
        from filtered_movements
    ),
    income_categories as (
        select category, sum(amount) as total
        from filtered_movements
        where movement_type = 'income'
        group by category order by total desc limit 3
    ),
    expense_categories as (
        select category, sum(amount) as total
        from filtered_movements
        where movement_type = 'expense'
        group by category order by total desc limit 3
    )
    select jsonb_build_object(
        'month_start', p_month_start,
        'month_end', p_month_end,
        'income_total', totals.income_total,
        'expense_total', totals.expense_total,
        'net_total', totals.income_total - totals.expense_total,
        'movement_count', totals.movement_count,
        'income_categories', coalesce((
            select jsonb_agg(jsonb_build_object(
                'category', category, 'total', total
            ) order by total desc)
            from income_categories
        ), '[]'::jsonb),
        'expense_categories', coalesce((
            select jsonb_agg(jsonb_build_object(
                'category', category, 'total', total
            ) order by total desc)
            from expense_categories
        ), '[]'::jsonb)
    )
    from totals;
$$;

alter table public.financial_movements enable row level security;
alter table public.financial_movement_sessions enable row level security;

revoke all on public.financial_movements from anon, authenticated;
revoke all on public.financial_movement_sessions from anon, authenticated;
grant select, insert, update, delete on public.financial_movements
    to service_role;
grant select, insert, update, delete on public.financial_movement_sessions
    to service_role;

revoke execute on function public.confirm_financial_movement(
    uuid, text, bigint, text, text, date, text, uuid
) from public, anon, authenticated;
grant execute on function public.confirm_financial_movement(
    uuid, text, bigint, text, text, date, text, uuid
) to service_role;

revoke execute on function public.soft_delete_financial_movement(uuid, uuid)
    from public, anon, authenticated;
grant execute on function public.soft_delete_financial_movement(uuid, uuid)
    to service_role;

revoke execute on function public.get_financial_month_summary(uuid, date, date)
    from public, anon, authenticated;
grant execute on function public.get_financial_month_summary(uuid, date, date)
    to service_role;

