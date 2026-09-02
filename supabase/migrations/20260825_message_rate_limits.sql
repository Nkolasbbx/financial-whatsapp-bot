create table if not exists public.message_rate_limits (
    phone text primary key,
    request_timestamps timestamptz[] not null
        default array[]::timestamptz[],
    blocked_until timestamptz,
    last_notification_at timestamptz,
    updated_at timestamptz not null default now()
);

alter table public.message_rate_limits enable row level security;

revoke all on public.message_rate_limits from anon, authenticated;

create or replace function public.check_message_rate_limit(
    p_phone text,
    p_max_messages integer default 10,
    p_window_seconds integer default 60,
    p_block_seconds integer default 60
)
returns table (
    allowed boolean,
    notify_user boolean,
    retry_after_seconds integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_requests timestamptz[];
    v_blocked_until timestamptz;
begin
    insert into public.message_rate_limits (phone)
    values (p_phone)
    on conflict (phone) do nothing;

    select request_timestamps, blocked_until
    into v_requests, v_blocked_until
    from public.message_rate_limits
    where phone = p_phone
    for update;

    if v_blocked_until is not null and v_blocked_until > v_now then
        return query
        select
            false,
            false,
            greatest(
                1,
                ceil(extract(epoch from (v_blocked_until - v_now)))::integer
            );
        return;
    end if;

    select coalesce(
        array_agg(request_time order by request_time),
        array[]::timestamptz[]
    )
    into v_requests
    from unnest(
        coalesce(v_requests, array[]::timestamptz[])
    ) as request_time
    where request_time > (
        v_now - make_interval(secs => p_window_seconds)
    );

    if cardinality(v_requests) >= p_max_messages then
        v_blocked_until := v_now + make_interval(secs => p_block_seconds);

        update public.message_rate_limits
        set
            request_timestamps = v_requests,
            blocked_until = v_blocked_until,
            last_notification_at = v_now,
            updated_at = v_now
        where phone = p_phone;

        return query
        select false, true, p_block_seconds;
        return;
    end if;

    v_requests := array_append(v_requests, v_now);

    update public.message_rate_limits
    set
        request_timestamps = v_requests,
        blocked_until = null,
        updated_at = v_now
    where phone = p_phone;

    return query
    select true, false, 0;
end;
$$;

revoke all on function public.check_message_rate_limit(
    text,
    integer,
    integer,
    integer
) from public, anon, authenticated;

grant execute on function public.check_message_rate_limit(
    text,
    integer,
    integer,
    integer
) to service_role;
