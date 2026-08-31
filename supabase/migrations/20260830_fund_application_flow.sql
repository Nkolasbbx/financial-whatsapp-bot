-- HdU05: selección, evaluación y seguimiento conversacional de fondos.
-- Esta migración parte de la tabla public.fondos existente.

begin;

alter table public.fondos
    add column if not exists slug text,
    add column if not exists aliases text[] not null default '{}',
    add column if not exists fecha_apertura date;

update public.fondos set
    slug = 'capital_semilla_emprende',
    aliases = array['capital semilla', 'semilla emprende', 'fondo capital semilla']
where nombre = 'Capital Semilla Emprende';

update public.fondos set
    slug = 'capital_abeja_emprende',
    aliases = array['capital abeja', 'abeja emprende', 'fondo capital abeja']
where nombre = 'Capital Abeja Emprende';

update public.fondos set
    slug = 'capital_pioneras_emprende',
    aliases = array['capital pioneras', 'pioneras emprende', 'fondo pioneras']
where nombre = 'Capital Pioneras Emprende';

update public.fondos set
    slug = 'crece',
    aliases = array['fondo crece', 'sercotec crece', 'programa crece']
where nombre = 'Crece';

alter table public.fondos alter column slug set not null;

create unique index if not exists fondos_slug_unique_idx
    on public.fondos (slug);
create index if not exists fondos_activo_fecha_cierre_idx
    on public.fondos (activo, fecha_cierre);

with requisitos_enriquecidos as (
    select
        f.id,
        jsonb_agg(
            req.value
            || jsonb_build_object('obligatorio', true)
            || jsonb_build_object(
                'corregible',
                case req.value->>'clave'
                    when 'proyecto_negocio' then true
                    when 'capacitacion' then true
                    when 'inicio_sii' then true
                    when 'capacitacion_crece' then true
                    when 'mayor_edad' then false
                    when 'genero_femenino' then false
                    when 'sin_inicio_sii' then false
                    when 'sin_beneficio_reciente' then false
                    when 'rubro_pioneras' then false
                    when 'actividad_coherente' then false
                    else null
                end
            )
            || case req.value->>'clave'
                when 'proyecto_negocio' then jsonb_build_object('plazo_dias', 2)
                when 'capacitacion' then jsonb_build_object('plazo_dias', 28)
                when 'inicio_sii' then jsonb_build_object('plazo_dias', 1)
                when 'capacitacion_crece' then jsonb_build_object('plazo_dias', 14)
                else '{}'::jsonb
            end
            order by req.position
        ) as requisitos
    from public.fondos f
    cross join lateral jsonb_array_elements(f.requisitos)
        with ordinality as req(value, position)
    group by f.id
)
update public.fondos f set
    requisitos = enriched.requisitos,
    updated_at = now()
from requisitos_enriquecidos enriched
where f.id = enriched.id;

create table if not exists public.fund_user_answers (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    field_key text not null,
    value jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint fund_user_answers_field_key_not_empty
        check (length(trim(field_key)) > 0),
    constraint fund_user_answers_user_field_unique
        unique (user_id, field_key)
);

create index if not exists fund_user_answers_user_id_idx
    on public.fund_user_answers (user_id);

create table if not exists public.fund_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    fondo_id uuid references public.fondos(id) on delete set null,
    status text not null default 'selecting',
    pending_field_key text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint fund_sessions_one_per_user unique (user_id),
    constraint fund_sessions_status_check check (
        status in ('selecting', 'collecting_data', 'evaluated', 'cancelled')
    )
);

create index if not exists fund_sessions_status_idx
    on public.fund_sessions (status);
create index if not exists fund_sessions_fondo_id_idx
    on public.fund_sessions (fondo_id);

create table if not exists public.fund_requirement_definitions (
    field_key text primary key,
    label text not null,
    question text,
    answer_type text not null,
    source_type text not null,
    profile_field text,
    options jsonb not null default '[]'::jsonb,
    question_order integer not null default 100,
    evaluation_rule jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint fund_requirement_answer_type_check check (
        answer_type in ('boolean', 'number', 'text', 'derived')
    ),
    constraint fund_requirement_source_type_check check (
        source_type in ('user_profile', 'user_answer', 'computed')
    ),
    constraint fund_requirement_question_required_check check (
        source_type <> 'user_answer' or question is not null
    )
);

insert into public.fund_requirement_definitions (
    field_key, label, question, answer_type, source_type, profile_field,
    options, question_order, evaluation_rule
) values
('sin_inicio_sii', 'Sin inicio de actividades en el SII', null, 'derived',
 'user_profile', 'inicio_sii', '[]', 0,
 '{"operator":"equals","expected":"no"}'),
('inicio_sii', 'Inicio de actividades en el SII', null, 'derived',
 'user_profile', 'inicio_sii', '[]', 0,
 '{"operator":"equals","expected":"si"}'),
('rubro_pioneras', 'Rubro correspondiente a sectores no tradicionales', null,
 'derived', 'computed', 'rubro', '[]', 0,
 '{"operator":"custom","handler":"rubro_pioneras"}'),
('mayor_edad', 'Mayor de edad', '¿Eres mayor de 18 años?', 'boolean',
 'user_answer', null,
 '[{"id":"yes","title":"Sí","value":true},{"id":"no","title":"No","value":false},{"id":"unknown","title":"Prefiero omitir","value":null}]',
 10, '{"operator":"equals","expected":true}'),
('genero_femenino', 'Sexo registral femenino',
 '¿Tu sexo registral es femenino?', 'boolean', 'user_answer', null,
 '[{"id":"yes","title":"Sí","value":true},{"id":"no","title":"No","value":false},{"id":"unknown","title":"Prefiero omitir","value":null}]',
 20, '{"operator":"equals","expected":true}'),
('sin_beneficio_reciente', 'Sin beneficios SERCOTEC recientes',
 '¿Confirmas que no has recibido beneficios SERCOTEC Emprende durante los últimos 2 años?',
 'boolean', 'user_answer', null,
 '[{"id":"yes","title":"Sí, confirmo","value":true},{"id":"no","title":"Recibí beneficios","value":false},{"id":"unknown","title":"No lo sé","value":null}]',
 30, '{"operator":"equals","expected":true}'),
('ventas_crece', 'Ventas anuales en UF',
 '¿Aproximadamente cuántas UF vendió tu negocio durante el último año?',
 'number', 'user_answer', null, '[]', 40,
 '{"operator":"between","min":200,"max":25000,"unit":"UF"}'),
('proyecto_negocio', 'Proyecto de negocio y video pitch',
 '¿Ya tienes preparado tu proyecto de negocio y el video pitch solicitado?',
 'boolean', 'user_answer', null,
 '[{"id":"yes","title":"Sí, lo tengo","value":true},{"id":"no","title":"Todavía no","value":false},{"id":"unknown","title":"No estoy seguro","value":null}]',
 50, '{"operator":"equals","expected":true}'),
('capacitacion', 'Capacitación en gestión empresarial',
 '¿Ya completaste una capacitación en gestión empresarial?',
 'boolean', 'user_answer', null,
 '[{"id":"yes","title":"Sí","value":true},{"id":"no","title":"Todavía no","value":false},{"id":"unknown","title":"No estoy seguro","value":null}]',
 60, '{"operator":"equals","expected":true}'),
('capacitacion_crece', 'Cursos requeridos por Crece',
 '¿Ya aprobaste los 3 cursos requeridos en capacitacion.sercotec.cl?',
 'boolean', 'user_answer', null,
 '[{"id":"yes","title":"Sí, los aprobé","value":true},{"id":"no","title":"Todavía no","value":false},{"id":"unknown","title":"No estoy seguro","value":null}]',
 60, '{"operator":"equals","expected":true}'),
('actividad_coherente', 'Actividad económica compatible',
 'Según las bases del fondo, ¿tu actividad económica está incluida en la convocatoria?',
 'boolean', 'user_answer', null,
 '[{"id":"yes","title":"Sí","value":true},{"id":"no","title":"No","value":false},{"id":"unknown","title":"No lo sé","value":null}]',
 70, '{"operator":"equals","expected":true}')
on conflict (field_key) do update set
    label = excluded.label,
    question = excluded.question,
    answer_type = excluded.answer_type,
    source_type = excluded.source_type,
    profile_field = excluded.profile_field,
    options = excluded.options,
    question_order = excluded.question_order,
    evaluation_rule = excluded.evaluation_rule,
    updated_at = now();

alter table public.fund_user_answers enable row level security;
alter table public.fund_sessions enable row level security;
alter table public.fund_requirement_definitions enable row level security;

commit;
